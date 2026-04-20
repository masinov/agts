from __future__ import annotations

import os
import subprocess
import sys
import json
from pathlib import Path

import numpy as np
import pandas as pd


def main() -> int:
    split = os.environ.get("AGTS_EVAL_SPLIT", "private_dev")
    private_dir = Path(os.environ.get("AGTS_PRIVATE_DIR", ""))
    targets_path = private_dir / "hidden_eval_targets.csv.gz"

    pred_path = Path(os.environ.get("AGTS_PRED_DIR", "/tmp")) / "hidden_eval_predictions.csv"
    if not pred_path.exists():
        pred_path = seed_dir.parent / "hidden_eval_predictions.csv"

    targets = pd.read_csv(private_dir / "hidden_eval_targets.csv.gz")
    if not pred_path.exists():
        print(f"ERROR: predictions not found at {pred_path}", file=sys.stderr)
        return 1

    preds = pd.read_csv(pred_path)
    if "fin_residue_pred" not in preds.columns:
        print(f"ERROR: prediction CSV missing fin_residue_pred column", file=sys.stderr)
        return 1

    merged = pd.merge(targets, preds, on="read_time_ms", how="inner")
    if merged.empty:
        print("ERROR: no matching read_time_ms between predictions and targets", file=sys.stderr)
        return 1

    y_true = merged["fin_residue"].to_numpy()
    y_pred = merged["fin_residue_pred"].to_numpy()

    rmse = float(np.sqrt(np.mean((y_pred - y_true) ** 2)))
    mae = float(np.mean(np.abs(y_pred - y_true)))
    bias = float(np.mean(y_pred - y_true))

    bundle = {"score": rmse, "rmse": rmse, "mae": mae, "bias": bias, "n_samples": len(merged)}
    print(f"AGTS_SCORE_BUNDLE={json.dumps(bundle, sort_keys=True)}")
    print(f"score: {rmse:.8f}")
    print(f"rmse: {rmse:.8f}")
    print(f"mae: {mae:.8f}")
    print(f"bias: {bias:.8f}")
    print(f"n_samples: {len(merged)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
