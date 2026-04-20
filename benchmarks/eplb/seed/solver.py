from __future__ import annotations

import json
import sys


def solve_instance(instance: dict) -> dict[str, list[int]]:
    devices = instance["devices"]
    experts = instance["experts"]
    replicas = int(instance.get("replicas", 2))
    memory_used = [0 for _ in devices]
    load_used = [0.0 for _ in devices]
    placements: dict[str, list[int]] = {}
    ordered = sorted(experts, key=lambda item: (item["load"], item["memory"]), reverse=True)
    for expert in ordered:
        chosen: list[int] = []
        for _ in range(replicas):
            candidates = []
            for device in devices:
                device_id = int(device["id"])
                if device_id in chosen:
                    continue
                if memory_used[device_id] + int(expert["memory"]) > int(device["memory_capacity"]):
                    continue
                projected_load = load_used[device_id] + float(expert["load"]) / replicas
                candidates.append((projected_load, load_used[device_id], device_id))
            if not candidates:
                break
            _, _, device_id = min(candidates)
            chosen.append(device_id)
            memory_used[device_id] += int(expert["memory"])
            load_used[device_id] += float(expert["load"]) / replicas
        placements[str(expert["id"])] = chosen
    return placements


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else "public_instances.json"
    data = json.loads(open(path, encoding="utf-8").read())
    output = {
        "solutions": [
            {"id": instance["id"], "placements": solve_instance(instance)}
            for instance in data["instances"]
        ]
    }
    print(json.dumps(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
