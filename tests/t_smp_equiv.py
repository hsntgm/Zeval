#!/usr/bin/env python3
"""Lazy SMP - is the HELPER flavour of the search the same search as the main thread's?

Linux only (needs nasm, ld).  Builds a TEST binary in a temp dir: the release flavour (SMP = 1) plus
    -DSMPTEST        smp_stop does not set hstop (the helper threads run to their own end: use depth-limited searches) and prints one line per unit
                     "info string smpunit <k> <nodes> <rootscore> <rootbest> <depth>" for unit 0 (main thread) and every helper after the join
    -DNOTT           transposition table off (every probe misses, tt_store does nothing): the threads share NOTHING, so every search is deterministic
    -DSMP_ASPSTEP=0  the helpers use the same aspiration window as the main thread
In that build, with Threads = 3, the main thread and the helper threads 1 and 2 search the same position to the same fixed depth at the same time, each in its
own copy of the code with its own state block.  The helper flavour of think() (no clock, no output, other exits) must then have done EXACTLY what the main
thread did:
  1. for every position of tests/golden_depth8.json (60), depth 7: the nodes, root score, best move and depth of the units 0, 1 and 2 are identical;
  2. the main thread's node count is identical to a Threads=1 run of the same build (the helper threads do not disturb the main thread);
  3. self-test: a helper flavour that ends one iteration early (jg -> jge on the depth test) and one with another aspiration window are both
     caught by check 1.
Whether the threads share the table CORRECTLY (lock-free XOR scheme) is tested by tests/t_ttrace.py; what a helper may write is audited by tests/t_smp_audit.py.

usage: python3 tests/t_smp_equiv.py [--depth 7] [--positions 60] [--threads 3]       exit code 0 = everything passed
       python3 tests/t_smp_equiv.py --exe "wine zeval_smptest.exe" ...     checks 1 and 2 and the history check on a ready-made test build of any platform
           (testbuild/zeval_smptest.exe = build/build.sh with NASMFLAGS="-DSMPTEST -DNOTT -DSMP_ASPSTEP=0"; Windows: CreateThread helpers); no building, no self-tests
"""
import json, os, re, subprocess, sys, tempfile, shutil, shlex

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from _eng import expect, finish, start      # a crashed / silent engine ends the script with exit code 2 and one ERROR line
args = sys.argv[1:]
def opt(name, default): return type(default)(args[args.index(name) + 1]) if name in args else default
DEPTH = opt("--depth", 7); NPOS = opt("--positions", 60); TH = opt("--threads", 3)
NU = max(TH, 3)
EXE_ARG = args[args.index("--exe") + 1] if "--exe" in args else None

