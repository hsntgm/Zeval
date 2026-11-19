#!/usr/bin/env python3
"""Analysis of run_suite.py results.
usage: analyze.py TAG [--a NAME_A] [--b NAME_B] [--ref NAME_REF | --ref none] [--dir DIR] [--dir-a DIR] [--dir-b DIR] [--dir-ref DIR] [--json out.json]
reads <dir>/<NAME>_<TAG>.jsonl  (default dir = tactics/results;  the recorded baseline lives in tactics/reference).
Only positions finished by BOTH engines under test are compared, suites that were not run are skipped, the reference column is optional."""
import argparse, json, math, os, random, statistics as st, sys
import chess
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ap = argparse.ArgumentParser(); ap.add_argument("tag", default="1000ms"); ap.add_argument("--a", default="C6"); ap.add_argument("--b", default="DEF")
ap.add_argument("--ref", default="SF16"); ap.add_argument("--json"); ap.add_argument("--dir", default=os.path.join(ROOT, "results"))
ap.add_argument("--dir-a"); ap.add_argument("--dir-b"); ap.add_argument("--dir-ref"); a = ap.parse_args()
DIRS = {a.a: a.dir_a or a.dir, a.b: a.dir_b or a.dir}
REF = None if a.ref.lower() == "none" else a.ref
if REF: DIRS[REF] = a.dir_ref or a.dir
ENGS = [e for e in (a.a, a.b, REF) if e]
ORDER = ["arasan2026","iq4","ecmgcp","sbd","classics","mates","wac"]
HARD = ["arasan2026","iq4","ecmgcp","sbd","classics","mates"]       # everything except the saturated WAC
def load(eng):
    d = {}
    try:
        for l in open(os.path.join(DIRS[eng], f"{eng}_{a.tag}.jsonl")):
            r = json.loads(l); d[(r["suite"], r["id"], r["fen"])] = r
    except FileNotFoundError: pass
    return d
R = {e: load(e) for e in ENGS}
for e in (a.a, a.b):
    if not R[e]: sys.exit(f"no results for {e}: expected {os.path.join(DIRS[e], f'{e}_{a.tag}.jsonl')}")
keys = [k for k in R[a.a] if k in R[a.b]]                              # positions finished by both engines under test
def rate(eng, ks): n = len(ks); s = sum(R[eng][k]["solved"] for k in ks if k in R[eng]); return s, n
def binom_two_sided(b, c):                                             # exact sign test on discordant pairs
    n = b + c
    if n == 0: return 1.0
    k = min(b, c); p = sum(math.comb(n, i) for i in range(k+1)) / 2**n
    return min(1.0, 2*p)
def boot(ks, A, B, n=4000, seed=1):
    rnd = random.Random(seed); d = [int(R[A][k]["solved"]) - int(R[B][k]["solved"]) for k in ks]; m = len(d)
    xs = sorted(sum(rnd.choice(d) for _ in range(m)) / m * 100 for _ in range(n)); return xs[int(.025*n)], xs[int(.975*n)]
def classify(r):
    b = chess.Board(r["fen"]); mv = chess.Move.from_uci((r["bm"] or r["am"])[0]); cls = []
    if not r["bm"]: return "avoid(am)"
    if mv.promotion: return "promotion"
    if b.is_capture(mv): c = "capture"
    elif b.gives_check(mv): c = "check"
    elif b.is_castling(mv): c = "castle"
    else: c = "quiet"
    return c
def phase(r):
    n = len(chess.Board(r["fen"]).piece_map()); return "endgame(<=12 men)" if n <= 12 else "middlegame(>12 men)"
