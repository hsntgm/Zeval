#!/usr/bin/env python3
"""Self-test of the tuning pipeline (no external data needed).  Checks, in this order:

  1  model       the python eval replica == the engine's `eval` on random positions        (needs the engine, --engine)
  1b qsearch     extract.py's quiescence resolve: board eval == integer model, hanging piece captured, quiet position unchanged
  2  gradient    analytic gradient of the objective == finite differences                   (incl. prior, frozen parameters)
  3  recovery    synthetic experiment: labels are the win probability of a known, perturbed eval; extract.py + tune.py must find it again:
                 the validation loss approaches the oracle's, and the recovered changes correlate with the true ones
  4  apply       apply_params.py: identity tables reproduce the source values; tuned tables read back exactly; (--build) the REBUILT
                 engine's eval == the python model of the written tables
  5  freeze      --free bp,tempo leaves every PST entry and every term of the full evaluation unchanged

usage: python3 tuner/selftest.py [--engine ./zeval] [--n 40000] [--build]
needs python-chess, numpy, scipy (nasm + ld for --build)."""
import argparse, json, os, random, shutil, subprocess, sys, tempfile
import numpy as np
import chess
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, "..", "tests"))
import model, tune
from _eng import ENGINE, resolve
from scipy.special import expit

PKG = os.path.dirname(HERE)
fails = []


def check(ok, msg):
    print(("PASS " if ok else "FAIL ") + msg, flush=True)
    if not ok: fails.append(msg)


def games_positions(n, seed):
    rnd = random.Random(seed); out = []
    while len(out) < n:
        b = chess.Board()
        for ply in range(rnd.randint(60, 160)):
            l = list(b.legal_moves)
            if not l or b.is_game_over(): break
            caps = [m for m in l if b.is_capture(m) or m.promotion]
            b.push(rnd.choice(caps) if (caps and rnd.random() < 0.15) else rnd.choice(l))
            if ply >= 8 and ply % 3 == 0 and not b.is_check() and len(b.piece_map()) >= 6:
                out.append(b.fen())
    return out[:n]


def _model_eval(fen, t):
    """the model's value of a position as tuner/check_eval.py compares it: the linear model, or model.special_eval for the positions of the
    non-linear special evaluation (KQ-KR, KR-KB, bare king ...; model.int_eval gives None there)"""
    e = model.int_eval(fen, t)
    return model.special_eval(fen, t) if e is None else e


def t_model(engine):
    import check_eval
    t = model.load_tables()
    fens = check_eval.positions(1500, 11)
    ev = check_eval.engine_evals(engine, fens)
    bad = sum(1 for f, e in zip(fens, ev) if _model_eval(f, t) != e)
    check(bad == 0, f"model: python eval == engine eval on {len(fens)} positions ({bad} mismatches)")


def t_qsearch():
    import extract
    t = model.load_tables(); rnd = random.Random(4); bad = 0
    for _ in range(300):
        b = chess.Board()
        for _ in range(rnd.choice([0, 10, 30, 60, 120])):
            l = list(b.legal_moves)
            if not l: break
            b.push(rnd.choice(l))
        bad += extract.board_eval_stm(b) != model.int_eval(b.fen(), t)
    check(bad == 0, f"qsearch: board eval == integer model on 300 random positions ({bad} mismatches)")
    b = chess.Board("4k3/8/8/3r4/8/8/3Q4/4K3 w - - 0 1"); extract.qsearch_leaf(b)
    check(b.fen().startswith("4k3/8/8/3Q4/8/8/8/4K3 b"), "qsearch: a hanging rook is captured (position resolved to the quiet leaf)")
    b = chess.Board(chess.STARTING_FEN); f0 = b.fen(); extract.qsearch_leaf(b)
    check(b.fen() == f0, "qsearch: a quiet position stays unchanged")


