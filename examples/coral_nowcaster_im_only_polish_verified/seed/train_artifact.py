from __future__ import annotations

import argparse
import json
import platform
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from predictor import ARTIFACT_FORMAT_VERSION, _add_time_features, _sanitize_features
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.model_selection import TimeSeriesSplit

try:
    import lightgbm as lgb

    _HAS_LGB = True
except ImportError:
    _HAS_LGB = False


TARGET_COL = "fin_residue"
DROP_COLS = {"read_time_ms", "fin_residue", "dfin"}
N_SPLITS = 7


def _feature_cols(df: pd.DataFrame) -> list[str]:
    cols = [c for c in df.columns if c not in DROP_COLS]
    return [c for c in cols if np.issubdtype(df[c].dtype, np.number)]


def _clip_target(y: np.ndarray) -> tuple[np.ndarray, float, float]:
    low = float(np.percentile(y, 1))
    high = float(np.percentile(y, 99))
    return np.clip(y, low, high), low, high


def _time_splitter(n_samples: int, n_splits: int = N_SPLITS) -> TimeSeriesSplit:
    adjusted = min(n_splits, n_samples - 1)
    if adjusted < 2:
        raise ValueError(f"Need at least 3 samples for time-series CV, got {n_samples}")
    return TimeSeriesSplit(n_splits=adjusted)


def _make_lgb(cfg: dict[str, Any], *, seed: int):
    if not _HAS_LGB:
        raise RuntimeError("LightGBM is required to train the selected production artifact.")
    params = dict(cfg)
    quantile = params.pop("quantile", None)
    common = dict(
        n_estimators=params["n_est"],
        learning_rate=params["lr"],
        num_leaves=params["leaves"],
        min_child_samples=params["min_child"],
        reg_lambda=params["reg_lambda"],
        reg_alpha=params.get("reg_alpha", 0.0),
        subsample=params.get("subsample", 0.75),
        colsample_bytree=params.get("colsample", 0.7),
        random_state=seed,
        verbosity=-1,
        n_jobs=1,
    )
    if quantile is not None:
        common["objective"] = "quantile"
        common["alpha"] = quantile
    return lgb.LGBMRegressor(**common)


def _make_hgb(cfg: dict[str, Any], *, seed: int) -> HistGradientBoostingRegressor:
    return HistGradientBoostingRegressor(
        loss="squared_error",
        learning_rate=cfg["lr"],
        max_iter=cfg["n_est"],
        max_leaf_nodes=2 ** cfg["max_depth"],
        min_samples_leaf=cfg["min_leaf"],
        l2_regularization=cfg["l2"],
        random_state=seed,
    )


def _fit_oof_models(
    spec: dict[str, Any],
    X: pd.DataFrame,
    y: np.ndarray,
    *,
    sample_weight: np.ndarray | None,
    seed: int = 42,
) -> tuple[list[Any], np.ndarray]:
    splitter = _time_splitter(len(X), N_SPLITS)
    fold_models: list[Any] = []
    oof = np.full(len(y), np.nan)
    for fold, (tr_idx, val_idx) in enumerate(splitter.split(X)):
        model_seed = seed + fold
        if spec["kind"] == "lgb":
            model = _make_lgb(spec["config"], seed=model_seed)
        elif spec["kind"] == "hgb":
            model = _make_hgb(spec["config"], seed=model_seed)
        else:
            raise ValueError(f"Unknown model kind: {spec['kind']!r}")
        w_tr = sample_weight[tr_idx] if sample_weight is not None else None
        model.fit(X.iloc[tr_idx], y[tr_idx], sample_weight=w_tr)
        oof[val_idx] = model.predict(X.iloc[val_idx])
        fold_models.append(model)
    return fold_models, oof


