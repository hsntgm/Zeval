#!/usr/bin/env python3
"""Texel tuning of the engine's eval terms (material + PST, the terms of the full evaluation, bishop pair, tempo) - logistic regression on game results.

Method (Texel-style tuning of a linear eval):
  * the eval is linear in its parameters, the per-position feature counts are extracted ONCE (extract.py), the eval is never run again
  * prediction  p = sigmoid(K * eval / 400)  (White's view),  loss = mean (result - p)^2
  * K is fitted ONCE on the starting parameters (so the eval scale of the engine is kept), then fixed
  * gradient over the whole data set every epoch, Adam, early stopping on a held-out set
Additions for a small, self-generated data set:
  * --prior P : L2 pull of every parameter towards its starting value, strength = P pseudo-observations (a parameter that the data
    hardly sees stays near its starting value instead of drifting).  --prior-sweep 0,10,30,100 runs several and picks the best validation loss
  * validation split by GAME (positions of one game are never in both sets), early stopping, best-validation parameters returned
  * --free all|pst|p,n,b,r,q,k|bp|tempo|full|mob|pawns|passed|rooks|king|pieces|<term> : tune only some terms, the rest stay at their value
    (= "tune around the other terms").  First run:  --free bp,tempo  (low risk), then all
  * --lam : blend the game result with the engine's own search score as the training target (0 = pure result)
  * rounding to integers is evaluated (loss of the ROUNDED parameters = what the engine will really use)
  * --optimizer lbfgs : same objective, quasi-Newton (far fewer passes over the data)

usage: python3 tuner/tune.py data.npz --out params.json [--prior 30] [--free all] [--optimizer adam|lbfgs] [--epochs 3000]
                         [--lr 1.0] [--val 0.05] [--k K] [--lam 0] [--from-zero] [--seed 1] [--asm src/eval.asm]
needs numpy + scipy."""
import argparse, json, math, os, sys, time
import numpy as np
import scipy.sparse as sp
from scipy.special import expit
from scipy.optimize import minimize_scalar, minimize
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import model
from model import NF, NPAR, TEMPO_IDX


class Data:
    """a set of positions: X (csr, white-minus-black counts), phase fraction ph, side-to-move sign, target, endgame scale factor sf (0..64).
    The eval is  ph * mg + egw * eg + stm * tempo  with  egw = (1 - ph) * sf / 64  (the engine's taper, src/eval.asm)"""
    def __init__(self, X, ph, stm, y, cp, grp, sf=None):
        self.X, self.ph, self.stm, self.y, self.cp, self.grp = X, ph, stm, y, cp, grp
        self.sf = np.full(X.shape[0], 64.0) if sf is None else np.asarray(sf, np.float64)
        self.egw = (1.0 - ph) * self.sf / 64.0
        self.n = X.shape[0]

    def subset(self, sel):
        return Data(self.X[sel], self.ph[sel], self.stm[sel], self.y[sel], self.cp[sel], self.grp[sel], self.sf[sel])


def load(path, dtype):
    z = np.load(path)
    n = len(z["y"])
    X = sp.csr_matrix((z["data"].astype(dtype), z["indices"].astype(np.int32), z["indptr"]), shape=(n, NF))
    ph = np.minimum(z["phase"], 24).astype(np.float64) / 24.0
    if "cfg" in z.files and int(z["cfg"][0]) != model.cfg_mask():
        model.set_cfg(int(z["cfg"][0]))
        print(f"{path}: extracted with the evaluation switches {model.cfg_text()}")
    sf = z["sf"].astype(np.float64) if "sf" in z.files else None
    return Data(X, ph, z["stm"].astype(np.float64), z["y"].astype(np.float64), z["cp"].astype(np.float64), z["group"], sf)


