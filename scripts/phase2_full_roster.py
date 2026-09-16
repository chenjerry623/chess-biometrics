"""Phase 2, full-roster evaluation: runs the same injected/simulated
cheat-detection scenarios as phase2_cheat_simulation.py, but across every
player in data/models/ with a trained think-time model instead of 2-3
hand-picked ones — closing the "not yet tested at scale" gap noted in
PROJECT_LOG.md.

Produces one aggregate JSON (per-player AUCs, pooled distributions, a
trained-combiner summary) meant to feed a SEPARATE cheat-detection
dashboard, never the identification dashboard. Still injected/simulated
ground truth only — never real accused players, per CLAUDE.md.

Usage:
    python -m scripts.phase2_full_roster --out scratchpad/phase2_dashboard/data.json
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
from sklearn.metrics import roc_auc_score

sys.path.insert(0, ".")
from src.model import COMPLEXITY_FEATURES, CLOCK_FEATURES, _prep_X, _time_split  # noqa: E402
from scripts.phase2_cheat_simulation import (  # noqa: E402
    inject_cheat,
    score_game,
    score_game_max,
)

FEATURES_DIR = Path("data/features")
MODELS_DIR = Path("data/models")

FLAT_RATES = (0.1, 0.25, 0.5, 1.0)
SELECTIVE_KS = (1, 2, 3)


def discover_players() -> list[str]:
    stems = []
    for f in sorted(FEATURES_DIR.glob("*_features.parquet")):
        stem = f.stem.removesuffix("_features")
        model_path = MODELS_DIR / f"{stem}_thinktime_live.txt"
        if not model_path.exists():
            model_path = MODELS_DIR / f"{stem}_thinktime_descriptive.txt"
        if model_path.exists():
            stems.append(stem)
    return stems


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
    if len(game_ids) < 8:
        return None
    rng = np.random.default_rng(seed)
    picked = rng.choice(game_ids, size=min(n_games, len(game_ids)), replace=False)

    flat_rows, sel_rows = [], []
    for gid in picked:
        g = test[test["game_id"] == gid].sort_values("ply")
        if len(g) < 10:
            continue
        flat_rows.append({"game_id": gid, "rate": 0.0, "mode": "clean", **score_game(g, tt_model, tt_features)})
        for mode in ("naive", "smart"):
            for rate in FLAT_RATES:
                inj = inject_cheat(g, rate, mode, rng, tt_model, tt_features)
                flat_rows.append({"game_id": gid, "rate": rate, "mode": mode, **score_game(inj, tt_model, tt_features)})
        if g["gap_1_2"].notna().sum() >= max(SELECTIVE_KS):
            sel_rows.append({"game_id": gid, "k": 0, "mode": "clean", **score_game_max(g, tt_model, tt_features)})
            for mode in ("naive", "smart"):
                for k in SELECTIVE_KS:
                    inj = inject_cheat(g, 0.0, mode, rng, tt_model, tt_features, selective_k=k)
                    sel_rows.append({"game_id": gid, "k": k, "mode": mode, **score_game_max(inj, tt_model, tt_features)})

    if len(flat_rows) < 10:
        return None
    flat = pd.DataFrame(flat_rows)
    sel = pd.DataFrame(sel_rows) if sel_rows else pd.DataFrame()

    result = {"player": stem, "n_games": len(picked), "flat": {}, "selective": {}}

    for mode in ("naive", "smart"):
        result["flat"][mode] = {}
        for rate in FLAT_RATES:
            sub = flat[(flat["mode"] == "clean") | ((flat["mode"] == mode) & (flat["rate"] == rate))]
            y = (sub["mode"] == mode).astype(int)
            if y.nunique() < 2:
                continue
            row = {}
            for col in ("timing_score", "maia_score", "combined"):
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    row[col] = float(roc_auc_score(y, sub[col]))
            result["flat"][mode][str(rate)] = row

    if not sel.empty:
        for mode in ("naive", "smart"):
            result["selective"][mode] = {}
            for k in SELECTIVE_KS:
                sub = sel[(sel["mode"] == "clean") | ((sel["mode"] == mode) & (sel["k"] == k))]
                y = (sub["mode"] == mode).astype(int)
                if y.nunique() < 2:
                    continue
                row = {}
                for col in ("timing_max", "timing_top3_mean", "maia_max", "maia_top3_mean"):
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore")
                        row[col] = float(roc_auc_score(y, sub[col]))
                result["selective"][mode][str(k)] = row

    # Per-player trained combiner at the hardest single scenario worth
    # reporting per-player (smart, rate=100% — every ply replaced, the
    # ceiling case for how well timing+maia generalizes for THIS player).
    sub = flat[(flat["mode"] == "clean") | ((flat["mode"] == "smart") & (flat["rate"] == 1.0))]
    y = (sub["mode"] == "smart").astype(int)
    combiner = None
    if y.nunique() == 2 and len(sub) >= 12:
        ids = np.array(sub["game_id"].unique().tolist())
        rng2 = np.random.default_rng(seed + 1)
        rng2.shuffle(ids)
        half = len(ids) // 2
        train_ids, eval_ids = set(ids[:half]), set(ids[half:])
        train_mask, eval_mask = sub["game_id"].isin(train_ids), sub["game_id"].isin(eval_ids)
        if y[train_mask].nunique() == 2 and y[eval_mask].nunique() == 2:
            clf = LogisticRegression()
            clf.fit(sub.loc[train_mask, ["timing_score", "maia_score"]], y[train_mask])
            pred = clf.predict_proba(sub.loc[eval_mask, ["timing_score", "maia_score"]])[:, 1]
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                trained_auc = float(roc_auc_score(y[eval_mask], pred))
                naive_auc = float(roc_auc_score(y[eval_mask], sub.loc[eval_mask, "combined"]))
            combiner = {
                "trained_auc": trained_auc,
                "naive_auc": naive_auc,
                "weights": {"timing": float(clf.coef_[0][0]), "maia": float(clf.coef_[0][1])},
            }
    result["combiner_smart_100pct"] = combiner
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="scratchpad/phase2_dashboard/data.json")
    parser.add_argument("--n-games", type=int, default=20)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--limit", type=int, default=None, help="cap number of players (for a fast test run)")
    args = parser.parse_args()

    players = discover_players()
    if args.limit:
        players = players[: args.limit]
    print(f"Evaluating {len(players)} players...", flush=True)

    results = []
    t0 = time.time()
    for i, stem in enumerate(players):
        try:
            r = eval_player(stem, args.n_games, args.seed)
        except Exception as e:  # noqa: BLE001 — one bad player shouldn't kill a multi-hour roster run
            print(f"  [{i+1}/{len(players)}] {stem}: SKIPPED ({e})", flush=True)
            continue
        if r is not None:
            results.append(r)
            print(f"  [{i+1}/{len(players)}] {stem}: ok ({r['n_games']} games, {time.time()-t0:.0f}s elapsed)", flush=True)
        else:
            print(f"  [{i+1}/{len(players)}] {stem}: SKIPPED (insufficient data)", flush=True)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump({"players": results, "n_players_evaluated": len(results),
                   "flat_rates": list(FLAT_RATES), "selective_ks": list(SELECTIVE_KS)}, f)
    print(f"\nWrote {out_path} — {len(results)}/{len(players)} players, {time.time()-t0:.0f}s total")


if __name__ == "__main__":
    main()
