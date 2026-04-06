"""Skill Distiller — 自动化知识蒸馏。

Usage:
    python distiller.py
    python distiller.py path/to/config.json
"""

import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from prompts import (
    baseline_prompt,
    blind_eval_prompt,
    diff_evaluate_prompt,
    evaluate_prompt,
    execute_prompt,
    optimize_prompt,
    reasoning_diff_prompt,
)

PROJECT_ROOT = Path(__file__).resolve().parent
TEXT_FILE_EXTENSIONS = {
    ".md", ".txt", ".json", ".yaml", ".yml", ".csv", ".tsv", ".html", ".xml"
}

_RUN_LOG_PATH: Path | None = None
_CRITICAL_FAIL_CAP = 0.84
_MULTI_CRITICAL_FAIL_CAP = 0.72
_DEFAULT_ARTIFACT_ASSERTION_IDS = {
    "article_file_exists",
    "no_markdown_headers_in_body",
    "no_numbered_action_list",
    "no_numbered_skill_list",
    "no_numbered_list",
    "word_count_approximately_1000",
    "word_count_reasonable_range",
}


def init_run_log(run_dir: Path) -> Path:
    """在工作区创建 distiller.log，用于镜像终端输出与子进程详情。"""
    global _RUN_LOG_PATH
    p = run_dir / "distiller.log"
    _RUN_LOG_PATH = p
    header = (
        "Skill Distiller 运行日志\n"
        f"启动: {datetime.now().isoformat(timespec='seconds')}\n"
        f"工作区: {run_dir}\n\n"
    )
    p.write_text(header, encoding="utf-8")
    return p


