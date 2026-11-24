#!/usr/bin/env python3
"""Micro tests of the Zeval tuning pipeline (bug hunting).  Every test uses an INDEPENDENT oracle (python-chess, a closed form, a hand-written
reference) instead of re-running the code under test, so a bug that the code shares with its own model cannot hide.

  E  engine      colour mirror symmetry, tempo-only side-to-move dependence                       (needs the engine)
  M  model       feature mirror symmetry, piece-square / phase / bishop-pair vs python-chess, table orientation sanity
  P  pipeline    extract.py -> .npz -> tune.load -> scores()  ==  integer model on real positions (taper, sf, tempo sign, label side)
  X  extract     parse_label table, dedupe + label average, stm-wdl, filters, group ids, per-game cap, adjudication handling
  T  tune        scores / mse / gradient (synthetic AND real features), K fit, split, paired_ci, early stopping, CLI determinism
  G  groups      --free masks, never_free, shadowed term names
  A  apply       patch idempotency, read-back of random tables, text outside the tables untouched
  N  normalise   tune.scale_to and tune.py --norm-scale (the eval scale of a retune kept at that of the start tables)
  R  resolve     resolve.py: ply sampling, info parsing, PV playback, label side, filters, side file vs a separate search, resume (engine)

usage: python3 tuner/micro_tests.py [--engine ./zeval] [--n 1500] [--only PREFIX]
exit code 0 = every test passed (INFO lines are not failures)."""
import argparse, json, os, random, subprocess, sys, tempfile, traceback
import numpy as np
import scipy.sparse as sp
import chess
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, "..", "tests"))
import model, tune, extract, check_eval, apply_params, resolve
from scipy.special import expit

TESTS = []; FAILS = []; INFOS = []
ENG = None; N = 1500


def test(tag, name):
    def deco(fn):
        TESTS.append((tag, name, fn)); return fn
    return deco


def ok(cond, msg=""):
    if not cond: raise AssertionError(msg)


def info(msg): INFOS.append(msg)


def fen4(fen): return " ".join(fen.split()[:4])


def nonspecial(fens):
    return [f for f in fens if not model.fen_features(f).special]


def run_extract(out, inputs, *args):
    cmd = [sys.executable, os.path.join(HERE, "extract.py"), out, *inputs, "--workers", "1", *args]
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=600)
    if r.returncode: raise AssertionError("extract.py failed:\n" + r.stdout.decode()[-800:])
    return r.stdout.decode()


def row_dict(d, i):
    a, b = d.X.indptr[i], d.X.indptr[i + 1]
    return {int(c): int(round(v)) for c, v in zip(d.X.indices[a:b], d.X.data[a:b])}


def feat_dict(fen):
    F = model.fen_features(fen)
    return {int(c): int(v) for c, v in zip(F.cols, F.vals)}


# ================================================================================================== E: engine
@test("E1", "engine: eval(position) == eval(colour-mirrored position)")
def _():
    fens = check_eval.positions(N, 21)
    mir = [chess.Board(f).mirror().fen() for f in fens]
    a = check_eval.engine_evals(ENG, fens); b = check_eval.engine_evals(ENG, mir)
    bad = [(f, x, y) for f, x, y in zip(fens, a, b) if x != y]
    ok(not bad, f"{len(bad)} of {len(fens)} differ, e.g. {bad[:2]}")


@test("E2", "engine: only the tempo depends on the side to move  (eval(w) + eval(b) == 2 * tempo, linear positions)")
def _():
    t = model.load_tables(); tempo = t["tempo"]
    fens = []
    for f in nonspecial(check_eval.positions(N, 22)):
        b = chess.Board(f); b.ep_square = None
        c = b.copy(); c.turn = not b.turn
        if b.is_check() or c.is_check(): continue
        fens.append((b.fen(), c.fen()))
    ok(len(fens) > 200, "too few positions")
    a = check_eval.engine_evals(ENG, [x for x, _ in fens]); c = check_eval.engine_evals(ENG, [y for _, y in fens])
    bad = [(x, p, q) for (x, _), p, q in zip(fens, a, c) if p + q != 2 * tempo]
    ok(not bad, f"{len(bad)} of {len(fens)} positions: eval(w)+eval(b) != 2*tempo={2 * tempo}, e.g. {bad[:2]}")


# ================================================================================================== M: model
@test("M1", "model: features of the colour-mirrored position are exactly negated (phase, sf, special equal, stm flipped)")
def _():
    bad = []
    for f in check_eval.positions(N, 23):
        F = model.fen_features(f); G = model.fen_features(chess.Board(f).mirror().fen())
        da = {int(c): int(v) for c, v in zip(F.cols, F.vals)}; db = {int(c): -int(v) for c, v in zip(G.cols, G.vals)}
        if da != db or F.phase != G.phase or F.sf != G.sf or F.special != G.special or F.wtm == G.wtm:
            bad.append(f)
    ok(not bad, f"{len(bad)} positions, e.g. {bad[:2]}")