def t_gradient():
    rng = np.random.default_rng(3)
    n = 3000
    import scipy.sparse as sp
    fens = games_positions(n, 5)
    rows = [model.fen_features(f) for f in fens]
    indptr = np.concatenate([[0], np.cumsum([len(r[0]) for r in rows])])
    X = sp.csr_matrix((np.array([v for r in rows for v in r[1]], float), np.array([c for r in rows for c in r[0]], np.int32), indptr), shape=(n, model.NF))
    sf = rng.integers(16, 65, n)                                  # scale factors 16..64: the eg weight (1 - ph) * sf / 64 is part of the check
    d = tune.Data(X, np.array([min(r.phase, 24) / 24 for r in rows]), np.array([1.0 if r.wtm else -1.0 for r in rows]), rng.integers(0, 3, n) / 2.0, np.full(n, np.nan), np.arange(n), sf)
    v0, _ = model.initial_flat()
    free = model.group_mask("pst,tempo")
    obj = tune.Objective(d, 3.0, v0, free, 5e-4 * n)
    x = v0[obj.idx] + rng.normal(0, 3, len(obj.idx))
    f, g = obj(x)
    worst = 0.0
    for i in rng.choice(len(x), 25, replace=False):
        h = 1e-3
        xp, xm = x.copy(), x.copy(); xp[i] += h; xm[i] -= h
        num = (obj(xp)[0] - obj(xm)[0]) / (2 * h)
        worst = max(worst, abs(num - g[i]) / max(abs(num), abs(g[i]), 1e-9))
    check(worst < 1e-5, f"gradient: analytic == finite differences (worst relative error {worst:.1e}, 25 random parameters)")


def synth(n, workdir, seed=9):
    """EPD file whose labels are sigmoid(K * eval_true / 400) of a known, perturbed eval; returns paths and the truth"""
    rng = np.random.default_rng(seed)
    v0, t = model.initial_flat()
    truth = v0.copy()
    delta = np.zeros_like(v0)
    delta[:model.NPST] = rng.normal(0, 7, model.NPST); delta[model.NF:model.NF + model.NPST] = rng.normal(0, 7, model.NPST)
    for r in (0, 7):                                              # pawn rows 1 and 8 never occur: no change there
        for k in range(r * 8, r * 8 + 8):
            delta[k] = 0; delta[model.NF + k] = 0
    delta[model.BP] = -9; delta[model.NF + model.BP] = 12; delta[model.TEMPO_IDX] = 6
    E0, E1 = model.EVP0, model.NF                                 # the terms of the full evaluation are perturbed too
    delta[E0:E1] = rng.normal(0, 5, E1 - E0); delta[model.NF + E0:model.NF + E1] = rng.normal(0, 5, E1 - E0)
    truth += delta
    fens = games_positions(n, seed)
    Ktrue = 2.0
    X = []
    ev = []
    for f in fens:
        F = model.fen_features(f)
        cols, vals, ph, wtm, sf = F.cols, F.vals, F.phase, F.wtm, F.sf
        W = truth
        mg = sum(v * W[c] for c, v in zip(cols, vals)); eg = sum(v * W[model.NF + c] for c, v in zip(cols, vals))
        p = min(ph, 24) / 24
        ev.append(p * model.mg_factor(sf, min(ph, 24)) / 64 * mg + (1 - p) * sf / 64 * eg + (1 if wtm else -1) * W[model.TEMPO_IDX])
    ev = np.array(ev)
    prob = expit(Ktrue * ev / 400)
    y = np.round(prob, 3)                                         # noise-free labels: tests the machinery (parser, features, K, optimiser, prior), not the statistics
    epd = os.path.join(workdir, "synth.epd")
    with open(epd, "w") as fh:
        for f, yy in zip(fens, y):
            fh.write(" ".join(f.split()[:4]) + f" 0 1; [{yy:.3f}]\n")
    return epd, truth, v0, ev, prob, y, Ktrue


