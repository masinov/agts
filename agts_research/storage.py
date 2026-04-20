from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any
import os
import tempfile
import time

from agts.jsonutil import dumps, to_jsonable
from agts_research.models import ResearchAttempt, ResearchBranch, ResearchRunState


PUBLIC_DIRS = [
    "attempts",
    "notes",
    "skills",
    "evidence",
    "eval_logs",
    "summaries",
    "agents",
    "heartbeat",
    "evaluator",
]


def create_run_id(name: str) -> str:
    safe = "".join(ch if ch.isalnum() else "-" for ch in name.lower()).strip("-")
    safe = "-".join(part for part in safe.split("-") if part) or "research"
    return f"{time.strftime('%Y%m%d-%H%M%S')}-{safe[:48]}"


def ensure_run_layout(run_dir: Path) -> None:
    (run_dir / "public").mkdir(parents=True, exist_ok=True)
    (run_dir / "private").mkdir(parents=True, exist_ok=True)
    (run_dir / "worktrees").mkdir(parents=True, exist_ok=True)
    (run_dir / "repo").mkdir(parents=True, exist_ok=True)
    for item in PUBLIC_DIRS:
        (run_dir / "public" / item).mkdir(parents=True, exist_ok=True)


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = dumps(value) + "\n"
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def append_jsonl(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(dumps(value, indent=None) + "\n")


def write_state(run_dir: Path, state: ResearchRunState) -> None:
    write_json_atomic(run_dir / "meta_state.json", state)
    for branch in state.branches.values():
        write_branch(run_dir, branch)
    for attempt in state.attempts.values():
        write_attempt(run_dir, attempt)


def read_state(run_dir: str | Path) -> ResearchRunState:
    from agts_research.hydrate import hydrate_run_state

    data = _read_json(Path(run_dir) / "meta_state.json")
    return hydrate_run_state(data)


def write_branch(run_dir: Path, branch: ResearchBranch) -> None:
    write_json_atomic(run_dir / "public" / "summaries" / f"{branch.branch_id}.json", branch)


def write_attempt(run_dir: Path, attempt: ResearchAttempt) -> None:
    write_json_atomic(run_dir / "public" / "attempts" / f"{attempt.attempt_id}.json", attempt)


def _read_json(path: Path) -> dict[str, Any]:
    import json

    return json.loads(path.read_text(encoding="utf-8"))


def branch_snapshots(state: ResearchRunState) -> list[dict[str, Any]]:
    return [
        {
            "branch_id": branch.branch_id,
            "parent_id": branch.parent_id,
            "title": branch.title,
            "hypothesis": branch.hypothesis,
            "mode": branch.research_mode.value,
            "status": branch.status.value,
            "best_attempt_id": branch.best_attempt_id,
            "value_estimate": branch.value_estimate,
            "value_of_information": branch.summary.value_of_information if branch.summary else 0.0,
            "uncertainty": branch.uncertainty,
            "novelty": branch.novelty,
            "eval_count": branch.eval_count,
            "evals_since_improvement": branch.evals_since_improvement,
            "worktree_path": branch.worktree_path,
        }
        for branch in state.branches.values()
    ]


def read_attempt_file(path: Path) -> ResearchAttempt:
    from agts_research.hydrate import hydrate_attempt

    return hydrate_attempt(_read_json(path))


def state_to_dict(state: ResearchRunState) -> dict[str, Any]:
    return to_jsonable(asdict(state))
