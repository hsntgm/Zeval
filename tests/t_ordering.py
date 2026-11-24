#!/usr/bin/env python3
"""Move-ordering diagnostics: where does the move that causes the beta cutoff stand in the move order?   Needs an ORDSTAT test build (nasm -DORDSTAT).

A DIAGNOSTIC TOOL, NOT A ROUTINE GATE.  It tells WHERE the ordering loses (depth, node type, TT move or not, kind of the cutoff move) and whether a
change of the ordering made it better or worse.  It does not measure Elo: a better order can cost more time than it saves.

"Perfect ordering" means: at every node that fails high, the FIRST move tried already causes the cutoff (100 % first-move cutoffs).  That needs the
answer before the search and is out of reach; the numbers say how close the ordering gets.  The engine counts (main thread, unit 0; counters only,
the plain build has none of this code):
  search():   fail-high nodes, the position of the cutoff move (1 2 3 4 5 6-8 9-16 17+), its kind (TT move / capture / killer / other quiet), the rank
              of a quiet cutoff move among the quiet moves picked; per depth class (1 2 3 4 5-7 8+): fail-highs, first-move cutoffs, with / without a
              TT move at the node; per node type (PV / cut / all): fail-highs and first-move cutoffs; nodes with a TT move: is the TT move picked first?
  qsearch():  fail-high nodes, the position of the cutoff move (1 2 3 4+), in check (evasions) or not
Threads=1 and a fixed depth make the search deterministic: the counters are EXACTLY reproducible, so a difference between two builds is a real one
(no games, no error bars).

Reading an A/B comparison.  A change of pruning, reductions, extensions or of the ordering itself changes the tree, and with it the set of nodes
that fail high: the shares then move although no node is ordered differently.  Compare the node counts first, and prefer the numbers inside one
class (a depth class, a node type, quiet ranks) to the overall first-move share.  The qsearch() share without check is not touched by the quiet
ordering at all (captures only): a small move there means the tree moved,
a large one a changed capture ordering.

usage:  t_ordering.py ENGINE [--depth 10] [--n 60] [--save ref.json] [--ref ref.json | --ab ENGINE_B] [--gate [--tol 0.005]]
        ENGINE may also come from the ENVIRONMENT variable ENGINE.  With --ref / --ab the report shows both builds and the differences
        (A = ENGINE or the reference, B = the candidate).  --gate (opt-in): exit 1 when one of the gated shares of the candidate is lower than the
        reference by more than --tol (absolute, default 0.5 pp): first-move cutoffs at depth >= 5, quiet cutoffs on quiet ranks 1-3, qsearch() first
        move without check.  Measured at depth 10: a changed tree alone (another search, same ordering) moves these shares by up to ~0.4 pp
        (+0.19 / +1.95 / -0.23 pp, the gate passes); broken ordering is far bigger (SEE_ORD=0: qsearch -12.6 pp, nodes +29 %;
        CH_BL=0: quiet ranks 1-3 -1.1 pp) and fails.
        References (depth 10, 60 positions): tests/ordering_ref_nosing_nocont.json (-DCONTHIST=0 -DSING=0), tests/ordering_ref_nosing.json
        (-DSING=0), tests/ordering_ref_sem2.json (-DSE_M=2 -DSE_TTD=3), tests/ordering_ref_zeval10.json (the search of Zeval 1.0).
exit codes: 0 report (and the gate, when asked for, passed), 1 instrument check failed / gate failed, 2 engine / harness could not be run
            (also: the engine has no "ordstat" command, i.e. it is not an ORDSTAT build).
Instrument checks: the counter sums agree, the TT move is the first move picked at (nearly) every node that has one, and a second run of the first
three positions gives identical counters."""
import argparse, json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _eng import resolve, expect, finish, start, default_engine

