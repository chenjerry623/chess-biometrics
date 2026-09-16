"""Aggregates scripts/phase2_full_roster.py's raw per-player output into
the summary shape the standalone cheat-detection dashboard consumes:
medians/IQR per scenario across the whole roster (not just point
estimates from 2-3 hand-picked players), score distributions for
histograms, and a compact per-player table.

Usage:
    python -m scripts.phase2_build_dashboard_data \
        --in scratchpad/phase2_dashboard/data.json \
        --out scratchpad/cheat_dashboard/data.js
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np


FLAT_RATES = ["0.1", "0.25", "0.5", "1.0"]
SELECTIVE_KS = ["1", "2", "3"]
FLAT_COLS = ["timing_score", "maia_score", "combined"]
SEL_COLS = ["timing_max", "timing_top3_mean", "maia_max", "maia_top3_mean"]


def _summary(vals: list[float]) -> dict | None:
    if not vals:
        return None
    a = np.array(vals, dtype=float)
    return {
        "median": float(np.median(a)),
        "q25": float(np.percentile(a, 25)),
        "q75": float(np.percentile(a, 75)),
        "min": float(a.min()),
        "max": float(a.max()),
        "n": int(len(a)),
        "values": [round(float(v), 4) for v in a],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default="scratchpad/phase2_dashboard/data.json")
    ap.add_argument("--out", default="scratchpad/cheat_dashboard/data.js")
    ap.add_argument("--multigame", default="scratchpad/phase2_dashboard/multigame.json")
    ap.add_argument("--direction", default="scratchpad/phase2_dashboard/per_player_direction.json")
    ap.add_argument("--trained-flat", default="scratchpad/phase2_dashboard/trained_detector.json")
    ap.add_argument("--trained-selective", default="scratchpad/phase2_dashboard/trained_detector_selective.json")
    args = ap.parse_args()

    def _trained_summary(path: str) -> dict | None:
        if not Path(path).exists():
            return None
        d = json.load(open(path))
        rich = [p["auc_trained_rich"] for p in d["players"]]
        thin = [p["auc_thin_2feat"] for p in d["players"]]
        return {
            "n": len(rich),
            "rich_median": float(np.median(rich)), "thin_median": float(np.median(thin)),
            "rich_wins": sum(1 for r, t in zip(rich, thin) if r > t),
            "rows": sorted(
                [{"player": p["player"].replace("_plies", ""), "rich": p["auc_trained_rich"], "thin": p["auc_thin_2feat"]} for p in d["players"]],
                key=lambda r: -r["rich"],
            ),
        }

    trained_flat = _trained_summary(args.trained_flat)
    trained_selective = _trained_summary(args.trained_selective)

    raw = json.load(open(args.inp))
    players = raw["players"]
    n_players = len(players)

    multigame = None
    if Path(args.multigame).exists():
        multigame = json.load(open(args.multigame))

    direction_rows = None
    if Path(args.direction).exists():
        direction_rows = json.load(open(args.direction))
        # Reframed as direction-agnostic STRENGTH (max(auc, 1-auc)) rather
        # than a raw AUC under one arbitrary global sign — see
        # PROJECT_LOG.md "the sign isn't backwards, it's PLAYER-DEPENDENT":
        # which direction is "suspicious" for this signal is genuinely
        # per-player, so reporting a fixed-sign AUC here would just bake
        # in a specific (now-reverted) code choice. Strength — "how well
        # does aggregated evidence separate clean from cheating for this
        # player, in whichever direction actually works for them" — is
        # the sign-invariant, honest version of the same finding, and it's
        # exactly the quantity a per-player-calibrated model (like the
        # trained combiner) can actually capture.
        strengths = []
        for r in direction_rows:
            a = r.get("auc_n20")
            if a is not None:
                strengths.append({"player": r["player"].replace("_plies", ""), "strength": max(a, 1 - a)})
        strong = sum(1 for r in strengths if r["strength"] >= 0.75)
        weak = sum(1 for r in strengths if r["strength"] < 0.6)
        vals = [r["strength"] for r in strengths]
        direction_summary = {
            "n": len(strengths),
            "median": float(np.median(vals)) if vals else None,
            "strong": strong, "weak": weak, "moderate": len(strengths) - strong - weak,
            "rows": sorted(strengths, key=lambda r: -r["strength"]),
        }
    else:
        direction_summary = None

    flat_agg = {"naive": {}, "smart": {}}
    for mode in ("naive", "smart"):
        for rate in FLAT_RATES:
            per_col = {c: [] for c in FLAT_COLS}
            for p in players:
                row = p.get("flat", {}).get(mode, {}).get(rate)
                if row:
                    for c in FLAT_COLS:
                        if c in row:
                            per_col[c].append(row[c])
            flat_agg[mode][rate] = {c: _summary(v) for c, v in per_col.items()}

    sel_agg = {"naive": {}, "smart": {}}
    for mode in ("naive", "smart"):
        for k in SELECTIVE_KS:
            per_col = {c: [] for c in SEL_COLS}
            for p in players:
                row = p.get("selective", {}).get(mode, {}).get(k)
                if row:
                    for c in SEL_COLS:
                        if c in row:
                            per_col[c].append(row[c])
            sel_agg[mode][k] = {c: _summary(v) for c, v in per_col.items()}

    combiner_rows = []
    trained_aucs, naive_aucs = [], []
    for p in players:
        c = p.get("combiner_smart_100pct")
        if c:
            combiner_rows.append({
                "player": p["player"],
                "trained_auc": c["trained_auc"],
                "naive_auc": c["naive_auc"],
                "timing_weight": c["weights"]["timing"],
                "maia_weight": c["weights"]["maia"],
            })
            trained_aucs.append(c["trained_auc"])
            naive_aucs.append(c["naive_auc"])

    per_player_table = []
    for p in players:
        row = {"player": p["player"].replace("_plies", ""), "n_games": p["n_games"]}
        naive100 = p.get("flat", {}).get("naive", {}).get("1.0", {})
        smart100 = p.get("flat", {}).get("smart", {}).get("1.0", {})
        sel_smart_1 = p.get("selective", {}).get("smart", {}).get("1", {})
        row["naive_combined_100"] = naive100.get("combined")
        row["smart_combined_100"] = smart100.get("combined")
        row["smart_timing_100"] = smart100.get("timing_score")
        row["selective_smart_k1_max"] = sel_smart_1.get("timing_max")
        per_player_table.append(row)

    out = {
        "n_players": n_players,
        "flat_rates": [float(r) for r in FLAT_RATES],
        "selective_ks": [int(k) for k in SELECTIVE_KS],
        "flat": flat_agg,
        "selective": sel_agg,
        "combiner": {
            "rows": combiner_rows,
            "trained_median": float(np.median(trained_aucs)) if trained_aucs else None,
            "naive_median": float(np.median(naive_aucs)) if naive_aucs else None,
        },
        "per_player": per_player_table,
        "multigame": multigame,
        "direction": direction_summary,
        "trained_flat": trained_flat,
        "trained_selective": trained_selective,
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        f.write("const CHEAT_DATA = ")
        json.dump(out, f)
        f.write(";")
    print(f"Wrote {out_path} ({n_players} players)")


if __name__ == "__main__":
    main()
