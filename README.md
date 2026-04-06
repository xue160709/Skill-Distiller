# Skill Distiller

自动化 Skill 蒸馏系统。它把一个初始 skill 目录当作可迭代优化的对象：Teacher 先跑出标杆解法与推理，再把关键质量标准提炼成 assertions；Student 按当前 skill 执行；Teacher 评测、对比、盲评、回写知识，直到收敛。

> 典型用法：准备一个 skill、放几个输入 case，运行 `python distiller.py`，最后查看 `Final/<skill-name>/` 和 `Workspace/<timestamp>/report.md`。

## 用途与适用场景

这个项目适合用来优化“已经有雏形，但质量不稳定、难以系统改进”的 skill。它特别适合下面几类场景：

- 写作类 skill：例如观点扩写、摘要、brief、邮件、报告生成
- 分析类 skill：例如需求拆解、问题诊断、评审意见生成
- 流程类 skill：例如固定输入格式下的多步处理、带模板的标准化输出
- 需要长期维护的 skill：你不想只改一句 prompt，而是想把规则、示例、参考材料、脚本一起沉淀

它尤其适合这些问题：

- 初始 skill “偶尔很好，偶尔跑偏”，缺少稳定质量
- 你知道 Teacher 能做得更好，但不知道该把哪些知识写回 skill
- 你希望优化的是整个 skill 资产，而不是一次性答案
- 你需要 keep / discard / rollback 这种可回退的实验闭环

不太适合的场景：

- 只是想手工润色一次 `SKILL.md`
- 输入 case 很少，无法形成稳定评测闭环
- 任务本身几乎无法定义 pass/fail 或质量维度

## Teacher 与 Student 是什么

在这个项目里，Teacher 和 Student 不是两个抽象概念，而是两类扮演不同职责的模型角色：

- `Teacher`：强模型，负责做标杆、提炼 assertions、执行 judge、做 diff / blind eval，并把高价值知识迁移回 skill
- `Student`：较弱模型或执行代理，负责只根据当前 skill 执行任务，是检验 skill 蒸馏效果的执行者

一个很典型的配置方式是：

- `Teacher` 使用更强模型，例如 `opus 4.6`
- `Student` 使用稍弱但更便宜或更常用的模型，例如 `glm 4.7`

可以把它理解成：

- Teacher 代表“你心目中的更强做法”或“标杆质量上限”
- Student 代表“真实会按 skill 说明执行的较弱代理”

所以这里蒸馏的不是 Teacher 本身，而是 Teacher 的知识如何被写进 skill，让 Student 仅靠 skill 也能更稳定地接近 Teacher。

## 这是哪种“知识蒸馏”

这个项目确实属于知识蒸馏，但不是传统意义上的模型参数蒸馏，而是面向整个 skill 的蒸馏。

更准确地说，它蒸馏的是：

- Teacher 的任务完成方式
- Teacher 的判断标准
- Teacher 的决策规则
- Teacher 的示例、模式和流程知识

然后把这些知识写回 skill 目录，让较弱模型在不改权重、不做训练的前提下，也能更稳定地逼近 Teacher 的表现。

所以它不是：

- logits / hidden states 层面的蒸馏
- 训练一个新的 Student 模型
- 把大模型权重压缩到小模型里

它更像是：

- `model distillation` 之外的 `skill distillation`
- `weights` 不变，`skill` 变强
- 蒸馏对象是整个 skill 资产，而不是模型参数

## 项目目标

- 让 Skill 优化从“拍脑袋改 prompt”变成“基于评测闭环的蒸馏”
- 用 Teacher 的推理过程提炼可复用知识，而不是只保留一次性答案
- 通过 assertions、blind eval 和 rollback 机制，尽量减少自举漂移

## 核心机制

- `baseline`：Teacher 执行当前 skill，产出标杆输出、推理、`assertions.json`、`failure_modes.json`
- `execute`：Student 按当前安装中的 skill 执行每个输入 case，并记录自己的 reasoning
- `evaluate`：先跑 deterministic `code` assertions，再把 `judge` assertions 交给 Teacher 判定
- `diff_evaluate`：Teacher 对比 baseline 与 Student 输出，识别 assertions 尚未覆盖的质量差距
- `reasoning_diff`：Teacher 对比 baseline reasoning 与 Student reasoning，定位认知偏差
- `blind_eval`：Teacher 不看 assertions 独立打分，检测“分数看起来不错，但评测漏了关键问题”的情况
- `optimize`：Teacher 依据 failed assertions、diff、blind eval 和 baseline reasoning，把高杠杆知识写回 skill 目录
- `keep / discard / rollback`：只有通过 artifact gate 且 core score 创新高时才保留；否则回滚到当前 best skill

