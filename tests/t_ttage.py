#!/usr/bin/env python3
"""Regression test for the ttage wrap: a fixed-depth search must NOT depend on
how many searches the engine process has run before.

ttage (the age stamp of the transposition table entries) used to count the searches modulo 64 and reach 0.  ucinewgame empties the table but does not reset ttage,
and an empty entry has age field 0, so in the searches where ttage == 0 (the 64th, 128th, ... since the start of the process) tt_store counted the empty entries of a
bucket as "current search" instead of "old", picked other victims and the tree differed slightly (a few nodes), also in --nodes self-play against a fresh engine.
The age of an entry in use now runs 1..63 and is never 0.  This script repeats one search 130 times with ucinewgame in front of every run:
  * no run differs from the others                -> "no difference (the issue is fixed)", exit 0
  * only the runs 64 / 128 differ                 -> the old bug is back: exit 1
  * any other difference                          -> a different bug: exit 1
(A binary without the fix shows the old result: the runs 64 and 128 differ.)
usage: python3 tests/t_ttage.py [engine]
Exit code 0 = no difference, 1 = difference, 2 = the engine crashed / hung / was not found.
"""
import subprocess, sys, re, os
from collections import Counter
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _eng import ENGINE, resolve, expect, finish, start
eng = resolve(sys.argv[1]) if len(sys.argv) > 1 else ENGINE
FEN = "r4rk1/1pp1qppp/p1np1n2/2b1p1B1/2B1P1b1/P1NP1N2/1PP1QPPP/R4RK1 w - - 0 10"
RUNS = 130
p = start(eng)
def send(s): p.stdin.write(s + "\n"); p.stdin.flush()
send("uci"); expect(p, "uciok", 30)
res = []
for i in range(RUNS):
    send("ucinewgame"); send("isready"); expect(p, "readyok", 60)
    send(f"position fen {FEN}"); send("go depth 7")
    lines = []; expect(p, "bestmove", 300, collect=lines)
    nodes = 0
    for l in lines:
        m = re.search(r"info depth 7 .*nodes (\d+)", l)
        if m: nodes = int(m[1])
    res.append((nodes, lines[-1].split()[1]))
send("quit"); finish(p, 30)
maj = Counter(res).most_common(1)[0][0]
if maj[0] == 0: sys.exit("ERROR: no 'info depth 7 ... nodes N' line seen: nothing to compare")      # all-zero node counts would be 'identical' for the wrong reason
diff = [i + 1 for i, r in enumerate(res) if r != maj]
print(f"{RUNS} identical searches (ucinewgame before each): majority {maj}; runs that differ: {diff}")
if not diff: print("no difference (the issue is fixed)"); sys.exit(0)
if all(d % 64 == 0 for d in diff): print("FAIL: only the runs where ttage wraps to 0 differ - the ttage bug is back"); sys.exit(1)
print("FAIL: differences outside the ttage wrap"); sys.exit(1)
