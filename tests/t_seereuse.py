#!/usr/bin/env python3
"""CAPIDX / QSSEE check: two speed-only changes of score_moves and quiescence (see CAPIDX and QSSEE in src/p1_data.asm).

Both keep the search exactly as it is, so:
  1. golden    both off, CAPIDX alone and both on (the default) reproduce golden_depth8.json bit for bit; with -DSTAGED=0 both on and
               off reproduce golden_depth8_nostaged.json, with -DSTAGED=0 -DTTFIRST=1 golden_depth8_ttfirst.json (t_golden.py).
  2. verify    -DQSSEE=1 -DQSSEEVERIFY: every capture that score_moves marks "SEE < 0" is also given to see_ge by quiescence; the answer must be
               "no" (exit code 14 = a capture would have been pruned that see_ge accepts).  Random positions and the fixed ones of t_ttfirst.py.
  --selftest   breaks the mark two ways (text patches of a temporary copy); check 2 must catch both (exit code 14).
Linux only: needs nasm, ld, python-chess.   usage:  python3 tests/t_seereuse.py [--positions 150] [--nodes 8000] [--selftest]
Exit code 0 = everything passed, 1 = a check failed, 2 = a tool could not be run."""
import os, subprocess, sys, tempfile, shutil
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
args = sys.argv[1:]
def opt(name, default):
    if name in args:
        i = args.index(name); v = type(default)(args[i + 1]); del args[i:i + 2]; return v
    return default
NPOS = opt("--positions", 150); NODES = opt("--nodes", 8000); SELFTEST = "--selftest" in args
import t_ttfirst as T          # positions(), build(), run() ; importing does not run its checks
fails = 0
def check(ok, msg):
    global fails
    print(("PASS " if ok else "FAIL ") + msg, flush=True)
    if not ok: fails += 1

def golden(engine, ref=None):
    cmd = [sys.executable, os.path.join(HERE, "t_golden.py"), engine] + ([os.path.join(HERE, ref)] if ref else [])
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode == 0 and "IDENTICAL" in r.stdout, (r.stdout.strip().splitlines() or ["?"])[-1]

BREAKS = [   # (name, file, text, replacement): each marks a capture that see_ge(0) accepts
    ("mark without the threshold test (t > 0 too)", "p4_search.asm",
     "    jl .nmk                     ; captab >> CAP_SEESH < CAP_SEEB: t > 0, SEE < 0 is not known\n", ""),
    ("mark the good captures as well", "p4_search.asm",
     "%if CAPIDX_ON\n    add esi, ebp                ; the value kept at .ch0 (= what caph_add would look up)\n",
     "%if QSSEE_ON\n    cmp dword [smq], 0\n    je .nmk2\n    or dword [r8+rbx*4], 0x10000\n.nmk2:\n%endif\n"
     "%if CAPIDX_ON\n    add esi, ebp                ; the value kept at .ch0 (= what caph_add would look up)\n"),
]

def main():
    tmp = tempfile.mkdtemp()
    try:
        if SELFTEST:
            fens = T.positions(NPOS, 31)
            good = T.build(tmp, "good", ["-DQSSEE=1", "-DQSSEEVERIFY"])
            ok, rc, why = T.verify_run(good, fens, NODES)
            check(ok, f"selftest: the unbroken mark passes on {len(fens)} positions")
            for name, part, old, new in BREAKS:
                e = T.build(tmp, "broken", ["-DQSSEE=1", "-DQSSEEVERIFY"], (part, old, new))
                ok, rc, why = T.verify_run(e, fens, NODES)
                check(not ok and rc == 14, f"selftest: caught  [{name}]  (exit code {rc})")
            print("SELFTEST", "FAILED" if fails else "PASSED"); return 1 if fails else 0
        for name, flags, ref in (("CAPIDX=0 QSSEE=0", ["-DCAPIDX=0", "-DQSSEE=0"], None),
                                 ("CAPIDX alone", ["-DCAPIDX=1", "-DQSSEE=0"], None),
                                 ("CAPIDX+QSSEE (default)", ["-DCAPIDX=1", "-DQSSEE=1"], None),
                                 ("STAGED=0, CAPIDX=0 QSSEE=0", ["-DSTAGED=0", "-DCAPIDX=0", "-DQSSEE=0"], "golden_depth8_nostaged.json"),
                                 ("STAGED=0, CAPIDX+QSSEE", ["-DSTAGED=0", "-DCAPIDX=1", "-DQSSEE=1"], "golden_depth8_nostaged.json"),
                                 ("TTFIRST alone, CAPIDX+QSSEE", ["-DSTAGED=0", "-DTTFIRST=1", "-DCAPIDX=1", "-DQSSEE=1"], "golden_depth8_ttfirst.json"),
                                 ("TTFIRST alone, CAPIDX=0 QSSEE=0", ["-DSTAGED=0", "-DTTFIRST=1", "-DCAPIDX=0", "-DQSSEE=0"], "golden_depth8_ttfirst.json")):
            e = T.build(tmp, "g", flags)
            ok, line = golden(e, ref)
            check(ok, f"{name} reproduces {ref or 'golden_depth8.json'} ({line})")
        ver = T.build(tmp, "v", ["-DQSSEE=1", "-DQSSEEVERIFY"])
        fens = T.positions(NPOS, 31)
        bad = None
        for i in range(0, len(fens), 15):
            part = fens[i:i + 15]
            rc, out = T.run(ver, part, f"go nodes {NODES}")
            if rc != 0 or out.count("bestmove ") != len(part): bad = (rc, i); break
        check(bad is None, f"a capture marked SEE < 0 is refused by see_ge too, {len(fens)} positions (go nodes {NODES})" + ("" if bad is None else f": exit code {bad[0]} near position {bad[1]}"))
        rc, out = T.run(ver, fens[:40], "go depth 9", timeout=1800)
        check(rc == 0 and out.count("bestmove ") == 40, "the same at depth 9 on 40 positions")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("ALL PASSED" if not fails else f"{fails} CHECK(S) FAILED")
    return 1 if fails else 0

if __name__ == "__main__":
    sys.exit(main())
