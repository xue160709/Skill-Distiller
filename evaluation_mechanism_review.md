# 2026-04-05_233559 测评机制复盘与重设计记录

## 目的

保存本轮关于 `Workspace/2026-04-05_233559` 的完整复盘结论，包括：

- 在真实文件中发现了哪些问题
- 这些问题分别属于真失败、规则/实现误伤、还是管线 bug
- 这些问题为什么会出现
- 现有测评机制的结构性缺陷是什么
- 新的测评机制应该如何分层、作用域化和稳定化
- 可以如何落地到 `assertions`、`report` 和 `distiller.py`

---

## 一、基于真实文件的续篇结论

### 1. `no_markdown_headers_in_body`

结论：

- `iter_1` 是规则/实现误伤
- `iter_2` 是真失败

证据：

- `iter_1/output_0/article.md` 正文没有 `# / ## / ###`
- 但 `iter_1/output_0/craft.md` 里有 Markdown 标题
- 当前实现中，`not_contains_any` 在未指定 `path` 时会扫描整个输出目录下的全部文本文件

因此：

- 断言语义写的是“文章正文”
- 代码实际扫的是“整个输出目录”
- 造成 `craft.md` 污染了 `article.md` 的评测结果

改进建议：

- 该类 assertion 必须显式设置 `code_check.path = "article.md"`
- 所有“成文类 code assertion”都应只评最终稿，不评草稿和过程文件

### 2. `no_numbered_action_list` / `no_numbered_skill_list`

结论：

- 属于纯管线 bug，不是文章质量问题

证据：

- `distiller.py` 支持的类型是 `no_pattern_match`
- 当前 assertion 使用的是 `regex_not_found`
- `prompts.py` 里列出的支持类型又没有把 `no_pattern_match` 列进去

因此：

- Teacher 生成了运行器不支持的 `code_check.type`
- evaluator 长期把这类断言判成失败
- 这是“接口契约不一致”，不是 Student 内容失败

改进建议：

- 把这两条断言改为：

```json
{
  "type": "no_pattern_match",
  "path": "article.md",
  "pattern": "第[一二三四五六七八九十]，"
}
```

- 在 `prompts.py` 的支持类型列表中正式加入 `no_pattern_match`
- 最好由运行时代码自动导出支持类型，而不是手写

### 3. 字数问题与“压稿”

结论：

- `iter_1` 的略超字数属于真失败，但也暴露了目标过硬
- `iter_2` 的明显过短属于真失败，并且是优化信号冲突导致的质量回退

观察：

- `iter_1/input_0` 明显偏长，按现有 900–1100 区间判 fail，与条文本身一致
- `iter_2/input_0` 又为了压回约束，直接缩到明显偏短
- 盲评中“论证一带而过、展开不足”与这一现象一致

因此：

- 这不只是打分噪声
- 而是系统同时施加了太多风格/结构约束，却没有明确优先级

改进建议：

- 字数规则改成分段惩罚而不是硬切
- 在总目标中明确“论证完整性优先于贴线”

### 4. 与标杆强绑定的 judge 断言

结论：

- 在本批输出上，对错参半
- 有些判罚合理，有些已开始外溢或过拟合

合理例子：

- `iter_1 input_0` 缺少马斯克本人行为反转
- `source_figure_behavior_used_as_reversal` 判 fail 是说得通的

过拟合/外溢例子：

- `human_advantage_has_dual_axes` 被应用到非 AI 文章
- `ending_is_open_binary_question_not_advice_list` 被当成通用结尾标准

因此：

- 一部分 judge 断言是在评真实质量
- 另一部分 judge 断言是在要求“长得像本轮 Teacher 标杆”

### 5. 当时整理出的现象分类表

| 现象 | 性质 |
|------|------|
| `iter_1` `no_markdown` fail 但 `article.md` 无 `#` | 实现误伤 |
| `regex_not_found` 长期失败 | 管线 bug |
| `iter_2` 标题行 `# ...` | 真失败 |
| `iter_1` 略超字数、`iter_2` 明显过短 | 真失败，但有目标冲突 |
| 人物反转、水管工、双数据点等 judge | 部分合理，部分需 scoped 化 |

---

## 二、为什么会出现这些问题

核心原因不是某条断言单独写错，而是现有机制把几件本应分开的事情混在了一起：

1. 在评最终交付物
2. 在评 Student 是否学会某种写作套路
3. 在评本轮 Teacher 从标杆里抽出来的新偏好

混在一起后，出现了以下结构性问题。

### 1. 评测对象边界不清

现在很多规则语义上是在评 `article.md`，但实现默认扫整个输出目录。

后果：

