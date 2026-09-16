"""Annotate positions with Maia human-move-likelihood policy distributions.

Same resumability principles as annotate.py, adapted for Maia:
  1. Resumable — never lose engine time to a crash or Spot interruption.
  2. Deduplicated by (FEN, rating bin) — a position is only ever queried
     once per rating bin, however many times it recurs (transpositions,
     multiple games) or whichever player it came from.
  3. One process per rating bin, not per chunk of FENs — reloading a
     ~1.3MB Maia weights file per position would dwarf the actual query
     cost (nodes=1 queries are fast; process/weights startup is not).

Every ply's player_rating is snapped to the nearest available Maia bin
(1100-1900, see maia_query.nearest_rating_bin) — the SAME (fen, rating_bin)
pair recurs often within one player's history and across players at similar
strength, so caching by that pair (not by row) avoids re-querying it.

Usage:
    python -m src.maia_annotate --plies data/parsed/<user>_plies.parquet
"""

from __future__ import annotations

import argparse
import concurrent.futures
import multiprocessing as mp
import os
import signal
from pathlib import Path

import chess
import pandas as pd

from src.maia_query import MAIA_RATING_BINS, nearest_rating_bin, open_maia_engine, query_maia_policy

MAIA_ANNOTATED_DIR = Path("data/maia_annotated")

# A single query should take ~16ms in practice (validated directly earlier
# this project). Real incident that motivated this: a run against
# jerrycdzn's data hung for 4.5 hours with under 6 minutes of actual CPU
# time used — the lc0 subprocess wedged (root cause not confirmed; possibly
# a GPU/Metal backend hiccup) and query_maia_policy() blocked forever
# waiting on a UCI response that was never coming. python-chess's
# SimpleEngine has no built-in per-call timeout, so this wraps each query
# in its own thread and force-kills the underlying engine PROCESS (not just
# the thread, which can't be killed) if it doesn't answer in time.
QUERY_TIMEOUT_S = 10
MAX_CONSECUTIVE_TIMEOUTS = 10


def _query_with_timeout(engine: "chess.engine.SimpleEngine", board: chess.Board) -> dict[str, float]:
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        future = ex.submit(query_maia_policy, engine, board)
        try:
            return future.result(timeout=QUERY_TIMEOUT_S)
        except concurrent.futures.TimeoutError:
            # The thread is still blocked on the engine's pipe and can't be
            # killed directly — kill the OS process instead, which unblocks
            # it (with an error we don't care about; the thread is
            # abandoned and cleaned up by the `with` block).
            try:
                pid = engine.protocol.transport.get_pid()
                if pid:
                    os.kill(pid, signal.SIGKILL)
            except Exception:
                pass
            raise


def _annotate_bin(args: tuple[int, list[str], str]) -> list[dict]:
    """Run in a worker process: open one Lc0 engine for this rating bin,
    query every FEN assigned to it, then close it. One process per bin
    (<=9 total) rather than per chunk of FENs — see module docstring.

    Resilient to a wedged engine (see QUERY_TIMEOUT_S above): a timed-out
    position is skipped (not silently dropped forever — it's just not in
    `done` yet, so a re-run of maia_annotate.py picks it up normally) and
    the engine is recreated before continuing. Bails out of the whole bin
    if MAX_CONSECUTIVE_TIMEOUTS happens in a row — at that point the
    problem is systemic (e.g. a broken GPU backend), not one bad position,
    and retrying forever would just repeat the original 4.5-hour hang.
    """
    rating_bin, fens, engine_path = args
    engine = open_maia_engine(rating_bin, engine_path)
    results = []
    consecutive_timeouts = 0
    try:
        for fen in fens:
            board = chess.Board(fen)
            try:
                probs = _query_with_timeout(engine, board)
                consecutive_timeouts = 0
            except Exception as e:
                consecutive_timeouts += 1
                print(
                    f"  [bin {rating_bin}] query failed/timed out on {fen} "
                    f"({type(e).__name__}, {consecutive_timeouts} in a row) — reopening engine"
                )
                try:
                    engine.close()
                except Exception:
                    pass
                if consecutive_timeouts >= MAX_CONSECUTIVE_TIMEOUTS:
                    print(f"  [bin {rating_bin}] giving up after {consecutive_timeouts} consecutive failures")
                    break
                engine = open_maia_engine(rating_bin, engine_path)
                continue
            if not probs:
                continue
            moves = list(probs.keys())
            ps = [probs[m] for m in moves]
            results.append(
                {
                    "fen_before": fen,
                    "maia_rating_bin": rating_bin,
                    "maia_moves": moves,
                    "maia_probs": ps,
                }
            )
    finally:
        try:
            engine.quit()
        except Exception:
            pass
    return results


