#!/usr/bin/env python3
"""Thread units, concurrency: do several units search AT THE SAME TIME without disturbing each other?

t_units.py runs one helper unit at a time.  A leak between units (a state variable that is shared although it must be thread-local, a block
overlapping another one, a helper that writes into unit 0) shows only when several units run together.  This test builds a TEST binary
(-DUNITTEST -DNOTT, not part of the release; Linux, needs nasm and ld) with the option "Conc n": "go" starts the units 0 .. n-1 at the same
time (unit 0 on the main thread, the others in helper threads, released together by one start signal); each unit first plays its first k legal
moves, so every unit searches ITS OWN position.  "ConcOnly k" runs unit k alone (same code path) = the reference.  -DNOTT switches the
transposition table off (every probe misses), which makes a search deterministic even when many threads run: the result of unit k in the
concurrent run (nodes, score, best move, whole root line) must equal the reference of unit k, exactly.

  1. concurrent == reference   for several positions, n = 5 and n = 32 units, repeated; ucinewgame before every run (clears the history of all units)
  2. self-test (a broken variant must be caught)       a build in which the killer table is shared by all units (text patch of a temporary copy) must FAIL (1)
  3. table on (-DUNITTEST only) smoke test: n = 32 units at once with the shared transposition table: no hang, no crash, every unit reports a
                                plausible result (nodes > 0, a root move, root line starts with it); the engine is still alive and its
                                single-thread golden is unchanged.  All units hammer the shared table; no value is compared (the result depends on timing).

usage: python3 tests/t_units_conc.py [--units 32] [--quick]           Exit code 0 = everything passed.
       python3 tests/t_units_conc.py --units 32 --exe "wine64 testbuild/zeval_ut_nott.exe" [--exe-tt "wine64 testbuild/zeval_ut.exe"]
           runs (1) and (3) on ready-made test builds (build/build.sh with NASMFLAGS="-DUNITTEST -DNOTT" resp. "-DUNITTEST"), e.g. zeval.exe under Wine
           or on a real Windows machine; no building, no self-test.
"""
import os, re, subprocess, sys, tempfile, shutil, json

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
args = sys.argv[1:]
N = int(args[args.index("--units") + 1]) if "--units" in args else 32
QUICK = "--quick" in args
PARTS = ["p1_data.asm", "eval.asm", "p2_core.asm", "p2b_bb_data.inc", "p2b_bb.asm", "p3_cmd.asm", "p4_search.asm", "p5_main.asm", "p6_win.asm", "p7_units.asm"]

bad = 0
def check(ok, msg):
    global bad
    print(("PASS " if ok else "FAIL ") + msg, flush=True)
    if not ok: bad += 1

