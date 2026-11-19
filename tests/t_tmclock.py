#!/usr/bin/env python3
"""t_tmclock.py ENGINE [--games N] [--plies P] [--base MS] [--inc MS] [--mtg K] [--seed S] [--opt NAME=VALUE ...] [--label TEXT]

Real-clock check of the time management (go wtime/btime/winc/binc [movestogo]): the engine plays both sides of N short games
from seeded random openings (the same openings for every engine and every run with the same --seed), each side with its own
clock.  Every move is timed from 'go' to 'bestmove'.  Checks: the move is legal, the clock never goes below 0 (flag fall),
the engine exits cleanly.  Prints the time used per move (mean / median / p95 / max), the worst share of the clock spent on one
move and the mean depth.  Needs python-chess.  Purpose: compare a TM_* build with the default build (same openings, same clock),
and catch a time management that runs into the hard limit every move (for example a clobbered register in the clock routine).
Exit code 0 = no illegal move, no flag fall, engine rc 0, at least one move played; 1 otherwise; 2 = the engine crashed / hung / was not found.
The engine may be a command line or a path with spaces; a silent engine is killed after the move's clock + 10 s.
"""
import argparse, os, random, statistics, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _eng import resolve, expect, finish, start
import chess

ap = argparse.ArgumentParser()
ap.add_argument("engine")
ap.add_argument("--games", type=int, default=3)
ap.add_argument("--plies", type=int, default=40)
ap.add_argument("--base", type=int, default=3000)
ap.add_argument("--inc", type=int, default=30)
ap.add_argument("--mtg", type=int, default=0)
ap.add_argument("--seed", type=int, default=7)
ap.add_argument("--opt", action="append", default=[])
ap.add_argument("--label", default="")
a = ap.parse_args()

p = start(resolve(a.engine))
def send(s): p.stdin.write(s + "\n"); p.stdin.flush()
def until(pred, timeout=60): return expect(p, pred, timeout)
send("uci"); until(lambda l: l == "uciok")
for o in a.opt:
    n, v = o.split("=", 1); send(f"setoption name {n} value {v}")
send("isready"); until(lambda l: l == "readyok")

rng = random.Random(a.seed)
used = []; worst = 0.0; depths = []; bad = 0; flags = 0; moves = 0
for g in range(a.games):
    b = chess.Board()
    for _ in range(4):                                   # seeded random opening, same for every engine
        b.push(rng.choice(list(b.legal_moves)))
    start = b.fen(); played = []
    clk = {chess.WHITE: a.base, chess.BLACK: a.base}
    send("ucinewgame"); send("isready"); until(lambda l: l == "readyok")
    for ply in range(a.plies):
        if b.is_game_over(): break
        send(f"position fen {start}" + (" moves " + " ".join(played) if played else ""))
        go = f"go wtime {clk[chess.WHITE]} btime {clk[chess.BLACK]} winc {a.inc} binc {a.inc}"
        if a.mtg: go += f" movestogo {a.mtg}"
        t0 = time.time(); send(go); d = 0; bm = None
        lines = []; expect(p, "bestmove", max(30.0, max(clk.values()) / 1000.0 * 2 + 10), collect=lines)
        for l in lines:
            if l.startswith("info") and " depth " in l:
                try: d = int(l.split(" depth ")[1].split()[0])
                except ValueError: pass
        bm = lines[-1].split()[1] if len(lines[-1].split()) > 1 else ""
        ms = (time.time() - t0) * 1000.0
        side = b.turn; before = clk[side]
        try:
            mv = chess.Move.from_uci(bm)
            if mv not in b.legal_moves: raise ValueError
        except Exception:
            bad += 1; print("ILLEGAL", bm, b.fen()); break
        clk[side] = before - ms
        if clk[side] < 0: flags += 1; print(f"FLAG game {g} ply {ply}: used {ms:.0f} ms of {before:.0f}"); clk[side] = 50
        clk[side] += a.inc
        used.append(ms); depths.append(d); moves += 1
        worst = max(worst, ms / max(before, 1))
        b.push(mv); played.append(bm)
send("quit"); rc = finish(p, 5)
used.sort()
label = a.label or os.path.basename(a.engine)
if not used:
    print(f"{label:18s} NO MOVE WAS PLAYED (illegal {bad}, flagfalls {flags}, rc {rc}): nothing to report")
    sys.exit(1)
p95 = used[int(0.95 * (len(used) - 1))]
print(f"{label:18s} moves {moves:4d} illegal {bad} flagfalls {flags} mean {statistics.mean(used):6.1f}ms median {statistics.median(used):6.1f}ms "
      f"p95 {p95:6.1f}ms max {used[-1]:6.1f}ms total {sum(used)/1000:5.1f}s meandepth {statistics.mean(depths):5.2f} worst%clock {worst*100:4.1f} rc {rc}")
sys.exit(0 if (bad == 0 and flags == 0 and rc == 0) else 1)
