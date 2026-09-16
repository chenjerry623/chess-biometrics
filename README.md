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

