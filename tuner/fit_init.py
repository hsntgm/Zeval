#!/usr/bin/env python3
"""Fit the EV_INIT constants (IN_* of src/eval.asm) to game results.

The initiative / complexity correction is not linear in its constants, so tune.py cannot fit it.  Its inputs do not depend on the constants:
the linear part of the evaluation (mg, eg with the tables of src/eval.asm), the phase, the scale factor for either strong side, and the seven
features (passed pawns, pawns, outflanking, infiltration, pawns on both flanks, no pieces, almost unwinnable).  Every position is reduced to
them once; the fit is then a numpy loop: coordinate descent over integer constants from zero (all zero = no correction), minimising the
tuner's loss  mean (label - sigmoid(K * eval / 400))^2  with the engine's integer arithmetic (tuner/model.py initiative, EVTOTAL).

Positions: a seeded reservoir sample of --n labelled positions from the given files (equal share per file, the same filters as extract.py:
at least --min-pieces pieces, positions with a special evaluation left out).  A fixed --val share is held out (by a hash of the FEN) and
reported with a bootstrap interval of the loss change against "no correction".
usage: fit_init.py --engine ./zeval --data a.epd b.epd [--n 1000000] [--k 3.1544] [--min-pieces 6] [--val 0.05] [--seed 1]
       [--workers 2] [--cache init.npz] [--out in.json]
--engine: any build of the source (its evalcfg sets the switches of the model; EV_INIT itself is applied here).  Needs numpy."""
import argparse, json, os, random, re, subprocess, sys, zlib
import numpy as np
from multiprocessing import Pool

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import model  # noqa: E402
from extract import parse_label  # noqa: E402  (the label formats of extract.py)

NAMES = ("IN_PASSED", "IN_PAWNS", "IN_OUTFL", "IN_BOTH", "IN_INFIL", "IN_NONPM", "IN_UNWIN", "IN_BIAS", "IN_MGADD")
LIMIT = 400
TABLES = None


def init_worker(mask):
    global TABLES
    model.set_cfg(mask)
    model.CFG["EV_INIT"] = 0
    TABLES = model.load_tables()


def reduce_position(args):
    """fen, label -> mg, eg, phase, wtm, sf if White is the strong side, sf if Black is, the seven features, label (or None)"""
    fen, label = args
    try:
        F = model.fen_features(fen)
    except ValueError:
        return None
    if F.special or "sinfo" not in F.aux:
        return None
    a = F.aux
    mg, eg = model.eval_sums(F, TABLES)
    wk, bk = a["wk"], a["bk"]
    outfl = abs((wk & 7) - (bk & 7)) - abs((wk >> 3) - (bk >> 3))
    both = int(bool(a["pawns"] & model.QSIDE and a["pawns"] & model.KSIDE))
    infil = int((wk >> 3) > 3 or (bk >> 3) < 4)
    unwin = int(not both and outfl < 0 and not a["npass"])
    return (mg, eg, min(F.phase, 24), int(F.wtm), model.scale_for(a["sinfo"], 1), model.scale_for(a["sinfo"], 0),
            a["npass"], model.popcount(a["pawns"]), outfl, infil, both, int(not a["pieces"]), unwin, label)


def sample(files, n, seed, min_pieces):
    rnd = random.Random(seed); keep = []; per = n // max(1, len(files))
    for path in files:
        res = []; seen = 0
        with open(path, errors="replace") as fh:
            for line in fh:
                t = line.split()
                if len(t) < 4 or sum(ch.isalpha() for ch in t[0]) < min_pieces:
                    continue
                lab = parse_label(" ".join(t[4:]))
                if lab is None:
                    continue
                seen += 1
                item = (" ".join(t[:4]) + " 0 1", lab)
                if len(res) < per:
                    res.append(item)
                else:
                    j = rnd.randrange(seen)
                    if j < per:
                        res[j] = item
        keep += res
    return keep