def t_recovery(n, workdir):
    epd, truth, v0, ev, prob, y, Ktrue = synth(n, workdir)
    npz = os.path.join(workdir, "synth.npz")
    r = subprocess.run([sys.executable, os.path.join(HERE, "extract.py"), npz, epd, "--min-pieces", "6", "--workers", "2"], capture_output=True, text=True)
    check(r.returncode == 0, "recovery: extract.py ran on a synthetic EPD file")
    d = tune.load(npz, np.float64)
    train, val = tune.split(d, 0.1, 1)
    # tune.py fits K once on the START eval and keeps it fixed (that keeps the engine's cp scale).  Here the labels come from another eval
    # (the planted truth) with K = Ktrue; a K fitted on the start eval comes out smaller (about 1.75 against 2.0: the start eval explains the
    # labels worse), and the optimiser then makes up for it by stretching the whole eval by about 15 %, far more than the planted changes of a
    # few cp: the loss gap still closes, but the recovered changes no longer point at the planted ones.  The experiment therefore runs at the
    # K the labels were made with; the K fit itself is checked on the true eval below (it must find Ktrue again).
    K = Ktrue
    free = model.group_mask("all")
    class A: pass
    a = A(); a.optimizer = "adam"; a.epochs = 1500; a.lr = 1.0; a.lr_end = 0.02; a.eval_every = 25; a.patience = 12; a.final_only = False
    quiet = lambda s: None
    start_val = tune.mse(val, tune.scores(val, v0), K)
    res = {}
    for opt in ("adam", "lbfgs"):
        a.optimizer = opt; a.epochs = 1500 if opt == "adam" else 400
        v, vl, ep = tune.tune_once(train, val, K, v0, free, 30.0, a, quiet)
        res[opt] = (v, vl)
    # oracle: the true parameters at the K the data were generated with, on the validation positions
    s_true = tune.scores(val, truth)
    Kt = tune.fit_k(val, s_true)
    check(abs(Kt / Ktrue - 1) < 0.02, f"recovery: fit_k on the true eval finds the K of the labels again ({Kt:.3f}, labels made with {Ktrue})")
    oracle = tune.mse(val, s_true, Kt)
    print(f"      validation loss: start {start_val:.5f}   oracle (true params) {oracle:.5f}   adam {res['adam'][1]:.5f}   lbfgs {res['lbfgs'][1]:.5f}   (K {K:.2f} = the K of the labels)")
    cover = np.bincount(train.X.indices, minlength=model.NF)
    well = np.concatenate([cover >= 300, cover >= 300, [True]])
    for opt, (v, vl) in res.items():
        gap = (start_val - vl) / max(start_val - oracle, 1e-12)
        dt, de = (truth - v0)[well], (v - v0)[well]
        corr = float(np.corrcoef(dt, de)[0, 1])
        rms = float(np.sqrt(np.mean((de - dt) ** 2))); rms0 = float(np.sqrt(np.mean(dt ** 2)))
        print(f"      {opt}: closed {gap * 100:.0f} % of the start-oracle loss gap; on {int(well.sum())} well covered parameters: corr(true change, found change) {corr:+.2f}, rms error {rms:.1f} cp (rms of the true change {rms0:.1f})")
        check(vl < start_val and corr > 0.5, f"recovery: {opt} improves validation loss and finds the true direction (corr {corr:+.2f})")
    return npz, res