这里的“优化对象”是整个 skill 目录，而不是单独一个 `SKILL.md` 文件。当前实现会把整个 skill 安装、评估、回写和最终复制，因此 `references/`、`scripts/`、`assets/`、`agents/openai.yaml` 这类配套资源也都属于蒸馏对象的一部分。

## 主流程

```text
setup_workspace
  -> baseline
  -> for each iteration:
       execute
       evaluate
       diff_evaluate
       reasoning_diff
       score (keep / discard / rollback)
       blind_eval (按间隔或 plateau 触发)
       optimize
  -> finalize
```

当前实现里，真正决定 keep / discard 的是：

- `artifact_gate_passed`：artifact 层的 `code` assertions 必须全部通过
- `overall_core_weighted_pass_rate`：core 层加权分必须超过历史最优

也就是说，`overall_weighted_pass_rate` 会被记录，但最终保留逻辑主要看 artifact gate + core score。

## 目录结构

```text
.
├── distiller.py              # 状态机入口
├── prompts.py                # baseline / execute / evaluate / optimize / diff / blind eval prompts
├── config.json               # Teacher / Student / stop 条件配置
├── SKILL/                    # 待蒸馏 skill，要求仅保留一个有效 skill 子目录
├── Input/                    # 输入用例，每个子目录一个 case
├── Workspace/                # 每次运行的完整中间产物
└── Final/                    # 最终 best skill 副本
```

`SKILL/` 下必须且只能有一个包含 `SKILL.md` 的子目录；`Input/` 下每个子目录都会被视为一个独立测试 case。

## 快速开始

### 1. 准备 skill

```text
SKILL/
└── my-skill/
    └── SKILL.md
```

当前项目会把整个 skill 目录安装到项目内的 `.claude/skills/<skill-name>/`，所以除了 `SKILL.md`，也支持一起蒸馏和复制：

- `references/`
- `scripts/`
- `assets/`
- `agents/openai.yaml`

### 2. 准备输入

```text
Input/
├── case_0/
│   └── ...
├── case_1/
│   └── ...
└── case_2/
    └── ...
```

每个 case 目录里的所有文件都会被 Teacher / Student 读取。

### 3. 配置模型与停止条件

最小可用配置示例：

```json
{
  "teacher": {
    "env": {
      "ANTHROPIC_MODEL": "claude-opus-4-20250514"
    }
  },
  "student": {
    "env": {
      "ANTHROPIC_MODEL": "claude-sonnet-4-20250514"
    }
  },
  "max_iterations": 15,
  "target_pass_rate": 0.9,
  "plateau_rounds": 3,
  "max_runtime_seconds": 1200,
  "blind_eval_interval": 3,
  "coverage_gap_threshold": 0.2
}
```

说明：

- 模型选择通过环境变量 `ANTHROPIC_MODEL` 注入，不走 `--model`
- `teacher.env` / `student.env` 会直接并入各自子进程环境
- 代码要求 `student.env.ANTHROPIC_MODEL` 必填；Teacher 可继承宿主环境
- `max_runtime_seconds` 会覆盖旧字段 `timeout`

### 4. 运行

新开一轮实验：

```bash
python distiller.py
```

指定配置：

```bash
python distiller.py config.json
```

从已有 workspace 恢复：

```bash
python distiller.py config.json Workspace/2026-04-06_113000
```

命令行用法：

```text
python distiller.py [config.json] [Workspace/<timestamp>]
```

## 运行产物

一次典型运行会生成：

```text
Workspace/<timestamp>/
├── distiller.log
├── run_meta.json
├── skill_v0/                 # 初始 skill 快照
├── best_skill/               # 当前最佳 skill 快照
├── assertions.json           # 累积后的检查项集合，只增不减
├── failure_modes.json        # baseline 提炼出的高价值失败模式
├── baseline/
│   ├── output_0/
│   ├── output_1/
│   ├── reasoning_0.md
│   └── reasoning_1.md
├── iter_0/
│   ├── output_0/
│   ├── reasoning_0.md
│   ├── eval.json
│   ├── judge_assertions.json
│   ├── judge_eval.json
│   ├── diff_eval.json
│   ├── reasoning_diff.json
│   ├── blind_eval.json       # 仅在触发时生成
│   ├── knowledge_candidates.md
│   ├── skill/
│   │   └── SKILL.md
│   ├── change.md
│   └── new_assertions.json
├── log.json
└── report.md
```

最终 best skill 会被复制到：

```text
Final/<skill-name>/
```

## 配置项

当前代码支持的主要配置如下：

