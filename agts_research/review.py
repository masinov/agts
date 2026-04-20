from __future__ import annotations

from pathlib import Path
from typing import Any
import time

from agts_research.config import load_run_config
from agts_research.meta import summarize_branch
from agts_research.provenance import record_provenance
from agts_research.storage import read_state, write_json_atomic, write_state


def review_branch(run_dir: Path, *, branch_id: str | None = None) -> dict[str, Any]:
    cfg = load_run_config(run_dir)
    state = read_state(run_dir)
    branch = state.branches[branch_id] if branch_id else next(iter(state.branches.values()))
    summarize_branch(cfg, branch, state.attempts)
    summary = branch.summary
    risks: list[str] = []
    recommendations: list[str] = []
    if summary:
        risks.extend(summary.open_questions)
        if summary.main_risk:
            risks.append(summary.main_risk)
        if summary.score_trend in {"flat", "worsening"}:
            recommendations.append("pivot or split before spending more evals")
        if not summary.key_evidence:
            recommendations.append("collect evidence with a baseline eval")
        if summary.value_of_information >= cfg.search.split_threshold:
            recommendations.append("split into evidence-derived directions")
        elif summary.best_score is not None:
            recommendations.append("consider verifier review before final holdout")
    if not recommendations:
        recommendations.append("continue with one bounded branch-local experiment")

    artifact = {
        "timestamp": time.time(),
        "role": "critic",
        "branch_id": branch.branch_id,
        "risks": _dedupe(risks),
        "recommendations": _dedupe(recommendations),
        "summary": summary,
    }
    path = run_dir / "public" / "evidence" / "reviews" / f"{branch.branch_id}.json"
    write_json_atomic(path, artifact)
    record_provenance(run_dir, path=path, kind="evidence", source="critic_review", branch_id=branch.branch_id)
    rel_path = str(path.relative_to(run_dir))
    if rel_path not in branch.evidence_paths:
        branch.evidence_paths.append(rel_path)
    write_state(run_dir, state)
    return artifact


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        output.append(item)
    return output
