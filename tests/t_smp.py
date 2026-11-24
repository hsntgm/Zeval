#!/usr/bin/env python3
"""Lazy SMP - behaviour of the release engine with Threads > 1.  Windows engine (the engine name ends in .exe, or SMP_WIN=1): the stdin reader thread
of zeval.exe is one more thread (BASE = 1).
Thread counts and CPU time per thread come from /proc/<pid>/task (Linux; under Wine the Windows threads are native threads and are counted the same
way) or, where there is no /proc (Windows), from psutil (pip install psutil).  Without either, the thread-count and idle checks
are skipped (printed) and the join in smp_stop is covered by checks 4 / 6 / 7 (no hang, exit code 0, legal moves).

usage: python3 tests/t_smp.py [engine] [--pool auto|0|1] [--psutil]   (default ./zeval, else dist/zeval; a Linux release build; needs python-chess)
       --psutil: use psutil also where /proc exists (self-test of the Windows path on Linux).      exit code 0 = everything passed

Two thread models (SMP_POOL in src/p1_data.asm): 0 = a new thread per helper and search, joined at its end; 1 = persistent helper
threads that wait between searches.  --pool auto (default) tells them apart by the thread count after check 1 and prints the model.  The
thread-count rules: model 0 - exactly Threads threads during a search, 1 after it; model 1 - the count never goes down: during and after a search
exactly max(count before, Threads).  In both models no thread may use CPU time once the bestmove is out: the idle check reads the CPU time of every
thread twice, 250 ms apart, and fails when the threads (except the main thread where it can be told apart) used 50 ms or more together.  Thread
times are counted in clock ticks (10 ms on Linux, 15.6 ms on Windows), hence a sum and a threshold instead of "any increase"; a helper that did not
stop, or waiting threads that spin, use far more (measured: a pool whose 31 waiting threads spin uses the whole machine).

  1. UCI:       option Threads is announced, default 1, max 32; values are clamped; Threads=1 gives the same node count as the single-thread engine in a fixed-depth search
  2. threads:   thread counts during / after a search by the rules above (no orphan), for 1, 2, 4, 8, 32 threads; no thread busy after the bestmove
  3. moves:     the bestmove is legal on 24 different positions with Threads = 4 and 32, short and long searches
  4. stop:      go infinite with 8 threads, stop -> bestmove within 150 ms, thread count by the rules, no thread busy; quit during a search -> exit code 0
                within 1 s, no thread left
  5. mate:      a forced mate under go infinite: the helpers have stopped as soon as the main thread waits for stop (model 0: ended; model 1: none of them
                uses CPU), the stop still answers
  6. churn:     600 very short searches (go depth 1, go movetime 5, go nodes 100) with Threads = 32 / 8: no hang, no crash, thread count by the rules,
                no thread busy afterwards, every move legal
  7. changes:   Threads 8 -> 1 -> 4 -> 2 -> 1 between searches; ucinewgame / Clear Hash with threads; Threads=1 afterwards == fresh Threads=1 node counts
  8. nodes:     go nodes N with Threads = 4: stops (the limit is a limit of the total, enforced by the main thread)
  9. timing:    movetime 200 with 8 threads returns within 200 ms + 120 ms
"""
import os, re, subprocess, sys, time
import chess, shlex
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _eng import LineReader, EngineGone, default_engine      # timed reads: a hung engine hits the timeouts below

HERE = os.path.dirname(os.path.abspath(__file__))
_pos = [a for i, a in enumerate(sys.argv[1:], 1) if not a.startswith("--") and sys.argv[i - 1] != "--pool"]
FORCE_PSUTIL = "--psutil" in sys.argv
EXE = _pos[0] if _pos else default_engine()
CMD = [EXE] if os.path.exists(EXE) else shlex.split(EXE, posix=(os.name != "nt"))     # a path, or a command line such as "wine zeval.exe"
WIN = EXE.lower().endswith(".exe") or os.environ.get("SMP_WIN") == "1" or os.name == "nt"
BASE = 1 if WIN else 0                                   # zeval.exe: + the stdin reader thread
PROC = os.path.isdir("/proc/self/task") and not FORCE_PSUTIL     # thread counting through /proc (Linux, Wine)
try:
    import psutil                                                 # Windows (or --psutil): thread count and CPU time per thread
