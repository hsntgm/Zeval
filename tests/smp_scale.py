#!/usr/bin/env python3
"""NPS scaling and time-to-depth of Lazy SMP (any platform, no python-chess needed).  INFORMATIONAL: prints numbers, makes no pass / fail claim.
usage: python3 tests/smp_scale.py ENGINE [--threads 1,2,4,8,12] [--ms 3000] [--depth 16]
For every Threads value: 3 positions x (go movetime MS: last reported nps) and (go depth D: wall time to reach it).  Run it on a quiet multi-core machine
(Threads <= logical processors); on fewer cores the numbers only show oversubscription.  ENGINE may be a path or a command line such as "wine zeval.exe".
"""
import subprocess, sys, time, re, os, shlex
a = sys.argv[1:]
eng = a[0]; opt = lambda n, d: a[a.index(n) + 1] if n in a else d
TH = [int(x) for x in opt("--threads", "1,2,4,8,12").split(",")]; MS = int(opt("--ms", "3000")); D = int(opt("--depth", "16"))
POS = ["position startpos",
       "position fen r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1",
       "position fen r4rk1/1pp1qppp/p1np1n2/2b1p1B1/2B1P1b1/P1NP1N2/1PP1QPPP/R4RK1 w - - 0 10"]
cmd = [eng] if os.path.exists(eng) else shlex.split(eng, posix=(os.name != "nt"))
def run(T, go):
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
    send = lambda s: (p.stdin.write(s + "\n"), p.stdin.flush())
    send("uci"); send(f"setoption name Threads value {T}"); send("setoption name Hash value 256")
    out = []
    for pos in POS:
        send("ucinewgame"); send(pos); t = time.time(); send(go); nps = 0; nodes = 0
        while True:
            l = p.stdout.readline()
            if not l or l.startswith("bestmove"): break
            m = re.search(r"nodes (\d+) nps (\d+)", l)
            if m: nodes, nps = int(m[1]), int(m[2])
        out.append((nps, nodes, time.time() - t))
    send("quit")
    try: p.wait(20)
    except subprocess.TimeoutExpired: p.kill()                    # an engine that does not exit after quit is killed
    return out
print(f"{'Threads':>7} {'nps (movetime %d ms)' % MS:>22} {'x Threads=1':>12} {'s to depth %d' % D:>16} {'x faster':>9}")
b_nps = b_t = None
for T in TH:
    r1 = run(T, f"go movetime {MS}"); nps = sum(x[0] for x in r1) / len(r1)
    r2 = run(T, f"go depth {D}"); tt = sum(x[2] for x in r2) / len(r2)
    if b_nps is None: b_nps, b_t = nps, tt
    print(f"{T:>7} {nps:>22,.0f} {nps/b_nps:>12.2f} {tt:>16.2f} {b_t/tt:>9.2f}", flush=True)
