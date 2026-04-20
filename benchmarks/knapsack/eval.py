from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PRIVATE = ROOT / "private_instances.json"
FINAL = ROOT / "final_instances.json"
PUBLIC = ROOT / "public_instances.json"


def optimal_value(instance: dict) -> int:
    capacity = int(instance["capacity"])
    dp = [0] * (capacity + 1)
    for item in instance["items"]:
        weight = int(item["weight"])
        value = int(item["value"])
        for remaining in range(capacity, weight - 1, -1):
            dp[remaining] = max(dp[remaining], dp[remaining - weight] + value)
    return max(dp)


def validate_solution(instance: dict, selected: list[int]) -> tuple[bool, str, int, int]:
    items = instance["items"]
    if not isinstance(selected, list):
        return False, "solution items must be a list", 0, 0
    seen: set[int] = set()
    total_weight = 0
    total_value = 0
    for index in selected:
        if not isinstance(index, int) or index < 0 or index >= len(items):
            return False, f"invalid item index {index}", 0, 0
        if index in seen:
            return False, f"duplicate item index {index}", 0, 0
        seen.add(index)
        total_weight += int(items[index]["weight"])
        total_value += int(items[index]["value"])
    if total_weight > int(instance["capacity"]):
        return False, f"capacity exceeded: {total_weight}", 0, 0
    return True, "", total_weight, total_value


def load_instances() -> dict:
    split = os.environ.get("AGTS_EVAL_SPLIT", "private_dev")
    private_dir = Path(os.environ.get("AGTS_PRIVATE_DIR", ROOT))
    filename = "final_instances.json" if split == "final_holdout" else "private_instances.json"
    path = private_dir / filename
    if not path.exists():
        path = FINAL if split == "final_holdout" else PRIVATE
    return json.loads(path.read_text(encoding="utf-8"))


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
    tmp_path = Path(f".agts_knapsack_eval_instances_{os.getpid()}_{uuid.uuid4().hex}.json").resolve()
    tmp_path.write_text(json.dumps(data), encoding="utf-8")
    try:
        output, elapsed = run_solver(tmp_path)
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass

    by_id = {solution["id"]: solution.get("items", []) for solution in output.get("solutions", [])}
    invalid: list[str] = []
    total_value = 0
    total_optimal = 0
    total_weight = 0
    for instance in data["instances"]:
        selected = by_id.get(instance["id"])
        if selected is None:
            invalid.append(f"missing solution for {instance['id']}")
            continue
        ok, reason, weight, value = validate_solution(instance, selected)
        if not ok:
            invalid.append(f"{instance['id']}: {reason}")
            continue
        total_weight += weight
        total_value += value
        total_optimal += optimal_value(instance)

    if invalid:
        bundle = {
            "score": 0.0,
            "valid": False,
            "failure_reason": "invalid solution",
            "metrics": {
                "invalid_count": len(invalid),
                "total_value": total_value,
                "optimal_value": total_optimal,
                "elapsed_seconds": elapsed,
            },
        }
        print(f"AGTS_SCORE_BUNDLE={json.dumps(bundle, sort_keys=True)}")
        print("score: 0.0")
        for item in invalid[:10]:
            print(f"- {item}")
        return 0

    quality = total_value / max(1, total_optimal)
    runtime_penalty = min(0.03, elapsed / 300.0)
    score = max(0.0, quality - runtime_penalty)
    bundle = {
        "score": score,
        "valid": True,
        "metrics": {
            "quality": quality,
            "runtime_penalty": runtime_penalty,
            "total_value": total_value,
            "optimal_value": total_optimal,
            "total_weight": total_weight,
            "elapsed_seconds": elapsed,
            "instance_count": len(data["instances"]),
        },
    }
    print(f"AGTS_SCORE_BUNDLE={json.dumps(bundle, sort_keys=True)}")
    print(f"score: {score:.8f}")
    print(f"total_value: {total_value}")
    print(f"optimal_value: {total_optimal}")
    print(f"elapsed_seconds: {elapsed:.6f}")
    print(f"public_instances: {PUBLIC}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