except ImportError:
    psutil = None
COUNT = PROC or psutil is not None                                # thread counting possible
TICK = 1.0 / os.sysconf("SC_CLK_TCK") if PROC else 0.0            # /proc/<pid>/task/<tid>/stat counts CPU time in clock ticks
BUSY_SUM = 0.05                                                   # seconds of CPU time in the 250 ms window that count as "busy"
def tok(x, want): return x is None or x == want + BASE   # x = measured thread count (None = not measurable)
POOL = None                                               # thread model, decided after check 1 (or by --pool)
if "--pool" in sys.argv:
    v = sys.argv[sys.argv.index("--pool") + 1]; POOL = None if v == "auto" else v == "1"
def tcount_ok(before, now, n):
    """thread count `now` (during or after a search with Threads = n) by the rules of the model; before = the count before that search"""
    if now is None: return True
    if not POOL: return now == n + BASE
    return now == max(before if before is not None else 0, n + BASE)
def tafter_ok(before, now, n):
    if now is None: return True
    return tcount_ok(before, now, n) if POOL else now == 1 + BASE
bad = 0
def check(ok, msg, extra=""):
    global bad
    print(("PASS " if ok else "FAIL ") + msg + ((" " + str(extra)) if (extra != "" and not ok) else ""))
    if not ok: bad += 1

