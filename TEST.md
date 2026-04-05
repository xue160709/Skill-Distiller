# Skill Distiller 端到端测试用例

## 1. 测试目标

验证当前项目在一次完整运行中，能够正确完成以下链路：

1. 发现唯一 Skill 与多个输入 case
2. 创建新的 `Workspace/<timestamp>/` 运行目录
3. 生成 baseline 输出、推理与 `assertions.json`
4. 生成 `failure_modes.json`，沉淀高价值失败模式目录
5. 让 Student 执行 Skill，生成输出与 reasoning
6. 让 Teacher 生成 `eval.json`、`diff_eval.json`、`reasoning_diff.json`
7. 在需要时执行 `blind_eval.json`
8. 生成优化后的 `iter_*/skill.md`、`change.md`、`new_assertions.json`
9. 正确执行 keep / discard / rollback 逻辑
10. 产出 `Final/<skill_name>/SKILL.md` 和最终 `report.md`

这个测试用例的重点不是验证某个固定分数，而是验证整个蒸馏流水线的输入、输出、文件结构和关键状态转换都能走通。

## 2. 前置条件

- 当前目录为项目根目录：`/Volumes/macOS/Github/Skill Distiller`
- 本机可执行 `python`
- 本机可执行 `claude`
- `claude -p` 可以正常访问模型
- 具备有效的 `ANTHROPIC_AUTH_TOKEN`

建议先手动确认：

```bash
python --version
claude --version
```

## 3. 测试数据设计

### 3.1 测试思路

这里使用一个“故意不够完整”的初始 Skill，让第一轮执行大概率不能直接满分，从而更容易覆盖：

- baseline
- execute
- evaluate
- diff_evaluate
- reasoning_diff
- blind_eval
- optimize
- finalize

### 3.2 被测 Skill

创建目录：

```text
SKILL/
└── release-brief/
    └── SKILL.md
```

`SKILL/release-brief/SKILL.md` 内容：

```markdown
---
name: release-brief
description: Read product update materials and produce a concise release brief.
---

# Release Brief

## Task

Read all files in the provided input directory and create a release brief for internal stakeholders.

## Output

Write a single Markdown file named `brief.md`.

## Requirements

- Use Chinese.
- Summarize the most important changes.
- Mention risks if you notice them.
- Keep the response concise.
```

说明：

- 这个 Skill 有基础方向，但缺少明确结构、优先级规则、输出模板、推理深度要求
- 按当前项目逻辑，Teacher baseline 往往会提炼出更多 assertions，Student 首轮通常有改进空间

### 3.3 输入用例

创建目录：

```text
Input/
├── case_0/
│   ├── announcement.md
│   └── qa.txt
└── case_1/
    ├── changelog.md
    └── support.txt
```

`Input/case_0/announcement.md`：

```markdown
# Product Update 2026-03

## New features

- Added scheduled exports for team admins.
- Added CSV download for audit logs.
- Added retry for failed webhook deliveries.

## Known limitations

- Scheduled exports only support daily frequency.
- Audit log CSV currently excludes deleted users.

## Rollout

- 10% customers on Monday
- 50% customers on Wednesday
- 100% customers next Monday if error rate stays below 0.5%
```

`Input/case_0/qa.txt`：

```text
Q: What should support tell customers who ask for hourly exports?
A: Not supported yet. Offer manual export as workaround.

Q: What is the biggest launch risk?
A: Webhook retry may cause duplicate downstream processing for customers without idempotency handling.
```

`Input/case_1/changelog.md`：

```markdown
# Changelog

## Improvements

- Search results are now grouped by project.
- Mobile dashboard load time improved by 28%.
- Added warning banner when API quota exceeds 80%.

## Bug fixes

- Fixed export job status stuck at "pending".
- Fixed timezone mismatch in weekly analytics email.

## Notes

- Project-grouped search is enabled for all users immediately.
- Quota warning banner is enabled only for Pro and Enterprise plans.
```

`Input/case_1/support.txt`：

```text
Support considerations:

1. Some free-tier users may ask why they cannot see the quota warning banner.
2. The timezone fix only affects newly generated analytics emails.
3. Dashboard performance gain is strongest on mobile web, not desktop.
```

## 4. 测试配置

为了尽可能覆盖完整链路，使用单独的测试配置文件，例如 `config.e2e.json`：

