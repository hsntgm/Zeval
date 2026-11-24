#!/usr/bin/env python3
"""Candidate matrix: every candidate against the default build on several opening seeds, one match after the other, with a summary.

usage (from the package root, Windows or Linux):
  python tests/cand_matrix.py [NAME ...] [--seeds 12001 12002] [--games 1000] [--tc 3+0.03] [--workers 10] [--base zeval.exe]

  NAME       candidate names (candidates/zeval_NAME.exe, or candidates/zeval_NAME on Linux).  Default: every candidates/zeval_*
             file, in the order of --order when given.
  --seeds    opening seeds.  Each seed draws its own sample of start positions (tests/match.py: the book tests/books/noob_3moves.epd
             when it is there, else random openings cached in tests/openings_<seed>.json), so every candidate plays the same openings on
             the same seed.  Two seeds are two independent samples.
  --games    games per candidate and seed (2 per opening: colours swapped).
  --workers  games in parallel.  One engine searches at a time per game, so logical cores - 2 keeps a quiet machine busy.
  --out      results folder (default results/matrix): one log and one games file per match, SUMMARY.txt and summary.csv.

Each match is tests/match.py with a fixed number of games (no SPRT stop), Threads = 1, real clock.  The matrix can be stopped with
Ctrl+C and started again with the same arguments: finished matches are read from their logs, an unfinished one is played again.
The summary is printed and written after every match: per seed and over all seeds together (the pentanomial counts of the pairs
added up), Elo with the 95 % half-width and LOS (probability that the candidate is stronger than the base).
"""
import argparse, glob, hashlib, math, os, re, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import sprt                                                            # noqa: E402  (tests/sprt.py: the same Elo / error as match.py)

FINAL = re.compile(r"^FINAL games=(\d+) \+(\d+) =(\d+) -(\d+) pairs=\[([\d, ]+)\] Elo=([+-]?[\d.]+) \+-([\d.]+)")


def exe_of(name):
    for p in (os.path.join(ROOT, "candidates", f"zeval_{name}.exe"), os.path.join(ROOT, "candidates", f"zeval_{name}")):
        if os.path.isfile(p):
            return p
    return None


