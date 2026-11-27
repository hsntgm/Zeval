#!/usr/bin/env python3
"""Where does the static eval miss the game results in the endgame?  Groups labelled positions by material and reports, per group, how far
the engine's eval (console command "eval") is from the results under the tuner's loss.

For every group (material signature, strong side first; pawns counted 0, 1, 2, 3+):
  n, share    positions of the group and their share of all positions read
  c*          the factor on the eval that fits the results of the group best: < 1 the eval is too confident there (it should be closer to a
              draw), > 1 not confident enough
  loss        mean (result - sigmoid(K * eval / 400))^2 of the group (strong side's view)
  score/pred  mean result and mean prediction of the strong side;  draws = share of drawn games
  excess      n * (loss - loss with c*): what a scale fix of the group alone could gain; the table is sorted by it
With --ab ENGINE2 the second engine's loss is printed beside it and the table is sorted by the change (n * (loss2 - loss)); negative = better.

usage: python3 tuner/eg_report.py --engine ./zeval FILE.epd ... [--ab ./zeval2] [--max-pieces 4] [--min-n 300] [--top 40] [--k 3.1544]
  FILE.epd  labelled positions as the tuner reads them (tuner/extract.py: "1-0" / "0-1" / "1/2-1/2", [1.0], a bare 0.5, ...)
  --max-pieces  only positions with at most this many pieces besides kings and pawns
Needs numpy and scipy.  The eval is the static eval of the position (no search), so use quiet positions (the tuner's data sets are)."""
import argparse, collections, os, subprocess, sys
import numpy as np
from scipy.special import expit
from scipy.optimize import minimize_scalar
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, "..", "tests"))
from _eng import resolve
from extract import parse_label

VAL = {"Q": 9, "R": 5, "B": 3, "N": 3}


def read(files, max_pieces):
    fens, y = [], []
    for path in files:
        with open(path, errors="replace") as fh:
            for line in fh:
                t = line.split()
                if len(t) < 4 or sum(ch in "NBRQnbrq" for ch in t[0]) > max_pieces:
                    continue
                lab = parse_label(" ".join(t[4:]))
                if lab is None:
                    continue
                fens.append(" ".join(t[:4]) + " 0 1"); y.append(lab)
    return fens, np.array(y)


def evals(engine, fens, chunk=200000):
    """White's view of the engine's eval of every position"""
    out = []
    for i in range(0, len(fens), chunk):
        part = fens[i:i + chunk]
        inp = "".join(f"position fen {f}\neval\n" for f in part) + "quit\n"
        o = subprocess.run(engine, input=inp, capture_output=True, text=True).stdout
        v = [int(l.split()[1]) for l in o.splitlines() if l.startswith("eval ")]
        if len(v) != len(part):
            raise SystemExit(f"{engine}: {len(v)} eval lines for {len(part)} positions")
        out += v
    e = np.array(out, np.float64)
    return np.where(np.array([f.split()[1] == "w" for f in fens]), e, -e)


def group(board):
    """material signature with the stronger side (pieces, then pawns) first; True when White is that side"""
    side = []
    for up in (True, False):
        pcs = "".join(sorted((c.upper() for c in board if c.upper() in VAL and c.isupper() == up), key="QRBN".index))
        side.append((sum(VAL[c] for c in pcs), sum(1 for c in board if c == ("P" if up else "p")), pcs))
    w, b = side
    a, o, white = (w, b, True) if (w[0], w[1]) >= (b[0], b[1]) else (b, w, False)
    pc = lambda n: str(n) if n < 3 else "3+"
    return f"K{a[2]}+{pc(a[1])}p v K{o[2]}+{pc(o[1])}p", white


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+"); ap.add_argument("--engine", required=True); ap.add_argument("--ab", default="")
    ap.add_argument("--max-pieces", type=int, default=4); ap.add_argument("--min-n", type=int, default=300)
    ap.add_argument("--top", type=int, default=40); ap.add_argument("--k", type=float, default=3.1544)
    a = ap.parse_args()
    fens, y = read(a.files, a.max_pieces)
    if not fens:
        raise SystemExit("no labelled positions")
    K = a.k
    ea = evals(resolve(a.engine), fens)
    eb = evals(resolve(a.ab), fens) if a.ab else None
    G = collections.defaultdict(list)
    sign = np.empty(len(fens))
    for i, f in enumerate(fens):
        g, white = group(f.split()[0])
        G[g].append(i); sign[i] = 1.0 if white else -1.0
    ys = np.where(sign > 0, y, 1 - y)                      # strong side's view
    la = (ys - expit(K * sign * ea / 400)) ** 2
    lb = (ys - expit(K * sign * eb / 400)) ** 2 if eb is not None else None
    rows = []
    for g, idx in G.items():
        if len(idx) < a.min_n:
            continue
        idx = np.array(idx); e = sign[idx] * ea[idx]; r = ys[idx]
        f = lambda c: float(np.mean((r - expit(K * c * e / 400)) ** 2))
        c = minimize_scalar(f, bounds=(0.0, 3.0), method="bounded")
        row = dict(g=g, n=len(idx), c=c.x, loss=float(la[idx].mean()), score=float(r.mean()), pred=float(expit(K * e / 400).mean()),
                   draws=float(np.mean(r == 0.5)), excess=len(idx) * (float(la[idx].mean()) - c.fun))
        if lb is not None:
            row["loss2"] = float(lb[idx].mean()); row["delta"] = len(idx) * (row["loss2"] - row["loss"])
        rows.append(row)
    rows.sort(key=(lambda r: r["delta"]) if lb is not None else (lambda r: -r["excess"]))
    tot = len(fens)
    print(f"{tot} positions with at most {a.max_pieces} pieces besides kings and pawns; groups with at least {a.min_n} positions; K {K}")
    print(f"loss: {a.engine} {la.mean():.6f}" + (f",  {a.ab} {lb.mean():.6f}  (change {lb.mean() - la.mean():+.6f})" if lb is not None else ""))
    head = f"{'group (strong side first)':26s} {'n':>7s} {'share':>6s} {'c*':>5s} {'loss':>7s} {'score':>6s} {'pred':>6s} {'draws':>6s} {'excess':>7s}"
    print(head + (f" {'loss2':>7s} {'change':>7s}" if lb is not None else ""))
    for r in rows[:a.top]:
        line = (f"{r['g']:26s} {r['n']:7d} {100 * r['n'] / tot:5.2f}% {r['c']:5.2f} {r['loss']:7.4f} {r['score']:6.3f} {r['pred']:6.3f}"
                f" {r['draws']:6.2f} {r['excess']:7.1f}")
        print(line + (f" {r['loss2']:7.4f} {r['delta']:+7.1f}" if lb is not None else ""))
    print(f"sum of the excess of the groups shown: {sum(r['excess'] for r in rows[:a.top]):.1f}, of all groups: {sum(r['excess'] for r in rows):.1f}")


if __name__ == "__main__":
    main()
