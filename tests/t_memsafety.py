#!/usr/bin/env python3
"""Memory-safety regression tests: input that used to write or read outside the engine's arrays must not crash it.   About 20 seconds.   No python-chess needed.
  ENGINE=./zeval python3 tests/t_memsafety.py          (Windows: set ENGINE=zeval.exe)

  1. game history longer than the undo stack (4096 plies, src/p1_data.asm MAXGAME): "position startpos moves <N plies>" followed by a search.  The history used
     to run into the shared attack tables behind it (crash from about 4100 plies).  The position reached after a long game must equal the one reached directly.
  2. perft with an absurd depth: the depth is limited to the ply arrays (the run itself is stopped by this script).
  3. FEN with a malformed or impossible en-passant field (file outside a..h, rank outside 3 / 6 for the side to move): the field is ignored.
  4. 300 malformed FEN strings (fixed seed: random character changes, truncation, extra fields) followed by a short search.
Every case runs in a fresh engine process.
Exit code 0 = ALL PASSED, 1 = a check failed (a crash, a hang or a wrong position), 2 = the engine could not be started."""
import os, random, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _eng import ENGINE

def run(script, timeout=60):
    """-> (returncode | 'timeout', stdout)"""
    try:
        p = subprocess.Popen(ENGINE, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError as e:
        sys.stderr.write(f"ERROR: cannot start the engine {ENGINE}: {e}\n"); sys.exit(2)
    try:
        out, _ = p.communicate(script.encode(), timeout=timeout)
        return p.returncode, out.decode(errors="replace")
    except subprocess.TimeoutExpired:
        p.kill(); out, _ = p.communicate()
        return "timeout", out.decode(errors="replace")

fails = 0
def check(name, cond, detail=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + str(detail))); fails += (not cond)

rc, out = run("uci\nisready\nquit\n", 20)
if rc != 0 or "uciok" not in out:
    sys.stderr.write(f"ERROR: the engine {' '.join(ENGINE)} does not answer 'uci' (return code {rc})\n"); sys.exit(2)

def fen_of(out):
    for l in out.splitlines():
        if l.startswith("Fen: "): return " ".join(l[5:].split()[:4])      # placement, side, castling, ep (no move counters)
    return None

# 1. long game history
shuffle = ("g1f3 g8f6 f3g1 f6g8 " * 1600).split()
short = run("uci\nposition startpos moves e2e4 e7e5 g1f3\nd\nquit\n")[1]
for n in (3900, 4100, 6000):
    rc, out = run("uci\nposition startpos moves " + " ".join(shuffle[:n]) + " e2e4 e7e5 g1f3\nd\ngo depth 8\nquit\n")
    check(f"{n}-ply game, then search: exit 0, a bestmove", rc == 0 and "bestmove" in out, f"rc={rc}")
    check(f"{n}-ply game: same position as the short game", fen_of(out) == fen_of(short), (fen_of(out), fen_of(short)))
rc, out = run("uci\nposition startpos moves " + " ".join(shuffle[:5000]) + "\ngo depth 6\nisready\nquit\n")
check("5000 plies without a final pawn move (50-move draw everywhere): search ends normally", rc == 0 and "bestmove" in out, f"rc={rc}")

# 2. perft depth far beyond the ply arrays: must neither crash nor corrupt (it runs until stopped here)
rc, out = run("uci\nposition startpos\nperft 100000\n", timeout=4)
check("perft 100000: no crash (still running after 4 s)", rc == "timeout", f"rc={rc}")
rc, out = run("uci\nposition startpos\nperft 4\nquit\n")
check("perft 4 = 197281", rc == 0 and "Nodes searched: 197281" in out, f"rc={rc}")

# 3. en-passant field
base = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR %s KQkq %s 0 1"
bad = 0
for side in ("w", "b"):
    for ep in ("k", "zz", "e9", "e0", "a1", "h8", "e4", "e5", "ee", "9", "e", "-", "e3", "e6"):
        rc, out = run("uci\nposition fen " + base % (side, ep) + "\ngo depth 4\nquit\n")
        if rc != 0 or "bestmove" not in out: bad += 1; print("   crash / no bestmove for side", side, "ep", repr(ep), "rc", rc)
check("28 en-passant fields (valid and malformed) x both sides", bad == 0, f"{bad} failed")
rc, out = run("uci\nposition fen rnbqkbnr/ppp1p1pp/8/3pPp2/8/8/PPPP1PPP/RNBQKBNR w KQkq f6 0 3\nperft 3\nquit\n")
check("a valid en-passant square still works (perft 3 = 21637)", rc == 0 and "Nodes searched: 21637" in out, f"rc={rc}")

# 4. malformed FEN strings
random.seed(20240229)
starts = ["rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
          "r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1",
          "8/2p5/3p4/KP5r/1R3p1k/8/4P1P1/8 w - - 0 1"]
alphabet = "0123456789/pnbrqkPNBRQK -wbKQkqabcdefgh!\t\x01"
def mutate(f):
    k = random.randint(0, 5)
    if k == 0:
        c = list(f)
        for _ in range(random.randint(1, 4)): c[random.randrange(len(c))] = random.choice(alphabet)
        return "".join(c)
    if k == 1: return f[:random.randrange(1, len(f))]
    if k == 2: return f + " " + str(random.choice([99999999999, -1, 2**31, 0, 300]))
    if k == 3: return f.replace("/", "//", random.randint(1, 3))
    if k == 4: return "/".join(f.split("/")[:random.randint(1, 7)]) + " w - - 0 1"
    p = f.split(" "); p[3] = random.choice(["a1", "h8", "e9", "zz", "e3e3", "-", "e4", "a8", "k", "w"]); return " ".join(p)
bad = []
for _ in range(300):
    f = mutate(random.choice(starts))
    rc, out = run("uci\nposition fen " + f + "\ngo depth 3\nquit\n", 15)
    if rc != 0: bad.append((rc, f))
check("300 malformed FEN strings: no crash, no hang", not bad, bad[:3])

print("ALL PASSED" if not fails else f"{fails} CHECK(S) FAILED")
sys.exit(1 if fails else 0)
