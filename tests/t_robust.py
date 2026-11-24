#!/usr/bin/env python3
"""Robustness regression tests: commands queued behind a running search are not lost, go infinite waits
for stop, a 1160-ply position line is not cut, console-mode repetition draw / go infinite, malformed FEN does not crash.   Seconds.   No python-chess needed.
  ENGINE=./zeval python3 tests/t_robust.py          (Windows: set ENGINE=zeval.exe)
Exit code 0 = ALL PASSED, 1 = a check failed (a hung engine is killed and counts as a failed check)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _eng import ENGINE
import subprocess, time
def run(inp, timeout=20, delays=None):
    try:
        p = subprocess.Popen(ENGINE, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    except OSError as e:
        sys.stderr.write(f"ERROR: cannot start the engine {ENGINE}: {e}\n"); sys.exit(2)
    try:
        if delays is None:
            out, _ = p.communicate(inp, timeout=timeout); return out
        for chunk, d in zip(inp, delays):
            p.stdin.write(chunk); p.stdin.flush(); time.sleep(d)
        out, _ = p.communicate(timeout=timeout); return out
    except subprocess.TimeoutExpired:
        p.kill(); out, _ = p.communicate(); return (out or "") + "\n<TIMEOUT: engine killed>\n"
fails = 0
def check(name, cond):
    global fails
    print(("PASS " if cond else "FAIL ") + name); fails += (not cond)

# 1. commands queued behind a running search must not be lost
o = run("uci\nposition startpos\ngo movetime 300\nposition startpos moves e2e4\ngo movetime 300\nquit\n")
check("piped go/position/go -> 2 bestmoves", o.count("bestmove") == 2)
o = run("uci\nposition startpos\ngo depth 8\nposition startpos moves e2e4\ngo depth 8\nposition startpos moves d2d4\ngo depth 8\n")
check("piped, EOF without quit -> 3 bestmoves", o.count("bestmove") == 3)

# 2. go infinite must wait for stop even after a proven mate
o = run(["uci\nposition fen 6k1/5ppp/8/8/8/8/5PPP/R5K1 w - - 0 1\ngo infinite\n", "isready\n", "stop\n", "quit\n"], delays=[1.0, 0.5, 0.3, 0.0])
lines = o.splitlines()
check("go infinite: bestmove only after stop", "bestmove a1a8" in lines and lines.index("readyok") < lines.index("bestmove a1a8"))

# 3. long position line (> 4096 chars) is not cut
mv = ("g1f3 g8f6 f3g1 f6g8 " * 290).strip()
o = run("uci\nposition startpos moves " + mv + "\nd\nquit\n")
check("1160-ply position line fully applied", "Fen: rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 1160 581" in o)

# 4. console mode: draw only on the third occurrence, 'go infinite' must not hang
o = run("position startpos moves g1f3 g8f6 f3g1\nf6g8\nq\n"); check("2nd repetition is not a draw", "Draw by repetition" not in o)
o = run("position startpos moves g1f3 g8f6 f3g1 f6g8 g1f3 g8f6 f3g1\nf6g8\nq\n"); check("3rd repetition is a draw", "Draw by repetition" in o)
t = time.time(); o = run("time 200\ngo infinite\nq\n", timeout=10); check("console go infinite returns", "Engine:" in o and time.time() - t < 5)

# 5. malformed FEN does not crash
for fen in ["rnbqkbnr/pppppppp/9999999999999999999999r/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", "////////////////////////////p w - - 0 1"]:
    try:
        p = subprocess.run(ENGINE, input=f"uci\nposition fen {fen}\ngo depth 3\nquit\n", capture_output=True, text=True, timeout=10); ok = p.returncode == 0
    except subprocess.TimeoutExpired:
        ok = False
    check("malformed FEN survives", ok)
print("FAILED" if fails else "ALL PASSED"); sys.exit(1 if fails else 0)
