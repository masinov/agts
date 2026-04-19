---
name: branch-audit
description: Audit a completed Agentic Tree Search run and derive branch-level credit labels for offline training.
disable-model-invocation: true
allowed-tools: Read Write Grep Glob Bash
---

Audit this run directory:

$ARGUMENTS

## Tasks
1. Read `branches.json`, `events.jsonl`, and `final_answer.md`.
2. Assign provisional branch credit:
   - finalized branch: high positive
   - evidence reused by winner: medium positive
   - critical flaw found: medium positive
   - stagnant expensive branch: negative
   - redundant branch: near zero
3. Write `credit_assignment.json`.
4. Write `value_rows.jsonl`.
5. Write `policy_rows.jsonl`.

## Output
Return a short summary and write artifacts to disk.
