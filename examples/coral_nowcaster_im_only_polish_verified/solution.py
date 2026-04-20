from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd


TARGET_COL = "fin_residue"
DROP_COLS = {"read_time_ms", "fin_residue", "dfin"}


def _feature_cols(df: pd.DataFrame) -> list[str]:
    cols = [c for c in df.columns if c not in DROP_COLS]
    return [c for c in cols if np.issubdtype(df[c].dtype, np.number)]


def _add_time_features(df: pd.DataFrame) -> pd.DataFrame:
    if "read_time_ms" not in df.columns:
        return df
    out = df.copy()
    t = pd.to_datetime(out["read_time_ms"], unit="ms", utc=True)
    out["_time_hour_sin"] = np.sin(2 * np.pi * t.dt.hour / 24.0)
    out["_time_hour_cos"] = np.cos(2 * np.pi * t.dt.hour / 24.0)
    out["_time_dow_sin"] = np.sin(2 * np.pi * t.dt.dayofweek / 7.0)
    out["_time_dow_cos"] = np.cos(2 * np.pi * t.dt.dayofweek / 7.0)
    # Rolling statistics for key PBM features
    for col in ['FC_M0803', 'FC_M0903', 'FI_1224_1', 'SC_M1609']:
        if col in out.columns:
            out[f"_roll3_{col}"] = out[col].rolling(3, min_periods=1).mean()
            out[f"_roll_std_{col}"] = out[col].rolling(5, min_periods=1).std().fillna(0)
    return out


def _sanitize(df: pd.DataFrame, feature_cols: list[str]) -> pd.DataFrame:
    out = df[feature_cols].replace([np.inf, -np.inf], np.nan)
    return out.ffill().bfill().fillna(0.0)


def _clip_target(y: np.ndarray) -> tuple[np.ndarray, float, float]:
    low = float(np.percentile(y, 1))
    high = float(np.percentile(y, 99))
    return np.clip(y, low, high), low, high


def _recent_sample_weight(fit_df: pd.DataFrame) -> np.ndarray:
    t_ms = fit_df["read_time_ms"].to_numpy(dtype=float)
    t_norm = (t_ms - t_ms.min()) / (t_ms.max() - t_ms.min() + 1e-10)
    return np.exp(t_norm - 1.0)


