"""Annotate positions with Stockfish MultiPV evaluations.

This is the expensive stage. Design principles:
  1. Resumable — never lose hours of engine time to a crash.
  2. Depth-parameterized — validate the whole pipeline at depth 12 before
     committing to a deep pass.
  3. Deduplicated by FEN — transpositions and repeated openings are free.

The MultiPV output is what powers the criticality feature: the eval gap between
the best and second-best move tells you whether this position had an only-move
(expensive to get wrong) or many equivalent options (cheap).

Usage:
    python -m src.annotate --plies data/parsed/<user>_plies.parquet --depth 12
"""

from __future__ import annotations

import argparse
import multiprocessing as mp
from pathlib import Path

import chess
import chess.engine
import pandas as pd

ANNOTATED_DIR = Path("data/annotated")
MULTIPV = 4
MATE_SCORE_CP = 10_000
# Roughly how many positions one chunk/checkpoint should cover — bounds
# how much work a Spot interruption can lose, regardless of worker count.
# See the comment at its use site (a real 1.94M-position job lost 100% of
# its progress to a Spot reclaim before this existed, one-chunk-per-worker
# meant each chunk was hours of work).
CHECKPOINT_CHUNK_TARGET = 600

# What analyse_fens() currently produces per position. Checked against any
# existing output/checkpoints before resuming — this exact class of bug
# (silently resuming from data that predates a schema change: a new column
# added to this module, or upstream in parse_pgn.py) has hit this project
# multiple times in practice (utc_time, opposite_side_castling, the
# search-instability fields themselves all needed a redo after being added
# without a full re-run). A loud warning here is cheap; a silently
# incomplete resume is not.
EXPECTED_COLUMNS = {
    "fen_before", "eval_before", "best_move", "gap_1_2", "top_n_spread",
    "top_moves", "top_cps", "top_mate_ins", "depth", "search_depth_reached",
    "search_stable_depth", "n_best_move_changes", "eval_swing_across_depth",
}


def _check_schema(df: pd.DataFrame, source: str) -> None:
    missing = EXPECTED_COLUMNS - set(df.columns)
    if missing:
        print(
            f"WARNING: {source} is missing columns the current annotate.py "
            f"produces: {sorted(missing)}. Resuming from it will silently carry "
            f"those positions forward without them (NaN after the merge in "
            f"features.py), not backfill them. If annotate.py's schema changed "
            f"since this was written, delete/rename it and re-annotate fresh "
            f"instead of resuming."
        )


def _score_to_cp(score: chess.engine.PovScore, pov: chess.Color) -> float:
    """Convert an engine score to centipawns from `pov`'s perspective."""
    rel = score.pov(pov)
    if rel.is_mate():
        mate_in = rel.mate()
        # Preserve sign; nearer mates score more extreme.
        return float(MATE_SCORE_CP - abs(mate_in) * 100) * (1 if mate_in > 0 else -1)
    return float(rel.score())


def _mate_in(score: chess.engine.PovScore, pov: chess.Color) -> int | None:
    """Raw mate distance in plies from `pov`'s perspective, or None if not mate.

    Kept separate from _score_to_cp's proxy value so downstream code can tell
    a real forced mate apart from a merely large centipawn score.
    """
    rel = score.pov(pov)
    return rel.mate() if rel.is_mate() else None


