#!/usr/bin/env python3
"""Determinism / regression check between two builds of the engine (e.g. Linux ELF vs Windows exe).

For a set of positions it runs `go depth N` and records, per iteration, the score, node count and
PV plus the final bestmove.  Time/nps fields are ignored (they differ by machine).  Single-threaded,
fixed-depth search is deterministic, so two correct builds must print IDENTICAL records.

  python3 tests/t_identical.py "./zeval" "wine zeval.exe" [--positions 60] [--depth 9] [--seed 7]
Each engine is a path (spaces are fine) or a command line such as "wine zeval.exe".
Needs python-chess for more than the 10 built-in positions (without it only those 10 run; the script says so).
Exit code 0 = identical, 1 = different, 2 = an engine crashed / hung / was not found (one ERROR line).
Two engines that both produce nothing usable do not count as "identical" (0 compared nodes = exit 2).
"""
import subprocess, sys, random, re, argparse, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _eng import resolve, expect, finish, start, EngineGone
try:
    import chess
except ImportError:
    chess = None

ap = argparse.ArgumentParser()
ap.add_argument("a"); ap.add_argument("b")
ap.add_argument("--positions", type=int, default=60)
ap.add_argument("--depth", type=int, default=9)
ap.add_argument("--seed", type=int, default=7)
ap.add_argument("--timeout", type=float, default=600, help="seconds one search may take before the engine counts as hung")
args = ap.parse_args()

FENS = [
 "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
 "r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1",
 "8/2p5/3p4/KP5r/1R3p1k/8/4P1P1/8 w - - 0 1",
 "r3k2r/Pppp1ppp/1b3nbN/nP6/BBP1P3/q4N2/Pp1P2PP/R2Q1RK1 w kq - 0 1",
 "rnbq1k1r/pp1Pbppp/2p5/8/2B5/8/PPP1NnPP/RNBQK2R w KQ - 1 8",
 "r4rk1/1pp1qppp/p1np1n2/2b1p1B1/2B1P1b1/P1NP1N2/1PP1QPPP/R4RK1 w - - 0 10",
 "6k1/5ppp/8/8/8/8/5PPP/3R2K1 w - - 0 1",
 "8/8/8/4k3/8/8/4P3/4K3 w - - 0 1",
 "4k3/8/8/8/8/8/4P3/4K3 b - - 0 1",
 "r1bqkb1r/pppp1ppp/2n2n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR w KQkq - 4 4",
]
def positions():
    random.seed(args.seed)
    out = list(FENS)
    if chess is None:
        if args.positions > len(out):
            print(f"NOTE: python-chess is not installed: only the {len(out)} built-in positions run (--positions {args.positions} ignored)")
        return out
    while len(out) < args.positions:
        b = chess.Board()
        for _ in range(random.randint(6, 70)):
            mv = list(b.legal_moves)
            if not mv: break
            b.push(random.choice(mv))
        if not b.is_game_over():
            out.append(b.fen())
    return out

def run(cmd, fens):
    p = start(resolve(cmd))
    def send(s): p.stdin.write(s + "\n"); p.stdin.flush()
    res = []
    try:
        send("uci"); expect(p, "uciok", 30, what=cmd)
        for fen in fens:
            send("ucinewgame"); send("isready"); expect(p, "readyok", 60, what=cmd)
            send(f"position fen {fen}"); send(f"go depth {args.depth}")
            lines = []; expect(p, "bestmove", args.timeout, collect=lines, what=cmd)
            rec = []
            for l in lines:
                if l.startswith("info"):
                    m = re.search(r"depth (\d+).*?score (\w+) (-?\d+).*?nodes (\d+).*? pv (.*)", l)
                    if m: rec.append(f"d{m[1]} {m[2]} {m[3]} n{m[4]} {m[5]}")
                elif l.startswith("bestmove"):
                    rec.append(l)
            res.append(rec)
        send("quit"); finish(p, 30)
    finally:
        if p.poll() is None: p.kill()
    return res

fens = positions()
ra, rb = run(args.a, fens), run(args.b, fens)
bad = 0; nodes = 0
for fen, x, y in zip(fens, ra, rb):
    nodes += sum(int(re.search(r" n(\d+)", s)[1]) for s in x if " n" in s and s.startswith("d"))
    if x != y:
        bad += 1
        print("MISMATCH", fen); print("  A:", x[-3:]); print("  B:", y[-3:])
print(f"{len(fens)} positions, depth {args.depth}, {nodes} nodes compared, mismatches: {bad}")
if nodes == 0:
    print("ERROR: no search output could be compared (no 'info depth .. score .. nodes .. pv' lines): not a valid result", file=sys.stderr)
    sys.exit(2)
sys.exit(1 if bad else 0)