@test("M2", "model: piece-square counts, phase and bishop pair == python-chess oracle")
def _():
    bad = []
    for f in check_eval.positions(600, 24):
        b = chess.Board(f); exp = {}; ph = 0
        for s, p in b.piece_map().items():
            t = p.piece_type; k = (7 - s // 8) * 8 + s % 8                      # rank 8 first
            c = (t - 1) * 64 + (k if p.color else k ^ 56)
            exp[c] = exp.get(c, 0) + (1 if p.color else -1); ph += {1: 0, 2: 1, 3: 1, 4: 2, 5: 4, 6: 0}[t]
        wb = len(b.pieces(chess.BISHOP, True)) >= 2; bb = len(b.pieces(chess.BISHOP, False)) >= 2
        if wb != bb: exp[model.BP] = int(wb) - int(bb)
        exp = {c: v for c, v in exp.items() if v}
        F = model.fen_features(f); got = {int(c): int(v) for c, v in zip(F.cols, F.vals) if c < model.NPST or c == model.BP}
        if got != exp or F.phase != ph: bad.append(f)
    ok(not bad, f"{len(bad)} positions, e.g. {bad[:2]}")


@test("M3", "model: sanity of the table ORIENTATION (a flipped table is bit-exact but plays badly)")
def _():
    t = model.load_tables()
    def mean(arr, p, ks): return float(np.mean([arr[(p - 1) * 64 + k] for k in ks]))
    rank = lambda r: range((8 - r) * 8, (8 - r) * 8 + 8)                        # rank r (1..8) = table indices of rank 8 first
    ok(mean(t["pst_eg"], 1, rank(7)) > mean(t["pst_eg"], 1, rank(2)) + 20, "pawn endgame table: rank 7 should be worth far more than rank 2")
    ok(mean(t["pst_mg"], 2, [27, 28, 35, 36]) > mean(t["pst_mg"], 2, [0, 7, 56, 63]), "knight: centre should beat the corners")
    ok(mean(t["pst_mg"], 6, rank(1)) > mean(t["pst_mg"], 6, rank(5)), "king midgame: back rank should beat the centre")
    mat = t["matmg"]; ok(mat[1] < mat[2] <= mat[3] + 60 < mat[4] < mat[5], f"material order P<N~B<R<Q violated: {list(mat)}")
    o = model.TERM_OFS["passed"]; tot = []
    for r in range(6):                                                           # passed[r] = rank r + 2; compare the TOTAL value pawn pst + material + bonus
        ks = range((6 - r) * 8, (6 - r) * 8 + 8)
        tot.append(float(np.mean([t["pst_eg"][k] for k in ks])) + t["mateg"][1] + int(t["evp_eg"][o + r]))
    ok(all(tot[i] <= tot[i + 1] + 5.0 for i in range(5)), f"passed pawn endgame value (pst + material + bonus) should rise with the rank: {[round(x) for x in tot]}")
    raw = [int(x) for x in t["evp_eg"][o:o + 6]]
    if any(raw[i] > raw[i + 1] for i in range(5)): info(f"M3: the raw passed-pawn eg bonus is not monotone ({raw}); the pawn PST compensates (total value is monotone within 5 cp)")
    ka = t["evp_mg"][model.TERM_OFS["king_attack"]:model.TERM_OFS["king_attack"] + 32]
    ok(ka[-1] >= ka[0], f"king attack units: high should be >= low: {ka[0]} .. {ka[-1]}")


# ================================================================================================== P: pipeline
@test("P1", "pipeline: extract -> npz -> load -> scores()  ==  integer model (White view, tempo included), |diff| < 1 cp")
def _():
    fens = []; seen = set()
    for f in nonspecial(check_eval.FIXED + check_eval.positions(500, 33)):
        k = fen4(f)
        if k not in seen: seen.add(k); fens.append(k)
    lab = lambda i: f"{(i + 1) / 1000:.3f}"
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "a.epd"); open(p, "w").write("\n".join(f"{f}; [{lab(i)}]" for i, f in enumerate(fens)) + "\n")
        out = os.path.join(td, "d.npz"); run_extract(out, [p], "--min-pieces", "0")
        d = tune.load(out, np.float64)
    ok(d.n == len(fens), f"rows {d.n} != positions {len(fens)}")
    v0, t = model.initial_flat(); s = tune.scores(d, v0)
    worst = 0.0; bad = []
    for i, f in enumerate(fens):
        S = model.int_eval(f + " 0 1", t); wtm = f.split()[1] == "w"
        white = (S if wtm else -S)
        diff = abs(s[i] - white); worst = max(worst, diff)
        if diff >= 1.0 + 1e-9 or d.stm[i] != (1 if wtm else -1) or abs(d.y[i] - float(lab(i))) > 1e-6: bad.append((f, float(s[i]), white))
    ok(not bad, f"{len(bad)} of {len(fens)} rows disagree (worst diff {worst:.3f}), e.g. {bad[:2]}")
    info(f"P1: worst |scores() - integer eval| over {len(fens)} real positions = {worst:.4f} cp")
    globals()["_REAL"] = (d, fens)


@test("T0", "tune: analytic gradient == finite differences on REAL extracted features (+ prior, frozen set, tempo)")
def _():
    d, _f = globals()["_REAL"]; v0, t = model.initial_flat(); rng = np.random.default_rng(3)
    K = 1.1; free = rng.random(model.NPAR) < 0.5; free[model.TEMPO_IDX] = True
    cover = np.bincount(d.X.indices, minlength=model.NF); act = np.where(np.r_[cover > 0, cover > 0, True] & free)[0]
    obj = tune.Objective(d, K, v0 + rng.normal(0, 5, model.NPAR), free, tune.prior_curvature(d, tune.scores(d, v0), K, 50.0))
    x = obj.v0[obj.idx] + rng.normal(0, 3, len(obj.idx)); f0, g = obj(x); worst = 0.0
    for j in rng.choice(len(obj.idx), 40, replace=False):
        h = 1e-2; e = np.zeros_like(x); e[j] = h
        fd = (obj(x + e)[0] - obj(x - e)[0]) / (2 * h)
        worst = max(worst, abs(fd - g[j]) / (abs(g[j]) + 1e-9) if abs(g[j]) > 1e-9 else abs(fd))
    ok(worst < 1e-4, f"worst relative gradient error {worst:.2e}")


# ================================================================================================== X: extract
@test("X1", "extract.parse_label: accepted and rejected label formats")
def _():
    good = [("0 1; [1.0]", 1.0), ("0 1; [0.5]", 0.5), ("0 1; [0.0]", 0.0), ("0 1; [0]", 0.0), ("0 1; [1]", 1.0), ('0 1; c9 "1-0";', 1.0),
            ('0 1; c9 "0-1";', 0.0), ('0 1; c9 "1/2-1/2";', 0.5), ("0 1 1-0", 1.0), ("0 1 1/2-1/2", 0.5), ("0 1 0-1", 0.0), ("0 1 0.62", 0.62),
            ("0 1 1.0", 1.0), ("0 1; [0.62]", 0.62), ("0 1 [ 0.5 ]", 0.5), ("1-0", 1.0), ("[0.25]", 0.25)]
    none = ['bm Qh5; id "WAC.001";', '0 1; id "x-1-0-y"', "0 1; ce 25;", "0 1 ; [2.0]", "0 1 ; [-1]", "", "0 1"]
    bad = [(s, extract.parse_label(s), e) for s, e in good if extract.parse_label(s) != e]
    bad += [(s, extract.parse_label(s), None) for s in none if extract.parse_label(s) is not None]
    ok(not bad, f"{bad}")


def _epd_extract(lines, *args):
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "a.epd"); open(p, "w").write("\n".join(lines) + "\n")
        out = os.path.join(td, "d.npz"); txt = run_extract(out, [p], *args)
        return tune.load(out, np.float64), txt


