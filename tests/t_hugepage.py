#!/usr/bin/env python3
"""TT_HUGE (Linux): is the transposition table backed by transparent huge pages?

The engine maps a table of 2 MB or more on a 2 MB boundary and marks it madvise(MADV_HUGEPAGE) (tt_alloc in src/p5_main.asm).  The script starts the
engine, sets Hash, runs a short search (the table is cleared = touched by ucinewgame) and reads /proc/<pid>/smaps:

  1. for Hash = 2, 16, 64, 256 MB: there is exactly one anonymous mapping of the table's size, it starts on a 2 MB boundary and is THP-eligible;
     with /sys/kernel/mm/transparent_hugepage/enabled = always or madvise most of it is backed by huge pages (AnonHugePages >= half the table:
     the kernel may lack free 2 MB pages, so not all of it is demanded); with "never" only the alignment is checked
  2. Hash = 1 (below 2 MB) takes the plain mmap path: the search still works
  3. 40 Hash changes (1 .. 1024 MB) leave the process size where it was (no leaked mapping: the unaligned head and tail are given back)

Not on Windows / without /proc (exit 0 with SKIP).  A build with -DTT_HUGE=0 fails check 1 (not eligible, no huge pages) unless THP is "always".
usage: python3 tests/t_hugepage.py [engine]        exit code 0 = passed or skipped, 1 = failed, 2 = engine could not be run
"""
import os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _eng import resolve, expect, finish, start, default_engine

HERE = os.path.dirname(os.path.abspath(__file__))
EXE = sys.argv[1] if len(sys.argv) > 1 else (os.environ.get("ENGINE") or default_engine())
if not os.path.isdir("/proc/self") or EXE.lower().endswith(".exe"):
    print("SKIP: needs Linux /proc and the Linux engine"); sys.exit(0)
try: THP = re.search(r"\[(\w+)\]", open("/sys/kernel/mm/transparent_hugepage/enabled").read())[1]
except Exception: THP = "never"
bad = 0
def check(ok, msg):
    global bad
    print(("PASS " if ok else "FAIL ") + msg); bad += (not ok)

p = start(resolve(EXE))
def send(s): p.stdin.write(s + "\n"); p.stdin.flush()
def search():
    send("ucinewgame"); send("isready"); expect(p, "readyok", 60)
    send("position startpos moves e2e4 e7e5"); send("go depth 8"); expect(p, "bestmove", 60)
def table(mb):
    """the anonymous mapping of the table: (start, size, eligible, AnonHugePages bytes) or None"""
    txt = open("/proc/%d/smaps" % p.pid).read(); found = []
    for blk in re.split(r"\n(?=[0-9a-f]+-[0-9a-f]+ )", txt):
        head = blk.split("\n", 1)[0].split()
        a, b = [int(x, 16) for x in head[0].split("-")]
        if b - a == mb << 20 and len(head) < 6:
            el = re.search(r"THPeligible:\s+(\d)", blk); ah = re.search(r"AnonHugePages:\s+(\d+)", blk)
            found.append((a, b - a, el is not None and el[1] == "1", int(ah[1]) << 10 if ah else 0))
    return found
def vmsize(): return int(re.search(r"VmSize:\s+(\d+)", open("/proc/%d/status" % p.pid).read())[1])
try:
    send("uci"); expect(p, "uciok", 30)
    print("transparent huge pages: %s" % THP)
    for mb in (2, 16, 64, 256):
        send("setoption name Hash value %d" % mb); search()
        t = table(mb)
        ok = len(t) == 1 and t[0][0] % (2 << 20) == 0 and t[0][2]
        if ok and THP in ("always", "madvise"): ok = t[0][3] >= (mb << 20) // 2
        check(ok, "1. Hash %4d MB: one mapping of the table, 2 MB aligned, THP-eligible, huge pages %s MB  %s"
              % (mb, (t[0][3] >> 20) if len(t) == 1 else "-", t if not ok else ""))
    send("setoption name Hash value 1"); search(); check(True, "2. Hash 1 MB (plain mmap) searches")
    send("setoption name Hash value 32"); search(); v0 = vmsize()
    for mb in [1, 2, 3, 64, 1024, 2, 128, 1] * 5:
        send("setoption name Hash value %d" % mb); send("isready"); expect(p, "readyok", 60)
    send("setoption name Hash value 32"); search(); v1 = vmsize()
    check(v1 == v0, "3. 40 Hash changes: process size %d kB before, %d kB after" % (v0, v1))
    send("quit"); finish(p, 10)
finally:
    if p.poll() is None: p.kill()
print("ALL PASSED" if not bad else "%d FAILED" % bad); sys.exit(1 if bad else 0)
