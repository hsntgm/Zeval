#!/usr/bin/env python3
"""Fit the EV_SCALE2 constants (SC_* of src/eval.asm) to game results and tablebase verdicts.

The scale factor multiplies only the eg part of the evaluation, so the tables stay fixed here (those of src/eval.asm) and only the
constants of the rules move.  Which rule applies, and its inputs, do not depend on the constants (model.scale_kind2), so every position is
reduced once to (mg, eg, phase, rule, inputs, label) and the fit itself is a fast numpy loop: coordinate descent over integer constants,
minimising the tuner's loss  mean (label - sigmoid(K * eval / 400))^2  (tuner/tune.py, the same K).

Two kinds of positions, both White's view:
  --data FILE ...   labelled positions of the tuner's data sets (game results); a seeded sample of --n quiet positions with a scale
                    factor below 64 under the starting constants or with few pieces (where the rules act)
  --tb DIR          Syzygy WDL tables: --tbn random quiet positions per class of --classes, labelled by the tablebase (win 1, draw 0.5;
                    cursed wins / blessed losses count as draws: the engine plays under the fifty-move rule).  Weighted by --tbw.
Positions with a special evaluation (EV_ENDG) are skipped: the scale factor does not act there.
usage: fit_scale.py --engine ./zeval_scale2 --data a.epd b.epd --n 400000 --tb DIR [--tbn 2000] [--tbw 0.3] [--k 3.1544] [--seed 1]
       [--cache fit.npz] [--out scale.json]
--engine must be a build with EV_SCALE=1 EV_SCALE2=1 (its evalcfg sets the model).  Prints the loss of the starting constants, of the v1 rules
(EV_SCALE2=0) and of the fitted constants, per source, and writes the constants (JSON) for src/eval.asm.  Needs numpy, python-chess (for --tb)."""
import argparse, json, os, random, re, subprocess, sys
import numpy as np
from multiprocessing import Pool

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import model  # noqa: E402

KINDS = ("z", "m1", "m2", "ob", "oc", "rk", "q", "g", "rp", "qp", "rpr", "mpm")
LIMITS = {"SC_M1": (0, 40), "SC_M2": (0, 48), "SC_OB0": (0, 64), "SC_OB1": (0, 16), "SC_OC0": (0, 64), "SC_OC1": (0, 12), "SC_OC2": (0, 12),
          "SC_RK": (0, 64), "SC_Q0": (0, 64), "SC_Q1": (0, 12), "SC_G0": (0, 64), "SC_G1": (0, 16), "SC_FL": (0, 16), "SC_G6FL": (0, 16),
          "SC_RKP": (0, 64), "SC_QKP": (0, 64), "SC_RPR": (0, 64), "SC_MPM": (0, 64)}
CFG_MASK = None


def parse_label(line):
    m = re.search(r'"(1-0|0-1|1/2-1/2)"', line)
    if m:
        return {"1-0": 1.0, "0-1": 0.0, "1/2-1/2": 0.5}[m.group(1)]
    m = re.search(r"\[(0(?:\.0)?|0\.5|1(?:\.0)?)\]", line)
    return float(m.group(1)) if m else None


def reduce_position(args):
    """fen, label -> one row (or None): mg, eg, phase, white_to_move, tempo sign handled by the caller, kind index, x1, x2, one_flank, v1 sf"""
    fen, label = args
    t = TABLES
    try:
        F = model.fen_features(fen)
    except ValueError:
        return None
    if F.special or "sinfo" not in F.aux:
        return None
    mg, eg = model.eval_sums(F, t)
    if model.CFG.get("EV_INIT"):
        mg, eg = model.initiative(mg, eg, F.aux)
    w, b = F.aux["sinfo"]
    s, o = (w, b) if eg > 0 else (b, w)
    kind, x1, x2, one = model.scale_kind2(s, o)
    model.CFG["EV_SCALE2"] = 0
    sf1 = model.scale_rule(s, o)                                  # the v1 rules (EV_SCALE2 = 0), for comparison
    model.CFG["EV_SCALE2"] = 1
    return (mg, eg, min(F.phase, 24), int(F.wtm), KINDS.index(kind), x1, x2, one, sf1, label)


TABLES = None


def init_worker(mask):
    global CFG_MASK, TABLES
    CFG_MASK = mask
    model.set_cfg(mask)
    TABLES = model.load_tables()