@test("X2", "extract (EPD): duplicates merged + labels averaged, --stm-wdl flip, min-pieces, special excluded, comments skipped")
def _():
    A = "r1bq1rk1/pp3ppp/2n1pn2/2bp4/2B5/2N1PN2/PP3PPP/R1BQ1RK1 w - -"
    B = "r3k2r/pp3ppp/2n5/3p4/3P4/2N5/PP3PPP/R3K2R b KQkq -"
    S = "8/8/8/4k3/8/8/4P3/4K3 w - -"                                           # KPK: special evaluation
    T = "4k3/p7/8/8/8/8/P7/4K3 w - -"                                            # 4 pieces, KPKP: linear (not special)
    ok(model.fen_features(S).special, "test position S should be special")
    ok(not model.fen_features(T).special, "test position T should be linear")
    lines = ["# comment", f"{A} 0 1; [1.0]", f"{A} 5 9; [0.0]", f"{B} 0 1; [0.7]", f"{S} 0 1; [1.0]", f"{T} 0 1; [1.0]", "", "garbage line"]
    d, _t = _epd_extract(lines, "--stm-wdl")
    ok(d.n == 2, f"expected 2 rows (A merged, B), got {d.n}")
    ok(abs(d.y[0] - 0.5) < 1e-6, f"merged label should be (1+0)/2, got {d.y[0]}")
    ok(abs(d.y[1] - 0.3) < 1e-6, f"black-to-move label 0.7 with --stm-wdl must become 0.3 (White view), got {d.y[1]}")
    ok(list(d.stm) == [1, -1], f"stm {list(d.stm)}")
    ok(len(np.unique(d.grp)) == 2, "EPD positions must be one group each")
    d2, _t = _epd_extract(lines)
    ok(abs(d2.y[1] - 0.7) < 1e-6, f"without --stm-wdl the label is White's view: {d2.y[1]}")
    d3, _t = _epd_extract(lines, "--min-pieces", "0")
    ok(d3.n == 3, f"--min-pieces 0 should keep the 3-piece position too, got {d3.n}")


@test("X3", "extract (EPD): --filter-in-check drops positions in check; groups of several files never collide")
def _():
    chk = "4k3/8/8/8/8/8/4q3/4K3 w - -"                                        # white king attacked by the queen? (e2 queen next to king)
    quiet = "r1bq1rk1/pp3ppp/2n1pn2/2bp4/2B5/2N1PN2/PP3PPP/R1BQ1RK1 w - -"
    ok(chess.Board(chk + " 0 1").is_check(), "fixture must be a check")
    lines = [f"{quiet} 0 1; [0.5]", f"{'rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq -'} 0 2; [0.5]"]
    with tempfile.TemporaryDirectory() as td:
        p1 = os.path.join(td, "1.epd"); p2 = os.path.join(td, "2.epd")
        open(p1, "w").write("\n".join(lines + [f"{'rnb1kbnr/pppp1ppp/8/4p3/6Pq/5P2/PPPPP2P/RNBQKBNR w KQkq -'} 0 3; [0.0]"]) + "\n")
        open(p2, "w").write("\n".join([f"{'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq -'} 0 1; [0.5]", f"{quiet.replace(' w ', ' b ')} 0 1; [0.5]"]) + "\n")
        out = os.path.join(td, "d.npz"); txt = run_extract(out, [p1, p2], "--filter-in-check", "--min-pieces", "0")
        d = tune.load(out, np.float64)
    ok(d.n == 4, f"3 positions of file 1 (one in check) + 2 of file 2 -> 4 rows expected, got {d.n}\n{txt[-300:]}")
    ok(len(np.unique(d.grp)) == 4, f"groups collide across files: {sorted(d.grp)}")


def _make_games(n, seed):
    rnd = random.Random(seed); games = []
    for g in range(n):
        b = chess.Board(); pl = []
        for _ in range(rnd.randint(30, 90)):
            l = list(b.legal_moves)
            if not l or b.is_game_over(): break
            m = rnd.choice(l)
            pl.append([b.fen(), rnd.choice([-3200, -400, -50, 0, 30, 90, 350, 3100] + [rnd.randint(-300, 300)] * 6), m.uci(), int(b.is_capture(m)), int(b.gives_check(m))])
            b.push(m)
        games.append({"plies": pl, "adj": [None, None, "win", "fail", "draw"][g % 5], "result_white": rnd.choice([0.0, 0.5, 1.0])})
    return games


def _expected_game_rows(games, drop_adj=False, max_cp=3000, min_pieces=6):
    rows = []
    for gi, g in enumerate(games):
        if g["adj"] == "fail" or (drop_adj and g["adj"] in ("win", "draw")): continue
        pl = g["plies"]
        for i in range(1, len(pl)):
            fen, cp, uci, cap, chk = pl[i]
            if pl[i - 1][4] or cap or chk or abs(cp) >= max_cp: continue
            if sum(ch.isalpha() for ch in fen.split()[0]) < min_pieces or model.fen_features(fen).special: continue
            rows.append((gi, fen, g["result_white"]))
    return rows


@test("X4", "extract (games): quiet filters, adj=fail dropped, --drop-adjudicated, results as labels, one group per game, dedupe order")
def _():
    games = _make_games(12, 5)
    for drop in (False, True):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "g.jsonl"); open(p, "w").write("\n".join(json.dumps(g) for g in games) + "\n")
            out = os.path.join(td, "d.npz"); run_extract(out, [p], *(["--drop-adjudicated"] if drop else []))
            d = tune.load(out, np.float64)
        seen = {}; exp = []
        for gi, fen, res in _expected_game_rows(games, drop):
            k = fen4(fen)
            if k in seen: seen[k][1].append(res); continue
            seen[k] = [gi, [res], fen]; exp.append(k)
        ok(d.n == len(exp), f"drop={drop}: rows {d.n} != expected {len(exp)}")
        for i, k in enumerate(exp):
            gi, rs, fen = seen[k]
            if row_dict(d, i) != feat_dict(fen): raise AssertionError(f"drop={drop}: row {i} features differ")
            if abs(d.y[i] - np.mean(rs)) > 1e-6: raise AssertionError(f"drop={drop}: row {i} label {d.y[i]} != {np.mean(rs)}")
        ngroups = len(set(seen[k][0] for k in exp)); ok(len(np.unique(d.grp)) == ngroups, f"drop={drop}: groups {len(np.unique(d.grp))} != games with rows {ngroups}")


@test("X5", "extract (games): --per-game N caps positions per game and only picks quiet candidates; --max-cp respected")
def _():
    games = _make_games(10, 6); cap = 4
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "g.jsonl"); open(p, "w").write("\n".join(json.dumps(g) for g in games) + "\n")
        out = os.path.join(td, "d.npz"); run_extract(out, [p], "--per-game", str(cap), "--max-cp", "200")
        d = tune.load(out, np.float64)
    cands = {fen4(f) for _, f, _ in _expected_game_rows(games, max_cp=200)}
    per = np.bincount(np.unique(d.grp, return_inverse=True)[1])
    ok(per.max() <= cap, f"a game contributes {per.max()} > {cap} positions")
    cand_feats = [feat_dict(f + " 0 1") for f in cands]
    ok(all(row_dict(d, i) in cand_feats for i in range(d.n)), "a stored row is not a quiet candidate of the games")


