from __future__ import annotations

from pathlib import Path
from typing import Any
import time

from agts_research.config import ResearchConfig, load_run_config
from agts_research.meta import summarize_branch
from agts_research.models import ResearchAttempt, ResearchBranch
from agts_research.provenance import record_provenance
from agts_research.storage import read_state, write_json_atomic, write_state


def verify_branch(run_dir: Path, *, branch_id: str | None = None, message: str = "") -> dict[str, Any]:
    cfg = load_run_config(run_dir)
    state = read_state(run_dir)
    branch = state.branches[branch_id] if branch_id else _best_branch(cfg, state)
    summarize_branch(cfg, branch, state.attempts)
    branch_attempts = [state.attempts[aid] for aid in branch.attempt_ids if aid in state.attempts]
    private_attempts = [
        attempt
        for attempt in branch_attempts
        if attempt.score is not None and attempt.metadata.get("eval_split", "private_dev") == "private_dev"
    ]
    final_attempts = [
        attempt
        for attempt in branch_attempts
        if attempt.metadata.get("eval_split") == "final_holdout"
    ]
    best = _best_attempt(cfg, private_attempts)
    checks = _checks(branch, branch_attempts, best, final_attempts)
    approved = all(item["ok"] for item in checks)
    artifact = {
        "timestamp": time.time(),
        "branch_id": branch.branch_id,
        "approved": approved,
        "message": message,
        "best_private_dev_attempt_id": best.attempt_id if best else None,
        "best_private_dev_score": best.score if best else None,
        "private_dev_eval_count": len(private_attempts),
        "final_holdout_eval_count": len(final_attempts),
        "checks": checks,
        "summary": branch.summary,
    }
    path = run_dir / "public" / "evidence" / "verifications" / f"{branch.branch_id}.json"
    write_json_atomic(path, artifact)
    record_provenance(run_dir, path=path, kind="evidence", source="verifier_review", branch_id=branch.branch_id)
    rel_path = str(path.relative_to(run_dir))
    if rel_path not in branch.evidence_paths:
        branch.evidence_paths.append(rel_path)
    write_state(run_dir, state)
    return artifact


def latest_verification(run_dir: Path, branch_id: str) -> dict[str, Any] | None:
    path = run_dir / "public" / "evidence" / "verifications" / f"{branch_id}.json"
    if not path.exists():
        return None
    import json

    return json.loads(path.read_text(encoding="utf-8"))


def verification_approved(run_dir: Path, branch_id: str) -> bool:
    artifact = latest_verification(run_dir, branch_id)
    if not artifact or not artifact.get("approved"):
        return False
    state = read_state(run_dir)
    branch = state.branches.get(branch_id)
    if branch is None:
        return False
    final_attempts = [
        state.attempts[aid]
        for aid in branch.attempt_ids
        if aid in state.attempts and state.attempts[aid].metadata.get("eval_split") == "final_holdout"
    ]
    return not final_attempts


def _checks(
    branch: ResearchBranch,
    attempts: list[ResearchAttempt],
    best: ResearchAttempt | None,
    final_attempts: list[ResearchAttempt],
) -> list[dict[str, Any]]:
    latest = attempts[-1] if attempts else None
    return [
        {
            "name": "has_private_dev_attempt",
            "ok": best is not None,
            "detail": "branch has at least one scored private-dev attempt" if best else "no scored private-dev attempt",
        },
        {
            "name": "best_attempt_has_commit",
            "ok": bool(best and best.commit_hash),
            "detail": best.commit_hash if best and best.commit_hash else "best attempt has no commit",
        },
        {
            "name": "latest_not_failed",
            "ok": latest is None or latest.status.value not in {"failed", "timeout"},
            "detail": latest.status.value if latest else "no attempts",
        },
        {
            "name": "summary_available",
            "ok": branch.summary is not None,
            "detail": branch.summary.recommended_action if branch.summary else "missing summary",
        },
        {
            "name": "no_prior_final_holdout",
            "ok": not final_attempts,
            "detail": f"{len(final_attempts)} prior final-holdout attempt(s)",
        },
    ]


def _best_branch(cfg: ResearchConfig, state) -> ResearchBranch:
    branches = list(state.branches.values())
    if not branches:
        raise RuntimeError("run has no branches")

    def key(branch: ResearchBranch) -> tuple[float, float]:
        attempt = state.attempts.get(branch.best_attempt_id or "")
        score = attempt.score if attempt and attempt.score is not None else float("-inf")
        if cfg.evaluator.direction == "minimize" and score != float("-inf"):
            score = -score
        return (score, branch.value_estimate)

    return max(branches, key=key)


def _best_attempt(cfg: ResearchConfig, attempts: list[ResearchAttempt]) -> ResearchAttempt | None:
    if not attempts:
        return None
    if cfg.evaluator.direction == "minimize":
        return min(attempts, key=lambda attempt: attempt.score if attempt.score is not None else float("inf"))
    return max(attempts, key=lambda attempt: attempt.score if attempt.score is not None else float("-inf"))