def tb_positions(tbdir, classes, n, seed):
    import chess, chess.syzygy
    PT = {"K": chess.KING, "Q": chess.QUEEN, "R": chess.ROOK, "B": chess.BISHOP, "N": chess.KNIGHT, "P": chess.PAWN}
    tb = chess.syzygy.open_tablebase(tbdir)
    out = []
    for cls in classes:
        rnd = random.Random(f"{seed}-{cls}"); got = 0; tries = 0
        while got < n and tries < 60 * n:
            tries += 1
            w, b = cls.split("v")
            if rnd.random() < 0.5: w, b = b, w
            board = chess.Board(None); used = set(); ok = True
            for col, spec in ((chess.WHITE, w), (chess.BLACK, b)):
                for ch in spec:
                    for _ in range(100):
                        sq = rnd.randrange(64)
                        if sq in used or (ch == "P" and not 8 <= sq <= 55): continue
                        used.add(sq); board.set_piece_at(sq, chess.Piece(PT[ch], col)); break
                    else: ok = False
            board.turn = rnd.random() < 0.5
            if not ok or not board.is_valid() or board.is_check(): continue
            if any(board.is_capture(m) for m in board.legal_moves): continue
            board.push(chess.Move.null()); cap = any(board.is_capture(m) for m in board.legal_moves); board.pop()
            if cap: continue
            wdl = tb.probe_wdl(board)
            res = 0.5 if abs(wdl) < 2 else (1.0 if wdl > 0 else 0.0)        # side to move -> White's view
            if board.turn == chess.BLACK: res = 1.0 - res
            out.append((board.fen(), res)); got += 1
    return out


def data_positions(files, n, seed):
    """reservoir sample of n / len(files) labelled positions with at most 6 pieces besides kings and pawns from every file"""
    rnd = random.Random(seed); keep = []; per = n // max(1, len(files))
    for path in files:
        res = []; seen = 0
        with open(path, errors="replace") as fh:
            for line in fh:
                t = line.split()
                if len(t) < 4 or sum(ch in "nbrqNBRQ" for ch in t[0]) > 6: continue
                lab = parse_label(line)
                if lab is None: continue
                seen += 1
                item = (" ".join(t[:4]) + " 0 1", lab)
                if len(res) < per: res.append(item)
                else:
                    j = rnd.randrange(seen)
                    if j < per: res[j] = item
        keep += res
    return keep


def sf_of(rows, c):
    k, x1, x2, one = rows["kind"], rows["x1"], rows["x2"], rows["one"]
    v = np.zeros(len(k), dtype=np.int64)
    v = np.where(k == KINDS.index("m1"), c["SC_M1"], v)
    v = np.where(k == KINDS.index("m2"), c["SC_M2"], v)
    v = np.where(k == KINDS.index("ob"), c["SC_OB0"] + c["SC_OB1"] * x1 - one * c["SC_FL"], v)
    v = np.where(k == KINDS.index("oc"), c["SC_OC0"] + c["SC_OC1"] * x1 + c["SC_OC2"] * x2 - one * c["SC_FL"], v)
    v = np.where(k == KINDS.index("rk"), c["SC_RK"] - one * c["SC_FL"], v)
    v = np.where(k == KINDS.index("q"), c["SC_Q0"] + c["SC_Q1"] * x1 - one * c["SC_FL"], v)
    g = np.minimum(64, c["SC_G0"] + c["SC_G1"] * x1) - one * (c["SC_G6FL"] + c["SC_FL"])
    v = np.where(k == KINDS.index("g"), g, v)
    for kind, name in (("rp", "SC_RKP"), ("qp", "SC_QKP"), ("rpr", "SC_RPR"), ("mpm", "SC_MPM")):
        v = np.where(k == KINDS.index(kind), c[name], v)
    return np.clip(v, 0, 64)


def loss(rows, sf, K, tempo, mask=None):
    m = model.mg_factor(sf.astype(np.int64), rows["ph"].astype(np.int64))
    e = (m * rows["mg"] * rows["ph"] + sf * rows["eg"] * (24 - rows["ph"])) / 1536.0
    e = e + np.where(rows["wtm"] == 1, tempo, -tempo)
    p = 1.0 / (1.0 + np.exp(-K * e / 400.0))
    d = (rows["y"] - p) ** 2 * rows["wt"]
    if mask is not None:
        return float(d[mask].sum() / rows["wt"][mask].sum())
    return float(d.sum() / rows["wt"].sum())


