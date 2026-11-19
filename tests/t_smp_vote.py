#!/usr/bin/env python3
"""Lazy SMP - the best-thread vote (SMP_VOTE, smp_vote in src/p7_units.asm): is the bestmove the one the rules choose?

Linux only (needs nasm, ld).  Builds a TEST binary in a temp dir: the release flavour (SMP = 1, 8 units) with -DSMP_VOTE=1 -DVOTETEST.  In that build
smp_vote prints its candidates ("info string vote <unit> <move> <score> <root line length>", unit order) and its choice ("info string votebest <unit>").
The script computes the choice again from the printed candidates with the rules written down here (independent of the assembly) and checks:

  1. the same unit is chosen, for every search (timed searches with 4 and 8 threads on the 60 golden positions and a few mate positions);
  2. the bestmove is the move of the chosen unit, and the last info line before it has a pv that starts with that move;
  3. "go depth N" is never voted on (no vote lines: a fixed-depth search keeps the main thread's result);
  4. self-test: a build in which equal votes do not look at the root line length (jg -> jge) must FAIL check 1.

The rules (lowest score = min over the candidates; candidates = the main thread and every helper with a root result of this search):
  vote(move) = sum of (score - lowest + 14) over the candidates that chose the move
  best = first candidate; for each further candidate t:
     |score(best)| > MATEB:  t replaces best only if score(t) > score(best) and |score(t)| > MATEB
     else: t replaces best if score(t) > MATEB, or score(t) >= -MATEB and (vote(t) > vote(best) or votes equal and len(t) > len(best))

usage: python3 tests/t_smp_vote.py [--exe ENGINE] [--positions 60]     exit code 0 = everything passed (2 = could not build / run)
       --exe: a ready-made VOTETEST build (build/build.sh with NASMFLAGS="-DSMP_VOTE=1 -DVOTETEST"); no building, no self-test.
"""
import os, re, subprocess, sys, tempfile, shutil, json
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _eng import resolve, expect, finish, start

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
args = sys.argv[1:]
def opt(name, default): return type(default)(args[args.index(name) + 1]) if name in args else default
NPOS = opt("--positions", 60); EXE_ARG = args[args.index("--exe") + 1] if "--exe" in args else None
NU = 8; MATEB = 29800
MATES = ["6k1/5ppp/8/8/8/8/5PPP/3R2K1 w - - 0 1", "r1bqkb1r/pppp1ppp/2n2n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR w KQkq - 4 4",
         "2r3k1/5ppp/8/8/8/8/5PPP/R5K1 b - - 0 1", "6k1/5ppp/8/8/8/5q2/5PPP/6K1 b - - 0 1", "7k/8/5K2/6Q1/8/8/8/8 w - - 0 1"]

