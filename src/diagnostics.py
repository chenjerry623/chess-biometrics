"""Check whether skill drift is severe enough to matter before you build around it.

Splits your game history into chronological quartiles and reports, per quartile:
  - mean rating (are you actually a different-strength player across the data?)
  - corr(think_time, gap_1_2) — does the "think longer on critical positions"
    relationship hold consistently, or has it changed?
  - median think_time and cp_loss — has your overall speed/accuracy shifted?

If rating and the correlations look roughly stable across quartiles, one pooled
model with rating + recency_weight is fine. If they diverge a lot, prefer a
trailing-window baseline (e.g. last N games) for live recommendations, and keep
the full-history model only for the descriptive "how have you evolved" story.

Usage:
    python -m src.diagnostics --features data/features/<u>_plies_features.parquet
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


def check_drift(features_path: Path) -> None:
    df = pd.read_parquet(features_path)
    clean = df[~df["is_suspect"]].dropna(subset=["think_time", "gap_1_2", "cp_loss"])

    if clean.empty:
        raise SystemExit("No clean rows to diagnose — check upstream filtering.")

    clean = clean.sort_values("game_date")
    clean["quartile"] = pd.qcut(clean["games_played_so_far"], 4, labels=[1, 2, 3, 4])

    print(f"{'Q':<3}{'n':<8}{'date range':<26}{'mean rtg':<10}"
          f"{'corr(t,gap)':<13}{'med think_s':<13}{'mean cp_loss':<12}")

    for q, g in clean.groupby("quartile", observed=True):
        date_range = f"{g['game_date'].min().date()} to {g['game_date'].max().date()}"
        corr = g["think_time"].corr(g["gap_1_2"])
        print(
            f"{q:<3}{len(g):<8}{date_range:<26}{g['player_rating'].mean():<10.0f}"
            f"{corr:<13.3f}{g['think_time'].median():<13.2f}{g['cp_loss'].mean():<12.1f}"
        )

    print(
        "\nRead this as: if mean rating or corr(t,gap) swings a lot across "
        "quartiles, a single pooled baseline blurs together different versions "
        "of you. Prefer a trailing-window baseline for 'what should I do now' "
        "recommendations, and treat the full-history model as descriptive only."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--features", required=True, type=Path)
    args = parser.parse_args()
    check_drift(args.features)


if __name__ == "__main__":
    main()
