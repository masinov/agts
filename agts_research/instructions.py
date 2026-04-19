from __future__ import annotations

from agts_research.config import ResearchConfig
from agts_research.models import ResearchBranch


def worker_instructions(
    cfg: ResearchConfig,
    branch: ResearchBranch,
    *,
    agent_id: str,
    shared_dir_name: str = ".claude",
) -> str:
    local = cfg.workers_local_agts
    if not local.enabled or local.mode == "disabled":
        agts_section = "Local AGTS is disabled for this worker."
    elif local.mode == "required":
        agts_section = f"""
Local AGTS is required at configured checkpoints.
- Before submitting an eval, run:
  `python -m agts.cli run "Review this branch attempt before eval" --provider claude-sdk --max-steps {local.max_steps}`
- Record the `.tot/runs/<run_id>` path in your branch note and attempt metadata if available.
""".strip()
    else:
        agts_section = f"""
Local AGTS is available as an optional reasoning tool.
Use it when planning a hard experiment, debugging a failed eval, or deciding whether to pivot:
`python -m agts.cli run "Plan or critique the next step for this research branch" --provider claude-sdk --max-steps {local.max_steps}`
Do not call it for routine edits where direct work is cheaper.
""".strip()

    return f"""# AGTS Research Worker

You are a branch-local autonomous research worker.

## Task
{cfg.task.description}

Objective: {cfg.task.objective}
Score direction: {cfg.evaluator.direction}

## Your Branch
- branch_id: {branch.branch_id}
- agent_id: {agent_id}
- mode: {branch.research_mode.value}
- title: {branch.title}
- hypothesis: {branch.hypothesis}

## Operating Contract
- You are autonomous inside this branch worktree. Do not ask the user for approval.
- The research manager already authorized you to run local shell commands and `./agts-research eval`.
- In one-shot mode, complete one bounded useful turn and exit with a written note.
- You own local execution for this branch only.
- You may inspect and edit this worktree.
- You may read shared attempts, notes, skills, evidence, and eval logs through `{shared_dir_name}/`.
- You may write branch notes under `{shared_dir_name}/notes/{branch.branch_id}/`.
- You may create reusable procedures under `{shared_dir_name}/skills/`.
- You may not create, stop, split, or finalize global research branches.
- You may propose split, stop, verify, or pivot in your notes.
- Do not read `.research/private` or any hidden evaluator material.

## Evaluation
When you have a candidate worth scoring, run:

```bash
./agts-research eval -m "short description of the attempt"
```

This stages the worktree, commits it when possible, runs the evaluator, and writes an attempt artifact.

If this branch has no attempts yet, run a baseline eval before proposing improvements:

```bash
./agts-research eval -m "baseline current solver"
```

## Local AGTS
{agts_section}

## Required Branch Note After Meaningful Work
Write or update `{shared_dir_name}/notes/{branch.branch_id}/latest.md` with:
- what changed
- evidence gathered
- eval result if any
- failed assumptions
- recommended next action: continue | split | stop | verify | pivot
- whether local AGTS was used
"""


def branch_brief(branch: ResearchBranch) -> str:
    return f"""# Branch {branch.branch_id}: {branch.title}

Hypothesis: {branch.hypothesis}
Mode: {branch.research_mode.value}
Status: {branch.status.value}

Use this file as a concise branch brief. Detailed work should go in shared notes.
"""