def sh(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode: print("FAILED:", " ".join(cmd), "\n", r.stdout[-800:], r.stderr[-1500:]); sys.exit(2)
    return r.stdout

def build(tmp, name, extra, patch=None):
    src = os.path.join(tmp, name + "_src"); os.makedirs(src)
    asm_path = os.path.join(src, "zeval.asm")
    with open(asm_path, "w", newline="") as o:
        for p in PARTS:
            t = open(os.path.join(ROOT, "src", p), newline="").read()
            if patch and p == patch[0]:
                assert t.count(patch[1]) == 1, "patch target not found"; t = t.replace(patch[1], patch[2])
            o.write(t)
    objs = []
    for k in range(N):
        ob = os.path.join(src, f"u{k}.o")
        sh(["nasm", "-f", "elf64", f"-DUNIT={k}", f"-DNUNITS={N}", "-DUNITTEST"] + extra + [asm_path, "-o", ob]); objs.append(ob)
    exe = os.path.join(tmp, name)
    sh(["ld"] + objs + ["-o", exe])
    return exe

def run_engine(exe, cmds, timeout):
    """Feed all commands at once.  Returns (list of runs, exit code); a run = dict k -> tuple (one 'info string conc' line per unit) of one 'go'."""
    try:
        r = subprocess.run(exe if isinstance(exe, list) else [exe], input="\n".join(["uci"] + cmds + ["quit"]) + "\n", capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, "timeout"
    runs, cur = [], {}
    for l in r.stdout.splitlines():
        m = re.match(r"info string conc (\d+) (.*)$", l)
        if m: cur[int(m[1])] = tuple(int(x) for x in m[2].split())
        elif l.startswith("bestmove"): runs.append(cur); cur = {}
    return runs, r.returncode

def positions():
    g = json.load(open(os.path.join(HERE, "golden_depth8.json")))["fens"]
    sel = g[0:10] + g[10:60:5]                # the first ten (hand-picked, incl. tactical / endgame) and every 5th random one
    return ["startpos"] + [f"fen {f}" for f in sel]

def compare(exe, poss, depth, n, reps, label, quiet=False):
    """concurrent run of units 0..n-1 == reference runs of each unit alone, for every position, reps times"""
    cmds = []
    for p in poss:
        for k in range(n):
            cmds += ["ucinewgame", f"setoption name ConcOnly value {k}", f"position {p}", f"go depth {depth}"]
        cmds += ["setoption name ConcOnly value 99", f"setoption name Conc value {n}"]
        for _ in range(reps): cmds += ["ucinewgame", f"position {p}", f"go depth {depth}"]
        cmds += [f"setoption name Conc value 0"]
    runs, rc = run_engine(exe, cmds, 900)
    if runs is None:
        if quiet: return False, "engine hung"
        check(False, f"{label}: engine hung (timeout)"); return
    per = n + reps; ok = True; nrun = 0; worst = ""
    if len(runs) != per * len(poss):
        if quiet: return False, f"{len(runs)} go results"
        check(False, f"{label}: expected {per*len(poss)} go results, got {len(runs)} (exit {rc})"); return
    for i, p in enumerate(poss):
        blk = runs[i * per:(i + 1) * per]
        ref = {k: blk[k][k] for k in range(n) if k in blk[k]}
        if len(ref) != n: ok = False; worst = f"{p}: reference run missing units"; break
        for r in blk[n:]:
            nrun += 1
            for k in range(n):
                if r.get(k) != ref[k]:
                    ok = False; worst = f"{p}, unit {k}: concurrent {r.get(k)} != reference {ref[k]}"; break
            if not ok: break
        if not ok: break
    if quiet: return ok, worst
    check(ok, f"{label}: {len(poss)} positions x {reps} concurrent runs of {n} units, depth {depth}: every unit identical to its reference" + ("" if ok else f"  [{worst}]"))
    return ok

def sane(exe, poss, depth, n, reps, label):
    cmds = []
    for p in poss:
        cmds += ["setoption name Conc value %d" % n]
        for _ in range(reps): cmds += ["ucinewgame", f"position {p}", f"go depth {depth}"]
    cmds += ["setoption name Conc value 0", "isready"]
    runs, rc = run_engine(exe, cmds, 900)
    if runs is None: check(False, f"{label}: engine hung (timeout)"); return
    problems = []
    if len(runs) != reps * len(poss): problems.append(f"{len(runs)} go results instead of {reps*len(poss)}")
    for i, r in enumerate(runs):
        if sorted(r) != list(range(n)): problems.append(f"run {i}: units reported {sorted(r)}"); continue
        for k, v in r.items():
            nodes, score, best, npv = v[0], v[1], v[2], v[3]
            if not (nodes > 0 and abs(score) <= 32000 and npv >= 1 and len(v) == 4 + npv and v[4] == best):
                if best == 0 and npv == 0: continue          # a unit whose shifted position has no legal move
                problems.append(f"run {i} unit {k}: {v[:6]}")
    check(not problems and rc == 0, f"{label}: {len(poss)} positions x {reps} runs of {n} units with the shared table, depth {depth}: no hang/crash, all results plausible (exit {rc})" + ("" if not problems else f"  [{problems[:3]}]"))

sys.path.insert(0, HERE)
from _eng import resolve
if "--exe" in args:                                   # use ready-made test builds (e.g. zeval.exe under Wine), no building here
    nott = resolve(args[args.index("--exe") + 1]); poss = positions()
    if QUICK: poss = poss[:4]
    nn = min(N, 5)
    compare(nott, poss, 7, nn, 3, f"[{os.path.basename(nott[-1])}] {nn} units")
    if N > 5: compare(nott, poss[:6], 6, N, 2, f"[{os.path.basename(nott[-1])}] {N} units")
    if "--exe-tt" in args:
        tt = resolve(args[args.index("--exe-tt") + 1])
        sane(tt, poss[:6], 7, N, 3, f"[{os.path.basename(tt[-1])}] table on, {N} units")
    print("ALL PASSED" if not bad else f"{bad} FAILED"); sys.exit(1 if bad else 0)

tmp = tempfile.mkdtemp(prefix="t_units_conc_")
try:
    poss = positions()
    nn = min(N, 5)
    exe = build(tmp, "zeval_nott", ["-DNOTT"])
    if QUICK: poss = poss[:4]
    compare(exe, poss, 7, nn, 3, f"{nn} units")
    if N > 5: compare(exe, poss[:6], 6, N, 2, f"{N} units")
    # self-test (a broken variant must be caught): killers shared between the units
    old = "killers:    resd MAXPLY*2\n"
    new = "%if UNIT == 0\nglobal killers\nkillers:    resd MAXPLY*2\n%else\nextern killers\nkillers_unused: resd MAXPLY*2\n%endif\n"
    exeb = build(tmp, "zeval_break", ["-DNOTT"], patch=("p1_data.asm", old, new))
    okb, worst = compare(exeb, poss, 7, nn, 3, "self-test", quiet=True)
    check(not okb, "self-test: a build in which all units share one killer table FAILS the comparison (detected: " + worst[:110] + ")")
    # table on
    exet = build(tmp, "zeval_tt", [])
    sane(exet, poss[:6], 7, N, 3, f"table on, {N} units")
    # the engine itself is intact after all that: single-thread golden of the table-on build
    try:
        g = subprocess.run([sys.executable, os.path.join(HERE, "t_golden.py"), exet], capture_output=True, text=True, timeout=600)
        last = g.stdout.strip().splitlines()[-1] if g.stdout.strip() else g.stderr[-200:]
        check(g.returncode == 0, f"golden of the unit test build (Unit 0, main thread): {last}")
    except subprocess.TimeoutExpired:
        check(False, "golden of the unit test build: timeout (600 s), the golden run hung")      
finally:
    shutil.rmtree(tmp, ignore_errors=True)
print("ALL PASSED" if not bad else f"{bad} FAILED")
sys.exit(1 if bad else 0)