def train(train_path: str, dev_path: str, artifact_out: str) -> dict:
    import joblib
    import lightgbm as lgb
    from sklearn.ensemble import HistGradientBoostingRegressor
    from sklearn.linear_model import Ridge, Lasso, ElasticNet
    from sklearn.model_selection import TimeSeriesSplit

    train_df = pd.read_csv(train_path)
    dev_df = pd.read_csv(dev_path)

    for df in [train_df, dev_df]:
        if "read_time_ms" in df.columns:
            df.sort_values("read_time_ms", inplace=True)
            df.reset_index(drop=True, inplace=True)

    fit_df = pd.concat([train_df, dev_df], ignore_index=True)
    fit_df = _add_time_features(fit_df)
    feature_cols_list = _feature_cols(fit_df)
    X = _sanitize(fit_df, feature_cols_list)
    y = fit_df[TARGET_COL].to_numpy(dtype=float)
    y_clipped, clip_low, clip_high = _clip_target(y)
    sample_weight = _recent_sample_weight(fit_df)

    N_SPLITS = 7
    splitter = TimeSeriesSplit(n_splits=min(N_SPLITS, len(X) - 1))

    base_models = []
    oof_preds = []

    lgb_cfgs = [
        dict(n_est=2000, lr=0.015, leaves=20, min_child=15, reg_lambda=3.0, reg_alpha=0.5, subsample=0.8, colsample=0.75),
        dict(n_est=1800, lr=0.02, leaves=15, min_child=20, reg_lambda=4.0, reg_alpha=1.0, subsample=0.75, colsample=0.7),
        dict(n_est=2500, lr=0.01, leaves=15, min_child=20, reg_lambda=6.0, reg_alpha=1.5, subsample=0.8, colsample=0.75),
        dict(n_est=1800, lr=0.02, leaves=25, min_child=25, reg_lambda=8.0, subsample=0.75, colsample=0.7),
        dict(n_est=1500, lr=0.025, leaves=18, min_child=15, reg_lambda=2.0, reg_alpha=0.3, subsample=0.85, colsample=0.8),
        dict(n_est=2200, lr=0.012, leaves=12, min_child=30, reg_lambda=10.0, reg_alpha=2.0, subsample=0.7, colsample=0.65),
        dict(n_est=3000, lr=0.008, leaves=10, min_child=40, reg_lambda=15.0, reg_alpha=3.0, subsample=0.7, colsample=0.6),
    ]
    hgb_cfgs = [
        dict(lr=0.025, n_est=700, min_leaf=15, l2=2.0, max_depth=5),
        dict(lr=0.02, n_est=900, min_leaf=18, l2=4.0, max_depth=4),
        dict(lr=0.04, n_est=400, min_leaf=20, l2=4.0, max_depth=5),
        dict(lr=0.015, n_est=1000, min_leaf=12, l2=3.0, max_depth=6),
        dict(lr=0.03, n_est=500, min_leaf=25, l2=5.0, max_depth=4),
    ]

    for i, cfg in enumerate(lgb_cfgs):
        fold_models = []
        oof = np.full(len(y), np.nan)
        for fold, (tr_idx, val_idx) in enumerate(splitter.split(X)):
            model = lgb.LGBMRegressor(
                n_estimators=cfg["n_est"], learning_rate=cfg["lr"],
                num_leaves=cfg["leaves"], min_child_samples=cfg["min_child"],
                reg_lambda=cfg["reg_lambda"], reg_alpha=cfg.get("reg_alpha", 0.0),
                subsample=cfg.get("subsample", 0.75), colsample_bytree=cfg.get("colsample", 0.7),
                random_state=42 + fold, verbosity=-1, n_jobs=1
            )
            model.fit(X.iloc[tr_idx], y_clipped[tr_idx], sample_weight=sample_weight[tr_idx])
            oof[val_idx] = model.predict(X.iloc[val_idx])
            fold_models.append(model)
        base_models.append({"kind": "lgb", "config": cfg, "fold_models": fold_models})
        oof_preds.append(oof)

    for i, cfg in enumerate(hgb_cfgs):
        fold_models = []
        oof = np.full(len(y), np.nan)
        for fold, (tr_idx, val_idx) in enumerate(splitter.split(X)):
            model = HistGradientBoostingRegressor(
                learning_rate=cfg["lr"], max_iter=cfg["n_est"],
                max_leaf_nodes=2 ** cfg["max_depth"], min_samples_leaf=cfg["min_leaf"],
                l2_regularization=cfg["l2"], random_state=42 + fold
            )
            model.fit(X.iloc[tr_idx], y_clipped[tr_idx], sample_weight=sample_weight[tr_idx])
            oof[val_idx] = model.predict(X.iloc[val_idx])
            fold_models.append(model)
        base_models.append({"kind": "hgb", "config": cfg, "fold_models": fold_models})
        oof_preds.append(oof)

    oof_stack = np.column_stack(oof_preds)
    valid = np.all(np.isfinite(oof_stack), axis=1)

    oof_rmses = np.array([np.sqrt(np.mean((oof_stack[valid, i] - y[valid]) ** 2)) for i in range(oof_stack.shape[1])])
    inv_rmse_w = 1.0 / oof_rmses
    inv_rmse_w = inv_rmse_w / inv_rmse_w.sum()

    # Multiple stackers with different blending strategies
    ridge = Ridge(alpha=1.0)
    ridge.fit(oof_stack[valid], y_clipped[valid])

    # Try different blend ratios
    weighted = np.sum(oof_stack * inv_rmse_w, axis=1)
    ridge_pred = np.full_like(weighted, np.nan)
    ridge_pred[valid] = ridge.predict(oof_stack[valid])

    blend_candidates = {}
    for w_ridge in [0.1, 0.2, 0.3, 0.4, 0.5]:
        w_weighted = 1.0 - w_ridge
        blend_candidates[f"blend_{int(w_weighted*100)}q{int(w_ridge*100)}r"] = w_weighted * weighted + w_ridge * ridge_pred

    scores = {
        "weighted": np.sqrt(np.mean((weighted[valid] - y[valid]) ** 2)),
        "ridge": np.sqrt(np.mean((ridge_pred[valid] - y[valid]) ** 2)),
    }
    for name, blend in blend_candidates.items():
        scores[name] = np.sqrt(np.mean((blend[valid] - y[valid]) ** 2))

    selected = min(scores, key=scores.get)

    artifact = {
        "base_models": base_models,
        "stacker": {
            "selected_method": selected,
            "inverse_rmse_weights": inv_rmse_w.tolist(),
            "ridge": ridge,
            "blend_candidates": {k: v[valid].tolist() for k, v in blend_candidates.items()},
            "weighted_base": weighted[valid].tolist(),
            "ridge_base": ridge_pred[valid].tolist(),
        },
        "feature_cols": feature_cols_list,
        "target_clip": {"low": clip_low, "high": clip_high},
        "sample_weight_enabled": True,
    }
    joblib.dump(artifact, artifact_out, compress=3)
    return {"n_features": len(feature_cols_list), "n_samples": len(fit_df), "selected": selected}


