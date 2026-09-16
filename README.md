# Chess Clock-Management Analyzer

Models how a specific player allocates clock time relative to position
complexity, and flags where that allocation cost them.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Stockfish
#   macOS:  brew install stockfish
#   Ubuntu: sudo apt install stockfish
stockfish --help   # confirm it's on PATH
```

## Pipeline

### 1. Fetch

Chess.com (primary source):

```bash
# IMPORTANT: edit HEADERS in src/fetch_chesscom.py with a real contact string
# first, or Chess.com returns 403.
python -m src.fetch_chesscom --player YOURNAME --class bullet

# quick smoke test on recent months only
python -m src.fetch_chesscom --player YOURNAME --class bullet --months 3
```

Lichess (optional, for cross-source validation):

```bash
export LICHESS_TOKEN=...   # optional, raises rate limit
python -m src.fetch_games --player YOURNAME --max 3000 --perf bullet
```

### 2. Parse — READ THE OUTPUT

```bash
python -m src.parse_pgn --pgn data/raw/YOURNAME_chesscom_bullet.pgn --player YOURNAME
```

Check the premove fraction and think-time distribution it prints before
continuing. This decision shapes everything downstream.

### 3. Annotate

```bash
# smoke-test 200 positions first
python -m src.annotate --plies data/parsed/YOURNAME_plies.parquet --depth 12 --limit 200

# full pass once step 3 looks right (hours — resumes if interrupted)
python -m src.annotate --plies data/parsed/YOURNAME_plies.parquet --depth 12
```

### 4. Features

```bash
python -m src.features \
  --plies data/parsed/YOURNAME_plies.parquet \
  --annotated data/annotated/YOURNAME_plies_d12.parquet
```

### 5. Check skill drift before modeling

```bash
python -m src.diagnostics --features data/features/YOURNAME_plies_features.parquet
```

Tells you whether one pooled baseline is defensible, or whether your rating/
style has shifted enough across the game history that you need a trailing-
window baseline for live recommendations instead.

## Source notes

Chess.com and Lichess differ in ways the parser handles, but which matter if
you extend it:

| | Chess.com | Lichess |
|---|---|---|
| Endpoint | monthly archives | single export stream |
| Clock data | in live-game PGN by default | needs `clocks=true` |
| TimeControl header | `"60"` (increment omitted when 0) | `"60+0"` always |
| Game ID | `Link` header | `Site` header |
| User-Agent | **required**, else 403 | not required |
| Rate limits | serial requests only | token raises limit |

The parser tags each row with a `source` column. If you combine both, treat
source as a feature — rating scales and player pools differ between sites, and
a Chess.com 2000 is not a Lichess 2000.

## Status

- [x] fetch (chess.com + lichess) / parse / annotate / features, all current
      for two players across bullet/blitz/rapid
- [x] lag-spike and skill-drift handling; premove/lag filtering validated
- [x] run diagnostics.py, decide pooled vs. windowed baseline — trailing window (default 270d) for live use, full history + `recency_weight` for descriptive
- [x] think-time baseline model (`model.py`, Model 1) — R²≈0.33-0.52 depending on player/mode, sensible SHAP features (clock state, phase, material, opponent's preceding think-time, Maia human-move-likelihood)
- [x] move-quality model (`model.py`, Model 2) — weak-to-moderate signal (R²≈0.08-0.31) even restricted to contested positions
- [x] `insights.py` — bucket scoring (Overthought/Underthought/Panic/Clean/Instinctive) built on the trained Model 1 booster's actual predictions, plus a premove behavior profile (rate/accuracy/situations, not just filtered noise)
- [x] Maia (github.com/CSSLab/maia-chess) human-move-likelihood integration (`src/maia_query.py`, `src/maia_annotate.py`) — per-move human probability from lc0, at the player's nearest rating bin
- [ ] dashboard
- [ ] multi-player identification pool (deferred — see PROJECT_LOG.md)

Known feature families beyond the original list: recapture/forced-move
"obviousness" (`is_recapture`, `is_forced`), mate-aware outcome buckets
(`outcome_bucket`, `outcome_changes`, `mate_distance_gap` — see annotate.py's
MultiPV sort fix), pawn-structure open/closed proxies (`open_files`,
`half_open_files`, `blocked_pawns`), pins/forks/skewers/discovered checks,
bishop pair and pawn weaknesses, book/theory-move and tablebase lookups,
search-instability across iterative deepening, voluntary-sacrifice detection,
a coarse `game_phase` label, `opp_think_time_prev`, session/fatigue features,
and Maia-derived human-move-likelihood (`maia_prob_played`, `maia_surprise`,
`maia_prob_best_move`, `requires_overriding_instinct`). See PROJECT_LOG.md
for the full running history.

See CLAUDE.md for design rationale and the phase 2 extension.
