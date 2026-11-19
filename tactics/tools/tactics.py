#!/usr/bin/env python3
"""Zeval tactics suite - one front end for everything.  Normally started through ./tactics.sh (Linux / WSL / macOS) or tactics.bat (Windows).

  check                      environment: python, python-chess, the engine answers UCI, suite files intact
  selftest                   the wiring regression test (tools/selftest.py): data hashes, every bm/am legal, runner + analysis reproduce the recorded outputs
  smoke    [-e ENGINE]       ~1 min     wac + mates @ 200 ms   : "did I break tactics?" tripwire after any code change
  standard [-e ENGINE]       ~11 min    iq4 ecmgcp sbd classics mates @ 1000 ms : the discriminating suites, one engine, compared with the recorded baseline
  full     [-e ENGINE]       ~19 min    all 7 suites @ 1000 ms (adds arasan2026 and the saturated WAC); the exact protocol of the recorded baseline
  ab  ENGINE_A ENGINE_B      alternating A/B on one machine (suite by suite, so drift hits both alike) + analysis A - B   [--profile smoke|standard|full]
  analyze TAG NAME_A NAME_B  re-run the analysis of two finished result files (results/NAME_TAG.jsonl)
  variant NAME [flags]       build a Linux variant of the engine (default + extra nasm flags) into tactics/bin/zeval_NAME   (Linux, needs nasm)
  baseline                   print the recorded analysis (C6 and DEF = two earlier builds of this engine, Stockfish 16 as reference)
  list                       suites and finished result files

Results go to tactics/results/<name>_<ms>ms.jsonl (resumable: run the same command again after Ctrl+C).  The default name is <binary>-<first 6 hex of its SHA-256>, so a rebuilt
engine never silently continues the file of the old one; a result file that belongs to another binary is refused unless --fresh (old file kept as .bak) is given."""
import argparse, hashlib, importlib.util, json, os, re, shlex, subprocess, sys, time

TOOLS = os.path.dirname(os.path.abspath(__file__))
TAC = os.path.dirname(TOOLS)                      # .../tactics
ROOT = os.path.dirname(TAC)                       # bundle root
SUITES = os.path.join(TAC, "suites"); RES = os.path.join(TAC, "results"); REFDIR = os.path.join(TAC, "reference")
PROFILES = {                                      # name: (suites, movetime ms, what)
    "smoke":    ("wac,mates", 200, "regression tripwire"),
    "standard": ("iq4,ecmgcp,sbd,classics,mates", 1000, "discriminating suites"),
    "full":     ("arasan2026,iq4,ecmgcp,sbd,classics,mates,wac", 1000, "recorded-baseline protocol"),
}
ALLSUITES = ["arasan2026", "iq4", "ecmgcp", "sbd", "classics", "mates", "wac"]


def die(msg, code=2):
    print("tactics: " + msg, file=sys.stderr); sys.exit(code)


def default_engine():
    for c in (os.environ.get("ENGINE"), os.path.join(ROOT, "zeval.exe" if os.name == "nt" else "zeval")):
        if c: return c
    return "zeval"


def engine_cmd(s): return [s] if os.path.exists(s) else shlex.split(s, posix=(os.name != "nt"))


def engine_file(s):
    for t in engine_cmd(s):
        if os.path.isfile(t): return t
    return None


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""): h.update(blk)
    return h.hexdigest()


def label_for(engine, name):
    if name:
        if not re.fullmatch(r"[A-Za-z0-9_.\-]+", name): die("--name may only contain letters, digits, _ . -")
        return name
    f = engine_file(engine)
    base = re.sub(r"[^A-Za-z0-9_.\-]", "_", os.path.splitext(os.path.basename(f or engine.split()[-1]))[0]) or "engine"
    return f"{base}-{sha256(f)[:6]}" if f else base


def nsuite(s): return sum(1 for _ in open(os.path.join(SUITES, s + ".jsonl")))


def py_has(mod): return importlib.util.find_spec(mod) is not None


def uci_ok(engine):
    try:
        p = subprocess.run(engine_cmd(engine), input=b"uci\nisready\nquit\n", stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=15)
        out = p.stdout.decode(errors="replace"); return "uciok" in out and "readyok" in out, out
    except Exception as e: return False, str(e)