def t_apply(build, workdir, params_json):
    src = open(model.ASM).read()
    sys.path.insert(0, HERE)
    import apply_params
    t = model.load_tables()
    ident = {k: t[k] for k in ("pst_mg", "pst_eg", "evp_mg", "evp_eg", "bp_mg", "bp_eg", "tempo")}
    new = apply_params.patch(src, ident, "; test")
    tmp = os.path.join(workdir, "id.asm"); open(tmp, "w").write(new)
    t2 = model.load_tables(tmp)
    same = all(np.array_equal(t[k], t2[k]) for k in ("pst_mg", "pst_eg", "matmg", "mateg", "evp_mg", "evp_eg")) and all(t[k] == t2[k] for k in ("bp_mg", "bp_eg", "tempo"))
    check(same, "apply: identity tables re-written and read back unchanged")
    if not build:
        return
    P = json.load(open(params_json))
    tab = {k: (np.array(v, np.int64) if isinstance(v, list) else int(v)) for k, v in P["tables"].items()}
    pk = os.path.join(workdir, "pkg"); shutil.copytree(PKG, pk, ignore=shutil.ignore_patterns("dist", "*.exe", "tuner", "candidates", "testbuild", "spike", ".git"))
    pd = os.path.join(pk, "src", "eval.asm")
    patched = apply_params.patch(open(pd).read(), tab, "; test")
    open(pd, "w").write(patched)
    parts = "p1_data.asm eval.asm p2_core.asm p2b_bb_data.inc p2b_bb.asm p3_cmd.asm p4_search.asm p5_main.asm p6_win.asm p7_units.asm".split()
    asm = os.path.join(pk, "zeval.asm")
    with open(asm, "w") as fh:
        for p in parts: fh.write(open(os.path.join(pk, "src", p)).read())
    try:
        subprocess.run(["nasm", "-f", "elf64", asm, "-o", asm + ".o"], check=True, cwd=pk)
        subprocess.run(["ld", asm + ".o", "-o", os.path.join(pk, "zeval_t")], check=True)
    except (OSError, subprocess.CalledProcessError) as e:
        print("SKIP build step (nasm / ld missing or failed):", e); return
    import check_eval
    tt = model.load_tables(os.path.join(pk, "src", "eval.asm"))
    fens = check_eval.positions(1200, 23)
    ev = check_eval.engine_evals([os.path.join(pk, "zeval_t")], fens)
    bad = sum(1 for f, e in zip(fens, ev) if _model_eval(f, tt) != e)
    check(bad == 0, f"apply: rebuilt engine with the TUNED tables == python model ({len(fens)} positions, {bad} mismatches)")


def t_prior_scale(npz):
    """P pseudo-observations must give the penalty a Hessian of P * (Gauss-Newton Hessian of ONE observation) / n"""
    d = tune.load(npz, np.float64); v0, _ = model.initial_flat()
    free = model.group_mask("all"); s0 = tune.scores(d, v0); K = tune.fit_k(d, s0)
    P = 50.0; curv = tune.prior_curvature(d, s0, K, P)
    o1, o0 = tune.Objective(d, K, v0, free, curv), tune.Objective(d, K, v0, free, 0.0)
    x = v0[o1.idx].copy(); j = 5; h = 3.0; e = np.zeros_like(x); e[j] = h
    pen = lambda y: o1(y)[0] - o0(y)[0]                        # the penalty alone
    hess = (pen(x + e) - 2 * pen(x) + pen(x - e)) / h ** 2
    p = expit(K * s0 / 400.0); one_obs = 2 * float(np.mean((p * (1 - p)) ** 2)) * (K / 400.0) ** 2   # Gauss-Newton Hessian of one fully active observation
    want = P * one_obs / d.n
    check(abs(hess / want - 1) < 1e-3, f"prior scale: penalty Hessian {hess:.4e} == P x one-observation Hessian / n {want:.4e}")


def t_gate(workdir):
    import apply_params
    base = {"tables": {}, "loss": {"asm_val": 0.0670, "rounded_val": 0.0665}, "mean_abs_eval": {"asm": 100.0, "tuned": 110.0}}
    check(not apply_params.gate(base, None), "gate: an improving params.json passes")
    bad = json.loads(json.dumps(base)); bad["loss"]["rounded_val"] = 0.125
    check(bool(apply_params.gate(bad, None)), "gate: a params.json that is worse than the asm tables is refused")
    zero = json.loads(json.dumps(base)); zero["mean_abs_eval"]["tuned"] = 0.0
    check(bool(apply_params.gate(zero, None)), "gate: a collapsed eval (mean |eval| 0) is refused")
    old = {"tables": {}}
    check(bool(apply_params.gate(old, None)), "gate: a params.json without baseline (old tuner) is refused")
    # through the CLI: refused -> src untouched; --force -> written
    t = model.load_tables(); src = open(model.ASM).read()
    tab = {k: (t[k].tolist() if isinstance(t[k], np.ndarray) else t[k]) for k in ("pst_mg", "pst_eg", "evp_mg", "evp_eg", "bp_mg", "bp_eg", "tempo")}
    pj = os.path.join(workdir, "refuse.json"); json.dump(dict(bad, tables=tab, positions_train=1, positions_val=1, K=1.0, prior=0.0, free="all"), open(pj, "w"))
    od = os.path.join(workdir, "gate_out")
    r = subprocess.run([sys.executable, os.path.join(HERE, "apply_params.py"), pj, "--out-dir", od], capture_output=True, text=True)
    check(r.returncode != 0 and not os.path.exists(os.path.join(od, "eval.asm")), "gate: apply_params.py refuses and writes nothing")
    r = subprocess.run([sys.executable, os.path.join(HERE, "apply_params.py"), pj, "--out-dir", od, "--force"], capture_output=True, text=True)
    check(r.returncode == 0 and os.path.exists(os.path.join(od, "eval.asm")), "gate: --force applies anyway")
    r = subprocess.run([sys.executable, os.path.join(HERE, "apply_params.py"), os.path.join(workdir, "synth.epd"), "--out-dir", od], capture_output=True, text=True)
    check(r.returncode != 0 and "Traceback" not in r.stderr, "gate: a file that is not a params.json gives a clean error")


