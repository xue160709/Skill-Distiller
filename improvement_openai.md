# 面向迁移能力与推理弹性的改进方案

> 当前 Skill Distiller 更擅长蒸馏“可显式表达的任务知识”，例如流程、检查点、格式约束和局部 heuristics。  
> 如果目标是进一步逼近大模型的跨领域迁移能力与临场推理弹性，就需要把蒸馏对象从“答案和规则”升级为“抽象、分叉点、自检和适应过程”。

---

## 核心判断

这两类能力很难通过单纯追加 `SKILL.md` 规则来获得：

- 跨领域迁移能力依赖“抽象映射”，不是记住某个领域的表面套路
- 临场推理弹性依赖“动态调参和错误恢复”，不是一次性把答案写对

因此，系统要蒸馏的不只是：

- Teacher 给了什么答案
- Teacher 提了哪些 assertions
- Student 哪些输出不合格

还要显式蒸馏：

- Teacher 如何识别任务本质
- Teacher 在什么节点切换策略
- Teacher 如何处理不确定信息
- Teacher 如何发现自己可能错了并回退

---

## 1. 从“任务规则”升级为“抽象层规则”

### 问题

现在的优化闭环容易沉淀出大量任务层规则，例如：

- “当用户要求总结时，先给结论再给细节”
- “输出里要包含 3 个要点”

这类规则能提升专项表现，但很难迁移到陌生场景。

### 改进

在 `OPTIMIZE` 阶段，要求 Teacher 为每轮新增知识同时生成两层表示：

- `task_rule`：具体任务规则
- `abstract_principle`：更高层抽象原则

例如：

```json
{
  "task_rule": "当输入信息很多时，先总结再展开",
  "abstract_principle": "优先降低认知负荷；先建立全局框架，再填充局部细节"
}
```

写入 `SKILL.md` 时，优先保留抽象原则，再附 1 个任务内示例。

### 效果

- Student 更容易把旧任务经验映射到新任务
- Skill 不会只是一堆领域特定模板
- 后续做跨领域评测时更容易复用

---

## 2. 增加“跨场景变体集”，逼 Student 学抽象而不是背题

### 问题

如果同一个技能只在单一输入分布上优化，Student 学到的往往是：

- 关键词触发
- 固定格式
- 局部措辞模仿

这会造成高分低迁移。

### 改进

在 `Input/` 之外增加一类变体 case。每个核心任务至少生成 4 类变体：

- 同目标，不同领域外壳
- 同目标，不同输入格式
- 同目标，加入干扰信息
- 同目标，故意缺失关键信息

例如一个“结构化分析”任务，可以同时提供：

- 技术文章
- 产品需求
- 法律条款摘要
- 混杂聊天记录

但都要求 Student 完成同类结构化抽取。

### 如何融入现有流程

在 `BASELINE` 后新增 `VARIANT_EXPAND`：

1. Teacher 为每个原始 case 生成少量受控变体
2. Student 在原始 case 和变体 case 上同时执行
3. EVALUATE 时分别计算：
   - in-domain 分数
   - cross-variant 分数

### 新增指标

```json
{
  "in_domain_score": 0.91,
  "cross_variant_score": 0.68,
  "transfer_gap": 0.23
}
```

### 效果

- 可以直接测出“会做这题”和“会做这类题”的差别
- OPTIMIZE 阶段会被迫迁移更高层的知识

---

## 3. 蒸馏“决策分叉点”，而不是只蒸馏最终答案

### 问题

大模型的弹性常常不体现在最终输出，而体现在中间决策：

- 要不要先澄清
- 当前信息够不够
- 该保守回答还是大胆假设
- 当前方法失败后要换哪种方法

如果这些分叉点不被记录，Student 很容易学成僵硬执行器。

### 改进

在 `BASELINE` 和 `EXECUTE` 阶段都要求输出结构化决策日志，例如：

```json
[
  {
    "step": 1,
    "decision": "先判定任务类型",
    "options_considered": ["直接回答", "先分类再回答"],
    "chosen": "先分类再回答",
    "reason": "输入中存在多重目标，直接回答容易遗漏约束"
  },
  {
    "step": 2,
    "decision": "信息不足时是否澄清",
    "chosen": "在答案中显式声明假设并继续",
    "reason": "当前任务允许合理假设，不值得中断流程"
  }
]
```

Teacher 在 `EVALUATE` 时不只比答案，还比：

- 是否识别了关键分叉点
- 是否选择了合理策略
- 是否给出了正确的切换理由

### 如何融入现有流程

- 修改 `baseline` prompt：产出 `decision_trace.json`
- 修改 `execute` prompt：Student 也产出 `decision_trace.json`
- 新增 `DECISION_EVAL`：专门比较分叉点质量

### 效果

- 蒸馏的不只是“写什么”，还有“何时换脑子”
- 对临场变化的适应能力提升最大

---

## 4. 把 assertions 从“结果检查”扩展到“中间判断检查”

### 问题

当前 assertions 更容易描述输出特征，例如：

- 是否包含结论
- 是否满足格式
- 是否覆盖关键点

但迁移能力和弹性往往体现在中间层。

### 改进

新增两类 assertions：

- `process_assertion`：检查是否执行了关键思考步骤
- `fallback_assertion`：检查在不确定或失败时是否采取了合理降级策略

