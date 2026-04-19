from __future__ import annotations

from pathlib import Path
import time

from agts_research.config import ResearchConfig
from agts_research.models import (
    BranchStatus,
    MetaAction,
    MetaActionType,
    MetaEvent,
    ResearchAttempt,
    ResearchBranch,
    ResearchBranchSummary,
    ResearchMode,
    new_id,
)
from agts_research.storage import append_jsonl, branch_snapshots, read_state, write_state
from agts_research.workspace import create_branch_worktree, write_worker_files
from agts_research.models import AgentSpec


def summarize_branch(cfg: ResearchConfig, branch: ResearchBranch, attempts: dict[str, ResearchAttempt]) -> ResearchBranchSummary:
    branch_attempts = [attempts[aid] for aid in branch.attempt_ids if aid in attempts]
    scored = [attempt for attempt in branch_attempts if attempt.score is not None]
    best = _best_attempt(cfg.evaluator.direction, scored)
    recent_failures = [
        attempt.title
        for attempt in branch_attempts[-5:]
        if attempt.status.value in {"failed", "timeout", "regressed"}
    ]
    score_trend = _score_trend(cfg.evaluator.direction, scored[-5:])
    open_questions = []
    if not branch_attempts:
        open_questions.append("No evaluated attempt yet.")
    if branch.evals_since_improvement >= cfg.heartbeat.pivot_after_stall:
        open_questions.append("Branch has stalled and may need a pivot.")
    if branch.best_attempt_id is None:
        open_questions.append("No best attempt has been established.")

    summary = ResearchBranchSummary(
        branch_id=branch.branch_id,
        hypothesis=branch.hypothesis,
        current_best_result=best.title if best else "",
        best_score=best.score if best else None,
        score_trend=score_trend,
        key_evidence=[
            f"{attempt.title}: score={attempt.score}"
            for attempt in scored[-3:]
        ],
        failed_approaches=recent_failures,
        reusable_findings=[],
        open_questions=open_questions,
        main_risk=_main_risk(branch, branch_attempts),
        recommended_action=_recommended_action(cfg, branch, scored),
        recommended_split_directions=_split_directions(branch),
    )
    branch.summary = summary
    branch.value_estimate = estimate_branch_value(cfg, branch)
    branch.uncertainty = min(1.0, 0.2 + 0.2 * len(summary.open_questions))
    return summary


def estimate_branch_value(cfg: ResearchConfig, branch: ResearchBranch) -> float:
    summary = branch.summary
    if summary is None:
        return 0.2
    progress = 0.15 if summary.best_score is None else 0.55
    trend_bonus = {"improving": 0.20, "flat": 0.05, "worsening": -0.10, "unknown": 0.0}.get(
        summary.score_trend,
        0.0,
    )
    stall_penalty = min(0.35, 0.08 * branch.evals_since_improvement)
    evidence_bonus = min(0.15, 0.03 * len(summary.key_evidence))
    risk_penalty = 0.10 if summary.main_risk else 0.0
    return max(0.0, min(1.0, progress + trend_bonus + evidence_bonus - stall_penalty - risk_penalty))


def choose_meta_action(cfg: ResearchConfig, branches: list[ResearchBranch], attempts: dict[str, ResearchAttempt]) -> MetaAction:
    active = [branch for branch in branches if branch.status == BranchStatus.ACTIVE]
    if not active:
        return MetaAction(MetaActionType.FINALIZE, branches[0].branch_id, "no active branches remain")

    if len(attempts) >= cfg.search.max_evals:
        best = _best_branch(cfg, branches, attempts)
        return MetaAction(MetaActionType.FINALIZE, best.branch_id, "eval budget exhausted")

    stoppable = [
        branch
        for branch in active
        if branch.value_estimate < cfg.search.stop_threshold and branch.eval_count > 0
    ]
    if stoppable and len(active) > 1:
        branch = sorted(stoppable, key=lambda item: item.value_estimate)[0]
        return MetaAction(MetaActionType.STOP, branch.branch_id, "low value estimate")

    split_candidates = [
        branch
        for branch in active
        if branch.value_estimate >= cfg.search.split_threshold
        and branch.evals_since_improvement >= cfg.heartbeat.pivot_after_stall
        and len(branches) < cfg.search.max_branches
    ]
    if split_candidates:
        branch = sorted(split_candidates, key=lambda item: item.value_estimate, reverse=True)[0]
        directions = branch.summary.recommended_split_directions if branch.summary else _split_directions(branch)
        return MetaAction(
            MetaActionType.SPLIT,
            branch.branch_id,
            "high value branch has stalled; split into alternative directions",
            direction_a=directions[0],
            direction_b=directions[1],
            expected_gain=branch.value_estimate,
            expected_cost=0.2,
        )

    best = sorted(active, key=lambda item: item.value_estimate, reverse=True)[0]
    return MetaAction(
        MetaActionType.CONTINUE,
        best.branch_id,
        "best active branch by value estimate",
        expected_gain=best.value_estimate,
        expected_cost=0.1,
    )


