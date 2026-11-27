#!/usr/bin/env python3
"""EV_SCMG check: the endgame scale factor on the mg part at low phase, on by default (see EV_SCMG in the header of src/eval.asm).

  1. model     -DEV_SCMG=1, also with -DHWPOPCNT=0: the tuner's model (tuner/model.py, which reads the switch from the build's evalcfg)
               reproduces the engine's eval on random positions and on random endgame positions (tuner/check_eval.py --endgame, pawn
               endings included)
  2. phase     the eval of every position with phase >= 24 (full material) is the same as without the switch
  3. verify    -DEV_SCMG=1 -DSFASTVERIFY: EV_SFAST and the switch together (exit code 17 = EV_SFAST took 64 where ev_scale gives another
               factor); the eval of random endgame positions and searches of random positions
  --selftest   breaks the mg factor three ways; check 1 must catch each one
Linux only: needs nasm, ld, python-chess.   usage:  python3 tests/t_scmg.py [--endgame 3000] [--nodes 8000] [--selftest]
Exit code 0 = everything passed, 1 = a check failed, 2 = a tool could not be run."""
import os, random, subprocess, sys, tempfile, shutil
import chess
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, "..", "tuner"))
args = sys.argv[1:]
def opt(name, default):
    if name in args:
        i = args.index(name); v = type(default)(args[i + 1]); del args[i:i + 2]; return v
    return default
NEND = opt("--endgame", 3000); NODES = opt("--nodes", 8000); SELFTEST = "--selftest" in args
import t_ttfirst as T          # build(), positions(), verify_run() ; importing does not run its checks
from check_eval import endgame_positions
CHECK = os.path.join(HERE, "..", "tuner", "check_eval.py")
ON = ["-DEV_SCMG=1"]
PW = {chess.KNIGHT: 1, chess.BISHOP: 1, chess.ROOK: 2, chess.QUEEN: 4}
fails = 0
def check(ok, msg):
    global fails
    print(("PASS " if ok else "FAIL ") + msg, flush=True)
    if not ok: fails += 1

def model_check(engine, n, nend):
    r = subprocess.run([sys.executable, CHECK, "--engine", engine, "--n", str(n), "--endgame", str(nend)], capture_output=True, text=True)
    line = next((l for l in r.stdout.splitlines() if l.startswith("check_eval:")), r.stdout.strip()[-200:] or r.stderr.strip()[-200:])
    return r.returncode == 0, line.split("  (")[0]

def evals(engine, fens):
    inp = "".join(f"position fen {f}\neval\n" for f in fens) + "quit\n"
    p = subprocess.run([engine], input=inp, capture_output=True, text=True)
    return p.returncode, [int(l.split()[1]) for l in p.stdout.splitlines() if l.startswith("eval ")]

def game_positions(n, seed):
    rnd = random.Random(seed); out = []
    while len(out) < n:
        b = chess.Board()
        for _ in range(rnd.randint(20, 200)):
            ms = list(b.legal_moves)
            if not ms: break
            caps = [m for m in ms if b.is_capture(m)]
            b.push(rnd.choice(caps) if caps and rnd.random() < 0.5 else rnd.choice(ms))
            if rnd.random() < 0.1: out.append(b.fen())
    return out[:n]

def verify(engine, fens, sfens):
    rc, v = evals(engine, fens)
    if rc != 0 or len(v) != len(fens): return False, rc
    ok, rc, _ = T.verify_run(engine, sfens, NODES)
    return ok, rc

BREAKS = [   # (name, old, new, how): text changes of eval.asm; how = "model" (check 1) or "verify" (check 3)
    ("SCMG: factor without the scale factor", "    lea r9d, [rax+r8]\n", "    lea r9d, [rax+64]\n", "model"),
    ("SCMG: factor from 63 - sf", "    mov ecx, 64\n    sub ecx, r8d\n", "    mov ecx, 63\n    sub ecx, r8d\n", "model"),
    ("SCMG: sf = 64 shortcut taken for every factor", "    cmp r8d, 64\n    je %%mgf\n", "    cmp r8d, 0\n    jne %%mgf\n", "model"),
]

def main():
    tmp = tempfile.mkdtemp()
    try:
        efens = endgame_positions(min(NEND, 3000), 11); sfens = T.positions(60, 35)
        if SELFTEST:
            ok, line = model_check(T.build(tmp, "good", ON), 300, min(NEND, 2000))
            check(ok, f"selftest: the unbroken switch passes ({line})")
            ok, rc = verify(T.build(tmp, "goodv", ON + ["-DSFASTVERIFY"]), efens, sfens)
            check(ok, f"selftest: the unbroken switch passes the verify build ({len(efens)} evals, {len(sfens)} searches)")
            for name, old, new, how in BREAKS:
                if how == "model":
                    ok, line = model_check(T.build(tmp, "broken", ON, ("eval.asm", old, new)), 300, min(NEND, 2000))
                    check(not ok, f"selftest: caught  [{name}]  ({line})")
                else:
                    ok, rc = verify(T.build(tmp, "broken", ON + ["-DSFASTVERIFY"], ("eval.asm", old, new)), efens, sfens)
                    check(not ok and rc == 17, f"selftest: caught  [{name}]  (exit code {rc})")
            print("SELFTEST", "FAILED" if fails else "PASSED"); return 1 if fails else 0
        for name, flags in (("EV_SCMG=1", ON), ("EV_SCMG=1, HWPOPCNT=0", ON + ["-DHWPOPCNT=0"])):
            ok, line = model_check(T.build(tmp, "m", flags), 1000, NEND)
            check(ok, f"{name}: the model reproduces the engine ({line})")
        fens = [f for f in game_positions(20000, 3) if sum(PW.get(p.piece_type, 0) for p in chess.Board(f).piece_map().values()) >= 24]
        (ra, a), (rb, b) = evals(T.build(tmp, "off", ["-DEV_SCMG=0"]), fens), evals(T.build(tmp, "on", ON), fens)
        check(ra == rb == 0 and len(a) == len(fens) and a == b, f"the same eval as without the switch on {len(fens)} positions with phase >= 24")
        ok, rc = verify(T.build(tmp, "v", ON + ["-DSFASTVERIFY"]), endgame_positions(NEND, 12), T.positions(150, 36))
        check(ok, f"SFASTVERIFY with EV_SCMG: {NEND} endgame evals and 150 searches (go nodes {NODES})" + ("" if ok else f": exit code {rc}"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("ALL PASSED" if not fails else f"{fails} CHECK(S) FAILED")
    return 1 if fails else 0

if __name__ == "__main__":
    sys.exit(main())
