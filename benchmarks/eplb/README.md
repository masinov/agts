# Expert Placement Load Balancing

This benchmark is intended for `agts-research`.

## Task

Improve `solver.py` for expert placement load balancing. Each instance has devices with memory
capacities, experts with load and memory requirements, a replica count, and traffic groups. The
solver must assign each expert to the required number of distinct devices without exceeding device
memory.

The objective rewards lower placement cost:

- lower max-load imbalance
- lower load standard deviation
- lower traffic-group separation penalty
- small runtime penalty

The score is normalized against a hidden greedy baseline:

```text
score = baseline_cost / candidate_cost - runtime_penalty
```

The benchmark has three data splits:

- `public_instances.json` is visible to workers.
- `private_instances.json` is used for iterative private-dev evals.
- `final_instances.json` is reserved for supervisor-only final holdout evals.

## Solver Interface

```bash
python solver.py instances.json
```

Output:

```json
{
  "solutions": [
    {
      "id": "instance-id",
      "placements": {
        "0": [1, 5],
        "1": [2, 7]
      }
    }
  ]
}
```

Placement keys are expert ids as strings. Device ids are integers.

## Launch

```bash
python -m agts.cli research start -c benchmarks/eplb/research.json
python -m agts.cli research monitor <run_dir> --iterations 8 --interval 5 --worker-timeout 600
```

After selecting a candidate, run the final holdout once from the supervisor:

```bash
python -m agts.cli research verify <run_dir>
python -m agts.cli research final-eval <run_dir> -m "final holdout"
python -m agts.cli research report <run_dir>
```