def _partial_dir(out: Path) -> Path:
    return out.parent / f".{out.stem}_partial"


def maia_annotate(
    plies_path: Path,
    engine_path: str = "lc0",
    workers: int | None = None,
    limit: int | None = None,
) -> Path:
    df = pd.read_parquet(plies_path)
    MAIA_ANNOTATED_DIR.mkdir(parents=True, exist_ok=True)
    out = MAIA_ANNOTATED_DIR / f"{plies_path.stem}_maia.parquet"
    partial_dir = _partial_dir(out)
    partial_dir.mkdir(parents=True, exist_ok=True)
    partial_files = sorted(partial_dir.glob("bin_*.parquet"))

    done: set[tuple[str, int]] = set()
    if out.exists():
        existing = pd.read_parquet(out)
        done |= set(zip(existing["fen_before"], existing["maia_rating_bin"]))
    for pf in partial_files:
        existing = pd.read_parquet(pf)
        done |= set(zip(existing["fen_before"], existing["maia_rating_bin"]))
    if done:
        print(f"Resuming — {len(done)} (fen, rating_bin) pairs already queried")

    df = df.dropna(subset=["player_rating"]).copy()
    df["maia_rating_bin"] = df["player_rating"].apply(nearest_rating_bin)
    pairs = df[["fen_before", "maia_rating_bin"]].drop_duplicates()
    todo = pairs[~pairs.apply(lambda r: (r["fen_before"], r["maia_rating_bin"]) in done, axis=1)]

    if limit:
        todo = todo.head(limit)

    if todo.empty:
        print("Nothing new to query — consolidating any leftover checkpoints.")
    else:
        counts = todo["maia_rating_bin"].value_counts().sort_index()
        print(f"Querying {len(todo)} new (fen, rating_bin) pairs across {len(counts)} rating bins:")
        for b, n in counts.items():
            print(f"  bin {b}: {n} positions")

        tasks = [
            (int(b), group["fen_before"].tolist(), engine_path)
            for b, group in todo.groupby("maia_rating_bin")
        ]
        n_workers = workers or min(len(tasks), max(1, mp.cpu_count() - 1))
        next_id = len(partial_files)
        with mp.Pool(n_workers) as pool:
            for i, bin_result in enumerate(pool.imap_unordered(_annotate_bin, tasks)):
                if not bin_result:
                    continue
                chunk_path = partial_dir / f"bin_{next_id + i}.parquet"
                pd.DataFrame(bin_result).to_parquet(chunk_path, index=False)
                print(f"  checkpointed {chunk_path.name} ({len(bin_result)} positions)")

    partial_files = sorted(partial_dir.glob("bin_*.parquet"))
    parts = [pd.read_parquet(out)] if out.exists() else []
    parts += [pd.read_parquet(pf) for pf in partial_files]
    if not parts:
        print("Nothing to do.")
        return out

    new_df = pd.concat(parts, ignore_index=True).drop_duplicates(
        ["fen_before", "maia_rating_bin"], keep="last"
    )
    new_df.to_parquet(out, index=False)
    for pf in partial_files:
        pf.unlink()
    partial_dir.rmdir()

    print(f"Wrote {len(new_df)} (fen, rating_bin) Maia policies to {out}")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plies", required=True, type=Path)
    parser.add_argument("--engine", default="lc0", help="path to the lc0 binary")
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument("--limit", type=int, default=None, help="cap (fen, rating_bin) pairs (smoke test)")
    args = parser.parse_args()

    maia_annotate(args.plies, args.engine, args.workers, args.limit)


if __name__ == "__main__":
    main()
