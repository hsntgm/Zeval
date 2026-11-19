#!/usr/bin/env python3
"""Exactness of the division in the evaluation's taper (src/eval.asm, macro DIV1536).

The evaluation divides the tapered numerator n = 64 * mg * ph + sf * eg * (24 - ph) by 1536 with truncation towards zero.  The code does not use
idiv: it computes q = (|n| * EV_RCP) >> EV_RSH and restores the sign.  This script reads EV_RCP / EV_RSH from src/eval.asm and checks
  1. the error bound: with e = EV_RCP * 1536 - 2^EV_RSH (0 <= e), floor(|n| * EV_RCP / 2^EV_RSH) == floor(|n| / 1536) holds for every |n| < 2^EV_RSH / e;
     that bound must cover every int32 numerator (|n| < 2^31), and |n| * EV_RCP must fit in 63 bits;
  2. exhaustively every |n| in 0 .. 2^27 - 1 (the numerator of a real position stays below about 10^8 < 2^27), plus 2 million random int32 values
     of both signs, against Python's exact integer division (truncation towards zero).
Needs numpy (pip install numpy).  About 5 seconds.
Exit code 0 = PASSED, 1 = a check failed, 2 = numpy or the constants are missing."""
import os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
try:
    import numpy as np
except ImportError:
    sys.stderr.write("ERROR: numpy is needed (pip install numpy)\n"); sys.exit(2)
src = open(os.path.join(HERE, "..", "src", "eval.asm")).read()
m1 = re.search(r"^%define\s+EV_RCP\s+(0x[0-9A-Fa-f]+|\d+)", src, re.M)
m2 = re.search(r"^%define\s+EV_RSH\s+(\d+)", src, re.M)
if not (m1 and m2):
    sys.stderr.write("ERROR: EV_RCP / EV_RSH not found in src/eval.asm\n"); sys.exit(2)
RCP, RSH, D = int(m1.group(1), 0), int(m2.group(1)), 1536
fails = 0
def check(name, cond, detail=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + str(detail))); fails += (not cond)

e = RCP * D - (1 << RSH)
check("reciprocal rounds up (0 <= e < 1536)", 0 <= e < D, e)
check("error bound covers every int32 numerator", e == 0 or (1 << RSH) // e >= (1 << 31), (1 << RSH) // max(e, 1))
check("|n| * EV_RCP fits in 63 bits for |n| <= 2^31", (1 << 31) * RCP < (1 << 63))

def q_code(n):                       # the asm: |n| * RCP >> RSH, then the sign
    a = np.abs(n).astype(np.uint64)
    q = ((a * np.uint64(RCP)) >> np.uint64(RSH)).astype(np.int64)
    return np.where(n < 0, -q, q)

def q_exact(n):                      # truncation towards zero
    q = np.abs(n) // D
    return np.where(n < 0, -q, q)

bad = 0
step = 1 << 22
for lo in range(0, 1 << 27, step):
    n = np.arange(lo, lo + step, dtype=np.int64)
    bad += int(np.count_nonzero(q_code(n) != q_exact(n)))
check("exhaustive 0 .. 2^27 - 1", bad == 0, f"{bad} differences")
rng = np.random.default_rng(1536)
n = rng.integers(-(1 << 31) + 1, 1 << 31, 2_000_000, dtype=np.int64)
n[:6] = [-(1 << 31) + 1, (1 << 31) - 1, -1, 1, -1535, -1536]
check("2,000,000 random int32 numerators (both signs)", np.array_equal(q_code(n), q_exact(n)))
print("PASSED" if not fails else f"{fails} CHECK(S) FAILED")
sys.exit(1 if fails else 0)
