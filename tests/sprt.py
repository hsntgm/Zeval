#!/usr/bin/env python3
"""SPRT helpers for tests/match.py: log-likelihood ratio for trinomial (per game) and pentanomial (per game pair) results.

Why pentanomial: match.py plays every opening twice with the colours swapped, so the two games of a pair are not
independent (a good opening for one side is good in both games).  The unit of observation is the PAIR; its score for
engine A is 0, 0.5, 1, 1.5 or 2, i.e. five outcomes.  Counting the 2N games as independent (trinomial) overstates the
confidence.

Method (the same family fishtest uses): the empirical distribution f over the outcome values x is replaced, for each
hypothesis with mean score s, by the maximum-likelihood distribution p with  sum p_i * x_i = s  (p_i = f_i / (1 + theta*(x_i - s)),
theta found by bisection); LLR = N * sum f_i * log(p1_i / p0_i).  Zero cells get a tiny count (1e-3) so the logarithms exist.
Elo bounds are turned into scores with the logistic model, s = 1 / (1 + 10^(-elo/400)).

  python3 tests/sprt.py --selftest [--runs 400]     checks the maths and the error rates of the whole procedure
"""
import math, random, sys

PENTA_X = [0.0, 0.25, 0.5, 0.75, 1.0]      # pair score / 2
TRI_X = [0.0, 0.5, 1.0]                    # game score (loss, draw, win)


def logistic(elo):
    return 1.0 / (1.0 + 10 ** (-elo / 400.0))


def elo_of(score):
    if score <= 0.0: return -999.0
    if score >= 1.0: return 999.0
    return 400.0 * math.log10(score / (1.0 - score))


def _regularize(counts):
    c = [x if x > 0 else 1e-3 for x in counts]
    t = float(sum(c))
    return [x / t for x in c]


def _mle(f, x, s):
    """distribution p closest to f (max sum f log p) with mean exactly s; needs min(x) < s < max(x), all f > 0"""
    lo = -1.0 / (max(x) - s)
    hi = 1.0 / (s - min(x))
    a = lo * (1.0 - 1e-12)
    b = hi * (1.0 - 1e-12)
    d = [xi - s for xi in x]
    for _ in range(60):                    # g(theta) = sum f_i d_i / (1 + theta d_i) is strictly decreasing
        m = 0.5 * (a + b)
        g = 0.0
        for fi, di in zip(f, d):
            g += fi * di / (1.0 + m * di)
        if g > 0.0: a = m
        else: b = m
    th = 0.5 * (a + b)
    return [fi / (1.0 + th * di) for fi, di in zip(f, d)]


def llr(counts, x, elo0, elo1):
    n = float(sum(counts))
    if n <= 0: return 0.0
    f = _regularize(counts)
    s0, s1 = logistic(elo0), logistic(elo1)
    p0, p1 = _mle(f, x, s0), _mle(f, x, s1)
    return n * sum(fi * (math.log(b) - math.log(a)) for fi, a, b in zip(f, p0, p1))


def pentanomial_llr(counts5, elo0, elo1):
    return llr(counts5, PENTA_X, elo0, elo1)


def trinomial_llr(counts3, elo0, elo1):
    return llr(counts3, TRI_X, elo0, elo1)


def mean_var(counts, x):
    n = float(sum(counts))
    if n <= 0: return 0.5, 0.0
    m = sum(c * xi for c, xi in zip(counts, x)) / n
    v = sum(c * (xi - m) ** 2 for c, xi in zip(counts, x)) / n
    return m, v


def elo_and_error(counts, x):
    """(elo, 95% half-width in elo) from the per-observation mean and variance (observation = game or pair)"""
    n = float(sum(counts))
    s, v = mean_var(counts, x)
    elo = elo_of(s)
    if n <= 0 or not (0.0 < s < 1.0) or v <= 0.0: return elo, 999.0
    se = math.sqrt(v / n)
    return elo, 400.0 / math.log(10) * 1.96 * se / (s * (1.0 - s))


