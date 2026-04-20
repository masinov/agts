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
- `[x]` Test a real `agts research monitor` run with Claude workers editing, evaluating, and writing notes.
- `[x]` Add interactive/resumable sessions:
  - `[x]` session id capture
  - `[x]` resume command
  - `[x]` graceful interrupt
  - `[x]` relaunch with prior session
  - `[x]` turn limits
  - `[x]` timeout handling
- `[~]` Improve process supervision:
  - `[x]` first-class detached `research run` command for fire-and-forget supervisor launch
  - `[x]` log tail command
  - `[x]` worker timeout
  - `[x]` auto-stop on monitor exit option
  - `[x]` crash classification
  - `[x]` process metadata refresh after exit
  - `[x]` stale PID reuse protection
  - `[x]` per-launch log files
  - `[x]` local subprocess reaping and exit-code capture

## Evaluator

- `[x]` Harden evaluator command execution.
- `[x]` Serialize eval submissions with a run-level eval lock.
- `[x]` Use unique evaluator temp files so concurrent eval splits cannot collide.
- `[x]` Wire private eval directories into worker/evaluator layout.
- `[x]` Add worker sandbox masking for private-dev and final-holdout files.
- `[x]` Route worker evals through a supervisor-side eval queue.
- `[x]` Add supervisor-only final holdout evaluation command.
- `[x]` Block final holdout evaluation from inside worker sandboxes.
- `[x]` Add richer `ScoreBundle`-style results.
- `[x]` Support held-out/public evaluation split.
- `[x]` Support structured feedback JSON.
- `[x]` Improve timeout and failure classification.
- `[x]` Commit branch scaffolding before first eval so baseline attempts do not attribute helper files as research edits.
- `[x]` Block private-dev evals on finalized/stopped branches.

## Branching And Meta-Control

- `[x]` Improve research policy beyond simple continue/split/stop:
  - `[x]` value-of-information scoring
  - `[x]` novelty penalties for near-duplicate branches
  - `[x]` stall-aware pivot selection
  - `[x]` explicit exploration vs exploitation budget allocation
- `[x]` Improve branch splitting quality:
  - `[x]` evidence-generated split briefs
  - `[x]` evidence-aware split directions
  - `[x]` branch novelty comparison
  - `[x]` branch merge/distill actions
- `[~]` Add verifier-gated finalization:
  - `[x]` private-dev best candidate selection
  - `[x]` verifier review before final holdout
  - `[x]` final holdout only after supervisor approval
  - `[x]` verifier approval is consumed by the first final-holdout eval
  - `[x]` final report compares private-dev best vs final-holdout score
- `[~]` Improve branch summarization from:
  - `[x]` notes
  - `[x]` diffs
  - `[x]` eval logs
  - `[x]` failed approaches
  - `[x]` local AGTS runs
  - citations/research evidence
- `[~]` Add richer agent roles:
  - `[x]` critic workers
  - `[x]` verifier workers
  - `[x]` literature workers
  - `[x]` implementation workers
  - `[x]` distillation workers

## Local AGTS Integration

- `[x]` Automatically detect and link `.tot/runs/...` artifacts into research attempt metadata.
- `[x]` Track whether local AGTS was used by a worker and whether it improved downstream attempts.
- `[x]` Keep `.tot/` local AGTS artifacts out of branch commits and changed-file reporting.
- `[x]` Expose local AGTS usage in branch summaries and final reports.
- `[x]` Add worker heartbeat prompts that recommend local AGTS only at high-value checkpoints.

## Shared Memory

- `[x]` Add note schema and branch-note conventions.
- `[x]` Add provenance conventions for notes, skills, and evidence.
- `[x]` Add skill validation.
- `[x]` Add stale/bad-note cleanup.
- `[x]` Add global consolidation artifacts.

## Heartbeats

- `[x]` Make heartbeat actions first-class action records.
- `[x]` Add configurable heartbeat action registry.
- `[x]` Persist heartbeat trigger history.

## Resource Accounting

- `[x]` Track token usage.
- `[x]` Track wall-clock budgets.
- `[x]` Track per-branch agent time.
- `[x]` Enforce max active workers.
- `[x]` Add global budget stop conditions beyond eval count.

## Tests

- `[x]` Add automated tests for config loading.
- `[x]` Add automated tests for run creation.
- `[x]` Add automated tests for worktree isolation.
- `[x]` Add automated tests for eval submission.
- `[x]` Add automated tests for score parsing.
- `[x]` Add automated tests for meta step decisions.
- `[x]` Add automated tests for monitor duplicate prevention.
- `[x]` Add automated tests for JSON artifact validity.
  - `[x]` provenance and memory-validation artifacts
  - `[x]` heartbeat action records
  - `[x]` monitor/event/report artifact sweep

## Benchmarks

- `[x]` Add first benchmark: 1D bin packing heuristic optimization.
- `[x]` Run full Claude worker benchmark on bin packing.
- `[x]` Add benchmark report command:
  - public/private-dev/final-holdout split metadata
  - best private-dev attempt
  - final-holdout attempt
  - eval budget used
  - local AGTS usage
  - branch lineage and notes
- `[ ]` Add additional benchmark domains:
  - `[x]` algorithmic optimization: 0/1 knapsack heuristic optimization
  - `[x]` systems optimization: expert placement load balancing
  - code repair with hidden tests
  - transformer/kernel optimization
  - literature synthesis with citation checks

## Current Next Step

Harden shared-memory maintenance and heartbeat policy configuration.

- `[x]` record provenance for eval logs, reviews, verifier artifacts, split briefs, and distillations
- `[x]` add shared-memory validation for notes, skills, and evidence
- `[x]` persist heartbeat action records with trigger names and prompt hashes
- `[x]` add stale/bad-note cleanup or quarantine
- `[x]` make heartbeat triggers configurable instead of hard-coded

Next, add a code-repair benchmark with hidden tests or a literature-synthesis benchmark with citation checks.
