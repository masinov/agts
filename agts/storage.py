from __future__ import annotations

from pathlib import Path
import time

from agts.jsonutil import dumps
from agts.models import BranchState, SearchEvent


def create_run_dir(root: Path = Path(".tot/runs")) -> Path:
    run_id = time.strftime("%Y%m%d-%H%M%S")
    run_dir = root / run_id
    suffix = 1
    while run_dir.exists():
        suffix += 1
        run_dir = root / f"{run_id}-{suffix}"
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir


def write_run_artifacts(
    run_dir: Path,
    *,
    task: str,
    answer: str,
    events: list[SearchEvent],
    branches: list[BranchState],
) -> None:
    (run_dir / "task.md").write_text(task + "\n", encoding="utf-8")
    (run_dir / "branches.json").write_text(dumps(branches) + "\n", encoding="utf-8")
    (run_dir / "events.jsonl").write_text(
        "".join(dumps(event, indent=None) + "\n" for event in events),
        encoding="utf-8",
    )
    (run_dir / "final_answer.md").write_text(answer.rstrip() + "\n", encoding="utf-8")
    rows = extract_training_rows(task, events)
    (run_dir / "training_rows.jsonl").write_text(
        "".join(dumps(row, indent=None) + "\n" for row in rows),
        encoding="utf-8",
    )


def extract_training_rows(task: str, events: list[SearchEvent]) -> list[dict[str, object]]:
    if not events:
        return []
    final_reward = events[-1].final_reward if events[-1].final_reward is not None else 0.0
    rows: list[dict[str, object]] = []
    for index, event in enumerate(events):
        rows.append(
            {
                "kind": "supervisor_policy",
                "task": task,
                "step": index,
                "branch_summaries": event.summaries,
                "values": event.values,
                "action": event.action,
                "final_reward": final_reward,
            }
        )
        for branch_id, summary in event.summaries.items():
            rows.append(
                {
                    "kind": "branch_value",
                    "task": task,
                    "step": index,
                    "branch_id": branch_id,
                    "summary": summary,
                    "remaining_horizon": len(events) - index,
                    "target_value": final_reward,
                }
            )
    return rows
