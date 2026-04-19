# Bin Packing Heuristic Optimization

This benchmark is intended for `agts-research`.

## Task

Improve `solver.py` for one-dimensional bin packing. The solver receives JSON instances with item sizes and capacity, and must output valid bins as lists of original item indices.

The objective is to minimize total bins used, with a small runtime penalty.

## Solver Interface

```bash
python solver.py instances.json
```

Output:

```json
{
  "solutions": [
    {"id": "instance-id", "bins": [[0, 2], [1, 3]]}
  ]
}
```

Each bin contains original item indices. The evaluator checks that every item is assigned exactly once and that no bin exceeds capacity.

## Launch

```bash
python -m agts.cli research start -c benchmarks/bin_packing/research.json
python -m agts.cli research monitor <run_dir> --iterations 8 --interval 5 --worker-timeout 600
```

For a safe process-only test:

```bash
python -m agts.cli research monitor <run_dir> --iterations 2 --interval 0.5 --dry-run --dry-run-seconds 0.1
```
