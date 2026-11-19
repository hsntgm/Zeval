#!/usr/bin/env python3
"""SPSA tuning of the numeric options T<n> of a -DTUNE build (search margins T0 .. T19, evaluation constants T20 .. T39, endgame scale T40 .. T57, qsearch delta margin T58, continuation history T59 .. T61).

Every iteration perturbs all parameters at once by +-c_k (random signs), plays game pairs (colours swapped, same opening) between theta + c_k*delta
and theta - c_k*delta with the same engine binary, and moves theta along delta by r_k * c_k * result (result = score of the + side - score of the
- side, per pair in [-1, +1]).  Schedules as in common chess-engine SPSA practice: c_k = c / (k + 1)^0.101, a_k = a / (A + k + 1)^0.602, r_k = a_k / c_k^2,
with a chosen so that r at the last iteration equals --r-end.
Games use tests/match.py (Eng / play / openings), so the time controls and adjudication are those of match.py.

usage:  python3 tools/spsa.py ENGINE --param T0=85,10 --param T4=110,12 ... [--iters 750] [--pairs 2] [--movetime 20 | --tc 10+0.1]
                                     [--workers 2] [--r-end 0.02] [--seed 1] [--out spsa.json] [--epd tests/endgames.epd --no-adj]
        --param Tn=start,c   start value and perturbation c (in the parameter's own units; c of about 5-10 % of the value)
        --pairs              game pairs per iteration (run in parallel on --workers processes)
The result (rounded theta, history) is written to --out every 10 iterations (atomically); --resume continues a run from that file (the perturbation
of iteration k is seeded by --seed and k, so a resumed run is the same run).  Verify it with an SPRT (tests/match.py --optA "T0=..;T4=..").
A few thousand games only move the parameters coarsely; real runs use tens of thousands.
"""
import argparse, json, os, random, sys, time
from multiprocessing import Pool

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))
import match  # noqa: E402


def pair(args):
    eng, opt_p, opt_m, opening, mode, adj = args
    r1, r2, _, _, _, _ = match.worker((eng, eng, opt_p, opt_m, opening, mode, False, adj))
    return (r1 + r2) - 1.0                     # + side score minus - side score over the pair: -1 .. +1


def opts(names, vals):
    return ";".join(f"{n}={max(0, int(round(v)))}" for n, v in zip(names, vals))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("engine")
    ap.add_argument("--param", action="append", required=True, help="Tn=start,c")
    ap.add_argument("--iters", type=int, default=750)
    ap.add_argument("--pairs", type=int, default=2)
    ap.add_argument("--movetime", type=int, default=20)
    ap.add_argument("--tc", default="")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--r-end", type=float, default=0.02)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", default="spsa.json")
    ap.add_argument("--epd", default="", help="start positions from a FEN / EPD file (tests/match.py --epd) instead of random openings")
    ap.add_argument("--no-adj", action="store_true", help="no win / draw adjudication (tests/match.py --no-adj)")
    ap.add_argument("--resume", action="store_true", help="continue the run of --out (same arguments) from its last saved iteration")
    a = ap.parse_args()
    names, theta, c0 = [], [], []
    for p in a.param:
        n, _, rest = p.partition("=")
        s, _, c = rest.partition(",")
        names.append(n.strip()); theta.append(float(s)); c0.append(float(c))
    mode = ("tc",) + match.parse_tc(a.tc) if a.tc else ("movetime", a.movetime)
    N = a.iters
    A = 0.1 * N
    c_end = [c / (N ** 0.101) for c in c0]
    a_end = a.r_end * c_end[0] ** 2            # one common a, defined through the first parameter's scale; r_k = a_k / c_k^2 per parameter
    a0 = a_end * (A + N) ** 0.602
    ops = match.epd_starts(a.epd, N * a.pairs, a.seed) if a.epd else match.get_openings(N * a.pairs, 0, a.seed, a.engine, 8, 90)
    hist = []; k0 = 0
    if a.resume and os.path.exists(a.out):
        st = json.load(open(a.out))
        if st.get("names") != names or "theta_float" not in st:
            raise SystemExit(f"--resume: {a.out} belongs to another parameter set")
        theta = list(st["theta_float"]); k0 = st["iters"]; hist = st.get("history_full", [])
        print(f"resumed at iteration {k0} of {N}", flush=True)
    t0 = time.time()
    with Pool(a.workers) as pool:
        for k in range(k0, N):
            ck = [c / (k + 1) ** 0.101 for c in c0]
            ak = a0 / (A + k + 1) ** 0.602
            rng = random.Random(a.seed * 1000003 + k)                  # the perturbation of iteration k depends on k only: a resumed run is the same run
            delta = [rng.choice((-1, 1)) for _ in names]
            plus = [t + c * d for t, c, d in zip(theta, ck, delta)]
            minus = [t - c * d for t, c, d in zip(theta, ck, delta)]
            jobs = [(a.engine, opts(names, plus), opts(names, minus), ops[(k * a.pairs + j) % len(ops)], mode, not a.no_adj) for j in range(a.pairs)]
            res = sum(pool.map(pair, jobs))
            for i in range(len(theta)):
                rk = ak / (ck[i] ** 2) * (c0[i] / c0[0]) ** 2      # the same relative step for every parameter
                theta[i] += rk * ck[i] * res * delta[i]
            hist.append([round(x, 2) for x in theta])
            if (k + 1) % 10 == 0 or k + 1 == N:
                out = {"names": names, "theta": [int(round(x)) for x in theta], "theta_float": theta, "iters": k + 1,
                       "games": (k + 1) * a.pairs * 2, "history": hist[::10], "history_full": hist}
                with open(a.out + ".tmp", "w") as fh: json.dump(out, fh)
                os.replace(a.out + ".tmp", a.out)                      # atomic: an interrupted run leaves the last complete state
                print(f"iter {k + 1}/{N}  games {(k + 1) * a.pairs * 2}  " + "  ".join(f"{n}={x:.1f}" for n, x in zip(names, theta))
                      + f"  ({time.time() - t0:.0f} s)", flush=True)
    print("SPSADONE", flush=True)


if __name__ == "__main__":
    main()
