# Distiller 断点续跑设计

## 背景

当前 `distiller.py` 只支持“新开一次实验”：

```bash
python3 distiller.py
python3 distiller.py path/to/config.json
```

启动后会固定执行以下流程：

1. 创建新的 `Workspace/<timestamp>/`
2. 复制初始 skill 到本次实验目录
3. 安装初始 skill 到 `.claude/skills/<skill_name>`
4. 重新执行 baseline
5. 从 `iter_0` 开始完整迭代

这意味着只要运行过程中出现 LLM/API 错误、网络波动或进程中断，之前已经完成的 baseline、assertions、评估结果和部分迭代都无法直接复用，只能重新开始。


## 需求

希望 `distiller.py` 增加一个可选的“恢复已有实验”参数，使其可以继续执行某个已有 `Workspace/<timestamp>` 下未完成的实验。

目标调用方式：

```bash
python3 distiller.py config.run3.json Workspace/2026-04-06_091409
```

这里第二个参数表示：

- 不创建新的 workspace
- 不重新跑 baseline
- 复用已有实验目录中的全部中间产物
- 从上一次中断的位置继续往后执行

核心目标是支持“继续同一次实验”，而不是“参考旧实验重新开一轮”。


## 非目标

本次设计不追求以下能力：

- 不做 step 级别的细粒度恢复
  例如某一轮已经执行完 `execute`，但还没做完 `evaluate`，恢复时不要求从 `evaluate` 接着跑
- 不做跨 workspace 的结果合并
- 不做自动修复损坏实验目录
- 不改变现有评分、保留/回退、assertions 只增不减等核心实验语义


## 推荐恢复语义

建议采用“按轮次恢复，不按 step 恢复”的方案。

### 新启动

```bash
python3 distiller.py config.run3.json
```

行为保持不变：

- 创建新的 `Workspace/<timestamp>/`
- 执行 baseline
- 从 `iter_0` 开始

### 恢复启动

```bash
python3 distiller.py config.run3.json Workspace/2026-04-06_091409
```

行为调整为：

- 使用这个已有 `run_dir`
- 跳过 workspace 初始化
- 跳过 baseline
- 从磁盘恢复实验状态
- 从“最后一个已提交 iteration 的下一轮”继续运行


## 为什么选择“按轮次恢复”

### 原因 1：实现简单且稳

当前主循环天然存在一个清晰的“提交点”：

- 每轮会生成 `entry`
- 写入 `log.json`
- 然后才继续下一轮

因此可以把 `log.json` 视为“这轮已经真正完成并提交”的标志。

### 原因 2：避免 step 恢复的复杂状态机

如果要支持 step 级恢复，就需要判断：

- `execute` 是否全部完成
- `evaluate` 是否已完成且文件可用
- `diff_evaluate` / `reasoning_diff` / `blind_eval` 是否已完成
- `optimize` 是否已经成功产出新 skill 并安装
- `best_skill`、`skill_content`、`installed_skill_dir` 是否已经切换

这会显著增加状态恢复复杂度，也更容易产生错乱。

### 原因 3：整轮重跑成本可接受

相比错误恢复的稳定性，重跑“最后一轮未提交 iteration”通常是可接受的。
只要已提交轮次不丢失，实验大部分成本已经被保留下来。


## 关键设计原则

### 1. `log.json` 是轮次提交日志

只有出现在 `log.json` 里的 iteration，才算“已完成并提交”。

### 2. 未提交轮次一律整轮重跑

如果存在 `iter_2/` 目录，但 `log.json` 中没有 `iteration = 2` 的记录，说明该轮只是部分执行过，不视为已完成。
恢复时直接从 `iter_2` 重新整轮执行。

### 3. baseline 和 assertions 完全复用

恢复运行时不应重复生成 baseline，也不应重置已有 `assertions.json`。

### 4. 当前 student 使用的 skill 应恢复到“当前最佳版本”

恢复时应先把“当前已知最佳的 skill 版本”重新安装到 `.claude/skills/<skill_name>`，再继续执行后续迭代。

- 若 `log.json` 存在且非空，则使用 `run_dir/best_skill/`
- 若 `log.json` 不存在或为空，则回退到 `run_dir/skill_v0/`

这样可以保证 student 从一致的 skill 状态继续，而不是从某个半成品 skill 继续。


