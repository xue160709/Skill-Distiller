"""Skill Distiller — 自动化知识蒸馏。

Usage:
    python distiller.py
    python distiller.py path/to/config.json
"""

import json
import os
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


# ─── 配置 & 发现 ───────────────────────────────────────────


def load_config(path: Path) -> dict:
    """读取并校验 config.json。"""
    cfg = json.loads(path.read_text(encoding="utf-8"))
    for role in ("teacher", "student"):
        env = cfg.get(role, {}).get("env", {})
        if not env.get("ANTHROPIC_MODEL"):
            sys.exit(f"config.json: {role}.env.ANTHROPIC_MODEL 不能为空")
    cfg.setdefault("max_iterations", 15)
    cfg.setdefault("target_pass_rate", 0.90)
    cfg.setdefault("plateau_rounds", 3)
    cfg.setdefault("timeout", 600)
    cfg.setdefault("blind_eval_interval", 3)
    cfg.setdefault("coverage_gap_threshold", 0.2)
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
    allowed_tools: str = "Read,Write,Edit,Bash",
) -> tuple[str, str, int]:
    """调用 claude -p 子进程。"""
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    env.update(env_config.get("env", {}))

    cmd = [
        "claude", "-p",
        "--output-format", "text",
        "--allowedTools", allowed_tools,
    ]

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
    return proc.stdout, proc.stderr, proc.returncode


# ─── Workspace ─────────────────────────────────────────────


def setup_workspace(
    workspace_root: Path,
    skill_name: str,
    skill_folder: Path,
) -> tuple[Path, Path]:
    """初始化本次运行目录，安装 skill。

    返回: (run_dir, installed_skill_md_path)
    """
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    run_dir = workspace_root / timestamp
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "baseline").mkdir(exist_ok=True)

    # 备份原始 skill
    src_skill_md = skill_folder / "SKILL.md"
    shutil.copy2(src_skill_md, run_dir / "skill_v0.md")

    # 安装 skill 到 .claude/skills/
    installed_dir = PROJECT_ROOT / ".claude" / "skills" / skill_name
    if installed_dir.exists():
        shutil.rmtree(installed_dir)
    shutil.copytree(skill_folder, installed_dir)

    return run_dir, installed_dir / "SKILL.md"


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

    prompt = baseline_prompt(
        skill_content=skill_content,
        input_dirs=[str(d) for d in input_dirs],
        baseline_dir=str(baseline_dir),
        assertions_path=str(assertions_path),
    )

    stdout, stderr, rc = run_claude(
        prompt, config["teacher"], PROJECT_ROOT, config["timeout"]
    )

    if rc != 0:
        print(f"  [WARN] baseline claude -p 退出码 {rc}", file=sys.stderr)
        if stderr.strip():
            print(f"  stderr: {stderr[:500]}", file=sys.stderr)

    if not assertions_path.exists():
        sys.exit("BASELINE 失败: assertions.json 未生成")


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
            prompt, config["student"], PROJECT_ROOT, config["timeout"]
        )

        if rc != 0:
            print(f"  [WARN] execute input_{i} 退出码 {rc}", file=sys.stderr)


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

    output_dirs = [str(iter_dir / f"output_{i}") for i in range(len(input_dirs))]

    prompt = evaluate_prompt(
        assertions_path=str(assertions_path),
        output_dirs=output_dirs,
        eval_path=str(eval_path),
        iteration=iteration,
    )

    stdout, stderr, rc = run_claude(
        prompt, config["teacher"], PROJECT_ROOT, config["timeout"]
    )

    if rc != 0:
        print(f"  [WARN] evaluate 退出码 {rc}", file=sys.stderr)

    if not eval_path.exists():
        print("  [ERROR] eval.json 未生成", file=sys.stderr)
        return None

    try:
        return json.loads(eval_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"  [ERROR] eval.json 解析失败: {e}", file=sys.stderr)
        return None