def split(d, val_frac, seed):
    """validation set = whole groups (games), chosen by a seeded hash"""
    g = d.grp.astype(np.uint64)
    # the seed must enter BEFORE the multiplicative hash: added to the product it only changes bits below 2^13, which the >> 40 discards
    # (every seed gave the same validation games).  Wrap-around of the uint64 arithmetic is intended.
    x = (g + np.uint64((seed * 0xD1B54A32D192ED03 + 13) & 0xFFFFFFFFFFFFFFFF)) * np.uint64(0x9E3779B97F4A7C15)
    x ^= x >> np.uint64(29); x *= np.uint64(0xBF58476D1CE4E5B9)
    h = x >> np.uint64(40)
    isval = (h % np.uint64(10000)).astype(np.float64) < val_frac * 10000
    if isval.sum() == 0 or isval.all():
        raise SystemExit("validation split is empty - use more data or another --val")
    return d.subset(np.where(~isval)[0]), d.subset(np.where(isval)[0])


def scores(d, v):
    """White-view eval (cp, float) of every position for the flat parameter vector v"""
    W = np.stack([v[:NF], v[NF:2 * NF]], 1)
    A = d.X @ W
    return d.ph * A[:, 0] + d.egw * A[:, 1] + d.stm * v[TEMPO_IDX]


def mse(d, s, K, target=None):
    t = d.y if target is None else target
    return float(np.mean((t - expit(K * s / 400.0)) ** 2))


def fit_k(d, s, target=None, lo=0.2, hi=30.0):
    r = minimize_scalar(lambda k: mse(d, s, k, target), bounds=(lo, hi), method="bounded", options={"xatol": 1e-6})
    return float(r.x)


class Objective:
    """mean squared error + prior, with gradient, over the FREE parameters only (the others are fixed at v0)"""
    def __init__(self, d, K, v0, free, prior_curv, target=None):
        self.d, self.K, self.v0, self.free = d, K, v0.copy(), free
        self.idx = np.where(free)[0]
        self.target = d.y if target is None else target
        # penalty = pen * |dv|^2 has Hessian 2*pen.  P pseudo-observations have the Gauss-Newton Hessian prior_curv / n in mean-loss units
        # (prior_curv = P * 2 E[(p(1-p))^2] (K/400)^2), hence pen = prior_curv / (2 n).
        self.pen = prior_curv / (2.0 * d.n)
        self.XT = None

    def full(self, x):
        v = self.v0.copy(); v[self.idx] = x
        return v

    def __call__(self, x):
        d, K = self.d, self.K
        v = self.full(x)
        W = np.stack([v[:NF], v[NF:2 * NF]], 1)
        A = d.X @ W
        s = d.ph * A[:, 0] + d.egw * A[:, 1] + d.stm * v[TEMPO_IDX]
        p = expit(K * s / 400.0)
        e = self.target - p
        f = float(np.mean(e * e))
        g = (-2.0 * e * p * (1.0 - p) * (K / 400.0)) / d.n                   # d f / d s_i
        G = np.empty((d.n, 2)); G[:, 0] = g * d.ph; G[:, 1] = g * d.egw
        gW = d.X.T @ G
        grad = np.empty(NPAR); grad[:NF] = gW[:, 0]; grad[NF:2 * NF] = gW[:, 1]; grad[TEMPO_IDX] = float(g @ d.stm)
        dv = v - self.v0
        f += self.pen * float(dv[self.idx] @ dv[self.idx])
        grad = grad + 2.0 * self.pen * dv
        return f, grad[self.idx]


def prior_curvature(d, s, K, P):
    """curvature of ONE observation of a fully active feature, scaled by P pseudo-observations:  2 * E[(p(1-p))^2] * (K/400)^2 * P"""
    p = expit(K * s / 400.0)
    return P * 2.0 * float(np.mean((p * (1 - p)) ** 2)) * (K / 400.0) ** 2