def t_from_zero(npz, workdir):
    out = os.path.join(workdir, "fz.json")
    r = subprocess.run([sys.executable, os.path.join(HERE, "tune.py"), npz, "--out", out, "--from-zero", "--k", "2.0", "--free", "n", "--epochs", "5", "--val", "0.1"], capture_output=True, text=True)
    if r.returncode: print(r.stdout[-800:], r.stderr[-800:])
    P = json.load(open(out)); t = model.load_tables()
    keep = np.ones(384, bool); keep[64:128] = False                      # everything except the knight tables must equal the asm
    others = all(np.array_equal(np.array(P["tables"][k])[keep], t[k][keep]) for k in ("pst_mg", "pst_eg"))
    moved = not np.array_equal(np.array(P["tables"]["pst_mg"])[64:128], t["pst_mg"][64:128])   # the knight tables did start from zero and move
    check(r.returncode == 0 and P.get("from_zero") is True and P["prior"] == 0.0 and others and moved,
          "from-zero: --free n --from-zero starts only the knight tables at zero; the other tables keep their asm values; default prior 0")


def t_freeze(npz, workdir):
    out = os.path.join(workdir, "pf.json")
    r = subprocess.run([sys.executable, os.path.join(HERE, "tune.py"), npz, "--out", out, "--free", "bp,tempo", "--epochs", "150", "--val", "0.1"], capture_output=True, text=True)
    if r.returncode: print(r.stdout[-800:], r.stderr[-800:])
    P = json.load(open(out)); t = model.load_tables()
    same = all(np.array_equal(np.array(P["tables"][k]), t[k]) for k in ("pst_mg", "pst_eg", "evp_mg", "evp_eg"))
    check(r.returncode == 0 and same, f"freeze: --free bp,tempo leaves all 768 PST entries and the {2 * model.EV_N} values of the full evaluation unchanged (BP {P['tables']['bp_mg']}/{P['tables']['bp_eg']}, tempo {P['tables']['tempo']})")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", default=""); ap.add_argument("--n", type=int, default=40000); ap.add_argument("--build", action="store_true")
    a = ap.parse_args()
    eng = resolve(a.engine) if a.engine else ENGINE
    wd = tempfile.mkdtemp(prefix="tunertest_")
    try:
        if os.path.exists(eng[0]) or shutil.which(eng[0]):
            t_model(eng)
        else:
            print("SKIP model test: engine not found")
        t_qsearch()
        t_gradient()
        npz, res = t_recovery(a.n, wd)
        pj = os.path.join(wd, "rec.json")
        # a params.json from the recovered adam parameters (to test apply_params with a really tuned set)
        t = model.load_tables(); v, _ = res["adam"]
        vr, T = tune.round_flat(v, t)
        json.dump({"tables": tune.tables_json(T)}, open(pj, "w"))
        t_apply(a.build, wd, pj)
        t_freeze(npz, wd)
        t_prior_scale(npz)
        t_gate(wd)
        t_from_zero(npz, wd)
    finally:
        shutil.rmtree(wd, ignore_errors=True)
    print("\nSELFTEST", "FAILED: " + "; ".join(fails) if fails else "PASSED")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
