from __future__ import annotations

from typing import Any

from agts_research.models import (
    AgentSpec,
    AttemptStatus,
    BranchStatus,
    LocalAgtsConfig,
    ResearchAttempt,
    ResearchBranch,
    ResearchBranchSummary,
    ResearchCost,
    ResearchMode,
    ResearchRunState,
)


def hydrate_run_state(data: dict[str, Any]) -> ResearchRunState:
    branches = {
        branch_id: hydrate_branch(branch)
        for branch_id, branch in data.get("branches", {}).items()
    }
    attempts = {
        attempt_id: hydrate_attempt(attempt)
        for attempt_id, attempt in data.get("attempts", {}).items()
    }
    agents = {
        agent_id: AgentSpec(**agent)
        for agent_id, agent in data.get("agents", {}).items()
    }
    return ResearchRunState(
        run_id=data["run_id"],
        task_name=data["task_name"],
        task_description=data["task_description"],
        run_dir=data["run_dir"],
        repo_dir=data["repo_dir"],
        created_at=float(data["created_at"]),
        branches=branches,
        attempts=attempts,
        agents=agents,
        finalized_branch_id=data.get("finalized_branch_id"),
    )


def hydrate_branch(data: dict[str, Any]) -> ResearchBranch:
    summary = data.get("summary")
    return ResearchBranch(
        branch_id=data["branch_id"],
        parent_id=data.get("parent_id"),
        title=data["title"],
        hypothesis=data["hypothesis"],
        research_mode=ResearchMode(data["research_mode"]),
        status=BranchStatus(data.get("status", BranchStatus.ACTIVE.value)),
        depth=int(data.get("depth", 0)),
        worktree_path=data.get("worktree_path"),
        assigned_agents=list(data.get("assigned_agents", [])),
        best_attempt_id=data.get("best_attempt_id"),
        attempt_ids=list(data.get("attempt_ids", [])),
        note_paths=list(data.get("note_paths", [])),
        skill_paths=list(data.get("skill_paths", [])),
        evidence_paths=list(data.get("evidence_paths", [])),
        summary=ResearchBranchSummary(**summary) if summary else None,
        value_estimate=float(data.get("value_estimate", 0.0)),
        uncertainty=float(data.get("uncertainty", 1.0)),
        novelty=float(data.get("novelty", 1.0)),
        eval_count=int(data.get("eval_count", 0)),
        evals_since_improvement=int(data.get("evals_since_improvement", 0)),
        cost=ResearchCost(**data.get("cost", {})),
    )


def hydrate_attempt(data: dict[str, Any]) -> ResearchAttempt:
    return ResearchAttempt(
        attempt_id=data["attempt_id"],
        branch_id=data["branch_id"],
        agent_id=data["agent_id"],
        title=data["title"],
        score=data.get("score"),
        status=AttemptStatus(data.get("status", AttemptStatus.PENDING.value)),
        timestamp=float(data["timestamp"]),
        commit_hash=data.get("commit_hash"),
        parent_attempt_id=data.get("parent_attempt_id"),
        feedback=data.get("feedback", ""),
        changed_files=list(data.get("changed_files", [])),
        eval_log_path=data.get("eval_log_path"),
        local_agts_runs=list(data.get("local_agts_runs", [])),
        metadata=dict(data.get("metadata", {})),
    )
