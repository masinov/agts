# agts

Agentic Tree Search is a budgeted branching reasoning controller for Claude Code.

The project follows the SDK-oriented design from `docs/specs/agts_claude_code.md`: Python owns the logical branch graph and run artifacts, while Claude Code/Claude Agent SDK provides local worker, summarizer, and verifier calls.

## Install

```bash
python -m pip install -e .
```

For real Claude SDK execution:

```bash
python -m pip install -e '.[claude]'
```

## Run

Smoke test the orchestrator without model calls:

```bash
agts run "Sketch the minimal AGTS architecture" --provider dry-run --max-steps 2
```

Run with Claude Agent SDK:

```bash
agts run "Implement the next AGTS feature" --provider claude-sdk
```

For quick integration checks, keep the budget small and set a timeout:

```bash
agts run "Smoke test AGTS integration" --provider claude-sdk --max-steps 1 --sdk-timeout 90
```

Each run writes:

- `.tot/runs/<run_id>/branches.json`
- `.tot/runs/<run_id>/events.jsonl`
- `.tot/runs/<run_id>/final_answer.md`
- `.tot/runs/<run_id>/training_rows.jsonl`

## Claude Code

Claude Code project files are included:

- `CLAUDE.md` keeps the permanent rules short.
- `.claude/agents/` defines coordinator and worker roles.
- `.claude/skills/run-tot/SKILL.md` invokes the SDK-first workflow.
- `.claude/skills/branch-audit/SKILL.md` captures the training-data audit workflow.

The important constraint is that branches are logical JSON records. Subagents are ephemeral local evaluators; only the coordinator/orchestrator owns branch topology.

## AGTS Research

`agts` remains the local tree-search helper. The research system is separate and lives under the `research` subcommand.

Create a durable research run:

```bash
python -m agts.cli research start -c examples/research_smoke/research.json
```

If installed with the console scripts, the equivalent command is:

```bash
agts-research start -c examples/research_smoke/research.json
```

The command prints a `run_dir` and root branch `worktree`. From that worktree, submit an evaluated attempt:

```bash
./agts-research eval -m "baseline smoke attempt"
```

Inspect and advance the meta-controller:

```bash
python -m agts.cli research status <run_dir>
python -m agts.cli research summarize <run_dir>
python -m agts.cli research distill <run_dir>
python -m agts.cli research review <run_dir>
python -m agts.cli research provenance <run_dir>
python -m agts.cli research validate-memory <run_dir>
python -m agts.cli research clean-memory <run_dir>
python -m agts.cli research report <run_dir>
python -m agts.cli research step <run_dir>
```

Run one meta step and launch the selected worker turn:

```bash
python -m agts.cli research advance <run_dir>
```

Run a bounded monitor loop that refreshes worker status, advances the meta-controller,
and launches selected worker turns:

```bash
python -m agts.cli research monitor <run_dir> --iterations 10 --interval 5 --worker-timeout 600
```

Claude Code workers run in a `bwrap` sandbox by default when configured. Hidden evaluator files
are masked from the worker. Worker calls to `./agts-research eval` go through a supervisor-side
eval queue under `public/evaluator/`, so private-dev scoring can run without exposing private data
inside the worker sandbox.

Shared-memory artifacts record provenance in `public/evidence/provenance.jsonl`.
Heartbeat launches record prompt hashes and trigger reasons in `public/heartbeat/actions.jsonl`.
Use `validate-memory` to check branch notes, skill files, and evidence artifacts before relying
on them for distillation or final reporting. `clean-memory` is report-only by default; pass
`--apply` to quarantine invalid shared-memory files under `public/evidence/quarantine/`.

Run a supervisor-only final holdout after a candidate is selected:

```bash
python -m agts.cli research verify <run_dir>
python -m agts.cli research final-eval <run_dir> -m "final holdout"
```

Print the benchmark report after private-dev and final-holdout evals:

```bash
python -m agts.cli research report <run_dir>
python -m agts.cli research report <run_dir> --json
```

Dry-run monitor mode uses harmless short-lived worker processes:

```bash
python -m agts.cli research monitor <run_dir> --iterations 2 --interval 0.5 --dry-run --dry-run-seconds 0.1
```

Included benchmark configs:

```bash
python -m agts.cli research start -c benchmarks/bin_packing/research.json
python -m agts.cli research start -c benchmarks/knapsack/research.json
```

Launch managed workers:

```bash
python -m agts.cli research launch <run_dir> --timeout 600
python -m agts.cli research workers <run_dir>
python -m agts.cli research logs <run_dir> --agent-id <agent-id> -n 80
python -m agts.cli research stop <run_dir>
```

Pass a one-shot prompt override when you want a bounded worker turn:

```bash
python -m agts.cli research launch <run_dir> --prompt "Inspect the branch and submit one baseline eval."
```

Use `--dry-run` to test process management without starting Claude Code:

```bash
python -m agts.cli research launch <run_dir> --dry-run
```

Research artifacts live under `.research/runs/<run_id>/`:

- `meta_state.json` for durable branch state
- `meta_events.jsonl` for meta-controller and attempt events
- `public/attempts/` for scored attempts
- `public/notes/` and `public/skills/` for shared memory
- `public/agents/` for worker logs and process metadata
- `public/evaluator/` for supervisor eval requests and responses
- `private/` for private-dev and final-holdout material, masked from workers
- `worktrees/` for branch-local execution

Workers may use local AGTS when configured, but `agts-research` does not require it.
When a worker creates `.tot/runs/...` artifacts inside its branch worktree, eval submission
automatically records those paths in the research attempt's `local_agts_runs` field and marks
`metadata.local_agts_used`.

Branch notes use a stable `latest.md` schema with sections for latest work, evidence, failed
assumptions, local AGTS usage, recommended next action, and open questions. Summaries and reports
consume those sections as shared memory.

Run the core local tests:

```bash
python -m unittest discover -s tests
```