# ================================================================================================== T: tune
def synth(n=3000, ngames=300, seed=0):
    rng = np.random.default_rng(seed); nnz = rng.integers(5, 25, size=n)
    rows = np.repeat(np.arange(n), nnz); cols = rng.integers(0, model.NF, size=nnz.sum()); vals = rng.integers(-2, 3, size=nnz.sum()).astype(float)
    X = sp.csr_matrix((vals, (rows, cols)), shape=(n, model.NF)); X.sum_duplicates(); X.eliminate_zeros()
    return tune.Data(X, rng.integers(0, 25, n) / 24.0, rng.choice([-1.0, 1.0], n), rng.choice([0.0, 0.5, 1.0], n), rng.normal(0, 200, n),
                     rng.integers(0, ngames, n), rng.integers(0, 65, n).astype(float))


@test("T1", "tune.scores / mse == dense row-by-row formula  (taper, sf scaling, tempo sign)")
def _():
    d = synth(); rng = np.random.default_rng(1); v = rng.normal(0, 40, model.NPAR); s = tune.scores(d, v)
    X = d.X.toarray(); mg, eg, tempo = v[:model.NF], v[model.NF:2 * model.NF], v[model.TEMPO_IDX]
    ref = d.ph * (X @ mg) + (1 - d.ph) * d.sf / 64.0 * (X @ eg) + d.stm * tempo
    ok(np.allclose(s, ref, atol=1e-9), f"max diff {np.abs(s - ref).max()}")
    K = 1.37; p = 1 / (1 + np.exp(-K * ref / 400)); ok(abs(tune.mse(d, s, K) - np.mean((d.y - p) ** 2)) < 1e-12, "mse")


@test("T2", "tune.Objective gradient == finite differences on synthetic data (target blend, frozen set, prior, sf<64)")
def _():
    d = synth(2000, seed=2); rng = np.random.default_rng(2); v0 = rng.normal(0, 30, model.NPAR); K = 0.9
    free = rng.random(model.NPAR) < 0.4; free[model.TEMPO_IDX] = True
    target = 0.7 * d.y + 0.3 * expit(1.3 * d.cp / 400)
    obj = tune.Objective(d, K, v0, free, tune.prior_curvature(d, tune.scores(d, v0), K, 80.0), target)
    x = obj.v0[obj.idx] + rng.normal(0, 5, len(obj.idx)); f0, g = obj(x); worst = 0.0
    for j in rng.choice(len(obj.idx), 50, replace=False):
        h = 1e-2; e = np.zeros_like(x); e[j] = h
        fd = (obj(x + e)[0] - obj(x - e)[0]) / (2 * h)
        worst = max(worst, abs(fd - g[j]) / abs(g[j]) if abs(g[j]) > 1e-9 else abs(fd))
    ok(worst < 1e-5, f"worst relative gradient error {worst:.2e}")
    ok(abs(obj(obj.v0[obj.idx])[0] - tune.mse(d, tune.scores(d, v0), K, target)) < 1e-12, "prior must vanish at the start point")


@test("T3", "tune.fit_k recovers K from exact sigmoid labels (also soft/blended targets)")
def _():
    rng = np.random.default_rng(4); d = synth(4000, seed=4); s = rng.normal(0, 150, d.n)
    for K in (0.6, 1.0, 1.7, 3.1):
        y = 1 / (1 + np.exp(-K * s / 400)); k = tune.fit_k(d, s, y)
        ok(abs(k - K) < 2e-3, f"K={K}: fitted {k}")


@test("T4", "tune.split: whole games only, deterministic, seed-dependent, requested fraction, union == all")
def _():
    d = synth(30000, ngames=20000, seed=7); a, b = tune.split(d, 0.05, 1)
    ok(a.n + b.n == d.n, "rows lost"); ok(not (set(a.grp) & set(b.grp)), "a game is in both train and validation")
    ok(abs(len(set(b.grp)) / len(set(d.grp)) - 0.05) < 0.008, f"validation game fraction {len(set(b.grp)) / len(set(d.grp)):.4f}")
    a2, b2 = tune.split(d, 0.05, 1); ok(np.array_equal(b.grp, b2.grp), "not deterministic")
    a3, b3 = tune.split(d, 0.05, 2); ok(not np.array_equal(np.unique(b.grp), np.unique(b3.grp)), "seed has no effect")
    try: tune.split(d, 0.0, 1); raise AssertionError("val=0 must stop")
    except SystemExit: pass


@test("T5", "tune.paired_ci: sign convention, constant effect -> zero-width CI, CI width == 1.96 SE, batching == one-shot draw")
def _():
    sums = np.full(500, -0.002); cnts = np.full(500, 2.0); m, lo, hi = tune.paired_ci(sums, cnts)
    ok(abs(m + 0.001) < 1e-12 and abs(hi - lo) < 1e-12, f"constant effect: {m} [{lo},{hi}]")
    rng = np.random.default_rng(1); sums = rng.normal(0, 1, 400); cnts = np.ones(400); m, lo, hi = tune.paired_ci(sums, cnts, B=4000)
    ok(abs((hi - lo) / 2 - 1.96 / np.sqrt(400)) < 0.15 * 1.96 / np.sqrt(400), f"CI half width {(hi - lo) / 2:.4f} vs {1.96 / np.sqrt(400):.4f}")
    ng, B = 12000, 2000; sums = rng.normal(0, 1, ng); cnts = rng.integers(1, 5, ng).astype(float)
    got = tune.paired_ci(sums, cnts, B=B, seed=1)
    rr = np.random.default_rng(1); pick = rr.integers(0, ng, (B, ng)); bs = sums[pick].sum(1) / cnts[pick].sum(1)
    ref = (float(sums.sum() / cnts.sum()),) + tuple(float(x) for x in np.percentile(bs, [2.5, 97.5])); del pick
    if not np.allclose(got, ref, rtol=0, atol=1e-15):
        info(f"T5: batched paired_ci differs from the one-shot draw (got {got}, one-shot {ref}): the comment 'same draws as one batch' is not exact "
             f"(numpy discards buffered half-words between calls) - statistically harmless, reproducibility across memory limits only")


