#!/usr/bin/env python3
"""Perft cross-check: the engine's own console `perft N` (after the `verify` self-check) against python-chess, move by move (divide), on 400 positions:
8 fixed perft positions at depth 3 + 392 random-playout positions at depth 2.  Catches any move-generation / make-unmake bug (castling, e.p., promotion, pins).
Then the six standard perft positions at depth 4 to 6 against their published node counts (Chess Programming Wiki, "Perft Results"): bugs that
need deeper trees (an en-passant capture that uncovers a check, castling rights lost in a capture sequence ...) show there.
  ENGINE=./zeval python3 tests/t_perft.py          (Windows: set ENGINE=zeval.exe)        ~ 25 s.  Needs python-chess.
Exit code 0 = no mismatch, 1 = mismatches, 2 = the engine could not be run.  """
import chess, random, subprocess, sys
from _eng import ENGINE, resolve, PipeReader
random.seed(11)
def perft_div(b, d):
    res = {}
    for m in b.legal_moves:
        b.push(m)
        res[m.uci()] = 1 if d == 1 else cnt(b, d-1)
        b.pop()
    return res
def cnt(b, d):
    if d == 0: return 1
    n = 0
    for m in b.legal_moves:
        b.push(m); n += 1 if d == 1 else cnt(b, d-1); b.pop()
    return n
def engine_div(fen, d):
    inp = f"verify\nposition fen {fen}\nperft {d}\nq\n"
    try:
        out = subprocess.run(ENGINE, input=inp, capture_output=True, text=True, timeout=120).stdout
    except subprocess.TimeoutExpired:
        return None                                      # a hung engine counts as a mismatch ("verify failed / no answer")
    except OSError as e:
        sys.stderr.write(f"ERROR: cannot run the engine {ENGINE}: {e}\n"); sys.exit(2)
    if "VERIFY FAILED" in out: return None
    r = {}
    for line in out.splitlines():
        k, sep, v = line.rpartition(": ")
        if sep and not line.startswith("Nodes") and v.strip().isdigit(): r[k] = int(v)
    return r
pre = engine_div("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", 1)       # pre-flight: the console command perft must work
if not pre or len(pre) != 20:
    sys.stderr.write(f"ERROR: the engine {' '.join(ENGINE)} does not answer the console command 'perft' (wrong path or not a Zeval engine)\n"); sys.exit(2)
bad = 0; N = 400
extra = [
 "r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1",
 "8/2p5/3p4/KP5r/1R3p1k/8/4P1P1/8 w - - 0 1",
 "r3k2r/Pppp1ppp/1b3nbN/nP6/BBP1P3/q4N2/Pp1P2PP/R2Q1RK1 w kq - 0 1",
 "rnbq1k1r/pp1Pbppp/2p5/8/2B5/8/PPP1NnPP/RNBQK2R w KQ - 1 8",
 "r4rk1/1pp1qppp/p1np1n2/2b1p1B1/2B1P1b1/P1NP1N2/1PP1QPPP/R4RK1 w - - 0 10",
 "8/P1k5/K7/8/8/8/8/8 w - - 0 1","4k3/8/8/8/8/8/4P3/4K3 w - - 0 1",
 "n1n5/PPPk4/8/8/8/8/4Kppp/5N1N b - - 0 1",
]
fens = list(extra)
while len(fens) < N:
    b = chess.Board()
    for _ in range(random.randint(0, 90)):
        mv = list(b.legal_moves)
        if not mv: break
        b.push(random.choice(mv))
    if not b.is_game_over(): fens.append(b.fen())
for i, f in enumerate(fens):
    d = 3 if i < len(extra) else 2
    exp = perft_div(chess.Board(f), d); got = engine_div(f, d)
    if got != exp:
        bad += 1
        if bad <= 5:
            print("MISMATCH", f, d)
            if got is None: print("  verify failed")
            else: print("  missing", sorted(set(exp)-set(got))[:6], "extra", sorted(set(got)-set(exp))[:6],
                        "cnt-diff", [(k, exp[k], got[k]) for k in exp if k in got and exp[k]!=got[k]][:6])
print(f"perft cross-check: {bad} mismatches / {len(fens)} positions")
KNOWN = [("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", 5, 4865609),
         ("r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1", 4, 4085603),
         ("8/2p5/3p4/KP5r/1R3p1k/8/4P1P1/8 w - - 0 1", 6, 11030083),
         ("r3k2r/Pppp1ppp/1b3nbN/nP6/BBP1P3/q4N2/Pp1P2PP/R2Q1RK1 w kq - 0 1", 4, 422333),
         ("rnbq1k1r/pp1Pbppp/2p5/8/2B5/8/PPP1NnPP/RNBQK2R w KQ - 1 8", 4, 2103487),
         ("r4rk1/1pp1qppp/p1np1n2/2b1p1B1/2B1P1b1/P1NP1N2/1PP1QPPP/R4RK1 w - - 0 10", 4, 3894594)]
def engine_total(fen, d):
    """'Nodes searched' of a plain perft (without the 'verify' self-check, which makes deep trees very slow)"""
    try:
        out = subprocess.run(ENGINE, input=f"position fen {fen}\nperft {d}\nq\n", capture_output=True, text=True, timeout=300).stdout
    except subprocess.TimeoutExpired:
        return None
    m = [l for l in out.splitlines() if l.startswith("Nodes searched:")]
    return int(m[-1].split(":")[1]) if m else None
kbad = 0
for f, d, want in KNOWN:
    total = engine_total(f, d)
    if total != want:
        kbad += 1; print("MISMATCH known perft", f, "depth", d, "engine", total, "published", want)
print(f"published perft totals: {kbad} mismatches / {len(KNOWN)} positions")
sys.exit(1 if bad or kbad else 0)