示例：

```json
[
  {
    "id": "identify_constraints_before_answering",
    "type": "process_assertion",
    "check": "在给出方案前，先识别任务约束和成功标准"
  },
  {
    "id": "state_assumptions_when_info_missing",
    "type": "fallback_assertion",
    "check": "信息不足时，显式说明假设或限制，而不是伪装成确定结论"
  }
]
```

### 效果

- Student 会逐渐形成更稳的思考骨架
- 面对陌生问题时，不容易因为缺少模板而崩掉

---

## 5. 单独蒸馏“自检与回退能力”

### 问题

很多强模型的弹性，其实来自第二拍：

- 先做一个初步解
- 再扫描风险
- 发现问题后回退和修正

如果系统只看 first-pass 输出，就蒸不到这部分能力。

### 改进

把 `EXECUTE` 改成两阶段：

1. `draft`
2. `self_review`

Student 在提交最终答案前，必须先输出：

- 最可能错的 1 到 3 个点
- 这些错误的风险等级
- 是否需要重写部分答案

Teacher 在 `EVALUATE` 时给两套分数：

- `raw_score`：初稿质量
- `recovery_score`：自检后修正质量

### 示例输出

```json
{
  "draft_risks": [
    {
      "risk": "把表面格式问题误判成核心需求",
      "severity": "high",
      "fix": "重读用户目标句，重新确认主任务"
    }
  ],
  "rewrite_required": true
}
```

### 效果

- Student 学到的不是“别犯错”，而是“犯错后怎么救”
- 对开放环境尤其重要

---

## 6. 增加“技能选择器”，提升多任务场景的迁移表现

### 问题

小模型经常不是完全不会做，而是不知道：

- 当前任务该套哪个 skill
- skill 不匹配时该如何降级
- 多个 skill 冲突时优先谁

### 改进

把系统拆成两层：

- `router`：先判定任务类型、风险和适用策略
- `executor`：再按 skill 执行

Teacher 需要为每个 case 额外标注：

- `task_family`
- `recommended_strategy`
- `fallback_strategy`

例如：

```json
{
  "task_family": "analysis_with_missing_information",
  "recommended_strategy": "state_assumptions_then_answer",
  "fallback_strategy": "ask_for_clarification_if_high_stakes"
}
```

### 效果

- Student 对陌生任务的第一步更稳
- 多 skill 环境下更接近“会选方法”的状态

---

## 7. 引入“课程式蒸馏”，不要直接逼小模型学最高级能力

### 问题

迁移能力和推理弹性是高阶复合能力，直接优化常常导致：

- 分数波动大
- 优化方向不稳定
- Skill 越写越复杂

### 改进

按难度分层蒸馏：

1. 稳定执行固定流程
2. 适配局部变体
3. 处理冲突约束
4. 在陌生领域中完成类比迁移
5. 在信息不全时自主降级和恢复

每层单独设阈值，只有通过当前层才进入下一层。

### 效果

- 优化路径更稳定
- 更容易知道当前瓶颈在“执行”还是“迁移”

---

## 对现有四阶段流程的具体改造

### baseline

除了 baseline 输出本身，再产出：

- `decision_trace.json`
- `abstract_principles.json`
- `task_family.json`

### execute

除了 Student 输出本身，再产出：

- `decision_trace.json`
- `draft_risks.json`
- `self_review.md`

### evaluate

拆成 4 个子评分：

- `output_score`
- `process_score`
- `transfer_score`
- `recovery_score`

最终不要只看一个 pass rate，而是组合判断。

### optimize

限制每轮最多迁移 3 条知识时，优先级建议改为：

1. 抽象原则缺失
2. 分叉点判断错误
3. 自检/回退缺失
4. 最后才是表层格式问题

这样更符合“先蒸高杠杆知识，再修表面细节”。

---

## 推荐新增数据结构

可以考虑在 `Workspace/iter_N/` 下新增：

- `decision_eval.json`
- `transfer_eval.json`
- `recovery_eval.json`
- `variant_scores.json`
- `abstract_knowledge.json`

这样后续就能区分：

- Student 是不会执行
- 还是不会迁移
- 还是不会在失败时修正

---

## 实施优先级

| 优先级 | 改动 | 原因 |
|--------|------|------|
| P0 | 跨场景变体集 + transfer_score | 最直接衡量迁移能力，收益最大 |
| P0 | 决策分叉点日志 + decision_eval | 最直接提升推理弹性 |
| P1 | 自检与回退机制 | 能显著提升开放任务稳定性 |
| P1 | 抽象层规则 | 防止 Skill 退化成领域模板库 |
| P2 | Skill 选择器 | 对多任务环境价值高，但实现稍复杂 |
| P2 | 课程式蒸馏 | 更适合系统成熟后再引入 |

---

## 最终判断

如果不做这些改造，这套系统更像：

- “专项能力蒸馏器”
- “工作流压缩器”
- “判题标准对齐器”

做了这些改造后，它才更有机会逼近：

- “跨场景能力迁移器”
- “策略性思考蒸馏器”
- “带恢复能力的小模型训练框架”

换句话说，想把大模型的迁移能力和临场弹性给到小模型，关键不是继续增加答案样本，而是把 Teacher 的抽象、决策、自检和回退过程也变成可训练对象。