out = {}
print(f"=== tag {a.tag}: {a.a} vs {a.b} (reference: {a.ref}) ===")
print(f"positions finished by both: {len(keys)}" + (f"   ({REF}: {len(R[REF])})" if REF else ""))
hdr = f"{'suite':11s} {'n':>4s} | " + " | ".join(f"{e:>13s}" for e in ENGS) + f" | {a.a}-{a.b}  ({a.a}-only/{a.b}-only, p)"
print(hdr); print("-"*len(hdr))
def line(name, ks):
    cells = []
    for e in ENGS:
        s, n = rate(e, [k for k in ks if k in R[e]]); cells.append(f"{s:4d} {100*s/max(n,1):5.1f}%")
    ka = [k for k in ks if k in R[a.a] and k in R[a.b]]
    ba = sum(R[a.a][k]["solved"] and not R[a.b][k]["solved"] for k in ka); bb = sum(R[a.b][k]["solved"] and not R[a.a][k]["solved"] for k in ka)
    p = binom_two_sided(ba, bb)
    print(f"{name:11s} {len(ka):4d} | " + " | ".join(f"{c:>13s}" for c in cells) + f" | {ba-bb:+4d} ({ba}/{bb}, p={p:.2f})")
    out[name] = dict(n=len(ka), a=ba, b=bb, p=p)
for s in ORDER:
    if any(k[0] == s for k in keys): line(s, [k for k in keys if k[0] == s])
print("-"*len(hdr)); line("HARD (no WAC)", [k for k in keys if k[0] in HARD]); line("ALL", keys)
hk = [k for k in keys if k[0] in HARD]
if hk:
    lo, hi = boot(hk, a.a, a.b); d = (sum(R[a.a][k]["solved"] for k in hk) - sum(R[a.b][k]["solved"] for k in hk)) / len(hk) * 100
    print(f"\nHARD solve-rate difference {a.a}-{a.b}: {d:+.1f} pp  (95% bootstrap CI {lo:+.1f} .. {hi:+.1f} pp)")
# subset where the reference engine found the move: removes suspect / too-hard-for-1s positions
rk = [k for k in hk if REF and k in R[REF] and R[REF][k]["solved"]]
if rk:
    print(f"\nPositions {REF} solves in this time (HARD): {len(rk)}/{len([k for k in hk if k in R[REF]])}")
    for e in (a.a, a.b): s, n = rate(e, rk); print(f"   {e:5s} on that subset: {s}/{n} = {100*s/n:.1f}%")
    print(f"   {REF} unsolved (possible suspect/very hard): {len([k for k in hk if k in R[REF] and not R[REF][k]['solved']])}")
print("\nSolution-move type / phase (HARD, both under test)")
for nm, f in (("type", classify), ("phase", phase)):
    groups = {}
    for k in hk: groups.setdefault(f(R[a.a][k]), []).append(k)
    for g, ks in sorted(groups.items()):
        sa, n = rate(a.a, ks); sb, _ = rate(a.b, ks)
        refc = ""
        if REF: sr, nr = rate(REF, [k for k in ks if k in R[REF]]); refc = f"  {REF} {100*sr/max(nr,1):5.1f}%"
        print(f"   {nm}:{g:22s} n={n:4d}  {a.a} {100*sa/n:5.1f}%  {a.b} {100*sb/n:5.1f}%{refc}")
both = [k for k in hk if R[a.a][k]["solved"] and R[a.b][k]["solved"]]
if both:
    ta = [R[a.a][k]["tts"] for k in both]; tb = [R[a.b][k]["tts"] for k in both]
    print(f"\nTime-to-solution on positions both solve (n={len(both)}): median {a.a} {st.median(ta):.0f} ms, {a.b} {st.median(tb):.0f} ms; mean {st.mean(ta):.0f} / {st.mean(tb):.0f} ms")
for e in ENGS:
    ks = [k for k in keys if k in R[e]]
    if ks: print(f"   {e:5s} mean depth {st.mean(R[e][k]['depth'] for k in ks):.1f}  mean nps {st.mean(R[e][k]['nps'] for k in ks)/1e6:.2f} M")
allfail = [k for k in hk if not any(R[e][k]["solved"] for e in ENGS if k in R[e])]
print(f"\nUnsolved by ALL of the engines: {len(allfail)} of {len(hk)} HARD positions")
if a.json: json.dump(dict(summary=out), open(a.json, "w"), indent=1)
