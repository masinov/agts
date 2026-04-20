from __future__ import annotations

from pathlib import Path
from typing import Any
import shutil
import time

from agts_research.storage import append_jsonl, write_json_atomic


PROVENANCE_LOG = Path("public") / "evidence" / "provenance.jsonl"
PROVENANCE_INDEX = Path("public") / "evidence" / "provenance_index.json"


def record_provenance(
    run_dir: Path,
    *,
    path: Path,
    kind: str,
    source: str,
    branch_id: str | None = None,
    agent_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rel_path = _relative_to_run(run_dir, path)
    record = {
        "timestamp": time.time(),
        "path": rel_path,
        "kind": kind,
        "source": source,
        "branch_id": branch_id,
        "agent_id": agent_id,
        "metadata": metadata or {},
    }
    append_jsonl(run_dir / PROVENANCE_LOG, record)
    refresh_provenance_index(run_dir)
    return record


def refresh_provenance_index(run_dir: Path) -> dict[str, Any]:
    records = _read_provenance_records(run_dir)
    latest_by_path: dict[str, dict[str, Any]] = {}
    counts_by_kind: dict[str, int] = {}
    for record in records:
        path = str(record.get("path", ""))
        if path:
            latest_by_path[path] = record
        kind = str(record.get("kind", "unknown"))
        counts_by_kind[kind] = counts_by_kind.get(kind, 0) + 1
    index = {
        "timestamp": time.time(),
        "record_count": len(records),
        "counts_by_kind": counts_by_kind,
        "latest_by_path": latest_by_path,
    }
    write_json_atomic(run_dir / PROVENANCE_INDEX, index)
    return index


def validate_shared_memory(run_dir: Path) -> dict[str, Any]:
    note_results = [_validate_note(path) for path in sorted((run_dir / "public" / "notes").glob("*.md"))]
    skill_results = [_validate_skill(path) for path in sorted((run_dir / "public" / "skills").glob("*.md"))]
    evidence_results = [_validate_evidence(path) for path in sorted((run_dir / "public" / "evidence").rglob("*")) if path.is_file()]
    provenance_index = refresh_provenance_index(run_dir)
    results = {
        "timestamp": time.time(),
        "ok": all(item["ok"] for item in [*note_results, *skill_results, *evidence_results]),
        "notes": note_results,
        "skills": skill_results,
        "evidence": evidence_results,
        "provenance": provenance_index,
    }
    write_json_atomic(run_dir / "public" / "summaries" / "memory_validation.json", results)
    return results


def cleanup_shared_memory(run_dir: Path, *, apply: bool = False) -> dict[str, Any]:
    validation = validate_shared_memory(run_dir)
    invalid = [
        item
        for group in ("notes", "skills", "evidence")
        for item in validation[group]
        if not item["ok"] and _can_quarantine(Path(item["path"]))
    ]
    quarantine_root = run_dir / "public" / "evidence" / "quarantine" / time.strftime("%Y%m%d-%H%M%S")
    quarantined: list[dict[str, Any]] = []
    for item in invalid:
        source = Path(item["path"])
        if apply and source.exists():
            destination = quarantine_root / _safe_relative(run_dir, source)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(destination))
            record = {**item, "quarantined_path": str(destination)}
            quarantined.append(record)
            record_provenance(
                run_dir,
                path=destination,
                kind=item["kind"],
                source="memory_cleanup",
                metadata={"original_path": str(source), "missing": item["missing"]},
            )
        else:
            quarantined.append({**item, "quarantined_path": None})
    artifact = {
        "timestamp": time.time(),
        "applied": apply,
        "candidate_count": len(invalid),
        "quarantined_count": len(quarantined) if apply else 0,
        "items": quarantined,
    }
    write_json_atomic(run_dir / "public" / "summaries" / "memory_cleanup.json", artifact)
    return artifact


def _validate_note(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    required = ["# ", "## Current Hypothesis", "## Evidence", "## Next Action"]
    missing = [item for item in required if item not in text]
    return _validation_record(path, "note", missing)


def _validate_skill(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    missing: list[str] = []
    if not text.lstrip().startswith("# "):
        missing.append("# heading")
    if "## When To Use" not in text and "## Usage" not in text:
        missing.append("usage section")
    if "## Inputs" not in text and "## Procedure" not in text:
        missing.append("procedure or inputs section")
    return _validation_record(path, "skill", missing)


def _validate_evidence(path: Path) -> dict[str, Any]:
    if path.name in {PROVENANCE_LOG.name, PROVENANCE_INDEX.name}:
        return _validation_record(path, "evidence", [])
    missing = []
    if path.stat().st_size == 0:
        missing.append("nonempty content")
    return _validation_record(path, "evidence", missing)


def _validation_record(path: Path, kind: str, missing: list[str]) -> dict[str, Any]:
    return {
        "path": str(path),
        "kind": kind,
        "ok": not missing,
        "missing": missing,
    }


def _read_provenance_records(run_dir: Path) -> list[dict[str, Any]]:
    import json

    path = run_dir / PROVENANCE_LOG
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def _relative_to_run(run_dir: Path, path: Path) -> str:
    try:
        return str(path.relative_to(run_dir))
    except ValueError:
        return str(path)


def _safe_relative(run_dir: Path, path: Path) -> Path:
    try:
        return path.relative_to(run_dir)
    except ValueError:
        return Path(path.name)


def _can_quarantine(path: Path) -> bool:
    if path.name in {PROVENANCE_LOG.name, PROVENANCE_INDEX.name}:
        return False
    return True