NC = 80                 # counters printed by 'ordstat' (64 .. 72: captures, see ORDCAP in src/p4_search.asm; older references hold 64)
DCL = ["1", "2", "3", "4", "5-7", "8+"]
NTY = ["PV", "cut", "all"]
ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
ap.add_argument("engine", nargs="?", default=os.environ.get("ENGINE") or default_engine())
ap.add_argument("--depth", type=int, default=10)
ap.add_argument("--n", type=int, default=60)
ap.add_argument("--save"); ap.add_argument("--ref"); ap.add_argument("--ab")
ap.add_argument("--gate", action="store_true")
ap.add_argument("--tol", type=float, default=0.005)
args = ap.parse_args()
if args.ref and args.ab: ap.error("--ref and --ab exclude each other")
if args.gate and not (args.ref or args.ab): ap.error("--gate needs --ref or --ab")
HERE = os.path.dirname(os.path.abspath(__file__))
FENS = json.load(open(os.path.join(HERE, "golden_depth8.json")))["fens"][:args.n]


def run(path, fens, depth):
    """one engine process, one position after the other (ucinewgame in between): summed counters and nodes"""
    p = start(resolve(path))
    def send(s): p.stdin.write(s + "\n"); p.stdin.flush()
    tot = [0] * NC; nodes = 0
    try:
        send("uci"); expect(p, "uciok", 30)
        prev = [0] * NC
        for f in fens:
            send("ucinewgame"); send("isready"); expect(p, "readyok", 30)
            send("position fen " + f); send("go depth %d" % depth)
            lines = []; expect(p, "bestmove", 600, collect=lines)
            n = 0
            for l in lines:
                m = re.search(r" nodes (\d+)", l)
                if l.startswith("info") and m: n = int(m[1])
            nodes += n
            send("ordstat"); send("isready")
            lines = []; expect(p, "readyok", 30, collect=lines)
            l = next((s for s in lines if s.startswith("ordstat ")), "")
            if not l:
                print("ERROR: %s has no 'ordstat' command: build it with  sh tests/build1.sh OUT -DORDSTAT" % path, file=sys.stderr); sys.exit(2)
            cur = [int(x) for x in l.split()[1:]]
            if len(cur) != NC:
                print("ERROR: %s prints %d ordstat counters, %d expected (an older ORDSTAT build?)" % (path, len(cur), NC), file=sys.stderr); sys.exit(2)
            for i in range(NC): tot[i] += cur[i] - prev[i]
            prev = cur
        send("quit"); finish(p, 10)
        return tot, nodes
    finally:
        if p.poll() is None: p.kill()


def pct(a, b): return a / b if b else 0.0

