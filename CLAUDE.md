# Project context

## What this is

A personalized chess clock-management analyzer. It models, for a specific player,
the expected relationship between **position complexity** and **time spent per move**,
then flags where that player's actual clock usage diverged in ways that cost them.

Primary user is a strong bullet player who wants actionable feedback, not generic
"spend less time in the opening" advice.

## Why it's built this way

Existing chess coaching tools (Aimchess, Knightly, Sensei, Chessy) all do
"analyze your games, find your weaknesses, suggest training." None of them model
*clock allocation as a function of position difficulty, personalized per player.*
That gap is the whole point. Do not drift toward rebuilding a generic coaching app.

## Phase 2 (do not build yet, but don't foreclose it)

This same per-player baseline infrastructure is the foundation for a single-game
cheat-detection model that combines:
- deviation from the player's personal time-vs-complexity baseline
- human-move-likelihood (Maia: github.com/CSSLab/maia-chess)
- difficulty-conditioned move quality (Regan-style: probability a player of skill S
  finds move M given position difficulty D)
- personalized style deviation (Maia fine-tuned on the individual player)

Design decisions in Phase 1 should keep feature extraction and baseline-deviation
scoring reusable and player-agnostic. Don't hardcode one username anywhere.

Known constraint from the literature: single-game detection of a *selective* cheater
(engine on 1-3 critical moves) is near chance from move quality alone. Timing is an
additional signal, not a silver bullet, and is spoofable by a deliberate adversary.
Any Phase 2 evaluation must use injected/simulated cheat moves with known ground
truth, never real accused players.

## Data source

Primary source is **Chess.com** — that's where the player's game volume is.
Lichess support exists but the sample there is much smaller; use it only for
cross-source validation, never as the primary training set.

Do not naively pool the two sources. Rating scales and player pools differ
between sites, so a Chess.com 2000 is not a Lichess 2000. The parser tags rows
with a `source` column; if pooling, include it as a feature, or fit separate
baselines per source.

Chess.com API gotchas (already handled in fetch_chesscom.py, don't regress them):
  - A descriptive User-Agent is required or you get 403
  - Monthly archives must be fetched serially; parallel requests get 429
  - TimeControl omits increment when zero ("60" not "60+0")
  - Game ID lives in the `Link` header, not `Site`
  - Daily/correspondence games have day-scale clocks — filtered out, keep it that way

## Architecture

```
src/
  fetch_chesscom.py # Chess.com monthly archives -> raw PGN  (PRIMARY)
  fetch_games.py   # Lichess API -> raw PGN with clock timestamps
  parse_pgn.py     # PGN -> per-ply records (FEN, move, clock delta)
  annotate.py      # Stockfish MultiPV -> evals, cp loss, best/2nd gap
  features.py      # per-ply position complexity features
  model.py         # per-player baselines (think-time, move-quality)
  insights.py      # bucket moves: overthought / underthought / panic / clean
data/
  raw/             # PGN dumps
  parsed/          # parquet, per-ply records
  annotated/       # parquet, + engine evals
```

Data flows one direction through those stages. Every stage caches to Parquet so
re-running a later stage never re-triggers engine analysis.

## The two models

1. **Expected think-time**: `time_spent ~ complexity + clock_state`
2. **Expected move quality**: `cp_loss ~ complexity + time_spent`

Both gradient-boosted trees (LightGBM). Interpretability matters more than raw
accuracy here — SHAP values feed the user-facing explanations and Phase 2 scoring.

## Insight buckets

- **Overthought**: time >> personal baseline, low criticality, cp_loss fine
- **Underthought**: time << baseline, high criticality, eval dropped
- **Panic**: high time AND high cp_loss (time didn't convert)
- **Clean**: allocation roughly matched criticality

## Critical gotcha — validate this FIRST

Bullet clock data is noisy, in two distinct ways — don't conflate them:
  - **Premoves/instant moves**: near-zero think time, no real decision happened.
  - **Lag spikes**: implausibly *large* think time from a connection stall or
    reconnect, not genuine long thought. Flagged via `lag_suspect` in
    parse_pgn.py (heuristic — there's no server-side ping log available, so this
    can't be proven, only estimated by a large-multiple-of-time-control test).

Both get flagged, not silently dropped. Read the breakdown parse_pgn.py prints
before trusting anything downstream.

## Skill drift — a confound to model, not just filter out

Games likely span a period where the player actually got better. Two things
follow, both implemented in features.py:
  - `player_rating` is already a per-game covariate — include it in both
    models so they condition on skill level at the time of that game.
  - `recency_weight` (exponential decay, see RECENCY_HALFLIFE_DAYS) — use as
    sample_weight when fitting the "what should I do right now" model, so a
    year-old game doesn't count as much as last week's. Use uniform weight
    only for the separate descriptive question of "how has my time management
    evolved over time" — that's a real, useful insight in its own right, not
    just noise to control for.

Run `python -m src.diagnostics` before deciding whether one pooled model is
defensible or whether a trailing-window baseline is needed instead. Don't
assume — check the printed quartile breakdown.

## Conventions

- Python 3.11+, `python-chess` for parsing, `stockfish` binary via UCI
- Parquet (pyarrow) for all intermediate data, never CSV
- Engine analysis is the expensive step: validate the full pipeline at low depth
  (depth 12) before committing to a deep pass (depth 18-20)
- No secrets in the repo; Lichess personal token via env var `LICHESS_TOKEN`
- Never hardcode a username — everything takes `--player` as a parameter

## Sanity check loop

The developer is an experienced chess player. When the model classifies a move,
spot-check it against games they actually remember. If the model says "overthought"
on a move that was genuinely critical, the complexity features are wrong — fix the
features, don't tune the model.

## Project log

See [PROJECT_LOG.md](PROJECT_LOG.md) for the running record of design decisions,
findings, and rationale — read it first if picking this project up cold or after
a compressed conversation. Keep it updated as work progresses; it's the durable
memory of *why* things are the way they are, not just what the code does.
