# Skill Distiller

[中文说明](/Volumes/macOS/Github/Skill%20Distiller%20/README.zh-CN.md)

Skill Distiller is an automated skill-distillation system. It treats a skill directory as an optimization target: a Teacher model first produces a benchmark solution and reasoning traces, extracts quality assertions, lets a Student execute the current skill, evaluates the gap, writes back high-leverage knowledge, and iterates until the process converges.

> Typical usage: prepare one skill, add several input cases, run `python distiller.py`, then inspect `Final/<skill-name>/` and `Workspace/<timestamp>/report.md`.

## When to use it

This project is designed for skills that already have a rough first version but still behave inconsistently and are hard to improve systematically. It works especially well for:

- Writing skills, such as article expansion, summarization, briefs, email drafting, or report generation
- Analysis skills, such as requirement breakdown, diagnosis, or review comment generation
- Process skills, such as multi-step handling under a fixed input format or template-driven output
- Long-lived skills, where you want to preserve rules, examples, references, and scripts instead of tweaking a single prompt

It is especially useful when:

- The initial skill is occasionally great but often drifts
- You know the Teacher can do better, but you do not know what knowledge should be written back into the skill
- You want to optimize the whole skill asset, not just a one-off answer
- You want an experimental loop with `keep`, `discard`, and `rollback`

It is less suitable when:

- You only want to manually polish one `SKILL.md`
- You have too few input cases to build a stable evaluation loop
- The task itself has almost no meaningful pass/fail or quality dimensions

## Teacher and Student

Teacher and Student are two model roles with different responsibilities:

- `Teacher`: a stronger model that produces the benchmark, extracts assertions, judges outputs, runs diff and blind evaluation, and migrates high-value knowledge back into the skill
- `Student`: a weaker model or execution agent that only follows the current skill and acts as the real test subject of the distillation loop

A common setup is:

- `Teacher` uses a stronger model such as `opus 4.6`
- `Student` uses a weaker but cheaper or more representative model such as `glm 4.7`

You can think of them this way:

- Teacher represents the higher-quality approach you want
- Student represents the weaker agent that must rely on the skill itself

The goal is not to distill the Teacher model weights. The goal is to distill Teacher knowledge into the skill so that the Student can get much closer to Teacher-level performance by following the skill alone.

## What kind of distillation this is

This is knowledge distillation, but not parameter distillation in the traditional ML sense. It is skill-level distillation.

What gets distilled is:

- How the Teacher completes the task
- What standards the Teacher uses to judge quality
- What decision rules the Teacher follows
- What examples, patterns, and process knowledge the Teacher relies on

That knowledge is written back into the skill directory so a weaker model can perform more like the Teacher without retraining or weight changes.

So this is not:

- Logit or hidden-state distillation
- Training a new Student model
- Compressing a larger model into a smaller one

It is closer to:

- `skill distillation` rather than `model distillation`
- Stronger `skill`, unchanged `weights`
- Distilling the whole skill asset rather than model parameters

## Goals

- Turn skill optimization from ad hoc prompt editing into an evaluation-driven distillation loop
- Extract reusable knowledge from Teacher reasoning instead of preserving only one-off answers
- Reduce self-bootstrapping drift through assertions, blind evaluation, and rollback

## Core mechanism

- `baseline`: Teacher runs the current skill and produces benchmark outputs, reasoning, `assertions.json`, and `failure_modes.json`
- `execute`: Student runs each input case with the currently installed skill and records its own reasoning
- `evaluate`: deterministic `code` assertions run first, then `judge` assertions are evaluated by Teacher
- `diff_evaluate`: Teacher compares the baseline and Student outputs to find gaps not yet covered by assertions
- `reasoning_diff`: Teacher compares baseline reasoning and Student reasoning to locate cognitive mismatch
- `blind_eval`: Teacher scores the output without looking at assertions, to catch important issues the assertion set may have missed
- `optimize`: Teacher writes high-leverage knowledge back into the skill directory based on failed assertions, diffs, blind eval, and baseline reasoning
- `keep / discard / rollback`: a candidate is only kept if it passes the artifact gate and achieves a new best core score; otherwise the system rolls back to the current best skill

