"""Phase 2, multi-game aggregation: does looking across several games from
the same suspect (rather than judging one game in isolation) recover any
of the signal that's near-chance single-game?

This directly mirrors how real move-quality anti-cheat systems actually
operate (Ken Regan's Intrinsic Performance Rating, Chess.com's Fair Play
review) — they essentially never call it from one game. They compare a
player's engine-agreement rate against a rating-calibrated reference
distribution accumulated over MANY games (100+ moves is a typical
practical minimum). The identification dashboard already has this kind
of multi-game curve (1/5/20 games); Phase 2's cheat-detection scoring
never had the equivalent until this script — every number on the Signal
Detection Lab page was still single-game.

Only the two hardest, most policy-relevant scenarios are covered here
(not the full rate/k grid phase2_full_roster.py already covers):
  - flat, smart timing, 100% of plies replaced — the ceiling case for a
    consistent, fully-disguised cheater.
  - selective, smart timing, k=1 critical move — CLAUDE.md's actual
    hardest realistic case (a selective cheater on just the most
    important moment), scored by that one move's suspicion.

For each player, bootstrap-resamples groups of N of that SAME player's
held-out games (with replacement) to build many simulated "N-game
suspects" — clean ones (all N games genuinely clean) and cheating ones
(all N games run through the same injection) — and reports the AUC
separating them, exactly the same design as run_selective/run's
single-game AUC, just with the evidence averaged over N games first.

Usage:
    python -m scripts.phase2_multigame --out scratchpad/phase2_dashboard/multigame.json
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
from sklearn.metrics import roc_auc_score

sys.path.insert(0, ".")
from src.model import COMPLEXITY_FEATURES, CLOCK_FEATURES, _time_split  # noqa: E402
from scripts.phase2_cheat_simulation import inject_cheat, score_game, score_game_max  # noqa: E402
from scripts.phase2_full_roster import discover_players  # noqa: E402

FEATURES_DIR = Path("data/features")
MODELS_DIR = Path("data/models")
N_VALUES = (1, 3, 5, 10, 20)
B = 400  # bootstrap resamples per player per N


def per_player_raw_scores(stem: str, n_games: int, seed: int):
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
    if len(game_ids) < 10:
        return None
    rng = np.random.default_rng(seed)
    picked = rng.choice(game_ids, size=min(n_games, len(game_ids)), replace=False)

    flat_clean, flat_smart, sel_clean, sel_smart = [], [], [], []
    for gid in picked:
        g = test[test["game_id"] == gid].sort_values("ply")
        if len(g) < 10:
            continue
        flat_clean.append(score_game(g, tt_model, tt_features)["combined"])
        inj = inject_cheat(g, 1.0, "smart", rng, tt_model, tt_features)
        flat_smart.append(score_game(inj, tt_model, tt_features)["combined"])
        if g["gap_1_2"].notna().sum() >= 1:
            sel_clean.append(score_game_max(g, tt_model, tt_features)["timing_max"])
            inj1 = inject_cheat(g, 0.0, "smart", rng, tt_model, tt_features, selective_k=1)
            sel_smart.append(score_game_max(inj1, tt_model, tt_features)["timing_max"])

    if len(flat_clean) < 10:
        return None
    return {
        "player": stem,
        "flat_clean": np.array(flat_clean), "flat_smart": np.array(flat_smart),
        "sel_clean": np.array(sel_clean), "sel_smart": np.array(sel_smart),
    }


def bootstrap_auc(clean: np.ndarray, cheat: np.ndarray, n: int, b: int, rng: np.random.Generator) -> float | None:
    if len(clean) < n or len(cheat) < n:
        return None
    clean_evidence = np.array([rng.choice(clean, size=n, replace=True).mean() for _ in range(b)])
    cheat_evidence = np.array([rng.choice(cheat, size=n, replace=True).mean() for _ in range(b)])
    y = np.concatenate([np.zeros(b), np.ones(b)])
    scores = np.concatenate([clean_evidence, cheat_evidence])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return float(roc_auc_score(y, scores))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="scratchpad/phase2_dashboard/multigame.json")
    ap.add_argument("--n-games", type=int, default=40, help="held-out games sampled per player before bootstrapping")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    players = discover_players()
    print(f"Scoring {len(players)} players (raw per-game scores, 2 scenarios each)...", flush=True)

    per_player = []
    t0 = time.time()
    for i, stem in enumerate(players):
        try:
            r = per_player_raw_scores(stem, args.n_games, args.seed)
        except Exception as e:  # noqa: BLE001
            print(f"  [{i+1}/{len(players)}] {stem}: SKIPPED ({e})", flush=True)
            continue
        if r is not None:
            per_player.append(r)
        if (i + 1) % 20 == 0:
            print(f"  [{i+1}/{len(players)}] ... {time.time()-t0:.0f}s elapsed", flush=True)
    print(f"Scored {len(per_player)} players in {time.time()-t0:.0f}s. Bootstrapping multi-game curves...", flush=True)

    flat_curve, sel_curve = [], []
    rng = np.random.default_rng(args.seed + 100)
    for n in N_VALUES:
        flat_aucs, sel_aucs = [], []
        for p in per_player:
            a = bootstrap_auc(p["flat_clean"], p["flat_smart"], n, B, rng)
            if a is not None:
                flat_aucs.append(a)
            a2 = bootstrap_auc(p["sel_clean"], p["sel_smart"], n, B, rng)
            if a2 is not None:
                sel_aucs.append(a2)
        flat_curve.append({
            "n_games": n, "n_players": len(flat_aucs),
            "median": float(np.median(flat_aucs)) if flat_aucs else None,
            "q25": float(np.percentile(flat_aucs, 25)) if flat_aucs else None,
            "q75": float(np.percentile(flat_aucs, 75)) if flat_aucs else None,
        })
        sel_curve.append({
            "n_games": n, "n_players": len(sel_aucs),
            "median": float(np.median(sel_aucs)) if sel_aucs else None,
            "q25": float(np.percentile(sel_aucs, 25)) if sel_aucs else None,
            "q75": float(np.percentile(sel_aucs, 75)) if sel_aucs else None,
        })
        print(f"  n_games={n}: flat smart median AUC={flat_curve[-1]['median']:.3f} (n={flat_curve[-1]['n_players']})"
              f" | selective k=1 smart median AUC={sel_curve[-1]['median']:.3f} (n={sel_curve[-1]['n_players']})", flush=True)

    out = {
        "flat_smart_100pct": flat_curve,
        "selective_smart_k1": sel_curve,
        "n_players_scored": len(per_player),
        "bootstrap_resamples": B,
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(out, f)
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
