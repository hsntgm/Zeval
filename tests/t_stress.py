#!/usr/bin/env python3
"""Stress test over UCI: N random positions, `go movetime MS` each.  Per position: a bestmove arrives, it is legal (python-chess), the position is
unchanged after the search (the console command `d` prints the same board / side / castling), and the latency is reported.
usage:  ENGINE=./zeval python3 tests/t_stress.py [seed=1] [N=200] [MS=40] [engine]          (needs python-chess)
Exit code:
  0 = no hard failure;  1 = a hard failure: no bestmove, an illegal move, STATE CHANGED after a search, or a gross latency overrun (> 2 x MS + 250 ms);
  2 = the engine crashed / was not found.
A search that is merely slower than MS + 40 ms is a WARNING (printed, exit code unchanged): on a loaded machine that is noise, not a bug."""
import chess, subprocess, random, time, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _eng import ENGINE, resolve, LineReader, EngineGone, start, finish
random.seed(int(sys.argv[1]) if len(sys.argv) > 1 else 1)
N = int(sys.argv[2]) if len(sys.argv) > 2 else 200
MS = int(sys.argv[3]) if len(sys.argv) > 3 else 40
BIN = resolve(sys.argv[4]) if len(sys.argv) > 4 else ENGINE
p = start(BIN, bufsize=1)
lr = LineReader(p.stdout)
def readuntil(pred, timeout=10):
    out = []; end = time.time() + timeout
    while True:
        left = end - time.time()
        if left <= 0: out.append("<TIMEOUT>"); return out
        try: l = lr.get(left)
        except EngineGone: out.append("<EOF>"); return out
        if l is None: continue
        out.append(l)
        if pred(l): return out
p.stdin.write("uci\n"); p.stdin.flush()
if readuntil(lambda l: l.startswith("uciok"), 30)[-1] != "uciok": raise EngineGone("no 'uciok' from the engine (crash, wrong path, not a UCI engine?)")
def gen_pos():
    while True:
        b = chess.Board()
        n = random.randint(4, 90)
        for _ in range(n):
            mv = list(b.legal_moves)
            if not mv: break
            b.push(random.choice(mv))
        if not b.is_game_over(claim_draw=False): return b
issues = []; slow = []; tmax = 0; i = -1
for i in range(N):
    b = gen_pos()
    fen = b.fen()
    try:
        p.stdin.write(f"position fen {fen}\ngo movetime {MS}\n"); p.stdin.flush()
    except OSError:
        issues.append((i, fen, "engine pipe closed (crash?)", None)); break
    t = time.time()
    out = readuntil(lambda l: l.startswith("bestmove"))
    dt = (time.time() - t) * 1000
    tmax = max(tmax, dt)
    bm = out[-1]
    if not bm.startswith("bestmove"):
        issues.append((i, fen, "no bestmove", out[-3:])); break
    mv = bm.split()[1]
    try: m = chess.Move.from_uci(mv)
    except Exception: m = None
    if m is None or m not in b.legal_moves:
        issues.append((i, fen, "ILLEGAL " + mv, None))
    if dt > 2 * MS + 250: issues.append((i, fen, f"gross latency overrun {dt:.0f}ms", None))
    elif dt > MS + 40: slow.append((i, fen, f"slow {dt:.0f}ms"))
    # state integrity after the search
    try: p.stdin.write("d\n"); p.stdin.flush()
    except OSError:
        issues.append((i, fen, "engine pipe closed (crash?)", None)); break
    o2 = readuntil(lambda l: l.startswith("Fen:"))
    got = o2[-1].split("Fen: ")[-1].split()[:3] if o2[-1].startswith("Fen:") else None
    exp = fen.split()[:3]
    if got != exp: issues.append((i, fen, "STATE CHANGED after search", got))
try: p.stdin.write("quit\n"); p.stdin.flush()
except OSError: pass
finish(p, 3)
print(f"positions={i+1} issues={len(issues)} warnings(slow)={len(slow)} max_latency={tmax:.0f}ms")
for x in issues[:15]: print(x)
for x in slow[:5]: print("WARN", x)
sys.exit(1 if issues else 0)