def tdiv(num, d):
    return np.where(num < 0, -((-num) // d), num // d)


def evals(R, c):
    """the engine's integer eval (White's view, tempo included) of every row under the constants c (None = EV_INIT off)"""
    mg, eg = R["mg"], R["eg"]
    if c is not None:
        cx = (c["IN_PASSED"] * R["npass"] + c["IN_PAWNS"] * R["pawns"] + c["IN_OUTFL"] * R["outfl"] + c["IN_INFIL"] * R["infil"]
              + c["IN_BOTH"] * R["both"] + c["IN_NONPM"] * R["nonpm"] - c["IN_UNWIN"] * R["unwin"] - c["IN_BIAS"])
        u = np.sign(mg) * np.minimum(np.maximum(cx + c["IN_MGADD"], -np.abs(mg)), 0)
        v = np.sign(eg) * np.maximum(cx, -np.abs(eg))
        mg, eg = mg + u, eg + v
    sf = np.where(eg > 0, R["sfw"], R["sfb"])
    e = tdiv(64 * mg * R["ph"] + sf * eg * (24 - R["ph"]), 1536)
    return e + np.where(R["wtm"] == 1, R["tempo"], -R["tempo"])


def sq_err(R, c, K):
    p = 1.0 / (1.0 + np.exp(-K * evals(R, c) / 400.0))
    return (R["y"] - p) ** 2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", required=True); ap.add_argument("--data", nargs="+", required=True)
    ap.add_argument("--n", type=int, default=1000000); ap.add_argument("--k", type=float, default=3.1544)
    ap.add_argument("--min-pieces", type=int, default=6); ap.add_argument("--val", type=float, default=0.05)
    ap.add_argument("--seed", type=int, default=1); ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--cache", default=""); ap.add_argument("--out", default=""); ap.add_argument("--rounds", type=int, default=8)
    ap.add_argument("--boot", type=int, default=200)
    a = ap.parse_args()
    out = subprocess.run([a.engine], input=b"uci\nevalcfg\nquit\n", stdout=subprocess.PIPE).stdout.decode()
    mask = int(re.search(r"^evalcfg (\d+)", out, re.M).group(1))
    model.set_cfg(mask)
    print("switches of the model: " + model.cfg_text() + "  (EV_INIT applied by this fit)", flush=True)
    tempo = int(model.load_tables()["tempo"])
    cols = ("mg", "eg", "ph", "wtm", "sfw", "sfb", "npass", "pawns", "outfl", "infil", "both", "nonpm", "unwin", "y")
    if a.cache and os.path.exists(a.cache):
        z = np.load(a.cache); R = {k: z[k] for k in z.files}
        print(f"cache {a.cache}: {len(R['y'])} positions", flush=True)
    else:
        items = sample(a.data, a.n, a.seed, a.min_pieces)
        print(f"{len(items)} positions to reduce", flush=True)
        with Pool(a.workers, initializer=init_worker, initargs=(mask,)) as pool:
            red = pool.map(reduce_position, items, chunksize=1000)
        keep = [(r, f) for r, (f, _) in zip(red, items) if r is not None]
        R = {n: np.array(v, dtype=np.float64 if n == "y" else np.int64) for n, v in zip(cols, zip(*[r for r, _ in keep]))}
        R["val"] = np.array([(zlib.crc32(f.encode()) % 10000) < a.val * 10000 for _, f in keep])
        if a.cache:
            np.savez(a.cache, **R)
    R["tempo"] = tempo
    tr = {k: (v[~R["val"]] if isinstance(v, np.ndarray) else v) for k, v in R.items()}
    va = {k: (v[R["val"]] if isinstance(v, np.ndarray) else v) for k, v in R.items()}
    n = len(R["y"])
    print(f"{n} positions (validation {int(R['val'].sum())}); unwin {int(R['unwin'].sum())}, no pieces {int(R['nonpm'].sum())}, "
          f"both flanks {int(R['both'].sum())}, infiltration {int(R['infil'].sum())}", flush=True)
    L = lambda S, c: float(sq_err(S, c, a.k).mean())
    hand = {k: model.IN[k] for k in NAMES}
    for tag, c in (("EV_INIT off", None), ("hand-set IN_* (asm)", hand)):
        print(f"{tag:22s} train {L(tr, c):.6f}  validation {L(va, c):.6f}", flush=True)
    c = {k: 0 for k in NAMES}; best = L(tr, c)
    for rnd_ in range(a.rounds):
        changed = False
        for name in NAMES:
            for step in (32, 16, 8, 4, 2, 1):
                while True:
                    improved = False
                    for d in (step, -step):
                        v = c[name] + d
                        if not -LIMIT <= v <= LIMIT:
                            continue
                        trial = dict(c); trial[name] = v
                        l = L(tr, trial)
                        if l < best - 1e-10:
                            best, c, improved, changed = l, trial, True, True
                            break
                    if not improved:
                        break
        print(f"round {rnd_ + 1}: train {best:.6f}  validation {L(va, c):.6f}  " + " ".join(f"{k[3:]}={v}" for k, v in c.items()), flush=True)
        if not changed:
            break
    d_off = sq_err(va, c, a.k) - sq_err(va, None, a.k)
    d_hand = sq_err(va, c, a.k) - sq_err(va, hand, a.k)
    rng = np.random.default_rng(a.seed)
    def ci(d):
        bs = [d[rng.integers(0, len(d), len(d))].mean() for _ in range(a.boot)]
        return float(d.mean()), float(np.percentile(bs, 2.5)), float(np.percentile(bs, 97.5))
    for tag, d in (("fitted - off", d_off), ("fitted - hand-set", d_hand)):
        m, lo, hi = ci(d)
        print(f"validation {tag:18s} {m:+.6f}  95 % [{lo:+.6f}, {hi:+.6f}]  " + ("significant" if hi < 0 else "not significant"), flush=True)
    changed_eval = float((evals(va, c) != evals(va, None)).mean())
    print(f"fitted constants change the eval of {100 * changed_eval:.1f} % of the validation positions", flush=True)
    if a.out:
        with open(a.out, "w") as fh:
            json.dump(c, fh, indent=1)
        print("constants ->", a.out)


if __name__ == "__main__":
    main()