```json
{
  "teacher": {
    "env": {
      "ANTHROPIC_AUTH_TOKEN": "YOUR_TOKEN",
      "ANTHROPIC_BASE_URL": "https://api.anthropic.com",
      "ANTHROPIC_MODEL": "claude-opus-4-20250514"
    }
  },
  "student": {
    "env": {
      "ANTHROPIC_AUTH_TOKEN": "YOUR_TOKEN",
      "ANTHROPIC_BASE_URL": "https://api.anthropic.com",
      "ANTHROPIC_MODEL": "claude-sonnet-4-20250514"
    }
  },
  "max_iterations": 3,
  "target_pass_rate": 0.95,
  "plateau_rounds": 2,
  "timeout": 600,
  "blind_eval_interval": 1,
  "coverage_gap_threshold": 0.2
}
```

配置理由：

- `blind_eval_interval = 1`：保证每轮都执行 blind eval，覆盖该分支
- `target_pass_rate = 0.95`：避免过早结束，更容易覆盖 optimize
- `max_iterations = 3`：控制测试时长
- `plateau_rounds = 2`：更容易覆盖 plateau 检查

## 5. 执行步骤

### 5.1 准备测试文件

如果仓库中没有 `SKILL/`、`Input/`、`Workspace/`、`Final/`，先创建目录并写入上述内容。

### 5.2 运行命令

```bash
python distiller.py config.e2e.json
```

## 6. 预期控制台行为

控制台应出现与以下阶段一致的日志：

```text
[INFO] Skill: release-brief
[INFO] Inputs: 2 case(s)
[INFO] Workspace: .../Workspace/<timestamp>

[BASELINE] Teacher 执行 release-brief...
[BASELINE] 完成 (...)

[ITER 0] Student 执行中...
[ITER 0] Teacher 评估中...
[ITER 0] Teacher 对比评估中...
[ITER 0] Teacher 推理对比中...
[ITER 0] Teacher 盲评中...
[ITER 0] Teacher 优化中...

[FINALIZE]
```

允许分数、耗时、stop reason 不同，但阶段顺序应符合 `distiller.py` 主流程。

## 7. 文件级验收点

### 7.1 Workspace 目录

运行后应新增一个目录：

```text
Workspace/<timestamp>/
```

其中至少应包含：

```text
Workspace/<timestamp>/
├── baseline/
├── assertions.json
├── failure_modes.json
├── skill_v0.md
├── log.json
└── report.md
```

### 7.2 Baseline 阶段产物

以下文件应存在：

```text
Workspace/<timestamp>/baseline/output_0/
Workspace/<timestamp>/baseline/output_1/
Workspace/<timestamp>/baseline/reasoning_0.md
Workspace/<timestamp>/baseline/reasoning_1.md
Workspace/<timestamp>/assertions.json
Workspace/<timestamp>/failure_modes.json
```

验收标准：

- `assertions.json` 是合法 JSON 数组
- 至少包含 1 条 assertion
- 每条 assertion 至少有 `id`、`check`
- `failure_modes.json` 是合法 JSON 数组
- 每条 failure mode 至少有 `id`、`name`、`description`

### 7.3 第 0 轮迭代产物

以下文件应存在：

```text
Workspace/<timestamp>/iter_0/output_0/
Workspace/<timestamp>/iter_0/output_1/
Workspace/<timestamp>/iter_0/reasoning_0.md
Workspace/<timestamp>/iter_0/reasoning_1.md
Workspace/<timestamp>/iter_0/eval.json
Workspace/<timestamp>/iter_0/judge_eval.json   # 当存在 judge assertions 时
Workspace/<timestamp>/iter_0/diff_eval.json
Workspace/<timestamp>/iter_0/reasoning_diff.json
Workspace/<timestamp>/iter_0/blind_eval.json
```

如果进入 optimize，还应存在：

```text
Workspace/<timestamp>/iter_0/skill.md
Workspace/<timestamp>/iter_0/change.md
```

`new_assertions.json` 可选，因为当前实现只有在 Teacher 明确产出时才会生成。

### 7.4 Final 阶段产物

以下文件应存在：

```text
Final/release-brief/SKILL.md
Workspace/<timestamp>/report.md
Workspace/<timestamp>/log.json
```

验收标准：

- `Final/release-brief/SKILL.md` 存在
- `report.md` 为可读 Markdown
- `log.json` 为合法 JSON 数组，至少有 1 条迭代记录

## 8. 内容级验收点

### 8.1 `assertions.json`

检查：

