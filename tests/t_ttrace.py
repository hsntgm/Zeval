#!/usr/bin/env python3
"""Lock-free transposition table: do two or more threads that hammer the SAME table at the same time ever see a torn entry?

The test build (-DUNITTEST, not part of the release) has the UCI option "TTRace value n": n * 1000 iterations per unit, the units 0 .. c-1 (option
Conc, default 2) run at the same time, each in its own thread with ITS OWN copy of tt_store / tt_probe (src/p7_units.asm, ttr_main_<k>).  Every
iteration stores one entry and probes two random keys.  The keys fall into 4 buckets (64 keys per bucket), so the threads constantly replace each
other's entries.  The data of an entry is a function of its key; a probe hit whose data is not the data of the key it was found under is a
CORRUPT entry (the two halves come from different writes).  Per unit the engine prints   info string ttrace <k> <stores> <probes> <hits> <bad>.

Why a special build: a one-core machine runs the threads one after the other and a race practically never happens.  -DTTRACE puts a pause loop and
sched_yield between the two stores of tt_store, so the other thread runs exactly inside the window.  -DTTPLAIN is the OLD table scheme (plain key,
four separate data stores) for comparison.

  1. lock-free table + wide window:   bad == 0 for every unit, the check had something to check (hits > 0), stores == iterations
  2. self-test (a broken variant must be caught):            the OLD scheme + the same window must produce bad > 0 (otherwise the test could not detect anything)
  3. lock-free table, no window:      bad == 0 as well (on a multi-core machine this is a real race; here it is only a smoke test)

usage:  python3 tests/t_ttrace.py [--units 2] [--iters 100]        (Linux: needs nasm and ld; --iters = thousands of iterations per unit)
        python3 tests/t_ttrace.py --exe zeval_ut.exe [--exe-plain zeval_ut_plain.exe] [--units 4] [--iters 3000]
            ready-made test builds (built with build/build.sh, NASMFLAGS="-DUNITTEST" and "-DUNITTEST -DTTPLAIN"; e.g. on a real Windows machine,
            no building): checks 1 and 3 on --exe (bad == 0); --exe-plain only reports how many corrupt entries the old scheme produced
            (informational: on a multi-core machine it is normally > 0, but a race is not guaranteed to happen in any given run).
Exit code 0 = everything passed.
"""
import os, re, subprocess, sys, tempfile, shutil

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from _eng import resolve
args = sys.argv[1:]
def opt(name, default):
    return args[args.index(name) + 1] if name in args else default
N = int(opt("--units", 2)); ITERS = int(opt("--iters", 100))
PARTS = ["p1_data.asm", "eval.asm", "p2_core.asm", "p2b_bb_data.inc", "p2b_bb.asm", "p3_cmd.asm", "p4_search.asm", "p5_main.asm", "p6_win.asm", "p7_units.asm"]

bad_count = 0
def check(ok, msg):
    global bad_count
    print(("PASS " if ok else "FAIL ") + msg, flush=True)
    if not ok: bad_count += 1

def sh(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode: print("FAILED:", " ".join(cmd), "\n", r.stdout[-800:], r.stderr[-1500:]); sys.exit(2)
    return r.stdout

def build(tmp, name, extra):
    asm_path = os.path.join(tmp, name + ".asm")
    with open(asm_path, "wb") as o:
        for p in PARTS: o.write(open(os.path.join(ROOT, "src", p), "rb").read())
    objs = []
    for k in range(N):
        ob = os.path.join(tmp, f"{name}_u{k}.o")
        sh(["nasm", "-f", "elf64", f"-DUNIT={k}", f"-DNUNITS={N}", "-DUNITTEST"] + extra + [asm_path, "-o", ob]); objs.append(ob)
    exe = os.path.join(tmp, name)
    sh(["ld"] + objs + ["-o", exe])
    return exe

def race(exe, units, iters):
    """-> {unit: (stores, probes, hits, bad)} of one TTRace run, or None (hang / crash / missing output)"""
    cmds = ["uci", "ucinewgame", f"setoption name Conc value {units}", f"setoption name TTRace value {iters}", "isready", "quit"]
    try:
        r = subprocess.run(exe if isinstance(exe, list) else [exe], input="\n".join(cmds) + "\n", capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        return None
    res = {}
    for l in r.stdout.splitlines():
        m = re.match(r"info string ttrace (\d+) (\d+) (\d+) (\d+) (\d+)$", l)
        if m: res[int(m[1])] = tuple(int(m[i]) for i in range(2, 6))
    return res if len(res) == units and "readyok" in r.stdout and r.returncode == 0 else None

def totals(res): return tuple(sum(v[i] for v in res.values()) for i in range(4))

def verdict_clean(res, units, iters, label):
    if res is None: check(False, f"{label}: engine hung / crashed / printed no result"); return
    stores, probes, hits, bad = totals(res)
    ok = bad == 0 and hits > 0 and all(v[0] == iters * 1000 for v in res.values())
    check(ok, f"{label}: {units} threads on one table, {stores} stores, {probes} probes, {hits} hits: corrupt entries {bad}" + ("" if ok else "  <-- expected 0 (and hits > 0)"))

if "--exe" in args:
    exe = resolve(opt("--exe", "")); units = N
    verdict_clean(race(exe, units, ITERS), units, ITERS, f"[{os.path.basename(exe[-1])}] lock-free table")
    if "--exe-plain" in args:
        plain = resolve(opt("--exe-plain", ""))
        runs = [race(plain, units, ITERS) for _ in range(3)]
        if None in runs: check(False, f"[{os.path.basename(plain[-1])}] old scheme: engine hung / crashed")
        else: print(f"INFO [{os.path.basename(plain[-1])}] old (plain) scheme, 3 runs: corrupt entries {[totals(r)[3] for r in runs]} (informational; > 0 shows the race is real on this machine)")
    print("ALL PASSED" if not bad_count else f"{bad_count} FAILED"); sys.exit(1 if bad_count else 0)

tmp = tempfile.mkdtemp(prefix="t_ttrace_")
try:
    xor_race = build(tmp, "zeval_xor_race", ["-DTTRACE"])
    plain_race = build(tmp, "zeval_plain_race", ["-DTTRACE", "-DTTPLAIN"])
    xor_plain = build(tmp, "zeval_xor", [])
    for i in range(3):
        verdict_clean(race(xor_race, N, ITERS), N, ITERS, f"lock-free table, wide race window, run {i+1}")
    runs = [race(plain_race, N, ITERS) for _ in range(3)]
    if None in runs: check(False, "self-test: old scheme build did not run")
    else:
        b = [totals(r)[3] for r in runs]
        check(all(x > 0 for x in b), f"self-test: the OLD scheme with the same window produces corrupt entries in every run ({b}), so the test can detect a torn entry")
    verdict_clean(race(xor_plain, N, ITERS), N, ITERS, "lock-free table, no window (smoke test on this machine)")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
print("ALL PASSED" if not bad_count else f"{bad_count} FAILED")
sys.exit(1 if bad_count else 0)