# ------------------------------------------------------------------------------------------------ run one engine on suites
def check_meta(out_file, engine, movetime, fresh):
    """result file <-> binary guard: the .meta.json next to it records the SHA-256 of the binary; a different binary may not continue the file"""
    meta = out_file[:-6] + ".meta.json"; f = engine_file(engine); sha = sha256(f) if f else None
    if fresh and os.path.exists(out_file):
        bak = f"{out_file}.bak.{int(time.time())}"; os.replace(out_file, bak); print(f"--fresh: old results kept as {os.path.relpath(bak, ROOT)}")
        if os.path.exists(meta): os.remove(meta)
    if os.path.exists(meta) and sha:
        old = json.load(open(meta)).get("sha256")
        if old and old != sha:
            die(f"{os.path.relpath(out_file, ROOT)} was produced by another binary (sha {old[:12]}..., this one {sha[:12]}...).\n"
                f"         use another --name, or --fresh to start the file again (the old one is kept as .bak)")
    elif os.path.exists(out_file) and os.path.getsize(out_file) and sha and not os.path.exists(meta):
        die(f"{os.path.relpath(out_file, ROOT)} exists without a .meta.json (older run): cannot tell which binary made it. Use another --name or --fresh.")
    json.dump({"engine": engine, "sha256": sha, "movetime": movetime, "created": time.strftime("%Y-%m-%d %H:%M:%S")}, open(meta, "w"))


def run_engine(engine, label, suites, movetime, hash_mb, fresh, limit=0):
    os.makedirs(RES, exist_ok=True)
    out = os.path.join(RES, f"{label}_{movetime}ms.jsonl")
    check_meta(out, engine, movetime, fresh)
    cmd = [sys.executable, os.path.join(TOOLS, "run_suite.py"), label, engine, "--movetime", str(movetime), "--suites", suites, "--hash", str(hash_mb), "--out", out]
    if limit: cmd += ["--limit", str(limit)]
    r = subprocess.run(cmd)
    if r.returncode: die(f"run_suite failed for {label} (exit {r.returncode})", 1)
    return out


def estimate(suites, movetime, engines=1):
    n = sum(nsuite(s) for s in suites.split(",")); sec = n * movetime / 1000.0 * 1.02 * engines
    return n, sec


def announce(what, suites, movetime, engines=1):
    n, sec = estimate(suites, movetime, engines)
    print(f"== {what}: {n} positions x {movetime} ms x {engines} engine(s)  ~ {sec / 60:.0f} min  (one engine at a time, Threads=1; do not run other heavy jobs meanwhile)", flush=True)


def analyze(tag, a, b, dir_a=None, dir_b=None, ref=True, extra=()):
    cmd = [sys.executable, os.path.join(TOOLS, "analyze.py"), tag, "--a", a, "--b", b, "--dir", RES]
    if dir_a: cmd += ["--dir-a", dir_a]
    if dir_b: cmd += ["--dir-b", dir_b]
    if ref and os.path.exists(os.path.join(REFDIR, f"SF16_{tag}.jsonl")): cmd += ["--ref", "SF16", "--dir-ref", REFDIR]
    else: cmd += ["--ref", "none"]
    return subprocess.run(cmd + list(extra)).returncode


# ------------------------------------------------------------------------------------------------ commands
def cmd_profile(a):
    suites0, mt0, what = PROFILES[a.cmd]
    suites = a.suites or suites0; movetime = a.movetime or mt0
    for s in suites.split(","):
        if s not in ALLSUITES: die(f"unknown suite {s} (have {', '.join(ALLSUITES)})")
    ok, _ = uci_ok(a.engine)
    if not ok: die(f"engine '{a.engine}' does not answer uci/isready (use -e PATH; run './tactics.sh check')")
    label = label_for(a.engine, a.name)
    announce(f"{a.cmd} ({what})", suites, movetime)
    print(f"   engine {a.engine}  name {label}", flush=True)
    t0 = time.time(); run_engine(a.engine, label, suites, movetime, a.hash, a.fresh)
    print(f"\ndone in {(time.time() - t0) / 60:.1f} min -> tactics/results/{label}_{movetime}ms.jsonl")
    if movetime == 1000 and os.path.exists(os.path.join(REFDIR, "C6_1000ms.jsonl")):
        if not py_has("chess"): print("(python-chess missing: pip install chess -> then the comparison with the recorded baseline is printed; ./tactics.sh analyze 1000ms %s C6 would also work)" % label); return
        print("\n== comparison with the RECORDED baseline (C6 = the current default engine; recorded on ANOTHER machine, 1 core, ONE run: informational only,\n"
              "   for a real A/B run both builds on THIS machine: ./tactics.sh ab ENGINE_A ENGINE_B)\n")
        analyze("1000ms", label, "C6", dir_b=REFDIR)
    else:
        print("(no recorded baseline for this time: compare two of your own runs with ./tactics.sh ab ...  or  ./tactics.sh analyze %dms NAME_A NAME_B)" % movetime)


