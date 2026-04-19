from __future__ import annotations

from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any
import json
import re


def to_jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return to_jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [to_jsonable(item) for item in value]
    return value


def dumps(value: Any, *, indent: int | None = 2) -> str:
    return json.dumps(to_jsonable(value), ensure_ascii=False, indent=indent)


def parse_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    if not text:
        raise ValueError("empty model response")
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        for candidate in reversed(_json_object_candidates(text)):
            try:
                value = json.loads(candidate)
                break
            except json.JSONDecodeError:
                continue
        else:
            preview = re.sub(r"\s+", " ", text)[:500]
            raise ValueError(f"could not parse JSON object from model response: {preview}")
    if not isinstance(value, dict):
        raise ValueError("expected a JSON object")
    return value


def _json_object_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    start: int | None = None
    depth = 0
    in_string = False
    escaped = False

    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start is not None:
                candidates.append(text[start : index + 1])
                start = None

    return candidates
