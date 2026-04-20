# Knapsack Heuristic Optimization

This benchmark is intended for `agts-research`.

## Task

Improve `solver.py` for 0/1 knapsack. The solver receives JSON instances with item weights,
values, and capacity, and must output selected original item indices.

The objective is to maximize total selected value, with a small runtime penalty. The evaluator
computes the exact optimal value for each hidden instance by dynamic programming, so workers get
a precise private-dev score without seeing the hidden instances.

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
    {"id": "instance-id", "items": [0, 2, 5]}
  ]
}
```

Each solution contains original item indices. The evaluator checks that indices are unique and
that total selected weight does not exceed capacity.

## Launch

```bash
python -m agts.cli research start -c benchmarks/knapsack/research.json
python -m agts.cli research monitor <run_dir> --iterations 8 --interval 5 --worker-timeout 600
```

After selecting a candidate, run the final holdout once from the supervisor:

```bash
python -m agts.cli research verify <run_dir>
python -m agts.cli research final-eval <run_dir> -m "final holdout"
python -m agts.cli research report <run_dir>
```