- `craft.md`
- `reasoning_*.md`
- 自检文件

这些过程产物都会污染最终稿评测。

于是：

- “过程文件违规，正文背锅”
- 规则失去可解释性

### 2. Assertion schema 与执行器不同步

Teacher 看到的支持类型和运行器实际支持的类型不是同一套。

后果：

- Teacher 会生成看起来合理、但运行器不认识的 `code_check.type`
- evaluator 只会把它判成失败
- 系统表面上是在“更严格评测”，实际上是在“累积假失败”

### 3. 断言持续膨胀，但没有作用域

很多新断言来自少数标杆样本的高分特征，却被提升成全局规则。

后果：

- AI 题特有标准去惩罚非 AI 题
- 有名人输入的结构要求去惩罚普通题目
- 宏大议题的结尾偏好去惩罚操作型文章

也就是说，系统把“局部经验”误当成了“通用能力”。

### 4. 每轮都在换试卷，却直接横向比总分

assertions 在持续增加、调整和升级，但报告还把每轮 pass_rate 连成收敛曲线。

后果：

- 分数不可比
- 优化是否真的有效，无法从单个总分判断
- “提升/回退”里混杂了题目变化、断言变化和实现 bug

### 5. Teacher 更容易提炼风格规则，不容易修真实根因

真正反复出现的根因是：

- 搜索缺失
- 归因薄弱
- 数据取舍差
- 无网时缺少降级策略

但这些不容易直接 prompt 化。

更容易被写进 Skill 的是：

- 标题像悬念句
- 结尾有追问
- 人物形成闭环
- 凝练一条金句

后果：

- prompt 越来越长
- 文章越来越像模板
- 真实 grounding 问题却反复存在

### 6. blind eval 位置太后，只能事后兜底

blind eval 的 uncovered dimensions 现在容易直接被转成新断言。

后果：

- assertions 不断增长
- 新断言不一定通用
- 研究性发现直接污染主 benchmark

### 7. 多重目标之间没有优先级

系统同时要求：

- 约 1000 字
- 开篇共鸣
- 人物反转
- 对仗金句
- 开放性结尾
- 数据与归因

但没有明确这些约束谁优先。

后果：

- 模型会通过“压稿”或“堆结构钩子”来满足表层规则
- 论证深度反而下降

---

## 三、对现有测评机制的总判断

一句话总结：

当前机制在用“不断膨胀的、部分 case-specific 的风格规则”去近似“文章质量”，但缺少：

- 产物边界
- 规则作用域
- schema 一致性
- 分数分层
- benchmark 稳定性

所以它会同时产生：

- 误伤
- 假失败
- 过拟合
- 分数失真

---

## 四、重设计思路：分层、作用域化、版本稳定化

### 总体目标

不要再用一个总分把所有东西揉在一起。

建议把测评拆成 4 层：

1. `Artifact Layer`
2. `Core Quality Layer`
3. `Scoped Quality Layer`
4. `Research Layer`

### 1. Artifact Layer

评最终交付物是否满足基本要求。

特点：

- 只看最终稿
- 强确定性
- 不讨论风格偏好

示例：

- `article_file_exists`
- `no_markdown_headers_in_body`
- `no_numbered_list`
- `word_count_reasonable_range`

### 2. Core Quality Layer

评跨题稳定成立的通用质量。

示例：

- 标题有转化
- 开篇建立阅读动机或共鸣
- 逻辑链完整
- 核心主张有支撑
- 结尾不空泛

特点：

- 这层应该保持稳定
- keep / rollback 主决策只看这层和 artifact 层

### 3. Scoped Quality Layer

只在特定题型或输入条件下适用。

示例：

- `source_figure_behavior_used_as_reversal`
- `human_advantage_has_dual_axes`
- `ending_is_open_binary_question_not_advice_list`
- `data_used_with_counter_intuitive_interpretation`

特点：

- 必须有 `applies_when`
- 不得默认应用到所有样本

### 4. Research Layer

用于收容 blind eval 的新发现。

特点：

- 只观察
- 不参与主分
- 先进入候选池，连续多轮跨题复现后再决定是否升级

---

## 五、新 assertion 结构设计

建议 assertion 至少包含以下字段：

- `id`
- `label`
- `description`
- `layer`
- `scope`
- `target`
- `applies_when`
- `weight`
- `severity`
- `evaluation_method`
- `failure_kind`
- `version`
- `code_check`（若为 code）

关键新增字段的意义：

### `layer`

可选：

- `artifact`
- `core`
- `scoped`
- `process`
- `research`

### `target`

可选：

- `article`
- `process`
- `both`

作用：