def sh(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode: print("FAILED:", " ".join(cmd), "\n", r.stdout[-800:], r.stderr[-1500:]); sys.exit(2)
    return r.stdout

def build(tmp, srcdir, name, extra):
    asm_path = os.path.join(tmp, name + ".asm")
    parts = ["p1_data.asm", "eval.asm", "p2_core.asm", "p2b_bb_data.inc", "p2b_bb.asm", "p3_cmd.asm", "p4_search.asm", "p5_main.asm", "p6_win.asm", "p7_units.asm"]
    with open(asm_path, "wb") as o:
        for p in parts: o.write(open(os.path.join(srcdir, p), "rb").read())
    objs = []
    for k in range(NU):
        ob = os.path.join(tmp, f"{name}_u{k}.o")
        sh(["nasm", "-f", "elf64", f"-DUNIT={k}", f"-DNUNITS={NU}", "-DSMPTEST", "-DNOTT", "-DSMP_ASPSTEP=0"] + extra + [asm_path, "-o", ob]); objs.append(ob)
    exe = os.path.join(tmp, name)
    sh(["ld"] + objs + ["-o", exe])
    return exe

def run(exe, fens, threads):
    """-> list per position of (units dict k -> (nodes, score, best, depth), main nodes of the last info line)"""
    p = start(shlex.split(exe, posix=(os.name != "nt")) if (EXE_ARG and not os.path.exists(exe)) else [exe])
    def send(s): p.stdin.write(s + "\n"); p.stdin.flush()
    res = []
    try:
        send("uci"); expect(p, "uciok", 30)
        send(f"setoption name Threads value {threads}")
        for fen in fens:
            send("ucinewgame"); send("isready"); expect(p, "readyok", 60)
            send(fen if fen.startswith("position ") else f"position fen {fen}"); send(f"go depth {DEPTH}")
            units = {}; last = None; lines = []
            expect(p, "bestmove", 600, collect=lines)
            for l in lines:
                m = re.match(r"info string smpunit (\d+) (\d+) (-?\d+) (\d+) (\d+)", l)
                if m: units[int(m[1])] = tuple(int(x) for x in m.groups()[1:])
                m = re.search(r"info depth (\d+) .*? nodes (\d+)", l)
                if m: last = (int(m[1]), int(m[2]))
            res.append((units, last))
        send("quit"); finish(p, 30)
    finally:
        if p.poll() is None: p.kill()
    return res

def compare(exe, fens):
    multi = run(exe, fens, TH); single = run(exe, fens, 1)
    bad_units = []; bad_main = []
    for i, ((u, last), (u1, last1)) in enumerate(zip(multi, single)):
        ref = u.get(0)
        if ref is None or any(u.get(k) != ref for k in range(TH)) or "EOF" in u:
            bad_units.append((i, u))
        if last is None or last1 is None or (ref is not None and ref[0] != last1[1]):
            bad_main.append((i, ref, last1))
    return bad_units, bad_main

# positions WITH game history (repetitions possible): the helpers only know it if the main thread pushes ustack to them
HIST = ["position startpos moves g1f3 g8f6 f3g1 f6g8 g1f3 g8f6 f3g1",
        "position startpos moves e2e4 e7e5 g1f3 b8c6 f1c4 g8f6 f3g5 d7d5 e4d5 c6a5 c4b5 c7c6 d5c6 b7c6 b5e2 h7h6 g5f3 e5e4 f3e5 f8d6 d2d4 e4d3 e5d3 d8c7",
        "position startpos moves d2d4 d7d5 c1f4 c8f5 f4c1 f5c8 c1f4 c8f5 f4c1",
        "position startpos moves e2e4 e7e5 f1c4 f8c5 c4f1 c5f8 f1c4 f8c5 c4f1 c5f8 g1f3 g8f6 f3g1 f6g8"]

def main():
    bad = 0
    def check(ok, msg):
        nonlocal bad
        print(("PASS " if ok else "FAIL ") + msg)
        if not ok: bad += 1
    fens = json.load(open(os.path.join(HERE, "golden_depth8.json")))["fens"][:NPOS]
    tmp = tempfile.mkdtemp(prefix="smpequiv_")
    try:
        src = os.path.join(ROOT, "src")
        exe = EXE_ARG or build(tmp, src, "good", [])
        bu, bm = compare(exe, fens)
        for x in bu[:5]: print("    units differ:", x)
        for x in bm[:5]: print("    main differs from Threads=1:", x)
        check(not bu, f"{len(fens)} positions, depth {DEPTH}: unit 0 and the helper units 1..{TH-1} agree on nodes, root score, best move and depth ({len(bu)} differ)")
        check(not bm, f"main thread with {TH} threads == Threads=1 (nodes of the main thread, {len(bm)} differ)")
        bh, bmh = compare(exe, HIST)
        for x in bh[:5]: print("    units differ:", x)
        check(not bh and not bmh, f"{len(HIST)} positions with game history (repetitions): the helper units see the history and agree with the main thread ({len(bh)} differ)")
        if EXE_ARG:
            print("ALL PASSED" if not bad else f"{bad} FAILED"); sys.exit(1 if bad else 0)
        # ---- self-tests (a broken variant must be caught)
        for name, old, new in (
                ("a helper that ends one iteration early", "%if HELPER\n    jg .done                    ; a helper has no", "%if HELPER\n    jge .done                   ; a helper has no"),
                ("a helper with another aspiration window", "    mov ebx, HASPW              ; aspiration delta", "    mov ebx, HASPW+9            ; aspiration delta")):
            bsrc = os.path.join(tmp, "src_" + str(abs(hash(name)))); shutil.copytree(src, bsrc)
            p4 = os.path.join(bsrc, "p4_search.asm"); s = open(p4).read()
            if s.count(old) != 1: check(False, f"self-test setup: pattern of '{name}' not found exactly once"); continue
            open(p4, "w").write(s.replace(old, new))
            exe_b = build(tmp, bsrc, "broken" + str(abs(hash(name))), [])
            bu2, bm2 = compare(exe_b, fens[:30])
            check(bool(bu2), f"self-test: {name} is caught ({len(bu2)} of 30 positions differ)")
        # the history is only seen if ustack is pushed: without the copy the helpers must differ on the positions with history
        bsrc = os.path.join(tmp, "src_nohist"); shutil.copytree(src, bsrc)
        p7 = os.path.join(bsrc, "p7_units.asm"); s = open(p7).read()
        old = "    rep movsb\n    mov qword [rdx + nodes - tlb_0], 0"
        if s.count(old) != 1: check(False, "self-test setup: ustack copy not found exactly once")
        else:
            open(p7, "w").write(s.replace(old, "    mov qword [rdx + nodes - tlb_0], 0"))
            exe_h = build(tmp, bsrc, "nohist", [])
            bh2, _ = compare(exe_h, HIST)
            check(bool(bh2), f"self-test: helpers that do not get the game history are caught ({len(bh2)} of {len(HIST)} positions differ)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("ALL PASSED" if not bad else f"{bad} FAILED")
    sys.exit(1 if bad else 0)

main()
