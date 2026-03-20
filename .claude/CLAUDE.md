# Skill Distiller

## 概述
自动化 Skill 蒸馏系统。Teacher（强模型）执行任务产出标杆 + 推理，提取 assertions 作为评分标准，Student（弱模型）按 SKILL.md 执行，Teacher 评分，分数高保留低回退，迭代优化直到收敛。

## 架构
- `distiller.py` — Python 状态机，唯一入口
- `prompts.py` — 4 个 prompt 模板（baseline, execute, evaluate, optimize）
- `config.json` — Teacher/Student 模型配置（env vars、阈值）
- 所有 LLM 调用通过 `claude -p` 子进程，不依赖当前会话

## 目录约定
- `SKILL/` — 用户放置 skill 目录（子目录形式，含 SKILL.md）
- `Input/` — 用户放置输入用例（每个子目录一个 case，任意文件）
- `Workspace/` — 运行时产物，按时间戳子目录保留历史
- `Final/` — 最终优化后的 skill 输出

## 规则
- **不要修改** `.claude/skills/skill-creator/` 下的任何文件
- 子进程调用 `claude -p` 时必须移除 `CLAUDECODE` 环境变量
- 模型选择通过 `ANTHROPIC_MODEL` 环境变量，不用 `--model` flag
- 子进程 cwd 始终为项目根目录（确保 .claude/skills/ 可被发现）
- assertions 只增不减
- 每轮 optimize 最多迁移 3 条知识