| 字段 | 默认值 | 作用 |
|------|--------|------|
| `max_iterations` | `15` | 最大迭代轮数 |
| `target_pass_rate` | `0.90` | 当 `best_core_score` 达到该值时停止 |
| `plateau_rounds` | `3` | 连续 N 轮 best score 不上涨则判定 plateau |
| `max_runtime_seconds` / `timeout` | `1200` | 单次 `claude -p` 子进程超时秒数 |
| `blind_eval_interval` | `3` | 每 N 轮触发一次 blind eval |
| `coverage_gap_threshold` | `0.2` | `core_score - blind_score` 超过阈值时，覆盖 plateau 停止 |
| `min_iterations_before_stop` | `2` | 至少跑到多少轮后才允许停止 |
| `min_optimize_rounds` | `1` | 至少完成多少轮 optimize 后才允许停止 |
| `critical_fail_cap` | `0.84` | 单个高权重失败项时，对 weighted score 的封顶 |
| `multi_critical_fail_cap` | `0.72` | 两个及以上高权重失败项时，对 weighted score 的封顶 |

## 评分与断言

assertion 支持两种评测方式：

- `code`：确定性检查，当前支持
  - `file_exists`
  - `contains_all`
  - `contains_any`
  - `not_contains_any`
  - `max_sentences_per_paragraph`
  - `no_pattern_match`
- `judge`：由 Teacher 结合输出证据判定

assertion 还会被分层：

- `artifact`：交付物格式与合规门槛
- `core`：跨输入通用的关键质量标准
- `scoped`：只适用于部分输入的标准

合并评测结果后，每个 sample 会得到：

- `pass_rate`
- `weighted_pass_rate`
- `artifact_gate_passed`
- `artifact_weighted_pass_rate`
- `core_weighted_pass_rate`
- `scoped_weighted_pass_rate`

其中有两个重要规则：

1. artifact 层的 `code` assertions 必须全部通过，否则该轮直接不保留。
2. 若有高权重失败项，weighted score 会被封顶，避免“关键问题没解决但靠格式项刷分”。

## Blind Eval 与覆盖缺口

blind eval 的作用不是替代 assertions，而是发现“当前断言集漏掉了什么”。

流程上它会：

- 对输出独立打 `blind_score`
- 提取 `weak_dimensions`
- 产出 3-5 个 `uncovered_dimensions`
- 计算 `coverage_gap = core_score - blind_score`

如果 `coverage_gap > coverage_gap_threshold`：

- 本轮会覆盖 plateau 停止判定，继续跑下去
- `uncovered_dimensions` 会被自动转成新的 assertions，补进评测闭环

## Optimize 阶段

优化阶段不是简单重写 `SKILL.md`，而是把当前 skill 当成完整目录资产处理。

Teacher 会综合读取：

- 当前 skill 目录
- `eval.json`
- `baseline/reasoning_*.md`
- `diff_eval.json`
- `reasoning_diff.json`
- `knowledge_candidates.md`
- `blind_eval.json`
- `failure_modes.json`

并遵守以下约束：

- 每轮最多迁移 3 条知识
- assertions 只增不减
- 尽量保留原有结构，只做高价值增量修改
- skill 长度不应失控膨胀

## 恢复运行

如果运行中断，可以基于已有 workspace 恢复。恢复逻辑会：

- 校验 `baseline/`、`assertions.json`、`skill_v0/` 等关键产物
- 根据 `log.json` 推断下一轮 iteration
- 重新安装当时的 current skill 与 best skill
- 拒绝恢复已经写入 `stop_reason` 的已结束实验

适合的场景：

- 子进程超时或模型服务中断
- 中途手动停止
- 想延续一轮已跑一半的实验

## 报告

每次运行结束后会在 `Workspace/<timestamp>/report.md` 生成摘要，包含：

- 初始 / 最终 core pass rate
- 收敛曲线
- 每轮 keep / discard
- blind score 与 coverage gap
- 当前仍未通过的 assertions

## 运行依赖

- Python 3.10+
- `claude` 命令可用，且支持 `claude -p`
- 对应模型供应商所需环境变量已配置

## 约束与项目约定

- `distiller.py` 是唯一入口
- 子进程调用固定在项目根目录执行，确保本地 skill 可被发现
- 模型选择通过环境变量控制，不通过命令行 `--model`
- `assertions.json` 采用只增不减策略
- 不要修改 `.Codex/skills/skill-creator/` 下的文件

## 开发提示

- 想覆盖完整链路，可以参考 [`TEST.md`](/Volumes/macOS/Github/Skill%20Distiller%20/TEST.md)
- 设计恢复机制的背景说明可见 [`resume_recovery_design.md`](/Volumes/macOS/Github/Skill%20Distiller%20/resume_recovery_design.md)
- 当前仓库里示例 skill 位于 [`SKILL/opinion-to-article/SKILL.md`](/Volumes/macOS/Github/Skill%20Distiller%20/SKILL/opinion-to-article/SKILL.md)

## License

MIT
