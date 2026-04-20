from __future__ import annotations

from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd


ARTIFACT_FORMAT_VERSION = "fineness-nowcaster-v1"


def _add_time_features(df: pd.DataFrame) -> pd.DataFrame:
    if "read_time_ms" not in df.columns:
        return df
    out = df.copy()
    t = pd.to_datetime(out["read_time_ms"], unit="ms", utc=True)
    out["_time_hour_sin"] = np.sin(2 * np.pi * t.dt.hour / 24.0)
    out["_time_hour_cos"] = np.cos(2 * np.pi * t.dt.hour / 24.0)
    return out


def _sanitize_features(df: pd.DataFrame, feature_cols: list[str]) -> pd.DataFrame:
    out = df[feature_cols].replace([np.inf, -np.inf], np.nan)
    return out.ffill().bfill().fillna(0.0)


def load_artifact(path: str | Path) -> dict[str, Any]:
    artifact = joblib.load(path)
    version = artifact.get("artifact_format_version")
    if version != ARTIFACT_FORMAT_VERSION:
        raise ValueError(f"Unsupported artifact format: {version!r}")
    return artifact


def required_feature_columns(artifact: dict[str, Any]) -> list[str]:
    return list(artifact["feature_cols"])


def validate_feature_schema(df: pd.DataFrame, artifact: dict[str, Any]) -> None:
    with_time = _add_time_features(df)
    required = required_feature_columns(artifact)
    missing = [c for c in required if c not in with_time.columns]
    if missing:
        preview = ", ".join(missing[:20])
        suffix = "" if len(missing) <= 20 else f" ... (+{len(missing) - 20} more)"
        raise ValueError(
            f"Input feature table is missing {len(missing)} required feature column(s): "
            f"{preview}{suffix}"
        )


def _base_prediction_stack(artifact: dict[str, Any], X: pd.DataFrame) -> np.ndarray:
    base_preds: list[np.ndarray] = []
    for model_bundle in artifact["base_models"]:
        fold_preds = [np.asarray(model.predict(X), dtype=float) for model in model_bundle["fold_models"]]
        base_preds.append(np.mean(np.vstack(fold_preds), axis=0))
    return np.column_stack(base_preds)


def predict_df(features_df: pd.DataFrame, artifact_or_path: dict[str, Any] | str | Path) -> np.ndarray:
    artifact = (
        load_artifact(artifact_or_path)
        if isinstance(artifact_or_path, (str, Path))
        else artifact_or_path
    )
    validate_feature_schema(features_df, artifact)
    with_time = _add_time_features(features_df)
    X = _sanitize_features(with_time, required_feature_columns(artifact))
    stack = _base_prediction_stack(artifact, X)

    stacker = artifact["stacker"]
    method = stacker["selected_method"]
    weighted = stack @ np.asarray(stacker["inverse_rmse_weights"], dtype=float)
    ridge = np.asarray(stacker["ridge"].predict(stack), dtype=float)

    if method == "weighted":
        return weighted
    if method == "ridge":
        return ridge
    if method == "blend70q30r":
        return 0.7 * weighted + 0.3 * ridge
    raise ValueError(f"Unknown stacker method: {method!r}")


def predict_csv(input_csv: str | Path, artifact_or_path: dict[str, Any] | str | Path) -> np.ndarray:
    return predict_df(pd.read_csv(input_csv), artifact_or_path)


def write_predictions_csv(
    input_csv: str | Path,
    output_csv: str | Path,
    artifact_or_path: dict[str, Any] | str | Path,
    *,
    prediction_col: str = "fin_residue_pred",
) -> None:
    df = pd.read_csv(input_csv)
    pred = predict_df(df, artifact_or_path)
    out = pd.DataFrame({prediction_col: pred})
    if "read_time_ms" in df.columns:
        out.insert(0, "read_time_ms", df["read_time_ms"].to_numpy())
    Path(output_csv).parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(output_csv, index=False)


def main() -> None:
    import argparse
    import json

    ap = argparse.ArgumentParser(description="Run the fitted fineness nowcaster artifact on an engineered feature table.")
    ap.add_argument("--artifact", required=True)
    ap.add_argument("--input", required=True, help="CSV/CSV.GZ feature table with the training feature schema.")
    ap.add_argument("--output", default=None, help="Optional output CSV for predictions.")
    args = ap.parse_args()

    pred = predict_csv(args.input, args.artifact)
    if args.output:
        write_predictions_csv(args.input, args.output, args.artifact)
    print(json.dumps({"n": int(pred.size), "mean_pred": float(np.mean(pred))}))


if __name__ == "__main__":
    main()
