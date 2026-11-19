#!/usr/bin/env python3
"""CH_SHARED: do the helper threads really work in the main thread's continuation-history table, and nothing else changes?

Linux only (needs nasm, ld).  Builds two TEST binaries in a temp dir (release flavour, 4 units, -DCHSHTEST): CH_SHARED=1 and CH_SHARED=0.  The
console command "chsum" of a CHSHTEST build prints, for every unit, the number of non-zero entries of that unit's own continuation table (chtab).

  1. shared build, Threads = 4, timed searches on 8 positions (the first golden positions whose Threads = 1 search fills the table at all): the helpers' own tables stay empty (0 entries) after every search, the main
     thread's table is not empty, and with 4 threads it holds more entries than after the same fixed-depth search with Threads = 1 (the helpers
     write into it); "ucinewgame" empties it again
  2. Threads = 1: the shared and the non-shared build search the same tree (node counts of fixed-depth searches identical, 10 positions)
  3. self-test: in the non-shared build the helpers' own tables fill up (so check 1 would see a helper that writes its own table)

usage: python3 tests/t_chshared.py          exit code 0 = everything passed, 1 = a check failed, 2 = build / engine problem
"""
import os, re, subprocess, sys, tempfile, shutil, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _eng import resolve, expect, finish, start

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
NU = 4
PARTS = ["p1_data.asm", "eval.asm", "p2_core.asm", "p2b_bb_data.inc", "p2b_bb.asm", "p3_cmd.asm", "p4_search.asm", "p5_main.asm", "p6_win.asm", "p7_units.asm"]
FENS = json.load(open(os.path.join(HERE, "golden_depth8.json")))["fens"]

def sh(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode: print("FAILED:", " ".join(cmd), "\n", r.stdout[-800:], r.stderr[-1500:]); sys.exit(2)

def build(tmp, name, flags):
    asm_path = os.path.join(tmp, name + ".asm")
    with open(asm_path, "wb") as o:
        for p in PARTS: o.write(open(os.path.join(ROOT, "src", p), "rb").read())
    objs = []
    for k in range(NU):
        ob = os.path.join(tmp, f"{name}_u{k}.o")
        sh(["nasm", "-f", "elf64", f"-DUNIT={k}", f"-DNUNITS={NU}", "-DCHSHTEST"] + flags + [asm_path, "-o", ob]); objs.append(ob)
    exe = os.path.join(tmp, name); sh(["ld"] + objs + ["-o", exe]); return exe

class Eng:
    def __init__(s, exe):
        s.p = start(resolve(exe)); s.send("uci"); expect(s.p, "uciok", 30)
    def send(s, *c):
        for x in c: s.p.stdin.write(x + "\n")
        s.p.stdin.flush()
    def ready(s): s.send("isready"); expect(s.p, "readyok", 60)
    def go(s, fen, cmd):
        s.send("position fen " + fen, cmd); L = []; expect(s.p, "bestmove", 120, collect=L)
        n = [int(m[1]) for l in L for m in [re.search(r" nodes (\d+)", l)] if l.startswith("info depth") and m]
        return n[-1] if n else 0
    def counts(s):
        s.send("chsum"); l = expect(s.p, "chsum", 30)
        return [int(x) for x in l.split()[1:]]
    def close(s):
        s.send("quit"); finish(s.p, 10)

def main():
    fails = 0
    def check(ok, msg):
        nonlocal fails
        print(("PASS " if ok else "FAIL ") + msg); fails += (not ok)
    tmp = tempfile.mkdtemp()
    try:
        sh1 = build(tmp, "shared", ["-DCH_SHARED=1"]); sh0 = build(tmp, "own", ["-DCH_SHARED=0"])
        # 1. shared table
        e = Eng(sh1); bad = []; more = 0; det = []; used = 0
        for f in FENS:
            if used == 8: break
            e.send("setoption name Threads value 1", "ucinewgame"); e.ready(); e.go(f, "go depth 9"); c1 = e.counts()
            if c1[0] == 0: continue                     # no quiet cutoff at all (e.g. a mate in one): nothing to compare
            used += 1
            e.send("setoption name Threads value 4", "ucinewgame"); e.ready(); e.go(f, "go depth 9"); e.go(f, "go movetime 150"); c4 = e.counts()
            if any(c4[1:]) or any(c1[1:]) or c4[0] == 0: bad.append((f, c1, c4))
            more += c4[0] > c1[0]; det.append((c1[0], c4[0]))
        e.send("ucinewgame"); e.ready(); c = e.counts(); e.close()
        check(not bad, "1. shared build: the helpers' own tables stay empty, the main table fills (per position: Threads=1 entries, Threads=4 entries) %s" % (det if not bad else bad[:2]))
        check(more == 8, "1. with 4 threads the shared table holds more entries than with 1 (helpers write into it): %d of 8 positions" % more)
        check(c == [0] * NU, "1. ucinewgame empties the shared table: %s" % c)
        # 2. Threads = 1 identical
        a = Eng(sh1); b = Eng(sh0); na = []; nb = []
        for f in FENS[:10]:
            a.send("ucinewgame"); a.ready(); na.append(a.go(f, "go depth 8"))
            b.send("ucinewgame"); b.ready(); nb.append(b.go(f, "go depth 8"))
        a.close(); b.close()
        check(na == nb, "2. Threads = 1: CH_SHARED=1 and =0 search the same trees (%d nodes over 10 positions)" % sum(na))
        # 3. self-test
        e = Eng(sh0); e.send("setoption name Threads value 4", "ucinewgame"); e.ready(); e.go(FENS[0], "go movetime 300"); c = e.counts(); e.close()
        check(all(x > 0 for x in c), "3. self-test: without sharing every helper fills its own table: %s" % c)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("ALL PASSED" if not fails else "%d FAILED" % fails); sys.exit(1 if fails else 0)

main()
