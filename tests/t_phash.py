#!/usr/bin/env python3
"""The pawn cache (src/eval.asm, EV_PHASH) and the attack maps (EV_ATTMAPS) do not change the search.

Builds three single-unit engines with tests/build1.sh (needs nasm + ld, Linux): the default, -DEV_PHASH=1 and -DEV_PHASH=1 -DEV_ATTMAPS=1 -DEVALPROF,
runs the golden searches of tests/golden_depth8.json with each and requires the same node counts and best moves (t_golden.py: IDENTICAL),
then prints the pawn cache probes / hits of a depth-12 search from the start position (console command "evalprof").
A second, smaller cache (-DEV_PHBITS=4: 16 entries, many collisions and replacements) must give the same result too.
usage: python3 tests/t_phash.py            about 1-2 minutes.   Exit code 0 = PASSED, 1 = a build differs, 2 = nasm / ld missing."""
import os, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if not (shutil.which("nasm") and shutil.which("ld")):
    print("SKIP t_phash: nasm / ld not found"); sys.exit(2)
VARIANTS = [("default", []), ("phash", ["-DEV_PHASH=1"]), ("phash+maps", ["-DEV_PHASH=1", "-DEV_ATTMAPS=1", "-DEVALPROF"]),
            ("phash16", ["-DEV_PHASH=1", "-DEV_PHBITS=4"])]
ok = True
with tempfile.TemporaryDirectory() as d:
    for name, flags in VARIANTS:
        exe = os.path.join(d, "chess_" + name.replace("+", "_"))
        r = subprocess.run(["sh", os.path.join(HERE, "build1.sh"), exe] + flags, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if r.returncode:
            print(f"FAIL {name}: build failed\n{r.stdout.decode()[-800:]}"); ok = False; continue
        out = subprocess.run([sys.executable, "-B", os.path.join(HERE, "t_golden.py"), exe], stdout=subprocess.PIPE).stdout.decode().strip().splitlines()
        last = out[-1] if out else "(no output)"
        same = "IDENTICAL" in last
        print(("PASS " if same else "FAIL ") + f"{name:11s} {last}")
        ok = ok and same
        if "-DEVALPROF" in flags:
            o = subprocess.run([exe], input=b"uci\nposition startpos\ngo depth 12\nevalprof\nquit\n", stdout=subprocess.PIPE, timeout=300).stdout.decode()
            line = [l for l in o.splitlines() if l.startswith("evalprof")]
            if line:
                f = line[-1].split()
                probes, hits = int(f[f.index("probes") + 1]), int(f[f.index("hits") + 1])
                print(f"     pawn cache, start position depth 12: {probes} probes, {hits} hits ({100.0 * hits / max(probes, 1):.1f}%)")
print("ALL PASSED" if ok else "FAILED")
sys.exit(0 if ok else 1)