def step_diff_evaluate(
    config: dict,
    input_dirs: list[Path],
    iter_dir: Path,
    run_dir: Path,
) -> None:
    """DIFF_EVALUATE: Teacher 逐段对比标杆输出与 Student 输出。"""
    baseline_dir = run_dir / "baseline"
    diff_eval_path = iter_dir / "diff_eval.json"
    output_dirs = [str(iter_dir / f"output_{i}") for i in range(len(input_dirs))]

    prompt = diff_evaluate_prompt(
        baseline_dir=str(baseline_dir),
        output_dirs=output_dirs,
        diff_eval_path=str(diff_eval_path),
    )

    stdout, stderr, rc = run_claude(
        prompt, config["teacher"], PROJECT_ROOT, config["timeout"]
    )

    if rc != 0:
        print(f"  [WARN] diff_evaluate 退出码 {rc}", file=sys.stderr)

    if not diff_eval_path.exists():
        print("  [WARN] diff_eval.json 未生成", file=sys.stderr)
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
        print("  [WARN] Student 未产出 reasoning 文件，跳过推理对比", file=sys.stderr)
        return

    prompt = reasoning_diff_prompt(
        baseline_dir=str(baseline_dir),
        iter_dir=str(iter_dir),
        input_count=len(input_dirs),
        reasoning_diff_path=str(reasoning_diff_path),
    )

    stdout, stderr, rc = run_claude(
        prompt, config["teacher"], PROJECT_ROOT, config["timeout"]
    )

    if rc != 0:
        print(f"  [WARN] reasoning_diff 退出码 {rc}", file=sys.stderr)

    if not reasoning_diff_path.exists():
        print("  [WARN] reasoning_diff.json 未生成", file=sys.stderr)


def step_blind_eval(
    config: dict,
    input_dirs: list[Path],
    iter_dir: Path,
    run_dir: Path,
    iteration: int,
) -> dict | None:
    """BLIND_EVAL: Teacher 不看 assertions，直接对 Student 输出打分。"""
    blind_eval_path = iter_dir / "blind_eval.json"
    output_dirs = [str(iter_dir / f"output_{i}") for i in range(len(input_dirs))]

    prompt = blind_eval_prompt(
        output_dirs=output_dirs,
        blind_eval_path=str(blind_eval_path),
        iteration=iteration,
    )

    stdout, stderr, rc = run_claude(
        prompt, config["teacher"], PROJECT_ROOT, config["timeout"]
    )

    if rc != 0:
        print(f"  [WARN] blind_eval 退出码 {rc}", file=sys.stderr)

    if not blind_eval_path.exists():
        print("  [WARN] blind_eval.json 未生成", file=sys.stderr)
        return None

    try:
        return json.loads(blind_eval_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _merge_assertions_list(run_dir: Path, new_assertions: list[dict]) -> None:
    """将一组 assertions 追加到主 assertions.json。"""
    master_path = run_dir / "assertions.json"
    try:
        existing = json.loads(master_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, FileNotFoundError):
        return

    existing_ids = {a["id"] for a in existing}
    for a in new_assertions:
        if a.get("id") and a["id"] not in existing_ids:
            existing.append(a)
            existing_ids.add(a["id"])

    master_path.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")


def step_score(
    eval_result: dict | None,
    best_score: float,
    best_skill: str,
    current_skill: str,
) -> tuple[float, str, str]:
    """SCORE: 比较 pass_rate，保留或丢弃。"""
    if eval_result is None:
        return best_score, best_skill, "discard"

    score = eval_result.get("overall_weighted_pass_rate", 0.0)

    if score > best_score:
        return score, current_skill, "keep"
    return best_score, best_skill, "discard"


def step_optimize(
    config: dict,
    installed_skill_path: Path,
    iter_dir: Path,
    run_dir: Path,
    iteration: int,
) -> str | None:
    """OPTIMIZE: Teacher 迁移知识到 Skill。返回新 skill 内容或 None。"""
    output_skill_path = iter_dir / "skill.md"
    change_path = iter_dir / "change.md"
    new_assertions_path = iter_dir / "new_assertions.json"

    prompt = optimize_prompt(
        skill_path=str(installed_skill_path),
        eval_path=str(iter_dir / "eval.json"),
        reasoning_dir=str(run_dir / "baseline"),
        output_skill_path=str(output_skill_path),
        change_path=str(change_path),
        new_assertions_path=str(new_assertions_path),
        iteration=iteration,
        baseline_dir=str(run_dir / "baseline"),
        diff_eval_path=str(iter_dir / "diff_eval.json") if (iter_dir / "diff_eval.json").exists() else "",
        reasoning_diff_path=str(iter_dir / "reasoning_diff.json") if (iter_dir / "reasoning_diff.json").exists() else "",
    )

    stdout, stderr, rc = run_claude(
        prompt, config["teacher"], PROJECT_ROOT, config["timeout"]
    )

    if rc != 0:
        print(f"  [WARN] optimize 退出码 {rc}", file=sys.stderr)

    if not output_skill_path.exists():
        print("  [ERROR] optimize 未生成新 skill.md", file=sys.stderr)
        return None

    new_skill = output_skill_path.read_text(encoding="utf-8")

    # 校验 frontmatter 未被破坏
    if not new_skill.strip().startswith("---"):
        print("  [WARN] 新 skill 缺少 YAML frontmatter，拒绝采用", file=sys.stderr)
        return None

    # 更新已安装的 skill
    installed_skill_path.write_text(new_skill, encoding="utf-8")

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
        existing = json.loads(master_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, FileNotFoundError):
        return

    existing_ids = {a["id"] for a in existing}
    for a in new:
        if a.get("id") and a["id"] not in existing_ids:
            existing.append(a)
            existing_ids.add(a["id"])

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
    blind_eval_override: bool = False,
) -> tuple[bool, str]:
    """检查终止条件。blind_eval_override=True 时覆盖 plateau 判定。"""
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

    initial = log[0]["score"]
    final_best = log[-1]["best_score"]
    total_iters = len(log)

    # assertions 数量
    assertions_path = run_dir / "assertions.json"
    if assertions_path.exists():
        assertions = json.loads(assertions_path.read_text(encoding="utf-8"))
        assertions_count = len(assertions)
    else:
        assertions_count = "?"

    lines = [
        "# Skill Distiller Report",
        "",
        "## 结果",
        "",
        f"- 初始 pass_rate: {initial:.2f}",
        f"- 最终 pass_rate: {final_best:.2f}",
        f"- 提升: +{final_best - initial:.2f}",
        f"- 迭代轮次: {total_iters}",
        f"- Assertions 数量: {assertions_count}",
        f"- 终止原因: {log[-1].get('stop_reason', 'N/A')}",
        "",
        "## 收敛曲线",
        "",
    ]

    bar_width = 40
    for entry in log:
        score = entry["score"]
        best = entry["best_score"]
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
            f"| {entry['iteration']} | {entry['action']} | {entry['score']:.2f} | {blind_str} | {gap_str} | {failed_str} |"
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
                f"- iter {entry['iteration']}: assertions={entry['score']:.2f}, "
                f"blind={entry['blind_score']:.2f}, gap={entry['coverage_gap']:.2f}"
            )

    report_path = run_dir / "report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")


