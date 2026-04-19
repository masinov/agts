---
name: run-tot
description: Run Agentic Tree Search on a coding or design task with explicit branch tracking and verifier-gated finalization.
disable-model-invocation: true
allowed-tools: Read Write Edit Grep Glob Bash
---

Run Agentic Tree Search on this task:

$ARGUMENTS

## Preferred Path
Use the Python SDK orchestrator:

```bash
agts run "$ARGUMENTS" --provider claude-sdk
```

## Procedure
1. Create a run directory under `.tot/runs/<run_id>/`.
2. Initialize one root branch in `branches.json`.
3. Repeatedly summarize active branches, estimate value, and choose exactly one action.
4. Delegate local work through Claude Code SDK prompts or narrow subagents.
5. Persist updated branch state after each action.
6. Finalize only if verifier passes, independent agreement is strong, or budget is exhausted.
7. Write `branches.json`, `events.jsonl`, `final_answer.md`, and `training_rows.jsonl`.

## Output
Report the final answer path and a compact branch audit.
