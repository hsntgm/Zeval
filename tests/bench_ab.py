#!/usr/bin/env python3
"""A/B speed comparison of two engine builds (gate: "bench NPS drop <= 3 %").

Fixed-depth search of the first --n golden positions (same work for both builds: the node counts must be equal, the script checks it), the two
engines ALTERNATE (A B A B ...) so that slow drift of the machine (frequency, other load) hits both the same way.  Per run the time of the
engine process is measured: CPU time (user+system) of the child where the operating system gives it (Linux), otherwise wall time.
Report: min and median per engine, and the median of the per-pair ratios B/A with the spread (middle half) of those ratios.
A difference smaller than the spread is noise.  On a quiet machine use --runs 15 or more; close other programs; plug the laptop in.

usage:  python3 tests/bench_ab.py A B [--runs 15] [--depth 10] [--n 40]        e.g.  bench_ab.py old\\zeval.exe dist\\zeval.exe
Exit code: 0 = same work and B within the 3 % threshold, 1 = B above the threshold or NOT the same work (comparison void), 2 = an engine crashed / hung.
"""
import subprocess, sys, json, re, os, time, statistics
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _eng import resolve, expect, finish, start
args = sys.argv[1:]
def opt(name, default):
    if name in args:
        i = args.index(name); v = int(args[i + 1]); del args[i:i + 2]; return v
    return default
runs = opt("--runs", 15); depth = opt("--depth", 10); n = opt("--n", 40)
if len(args) != 2: print(__doc__); sys.exit(2)
fens = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden_depth8.json")))["fens"][:n]
try:
    import resource
    def cpu(): r = resource.getrusage(resource.RUSAGE_CHILDREN); return r.ru_utime + r.ru_stime
    WHAT = "CPU time of the engine process"
except ImportError:
    cpu = None; WHAT = "wall time (no per-process CPU time on this system)"

def one(path):
    c0 = cpu() if cpu else 0; t0 = time.perf_counter()
    p = start(resolve(path))
    def send(s): p.stdin.write(s + "\n"); p.stdin.flush()
    try:
        send("uci"); expect(p, "uciok", 30)
        nodes = 0
        for f in fens:
            send("ucinewgame"); send("isready"); expect(p, "readyok", 30)
            send(f"position fen {f}"); send(f"go depth {depth}")
            lines = []; expect(p, "bestmove", 600, collect=lines)
            last = 0
            for l in lines:
                if l.startswith("info") and " nodes " in l: last = int(re.search(r" nodes (\d+)", l)[1])
            nodes += last
        send("quit"); finish(p, 30)
    finally:
        if p.poll() is None: p.kill()
    return nodes, (cpu() - c0) if cpu else (time.perf_counter() - t0)

A, B = args
ta, tb, na, nb = [], [], set(), set()
for i in range(runs):
    order = [(A, ta, na), (B, tb, nb)] if i % 2 == 0 else [(B, tb, nb), (A, ta, na)]
    for path, lst, ns in order:
        nodes, t = one(path); lst.append(t); ns.add(nodes)
    print(f"  run {i+1:2d}/{runs}: A {ta[-1]:.3f}s  B {tb[-1]:.3f}s  ratio B/A {tb[-1]/ta[-1]:.3f}", flush=True)
ratios = sorted(b / a for a, b in zip(ta, tb))
q1, q3 = ratios[len(ratios) // 4], ratios[(3 * len(ratios)) // 4]
print(f"\nmeasured: {WHAT}; depth {depth}, {len(fens)} positions, {runs} alternating runs")
same = (na == nb and len(na) == 1)
print(f"nodes per run: A {sorted(na)}  B {sorted(nb)}  -> " + ("same work" if same else "NOT THE SAME WORK: the comparison is void"))
print(f"A: min {min(ta):.3f}s  median {statistics.median(ta):.3f}s")
print(f"B: min {min(tb):.3f}s  median {statistics.median(tb):.3f}s")
med = statistics.median(ratios)
print(f"B/A: median of pair ratios {med:.3f} (middle half {q1:.3f} .. {q3:.3f}); min-to-min {min(tb)/min(ta):.3f}  ->  B is {100*(med-1):+.1f} % time (positive = slower)")
print("within the 3 % threshold" if med <= 1.03 else "ABOVE the 3 % threshold: look into the cause")
sys.exit(0 if (same and med <= 1.03) else 1)
