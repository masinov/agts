from __future__ import annotations

from pathlib import Path
from typing import Any
import hashlib
import time

from agts_research.config import ResearchConfig
from agts_research.models import AgentSpec, MetaAction, ResearchBranch
from agts_research.storage import append_jsonl


def heartbeat_prompt(cfg: ResearchConfig, branch: ResearchBranch, *, reason: str) -> str:
    local_agts = _local_agts_guidance(cfg)
    prefix = (
        "You are running in non-interactive one-shot mode. Do not ask the user for approval. "
        "You are authorized to inspect files, edit this worktree, write branch notes, and run "
        "`./agts-research eval` when needed. Hidden tests are supervisor-only; do not try to inspect "
        "private-dev tests, final-holdout tests, evaluator secrets, or masked paths. "
    )
    if branch.eval_count == 0:
        return prefix + (
            "This branch has no evaluated attempts yet. First, submit a baseline eval for the current solver with "
            "`./agts-research eval -m \"baseline current solver\"`. Then inspect the public instances and evaluator "
            "feedback. If there is enough time in this turn, make one small candidate improvement, run a second eval, "
            "and update the branch note with both scores and the recommended next action. "
            f"{local_agts}"
        )
    if branch.evals_since_improvement >= cfg.heartbeat.pivot_after_stall:
        return prefix + (
            "Read CLAUDE.md, AGTS_RESEARCH.md, recent attempts, eval logs, and branch notes. "
            f"The branch has stalled for {branch.evals_since_improvement} evals. "
            "Do a pivot analysis, write the proposed pivot to shared notes, and only run ./agts-research eval "
            "if you have a concrete candidate. "
            f"{local_agts}"
        )
    if branch.eval_count > 0 and branch.eval_count % max(1, cfg.heartbeat.consolidate_every) == 0:
        return prefix + (
            "Consolidate this branch's useful findings. Read attempts, notes, and eval logs, then update "
            "shared notes with reusable lessons, failed approaches, and the next recommended experiment. "
            f"{local_agts}"
        )
    if branch.eval_count > 0 and branch.eval_count % max(1, cfg.heartbeat.reflect_every) == 0:
        return prefix + (
            "Reflect on the latest evaluated attempt for this branch. Identify what changed, what the score "
            "means, and the next best branch-local action. Run ./agts-research eval only for a real candidate. "
            f"{local_agts}"
        )
    return prefix + (
        "Read CLAUDE.md, AGTS_RESEARCH.md, shared attempts, and branch notes. "
        f"Meta-controller selected this branch because: {reason}. "
        "Make one useful branch-local step. If you produce a candidate, run ./agts-research eval. "
        f"{local_agts}"
    )


def heartbeat_action_record(
    run_dir: Path,
    *,
    cfg: ResearchConfig,
    iteration: int,
    action: MetaAction,
    branch: ResearchBranch,
    agent: AgentSpec,
    prompt: str,
) -> dict[str, Any]:
    trigger = _trigger_name(branch, action.reason)
    record = {
        "timestamp": time.time(),
        "iteration": iteration,
        "meta_action": action.type.value,
        "branch_id": branch.branch_id,
        "agent_id": agent.agent_id,
        "reason": action.reason,
        "trigger": trigger,
        "trigger_description": cfg.heartbeat.trigger_registry.get(trigger, trigger),
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "prompt_preview": prompt[:240],
    }
    append_jsonl(run_dir / "public" / "heartbeat" / "actions.jsonl", record)
    return record


def _trigger_name(branch: ResearchBranch, reason: str) -> str:
    reason_lower = reason.lower()
    if branch.eval_count == 0:
        return "baseline"
    if "stall" in reason_lower or branch.evals_since_improvement > 0:
        return "stall_or_pivot"
    if "split" in reason_lower:
        return "split_followup"
    if "verify" in reason_lower:
        return "verification"
    return "continue"


def _local_agts_guidance(cfg: ResearchConfig) -> str:
    local = cfg.workers_local_agts
    if not local.enabled or local.mode == "disabled":
        return ""
    if local.mode == "required":
        return (
            "Before a nontrivial eval, run local AGTS with "
            f"`python -m agts.cli run \"Critique this research attempt before eval\" --provider claude-sdk --max-steps {local.max_steps}` "
            "and mention the `.tot/runs/...` path in the branch note."
        )
    return (
        "Use local AGTS only if this turn involves a hard design choice, failed-eval recovery, or pivot decision: "
        f"`python -m agts.cli run \"Plan or critique the next research step\" --provider claude-sdk --max-steps {local.max_steps}`. "
        "Skip it for routine edits."
    )