def predict(features_path: str, artifact_path: str, output_path: str) -> dict:
    import joblib

    feat_df = pd.read_csv(features_path)
    feat_df = _add_time_features(feat_df)
    artifact = joblib.load(artifact_path)

    feature_cols = artifact["feature_cols"]
    missing = [c for c in feature_cols if c not in feat_df.columns]
    if missing:
        raise ValueError(f"Missing feature columns: {missing}")

    X = _sanitize(feat_df, feature_cols)

    preds = []
    for bm in artifact["base_models"]:
        fold_preds = [m.predict(X) for m in bm["fold_models"]]
        preds.append(np.mean(np.vstack(fold_preds), axis=0))
    stack = np.column_stack(preds)

    stacker = artifact["stacker"]
    weights = np.asarray(stacker["inverse_rmse_weights"])
    weighted = stack @ weights
    ridge_pred = stacker["ridge"].predict(stack)

    method = stacker["selected_method"]
    if method == "weighted":
        final = weighted
    elif method == "ridge":
        final = ridge_pred
    elif method.startswith("blend_"):
        # Reconstruct blend from stored vectors (not possible for new data)
        # Use computed predictions directly
        w_str = method.replace("blend_", "").replace("q", "_").replace("r", "")
        parts = w_str.split("_")
        w_weighted = int(parts[0]) / 100.0
        w_ridge = int(parts[1]) / 100.0
        final = w_weighted * weighted + w_ridge * ridge_pred
    else:
        final = weighted

    out = pd.DataFrame({"read_time_ms": feat_df["read_time_ms"], "fin_residue_pred": final})
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(output_path, index=False)
    return {"n": len(final), "mean_pred": float(np.mean(final))}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", default="seed/train_samples.csv.gz")
    ap.add_argument("--dev", default="seed/dev_samples.csv.gz")
    ap.add_argument("--predict", default=None, help="Features CSV to predict")
    ap.add_argument("--artifact-in", default="fineness_nowcaster.joblib")
    ap.add_argument("--artifact-out", default="fineness_nowcaster.joblib")
    ap.add_argument("--out", default="/tmp/hidden_eval_predictions.csv")
    args = ap.parse_args()

    seed_dir = Path(__file__).parent

    if args.predict:
        result = predict(args.predict, args.artifact_in, args.out)
        print(json.dumps(result))
    else:
        result = train(seed_dir / args.train, seed_dir / args.dev, args.artifact_out)
        print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())