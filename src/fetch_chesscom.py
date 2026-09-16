"""Fetch a player's games from the Chess.com public API.

Differences from Lichess that actually matter:

  1. No single export endpoint. You list monthly archives, then pull each month.
  2. A descriptive User-Agent is REQUIRED. Chess.com blocks the default
     requests UA with 403. This is the #1 reason people think the API is down.
  3. No auth needed for public games, but requests must be serial — parallel
     hammering gets you 429'd.
  4. Clock comments are present in live-game PGN by default (no flag needed),
     but daily/correspondence games use day-scale clocks and are useless here.
     We filter to live time classes.
  5. TimeControl header format differs: "60" means 60s with no increment,
     "180+2" means 3+2. Lichess always writes "base+inc".

Usage:
    python -m src.fetch_chesscom --player YOURNAME --class bullet
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import requests

ARCHIVES_URL = "https://api.chess.com/pub/player/{username}/games/archives"
RAW_DIR = Path("data/raw")

# Chess.com requires a descriptive UA with contact info. Change this to yours.
HEADERS = {
    "User-Agent": "chess-clock-analyzer/0.1 (personal research project; contact: chenjerry623@gmail.com)"
}

LIVE_CLASSES = {"bullet", "blitz", "rapid"}


def list_archives(username: str) -> list[str]:
    resp = requests.get(
        ARCHIVES_URL.format(username=username.lower()),
        headers=HEADERS,
        timeout=30,
    )
    if resp.status_code == 403:
        raise SystemExit(
            "403 from Chess.com — this is almost always the User-Agent.\n"
            "Edit HEADERS in this file to include a real contact string."
        )
    if resp.status_code == 404:
        raise SystemExit(f"No such Chess.com user: {username}")
    resp.raise_for_status()
    return resp.json().get("archives", [])


def fetch_month(url: str, retries: int = 3) -> list[dict]:
    """Fetch one monthly archive. Backs off on 429."""
    for attempt in range(retries):
        resp = requests.get(url, headers=HEADERS, timeout=60)
        if resp.status_code == 429:
            wait = 5 * (attempt + 1)
            print(f"    rate limited, sleeping {wait}s")
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json().get("games", [])
    print(f"    giving up on {url}")
    return []


def fetch_games(
    username: str,
    time_class: str = "bullet",
    rated_only: bool = True,
    max_games: int | None = None,
    months_back: int | None = None,
) -> Path:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RAW_DIR / f"{username}_chesscom_{time_class}.pgn"

    archives = list_archives(username)
    if not archives:
        raise SystemExit("No archives returned — is the account public?")

    if months_back:
        archives = archives[-months_back:]

    print(f"Found {len(archives)} monthly archives for {username}")

    kept = 0
    skipped_no_clock = 0

    with out_path.open("w", encoding="utf-8") as fh:
        for i, url in enumerate(archives, 1):
            month = "/".join(url.split("/")[-2:])
            games = fetch_month(url)
            month_kept = 0

            for g in games:
                if g.get("time_class") != time_class:
                    continue
                if g.get("time_class") not in LIVE_CLASSES:
                    continue
                if rated_only and not g.get("rated", False):
                    continue
                if g.get("rules") != "chess":  # skip chess960, bughouse etc.
                    continue

                pgn = g.get("pgn")
                if not pgn:
                    continue
                # Without clock comments this row is worthless to us.
                if "[%clk" not in pgn:
                    skipped_no_clock += 1
                    continue

                fh.write(pgn.rstrip() + "\n\n")
                kept += 1
                month_kept += 1

                if max_games and kept >= max_games:
                    print(f"Hit max_games={max_games}, stopping")
                    print(f"Wrote {kept} games to {out_path}")
                    return out_path

            print(f"  [{i}/{len(archives)}] {month}: +{month_kept} (total {kept})")
            time.sleep(0.4)  # be polite; serial requests only

    if skipped_no_clock:
        print(f"Skipped {skipped_no_clock} games with no clock data")
    print(f"\nWrote {kept} games to {out_path}")
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--player", required=True, help="Chess.com username")
    parser.add_argument(
        "--class",
        dest="time_class",
        default="bullet",
        choices=sorted(LIVE_CLASSES),
    )
    parser.add_argument("--max", type=int, default=None, dest="max_games")
    parser.add_argument(
        "--months",
        type=int,
        default=None,
        dest="months_back",
        help="only the N most recent months (useful for a quick smoke test)",
    )
    parser.add_argument("--include-unrated", action="store_true")
    args = parser.parse_args()

    fetch_games(
        username=args.player,
        time_class=args.time_class,
        rated_only=not args.include_unrated,
        max_games=args.max_games,
        months_back=args.months_back,
    )


if __name__ == "__main__":
    main()
