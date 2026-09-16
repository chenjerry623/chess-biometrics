"""Phase 2: a smarter adversary that plays an ALTERNATE engine-approved
move (rank 2-4 of Stockfish's MultiPV, not always the literal #1 best)
on tampered plies, instead of always the objectively-best move — a real
evasion a sophisticated cheater might use specifically to dodge a
"always finds the objectively-best move" tell.

Everything else about the injection is identical to the existing "smart"
adversary (see phase2_cheat_simulation.py): timing is sampled from the
player's own fitted think-time model. The only change is WHICH move gets
played on a tampered ply. Needs live Maia inference (not just the
precomputed maia_prob_best_move column, which is specific to rank-1)
since we need Maia's probability for whichever alternate move got
picked — src/maia_query.py, reusing one engine per player since Maia
weight loading is the expensive part, not per-position queries (~2ms
once warm).

Tests both k=1 and k=3 critical-move selective scenarios, comparing
detection AUC (same rich 8-feature + 5-fold-CV approach as
phase2_trained_detector.py) between the best-move-only adversary and
this alternate-move one, on the same games, so the comparison is exact.

Usage:
    python -m scripts.phase2_altmove_adversary --limit 20
"""
from __future__ import annotations

import argparse
import math
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
from src.maia_query import open_maia_engine, query_maia_policy, nearest_rating_bin  # noqa: E402
from scripts.phase2_cheat_simulation import inject_cheat  # noqa: E402
from scripts.phase2_full_roster import discover_players  # noqa: E402
from scripts.phase2_trained_detector import game_features_selective, SEL_FEATURE_NAMES  # noqa: E402

FEATURES_DIR = Path("data/features")
MODELS_DIR = Path("data/models")


def inject_altmove(game_df: pd.DataFrame, k: int, rng: np.random.Generator,
                    tt_model, tt_features: list[str], maia_engine, board_cache: dict) -> pd.DataFrame:
    """Same as inject_cheat(..., mode='smart', selective_k=k) except the
    injected move is sampled from MultiPV ranks 2-4 (falls back to
    best_move if no alternative exists at that position), and
    maia_surprise is computed via a LIVE Maia query for that specific
    alternate move rather than the precomputed best-move-only column."""
    import chess

    df = game_df.copy()
    n = len(df)
    df["is_injected"] = False
    if n == 0:
        return df
    gaps = df["gap_1_2"]
    candidates = gaps.dropna().sort_values().index
    idx = pd.Index(candidates[:k])
    if len(idx) == 0:
        return df
    df.loc[idx, "is_injected"] = True

    for i in idx:
        top_moves = df.loc[i, "top_moves"]
        alt_pool = list(top_moves[1:]) if top_moves is not None and len(top_moves) > 1 else list(top_moves) if top_moves is not None else []
        if not alt_pool:
            continue
        alt_uci = rng.choice(alt_pool)
        fen = df.loc[i, "fen_before"]
        if fen not in board_cache:
            board_cache[fen] = query_maia_policy(maia_engine, chess.Board(fen))
        probs = board_cache[fen]
        p = probs.get(alt_uci, 1e-6)
        df.loc[i, "maia_prob_played"] = p
        df.loc[i, "maia_surprise"] = -math.log(max(p, 1e-6))
        df.loc[i, "played_best_move"] = False

    X = _prep_X(df.loc[idx], tt_features)
    pred_log_tt = tt_model.predict(X)
    noise = rng.normal(0, 0.15, size=len(idx))
    df.loc[idx, "think_time"] = np.expm1(pred_log_tt + noise).clip(min=0.1)
    return df


def eval_player(stem: str, n_games: int, k: int, seed: int) -> dict | None:
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

    rating_bin = nearest_rating_bin(float(test["player_rating"].median()))
    engine = open_maia_engine(rating_bin)
    board_cache: dict = {}

    rows_best, rows_alt, labels = [], [], []
    try:
        for gid in picked:
            g = test[test["game_id"] == gid].sort_values("ply")
            if len(g) < 10 or g["gap_1_2"].notna().sum() < k:
                continue
            clean_feat = game_features_selective(g, tt_model, tt_features)
            rows_best.append(clean_feat); rows_alt.append(clean_feat); labels.append(0)

            inj_best = inject_cheat(g, 0.0, "smart", rng, tt_model, tt_features, selective_k=k)
            rows_best.append(game_features_selective(inj_best, tt_model, tt_features)); labels.append(1)

            inj_alt = inject_altmove(g, k, rng, tt_model, tt_features, engine, board_cache)
            rows_alt.append(game_features_selective(inj_alt, tt_model, tt_features))
    finally:
        engine.quit()

    if len(rows_best) < 24:
        return None
    y = np.array(labels)

    def cv_auc(rows):
        X = np.array([[r[f] for f in SEL_FEATURE_NAMES] for r in rows])
        kf = 5 if len(rows) >= 50 else 4 if len(rows) >= 32 else 3
        cv = StratifiedKFold(kf, shuffle=True, random_state=seed)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            proba = cross_val_predict(LogisticRegression(class_weight="balanced", max_iter=1000), X, y, cv=cv, method="predict_proba")[:, 1]
            return float(roc_auc_score(y, proba))

    return {"player": stem, "n_pairs": len(picked), "k": k,
            "auc_best_move": cv_auc(rows_best), "auc_alt_move": cv_auc(rows_alt)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n-games", type=int, default=40)
    ap.add_argument("--k", type=int, default=1)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--limit", type=int, default=20)
    args = ap.parse_args()

    players = discover_players()
    if args.limit:
        players = players[: args.limit]
    print(f"Evaluating {len(players)} players, k={args.k} critical move(s), best-move vs. alt-move adversary...", flush=True)

    results = []
    t0 = time.time()
    for i, stem in enumerate(players):
        try:
            r = eval_player(stem, args.n_games, args.k, args.seed)
        except Exception as e:  # noqa: BLE001
            print(f"  [{i+1}/{len(players)}] {stem}: SKIPPED ({e})", flush=True)
            continue
        if r is not None:
            results.append(r)
            print(f"  [{i+1}/{len(players)}] {stem}: best={r['auc_best_move']:.3f} alt={r['auc_alt_move']:.3f}  ({time.time()-t0:.0f}s)", flush=True)

    best = [r["auc_best_move"] for r in results]
    alt = [r["auc_alt_move"] for r in results]
    print(f"\nn={len(results)} players, k={args.k}")
    print(f"best-move adversary: median={np.median(best):.3f}")
    print(f"alt-move adversary:  median={np.median(alt):.3f}")
    print(f"alt-move harder to detect (lower AUC) on {sum(1 for b,a in zip(best,alt) if a<b)}/{len(results)} players")


if __name__ == "__main__":
    main()
