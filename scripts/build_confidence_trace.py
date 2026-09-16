"""Builds a concrete, checkable trace of the multi-game score-fusion claim
(see export_dashboard_data.py's _multi_game_curve) for ONE real held-out
player: as their games are added one at a time, how does the model's
confidence in the correct answer move?

This exists because the dashboard's multi-game accuracy curve is an
aggregate statistic — real and honestly measured, but abstract. A
skeptical viewer can't check "89% balanced accuracy at 20 games" against
anything concrete. This script picks one real player with a lot of
held-out games and threads through the EXACT same score-fusion method
(plain average of per-class probability vectors, same as
_multi_game_curve with weighted=False) so the resulting trace is a real
instance of the claim, not a new one invented for the page — every game
in it links out to the real Chess.com game (see the dashboard's existing
"View this exact game" links).

Only fits the ONE model that actually wins on pool_biometric (LightGBM,
confirmed in the live export) rather than the full LR/HGB/LightGBM
three-way comparison export_dashboard_data.py runs every time — this
script only needs proba, not "which model is best," so skipping the
other two candidates is a real time saving, not a shortcut on rigor.

Usage:
    python -m scripts.build_confidence_trace --out scratchpad/confidence_trace.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, ".")
from scripts.export_dashboard_data import (  # noqa: E402
    FEATURES_DIR, IDENTIFICATION_COHORTS, IDENT_FEATURES, MOVE_CONTENT_FEATURES, _per_game_features, _f,
)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="scratchpad/confidence_trace.json")
    ap.add_argument("--min-games", type=int, default=25, help="only consider players with at least this many held-out games")
    args = ap.parse_args()

    import lightgbm as lgb
    from sklearn.model_selection import StratifiedKFold, cross_val_predict
    from sklearn.model_selection import cross_val_score  # noqa: F401  (not used, kept out of guesswork)

    player_stems = IDENTIFICATION_COHORTS["pool"]

    frames = []
    for player, stem in player_stems.items():
        df = pd.read_parquet(FEATURES_DIR / f"{stem}_features.parquet")
        df = df[~df["is_suspect"]].dropna(subset=["think_time"])
        df = df.dropna(subset=IDENT_FEATURES)
        df["player"] = player
        frames.append(df)
    all_df = pd.concat(frames, ignore_index=True)

    # game_date per (player, game_id) — needed for chronological ordering,
    # and NOT kept by _per_game_features (which only aggregates ply-level
    # stats), so grab it here before building the per-game feature table.
    game_dates = all_df.groupby(["player", "game_id"])["game_date"].first().reset_index()

    per_game = _per_game_features(all_df)
    feature_cols = [c for c in per_game.columns if c not in ("player", "game_id") and c not in MOVE_CONTENT_FEATURES]
    n_plies = all_df.groupby(["player", "game_id"]).size().rename("n_plies")
    del all_df, frames

    per_game = per_game.merge(n_plies, on=["player", "game_id"])
    per_game = per_game.merge(game_dates, on=["player", "game_id"])
    per_game = per_game[per_game["n_plies"] >= 15].reset_index(drop=True)

    X_raw = per_game[feature_cols].to_numpy(dtype=float)
    y = per_game["player"].to_numpy()
    unique_players = sorted(set(y))

    cv = StratifiedKFold(5, shuffle=True, random_state=0)
    class_freq = pd.Series(y).value_counts(normalize=True)
    sample_weight = pd.Series(y).map(lambda p: 1.0 / class_freq[p]).to_numpy()

    print(f"Fitting LightGBM via 5-fold CV on {len(per_game)} games, {len(unique_players)} players...", flush=True)
    lgbm = lgb.LGBMClassifier(random_state=0, max_depth=4, n_estimators=100, verbosity=-1)
    proba = cross_val_predict(lgbm, X_raw, y, cv=cv, method="predict_proba", params={"sample_weight": sample_weight})
    classes_order = sorted(unique_players)
    class_index = {p: i for i, p in enumerate(classes_order)}
    print("Done.", flush=True)

    # Pick a player with plenty of held-out games AND a real single-game
    # accuracy that's genuinely mediocre-to-poor (not already trivially
    # easy) — the honest version of this demo is one where a single game
    # is a real coin flip and averaging is what actually earns the win,
    # not a player the model would nail every time anyway.
    counts = per_game.groupby("player").size()
    candidates = counts[counts >= args.min_games].index.tolist()
    if not candidates:
        candidates = counts.nlargest(5).index.tolist()

    best_player, best_score = None, None
    for p in candidates:
        idx = per_game.index[per_game["player"] == p].to_numpy()
        correct_idx = class_index[p]
        single_game_acc = float((proba[idx].argmax(axis=1) == correct_idx).mean())
        # Prefer players whose single-game accuracy sits in a realistic,
        # not-already-easy band — informative rather than a foregone
        # conclusion either way.
        score = -abs(single_game_acc - 0.25)
        if best_score is None or score > best_score:
            best_player, best_score = p, score

    p = best_player
    idx = per_game.index[per_game["player"] == p].to_numpy()
    sub = per_game.loc[idx].copy()
    sub["proba_row"] = list(proba[idx])
    sub = sub.sort_values("game_date")

    running = np.zeros(len(classes_order))
    trace = []
    correct_idx = class_index[p]
    for i, (_, row) in enumerate(sub.iterrows(), start=1):
        running = running + (row["proba_row"] - running) / i  # running mean
        top_idx = int(np.argmax(running))
        trace.append({
            "n_games": i,
            "game_id": str(row["game_id"]),
            "game_date": str(row["game_date"]),
            "running_prob_correct": _f(float(running[correct_idx])),
            "top_guess": classes_order[top_idx],
            "top_guess_prob": _f(float(running[top_idx])),
            "correct_so_far": bool(top_idx == correct_idx),
        })

    out = {"player": p, "n_games": len(trace), "trace": trace}
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(out, f)
    print(f"\nWrote {out_path} — player={p}, {len(trace)} games, "
          f"single-game acc={trace[0]['correct_so_far']}, final correct={trace[-1]['correct_so_far']}, "
          f"final prob={trace[-1]['running_prob_correct']:.3f}")


if __name__ == "__main__":
    main()