## 磁盘状态的权威来源

恢复时建议使用以下文件/目录作为真相源。

### `run_dir/assertions.json`

表示当前实验累计下来的完整 assertions 集合。

用途：

- `evaluate` 时作为主评分标准
- `diff_eval` / `blind_eval` 追加新 assertions 后的最新状态

要求：

- 恢复时必须存在

### `run_dir/failure_modes.json`

baseline 生成的失败模式目录。

用途：

- 后续 `evaluate`、`diff_eval`、`blind_eval`、`optimize` 参考

要求：

- 最好存在
- 若当前系统允许其缺失并退化运行，则恢复时也可保持相同策略

### `run_dir/baseline/`

baseline 的输出与 reasoning。

用途：

- `diff_eval`
- `reasoning_diff`
- `optimize`

要求：

- 恢复时必须存在且可用

### `run_dir/log.json`

已提交 iteration 的历史日志。

用途：

- 恢复 `history`
- 恢复 `best_core_score`
- 推断下一轮 iteration 编号
- 推断实验是否已结束

语义：

- 存在且有记录：说明已经至少完成了一些 iteration
- 不存在：说明 baseline 可能已完成，但 iteration 尚未提交过

### `run_dir/best_skill/`

当前实验已确认的最佳 skill 快照。

用途：

- 恢复时重装到 `.claude/skills/<skill_name>`
- 作为继续迭代的起点

要求：

- 当 `log.json` 已存在且至少有一条已提交 iteration 时，推荐作为恢复必需目录
- 当 `log.json` 不存在或为空时，可以允许其缺失，并回退到 `skill_v0/`

### `run_dir/skill_v0/`

初始 skill 快照。

用途：

- 兜底回退
- 在极端情况下帮助判断实验起始状态
- 当 `log.json` 不存在或为空时，作为恢复时的初始 best skill

### `run_dir/iter_*`

每轮的中间产物目录。

用途：

- 判断是否存在未提交但已部分执行的轮次
- 恢复时复用目录名并覆盖该轮中间产物


## 恢复状态重建方案

### `history`

直接从 `log.json` 读取。

### `best_core_score`

- 当 `log.json` 存在且非空时：取最后一条记录中的 `best_score`
- 当 `log.json` 不存在或为空时：显式设为 `0.0`

第一版建议不要尝试使用 baseline 分数作为初始 best score，因为当前 keep/discard 的语义是“和历史最优 student 结果比较”，而不是“和 baseline 比较”。

### `best_skill`

- 当 `log.json` 存在且非空时：以 `run_dir/best_skill/SKILL.md` 为准
- 当 `log.json` 不存在或为空时：以 `run_dir/skill_v0/SKILL.md` 为准

### 当前安装 skill

恢复时先执行：

- 若 `log.json` 存在且非空：用 `run_dir/best_skill/` 覆盖 `.claude/skills/<skill_name>`
- 若 `log.json` 不存在或为空：用 `run_dir/skill_v0/` 覆盖 `.claude/skills/<skill_name>`

这样 student 后续执行时，使用的是当前已知最佳 skill；若尚无任何已提交 iteration，则从初始 skill 继续。

### `optimize_rounds_completed`

第一版建议直接取：

- `len(history)`

原因：

- 当前主循环里，每个 iteration 的 `entry` 只有在完成该轮主要流程后才会写入 `log.json`
- 因此已写入 `log.json` 的 iteration，可以视为“已完成一轮可计入历史的优化尝试”

这里不按 `action == "keep"` 计数，因为 `min_optimize_rounds` 的语义更接近“已经跑过多少轮优化流程”，而不是“成功保留了多少轮”。

### 下一轮 iteration

规则建议如下：

- 若 `log.json` 不存在或为空：从 `iter_0` 开始
- 若 `log.json` 最后一条为 `iteration = n`：下一轮从 `iter_{n+1}` 开始

但还要结合 `iter_*` 目录做补充判断：

- 如果存在 `iter_{n+1}/`，但 `log.json` 里没有这轮记录
- 说明这轮曾经开始过但未提交
- 恢复时就从 `iter_{n+1}` 整轮重跑


## 半完成轮次的处理

这是整个恢复功能的关键。

### 示例

假设目录中有：

- `log.json` 最后一条是 `iteration = 1`
- 同时存在 `iter_2/`

