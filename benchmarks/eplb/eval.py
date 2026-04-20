from __future__ import annotations

import json
import math
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
        timeout=10,
    )
    elapsed = time.perf_counter() - start
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "solver failed")
    return json.loads(result.stdout), elapsed


def validate_and_cost(instance: dict, placements: dict) -> tuple[bool, str, dict[str, float]]:
    devices = {int(device["id"]): device for device in instance["devices"]}
    experts = {str(expert["id"]): expert for expert in instance["experts"]}
    replicas = int(instance.get("replicas", 2))
    memory_used = {device_id: 0 for device_id in devices}
    load_used = {device_id: 0.0 for device_id in devices}
    if not isinstance(placements, dict):
        return False, "placements must be an object", {}
    for expert_id, expert in experts.items():
        assigned = placements.get(expert_id)
        if not isinstance(assigned, list):
            return False, f"missing placement for expert {expert_id}", {}
        if len(assigned) != replicas:
            return False, f"expert {expert_id} has {len(assigned)} replicas, expected {replicas}", {}
        if len(set(assigned)) != replicas:
            return False, f"expert {expert_id} has duplicate replica devices", {}
        for raw_device_id in assigned:
            if not isinstance(raw_device_id, int) or raw_device_id not in devices:
                return False, f"expert {expert_id} uses invalid device {raw_device_id}", {}
            memory_used[raw_device_id] += int(expert["memory"])
            load_used[raw_device_id] += float(expert["load"]) / replicas
    for device_id, device in devices.items():
        if memory_used[device_id] > int(device["memory_capacity"]):
            return False, f"device {device_id} memory exceeded", {}

    loads = list(load_used.values())
    mean_load = sum(loads) / max(1, len(loads))
    imbalance = max(loads) / max(1e-9, mean_load)
    variance = sum((load - mean_load) ** 2 for load in loads) / max(1, len(loads))
    stddev = math.sqrt(variance)
    traffic_penalty = _traffic_penalty(instance, placements)
    cost = 1.0 + 1.7 * (imbalance - 1.0) + 0.025 * stddev + traffic_penalty
    return True, "", {
        "cost": cost,
        "imbalance": imbalance,
        "stddev": stddev,
        "traffic_penalty": traffic_penalty,
        "max_load": max(loads) if loads else 0.0,
        "mean_load": mean_load,
        "used_memory": float(sum(memory_used.values())),
    }


def _traffic_penalty(instance: dict, placements: dict) -> float:
    penalty = 0.0
    groups = instance.get("groups", [])
    for group in groups:
        members = [str(item) for item in group.get("experts", [])]
        weight = float(group.get("weight", 1.0))
        for index, left in enumerate(members):
            for right in members[index + 1 :]:
                left_devices = set(placements.get(left, []))
                right_devices = set(placements.get(right, []))
                if not left_devices or not right_devices:
                    penalty += weight
                elif left_devices.isdisjoint(right_devices):
                    penalty += weight * 0.35
    return penalty


def greedy_baseline_cost(instance: dict) -> float:
    devices = instance["devices"]
    experts = sorted(instance["experts"], key=lambda item: (item["load"], item["memory"]), reverse=True)
    replicas = int(instance.get("replicas", 2))
    memory_used = [0 for _ in devices]
    load_used = [0.0 for _ in devices]
    placements: dict[str, list[int]] = {}
    for expert in experts:
        chosen: list[int] = []
        for _ in range(replicas):
            candidates = []
            for device in devices:
                device_id = int(device["id"])
                if device_id in chosen:
                    continue
                if memory_used[device_id] + int(expert["memory"]) > int(device["memory_capacity"]):
                    continue
                projected = load_used[device_id] + float(expert["load"]) / replicas
                candidates.append((projected, device_id))
            if not candidates:
                return 1e9
            _, device_id = min(candidates)
            chosen.append(device_id)
            memory_used[device_id] += int(expert["memory"])
            load_used[device_id] += float(expert["load"]) / replicas
        placements[str(expert["id"])] = chosen
    ok, _, metrics = validate_and_cost(instance, placements)
    return metrics["cost"] if ok else 1e9


def main() -> int:
    data = load_instances()
    tmp_path = Path(f".agts_eplb_eval_instances_{os.getpid()}_{uuid.uuid4().hex}.json").resolve()
    tmp_path.write_text(json.dumps(data), encoding="utf-8")
    try:
        output, elapsed = run_solver(tmp_path)
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass

    by_id = {solution["id"]: solution.get("placements", {}) for solution in output.get("solutions", [])}
    invalid: list[str] = []
    total_cost = 0.0
    total_baseline_cost = 0.0
    max_imbalance = 0.0
    total_traffic_penalty = 0.0
    for instance in data["instances"]:
        placements = by_id.get(instance["id"])
        if placements is None:
            invalid.append(f"missing solution for {instance['id']}")
            continue
        ok, reason, metrics = validate_and_cost(instance, placements)
        if not ok:
            invalid.append(f"{instance['id']}: {reason}")
            continue
        total_cost += metrics["cost"]
        total_baseline_cost += greedy_baseline_cost(instance)
        max_imbalance = max(max_imbalance, metrics["imbalance"])
        total_traffic_penalty += metrics["traffic_penalty"]

    if invalid:
        bundle = {
            "score": 0.0,
            "valid": False,
            "failure_reason": "invalid placement",
            "metrics": {
                "invalid_count": len(invalid),
                "total_cost": total_cost,
                "baseline_cost": total_baseline_cost,
                "elapsed_seconds": elapsed,
            },
        }
        print(f"AGTS_SCORE_BUNDLE={json.dumps(bundle, sort_keys=True)}")
        print("score: 0.0")
        for item in invalid[:10]:
            print(f"- {item}")
        return 0

    quality = total_baseline_cost / max(1e-9, total_cost)
    runtime_penalty = min(0.03, elapsed / 300.0)
    score = max(0.0, quality - runtime_penalty)
    bundle = {
        "score": score,
        "valid": True,
        "metrics": {
            "quality": quality,
            "runtime_penalty": runtime_penalty,
            "total_cost": total_cost,
            "baseline_cost": total_baseline_cost,
            "max_imbalance": max_imbalance,
            "traffic_penalty": total_traffic_penalty,
            "elapsed_seconds": elapsed,
            "instance_count": len(data["instances"]),
        },
    }
    print(f"AGTS_SCORE_BUNDLE={json.dumps(bundle, sort_keys=True)}")
    print(f"score: {score:.8f}")
    print(f"total_cost: {total_cost:.6f}")
    print(f"baseline_cost: {total_baseline_cost:.6f}")
    print(f"max_imbalance: {max_imbalance:.6f}")
    print(f"traffic_penalty: {total_traffic_penalty:.6f}")
    print(f"elapsed_seconds: {elapsed:.6f}")
    print(f"public_instances: {PUBLIC}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
