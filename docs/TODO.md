# AGTS Research TODO

This tracks remaining work for `agts-research`, separate from the local `agts` tool.

## Status Legend

- `[ ]` Not started
- `[~]` In progress
- `[x]` Completed

## Runtime And Worker Execution

- `[x]` Add worker log tooling.
- `[x]` Print monitor tick progress by default.
- `[x]` Add real worker turn validation.
- `[x]` Launch Claude Code workers with non-interactive permission mode.
- `[~]` Test a real `agts research monitor` run with Claude workers editing, evaluating, and writing notes.
- `[ ]` Add interactive/resumable sessions:
  - session id capture
  - resume command
  - graceful interrupt
  - relaunch with prior session
  - turn limits
  - timeout handling
- `[~]` Improve process supervision:
  - `[x]` log tail command
  - `[x]` worker timeout
  - auto-stop on monitor exit option
  - crash classification
  - `[x]` process metadata refresh after exit
  - `[x]` stale PID reuse protection
  - `[x]` per-launch log files

## Evaluator

- `[ ]` Harden evaluator command execution.
- `[ ]` Wire private eval directories into worker/evaluator layout.
- `[ ]` Add richer `ScoreBundle`-style results.
- `[ ]` Support held-out/public evaluation split.
- `[ ]` Support structured feedback JSON.
- `[ ]` Improve timeout and failure classification.
- `[x]` Commit branch scaffolding before first eval so baseline attempts do not attribute helper files as research edits.

## Branching And Meta-Control

- `[ ]` Improve branch splitting quality:
  - LLM-generated split briefs
  - evidence-aware split directions
  - branch novelty comparison
  - branch merge/distill actions
- `[ ]` Improve branch summarization from:
  - notes
  - diffs
  - eval logs
  - failed approaches
  - local AGTS runs
  - citations/research evidence
- `[ ]` Add richer agent roles:
  - critic workers
  - verifier workers
  - literature workers
  - implementation workers
  - distillation workers

## Local AGTS Integration

- `[ ]` Automatically detect and link `.tot/runs/...` artifacts into research attempt metadata.
- `[ ]` Track whether local AGTS was used by a worker and whether it improved downstream attempts.

## Shared Memory

- `[ ]` Add note schema and branch-note conventions.
- `[ ]` Add provenance conventions for notes, skills, and evidence.
- `[ ]` Add skill validation.
- `[ ]` Add stale/bad-note cleanup.
- `[ ]` Add global consolidation artifacts.

## Heartbeats

- `[ ]` Make heartbeat actions first-class action records.
- `[ ]` Add configurable heartbeat action registry.
- `[ ]` Persist heartbeat trigger history.

## Resource Accounting

- `[ ]` Track token usage.
- `[ ]` Track wall-clock budgets.
- `[ ]` Track per-branch agent time.
- `[ ]` Enforce max active workers.
- `[ ]` Add global budget stop conditions beyond eval count.

## Tests

- `[ ]` Add automated tests for config loading.
- `[ ]` Add automated tests for run creation.
- `[ ]` Add automated tests for worktree isolation.
- `[ ]` Add automated tests for eval submission.
- `[ ]` Add automated tests for score parsing.
- `[ ]` Add automated tests for meta step decisions.
- `[ ]` Add automated tests for monitor duplicate prevention.
- `[ ]` Add automated tests for JSON artifact validity.

## Benchmarks

- `[x]` Add first benchmark: 1D bin packing heuristic optimization.
- `[x]` Run full Claude worker benchmark on bin packing.

## Current Next Step

Add real worker turn validation and log tooling:

- `[x]` `agts research logs <run_dir> [--agent-id ...]`
- `[x]` `agts research launch --timeout ...`
- `[ ]` real Claude dry-smoke prompt that only reads/writes notes, not code
- `[x]` verify Claude can write a branch note and optionally submit one eval
- `[x]` capture session id if emitted

Next, run the bounded `monitor` path again with Claude workers now that `advance` is validated, then add automatic linking of local AGTS runs into research attempts. That link is central to the design: meta-research outside, local AGTS optionally inside each worker.
