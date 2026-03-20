# Skill Distiller 改进方案

> 当前系统能有效迁移"可编码为规则的显式知识"，但对深层推理、审美判断等隐性知识存在天花板。以下 4 个改进点针对这些瓶颈。

---

## 1. 对比信号（Diff-based Evaluation）

### 问题

Teacher 预设 assertions 后只检查 Student 是否满足。但 Student 的真实问题可能根本不在 assertions 覆盖范围内。

### 改进

在 EVALUATE 阶段，让 Teacher 把 baseline 输出和 Student 输出逐段对比，找出 assertions 未覆盖的差距。

### 触发时机

在现有 EVALUATE 之后新增一步 `DIFF_EVALUATE`，每轮执行。

### 输入

- `baseline/output_{i}/` — Teacher 标杆输出
- `iter_N/output_{i}/` — Student 当轮输出

### 输出

写入 `iter_N/diff_eval.json`：

```json
[
  {
    "input_id": 0,
    "diffs": [
      {
        "teacher_fragment": "16GB 的 MacBook 跑赢了 32GB 的竞品——代价是你永远无法自己加内存",
        "student_fragment": "M4 芯片采用了统一内存架构。这种架构有很多优点。它可以提升性能，同时降低功耗。",
        "gap": "Student 用泛化描述替代了具体的对比论证",
        "teacher_pattern": "用具体数字 + 转折构建张力",
        "suggested_assertion": {
          "id": "claim_has_concrete_comparison",
          "check": "每个技术论断至少附带一个具体的对比（数字、产品、场景）",
          "weight": 2
        }
      }
    ]
  }
]
```

### 如何融入现有流程

1. EVALUATE 照常运行，产出 `eval.json`
2. 新增 `DIFF_EVALUATE`，产出 `diff_eval.json`
3. OPTIMIZE 阶段同时读取 `eval.json` 和 `diff_eval.json`，`suggested_assertion` 自动追加到 assertions 池

### 预期效果

- 发现 assertions 盲区中的真实差距
- 自动补充更有针对性的检查项
- 让 assertions 集合随迭代变得更全面

---

## 2. Student 自省（Student Reasoning）

### 问题

Teacher 只看 Student 的输出结果，不知道 Student 是"理解了但没做好"还是"根本没理解"。这两种情况需要不同的优化策略：
- 不知道规则 → 在 Skill 中补充规则
- 理解错了规则 → 在 Skill 中加正反例纠正

### 改进

让 Student 执行任务时同时输出 reasoning，然后 Teacher 对比两份 reasoning。

### 触发时机

修改 EXECUTE 阶段的 prompt，要求 Student 同时产出 reasoning。

### 输入（Teacher 对比阶段）

- `baseline/reasoning_{i}.md` — Teacher 推理
- `iter_N/reasoning_{i}.md` — Student 推理

### 输出

写入 `iter_N/reasoning_diff.json`：

```json
[
  {
    "input_id": 0,
    "reasoning_gaps": [
      {
        "topic": "结尾推进",
        "teacher_reasoning": "从现象推到本质：重复性工作消失后，人类价值将被重新定义",
        "student_reasoning": "理解为'加一句展望未来的话'，写了'让我们拭目以待'",
        "gap_type": "misunderstanding",
        "fix_strategy": "在 Skill 中加入正反例，而不是重复规则",
        "skill_patch": "## 结尾推进\n✓ 好的推进：'重复劳动消失后，人的价值将被重新定义'\n✗ 不是推进：'让我们拭目以待'、'未来将更加美好'"
      }
    ]
  }
]
```

### gap_type 分类

| 类型 | 含义 | 优化策略 |
|------|------|----------|
| `missing` | Student reasoning 中完全没提到这个维度 | Skill 中补充规则 |
| `misunderstanding` | 提到了但理解偏差 | Skill 中加正反例 |
| `shallow` | 方向对但深度不够 | Skill 中加推理链示范 |

### 如何融入现有流程

1. 修改 `execute_prompt`，要求 Student 输出 `reasoning_{i}.md`
2. 新增 `REASONING_DIFF` 步骤（在 EVALUATE 之后）
3. OPTIMIZE 阶段根据 `gap_type` 选择不同的知识迁移方式

### 预期效果

- 精准定位 Student 的认知偏差
- 避免"规则写了但 Student 理解错了"的反复循环
- 让 OPTIMIZE 从"加规则"升级为"纠正认知"

---

## 3. Assertions 覆盖率检查（Blind Evaluation）

### 问题

pass_rate 0.90 看起来很高，但如果 assertions 只覆盖了质量的 60%，实际水平可能只有 Teacher 的 54%。系统可能在假收敛状态下停下来。

### 改进

