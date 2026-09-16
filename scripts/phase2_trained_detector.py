"""Phase 2: a genuine per-player TRAINED detector, replacing the thin
2-feature combiner (score_game's timing_score + maia_score, a single
train/eval half-split) with a richer feature set and real cross-
validation.

Motivation (see PROJECT_LOG.md, "the sign isn't backwards, it's
PLAYER-DEPENDENT" and the "strength" finding): the Maia-surprise signal's
useful direction is genuinely player-specific, and a single global sign
assumption (score_game's "-mean(surprise)") only weakly beats chance on
average (median AUC 0.586) even though a per-player-calibrated read of
the SAME evidence reaches a median of 0.805 (diagnostic, not a trained
model). The gap between those two numbers is exactly what a properly
built per-player classifier should be able to close for real: it needs
(a) more than one summary number per signal — mean alone throws away the
shape of the distribution — and (b) real cross-validation instead of one
train/eval split, which is a noisy estimate at ~30-40 games per class.

Feature set per game (6 features, all UNSIGNED — no hardcoded "which
direction is suspicious" assumption; let the per-player model learn
that, since tonight's finding is that assuming one global sign is the
whole problem):
    resid_mean, resid_std, resid_max   — think-time residual (actual -
                                          predicted, in log-seconds)
    surprise_mean, surprise_std, surprise_max — Maia surprise per move

Usage:
    python -m scripts.phase2_trained_detector --out scratchpad/phase2_dashboard/trained_detector.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import lightgbm as lgb
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.metrics import roc_auc_score

sys.path.insert(0, ".")
from src.model import COMPLEXITY_FEATURES, CLOCK_FEATURES, _time_split, _prep_X  # noqa: E402
from scripts.phase2_cheat_simulation import inject_cheat  # noqa: E402
from scripts.phase2_full_roster import discover_players  # noqa: E402

FEATURES_DIR = Path("data/features")
MODELS_DIR = Path("data/models")
N_GAMES = 40


def game_features(game_df: pd.DataFrame, tt_model, tt_features: list[str]) -> dict:
    """Richer, direction-agnostic per-game summary — see module docstring."""
    X = _prep_X(game_df, tt_features)
    pred_log_tt = tt_model.predict(X)
    actual_log_tt = np.log1p(game_df["think_time"].clip(lower=0)).to_numpy()
    resid = actual_log_tt - pred_log_tt
    surprise = game_df["maia_surprise"].to_numpy()
    return {
        "resid_mean": float(resid.mean()), "resid_std": float(resid.std()), "resid_max": float(np.abs(resid).max()),
        "surprise_mean": float(surprise.mean()), "surprise_std": float(surprise.std()), "surprise_max": float(surprise.max()),
    }


FEATURE_NAMES = ["resid_mean", "resid_std", "resid_max", "surprise_mean", "surprise_std", "surprise_max"]

SEL_FEATURE_NAMES = [
    "resid_mean", "resid_std", "resid_max", "resid_top3_mean",
    "surprise_mean", "surprise_std", "surprise_max", "surprise_top3_mean",
]


def game_features_selective(game_df: pd.DataFrame, tt_model, tt_features: list[str]) -> dict:
    """Adds MAX/top-3 reductions alongside mean/std — for a selective
    cheater (1 tampered move out of 20-60), whole-game mean/std barely
    move, so the extreme-value features are where the actual signal
    lives (same reasoning as score_game_max, extended with more than one
    reduction so a trained model has more than a single number to use)."""
    X = _prep_X(game_df, tt_features)
    pred_log_tt = tt_model.predict(X)
    actual_log_tt = np.log1p(game_df["think_time"].clip(lower=0)).to_numpy()
    resid = actual_log_tt - pred_log_tt
    surprise = game_df["maia_surprise"].to_numpy()
    resid_abs_sorted = np.sort(np.abs(resid))
    surprise_sorted = np.sort(surprise)
    return {
        "resid_mean": float(resid.mean()), "resid_std": float(resid.std()), "resid_max": float(resid_abs_sorted[-1]),
        "resid_top3_mean": float(resid_abs_sorted[-3:].mean() if len(resid_abs_sorted) >= 3 else resid_abs_sorted.mean()),
        "surprise_mean": float(surprise.mean()), "surprise_std": float(surprise.std()), "surprise_max": float(surprise_sorted[-1]),
        "surprise_top3_mean": float(surprise_sorted[-3:].mean() if len(surprise_sorted) >= 3 else surprise_sorted.mean()),
    }


def eval_player_selective(stem: str, n_games: int, seed: int) -> dict | None:
    df = pd.read_parquet(FEATURES_DIR / f"{stem}_features.parquet")
    df = df[~df["is_suspect"]].dropna(subset=["think_time", "player_rating", "maia_prob_best_move"])
    if df.empty:
        return None
    tt_features = [c for c in COMPLEXITY_FEATURES + CLOCK_FEATURES + ["player_rating"] if c in df.columns]

    model_path = MODELS_DIR / f"{stem}_thinktime_live.txt"
    if not model_path.exists():
        model_path = MODELS_DIR / f"{stem}_thinktime_descriptive.txt"
    tt_model = lgb.Booster(model_file=str(model_path))

    _, test = _time_split(df)
    game_ids = test["game_id"].drop_duplicates().to_numpy()
    if len(game_ids) < 16:
        return None
    rng = np.random.default_rng(seed)
    picked = rng.choice(game_ids, size=min(n_games, len(game_ids)), replace=False)

    rows, labels = [], []
    for gid in picked:
        g = test[test["game_id"] == gid].sort_values("ply")
        if len(g) < 10 or g["gap_1_2"].notna().sum() < 1:
            continue
        rows.append(game_features_selective(g, tt_model, tt_features)); labels.append(0)
        inj = inject_cheat(g, 0.0, "smart", rng, tt_model, tt_features, selective_k=1)
        rows.append(game_features_selective(inj, tt_model, tt_features)); labels.append(1)

    if len(rows) < 24:
        return None
    X = np.array([[r[f] for f in SEL_FEATURE_NAMES] for r in rows])
    y = np.array(labels)

    k = 5 if len(rows) >= 50 else 4 if len(rows) >= 32 else 3
    cv = StratifiedKFold(k, shuffle=True, random_state=seed)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        proba = cross_val_predict(LogisticRegression(class_weight="balanced", max_iter=1000), X, y, cv=cv, method="predict_proba")[:, 1]
        auc_trained = float(roc_auc_score(y, proba))
        thin_X = X[:, [2, 6]]  # resid_max, surprise_max — closest analogue to the old score_game_max heuristic
        thin_proba = cross_val_predict(LogisticRegression(class_weight="balanced", max_iter=1000), thin_X, y, cv=cv, method="predict_proba")[:, 1]
        auc_thin = float(roc_auc_score(y, thin_proba))

    return {"player": stem, "n_pairs": len(picked), "auc_trained_rich": auc_trained, "auc_thin_2feat": auc_thin}


def eval_player(stem: str, n_games: int, seed: int) -> dict | None:
    df = pd.read_parquet(FEATURES_DIR / f"{stem}_features.parquet")
    df = df[~df["is_suspect"]].dropna(subset=["think_time", "player_rating", "maia_prob_best_move"])
    if df.empty:
        return None
    tt_features = [c for c in COMPLEXITY_FEATURES + CLOCK_FEATURES + ["player_rating"] if c in df.columns]

    model_path = MODELS_DIR / f"{stem}_thinktime_live.txt"
    if not model_path.exists():
        model_path = MODELS_DIR / f"{stem}_thinktime_descriptive.txt"
    tt_model = lgb.Booster(model_file=str(model_path))

    _, test = _time_split(df)
    game_ids = test["game_id"].drop_duplicates().to_numpy()
    if len(game_ids) < 16:
        return None
    rng = np.random.default_rng(seed)
    picked = rng.choice(game_ids, size=min(n_games, len(game_ids)), replace=False)

    rows, labels = [], []
    for gid in picked:
        g = test[test["game_id"] == gid].sort_values("ply")
        if len(g) < 10:
            continue
        rows.append(game_features(g, tt_model, tt_features)); labels.append(0)
        inj = inject_cheat(g, 1.0, "smart", rng, tt_model, tt_features)
        rows.append(game_features(inj, tt_model, tt_features)); labels.append(1)

    if len(rows) < 24:  # need enough for meaningful k-fold CV
        return None
    X = np.array([[r[f] for f in FEATURE_NAMES] for r in rows])
    y = np.array(labels)

    k = 5 if len(rows) >= 50 else 4 if len(rows) >= 32 else 3
    cv = StratifiedKFold(k, shuffle=True, random_state=seed)
    clf = LogisticRegression(class_weight="balanced", max_iter=1000)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        proba = cross_val_predict(clf, X, y, cv=cv, method="predict_proba")[:, 1]
        auc_trained = float(roc_auc_score(y, proba))

    # Old 2-feature/single-split combiner, computed identically here for
    # an apples-to-apples comparison on the EXACT same games.
    thin_X = X[:, [0, 3]]  # resid_mean, surprise_mean == old timing_score(negated)/maia_score(unsigned)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        thin_proba = cross_val_predict(LogisticRegression(class_weight="balanced", max_iter=1000), thin_X, y, cv=cv, method="predict_proba")[:, 1]
        auc_thin = float(roc_auc_score(y, thin_proba))

    return {"player": stem, "n_pairs": len(picked), "auc_trained_rich": auc_trained, "auc_thin_2feat": auc_thin}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="scratchpad/phase2_dashboard/trained_detector.json")
    ap.add_argument("--n-games", type=int, default=N_GAMES)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--selective", action="store_true", help="evaluate the 1-critical-move scenario instead of full-game replacement")
    args = ap.parse_args()

    players = discover_players()
    if args.limit:
        players = players[: args.limit]
    print(f"Evaluating {len(players)} players ({'selective k=1' if args.selective else 'flat 100%'})...", flush=True)
    eval_fn = eval_player_selective if args.selective else eval_player

    results = []
    t0 = time.time()
    for i, stem in enumerate(players):
        try:
            r = eval_fn(stem, args.n_games, args.seed)
        except Exception as e:  # noqa: BLE001
            print(f"  [{i+1}/{len(players)}] {stem}: SKIPPED ({e})", flush=True)
            continue
        if r is not None:
            results.append(r)
        if (i + 1) % 30 == 0:
            print(f"  [{i+1}/{len(players)}] ... {time.time()-t0:.0f}s elapsed", flush=True)

    rich = [r["auc_trained_rich"] for r in results]
    thin = [r["auc_thin_2feat"] for r in results]
    print(f"\nn={len(results)} players")
    print(f"rich 6-feature CV'd model:  median={np.median(rich):.3f}  mean={np.mean(rich):.3f}")
    print(f"thin 2-feature CV'd model:  median={np.median(thin):.3f}  mean={np.mean(thin):.3f}")
    print(f"rich beats thin on {sum(1 for r,t in zip(rich,thin) if r>t)}/{len(results)} players")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump({"players": results, "feature_names": FEATURE_NAMES}, f)
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