def run_adam(obj, x0, val_loss, a, log):
    x = x0.copy(); m = np.zeros_like(x); u = np.zeros_like(x)
    b1, b2 = 0.9, 0.999
    best = (val_loss(x), x.copy(), 0); bad = 0
    t0 = time.time()
    for ep in range(1, a.epochs + 1):
        lr = a.lr * (a.lr_end / a.lr) ** (ep / a.epochs) if a.lr_end < a.lr else a.lr
        f, g = obj(x)
        # Adam is scale-free, so the raw gradient is fine
        m = b1 * m + (1 - b1) * g; u = b2 * u + (1 - b2) * g * g
        x -= lr * (m / (1 - b1 ** ep)) / (np.sqrt(u / (1 - b2 ** ep)) + 1e-12)
        if ep % a.eval_every == 0 or ep == a.epochs:
            vl = val_loss(x)
            if vl < best[0] - 1e-9:
                best = (vl, x.copy(), ep); bad = 0
            else:
                bad += 1
            log(f"epoch {ep:5d}  lr {lr:.4f}  train {f:.6f}  val {vl:.6f}  best {best[0]:.6f}@{best[2]}  [{time.time() - t0:.0f}s]")
            if bad >= a.patience and not a.final_only:
                log(f"early stop (no validation improvement for {bad * a.eval_every} epochs)"); break
    if a.final_only:
        return x, val_loss(x), ep
    return best[1], best[0], best[2]


class _Stop(Exception):
    pass


def run_lbfgs(obj, x0, val_loss, a, log):
    st = {"best": (val_loss(x0), x0.copy(), 0), "bad": 0, "it": 0, "x": x0.copy()}
    t0 = time.time()

    def cb(xk, *_):
        st["it"] += 1; st["x"] = xk.copy()
        if st["it"] % a.eval_every == 0:
            vl = val_loss(xk)
            if vl < st["best"][0] - 1e-9:
                st["best"] = (vl, xk.copy(), st["it"]); st["bad"] = 0
            else:
                st["bad"] += 1
            log(f"iter {st['it']:5d}  val {vl:.6f}  best {st['best'][0]:.6f}@{st['best'][2]}  [{time.time() - t0:.0f}s]")
            if st["bad"] >= a.patience and not a.final_only:
                raise _Stop()
    try:
        r = minimize(obj, x0, jac=True, method="L-BFGS-B", callback=cb, options={"maxiter": a.epochs, "maxfun": a.epochs * 2, "ftol": 1e-13, "gtol": 1e-10, "maxcor": 30})
        xf = r.x
        log(f"L-BFGS finished: {r.message}  iterations {r.nit}")
    except _Stop:
        log("early stop (no validation improvement)"); xf = st["x"]
    vl = val_loss(xf)
    if a.final_only:
        return xf, vl, st["it"]
    if vl < st["best"][0]: st["best"] = (vl, xf.copy(), st["it"])
    return st["best"][1], st["best"][0], st["best"][2]


def pname(i):
    if i == TEMPO_IDX: return "tempo"
    return f"{model.param_name(i % NF)} {'mg' if i < NF else 'eg'}"


def bootstrap(train, K, v0, free, P, B, seed, target=None):
    """game-cluster bootstrap for runs with few free parameters: re-fit on B resamples of the GAMES, return the (B, n_free) array of fitted values"""
    rng = np.random.default_rng(seed)
    order = np.argsort(train.grp, kind="stable")
    g = train.grp[order]
    cut = np.flatnonzero(np.r_[True, g[1:] != g[:-1], True])
    ng = len(cut) - 1
    out = []
    for b in range(B):
        pick = rng.integers(0, ng, ng)
        rows = np.concatenate([order[cut[i]:cut[i + 1]] for i in pick])
        sub = train.subset(rows)
        tg = None if target is None else target[rows]
        s0 = scores(sub, v0)
        obj = Objective(sub, K, v0, free, prior_curvature(sub, s0, K, P), tg)
        r = minimize(obj, v0[obj.idx].copy(), jac=True, method="L-BFGS-B", options={"maxiter": 500, "ftol": 1e-14, "gtol": 1e-10})
        out.append(r.x)
    return np.array(out)


