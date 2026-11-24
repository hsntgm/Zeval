#!/usr/bin/env python3
"""SPSA tuning of the numeric options T<n> of a -DTUNE build (search margins T0 .. T19, evaluation constants T20 .. T39, endgame scale T40 .. T57, qsearch delta margin T58, continuation history T59 .. T61,
further search constants T62 .. T79, time curve T80 .. T84: see tune_def in src/p5_main.asm).

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
        --param Tn=start,c[,min,max]   start value, perturbation c (in the parameter's own units; c of about 5-10 % of the value, and at
                             least 0.5 * iters^0.101, about 1.0 for 750 iterations: smaller c is refused) and optional bounds: theta and both
                             perturbed values stay within [min, max] (a divisor or a window that must not reach 0, a margin that must stay > 0)
        --avg-last N         the result also gives the mean of theta over the last N iterations (default: the last fifth), which is less
                             noisy than the final point
        --no-check           skip the start-up check that every parameter changes the search (a search to depth 9 on 4 positions with
                             start - c and start + c must differ in nodes; time-management parameters are checked for the mode only)
        --book auto|random|FILE   start positions as tests/match.py --book (default auto: tests/books/noob_3moves.epd when present)
        --pairs              game pairs per iteration (run in parallel on --workers processes)
The result (rounded theta, history) is written to --out every 10 iterations (atomically); --resume continues a run from that file (the perturbation
of iteration k is seeded by --seed and k, so a resumed run is the same run).  Verify it with an SPRT (tests/match.py --optA "T0=..;T4=..").
A few thousand games only move the parameters coarsely; real runs use tens of thousands.
Parameters that do nothing in the default -DTUNE build (their switch is off; build with the switch on to tune them): T17 (EVALDIFF_HIST),
T18 (NMP_VER), T19 (IIR_CUT; the live IIR depth is T77), T28 / T29 (EV_LAZY), T30 (EV_R50), T31 .. T39 (EV_INIT), T59 (CH_LMR); T85 .. T95
are unused.  The start-up check finds them.  T12 .. T16 and T80 .. T84 are time-management constants: they act only with --tc (go wtime /
btime), not with --movetime (refused).  Adjudication (win / draw by the engines' own scores) leans towards the engine's own judgement: for
evaluation constants (T20 .. T57) use --no-adj.
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


TM_SLOTS = set(range(12, 17)) | set(range(80, 85))      # time-management constants: they act with a clock only
CHECK_FENS = ["r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4",
              "r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1",
              "8/2p5/3p4/KP5r/1R3p1k/8/4P1P1/8 w - - 0 1",
              "r2q1rk1/pp2bppp/2n1pn2/3p4/2PP4/2N1PN2/PP3PPP/R2QKB1R w KQ - 0 9"]


def nodes_with(engine, setopts):
    """total nodes of a depth-9 search on CHECK_FENS with the given 'Tn=v' options"""
    import subprocess
    p = subprocess.Popen([engine], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
    def send(x): p.stdin.write(x + "\n"); p.stdin.flush()
    send("uci")
    for kv in setopts:
        n, v = kv.split("=")
        send(f"setoption name {n} value {v}")
    total = 0
    for fen in CHECK_FENS:
        send("ucinewgame"); send("position fen " + fen); send("go depth 9")
        last = 0
        while True:
            line = p.stdout.readline()
            if not line: raise SystemExit(f"start-up check: {engine} stopped")
            t = line.split()
            if "nodes" in t: last = int(t[t.index("nodes") + 1])
            if line.startswith("bestmove"): break
        total += last
    send("quit"); p.wait(timeout=10)
    return total


def check_active(engine, names, theta, c0, lo, hi):
    """refuse a parameter that does not change the search (its switch off in this build, or not a tuning slot at all)"""
    dead = []
    for n, t, c, l, h in zip(names, theta, c0, lo, hi):
        if int(n[1:]) in TM_SLOTS: continue
        a = int(round(max(l, t - c))); b = int(round(min(h, t + c)))
        if a == b or nodes_with(engine, [f"{n}={a}"]) == nodes_with(engine, [f"{n}={b}"]): dead.append(n)
    if dead:
        raise SystemExit(f"start-up check: the search of {engine} does not change with {', '.join(dead)} (its switch is off in this build, "
                         "or the slot is unused); --no-check skips the check")
    print(f"start-up check: all {len(names)} parameters change the search", flush=True)


def opts(names, vals):
    return ";".join(f"{n}={int(round(v))}" for n, v in zip(names, vals))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("engine")
    ap.add_argument("--param", action="append", required=True, help="Tn=start,c[,min,max]")
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
    ap.add_argument("--avg-last", type=int, default=0, help="mean of theta over the last N iterations in the result (0 = the last fifth)")
    ap.add_argument("--no-check", action="store_true", help="skip the start-up check that every parameter changes the search")
    ap.add_argument("--book", default="auto", help="start positions: auto (tests/books/noob_3moves.epd when present), random, or an EPD file")
    a = ap.parse_args()
    names, theta, c0, lo, hi = [], [], [], [], []
    for p in a.param:
        n, _, rest = p.partition("=")
        f = rest.split(",")
        n = n.strip()
        if not (n[:1] in "Tt" and n[1:].isdigit()) or len(f) not in (2, 4):
            raise SystemExit(f"--param {p!r}: expected Tn=start,c or Tn=start,c,min,max")
        try:
            names.append(n.upper()); theta.append(float(f[0])); c0.append(float(f[1]))
            lo.append(float(f[2]) if len(f) == 4 else -float("inf")); hi.append(float(f[3]) if len(f) == 4 else float("inf"))
        except ValueError: raise SystemExit(f"--param {p!r}: start, c, min and max must be numbers")
        if not lo[-1] <= theta[-1] <= hi[-1]: raise SystemExit(f"--param {p!r}: start outside [min, max]")
        if hi[-1] - lo[-1] < 2 * c0[-1]: raise SystemExit(f"--param {p!r}: [min, max] narrower than 2 c")
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
    tm = [n for n in names if int(n[1:]) in TM_SLOTS]
    if tm and not a.tc: raise SystemExit(f"--param {', '.join(tm)}: time-management constants act only with --tc (a clock), not with --movetime")
    if not a.no_check and not a.resume: check_active(a.engine, names, theta, c0, lo, hi)
    N = a.iters
    A = 0.1 * N
    c_end = [c / (N ** 0.101) for c in c0]
    a_end = a.r_end * c_end[0] ** 2            # one common a, defined through the first parameter's scale; r_k = a_k / c_k^2 per parameter
    a0 = a_end * (A + N) ** 0.602
    if a.resume and a.book == "auto" and os.path.exists(a.out):
        try: old_args = json.load(open(a.out)).get("args") or {}
        except (OSError, ValueError): old_args = {}
        if "book" not in old_args: a.book = "random"                 # a run started before --book (random openings) resumes as it began
    book = "" if a.epd or a.book == "random" else (a.book if a.book != "auto" else (match.BOOK if os.path.isfile(match.BOOK) else ""))
    if book and not os.path.isfile(book): raise SystemExit(f"--book {book!r} not found")
    if a.epd: ops = match.epd_starts(a.epd, N * a.pairs, a.seed)
    elif book: ops = match.book_starts(book, N * a.pairs, a.seed)
    else: ops = match.get_openings(N * a.pairs, 0, a.seed, a.engine, 8, 90)
    print(f"spsa: {len(names)} parameters, {N} iterations x {a.pairs} pairs, start positions: "
          + (a.epd if a.epd else (f"book {os.path.basename(book)}" if book else "random openings")), flush=True)
    run_args = {"start": theta[:], "c": c0, "iters": N, "pairs": a.pairs, "seed": a.seed, "r_end": a.r_end, "mode": list(mode), "epd": a.epd, "no_adj": a.no_adj}
    if any(x != -float("inf") for x in lo) or any(x != float("inf") for x in hi): run_args["bounds"] = [[l if l > -float("inf") else None, h if h < float("inf") else None] for l, h in zip(lo, hi)]
    if book: run_args["book"] = os.path.basename(book)
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
            plus = [min(h, max(l, t + c * d)) for t, c, d, l, h in zip(theta, ck, delta, lo, hi)]
            minus = [min(h, max(l, t - c * d)) for t, c, d, l, h in zip(theta, ck, delta, lo, hi)]
            jobs = [(a.engine, opts(names, plus), opts(names, minus), ops[(k * a.pairs + j) % len(ops)], mode, not a.no_adj) for j in range(a.pairs)]
            out_pairs = pool.map(pair, jobs)
            res = sum(r for r, _ in out_pairs); fails += sum(f for _, f in out_pairs)
            for i in range(len(theta)):
                rk = ak / (ck[i] ** 2) * (c0[i] / c0[0]) ** 2      # the same relative step for every parameter
                theta[i] = min(hi[i], max(lo[i], theta[i] + rk * ck[i] * res * delta[i]))
            hist.append([round(x, 2) for x in theta])
            if (k + 1) % 10 == 0 or k + 1 == N:
                navg = min(len(hist), a.avg_last if a.avg_last > 0 else max(1, len(hist) // 5))
                mean = [sum(h[i] for h in hist[-navg:]) / navg for i in range(len(names))]
                out = {"names": names, "theta": [int(round(x)) for x in theta], "theta_float": theta, "iters": k + 1,
                       "games": (k + 1) * a.pairs * 2, "fails": fails, "args": run_args, "history": hist[::10], "history_full": hist,
                       "mean_last": navg, "theta_mean": [round(x, 2) for x in mean], "theta_mean_int": [int(round(x)) for x in mean]}
                with open(a.out + ".tmp", "w") as fh: json.dump(out, fh)
                os.replace(a.out + ".tmp", a.out)                      # atomic: an interrupted run leaves the last complete state
                print(f"iter {k + 1}/{N}  games {(k + 1) * a.pairs * 2}  " + "  ".join(f"{n}={x:.1f}" for n, x in zip(names, theta))
                      + (f"  fails={fails}" if fails else "") + f"  ({time.time() - t0:.0f} s)", flush=True)
    navg = min(len(hist), a.avg_last if a.avg_last > 0 else max(1, len(hist) // 5))
    if hist:
        mean = [sum(h[i] for h in hist[-navg:]) / navg for i in range(len(names))]
        print("final  " + "  ".join(f"{n}={x:.1f}" for n, x in zip(names, theta)), flush=True)
        print(f"mean of the last {navg} iterations  " + "  ".join(f"{n}={x:.1f}" for n, x in zip(names, mean))
              + "   (verify: tests/match.py --optA \"" + opts(names, mean) + "\")", flush=True)
    print("SPSADONE", flush=True)


if __name__ == "__main__":
    main()