则说明：

- 第 0 轮和第 1 轮是已提交结果
- 第 2 轮曾经开始执行，但尚未成功提交到 `log.json`

推荐处理方式：

- 恢复时从 `iter_2` 重新整轮执行
- 不尝试从 `iter_2` 的某个中间 step 接着跑
- 开始该轮前，先删除整个 `iter_2/` 目录，再重建空目录，避免旧文件残留影响本轮结果

### 为什么这样最稳

因为当前代码并没有显式持久化“step 指针”，无法可靠判断该轮到底执行到了哪里。
例如：

- `eval.json` 存在，不代表 `diff_eval` 已跑
- `diff_eval.json` 存在，不代表 `best_skill` 已更新
- `blind_eval.json` 存在，不代表这一轮已经 append 到 log

因此最保守且一致的策略是：

- 以 `log.json` 为唯一提交依据
- 未提交轮次全部重跑


## `assertions.json` 在半完成轮次中的“污染”问题

需要明确一个现实情况：

- 某个未提交 iteration 可能已经在 `diff_eval` 或 `blind_eval` 阶段向 `assertions.json` 追加了新断言
- 之后进程中断
- 恢复时该 iteration 会被整轮重跑

这意味着：

- 重跑后的该轮，会在“已经包含这些新增 assertions”的标准下重新被评估
- 它不一定等价于“回到该轮开始前的原始断言集合再跑一次”

第一版建议明确接受这个行为，不回滚 `assertions.json`。

原因：

1. 与项目规则一致
   `assertions` 的规则本来就是“只增不减”
2. 语义上可接受
   即使某个未提交轮次提前把标准变严了，也只会让后续评估更保守，不会破坏实验正确性
3. 实现显著更简单
   如果要精确回滚，就必须为每轮保存 assertions 快照并在恢复时判定回滚点，复杂度明显上升

可选增强项：

- 在每轮开始前保存一份 `assertions.snapshot.json`
- 恢复时用于调试对比，而不是自动回滚

第一版不建议自动回滚。


## 边界规则

### 1. 恢复目录不存在

直接报错并退出。

### 2. baseline 关键产物缺失

若缺失以下关键项，拒绝恢复：

- `baseline/`
- `assertions.json`

因为这说明实验上下文不完整。

### 3. `best_skill/` 缺失

需要分情况处理。

#### 情况 A：`log.json` 存在且非空

此时推荐作为恢复失败处理，直接报错。

原因：

- 恢复后的 student 应从“当前最佳 skill”继续
- 如果没有 `best_skill/`，就需要额外推断 keep/discard 历史并重建状态，复杂度更高

#### 情况 B：`log.json` 不存在或为空

此时允许 `best_skill/` 缺失，并以 `skill_v0/` 作为兜底继续恢复。

这是一个很常见的中断场景：

- baseline 已完成
- `iter_0` 尚未提交
- 进程中断

如果这里仍强制要求 `best_skill/` 存在，会导致最常见的恢复场景无法工作。

后续若需要，可以再增加更强的兜底逻辑：

- 没有 `best_skill/` 时从 `skill_v0/` 或最近 keep 结果恢复

但第一版至少应覆盖“空日志时回退到 `skill_v0/`”。

#### 情况 C：`log.json` 不存在或为空，且 `skill_v0/` 也缺失

此时直接拒绝恢复并报错。

这说明 workspace 在初始化阶段就已经不完整，无法可靠判断实验的初始 skill 是什么。
虽然这种情况很少见，但需要作为明确的边界规则写清楚。

### 4. `log.json` 不存在

视为：

- baseline 已完成
- iteration 尚未提交过

此时恢复从 `iter_0` 开始。

### 5. `log.json` 最后一条已经带 `stop_reason`

说明这次实验已经正常结束。

第一版建议：

- 直接拒绝恢复

原因：

- “恢复未完成实验”和“基于已完成实验继续突破 max_iterations”是两个不同需求
- 混在一起会让语义变得模糊

### 6. config 与恢复目录不一致

恢复时需要增加一致性校验，避免把错误的 skill 装进已有实验目录。

第一版可做的最小校验：

- 使用 `discover_skill(PROJECT_ROOT / "SKILL")` 得到当前工作区的 `skill_name`
- 校验 `run_dir/skill_v0/` 是否存在
- 校验 `run_dir/skill_v0/SKILL.md` 是否存在

