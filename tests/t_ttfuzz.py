#!/usr/bin/env python3
"""A corrupt move taken from the transposition table must never be played and never be announced.

With several threads a torn table entry can in principle deliver any move (the lock-free table makes that very unlikely, a move
from a key collision remains possible).  The engine has two gates that make such a move harmless, and this test checks both:
  * search / qsearch: a table move gets priority only if it is IDENTICAL to a move that the generator produced for this position (score_moves);
    a garbage move is never played, an unrelated generated move is merely searched early (and still has to pass the legality test of domove);
  * "bestmove ... ponder <m>": the ponder move comes from the root line or, when that is only one move long, from the table; it is printed only
    after move_is_legal.
The test build -DTTFUZZ replaces the move of every table hit by a random 16-bit number (the range of real move codes).  The tests:
  1. the fuzz is active: the search of the fuzz build differs from the normal build (node counts)
  2. over many random positions, at several depths and movetimes, with python-chess: bestmove is always legal, ponder (if printed) is always legal
     after the bestmove, the engine does not hang or crash.  "go depth 1 / 2" is included on purpose: the root line is then one move long and the
     ponder move comes from the (fuzzed) table.
  3. self-test: the same fuzz build with the legality check in front of "ponder" removed (text patch of a temporary copy) must FAIL (2)
  4. tests/t_stress.py and tests/t_uci.py (ENGINE = the fuzz build) pass.
usage:  python3 tests/t_ttfuzz.py [--positions 150] [--quick]      (Linux: needs nasm, ld and python-chess)      Exit code 0 = everything passed.
"""
import os, re, subprocess, sys, tempfile, shutil, random
import chess

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
args = sys.argv[1:]
NPOS = int(args[args.index("--positions") + 1]) if "--positions" in args else 150
QUICK = "--quick" in args
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

def build(tmp, name, extra, patch=None):
    zeval_asm = os.path.join(tmp, name + ".asm")
    with open(zeval_asm, "w", newline="") as o:
        for p in PARTS:
            t = open(os.path.join(ROOT, "src", p), newline="").read()
            if patch and p == patch[0]:
                assert t.count(patch[1]) == 1, "patch target not found"; t = t.replace(patch[1], patch[2])
            o.write(t)
    ob = os.path.join(tmp, name + ".o")
    sh(["nasm", "-f", "elf64"] + extra + [zeval_asm, "-o", ob])
    exe = os.path.join(tmp, name)
    sh(["ld", ob, "-o", exe])
    return exe

def positions(n, seed):
    rnd = random.Random(seed); out = []
    while len(out) < n:
        b = chess.Board()
        for _ in range(rnd.randint(2, 90)):
            mv = list(b.legal_moves)
            if not mv: break
            b.push(rnd.choice(mv))
        if not b.is_game_over(): out.append(b.fen())
    return out

def play(exe, fens, plan):
    """plan = list of 'go' argument strings, run for every position.  -> (list of (fen, go, bestmove, ponder, nodes), problems)"""
    cmds = ["uci"]
    for f in fens:
        for g in plan: cmds += [f"position fen {f}", f"go {g}"]
    cmds.append("quit")
    try:
        r = subprocess.run([exe], input="\n".join(cmds) + "\n", capture_output=True, text=True, timeout=900)
    except subprocess.TimeoutExpired:
        return [], ["engine hung (timeout)"]
    res, problems, nodes = [], [], 0
    for l in r.stdout.splitlines():
        m = re.search(r" nodes (\d+)", l)
        if l.startswith("info") and m: nodes = int(m[1])
        if l.startswith("bestmove"):
            t = l.split()
            res.append((t[1], t[3] if len(t) >= 4 and t[2] == "ponder" else None, nodes)); nodes = 0
    jobs = [(f, g) for f in fens for g in plan]
    if len(res) != len(jobs): problems.append(f"{len(res)} bestmove lines for {len(jobs)} searches (exit {r.returncode})")
    out = []
    for (f, g), (bm, pm, n) in zip(jobs, res):
        out.append((f, g, bm, pm, n))
    return out, problems

def legality(rows):
    problems = []; nponder = 0
    for f, g, bm, pm, n in rows:
        b = chess.Board(f)
        try: m = chess.Move.from_uci(bm)
        except Exception: m = None
        if m is None or m not in b.legal_moves: problems.append(f"ILLEGAL bestmove {bm} in {f} ({g})"); continue
        if pm:
            nponder += 1
            b.push(m)
            try: p = chess.Move.from_uci(pm)
            except Exception: p = None
            if p is None or p not in b.legal_moves: problems.append(f"ILLEGAL ponder {pm} after {bm} in {f} ({g})")
    return problems, nponder

PLAN = ["depth 1", "depth 2", "depth 4", "movetime 15"] if not QUICK else ["depth 1", "depth 2", "depth 4"]
tmp = tempfile.mkdtemp(prefix="t_ttfuzz_")
try:
    fens = positions(NPOS, 4242)
    normal = build(tmp, "zeval_normal", [])
    fuzz = build(tmp, "zeval_fuzz", ["-DTTFUZZ"])
    # 1. the fuzz does something
    rows_n, pn = play(normal, fens[:40], ["depth 5"])
    rows_f, pf = play(fuzz, fens[:40], ["depth 5"])
    diff = sum(1 for a, b in zip(rows_n, rows_f) if a[4] != b[4])
    check(not pn and not pf and diff > 0, f"fuzz is active: the node count differs from the normal build in {diff} of 40 searches at depth 5")
    # 2. nothing illegal is played or announced
    rows, pr = play(fuzz, fens, PLAN)
    lp, nponder = legality(rows)
    check(not pr and not lp, f"fuzz build, {len(fens)} random positions x {len(PLAN)} searches ({', '.join(PLAN)}): all bestmoves legal, all {nponder} announced ponder moves legal" + ("" if not (pr or lp) else f"  [{(pr + lp)[:3]}]"))
    # 3. self-test (a broken variant must be caught): no legality check before 'ponder'
    old = """    mov edi, ebp
    call move_is_legal
    test eax, eax
    jz .pundo
    OBS s_ponder, 8"""
    new = """    mov edi, ebp
    OBS s_ponder, 8"""
    mut = build(tmp, "zeval_mutant", ["-DTTFUZZ"], patch=("p5_main.asm", old, new))
    rows_m, pm_ = play(mut, fens, PLAN)
    lpm, _ = legality(rows_m)
    check(len(lpm) > 0, f"self-test: the fuzz build WITHOUT the legality check before 'ponder' is caught ({len(lpm)} illegal moves found, e.g. {lpm[0] if lpm else '-'})")
    # 4. the existing batteries on the fuzz build
    if not QUICK:
        env = dict(os.environ, ENGINE=fuzz)
        for script, extra in (("t_stress.py", ["7", "60", "40"]), ("t_uci.py", [])):
            try:
                r = subprocess.run([sys.executable, os.path.join(HERE, script)] + extra, capture_output=True, text=True, env=env, cwd=ROOT, timeout=1200)
            except subprocess.TimeoutExpired:
                check(False, f"{script} on the fuzz build: timeout (1200 s)"); continue
            tail = (r.stdout.strip().splitlines() or ["(no output)"])[-1]
            check(r.returncode == 0 and ("FAIL" not in r.stdout.replace("0 FAILED", "")) and (script != "t_stress.py" or "issues=0" in r.stdout), f"{script} on the fuzz build: {tail}")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
print("ALL PASSED" if not bad_count else f"{bad_count} FAILED")
sys.exit(1 if bad_count else 0)
