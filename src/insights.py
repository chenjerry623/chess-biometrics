"""Bucket every move into Overthought / Underthought / Panic / Clean /
Instinctive, using the trained per-player Model 1 (think-time) prediction as
the personal baseline — not a hand-rolled local median.

CLAUDE.md's original four buckets:
    Overthought  — time >> baseline, low criticality, cp_loss fine
    Underthought — time << baseline, high criticality, eval dropped
    Panic        — high time AND high cp_loss (time didn't convert)
    Clean        — allocation roughly matched criticality

This module adds a fifth, motivated by this project's own analysis: a
correct move found WITHOUT elevated time on a genuinely critical position
isn't just "Clean" — it's a distinct, notable pattern (fast, correct,
despite real difficulty), worth surfacing on its own rather than folding
into a generic bucket.

    Instinctive  — time << baseline, high criticality, but cp_loss fine

Design choices, both deliberate:
  - "Expected think-time" comes from the trained Model 1 booster (predicts
    log_think_time from the full complexity+clock feature set) — much
    sharper than the clock-decile local-median proxy used in this project's
    exploratory analysis, since it conditions on everything, not just clock
    state.
  - "Good move" uses the existing is_mistake threshold (cp_loss <= 100),
    NOT a Model 2 (move-quality) prediction. Model 2's R^2 is still weak for
    most player/mode combinations (0.07-0.12) — inheriting that weakness
    into the bucket definition would make buckets less trustworthy than the
    simple threshold that has produced every real finding in this project
    so far. Revisit this once Model 2 is good enough to earn the swap.

Criticality reuses the obviousness-adjusted definition validated throughout
this project: top-quartile eval-gap stakes among moves that are neither
recaptures nor forced (is_recapture/is_forced) — a recapture or one-legal-
move can have a huge eval swing with near-zero real difficulty, so raw
gap_1_2 alone overstates criticality for those moves.

Usage:
    python -m src.insights --features data/features/<u>_plies_features.parquet \
        --model-stem data/models/<u>_plies_thinktime_live
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from src.model import CATEGORICAL_FEATURES, CLOCK_FEATURES, COMPLEXITY_FEATURES, _prep_X

INSIGHTS_DIR = Path("data/insights")


def critical_mask(df: pd.DataFrame) -> pd.Series:
    """Top-quartile eval-gap stakes among non-obvious (non-recapture,
    non-forced) moves — the criticality definition validated across every
    player/mode analysis in this project so far."""
    obvious = df["is_recapture"] | df["is_forced"]
    gap_q75 = df.loc[~obvious, "gap_1_2"].quantile(0.75)
    return (~obvious) & (df["gap_1_2"] >= gap_q75)


def predict_expected_think_time(df: pd.DataFrame, booster: lgb.Booster) -> np.ndarray:
    """Expected think-time in seconds, from the trained Model 1 booster.
    Falls back gracefully if the model was trained without some features
    this dataset doesn't have (e.g. search-instability fields not yet
    re-annotated for this player)."""
    feature_names = booster.feature_name()
    feature_cols = [c for c in feature_names if c in df.columns]
    missing = [c for c in feature_names if c not in df.columns]
    if missing:
        print(f"Note: model was trained with features not in this data (using 0 for now): {missing}")
        for c in missing:
            df[c] = 0
        feature_cols = feature_names
    X = _prep_X(df, feature_cols)
    log_pred = booster.predict(X)
    return np.expm1(log_pred)


def assign_buckets(df: pd.DataFrame, expected_think_time: np.ndarray, is_critical: pd.Series) -> pd.DataFrame:
    df = df.copy()
    df["expected_think_time"] = expected_think_time
    df["elevated"] = df["think_time"] > df["expected_think_time"]
    df["is_critical"] = is_critical
    df["good_move"] = ~df["is_mistake"]

    conditions = [
        df["elevated"] & ~df["good_move"],  # Panic — spent the time, still bad
        ~df["elevated"] & df["is_critical"] & ~df["good_move"],  # Underthought
        df["elevated"] & ~df["is_critical"] & df["good_move"],  # Overthought
        ~df["elevated"] & df["is_critical"] & df["good_move"],  # Instinctive
    ]
    choices = ["panic", "underthought", "overthought", "instinctive"]
    df["bucket"] = np.select(conditions, choices, default="clean")
    return df


def premove_profile(df: pd.DataFrame) -> dict:
    """Premove behavior as part of the player's profile, not just filtered
    noise. A premove is the most extreme case of "zero real-time decision" —
    queued before even seeing the opponent's reply — so its RATE, the
    SITUATIONS it happens in, and its ACCURACY are a real behavioral
    signature, extending the same idea behind is_recapture/is_forced
    (separating "obvious" from "genuinely hard") to its limit. Unlike the
    rest of this module, this works from data that still INCLUDES premoves
    — is_suspect would filter them out, since think_time isn't meaningful
    for them, but their rate and accuracy are exactly what we want here.
    """
    obvious = df["is_recapture"] | df["is_forced"]
    gap_q75 = df.loc[~obvious, "gap_1_2"].quantile(0.75)
    is_critical = (~obvious) & (df["gap_1_2"] >= gap_q75)

    has_cp = df.dropna(subset=["cp_loss"])
    risky = df[is_critical & df["is_premove"]]

    def _f(x) -> float | None:
        # numpy scalars (incl. NaN) aren't natively JSON-serializable;
        # coerce to plain Python float, or None if genuinely missing.
        return None if x is None or pd.isna(x) else float(x)

    return {
        "premove_rate": _f(df["is_premove"].mean()),
        "premove_cp_loss": _f(has_cp.loc[has_cp["is_premove"], "cp_loss"].mean()),
        "non_premove_cp_loss": _f(has_cp.loc[~has_cp["is_premove"], "cp_loss"].mean()),
        # The calibration question: do they mostly premove where it's safe
        # to (obvious moves), or do they also premove on positions that were
        # genuinely critical (a real risk, or a sign intuition alone is
        # enough there)?
        "premove_rate_on_obvious": _f(df.loc[obvious, "is_premove"].mean()),
        "premove_rate_on_critical": _f(df.loc[is_critical, "is_premove"].mean()),
        "n_risky_premoves": int(len(risky)),
        "risky_premove_cp_loss": _f(risky["cp_loss"].mean()) if len(risky) else None,
    }


def build(features_path: Path, model_stem: str) -> Path:
    df = pd.read_parquet(features_path)
    clean = df[~df["is_suspect"]].dropna(subset=["think_time", "gap_1_2", "cp_loss"]).copy()

    booster_path = Path(f"{model_stem}.txt")
    if not booster_path.exists():
        raise SystemExit(f"No trained model at {booster_path} — run src.model first.")
    booster = lgb.Booster(model_file=str(booster_path))

    expected = predict_expected_think_time(clean, booster)
    crit = critical_mask(clean)
    scored = assign_buckets(clean, expected, crit)

    print(f"n={len(scored)}")
    print("\nBucket rates (overall):")
    print(scored["bucket"].value_counts(normalize=True).round(3).to_string())
    print("\nBucket rates, critical positions only:")
    crit_sub = scored[scored["is_critical"]]
    print(crit_sub["bucket"].value_counts(normalize=True).round(3).to_string())

    # Premove profile uses a separately-cleaned frame that keeps premoves in
    # (is_suspect would exclude them) and drops only the other noise types.
    premove_clean = df[~(df["is_negative"] | df["lag_suspect"] | df["think_time"].isna())].dropna(
        subset=["gap_1_2", "cp_loss"]
    )
    profile = premove_profile(premove_clean)
    print("\nPremove profile:")
    for k, v in profile.items():
        print(f"  {k}: {v:.3f}" if isinstance(v, float) else f"  {k}: {v}")

    INSIGHTS_DIR.mkdir(parents=True, exist_ok=True)
    stem = features_path.stem.replace("_features", "")
    out = INSIGHTS_DIR / f"{stem}_insights.parquet"
    scored.to_parquet(out, index=False)
    print(f"\nWrote {out}")

    profile_out = INSIGHTS_DIR / f"{stem}_premove_profile.json"
    with profile_out.open("w") as f:
        json.dump(profile, f, indent=2)
    print(f"Wrote {profile_out}")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--features", required=True, type=Path)
    parser.add_argument(
        "--model-stem",
        required=True,
        help="path prefix for the trained think-time booster "
        "(e.g. data/models/<u>_plies_thinktime_live, no .txt)",
    )
    args = parser.parse_args()
    build(args.features, args.model_stem)


if __name__ == "__main__":
    main()