# ─── Finalize ──────────────────────────────────────────────


def finalize(
    best_skill: str,
    skill_name: str,
    skill_folder: Path,
    final_root: Path,
    run_dir: Path,
) -> None:
    """复制最佳 skill 到 Final/，生成报告。"""
    final_dir = final_root / skill_name
    if final_dir.exists():
        shutil.rmtree(final_dir)
    shutil.copytree(skill_folder, final_dir)
    (final_dir / "SKILL.md").write_text(best_skill, encoding="utf-8")

    generate_report(run_dir)


# ─── 主入口 ────────────────────────────────────────────────


def main():
    config_path = Path(sys.argv[1]) if len(sys.argv) > 1 else PROJECT_ROOT / "config.json"
    config = load_config(config_path)

    skill_name, skill_folder = discover_skill(PROJECT_ROOT / "SKILL")
    input_dirs = discover_inputs(PROJECT_ROOT / "Input")

    print(f"[INFO] Skill: {skill_name}")
    print(f"[INFO] Inputs: {len(input_dirs)} case(s)")

    # 初始化 workspace
    run_dir, installed_skill_path = setup_workspace(
        PROJECT_ROOT / "Workspace", skill_name, skill_folder
    )
    print(f"[INFO] Workspace: {run_dir}")

    skill_content = installed_skill_path.read_text(encoding="utf-8")

    # ── BASELINE ──
    print(f"\n[BASELINE] Teacher 执行 {skill_name}...")
    t0 = time.time()
    step_baseline(config, skill_content, input_dirs, run_dir)
    print(f"[BASELINE] 完成 ({time.time() - t0:.0f}s)")

    # ── 迭代循环 ──
    best_score = 0.0
    best_skill = skill_content
    history: list[dict] = []
    log_path = run_dir / "log.json"
    blind_eval_interval = config.get("blind_eval_interval", 3)
    coverage_gap_threshold = config.get("coverage_gap_threshold", 0.2)

    for iteration in range(config["max_iterations"]):
        iter_dir = run_dir / f"iter_{iteration}"
        iter_dir.mkdir(exist_ok=True)

        # EXECUTE
        print(f"\n[ITER {iteration}] Student 执行中...")
        t0 = time.time()
        step_execute(config, skill_name, input_dirs, iter_dir)
        print(f"  执行完成 ({time.time() - t0:.0f}s)")

        # EVALUATE
        print(f"[ITER {iteration}] Teacher 评估中...")
        t0 = time.time()
        eval_result = step_evaluate(config, input_dirs, iter_dir, run_dir, iteration)
        print(f"  评估完成 ({time.time() - t0:.0f}s)")

        score = eval_result.get("overall_weighted_pass_rate", 0.0) if eval_result else 0.0

        # DIFF_EVALUATE — 对比信号
        print(f"[ITER {iteration}] Teacher 对比评估中...")
        t0 = time.time()
        step_diff_evaluate(config, input_dirs, iter_dir, run_dir)
        print(f"  对比评估完成 ({time.time() - t0:.0f}s)")

        # REASONING_DIFF — Student 自省
        print(f"[ITER {iteration}] Teacher 推理对比中...")
        t0 = time.time()
        step_reasoning_diff(config, input_dirs, iter_dir, run_dir)
        print(f"  推理对比完成 ({time.time() - t0:.0f}s)")

        # SCORE
        best_score, active_skill, action = step_score(
            eval_result, best_score, best_skill, skill_content
        )

        if action == "keep":
            best_skill = skill_content
            print(f"[ITER {iteration}] ✓ {score:.2f} (new best)")
        else:
            skill_content = best_skill
            installed_skill_path.write_text(best_skill, encoding="utf-8")
            print(f"[ITER {iteration}] ✗ {score:.2f} ≤ {best_score:.2f}, rollback")

        # 日志
        failed = _extract_failed(eval_result)
        entry = {
            "iteration": iteration,
            "score": score,
            "best_score": best_score,
            "action": action,
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
            print(f"[ITER {iteration}] Teacher 盲评中...")
            t0 = time.time()
            blind_result = step_blind_eval(config, input_dirs, iter_dir, run_dir, iteration)
            print(f"  盲评完成 ({time.time() - t0:.0f}s)")

            if blind_result:
                blind_score = blind_result.get("overall_blind_score", 0.0)
                coverage_gap = score - blind_score
                entry["blind_score"] = blind_score
                entry["coverage_gap"] = coverage_gap
                print(f"  assertions pass_rate: {score:.2f}, blind_score: {blind_score:.2f}, gap: {coverage_gap:.2f}")

                if coverage_gap > coverage_gap_threshold:
                    print(f"  [WARN] 覆盖率缺口 {coverage_gap:.2f} > {coverage_gap_threshold}，覆盖 plateau 判定")
                    blind_eval_override = True
                    # 将 uncovered_dimensions 转化为新 assertions
                    uncovered = blind_result.get("uncovered_dimensions", [])
                    if uncovered:
                        new_assertions = []
                        for dim in uncovered:
                            new_assertions.append({
                                "id": f"blind_{dim.replace(' ', '_').lower()[:40]}",
                                "check": dim,
                                "weight": 2,
                            })
                        _merge_assertions_list(run_dir, new_assertions)

        # 收敛检查
        history.append(entry)
        stop, reason = should_stop(best_score, history, config, blind_eval_override)
        if stop:
            entry["stop_reason"] = reason
            append_log(log_path, entry)
            print(f"[STOP] {reason}")
            break

        append_log(log_path, entry)

        # OPTIMIZE
        print(f"[ITER {iteration}] Teacher 优化中...")
        t0 = time.time()
        new_skill = step_optimize(config, installed_skill_path, iter_dir, run_dir, iteration)
        print(f"  优化完成 ({time.time() - t0:.0f}s)")

        if new_skill:
            skill_content = new_skill
        else:
            print("  [WARN] 优化失败，保持当前 skill 不变")

    # ── FINALIZE ──
    print(f"\n[FINALIZE]")
    finalize(best_skill, skill_name, skill_folder, PROJECT_ROOT / "Final", run_dir)
    print(f"  最终 pass_rate: {best_score:.2f}")
    print(f"  Final skill: Final/{skill_name}/SKILL.md")
    print(f"  Report: {run_dir}/report.md")


if __name__ == "__main__":
    main()
