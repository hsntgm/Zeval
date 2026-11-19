#!/usr/bin/env python3
"""Centipawn-loss check against an external reference engine (Stockfish): N random positions, the engine under test plays `go movetime MS`, Stockfish (depth 12) scores the best move and OUR move;
the loss = best - played move.  Prints the average loss and the blunders (>= 250 cp).  INFORMATIONAL (no pass / fail threshold): a hand-run sanity check for a big
change of search or eval, not an Elo test and not part of any verify script.   Needs python-chess and a Stockfish binary.
usage:  python3 tests/t_vs_stockfish.py SEED N MS [engine]        e.g.   t_vs_stockfish.py 1 100 100
        Stockfish: environment variable STOCKFISH, else `stockfish` on the PATH, else /usr/games/stockfish (Debian / Ubuntu package).
        Engine under test: argument 4, else the environment variable ENGINE, else ./zeval (zeval.exe on Windows).
Exit code 0 = ran; 2 = usage error / Stockfish or the engine under test not found."""
import os, random, shutil, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _eng import ENGINE, resolve
import chess, chess.engine
if len(sys.argv) < 4: print(__doc__); sys.exit(2)
random.seed(int(sys.argv[1])); N = int(sys.argv[2]); MS = int(sys.argv[3]); BIN = resolve(sys.argv[4]) if len(sys.argv) > 4 else ENGINE
SF = os.environ.get("STOCKFISH") or shutil.which("stockfish") or ("/usr/games/stockfish" if os.path.exists("/usr/games/stockfish") else None)
if not SF: sys.stderr.write("ERROR: Stockfish not found (set STOCKFISH=/path/to/stockfish, or apt install stockfish)\n"); sys.exit(2)
try:
    e = chess.engine.SimpleEngine.popen_uci(BIN)
    sf = chess.engine.SimpleEngine.popen_uci(SF)
except (OSError, chess.engine.EngineError, chess.engine.EngineTerminatedError) as ex:
    sys.stderr.write(f"ERROR: cannot start an engine: {ex}\n"); sys.exit(2)
sf.configure({"Threads":1,"Hash":64})
def pos():
    while True:
        b=chess.Board()
        for _ in range(random.randint(8,70)):
            m=list(b.legal_moves)
            if not m: break
            b.push(random.choice(m))
        if not b.is_game_over() and len(list(b.legal_moves))>1: return b
bad=[]; losses=[]
for i in range(N):
    b=pos()
    r=e.play(b, chess.engine.Limit(time=MS/1000))
    lim=chess.engine.Limit(depth=12)
    best=sf.analyse(b, lim)["score"].relative.score(mate_score=10000)
    mine=sf.analyse(b, lim, root_moves=[r.move])["score"].relative.score(mate_score=10000)
    loss=max(0,best-mine); losses.append(loss)
    if loss>=250: bad.append((b.fen(), r.move.uci(), best, mine))
e.quit(); sf.quit()
print(f"N={N} avg_cp_loss={sum(losses)/len(losses):.1f} blunders(>=250cp)={len(bad)}")
for x in bad[:12]: print(x)
