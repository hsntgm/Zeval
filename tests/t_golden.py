#!/usr/bin/env python3
"""Golden-file regression check: does THIS engine binary search exactly like the verified Linux build?

A fixed-depth, single-threaded search is deterministic, so every correct build prints the same
score / node count / best move per iteration ("pv" is the full line and bestmove may carry "ponder":
only the first pv move and the bestmove move are compared; tests/t_pv.py checks the rest of the line).  Reference files (60 positions, depth 8):
  golden_depth8.json                    current default build, recorded from the Linux ELF (2,401,114 nodes)
  golden_depth8_noscmg.json             build with -DEV_SCMG=0 (2,397,486 nodes)
  golden_depth8_nostaged.json           build with -DEV_SCMG=0 -DSTAGED=0 (2,382,187 nodes; also with -DSTAGED=0 -DTTFIRST=1 -DTTFEAGER)
  golden_depth8_ttfirst.json            build with -DEV_SCMG=0 -DSTAGED=0 -DTTFIRST=1 (2,400,423 nodes; also with CAPIDX / QSSEE on or off)
  golden_depth8_noklines.json           build with -DEV_SCMG=0 -DSTAGED=0 -DEV_KLINES=0 (2,445,720 nodes)
  golden_depth8_zeval10.json            build with -DEV_SCMG=0 -DSTAGED=0 -DCAPHIST=0 -DEV_KLINES=0: the search of Zeval 1.0 (2,484,151 nodes)
  golden_depth8_sem2.json               build with -DEV_SCMG=0 -DSTAGED=0 -DCAPHIST=0 -DSE_M=2 -DSE_TTD=3 -DEV_KLINES=0 (2,713,101 nodes)
  golden_depth8_nosing.json             build with -DEV_SCMG=0 -DSTAGED=0 -DCAPHIST=0 -DSING=0 -DEV_KLINES=0 (1,945,787 nodes)
  golden_depth8_nosing_nocont.json      build with -DEV_SCMG=0 -DSTAGED=0 -DCAPHIST=0 -DCONTHIST=0 -DSING=0 -DEV_KLINES=0 (1,931,516 nodes)
  golden_depth8_evalfull0.json          build with -DSTAGED=0 -DCAPHIST=0 -DEVAL_FULL=0 -DSING=0 (1,806,878 nodes)
  golden_depth8_dev8search.json         build with -DSTAGED=0 -DDEV8SEARCH -DEVAL_FULL=0 (2,512,457 nodes)
  golden_depth8_0x88.json               0x88 move generator: build with -DBB=0 -DDEV8SEARCH (2,575,085 nodes)
  golden_depth8_0x88_nosee.json         0x88 move generator without SEE: build with -DBB=0 -DDEV8SEARCH and every SEE_* switch 0
                                        (-DSEE_QS=0 -DSEE_ORD=0 -DSEE_BADLAST=0 -DSEE_PC=0 -DSEE_CM=0 -DSEE_PQ=0 -DSEE_QM=0; 3,520,068 nodes)
The Windows exe must reproduce the reference bit for bit (time / nps fields are ignored).
Needs no python-chess.

  Windows :  set ENGINE=zeval.exe          (or pass the engine as 1st argument)
             python tests\\t_golden.py [engine] [golden.json]
  Linux   :  python3 tests/t_golden.py ./zeval
  re-record (needs python-chess):  python3 tests/t_golden.py --make ./zeval tests/golden_depth8.json [positions] [depth]
Exit code 0 = identical, 1 = different (or a mismatch), 2 = the engine crashed / hung / was not found (one ERROR line).
GOLDEN_TIMEOUT = seconds one search may take before the engine counts as hung (default 600).
"""
import subprocess, sys, re, os, json, random
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _eng import resolve as engine_cmd, expect, finish, start, default_engine

TIMEOUT = float(os.environ.get("GOLDEN_TIMEOUT", "600"))

