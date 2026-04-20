from __future__ import annotations

import json
import sys


def solve_instance(instance: dict) -> list[int]:
    items = list(instance["items"])
    capacity = int(instance["capacity"])
    ordered = sorted(
        enumerate(items),
        key=lambda pair: (
            pair[1]["value"] / max(1, pair[1]["weight"]),
            pair[1]["value"],
        ),
        reverse=True,
    )
    chosen: list[int] = []
    used = 0
    for index, item in ordered:
        weight = int(item["weight"])
        if used + weight <= capacity:
            chosen.append(index)
            used += weight
    return sorted(chosen)


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else "public_instances.json"
    data = json.loads(open(path, encoding="utf-8").read())
    output = {
        "solutions": [
            {"id": instance["id"], "items": solve_instance(instance)}
            for instance in data["instances"]
        ]
    }
    print(json.dumps(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