def main():
    global CFG_MASK
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", required=True); ap.add_argument("--data", nargs="*", default=[]); ap.add_argument("--n", type=int, default=400000)
    ap.add_argument("--tb", default=""); ap.add_argument("--tbn", type=int, default=2000); ap.add_argument("--tbw", type=float, default=0.3)
    ap.add_argument("--classes", default="KRvKB,KRvKN,KBvKP,KNvKP,KQvKR,KQvKP,KRvKP,KPvKP,KRPvKR,KRPvKB,KRPvKN,KBPvKB,KBPvKN,KNPvKB,KNPvKN,"
                                         "KPPvKP,KQvKRP,KRvKBP,KRvKNP,KBPvKP,KNPvKP,KQPvKQ,KRPvKP,KBBvKN,KBNvKB,KRBvKR,KRNvKR,KQBvKQ,KQRvKQ")
    ap.add_argument("--k", type=float, default=3.1544); ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--workers", type=int, default=2); ap.add_argument("--cache", default=""); ap.add_argument("--out", default="")
    ap.add_argument("--rounds", type=int, default=6)
    a = ap.parse_args()
    out = subprocess.run([a.engine], input=b"uci\nevalcfg\nquit\n", stdout=subprocess.PIPE).stdout.decode()
    CFG_MASK = int(re.search(r"^evalcfg (\d+)", out, re.M).group(1))
    model.set_cfg(CFG_MASK)
    if not (model.CFG.get("EV_SCALE") and model.CFG.get("EV_SCALE2")):
        raise SystemExit("--engine must be a build with EV_SCALE=1 EV_SCALE2=1")
    t = model.load_tables()
    tempo = int(t["tempo"])
    if a.cache and os.path.exists(a.cache):
        z = np.load(a.cache); rows = {k: z[k] for k in z.files}
    else:
        items = [(f, y, 1.0, 0) for f, y in data_positions(a.data, a.n, a.seed)] if a.data else []
        if a.tb:
            items += [(f, y, a.tbw, 1) for f, y in tb_positions(a.tb, a.classes.split(","), a.tbn, a.seed)]
        print(f"{len(items)} positions to reduce", flush=True)
        with Pool(a.workers, initializer=init_worker, initargs=(CFG_MASK,)) as pool:
            red = pool.map(reduce_position, [(f, y) for f, y, _, _ in items], chunksize=500)
        keep = [(r, w, src) for r, (_, _, w, src) in zip(red, items) if r is not None]
        cols = list(zip(*[r for r, _, _ in keep]))
        rows = {n: np.array(v) for n, v in zip(("mg", "eg", "ph", "wtm", "kind", "x1", "x2", "one", "sf1", "y"), cols)}
        rows["wt"] = np.array([w for _, w, _ in keep]); rows["src"] = np.array([s for _, _, s in keep])
        if a.cache: np.savez(a.cache, **rows)
    n = len(rows["y"]); src = rows["src"]
    print(f"{n} positions: data {int((src == 0).sum())}, tablebase {int((src == 1).sum())}; by rule: "
          + ", ".join(f"{k} {int((rows['kind'] == i).sum())}" for i, k in enumerate(KINDS)), flush=True)
    c0 = model.sc_consts()
    def report(tag, sf):
        parts = [f"all {loss(rows, sf, a.k, tempo):.6f}"]
        for name, m in (("data", src == 0), ("tb", src == 1)):
            if m.any(): parts.append(f"{name} {loss(rows, sf, a.k, tempo, m):.6f}")
        print(f"{tag:22s} " + "  ".join(parts), flush=True)
    report("v1 rules (EV_SCALE2=0)", rows["sf1"].astype(float))
    report("no scale (sf 64)", np.full(n, 64.0))
    report("starting constants", sf_of(rows, c0).astype(float))
    c = dict(c0); best = loss(rows, sf_of(rows, c).astype(float), a.k, tempo)
    for rnd_ in range(a.rounds):
        changed = False
        for name in model.SC_NAMES:
            lo, hi = LIMITS[name]
            for step in (8, 4, 2, 1):
                while True:
                    improved = False
                    for d in (step, -step):
                        v = c[name] + d
                        if not lo <= v <= hi: continue
                        trial = dict(c); trial[name] = v
                        l = loss(rows, sf_of(rows, trial).astype(float), a.k, tempo)
                        if l < best - 1e-9:
                            best, c, improved, changed = l, trial, True, True
                            break
                    if not improved: break
        print(f"round {rnd_ + 1}: loss {best:.6f}  " + " ".join(f"{k[3:]}={v}" for k, v in c.items()), flush=True)
        if not changed: break
    report("fitted constants", sf_of(rows, c).astype(float))
    for i, k in enumerate(KINDS):
        m = rows["kind"] == i
        if m.any():
            print(f"  rule {k:3s} n {int(m.sum()):7d}  loss start {loss(rows, sf_of(rows, c0).astype(float), a.k, tempo, m):.6f}"
                  f"  fitted {loss(rows, sf_of(rows, c).astype(float), a.k, tempo, m):.6f}  v1 {loss(rows, rows['sf1'].astype(float), a.k, tempo, m):.6f}")
    if a.out:
        with open(a.out, "w") as fh: json.dump(c, fh, indent=1)
        print("constants ->", a.out)


if __name__ == "__main__":
    main()
