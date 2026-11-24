#!/usr/bin/env python3
"""SPSA tuning of the numeric options T<n> of a -DTUNE build (search margins T0 .. T19, evaluation constants T20 .. T39, endgame scale T40 .. T57, qsearch delta margin T58, continuation history T59 .. T61,
further search constants T62 .. T76: see tune_def in src/p5_main.asm).

Every iteration perturbs all parameters at once by +-c_k (random signs), plays --pairs game pairs (colours swapped, same opening) between
theta + c_k*delta and theta - c_k*delta with the same engine binary, and moves theta along delta by r_k * c_k * result, where result is the SUM over
the pairs of (score of the + side - score of the - side), each pair in [-1, +1]: every pair counts as one observation, as in fishtest, so --r-end is
the step per pair and an iteration with --pairs P moves up to P times as far as one pair.  Schedules as in common chess-engine SPSA practice:
c_k = c / (k + 1)^0.101, a_k = a / (A + k + 1)^0.602, r_k = a_k / c_k^2, with a chosen so that r at the last iteration equals --r-end.
The values sent to the engine are rounded to integers (negative values too: the -DTUNE build accepts "setoption name Tn value -5").  A perturbation
below half a unit would often give both sides the same integer, so every c must keep c_k >= 0.5 up to the last iteration (checked at the start).
An engine failure (crash, illegal move, no answer, time forfeit; tests/match.py adj="fail") loses the game as in match.py and is counted and
reported (fails=), so that a parameter region that breaks the engine is seen.
Games use tests/match.py (Eng / play / openings), so the time controls and adjudication are those of match.py.

usage:  python3 tools/spsa.py ENGINE --param T0=85,10 --param T4=110,12 ... [--iters 750] [--pairs 2] [--movetime 20 | --tc 10+0.1]
                                     [--workers 2] [--r-end 0.02] [--seed 1] [--out spsa.json] [--epd tests/endgames.epd --no-adj]
        --param Tn=start,c   start value and perturbation c (in the parameter's own units; c of about 5-10 % of the value, and at least
                             0.5 * iters^0.101, about 1.0 for 750 iterations: smaller c is refused)
        --pairs              game pairs per iteration (run in parallel on --workers processes)
The result (rounded theta, history) is written to --out every 10 iterations (atomically); --resume continues a run from that file (the perturbation
of iteration k is seeded by --seed and k, so a resumed run is the same run).  Verify it with an SPRT (tests/match.py --optA "T0=..;T4=..").
A few thousand games only move the parameters coarsely; real runs use tens of thousands.
Parameters that do nothing in the default -DTUNE build (their switch is off; build with the switch on to tune them): T17 (EVALDIFF_HIST),
T18 (NMP_VER), T19 (IIR_CUT), T28 / T29 (EV_LAZY), T30 (EV_R50), T31 .. T39 (EV_INIT), T59 (CH_LMR); T77 .. T95 are unused.  T12 .. T16 are
time-management constants: they act only with --tc (go wtime / btime), not with --movetime.
"""
import argparse, json, os, random, sys, time
from multiprocessing import Pool

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tests"))
import match  # noqa: E402


def pair(args):
    eng, opt_p, opt_m, opening, mode, adj = args
    r1, r2, w1, w2, _, _ = match.worker((eng, eng, opt_p, opt_m, opening, mode, False, adj))
    return (r1 + r2) - 1.0, (w1 == "fail") + (w2 == "fail")     # + side score minus - side score over the pair (-1 .. +1), engine failures


def opts(names, vals):
    return ";".join(f"{n}={int(round(v))}" for n, v in zip(names, vals))


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
        n = n.strip()
        if not (n[:1] in "Tt" and n[1:].isdigit()) or not c:
            raise SystemExit(f"--param {p!r}: expected Tn=start,c")
        try: names.append(n.upper()); theta.append(float(s)); c0.append(float(c))
        except ValueError: raise SystemExit(f"--param {p!r}: start and c must be numbers")
    if len(set(names)) != len(names): raise SystemExit("--param: a parameter is given twice")
    if a.iters < 1 or a.pairs < 1 or a.workers < 1: raise SystemExit("--iters, --pairs and --workers must be >= 1")
    if a.r_end <= 0: raise SystemExit("--r-end must be > 0")
    for n, c in zip(names, c0):
        if c <= 0: raise SystemExit(f"--param {n}: c must be > 0")
        if c / a.iters ** 0.101 < 0.5:
            raise SystemExit(f"--param {n}: c = {c:g} falls to {c / a.iters ** 0.101:.2f} < 0.5 at the last iteration: both sides would often get the "
                             f"same integer value.  Use c >= {0.5 * a.iters ** 0.101:.2f} for --iters {a.iters}")
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)   # the folder of --out (e.g. results\) need not exist yet
    mode = ("tc",) + match.parse_tc(a.tc) if a.tc else ("movetime", a.movetime)
    N = a.iters
    A = 0.1 * N
    c_end = [c / (N ** 0.101) for c in c0]
    a_end = a.r_end * c_end[0] ** 2            # one common a, defined through the first parameter's scale; r_k = a_k / c_k^2 per parameter
    a0 = a_end * (A + N) ** 0.602
    ops = match.epd_starts(a.epd, N * a.pairs, a.seed) if a.epd else match.get_openings(N * a.pairs, 0, a.seed, a.engine, 8, 90)
    run_args = {"start": theta[:], "c": c0, "iters": N, "pairs": a.pairs, "seed": a.seed, "r_end": a.r_end, "mode": list(mode), "epd": a.epd, "no_adj": a.no_adj}
    hist = []; k0 = 0; fails = 0
    if a.resume:
        if not os.path.exists(a.out): raise SystemExit(f"--resume: {a.out} does not exist")
        st = json.load(open(a.out))
        if st.get("names") != names or "theta_float" not in st:
            raise SystemExit(f"--resume: {a.out} belongs to another parameter set")
        old = st.get("args")
        if old is not None and old != json.loads(json.dumps(run_args)):
            diff = ", ".join(k for k in run_args if old.get(k) != json.loads(json.dumps(run_args[k])))
            raise SystemExit(f"--resume: {a.out} was started with other arguments ({diff}); a resumed run must use the same ones")
        theta = list(st["theta_float"]); k0 = st["iters"]; hist = st.get("history_full", []); fails = st.get("fails", 0)
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
            out_pairs = pool.map(pair, jobs)
            res = sum(r for r, _ in out_pairs); fails += sum(f for _, f in out_pairs)
            for i in range(len(theta)):
                rk = ak / (ck[i] ** 2) * (c0[i] / c0[0]) ** 2      # the same relative step for every parameter
                theta[i] += rk * ck[i] * res * delta[i]
            hist.append([round(x, 2) for x in theta])
            if (k + 1) % 10 == 0 or k + 1 == N:
                out = {"names": names, "theta": [int(round(x)) for x in theta], "theta_float": theta, "iters": k + 1,
                       "games": (k + 1) * a.pairs * 2, "fails": fails, "args": run_args, "history": hist[::10], "history_full": hist}
                with open(a.out + ".tmp", "w") as fh: json.dump(out, fh)
                os.replace(a.out + ".tmp", a.out)                      # atomic: an interrupted run leaves the last complete state
                print(f"iter {k + 1}/{N}  games {(k + 1) * a.pairs * 2}  " + "  ".join(f"{n}={x:.1f}" for n, x in zip(names, theta))
                      + (f"  fails={fails}" if fails else "") + f"  ({time.time() - t0:.0f} s)", flush=True)
    print("SPSADONE", flush=True)


if __name__ == "__main__":
    main()
