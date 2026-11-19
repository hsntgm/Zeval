#!/usr/bin/env python3
"""Fit the EV_ENDG2 constants (EG2_* of src/eval.asm: KQ-KR, KR-KB, KR-KN without pawns) to Syzygy WDL verdicts.

Random quiet positions of each class (tuner/fit_scale.py tb_positions, the same seed scheme) are labelled by the tablebase (win 1, draw 0.5;
cursed wins / blessed losses count as draws) and the constants are moved by coordinate descent over integers to minimise the tuner's loss
mean (label - sigmoid(K * eval / 400))^2 with eval = model.special_eval (White's view).
usage: fit_special.py --tb DIR [--n 4000] [--k 3.1544] [--seed 1] [--out x.json]      Needs numpy, python-chess."""
import argparse, json, os, sys
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import model  # noqa: E402
from fit_scale import tb_positions  # noqa: E402

LIMITS = {"EG2_QR": (0, 3000), "EG2_QEDGE": (0, 100), "EG2_QCLOSE": (0, 100), "EG2_RB": (0, 400), "EG2_RN": (0, 400), "EG2_REDGE": (0, 60),
          "EG2_RNFAR": (0, 60)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tb", required=True); ap.add_argument("--n", type=int, default=4000)
    ap.add_argument("--k", type=float, default=3.1544); ap.add_argument("--seed", type=int, default=1); ap.add_argument("--out", default="")
    a = ap.parse_args()
    model.load_cfg(); model.CFG["EV_ENDG"] = 1; model.CFG["EV_ENDG2"] = 1
    t = model.load_tables(); tempo = int(t["tempo"])
    rows = []
    for cls in ("KQvKR", "KRvKB", "KRvKN"):
        for fen, y in tb_positions(a.tb, [cls], a.n, a.seed):
            f = fen.split(); bbw = [0] * 7; bbb = [0] * 7; k = 0
            for ch in f[0]:
                if ch == "/": continue
                if "1" <= ch <= "8": k += ord(ch) - 48; continue
                ty, white = model.PIECE_CODE[ch]
                (bbw if white else bbb)[ty] |= 1 << ((7 - (k >> 3)) * 8 + (k & 7)); k += 1
            c, swhite = model.spec2_class(bbw, bbb)
            wk, bk = bbw[6].bit_length() - 1, bbb[6].bit_length() - 1
            o = bbb if swhite else bbw
            rows.append((c, wk if swhite else bk, bk if swhite else wk, o[2].bit_length() - 1, swhite, f[1] == "w", y))
    print(f"{len(rows)} positions: " + ", ".join(f"{c} {sum(1 for r in rows if r[0] == c)} (wins {sum(1 for r in rows if r[0] == c and r[6] != 0.5)})"
                                                  for c in ("qr", "rb", "rn")), flush=True)
    y = np.array([r[6] for r in rows])

    def loss(c, cls=None):
        e = []
        for (k, sk, wk, kn, sw, wtm, _) in rows:
            v = model.spec2_value(k, sk, wk, kn, c)
            e.append((v if sw else -v) + (tempo if wtm else -tempo))
        p = 1 / (1 + np.exp(-a.k * np.array(e, dtype=float) / 400))
        d = (y - p) ** 2
        if cls:
            m = np.array([r[0] == cls for r in rows]); return float(d[m].mean())
        return float(d.mean())
    c0 = model.eg2_consts(); c = dict(c0); best = loss(c)
    print(f"start  {best:.6f}  " + "  ".join(f"{k} {loss(c, k):.6f}" for k in ("qr", "rb", "rn")), flush=True)
    for rnd in range(30):
        changed = False
        for name in model.EG2_NAMES:
            lo, hi = LIMITS[name]
            for step in (64, 16, 4, 1):
                while True:
                    imp = False
                    for d in (step, -step):
                        v = c[name] + d
                        if not lo <= v <= hi: continue
                        tr = dict(c); tr[name] = v; l = loss(tr)
                        if l < best - 1e-10:
                            best, c, imp, changed = l, tr, True, True; break
                    if not imp: break
        print(f"round {rnd + 1}: {best:.6f}  " + " ".join(f"{k[4:]}={v}" for k, v in c.items()), flush=True)
        if not changed: break
    print(f"fitted {best:.6f}  " + "  ".join(f"{k} {loss(c, k):.6f}" for k in ("qr", "rb", "rn")))
    if a.out:
        with open(a.out, "w") as fh: json.dump(c, fh, indent=1)


if __name__ == "__main__":
    main()
