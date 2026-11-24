#!/usr/bin/env python3
"""Wiring regression test of the tactics suite.  ~20 s.  usage: python3 tactics/tools/selftest.py [--engine ./zeval]   (or ./tactics.sh selftest)
It does NOT judge engine strength; it proves that the integration is intact:
  1 data        every file of suites/, suites_epd/ and reference/ matches tactics/SHA256SUMS.txt
  2 positions   all 1146 positions are legal, every bm / am move is legal in its position (python-chess)
  3 wac         suites/wac.jsonl comes from the same file as tests/wac.epd of the engine package (bit-identical EPD)
  4 analysis    analyze.py reproduces the recorded analysis (reference/analysis_1000ms.txt) character for character
  5 rebuild     build_suites.py regenerates the shipped suites/*.jsonl bit for bit from suites_epd/ (into a temp copy)
  6 runner      run_suite.py on the engine: 3 positions of two suites, every record complete, bestmove legal, resume skips finished work
  7 ab          two result files -> analyze.py with --ref none works, a suite that was not run is skipped
  8 guard       a result file of another binary is refused by tactics.py, --fresh keeps the old file as .bak
  9 binary      if the engine is the shipped ./zeval(.exe): SHA-256 matches SHA256SUMS.txt
Nothing is written into tactics/results (a temp directory is used)."""
import argparse, hashlib, json, os, shutil, subprocess, sys, tempfile
import chess

HERE = os.path.dirname(os.path.abspath(__file__)); TAC = os.path.dirname(HERE); ROOT = os.path.dirname(TAC)
sys.path.insert(0, HERE)
fails = []


def ok(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg, flush=True)
    if not cond: fails.append(msg)


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f: h.update(f.read())
    return h.hexdigest()


EXE = "zeval.exe" if os.name == "nt" else "zeval"           # the root of the checkout, else its dist/ folder (build/build.sh, build\build.bat)
DEF_ENGINE = next((p for p in (os.path.join(ROOT, EXE), os.path.join(ROOT, "dist", EXE)) if os.path.isfile(p)), os.path.join(ROOT, EXE))
ap = argparse.ArgumentParser(); ap.add_argument("--engine", default=DEF_ENGINE); a = ap.parse_args()

# 1 data hashes
sums = os.path.join(TAC, "SHA256SUMS.txt"); bad = []; n = 0
for l in open(sums):
    if not l.strip(): continue
    h, rel = l.split(None, 1); rel = rel.strip(); n += 1
    p = os.path.join(TAC, rel)
    if not os.path.exists(p) or sha(p) != h: bad.append(rel)
ok(not bad and n > 20, f"data: {n} files match tactics/SHA256SUMS.txt" + (f"  (BAD: {bad[:5]})" if bad else ""))

# 2 positions legal
tot = 0; problems = []
for s in ("arasan2026", "iq4", "ecmgcp", "sbd", "classics", "mates", "wac"):
    for l in open(os.path.join(TAC, "suites", s + ".jsonl")):
        tot += 1
        try:
            r = json.loads(l); b = chess.Board(r["fen"])
            if not b.is_valid() or not (r["bm"] or r["am"]) or any(chess.Move.from_uci(m) not in b.legal_moves for m in r["bm"] + r["am"]): problems.append((s, r["id"]))
        except Exception: problems.append((s, l[:40]))
ok(tot == 1146 and not problems, f"positions: {tot} positions, all legal with legal bm/am moves" + (f"  (BAD: {problems[:3]})" if problems else ""))

# 3 wac identical to the engine package's tests/wac.epd
w1, w2 = os.path.join(ROOT, "tests", "wac.epd"), os.path.join(TAC, "suites_epd", "wacnew.epd")
ok(os.path.exists(w1) and sha(w1) == sha(w2), "wac: tactics/suites_epd/wacnew.epd is bit-identical to tests/wac.epd")