def tune_once(train, val, K, v0, free, P, a, log, targ_tr=None, targ_va=None):
    s0 = scores(train, v0)
    curv = prior_curvature(train, s0, K, P)
    obj = Objective(train, K, v0, free, curv, targ_tr)
    vobj = Objective(val, K, v0, free, 0.0, targ_va)
    val_loss = lambda x: vobj(x)[0]
    x0 = v0[obj.idx].copy()
    log(f"prior {P:g} pseudo-observations  (curvature {curv:.3e} per cp^2 on the summed loss);  free parameters: {len(obj.idx)} of {NPAR}")
    run = run_adam if a.optimizer == "adam" else run_lbfgs
    # few free parameters (e.g. --free bp,tempo): no overfitting possible, the validation set is just noise -> converge fully, no early stopping
    a.final_only = len(obj.idx) <= 50
    if a.final_only:
        log("few free parameters: running to convergence, no early stopping")
    x, vl, ep = run(obj, x0, val_loss, a, log)
    return obj.full(x), vl, ep


def game_sums(d, v_new, v_old, K, target=None):
    """per game: (sum of [squared error of v_new - squared error of v_old] over its positions, number of positions) - the units of the paired game-cluster bootstrap"""
    t = d.y if target is None else target
    dd = (t - expit(K * scores(d, v_new) / 400.0)) ** 2 - (t - expit(K * scores(d, v_old) / 400.0)) ** 2
    _, inv = np.unique(d.grp, return_inverse=True)
    return np.bincount(inv, weights=dd), np.bincount(inv).astype(np.float64)


