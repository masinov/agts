For Claude Code, I would adapt the design in one important way:

**Make the tree logical, not literal.**
Use one top-level **coordinator** that owns the branch graph and spawns specialized worker subagents, but do not let workers recursively spawn more workers. In Claude Code, subagents are defined in Markdown with YAML frontmatter, can be managed via `/agents`, and subagents themselves cannot spawn other subagents; only an agent running as the main thread can use the `Agent(...)` tool to spawn allowed subagent types. ([Claude API Docs][1])

That leads to the cleanest Claude Code mapping:

* `CLAUDE.md` holds the permanent, short project rules.
* long procedures live in **skills**, because skills load only when invoked or when Claude decides they are relevant.
* branch roles live as **subagents** with narrow tool permissions.
* deterministic enforcement and logging live in **hooks**.
* if you want true programmatic orchestration, wrap the whole thing in the **Claude Agent SDK**, which exposes tools, hooks, subagents, permissions, and sessions. ([Claude API Docs][2])

Also keep `CLAUDE.md` small. Claude Code loads `CLAUDE.md` into startup context, and Anthropic recommends concise, structured instructions with a target under 200 lines because context fills quickly and performance degrades as it fills. Skills are the right place for the long “playbook” part of your ToT system because their body loads only when used. ([Claude API Docs][2])

## Recommended repository layout

```text id="c7c6ff"
your-repo/
  CLAUDE.md
  .claude/
    agents/
      tot-coordinator.md
      tot-solver.md
      tot-critic.md
      tot-verifier.md
      tot-tool-researcher.md
    skills/
      run-tot/SKILL.md
      branch-audit/SKILL.md
    hooks/
      # optional helper scripts if you want shell hooks to write logs
  scripts/
    tot_log.py
    tot_merge.py
  .tot/
    runs/
      # JSON logs written here
```

## 1) `CLAUDE.md`

This should be short and only define the stable operating contract. Claude Code supports project-level `./CLAUDE.md` or `./.claude/CLAUDE.md`; `/init` can generate one, and Claude can also import other files with `@path`. If you already use `AGENTS.md`, Anthropic explicitly recommends importing it from `CLAUDE.md` instead of duplicating content. ([Claude API Docs][2])

```md id="5juos1"
# Branching Deliberation Project

## Purpose
This repository implements a budgeted branching reasoning system:
- a coordinator manages multiple reasoning branches
- each branch is summarized into structured state
- the coordinator decides continue / split / stop / finalize
- all runs are logged for offline learning

## Global Rules
- Treat the branch graph as the source of truth.
- Do not invent hidden branch state; write all branch decisions to JSON artifacts under `.tot/runs/`.
- Prefer structured JSON outputs over prose whenever producing branch summaries, supervisor actions, verifier scores, or training rows.
- Keep prompts short, explicit, and machine-readable.
- Do not let worker agents decide global scheduling.
- Do not allow worker agents to write production code unless explicitly requested.
- Favor read-only exploration before editing.
- When implementing code, preserve deterministic reproducibility and log schema stability.

## Architecture Constraints
- The coordinator owns all branching decisions.
- Workers are local specialists.
- A branch split means creating two logical child branches in the coordinator state, not recursively spawning subagents from subagents.
- Finalization requires either verifier pass, strong independent agreement, or explicit budget exhaustion fallback.

## Standard Output Schemas
- branch summary: JSON
- supervisor action: JSON
- verifier result: JSON
- training rows: JSONL

## Implementation Preferences
- Python for orchestration
- small, typed dataclasses or pydantic models
- side effects isolated behind interfaces
- every run persisted to disk
```

## 2) Coordinator subagent

Subagents are Markdown files with YAML frontmatter. You can store project ones in `.claude/agents/`; that location has higher priority than user-level `~/.claude/agents/`. The `tools` field can allowlist tools, and for a main-thread agent you can restrict spawned subagent types with `Agent(worker-a, worker-b)`. ([Claude API Docs][1])

Use this as your coordinator:

