"""Per-player baselines: expected think-time and expected move quality.

Two LightGBM (GBDT) models:
  1. think_time ~ complexity + clock_state   (log_think_time as target)
  2. cp_loss    ~ complexity + time_spent    (move quality)

Interpretability matters more than squeezing out marginal accuracy — SHAP
values are what turn a prediction into a user-facing explanation, and later
feed Phase 2 deviation scoring. Don't swap in a blacker-box model for a small
accuracy gain.

Each model is fit two ways, per the skill-drift diagnostic (see diagnostics.py):
  - "live"        — trailing window only (recent games), recency-weighted.
                    This is the baseline for "what should I do right now."
  - "descriptive" — full history, uniform weight. Only for the separate
                    "how have I evolved over time" story — never for live
                    advice, since a long pooled history blurs together
                    different versions of the player.

Usage:
    python -m src.model --features data/features/<u>_plies_features.parquet
"""

from __future__ import annotations

import argparse
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
import shap
from sklearn.metrics import mean_absolute_error, r2_score

MODELS_DIR = Path("data/models")
# Below this many rows, a chronological train/test split degenerates (e.g. a
# live window that's almost entirely one concentrated session/day) — skip
# the live model rather than fit something meaningless or crash.
MIN_LIVE_ROWS = 500

# Position-complexity features: criticality, volatility, forcing options,
# branching, tactical geometry, phase, trajectory. Shared by both models.
COMPLEXITY_FEATURES = [
    "gap_1_2",  # reference only (see features.py) — kept so SHAP can show
                # the model actually prefers outcome_changes over it.
    "outcome_changes",
    "mate_distance_gap",
    "top_n_spread",
    "n_moves_within_30cp",
    "n_moves_within_100cp",
    "abs_eval",
    "is_decided",
    "outcome_bucket",
    "is_recapture",
    "is_forced",
    "legal_moves",
    "total_material",
    "material_diff",
    "is_endgame",
    "n_pieces",
    "halfmove_clock",
    "game_phase",
    "open_files",
    "half_open_files",
    "blocked_pawns",
    "blocked_pawn_frac",
    "own_pinned",
    "opp_pinned",
    "own_passed_pawns",
    "opp_passed_pawns",
    "own_bishop_pair",
    "opp_bishop_pair",
    "own_doubled_pawns",
    "opp_doubled_pawns",
    "own_isolated_pawns",
    "opp_isolated_pawns",
    "own_pawn_islands",
    "opp_pawn_islands",
    "own_forking_pieces",
    "opp_forking_pieces",
    "own_skewers",
    "opp_skewers",
    "n_discovered_check_moves",
    "n_double_check_moves",
    "opposite_side_castling",
    "move_value_entropy",
    "in_book",
    "plies_since_book",
    "search_stable_depth",
    "n_best_move_changes",
    "eval_swing_across_depth",
    "n_checks_available",
    "n_captures_available",
    "n_promotions_available",
    "in_check",
    "loose_own",
    "loose_opp",
    "king_attackers_own",
    "king_attackers_opp",
    "has_castled_rights",
    "pawn_count",
    "eval_delta_1",
    "eval_roll_std_3",
    "recently_volatile",
    # Human-move-likelihood, from Maia at the player's nearest rating bin —
    # "how hard is this for a human at this strength," a different axis
    # from every engine-derived complexity feature above. See
    # add_maia_features() in features.py and PROJECT_LOG.md.
    "maia_prob_played",
    "maia_top1_prob",
    "maia_is_top_choice",
    "maia_surprise",
    "maia_prob_best_move",
    "requires_overriding_instinct",
    "material_offered",
]

CLOCK_FEATURES = [
    "clock_frac_remaining",
    "clock_diff",
    "behind_on_clock",
    "low_time",
    "game_progress",
    # How long the opponent spent on their immediately preceding move — a
    # fast move on our end doesn't have to mean "solved instantly": it can
    # mean pre-calculating while they were still thinking.
    "opp_think_time_prev",
    # Fatigue/warm-up proxies: how deep into a continuous playing session
    # this game is, and the gap since the previous game started.
    "session_game_index",
    "minutes_since_prev_game",
]

