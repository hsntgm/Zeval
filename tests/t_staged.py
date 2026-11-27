#!/usr/bin/env python3
"""STAGED check: the staged move picking of search() (see STAGED in src/p1_data.asm).

  1. golden    -DSTAGED=1 (the default) reproduces tests/golden_depth8.json bit for bit (t_golden.py).
  2. verify    -DSTAGED=1 -DSTAGEVERIFY: at every node that went through all four stages, every move of gen's list was picked exactly once
               (exit code 15: a move missing or picked twice, 16: more or fewer picks than moves).  Random positions and the fixed ones of
               t_ttfirst.py, at go nodes and at depth 9.
  3. fuzz      -DSTAGED=1 -DSTGFUZZ -DTTFUZZ: the killers of stage 3 and the TT moves are garbage; every search must end with a legal bestmove.
  --selftest   builds five broken versions (text patches of a temporary copy); check 2 must catch each one (exit code 15 or 16).
Linux only: needs nasm, ld, python-chess.   usage:  python3 tests/t_staged.py [--positions 150] [--nodes 30000] [--quick] [--selftest]
Exit code 0 = everything passed, 1 = a check failed, 2 = a tool could not be run."""
import os, re, subprocess, sys, tempfile, shutil
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
args = sys.argv[1:]
def opt(name, default):
    if name in args:
        i = args.index(name); v = type(default)(args[i + 1]); del args[i:i + 2]; return v
    return default
NPOS = opt("--positions", 150); NODES = opt("--nodes", 30000)
QUICK = "--quick" in args; SELFTEST = "--selftest" in args
import chess
import t_ttfirst as T          # positions(), build(), run(), verify_run() ; importing does not run its checks
fails = 0
def check(ok, msg):
    global fails
    print(("PASS " if ok else "FAIL ") + msg, flush=True)
    if not ok: fails += 1

BREAKS = [   # (name, file, text, replacement): each makes a staged node search a move twice or not at all
    ("stage 4 does not move the earlier picks to the front", "p4_search.asm",
     "    cmp r8d, [rbp+L_NPK]\n    jge .s4e\n", "    jmp .s4e\n"),
    ("stage 4 starts one move too late", "p4_search.asm",
     ".s4e:\n    mov eax, [rbp+L_NPK]\n    mov [rbp+L_IDX], eax\n", ".s4e:\n    mov eax, [rbp+L_NPK]\n    inc eax\n    mov [rbp+L_IDX], eax\n"),
    ("the picks of stage 3 are not recorded", "p4_search.asm",
     "    cmp ecx, 4\n    je .pk0\n    test ecx, ecx\n", "    cmp ecx, 3\n    jae .pk0\n    test ecx, ecx\n"),
    ("stage 2 keeps the TT move", "p4_search.asm",
     "    cmp eax, r9d\n    je .s2n                         ; the TT move: searched in stage 1\n",
     "    cmp eax, r9d\n    nop\n"),
    ("stage 3 takes a killer that is a capture", "p4_search.asm",
     "    jne .r                          ; a capture: stage 2\n", "    nop\n"),
]

def depth_run(engine, fens, depth, chunk=10):
    """True when every search at the given depth ended with exit code 0 and a bestmove"""
    for i in range(0, len(fens), chunk):
        part = fens[i:i + chunk]
        rc, out = T.run(engine, part, f"go depth {depth}", timeout=1800)
        if rc != 0 or out.count("bestmove ") != len(part):
            return False, rc, i
    return True, 0, -1

def main():
    fens = T.positions(NPOS, 11)
    tmp = tempfile.mkdtemp()
    try:
        if SELFTEST:
            good = T.build(tmp, "good", ["-DSTAGED=1", "-DSTAGEVERIFY"])
            ok, rc, why = T.verify_run(good, fens, NODES)
            check(ok, f"selftest: the unbroken stages pass on {len(fens)} positions")
            for name, part, old, new in BREAKS:
                e = T.build(tmp, "broken", ["-DSTAGED=1", "-DSTAGEVERIFY"], (part, old, new))
                ok, rc, why = T.verify_run(e, fens, NODES)
                check(not ok and rc in (15, 16), f"selftest: caught  [{name}]  (exit code {rc})")
            print("SELFTEST", "FAILED" if fails else "PASSED"); return 1 if fails else 0

        # 1. the staged search against its reference
        st = T.build(tmp, "staged", ["-DSTAGED=1"])
        gold = os.path.join(HERE, "golden_depth8.json")
        r = subprocess.run([sys.executable, os.path.join(HERE, "t_golden.py"), st, gold], capture_output=True, text=True)
        check(r.returncode == 0 and "IDENTICAL" in r.stdout, "STAGED 1 reproduces golden_depth8.json (" + (r.stdout.strip().splitlines() or ["?"])[-1] + ")")
        # 2. every move exactly once
        ver = T.build(tmp, "verify", ["-DSTAGED=1", "-DSTAGEVERIFY"])
        ok, rc, why = T.verify_run(ver, fens, NODES)
        check(ok, f"every move of a staged node picked exactly once, {len(fens)} positions (go nodes {NODES})" + ("" if ok else f": exit code {rc} at {why}"))
        n9 = 20 if QUICK else 40
        ok, rc, at = depth_run(ver, fens[:n9], 9)
        check(ok, f"the same at depth 9 on {n9} positions" + ("" if ok else f": exit code {rc} near position {at}"))
        # 3. garbage killers and TT moves
        fuzz = T.build(tmp, "fuzz", ["-DSTAGED=1", "-DSTGFUZZ", "-DTTFUZZ"])
        bad = 0
        for f in T.positions(40 if QUICK else 100, 23):
            for go in ("go depth 1", "go depth 3", "go nodes 4000"):
                rc, out = T.run(fuzz, [f], go, timeout=60)
                m = re.search(r"^bestmove (\S+)", out, re.M)
                b = chess.Board(f)
                if rc != 0 or not m or chess.Move.from_uci(m[1]) not in b.legal_moves:
                    bad += 1; print("  bad:", f, go, rc, m and m[1])
        check(bad == 0, "STGFUZZ + TTFUZZ (garbage killers and TT moves): legal bestmove, no crash")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("ALL PASSED" if not fails else f"{fails} CHECK(S) FAILED")
    return 1 if fails else 0

if __name__ == "__main__":
    sys.exit(main())