- 明确评最终稿还是评过程文件

### `applies_when`

控制规则生效条件。

建议支持：

- `task_type`
- `input_mentions_named_person`
- `topic_tags_any`
- `topic_tags_all`
- `article_mode`
- `uses_external_data`
- `requires_external_grounding`

### `version`

作用：

- 防止断言定义变了还拿旧分数硬比
- 为 benchmark 稳定性服务

---

## 六、断言分层示例

### Artifact 层示例

```json
{
  "id": "no_markdown_headers_in_body",
  "layer": "artifact",
  "target": "article",
  "evaluation_method": "code",
  "code_check": {
    "type": "not_contains_any",
    "path": "article.md",
    "phrases": ["# ", "## ", "### "],
    "case_sensitive": true
  }
}
```

```json
{
  "id": "no_numbered_list",
  "layer": "artifact",
  "target": "article",
  "evaluation_method": "code",
  "code_check": {
    "type": "no_pattern_match",
    "path": "article.md",
    "pattern": "第[一二三四五六七八九十]，"
  }
}
```

### Core 层示例

- `title_transforms_not_restates`
- `opening_creates_recognition_or_motivation`
- `complete_logic_chain`
- `core_claim_is_supported`
- `ending_not_generic`

### Scoped 层示例

```json
{
  "id": "human_advantage_has_dual_axes",
  "layer": "scoped",
  "applies_when": {
    "topic_tags_any": ["ai_future_work", "human_value"]
  }
}
```

```json
{
  "id": "ending_is_open_binary_question_not_advice_list",
  "layer": "scoped",
  "applies_when": {
    "article_mode": ["macro"]
  }
}
```

### Research 层示例

- `conclusion_loops_back_to_central_analogy`
- `opening_reference_is_self_contained`
- `quantified_claims_need_attribution`

---

## 七、新的评分与 keep / rollback 机制

### 不再只保留一个总分

每轮至少输出：

- `artifact_score`
- `core_score`
- `scoped_score`
- `research_observation_score`
- `blind_score`

### 主决策规则

建议：

1. `artifact_score` 必须过最低线
2. `core_score` 不能低于当前 best core score
3. 若 `blind_score - core_score` gap 明显扩大，标记过拟合风险
4. `scoped_score` 只作辅助参考
5. `research` 层从不参与 keep / rollback

### 这样做的好处

- 修 bug 不会污染主曲线
- 新增 scoped 断言不会让主分不可比
- blind eval 能发挥“过拟合报警器”的作用

---

## 八、blind eval 的正确接入方式

### 现在的问题

blind eval 一发现新维度，系统就容易直接把它升级成新 assertion。

### 建议改法

先进入候选池，不直接进主 benchmark。

候选项升级条件：

- 至少连续 2 轮出现
- 至少跨 2 个不同输入主题出现
- 可以稳定写成清晰定义
- 不是某一篇 Teacher 标杆的局部偏好
- 不和现有 core 断言高度重复

也就是说：

- blind eval = assertion 研发池
- 不是下一轮立即涨分母的入口

---

## 九、Teacher Optimize 阶段的治理原则

优化阶段不应再默认：

“发现一个差距 -> 加一条断言 -> 改 Skill”

应该先判断问题类型：

- `artifact_bug`
- `pipeline_bug`
- `core_skill_gap`
- `scoped_skill_gap`
- `teacher_style_preference`

对应动作：

- `artifact_bug`：修 evaluator / schema，不改 Skill
- `pipeline_bug`：修实现，不改 Skill
- `core_skill_gap`：允许改 Skill
- `scoped_skill_gap`：谨慎改 Skill，且需 scoped 化
- `teacher_style_preference`：放入 research pool，不进主断言

这是为了防止系统继续长成：

- 凡出问题都往 prompt 里塞规则

---

## 十、过程文件与最终稿必须彻底分开

建议目录语义更清晰：

- `deliverable/article.md`
- `process/craft.md`
- `process/reasoning_0.md`
- `process/self_check.md`

这样：

- evaluator 默认只看 `deliverable/`
- process assertions 才显式看 `process/`

即使短期不改目录，也至少要做到：

- `target = article` 默认绑定 `path = article.md`

---

## 十一、字数机制建议

不要用单一硬阈值。

建议改成分段惩罚：

- 900–1100：满分
- 800–899 或 1101–1200：轻微扣分
- 700–799 或 1201–1300：明显扣分
- `<700` 或 `>1300`：重扣

同时加入原则：

- 字数不应压倒论证完整性
- 若为了满足字数约束而明显压稿，应在 blind eval 中记为质量回退

---

## 十二、建议保留与降级的断言思路