def analyse_fens(
    fens: list[str],
    engine_path: str,
    depth: int,
    threads: int = 1,
    hash_mb: int = 256,
) -> list[dict]:
    """Analyse a list of FENs. Runs in a worker process.

    Tracks not just the final-depth MultiPV lines but how the top line's
    move/eval evolved across the whole iterative-deepening search. Research
    on move difficulty points to search-instability (did the "best" move
    keep flipping on the way to full depth?) as a better "how hard is this
    to find" proxy than a single fixed-depth snapshot — two positions can
    have an identical depth-12 eval while one was obvious from depth 4
    onward and the other kept changing its mind until the last iteration.
    """
    results: list[dict] = []
    with chess.engine.SimpleEngine.popen_uci(engine_path) as engine:
        engine.configure({"Threads": threads, "Hash": hash_mb})

        for fen in fens:
            board = chess.Board(fen)
            mover = board.turn

            # Latest line seen per MultiPV rank, updated as the search
            # deepens (so by the end this holds the same final-depth data
            # the old single-shot analyse() call returned).
            latest_by_rank: dict[int, dict] = {}
            # The top-ranked (rank 1) line's move/eval at each depth actually
            # reached, so we can measure how much it flip-flopped en route.
            top_move_by_depth: dict[int, str] = {}
            top_cp_by_depth: dict[int, float] = {}

            try:
                with engine.analysis(
                    board, chess.engine.Limit(depth=depth), multipv=MULTIPV
                ) as analysis:
                    for info in analysis:
                        d = info.get("depth")
                        rank = info.get("multipv", 1)
                        pv = info.get("pv")
                        score = info.get("score")
                        if d is None or not pv or score is None:
                            continue
                        latest_by_rank[rank] = {
                            "move": pv[0].uci(),
                            "cp": _score_to_cp(score, mover),
                            "mate_in": _mate_in(score, mover),
                        }
                        if rank == 1:
                            top_move_by_depth[d] = pv[0].uci()
                            top_cp_by_depth[d] = _score_to_cp(score, mover)
            except chess.engine.EngineError:
                continue

            if not latest_by_rank:
                continue

            # MultiPV's return order isn't always already sorted near forced
            # mates (a root line that runs into a forcing mate sequence gets
            # search extensions past sibling lines at nominal depth, which can
            # leave a "worse" line ranked ahead of a "better" one). Sort
            # explicitly rather than trusting engine order.
            lines = sorted(latest_by_rank.values(), key=lambda ln: ln["cp"], reverse=True)
            cps = [ln["cp"] for ln in lines]

            depths_seen = sorted(top_move_by_depth)
            n_best_move_changes = sum(
                1
                for a, b in zip(depths_seen, depths_seen[1:])
                if top_move_by_depth[a] != top_move_by_depth[b]
            )
            final_move = top_move_by_depth[depths_seen[-1]] if depths_seen else None
            stable_depth = depths_seen[-1] if depths_seen else depth
            for d in reversed(depths_seen):
                if top_move_by_depth[d] != final_move:
                    break
                stable_depth = d
            top_cp_seq = [top_cp_by_depth[d] for d in depths_seen]
            eval_swing = (max(top_cp_seq) - min(top_cp_seq)) if len(top_cp_seq) > 1 else 0.0

            results.append(
                {
                    "fen_before": fen,
                    "eval_before": cps[0],
                    "best_move": lines[0]["move"],
                    # criticality: how much worse is the 2nd-best option?
                    "gap_1_2": (cps[0] - cps[1]) if len(cps) > 1 else None,
                    # volatility: spread across all considered options
                    "top_n_spread": (max(cps) - min(cps)) if len(cps) > 1 else None,
                    "top_moves": [ln["move"] for ln in lines],
                    "top_cps": cps,
                    "top_mate_ins": [ln["mate_in"] for ln in lines],
                    "depth": depth,
                    # search-instability: how hard was THIS to find, distinct
                    # from how good/bad the final answer turned out to be.
                    "search_depth_reached": depths_seen[-1] if depths_seen else depth,
                    "search_stable_depth": stable_depth,
                    "n_best_move_changes": n_best_move_changes,
                    "eval_swing_across_depth": eval_swing,
                }
            )
    return results