def run(cmd, fens, depth):
    p = start(cmd)
    def send(s): p.stdin.write(s + "\n"); p.stdin.flush()
    res = []
    try:
        send("uci"); expect(p, "uciok", 30)
        for o in filter(None, os.environ.get("GOLDEN_OPTS", "").split(";")):     # e.g. GOLDEN_OPTS="Unit=1" (UNITTEST build, tests/t_units.py)
            k, _, v = o.partition("="); send(f"setoption name {k.strip()} value {v.strip()}")
        for fen in fens:
            send("ucinewgame"); send("isready"); expect(p, "readyok", 60)
            send(f"position fen {fen}"); send(f"go depth {depth}")
            lines = []; expect(p, "bestmove", TIMEOUT, collect=lines)
            rec = []
            for l in lines:
                if l.startswith("info"):
                    m = re.search(r"depth (\d+).*?score (\w+) (-?\d+).*?nodes (\d+).*? pv (.*)", l)
                    if m: rec.append(f"d{m[1]} {m[2]} {m[3]} n{m[4]} {m[5]}")
                elif l.startswith("bestmove"):
                    rec.append(l)
            res.append(rec)
        send("quit"); finish(p, 30)
    finally:
        if p.poll() is None: p.kill()
    return res

def make(cmd, path, npos, depth):
    import chess
    random.seed(7)
    fens = ["rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
            "r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1",
            "8/2p5/3p4/KP5r/1R3p1k/8/4P1P1/8 w - - 0 1",
            "r3k2r/Pppp1ppp/1b3nbN/nP6/BBP1P3/q4N2/Pp1P2PP/R2Q1RK1 w kq - 0 1",
            "rnbq1k1r/pp1Pbppp/2p5/8/2B5/8/PPP1NnPP/RNBQK2R w KQ - 1 8",
            "r4rk1/1pp1qppp/p1np1n2/2b1p1B1/2B1P1b1/P1NP1N2/1PP1QPPP/R4RK1 w - - 0 10",
            "6k1/5ppp/8/8/8/8/5PPP/3R2K1 w - - 0 1", "8/8/8/4k3/8/8/4P3/4K3 w - - 0 1",
            "4k3/8/8/8/8/8/4P3/4K3 b - - 0 1",
            "r1bqkb1r/pppp1ppp/2n2n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR w KQkq - 4 4"]
    while len(fens) < npos:
        b = chess.Board()
        for _ in range(random.randint(6, 70)):
            mv = list(b.legal_moves)
            if not mv: break
            b.push(random.choice(mv))
        if not b.is_game_over(): fens.append(b.fen())
    json.dump({"depth": depth, "fens": fens, "records": run(cmd, fens, depth)}, open(path, "w"), indent=0)
    print("recorded", len(fens), "positions at depth", depth, "->", path)

if len(sys.argv) > 1 and sys.argv[1] == "--make":
    make(engine_cmd(sys.argv[2]), sys.argv[3], int(sys.argv[4]) if len(sys.argv) > 4 else 60,
         int(sys.argv[5]) if len(sys.argv) > 5 else 8)
    sys.exit(0)

eng = sys.argv[1] if len(sys.argv) > 1 else (os.environ.get("ENGINE") or default_engine())
if eng.startswith("--"): sys.exit("usage: t_golden.py [engine] [golden.json]   |   t_golden.py --make engine out.json [positions] [depth]")
gold = sys.argv[2] if len(sys.argv) > 2 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden_depth8.json")
g = json.load(open(gold))
got = run(engine_cmd(eng), g["fens"], g["depth"])
def norm(rec):
    out = []
    for x in rec:
        if x.startswith("bestmove"): out.append(" ".join(x.split()[:2]))      # drop "ponder <move>"
        elif x.startswith("d"):
            f = x.split(); out.append(" ".join(f[:5]))                       # keep only the first pv move
        else: out.append(x)
    return out
bad = 0; nodes = 0
for fen, exp, rec in zip(g["fens"], g["records"], got):
    exp, rec = norm(exp), norm(rec)
    nodes += sum(int(re.search(r" n(\d+)", s)[1]) for s in exp if s.startswith("d"))
    if exp != rec:
        bad += 1
        if bad <= 5: print("MISMATCH", fen); print("  expected:", exp[-2:]); print("  got     :", rec[-2:])
print(f"{len(g['fens'])} positions, depth {g['depth']}, {nodes} nodes, mismatches: {bad}  ->", "IDENTICAL to Linux reference" if not bad else "DIFFERENT")
sys.exit(1 if bad else 0)