def run_meta_step(cfg: ResearchConfig, run_dir: Path) -> MetaAction:
    state = read_state(run_dir)
    for branch in state.branches.values():
        summarize_branch(cfg, branch, state.attempts)
    action = choose_meta_action(cfg, list(state.branches.values()), state.attempts)
    if action.type == MetaActionType.STOP:
        state.branches[action.branch_id].status = BranchStatus.STOPPED
    elif action.type == MetaActionType.SPLIT:
        _apply_split(cfg, state, action)
    elif action.type == MetaActionType.FINALIZE:
        state.finalized_branch_id = action.branch_id
        state.branches[action.branch_id].status = BranchStatus.FINALIZED
    write_state(run_dir, state)
    append_jsonl(
        run_dir / "meta_events.jsonl",
        MetaEvent.now(
            action,
            branch_snapshots(state),
            attempts_seen=len(state.attempts),
            reason=action.reason,
        ),
    )
    return action


def _apply_split(cfg: ResearchConfig, state, action: MetaAction) -> None:
    parent = state.branches[action.branch_id]
    parent.status = BranchStatus.SPLIT
    directions = [action.direction_a or "alternative implementation", action.direction_b or "counterexample search"]
    modes = [ResearchMode.IMPLEMENTATION_EXPERIMENT, ResearchMode.COUNTEREXAMPLE_SEARCH]
    run_dir = Path(state.run_dir)
    repo_dir = Path(state.repo_dir)
    for index, direction in enumerate(directions):
        branch = ResearchBranch(
            branch_id=new_id("rb"),
            parent_id=parent.branch_id,
            title=f"{parent.title}: {direction}",
            hypothesis=f"{parent.hypothesis}\n\nSplit direction: {direction}",
            research_mode=modes[index],
            depth=parent.depth + 1,
        )
        worktree = create_branch_worktree(run_dir, repo_dir, branch.branch_id)
        branch.worktree_path = str(worktree)
        agent_id = f"agent-{branch.branch_id}-a"
        agent = AgentSpec(
            agent_id=agent_id,
            branch_id=branch.branch_id,
            role="research_worker",
            runtime=cfg.agents.runtime,
            model=cfg.agents.model,
            worktree_path=str(worktree),
        )
        branch.assigned_agents.append(agent_id)
        state.branches[branch.branch_id] = branch
        state.agents[agent_id] = agent
        write_worker_files(cfg, run_dir, branch, agent)


def _best_attempt(direction: str, attempts: list[ResearchAttempt]) -> ResearchAttempt | None:
    if not attempts:
        return None
    if direction == "minimize":
        return min(attempts, key=lambda attempt: attempt.score)
    return max(attempts, key=lambda attempt: attempt.score)


def _best_branch(cfg: ResearchConfig, branches: list[ResearchBranch], attempts: dict[str, ResearchAttempt]) -> ResearchBranch:
    def score(branch: ResearchBranch) -> tuple[float, float]:
        best = attempts.get(branch.best_attempt_id or "")
        raw = best.score if best and best.score is not None else float("-inf")
        if cfg.evaluator.direction == "minimize" and raw != float("-inf"):
            raw = -raw
        return (raw, branch.value_estimate)

    return max(branches, key=score)


def _score_trend(direction: str, attempts: list[ResearchAttempt]) -> str:
    if len(attempts) < 2:
        return "unknown"
    first = attempts[0].score
    last = attempts[-1].score
    if first is None or last is None:
        return "unknown"
    if direction == "minimize":
        if last < first:
            return "improving"
        if last > first:
            return "worsening"
    else:
        if last > first:
            return "improving"
        if last < first:
            return "worsening"
    return "flat"


def _main_risk(branch: ResearchBranch, attempts: list[ResearchAttempt]) -> str:
    if not attempts:
        return "branch has not been evaluated"
    if branch.evals_since_improvement >= 3:
        return "stagnation after repeated evals"
    if attempts[-1].status.value in {"failed", "timeout"}:
        return "latest eval failed"
    return ""


def _recommended_action(cfg: ResearchConfig, branch: ResearchBranch, scored: list[ResearchAttempt]) -> str:
    if branch.evals_since_improvement >= cfg.heartbeat.pivot_after_stall:
        return "split"
    if not scored:
        return "continue"
    return "continue"


def _split_directions(branch: ResearchBranch) -> list[str]:
    return [
        "verify and strengthen the current best approach",
        "try a substantially different counter-hypothesis",
    ]