class Eng:
    def __init__(s):
        s.p = subprocess.Popen(CMD, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
        s.lr = LineReader(s.p.stdout)
        s.send("uci"); s.until(lambda l: l == "uciok")
    def send(s, *c):
        for x in c: s.p.stdin.write(x + "\n")
        s.p.stdin.flush()
    def until(s, pred, timeout=20):
        out = []; end = time.time() + timeout
        while True:
            left = end - time.time()
            if left <= 0: out.append("<TIMEOUT>"); return out
            try: l = s.lr.get(left)
            except EngineGone: out.append("<EOF>"); return out
            if l is None: continue
            l = l.strip(); out.append(l)
            if pred(l): return out
    def ready(s): s.send("isready"); s.until(lambda l: l == "readyok")
    def threads(s):
        if PROC: return len(os.listdir(f"/proc/{s.p.pid}/task"))
        if psutil is not None:
            try: return psutil.Process(s.p.pid).num_threads()
            except Exception: return None
        return None
    def _cpu(s):
        """{thread id: CPU seconds} of every thread of the engine process"""
        d = {}
        if PROC:
            try: tids = os.listdir(f"/proc/{s.p.pid}/task")
            except OSError: return d
            for t in tids:
                try: f = open(f"/proc/{s.p.pid}/task/{t}/stat").read()
                except OSError: continue
                w = f[f.rindex(")") + 2:].split(); d[t] = (int(w[11]) + int(w[12])) * TICK
        elif psutil is not None:
            try:
                for t in psutil.Process(s.p.pid).threads(): d[str(t.id)] = t.user_time + t.system_time
            except Exception: pass
        return d
    def busy(s, window=0.25, skip_main=False):
        """threads that used CPU time during `window` seconds, when together (without the main thread if skip_main and it can be told apart:
        /proc, tid = pid) they used BUSY_SUM seconds or more; [] otherwise or where it cannot be measured"""
        if not COUNT: return []
        a = s._cpu(); time.sleep(window); b = s._cpu()
        main = str(s.p.pid) if (skip_main and PROC) else None
        used = {t: b[t] - a[t] for t in b if t in a and b[t] > a[t] and t != main}
        return sorted(used) if sum(used.values()) >= BUSY_SUM else []
    def go(s, pos, cmd, timeout=30):
        s.send(pos, cmd); t = time.time()
        out = s.until(lambda l: l.startswith("bestmove"), timeout)
        return out, time.time() - t
    def quit(s):
        s.send("quit")
        try: s.p.wait(5)
        except Exception: s.p.kill()
        return s.p.returncode

def bm(out):
    for l in reversed(out):
        if l.startswith("bestmove"): return l.split()[1]
    return None

def legal(fen, mv):
    try:
        b = chess.Board(fen); return chess.Move.from_uci(mv) in b.legal_moves
    except Exception: return False

FENS = [chess.STARTING_FEN,
 "r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1", "8/2p5/3p4/KP5r/1R3p1k/8/4P1P1/8 w - - 0 1",
 "r3k2r/Pppp1ppp/1b3nbN/nP6/BBP1P3/q4N2/Pp1P2PP/R2Q1RK1 w kq - 0 1", "rnbq1k1r/pp1Pbppp/2p5/8/2B5/8/PPP1NnPP/RNBQK2R w KQ - 1 8",
 "r4rk1/1pp1qppp/p1np1n2/2b1p1B1/2B1P1b1/P1NP1N2/1PP1QPPP/R4RK1 w - - 0 10", "8/8/8/8/8/6k1/6p1/5K2 w - - 0 1",
 "6k1/5ppp/8/8/8/8/5PPP/3R2K1 w - - 0 1", "4k3/8/8/8/8/8/4P3/4K3 w - - 0 1", "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3",
 "2r3k1/pp3ppp/8/3q4/8/P4P2/1P4PP/2RQ2K1 b - - 0 1", "1k6/1p6/8/8/8/8/6P1/1K6 b - - 0 1",
 "rnbqkb1r/pp2pppp/3p1n2/8/3NP3/2N5/PPP2PPP/R1BQKB1R b KQkq - 2 5", "r2q1rk1/ppp2ppp/2n1bn2/3pp3/2B1P3/2NP1N2/PPP2PPP/R1BQ1RK1 w - - 0 8",
 "8/5pk1/6p1/8/8/6P1/5PK1/8 w - - 0 1", "3r2k1/5ppp/8/8/8/8/5PPP/R5K1 w - - 0 1", "r1b1k2r/ppppqppp/2n2n2/2b1p3/2B1P3/2N2N2/PPPPQPPP/R1B1K2R w KQkq - 6 6",
 "5rk1/pp3pp1/2p4p/8/3P4/2P2N2/PP3PPP/6K1 b - - 0 1", "7k/8/8/8/8/8/8/K6R w - - 0 1", "rnb1kbnr/pppp1ppp/8/4p3/6Pq/8/PPPPPP1P/RNBQKBNR w KQkq - 1 3",
 "8/p7/8/1P6/K1k5/8/8/8 w - - 0 1", "r1bq1rk1/pp3ppp/2n1pn2/2pp4/1bPP4/2NBPN2/PP3PPP/R1BQ1RK1 w - - 0 9", "2kr3r/ppp2ppp/2n1b3/3q4/3P4/2N2N2/PPP2PPP/R2Q1RK1 w - - 0 12",
 "4rrk1/pp3ppp/2n1b3/3p4/3P4/2P2N2/PP3PPP/R3R1K1 w - - 0 1"]

def main():
    e = Eng()
    # ---- 1. UCI
    e.send("uci"); o = e.until(lambda l: l == "uciok")
    check("option name Threads type spin default 1 min 1 max 32" in o, "1. uci announces: option name Threads type spin default 1 min 1 max 32")
    e.send("setoption name Threads value 0", "setoption name Threads value 99", "setoption name Threads value x", "isready"); e.until(lambda l: l == "readyok")
    out, dt = e.go("position startpos", "go depth 6"); check(bm(out) is not None, "1. out-of-range / bad Threads values are accepted without harm", out[-2:])
    e.send("setoption name Threads value 1"); e.ready()
    def nodes_at(pos, d):
        o, _ = e.go(pos, f"go depth {d}")
        return [int(re.search(r"nodes (\d+)", l)[1]) for l in o if l.startswith("info depth")][-1]
    e.send("ucinewgame"); e.ready(); ref = [nodes_at("position fen " + f, 7) for f in FENS[:6]]
    # ---- 2. thread counts
    global POOL
    print("     thread counts / CPU time: %s" % ("/proc" if PROC else "psutil" if psutil is not None else "NOT AVAILABLE (pip install psutil): count and idle checks skipped"))
    if POOL is None: POOL = COUNT and e.threads() > 1 + BASE      # check 1 searched with Threads = 32 (99 clamped): model 1 keeps those threads
    print("     thread model: %s" % ("1, persistent helper threads (SMP_POOL)" if POOL else "0, a new thread per helper and search"))
    ok = True; det = []
    for n in (1, 2, 4, 8, 32):
        e.send(f"setoption name Threads value {n}"); e.ready(); before = e.threads()
        e.send("position startpos", "go movetime 400"); time.sleep(0.2); during = e.threads()
        out = e.until(lambda l: l.startswith("bestmove")); time.sleep(0.02); after = e.threads(); busy = e.busy()
        det.append((n, before, during, after, len(busy)))
        if not (tcount_ok(before, during, n) and tafter_ok(before, after, n) and not busy): ok = False
    check(ok, "2. thread counts during / after a search follow the model, no thread busy after the bestmove (Threads, before, during, after, busy)", det)
    # ---- 3. legal moves
    ok = True; det = []
    for n in (4, 32):
        e.send(f"setoption name Threads value {n}", "ucinewgame"); e.ready()
        for i, f in enumerate(FENS):
            out, dt = e.go("position fen " + f, "go movetime 60" if i % 2 else "go depth 9")
            m = bm(out)
            if not (m and legal(f, m)): ok = False; det.append((n, f, m))
    check(ok, "3. bestmove legal on 24 positions with 4 and 32 threads", det[:3])
    # ---- 4. stop / quit
    e.send("setoption name Threads value 8"); e.ready(); before = e.threads()
    e.send("position startpos", "go infinite"); time.sleep(0.4); t = time.time(); e.send("stop")
    out = e.until(lambda l: l.startswith("bestmove")); dt = time.time() - t; time.sleep(0.02); busy = e.busy()
    check(bm(out) is not None and dt < 0.15 and tafter_ok(before, e.threads(), 8) and not busy,
          f"4. stop with 8 threads: bestmove within 150 ms (took {dt*1000:.0f} ms), threads {e.threads()} by the model, busy {len(busy)}")
    e.send("position startpos", "go infinite"); time.sleep(0.3); t = time.time(); e.send("quit")
    try: e.p.wait(3)
    except Exception: e.p.kill()
    dt = time.time() - t
    check(e.p.returncode == 0 and dt < 1.0, f"4. quit during an 8-thread search: exit code {e.p.returncode} after {dt*1000:.0f} ms")
    try: left = (e.p.poll() is None) if WIN else os.path.exists(f"/proc/{e.p.pid}")
    except Exception: left = False
    check(not left, "4. no process / thread left after quit")
    # stdin EOF during a search
    e = Eng(); e.send("setoption name Threads value 8", "position startpos", "go depth 12"); e.p.stdin.close()
    try: e.p.wait(10); rc = e.p.returncode
    except Exception: e.p.kill(); rc = None
    check(rc == 0, f"4. EOF on stdin during an 8-thread search: engine finishes the bounded search and exits (rc {rc})")
    # ---- 5. mate under go infinite
    e = Eng(); e.send("setoption name Threads value 4"); e.ready(); before = e.threads()
    e.send("position fen 6k1/5ppp/8/8/8/8/5PPP/3R2K1 w - - 0 1", "go infinite")
    out = e.until(lambda l: "score mate 1" in l, 5); time.sleep(0.3)
    found = any("score mate 1" in l for l in out)
    nt = e.threads(); hb = e.busy(skip_main=True); e.send("stop"); out = e.until(lambda l: l.startswith("bestmove"), 3)
    check(found, "5. the mate was found: 'score mate 1' printed under go infinite", out[-2:])
    check(tafter_ok(before, nt, 4) and not hb and bm(out) == "d1d8",
          f"5. proven mate under go infinite: helpers stopped while the main thread waits for stop (threads {nt}, helpers busy {len(hb)}), bestmove {bm(out)}")
    # ---- 6. churn
    ok = True; det = []; t0 = time.time()
    for n, cmds in ((32, ("go depth 1", "go movetime 5", "go nodes 100")), (8, ("go depth 1", "go movetime 5", "go depth 3"))):
        e.send(f"setoption name Threads value {n}"); e.ready(); before = e.threads()
        for i in range(100):
            f = FENS[i % len(FENS)]
            for c in cmds:
                out, dt = e.go("position fen " + f, c, 10)
                m = bm(out)
                if not (m and legal(f, m)) or e.p.poll() is not None: ok = False; det.append((n, c, f, m, out[-1])); break
            if not ok: break
        if WIN: time.sleep(0.3)                          # Wine removes the native thread of an ended Windows thread a moment after the join returned
        if e.p.poll() is None and not tafter_ok(before, e.threads(), n): ok = False; det.append(("threads", e.threads()))
        if e.p.poll() is None and e.busy(): ok = False; det.append(("busy threads", len(e.busy())))
        if not ok: break
    check(ok, f"6. 600 short searches with 32 / 8 threads: no hang, no crash, legal moves, threads by the model, none busy ({time.time()-t0:.1f} s)", det[:2])
    # ---- 7. Threads changes, new game, clear hash
    e = Eng(); seq = [8, 1, 4, 2, 1]; ok = True; det = []
    for n in seq:
        e.send(f"setoption name Threads value {n}", "ucinewgame", "setoption name Clear Hash"); e.ready(); before = e.threads()
        for f in FENS[:4]:
            out, dt = e.go("position fen " + f, "go depth 8")
            m = bm(out)
            if not (m and legal(f, m)): ok = False; det.append((n, f, m))
        if WIN: time.sleep(0.3)
        if not tafter_ok(before, e.threads(), n) or e.busy(): ok = False; det.append(("threads", n, e.threads()))
    check(ok, "7. Threads 8 -> 1 -> 4 -> 2 -> 1 with ucinewgame / Clear Hash between: fine", det[:2])
    e.send("setoption name Threads value 1", "ucinewgame"); e.ready()
    again = [nodes_at_e(e, "position fen " + f, 7) for f in FENS[:6]]
    check(again == ref, "7. after the thread changes, Threads=1 gives exactly the node counts of a fresh process", (ref, again))
    # ---- 8. go nodes
    e.send("setoption name Threads value 4"); e.ready(); ok = True; det = []
    for lim in (1, 100, 5000, 50000):
        out, dt = e.go("position startpos", f"go nodes {lim}", 10)
        if bm(out) is None or dt > 0.5: ok = False; det.append((lim, dt))
    check(ok, "8. go nodes N with 4 threads stops quickly and answers", det)
    # ---- 9. timing
    e.send("setoption name Threads value 8"); e.ready(); worst = 0
    for f in FENS[:6]:
        out, dt = e.go("position fen " + f, "go movetime 200"); worst = max(worst, dt)
    check(worst < 0.32, f"9. movetime 200 with 8 threads: worst {worst*1000:.0f} ms (< 320 ms)")
    rc = e.quit(); check(rc == 0, f"exit code {rc}")
    print("ALL PASSED" if not bad else f"{bad} FAILED"); sys.exit(1 if bad else 0)

def nodes_at_e(e, pos, d):
    o, _ = e.go(pos, f"go depth {d}")
    return [int(re.search(r"nodes (\d+)", l)[1]) for l in o if l.startswith("info depth")][-1]

main()
