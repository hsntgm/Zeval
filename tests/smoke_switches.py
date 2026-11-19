#!/usr/bin/env python3
"""Smoke test of an engine BUILD (a build with some -D switch on / off): random positions, go depth D (or a clock go), the bestmove must be legal,
the engine must exit with code 0.  Prints the total node count.  Needs python-chess.
usage: python3 tests/smoke_switches.py ENGINE N DEPTH [clock]       e.g.  smoke_switches.py ./zeval_chkext 200 6        smoke_switches.py ./zeval 100 0 clock
  clock = every search is "go wtime 400 btime 400 winc 20 binc 20" instead of "go depth D" (DEPTH is then ignored)
Exit code: 0 = all moves legal and rc 0, 1 = an illegal move / bad exit code, 2 = the engine crashed or hung (EngineGone: one ERROR line)."""
import sys, os, subprocess, random, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _eng import resolve, expect, finish, start
import chess

if len(sys.argv) < 4: print(__doc__); sys.exit(2)
eng = sys.argv[1]; N = int(sys.argv[2]); D = int(sys.argv[3]); clock = len(sys.argv) > 4 and sys.argv[4] == "clock"
random.seed(5)
p = start(resolve(eng))
def send(s): p.stdin.write(s + "\n"); p.stdin.flush()
send("uci"); expect(p, "uciok", 30)
tot = 0; bad = 0
for i in range(N):
    b = chess.Board()
    for _ in range(random.randint(0, 80)):
        m = list(b.legal_moves)
        if not m: break
        b.push(random.choice(m))
    if b.is_game_over(): continue
    send("ucinewgame" if i % 7 == 0 else "isready")
    if i % 7 != 0: expect(p, "readyok", 30)
    send("position fen " + b.fen())
    send("go wtime 400 btime 400 winc 20 binc 20" if clock else f"go depth {D}")
    lines = []
    try: expect(p, "bestmove", 120, collect=lines)
    except Exception:
        print("EOF/CRASH/HANG at position", b.fen()); raise
    nodes = 0; bm = lines[-1].split()[1] if len(lines[-1].split()) > 1 else ""
    for l in lines:
        if l.startswith("info") and " nodes " in l: nodes = int(l.split(" nodes ")[1].split()[0])
    tot += nodes
    try:
        mv = chess.Move.from_uci(bm)
        if mv not in b.legal_moves: bad += 1; print("ILLEGAL", bm, b.fen())
    except Exception: bad += 1; print("BAD", bm, b.fen())
send("quit"); rc = finish(p, 10)
print(f"rc={rc} bad={bad} nodes={tot}")
sys.exit(1 if bad or rc != 0 else 0)