# ---------------------------------------------------------------- self-test
def _pair_probs(elo, draw_ratio):
    s = logistic(elo)
    w, l = s - draw_ratio / 2.0, 1.0 - s - draw_ratio / 2.0
    g = [l, draw_ratio, w]                 # one game: loss, draw, win -> index 0, 1, 2
    p = [0.0] * 5
    for i in range(3):
        for j in range(3): p[i + j] += g[i] * g[j]
    return p


def _run_sprt(rng, p, elo0, elo1, alpha=0.05, beta=0.05, every=5, cap=20000):
    lo, hi = math.log(beta / (1 - alpha)), math.log((1 - beta) / alpha)
    counts = [0] * 5
    cum = [sum(p[:i + 1]) for i in range(5)]
    for n in range(1, cap + 1):
        u = rng.random()
        k = 0
        while k < 4 and u > cum[k]: k += 1
        counts[k] += 1
        if n % every == 0:
            v = pentanomial_llr(counts, elo0, elo1)
            if v >= hi: return "H1", n
            if v <= lo: return "H0", n
    return "none", cap


def selftest(runs=400):
    ok = True

    def check(name, cond, info=""):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + name + ("" if cond or not info else "  [" + info + "]"))
        ok = ok and cond

    # 1. the constrained distribution really has the requested mean and sums to 1
    f = _regularize([3, 20, 47, 22, 8])
    for s in (0.45, 0.5, 0.52):
        p = _mle(f, PENTA_X, s)
        check(f"mle mean = {s}", abs(sum(pi * xi for pi, xi in zip(p, PENTA_X)) - s) < 1e-9 and abs(sum(p) - 1) < 1e-9)
    # 2. antisymmetry: mirroring the result (win <-> loss) with mirrored bounds flips the sign of the LLR
    c = [3, 20, 47, 22, 8]
    a = pentanomial_llr(c, -5, 10)
    b = pentanomial_llr(c[::-1], -10, 5)
    check("antisymmetry under colour of result", abs(a + b) < 1e-9, f"{a:.9f} {b:.9f}")
    # 3. data that sit exactly on the H1 mean favour H1, data on the H0 mean favour H0
    s1 = logistic(10)
    n = 4000
    pr = _pair_probs(10, 0.4)
    c1 = [round(n * q) for q in pr]
    check("data at elo1 -> LLR > 0", pentanomial_llr(c1, 0, 10) > 0)
    pr = _pair_probs(0, 0.4)
    c0 = [round(n * q) for q in pr]
    check("data at elo0 -> LLR < 0", pentanomial_llr(c0, 0, 10) < 0)
    # 4. no data -> 0
    check("empty -> 0", pentanomial_llr([0] * 5, 0, 10) == 0.0)
    # 5. the whole procedure: error rates of a [0, 30] Elo SPRT, alpha = beta = 0.05, 40% draws
    rng = random.Random(12345)
    acc_h1_under_h0 = sum(1 for _ in range(runs) if _run_sprt(rng, _pair_probs(0, 0.4), 0, 30)[0] == "H1")
    res = [_run_sprt(rng, _pair_probs(30, 0.4), 0, 30) for _ in range(runs)]
    acc_h0_under_h1 = sum(1 for r, _ in res if r == "H0")
    undecided = sum(1 for r, _ in res if r == "none")
    avg_n = sum(n for _, n in res) / runs
    fa, fb = acc_h1_under_h0 / runs, acc_h0_under_h1 / runs
    print(f"     runs={runs}: H1 accepted when true elo = 0: {fa:.3f} (alpha 0.05);  H0 accepted when true elo = 30: {fb:.3f} (beta 0.05);"
          f"  avg pairs at elo 30: {avg_n:.0f}, undecided: {undecided}")
    # sequential tests checked every 5 pairs stay at or below the nominal level; allow sampling noise
    check("false-positive rate <= 0.08", fa <= 0.08)
    check("false-negative rate <= 0.08", fb <= 0.08)
    print("ALL PASSED" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        r = 400
        if "--runs" in sys.argv: r = int(sys.argv[sys.argv.index("--runs") + 1])
        sys.exit(selftest(r))
    print(__doc__)
