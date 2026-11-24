#!/usr/bin/env python3
"""UCI conformance for tournament managers and GUIs.

Short, targeted checks (78); the whole run takes about 7 s.  Needs python-chess (pip install chess).
  ENGINE=./zeval python3 tests/t_uci.py          (Windows: set ENGINE=zeval.exe)
Exit code 0 = no FAIL.  WARN lines are informational (optional protocol features).
"""
import os, sys, time, re, subprocess
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _eng import ENGINE, PipeReader
import chess

EOF = object()
STARTFEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
results = []

def chk(name, ok, info=""):
    results.append(("PASS" if ok else "FAIL", name))
    print(("PASS " if ok else "FAIL ") + name + ("" if ok or not info else "  [" + str(info)[:300] + "]"), flush=True)
    return ok

def warn(name, ok, info=""):
    if ok: print("PASS " + name, flush=True)
    else:
        results.append(("WARN", name)); print("WARN " + name + ("" if not info else "  [" + str(info)[:200] + "]"), flush=True)

class Eng:
    def __init__(s):
        s.p = subprocess.Popen(ENGINE, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, bufsize=0)
        s.rd = PipeReader(s.p.stdout); s.buf = b""; s.dead = False
    def send(s, *lines):
        data = b"".join((l if isinstance(l, bytes) else l.encode()) + b"\n" for l in lines)
        try: s.p.stdin.write(data); s.p.stdin.flush()
        except OSError: pass
    def readline(s, timeout):
        end = time.time() + timeout
        while True:
            i = s.buf.find(b"\n")
            if i >= 0:
                l, s.buf = s.buf[:i], s.buf[i + 1:]
                return l.decode(errors="replace").rstrip("\r")
            if s.dead:
                if s.buf: l, s.buf = s.buf, b""; return l.decode(errors="replace")
                return EOF
            d = s.rd.get(max(0.0, end - time.time()))
            if d is None: return None
            if d == b"": s.dead = True
            else: s.buf += d
    def collect(s, until, timeout=5):
        lines, end = [], time.time() + timeout
        while True:
            l = s.readline(max(0.0, end - time.time()))
            if l is None or l is EOF: return lines, False
            lines.append(l)
            if until(l): return lines, True
    def quiet(s, t=0.15):                      # everything that arrives within t seconds
        out = []
        while True:
            l = s.readline(t)
            if l is None or l is EOF: return out
            out.append(l)
    def uci(s):
        s.send("uci"); return s.collect(lambda l: l == "uciok", 5)
    def ready(s, t=3):
        s.send("isready"); return s.collect(lambda l: l == "readyok", t)[1]
    def go(s, cmd, t=6):
        t0 = time.time(); s.send(cmd)
        lines, ok = s.collect(lambda l: l.startswith("bestmove"), t)
        return lines, ok, (time.time() - t0) * 1000
    def fen(s):
        s.send("d"); lines, ok = s.collect(lambda l: l.startswith("Fen:"), 2)
        return lines[-1][4:].strip() if ok else None
    def close(s):
        s.send("quit")
        try: s.p.wait(3)
        except Exception: s.p.kill(); s.p.wait()
        return s.p.returncode

def bm(lines):
    b = [l for l in lines if l.startswith("bestmove")]
    return b[-1].split()[1] if b else None

def legal_in(board, mv):
    try: return chess.Move.from_uci(mv) in board.legal_moves
    except Exception: return False

def board_of(fen_or_none, moves=()):
    b = chess.Board(fen_or_none) if fen_or_none else chess.Board()
    for m in moves: b.push_uci(m)
    return b

def infos(lines):
    out = []
    for l in lines:
        if not l.startswith("info"): continue
        t = l.split(); d = {}
        if "depth" in t: d["depth"] = int(t[t.index("depth") + 1])
        if "nodes" in t: d["nodes"] = int(t[t.index("nodes") + 1])
        if "score" in t: d["score"] = (t[t.index("score") + 1], int(t[t.index("score") + 2]))
        if "pv" in t: d["pv"] = t[t.index("pv") + 1:]
        d["raw"] = l; out.append(d)
    return out