```md id="zrc5bi"
---
name: tot-coordinator
description: Coordinates branching reasoning for complex implementation or research tasks. Use proactively for tasks that may benefit from multiple competing approaches, explicit branch summaries, branch stopping, and verifier-gated finalization.
tools: Agent(tot-solver, tot-critic, tot-verifier, tot-tool-researcher), Read, Write, Edit, Bash, Glob, Grep
model: sonnet
color: purple
---

You are the global supervisor for a branching reasoning system.

Your job is not to solve the task directly in one chain.
Your job is to manage a set of logical branches and allocate budget.

## Invariants
- You own the branch graph.
- Only you may create, split, stop, or finalize branches.
- Worker subagents may propose local next steps but may not make global decisions.
- Every important state transition must be written as JSON to `.tot/runs/<run_id>/`.

## Branch State
Each branch must have:
- branch_id
- parent_id
- mode
- status
- depth
- candidate_answer
- summary
- value_estimate
- verifier_score
- cost_so_far
- stagnation_count

## Allowed Actions
Return exactly one action at a time:
- continue(branch_id)
- split(branch_id, mode1, mode2)
- stop(branch_id, reason)
- finalize(branch_id, reason)

## Decision Policy
Prefer:
- continue: when value is high and direction is clear
- split: when value is high and strategic uncertainty is high
- stop: when branch is stagnant, redundant, or low-value
- finalize: when verifier passes or independent agreement is strong

## Branching Rules
When splitting:
- create two child branches with distinct modes
- ensure children are materially different
- never create more than 2 children from one split
- avoid duplicate branches

## Modes
Use only these branch modes unless explicitly extended:
- direct_solve
- decompose
- independent_rederive
- tool_verify
- counterexample_search
- assumption_stress_test
- compress_and_finalize

## Required Process
1. Read current run state.
2. Decide which branch needs work.
3. Delegate local work to exactly one worker subagent.
4. Update branch JSON.
5. Recompute summary and value.
6. Emit exactly one supervisor action JSON.
7. If action is finalize, produce a final answer artifact too.

## Output Contract
Always emit JSON of the form:

{
  "action": "continue|split|stop|finalize",
  "branch_id": "b1",
  "mode1": null,
  "mode2": null,
  "reason": "short explanation",
  "expected_gain": 0.0,
  "expected_cost": 0.0
}
```

## 3) Solver worker

Workers should be local, not global.

```md id="js8edq"
---
name: tot-solver
description: Local branch worker for advancing one branch's reasoning or implementation state. Use when a branch needs one concrete productive step.
tools: Read, Grep, Glob, Bash
model: sonnet
color: blue
---

You are a local branch worker.

You operate on exactly one branch.
You do not decide global scheduling.
You do not compare branches.
You do not finalize the overall task.

## Your Role
Advance the assigned branch by one meaningful local step.

## Inputs
You will receive:
- task
- branch_id
- branch mode
- recent branch trace
- current candidate answer
- known evidence
- current risk
- next suggested move

## Mode Behavior
- direct_solve: push directly toward an answer
- decompose: identify subproblems and solve one
- independent_rederive: derive fresh without trusting prior reasoning
- tool_verify: gather concrete evidence from files, commands, or tests
- counterexample_search: try to break the current candidate
- assumption_stress_test: expose hidden assumptions
- compress_and_finalize: refine into a clean final deliverable

## Required Output
Return JSON only:

{
  "reasoning_delta": "what changed in this step",
  "new_evidence": ["..."],
  "updated_candidate_answer": "...",
  "confidence": 0.0,
  "key_risk": "...",
  "proposed_next_step": "...",
  "should_request_split": false,
  "suggested_split_modes": [],
  "novelty_hint": "...",
  "tokens_used_estimate": 0
}

## Style
- concise
- evidence-oriented
- no hidden global claims
- no long essays
```

## 4) Critic worker

```md id="66co28"
---
name: tot-critic
description: Adversarial local reviewer for a branch. Use proactively when a candidate answer may be wrong, fragile, unsupported, or overconfident.
tools: Read, Grep, Glob, Bash
model: sonnet
color: orange
---

You are an adversarial reviewer for one branch.

Your job is to attack the local branch, not to solve the whole problem.

## Goals
- find contradictions
- find unsupported assumptions
- find missing evidence
- find likely failure cases
- assess whether the branch should be stopped or split

## Required Output
Return JSON only:

{
  "branch_id": "...",
  "failure_modes": ["..."],
  "strongest_objection": "...",
  "evidence_missing": ["..."],
  "counterexample_candidates": ["..."],
  "suggested_action": "continue|split|stop",
  "suggested_split_modes": [],
  "confidence_in_objection": 0.0
}
```

## 5) Verifier worker

