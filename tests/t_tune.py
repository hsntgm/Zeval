#!/usr/bin/env python3
"""The tuning options of a -DTUNE build (tools/spsa.py relies on them).

1. Source: every read of the option table (tunetab) marks its slot with TUSE, so that "uci" declares the slot (src/p1_data.asm).
2. Engine: "uci" declares options T<n> with n in 0 .. 84, among them T0 (reverse futility margin, always compiled), and "default"
   shows the current value (setoption name T0 value 77, then uci again).
  sh tests/build1.sh zeval_tune -DTUNE
  ENGINE=./zeval_tune python3 tests/t_tune.py
Exit code 0 = no FAIL.
"""
import os, re, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _eng import ENGINE

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "src")
bad = 0


def chk(name, ok, info=""):
    global bad
    if not ok: bad += 1
    print(("PASS " if ok else "FAIL ") + name + ("" if ok or not info else "  [" + str(info)[:300] + "]"), flush=True)


# ---- 1. source
reads, unmarked = 0, []
for fn in sorted(os.listdir(SRC)):
    if not fn.endswith(".asm"): continue
    lines = open(os.path.join(SRC, fn)).read().split("\n")
    for i, l in enumerate(lines):
        code = l.split(";")[0]
        m = re.search(r"\[tunetab\+(\d+)\*4\]", code)
        if m:
            reads += 1
            near = [x.split(";")[0].strip() for x in lines[max(0, i - 6):i]]
            if f"TUSE {m.group(1)}" not in near: unmarked.append(f"{fn}:{i + 1} T{m.group(1)}")
        elif re.search(r"\[tunetab\+\(%2\)\*4\]", code):
            reads += 1
            near = [x.split(";")[0].strip() for x in lines[max(0, i - 3):i]]
            if "TUSE %2" not in near: unmarked.append(f"{fn}:{i + 1} macro")
chk(f"every read of tunetab is marked with TUSE ({reads} reads)", reads > 0 and not unmarked, ", ".join(unmarked))

# ---- 2. engine
try:
    out = subprocess.run(ENGINE, input="uci\nsetoption name T0 value 77\nuci\nquit\n", capture_output=True, text=True, timeout=30).stdout
except (OSError, subprocess.TimeoutExpired) as ex:
    out = ""; chk("engine answers uci", False, ex)
blocks = out.split("uciok")
first = [l.split() for l in blocks[0].splitlines()] if blocks else []
second = [l.split() for l in blocks[1].splitlines()] if len(blocks) > 1 else []
def slots(lines):
    return {int(t[2][1:]): int(t[t.index("default") + 1]) for t in lines
            if len(t) >= 6 and t[:2] == ["option", "name"] and t[2][:1] == "T" and t[2][1:].isdigit() and "default" in t}
s1, s2 = slots(first), slots(second)
chk("uci declares T options (a -DTUNE build)", bool(s1), "none: build with -DTUNE")
if s1:
    chk(f"declared slots lie in 0 .. 84 ({len(s1)} slots)", all(0 <= n <= 84 for n in s1), sorted(n for n in s1 if not 0 <= n <= 84))
    chk("T0 is declared", 0 in s1)
    chk("the default shown is the current value (T0 77 after setoption)", s2.get(0) == 77, s2.get(0))
    chk("the second uci declares the same slots", set(s1) == set(s2))
print("PASSED" if bad == 0 else f"FAILED ({bad})")
sys.exit(1 if bad else 0)