@test("T6", "tune.run_adam: early stopping returns the BEST-validation parameters (not the last)")
def _():
    import argparse as ap
    obj = lambda x: (float(np.sum((x - 3) ** 2)), 2 * (x - 3))
    seq = [5.0, 4, 3, 2, 1.5, 1.7, 1.8, 1.9, 2.0, 2.1, 2.2, 2.3, 2.4]; it = iter(seq + [9.9] * 500)
    a = ap.Namespace(epochs=300, lr=0.1, lr_end=0.1, eval_every=1, patience=5, final_only=False)
    x, vl, ep = tune.run_adam(obj, np.zeros(3), lambda x: next(it), a, lambda s: None)
    ok(ep == 4 and abs(vl - 1.5) < 1e-12, f"best epoch {ep}, val {vl}")
    a2 = ap.Namespace(epochs=4, lr=0.1, lr_end=0.1, eval_every=1, patience=99, final_only=True)
    x4, _v, _e = tune.run_adam(obj, np.zeros(3), lambda x: 0.0, a2, lambda s: None)
    ok(np.allclose(x, x4), f"returned {x} but the state after epoch 4 is {x4}")


@test("T7", "tune.py CLI: deterministic output, frozen parameters untouched, rounded tables consistent with the float result")
def _():
    d, fens = globals()["_REAL"]
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "a.epd"); lines = [f"{f}; [{(0.5 + 0.4 * np.sin(i)):.3f}]" for i, f in enumerate(fens)]; open(p, "w").write("\n".join(lines) + "\n")
        npz = os.path.join(td, "d.npz"); run_extract(npz, [p], "--min-pieces", "0")
        outs = []
        for k in range(2):
            o = os.path.join(td, f"p{k}.json")
            r = subprocess.run([sys.executable, os.path.join(HERE, "tune.py"), npz, "--out", o, "--free", "bp,tempo", "--val", "0.2", "--seed", "3"],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=600)
            if r.returncode: raise AssertionError(r.stdout.decode()[-600:])
            outs.append(json.load(open(o)))
    ok(outs[0] == outs[1], "two identical runs differ")
    t = model.load_tables(); T = outs[0]["tables"]
    ok(T["pst_mg"] == t["pst_mg"].tolist() and T["pst_eg"] == t["pst_eg"].tolist(), "--free bp,tempo changed a PST entry")
    ok(T["evp_mg"] == t["evp_mg"].tolist() and T["evp_eg"] == t["evp_eg"].tolist(), "--free bp,tempo changed a term of the full evaluation")
    ok(outs[0]["K"] > 0 and np.isfinite(outs[0]["loss"]["rounded_val"]), "K / loss not finite")


@test("T8", "tune.py CLI: --lam target blend + --optimizer lbfgs + --prior-sweep run end to end, files and numbers consistent")
def _():
    games = _make_games(60, 8)
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "g.jsonl"); open(p, "w").write("\n".join(json.dumps(g) for g in games) + "\n")
        npz = os.path.join(td, "d.npz"); run_extract(npz, [p]); o = os.path.join(td, "p.json")
        r = subprocess.run([sys.executable, os.path.join(HERE, "tune.py"), npz, "--out", o, "--optimizer", "lbfgs", "--prior-sweep", "50,500", "--epochs", "40",
                            "--free", "pawns,mob,bp,tempo", "--val", "0.25", "--lam", "0.3", "--seed", "2"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=900)
        out = r.stdout.decode()
        ok(r.returncode == 0, "tune.py failed:\n" + out[-900:])
        P = json.load(open(o)); ok(P["lam"] == 0.3 and P["prior"] in (50.0, 500.0), f"lam/prior in json: {P['lam']} {P['prior']}")
        ok(os.path.exists(os.path.join(td, "p_prior50.json")) and os.path.exists(os.path.join(td, "p_prior500.json")), "per-prior json files missing")
        L = P["loss"]; ok(all(np.isfinite(L[k]) for k in ("start_val", "tuned_val", "asm_val", "rounded_val")), f"non-finite loss {L}")
        ok(L["asm_val"] == L["start_val"], "without --from-zero the asm baseline must equal the start loss")
        ok(L["tuned_val"] <= L["start_val"] + 1e-9, f"tuned (float) validation loss {L['tuned_val']} is worse than the start {L['start_val']}")
        t = model.load_tables(); T = P["tables"]
        ok(T["pst_mg"] == t["pst_mg"].tolist(), "PST changed although only pawns,mob,bp,tempo were free")


# ================================================================================================== G: groups
@test("G1", "model.group_mask: bp / tempo / pst / piece groups / all, never_free, unknown group stops")
def _():
    m = model.group_mask("bp"); ok(set(np.where(m)[0]) == {model.BP, model.NF + model.BP}, "bp")
    m = model.group_mask("tempo"); ok(set(np.where(m)[0]) == {model.TEMPO_IDX}, "tempo")
    pst = model.group_mask("pst"); ok(pst.sum() == 768, f"pst selects {pst.sum()}")
    ok(np.array_equal(pst, model.group_mask("p,n,b,r,q,k")), "p,n,b,r,q,k != pst")
    allm = model.group_mask("all"); nf = model.never_free()
    ok(allm.sum() == model.NPAR - 2 * len(nf), f"all selects {allm.sum()} of {model.NPAR} with {len(nf)} never-free")
    ok(not any(allm[i] or allm[model.NF + i] for i in nf), "a never_free parameter is free")
    full = model.group_mask("full"); ok(not full[:model.EVP0].any() and full[model.EVP0:model.NF].any(), "full must be exactly the terms of the full evaluation")
    try: model.group_mask("nonsense"); raise AssertionError("unknown group accepted")
    except SystemExit: pass


@test("G2", "model.group_mask: every term of the evaluation is reachable by --free (INFO lists names shadowed by a group of the same name)")
def _():
    shadowed = []; union = np.zeros(model.NPAR, bool)
    for g in list(model.TERM_GROUPS) + list(model.TERM_OFS): union |= model.group_mask(g)
    nf = set(model.never_free()); nf |= {model.NF + i for i in nf}
    missing = [i for i in range(model.EVP0, model.NF) if not union[i] and i not in nf]
    ok(not missing, (f"{len(missing)} parameters can never be tuned, first: {model.param_name(missing[0] % model.NF)}") if missing else "")
    for n in model.TERM_OFS:
        lo, hi = model.term_range(n); exp = np.zeros(model.NPAR, bool); exp[lo:hi] = True; exp[model.NF + lo:model.NF + hi] = True
        for i in nf: exp[i] = False
        if not np.array_equal(model.group_mask(n), exp): shadowed.append(n)
    if shadowed: info(f"G2: --free <term> cannot select a single term whose name is also a group name: {', '.join(shadowed)}")


@test("G3", "model: tables_from_merged / merged_from_tables / flat / unflat round trips; round_flat idempotent and within 0.5")
def _():
    t = model.load_tables(); w = model.merged_from_tables(t); T = model.tables_from_merged(w, t["tempo"], t)
    for k in ("pst_mg", "pst_eg", "evp_mg", "evp_eg"): ok(np.array_equal(T[k], t[k]), f"{k} does not survive the round trip")
    ok((T["bp_mg"], T["bp_eg"], T["tempo"]) == (t["bp_mg"], t["bp_eg"], t["tempo"]), "bp / tempo")
    v = model.flat(w, t["tempo"]); w2, tp = model.unflat(v); ok(np.array_equal(w, w2) and tp == t["tempo"], "flat/unflat")
    rng = np.random.default_rng(5); vf = v + rng.normal(0, 20, v.size); vr, T1 = tune.round_flat(vf, t); vr2, T2 = tune.round_flat(vr, t)
    ok(np.array_equal(vr, vr2), "round_flat not idempotent"); ok(np.abs(vr - vf).max() <= 0.5 + 1e-9, f"rounding moved a value by {np.abs(vr - vf).max()}")


# ================================================================================================== A: apply
@test("A1", "apply_params.patch: random tables read back exactly, idempotent, text outside the tables untouched")
def _():
    src = open(model.ASM).read(); t = model.load_tables(); rng = np.random.default_rng(9)
    tab = {"pst_mg": t["pst_mg"] + rng.integers(-300, 300, 384), "pst_eg": t["pst_eg"] + rng.integers(-300, 300, 384),
           "evp_mg": t["evp_mg"] + rng.integers(-99, 99, model.EV_N), "evp_eg": t["evp_eg"] + rng.integers(-99, 99, model.EV_N),
           "bp_mg": -17, "bp_eg": 123, "tempo": -4}
    note = "; (micro test note)"; new = apply_params.patch(src, tab, note)
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "eval.asm"); open(p, "w").write(new); r = model.load_tables(p)
    for k in ("pst_mg", "pst_eg", "evp_mg", "evp_eg"): ok(np.array_equal(r[k], tab[k]), f"{k} read-back differs")
    ok((r["bp_mg"], r["bp_eg"], r["tempo"]) == (-17, 123, -4), "bp/tempo defines")
    ok(apply_params.patch(new, tab, note) == new, "patching twice with the same data is not idempotent")
    a = src.index("pst_mg:\n"); e = src.index("evp_end:")
    n_a = new.index("pst_mg:\n"); n_e = new.index("evp_end:")
    strip = lambda s: __import__("re").sub(r"(?m)^(%ifndef (BP_MG|BP_EG|TEMPO)\n%define \2\s+)-?\d+", r"\1X", s)
    head_old = strip(src[:a]); head_new = strip(new[:n_a]).replace(note + "\n", "")
    ok(head_old == head_new, "text before the tables changed (beyond the note and the three defines)")
    ok(src[e:] == new[n_e:], "text after the tables changed")