def sha8(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for blk in iter(lambda: fh.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()[:8]


def read_final(log):
    """(games, pentanomial counts) of a finished match log, else None"""
    try:
        with open(log, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                m = FINAL.match(line.strip())
                if m:
                    return int(m.group(1)), [int(x) for x in m.group(5).split(",")]
    except OSError:
        pass
    return None


def los(elo, err):
    if err <= 0 or err >= 999:
        return float("nan")
    se = err / 1.96
    return 0.5 * (1.0 + math.erf(elo / (se * math.sqrt(2.0))))


def fmt(p):
    if not p or sum(p) == 0:
        return "-"
    e, err = sprt.elo_and_error(p, sprt.PENTA_X)
    return f"{e:+6.1f} +-{err:4.1f}"


def summary(a, names, done, hashes, base_hash, out):
    seeds = a.seeds
    lines = [f"Zeval candidate matrix: tc {a.tc}, {a.games} games per seed, {a.workers} workers, base {a.base} ({base_hash})",
             f"updated {time.strftime('%Y-%m-%d %H:%M:%S')}; Elo of the candidate against the base, 95 % half-width from the pentanomial pairs",
             ""]
    head = f"{'candidate':<16}{'sha':<10}" + "".join(f"{'seed ' + str(s):<18}" for s in seeds) + f"{'all seeds':<18}{'games':>7}{'LOS':>9}   pairs (0 .. 2 points)"
    lines += [head, "-" * len(head)]
    rows = []
    for n in names:
        tot = [0] * 5
        cells = []
        for s in seeds:
            r = done.get((n, s))
            cells.append(fmt(r[1]) if r else ("running" if (n, s) == done.get("_running") else "-"))
            if r:
                tot = [x + y for x, y in zip(tot, r[1])]
        g = 2 * sum(tot)
        if g:
            e, err = sprt.elo_and_error(tot, sprt.PENTA_X)
            l = los(e, err)
        else:
            e, err, l = float("-inf"), 0.0, float("nan")
        rows.append((e, n, cells, tot, g, err, l))
    finished = all((n, s) in done for n in names for s in seeds)
    order = sorted(rows, key=lambda r: -r[0]) if finished else rows
    for e, n, cells, tot, g, err, l in order:
        allc = f"{e:+6.1f} +-{err:4.1f}" if g else "-"
        lstr = f"{100 * l:7.1f} %" if g and l == l else "      - "
        lines.append(f"{n:<16}{hashes.get(n, ''):<10}" + "".join(f"{c:<18}" for c in cells) + f"{allc:<18}{g:>7}{lstr}   {tot if g else ''}")
    lines += ["", "sorted by the result over all seeds" if finished else "in the order of play (sorted when every match is done)",
              "LOS > 97.5 % or < 2.5 %: the interval of 'all seeds' does not contain 0."]
    text = "\n".join(lines) + "\n"
    with open(os.path.join(out, "SUMMARY.txt"), "w", encoding="utf-8") as fh:
        fh.write(text)
    with open(os.path.join(out, "summary.csv"), "w", encoding="utf-8") as fh:
        fh.write("candidate,sha," + ",".join(f"elo_{s},err_{s},pairs_{s}" for s in seeds) + ",elo_all,err_all,games_all,los_all\n")
        for e, n, cells, tot, g, err, l in rows:
            parts = [n, hashes.get(n, "")]
            for s in seeds:
                r = done.get((n, s))
                if r:
                    se, serr = sprt.elo_and_error(r[1], sprt.PENTA_X)
                    parts += [f"{se:.1f}", f"{serr:.1f}", " ".join(map(str, r[1]))]
                else:
                    parts += ["", "", ""]
            parts += [f"{e:.1f}" if g else "", f"{err:.1f}" if g else "", str(g), f"{l:.4f}" if g and l == l else ""]
            fh.write(",".join(parts) + "\n")
    return text


def main():
    ap = argparse.ArgumentParser(description="see the module docstring")
    ap.add_argument("names", nargs="*")
    ap.add_argument("--seeds", type=int, nargs="+", default=[12001, 12002])
    ap.add_argument("--games", type=int, default=1000)
    ap.add_argument("--tc", default="3+0.03")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 4) - 2))
    ap.add_argument("--base", default="zeval.exe" if os.name == "nt" else "zeval")
    ap.add_argument("--out", default=os.path.join("results", "matrix"))
    ap.add_argument("--order", default="",
                    help="comma-separated play order of the default candidate list (names not in it follow, sorted)")
    ap.add_argument("--no-save-games", action="store_true", help="do not keep the games (.jsonl)")
    a = ap.parse_args()
    if a.games < 2 or a.games % 2:
        sys.exit("--games must be even and >= 2")
    os.chdir(ROOT)
    base = os.path.abspath(a.base) if os.path.isfile(a.base) else None
    if not base:
        sys.exit(f"ERROR: base engine {a.base} not found (run from the package root)")
    names = a.names
    if not names:
        found = sorted({re.sub(r"\.exe$", "", os.path.basename(p))[6:] for p in glob.glob(os.path.join("candidates", "zeval_*"))
                        if os.path.isfile(p) and (p.endswith(".exe") or os.name != "nt")})
        pref = [n for n in a.order.split(",") if n in found]
        names = pref + [n for n in found if n not in pref]
    missing = [n for n in names if not exe_of(n)]
    if missing:
        sys.exit("ERROR: no candidate binary for " + ", ".join(missing))
    if not names:
        sys.exit("ERROR: no candidate in candidates/")
    os.makedirs(a.out, exist_ok=True)
    hashes = {n: sha8(exe_of(n)) for n in names}
    base_hash = sha8(base)
    jobs = [(n, s) for n in names for s in a.seeds]
    done = {}
    for n, s in jobs:
        r = read_final(os.path.join(a.out, f"{n}_{s}.log"))
        if r and r[0] == a.games:
            done[(n, s)] = r
    left = [j for j in jobs if j not in done]
    print(f"{len(names)} candidates x {len(a.seeds)} seeds x {a.games} games = {len(jobs) * a.games} games; "
          f"{len(jobs) - len(left)} matches already done, {len(left)} to play; tc {a.tc}, {a.workers} workers", flush=True)
    print(summary(a, names, done, hashes, base_hash, a.out), flush=True)
    t_start = time.time()
    for k, (n, s) in enumerate(left, 1):
        log = os.path.join(a.out, f"{n}_{s}.log")
        games = os.path.join(a.out, f"{n}_{s}.jsonl")
        for p in (log, games):                                         # an unfinished match is played again from the start
            if os.path.exists(p):
                os.remove(p)
        cmd = [sys.executable, os.path.join("tests", "match.py"), exe_of(n), base, "--tc", a.tc, "--workers", str(a.workers),
               "--games", str(a.games), "--nosprt", "--seed", str(s)]
        if not a.no_save_games:
            cmd += ["--save-games", games]
        print(f"\n[{k}/{len(left)}] {n} vs base, seed {s}: {' '.join(cmd)}", flush=True)
        done["_running"] = (n, s)
        t0 = time.time()
        with open(log, "w", encoding="utf-8") as lf:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
            try:
                for line in proc.stdout:
                    lf.write(line)
                    lf.flush()
                    line = line.rstrip()
                    if line.startswith("games="):
                        print("\r  " + line[:150].ljust(150), end="", flush=True)
                    else:
                        print("\n  " + line if line.startswith("FINAL") else "  " + line, flush=True)
                proc.wait()
            except KeyboardInterrupt:
                proc.terminate()
                print("\nstopped: start the same command again to go on (this match is played again)", flush=True)
                sys.exit(1)
        del done["_running"]
        r = read_final(log)
        if not r or r[0] != a.games:
            print(f"\nERROR: {n} seed {s} did not finish (exit code {proc.returncode}); see {log}", flush=True)
            continue
        done[(n, s)] = r
        dt = time.time() - t0
        rest = (time.time() - t_start) / k * (len(left) - k)
        print(f"  {dt / 60:.1f} min; about {rest / 3600:.1f} h left\n", flush=True)
        print(summary(a, names, done, hashes, base_hash, a.out), flush=True)
    print(f"done: {os.path.join(a.out, 'SUMMARY.txt')}, {os.path.join(a.out, 'summary.csv')}")


if __name__ == "__main__":
    main()