# 4 analysis reproduces the recorded one
r = subprocess.run([sys.executable, "-I", os.path.join(HERE, "analyze.py"), "1000ms", "--a", "C6", "--b", "DEF", "--ref", "SF16", "--dir", os.path.join(TAC, "reference")], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
want = open(os.path.join(TAC, "reference", "analysis_1000ms.txt"), encoding="utf-8").read()
ok(r.returncode == 0 and r.stdout.decode().replace("\r\n", "\n") == want.replace("\r\n", "\n"), "analysis: analyze.py reproduces reference/analysis_1000ms.txt exactly")

with tempfile.TemporaryDirectory() as tmp:
    # 5 rebuild of the suites from the EPD files
    t2 = os.path.join(tmp, "tactics"); os.makedirs(os.path.join(t2, "tools")); os.makedirs(os.path.join(t2, "suites"))
    shutil.copytree(os.path.join(TAC, "suites_epd"), os.path.join(t2, "suites_epd")); shutil.copy(os.path.join(HERE, "build_suites.py"), os.path.join(t2, "tools"))
    rb = subprocess.run([sys.executable, "-I", os.path.join(t2, "tools", "build_suites.py")], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    same = rb.returncode == 0 and all(os.path.exists(os.path.join(t2, "suites", f)) and sha(os.path.join(t2, "suites", f)) == sha(os.path.join(TAC, "suites", f)) for f in os.listdir(os.path.join(TAC, "suites")))
    ok(same, "rebuild: build_suites.py regenerates all suites/*.jsonl and _summary.json bit for bit")

    # 6 runner
    out = os.path.join(tmp, "T_100ms.jsonl")
    cmd = [sys.executable, os.path.join(HERE, "run_suite.py"), "T", a.engine, "--movetime", "100", "--suites", "mates,wac", "--limit", "3", "--out", out]
    r1 = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    recs = [json.loads(l) for l in open(out)] if os.path.exists(out) else []
    keys = {"suite", "id", "fen", "bm", "am", "best", "solved", "tts", "depth", "nodes", "nps", "score", "wall"}
    legal = True
    for x in recs:
        try: legal &= chess.Move.from_uci(x["best"]) in chess.Board(x["fen"]).legal_moves
        except Exception: legal = False
    ok(r1.returncode == 0 and len(recs) == 6 and all(keys <= set(x) for x in recs) and legal and all(x["depth"] > 0 for x in recs),
       f"runner: 6 records, all fields present, every bestmove legal, depth > 0  ({a.engine})")
    r2 = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    ok(r2.returncode == 0 and b"0 positions to run (6 already done)" in r2.stdout and sum(1 for _ in open(out)) == 6, "runner: second run resumes and does nothing (6 already done)")

    # 7 ab analysis with a copy as engine B, no reference
    shutil.copy(out, os.path.join(tmp, "U_100ms.jsonl"))
    ra = subprocess.run([sys.executable, "-I", os.path.join(HERE, "analyze.py"), "100ms", "--a", "T", "--b", "U", "--ref", "none", "--dir", tmp], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    txt = ra.stdout.decode()
    ok(ra.returncode == 0 and "mates" in txt and "wac" in txt and "sbd" not in txt and "+0 (0/0" in txt, "ab: analyze.py compares two result files, skips suites that were not run, works without a reference engine")

    # 8 binary guard
    import tactics as T
    T.RES = tmp; f = T.engine_file(a.engine)
    if f:
        o = os.path.join(tmp, "G_100ms.jsonl"); open(o, "w").write("{}\n"); json.dump({"sha256": "0" * 64}, open(o[:-6] + ".meta.json", "w"))
        import contextlib, io
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            try: T.check_meta(o, a.engine, 100, False); refused = False
            except SystemExit: refused = True
            T.check_meta(o, a.engine, 100, True)
        baks = [x for x in os.listdir(tmp) if x.startswith("G_100ms.jsonl.bak")]
        ok(refused and len(baks) == 1 and not os.path.exists(o), "guard: result file of another binary refused; --fresh keeps the old file as .bak and starts again")
    else:
        ok(False, f"guard: engine '{a.engine}' is not a file, cannot test")

# 9 shipped binary
sumf = os.path.join(ROOT, "SHA256SUMS.txt"); f = os.path.abspath(a.engine)
if os.path.exists(sumf) and f in (os.path.join(ROOT, "zeval"), os.path.join(ROOT, "zeval.exe")):
    want = dict((l.split()[1], l.split()[0]) for l in open(sumf) if len(l.split()) == 2)
    ok(want.get(os.path.basename(f)) == sha(f), f"binary: {os.path.basename(f)} matches SHA256SUMS.txt")
elif not os.path.exists(sumf):
    print("skip binary: no SHA256SUMS.txt (a source checkout: only a release package lists the hashes of its binaries)")
else:
    print("skip binary: the engine is not the zeval / zeval.exe of this folder")

print("\nSELFTEST PASSED" if not fails else f"\nSELFTEST FAILED ({len(fails)}): " + "; ".join(fails))
sys.exit(1 if fails else 0)