def info_ok(l, board):
    t = l.split()
    if t[0] != "info": return True
    if "string" in t: return True
    i = 1
    while i < len(t):
        k = t[i]
        if k in ("depth", "seldepth", "multipv", "nodes", "nps", "time", "hashfull", "tbhits", "currmovenumber", "cpuload"):
            if i + 1 >= len(t) or not re.fullmatch(r"-?\d+", t[i + 1]): return False
            i += 2
        elif k == "score":
            if i + 2 >= len(t) or t[i + 1] not in ("cp", "mate") or not re.fullmatch(r"-?\d+", t[i + 2]): return False
            i += 3
            if i < len(t) and t[i] in ("lowerbound", "upperbound"): i += 1
        elif k == "pv":
            b = board.copy()
            for m in t[i + 1:]:
                try: mv = chess.Move.from_uci(m)
                except Exception: return False
                if mv not in b.legal_moves: return False
                b.push(mv)
            return True
        elif k in ("currmove", "refutation", "currline", "string"): return True
        else: return False
    return True

def main():
    e = Eng()
    # ---------------------------------------------------------------- handshake
    t0 = time.time(); lines, ok = e.uci(); dt = time.time() - t0
    chk("uci -> uciok within 2 s", ok and dt < 2, (ok, dt))
    chk("id name present", any(l.startswith("id name ") and len(l) > 8 for l in lines))
    chk("id author present", any(l.startswith("id author ") and len(l) > 10 for l in lines))
    chk("id name is 'Zeval 1.0'", "id name Zeval 1.0" in lines, [l for l in lines if l.startswith("id name")])
    chk("id author is 'Hasan CALISIR' (ASCII)", "id author Hasan CALISIR" in lines and all(l.isascii() for l in lines), [l for l in lines if l.startswith("id author")])
    chk("only id/option lines before uciok", all(re.match(r"(id (name|author) |option name |uciok$)", l) for l in lines),
        [l for l in lines if not re.match(r"(id (name|author) |option name |uciok$)", l)])
    opts = {}
    wellformed = True
    for l in lines:
        m = re.match(r"option name (.+?) type (check|spin|combo|button|string)(.*)$", l)
        if l.startswith("option"):
            if not m: wellformed = False; continue
            n, ty, rest = m.groups(); d = {"type": ty}
            for k in ("default", "min", "max"):
                mm = re.search(r"\b%s (-?\d+)\b" % k, rest)
                if mm: d[k] = int(mm.group(1))
            if ty == "spin" and not all(k in d for k in ("default", "min", "max") ) : wellformed = False
            if ty == "spin" and "min" in d and not (d["min"] <= d.get("default", 0) <= d["max"]): wellformed = False
            opts[n] = d
    chk("all option lines well-formed (spin: default/min/max, default in range)", wellformed, lines)
    h = opts.get("Hash", {})
    chk("Hash option advertised (spin, CCRL sets 128/256 MB through it)", h.get("type") == "spin", opts)
    chk("Hash max >= 256", h.get("max", 0) >= 256)
    chk("Threads option advertised (tools send option.Threads=1)", "Threads" in opts and opts["Threads"]["type"] == "spin", opts)
    chk("Move Overhead option advertised", opts.get("Move Overhead", {}).get("type") == "spin")
    chk("Clear Hash button advertised", opts.get("Clear Hash", {}).get("type") == "button")
    chk("no unsupported feature claimed (Ponder/OwnBook/UCI_Chess960)",
        not any(k in opts for k in ("Ponder", "OwnBook", "UCI_Chess960")))
    # ---------------------------------------------------------------- readiness, whitespace, junk
    chk("isready -> readyok", e.ready())
    e.send("ucinewgame"); chk("ucinewgame + isready -> readyok", e.ready())
    for name, raw in (("leading spaces", "   isready"), ("leading tab", "\tisready"), ("trailing spaces", "isready   "),
                      ("CRLF", "isready\r"), ("mixed blanks", " \t isready \t ")):
        e.send(raw); chk("isready with %s" % name, e.collect(lambda l: l == "readyok", 2)[1])
    e.send("  position   startpos   moves  e2e4   e7e5 ")
    lines, ok, _ = e.go("  go   depth   2 ")
    chk("extra whitespace in position/go lines", ok and legal_in(board_of(None, ["e2e4", "e7e5"]), bm(lines)), lines[-2:])
    e.send("position\tstartpos\tmoves\te2e4")
    lines, ok, _ = e.go("go\tdepth\t2")
    chk("tabs between tokens", ok and legal_in(board_of(None, ["e2e4"]), bm(lines)), lines[-2:])
    e.send("position startpos\r"); lines, ok, _ = e.go("go depth 2\r")
    chk("CRLF line endings on position/go", ok and legal_in(board_of(None), bm(lines)), lines[-2:])
    e.send("foo", "debug on", "register later", "xyzzy 1 2 3", "ponderhit", "\x01\x02\x03", "go2 depth 3", "isready")
    lines, ok = e.collect(lambda l: l == "readyok", 2)
    chk("unknown / mistimed commands are ignored silently (no output)", ok and lines == ["readyok"], lines)
    e.send("position startpos", "e2e4", "d2d4", "g1f3")
    chk("stray move text in UCI mode does not alter the position", e.fen() == STARTFEN.replace(" KQkq - 0 1", " KQkq - 0 1"), e.fen())
    e.send(b"x" * 200000, "isready")
    chk("200 KB junk line then isready", e.collect(lambda l: l == "readyok", 3)[1])
    # ---------------------------------------------------------------- position
    def pos_case(name, cmd, fen=None, moves=()):
        e.send(cmd); lines, ok, _ = e.go("go depth 3")
        chk("position " + name + " -> legal bestmove", ok and legal_in(board_of(fen, moves), bm(lines)), lines[-2:])
    pos_case("startpos", "position startpos")
    pos_case("startpos moves (empty)", "position startpos moves")
    F1 = "r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1"
    pos_case("full FEN", "position fen " + F1, F1)
    pos_case("4-field FEN (no counters)", "position fen " + " ".join(F1.split()[:4]), F1)
    pos_case("FEN with double spaces", "position fen " + F1.replace(" ", "  "), F1)
    F2 = "r3k2r/pppq1ppp/2npbn2/2b1p3/2B1P3/2NPBN2/PPPQ1PPP/R3K2R w KQkq - 0 1"
    pos_case("FEN + castling moves", "position fen %s moves e1g1 e8c8" % F2, F2, ["e1g1", "e8c8"])
    pos_case("en passant line", "position startpos moves e2e4 a7a6 e4e5 d7d5", None, ["e2e4", "a7a6", "e4e5", "d7d5"])
    F3 = "8/P6k/8/8/8/8/7p/K7 w - - 0 1"
    e.send("position fen " + F3); lines, ok, _ = e.go("go depth 4")
    chk("promotion printed with lowercase piece suffix", ok and re.fullmatch(r"a7a8[qrbn]", bm(lines) or "") is not None, lines[-1:])
    e.send("position startpos moves e2e4 e2e4 d7d5"); lines, ok, _ = e.go("go depth 2")
    chk("illegal move inside 'moves' does not crash/hang", ok and legal_in(board_of(None, ["e2e4"]), bm(lines)), lines[-1:])
    seq = ["g1f3", "g8f6", "f3g1", "f6g8"] * 280
    e.send("position startpos moves " + " ".join(seq)); lines, ok, _ = e.go("go depth 2")
    chk("1120-ply position line (> 4 KB)", ok and legal_in(board_of(None), bm(lines)), lines[-1:])
    e.send("position startpos moves e2e4"); f_before = e.fen()
    e.go("go depth 4"); chk("search leaves the position unchanged", e.fen() == f_before, (f_before, e.fen()))
    # ---------------------------------------------------------------- go: limits
    e.send("ucinewgame", "position startpos"); lines, ok, _ = e.go("go depth 6")
    inf = infos(lines)
    chk("go depth 6: legal bestmove, last info depth == 6", ok and inf and inf[-1].get("depth") == 6 and legal_in(board_of(None), bm(lines)), lines[-2:])
    chk("every info line well-formed, PV legal in sequence", all(info_ok(l, board_of(None)) for l in lines), [l for l in lines if not info_ok(l, board_of(None))][:2])
    for ms, lo, hi in ((50, 25, 90), (200, 100, 240)):
        e.send("position startpos"); lines, ok, dt = e.go("go movetime %d" % ms)
        chk("go movetime %d returns in [%d, %d] ms" % (ms, lo, hi), ok and lo <= dt <= hi, dt)
    e.send("position startpos"); lines, ok, dt = e.go("go nodes 20000")
    mx = max([d.get("nodes", 0) for d in infos(lines)] or [0])
    chk("go nodes 20000: stops near the node limit and quickly", ok and dt < 400 and mx <= 20000 + 2100 and legal_in(board_of(None), bm(lines)), (mx, dt))
    lines, ok, dt = e.go("go nodes 1")
    chk("go nodes 1 still answers with a legal move", ok and dt < 300 and legal_in(board_of(None), bm(lines)), (lines[-1:], dt))
    lines, ok, dt = e.go("go depth 3 nodes 100000000")
    chk("depth + nodes: first limit wins", ok and dt < 1500 and infos(lines)[-1].get("depth") == 3, (dt, lines[-1:]))
    SC = "r1bqkb1r/pppp1ppp/2n2n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR w KQkq - 4 4"
    e.send("position fen " + SC); lines, ok, dt = e.go("go mate 1")
    inf = infos(lines)
    chk("go mate 1: finds Qxf7# (score mate 1)", ok and bm(lines) == "h5f7" and inf and inf[-1]["score"] == ("mate", 1) and dt < 1500, (lines[-2:], dt))
    # searchmoves
    e.send("position startpos")
    lines, ok, _ = e.go("go depth 4 searchmoves a2a3 h2h3")
    chk("go searchmoves a2a3 h2h3: bestmove restricted to the list", ok and bm(lines) in ("a2a3", "h2h3"), lines[-1:])
    chk("searchmoves: every PV starts with a listed move", all(d["pv"][0] in ("a2a3", "h2h3") for d in infos(lines) if "pv" in d), lines[-3:])
    e.send("position startpos"); e.send("go infinite searchmoves g1f3"); time.sleep(0.2); e.send("stop")
    lines, ok = e.collect(lambda l: l.startswith("bestmove"), 2)
    chk("go infinite searchmoves g1f3 + stop", ok and bm(lines) == "g1f3", lines[-2:])
    e.send("position fen " + F3); lines, ok, _ = e.go("go depth 4 searchmoves a7a8n")
    chk("searchmoves with an under-promotion", ok and bm(lines) == "a7a8n", lines[-1:])
    e.send("position startpos"); lines, ok, _ = e.go("go depth 3 searchmoves e9e9 zzzz")
    chk("searchmoves with no valid move is ignored", ok and legal_in(board_of(None), bm(lines)), lines[-1:])
    lines, ok, dt = e.go("go searchmoves e2e4 wtime 60000 btime 60000")
    chk("searchmoves + clock, single allowed move answers at once", ok and bm(lines) == "e2e4" and dt < 300, (lines[-1:], dt))
    # clocks
    e.send("position startpos")
    lines, ok, dt = e.go("go wtime 1000 btime 1000 winc 100 binc 100 movestogo 10")
    chk("go wtime/btime/winc/binc/movestogo", ok and legal_in(board_of(None), bm(lines)) and dt < 700, dt)
    lines, ok, dt = e.go("go wtime 300 btime 300")
    chk("go wtime 300 btime 300 returns well inside the clock", ok and dt < 200, dt)
    worst = 0
    for _ in range(6):
        e.send("ucinewgame", "position startpos"); lines, ok, dt = e.go("go wtime 120 btime 120"); worst = max(worst, dt if ok else 9999)
    chk("6 x 'wtime 120' never exceeds the clock", worst < 120, worst)
    lines, ok, dt = e.go("go wtime -50 btime -50")
    chk("negative clock values handled", ok and dt < 250 and legal_in(board_of(None), bm(lines)), (dt, lines[-1:]))
    lines, ok, dt = e.go("go")
    chk("plain 'go' (no limits) terminates by itself", ok and dt < 3000 and legal_in(board_of(None), bm(lines)), dt)
    # mate / no-move positions
    e.send("position fen 7k/R7/1R6/8/8/8/8/K7 b - - 0 1"); lines, ok, _ = e.go("go depth 4")
    inf = infos(lines)
    chk("mated side reports 'score mate -1'", ok and inf and inf[-1]["score"] == ("mate", -1), lines[-2:])
    e.send("position fen 7k/5Q2/6K1/8/8/8/8/8 b - - 0 1"); lines, ok, dt = e.go("go depth 3")
    chk("go in stalemate position answers once with a null move", ok and bm(lines) in ("0000", "(none)") and sum(l.startswith("bestmove") for l in lines) == 1, lines[-2:])
    # ---------------------------------------------------------------- search control while running
    e.send("position startpos", "go infinite"); time.sleep(0.25)
    e.send("isready"); lines, ok = e.collect(lambda l: l == "readyok", 1)
    chk("isready answered during search, search keeps running (no bestmove yet)", ok and not any(l.startswith("bestmove") for l in lines), lines[-2:])
    t0 = time.time(); e.send("stop"); lines, ok = e.collect(lambda l: l.startswith("bestmove"), 1)
    chk("stop -> bestmove within 100 ms", ok and (time.time() - t0) < 0.1 and legal_in(board_of(None), bm(lines)), time.time() - t0)
    chk("exactly one bestmove per go", not any(l.startswith("bestmove") for l in e.quiet(0.2)))
    e.send("stop"); chk("stop while idle: ignored, no output", e.quiet(0.15) == [])
    # commands sent during a running search are queued, never lost; stop still gets through
    e.send("position startpos", "go infinite"); time.sleep(0.15)
    e.send("position startpos moves e2e4", "go depth 3"); time.sleep(0.15); e.send("stop")
    lines, ok = e.collect(lambda l: l.startswith("bestmove"), 2)
    ok1 = ok and legal_in(board_of(None), bm(lines))
    lines2, ok2 = e.collect(lambda l: l.startswith("bestmove"), 3)
    chk("position+go sent during 'go infinite' are not lost (queued, stop still works)",
        ok1 and ok2 and legal_in(board_of(None, ["e2e4"]), bm(lines2)), (lines[-1:], lines2[-1:]))
    e.send("position startpos", "go infinite"); time.sleep(0.1)
    e.send("setoption name Hash value 16", "ucinewgame", "isready"); time.sleep(0.1); e.send("stop")
    l1, ok = e.collect(lambda l: l.startswith("bestmove"), 2)
    chk("setoption/ucinewgame during search are deferred, answers stay in order", ok and e.ready(), l1[-2:])
    # ---------------------------------------------------------------- setoption
    def nodes_depth(d, setup=("ucinewgame", "position startpos")):
        e.send(*setup); lines, ok, _ = e.go("go depth %d" % d)
        i = infos(lines); return i[-1]["nodes"] if ok and i else None
    # depth 11 from the start position: the node count depends on the table size (Hash 1 MB and 32 MB differ; at depth 7 every size gives
    # the same count, so a check there could not fail).  The table size was changed above (Hash 16 during a search), so "default" is
    # measured in a fresh process.
    e.send("setoption name Hash value 32", "setoption name Threads value 1")
    n1 = nodes_depth(11); n2 = nodes_depth(11)
    chk("ucinewgame makes searches reproducible (TT cleared)", n1 is not None and n1 == n2, (n1, n2))
    f = Eng(); f.uci(); f.send("ucinewgame", "position startpos"); lines, ok, _ = f.go("go depth 11"); i = infos(lines); f.close()
    n_def = i[-1]["nodes"] if ok and i else None
    chk("explicit Hash 32 == default behaviour (a fresh process)", n_def is not None and n1 == n_def, (n_def, n1))
    e.send("setoption name Hash value 1"); n_h1 = nodes_depth(11)
    chk("Hash 1 and Hash 32 give different trees at depth 11 (the Hash checks here can fail)", n_h1 is not None and n_h1 != n1, (n1, n_h1))
    for hv in (64, 256, 1024):
        e.send("setoption name Hash value %d" % hv)
        n = nodes_depth(7); chk("setoption Hash %d -> search works" % hv, n is not None and e.ready(), n)
    e.send("setoption name Hash value 1", "setoption name hash value 32")
    n4 = nodes_depth(11); chk("option names are case-insensitive (hash)", n4 == n1, (n1, n_h1, n4))
    e.send("ucinewgame", "position startpos"); e.go("go depth 10")
    n_warm = nodes_depth(11, ("position startpos",))
    e.send("ucinewgame", "position startpos"); e.go("go depth 10")
    e.send("setoption name Clear Hash"); n5 = nodes_depth(11, ("position startpos",))
    chk("Clear Hash clears the table (same tree as after ucinewgame, not the tree of a filled table)", n5 == n1 and n5 != n_warm, (n1, n_warm, n5))
    for bad in ("setoption name Hash value 99999999999", "setoption name Hash value abc", "setoption name Hash value 0",
                "setoption name Hash value -5", "setoption name Hash", "setoption name Nonexistent value 1",
                "setoption", "setoption name", "setoption value 3", "setoption name Move Overhead value 99999999999"):
        e.send(bad)
    chk("bad/unknown setoption lines never break the engine", e.ready() and nodes_depth(5) is not None)
    e.send("setoption name Hash value 32", "setoption name Move Overhead value 12")
    e.send("setoption name Move Overhead value 500"); e.send("position startpos")
    lines, ok, dt_big = e.go("go movetime 600")
    e.send("setoption name Move Overhead value 12"); lines, ok2, dt_def = e.go("go movetime 600")
    chk("Move Overhead 500 shortens go movetime 600 (default does not)", ok and ok2 and 40 <= dt_big <= 250 and 480 <= dt_def <= 660, (dt_big, dt_def))
    # ---------------------------------------------------------------- shutdown
    e.send("go infinite"); time.sleep(0.1); t0 = time.time(); e.send("quit")
    try: e.p.wait(1.0)
    except Exception: pass
    chk("quit during search: process exits within 1 s, rc 0", e.p.poll() == 0, (e.p.poll(), time.time() - t0))
    e2 = Eng(); e2.uci(); e2.send("isready"); e2.collect(lambda l: l == "readyok", 2)
    e2.p.stdin.close()
    try: e2.p.wait(2)
    except Exception: pass
    chk("EOF on stdin while idle: process exits", e2.p.poll() is not None); e2.p.kill()
    e3 = Eng(); e3.uci(); e3.send("position startpos", "go infinite"); time.sleep(0.15)
    e3.p.stdin.close()
    try: e3.p.wait(3)
    except Exception: pass
    chk("EOF during 'go infinite': engine stops and exits (no orphaned CPU burner)", e3.p.poll() is not None, e3.p.poll()); e3.p.kill()
    e4 = Eng(); e4.send("uci", "position startpos", "go depth 5"); e4.p.stdin.close()
    lines, ok = e4.collect(lambda l: l.startswith("bestmove"), 5)
    chk("piped script ending in EOF: bounded search still completes and answers", ok and infos(lines)[-1].get("depth") == 5, lines[-2:])
    e4.p.kill()
    n_fail = sum(1 for r in results if r[0] == "FAIL"); n_warn = sum(1 for r in results if r[0] == "WARN")
    print("\n%d passed, %d FAILED, %d warnings" % (sum(1 for r in results if r[0] == "PASS"), n_fail, n_warn))
    for r in results:
        if r[0] == "FAIL": print("  FAIL:", r[1])
    return 1 if n_fail else 0

if __name__ == "__main__":
    sys.exit(main())