def _append_run_log(text: str) -> None:
    if _RUN_LOG_PATH is None:
        return
    with open(_RUN_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(text)


def run_log_print(*args, sep: str = " ", end: str = "\n", file=None, flush: bool = False) -> None:
    """与 print 相同，并追加相同内容到 distiller.log（若已 init_run_log）。"""
    if file is None:
        file = sys.stdout
    buf = io.StringIO()
    print(*args, sep=sep, end=end, file=buf)
    _append_run_log(buf.getvalue())
    print(*args, sep=sep, end=end, file=file, flush=flush)


# ─── 配置 & 发现 ───────────────────────────────────────────


def load_config(path: Path) -> dict:
    """读取并校验 config.json。"""
    cfg = json.loads(path.read_text(encoding="utf-8"))
    student_env = cfg.get("student", {}).get("env", {})
    if not student_env.get("ANTHROPIC_MODEL"):
        sys.exit("config.json: student.env.ANTHROPIC_MODEL 不能为空")
    cfg.setdefault("max_iterations", 15)
    cfg.setdefault("target_pass_rate", 0.90)
    cfg.setdefault("plateau_rounds", 3)
    # 子进程 claude -p 单次最长等待（秒）。可在 JSON 里用 max_runtime_seconds 显式配置（优先于 timeout）。
    cfg.setdefault("timeout", 1200)
    if "max_runtime_seconds" in cfg:
        cfg["timeout"] = int(cfg["max_runtime_seconds"])
    cfg.setdefault("blind_eval_interval", 3)
    cfg.setdefault("coverage_gap_threshold", 0.2)
    cfg.setdefault("min_iterations_before_stop", 2)
    cfg.setdefault("min_optimize_rounds", 1)
    cfg.setdefault("critical_fail_cap", 0.84)
    cfg.setdefault("multi_critical_fail_cap", 0.72)
    global _CRITICAL_FAIL_CAP, _MULTI_CRITICAL_FAIL_CAP
    _CRITICAL_FAIL_CAP = float(cfg["critical_fail_cap"])
    _MULTI_CRITICAL_FAIL_CAP = float(cfg["multi_critical_fail_cap"])
    return cfg


def discover_skill(skill_root: Path) -> tuple[str, Path]:
    """在 SKILL/ 下找唯一子目录（含 SKILL.md）。"""
    if not skill_root.is_dir():
        sys.exit(f"目录不存在: {skill_root}")
    subdirs = [d for d in skill_root.iterdir() if d.is_dir() and (d / "SKILL.md").exists()]
    if len(subdirs) == 0:
        sys.exit("SKILL/ 下未找到含 SKILL.md 的子目录")
    if len(subdirs) > 1:
        sys.exit(f"SKILL/ 下有多个 skill 目录: {[d.name for d in subdirs]}，请只保留一个")
    folder = subdirs[0]
    return folder.name, folder


def discover_inputs(input_root: Path) -> list[Path]:
    """在 Input/ 下找所有子目录作为输入 case。"""
    if not input_root.is_dir():
        sys.exit(f"目录不存在: {input_root}")
    dirs = sorted(d for d in input_root.iterdir() if d.is_dir() and not d.name.startswith("."))
    if not dirs:
        sys.exit("Input/ 下未找到子目录")
    return dirs


# ─── 子进程 ────────────────────────────────────────────────


def run_claude(
    prompt: str,
    env_config: dict,
    cwd: Path,
    timeout: int = 600,
    log_label: str = "claude",
) -> tuple[str, str, int]:
    """调用 claude -p 子进程。"""
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    role_env = env_config.get("env", {})
    if role_env:
        env.update(role_env)

    cmd = [
        "claude", "-p",
        "--dangerously-skip-permissions",
        "--output-format", "text",
        # "--model", "opus",
    ]

    try:
        proc = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            cwd=str(cwd),
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        # 超时在 communicate 阶段抛出，正常返回后的落盘逻辑不会执行，需单独记录
        if _RUN_LOG_PATH is not None:
            out = e.output if isinstance(e.output, str) else ""
            err = e.stderr if isinstance(e.stderr, str) else ""
            _append_run_log(
                f"\n{'=' * 60}\n"
                f"TIMEOUT claude -p [{log_label}] after {e.timeout}s\n"
                f"(子进程已被终止；以下为已捕获的部分输出)\n"
                f"--- stdout partial ({len(out)} chars) ---\n{out}\n"
                f"--- stderr partial ({len(err)} chars) ---\n{err}\n"
            )
        raise

    if _RUN_LOG_PATH is not None:
        stamp = datetime.now().isoformat(timespec="seconds")
        out = proc.stdout or ""
        err = proc.stderr or ""
        _append_run_log(
            f"\n{'=' * 60}\n[{stamp}] claude -p [{log_label}] exit={proc.returncode}\n"
            f"--- stdout ({len(out)} chars) ---\n{out}\n"
            f"--- stderr ({len(err)} chars) ---\n{err}\n"
        )
    return proc.stdout, proc.stderr, proc.returncode


def _normalize_assertion(assertion: dict) -> dict:
    """补齐 assertion 默认字段，兼容旧格式。"""
    normalized = dict(assertion)
    method = normalized.get("evaluation_method", "judge")
    if method not in {"code", "judge"}:
        method = "judge"
    normalized["evaluation_method"] = method

    layer = normalized.get("layer")
    if not layer and normalized.get("id") in _DEFAULT_ARTIFACT_ASSERTION_IDS:
        layer = "artifact"
    if not layer:
        layer = "core"
    if layer not in {"artifact", "core", "scoped"}:
        layer = "core"
    normalized["layer"] = layer

    target = normalized.get("target", "article")
    if target not in {"article", "process"}:
        target = "article"
    normalized["target"] = target

    applies_to_inputs = normalized.get("applies_to_inputs", "all")
    if applies_to_inputs == "all":
        normalized["applies_to_inputs"] = "all"
    elif isinstance(applies_to_inputs, list):
        filtered_inputs = []
        seen_inputs: set[int] = set()
        for value in applies_to_inputs:
            if isinstance(value, int) and value >= 0 and value not in seen_inputs:
                filtered_inputs.append(value)
                seen_inputs.add(value)
        normalized["applies_to_inputs"] = filtered_inputs or "all"
    else:
        normalized["applies_to_inputs"] = "all"

    code_check = normalized.get("code_check")
    if method == "code" and not isinstance(code_check, dict):
        normalized["evaluation_method"] = "judge"
        return normalized

    if method == "code":
        code_check = dict(code_check)
        check_type = code_check.get("type")
        if check_type == "regex_not_found":
            code_check["type"] = "no_pattern_match"
        if target == "article" and not code_check.get("path"):
            code_check["path"] = "article.md"
        normalized["code_check"] = code_check
    return normalized


def _load_assertions(path: Path) -> list[dict]:
    """读取 assertions，并对旧格式做兼容归一化。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        return []
    return [_normalize_assertion(a) for a in data if isinstance(a, dict)]


def _split_assertions(assertions: list[dict]) -> tuple[list[dict], list[dict]]:
    """按评测方法拆分 assertions。"""
    code_assertions = []
    judge_assertions = []
    for assertion in assertions:
        if assertion.get("evaluation_method") == "code":
            code_assertions.append(assertion)
        else:
            judge_assertions.append(assertion)
    return code_assertions, judge_assertions


def _assertion_applies_to_input(assertion: dict, input_id: int) -> bool:
    """判断 assertion 是否适用于某个 input。"""
    applies = assertion.get("applies_to_inputs", "all")
    if applies == "all":
        return True
    if isinstance(applies, list):
        return input_id in applies
    return True


# ─── Workspace ─────────────────────────────────────────────


def _replace_dir(src: Path, dst: Path) -> None:
    """用 src 的完整内容替换 dst。"""
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


def setup_workspace(
    workspace_root: Path,
    skill_name: str,
    skill_folder: Path,
) -> tuple[Path, Path]:
    """初始化本次运行目录，安装 skill。

    返回: (run_dir, installed_skill_dir)
    """
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    run_dir = workspace_root / timestamp
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "baseline").mkdir(exist_ok=True)

    # 备份原始 skill
    src_skill_md = skill_folder / "SKILL.md"
    shutil.copy2(src_skill_md, run_dir / "skill_v0.md")
    _replace_dir(skill_folder, run_dir / "skill_v0")

    # 安装 skill 到 .claude/skills/
    installed_dir = PROJECT_ROOT / ".claude" / "skills" / skill_name
    _replace_dir(skill_folder, installed_dir)

    return run_dir, installed_dir


# ─── 管道步骤 ──────────────────────────────────────────────


def step_baseline(
    config: dict,
    skill_content: str,
    input_dirs: list[Path],
    run_dir: Path,
) -> None:
    """BASELINE: Teacher 执行标杆 + 推理 + 提取 assertions。"""
    baseline_dir = run_dir / "baseline"
    assertions_path = run_dir / "assertions.json"
    failure_modes_path = run_dir / "failure_modes.json"

    prompt = baseline_prompt(
        skill_content=skill_content,
        input_dirs=[str(d) for d in input_dirs],
        baseline_dir=str(baseline_dir),
        assertions_path=str(assertions_path),
        failure_modes_path=str(failure_modes_path),
    )

    stdout, stderr, rc = run_claude(
        prompt,
        config["teacher"],
        PROJECT_ROOT,
        config["timeout"],
        log_label="baseline",
    )

    if rc != 0:
        run_log_print(f"  [WARN] baseline claude -p 退出码 {rc}", file=sys.stderr)
        if stderr.strip():
            run_log_print(f"  stderr: {stderr[:500]}", file=sys.stderr)

    if not assertions_path.exists():
        _append_run_log("FATAL: BASELINE 失败: assertions.json 未生成\n")
        sys.exit("BASELINE 失败: assertions.json 未生成")
    try:
        normalized_assertions = _load_assertions(assertions_path)
        assertions_path.write_text(
            json.dumps(normalized_assertions, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except json.JSONDecodeError as e:
        _append_run_log(f"FATAL: BASELINE 失败: assertions.json 解析失败: {e}\n")
        sys.exit(f"BASELINE 失败: assertions.json 解析失败: {e}")
    if not failure_modes_path.exists():
        run_log_print("  [WARN] failure_modes.json 未生成，后续将退化为纯 assertions 驱动", file=sys.stderr)


def step_execute(
    config: dict,
    skill_name: str,
    input_dirs: list[Path],
    iter_dir: Path,
) -> None:
    """EXECUTE: Student 逐个输入执行 Skill + 推理。"""
    for i, inp_dir in enumerate(input_dirs):
        output_dir = iter_dir / f"output_{i}"
        output_dir.mkdir(parents=True, exist_ok=True)

        prompt = execute_prompt(
            skill_name=skill_name,
            input_dir=str(inp_dir),
            output_dir=str(output_dir),
            reasoning_path=str(iter_dir / f"reasoning_{i}.md"),
        )

        stdout, stderr, rc = run_claude(
            prompt,
            config["student"],
            PROJECT_ROOT,
            config["timeout"],
            log_label=f"execute_input_{i}",
        )

        if rc != 0:
            run_log_print(f"  [WARN] execute input_{i} 退出码 {rc}", file=sys.stderr)


def step_evaluate(
    config: dict,
    input_dirs: list[Path],
    iter_dir: Path,
    run_dir: Path,
    iteration: int,
) -> dict | None:
    """EVALUATE: Teacher 逐条检查 assertions。返回 eval 结果或 None。"""
    eval_path = iter_dir / "eval.json"
    assertions_path = run_dir / "assertions.json"
    failure_modes_path = run_dir / "failure_modes.json"
    output_dirs = [str(iter_dir / f"output_{i}") for i in range(len(input_dirs))]

    try:
        assertions = _load_assertions(assertions_path)
    except json.JSONDecodeError as e:
        run_log_print(f"  [ERROR] assertions.json 解析失败: {e}", file=sys.stderr)
        return None

    code_assertions, judge_assertions = _split_assertions(assertions)
    code_eval_result = _evaluate_code_assertions(code_assertions, output_dirs) if code_assertions else None
    judge_eval_result = None

    if judge_assertions:
        judge_assertions_path = iter_dir / "judge_assertions.json"
        judge_eval_path = iter_dir / "judge_eval.json"
        judge_assertions_path.write_text(
            json.dumps(judge_assertions, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        prompt = evaluate_prompt(
            assertions_path=str(judge_assertions_path),
            output_dirs=output_dirs,
            eval_path=str(judge_eval_path),
            iteration=iteration,
            failure_modes_path=str(failure_modes_path) if failure_modes_path.exists() else "",
        )

        stdout, stderr, rc = run_claude(
            prompt,
            config["teacher"],
            PROJECT_ROOT,
            config["timeout"],
            log_label=f"evaluate_{iter_dir.name}",
        )

        if rc != 0:
            run_log_print(f"  [WARN] evaluate 退出码 {rc}", file=sys.stderr)

        if not judge_eval_path.exists():
            run_log_print("  [ERROR] judge_eval.json 未生成", file=sys.stderr)
            return None

        try:
            judge_eval_result = json.loads(judge_eval_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            run_log_print(f"  [ERROR] judge_eval.json 解析失败: {e}", file=sys.stderr)
            return None

    merged_result = _merge_eval_results(
        assertions,
        code_eval_result,
        judge_eval_result,
        iteration,
        len(input_dirs),
    )
    eval_path.write_text(json.dumps(merged_result, indent=2, ensure_ascii=False), encoding="utf-8")
    return merged_result


def step_diff_evaluate(
    config: dict,
    input_dirs: list[Path],
    iter_dir: Path,
    run_dir: Path,
) -> None:
    """DIFF_EVALUATE: Teacher 逐段对比标杆输出与 Student 输出。"""
    baseline_dir = run_dir / "baseline"
    diff_eval_path = iter_dir / "diff_eval.json"
    failure_modes_path = run_dir / "failure_modes.json"
    output_dirs = [str(iter_dir / f"output_{i}") for i in range(len(input_dirs))]

    prompt = diff_evaluate_prompt(
        baseline_dir=str(baseline_dir),
        output_dirs=output_dirs,
        diff_eval_path=str(diff_eval_path),
        failure_modes_path=str(failure_modes_path) if failure_modes_path.exists() else "",
    )

    stdout, stderr, rc = run_claude(
        prompt,
        config["teacher"],
        PROJECT_ROOT,
        config["timeout"],
        log_label=f"diff_evaluate_{iter_dir.name}",
    )

    if rc != 0:
        run_log_print(f"  [WARN] diff_evaluate 退出码 {rc}", file=sys.stderr)

    if not diff_eval_path.exists():
        run_log_print("  [WARN] diff_eval.json 未生成", file=sys.stderr)
        return

    # 将 suggested_assertions 自动追加到主 assertions
    try:
        diffs = json.loads(diff_eval_path.read_text(encoding="utf-8"))
        new_assertions = []
        for sample in diffs:
            for d in sample.get("diffs", []):
                sa = d.get("suggested_assertion")
                if sa and sa.get("id"):
                    new_assertions.append(sa)
        if new_assertions:
            _merge_assertions_list(run_dir, new_assertions)
    except (json.JSONDecodeError, KeyError):
        pass


def step_reasoning_diff(
    config: dict,
    input_dirs: list[Path],
    iter_dir: Path,
    run_dir: Path,
) -> None:
    """REASONING_DIFF: Teacher 对比两份 reasoning，诊断 Student 认知偏差。"""
    baseline_dir = run_dir / "baseline"
    reasoning_diff_path = iter_dir / "reasoning_diff.json"

    # 检查 Student 是否产出了 reasoning 文件
    has_reasoning = any((iter_dir / f"reasoning_{i}.md").exists() for i in range(len(input_dirs)))
    if not has_reasoning:
        run_log_print("  [WARN] Student 未产出 reasoning 文件，跳过推理对比", file=sys.stderr)
        return

    prompt = reasoning_diff_prompt(
        baseline_dir=str(baseline_dir),
        iter_dir=str(iter_dir),
        input_count=len(input_dirs),
        reasoning_diff_path=str(reasoning_diff_path),
    )

    stdout, stderr, rc = run_claude(
        prompt,
        config["teacher"],
        PROJECT_ROOT,
        config["timeout"],
        log_label=f"reasoning_diff_{iter_dir.name}",
    )

    if rc != 0:
        run_log_print(f"  [WARN] reasoning_diff 退出码 {rc}", file=sys.stderr)

    if not reasoning_diff_path.exists():
        run_log_print("  [WARN] reasoning_diff.json 未生成", file=sys.stderr)


def step_blind_eval(
    config: dict,
    input_dirs: list[Path],
    iter_dir: Path,
    run_dir: Path,
    iteration: int,
) -> dict | None:
    """BLIND_EVAL: Teacher 不看 assertions，直接对 Student 输出打分。"""
    blind_eval_path = iter_dir / "blind_eval.json"
    failure_modes_path = run_dir / "failure_modes.json"
    output_dirs = [str(iter_dir / f"output_{i}") for i in range(len(input_dirs))]

    prompt = blind_eval_prompt(
        output_dirs=output_dirs,
        blind_eval_path=str(blind_eval_path),
        iteration=iteration,
        failure_modes_path=str(failure_modes_path) if failure_modes_path.exists() else "",
    )

    stdout, stderr, rc = run_claude(
        prompt,
        config["teacher"],
        PROJECT_ROOT,
        config["timeout"],
        log_label=f"blind_eval_iter_{iteration}",
    )

    if rc != 0:
        run_log_print(f"  [WARN] blind_eval 退出码 {rc}", file=sys.stderr)

    if not blind_eval_path.exists():
        run_log_print("  [WARN] blind_eval.json 未生成", file=sys.stderr)
        return None

    try:
        return json.loads(blind_eval_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _is_probably_text_file(path: Path) -> bool:
    """粗略判断文件是否适合按文本读取。"""
    if path.suffix.lower() in TEXT_FILE_EXTENSIONS:
        return True
    try:
        sample = path.read_bytes()[:1024]
    except OSError:
        return False
    return b"\x00" not in sample


def _read_text_file(path: Path) -> str:
    """尽量稳健地读取文本文件。"""
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _collect_output_files(output_dir: Path, relative_path: str = "") -> list[Path]:
    """收集输出目录中的目标文件。"""
    if relative_path:
        target = output_dir / relative_path
        return [target] if target.exists() and target.is_file() else []
    return sorted(p for p in output_dir.rglob("*") if p.is_file())


def _collect_text_blobs(output_dir: Path, relative_path: str = "") -> list[tuple[str, str]]:
    """读取输出目录中的文本内容。"""
    blobs: list[tuple[str, str]] = []
    for path in _collect_output_files(output_dir, relative_path):
        if not _is_probably_text_file(path):
            continue
        text = _read_text_file(path)
        if not text.strip():
            continue
        blobs.append((str(path.relative_to(output_dir)), text))
    return blobs


def _normalize_search_text(text: str, case_sensitive: bool) -> str:
    """根据大小写配置归一化待搜索文本。"""
    return text if case_sensitive else text.lower()


def _count_sentences(text: str) -> int:
    """粗略统计段落句数。"""
    stripped = text.strip()
    if not stripped:
        return 0
    matches = re.findall(r"[。！？!?\.]+", stripped)
    if matches:
        return len(matches)
    return 1


def _evaluate_code_assertion_on_output(assertion: dict, output_dir: Path) -> dict:
    """对单个输出目录执行确定性 assertion 检查。"""
    code_check = assertion.get("code_check", {}) or {}
    check_type = code_check.get("type")
    relative_path = code_check.get("path", "")
    case_sensitive = bool(code_check.get("case_sensitive", False))
    phrases = _normalize_text_list(code_check.get("phrases", []))
    text_blobs = _collect_text_blobs(output_dir, relative_path)
    joined_text = "\n".join(text for _, text in text_blobs)
    haystack = _normalize_search_text(joined_text, case_sensitive)

    def fail(evidence: str, gap: str) -> dict:
        return {
            "id": assertion.get("id", "unknown"),
            "passed": False,
            "evidence": evidence,
            "gap": gap,
            "evaluation_method": "code",
        }

    def passed(evidence: str) -> dict:
        return {
            "id": assertion.get("id", "unknown"),
            "passed": True,
            "evidence": evidence,
            "evaluation_method": "code",
        }

    if check_type == "file_exists":
        target = output_dir / relative_path
        if target.exists() and target.is_file():
            return passed(f"找到目标文件 `{relative_path}`")
        return fail(f"未找到目标文件 `{relative_path}`", f"缺少必需文件 `{relative_path}`")

    if check_type in {"contains_all", "contains_any", "not_contains_any"}:
        if not text_blobs:
            target_desc = relative_path or "输出目录中的文本文件"
            return fail(f"未找到可读取文本：`{target_desc}`", f"无法在 `{target_desc}` 中验证内容要求")
        normalized_phrases = [_normalize_search_text(p, case_sensitive) for p in phrases]
        matches = [phrase for phrase, norm in zip(phrases, normalized_phrases) if norm in haystack]
        if check_type == "contains_all":
            missing = [phrase for phrase in phrases if phrase not in matches]
            if missing:
                return fail(
                    f"已匹配 {len(matches)}/{len(phrases)} 个短语；缺失: {', '.join(missing)}",
                    f"需要明确包含这些短语: {', '.join(missing)}",
                )
            return passed(f"已包含全部短语: {', '.join(matches)}")
        if check_type == "contains_any":
            if matches:
                return passed(f"已包含短语: {', '.join(matches)}")
            return fail(
                f"未命中任一候选短语: {', '.join(phrases)}",
                f"至少应包含以下短语之一: {', '.join(phrases)}",
            )
        forbidden = matches
        if forbidden:
            return fail(
                f"发现禁用短语: {', '.join(forbidden)}",
                f"不应出现以下短语: {', '.join(forbidden)}",
            )
        return passed(f"未发现禁用短语: {', '.join(phrases)}")

    if check_type == "max_sentences_per_paragraph":
        max_sentences = code_check.get("max_sentences")
        if not isinstance(max_sentences, int) or max_sentences <= 0:
            return fail("code_check.max_sentences 非法", "需要提供正整数的 max_sentences")
        if not text_blobs:
            target_desc = relative_path or "输出目录中的文本文件"
            return fail(f"未找到可读取文本：`{target_desc}`", f"无法在 `{target_desc}` 中验证段落句数")
        for rel_path, text in text_blobs:
            paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
            for paragraph in paragraphs:
                sentence_count = _count_sentences(paragraph)
                if sentence_count > max_sentences:
                    snippet = paragraph.replace("\n", " ")[:120]
                    return fail(
                        f"`{rel_path}` 中存在 {sentence_count} 句段落：{snippet}",
                        f"单段句数超过上限 {max_sentences}",
                    )
        target_desc = relative_path or "全部文本文件"
        return passed(f"`{target_desc}` 中所有段落均不超过 {max_sentences} 句")

    if check_type == "no_pattern_match":
        pattern = code_check.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            return fail("code_check.pattern 非法", "需要提供有效的正则 pattern")
        if not text_blobs:
            target_desc = relative_path or "输出目录中的文本文件"
            return fail(f"未找到可读取文本：`{target_desc}`", f"无法在 `{target_desc}` 中验证模式约束")
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            regex = re.compile(pattern, flags)
        except re.error as exc:
            return fail(f"正则表达式非法: {exc}", "请提供合法的 code_check.pattern")
        for rel_path, text in text_blobs:
            match = regex.search(text)
            if match:
                snippet = match.group(0).replace("\n", " ")[:120]
                return fail(
                    f"`{rel_path}` 命中禁用模式 `{pattern}`：{snippet}",
                    f"不应出现匹配模式 `{pattern}` 的内容",
                )
        target_desc = relative_path or "全部文本文件"
        return passed(f"`{target_desc}` 中未命中禁用模式 `{pattern}`")

    return fail(
        f"不支持的 code_check.type: `{check_type}`",
        "该检查类型当前无法用确定性评测执行，请改用 judge 或补充实现",
    )


def _evaluate_code_assertions(assertions: list[dict], output_dirs: list[str]) -> dict:
    """对全部样本执行确定性 assertion 检查。"""
    samples = []
    for i, output_dir in enumerate(output_dirs):
        applicable_assertions = [
            assertion for assertion in assertions
            if _assertion_applies_to_input(assertion, i)
        ]
        results = [
            _evaluate_code_assertion_on_output(assertion, Path(output_dir))
            for assertion in applicable_assertions
        ]
        samples.append({"input_id": i, "results": results})
    return {"samples": samples}

def _summarize_weighted_results(results: list[dict], assertions_by_id: dict[str, dict]) -> tuple[float, float]:
    """按断言权重回算 sample 级 pass_rate。"""
    if not results:
        return 0.0, 0.0
    total = len(results)
    passed = sum(1 for r in results if r.get("passed"))
    total_weight = 0.0
    passed_weight = 0.0
    failed_critical = 0
    for result in results:
        assertion = assertions_by_id.get(result.get("id", ""), {})
        weight = float(assertion.get("weight", 1))
        total_weight += weight
        if result.get("passed"):
            passed_weight += weight
        elif weight >= 3:
            failed_critical += 1
    pass_rate = passed / total if total else 0.0
    weighted = passed_weight / total_weight if total_weight else 0.0
    if failed_critical >= 2:
        weighted = min(weighted, _MULTI_CRITICAL_FAIL_CAP)
    elif failed_critical == 1:
        weighted = min(weighted, _CRITICAL_FAIL_CAP)
    return pass_rate, weighted


def _score_results_subset(
    results: list[dict],
    assertions_by_id: dict[str, dict],
    *,
    layer: str | None = None,
    evaluation_method: str | None = None,
) -> tuple[float, float] | None:
    """按 layer / evaluation_method 过滤后回算分数。"""
    filtered = []
    for result in results:
        assertion = assertions_by_id.get(result.get("id", ""), {})
        if layer is not None and assertion.get("layer", "core") != layer:
            continue
        if evaluation_method is not None and assertion.get("evaluation_method", "judge") != evaluation_method:
            continue
        filtered.append(result)
    if not filtered:
        return None
    return _summarize_weighted_results(filtered, assertions_by_id)


def _artifact_gate_passed(results: list[dict], assertions_by_id: dict[str, dict]) -> bool:
    """artifact 层的 code assertions 必须全部通过。"""
    artifact_code_results = []
    for result in results:
        assertion = assertions_by_id.get(result.get("id", ""), {})
        if assertion.get("layer", "core") != "artifact":
            continue
        if assertion.get("evaluation_method", "judge") != "code":
            continue
        artifact_code_results.append(result)
    return all(r.get("passed", False) for r in artifact_code_results)


def _merge_eval_results(
    assertions: list[dict],
    code_eval_result: dict | None,
    judge_eval_result: dict | None,
    iteration: int,
    sample_count: int,
) -> dict:
    """合并 code / judge 评测结果，并统一回算总分。"""
    assertions_by_id = {a.get("id", ""): a for a in assertions}
    code_samples = {s.get("input_id"): s for s in (code_eval_result or {}).get("samples", [])}
    judge_samples = {s.get("input_id"): s for s in (judge_eval_result or {}).get("samples", [])}
    merged_samples = []
    overall_pass = []
    overall_weighted = []
    overall_core_weighted = []
    overall_scoped_weighted = []
    artifact_gate_all_passed = True

    for input_id in range(sample_count):
        code_results = {
            r.get("id"): r for r in code_samples.get(input_id, {}).get("results", [])
            if isinstance(r, dict)
        }
        judge_results = {
            r.get("id"): r for r in judge_samples.get(input_id, {}).get("results", [])
            if isinstance(r, dict)
        }
        merged_results = []
        for assertion in assertions:
            if not _assertion_applies_to_input(assertion, input_id):
                continue
            assertion_id = assertion.get("id", "")
            result = code_results.get(assertion_id) or judge_results.get(assertion_id)
            if result is None:
                result = {
                    "id": assertion_id,
                    "passed": False,
                    "evidence": "评估结果缺失",
                    "gap": "该检查项未成功完成评测",
                    "evaluation_method": assertion.get("evaluation_method", "judge"),
                }
            merged_results.append(result)

        pass_rate, weighted = _summarize_weighted_results(merged_results, assertions_by_id)
        artifact_gate = _artifact_gate_passed(merged_results, assertions_by_id)
        artifact_scores = _score_results_subset(
            merged_results, assertions_by_id, layer="artifact"
        )
        core_scores = _score_results_subset(
            merged_results, assertions_by_id, layer="core"
        )
        scoped_scores = _score_results_subset(
            merged_results, assertions_by_id, layer="scoped"
        )

        overall_pass.append(pass_rate)
        overall_weighted.append(weighted)
        artifact_gate_all_passed = artifact_gate_all_passed and artifact_gate
        if core_scores is not None:
            overall_core_weighted.append(core_scores[1])
        if scoped_scores is not None:
            overall_scoped_weighted.append(scoped_scores[1])
        merged_samples.append({
            "input_id": input_id,
            "results": merged_results,
            "pass_rate": pass_rate,
            "weighted_pass_rate": weighted,
            "artifact_gate_passed": artifact_gate,
            "artifact_pass_rate": artifact_scores[0] if artifact_scores is not None else None,
            "artifact_weighted_pass_rate": artifact_scores[1] if artifact_scores is not None else None,
            "core_pass_rate": core_scores[0] if core_scores is not None else None,
            "core_weighted_pass_rate": core_scores[1] if core_scores is not None else None,
            "scoped_pass_rate": scoped_scores[0] if scoped_scores is not None else None,
            "scoped_weighted_pass_rate": scoped_scores[1] if scoped_scores is not None else None,
        })

    overall_pass_rate = sum(overall_pass) / len(overall_pass) if overall_pass else 0.0
    overall_weighted_pass_rate = (
        sum(overall_weighted) / len(overall_weighted) if overall_weighted else 0.0
    )
    overall_core_weighted_pass_rate = (
        sum(overall_core_weighted) / len(overall_core_weighted) if overall_core_weighted else 0.0
    )
    overall_scoped_weighted_pass_rate = (
        sum(overall_scoped_weighted) / len(overall_scoped_weighted) if overall_scoped_weighted else 0.0
    )
    return {
        "iteration": iteration,
        "samples": merged_samples,
        "artifact_gate_passed": artifact_gate_all_passed,
        "overall_pass_rate": overall_pass_rate,
        "overall_weighted_pass_rate": overall_weighted_pass_rate,
        "overall_core_weighted_pass_rate": overall_core_weighted_pass_rate,
        "overall_scoped_weighted_pass_rate": overall_scoped_weighted_pass_rate,
    }


def _normalize_text_list(values: list) -> list[str]:
    """规整字符串列表，保留顺序并去重。"""
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        text = " ".join(value.strip().split())
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(text)
    return normalized


def _slugify(text: str, max_len: int = 40) -> str:
    """将文本转成适合作为 assertion id 的 slug。"""
    chars = []
    prev_underscore = False
    for ch in text.lower():
        if ch.isascii() and ch.isalnum():
            chars.append(ch)
            prev_underscore = False
            continue
        if "\u4e00" <= ch <= "\u9fff":
            chars.append(ch)
            prev_underscore = False
            continue
        if not prev_underscore:
            chars.append("_")
            prev_underscore = True
    slug = "".join(chars).strip("_")
    slug = "_".join(part for part in slug.split("_") if part)
    return slug[:max_len] or "dimension"


def _extract_uncovered_dimensions(blind_result: dict | None) -> list[str]:
    """从 blind eval 中提取值得回灌 assertions 的维度。"""
    if not blind_result:
        return []

    dims: list[str] = []
    dims.extend(blind_result.get("uncovered_dimensions", []))
    for sample in blind_result.get("samples", []):
        dims.extend(sample.get("weak_dimensions", []))
        for deduction in sample.get("deductions", []):
            if isinstance(deduction, dict):
                dims.append(deduction.get("dimension", ""))
    return _normalize_text_list(dims)


def _compute_overall_blind_score(blind_result: dict | None) -> float:
    """读取或回算 overall_blind_score。"""
    if not blind_result:
        return 0.0
    score = blind_result.get("overall_blind_score")
    if isinstance(score, (int, float)):
        return float(score)

    samples = blind_result.get("samples", [])
    if not samples:
        return 0.0

    sample_scores = []
    for sample in samples:
        blind_score = sample.get("blind_score")
        max_score = sample.get("max_score", 10)
        if not isinstance(blind_score, (int, float)) or not isinstance(max_score, (int, float)):
            continue
        if max_score <= 0:
            continue
        sample_scores.append(float(blind_score) / float(max_score))

    if not sample_scores:
        return 0.0
    return sum(sample_scores) / len(sample_scores)


def _merge_assertions_list(run_dir: Path, new_assertions: list[dict]) -> None:
    """将一组 assertions 追加到主 assertions.json。"""
    master_path = run_dir / "assertions.json"
    try:
        existing = _load_assertions(master_path)
    except (json.JSONDecodeError, FileNotFoundError):
        return

    existing_ids = {a["id"] for a in existing}
    for a in new_assertions:
        normalized = _normalize_assertion(a)
        if normalized.get("id") and normalized["id"] not in existing_ids:
            existing.append(normalized)
            existing_ids.add(normalized["id"])

    master_path.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")


def _build_knowledge_candidates(iter_dir: Path) -> Path | None:
    """汇总 diff / reasoning diff 中最值得写回 Skill 的知识候选。"""
    sections: list[str] = []

    diff_path = iter_dir / "diff_eval.json"
    if diff_path.exists():
        try:
            diff_data = json.loads(diff_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            diff_data = []
        diff_lines = ["# Knowledge Candidates", "", "## From diff_eval"]
        added = 0
        for sample in diff_data:
            input_id = sample.get("input_id")
            for diff in sample.get("diffs", [])[:5]:
                pattern = diff.get("teacher_pattern", "").strip()
                gap = diff.get("gap", "").strip()
                suggested = diff.get("suggested_assertion", {})
                if not pattern and not gap and not suggested:
                    continue
                diff_lines.append(f"- input_{input_id}: {pattern or gap}")
                if gap:
                    diff_lines.append(f"  gap: {gap}")
                if isinstance(suggested, dict) and suggested.get("check"):
                    diff_lines.append(f"  suggested_assertion: {suggested.get('id', 'unknown')} — {suggested['check']}")
                added += 1
        if added:
            sections.append("\n".join(diff_lines))

    reasoning_path = iter_dir / "reasoning_diff.json"
    if reasoning_path.exists():
        try:
            reasoning_data = json.loads(reasoning_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            reasoning_data = []
        reasoning_lines = ["## From reasoning_diff"]
        added = 0
        for sample in reasoning_data:
            input_id = sample.get("input_id")
            for gap in sample.get("reasoning_gaps", [])[:5]:
                topic = gap.get("topic", "").strip()
                gap_type = gap.get("gap_type", "").strip()
                fix_strategy = gap.get("fix_strategy", "").strip()
                skill_patch = gap.get("skill_patch", "").strip()
                if not any([topic, gap_type, fix_strategy, skill_patch]):
                    continue
                title = f"- input_{input_id}: {topic}" if topic else f"- input_{input_id}: reasoning gap"
                if gap_type:
                    title += f" [{gap_type}]"
                reasoning_lines.append(title)
                if fix_strategy:
                    reasoning_lines.append(f"  fix_strategy: {fix_strategy}")
                if skill_patch:
                    compact_patch = " ".join(line.strip() for line in skill_patch.splitlines() if line.strip())
                    reasoning_lines.append(f"  skill_patch: {compact_patch}")
                added += 1
        if added:
            sections.append("\n".join(reasoning_lines))

    if not sections:
        return None

    knowledge_path = iter_dir / "knowledge_candidates.md"
    knowledge_path.write_text("\n\n".join(sections) + "\n", encoding="utf-8")
    return knowledge_path


def step_score(
    eval_result: dict | None,
    best_core_score: float,
    best_skill: str,
    current_skill: str,
) -> tuple[float, str, str]:
    """SCORE: artifact gate + core score 决定保留或丢弃。"""
    if eval_result is None:
        return best_core_score, best_skill, "discard"

    artifact_gate_passed = bool(eval_result.get("artifact_gate_passed", False))
    core_score = float(eval_result.get("overall_core_weighted_pass_rate", 0.0))

    if not artifact_gate_passed:
        return best_core_score, best_skill, "discard"
    if core_score > best_core_score:
        return core_score, current_skill, "keep"
    return best_core_score, best_skill, "discard"


def step_optimize(
    config: dict,
    installed_skill_dir: Path,
    iter_dir: Path,
    run_dir: Path,
    iteration: int,
) -> str | None:
    """OPTIMIZE: Teacher 迁移知识到 Skill。返回新 skill 内容或 None。"""
    output_skill_dir = iter_dir / "skill"
    output_skill_path = output_skill_dir / "SKILL.md"
    change_path = iter_dir / "change.md"
    new_assertions_path = iter_dir / "new_assertions.json"
    knowledge_path = _build_knowledge_candidates(iter_dir)

    prompt = optimize_prompt(
        skill_dir=str(installed_skill_dir),
        eval_path=str(iter_dir / "eval.json"),
        reasoning_dir=str(run_dir / "baseline"),
        output_skill_dir=str(output_skill_dir),
        change_path=str(change_path),
        new_assertions_path=str(new_assertions_path),
        iteration=iteration,
        baseline_dir=str(run_dir / "baseline"),
        diff_eval_path=str(iter_dir / "diff_eval.json") if (iter_dir / "diff_eval.json").exists() else "",
        reasoning_diff_path=str(iter_dir / "reasoning_diff.json") if (iter_dir / "reasoning_diff.json").exists() else "",
        knowledge_path=str(knowledge_path) if knowledge_path else "",
        blind_eval_path=str(iter_dir / "blind_eval.json") if (iter_dir / "blind_eval.json").exists() else "",
        failure_modes_path=str(run_dir / "failure_modes.json") if (run_dir / "failure_modes.json").exists() else "",
    )

    stdout, stderr, rc = run_claude(
        prompt,
        config["teacher"],
        PROJECT_ROOT,
        config["timeout"],
        log_label=f"optimize_iter_{iteration}",
    )

    if rc != 0:
        run_log_print(f"  [WARN] optimize 退出码 {rc}", file=sys.stderr)

    if not output_skill_path.exists():
        run_log_print("  [ERROR] optimize 未生成新的 skill 目录或其中缺少 SKILL.md", file=sys.stderr)
        return None

    new_skill = output_skill_path.read_text(encoding="utf-8")

    # 校验 frontmatter 未被破坏
    if not new_skill.strip().startswith("---"):
        run_log_print("  [WARN] 新 skill 缺少 YAML frontmatter，拒绝采用", file=sys.stderr)
        return None

    # 更新已安装的整套 skill 目录
    _replace_dir(output_skill_dir, installed_skill_dir)

    # 合并新 assertions
    _merge_assertions(run_dir, new_assertions_path)

    return new_skill


def _merge_assertions(run_dir: Path, new_path: Path) -> None:
    """将新 assertions 追加到主 assertions.json（只增不减）。"""
    if not new_path.exists():
        return

    master_path = run_dir / "assertions.json"
    try:
        new = json.loads(new_path.read_text(encoding="utf-8"))
        existing = _load_assertions(master_path)
    except (json.JSONDecodeError, FileNotFoundError):
        return

    existing_ids = {a["id"] for a in existing}
    for a in new:
        normalized = _normalize_assertion(a)
        if normalized.get("id") and normalized["id"] not in existing_ids:
            existing.append(normalized)
            existing_ids.add(normalized["id"])

    master_path.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")


# ─── 收敛 & 日志 ──────────────────────────────────────────


def _is_plateau(history: list[dict], config: dict) -> bool:
    """检查是否处于 plateau 状态（不触发终止）。"""
    n = config["plateau_rounds"]
    if len(history) < n:
        return False
    recent = history[-n:]
    return all(h["best_score"] == recent[0]["best_score"] for h in recent)


def should_stop(
    best_score: float,
    history: list[dict],
    config: dict,
    optimize_rounds_completed: int = 0,
    blind_eval_override: bool = False,
) -> tuple[bool, str]:
    """检查终止条件。blind_eval_override=True 时覆盖 plateau 判定。"""
    if len(history) < config.get("min_iterations_before_stop", 1):
        return False, ""
    if optimize_rounds_completed < config.get("min_optimize_rounds", 0):
        return False, ""
    if best_score >= config["target_pass_rate"]:
        return True, "target_reached"
    if len(history) >= config["max_iterations"]:
        return True, "max_iterations"
    n = config["plateau_rounds"]
    if len(history) >= n:
        recent = history[-n:]
        if all(h["best_score"] == recent[0]["best_score"] for h in recent):
            if blind_eval_override:
                return False, ""
            return True, "plateau"
    return False, ""


def append_log(log_path: Path, entry: dict) -> None:
    """追加一条记录到 log.json。"""
    if log_path.exists():
        log = json.loads(log_path.read_text(encoding="utf-8"))
    else:
        log = []
    log.append(entry)
    log_path.write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8")


def _extract_failed(eval_result: dict | None) -> list[str]:
    """从 eval 结果提取 failed assertion ids。"""
    if not eval_result:
        return []
    failed = []
    for sample in eval_result.get("samples", []):
        for r in sample.get("results", []):
            if not r.get("passed", True):
                fid = r.get("id", "unknown")
                if fid not in failed:
                    failed.append(fid)
    return failed


def generate_report(run_dir: Path) -> None:
    """生成 report.md。"""
    log_path = run_dir / "log.json"
    if not log_path.exists():
        return

    log = json.loads(log_path.read_text(encoding="utf-8"))
    if not log:
        return

    initial = log[0].get("core_score", log[0]["score"])
    final_best = log[-1]["best_score"]
    total_iters = len(log)

    # assertions 数量
    assertions_path = run_dir / "assertions.json"
    if assertions_path.exists():
        assertions = _load_assertions(assertions_path)
        assertions_count = len(assertions)
        code_assertions_count = sum(1 for a in assertions if a.get("evaluation_method") == "code")
        judge_assertions_count = assertions_count - code_assertions_count
    else:
        assertions_count = "?"
        code_assertions_count = "?"
        judge_assertions_count = "?"

    failure_modes_path = run_dir / "failure_modes.json"
    if failure_modes_path.exists():
        failure_modes = json.loads(failure_modes_path.read_text(encoding="utf-8"))
        failure_modes_count = len(failure_modes)
    else:
        failure_modes_count = "?"

    lines = [
        "# Skill Distiller Report",
        "",
        "## 结果",
        "",
        f"- 初始 core pass_rate: {initial:.2f}",
        f"- 最终 core pass_rate: {final_best:.2f}",
        f"- 提升: +{final_best - initial:.2f}",
        f"- 迭代轮次: {total_iters}",
        f"- Assertions 数量: {assertions_count}",
        f"- 其中 code assertions: {code_assertions_count}",
        f"- 其中 judge assertions: {judge_assertions_count}",
        f"- Failure modes 数量: {failure_modes_count}",
        f"- 终止原因: {log[-1].get('stop_reason', 'N/A')}",
        "",
        "## 收敛曲线",
        "",
    ]

    bar_width = 40
    for entry in log:
        score = entry.get("core_score", entry["score"])
        filled = int(score * bar_width)
        bar = "█" * filled + "░" * (bar_width - filled)
        action = "✓ keep" if entry["action"] == "keep" else "✗ discard"
        lines.append(f"iter {entry['iteration']:2d}: {bar} {score:.2f} {action}")

    lines += [
        "",
        "## 知识迁移记录",
        "",
        "| 轮次 | 动作 | 分数 | 盲评 | 覆盖缺口 | 失败项 |",
        "|------|------|------|------|----------|--------|",
    ]
    for entry in log:
        failed_str = ", ".join(entry.get("failed", [])[:3])
        if len(entry.get("failed", [])) > 3:
            failed_str += "..."
        blind_str = f"{entry['blind_score']:.2f}" if "blind_score" in entry else "-"
        gap_str = f"{entry['coverage_gap']:.2f}" if "coverage_gap" in entry else "-"
        lines.append(
            f"| {entry['iteration']} | {entry['action']} | {entry.get('core_score', entry['score']):.2f} | {blind_str} | {gap_str} | {failed_str} |"
        )

    # 最终未通过项
    last_failed = log[-1].get("failed", [])
    if last_failed:
        lines += ["", "## 仍未通过的 Assertions", ""]
        for fid in last_failed:
            lines.append(f"- {fid}")

    # 盲评覆盖率趋势
    blind_entries = [e for e in log if "blind_score" in e]
    if blind_entries:
        lines += ["", "## 盲评覆盖率趋势", ""]
        for entry in blind_entries:
            lines.append(
                f"- iter {entry['iteration']}: core={entry.get('core_score', entry['score']):.2f}, "
                f"blind={entry['blind_score']:.2f}, gap={entry['coverage_gap']:.2f}"
            )
            dims = entry.get("uncovered_dimensions", [])
            if dims:
                lines.append(f"  未覆盖维度: {', '.join(dims[:5])}")

    report_path = run_dir / "report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")


# ─── Finalize ──────────────────────────────────────────────


def finalize(
    best_skill_dir: Path,
    skill_name: str,
    final_root: Path,
    run_dir: Path,
) -> None:
    """复制最佳 skill 到 Final/，生成报告。"""
    final_dir = final_root / skill_name
    _replace_dir(best_skill_dir, final_dir)

    generate_report(run_dir)


# ─── 主入口 ────────────────────────────────────────────────


def main():
    config_path = Path(sys.argv[1]) if len(sys.argv) > 1 else PROJECT_ROOT / "config.json"
    config = load_config(config_path)

    skill_name, skill_folder = discover_skill(PROJECT_ROOT / "SKILL")
    input_dirs = discover_inputs(PROJECT_ROOT / "Input")

    # 初始化 workspace + 文件日志（distiller.log）
    run_dir, installed_skill_dir = setup_workspace(
        PROJECT_ROOT / "Workspace", skill_name, skill_folder
    )
    init_run_log(run_dir)
    run_log_print(f"[INFO] Skill: {skill_name}")
    run_log_print(f"[INFO] Inputs: {len(input_dirs)} case(s)")
    run_log_print(f"[INFO] Workspace: {run_dir}")
    run_log_print(f"[INFO] 运行日志文件: {run_dir / 'distiller.log'}")

    installed_skill_path = installed_skill_dir / "SKILL.md"
    skill_content = installed_skill_path.read_text(encoding="utf-8")
    best_skill_dir = run_dir / "best_skill"
    _replace_dir(installed_skill_dir, best_skill_dir)

    # ── BASELINE ──
    run_log_print(f"\n[BASELINE] Teacher 执行 {skill_name}...")
    t0 = time.time()
    step_baseline(config, skill_content, input_dirs, run_dir)
    run_log_print(f"[BASELINE] 完成 ({time.time() - t0:.0f}s)")

    # ── 迭代循环 ──
    best_core_score = 0.0
    best_skill = skill_content
    history: list[dict] = []
    log_path = run_dir / "log.json"
    blind_eval_interval = config.get("blind_eval_interval", 3)
    coverage_gap_threshold = config.get("coverage_gap_threshold", 0.2)
    optimize_rounds_completed = 0

    for iteration in range(config["max_iterations"]):
        iter_dir = run_dir / f"iter_{iteration}"
        iter_dir.mkdir(exist_ok=True)

        # EXECUTE
        run_log_print(f"\n[ITER {iteration}] Student 执行中...")
        t0 = time.time()
        step_execute(config, skill_name, input_dirs, iter_dir)
        run_log_print(f"  执行完成 ({time.time() - t0:.0f}s)")

        # EVALUATE
        run_log_print(f"[ITER {iteration}] Teacher 评估中...")
        t0 = time.time()
        eval_result = step_evaluate(config, input_dirs, iter_dir, run_dir, iteration)
        run_log_print(f"  评估完成 ({time.time() - t0:.0f}s)")

        score = eval_result.get("overall_weighted_pass_rate", 0.0) if eval_result else 0.0
        core_score = eval_result.get("overall_core_weighted_pass_rate", 0.0) if eval_result else 0.0
        artifact_gate_passed = bool(eval_result.get("artifact_gate_passed", False)) if eval_result else False

        # DIFF_EVALUATE — 对比信号
        run_log_print(f"[ITER {iteration}] Teacher 对比评估中...")
        t0 = time.time()
        step_diff_evaluate(config, input_dirs, iter_dir, run_dir)
        run_log_print(f"  对比评估完成 ({time.time() - t0:.0f}s)")

        # REASONING_DIFF — Student 自省
        run_log_print(f"[ITER {iteration}] Teacher 推理对比中...")
        t0 = time.time()
        step_reasoning_diff(config, input_dirs, iter_dir, run_dir)
        run_log_print(f"  推理对比完成 ({time.time() - t0:.0f}s)")

        # SCORE
        best_core_score, active_skill, action = step_score(
            eval_result, best_core_score, best_skill, skill_content
        )

        if action == "keep":
            best_skill = skill_content
            _replace_dir(installed_skill_dir, best_skill_dir)
            run_log_print(f"[ITER {iteration}] ✓ core={core_score:.2f} (new best)")
        else:
            skill_content = best_skill
            _replace_dir(best_skill_dir, installed_skill_dir)
            installed_skill_path = installed_skill_dir / "SKILL.md"
            if not artifact_gate_passed:
                run_log_print(f"[ITER {iteration}] ✗ artifact gate failed, rollback")
            else:
                run_log_print(f"[ITER {iteration}] ✗ core={core_score:.2f} ≤ best={best_core_score:.2f}, rollback")

        # 日志
        failed = _extract_failed(eval_result)
        entry = {
            "iteration": iteration,
            "score": score,
            "core_score": core_score,
            "best_score": best_core_score,
            "action": action,
            "artifact_gate_passed": artifact_gate_passed,
            "failed": failed,
        }

        # BLIND_EVAL — 覆盖率检查（每 N 轮 or plateau 时触发）
        blind_eval_override = False
        is_plateau = _is_plateau(history + [entry], config)
        should_blind_eval = (
            (iteration + 1) % blind_eval_interval == 0
            or is_plateau
        )

        if should_blind_eval:
            run_log_print(f"[ITER {iteration}] Teacher 盲评中...")
            t0 = time.time()
            blind_result = step_blind_eval(config, input_dirs, iter_dir, run_dir, iteration)
            run_log_print(f"  盲评完成 ({time.time() - t0:.0f}s)")

            if blind_result:
                blind_score = _compute_overall_blind_score(blind_result)
                coverage_gap = core_score - blind_score
                uncovered = _extract_uncovered_dimensions(blind_result)
                entry["blind_score"] = blind_score
                entry["coverage_gap"] = coverage_gap
                if uncovered:
                    entry["uncovered_dimensions"] = uncovered
                run_log_print(f"  core pass_rate: {core_score:.2f}, blind_score: {blind_score:.2f}, gap: {coverage_gap:.2f}")

                if coverage_gap > coverage_gap_threshold:
                    run_log_print(f"  [WARN] 覆盖率缺口 {coverage_gap:.2f} > {coverage_gap_threshold}，覆盖 plateau 判定")
                    blind_eval_override = True
                    # 将 uncovered_dimensions 转化为新 assertions
                    if uncovered:
                        new_assertions = []
                        for dim in uncovered:
                            new_assertions.append({
                                "id": f"blind_{_slugify(dim)}",
                                "check": dim,
                                "weight": 2,
                                "source": "blind_eval",
                            })
                        _merge_assertions_list(run_dir, new_assertions)

        # 收敛检查
        history.append(entry)
        stop, reason = should_stop(
            best_core_score,
            history,
            config,
            optimize_rounds_completed=optimize_rounds_completed,
            blind_eval_override=blind_eval_override,
        )
        if stop:
            entry["stop_reason"] = reason
            append_log(log_path, entry)
            run_log_print(f"[STOP] {reason}")
            break

        append_log(log_path, entry)

        # OPTIMIZE
        run_log_print(f"[ITER {iteration}] Teacher 优化中...")
        t0 = time.time()
        new_skill = step_optimize(config, installed_skill_dir, iter_dir, run_dir, iteration)
        run_log_print(f"  优化完成 ({time.time() - t0:.0f}s)")
        optimize_rounds_completed += 1

        if new_skill:
            skill_content = new_skill
        else:
            run_log_print("  [WARN] 优化失败，保持当前 skill 不变")

    # ── FINALIZE ──
    run_log_print(f"\n[FINALIZE]")
    finalize(best_skill_dir, skill_name, PROJECT_ROOT / "Final", run_dir)
    run_log_print(f"  最终 core pass_rate: {best_core_score:.2f}")
    run_log_print(f"  Final skill: Final/{skill_name}/SKILL.md")
    run_log_print(f"  Report: {run_dir}/report.md")


if __name__ == "__main__":
    main()
