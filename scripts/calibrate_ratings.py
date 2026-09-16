"""One-off: recompute RATING_ATTRIBUTES/STYLE_ATTRIBUTES lo/hi anchors from
the ACTUAL distribution of raw values across the current player pool,
instead of the hand-picked guesses they were seeded with back when only
cdznjerry/BIG_TONKA_T existed. Those fixed guesses are now miscalibrated
against a 44-player pool spanning untitled club players up to Hikaru and
Magnus Carlsen — this prints the real 5th/95th percentile per attribute so
the constants in export_dashboard_data.py can be updated to match.

5th/95th (not min/max) deliberately: one extreme outlier (a single very
low-rated or very strong player) would otherwise single-handedly collapse
everyone else's score toward 0 or 100. Percentile bounds still keep the
"fixed domain, not pool-relative" scoring model intact — the anchors are
just now DERIVED from a real pool instead of guessed, not recomputed live
per viewer.

Usage: python -m scripts.calibrate_ratings
"""

from __future__ import annotations

import numpy as np

from scripts.export_dashboard_data import (
    DATASETS,
    RATING_ATTRIBUTES,
    STYLE_ATTRIBUTES,
    _raw_rating_values,
    build_profile,
)


def main() -> None:
    all_attrs = RATING_ATTRIBUTES + STYLE_ATTRIBUTES
    keys = [a[0] for a in all_attrs]
    values: dict[str, list[float]] = {k: [] for k in keys}

    for player, modes in DATASETS.items():
        for mode, stem in modes.items():
            profile = build_profile(stem)
            raw = _raw_rating_values(profile)
            for k in keys:
                v = raw.get(k)
                if v is not None and np.isfinite(v):
                    values[k].append(v)

    print(f"{'key':14} {'n':>4} {'old_lo':>8} {'old_hi':>8}   {'p5':>8} {'p50':>8} {'p95':>8}   {'min':>8} {'max':>8}")
    for key, label, lo, hi, higher, fmt, desc, formula in all_attrs:
        vs = np.array(values[key])
        p5, p50, p95 = np.percentile(vs, [5, 50, 95])
        print(
            f"{key:14} {len(vs):>4} {lo:>8.3f} {hi:>8.3f}   "
            f"{p5:>8.3f} {p50:>8.3f} {p95:>8.3f}   {vs.min():>8.3f} {vs.max():>8.3f}"
        )


if __name__ == "__main__":
    main()