def cmd_ab(a):
    suites0, mt0, what = PROFILES[a.profile]
    suites = a.suites or suites0; movetime = a.movetime or mt0
    for e in (a.engine_a, a.engine_b):
        ok, _ = uci_ok(e)
        if not ok: die(f"engine '{e}' does not answer uci/isready")
    la, lb = label_for(a.engine_a, a.name_a), label_for(a.engine_b, a.name_b)
    if la == lb: die("both engines get the same name: they are the same binary or --name-a / --name-b are equal")
    announce(f"A/B {a.profile} ({what})", suites, movetime, 2)
    print(f"   A = {a.engine_a}  ({la})\n   B = {a.engine_b}  ({lb})", flush=True)
    t0 = time.time()
    for s in suites.split(","):                                      # alternate per suite: slow drift on a shared machine hits both alike
        print(f"\n-- suite {s}", flush=True)
        run_engine(a.engine_a, la, s, movetime, a.hash, a.fresh and s == suites.split(",")[0])
        run_engine(a.engine_b, lb, s, movetime, a.hash, a.fresh and s == suites.split(",")[0])
    print(f"\ndone in {(time.time() - t0) / 60:.1f} min\n")
    if not py_has("chess"): die("python-chess missing (pip install chess): the analysis needs it; results are saved, run ./tactics.sh analyze %dms %s %s afterwards" % (movetime, la, lb), 1)
    analyze(f"{movetime}ms", la, lb)


def cmd_analyze(a):
    if not py_has("chess"): die("python-chess missing: pip install chess")
    sys.exit(analyze(a.tag, a.name_a, a.name_b, dir_a=a.dir_a, dir_b=a.dir_b))


def cmd_variant(a):
    if os.name == "nt": die("variant builds are Linux only (nasm + ld); on Windows build candidates with build\\build.bat and pass the exe to ab")
    sys.exit(subprocess.run(["sh", os.path.join(TOOLS, "build_variant.sh"), a.name, " ".join(a.flags)]).returncode)


def cmd_baseline(a):
    print(open(os.path.join(REFDIR, "analysis_1000ms.txt"), encoding="utf-8").read())
    print("Recorded: C6 = an earlier build of this engine (binary SHA-256 634a40bf..., evaluation material + PST only), 1000 ms / position, Threads=1, Hash=64, one core, ONE run.")


def cmd_list(a):
    summ = json.load(open(os.path.join(SUITES, "_summary.json")))
    print("suites (tactics/suites/*.jsonl):")
    for s in ALLSUITES: print(f"  {s:11s} {nsuite(s):4d} positions   {summ[s]['desc']}")
    print("\nfinished result files (tactics/results/):")
    fs = sorted(x for x in os.listdir(RES) if x.endswith(".jsonl")) if os.path.isdir(RES) else []
    for x in fs: print(f"  {x:40s} {sum(1 for _ in open(os.path.join(RES, x))):5d} positions")
    if not fs: print("  (none yet)")
    print("\nrecorded baseline (tactics/reference/): " + ", ".join(sorted(x for x in os.listdir(REFDIR) if x.endswith(".jsonl"))))


