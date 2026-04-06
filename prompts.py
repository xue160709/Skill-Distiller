"""Skill Distiller prompt 模板。

prompt 函数，每个返回一个字符串 prompt，传给 claude -p 子进程。
Prompt 指示 Claude 读取输入文件、执行任务、写入输出文件。
"""


def baseline_prompt(
    skill_content: str,
    input_dirs: list[str],
    baseline_dir: str,
    assertions_path: str,
    failure_modes_path: str = "",
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
- 优先选择高影响、常出现、可执行的检查项，避免空泛的"质量高""表达好"
- 每条标注来源（reasoning 中的哪个部分）
- 每条给权重（1=一般，2=重要，3=关键）
- 权重要体现真正的质量杠杆：
  - `3` 只给缺失后会明显拉低整体质量的关键项（核心洞察、关键逻辑链、解法可操作性、关键结构闭环、关键证据质量）
  - `2` 给重要但非致命项
  - `1` 给纯格式/合规/辅助项
- 不要让大量格式项占满高权重；`code` assertion 默认应偏低权重，除非缺失会直接导致任务失败
- 至少产出 4 条 `judge` 类关键断言（weight=3），并确保它们真的能拉开 Teacher / Student 的质量差距
- 避免只写"是否出现了某元素"；更优先写"该元素是否达到足够质量门槛"
- 每条必须声明 `evaluation_method`
  - `code`：只有当检查项能严格映射到下方支持的确定性检查时才能使用
  - `judge`：其余都使用 Teacher 判定
- 8-12 条为宜；宁可少而锋利，也不要多而松

支持的 `code` 检查类型：

- `file_exists`
- `contains_all`
- `contains_any`
- `not_contains_any`
- `max_sentences_per_paragraph`
- `no_pattern_match`

`code` assertion 的 `code_check` 字段格式：

```json
{{
    "type": "file_exists | contains_all | contains_any | not_contains_any | max_sentences_per_paragraph | no_pattern_match",
    "path": "可选，目标文件相对路径；若 target=article 且省略，默认只检查 article.md",
    "phrases": ["用于 contains/not_contains 检查的短语列表"],
    "case_sensitive": false,
    "max_sentences": 4,
    "pattern": "用于 no_pattern_match 的正则"
}}
```

只有在以上结构足以无歧义完成检查时，才允许选择 `code`。
凡是涉及风格、逻辑完整性、信息取舍、忠实度、语气、论证质量等主观判断，一律使用 `judge`。
当 `evaluation_method = "judge"` 时，不要填写 `code_check`。
每条 assertion 还必须声明：

- `layer`：`artifact | core | scoped`
- `target`：`article | process`
- `applies_to_inputs`：`all` 或输入 id 列表（如 `[0]`、`[1]`、`[0, 1]`）

规则：

- `artifact`：最终交付物的格式/合规检查
- `core`：跨输入通用的关键质量标准
- `scoped`：只适用于部分输入的标准，必须通过 `applies_to_inputs` 明确标出
- 面向最终成文的 code assertion，优先使用 `target: "article"`，并让检查只落在 `article.md`

写入 `{assertions_path}`，格式：

```json
[
    {{
        "id": "snake_case_id",
        "check": "描述检查条件",
        "source": "reasoning_X.md — 相关段落",
        "layer": "artifact | core | scoped",
        "target": "article | process",
        "applies_to_inputs": "all | [0] | [1] | [0, 1]",
        "weight": 2,
        "evaluation_method": "code | judge",
        "code_check": {{
            "type": "file_exists",
            "path": "brief.md"
        }}
    }}
]
```

## 失败模式目录

另外，写一份失败模式目录到 `{failure_modes_path}`。
这不是评分结果，而是后续评测和优化要优先关注的失败空间。

要求：
- 产出 5-10 条最重要的失败模式
- 必须基于输入任务、你的 baseline 输出、以及推理中暴露的关键决策点
- 每条失败模式必须原子化，避免把多个问题混成一条
- 不要使用"整体质量差""不够好"这类空泛表述
- 优先记录高影响、高频、后续值得进入评测闭环的问题

格式：

```json
[
    {{
        "id": "snake_case_id",
        "name": "失败模式名称",
        "description": "什么情况下算这个失败",
        "severity": "low | medium | high",
        "why_it_matters": "为什么这个失败重要",
        "symptoms": [
            "可观察信号 1",
            "可观察信号 2"
        ],
        "recommended_eval_focus": "后续评测应重点检查什么"
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
    failure_modes_path: str = "",
) -> str:
    """EVALUATE: Teacher 逐条检查 assertions。"""

    output_list = "\n".join(
        f"- 输出 {i}: 读取 `{d}` 中的所有文件" for i, d in enumerate(output_dirs)
    )
    failure_modes_section = ""
    if failure_modes_path:
        failure_modes_section = f"""

## 失败模式目录（仅作 gap 诊断参考）

读取 `{failure_modes_path}`。
可借助这些失败模式来描述 gap，但**不要**因此新增评分标准；最终仍只按 assertions 判分。"""

    return f"""你是检查员（Teacher），负责逐条检查 Student 的输出是否满足检查项。

## Student 输出

{output_list}

## 检查项

读取 `{assertions_path}`
{failure_modes_section}

## 要求

对每个输出的每条 assertion：
- 判断 pass 或 fail
- 附带 evidence（引用 Student 输出中的具体内容）
- 如果 fail，附带 gap（Student 缺失了什么知识）
- 对 `judge` assertion，评估标准是“是否达到 Teacher baseline 级别的完成度”，不是“是否大致沾到这个元素”
- 如果 Student 只有表面对应元素，但深度、独特性、论证力度、可操作性明显弱于高质量标准，应判 fail，不要因为“差不多有”就判 pass
- 如果你在 pass / fail 之间犹豫，默认判 fail
- 重大缺口必须反映到正式评分里，不能只在心里觉得差、却仍然判 pass

## 重大缺口处理规则

以下任一情况出现时，相关 assertion 应优先判 fail，而不是给“勉强通过”：
- 核心洞察缺失，只剩表层复述
- 逻辑链关键跳步（问题直接跳解法、缺少必要推导/过渡）
- 解法停留在口号或方向感，没有可操作动作/练习路径
- 类比只出现名词，没有说明相似点或为何能支撑论证
- 结尾没有形成压缩句、闭环或可带走的核心判断
- 证据/权威引用明显弱化为模糊表述，无法支撑核心论点

## 校准原则

- 不要因为文章整体流畅、语言顺滑，就补偿关键缺失
- 不要把“有这个部分”当作“这个部分做对了”
- 当某个高权重 assertion fail 时，sample 的整体质量应明显下调

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
                    "why_not_enough": "虽然出现了相关元素，但为什么仍未达到要求",
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
- 对可客观判断的检查项，优先基于输出中的可验证事实判定，不要做整体印象打分
- evidence 必须引用 Student 输出中的具体内容
- fail 时优先补充 `why_not_enough`，解释为什么“有但不够”仍应失败
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
    knowledge_path: str = "",
    blind_eval_path: str = "",
    failure_modes_path: str = "",
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

    if knowledge_path:
        extra_eval_section += f"""

## 可迁移知识候选

读取 `{knowledge_path}`。
这里已经汇总了本轮 diff / reasoning diff 中最值得写回 Skill 的候选知识。
默认优先从这里挑选 1-3 条真正高杠杆的知识写回 Skill，而不是只修表层措辞。"""

    if blind_eval_path:
        extra_eval_section += f"""

## 盲评结果

读取 `{blind_eval_path}`。
关注：
- `deductions`：真实扣分点
- `weak_dimensions`：当前输出薄弱维度
- `uncovered_dimensions`：assertions 尚未充分覆盖、但值得进入闭环的新维度

当 blind eval 与 assertions 评分不一致时，优先相信 blind eval 揭示的漏评问题，并考虑把这些问题转化为更原子的新 assertions。"""

    if failure_modes_path:
        extra_eval_section += f"""

## 失败模式目录

读取 `{failure_modes_path}`。
优先修复其中 `severity` 高、且与当前 fail/gap 对应的失败模式。
不要为了局部措辞优化而忽略高影响失败。"""

    # baseline 目录（用于截取正例片段）
    baseline_section = ""
    if baseline_dir:
        baseline_section = f"""

## Teacher 标杆输出（正例来源）

读取 `{baseline_dir}` 中的 output_*/ 目录，作为截取示例片段的来源。"""

    return f"""你是 Skill 蒸馏优化专家（Teacher）。

## 任务

根据 failed assertions，把 Teacher 的知识迁移到 Skill 中。
在修改 Skill 时，优先采用 `/.claude/skills/skill-creator` 所代表的“更新现有 skill”工作方式来思考和组织修改。
也就是说：把当前 `SKILL.md` 当作一个待迭代的 skill 资产来更新，而不是把它当作普通 Markdown 文档随意重写。

## 修改方式

- 明确按“更新已有 skill”的方式工作
- 优先沿用现有 skill 的结构、章节、frontmatter 和触发描述
- 只有在确有必要时才重组章节；默认做最小且高价值的增量修改
- 写出来的内容要像一个可长期维护的 skill，而不是一次性的补丁说明
- 不要修改 `skill-creator` 本身；只是借鉴它支持的 skill 更新方式来修改当前 skill

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
   - 新 assertion 必须显式带上 `layer`
   - 若是面向最终成文的检查，设 `target: "article"`
   - 若只适用于部分输入，显式写 `applies_to_inputs`
   - 只允许使用受支持的 `code_check.type`，不要发明新类型

## 重要

- 聚焦于权重高的 failed assertions
- 若某个新增检查项可以稳定转成受支持的 `code_check`，优先使用 `evaluation_method: "code"`
- 把这次工作视为一次 skill update，而不是一次自由改写
- 迁移的是判断逻辑和决策原则，不是具体的输出内容
- Skill 是给 Student 看的指令，要清晰、可操作
- 对"感觉类"知识优先用示例迁移——利用 Student 的模仿能力，而非只依赖指令遵循
- 优先写回那些在 diff / reasoning diff 中重复出现、且能解释高权重失败或明显质量落差的知识
- 如果 eval 分数已经很高，但 diff / reasoning diff 仍揭示出关键质量差距，仍然必须迁移知识；不要因为“分数够了”而空转
- 这是第 {iteration} 轮优化"""


def diff_evaluate_prompt(
    baseline_dir: str,
    output_dirs: list[str],
    diff_eval_path: str,
    failure_modes_path: str = "",
) -> str:
    """DIFF_EVALUATE: Teacher 逐段对比标杆输出与 Student 输出。"""

    output_list = "\n".join(
        f"- Student 输出 {i}: 读取 `{d}` 中的所有文件" for i, d in enumerate(output_dirs)
    )
    failure_modes_section = ""
    if failure_modes_path:
        failure_modes_section = f"""

## 已知失败模式目录

读取 `{failure_modes_path}`。
优先关注这些高价值失败模式是否仍有遗漏，也允许发现新的重要差距。"""

    return f"""你是对比分析专家（Teacher），负责逐段对比标杆输出与 Student 输出，找出 assertions 可能未覆盖的差距。

## Teacher 标杆输出

读取 `{baseline_dir}` 中的 output_*/ 目录

## Student 输出

{output_list}
{failure_modes_section}

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
                    "weight": 2,
                    "evaluation_method": "code | judge",
                    "code_check": {{
                        "type": "file_exists",
                        "path": "brief.md"
                    }}
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
- 如果 suggested_assertion 可以用确定性规则检查，优先给出 `evaluation_method: "code"` 和完整 `code_check`
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
    failure_modes_path: str = "",
) -> str:
    """BLIND_EVAL: Teacher 不看 assertions，直接对 Student 输出打分。"""

    output_list = "\n".join(
        f"- 输出 {i}: 读取 `{d}` 中的所有文件" for i, d in enumerate(output_dirs)
    )
    failure_modes_section = ""
    if failure_modes_path:
        failure_modes_section = f"""

## 已知高价值失败模式

读取 `{failure_modes_path}`。
可参考这些失败模式理解什么问题最重要，但不要机械复述；你的任务是独立识别当前输出真正的薄弱点。"""

    return f"""你是质量评估专家（Teacher），负责对 Student 的输出进行独立质量评估。

**重要：不要参考任何 assertions 或检查项，完全基于你的专业判断。**

## Student 输出

{output_list}
{failure_modes_section}

## 任务

对每个输出进行全面质量评估：

1. 满分 10 分，从专业角度打分
2. 列出所有扣分项及扣分理由
3. 识别输出中的薄弱维度（`weak_dimensions`）
4. 识别 3-5 个最值得补进评测闭环的未覆盖维度（`uncovered_dimensions`）

`weak_dimensions` 与 `uncovered_dimensions` 要求：
- 尽量原子化，一条只描述一个能力缺口
- 用可转成 assertion 的方式表述，不要用空泛形容词
- 若已知失败模式目录中有对应项，优先沿用其术语
- 只有在你认为该维度当前确实影响质量、且值得长期追踪时，才放进 `uncovered_dimensions`

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
            "weak_dimensions": [
                "忽略已知限制条件",
                "没有明确点出上线风险"
            ],
            "deductions": [
                {{
                    "dimension": "忽略已知限制条件",
                    "reason": "扣分理由",
                    "points": -1
                }},
                {{
                    "dimension": "没有明确点出上线风险",
                    "reason": "扣分理由",
                    "points": -2
                }}
            ]
        }}
    ],
    "overall_blind_score": 0.70,
    "uncovered_dimensions": [
        "明确覆盖输入中的限制条件和适用范围",
        "单独总结 rollout 或 launch 风险"
    ]
}}
```

## 重要

- 完全独立评估，不要参考任何 assertions
- 评分要严格、客观，基于专业标准
- deductions 要具体——精确描述问题所在
- 每条 deduction 尽量绑定一个 `dimension`
- `uncovered_dimensions` 只保留最值得进入长期评测闭环的 3-5 项，不要泛滥
- overall_blind_score = 所有 samples 的 blind_score 的平均值 / max_score"""