def _model_specs() -> list[dict[str, Any]]:
    quantile_cfgs = [
        dict(n_est=1500, lr=0.02, leaves=15, min_child=20, reg_lambda=5.0, quantile=0.50),
        dict(n_est=1500, lr=0.02, leaves=15, min_child=30, reg_lambda=10.0, quantile=0.50),
        dict(n_est=1200, lr=0.03, leaves=15, min_child=20, reg_lambda=5.0, quantile=0.50),
        dict(n_est=1500, lr=0.02, leaves=15, min_child=20, reg_lambda=5.0, quantile=0.52),
        dict(n_est=1200, lr=0.03, leaves=15, min_child=25, reg_lambda=8.0, quantile=0.52),
        dict(n_est=1500, lr=0.02, leaves=15, min_child=20, reg_lambda=5.0, quantile=0.48),
    ]
    mean_cfgs = [
        dict(n_est=1500, lr=0.02, leaves=15, min_child=20, reg_lambda=5.0, reg_alpha=1.0),
        dict(n_est=1200, lr=0.03, leaves=15, min_child=25, reg_lambda=3.0, reg_alpha=1.0),
        dict(n_est=2000, lr=0.01, leaves=15, min_child=20, reg_lambda=8.0, reg_alpha=2.0),
    ]
    hgb_cfgs = [
        dict(lr=0.03, n_est=600, min_leaf=15, l2=3.0, max_depth=5),
        dict(lr=0.02, n_est=800, min_leaf=20, l2=5.0, max_depth=4),
        dict(lr=0.05, n_est=300, min_leaf=25, l2=5.0, max_depth=5),
    ]
    specs: list[dict[str, Any]] = []
    for i, cfg in enumerate(quantile_cfgs):
        specs.append({"name": f"lgb_quantile_{i}", "kind": "lgb", "config": cfg})
    for i, cfg in enumerate(mean_cfgs):
        specs.append({"name": f"lgb_mean_{i}", "kind": "lgb", "config": cfg})
    for i, cfg in enumerate(hgb_cfgs):
        specs.append({"name": f"hgb_{i}", "kind": "hgb", "config": cfg})
    return specs


def _recent_sample_weight(fit_df: pd.DataFrame) -> np.ndarray | None:
    if "read_time_ms" not in fit_df.columns:
        return None
    t_ms = fit_df["read_time_ms"].to_numpy(dtype=float)
    t_norm = (t_ms - t_ms.min()) / (t_ms.max() - t_ms.min() + 1e-10)
    sample_weight = np.exp(t_norm - 1.0)
    return sample_weight / sample_weight.mean()


def _select_stacker(
    oof_stack_v: np.ndarray,
    y_fit_v: np.ndarray,
    y_fit_clipped_v: np.ndarray,
) -> dict[str, Any]:
    oof_rmses = np.array([
        np.sqrt(np.mean((oof_stack_v[:, i] - y_fit_v) ** 2))
        for i in range(oof_stack_v.shape[1])
    ])
    inv_rmse_w = 1.0 / oof_rmses
    inv_rmse_w = inv_rmse_w / inv_rmse_w.sum()

    ridge = Ridge(alpha=1.0)
    ridge.fit(oof_stack_v, y_fit_clipped_v)

    scores = {"weighted": [], "ridge": [], "blend70q30r": []}
    splitter = _time_splitter(len(oof_stack_v), N_SPLITS)
    for tr_idx, val_idx in splitter.split(oof_stack_v):
        y_tr_v = y_fit_v[tr_idx]
        y_val_v = y_fit_v[val_idx]
        y_tr_clipped, _low, _high = _clip_target(y_tr_v)

        w = 1.0 / np.array([
            np.sqrt(np.mean((oof_stack_v[tr_idx, i] - y_tr_v) ** 2))
            for i in range(oof_stack_v.shape[1])
        ])
        w = w / w.sum()
        p_w = oof_stack_v[val_idx] @ w

        m = Ridge(alpha=1.0)
        m.fit(oof_stack_v[tr_idx], y_tr_clipped)
        p_r = m.predict(oof_stack_v[val_idx])
        p_b = 0.7 * p_w + 0.3 * p_r

        scores["weighted"].append(np.mean((p_w - y_val_v) ** 2))
        scores["ridge"].append(np.mean((p_r - y_val_v) ** 2))
        scores["blend70q30r"].append(np.mean((p_b - y_val_v) ** 2))

    mean_scores = {k: float(np.mean(v)) for k, v in scores.items()}
    selected = min(mean_scores, key=mean_scores.get)
    return {
        "selected_method": selected,
        "cv_mse": mean_scores,
        "inverse_rmse_weights": inv_rmse_w.tolist(),
        "oof_rmses": oof_rmses.tolist(),
        "ridge": ridge,
    }