The optimization target is the entire skill directory, not just one `SKILL.md`. The current implementation installs, evaluates, rewrites, and copies the whole skill, so companion assets such as `references/`, `scripts/`, `assets/`, and `agents/openai.yaml` are all part of the distillation target.

## Main loop

```text
setup_workspace
  -> baseline
  -> for each iteration:
       execute
       evaluate
       diff_evaluate
       reasoning_diff
       score (keep / discard / rollback)
       blind_eval (triggered by interval or plateau)
       optimize
  -> finalize
```

In the current implementation, the real keep/discard decision is based on:

- `artifact_gate_passed`: all artifact-layer `code` assertions must pass
- `overall_core_weighted_pass_rate`: the weighted core score must beat the historical best

`overall_weighted_pass_rate` is still recorded, but the retention logic mainly depends on the artifact gate plus core score.

## Repository layout

```text
.
├── distiller.py              # State machine entrypoint
├── prompts.py                # Prompts for baseline / execute / evaluate / optimize / diff / blind eval
├── config.json               # Teacher / Student / stopping configuration
├── SKILL/                    # The skill to distill; keep exactly one valid skill subdirectory here
├── Input/                    # Input cases; each subdirectory is one case
├── Workspace/                # Full intermediate artifacts for every run
└── Final/                    # Final copy of the best skill
```

`SKILL/` must contain exactly one subdirectory with `SKILL.md`. Every subdirectory under `Input/` is treated as an independent test case.

## Quick start

### 1. Prepare a skill

```text
SKILL/
└── my-skill/
    └── SKILL.md
```

The whole skill directory is installed into `.claude/skills/<skill-name>/`, so the system can distill and copy not only `SKILL.md`, but also:

- `references/`
- `scripts/`
- `assets/`
- `agents/openai.yaml`

### 2. Prepare inputs

```text
Input/
├── case_0/
│   └── ...
├── case_1/
│   └── ...
└── case_2/
    └── ...
```

All files inside each case directory are read by Teacher and Student.

### 3. Configure models and stopping conditions

Minimal example:

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

Notes:

- Model selection is injected through `ANTHROPIC_MODEL`, not a `--model` flag
- `teacher.env` and `student.env` are merged directly into each subprocess environment
- `student.env.ANTHROPIC_MODEL` is required by the code; Teacher can inherit from the host environment
- `max_runtime_seconds` overrides the legacy `timeout` field

### 4. Run

Start a new experiment:

```bash
python distiller.py
```

Use an explicit config:

```bash
python distiller.py config.json
```

Resume from an existing workspace:

```bash
python distiller.py config.json Workspace/2026-04-06_113000
```

CLI usage:

```text
python distiller.py [config.json] [Workspace/<timestamp>]
```

## Run artifacts

A typical run produces:

```text
Workspace/<timestamp>/
├── distiller.log
├── run_meta.json
├── skill_v0/                 # Snapshot of the initial skill
├── best_skill/               # Snapshot of the current best skill
├── assertions.json           # Accumulated assertion set; only grows
├── failure_modes.json        # High-value failure modes extracted from baseline
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
│   ├── blind_eval.json       # Only generated when triggered
│   ├── knowledge_candidates.md
│   ├── skill/
│   │   └── SKILL.md
│   ├── change.md
│   └── new_assertions.json
├── log.json
└── report.md
```

The final best skill is copied to:

```text
Final/<skill-name>/
```

## Configuration

Main configuration fields supported by the current code:

| Field | Default | Purpose |
|------|--------|------|
| `max_iterations` | `15` | Maximum number of iterations |
| `target_pass_rate` | `0.90` | Stop when `best_core_score` reaches this value |
| `plateau_rounds` | `3` | Stop on plateau after N rounds without a better best score |
| `max_runtime_seconds` / `timeout` | `1200` | Timeout in seconds for one `claude -p` subprocess call |
| `blind_eval_interval` | `3` | Trigger blind eval every N rounds |
| `coverage_gap_threshold` | `0.2` | If `core_score - blind_score` exceeds this value, override plateau stopping |
| `min_iterations_before_stop` | `2` | Minimum iteration count before stopping is allowed |
| `min_optimize_rounds` | `1` | Minimum number of completed optimize rounds before stopping is allowed |
| `critical_fail_cap` | `0.84` | Score cap when one high-weight critical assertion fails |
| `multi_critical_fail_cap` | `0.72` | Score cap when two or more high-weight critical assertions fail |