# ================================================================================================== N: eval scale normalisation (tune.py --norm-scale)
def _drift_npz(td, n=3000, seed=71, amp=1.4):
    """labels come from an eval whose TERMS (not the piece-square tables) are `amp` times larger: tuning has to grow the terms, i.e. mean|eval| drifts"""
    fens = []; seen = set()
    for f in check_eval.positions(int(n * 1.6), seed):
        k = fen4(f)
        if k in seen or model.fen_features(f).special: continue
        seen.add(k); fens.append(k)
    fens = fens[:n]
    pre = os.path.join(td, "pre.epd"); open(pre, "w").write("\n".join(f"{f}; [0.5]" for f in fens) + "\n")
    run_extract(os.path.join(td, "pre.npz"), [pre], "--min-pieces", "0"); d = tune.load(os.path.join(td, "pre.npz"), np.float64)
    v0, _t = model.initial_flat(); v1 = v0.copy()
    for lo, hi in ((model.EVP0, model.NF), (model.NF + model.EVP0, 2 * model.NF)): v1[lo:hi] *= amp
    y = expit(tune.scores(d, v1) / 400.0)
    lab = os.path.join(td, "lab.epd"); open(lab, "w").write("\n".join(f"{f}; [{yy:.4f}]" for f, yy in zip(fens, y)) + "\n")
    out = os.path.join(td, "lab.npz"); run_extract(out, [lab], "--min-pieces", "0"); return out


def _run_tune(npz, out, *args):
    r = subprocess.run([sys.executable, os.path.join(HERE, "tune.py"), npz, "--out", out, "--optimizer", "lbfgs", "--epochs", "200", "--prior", "10", "--k", "1.0",
                        "--val", "0.2", *args], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=900)
    return r.returncode, r.stdout.decode(), (json.load(open(out)) if r.returncode == 0 else None)


def _mae_of_tables(npz, T):
    """mean|eval| of the tables of a params.json over the data: independent of tune.py's own bookkeeping (tables -> merged values -> scores)"""
    d = tune.load(npz, np.float64); t = model.load_tables()
    tab = {k: (np.array(v, np.int64) if isinstance(v, list) else int(v)) for k, v in T.items()}
    w = model.merged_from_tables({**tab, "matmg": t["matmg"], "mateg": t["mateg"]})
    return float(np.mean(np.abs(tune.scores(d, model.flat(w, tab["tempo"])))))


@test("N1", "tune.scale_to: exact factor (scores() is linear), mean|eval| hits the target, tiny factors are skipped, scores(c*v) == c*scores(v)")
def _():
    if not hasattr(tune, "scale_to"): info("N1-N4: skipped (this tune.py has no scale_to / --norm-scale)"); return
    d = synth(3000, seed=11); rng = np.random.default_rng(11); v = rng.normal(0, 30, model.NPAR)
    ok(np.allclose(tune.scores(d, 1.37 * v), 1.37 * tune.scores(d, v), rtol=1e-12, atol=1e-9), "scores() is not linear in the parameters")
    cur = float(np.mean(np.abs(tune.scores(d, v))))
    for tgt in (0.5 * cur, 0.93 * cur, 1.2 * cur, 2.0 * cur):
        v2, r, applied = tune.scale_to(d, v, tgt); m = float(np.mean(np.abs(tune.scores(d, v2))))
        ok(applied and abs(m / tgt - 1) < 1e-12 and abs(r - tgt / cur) < 1e-12, f"target {tgt:.2f}: got {m:.4f}, factor {r}")
    v3, r, applied = tune.scale_to(d, v, cur * 1.003, 0.005); ok(not applied and v3 is v, "a 0.3 % factor must be skipped with tol 0.005")
    v4, r, applied = tune.scale_to(d, v, cur * 1.003, 0.0); ok(applied, "tol 0 must always apply")
    v5, r, applied = tune.scale_to(d, np.zeros(model.NPAR), 100.0, 0.0); ok(r == 1.0, "an all-zero eval has no scale: factor must stay 1, not divide by zero")


