from __future__ import annotations

from pathlib import Path
from typing import Any
import time

from agts_research.config import load_run_config
from agts_research.meta import summarize_branch
from agts_research.provenance import record_provenance
from agts_research.storage import read_state, write_json_atomic, write_state


def distill_run(run_dir: Path) -> dict[str, Any]:
    cfg = load_run_config(run_dir)
    state = read_state(run_dir)
    for branch in state.branches.values():
        summarize_branch(cfg, branch, state.attempts)

    reusable: list[str] = []
    failures: list[str] = []
    open_questions: list[str] = []
    best_by_branch: list[dict[str, Any]] = []
    local_agts_runs: list[str] = []
    for branch in state.branches.values():
        summary = branch.summary
        if not summary:
            continue
        reusable.extend(_tagged(branch.branch_id, summary.reusable_findings))
        failures.extend(_tagged(branch.branch_id, summary.failed_approaches))
        open_questions.extend(_tagged(branch.branch_id, summary.open_questions))
        if branch.best_attempt_id:
            attempt = state.attempts.get(branch.best_attempt_id)
            best_by_branch.append(
                {
                    "branch_id": branch.branch_id,
                    "attempt_id": branch.best_attempt_id,
                    "score": attempt.score if attempt else None,
                    "title": attempt.title if attempt else "",
                }
            )
        for attempt_id in branch.attempt_ids:
            attempt = state.attempts.get(attempt_id)
            if attempt:
                local_agts_runs.extend(attempt.local_agts_runs)

    artifact = {
        "timestamp": time.time(),
        "run_id": state.run_id,
        "task": state.task_name,
        "branch_count": len(state.branches),
        "attempt_count": len(state.attempts),
        "best_by_branch": best_by_branch,
        "reusable_findings": _dedupe(reusable),
        "failed_approaches": _dedupe(failures),
        "open_questions": _dedupe(open_questions),
        "local_agts_runs": _dedupe(local_agts_runs),
    }
    path = run_dir / "public" / "summaries" / "distilled_findings.json"
    write_json_atomic(path, artifact)
    markdown = _format_distillation(artifact)
    markdown_path = run_dir / "public" / "summaries" / "distilled_findings.md"
    markdown_path.write_text(markdown, encoding="utf-8")
    record_provenance(run_dir, path=path, kind="summary", source="distillation")
    record_provenance(run_dir, path=markdown_path, kind="summary", source="distillation")
    write_state(run_dir, state)
    return artifact


def _format_distillation(artifact: dict[str, Any]) -> str:
    lines = [
        f"# Distilled Findings: {artifact['task']}",
        "",
        f"- run_id: {artifact['run_id']}",
        f"- branches: {artifact['branch_count']}",
        f"- attempts: {artifact['attempt_count']}",
        "",
        "## Best By Branch",
        *_items([f"{item['branch_id']} {item['attempt_id']} score={item['score']} {item['title']}" for item in artifact["best_by_branch"]]),
        "",
        "## Reusable Findings",
        *_items(artifact["reusable_findings"]),
        "",
        "## Failed Approaches",
        *_items(artifact["failed_approaches"]),
        "",
        "## Open Questions",
        *_items(artifact["open_questions"]),
        "",
        "## Local AGTS Runs",
        *_items(artifact["local_agts_runs"]),
    ]
    return "\n".join(lines).rstrip() + "\n"


def _tagged(branch_id: str, items: list[str]) -> list[str]:
    return [f"{branch_id}: {item}" for item in items]


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        output.append(item)
    return output


def _items(items: list[str]) -> list[str]:
    if not items:
        return ["- none"]
    return [f"- {item}" for item in items]