每隔 N 轮，让 Teacher 做一次盲评——不看 assertions，直接对 Student 输出打分并说明扣分原因。对比 assertions pass_rate 和盲评分数的 gap。

### 触发时机

每 3 轮执行一次（可配置），或在系统判定 `plateau` 时触发。

### 输入

- `iter_N/output_{i}/` — Student 当轮输出（不提供 assertions）

### 输出

写入 `iter_N/blind_eval.json`：

```json
{
  "iteration": 6,
  "samples": [
    {
      "input_id": 0,
      "blind_score": 6,
      "max_score": 10,
      "deductions": [
        {"reason": "文章整体读起来像说明书，没有节奏感", "points": -2},
        {"reason": "论点之间缺乏逻辑连接词", "points": -1},
        {"reason": "用词单调，反复出现'进行''实现''推动'", "points": -1}
      ]
    }
  ],
  "overall_blind_score": 0.60,
  "assertions_pass_rate": 0.88,
  "coverage_gap": 0.28,
  "uncovered_dimensions": [
    "文章节奏感和可读性",
    "段落间逻辑过渡",
    "用词多样性"
  ],
  "action": "gap > 0.2，assertions 覆盖率不足，需要补充检查项并继续迭代"
}
```

### 决策逻辑

```python
if coverage_gap > 0.2:
    # assertions 覆盖率不足，不应终止
    # 将 uncovered_dimensions 转化为新 assertions
    override_plateau = True
elif coverage_gap <= 0.1:
    # assertions 覆盖良好，可信赖 pass_rate
    pass
```

### 如何融入现有流程

1. 在 `should_stop` 判定 plateau 时触发盲评
2. 如果 `coverage_gap > 0.2`，覆盖 plateau 判定，继续迭代
3. 将 `uncovered_dimensions` 传给 OPTIMIZE，转化为新 assertions

### 预期效果

- 防止假收敛
- 确保 assertions pass_rate 真正反映输出质量
- 系统知道什么时候该停、什么时候还不够

---

## 4. Few-shot 替代纯规则（Example-based Knowledge Transfer）

### 问题

所有知识都以规则形式写入 Skill（"当...则...因为..."）。有些知识——如节奏感、张力、语气——用规则描述很别扭，越写越长，Student 反而更容易误解或机械执行。

### 改进

OPTIMIZE 阶段，Teacher 判断每条知识适合用规则还是示例迁移。对于"感觉类"知识，直接从 baseline 输出中截取片段作为正例，配合反例写入 Skill。

### 判断标准

| 知识类型 | 特征 | 迁移方式 |
|----------|------|----------|
| 结构规则 | 可量化（"不超过4句"） | 规则 |
| 决策逻辑 | 条件明确（"当有因果关系时用递进"） | 规则 |
| 风格/语感 | 难以量化（"有张力""节奏好"） | 示例 |
| 复合技巧 | 多个要素同时起作用 | 示例 + 简短说明 |

### 输出变化

OPTIMIZE 产出的 `skill.md` 中，部分知识以示例形式呈现：

```markdown
## 转折策略

用转折构建张力，把优势和代价放在同一个句子里碰撞。

示例：
✓ "16GB 的 MacBook 跑赢了 32GB 的竞品——代价是你永远无法自己加内存"
✓ "Rust 消灭了一整类内存 bug，但编译器成了你最严厉的代码审查员"
✗ "M4 芯片性能很强。但是它也有缺点。"（拆成两句就没有张力了）
```

### 如何融入现有流程

1. 修改 `optimize_prompt`，要求 Teacher 对每条迁移的知识标注 `transfer_type: "rule" | "example"`
2. 当 `transfer_type: "example"` 时，Teacher 从 baseline 输出中截取正例片段
3. `change.md` 中记录每条知识的迁移方式

### 约束

- 示例从 baseline 输出中截取，不凭空编造
- 每条示例不超过 2 行，避免 Skill 膨胀
- 正例 + 反例成对出现，帮助 Student 区分边界

### 预期效果

- 对"感觉类"知识的迁移效率大幅提升
- Skill 更易读——规则告诉 Student "该做什么"，示例告诉 Student "做成什么样"
- 利用 Student 模型本身的模仿能力，而不是只依赖指令遵循能力

---

## 实施优先级

| 优先级 | 改进点 | 理由 |
|--------|--------|------|
| P0 | 4. Few-shot 替代纯规则 | 改动最小（只改 optimize_prompt），收益最直接 |
| P1 | 1. 对比信号 | 解决 assertions 盲区问题，提升评估质量 |
| P1 | 2. Student 自省 | 解决"写了规则但没用"的核心痛点 |
| P2 | 3. 覆盖率检查 | 防止假收敛，但需要额外的 LLM 调用成本 |
