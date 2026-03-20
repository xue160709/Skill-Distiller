"""Skill Distiller prompt 模板。

prompt 函数，每个返回一个字符串 prompt，传给 claude -p 子进程。
Prompt 指示 Claude 读取输入文件、执行任务、写入输出文件。
"""


def baseline_prompt(
    skill_content: str,
    input_dirs: list[str],
    baseline_dir: str,
    assertions_path: str,
) -> str:
    """BASELINE: Teacher 执行标杆 + 推理 + 提取 assertions。"""

    input_list = "\n".join(
        f"- 输入 {i}: 读取 `{d}` 中的所有文件" for i, d in enumerate(input_dirs)
    )

    return f"""你是一位资深专家（Teacher），负责执行以下 Skill 并产出标杆输出。

## 你要执行的 Skill

<skill>
{skill_content}
</skill>

## 输入列表

{input_list}

## 任务

对每个输入 i，按照 Skill 的指令执行任务：

1. **执行任务**：将输出写入 `{baseline_dir}/output_{{i}}/`
2. **记录推理**：详细记录你的推理过程——为什么做出某个选择、遵循了什么原则、刻意避免了什么。写入 `{baseline_dir}/reasoning_{{i}}.md`

推理文件要求：
- 记录关键决策点及其原因
- 记录你刻意遵循的规则或模式
- 记录你刻意避免的做法及原因

## 提取检查项

完成所有输入后，基于你的推理过程，提取一组可验证的检查项（assertions）。
每条描述"如果 Student 做对了，应该满足的条件"。

要求：
- 每条必须可明确判断 pass/fail，不能模糊
- 每条标注来源（reasoning 中的哪个部分）
- 每条给权重（1=一般，2=重要，3=关键）
- 8-15 条为宜

写入 `{assertions_path}`，格式：

```json
[
    {{
        "id": "snake_case_id",
        "check": "描述检查条件",
        "source": "reasoning_X.md — 相关段落",
        "weight": 2
    }}
]
```

## 重要

- 先执行所有输入，最后再提取 assertions
- 输出要体现你的最高水平
- 推理要足够详细，让后续优化步骤能从中提取可迁移的知识"""


def execute_prompt(
    skill_name: str,
    input_dir: str,
    output_dir: str,
    reasoning_path: str = "",
) -> str:
    """EXECUTE: Student 按当前 Skill 执行单个输入。"""

    reasoning_section = ""
    if reasoning_path:
        reasoning_section = f"""

## 推理记录

在执行任务的过程中，将你的推理过程写入 `{reasoning_path}`：
- 你理解了哪些关键规则/原则
- 你做出了哪些选择，为什么
- 你刻意避免了什么做法
- 你对任务要求的理解"""

    return f"""请使用 /{skill_name} 技能，对以下输入执行任务。

## 输入

读取 `{input_dir}` 中的所有文件作为输入。

## 输出

将所有输出文件写入 `{output_dir}/`
{reasoning_section}

## 要求

- 严格按照技能的指令执行
- 不要跳过任何步骤
- 将所有产出写入指定的输出目录"""


def evaluate_prompt(
    assertions_path: str,
    output_dirs: list[str],
    eval_path: str,
    iteration: int,
) -> str:
    """EVALUATE: Teacher 逐条检查 assertions。"""

    output_list = "\n".join(
        f"- 输出 {i}: 读取 `{d}` 中的所有文件" for i, d in enumerate(output_dirs)
    )

    return f"""你是检查员（Teacher），负责逐条检查 Student 的输出是否满足检查项。

## Student 输出

{output_list}

## 检查项

读取 `{assertions_path}`

## 要求

对每个输出的每条 assertion：
- 判断 pass 或 fail
- 附带 evidence（引用 Student 输出中的具体内容）
- 如果 fail，附带 gap（Student 缺失了什么知识）

将结果写入 `{eval_path}`，严格使用以下 JSON 格式：

```json
{{
    "iteration": {iteration},
    "samples": [
        {{
            "input_id": 0,
            "results": [
                {{
                    "id": "assertion_id",
                    "passed": true,
                    "evidence": "具体证据"
                }},
                {{
                    "id": "assertion_id",
                    "passed": false,
                    "evidence": "具体证据",
                    "gap": "Student 缺失的知识"
                }}
            ],
            "pass_rate": 0.75,
            "weighted_pass_rate": 0.72
        }}
    ],
    "overall_pass_rate": 0.75,
    "overall_weighted_pass_rate": 0.72
}}
```

## 重要

- 严格基于证据判断，不要猜测
- evidence 必须引用 Student 输出中的具体内容
- gap 要具体描述缺失的知识点
- pass_rate = passed / total
- weighted_pass_rate = sum(passed * weight) / sum(weight)"""


