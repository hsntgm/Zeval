#!/usr/bin/env python3
"""Thread units: does every unit - every per-thread copy of the search code, each with its own state block - search
exactly like unit 0, when it runs in a helper thread?

Linux only (needs nasm and ld; nm and objdump for the audit).  Builds a TEST binary (-DUNITTEST, not part of the release) in a temp dir:
zeval.asm assembled once per unit (nasm -DUNIT=k), linked together.  The test build adds the UCI option "Unit" (setoption name Unit value k):
"go" then runs the search in a freshly created helper thread (raw clone, own stack) of unit k; the main thread waits.  The position is
copied from unit 0 (unit_import), the root result copied back (unit_export).

  1. golden:     for every unit k: tests/t_golden.py with GOLDEN_OPTS="Unit=k" must give the recorded 60 positions / depth 8 (tests/golden_depth8.json)
                 (the node counts printed by unit k's own counter), 0 mismatches.
  2. self-test:  a build in which a helper unit does NOT receive the position (-DUNITBREAK) must fail the golden test, i.e. the test really
                 runs unit k's code on unit k's block.
  3. audit:      per helper object: the undefined symbols (what the unit takes from unit 0) must all be on the shared list, none may be one of
                 the thread-local symbols, and the thread-local symbols must be defined in the object itself.
  4. layout:     the state blocks of all units are distinct, do not overlap, and have the same size.

usage:  python3 tests/t_units.py [--units 32] [--bb 0|1]           (default 32 units, magic-bitboard build)
Exit code 0 = everything passed.
"""
import os, re, subprocess, sys, tempfile, shutil

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
args = sys.argv[1:]
N = int(args[args.index("--units") + 1]) if "--units" in args else 32
BB = args[args.index("--bb") + 1] if "--bb" in args else "1"
GOLD = "golden_depth8.json" if BB == "1" else "golden_depth8_r24.json"
BBFLAGS = [f"-DBB={BB}"] + ([] if BB == "1" else ["-DDEV8SEARCH"])      # golden_depth8_r24.json = the 0x88 engine with the previous default search

TL = "board stm castle epsq halfm ksq hashk mgsc egsc phase gply cnt bbp bbc ply seldep itdepth nodes rootbest rootscore rpvlen rpv sevals pvlen killers histtab pvtab ustack".split()
if BB == "0": TL = [t for t in TL if t not in ("bbp", "bbc")]

def sh(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode: print("FAILED:", " ".join(cmd), "\n", r.stdout[-800:], r.stderr[-1500:]); sys.exit(2)
    return r.stdout

def build(tmp, name, extra):
    asm_path = os.path.join(tmp, "zeval.asm")
    parts = ["p1_data.asm", "eval.asm", "p2_core.asm", "p2b_bb_data.inc", "p2b_bb.asm", "p3_cmd.asm", "p4_search.asm", "p5_main.asm", "p6_win.asm", "p7_units.asm"]
    with open(asm_path, "wb") as o:
        for p in parts: o.write(open(os.path.join(ROOT, "src", p), "rb").read())
    objs = []
    for k in range(N):
        ob = os.path.join(tmp, f"{name}_u{k}.o")
        sh(["nasm", "-f", "elf64", f"-DUNIT={k}", f"-DNUNITS={N}"] + BBFLAGS + ["-DUNITTEST"] + extra + [asm_path, "-o", ob]); objs.append(ob)
    exe = os.path.join(tmp, name)
    sh(["ld"] + objs + ["-o", exe])
    return exe, objs

def golden(exe, unit):
    env = dict(os.environ, GOLDEN_OPTS=f"Unit={unit}")
    try:
        r = subprocess.run([sys.executable, os.path.join(HERE, "t_golden.py"), exe, os.path.join(HERE, GOLD)], capture_output=True, text=True, env=env, timeout=600)
    except subprocess.TimeoutExpired:
        return False, "timeout (600 s): the golden run hung"
    last = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "(no output) " + r.stderr[-200:]
    return r.returncode == 0, last

bad = 0
def check(ok, msg):
    global bad
    print(("PASS " if ok else "FAIL ") + msg)
    if not ok: bad += 1

tmp = tempfile.mkdtemp(prefix="t_units_")
try:
    exe, objs = build(tmp, "zeval_ut", [])
    # 1. golden for every unit (0 = unit 0 in a helper thread as well)
    for k in range(N):
        ok, last = golden(exe, k)
        check(ok, f"golden, search in a helper thread of unit {k}: {last}")
    # 2. self-test (a broken variant must be caught)
    exeb, _ = build(tmp, "zeval_break", ["-DUNITBREAK"])
    ok, last = golden(exeb, 1 if N > 1 else 0)
    check(not ok or N == 1, f"self-test: a unit that does not receive the position FAILS the golden test ({last})" if N > 1 else "self-test skipped (1 unit)")
    # 3. audit of the helper objects
    shared_ok = None
    for k in range(1, N):
        und = set(l.split()[-1] for l in sh(["nm", "-u", objs[k]]).splitlines() if l.strip())
        defd = set(l.split()[-1] for l in sh(["nm", objs[k]]).splitlines() if len(l.split()) == 3 and l.split()[1] in "bBtTdD")
        leak = sorted(und & set(TL)); missing = sorted(set(TL) - defd)
        check(not leak and not missing, f"audit unit {k}: {len(und)} undefined symbols taken from unit 0, thread-local symbols shared: {leak or 'none'}, not defined locally: {missing or 'none'}")
        if shared_ok is None: shared_ok = und
        else: check(und == shared_ok, f"audit unit {k}: same shared symbols as unit 1" if und == shared_ok else f"audit unit {k}: shared symbols differ from unit 1: {sorted(und ^ shared_ok)}")
    if shared_ok: print("     shared symbols the helper units use:", " ".join(sorted(shared_ok)))
    # 4. layout of the state blocks
    syms = {}
    for l in sh(["nm", exe]).splitlines():
        m = re.match(r"([0-9a-f]+) \w (tlb_(\d+))$", l)
        if m: syms[int(m[3])] = int(m[1], 16)
    byunit = [syms[k] for k in sorted(syms)]
    gaps = [b - a for a, b in zip(byunit, byunit[1:])]
    check(len(syms) == N and len(set(byunit)) == N, f"layout: {len(syms)} state blocks at distinct addresses")
    # unit 0's object also holds the shared tables, so only the blocks of the helper units 1.. are compared with each other
    helper_gaps = gaps[1:-1] if len(gaps) > 2 else gaps[1:]
    check(all(g >= 0x40000 for g in gaps) and len(set(helper_gaps)) <= 1,
          f"layout: no overlap (block >= 266 KB), helper blocks equally spaced (spacing {sorted(set(helper_gaps))} bytes, unit 0 -> 1: {gaps[0] if gaps else '-'})")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
print("ALL PASSED" if not bad else f"{bad} FAILED")
sys.exit(1 if bad else 0)