def train_artifact(train_path: str, dev_path: str, out_dir: str) -> dict[str, Any]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    train_df = _add_time_features(pd.read_csv(train_path))
    dev_df = _add_time_features(pd.read_csv(dev_path))
    feature_cols = _feature_cols(train_df)

    fit_df = pd.concat([train_df, dev_df], ignore_index=True)
    if "read_time_ms" in fit_df.columns:
        fit_df = fit_df.sort_values("read_time_ms").reset_index(drop=True)

    X_fit = _sanitize_features(fit_df, feature_cols)
    y_fit = fit_df[TARGET_COL].to_numpy(dtype=float)
    y_fit_clipped, clip_low, clip_high = _clip_target(y_fit)
    sample_weight = _recent_sample_weight(fit_df)

    base_models = []
    oof_preds = []
    for spec in _model_specs():
        print(f"Fitting {spec['name']} ...", flush=True)
        fold_models, oof = _fit_oof_models(
            spec,
            X_fit,
            y_fit_clipped,
            sample_weight=sample_weight,
            seed=42,
        )
        base_models.append(
            {
                "name": spec["name"],
                "kind": spec["kind"],
                "config": spec["config"],
                "fold_models": fold_models,
            }
        )
        oof_preds.append(oof)

    oof_stack = np.column_stack(oof_preds)
    valid_mask = np.all(np.isfinite(oof_stack), axis=1)
    stacker = _select_stacker(
        oof_stack[valid_mask],
        y_fit[valid_mask],
        y_fit_clipped[valid_mask],
    )

    artifact = {
        "artifact_format_version": ARTIFACT_FORMAT_VERSION,
        "model_name": "instantaneous_fineness_nowcaster",
        "source_coral_commit": "18d3194482c66356553fa6c6f3c9cdaf0fc3a1d3",
        "source_hidden_rmse": 2.8858483667302077,
        "target_col": TARGET_COL,
        "drop_cols": sorted(DROP_COLS),
        "feature_cols": feature_cols,
        "n_features": len(feature_cols),
        "n_train_rows": int(len(train_df)),
        "n_dev_rows": int(len(dev_df)),
        "n_fit_rows": int(len(fit_df)),
        "target_clip": {"low_p01": clip_low, "high_p99": clip_high},
        "sample_weight": {
            "kind": "recent_exponential",
            "enabled": sample_weight is not None,
        },
        "base_models": base_models,
        "stacker": stacker,
        "environment": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "lightgbm": getattr(lgb, "__version__", None) if _HAS_LGB else None,
        },
    }

    artifact_path = out / "fineness_nowcaster.joblib"
    joblib.dump(artifact, artifact_path, compress=3)

    manifest = {
        k: v for k, v in artifact.items()
        if k not in {"base_models", "stacker"}
    }
    manifest["artifact_path"] = artifact_path.name
    manifest["base_models"] = [
        {
            "name": m["name"],
            "kind": m["kind"],
            "config": m["config"],
            "n_folds": len(m["fold_models"]),
        }
        for m in base_models
    ]
    manifest["stacker"] = {
        k: v for k, v in stacker.items()
        if k != "ridge"
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main() -> None:
    ap = argparse.ArgumentParser(description="Train and serialize the production fineness nowcaster artifact.")
    ap.add_argument("--train", required=True)
    ap.add_argument("--dev", required=True)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()

    manifest = train_artifact(args.train, args.dev, args.out_dir)
    print(json.dumps({
        "artifact": manifest["artifact_path"],
        "n_features": manifest["n_features"],
        "selected_method": manifest["stacker"]["selected_method"],
    }))


if __name__ == "__main__":
    main()