def optimize_prompt(
    skill_path: str,
    eval_path: str,
    reasoning_dir: str,
    output_skill_path: str,
    change_path: str,
    new_assertions_path: str,
    iteration: int,
    baseline_dir: str = "",
    diff_eval_path: str = "",
    reasoning_diff_path: str = "",
) -> str:
    """OPTIMIZE: Teacher 分析失败项，迁移知识到 Skill。"""

    # 额外评估数据（对比信号 + Student 自省）
    extra_eval_section = ""
    if diff_eval_path:
        extra_eval_section += f"""

## 对比评估结果

读取 `{diff_eval_path}`，这是 Teacher 标杆输出与 Student 输出的逐段对比分析。
关注 `gap` 和 `suggested_assertion` 字段——它们揭示了 assertions 未覆盖的真实差距。
将 `suggested_assertion` 中有价值的检查项纳入新 assertions。"""

    if reasoning_diff_path:
        extra_eval_section += f"""

## Student 推理偏差分析

读取 `{reasoning_diff_path}`，这是 Teacher 与 Student 推理过程的对比。
关注 `gap_type` 字段来选择知识迁移方式：
- `missing`（Student 完全不知道）→ 在 Skill 中补充规则
- `misunderstanding`（Student 理解偏差）→ 在 Skill 中加正反例纠正
- `shallow`（方向对但深度不够）→ 在 Skill 中加推理链示范"""

    # baseline 目录（用于截取正例片段）
    baseline_section = ""
    if baseline_dir:
        baseline_section = f"""

## Teacher 标杆输出（正例来源）

读取 `{baseline_dir}` 中的 output_*/ 目录，作为截取示例片段的来源。"""

    return f"""你是 Skill 蒸馏优化专家（Teacher）。

## 任务

根据 failed assertions，把 Teacher 的知识迁移到 Skill 中。

## 当前 Skill

读取 `{skill_path}`

## 评估结果

读取 `{eval_path}`，关注 passed=false 的条目，特别注意 gap 字段
{extra_eval_section}

## Teacher 推理过程（知识来源）

读取 `{reasoning_dir}` 中的所有 reasoning_*.md 文件
{baseline_section}

## 知识迁移方式

对每条要迁移的知识，先判断它适合用**规则**还是**示例**：

| 知识类型 | 特征 | 迁移方式 |
|----------|------|----------|
| 结构规则 | 可量化（"不超过4句"） | rule |
| 决策逻辑 | 条件明确（"当有因果关系时用递进"） | rule |
| 风格/语感 | 难以量化（"有张力""节奏好"） | example |
| 复合技巧 | 多个要素同时起作用 | example + 简短说明 |

**rule 格式**：用"当...则...因为..."格式写成清晰指令。

**example 格式**：从 Teacher 标杆输出中截取真实片段作为正例，配合反例：
```markdown
## [技巧名称]

[一句话说明原则]

示例：
✓ "[从 baseline 输出截取的正例]"
✓ "[另一个正例]"
✗ "[反例]"（[解释为什么不好]）
```

**示例约束**：
- 正例必须从 Teacher 标杆输出中截取，不凭空编造
- 每条示例不超过 2 行，避免 Skill 膨胀
- 正例 + 反例成对出现，帮助 Student 区分边界

## 约束

- 每轮最多迁移 3 条知识（聚焦最重要的 gap）
- 修改后的 Skill 长度不超过前一版的 150%
- 不要删除 Skill 中已有的有效指令
- 保持 Skill 的 YAML frontmatter 不变
- 新增的指令要自然融入 Skill 的结构

## 输出

1. 修改后的 SKILL.md → 写入 `{output_skill_path}`
2. 修改说明 → 写入 `{change_path}`，包含：
   - 本轮迁移了哪些知识
   - 每条知识的 `transfer_type`（"rule" 或 "example"）
   - 每条知识来自哪个 reasoning 文件
   - 预期能修复哪些 failed assertions
3. 如发现需要新增检查项 → 写入 `{new_assertions_path}`，格式同 assertions.json

## 重要

- 聚焦于权重高的 failed assertions
- 迁移的是判断逻辑和决策原则，不是具体的输出内容
- Skill 是给 Student 看的指令，要清晰、可操作
- 对"感觉类"知识优先用示例迁移——利用 Student 的模仿能力，而非只依赖指令遵循
- 这是第 {iteration} 轮优化"""


