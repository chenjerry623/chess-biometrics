"""How well can we tell any two players apart, not just one hand-picked
pair? The dashboard's "primary" cohort was always cdznjerry vs.
BIG_TONKA_T specifically — a real result, but one pair, chosen for
narrative reasons (same person, two accounts — see PROJECT_LOG.md). That
tells you the ceiling for a pair picked to be interesting, not what a
random pair of players looks like.

Samples many random pairs from the full pool and fits the same 2-class
identification model (LR/HGB/LightGBM, cross-validated, best wins — same
methodology as build_identification) on each, reporting the DISTRIBUTION
of pairwise balanced accuracy, not a single number.

Usage:
    python -m scripts.pairwise_identification --n-pairs 40 --out scratchpad/pairwise.json
"""
from __future__ import annotations

import argparse
import json
import random
import time
import warnings
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd

import sys
sys.path.insert(0, ".")
from scripts.export_dashboard_data import (  # noqa: E402
    FEATURES_DIR, IDENTIFICATION_COHORTS, IDENT_FEATURES, MOVE_CONTENT_FEATURES, _per_game_features, _f,
)


def eval_pair(a: str, b: str, stems: dict[str, str], feature_mode: str, seed: int) -> dict | None:
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import StratifiedKFold, cross_val_predict
    import lightgbm as lgb

    frames = []
    for player in (a, b):
        df = pd.read_parquet(FEATURES_DIR / f"{stems[player]}_features.parquet")
        df = df[~df["is_suspect"]].dropna(subset=["think_time"])
        df = df.dropna(subset=IDENT_FEATURES)
        df["player"] = player
        frames.append(df)
    all_df = pd.concat(frames, ignore_index=True)

    per_game = _per_game_features(all_df)
    feature_cols = [c for c in per_game.columns if c not in ("player", "game_id")]
    if feature_mode == "biometric":
        feature_cols = [c for c in feature_cols if c not in MOVE_CONTENT_FEATURES]
    n_plies = all_df.groupby(["player", "game_id"]).size().rename("n_plies")
    per_game = per_game.merge(n_plies, on=["player", "game_id"])
    per_game = per_game[per_game["n_plies"] >= 15].reset_index(drop=True)
    if per_game["player"].nunique() < 2 or per_game.groupby("player").size().min() < 10:
        return None

    X_raw = per_game[feature_cols].to_numpy(dtype=float)
    y = per_game["player"].to_numpy()
    unique_players = sorted(set(y))
    min_class = per_game.groupby("player").size().min()
    k = min(5, min_class)
    if k < 2:
        return None
    cv = StratifiedKFold(k, shuffle=True, random_state=seed)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        lr = LogisticRegression(class_weight="balanced", max_iter=1000)
        from sklearn.preprocessing import StandardScaler
        X = StandardScaler().fit_transform(X_raw)
        lr_pred = cross_val_predict(lr, X, y, cv=cv)
        lr_bal = float(np.mean([(lr_pred[y == p] == y[y == p]).mean() for p in unique_players]))

        hgb = HistGradientBoostingClassifier(random_state=0, max_depth=4)
        hgb_pred = cross_val_predict(hgb, X_raw, y, cv=cv)
        hgb_bal = float(np.mean([(hgb_pred[y == p] == y[y == p]).mean() for p in unique_players]))

        lgbm = lgb.LGBMClassifier(random_state=0, max_depth=4, n_estimators=100, verbosity=-1)
        lgbm_pred = cross_val_predict(lgbm, X_raw, y, cv=cv)
        lgbm_bal = float(np.mean([(lgbm_pred[y == p] == y[y == p]).mean() for p in unique_players]))

    best_bal = max(lr_bal, hgb_bal, lgbm_bal)
    return {
        "pair": [a, b], "n_games": len(per_game),
        "balanced_accuracy": _f(best_bal),
        "lr": _f(lr_bal), "hgb": _f(hgb_bal), "lightgbm": _f(lgbm_bal),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n-pairs", type=int, default=40)
    ap.add_argument("--feature-mode", default="biometric", choices=["biometric", "full"])
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default="scratchpad/pairwise.json")
    args = ap.parse_args()

    stems = IDENTIFICATION_COHORTS["pool"]
    players = list(stems.keys())
    rng = random.Random(args.seed)
    all_pairs = list(combinations(players, 2))
    rng.shuffle(all_pairs)

    results = []
    t0 = time.time()
    i = 0
    while len(results) < args.n_pairs and i < len(all_pairs):
        a, b = all_pairs[i]
        i += 1
        try:
            r = eval_pair(a, b, stems, args.feature_mode, args.seed)
        except Exception as e:  # noqa: BLE001
            print(f"  {a} vs {b}: SKIPPED ({e})", flush=True)
            continue
        if r is not None:
            results.append(r)
            print(f"  [{len(results)}/{args.n_pairs}] {a} vs {b}: {r['balanced_accuracy']:.3f} ({time.time()-t0:.0f}s)", flush=True)

    accs = [r["balanced_accuracy"] for r in results]
    print(f"\nn={len(results)} pairs")
    print(f"median={np.median(accs):.3f} min={min(accs):.3f} max={max(accs):.3f} mean={np.mean(accs):.3f}")

    out = {
        "feature_mode": args.feature_mode,
        "n_pairs": len(results),
        "median": _f(np.median(accs)), "min": _f(min(accs)), "max": _f(max(accs)), "mean": _f(np.mean(accs)),
        "q25": _f(np.percentile(accs, 25)), "q75": _f(np.percentile(accs, 75)),
        "pairs": sorted(results, key=lambda r: -r["balanced_accuracy"]),
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(out, f)
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
