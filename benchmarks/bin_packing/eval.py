from __future__ import annotations

import json
import math
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PRIVATE = ROOT / "private_instances.json"
PUBLIC = ROOT / "public_instances.json"


def lower_bound(items: list[float], capacity: float) -> int:
    return math.ceil(sum(items) / capacity)


def validate_solution(instance: dict, bins: list[list[int]]) -> tuple[bool, str, int]:
    items = [float(item) for item in instance["items"]]
    capacity = float(instance.get("capacity", 1.0))
    seen: list[int] = []
    for bin_no, bin_items in enumerate(bins):
        total = 0.0
        for index in bin_items:
            if not isinstance(index, int) or index < 0 or index >= len(items):
                return False, f"invalid item index {index} in bin {bin_no}", 0
            seen.append(index)
            total += items[index]
        if total > capacity + 1e-9:
            return False, f"bin {bin_no} exceeds capacity: {total}", 0
    if sorted(seen) != list(range(len(items))):
        return False, "items are missing or duplicated", 0
    return True, "", len(bins)


def load_instances() -> dict:
    # The private set is intentionally referenced by absolute path from the evaluator.
    # Workers should not read this file directly; they should optimize against feedback.
    return json.loads(PRIVATE.read_text(encoding="utf-8"))


def run_solver(instances_path: Path) -> tuple[dict, float]:
    start = time.perf_counter()
    result = subprocess.run(
        [sys.executable, "solver.py", str(instances_path)],
        capture_output=True,
        text=True,
        timeout=8,
    )
    elapsed = time.perf_counter() - start
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "solver failed")
    return json.loads(result.stdout), elapsed


def main() -> int:
    data = load_instances()
    tmp_path = Path(".agts_bin_packing_eval_instances.json").resolve()
    tmp_path.write_text(json.dumps(data), encoding="utf-8")
    try:
        output, elapsed = run_solver(tmp_path)
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass

    by_id = {solution["id"]: solution["bins"] for solution in output.get("solutions", [])}
    total_bins = 0
    total_lb = 0
    invalid = []
    for instance in data["instances"]:
        bins = by_id.get(instance["id"])
        if bins is None:
            invalid.append(f"missing solution for {instance['id']}")
            continue
        ok, reason, count = validate_solution(instance, bins)
        if not ok:
            invalid.append(f"{instance['id']}: {reason}")
            continue
        total_bins += count
        total_lb += lower_bound(instance["items"], float(instance.get("capacity", 1.0)))

    if invalid:
        print("score: 0.0")
        print("invalid:")
        for item in invalid[:10]:
            print(f"- {item}")
        return 0

    quality = total_lb / max(1, total_bins)
    runtime_penalty = min(0.05, elapsed / 200.0)
    score = max(0.0, quality - runtime_penalty)
    print(f"score: {score:.8f}")
    print(f"total_bins: {total_bins}")
    print(f"lower_bound: {total_lb}")
    print(f"elapsed_seconds: {elapsed:.6f}")
    print(f"public_instances: {PUBLIC}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