def diff_evaluate_prompt(
    baseline_dir: str,
    output_dirs: list[str],
    diff_eval_path: str,
) -> str:
    """DIFF_EVALUATE: Teacher 逐段对比标杆输出与 Student 输出。"""

    output_list = "\n".join(
        f"- Student 输出 {i}: 读取 `{d}` 中的所有文件" for i, d in enumerate(output_dirs)
    )

    return f"""你是对比分析专家（Teacher），负责逐段对比标杆输出与 Student 输出，找出 assertions 可能未覆盖的差距。

## Teacher 标杆输出

读取 `{baseline_dir}` 中的 output_*/ 目录

## Student 输出

{output_list}

## 任务

对每个输入，将 Teacher 标杆输出与 Student 输出逐段对比：

1. 找出质量差距（不限于已有的 assertions）
2. 分析 Teacher 用了什么技巧/模式，Student 缺失了什么
3. 对每个差距建议一条新的 assertion

## 输出

将结果写入 `{diff_eval_path}`，严格使用以下 JSON 格式：

```json
[
    {{
        "input_id": 0,
        "diffs": [
            {{
                "teacher_fragment": "从 Teacher 输出截取的具体片段",
                "student_fragment": "Student 对应部分的具体片段",
                "gap": "Student 的问题描述",
                "teacher_pattern": "Teacher 使用的技巧/模式",
                "suggested_assertion": {{
                    "id": "snake_case_id",
                    "check": "描述检查条件",
                    "weight": 2
                }}
            }}
        ]
    }}
]
```

## 重要

- 聚焦于有实质意义的差距，忽略措辞上的微小差异
- teacher_fragment 和 student_fragment 必须是具体引用，不是概括
- suggested_assertion 的 check 必须可明确判断 pass/fail
- 每个输入最多报告 5 个最重要的差距"""


def reasoning_diff_prompt(
    baseline_dir: str,
    iter_dir: str,
    input_count: int,
    reasoning_diff_path: str,
) -> str:
    """REASONING_DIFF: Teacher 对比两份 reasoning，诊断 Student 的认知偏差。"""

    return f"""你是认知诊断专家（Teacher），负责对比 Teacher 与 Student 的推理过程，找出 Student 的认知偏差。

## Teacher 推理

读取 `{baseline_dir}` 中的 reasoning_*.md 文件（共 {input_count} 个）

## Student 推理

读取 `{iter_dir}` 中的 reasoning_*.md 文件（共 {input_count} 个）

## 任务

对每个输入，逐主题对比两份推理：

1. 找出 Student 推理中缺失、偏差或浅层的部分
2. 为每个偏差分类 gap_type
3. 建议具体的修复策略和 Skill 补丁

## gap_type 分类

| 类型 | 含义 | 优化策略 |
|------|------|----------|
| `missing` | Student 推理中完全没提到这个维度 | 在 Skill 中补充规则 |
| `misunderstanding` | 提到了但理解偏差 | 在 Skill 中加正反例纠正 |
| `shallow` | 方向对但深度不够 | 在 Skill 中加推理链示范 |

## 输出

将结果写入 `{reasoning_diff_path}`，严格使用以下 JSON 格式：

```json
[
    {{
        "input_id": 0,
        "reasoning_gaps": [
            {{
                "topic": "主题名称",
                "teacher_reasoning": "Teacher 的推理要点",
                "student_reasoning": "Student 对应的推理（如果有）",
                "gap_type": "missing | misunderstanding | shallow",
                "fix_strategy": "建议的修复策略",
                "skill_patch": "建议写入 Skill 的内容片段"
            }}
        ]
    }}
]
```

## 重要

- 聚焦于影响输出质量的实质性推理差距
- Teacher 推理中的所有关键决策点都应检查
- skill_patch 要具体、可操作，不要泛泛而谈
- 每个输入最多报告 5 个最重要的差距"""


def blind_eval_prompt(
    output_dirs: list[str],
    blind_eval_path: str,
    iteration: int,
) -> str:
    """BLIND_EVAL: Teacher 不看 assertions，直接对 Student 输出打分。"""

    output_list = "\n".join(
        f"- 输出 {i}: 读取 `{d}` 中的所有文件" for i, d in enumerate(output_dirs)
    )

    return f"""你是质量评估专家（Teacher），负责对 Student 的输出进行独立质量评估。

**重要：不要参考任何 assertions 或检查项，完全基于你的专业判断。**

## Student 输出

{output_list}

## 任务

对每个输出进行全面质量评估：

1. 满分 10 分，从专业角度打分
2. 列出所有扣分项及扣分理由
3. 识别输出中的薄弱维度

## 输出

将结果写入 `{blind_eval_path}`，严格使用以下 JSON 格式：

```json
{{
    "iteration": {iteration},
    "samples": [
        {{
            "input_id": 0,
            "blind_score": 7,
            "max_score": 10,
            "deductions": [
                {{"reason": "扣分理由", "points": -1}},
                {{"reason": "扣分理由", "points": -2}}
            ]
        }}
    ],
    "overall_blind_score": 0.70
}}
```

## 重要

- 完全独立评估，不要参考任何 assertions
- 评分要严格、客观，基于专业标准
- deductions 要具体——精确描述问题所在
- overall_blind_score = 所有 samples 的 blind_score 的平均值 / max_score"""