```md id="dgo0f3"
---
name: tot-verifier
description: Evaluates a branch candidate against explicit checks, consistency, tests, and evidence. Use proactively before finalization and after major branch updates.
tools: Read, Grep, Glob, Bash
model: sonnet
color: green
---

You are a verifier.

You do not create new branches.
You do not solve the task from scratch.
You score whether a branch is ready to finalize.

## Verification Sources
Use whichever are applicable:
- direct evidence in repository files
- command output or tests
- agreement with independent branch results
- internal consistency
- compliance with requested format

## Required Output
Return JSON only:

{
  "branch_id": "...",
  "score": 0.0,
  "passed": false,
  "reasons": ["..."],
  "remaining_gaps": ["..."],
  "finalization_risk": "low|medium|high"
}
```

## 6) Tool-heavy researcher

This is useful when a branch should inspect the repo deeply or use shell tools aggressively.

```md id="7i4vuz"
---
name: tot-tool-researcher
description: Read-heavy branch worker for codebase exploration, evidence gathering, grep/glob/bash inspection, and implementation feasibility checks. Use proactively when the coordinator needs grounded repository evidence.
tools: Read, Grep, Glob, Bash
model: sonnet
color: cyan
---

You are a repository evidence gatherer.

Your job is to answer one narrow branch question with concrete evidence from the repo.

## Rules
- prefer direct file evidence over speculation
- quote exact file paths
- if running commands, explain why briefly
- do not edit files

## Required Output
Return JSON only:

{
  "question": "...",
  "answer": "...",
  "evidence": [
    {"path": "...", "reason": "..."}
  ],
  "uncertainties": ["..."],
  "recommended_branch_mode": "direct_solve|decompose|independent_rederive|tool_verify|counterexample_search|assumption_stress_test|compress_and_finalize"
}
```

## 7) A skill to launch the whole workflow

Skills use `SKILL.md`, support YAML frontmatter, can be user-invocable, can pre-approve tools with `allowed-tools`, and can accept `$ARGUMENTS`. If you want a skill to run in isolation, `context: fork` runs it in a separate agent context. ([Claude API Docs][3])

For your case, I would make a manually-invoked skill:

```md id="nktuxs"
---
name: run-tot
description: Run the branching reasoning workflow on a coding or design task with explicit branch tracking and verifier-gated finalization.
disable-model-invocation: true
allowed-tools: Read Write Edit Grep Glob Bash
---

Run the branching reasoning workflow on this task:

$ARGUMENTS

## Procedure
1. Use the `tot-coordinator` agent as the global controller.
2. Create a run directory under `.tot/runs/<timestamp>/`.
3. Initialize one root branch in `branches.json`.
4. Repeatedly:
   - summarize active branches
   - estimate value heuristically
   - choose exactly one action
   - if local work is needed, delegate to one specialized worker agent
   - persist updated state after each action
5. Finalize only if verifier passes or branch agreement is strong.
6. Write:
   - `branches.json`
   - `events.jsonl`
   - `final_answer.md`
   - `training_rows.jsonl`

## Output Requirements
- Use strict JSON for state artifacts
- Keep branch count small unless explicitly asked otherwise
- Prefer evidence-backed branch work
- Report final answer plus a compact branch audit
```

## 8) A branch-audit skill

This is helpful for postmortems and credit assignment.

```md id="3tq0en"
---
name: branch-audit
description: Audit a completed branching run and derive branch-level value and credit labels for offline training.
disable-model-invocation: true
allowed-tools: Read Write Grep Glob Bash
---

Audit this run directory:

$ARGUMENTS

## Tasks
1. Read `branches.json`, `events.jsonl`, and `final_answer.md`.
2. Assign provisional branch credit:
   - winning finalized branch: high positive
   - branch that provided reused evidence: medium positive
   - branch that exposed a critical flaw: medium positive
   - stagnant expensive branch: negative
   - redundant branch: near zero
3. Write `credit_assignment.json`.
4. Write `value_rows.jsonl` for branch state -> eventual utility training.
5. Write `policy_rows.jsonl` for supervisor state -> chosen action training.

## Output
Return a short summary and write the artifacts to disk.
```

## 9) Hook strategy

Hooks are where you enforce things deterministically rather than hoping the model remembers. Claude Code hooks can run commands at lifecycle events like tool use, notifications, and validation, and Anthropic explicitly recommends hooks when you want guaranteed behavior rather than relying on the model to choose it. ([Claude API Docs][4])

For this project, I would use hooks for three things:

1. **JSON validation after writes**
   If Claude writes `.tot/runs/**/*.json` or `.jsonl`, automatically validate syntax.

