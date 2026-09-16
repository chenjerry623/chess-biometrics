"""Phase 2 v0: cheat-detection evaluation using INJECTED/SIMULATED cheat
moves with known ground truth — never real accused players, per
CLAUDE.md's explicit constraint. This is a "does the signal exist at all,
and how hard does an adversary have to work to spoof it" harness, not a
finished detector — a deliberate first step, not the whole of Phase 2.

Why cp_loss is NOT used here (see PROJECT_LOG.md for the full
investigation): it's computed as the gap between two INDEPENDENT engine
searches at consecutive plies, not loss relative to the position's own
best-move PV — it measures cross-search evaluation instability, which can
be huge (thousands of cp) even when the objectively best move was played,
especially near forced mates. Simulating "this ply is now an engine
move" by borrowing that field would be actively wrong, not just noisy.
A real move-quality signal for Phase 2 needs fresh engine re-analysis of
the resulting position, which is a genuine future scope item requiring
real Stockfish compute, not something this v0 fakes for free.

What IS used, and why it's safe to simulate cheaply from data already on
disk:
  - Maia human-move-likelihood (`maia_prob_best_move`) is a single,
    well-defined neural-net forward pass over the position — swapping in
    "what Maia assigns to the engine's own best move" for an injected ply
    is a correct simulation of "this move came from an engine, not this
    human," not an approximation of something else.
  - The player's own fitted think-time-vs-complexity model (already
    trained per player in src/model.py) gives a genuine, personalized
    "how long should THIS player take on a position this hard" baseline
    to compare injected timing against.

Injection has two adversary strength levels:
  - "naive": replaced moves get an unnaturally fast, complexity-blind
    think-time (0.2-1.0s) — the lazy-cheater case a timing check alone
    should catch easily.
  - "smart": replaced moves get timing SAMPLED from this exact player's
    own fitted think-time model's prediction for that position (plus
    realistic noise) — the adversarial case, deliberately as hard as
    possible for the timing signal specifically, to honestly test
    whether anything survives once timing is well-disguised.

Usage:
    python -m scripts.phase2_cheat_simulation --player cdznjerry_2023on_plies
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import lightgbm as lgb
from sklearn.metrics import roc_auc_score

sys.path.insert(0, ".")
from src.model import COMPLEXITY_FEATURES, CLOCK_FEATURES, _prep_X, _time_split  # noqa: E402

FEATURES_DIR = Path("data/features")
MODELS_DIR = Path("data/models")


def inject_cheat(game_df: pd.DataFrame, rate: float, mode: str, rng: np.random.Generator,
                  tt_model=None, tt_features: list[str] | None = None,
                  selective_k: int | None = None) -> pd.DataFrame:
    """selective_k, when set, ignores `rate` and instead injects on the
    `selective_k` most CRITICAL plies in the game (lowest gap_1_2 — the
    top two candidate moves were close in value, a genuinely hard
    decision) — this is CLAUDE.md's actual hardest, most realistic case:
    "engine on 1-3 critical moves," not a flat rate across the whole
    game. A real selective cheater doesn't need engine help on forced
    recaptures; they need it on the moments that actually matter."""
    df = game_df.copy()
    n = len(df)
    df["is_injected"] = False
    if n == 0:
        return df
    if selective_k is not None:
        gaps = df["gap_1_2"]
        candidates = gaps.dropna().sort_values().index
        idx = pd.Index(candidates[:selective_k])
        if len(idx) == 0:
            return df
    else:
        if rate <= 0:
            return df
        k = max(1, min(n, int(round(n * rate))))
        idx = rng.choice(df.index, size=k, replace=False)
    df.loc[idx, "is_injected"] = True

    eps = 1e-6
    df.loc[idx, "maia_prob_played"] = df.loc[idx, "maia_prob_best_move"]
    df.loc[idx, "maia_surprise"] = -np.log(df.loc[idx, "maia_prob_best_move"].clip(lower=eps))
    df.loc[idx, "played_best_move"] = True

    if mode == "naive":
        df.loc[idx, "think_time"] = rng.uniform(0.2, 1.0, size=len(idx))
    elif mode == "smart" and tt_model is not None:
        # tt_model was trained on log1p(think_time) (see features.py) — the
        # correct inverse is expm1, not exp. Using exp() here originally
        # added a systematic +1s bias to every injected time (exp(log1p(x))
        # = 1+x for small x), which is enormous at bullet timescales and
        # was silently making "smart" injections look SLOWER, not
        # disguised — caught by the AUC going below 0.5 (reversed) instead
        # of settling near the expected ~0.5 once well-disguised.
        X = _prep_X(df.loc[idx], tt_features)
        pred_log_tt = tt_model.predict(X)
        noise = rng.normal(0, 0.15, size=len(idx))
        df.loc[idx, "think_time"] = np.expm1(pred_log_tt + noise).clip(min=0.1)
    return df


def score_game(game_df: pd.DataFrame, tt_model, tt_features: list[str]) -> dict:
    X = _prep_X(game_df, tt_features)
    pred_log_tt = tt_model.predict(X)
    actual_log_tt = np.log1p(game_df["think_time"].clip(lower=0))
    residual = actual_log_tt.to_numpy() - pred_log_tt
    timing_score = float(-residual.mean())  # suspicious = faster than expected for the complexity
    # NOTE ON SIGN, after a real back-and-forth (see PROJECT_LOG.md,
    # "the sign isn't backwards, it's PLAYER-DEPENDENT"): a one-player
    # check (cdznjerry) suggested "-mean(surprise)" here had the sign
    # backwards and flipping to "+mean(surprise)" was tried. Checked
    # against the FULL 135-player roster via phase2_multigame.py: neither
    # fixed sign is a good global rule. Whether "the engine's best move is
    # more surprising to Maia than this player's own real choice" is true
    # depends on the individual player — 33/135 players strongly matched
    # the flipped direction (n=20-game AUC >= 0.75), 48/135 strongly
    # matched THIS (original) direction (AUC <= 0.25), and the rest were
    # ambiguous. Population median AUC under the flipped sign was 0.414
    # (net WORSE than chance); under this original sign, 0.586 (weakly
    # net better) — so this is the marginally better fixed global default,
    # not because the underlying assumption is reliably true, but because
    # it's true slightly more often than not across this roster. Neither
    # is trustworthy as a per-player claim. The per-player TRAINED
    # COMBINER below (LogisticRegression per player) is the credible way
    # to use this signal — it learns the right sign/magnitude for each
    # player from their own data instead of assuming one global rule.
    maia_score = float(-game_df["maia_surprise"].mean())  # suspicious = low surprise (marginally better GLOBAL default — see note above; not reliable per-player)
    return {"timing_score": timing_score, "maia_score": maia_score, "combined": timing_score + maia_score}


def score_game_max(game_df: pd.DataFrame, tt_model, tt_features: list[str]) -> dict:
    """Per-move suspicion, reduced by MAX (and top-3 mean) rather than a
    whole-game average — for a selective cheater who only tampers with
    1-3 moves out of 20-60, averaging over the whole game dilutes the
    signal from those few moves down to near-nothing. Looking at the
    single worst outlier move (or the worst few) is the realistic
    forensic approach: does ANY moment in this game look anomalous, not
    "does the game look anomalous on average."""
    X = _prep_X(game_df, tt_features)
    pred_log_tt = tt_model.predict(X)
    actual_log_tt = np.log1p(game_df["think_time"].clip(lower=0))
    residual = actual_log_tt.to_numpy() - pred_log_tt
    per_move_timing = -residual  # suspicious = faster than expected
    per_move_maia = -game_df["maia_surprise"].to_numpy()  # suspicious = low surprise — see score_game's comment on sign
    top3_timing = np.sort(per_move_timing)[-3:] if len(per_move_timing) >= 3 else per_move_timing
    top3_maia = np.sort(per_move_maia)[-3:] if len(per_move_maia) >= 3 else per_move_maia
    return {
        "timing_max": float(per_move_timing.max()),
        "timing_top3_mean": float(top3_timing.mean()),
        "maia_max": float(per_move_maia.max()),
        "maia_top3_mean": float(top3_maia.mean()),
    }