def _chunk(seq: list, n: int) -> list[list]:
    size = max(1, (len(seq) + n - 1) // n)
    return [seq[i : i + size] for i in range(0, len(seq), size)]


def _analyse_chunk(args: tuple[list[str], str, int]) -> list[dict]:
    """Unpack-and-call wrapper so imap_unordered (single-arg only) can drive
    analyse_fens, which needs several — see annotate() for why unordered
    completion matters here."""
    fens, engine_path, depth = args
    return analyse_fens(fens, engine_path, depth)


def _partial_dir(out: Path) -> Path:
    return out.parent / f".{out.stem}_partial"


def annotate(
    plies_path: Path,
    engine_path: str,
    depth: int,
    workers: int,
    limit: int | None = None,
) -> Path:
    df = pd.read_parquet(plies_path)
    ANNOTATED_DIR.mkdir(parents=True, exist_ok=True)
    out = ANNOTATED_DIR / f"{plies_path.stem}_d{depth}.parquet"
    partial_dir = _partial_dir(out)
    partial_dir.mkdir(parents=True, exist_ok=True)
    partial_files = sorted(partial_dir.glob("chunk_*.parquet"))

    # Resume: skip FENs already analysed at this depth, whether they made it
    # into the consolidated output file or are still sitting in unconsolidated
    # per-chunk checkpoints from a run that got interrupted (a killed
    # process, a reclaimed Spot instance) before it could consolidate.
    done: set[str] = set()
    if out.exists():
        existing = pd.read_parquet(out)
        _check_schema(existing, str(out))
        done |= set(existing["fen_before"])
    for pf in partial_files:
        existing = pd.read_parquet(pf)
        _check_schema(existing, str(pf))
        done |= set(existing["fen_before"])
    if done:
        print(f"Resuming — {len(done)} positions already analysed at depth {depth}")

    fens = sorted(set(df["fen_before"]) - done)
    if limit:
        fens = fens[:limit]

    if not fens:
        print("Nothing new to analyse — consolidating any leftover checkpoints.")
    else:
        print(f"Analysing {len(fens)} unique positions at depth {depth} on {workers} workers")
        print("(unique FENs only — transpositions are free)")

        # Chunk count scales with the WORK, not just the worker count — one
        # chunk per worker is fine for a small job, but for a job with
        # hundreds of thousands of positions it means each chunk is itself
        # hours of work, and a checkpoint only lands once a whole chunk
        # finishes. Confirmed the hard way: a 1.94M-position job on 16
        # workers got Spot-reclaimed ~50 min in with EVERY chunk (~121K
        # positions each) still in progress — zero checkpoints saved,
        # nothing to resume from. CHECKPOINT_CHUNK_TARGET below bounds how
        # much work a single checkpoint interval can lose, independent of
        # how many workers are running.
        n_chunks = max(workers, len(fens) // CHECKPOINT_CHUNK_TARGET)
        chunks = _chunk(fens, n_chunks)
        tasks = [(c, engine_path, depth) for c in chunks]
        next_chunk_id = len(partial_files)
        with mp.Pool(workers) as pool:
            # imap_unordered (not starmap) so each chunk is checkpointed to
            # disk as soon as IT finishes, not only after every worker does —
            # a mid-run interruption then loses at most one chunk's worth of
            # work, not the whole run.
            for i, chunk_result in enumerate(pool.imap_unordered(_analyse_chunk, tasks)):
                if not chunk_result:
                    continue
                chunk_path = partial_dir / f"chunk_{next_chunk_id + i}.parquet"
                pd.DataFrame(chunk_result).to_parquet(chunk_path, index=False)
                print(f"  checkpointed chunk {i + 1}/{len(tasks)} ({len(chunk_result)} positions) -> {chunk_path.name}")

    # Consolidate every checkpoint (this run's plus any left over from a
    # prior interrupted run) into the single output file, then clean up.
    partial_files = sorted(partial_dir.glob("chunk_*.parquet"))
    parts = [pd.read_parquet(out)] if out.exists() else []
    parts += [pd.read_parquet(pf) for pf in partial_files]
    if not parts:
        print("Nothing to do.")
        return out

    new_df = pd.concat(parts, ignore_index=True).drop_duplicates("fen_before", keep="last")
    new_df.to_parquet(out, index=False)
    for pf in partial_files:
        pf.unlink()
    partial_dir.rmdir()

    print(f"Wrote {len(new_df)} annotated positions to {out}")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plies", required=True, type=Path)
    parser.add_argument("--engine", default="stockfish", help="path to stockfish binary")
    parser.add_argument(
        "--depth",
        type=int,
        default=12,
        help="START AT 12 to validate the pipeline. Go deeper only once it works.",
    )
    parser.add_argument("--workers", type=int, default=max(1, mp.cpu_count() - 1))
    parser.add_argument("--limit", type=int, default=None, help="cap positions (smoke test)")
    args = parser.parse_args()

    annotate(args.plies, args.engine, args.depth, args.workers, args.limit)


if __name__ == "__main__":
    main()
