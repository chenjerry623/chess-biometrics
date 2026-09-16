# Proof

The numbers on the [dashboard](https://github.com/chenjerry623/chess-biometrics) and in the README aren't
hand-picked. Here's where they come from and how to check them yourself.

## What's running right now

A fresh run of the exact production pipeline (`cloud/remote_export_aws.py`, unmodified) is in progress as of this
commit, specifically to capture its real, unedited console output for this file. That output will replace this
section once the run finishes — it takes 35-50 minutes on a `r6i.2xlarge` instance for the full 126-class
identification comparison.

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

That prints lines like:

```
[pool_biometric] logistic regression balanced acc: 0.787 | HGB: 0.863 | LightGBM: 0.865
```

for each cohort — three models, cross-validated, whichever wins on held-out data is the one that ships. No model is
assumed in advance.

For a faster, smaller-scale check that doesn't need AWS-level memory:

```bash
python -m scripts.pairwise_identification --n-pairs 10 --feature-mode biometric
```

Runs in under a minute and prints real per-pair accuracy plus a median/min/max summary, matching the methodology
behind the "two players" section of the dashboard.
