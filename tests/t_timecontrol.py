#!/usr/bin/env python3
"""Time-control battery over UCI: 14 `go` variants (clocks 5000 .. 0 / negative, movestogo, movetime 250 / 1, depth 8 / 1, infinite + stop, infinite + isready + stop,
plain `go`).  Every case must end with a bestmove; `isready` during a search must be answered; `go infinite` must not answer before the stop.  No python-chess needed.
  ENGINE=./zeval python3 tests/t_timecontrol.py          (Windows: set ENGINE=zeval.exe)         ~ 5 s
Exit code 0 = all cases returned a bestmove, 1 = a case failed (a hung engine is killed after 12 s and counts as "no bestmove")."""
from _eng import ENGINE, resolve, PipeReader
import subprocess, time, sys, os
def uci_go(go, pos="position startpos", extra=None, delay=0, timeout=12):
    p = subprocess.Popen(ENGINE, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
    p.stdin.write("uci\nisready\n"+pos+"\n"+go+"\n"); p.stdin.flush()
    t=time.time(); lines=[]; best=None
    # extra: one command sent at 'delay', or a list of (delay, command) pairs
    evs = [] if extra is None else (sorted(extra) if isinstance(extra, list) else [(delay, extra)])
    rd = PipeReader(p.stdout)
    while time.time()-t < timeout:
        while evs and time.time()-t >= evs[0][0]:
            p.stdin.write(evs.pop(0)[1]+"\n"); p.stdin.flush()
        d = rd.get(0.05)
        if d is None: continue
        if not d: break
        for x in d.decode(errors="replace").splitlines():
            lines.append(x)
            if x.startswith("bestmove"): best=x
        if best: break
    dt=time.time()-t
    try: p.stdin.write("quit\n"); p.stdin.flush(); p.wait(timeout=3)
    except Exception: p.kill()
    return best, round(dt,3), lines
tests = [
 ("clock 5000/5000 +100", "go wtime 5000 btime 5000 winc 100 binc 100", {}),
 ("clock 300/300", "go wtime 300 btime 300", {}),
 ("clock 60/60", "go wtime 60 btime 60", {}),
 ("clock 5/5", "go wtime 5 btime 5", {}),
 ("clock 0/0", "go wtime 0 btime 0", {}),
 ("clock negative", "go wtime -50 btime -50", {}),
 ("black 3000 mtg 1", "go wtime 3000 btime 3000 movestogo 1", {"pos":"position startpos moves e2e4"}),
 ("movetime 250", "go movetime 250", {}),
 ("movetime 1", "go movetime 1", {}),
 ("depth 8", "go depth 8", {}),
 ("depth 1", "go depth 1", {}),
 ("infinite+stop@1s", "go infinite", {"extra":"stop","delay":1.0}),
 ("infinite+isready@.5 then stop@1.0", "go infinite", {"extra":[(0.5,"isready"),(1.0,"stop")]}),
 ("go (no args)", "go", {}),
]
bad = 0
for name, go, kw in tests:
    b, dt, l = uci_go(go, **kw)
    print(f"{name:36s} -> {b} {dt}s  readyok_during={l.count('readyok')}")
    if b is None: bad += 1; print("FAIL: no bestmove")                 # every case here must end with a bestmove (the infinite ones get their stop)
    if "isready@" in name and l.count("readyok") != 2: bad += 1; print("FAIL: isready during a search was not answered")
    if name.startswith("infinite") and dt < 0.9: bad += 1; print("FAIL: go infinite answered before the stop")
if bad: print(bad, "FAILED"); sys.exit(1)
print("time controls: all cases returned a bestmove")