- 是 JSON 数组
- 每项包含 `id`
- 大部分项包含 `check`
- 如果来自 baseline 提取，通常还会包含 `source` 与 `weight`
- 新实现下，建议大部分项包含 `evaluation_method`
- 当 `evaluation_method = "code"` 时，应包含 `code_check`

### 8.2 `eval.json`

检查：

- 顶层有 `iteration`
- 顶层有 `samples`
- 顶层有 `overall_pass_rate`
- 顶层有 `overall_weighted_pass_rate`
- 每个 sample 中有 `results`
- `results` 中至少同时出现过 `passed: true` 或 `passed: false` 之一
- `results` 中可能包含 `evaluation_method: "code"` 或 `evaluation_method: "judge"`

### 8.3 `judge_eval.json`

检查：

- 当存在 `judge` assertions 时，`iter_0/judge_eval.json` 应存在
- 它是 Teacher 仅针对 `judge` assertions 的原始评分结果
- 最终 `eval.json` 则是 code + judge 合并并重新汇总后的结果

### 8.4 `diff_eval.json`

检查：

- 是 JSON 数组
- 每个元素有 `input_id`
- 每个元素可包含 `diffs`
- 如果存在 `suggested_assertion`，其内容应被合并回 `assertions.json`

### 8.5 `reasoning_diff.json`

检查：

- 是 JSON 数组
- 每项可包含 `reasoning_gaps`
- `gap_type` 如果存在，应为 `missing`、`misunderstanding`、`shallow` 之一

### 8.6 `blind_eval.json`

检查：

- 顶层有 `overall_blind_score`
- 每个 sample 有 `blind_score`
- 每个 sample 有 `weak_dimensions`
- 每个 sample 有 `deductions`
- 顶层通常有 `uncovered_dimensions`

说明：

- 代码会优先读取顶层 `uncovered_dimensions`
- 如果缺失，也会从 `weak_dimensions` 与 `deductions[].dimension` 中回推候选未覆盖维度

### 8.7 `report.md`

检查应包含以下章节或关键词：

- `# Skill Distiller Report`
- `## 结果`
- `## 收敛曲线`
- `## 知识迁移记录`

如果触发盲评，还应包含：

- `## 盲评覆盖率趋势`

## 9. 通过标准

本用例判定为通过，需要同时满足：

1. `python distiller.py config.e2e.json` 能执行到 `FINALIZE`
2. 新建了 `Workspace/<timestamp>/`
3. baseline、iter_0、final 的关键产物都存在
4. `assertions.json`、`eval.json`、`log.json` 都能被 JSON 正常解析
5. `failure_modes.json` 能被 JSON 正常解析
6. `report.md` 成功生成且内容非空
7. `Final/release-brief/SKILL.md` 成功生成
8. 运行过程中未因缺失 `assertions.json`、`eval.json`、`skill.md` 等关键文件而提前退出

## 10. 失败判定

出现以下任一情况，判定为失败：

1. 程序在 baseline 阶段退出，并提示 `assertions.json 未生成`
2. 程序在 iter 阶段持续无法生成 `eval.json`
3. 最终没有生成 `Final/release-brief/SKILL.md`
4. `log.json` 不存在或为空
5. `report.md` 未生成
6. 运行目录存在，但主流程没有进入任何一次迭代

## 11. 推荐的人工检查

为了确认这不是“只生成了文件名”的假通过，建议额外人工检查以下内容：

1. 打开 `baseline/reasoning_0.md` 与 `iter_0/reasoning_0.md`，确认确实有自然语言推理记录
2. 打开 `iter_0/change.md`，确认优化说明不是空文件
3. 对比 `skill_v0.md` 和 `Final/release-brief/SKILL.md`，确认最终 Skill 有实际变化，且 YAML frontmatter 仍存在
4. 查看 `log.json` 中的 `action` 字段，确认出现了合理的 `keep` 或 `discard`

## 12. 测试后清理

如果只做一次临时验证，可清理测试产物：

```text
SKILL/release-brief/
Input/case_0/
Input/case_1/
Final/release-brief/
Workspace/<timestamp>/
config.e2e.json
```

是否保留 `Workspace/<timestamp>/` 取决于你是否还需要分析本次蒸馏过程。

## 13. 补充说明

这是一个偏“系统验收”的端到端测试，不适合断言固定分数，原因是：

- baseline/assertions 由模型动态生成
- evaluate/blind_eval 结果带有模型波动
- optimize 后 Skill 的改写内容也具有非确定性

因此本用例把“流程走通、关键文件落盘、结构合法、状态转换正确”作为主判断标准，这也更符合当前项目的真实风险面。