def metrics(c):
    """named numbers derived from the counters (shares are 0..1)"""
    c = list(c) + [0] * (NC - len(c))       # a reference saved before the capture counters existed: they read as 0
    cut = c[0]; qn = sum(c[13:20]); rep = [1, 2, 3, 4, 5, 7, 12, 20]
    other = cut - c[9] - c[64] - c[67]      # cutoffs by a killer, another quiet move or an under-promotion
    m = {
        "search fail-high nodes": cut,
        "first move cuts": pct(c[1], cut),
        "first two moves": pct(c[1] + c[2], cut),
        "first three moves": pct(c[1] + c[2] + c[3], cut),
        "mean position (~)": sum(c[1 + i] * rep[i] for i in range(8)) / cut if cut else 0.0,
        "depth>=5 first move": pct(c[31], c[30]),
        "cut by TT move": pct(c[9], cut),
        "cut by capture/promo": pct(c[10], cut),
        "cut by killer": pct(c[11], cut),
        "cut by other quiet": pct(c[12], cut),
        "TT move picked first": pct(c[21], c[20]),
        "quiet cutoffs (killer + other)": qn,
        "quiet rank 1": pct(c[13], qn),
        "quiet rank 1-3": pct(c[13] + c[14] + c[15], qn),
        "quiet rank 9+": pct(c[18] + c[19], qn),
        "qsearch fail-high nodes": c[24],
        "qsearch first move": pct(c[25], c[24]),
        "qsearch in check: share": pct(c[29], c[24]),
        "qsearch in check: first move": pct(c[62], c[29]),
        "qsearch no check: first move": pct(c[25] - c[62], c[24] - c[29]),
        "cut by a good capture": pct(c[64], cut),
        "... by the first good capture": pct(c[65], c[64]),
        "... good captures before it (mean)": pct(c[66], c[64]),
        "depth>=5: by the first good capture": pct(c[72], c[71]),
        "cut by a bad capture": pct(c[67], cut),
        "... its position (mean)": pct(c[68], c[67]),
        "quiet cut after failed good captures": pct(c[69], other),
        "... failed good captures (mean)": pct(c[70], other),
    }
    for t, ty in enumerate(NTY):
        m["%s share of fail-highs" % ty] = pct(c[56 + t], cut)
        m["%s first move" % ty] = pct(c[59 + t], c[56 + t])
    for b, d in enumerate(DCL):
        fh, f1, tfh, tf1 = c[32 + b], c[38 + b], c[44 + b], c[50 + b]
        m["d%s share of fail-highs" % d] = pct(fh, cut)
        m["d%s first move" % d] = pct(f1, fh)
        m["d%s with TT move" % d] = pct(tfh, fh)
        m["d%s first, TT move" % d] = pct(tf1, tfh)
        m["d%s first, no TT move" % d] = pct(f1 - tf1, fh - tfh)
    return m

GATED = ["depth>=5 first move", "quiet rank 1-3", "qsearch no check: first move"]

