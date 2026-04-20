from __future__ import annotations

from pathlib import Path
from typing import Any

from agts_research.config import ResearchConfig, load_run_config
from agts_research.models import ResearchAttempt, ResearchRunState
from agts_research.storage import read_state


def build_report(run_dir: Path) -> dict[str, Any]:
    cfg = load_run_config(run_dir)
    state = read_state(run_dir)
    private_attempts = [
        attempt
        for attempt in state.attempts.values()
        if attempt.metadata.get("eval_split", "private_dev") == "private_dev"
    ]
    final_attempts = [
        attempt
        for attempt in state.attempts.values()
        if attempt.metadata.get("eval_split") == "final_holdout"
    ]
    best_private = _best_attempt(cfg, private_attempts)
    best_final = _best_attempt(cfg, final_attempts)
    local_agts_attempts = [
        attempt
        for attempt in state.attempts.values()
        if attempt.metadata.get("local_agts_used") or attempt.local_agts_runs
    ]
    return {
        "run_id": state.run_id,
        "task": state.task_name,
        "objective": cfg.task.objective,
        "direction": cfg.evaluator.direction,
        "eval_budget": cfg.search.max_evals,
        "agent_turn_budget": cfg.search.max_agent_turns,
        "wall_seconds_budget": cfg.search.max_wall_seconds,
        "private_dev_eval_count": len(private_attempts),
        "final_holdout_eval_count": len(final_attempts),
        "branch_count": len(state.branches),
        "agent_count": len(state.agents),
        "resource_usage": {
            "agent_turns": sum(branch.cost.agent_turns for branch in state.branches.values()),
            "evals": sum(branch.cost.evals for branch in state.branches.values()),
            "wall_seconds": sum(branch.cost.wall_seconds for branch in state.branches.values()),
            "tokens": sum(branch.cost.tokens for branch in state.branches.values()),
        },
        "best_private_dev": _attempt_summary(best_private),
        "best_final_holdout": _attempt_summary(best_final),
        "local_agts": {
            "attempt_count": len(local_agts_attempts),
            "run_count": sum(len(attempt.local_agts_runs) for attempt in local_agts_attempts),
            "attempts": [_attempt_summary(attempt) for attempt in local_agts_attempts],
        },
        "branches": [
            {
                "branch_id": branch.branch_id,
                "status": branch.status.value,
                "eval_count": branch.eval_count,
                "best_attempt_id": branch.best_attempt_id,
                "value_estimate": branch.value_estimate,
                "value_of_information": branch.summary.value_of_information if branch.summary else 0.0,
                "uncertainty": branch.uncertainty,
                "novelty": branch.novelty,
                "note_paths": branch.note_paths,
                "evidence_paths": branch.evidence_paths,
                "summary": _summary(branch.summary),
            }
            for branch in state.branches.values()
        ],
    }


def format_report(report: dict[str, Any]) -> str:
    private = report.get("best_private_dev") or {}
    final = report.get("best_final_holdout") or {}
    local = report.get("local_agts") or {}
    usage = report.get("resource_usage") or {}
    lines = [
        f"# AGTS Research Report: {report['task']}",
        "",
        f"- run_id: {report['run_id']}",
        f"- objective: {report['objective']}",
        f"- direction: {report['direction']}",
        f"- private_dev_evals: {report['private_dev_eval_count']} / {report['eval_budget']}",
        f"- final_holdout_evals: {report['final_holdout_eval_count']}",
        f"- agent_turn_budget: {report['agent_turn_budget'] or 'unbounded'}",
        f"- wall_seconds_budget: {report['wall_seconds_budget'] or 'unbounded'}",
        f"- branches: {report['branch_count']}",
        f"- agents: {report['agent_count']}",
        f"- agent_turns: {usage.get('agent_turns', 0)}",
        f"- worker_wall_seconds: {usage.get('wall_seconds', 0):.1f}",
        f"- worker_tokens: {usage.get('tokens', 0)}",
        "",
        "## Best Private-Dev",
        _format_attempt(private),
        "",
        "## Best Final-Holdout",
        _format_attempt(final),
        "",
        "## Local AGTS Usage",
        f"- attempts_with_local_agts: {local.get('attempt_count', 0)}",
        f"- linked_local_agts_runs: {local.get('run_count', 0)}",
        "",
        "## Branches",
    ]
    for branch in report.get("branches", []):
        lines.append(
            f"- {branch['branch_id']}: status={branch['status']} evals={branch['eval_count']} "
            f"best={branch['best_attempt_id']} value={branch['value_estimate']:.2f} "
            f"voi={branch['value_of_information']:.2f} novelty={branch['novelty']:.2f}"
        )
        summary = branch.get("summary") or {}
        if summary.get("key_evidence"):
            lines.append(f"  evidence: {summary['key_evidence'][0]}")
        if branch.get("evidence_paths"):
            lines.append(f"  verifier/evidence: {', '.join(branch['evidence_paths'])}")
    return "\n".join(lines).rstrip() + "\n"


def _best_attempt(cfg: ResearchConfig, attempts: list[ResearchAttempt]) -> ResearchAttempt | None:
    scored = [attempt for attempt in attempts if attempt.score is not None]
    if not scored:
        return None
    if cfg.evaluator.direction == "minimize":
        return min(scored, key=lambda attempt: attempt.score if attempt.score is not None else float("inf"))
    return max(scored, key=lambda attempt: attempt.score if attempt.score is not None else float("-inf"))


def _attempt_summary(attempt: ResearchAttempt | None) -> dict[str, Any] | None:
    if attempt is None:
        return None
    return {
        "attempt_id": attempt.attempt_id,
        "branch_id": attempt.branch_id,
        "title": attempt.title,
        "score": attempt.score,
        "status": attempt.status.value,
        "commit_hash": attempt.commit_hash,
        "eval_split": attempt.metadata.get("eval_split", "private_dev"),
        "changed_files": attempt.changed_files,
        "local_agts_runs": attempt.local_agts_runs,
        "eval_log_path": attempt.eval_log_path,
        "score_bundle": attempt.metadata.get("score_bundle"),
    }


def _summary(summary) -> dict[str, Any] | None:
    if summary is None:
        return None
    return {
        "current_best_result": summary.current_best_result,
        "best_score": summary.best_score,
        "score_trend": summary.score_trend,
        "key_evidence": summary.key_evidence,
        "failed_approaches": summary.failed_approaches,
        "reusable_findings": summary.reusable_findings,
        "open_questions": summary.open_questions,
        "main_risk": summary.main_risk,
        "recommended_action": summary.recommended_action,
        "value_of_information": summary.value_of_information,
        "policy_reason": summary.policy_reason,
    }


def _format_attempt(attempt: dict[str, Any] | None) -> str:
    if not attempt:
        return "- none"
    return "\n".join(
        [
            f"- attempt_id: {attempt['attempt_id']}",
            f"- branch_id: {attempt['branch_id']}",
            f"- title: {attempt['title']}",
            f"- score: {attempt['score']}",
            f"- status: {attempt['status']}",
            f"- commit: {attempt['commit_hash']}",
            f"- local_agts_runs: {len(attempt.get('local_agts_runs') or [])}",
            f"- metrics: {_format_metrics(attempt.get('score_bundle'))}",
        ]
    )


def _format_metrics(bundle: object) -> str:
    if not isinstance(bundle, dict):
        return "none"
    metrics = bundle.get("metrics")
    if not isinstance(metrics, dict) or not metrics:
        return "none"
    items = [f"{key}={value}" for key, value in list(metrics.items())[:6]]
    return ", ".join(items)
