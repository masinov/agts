# Agentic Tree Search

## Purpose
This repository implements a budgeted branching reasoning system:
- a Python coordinator owns the branch graph
- Claude Code SDK calls advance, summarize, and verify branch state
- workers are local specialists and never own global scheduling
- every run is persisted for audit and self-supervised training

## Global Rules
- Treat `.tot/runs/<run_id>/branches.json` as the source of truth.
- Do not invent hidden branch state; persist branch decisions and summaries.
- Use structured JSON for branch deltas, summaries, actions, verifier results, and training rows.
- Keep branch count small unless the task explicitly asks for wider search.
- Worker agents may propose local next steps but must not split, stop, or finalize globally.
- The coordinator is the only owner of continue / split / stop / finalize decisions.
- Prefer read-only exploration before editing production code.
- Preserve log schema stability when changing the orchestrator.

## Architecture Constraints
- Branches are logical records, not recursive subagent conversations.
- Splitting creates two child branch records in coordinator state.
- Finalization requires verifier pass, strong independent agreement, or budget exhaustion fallback.
- Claude Code integration should prefer the Agent SDK for programmatic orchestration.

## Standard Artifacts
- `.tot/runs/<run_id>/branches.json`
- `.tot/runs/<run_id>/events.jsonl`
- `.tot/runs/<run_id>/final_answer.md`
- `.tot/runs/<run_id>/training_rows.jsonl`