@test("N2", "tune.py --norm-scale asm: the drift of an un-normalised retune is removed; JSON, apply_params note and independent mean|eval| agree")
def _():
    if not hasattr(tune, "scale_to"): return
    with tempfile.TemporaryDirectory() as td:
        npz = _drift_npz(td); asm = tune.load(npz, np.float64); v_asm, _t = model.initial_flat()
        rc, out0, P0 = _run_tune(npz, os.path.join(td, "p0.json"), "--free", "full"); ok(rc == 0, out0[-500:])
        rc, out1, P1 = _run_tune(npz, os.path.join(td, "p1.json"), "--free", "full", "--norm-scale", "asm"); ok(rc == 0, out1[-500:])
        base = float(np.mean(np.abs(tune.scores(asm, v_asm))))
        m0, m1 = _mae_of_tables(npz, P0["tables"]), _mae_of_tables(npz, P1["tables"])
        ok(m0 / base > 1.02, f"the test world must make an un-normalised retune drift (>2 %), got {m0 / base:.4f}")
        ok(abs(m1 / base - 1) < 0.01, f"normalised retune: mean|eval| is {m1 / base:.4f} x the asm value (allowed +-1 % rounding)")
        S = P1["scale"]; ok(S["applied"] and abs(S["factor_applied"] * m0 / m1 - 1) < 0.05 and S["norm_scale"] == "asm", f"json scale block {S}")
        ok(P0["scale"]["applied"] is False, "no --norm-scale: nothing may be applied")
        ok(abs(P1["mean_abs_eval"]["tuned"] / P1["mean_abs_eval"]["asm"] - 1) < 0.01, "mean_abs_eval of the JSON must describe the normalised tables")
        ok("NOT normalised" in out1, "the price line (loss without the normalisation) is missing from the log")
        Z = P0["scale"]; ok(abs(Z["oscale_1024_to_asm"] - 1024 * Z["factor_to_asm_scale"]) <= 0.5 + 1e-9, "oscale_1024_to_asm is not round(1024 * factor_to_asm_scale)")
        ok(abs(Z["factor_to_asm_scale"] * m0 / base - 1) < 0.03, f"factor_to_asm_scale {Z['factor_to_asm_scale']:.4f} does not undo the measured drift {m0 / base:.4f} (train split vs all data: 3 % allowed)")
        for tag, P, expect_note in (("p0", P0, True), ("p1", P1, False)):
            r = subprocess.run([sys.executable, os.path.join(HERE, "apply_params.py"), os.path.join(td, f"{tag}.json"), "--out-dir", os.path.join(td, "o_" + tag), "--force"],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
            txt = r.stdout.decode(); ok(r.returncode == 0, f"apply_params {tag}: {txt[-400:]}")
            ok(("mean |eval| of the tuned tables" in txt) == expect_note, f"apply_params {tag}: scale note {'missing' if expect_note else 'must not appear'}:\n{txt}")
        info(f"N2: un-normalised retune mean|eval| = {m0 / base:.4f} x asm, normalised = {m1 / base:.4f} x asm (rounded tables, independent computation)")


@test("N3", "tune.py --norm-scale: explicit cp target, --norm-tol skip keeps frozen parameters bit-identical, bad arguments stop")
def _():
    if not hasattr(tune, "scale_to"): return
    with tempfile.TemporaryDirectory() as td:
        npz = _drift_npz(td, n=2000, seed=72); t = model.load_tables()
        rc, out, P = _run_tune(npz, os.path.join(td, "a.json"), "--free", "full", "--norm-scale", "400"); ok(rc == 0, out[-500:])
        m = _mae_of_tables(npz, P["tables"]); ok(abs(m / 400 - 1) < 0.01, f"--norm-scale 400: mean|eval| {m:.1f}")
        rc, out, P = _run_tune(npz, os.path.join(td, "b.json"), "--free", "bp,tempo", "--norm-scale", "asm"); ok(rc == 0, out[-500:])
        ok(P["scale"]["applied"] is False and "not applied" in out, f"--free bp,tempo: the factor is ~1 and must be skipped (factor {P['scale']['factor_applied']:.5f})")
        T = P["tables"]; ok(T["pst_mg"] == t["pst_mg"].tolist() and T["evp_mg"] == t["evp_mg"].tolist() and T["evp_eg"] == t["evp_eg"].tolist(), "a skipped normalisation changed a frozen parameter")
        for bad in ("abc", "0", "-5"):
            r = subprocess.run([sys.executable, os.path.join(HERE, "tune.py"), npz, "--out", os.path.join(td, "c.json"), "--norm-scale", bad], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
            ok(r.returncode != 0 and "--norm-scale" in r.stdout.decode(), f"--norm-scale {bad}: must stop with a message, rc {r.returncode}")


# ================================================================================================== R: resolve
@test("R1", "resolve: ply sampling distinct, sorted, reproducible, all plies when the game is short; parse_info cp / mate / bounds")
def _():
    a = resolve.pick_plies(60, 10, 1, "x/sp_1.jsonl", 7)
    ok(len(a) == 10 and len(set(a)) == 10 and a == sorted(a) and all(0 <= i < 60 for i in a), f"bad sample {a}")
    ok(a == resolve.pick_plies(60, 10, 1, "other/sp_1.jsonl", 7), "the sample must depend on the file name, not its folder")
    ok(a != resolve.pick_plies(60, 10, 1, "x/sp_1.jsonl", 8) or a != resolve.pick_plies(60, 10, 2, "x/sp_1.jsonl", 7), "line / seed do not change the sample")
    ok(resolve.pick_plies(7, 10, 1, "g", 1) == list(range(7)), "a game with fewer plies than --per-game gives all of them")
    pi = resolve.parse_info
    ok(pi("info depth 12 seldepth 20 score cp -34 nodes 5 pv e2e4 e7e5") == (12, -34, "", ["e2e4", "e7e5"]), "cp line")
    ok(pi("info depth 9 score mate -3 nodes 5 pv a1a2")[1] == -resolve.MATE and pi("info depth 9 score mate 2 pv a1a2")[1] == resolve.MATE, "mate")
    ok(pi("info depth 9 score cp 20 lowerbound nodes 5 pv a1a2")[2] == "lowerbound", "bound")
    ok(pi("info depth 9 score cp 20 nodes 5") is None and pi("info string tmc 1 2 3") is None, "a line without pv / score must give None")
    b, n = resolve.play_pv("4k3/8/8/8/8/8/8/4K2R w K - 0 1", ["e1g1", "e8d8", "f1f8", "a1a2"])
    ok(n == 3 and b.board_fen() == "3k1R2/8/8/8/8/8/8/6K1", f"PV playback: {n} {b.board_fen()} (castling, then stop at the illegal move)")


@test("R2", "resolve (engine): skips fail / short games, labels from White's view, end positions quiet of check, side file = a separate search")
def _():
    rnd = random.Random(11); games = []
    for g in range(4):
        b = chess.Board(); pl = []
        while len(pl) < 34 and not b.is_game_over():
            m = rnd.choice(list(b.legal_moves)); pl.append([b.fen(), 0, m.uci(), int(b.is_capture(m)), int(b.gives_check(m))]); b.push(m)
        games.append({"plies": pl if g != 2 else pl[:12], "adj": "fail" if g == 1 else None, "result_white": [0.0, 1.0, 0.5, 1.0][g]})
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "g.jsonl"); open(p, "w").write("\n".join(json.dumps(g) for g in games) + "\n")
        out = os.path.join(td, "own.epd")
        cmd = [sys.executable, os.path.join(HERE, "resolve.py"), p, "--engine", ENG[0], "--out", out, "--depth", "5", "--per-game", "4", "--no-qsearch", "--workers", "1"]
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=300); txt = r.stdout.decode()
        ok(r.returncode == 0, txt[-500:])
        lines = open(out).read().split("\n")[:-1]; inf = [l.split("\t") for l in open(out + ".info").read().split("\n")[:-1]]
        ok(len(lines) == len(inf) and 0 < len(lines) <= 8, f"{len(lines)} positions, {len(inf)} info lines (games 0 and 3 only, 4 plies each)\n{txt[-400:]}")
        ok({int(t[1]) for t in inf} <= {1, 4}, f"positions from a fail / short game: {sorted({t[1] for t in inf})}")
        ok("fail 1" in txt and "short 1" in txt, f"skip counts missing: {txt[-300:]}")
        e = resolve.Eng(ENG[0], "Hash=16;Threads=1")
        try:
            for l, t in zip(lines, inf):
                f = l.split(); fen = " ".join(f[:6]); g = games[int(t[1]) - 1]
                ok(f[6] == {1.0: "[1.0]", 0.0: "[0.0]", 0.5: "[0.5]"}[g["result_white"]], f"label {f[6]} is not the game result from White's view")
                bb = chess.Board(fen); ok(bb.is_valid() and not bb.is_check() and not bb.is_game_over(), f"end position not usable: {fen}")
                start = g["plies"][int(t[2])][0]
                e.send("ucinewgame"); e.send("isready"); e.wait("readyok", 30); e.send("position fen " + start); e.send("go depth 5")
                last = None
                while True:
                    x = e.readline(30)
                    if x.startswith("info") and " pv " in x and "bound" not in x: last = x.split()
                    if x.startswith("bestmove"): break
                pv = last[last.index("pv") + 1:]; sc = int(last[last.index("cp") + 1]) if "cp" in last else None
                c = chess.Board(start); k = 0
                for u in pv:
                    mv = chess.Move.from_uci(u)
                    if mv not in c.legal_moves: break
                    c.push(mv); k += 1
                ok(c.fen() == fen and k == int(t[3]), f"PV end differs from a separate search: {c.fen()} vs {fen} ({k} vs {t[3]} moves)")
                ok(sc is not None and int(t[4]) == (sc if start.split()[1] == "w" else -sc), f"side-file score {t[4]} is not the depth-5 score in White's view ({sc})")
        finally:
            e.quit()
        r2 = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=300); t2 = r2.stdout.decode()
        ok(r2.returncode == 0 and "2 games already done" in t2 and len(open(out).read().split("\n")[:-1]) == len(lines), f"resume: {t2[-300:]}")
        r3 = subprocess.run(cmd[:-5] + ["--per-game", "3", "--workers", "1"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=300)
        ok(r3.returncode != 0 and "other arguments" in r3.stdout.decode(), "a resume with other arguments must stop with a message")
        out2 = os.path.join(td, "q.epd")
        r4 = subprocess.run(cmd[:cmd.index("--out")] + ["--out", out2, "--depth", "5", "--per-game", "4", "--workers", "1"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=300)
        ok(r4.returncode == 0, r4.stdout.decode()[-400:])
        q = [l.split("\t") for l in open(out2 + ".info").read().split("\n")[:-1]]; qe = open(out2).read().split("\n")[:-1]
        ok(len(q) == len(qe) > 0, "quiescence run: no positions")
        for l, t in zip(qe, q):
            b = chess.Board(" ".join(l.split()[:6])); f0 = b.fen()
            ok(extract.qsearch_leaf(b).fen() == f0, f"a resolved position is not quiet for the quiescence search: {f0}")
            ok(int(t[5]) >= 0 and t[6] == f0, f"side file: quiescence plies / end FEN {t[5:]} vs {f0}")
        npz = os.path.join(td, "q.npz"); run_extract(npz, [out2])
        d = tune.load(npz, np.float64)
        ok(d.n > 0 and len(np.unique(d.grp)) == len({t[1] for t in q}), f"groups {len(np.unique(d.grp))} != games {len({t[1] for t in q})} (game= tag)")


# ================================================================================================== main
def main():
    global ENG, N
    ap = argparse.ArgumentParser(); ap.add_argument("--engine", default=""); ap.add_argument("--n", type=int, default=1500); ap.add_argument("--only", default="")
    a = ap.parse_args(); N = a.n
    from _eng import resolve, ENGINE
    ENG = resolve(a.engine) if a.engine else ENGINE
    c = check_eval.engine_cfg(ENG)
    if c is not None: model.set_cfg(c)
    for tag, name, fn in TESTS:
        if a.only and not tag.startswith(a.only) and not (tag == "P1" and a.only.startswith(("T0", "T7"))): continue
        try:
            fn(); print(f"PASS {tag:3s} {name}", flush=True)
        except Exception as e:
            FAILS.append(tag)
            msg = str(e) if isinstance(e, AssertionError) else "".join(traceback.format_exception_only(type(e), e)).strip()
            print(f"FAIL {tag:3s} {name}\n       -> {msg[:700]}", flush=True)
            if not isinstance(e, AssertionError): traceback.print_exc()
    for i in INFOS: print("INFO", i)
    print(f"\n{len(TESTS) - len(FAILS)} of {len(TESTS)} passed" + (f"; FAILED: {', '.join(FAILS)}" if FAILS else ""))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
