#!/usr/bin/env python3
"""UCI output checks: full PV, 'ponder', seldepth / hashfull / tbhits / currmove.   Needs python-chess.

  ENGINE=./zeval python3 tests/t_pv.py            (Windows: set ENGINE=zeval.exe)
Checks (exit code 0 = all passed):
  * every 'info' line of a fixed-depth search has  depth seldepth score nodes nps hashfull tbhits time pv  in that order,
    values in range, and the WHOLE pv is a legal line from the root position (played move by move with python-chess)
  * every search, completed or interrupted (stop / nodes / movetime): bestmove == first move of the last pv, 'ponder' equals pv[1] when the
    pv has one (PV_FINAL: a search that ends inside an iteration with a new best root move prints one more info line)
  * stop in the middle of an iteration, go nodes, go movetime: bestmove and ponder still legal
  * mate scores: the pv has the length the mate score implies (mate N -> 2N-1 plies, or longer, never shorter)
  * promotions / castling / en passant inside a pv are printed in UCI notation and are legal
  * long pv (KQ vs K at depth 30) stays inside the table, no corruption of the following search
  * go infinite for 4.5 s: 'currmove' lines appear only after 3 s, are legal root moves, currmovenumber counts 1,2,3..
  * hashfull grows during a long search and is 0 after 'Clear Hash'
  * no ponder / no pv garbage for a mated or stalemated side, single legal move
"""
import sys, re, time, random, os
import chess
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _eng import ENGINE, PipeReader
import subprocess

