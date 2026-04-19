from __future__ import annotations

from agts_research.config import ResearchConfig
from agts_research.models import ResearchBranch


def heartbeat_prompt(cfg: ResearchConfig, branch: ResearchBranch, *, reason: str) -> str:
    prefix = (
        "You are running in non-interactive one-shot mode. Do not ask the user for approval. "
        "You are authorized to inspect files, edit this worktree, write branch notes, and run "
        "`./agts-research eval` when needed. Do not read private evaluator files. "
    )
    if branch.eval_count == 0:
        return prefix + (
            "This branch has no evaluated attempts yet. First, submit a baseline eval for the current solver with "
            "`./agts-research eval -m \"baseline current solver\"`. Then inspect the public instances and evaluator "
            "feedback. If there is enough time in this turn, make one small candidate improvement, run a second eval, "
            "and update the branch note with both scores and the recommended next action."
        )
    if branch.evals_since_improvement >= cfg.heartbeat.pivot_after_stall:
        return prefix + (
            "Read CLAUDE.md, AGTS_RESEARCH.md, recent attempts, eval logs, and branch notes. "
            f"The branch has stalled for {branch.evals_since_improvement} evals. "
            "Do a pivot analysis, write the proposed pivot to shared notes, and only run ./agts-research eval "
            "if you have a concrete candidate."
        )
    if branch.eval_count > 0 and branch.eval_count % max(1, cfg.heartbeat.consolidate_every) == 0:
        return prefix + (
            "Consolidate this branch's useful findings. Read attempts, notes, and eval logs, then update "
            "shared notes with reusable lessons, failed approaches, and the next recommended experiment."
        )
    if branch.eval_count > 0 and branch.eval_count % max(1, cfg.heartbeat.reflect_every) == 0:
        return prefix + (
            "Reflect on the latest evaluated attempt for this branch. Identify what changed, what the score "
            "means, and the next best branch-local action. Run ./agts-research eval only for a real candidate."
        )
    return prefix + (
        "Read CLAUDE.md, AGTS_RESEARCH.md, shared attempts, and branch notes. "
        f"Meta-controller selected this branch because: {reason}. "
        "Make one useful branch-local step. If you produce a candidate, run ./agts-research eval."
    )