2. **Auto-formatting**
   Format Python files after edit.

3. **Branch log append**
   Every branch-state write triggers a helper script that appends a normalized event row.

A minimal settings fragment could look like:

```json id="mmxzpr"
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Write|Edit",
        "hooks": [
          {
            "type": "command",
            "command": "jq empty $(jq -r '.tool_input.file_path' 2>/dev/null) >/dev/null 2>&1 || true"
          }
        ]
      }
    ]
  }
}
```

That exact matcher logic will need refinement for your repo, but the pattern is the important part.

## 10) If you want true orchestration, use the Agent SDK

Claude Code’s SDK exposes built-in tools, hooks, subagents, permissions, and sessions, and Anthropic’s examples show invoking agents programmatically with allowed tool sets. That is the best fit if you want your **global supervisor to be a real Python process** that repeatedly calls Claude for worker steps and keeps the branch graph in structured state on disk. ([Claude API Docs][5])

In that version, Claude Code becomes the execution substrate, but the branch tree lives in your Python orchestrator. The clean pattern is:

* Python owns `branches.json`
* Python picks next action
* Python calls Claude subagents or prompts for local work
* Python updates state and decides split/stop/finalize
* Claude Code tools are used for file reading, shell commands, and edits

That avoids trying to encode the whole tree in one conversation transcript, which is especially important because Anthropic warns that context fills quickly and performance degrades as it does. ([anthropic.com][6])

A minimal SDK shape would be:

```python id="m30r48"
import asyncio
from claude_agent_sdk import query, ClaudeAgentOptions

async def ask_claude(prompt: str) -> str:
    chunks = []
    async for message in query(
        prompt=prompt,
        options=ClaudeAgentOptions(
            allowed_tools=["Read", "Write", "Edit", "Glob", "Grep", "Bash"]
        ),
    ):
        if hasattr(message, "result"):
            chunks.append(message.result)
    return "\n".join(chunks)

async def main():
    task = "Implement the branching ToT supervisor in this repo"
    result = await ask_claude(f"/run-tot {task}")
    print(result)

asyncio.run(main())
```

## 11) The most important Claude Code-specific adaptation

Because subagents do not recursively spawn subagents, your branching logic should look like this:

* **tree in JSON**
* **workers as ephemeral local evaluators**
* **coordinator as sole owner of branch topology**

So when the coordinator decides to split branch `b3`, it should not ask worker `b3` to fork itself. It should:

1. write two child branch records, say `b7` and `b8`
2. assign them different modes
3. dispatch one worker turn to `b7` or `b8` as needed

That preserves your formal architecture while fitting Claude Code’s actual agent model. ([Claude API Docs][1])

## 12) Worktree isolation

If you later want branches to make conflicting edits safely, Claude Code subagents support `isolation: worktree`, which gives the subagent an isolated repository copy. That is the clean path for “speculative implementation branches” that may touch code, though I would start with read-only workers first. ([Claude API Docs][1])

## 13) The first command I would actually use

Once these files exist, I would start Claude Code in the repo and run:

```text id="v6xwu4"
/run-tot build the minimal Python orchestration layer for branch state, summaries, supervisor actions, and JSONL logging
```

Then, after the first run:

```text id="gz9w6m"
/branch-audit .tot/runs/<latest-run-id>
```

## 14) The shortest implementation advice

For Claude Code specifically:

* keep `CLAUDE.md` short
* put the ToT playbook in a skill
* represent branches in files, not only in chat context
* use coordinator-owned branching
* use narrow worker subagents
* use hooks for JSON validation and logging
* move to SDK orchestration once the prompt-only prototype works

If you want, I can turn this into a ready-to-drop set of files for your repo, including exact `.md` contents and a small Python orchestrator.

[1]: https://docs.anthropic.com/en/docs/claude-code/sub-agents "Create custom subagents - Claude Code Docs"
[2]: https://docs.anthropic.com/en/docs/claude-code/memory "How Claude remembers your project - Claude Code Docs"
[3]: https://docs.anthropic.com/en/docs/claude-code/skills "Extend Claude with skills - Claude Code Docs"
[4]: https://docs.anthropic.com/en/docs/claude-code/hooks-guide "Automate workflows with hooks - Claude Code Docs"
[5]: https://docs.anthropic.com/en/docs/claude-code/sdk "Agent SDK overview - Claude Code Docs"
[6]: https://www.anthropic.com/engineering/claude-code-best-practices "Best Practices for Claude Code - Claude Code Docs"