CATEGORICAL_FEATURES = ["outcome_bucket", "game_phase"]
# Fixed category->code mappings, matching the small known vocabularies in
# features.py's _outcome_bucket()/game_phase(). Using pd.Categorical with an
# explicit categories= list (rather than astype("category"), which infers
# categories from whatever happens to be present in that particular slice)
# guarantees the same codes regardless of which rows are in a given split —
# required for a raw Booster saved via save_model() to predict correctly
# later, since the saved model only stores integer category codes, not the
# category->code mapping the sklearn wrapper would otherwise remember.
CATEGORY_VALUES = {
    "outcome_bucket": ["mate_for", "winning", "balanced", "losing", "mate_against"],
    "game_phase": ["opening", "middlegame", "endgame"],
}


def _clean_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Drop premove/lag-spike rows — think_time isn't trustworthy there, and
    it's used as a target or predictor in both models."""
    return df[~df["is_suspect"]].copy()


def _prep_X(df: pd.DataFrame, feature_cols: list[str]) -> pd.DataFrame:
    X = df[feature_cols].copy()
    for c in CATEGORICAL_FEATURES:
        if c in X.columns:
            X[c] = pd.Categorical(X[c], categories=CATEGORY_VALUES[c])
    # outcome_changes is missing (not 0/1) for the rare only-one-legal-move
    # positions where there's no second-best line to compare against — keep
    # that as a real NaN rather than a silently-coerced object dtype.
    if "outcome_changes" in X.columns:
        X["outcome_changes"] = pd.to_numeric(X["outcome_changes"], errors="coerce")
    return X


