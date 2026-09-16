# Proof

The numbers on the dashboard and in the README aren't hand-picked. This is the real, unedited console output from
running the exact production pipeline (`cloud/remote_export_aws.py`, unmodified) on a fresh AWS instance. The full
raw log is committed at [`proof/export_2026-09-15.log`](proof/export_2026-09-15.log) — nothing here is reformatted
or invented, it's copied directly out of that file.

## The identification model comparison

Three model families, cross-validated, best one on held-out data wins. Never assumed in advance.

```
Building identification demo: cohort=primary (full)
    [primary] logistic regression balanced acc: 0.882 | HGB: 0.918 | LightGBM: 0.916
    [primary] rank-1/3/5: 0.920, 1.000 | multi-game (1/5/20 games): 0.920, 0.999, 1.000
Building identification demo: cohort=primary_biometric (biometric-only)
    [primary_biometric] logistic regression balanced acc: 0.787 | HGB: 0.861 | LightGBM: 0.863
    [primary_biometric] rank-1/3/5: 0.864, 1.000 | multi-game (1/5/20 games): 0.864, 0.992, 1.000
Building identification demo: cohort=pool (full)
    [pool] logistic regression balanced acc: 0.221 | HGB: 0.273 | LightGBM: 0.293
    [pool] rank-1/3/5: 0.299, 0.512, 0.622, 0.764 | multi-game (1/5/20 games): 0.299, 0.625, 0.938
Building identification demo: cohort=pool_biometric (biometric-only)
    [pool_biometric] logistic regression balanced acc: 0.154 | HGB: 0.233 | LightGBM: 0.249
    [pool_biometric] rank-1/3/5: 0.249, 0.450, 0.559, 0.713 | multi-game (1/5/20 games): 0.249, 0.556, 0.896
```

`pool_biometric` is the site's default (126 players, timing/session habits only, no move choice). LightGBM wins
that comparison at 24.9% single-game balanced accuracy, climbing to 89.6% at 20 games, exactly what's shown on the
live dashboard.

## Two things worth noticing in the raw log

- **The "confidence-weighted" and "session-selected" fusion experiments actually hurt accuracy on the full
  pool** (negative deltas across the board, e.g. `pool_biometric` at 20 games: `-0.226`). Both were tried as ways
  to combine multiple games smarter than a plain average, and both made things worse at scale. That's a real,
  measured negative result, not something left out of the write-up.
- **`Building profile: X / bullet` appears once per player** (127 times) as the pipeline processes each account
  individually. The full log has all of them; this page only shows the model-comparison lines for readability.

## How to reproduce this yourself

```bash
git clone https://github.com/chenjerry623/chess-biometrics.git
cd chess-biometrics
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Fetch a player's games, then run the pipeline stages in order (see README.md).
# The identification model itself:
python -m scripts.export_dashboard_data
```

Needs real memory for the 126-class comparison (this project moved it to a `r6i.2xlarge` on AWS after hitting an
OOM kill locally — see PROJECT_LOG.md). For a faster, laptop-sized check of the same methodology on a simpler
2-player case:

```bash
python -m scripts.pairwise_identification --n-pairs 10 --feature-mode biometric
```

Runs in under a minute and prints real per-pair accuracy plus a median/min/max summary, matching the methodology
behind the "two players" section of the dashboard.
