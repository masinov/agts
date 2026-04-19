#!/usr/bin/env python3
"""Best-effort JSON artifact validation for Claude Code hooks."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _candidate_path() -> Path | None:
    raw = os.environ.get("CLAUDE_TOOL_INPUT")
    if raw:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = {}
        value = data.get("file_path") or data.get("path")
        if value:
            return Path(value)

    if len(sys.argv) > 1:
        return Path(sys.argv[1])
    return None


def _validate_jsonl(path: Path) -> None:
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            if line.strip():
                json.loads(line)


def main() -> int:
    path = _candidate_path()
    if path is None or not path.exists():
        return 0
    if ".tot" not in path.parts:
        return 0

    try:
        if path.suffix == ".json":
            json.loads(path.read_text(encoding="utf-8"))
        elif path.suffix == ".jsonl":
            _validate_jsonl(path)
    except json.JSONDecodeError as exc:
        print(f"{path}: invalid JSON artifact: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