## Assertions and scoring

Assertions support two evaluation modes:

- `code`: deterministic checks, currently supporting
  - `file_exists`
  - `contains_all`
  - `contains_any`
  - `not_contains_any`
  - `max_sentences_per_paragraph`
  - `no_pattern_match`
- `judge`: decided by Teacher based on the output evidence

Assertions are also layered into:

- `artifact`: delivery format and compliance gates
- `core`: cross-input quality standards
- `scoped`: standards that only apply to some inputs

After merging evaluation results, each sample gets:

- `pass_rate`
- `weighted_pass_rate`
- `artifact_gate_passed`
- `artifact_weighted_pass_rate`
- `core_weighted_pass_rate`
- `scoped_weighted_pass_rate`

Two important rules:

1. All artifact-layer `code` assertions must pass, otherwise the iteration is immediately rejected.
2. Weighted scores are capped when high-weight critical failures remain, to prevent superficial formatting wins from hiding unresolved major issues.

## Blind eval and coverage gaps

Blind eval does not replace assertions. It exists to discover what the current assertion set failed to capture.

It:

- assigns an independent `blind_score`
- extracts `weak_dimensions`
- produces 3 to 5 `uncovered_dimensions`
- computes `coverage_gap = core_score - blind_score`

If `coverage_gap > coverage_gap_threshold`:

- the run ignores plateau-based stopping for that round and keeps going
- `uncovered_dimensions` are automatically converted into new assertions and added to the evaluation loop

## Optimize stage

Optimization is not just a rewrite of `SKILL.md`. The current skill is treated as a full directory asset.

Teacher reads:

- the current skill directory
- `eval.json`
- `baseline/reasoning_*.md`
- `diff_eval.json`
- `reasoning_diff.json`
- `knowledge_candidates.md`
- `blind_eval.json`
- `failure_modes.json`

And it follows these constraints:

- migrate at most 3 knowledge items per iteration
- assertions only grow, never shrink
- preserve the existing structure as much as possible and prefer high-value incremental edits
- avoid uncontrolled skill bloat

## Resuming runs

If a run is interrupted, it can be resumed from an existing workspace. The resume flow:

- validates required artifacts such as `baseline/`, `assertions.json`, and `skill_v0/`
- infers the next iteration from `log.json`
- reinstalls the saved current skill and best skill
- refuses to resume a run that already has a recorded `stop_reason`

Typical cases:

- subprocess timeout or model-service interruption
- manual stop in the middle of a run
- continuing an experiment that was only partially completed

## Reports

At the end of each run, a summary is written to `Workspace/<timestamp>/report.md`, including:

- initial and final core pass rate
- convergence curve
- keep/discard decision for each iteration
- blind score and coverage gap
- assertions that still fail at the end

## Requirements

- Python 3.10+
- `claude` available on `PATH`, with support for `claude -p`
- the necessary environment variables for your model provider

## Constraints and project conventions

- `distiller.py` is the only entrypoint
- subprocesses always run from the project root so local skills can be discovered
- model selection is controlled by environment variables, not `--model`
- `assertions.json` follows an append-only strategy
- do not modify files under `.Codex/skills/skill-creator/`

## Development notes

- For end-to-end coverage, see [`TEST.md`](/Volumes/macOS/Github/Skill%20Distiller%20/TEST.md)
- For background on the resume mechanism, see [`resume_recovery_design.md`](/Volumes/macOS/Github/Skill%20Distiller%20/resume_recovery_design.md)
- The example skill in this repo is [`SKILL/opinion-to-article/SKILL.md`](/Volumes/macOS/Github/Skill%20Distiller%20/SKILL/opinion-to-article/SKILL.md)

## License

MIT
