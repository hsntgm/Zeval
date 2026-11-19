#!/usr/bin/env python3
"""Time allocation of a real clock (wtime / btime): the engine's soft and hard limit against a Python model of the formula, case by case.
Needs a test build that prints the limits of every clock based go:
    sh tests/build1.sh /tmp/zeval_tmc -DTMCDBG                  (the default: the game curve, TM_CURVE 1)   -> python3 tests/t_tmcurve.py /tmp/zeval_tmc
    sh tests/build1.sh /tmp/zeval_tmc0 -DTMCDBG -DTM_CURVE=0    (clock / 28 + 3/4 inc)                     -> python3 tests/t_tmcurve.py /tmp/zeval_tmc0 --curve 0
The build prints "info string tmc <gameply> <clock> <inc> <softlim> <hardlim>" right after the go; the test sends "stop" at once.

Cases (default 1500, seed fixed): random clocks from 1 ms to 2 hours, increments 0 .. 60 s, with and without movestogo, Move Overhead 0 .. 500,
positions given as startpos + moves, as FEN with fullmove numbers 1 .. 300 (White and Black to move) + moves, and as FEN without the
halfmove / fullmove fields (game ply = the moves only, whatever the position before was).  Checked: the game ply, softlim and hardlim
(TM_CURVE builds) and softlim and hardlim exactly as the model computes them, hardlim <= clock (never a move longer than the clock), softlim >= 1, hardlim >= 1.
The model is the specification: a change of the formula in src/p5_main.asm must come with the same change here.

usage:  t_tmcurve.py ENGINE [--curve 1|0] [--n 1500] [--f0 140 --f1 260 --pm 60 --h 40 --cap 10]   (the TMC_* values the build was made with)
exit codes: 0 every case matched, 1 a mismatch, 2 engine / harness (also: not a TMCDBG build)."""
import argparse, os, random, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _eng import resolve, expect, finish, start

ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
ap.add_argument("engine")
ap.add_argument("--curve", type=int, default=1, choices=(0, 1))
ap.add_argument("--n", type=int, default=1500)
ap.add_argument("--f0", type=int, default=140)
ap.add_argument("--f1", type=int, default=260)
ap.add_argument("--pm", type=int, default=60)
ap.add_argument("--h", type=int, default=40)
ap.add_argument("--cap", type=int, default=10)
args = ap.parse_args()

START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
LINE = ["e2e4", "e7e5", "g1f3", "b8c6", "f1b5", "a7a6", "b5a4", "g8f6", "e1g1", "f8e7", "f1e1", "b7b5", "a4b3", "d7d6", "c2c3", "e8g8"]
# a position after 1.e4 (Black to move) and one with White to move, both reachable by LINE moves
FEN_W = "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3"
FEN_B = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"


def ovh_adjust(x, ovh):
    r = x - 12 if x > 30 else x
    r -= ovh - 12
    r = min(r, x)
    return r if r > 0 else 1


def model(clock, inc, mtg, ovh, gply):
    if args.curve and mtg is None:
        rem = max(1, clock + inc * (args.h - 1) - ovh * args.h)
        share = args.f0 + (args.f1 - args.f0) * min(gply, args.pm) // args.pm
        t = min(rem * share // 10000, clock // args.cap)
        soft = 2 * (t if t > 0 else 1)
    else:
        m = 28 if mtg is None else min(max(mtg, 1), 50) + 1
        a = min(clock // m + inc * 3 // 4, clock // 4)
        soft = a if a > 0 else 1
    hard = ovh_adjust(min(4 * soft, clock // 2 + clock // 16), ovh)
    return soft, hard


rng = random.Random(4141)
p = start(resolve(args.engine))
def send(s): p.stdin.write(s + "\n"); p.stdin.flush()
send("uci"); expect(p, "uciok")
fails = 0; n = 0; ovh_cur = None
for i in range(args.n):
    kind = rng.choice(("startpos", "fen_w", "fen_b", "fen_nocount", "fen_big"))
    k = rng.randint(0, len(LINE))
    if kind == "startpos":
        pos, base = "position startpos" + (" moves " + " ".join(LINE[:k]) if k else ""), 0
    elif kind in ("fen_w", "fen_b"):
        fm = rng.randint(1, 300)
        f = (FEN_W if kind == "fen_w" else FEN_B).rsplit(" ", 1)[0] + " %d" % fm
        k = 0                                   # (the LINE does not fit these positions: no moves)
        pos, base = "position fen " + f, 2 * (fm - 1) + (1 if kind == "fen_b" else 0)
    elif kind == "fen_nocount":
        pos, base = "position fen " + " ".join(START.split()[:4]) + (" moves " + " ".join(LINE[:k]) if k else ""), 0
    else:
        pos, base = "position fen " + START.rsplit(" ", 1)[0] + " 99999999" + (" moves " + " ".join(LINE[:k]) if k else ""), None
    gply = None if base is None else base + k
    clock = rng.choice((rng.randint(1, 50), rng.randint(50, 3000), rng.randint(3000, 120000), rng.randint(120000, 7200000)))
    inc = rng.choice((0, 0, rng.randint(1, 100), rng.randint(100, 3000), rng.randint(3000, 60000)))
    mtg = rng.choice((None, None, None, rng.randint(0, 80)))
    ovh = rng.choice((12, 12, 0, rng.randint(1, 500)))
    if ovh != ovh_cur:
        send("setoption name Move Overhead value %d" % ovh); ovh_cur = ovh
    send(pos)
    white = (kind != "fen_b") == (k % 2 == 0)
    c = "wtime %d btime %d winc %d binc %d" % ((clock, 777, inc, 5) if white else (777, clock, 5, inc))
    send("go " + c + ("" if mtg is None else " movestogo %d" % mtg))
    l = expect(p, lambda s: s.startswith("info string tmc") or s.startswith("bestmove"))
    if l.startswith("bestmove"):
        print("ERROR: no \"info string tmc\" line: not a TMCDBG build (see the docstring)"); sys.exit(2)
    send("stop"); expect(p, "bestmove")
    g, cl, ic, soft, hard = (int(x) for x in l.split()[3:8])
    n += 1
    if base is None:
        gply = g                                 # an absurd fullmove number: only bounded, the value is not specified
        if g > 2 * 1000000 + len(LINE):
            fails += 1; print("FAIL case %d: game ply %d not bounded" % (i, g)); continue
    es, eh = model(clock, inc, mtg, ovh, gply if args.curve else 0)
    bad = []
    if not args.curve: gply = g                  # (the default build does not read the fullmove number: the ply is not used there)
    if (g, cl, ic) != (gply, clock, inc): bad.append("ply/clock/inc %s != %s" % ((g, cl, ic), (gply, clock, inc)))
    if (soft, hard) != (es, eh): bad.append("soft/hard %s != model %s" % ((soft, hard), (es, eh)))
    if hard > max(clock, 1) or soft < 1 or hard < 1: bad.append("limits out of range: soft %d hard %d clock %d" % (soft, hard, clock))
    if bad:
        fails += 1
        if fails <= 10: print("FAIL case %d (%s, %s, mtg %s, ovh %d): %s" % (i, kind, c, mtg, ovh, "; ".join(bad)))
send("quit"); finish(p)
print("%d cases, curve %d: %d mismatches" % (n, args.curve, fails))
print("t_tmcurve: %s" % ("PASS" if not fails else "FAILED"))
sys.exit(1 if fails else 0)
