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

Dry-run monitor mode uses harmless short-lived worker processes:

```bash
python -m agts.cli research monitor <run_dir> --iterations 2 --interval 0.5 --dry-run --dry-run-seconds 0.1
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
- `worktrees/` for branch-local execution

Workers may use local AGTS when configured, but `agts-research` does not require it.
