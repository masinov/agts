from __future__ import annotations

import json
import sys
from pathlib import Path


def solve_instance(items: list[float], capacity: float = 1.0) -> list[list[int]]:
    """Baseline first-fit decreasing solver.

    Returns bins as lists of original item indices.
    """
    order = sorted(range(len(items)), key=lambda index: items[index], reverse=True)
    bins: list[list[int]] = []
    remaining: list[float] = []
    for index in order:
        size = items[index]
        placed = False
        for bin_index, free in enumerate(remaining):
            if size <= free + 1e-12:
                bins[bin_index].append(index)
                remaining[bin_index] -= size
                placed = True
                break
        if not placed:
            bins.append([index])
            remaining.append(capacity - size)
    return bins


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python solver.py instances.json", file=sys.stderr)
        return 2
    data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    solutions = []
    for instance in data["instances"]:
        capacity = float(instance.get("capacity", 1.0))
        items = [float(item) for item in instance["items"]]
        solutions.append(
            {
                "id": instance["id"],
                "bins": solve_instance(items, capacity),
            }
        )
    print(json.dumps({"solutions": solutions}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