更推荐的做法：

- 在新实验初始化时额外写入 `run_meta.json`
- 其中记录：
  - `skill_name`
  - `config_path`
  - `created_at`
  - `input_case_count`

恢复时读取 `run_meta.json` 并校验：

- 当前发现到的 `skill_name` 是否与历史一致

如果不一致，直接拒绝恢复。

第一版如果不想引入 `run_meta.json`，至少要基于 `skill_v0/` 做保守校验，而不是静默继续。


## 推荐实现方案

### 命令行接口

新增一个可选参数：

```bash
python3 distiller.py [config_path] [resume_run_dir]
```

解析规则建议为：

- 无参数：使用默认 `config.json`，新开实验
- 1 个参数：视为 `config_path`，新开实验
- 2 个参数：第一个为 `config_path`，第二个为 `resume_run_dir`

第一版不建议支持只传 workspace 不传 config 的模糊用法，避免歧义。

### 主流程分支

在 `main()` 中增加两条路径：

- 新实验路径
- 恢复实验路径

恢复路径需要替代当前的：

- `setup_workspace(...)`
- `step_baseline(...)`

改为：

- 校验 `resume_run_dir`
- 加载并恢复历史状态
- 安装 `best_skill/`
- 计算下一轮 iteration
- 从该轮继续主循环

### 新增辅助函数建议

建议新增类似职责的函数：

#### `load_resume_state(run_dir, skill_name) -> state`

负责：

- 校验恢复目录完整性
- 读取 `log.json`
- 恢复 `history`
- 恢复 `best_core_score`
- 恢复 `best_skill`
- 在函数内部自行推导 `.claude/skills/<skill_name>` 路径并重装
- 计算下一轮 iteration 编号

返回值可包含：

- `run_dir`
- `installed_skill_dir`
- `best_skill_dir`
- `skill_content`
- `best_skill`
- `best_core_score`
- `history`
- `next_iteration`
- `optimize_rounds_completed`

#### `detect_next_iteration(run_dir, history) -> int`

负责：

- 基于 `log.json` 确定最后已提交 iteration
- 如果发现存在未提交的 `iter_{n+1}` 目录，则返回该轮编号

### 重跑未提交轮次时的目录策略

建议：

- 继续使用原来的 `iter_n/` 目录名
- 在真正开始这一轮前，先 `rmtree(iter_n/)`，再重建空目录

好处：

- 语义直观
- 恢复后目录结构仍连续
- 不会引入 `iter_2_retry` 之类的额外命名复杂度


## 与现有规则的兼容性

该方案与现有项目规则兼容：

- `assertions` 仍然只增不减
- optimize 每轮最多迁移 3 条知识的规则不受影响
- 子进程 cwd 仍然保持项目根目录
- 子进程模型仍通过环境变量指定
- 不需要修改 `.Codex/skills/skill-creator/` 下任何文件


## 第一版建议范围

为了尽快落地并保持稳定，第一版建议只实现以下能力：

1. 支持传入已有 `Workspace/<timestamp>` 继续运行
2. 只恢复到“轮次级别”
3. 以 `log.json` 作为唯一提交依据
4. 未提交轮次整轮重跑
5. 空日志时允许 `best_skill/` 缺失，并回退到 `skill_v0/`
6. `best_core_score` 在空日志时显式设为 `0.0`
7. 已接受 `assertions.json` 在半完成轮次中的“只增不减污染”语义，不自动回滚
8. `optimize_rounds_completed` 由 `len(history)` 恢复
9. 已结束实验拒绝恢复
10. 增加 skill 一致性校验，避免 config / run_dir 错配

不建议第一版就做：

- step 级恢复
- 自动修复缺失文件
- 在已结束实验上继续追加 iteration


## 一句话结论

推荐把断点续跑设计成：

以 `log.json` 作为已提交轮次的唯一真相源，恢复时复用原 `run_dir`、跳过 baseline，并从最后一个已提交 iteration 的下一轮继续执行；若日志为空，则以 `skill_v0/` 作为初始 best skill；若存在未提交的半完成轮次，则先清空对应 `iter_n/` 目录，再整轮重跑该轮；`assertions.json` 保持只增不减，不做回滚。
