# Skill Distiller

自动化 Skill 知识蒸馏系统。Teacher（强模型）执行任务产出标杆与推理，提取 assertions 作为评分标准；Student（弱模型）按 SKILL.md 执行，Teacher 评分；分数高保留、低回退，迭代优化直到收敛。

> 给定一个 SKILL.md + 几个输入，`python distiller.py`，去睡觉，醒来看 report.md。

## 核心思路

借鉴 [autoresearch](https://github.com/jxnl/autoresearch) 的实验范式：

- **assertions 驱动评分** — 类似 val_bpb，客观、可复现、不漂移
- **保留或丢弃** — 数字说了算，不靠主观判断
- **assertions 只增不减** — 标准越来越严，Skill 必须越来越强

## 工作流程

```
Python 状态机
│
│  ① BASELINE
│     Teacher 执行 Skill → 标杆输出 + reasoning
│     Teacher 从 reasoning 中提取 assertions（检查项）
│
│  ┌──────────────────────────────────────────┐
│  │                                          │
│  │  ② EXECUTE                               │
│  │     Student 按当前 SKILL.md 执行          │
│  │                                          │
│  │  ③ EVALUATE                              │
│  │     Teacher 逐条检查 assertions           │
│  │     输出 pass/fail + weighted_pass_rate   │
│  │                                          │
│  │  ④ SCORE                                 │
│  │     pass_rate > best → 保留              │
│  │     pass_rate ≤ best → 丢弃，回退        │
│  │                                          │
│  │  ⑤ OPTIMIZE                              │
│  │     Teacher 分析 failed assertions        │
│  │     对照 reasoning 迁移知识到 Skill       │
│  │     可追加新 assertions                   │
│  │                                          │
│  └──────── 回到 ② ──────────────────────────┘
│
│  ⑥ 输出最终 SKILL.md + report.md
```

## 快速开始

### 1. 准备目录

```
Skill Distiller/
├── SKILL/
│   └── my-skill/          # 你的 Skill 目录
│       └── SKILL.md       # Skill prompt（含 YAML frontmatter）
├── Input/
│   ├── case_0/            # 输入用例 0（放任意文件）
│   └── case_1/            # 输入用例 1
└── config.json            # 模型配置
```

### 2. 配置模型

编辑 `config.json`：

```json
{
  "teacher": {
    "env": {
      "ANTHROPIC_AUTH_TOKEN": "your-api-key",
      "ANTHROPIC_BASE_URL": "https://api.anthropic.com",
      "ANTHROPIC_MODEL": "claude-opus-4-20250514"
    }
  },
  "student": {
    "env": {
      "ANTHROPIC_AUTH_TOKEN": "your-api-key",
      "ANTHROPIC_BASE_URL": "https://api.anthropic.com",
      "ANTHROPIC_MODEL": "claude-sonnet-4-20250514"
    }
  },
  "max_iterations": 15,
  "target_pass_rate": 0.90,
  "plateau_rounds": 3,
  "timeout": 600
}
```

### 3. 运行

```bash
python distiller.py
```

或指定配置文件：

```bash
python distiller.py path/to/config.json
```

### 4. 查看结果

```
Final/my-skill/SKILL.md    # 优化后的 Skill
Workspace/<timestamp>/
├── report.md              # 人类可读报告（含收敛曲线）
├── log.json               # 全部轮次记录
├── assertions.json        # 最终检查项集合
├── baseline/              # Teacher 标杆输出 + reasoning
├── iter_0/                # 第 0 轮 Student 输出 + 评估 + 优化
├── iter_1/
└── ...
```

## 终止条件

| 条件 | 默认值 | 说明 |
|------|--------|------|
| 达标 | `target_pass_rate ≥ 0.90` | weighted_pass_rate 达到目标 |
| 连续无提升 | `plateau_rounds = 3` | 连续 N 轮最优分数未上涨 |
| 硬上限 | `max_iterations = 15` | 最多迭代轮次 |

## 评分机制

**assertions** 是从 Teacher reasoning 中提取的可验证检查项，每条只有 pass/fail：

```json
{
  "id": "paragraph_max_4_sentences",
  "check": "每个段落不超过4句话",
  "source": "reasoning_1.md — 信息密度控制",
  "weight": 2
}
```

评分公式：

```
pass_rate = passed / total
weighted_pass_rate = Σ(passed × weight) / Σ(weight)
```

每轮 OPTIMIZE 可追加新 assertions，但**不删除已有的**——标准只增不减。

## Report 示例

```
iter  0: ████████████████████░░░░░░░░░░░░░░░░░░░░ 0.50 ✓ keep
iter  1: ████████████████████████████░░░░░░░░░░░░░ 0.70 ✓ keep
iter  2: ██████████████████████░░░░░░░░░░░░░░░░░░░ 0.55 ✗ discard
iter  3: █████████████████████████████████░░░░░░░░░ 0.82 ✓ keep
```

## 项目结构

```
├── distiller.py           # Python 状态机，唯一入口
├── prompts.py             # 4 个 prompt 模板（baseline, execute, evaluate, optimize）
├── config.json            # Teacher/Student 模型配置
├── SKILL/                 # 用户放置 Skill 目录
├── Input/                 # 用户放置输入用例
├── Workspace/             # 运行时产物（按时间戳保留历史）
└── Final/                 # 最终优化后的 Skill 输出
```

## 依赖

- Python 3.10+
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) (`claude` 命令可用)

## License

MIT
