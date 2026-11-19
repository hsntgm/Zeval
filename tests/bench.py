#!/usr/bin/env python3
"""Fixed-depth bench: total nodes / time / nps over the golden positions.  usage: bench.py engine [engine ...] [--depth 10] [--n 40]
Informational (prints numbers, no pass / fail).  Exit code 0 = ran, 2 = an engine crashed / hung / was not found.
For an A/B decision use tests/bench_ab.py (alternating runs, CPU time): one run of this script is only a rough number."""
import argparse, json, os, re, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _eng import resolve, expect, finish, start

ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
ap.add_argument("engines", nargs="+")
ap.add_argument("--depth", type=int, default=10)
ap.add_argument("--n", type=int, default=40)
args = ap.parse_args()
depth, n = args.depth, args.n
fens = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden_depth8.json")))["fens"][:n]

def run(path):
    p = start(resolve(path))
    def send(s): p.stdin.write(s + "\n"); p.stdin.flush()
    try:
        send("uci"); expect(p, "uciok", 30)
        tn = 0; tt = 0.0
        for f in fens:
            send("ucinewgame"); send("isready"); expect(p, "readyok", 30)
            send(f"position fen {f}"); t0 = time.time(); send(f"go depth {depth}")
            lines = []; expect(p, "bestmove", 600, collect=lines)
            tt += time.time() - t0
            nodes = 0
            for l in lines:
                if l.startswith("info") and " nodes " in l: nodes = int(re.search(r" nodes (\d+)", l)[1])
            tn += nodes
        send("quit"); finish(p, 10)
        return tn, tt
    finally:
        if p.poll() is None: p.kill()

for a in args.engines:
    tn, tt = run(a)
    print(f"{os.path.basename(a):12s} depth {depth} positions {len(fens)}: nodes {tn:>11,}  time {tt:6.1f}s  nps {tn/max(tt, 1e-9)/1e6:5.2f}M")