def run_selective(player_stem: str, n_games: int = 40, ks=(1, 2, 3), seed: int = 0) -> pd.DataFrame:
    df = pd.read_parquet(FEATURES_DIR / f"{player_stem}_features.parquet")
    df = df[~df["is_suspect"]].dropna(subset=["think_time", "player_rating", "maia_prob_best_move"])
    tt_features = [c for c in COMPLEXITY_FEATURES + CLOCK_FEATURES + ["player_rating"] if c in df.columns]

    model_path = MODELS_DIR / f"{player_stem}_thinktime_live.txt"
    if not model_path.exists():
        model_path = MODELS_DIR / f"{player_stem}_thinktime_descriptive.txt"
    tt_model = lgb.Booster(model_file=str(model_path))

    _, test = _time_split(df)
    game_ids = test["game_id"].drop_duplicates().to_numpy()
    rng = np.random.default_rng(seed)
    picked = rng.choice(game_ids, size=min(n_games, len(game_ids)), replace=False)

    results = []
    for gid in picked:
        g = test[test["game_id"] == gid].sort_values("ply")
        if len(g) < 10 or g["gap_1_2"].notna().sum() < max(ks):
            continue
        results.append({"game_id": gid, "k": 0, "mode": "clean", **score_game_max(g, tt_model, tt_features)})
        for mode in ("naive", "smart"):
            for k in ks:
                inj = inject_cheat(g, 0.0, mode, rng, tt_model, tt_features, selective_k=k)
                results.append({"game_id": gid, "k": k, "mode": mode, **score_game_max(inj, tt_model, tt_features)})

    res = pd.DataFrame(results)
    print(f"\n=== {player_stem}: SELECTIVE injection (1-3 critical moves), {len(picked)} held-out games ===")
    for mode in ("naive", "smart"):
        print(f"\n  -- {mode} timing, {mode} moves selectively injected on the {{k}} most critical plies --")
        for k in ks:
            sub = res[(res["mode"] == "clean") | ((res["mode"] == mode) & (res["k"] == k))]
            y = (sub["mode"] == mode).astype(int)
            if y.nunique() < 2:
                continue
            row = f"    k={k} critical moves "
            for score_col in ("timing_max", "timing_top3_mean", "maia_max", "maia_top3_mean"):
                auc = roc_auc_score(y, sub[score_col])
                row += f" {score_col}={auc:.3f}"
            print(row)
    return res