### 更接近 Core 的

- `title_transforms_not_restates`
- `opening_creates_recognition`
- `complete_logic_chain`
- `ending_is_actionable_or_resonant`
- `word_count_reasonable_range`
- `no_markdown_headers_in_body`
- `basic_source_attribution_if_claims_used`

### 应降到 Scoped 的

- `human_advantage_has_dual_axes`
- `ending_is_open_binary_question_not_advice_list`
- `source_figure_specific_answer_acknowledged_and_transcended`
- `opener_figure_used_as_resolution_twist`
- `title_creates_question_not_reveals_conclusion`
- `data_used_with_counter_intuitive_interpretation`

原因：

- 它们都更依赖题型、输入形式或议题类型
- 不适合作为全局通用规则

---

## 十三、最低成本可落地的六步

如果不想一次大改，建议先做：

1. 所有成文类 code assertion 补 `path: "article.md"`
2. 在 prompt 支持列表中加入 `no_pattern_match`
3. assertions 增加 `layer`
4. keep / rollback 只看 `artifact + core`
5. assertions 增加最简单的 `applies_when`
6. report 增加失败分类

这六步已经能大幅缓解：

- 误伤
- 假失败
- 分数不可比
- 跨题过拟合

---

## 十四、建议中的 schema 草案摘要

### schema 核心字段

- `id`
- `label`
- `description`
- `layer`
- `scope`
- `target`
- `applies_when`
- `weight`
- `severity`
- `evaluation_method`
- `failure_kind`
- `source`
- `version`
- `code_check`

### code_check 支持类型

- `file_exists`
- `contains_all`
- `contains_any`
- `not_contains_any`
- `max_sentences_per_paragraph`
- `no_pattern_match`

要求：

- 运行器支持什么，prompt 就暴露什么
- 不允许 Teacher 自由发明不存在的 code type

---

## 十五、建议中的 `distiller.py` 伪代码方向

### 1. 为每个 input 生成 metadata

```python
derive_sample_metadata(input_text) -> metadata
```

可先用启发式：

- 是否提及具名人物
- 是否是 AI / 人类价值 / 技能安全类
- 是 macro 还是 operational 文章
- 是否需要 grounding

### 2. 按 `applies_when` 过滤断言

```python
assertion_applies(assertion, metadata) -> bool
```

### 3. `target=article` 默认绑定 `article.md`

```python
resolve_default_target_path(assertion)
```

### 4. 分层算分

```python
split_results_by_layer(results)
weighted_score(items)
```

### 5. 失败分类

```python
classify_failure(assertion, result)
```

建议至少分成：

- `true_failure`
- `rule_mismatch`
- `scope_mismatch`
- `pipeline_bug`

### 6. 新的决策逻辑

```python
artifact_score >= artifact_floor
and core_score >= best_core
and blind_gap not too large
```

---

## 十六、建议中的新 report 结构

建议 report 分成以下部分：

### A. Summary

- iteration
- decision
- best core score
- current core score
- blind score

### B. Score Breakdown

- artifact
- core
- scoped
- research
- blind

### C. Failure Classification

- true failures
- rule / scope mismatches
- pipeline bugs

### D. Optimization Diagnosis

- 本轮改善了什么
- 没改善什么
- 是否出现 prompt 过拟合

### E. Research Candidates

- blind eval 暂存候选项
- 不直接进入主 benchmark

---

## 十七、这次讨论的最终总结

### 现有机制最核心的问题

不是“不够严格”，而是“没有分层、没有作用域、没有稳定 benchmark”。

### 直接后果

- 过程文件污染最终稿评测
- 断言 schema 与执行器脱节
- case-specific 偏好冒充通用能力
- 每轮分数不可比
- prompt 越来越像模板，但 grounding 问题没被修掉

### 新机制的核心原则

- 成文与过程分开
- 通用质量与题型专属分开
- 主 benchmark 与研究候选分开
- schema 与运行器单一真相源
- blind eval 用来发现盲点，不直接涨分母

### 推荐方向

把系统从“不断堆规则的总分游戏”，改造成：

- 一个有层级
- 有作用域
- 有版本
- 可解释
- 可横向比较

的测评系统。

---

## 十八、后续可执行项

下一步如果继续推进，可以做：

1. 新建 `assertions.schema.json`
2. 迁移 `assertions.json` 到分层结构
3. 在 `distiller.py` 中加入 metadata / applies_when / layer score
4. 把 `prompts.py` 中支持的 code 类型改为运行时注入
5. 升级 report 结构
6. 增加 failure classification

这样之后，再看后续 Workspace 的迭代，结论会比当前机制稳定得多。