def sh(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode: print("FAILED:", " ".join(cmd), "\n", r.stdout[-800:], r.stderr[-1500:]); sys.exit(2)

def build(tmp, srcdir, name):
    asm_path = os.path.join(tmp, name + ".asm")
    parts = ["p1_data.asm", "eval.asm", "p2_core.asm", "p2b_bb_data.inc", "p2b_bb.asm", "p3_cmd.asm", "p4_search.asm", "p5_main.asm", "p6_win.asm", "p7_units.asm"]
    with open(asm_path, "wb") as o:
        for p in parts: o.write(open(os.path.join(srcdir, p), "rb").read())
    objs = []
    for k in range(NU):
        ob = os.path.join(tmp, f"{name}_u{k}.o")
        sh(["nasm", "-f", "elf64", f"-DUNIT={k}", f"-DNUNITS={NU}", "-DSMP_VOTE=1", "-DVOTETEST", asm_path, "-o", ob]); objs.append(ob)
    exe = os.path.join(tmp, name); sh(["ld"] + objs + ["-o", exe]); return exe

def choose(c):
    """c = [(unit, move, score, length)] in unit order -> the unit the rules choose"""
    lo = min(x[2] for x in c); vote = defaultdict(int)
    for u, m, s, l in c: vote[m] += s - lo + 14
    b = c[0]
    for t in c[1:]:
        if abs(b[2]) > MATEB:
            if t[2] > b[2] and abs(t[2]) > MATEB: b = t
        elif t[2] > MATEB or (t[2] >= -MATEB and (vote[t[1]] > vote[b[1]] or (vote[t[1]] == vote[b[1]] and t[3] > b[3]))): b = t
    return b[0]

def mvnum(s):
    """UCI move -> the engine's move code (from | to << 7 | promo << 14, 0x88 squares): only from and to are compared"""
    f = (ord(s[0]) - 97) + 16 * (int(s[1]) - 1); t = (ord(s[2]) - 97) + 16 * (int(s[3]) - 1)
    return f, t

def run(exe, fens):
    """-> stats dict and a list of problems"""
    p = start(resolve(exe)); bad = []; st = {"searches": 0, "voted": 0, "helper chosen": 0, "candidates": 0}
    def send(s): p.stdin.write(s + "\n"); p.stdin.flush()
    try:
        send("uci"); expect(p, "uciok", 30)
        for th, cmds in ((4, ("go movetime 40", "go nodes 30000")), (8, ("go movetime 60", "go depth 6"))):
            send("setoption name Threads value %d" % th); send("ucinewgame"); send("isready"); expect(p, "readyok", 30)
            for f in fens:
                for c in cmds:
                    send("position fen " + f); send(c); L = []; expect(p, "bestmove", 60, collect=L)
                    st["searches"] += 1
                    cand = [tuple(int(x) for x in l.split()[3:7]) for l in L if l.startswith("info string vote ")]
                    vb = [int(l.split()[3]) for l in L if l.startswith("info string votebest ")]
                    bm = L[-1].split()[1]
                    if c.startswith("go depth"):
                        if cand or vb: bad.append(("go depth was voted on", f))
                        continue
                    if not vb: continue                     # no helper took part (or none had a result): no vote
                    st["voted"] += 1; st["candidates"] += len(cand)
                    want = choose(cand)
                    if want != vb[0]: bad.append(("choice", f, c, cand, "engine", vb[0], "rules", want)); continue
                    if want: st["helper chosen"] += 1
                    mv = [x for x in cand if x[0] == want][0][1]
                    if (mv & 127, (mv >> 7) & 127) != mvnum(bm): bad.append(("bestmove is not the chosen unit's move", f, c, bm, mv))
                    pv = [l for l in L if l.startswith("info depth") and " pv " in l]
                    if pv and pv[-1].split(" pv ")[1].split()[0] != bm: bad.append(("last pv does not start with the bestmove", f, c, pv[-1][-60:], bm, "unit", want))
        send("quit"); finish(p, 10)
    finally:
        if p.poll() is None: p.kill()
    return st, bad

def main():
    fails = 0
    def check(ok, msg):
        nonlocal fails
        print(("PASS " if ok else "FAIL ") + msg); fails += (not ok)
    fens = json.load(open(os.path.join(HERE, "golden_depth8.json")))["fens"][:NPOS] + MATES
    tmp = tempfile.mkdtemp()
    try:
        exe = EXE_ARG or build(tmp, os.path.join(ROOT, "src"), "vote")
        st, bad = run(exe, fens)
        check(not bad and st["voted"] > 0, "vote = the rules, bestmove and last pv = the chosen unit, no vote for go depth: %s" % st + ("" if not bad else "  " + str(bad[:3])))
        if not EXE_ARG:
            bsrc = os.path.join(tmp, "broken"); shutil.copytree(os.path.join(ROOT, "src"), bsrc)
            pth = os.path.join(bsrc, "p7_units.asm"); s = open(pth).read()
            old = "    cmp r8, rax\n    jg .take\n"
            if s.count(old) != 1: print("FAIL self-test: patch point not found"); sys.exit(1)
            open(pth, "w").write(s.replace(old, "    cmp r8, rax\n    jge .take\n"))
            st2, bad2 = run(build(tmp, bsrc, "broken_exe"), fens)
            check(any(b[0] == "choice" for b in bad2), "self-test: a vote that ignores the root line length on equal votes is caught (%d differences)" % len(bad2))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("ALL PASSED" if not fails else "%d FAILED" % fails); sys.exit(1 if fails else 0)

main()