def check(c, tag):
    bad = []
    if sum(c[1:9]) != c[0]: bad.append("position histogram does not add up to the fail-high nodes")
    if sum(c[9:13]) != c[0]: bad.append("kinds do not add up to the fail-high nodes")
    if sum(c[13:20]) != c[11] + c[12]: bad.append("quiet ranks do not add up to the quiet cutoffs")
    if sum(c[32:38]) != c[0] or sum(c[56:59]) != c[0]: bad.append("depth classes / node types do not add up to the fail-high nodes")
    if sum(c[38:44]) != c[1] or sum(c[59:62]) != c[1]: bad.append("first-move cutoffs per depth class / node type do not add up")
    if any(c[38 + b] > c[32 + b] or c[44 + b] > c[32 + b] or c[50 + b] > c[44 + b] or c[50 + b] > c[38 + b] for b in range(6)):
        bad.append("depth class counters inconsistent")
    if any(c[59 + t] > c[56 + t] for t in range(3)): bad.append("node type counters inconsistent")
    if sum(c[25:29]) != c[24] or c[29] > c[24] or c[62] > c[29] or c[62] > c[25]: bad.append("qsearch counters inconsistent")
    if c[31] > c[30] or c[30] > c[0] or c[21] > c[20]: bad.append("depth >= 5 / TT move counters inconsistent")
    if len(c) >= 73 and (c[64] + c[67] + c[9] > c[0] or c[65] > c[64] or c[72] > c[71] or c[71] > c[64] or c[64] + c[67] > c[10] + c[9]):
        bad.append("capture counters inconsistent")
    if c[20] - c[21] > max(1, c[20] // 1000):
        bad.append("TT move not picked first at %d of %d nodes with a TT move (more than 0.1 %%: an ordering bug)" % (c[20] - c[21], c[20]))
    return ["FAIL (instrument%s): %s" % (tag, b) for b in bad]

def fmt(k, v):
    if isinstance(v, int): return "%d" % v
    return ("%.3f" % v) if "mean" in k else ("%.2f%%" % (100 * v))

def dfmt(k, d):
    if isinstance(d, int): return "%+d" % d
    return ("%+.3f" % d) if "mean" in k else ("%+.2f pp" % (100 * d))

GROUPS = [
    ("search()", ["search fail-high nodes", "first move cuts", "first two moves", "first three moves", "mean position (~)", "depth>=5 first move",
                  "cut by TT move", "cut by capture/promo", "cut by killer", "cut by other quiet", "TT move picked first"]),
    ("quiet cutoffs (rank among the quiet moves picked)", ["quiet cutoffs (killer + other)", "quiet rank 1", "quiet rank 1-3", "quiet rank 9+"]),
    ("captures (good >= SEE 0 by score_moves, bad = sorted behind the quiet moves)", ["cut by a good capture", "... by the first good capture",
                  "... good captures before it (mean)", "depth>=5: by the first good capture", "cut by a bad capture", "... its position (mean)",
                  "quiet cut after failed good captures", "... failed good captures (mean)"]),
    ("qsearch()", ["qsearch fail-high nodes", "qsearch first move", "qsearch in check: share", "qsearch in check: first move", "qsearch no check: first move"]),
    ("node type (all = neither PV nor cut)", [x for ty in NTY for x in ("%s share of fail-highs" % ty, "%s first move" % ty)]),
] + [("depth %s" % d, ["d%s share of fail-highs" % d, "d%s first move" % d, "d%s with TT move" % d, "d%s first, TT move" % d, "d%s first, no TT move" % d])
     for d in DCL]

fails = 0
cA, nA = run(args.engine, FENS, args.depth)
print("%s: %d positions, depth %d, Threads=1, %d nodes" % (args.engine, len(FENS), args.depth, nA))
for b in check(cA, ""): print(b); fails += 1
c1, n1 = run(args.engine, FENS[:3], args.depth)
c2, n2 = run(args.engine, FENS[:3], args.depth)
if c1 != c2 or n1 != n2: print("FAIL (instrument): two runs of the same positions give different counters"); fails += 1

base = None
if args.ab:
    cB, nB = run(args.ab, FENS, args.depth)
    for b in check(cB, ", B"): print(b); fails += 1
    print("%s: %d nodes" % (args.ab, nB))
    base, cand, nodes_b, nodes_c, lab = metrics(cA), metrics(cB), nA, nB, ("A", "B")
elif args.ref:
    r = json.load(open(args.ref))
    if r.get("depth") != args.depth or r.get("positions") != len(FENS):
        print("NOTE: the reference is depth %s / %s positions, this run depth %d / %d positions: the numbers are not comparable"
              % (r.get("depth"), r.get("positions"), args.depth, len(FENS)))
    base, cand, nodes_b, nodes_c, lab = metrics(r["counters"]), metrics(cA), r["nodes"], nA, ("reference", "this build")
    print("reference: %s (%s)" % (args.ref, r.get("label", "")))
else:
    cand = metrics(cA)

if base is not None: print("\n  %-34s %11s %11s %11s" % ("", lab[0], lab[1], "delta"))
for title, keys in GROUPS:
    print("\n" + title)
    for k in keys:
        v = cand[k]
        if base is None:
            print("  %-34s %11s" % (k, fmt(k, v))); continue
        d = v - base[k]
        line = "  %-34s %11s %11s %11s" % (k, fmt(k, base[k]), fmt(k, v), dfmt(k, d))
        if args.gate and k in GATED:
            worse = d < -args.tol
            line += "   <-- gate: WORSE" if worse else "   (gate ok)"
            fails += worse
        print(line)
if base is not None:
    print("\nnodes (same depth): %s %d, %s %d  (%+.2f %%)" % (lab[0], nodes_b, lab[1], nodes_c, 100.0 * (nodes_c - nodes_b) / nodes_b))
    if not args.gate: print("report only (--gate compares: %s)" % ", ".join(GATED))
if args.save:
    json.dump({"label": os.path.basename(args.engine), "positions": len(FENS), "depth": args.depth, "nodes": nA, "counters": cA}, open(args.save, "w"), indent=1)
    print("saved " + args.save)
print("PASS" if not fails else "FAILED (%d)" % fails)
sys.exit(1 if fails else 0)