class E:
    def __init__(s):
        s.p = subprocess.Popen(ENGINE, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        s.r = PipeReader(s.p.stdout); s.buf = b""; s.lines = []
    def send(s, x):
        s.p.stdin.write((x + "\n").encode()); s.p.stdin.flush()
    def read_until(s, pred, to=30):
        end = time.time() + to; out = []
        while time.time() < end:
            while b"\n" in s.buf:
                l, s.buf = s.buf.split(b"\n", 1); l = l.decode(errors="replace").strip(); out.append(l)
                if pred(l): return out, True
            d = s.r.get(0.05)
            if d == b"": return out, False
            if d: s.buf += d
        return out, False
    def init(s):
        s.send("uci"); s.read_until(lambda l: l == "uciok", 5)
    def go(s, fen_or_moves, cmd, to=60):
        s.send("ucinewgame"); s.send("isready"); s.read_until(lambda l: l == "readyok", 10)
        s.send("position " + fen_or_moves); s.send(cmd)
        return s.read_until(lambda l: l.startswith("bestmove"), to)
    def close(s):
        try: s.send("quit"); s.p.wait(timeout=3)
        except Exception: s.p.kill()

fails = 0; passes = 0
def check(ok, msg):
    global fails, passes
    if ok: passes += 1
    else:
        fails += 1; print("FAIL", msg)

INFO = re.compile(r"^info depth (\d+) seldepth (\d+) score (cp|mate) (-?\d+) nodes (\d+) nps (\d+) hashfull (\d+) tbhits (\d+) time (\d+) pv ((?:[a-h][1-8][a-h][1-8][qrbn]? ?)+)$")

def verify_search(out, board, label, expect_bestmove=True, strict=True):
    """returns (last_info_match, bestmove_line)"""
    infos = [l for l in out if l.startswith("info") and " pv " in l]
    last = None; lastd = 0
    for l in infos:
        m = INFO.match(l)
        check(m is not None, f"{label}: info line format: {l[:160]}")
        if not m: continue
        d, sd, kind, sc, nodes, nps, hf, tb, tm, pv = m.groups()
        d, sd, hf = int(d), int(sd), int(hf)
        check(d > lastd, f"{label}: depth not increasing: {l[:100]}"); lastd = d
        check(sd >= 1, f"{label}: seldepth < 1: {l[:100]}")
        check(0 <= hf <= 1000, f"{label}: hashfull out of range: {l[:100]}")
        check(tb == "0", f"{label}: tbhits: {l[:100]}")
        b = board.copy(); ok = True
        for mv in pv.split():
            try: m_ = chess.Move.from_uci(mv)
            except Exception: ok = False; break
            if m_ not in b.legal_moves: ok = False; break
            b.push(m_)
        check(ok, f"{label}: pv is not a legal line from the root: {l[:200]}  fen={board.fen()}")
        check(len(pv.split()) <= 127, f"{label}: pv too long")
        last = m
    bm = [l for l in out if l.startswith("bestmove")]
    check(len(bm) == 1, f"{label}: expected exactly one bestmove, got {bm}")
    if bm and expect_bestmove:
        t = bm[0].split()
        mv = chess.Move.from_uci(t[1]) if t[1] not in ("0000", "(none)") else None
        check(mv is None or mv in board.legal_moves, f"{label}: illegal bestmove {bm[0]}")
        if "ponder" in t:
            check(len(t) == 4 and t.index("ponder") == 2, f"{label}: bestmove format {bm[0]}")
            b = board.copy()
            if mv: b.push(mv)
            try: pm = chess.Move.from_uci(t[3])
            except Exception: pm = None
            check(pm is not None and pm in b.legal_moves, f"{label}: illegal ponder move {bm[0]}  fen={board.fen()}")
        else:
            check(len(t) == 2, f"{label}: bestmove format {bm[0]}")
        if last and mv and strict:      # interrupted searches may have a newer root line than the last completed iteration
            pv = last.group(10).split()
            check(pv[0] == t[1], f"{label}: bestmove {t[1]} != pv[0] {pv[0]}")
            if "ponder" in t and len(pv) > 1:
                check(pv[1] == t[3], f"{label}: ponder {t[3]} != pv[1] {pv[1]}")
    return last, bm[0] if bm else None

random.seed(11)
e = E(); e.init()

# ---- 1. random positions, fixed depth
fens = ["rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        "r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1",       # castling both sides
        "8/2p5/3p4/KP5r/1R3p1k/8/4P1P1/8 w - - 0 1",
        "r3k2r/Pppp1ppp/1b3nbN/nP6/BBP1P3/q4N2/Pp1P2PP/R2Q1RK1 w kq - 0 1",            # promotions
        "8/P6k/8/8/8/8/8/K7 w - - 0 1", "8/8/8/8/8/8/p6K/k7 b - - 0 1",
        "4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 2"]                                            # en passant
while len(fens) < 100:
    b = chess.Board()
    for _ in range(random.randint(4, 80)):
        mv = list(b.legal_moves)
        if not mv: break
        b.push(random.choice(mv))
    if not b.is_game_over(): fens.append(b.fen())
nlong = 0
for fen in fens:
    b = chess.Board(fen)
    out, ok = e.go("fen " + fen, "go depth 9", 60)
    check(ok, f"depth 9 timed out: {fen}")
    last, bm = verify_search(out, b, "depth9")
    if last and len(last.group(10).split()) >= 2: nlong += 1
check(nlong > len(fens) * 0.8, f"too few multi-move pv lines: {nlong}/{len(fens)}")
print(f"1. fixed depth: {len(fens)} positions, {nlong} with a pv of 2+ moves")

# ---- 2. interrupted searches and limits
for fen in fens[:25]:
    b = chess.Board(fen)
    e.send("ucinewgame"); e.send("isready"); e.read_until(lambda l: l == "readyok", 10)
    e.send("position fen " + fen); e.send("go infinite")
    time.sleep(random.choice([0.01, 0.03, 0.08, 0.2]))
    e.send("stop")
    out, ok = e.read_until(lambda l: l.startswith("bestmove"), 10)
    check(ok, "stop: no bestmove")
    verify_search(out, b, "stop")
    out, ok = e.go("fen " + fen, "go nodes 20000"); verify_search(out, b, "nodes")
    out, ok = e.go("fen " + fen, "go movetime 60"); verify_search(out, b, "movetime")
print("2. stop / nodes / movetime: bestmove, pv, ponder legal; bestmove = first move of the last pv")

# ---- 3. mate scores and pv length
mates = [("6k1/5ppp/8/8/8/8/5PPP/3R2K1 w - - 0 1", 1), ("r1bqkb1r/pppp1ppp/2n2n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR w KQkq - 4 4", 1),
         ("7k/8/6K1/8/8/8/8/R7 w - - 0 1", 2), ("1k6/8/1K6/8/8/8/8/Q7 w - - 0 1", 2)]
for fen, _ in mates:
    b = chess.Board(fen)
    out, ok = e.go("fen " + fen, "go depth 8")
    last, bm = verify_search(out, b, "mate")
    if last and last.group(3) == "mate" and int(last.group(4)) > 0:
        n = int(last.group(4)); l = len(last.group(10).split())
        check(l >= 2 * n - 1, f"mate in {n}: pv has only {l} plies ({last.group(10)})")
        bb = b.copy()
        for mv in last.group(10).split(): bb.push(chess.Move.from_uci(mv))
        check(bb.is_checkmate() or l > 2 * n - 1, f"mate in {n}: pv does not end in mate: {last.group(10)}")
print("3. mate pv lengths")

# ---- 4. long pv inside the table
fen = "8/8/8/4k3/8/8/8/4KQ2 w - - 0 1"; b = chess.Board(fen)
out, ok = e.go("fen " + fen, "go depth 30", 120); check(ok, "KQK depth 30 timed out")
last, bm = verify_search(out, b, "kqk")
print("4. KQ vs K depth 30: longest pv", max((len(l.split(" pv ")[1].split()) for l in out if " pv " in l), default=0), "plies")
out, ok = e.go("startpos", "go depth 9"); verify_search(out, chess.Board(), "after-long")   # table state still sane

# ---- 5. no pv for finished games, single legal move
for fen in ["rnb1kbnr/pppp1ppp/8/4p3/6Pq/5P2/PPPPP2P/RNBQKBNR w KQkq - 1 3", "7k/5Q2/6K1/8/8/8/8/8 b - - 0 1"]:
    out, ok = e.go("fen " + fen, "go depth 5", 10)
    bm = [l for l in out if l.startswith("bestmove")]
    check(len(bm) == 1 and bm[0] == "bestmove 0000", f"finished game: {bm} {out[-3:]}")
fen = "7k/8/8/8/8/8/5q2/7K w - - 0 1"; b = chess.Board(fen)
out, ok = e.go("fen " + fen, "go wtime 60000 btime 60000", 10); verify_search(out, b, "single")
print("5. mate/stalemate/single reply")

# ---- 6. hashfull grows, Clear Hash empties it
e.send("setoption name Hash value 1"); e.send("isready"); e.read_until(lambda l: l == "readyok", 10)
e.send("ucinewgame"); e.send("isready"); e.read_until(lambda l: l == "readyok", 10)
e.send("position startpos"); e.send("go movetime 2500")
out, ok = e.read_until(lambda l: l.startswith("bestmove"), 15)
hfs = [int(INFO.match(l).group(7)) for l in out if INFO.match(l)]
check(len(hfs) > 5 and hfs[-1] > 100 and max(hfs) > hfs[0], f"hashfull did not grow in a 1 MB table: {hfs}")
check(all(0 <= h <= 1000 for h in hfs), "hashfull range")
e.send("setoption name Clear Hash"); e.send("position startpos"); e.send("go depth 1")
out, ok = e.read_until(lambda l: l.startswith("bestmove"), 10)
m = [INFO.match(l) for l in out if INFO.match(l)]
check(m and int(m[0].group(7)) <= 5, f"hashfull after Clear Hash: {[l for l in out][:3]}")
e.send("setoption name Hash value 32"); e.send("isready"); e.read_until(lambda l: l == "readyok", 10)
print("6. hashfull: grew to", hfs[-1] if hfs else None, "permille in 1 MB, small after Clear Hash")

# ---- 7. currmove after 3 s
fen = "r1bq1rk1/pp2bppp/2n1pn2/2pp4/3P1B2/2PBPN2/PP1N1PPP/R2QK2R w KQ - 0 8"; b = chess.Board(fen)
e.send("ucinewgame"); e.send("isready"); e.read_until(lambda l: l == "readyok", 10)
e.send("position fen " + fen); t0 = time.time(); e.send("go infinite")
lines = []; end = t0 + 14.0       # currmove lines come with the first root-move boundary after 3 s: a fast / deep build needs a longer window than a fixed 4.6 s (it depends on the search speed)
while time.time() < end:
    out, _ = e.read_until(lambda l: False, 0.2); lines += [(time.time(), l) for l in out]
    if time.time() > t0 + 3.5 and sum(1 for _, l in lines if " currmove " in l) > 10: break
e.send("stop"); out, ok = e.read_until(lambda l: l.startswith("bestmove"), 10); lines += [(time.time(), l) for l in out]
cm = [(t, l) for t, l in lines if " currmove " in l]
check(len(cm) > 10, f"currmove lines: {len(cm)}")
legal = {m.uci() for m in b.legal_moves}; nmoves = len(legal)
curdepth = 0; prev = None
for t, l in cm:
    m = re.match(r"^info depth (\d+) currmove ([a-h][1-8][a-h][1-8][qrbn]?) currmovenumber (\d+)$", l)
    check(m is not None, f"currmove format: {l}")
    if not m: continue
    check(m.group(2) in legal, f"currmove not a legal root move: {l}")
    check(1 <= int(m.group(3)) <= nmoves, f"currmovenumber out of range: {l}")
    check(t - t0 > 2.9, f"currmove printed before 3 s ({t - t0:.2f}s)")
    if prev and int(m.group(1)) == prev[0]: check(int(m.group(3)) == prev[1] + 1, f"currmovenumber not consecutive: {l}")
    prev = (int(m.group(1)), int(m.group(3)))
infos_before = [l for t, l in lines if INFO.match(l)]
check(len(infos_before) > 5, "no info lines in infinite search")
verify_search([l for t, l in lines], b, "infinite", strict=False)
print(f"7. currmove: {len(cm)} lines after 3 s, all legal root moves, numbering consecutive")

e.close()
print(f"\n{passes} checks passed, {fails} FAILED")
sys.exit(1 if fails else 0)