def run(player_stem: str, n_games: int = 40, rates=(0.1, 0.25, 0.5, 1.0), seed: int = 0) -> pd.DataFrame:
    df = pd.read_parquet(FEATURES_DIR / f"{player_stem}_features.parquet")
    # Match src/model.py exactly: only think_time/rating are required
    # non-null. Most complexity features (mate_distance_gap especially)
    # are NaN outside their relevant context by design — LightGBM handles
    # missing values natively, so dropping those rows would silently
    # discard 97%+ of the data for no reason (confirmed the hard way).
    df = df[~df["is_suspect"]].dropna(subset=["think_time", "player_rating", "maia_prob_best_move"])
    tt_features = [c for c in COMPLEXITY_FEATURES + CLOCK_FEATURES + ["player_rating"] if c in df.columns]

    model_path = MODELS_DIR / f"{player_stem}_thinktime_live.txt"
    if not model_path.exists():
        model_path = MODELS_DIR / f"{player_stem}_thinktime_descriptive.txt"
    if not model_path.exists():
        raise SystemExit(f"No think-time model found for {player_stem} — run src.model first.")
    tt_model = lgb.Booster(model_file=str(model_path))

    _, test = _time_split(df)  # held-out games only — never used to fit this player's own baseline
    game_ids = test["game_id"].drop_duplicates().to_numpy()
    rng = np.random.default_rng(seed)
    picked = rng.choice(game_ids, size=min(n_games, len(game_ids)), replace=False)

    results = []
    for gid in picked:
        g = test[test["game_id"] == gid].sort_values("ply")
        if len(g) < 10:
            continue
        results.append({"game_id": gid, "rate": 0.0, "mode": "clean", **score_game(g, tt_model, tt_features)})
        for mode in ("naive", "smart"):
            for rate in rates:
                inj = inject_cheat(g, rate, mode, rng, tt_model, tt_features)
                results.append({"game_id": gid, "rate": rate, "mode": mode, **score_game(inj, tt_model, tt_features)})

    res = pd.DataFrame(results)
    print(f"\n=== {player_stem}: {len(picked)} held-out games, clean vs. injected ===")
    for mode in ("naive", "smart"):
        print(f"\n  -- {mode} timing --")
        for rate in rates:
            sub = res[(res["mode"] == "clean") | ((res["mode"] == mode) & (res["rate"] == rate))]
            y = (sub["mode"] == mode).astype(int)
            if y.nunique() < 2:
                continue
            row = f"    rate={rate:>4.0%} "
            for score_col in ("timing_score", "maia_score", "combined"):
                auc = roc_auc_score(y, sub[score_col])
                row += f" {score_col}={auc:.3f}"
            print(row)

    # Trained combiner vs. the naive 1:1 sum "combined" score above — the
    # naive sum visibly HURT at low rates (e.g. rate=10%: timing_score
    # alone scored 0.670 but the naive sum only 0.530, since maia_score
    # is near-chance there and just adds noise to a perfectly good
    # timing signal). A logistic regression can learn to down-weight the
    # uninformative signal instead of blindly averaging it in. Trained
    # on HALF the held-out games, evaluated on the other half (a
    # different split from the think-time model's own train/test) so the
    # reported AUC isn't inflated by fitting and evaluating on the same
    # games.
    from sklearn.linear_model import LogisticRegression
    game_ids_all = res["game_id"].unique()
    rng2 = np.random.default_rng(seed + 1)
    rng2.shuffle(game_ids_all)
    half = len(game_ids_all) // 2
    train_ids, eval_ids = set(game_ids_all[:half]), set(game_ids_all[half:])
    print(f"\n  -- trained combiner (LogisticRegression on timing_score + maia_score) --")
    print(f"     fit on {len(train_ids)} games, evaluated on the other {len(eval_ids)} — a different split than the naive-sum numbers above")
    for mode in ("naive", "smart"):
        for rate in rates:
            sub = res[(res["mode"] == "clean") | ((res["mode"] == mode) & (res["rate"] == rate))]
            y = (sub["mode"] == mode).astype(int)
            if y.nunique() < 2:
                continue
            train_mask = sub["game_id"].isin(train_ids)
            eval_mask = sub["game_id"].isin(eval_ids)
            if y[train_mask].nunique() < 2 or y[eval_mask].nunique() < 2:
                continue
            clf = LogisticRegression()
            clf.fit(sub.loc[train_mask, ["timing_score", "maia_score"]], y[train_mask])
            pred = clf.predict_proba(sub.loc[eval_mask, ["timing_score", "maia_score"]])[:, 1]
            auc = roc_auc_score(y[eval_mask], pred)
            naive_auc = roc_auc_score(y[eval_mask], sub.loc[eval_mask, "combined"])
            print(f"    {mode:<6s} rate={rate:>4.0%}  trained_auc={auc:.3f}  (naive_sum on same eval split={naive_auc:.3f})  weights=[timing:{clf.coef_[0][0]:+.2f}, maia:{clf.coef_[0][1]:+.2f}]")
    return res


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--player", required=True, help="features file stem, e.g. cdznjerry_2023on_plies")
    parser.add_argument("--n-games", type=int, default=40)
    parser.add_argument("--selective", action="store_true", help="run the 1-3 critical-move injection scenario instead of the flat-rate one")
    args = parser.parse_args()
    if args.selective:
        run_selective(args.player, n_games=args.n_games)
    else:
        run(args.player, n_games=args.n_games)


if __name__ == "__main__":
    main()