def _time_split(df: pd.DataFrame, test_frac: float = 0.15) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Chronological holdout by game — never split a single game across
    train/test, and never let training see games that happened later."""
    game_dates = (
        df[["game_id", "game_date"]].drop_duplicates().sort_values("game_date")
    )
    cutoff_idx = int(len(game_dates) * (1 - test_frac))
    cutoff_date = game_dates.iloc[cutoff_idx]["game_date"]
    train = df[df["game_date"] < cutoff_date]
    test = df[df["game_date"] >= cutoff_date]
    return train, test


def _fit_and_report(
    name: str,
    train: pd.DataFrame,
    test: pd.DataFrame,
    feature_cols: list[str],
    target: str,
    sample_weight_col: str | None,
    objective: str,
) -> lgb.LGBMRegressor:
    X_train, X_test = _prep_X(train, feature_cols), _prep_X(test, feature_cols)
    y_train, y_test = train[target], test[target]
    w_train = train[sample_weight_col] if sample_weight_col else None

    model = lgb.LGBMRegressor(
        objective=objective,
        n_estimators=300,
        learning_rate=0.05,
        num_leaves=31,
        min_child_samples=20,
        random_state=0,
        verbose=-1,
    )
    model.fit(
        X_train,
        y_train,
        sample_weight=w_train,
        categorical_feature=[c for c in CATEGORICAL_FEATURES if c in feature_cols],
    )

    pred = model.predict(X_test)
    mae = mean_absolute_error(y_test, pred)
    r2 = r2_score(y_test, pred)
    print(f"\n[{name}] n_train={len(train)} n_test={len(test)} MAE={mae:.3f} R2={r2:.3f}")

    explainer = shap.TreeExplainer(model)
    shap_values = explainer.shap_values(X_test)
    importance = (
        pd.Series(np.abs(shap_values).mean(axis=0), index=feature_cols)
        .sort_values(ascending=False)
    )
    print("  top SHAP features:")
    for feat, val in importance.head(10).items():
        print(f"    {feat:<25} {val:.4f}")

    return model


def build(features_path: Path, window_days: int) -> None:
    df = pd.read_parquet(features_path)
    df = _clean_rows(df)
    df = df.dropna(subset=["think_time", "log_think_time", "player_rating"])

    last_date = df["game_date"].max()
    days_before_last = (last_date - df["game_date"]).dt.days
    live_df = df[days_before_last <= window_days].copy()

    print(
        f"Descriptive set (full history): {len(df)} rows, "
        f"{df['game_date'].min().date()} to {last_date.date()}, "
        f"rating {df['player_rating'].min():.0f}-{df['player_rating'].max():.0f}"
    )
    print(
        f"Live set (trailing {window_days}d): {len(live_df)} rows, "
        f"{live_df['game_date'].min().date()} to {last_date.date()}, "
        f"rating {live_df['player_rating'].min():.0f}-{live_df['player_rating'].max():.0f}"
    )

    tt_features_all = COMPLEXITY_FEATURES + CLOCK_FEATURES + ["player_rating"]
    tt_features = [c for c in tt_features_all if c in df.columns]
    missing = [c for c in tt_features_all if c not in df.columns]
    if missing:
        print(f"\nSkipping features not present in this dataset (older annotation?): {missing}")
    mq_features = tt_features + ["log_think_time"]

    print("\n=== Model 1: think-time ~ complexity + clock_state (log_think_time) ===")
    if len(live_df) < MIN_LIVE_ROWS:
        print(
            f"Live window has only {len(live_df)} rows (< {MIN_LIVE_ROWS}) — "
            f"too few/too narrow a date range for a meaningful train/test split "
            f"(e.g. all in one day). Skipping the live model; descriptive only."
        )
        tt_live = None
    else:
        train, test = _time_split(live_df)
        if train.empty or test.empty:
            # MIN_LIVE_ROWS only guards the total row count — it doesn't
            # guarantee the CHRONOLOGICAL split is non-degenerate. Found
            # for real: a player whose entire trailing-window activity
            # landed on just 2 calendar dates (1056 rows on one day, 72 on
            # another a month later) put the 85th-percentile-by-game cutoff
            # before every row, so train came out empty and LightGBM's
            # `Input data must be 2 dimensional and non empty` crashed the
            # whole export for that one player. A live model needs BOTH
            # sides of the split populated, not just enough raw rows.
            print(
                f"Live window's chronological split left one side empty "
                f"(train={len(train)}, test={len(test)}) — likely all activity "
                f"landed on too few distinct dates. Skipping the live model; descriptive only."
            )
            tt_live = None
        else:
            tt_live = _fit_and_report(
                "think-time / live", train, test, tt_features, "log_think_time",
                "recency_weight", objective="regression",
            )
    train, test = _time_split(df)
    tt_desc = _fit_and_report(
        "think-time / descriptive", train, test, tt_features, "log_think_time",
        None, objective="regression",
    )

    # cp_loss is heavily right-skewed (rare huge blunders alongside a mass of
    # near-zero losses) — L1 (MAE) loss keeps a handful of blunders from
    # dominating the fit the way L2 would.
    #
    # Restrict to contested positions (not already decided): once abs_eval is
    # near the mate-score ceiling, cp_loss is mechanically bounded by how
    # decided the position already is, which swamps any real time-spent
    # signal (see the SHAP check that motivated this — outcome_bucket/
    # abs_eval dominated while log_think_time barely registered). Move
    # quality in an already-won/lost position also isn't the interesting
    # question anyway.
    mq_df = df[~df["is_decided"]].dropna(subset=["cp_loss"])
    mq_live_df = live_df[~live_df["is_decided"]].dropna(subset=["cp_loss"])

    print("\n=== Model 2: cp_loss ~ complexity + time_spent ===")
    if len(mq_live_df) < MIN_LIVE_ROWS:
        print(
            f"Live window has only {len(mq_live_df)} contested-position rows "
            f"(< {MIN_LIVE_ROWS}) — skipping the live move-quality model."
        )
        mq_live = None
    else:
        train, test = _time_split(mq_live_df)
        if train.empty or test.empty:
            # Same guard as Model 1's live split, same reasoning — see there.
            print(
                f"Live window's chronological split left one side empty "
                f"(train={len(train)}, test={len(test)}) — skipping the live move-quality model."
            )
            mq_live = None
        else:
            mq_live = _fit_and_report(
                "move-quality / live", train, test, mq_features, "cp_loss",
                "recency_weight", objective="regression_l1",
            )
    train, test = _time_split(mq_df)
    mq_desc = _fit_and_report(
        "move-quality / descriptive", train, test, mq_features, "cp_loss",
        None, objective="regression_l1",
    )

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    stem = features_path.stem.replace("_features", "")
    for name, model in [
        ("thinktime_live", tt_live),
        ("thinktime_descriptive", tt_desc),
        ("movequality_live", mq_live),
        ("movequality_descriptive", mq_desc),
    ]:
        if model is None:
            continue
        out = MODELS_DIR / f"{stem}_{name}.txt"
        model.booster_.save_model(str(out))
        print(f"Saved {out}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--features", required=True, type=Path)
    parser.add_argument(
        "--window-days",
        type=int,
        default=270,
        help="trailing window size (days back from the most recent game) for the live model",
    )
    args = parser.parse_args()
    build(args.features, args.window_days)


if __name__ == "__main__":
    main()
