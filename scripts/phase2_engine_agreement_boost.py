"""Phase 2: does biometric (timing + Maia) evidence boost a REAL
engine-agreement anti-cheat baseline, rather than standing alone?

This is the "difficulty-conditioned move quality" signal CLAUDE.md
scoped from the start and this project never built — not because it
was skipped, but because `cp_loss` (the obvious candidate) turned out
to measure cross-search instability, not move quality (see
PROJECT_LOG.md's cp_loss investigation). `played_best_move` and
`n_moves_within_30cp` are different, already-valid columns from the
SAME Stockfish MultiPV annotation — completely unaffected by that
issue — and give a real, standard-anti-cheat-style signal for free: how
often does this player match the engine's top choice, restricted to
positions where more than one move was genuinely competitive (matching
in a forced-ish position is meaningless — confirmed on real data:
cdznjerry's natural match rate is 24.8% on informative positions vs.
47.6% on near-forced ones).

Three feature groups tested:
  agreement-only  — match_rate_all, match_rate_informative, a length
                     control (n_informative) — the standalone baseline,
                     analogous to what real tools (Regan's IPR, etc.)
                     use as their primary signal.
  biometric-only   — the existing 6 (flat) or 8 (selective) direction-
                     agnostic timing/Maia features from
                     phase2_trained_detector.py.
  combined         — both together, to see whether biometrics ADD
                     value on top of the baseline rather than replacing
                     it.

Usage:
    python -m scripts.phase2_engine_agreement_boost --selective
"""
from __future__ import annotations

import argparse
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
from scripts.phase2_trained_detector import game_features, game_features_selective, FEATURE_NAMES, SEL_FEATURE_NAMES  # noqa: E402

FEATURES_DIR = Path("data/features")
MODELS_DIR = Path("data/models")

AGREEMENT_NAMES = ["match_rate_all", "match_rate_informative", "n_informative"]


def agreement_features(game_df: pd.DataFrame) -> dict:
    informative = game_df[game_df["n_moves_within_30cp"] >= 2]
    return {
        "match_rate_all": float(game_df["played_best_move"].mean()),
        "match_rate_informative": float(informative["played_best_move"].mean()) if len(informative) else float(game_df["played_best_move"].mean()),
        "n_informative": float(len(informative)),
    }


def eval_player(stem: str, n_games: int, seed: int, selective: bool) -> dict | None:
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

    bio_fn = game_features_selective if selective else game_features
    bio_names = SEL_FEATURE_NAMES if selective else FEATURE_NAMES

    agree_rows, bio_rows, labels = [], [], []
    for gid in picked:
        g = test[test["game_id"] == gid].sort_values("ply")
        if len(g) < 10:
            continue
        if selective and g["gap_1_2"].notna().sum() < 1:
            continue
        agree_rows.append(agreement_features(g)); bio_rows.append(bio_fn(g, tt_model, tt_features)); labels.append(0)
        if selective:
            inj = inject_cheat(g, 0.0, "smart", rng, tt_model, tt_features, selective_k=1)
        else:
            inj = inject_cheat(g, 1.0, "smart", rng, tt_model, tt_features)
        agree_rows.append(agreement_features(inj)); bio_rows.append(bio_fn(inj, tt_model, tt_features)); labels.append(1)

    if len(agree_rows) < 24:
        return None
    y = np.array(labels)
    A = np.array([[r[f] for f in AGREEMENT_NAMES] for r in agree_rows])
    B = np.array([[r[f] for f in bio_names] for r in bio_rows])
    C = np.hstack([A, B])

    kf = 5 if len(agree_rows) >= 50 else 4 if len(agree_rows) >= 32 else 3
    cv = StratifiedKFold(kf, shuffle=True, random_state=seed)

    def cv_proba(X):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return cross_val_predict(LogisticRegression(class_weight="balanced", max_iter=1000), X, y, cv=cv, method="predict_proba")[:, 1]

    def auc(proba):
        return float(roc_auc_score(y, proba))

    agree_proba, bio_proba = cv_proba(A), cv_proba(B)
    raw_combined_proba = cv_proba(C)
    # Fair, low-dimensional combination: stack the two SEPARATELY
    # cross-validated models' own out-of-fold probabilities as just 2
    # meta-features into a small final LR, instead of dumping all 11
    # raw features into one classifier — at only ~60-80 samples per
    # player, throwing 8 extra biometric dimensions at a model already
    # asked to learn from a 3-feature baseline is likely to overfit
    # regardless of whether those dimensions carry real signal. This is
    # the standard stacking-ensemble pattern, and a much fairer test of
    # "does biometric evidence add anything" than raw feature
    # concatenation.
    stack_X = np.column_stack([agree_proba, bio_proba])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        stacked_proba = cross_val_predict(LogisticRegression(max_iter=1000), stack_X, y, cv=cv, method="predict_proba")[:, 1]

    return {"player": stem, "n_pairs": len(picked),
            "auc_agreement_only": auc(agree_proba), "auc_biometric_only": auc(bio_proba),
            "auc_combined_raw": auc(raw_combined_proba), "auc_combined_stacked": auc(stacked_proba)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n-games", type=int, default=40)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--selective", action="store_true")
    args = ap.parse_args()

    players = discover_players()
    if args.limit:
        players = players[: args.limit]
    print(f"Evaluating {len(players)} players ({'selective k=1' if args.selective else 'flat 100%'}), agreement vs biometric vs combined...", flush=True)

    results = []
    t0 = time.time()
    for i, stem in enumerate(players):
        try:
            r = eval_player(stem, args.n_games, args.seed, args.selective)
        except Exception as e:  # noqa: BLE001
            print(f"  [{i+1}/{len(players)}] {stem}: SKIPPED ({e})", flush=True)
            continue
        if r is not None:
            results.append(r)
        if (i + 1) % 30 == 0:
            print(f"  [{i+1}/{len(players)}] ... {time.time()-t0:.0f}s elapsed", flush=True)

    agree = [r["auc_agreement_only"] for r in results]
    bio = [r["auc_biometric_only"] for r in results]
    raw = [r["auc_combined_raw"] for r in results]
    stacked = [r["auc_combined_stacked"] for r in results]
    print(f"\nn={len(results)} players")
    print(f"agreement-only baseline:      median={np.median(agree):.3f}")
    print(f"biometric-only:               median={np.median(bio):.3f}")
    print(f"combined (raw concat):        median={np.median(raw):.3f}")
    print(f"combined (stacked 2-feature): median={np.median(stacked):.3f}")
    print(f"stacked beats agreement-only baseline on {sum(1 for c,a in zip(stacked,agree) if c>a)}/{len(results)} players")
    print(f"stacked beats raw concat on {sum(1 for s,r_ in zip(stacked,raw) if s>r_)}/{len(results)} players")


if __name__ == "__main__":
    main()