def paired_ci(sums, cnts, B=2000, seed=1):
    """mean loss change per position (negative = v_new is better) and its 95 % interval from a bootstrap over GAMES (positions of one game are correlated)"""
    rng = np.random.default_rng(seed)
    ng = len(sums)
    step = max(1, min(B, 20_000_000 // max(ng, 1)))      # resamples per batch: bounded memory for large validation sets, same draws as one batch
    bs = np.empty(B)
    for b0 in range(0, B, step):
        pick = rng.integers(0, ng, (min(step, B - b0), ng))
        bs[b0:b0 + len(pick)] = sums[pick].sum(1) / cnts[pick].sum(1)
    lo, hi = np.percentile(bs, [2.5, 97.5])
    return float(sums.sum() / cnts.sum()), float(lo), float(hi)


def round_flat(v, t):
    """round to what the engine can store: pst = round(merged) - material (material stays as in the asm); returns (flat vector of the rounded eval, tables)"""
    w, tempo = model.unflat(v)
    T = model.tables_from_merged(w, tempo, t)
    w2 = model.merged_from_tables({**T, "matmg": t["matmg"], "mateg": t["mateg"]})
    return model.flat(w2, T["tempo"]), T


def scale_to(d, v, target, tol=0.0):
    """pure scale change of the WHOLE eval (every parameter, the frozen ones too, and the tempo) so that mean|eval| over the positions of d equals `target`.
    scores() is linear in v, so the factor is exact (no search).  A factor within `tol` of 1 is not applied: the rounding noise of a tiny rescale (every
    table entry moves by up to 0.5) costs more than the shift.  Returns (v', factor, applied)."""
    cur = float(np.mean(np.abs(scores(d, v))))
    r = target / cur if cur > 0 else 1.0
    if abs(r - 1.0) < tol: return v, r, False
    return v * r, r, True


def tables_json(T):
    return {k: (v.tolist() if isinstance(v, np.ndarray) else v) for k, v in T.items()}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("data"); ap.add_argument("--out", default="params.json")
    ap.add_argument("--asm", default=model.ASM)
    ap.add_argument("--prior", type=float, default=None, help="pseudo-observations pulling every parameter towards its START value (default 30; 0 with --from-zero)"); ap.add_argument("--prior-sweep", default="")
    ap.add_argument("--free", default="all")
    ap.add_argument("--optimizer", choices=("adam", "lbfgs"), default="adam")
    ap.add_argument("--epochs", type=int, default=3000); ap.add_argument("--lr", type=float, default=1.0); ap.add_argument("--lr-end", type=float, default=0.02)
    ap.add_argument("--eval-every", type=int, default=10); ap.add_argument("--patience", type=int, default=30)
    ap.add_argument("--val", type=float, default=0.05); ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--k", type=float, default=0.0, help="fixed K (default: fitted on the starting parameters)")
    ap.add_argument("--bootstrap", type=int, default=0, help="runs with <= 50 free parameters: B game-resampled refits -> mean / sd of every tuned value (is the change significant?)")
    ap.add_argument("--lam", type=float, default=0.0, help="target = (1-lam)*result + lam*sigmoid(Kcp*search_cp/400)  (needs game data with scores)")
    ap.add_argument("--from-zero", action="store_true", help="start the FREE parameters from zero (needs --k); parameters that are not free keep their asm value; the default prior is then 0 (a prior towards zero would only shrink the eval)")
    ap.add_argument("--norm-scale", default="", metavar="asm|CP",
                    help="after tuning, rescale the whole eval by one factor so that mean|eval| over the training positions equals that of the START tables ('asm') or a number "
                         "of cp.  The search margins (RFP / futility / razor / NMP / delta, SPSA T0..T19, T58) are tuned for one eval scale; a tuned eval that is larger or smaller "
                         "costs Elo by itself (measured: about -30 Elo for +-10 %), which hides the gain of the tuning in an SPRT")
    ap.add_argument("--norm-tol", type=float, default=0.005, help="--norm-scale is skipped when the factor is within this distance of 1 (default 0.005 = 0.5 %%)")
    a = ap.parse_args()
    t_start = time.time()
    log = lambda s: print(s, flush=True)

    v0, t = model.initial_flat(a.asm)
    d = load(a.data, np.float64)
    log(f"data: {d.n} positions, {d.X.nnz / d.n:.1f} non-zero features each, mean label {d.y.mean():.3f}, {len(np.unique(d.grp))} games/groups")
    w = d.stm > 0
    for name, m in (("White to move", w), ("Black to move", ~w)):
        if m.any():
            y = d.y[m]
            log(f"  {name}: {int(m.sum())} ({m.mean() * 100:.1f} %)   label 1.0: {np.mean(y == 1) * 100:.1f} %   0.5: {np.mean(y == 0.5) * 100:.1f} %   0.0: {np.mean(y == 0) * 100:.1f} %   avg {y.mean():.3f}")
    nz = np.diff(d.X.indptr)
    log(f"  features per position: min {nz.min()}  max {nz.max()}  avg {nz.mean():.1f};  parameters {NPAR}")
    train, val = split(d, a.val, a.seed)
    log(f"split by group: train {train.n}  validation {val.n}")
    v_asm = v0.copy()
    free = model.group_mask(a.free)
    if a.from_zero:
        if a.k <= 0: raise SystemExit("--from-zero needs --k (K cannot be fitted on an all-zero eval)")
        v0 = np.where(free, 0.0, v_asm)                    # free parameters start at zero, the others stay as in the asm
        if a.prior is None:
            a.prior = 0.0
        elif a.prior > 0 or a.prior_sweep:
            log("NOTE: --from-zero with a prior > 0: the prior pulls the free parameters towards ZERO (their start), i.e. it shrinks the eval.")
    if a.prior is None:
        a.prior = 30.0

    s_tr, s_va = scores(train, v0), scores(val, v0)
    r = float(np.corrcoef(s_tr, train.y)[0, 1]) if (np.std(s_tr) > 0 and np.std(train.y) > 0) else float("nan")
    log(f"sanity: correlation(eval of the starting parameters, result) = {r:+.3f}" + ("   (constant eval: expected with --from-zero)" if r != r else ""))
    if r < 0.15 and not a.from_zero:
        log("WARNING: the eval hardly correlates with the labels. Wrong label side (use --stm-wdl for side-to-move labels) or garbage data?")
    K = a.k if a.k > 0 else fit_k(train, s_tr)
    log(f"K = {K:.4f}  (sigmoid(K * cp / 400))   mean|eval| {np.mean(np.abs(s_tr)):.1f} cp")

    tgt_tr = tgt_va = None
    if a.lam > 0:
        ok_tr, ok_va = ~np.isnan(train.cp), ~np.isnan(val.cp)
        if not (ok_tr.all() and ok_va.all()):
            raise SystemExit("--lam needs search scores in every position (game data only, no plain EPD sets)")
        Kc = fit_k(train, train.cp, train.y)
        log(f"search-score target: K for the engine's own search scores = {Kc:.3f}; blending lam = {a.lam}")
        tgt_tr = (1 - a.lam) * train.y + a.lam * expit(Kc * train.cp / 400.0)
        tgt_va = (1 - a.lam) * val.y + a.lam * expit(Kc * val.cp / 400.0)

    base_tr = mse(train, s_tr, K, tgt_tr); base_va = mse(val, s_va, K, tgt_va)
    const = float(np.var(train.y))
    log(f"start loss  train {base_tr:.6f}  validation {base_va:.6f}   (constant predictor: {const:.6f})")

    priors = [float(x) for x in a.prior_sweep.split(",")] if a.prior_sweep else [a.prior]
    results = []
    for P in priors:
        log(f"\n=== prior {P:g}")
        v, vl, ep = tune_once(train, val, K, v0, free, P, a, log, tgt_tr, tgt_va)
        results.append((vl, P, v, ep))
        log(f"prior {P:g}: best validation loss {vl:.6f} at epoch/iteration {ep}")
    if len(results) > 1:
        log("\nprior sweep (validation loss of the float parameters):")
        for vl, P, _, ep in results:
            log(f"  prior {P:8g}   val {vl:.6f}   ({(vl - base_va) / base_va * 100:+.2f} % vs start)   best at {ep}")
        best_v = min(results, key=lambda r: r[0])[2]
        log(f"paired bootstrap over the {len(np.unique(val.grp))} validation GAMES: change of the validation loss, 95 % interval (an interval that contains 0 = not distinguishable)")
        indist = []
        for vl, P, v_, _ in results:
            m0, lo0, hi0 = paired_ci(*game_sums(val, v_, v_asm, K, tgt_va), seed=a.seed)
            m1, lo1, hi1 = paired_ci(*game_sums(val, v_, best_v, K, tgt_va), seed=a.seed)
            if lo1 <= 0.0 <= hi1: indist.append(P)
            log(f"  prior {P:8g}   vs start {m0:+.6f} [{lo0:+.6f}, {hi0:+.6f}]   vs best prior {m1:+.6f} [{lo1:+.6f}, {hi1:+.6f}]")
        if indist:
            log(f"  suggestion: the largest prior that is not distinguishable from the best one is {max(indist):g} (less over-fit)")
    vl, P, v, ep = min(results, key=lambda r: r[0])
    log(f"\nchosen: prior {P:g}")

    mae_asm_train = float(np.mean(np.abs(scores(train, v_asm))))
    cur_abs = float(np.mean(np.abs(scores(train, v))))
    to_asm = mae_asm_train / cur_abs if cur_abs > 0 else 1.0
    log(f"eval scale: mean|eval| over the training positions: asm tables {mae_asm_train:.1f} cp, tuned (float) {cur_abs:.1f} cp.  To keep the asm scale every value would be x{to_asm:.4f}"
        f"  (= --norm-scale asm;  a dev switch that scales the eval by N/1024 would be N = {int(round(1024 * to_asm))})")
    tgt_abs = None
    if a.norm_scale:
        try: tgt_abs = mae_asm_train if a.norm_scale == "asm" else float(a.norm_scale)
        except ValueError: tgt_abs = -1.0
        if not tgt_abs > 0: raise SystemExit("--norm-scale: 'asm' or a positive number (mean|eval| in cp over the training positions)")
    v_raw = v; scale_applied = False; scale_ratio = 1.0
    if tgt_abs is not None:
        v, scale_ratio, scale_applied = scale_to(train, v, tgt_abs, a.norm_tol)
        if scale_applied:
            log(f"--norm-scale: the whole eval (all {NPAR} parameters, also the frozen ones) x {scale_ratio:.4f}: mean|eval| {cur_abs:.1f} -> {tgt_abs:.1f} cp (a pure scale change, rounded below)")
        else:
            log(f"--norm-scale: factor {scale_ratio:.4f} is within {a.norm_tol:g} of 1: not applied (the parameters stay exactly as tuned)")

    vr, T = round_flat(v, t)
    log("\n                              train       validation")
    log(f"start (asm as given)       {base_tr:.6f}    {base_va:.6f}")
    log(f"tuned, float               {mse(train, scores(train, v), K, tgt_tr):.6f}    {mse(val, scores(val, v), K, tgt_va):.6f}")
    log(f"tuned, rounded (ENGINE)    {mse(train, scores(train, vr), K, tgt_tr):.6f}    {mse(val, scores(val, vr), K, tgt_va):.6f}")
    if scale_applied:
        vr_raw, _T = round_flat(v_raw, t)
        log(f"tuned, rounded, NOT normalised {mse(train, scores(train, vr_raw), K, tgt_tr):.6f}    {mse(val, scores(val, vr_raw), K, tgt_va):.6f}   (the price of --norm-scale = the difference to the line above)")
    s_all = scores(train, vr)
    if tgt_abs is not None:
        log(f"mean|eval| of the rounded ENGINE tables {np.mean(np.abs(s_all)):.1f} cp, target {tgt_abs:.1f} cp ({(np.mean(np.abs(s_all)) / tgt_abs - 1) * 100:+.2f} %)")
    K2 = fit_k(train, s_all, tgt_tr)
    log(f"scale check: K before {K:.4f}, K refitted on the tuned eval {K2:.4f} ({(K2 / K - 1) * 100:+.1f} %);  mean|eval| {np.mean(np.abs(s_tr)):.1f} -> {np.mean(np.abs(s_all)):.1f} cp")
    log("   (judge a scale shift by mean|eval|, not by K: K also rises when the tuned eval simply predicts better.  A tuned eval that grew a lot shifts the margins of RFP / futility / razor / NMP: re-tune them with SPSA afterwards)")
    dv = vr - model.initial_flat(a.asm)[0]
    ch = np.abs(dv) > 0
    log(f"changed parameters: {int(ch.sum())} of {NPAR};  mean |change| {np.abs(dv[ch]).mean() if ch.any() else 0:.1f} cp, max {np.abs(dv).max():.0f};  "
        f"bishop pair {T['bp_mg']}/{T['bp_eg']} (was {t['bp_mg']}/{t['bp_eg']}), tempo {T['tempo']} (was {t['tempo']})")
    v_init = v_asm
    order = [i for i in np.argsort(-np.abs(dv))[:8] if dv[i] != 0]
    log("largest changes (merged value = pst + material, White's view):  " + ";  ".join(f"{pname(i)} {v_init[i]:.0f}->{vr[i]:.0f}" for i in order))
    # baseline = the asm tables as given (also with --from-zero, where the optimiser starts elsewhere): this is what apply_params's gate compares with
    asm_tr, asm_va = mse(train, scores(train, v_asm), K, tgt_tr), mse(val, scores(val, v_asm), K, tgt_va)
    rnd_va = mse(val, scores(val, vr), K, tgt_va)
    mae_asm, mae_new = float(np.mean(np.abs(scores(train, v_asm)))), float(np.mean(np.abs(s_all)))
    dm, dlo, dhi = paired_ci(*game_sums(val, vr, v_asm, K, tgt_va), seed=a.seed)
    verdict = "significant improvement" if dhi < 0 else ("significantly WORSE" if dlo > 0 else "NOT distinguishable from zero: more games or a larger --val; only an SPRT decides")
    log(f"validation loss change of the ROUNDED tables vs the asm tables: {dm:+.6f}  95 % game-bootstrap [{dlo:+.6f}, {dhi:+.6f}]  ({len(np.unique(val.grp))} validation games)  -> {verdict}")
    if rnd_va > asm_va + 1e-9:
        log(f"WARNING: the ROUNDED tuned parameters have a WORSE validation loss ({rnd_va:.6f}) than the asm tables ({asm_va:.6f}); apply_params.py will refuse them without --force.")
    cover = np.bincount(train.X.indices, minlength=NF)
    log(f"coverage: {int((cover[:model.NPST] == 0).sum())} of {model.NPST} piece-square entries never occur in the training data (they only feel the prior), {int((cover[:model.NPST] < 20).sum())} occur < 20 times;  "
        f"terms of the full evaluation: {int((cover[model.EVP0:] == 0).sum())} of {model.EV_N} never occur, {int((cover[model.EVP0:] < 20).sum())} occur < 20 times")

    if a.bootstrap > 0:
        idx = np.where(free)[0]
        if len(idx) > 50:
            log("--bootstrap is only done for <= 50 free parameters (e.g. --free bp,tempo); skipped")
        else:
            bs = bootstrap(train, K, v0, free, P, a.bootstrap, a.seed, tgt_tr)
            log(f"\ngame bootstrap ({a.bootstrap} resamples of {len(np.unique(train.grp))} training games): value  start -> tuned  | bootstrap mean +- sd  [2.5 %, 97.5 %]")
            for c, i in enumerate(idx):
                lo, hi = np.percentile(bs[:, c], [2.5, 97.5])
                sd = bs[:, c].std(ddof=1)
                z = (vr[i] - v0[i]) / sd if sd > 0 else float("inf")
                log(f"  {pname(i):16s} {v0[i]:7.1f} -> {vr[i]:7.1f}  | {bs[:, c].mean():7.1f} +- {sd:5.1f}  [{lo:.1f}, {hi:.1f}]   change = {z:+.1f} sd")
            log("  (a change below ~2 sd is not distinguishable from noise: generate more games before trusting it)")

    out = {"format": "asmchess-tuner-1", "K": K, "prior": P, "optimizer": a.optimizer, "epochs_best": ep, "positions_train": train.n, "positions_val": val.n,
           "free": a.free, "lam": a.lam, "from_zero": bool(a.from_zero), "data": os.path.basename(a.data),
           "mean_abs_eval": {"asm": mae_asm, "tuned": mae_new},
           "scale": {"norm_scale": a.norm_scale, "applied": bool(scale_applied), "factor_applied": scale_ratio, "mean_abs_eval_tuned_float": cur_abs,
                     "factor_to_asm_scale": to_asm, "oscale_1024_to_asm": int(round(1024 * to_asm))},
           "loss": {"start_train": base_tr, "start_val": base_va, "tuned_val": vl, "asm_train": asm_tr, "asm_val": asm_va,
                    "rounded_train": mse(train, scores(train, vr), K, tgt_tr), "rounded_val": mse(val, scores(val, vr), K, tgt_va),
                    "delta_val_ci": [dm, dlo, dhi]},
           "merged_float": {"mg": v[:NF].tolist(), "eg": v[NF:2 * NF].tolist(), "tempo": float(v[TEMPO_IDX])},
           "tables": tables_json(T),
           "base_asm_material": {"matmg": t["matmg"].tolist(), "mateg": t["mateg"].tolist()}}
    json.dump(out, open(a.out, "w"))
    log(f"-> {a.out}   [{time.time() - t_start:.0f}s]")
    if len(results) > 1:
        for vl_, P_, v_, _ in results:
            vr_, T_ = round_flat(scale_to(train, v_, tgt_abs, a.norm_tol)[0] if tgt_abs is not None else v_, t)
            o2 = dict(out, prior=P_, tables=tables_json(T_))
            json.dump(o2, open(os.path.splitext(a.out)[0] + f"_prior{P_:g}.json", "w"))


if __name__ == "__main__":
    main()