def cmd_check(a):
    bad = 0
    def line(ok, msg, fatal=True):
        nonlocal bad
        print(("  ok    " if ok else ("  FAIL  " if fatal else "  warn  ")) + msg)
        if not ok and fatal: bad += 1
    print(f"python {sys.version.split()[0]} ({sys.executable})")
    line(sys.version_info >= (3, 8), "python >= 3.8")
    line(py_has("chess"), "python-chess installed (needed by analyze / selftest / build_suites; pip install chess)", fatal=False)
    ok, out = uci_ok(a.engine); line(ok, f"engine '{a.engine}' answers uci + isready")
    if ok:
        m = re.search(r"id name (.*)", out); print("        " + (m.group(1).strip() if m else "(no id name)"))
        f = engine_file(a.engine)
        if f:
            sha = sha256(f); line(True, f"SHA-256 {sha[:16]}...")
            sums = os.path.join(ROOT, "SHA256SUMS.txt")
            if os.path.exists(sums) and os.path.abspath(f) in (os.path.join(ROOT, "zeval"), os.path.join(ROOT, "zeval.exe")):
                want = dict((l.split()[1], l.split()[0]) for l in open(sums) if len(l.split()) == 2)
                n = os.path.basename(f); line(want.get(n) == sha, f"{n} matches SHA256SUMS.txt (shipped binary)", fatal=False)
    miss = [s for s in ALLSUITES if not os.path.exists(os.path.join(SUITES, s + ".jsonl"))]
    line(not miss, "all 7 suites present (%s)" % (", ".join(miss) or f"{sum(nsuite(s) for s in ALLSUITES)} positions"))
    line(os.path.exists(os.path.join(REFDIR, "C6_1000ms.jsonl")), "recorded baseline present (tactics/reference)")
    try: os.makedirs(RES, exist_ok=True); t = os.path.join(RES, ".w"); open(t, "w").close(); os.remove(t); line(True, "tactics/results writable")
    except Exception as e: line(False, f"tactics/results not writable: {e}")
    print("\nenvironment OK" if not bad else f"\n{bad} problem(s)")
    sys.exit(1 if bad else 0)


def cmd_selftest(a):
    sys.exit(subprocess.run([sys.executable, "-I", os.path.join(TOOLS, "selftest.py"), "--engine", a.engine]).returncode)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)

    def common(p, eng=True):
        if eng: p.add_argument("-e", "--engine", default=default_engine(), help="engine binary or command line (default: ./zeval, zeval.exe on Windows; env ENGINE)")
        p.add_argument("--hash", type=int, default=64, help="Hash MB (default 64, the recorded baseline's value)")
    for nm in PROFILES:
        p = sp.add_parser(nm, help=f"{PROFILES[nm][0]} @ {PROFILES[nm][1]} ms"); common(p)
        p.add_argument("-n", "--name", help="result name (default <binary>-<sha6>)"); p.add_argument("-t", "--movetime", type=int, help="ms per position (default %d)" % PROFILES[nm][1])
        p.add_argument("--suites", help="override the suite list, comma separated"); p.add_argument("--fresh", action="store_true", help="start the result file again (old one kept as .bak)")
        p.set_defaults(fn=cmd_profile)
    p = sp.add_parser("ab", help="alternating A/B of two engines"); p.add_argument("engine_a"); p.add_argument("engine_b"); common(p, eng=False)
    p.add_argument("--profile", choices=list(PROFILES), default="standard"); p.add_argument("--name-a"); p.add_argument("--name-b")
    p.add_argument("-t", "--movetime", type=int); p.add_argument("--suites"); p.add_argument("--fresh", action="store_true"); p.set_defaults(fn=cmd_ab)
    p = sp.add_parser("analyze", help="analysis of two finished result files"); p.add_argument("tag", help="e.g. 1000ms"); p.add_argument("name_a"); p.add_argument("name_b")
    p.add_argument("--dir-a"); p.add_argument("--dir-b"); p.set_defaults(fn=cmd_analyze)
    p = sp.add_parser("variant", help="build tactics/bin/zeval_NAME (Linux)"); p.add_argument("name"); p.add_argument("flags", nargs=argparse.REMAINDER, help="extra nasm flags, quoted or not"); p.set_defaults(fn=cmd_variant)
    for nm, fn, h in (("baseline", cmd_baseline, "print the recorded baseline analysis"), ("list", cmd_list, "suites and result files")):
        p = sp.add_parser(nm, help=h); p.set_defaults(fn=fn)
    for nm, fn, h in (("check", cmd_check, "environment check"), ("selftest", cmd_selftest, "wiring regression test")):
        p = sp.add_parser(nm, help=h); p.add_argument("-e", "--engine", default=default_engine()); p.set_defaults(fn=fn)
    a = ap.parse_args(); a.fn(a)


if __name__ == "__main__":
    main()
