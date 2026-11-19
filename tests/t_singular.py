#!/usr/bin/env python3
"""Singular extension (SING): correctness of the exclusion search and the counters of the singular test.   Needs a test build:
    sh tests/build1.sh /tmp/zeval_se -DSING=2 -DSEVERIFY -DSESTAT        (SING=1 also works: test and counters, no extension, no multi-cut)

SEVERIFY makes the engine exit at once with a fixed code when the exclusion search breaks one of its rules:
    8   the exclusion search is in check (the singular test is never made at a node in check)
    9   the exclusion search makes the excluded move
    10  the exclusion search reaches tt_store (it must leave the table alone)
    11  the exclusion search does not search the position of the node that started it (hash key differs)
SESTAT adds the console command "sestat" (counters of the main thread, see SESTAT in src/p4_search.asm).

Checks:
  1. fixed depth (default 10) on the golden positions: no SEVERIFY exit, exclusion searches did happen, and the counters agree with each other
     (the filter chain 0 >= 1 >= ... >= 5, searches = singular + multi-cut + neither, the depth classes sum up to the totals)
  2. determinism: the first three positions searched again in a fresh process give the same counters
  3. clock play: random positions searched with short movetimes, so that the stop flag also arrives inside exclusion searches; no SEVERIFY exit,
     a bestmove every time
The report prints the shares (singular / multi-cut / neither, nodes spent in exclusion searches) per depth class.  They are a diagnosis of the
margin (SE_M) and the depth limits (SE_MIND, SE_TTD), not a gate.

usage:  t_singular.py [ENGINE] [--depth 10] [--n 60] [--play 40]
exit codes: 0 all checks passed, 1 a check failed (SEVERIFY exit codes are named), 2 engine / harness could not be run (also: not a SESTAT build)."""
import argparse, json, os, random, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _eng import resolve, expect, finish, start

SEV = {8: "exclusion search in check", 9: "excluded move made", 10: "exclusion search stored to the table", 11: "exclusion search on another position"}
DCL = ["6-7", "8-9", "10-12", "13+"]
ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
ap.add_argument("engine", nargs="?", default=os.environ.get("ENGINE", "./zeval"))
ap.add_argument("--depth", type=int, default=10)
ap.add_argument("--n", type=int, default=60)
ap.add_argument("--play", type=int, default=40)
args = ap.parse_args()
HERE = os.path.dirname(os.path.abspath(__file__))
FENS = json.load(open(os.path.join(HERE, "golden_depth8.json")))["fens"][:args.n]
fails = []


def died(p, what):
    rc = p.wait()
    fails.append("%s: engine exited with code %d%s" % (what, rc, " (" + SEV[rc] + ")" if rc in SEV else ""))


def counters(fens, depth):
    p = start(resolve(args.engine))
    def send(s): p.stdin.write(s + "\n"); p.stdin.flush()
    send("uci"); expect(p, "uciok")
    nodes = 0
    for i, f in enumerate(fens):
        send("ucinewgame"); send("isready"); expect(p, "readyok")
        send("position fen " + f); send("go depth %d" % depth)
        lines = []
        try:
            expect(p, "bestmove", collect=lines)
        except Exception:
            died(p, "position %d" % i); return None, 0
        nodes += [int(re.search(r" nodes (\d+)", l)[1]) for l in lines if " nodes " in l][-1]
    send("sestat")
    l = expect(p, "sestat")
    send("quit"); finish(p)
    c = [int(x) for x in l.split()[1:]]
    if len(c) != 24:
        print("ERROR: \"sestat\" answered %d numbers, 24 expected" % len(c)); sys.exit(2)
    return c, nodes


# a SESTAT build answers "sestat"; a plain build ignores the unknown command (then the expect() below runs into its timeout)
p = start(resolve(args.engine))
p.stdin.write("uci\n"); p.stdin.flush(); expect(p, "uciok")
p.stdin.write("sestat\nisready\n"); p.stdin.flush()
first = expect(p, lambda l: l.startswith("sestat") or l.startswith("readyok"))
p.stdin.write("quit\n"); p.stdin.flush(); finish(p)
if not first.startswith("sestat"):
    print("ERROR: the engine has no \"sestat\" command: not a SESTAT test build (see the docstring)"); sys.exit(2)

# 1. fixed depth
c, nodes = counters(FENS, args.depth)
if c is not None:
    chain = c[0:6]
    if any(chain[i] < chain[i + 1] for i in range(5)):
        fails.append("filter chain not monotone: %s" % chain)
    if c[5] != c[6] + c[7] + c[8]:
        fails.append("searches %d != singular %d + multi-cut %d + neither %d" % (c[5], c[6], c[7], c[8]))
    for k in range(3):
        s = sum(c[12 + 3 * j + k] for j in range(4))
        if s != c[5 + k]:
            fails.append("depth classes, column %d: %d != %d" % (k, s, c[5 + k]))
    if c[5] == 0 or c[9] == 0:
        fails.append("no exclusion search at depth %d (searches %d, nodes %d)" % (args.depth, c[5], c[9]))
    s = c[5] or 1
    print("depth %d, %d positions: %d nodes, %d exclusion searches (%.1f %% of the nodes)" % (args.depth, len(FENS), nodes, c[5], 100.0 * c[9] / max(nodes, 1)))
    print("  filter: TT move first %d, depth %d, bound %d, TT depth %d, mate / ply %d, legal %d" % tuple(chain))
    print("  singular %.1f %%   multi-cut %.1f %%   neither %.1f %%" % (100.0 * c[6] / s, 100.0 * c[7] / s, 100.0 * c[8] / s))
    for j, nm in enumerate(DCL):
        a, b, m = c[12 + 3 * j:15 + 3 * j]
        if a: print("  depth %-5s searches %7d   singular %5.1f %%   multi-cut %5.1f %%" % (nm, a, 100.0 * b / a, 100.0 * m / a))

    # 2. determinism
    c3a, n3a = counters(FENS[:3], args.depth)
    c3b, n3b = counters(FENS[:3], args.depth)
    if c3a is not None and c3b is not None and (c3a != c3b or n3a != n3b):
        fails.append("two runs of the first three positions differ")

# 3. clock play: short games from the golden positions, the engine's own bestmoves are played (random movetimes, so stops land anywhere)
rng = random.Random(3912)
p = start(resolve(args.engine))
def send(s): p.stdin.write(s + "\n"); p.stdin.flush()
send("uci"); expect(p, "uciok")
played = 0
for g in range(args.play):
    f = rng.choice(FENS)
    send("ucinewgame"); send("isready"); expect(p, "readyok")
    moves = []
    ok = True
    for ply in range(6):
        send("position fen %s%s" % (f, (" moves " + " ".join(moves)) if moves else ""))
        send("go movetime %d" % rng.choice((15, 40, 90, 200)))
        try:
            l = expect(p, "bestmove")
        except Exception:
            died(p, "clock play, game %d ply %d" % (g, ply)); ok = False; break
        m = l.split()[1]
        if m in ("0000", "(none)"): break
        moves.append(m); played += 1
    if not ok: break
else:
    send("quit"); finish(p)
print("clock play: %d searches" % played)

for f in fails: print("FAIL", f)
print("t_singular: %s" % ("PASS" if not fails else "%d FAILED" % len(fails)))
sys.exit(1 if fails else 0)
