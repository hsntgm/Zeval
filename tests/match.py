#!/usr/bin/env python3
"""Self-play match runner: A (new) vs B (baseline), paired openings (colours swapped), SPRT on PAIRS (pentanomial).

usage: match.py A B [--games 400] [--workers 2] [--seed S] [--pool N]
                    time:  --movetime 40            fixed time per move in ms (default)
                           --nodes 100000           fixed nodes per move (not affected by CPU load; use for A/B of eval/search)
                           --tc 10+0.1              real clock, seconds base + increment per move (flag fall = loss)
                    sprt:  --elo0 -3 --elo1 3 [--alpha 0.05 --beta 0.05] [--sprt penta|tri|none]
                    engines: --optA "Threads=4;Hash=64"  --optB "Threads=1"   (UCI setoption, ';'-separated)
                    data:  --save-games g.jsonl
                    starts: --book auto          (default) tests/books/noob_3moves.epd when it is there, else
                                                 random openings; --book random = random openings; --book FILE = an EPD book.  A book
                                                 match plays a seeded random sample of --games / 2 positions of the book, each with both
                                                 colours: a new seed draws a new sample.
                            --epd FILE           start positions from a FEN / EPD file (one per line, the first 4-6 fields; '#' = comment) instead of
                                                 random openings: a seeded sample of --games / 2 of them, each played with both colours
                                                 (e.g. tests/endgames.epd; with --no-adj the games are played out)

Why pairs: every opening is played twice with the colours swapped, so the two games of a pair are not independent.  The
observation unit is the pair (score for A: 0, 0.5, 1, 1.5, 2); tests/sprt.py computes the LLR and the Elo error from the five
pair counts.  The printed "Elo +-" is a 95 % interval (1.96 standard errors).  --sprt tri uses the per-game (trinomial) numbers
instead, which overstates the confidence (kept for comparison).  The Elo printed when an SPRT stops early is biased towards the
accepted hypothesis; for a size estimate of an accepted change, run a fixed number of games with a new seed.
Random openings (--book random): 8 random plies from the initial position, kept when the base engine scores them within +-90 cp at
depth 7, cached per seed in tests/openings_<seed>.json.  Why a book is the default: the same strength gap was shown with the same
relative error by random openings and noob_3moves, about half as well by UHO_4060_v4; the book's positions are human openings and
better balanced (white scores 51 % against 53 %).  Two seeds are two independent samples: their results differ like two runs of one seed.
Recommended practice: a NEW seed for every new match (default: a fresh one is drawn and printed), a test that
passes at 40 ms should also be run at ~200 ms, arithmetic changes with Threads=1, SMP changes with several threads.
Games end by the rules (a 3-fold repetition or the 50-move rule once it has happened on the board, not when the next move could
claim it), plus adjudication: 6 consecutive plies with |score| >= 700 (win), 8 consecutive plies with |score| <= 10 once 80 plies
have been played (draw; --no-draw-adj switches this one off), 400-ply cap; plies are counted from the start position (the random opening included; from the first move
with --epd).  A move played without a score line (a forced move answered at once) keeps the previous ply's score.  Engine failure,
illegal move, no answer or time forfeit = loss, recorded as adj="fail".
Exit code: 0 whenever the run completed (the verdict is the printed line, not the exit code); 2 = an engine could not be started or answered uci / isready before the match.
A write to a crashed engine (BrokenPipe) is an engine failure (a lost game, adj="fail"), not a crash of the whole match.  Both engines are started once
before the match as a pre-flight check (clear message for a wrong path).  An opening pool that cannot be filled (e.g. --plies 1 with --pool 50) stops with a
message.  A corrupt openings_<seed>.json is rebuilt; the pool file is written atomically."""
import argparse, subprocess, random, math, os, sys, time, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _eng import resolve, PipeReader
import sprt
import chess
from multiprocessing import Pool

HERE = os.path.dirname(os.path.abspath(__file__))


class Eng:
    def __init__(self, path, opts=""):
        self.p = subprocess.Popen(resolve(path), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, bufsize=0)
        self.rd = PipeReader(self.p.stdout); self.buf = b""
        self.send("uci"); self.wait("uciok", 60)                       # generous: a loaded machine must not turn a slow start into a crash
        for kv in filter(None, (x.strip() for x in opts.split(";"))):
            k, _, v = kv.partition("=")
            self.send(f"setoption name {k.strip()} value {v.strip()}")
        self.send("isready"); self.wait("readyok", 60)

    def send(self, s):
        try: self.p.stdin.write((s + "\n").encode()); self.p.stdin.flush()
        except OSError: raise RuntimeError("engine died (write)")      # BrokenPipeError / OSError: the engine is gone

    def readline(self, timeout=0.5):
        """returns a line (str), "" on timeout, raises on EOF"""
        end = time.time() + timeout
        while b"\n" not in self.buf:
            left = end - time.time()
            if left <= 0: return ""
            d = self.rd.get(left)
            if d is None: return ""
            if not d: raise RuntimeError("engine died")
            self.buf += d
        l, self.buf = self.buf.split(b"\n", 1)
        return l.decode() + "\n"

    def wait(self, tok, timeout=10):
        end = time.time() + timeout
        while time.time() < end:
            l = self.readline()
            if l.startswith(tok): return l
        raise RuntimeError("timeout waiting " + tok)

    def newgame(self):
        self.send("ucinewgame"); self.send("isready"); self.wait("readyok", 60)

    def think(self, moves, go, timeout, fen=None):
        """go = the text after 'go'; timeout in seconds; fen = start position (None = the initial position); returns (bestmove, score cp or None, elapsed ms)
        score = None when the engine sent no score line (Zeval answers a position with one legal move at once)"""
        start = f"position fen {fen}" if fen else "position startpos"
        self.send(start + (" moves " + " ".join(moves) if moves else ""))
        t0 = time.time()
        self.send("go " + go)
        score = None; end = t0 + timeout
        while time.time() < end:
            l = self.readline()
            if not l: continue
            if l.startswith("info") and " score " in l:
                t = l.split()
                i = t.index("score")
                score = int(t[i + 2]) if t[i + 1] == "cp" else (10000 if int(t[i + 2]) > 0 else -10000)
            elif l.startswith("bestmove"):
                return l.split()[1], score, (time.time() - t0) * 1000.0
        raise RuntimeError("no bestmove (time forfeit)")

    def quit(self):
        try: self.send("quit"); self.p.wait(timeout=2)
        except Exception: self.p.kill()


def parse_tc(s):
    base, _, inc = s.partition("+")
    return int(float(base) * 1000), int(float(inc or 0) * 1000)


def rules_result(b):
    """the result by the rules (white's view: 1 / 0.5 / 0), or None while the game goes on.  python-chess's claim_draw=True is not
    used: it also ends the game when the side to move merely COULD reach a 3-fold repetition or the 50-move limit with its next move,
    which a side that is better would not do.  Here a repetition / the 50-move rule counts once it has happened on the board."""
    o = b.outcome(claim_draw=False)                      # checkmate, stalemate, insufficient material, 5-fold, 75-move rule
    if o is not None:
        return 0.5 if o.winner is None else (1.0 if o.winner == chess.WHITE else 0.0)
    if b.is_repetition(3) or b.halfmove_clock >= 100:
        return 0.5
    return None


def play(white, black, opening, mode, rec=None, adjudicate=True):
    """returns (result from white's view: 1 / 0.5 / 0, adj).
    mode = ("movetime", ms) | ("nodes", n) | ("tc", base_ms, inc_ms)
    adjudicate: True = win and draw adjudication, "win" = win adjudication only, False = none
    rec (dict or None): filled with {"opening", "plies": [[fen, white-view cp, uci, is_capture, gives_check], ...], "adj"}
    adj = how the game ended: None (real end), "win"/"draw" (adjudicated by the runner), "len" (move cap), "fail" (engine failure / illegal move / forfeit on time)."""
    fen = opening if isinstance(opening, str) else None              # a start position (str) or opening moves from the initial position (list)
    b = chess.Board(fen) if fen else chess.Board(); moves = []
    for m in ([] if fen else opening): b.push_uci(m); moves.append(m)
    for who, e0 in ((0, white), (1, black)):                          # an engine that cannot even start a game loses it
        try: e0.newgame()
        except RuntimeError:
            if rec is not None: rec.update({"opening": opening if fen else list(opening), "plies": [], "adj": "fail"})
            return (0.0 if who == 0 else 1.0), "fail"
    win_run = 0; draw_run = 0; last_sign = 0; wsc = 0
    clock = [mode[1], mode[1]] if mode[0] == "tc" else None      # [white, black] ms
    if rec is not None: rec.update({"opening": opening if fen else list(opening), "plies": [], "adj": None})

    def end(r, why=None):
        if rec is not None: rec["adj"] = why
        return r, why
    while True:
        r = rules_result(b)
        if r is not None:
            return end(r)
        side = 0 if b.turn == chess.WHITE else 1
        e = white if side == 0 else black
        if mode[0] == "movetime": go, tmo = f"movetime {mode[1]}", mode[1] / 1000 + 5
        elif mode[0] == "nodes": go, tmo = f"nodes {mode[1]}", 120
        else:
            go = f"wtime {max(1, int(clock[0]))} btime {max(1, int(clock[1]))} winc {mode[2]} binc {mode[2]}"
            tmo = max(clock[side], 1) / 1000 + 1.0
        try:
            mv, sc, used = e.think(moves, go, tmo, fen)
        except RuntimeError:
            return end(0.0 if side == 0 else 1.0, "fail")        # engine failure / no answer in time counts as loss
        if clock is not None:
            clock[side] -= used
            if clock[side] <= 0: return end(0.0 if side == 0 else 1.0, "fail")   # flag fall
            clock[side] += mode[2]
        try:
            m = chess.Move.from_uci(mv)
            if m not in b.legal_moves: raise ValueError
        except ValueError:
            return end(0.0 if side == 0 else 1.0, "fail")        # illegal move = loss
        if sc is not None:                                           # no score line: keep the previous ply's score (white's view)
            wsc = sc if side == 0 else -sc
        if rec is not None:
            rec["plies"].append([b.fen(), wsc, mv, int(b.is_capture(m)), int(b.gives_check(m))])
        b.push(m); moves.append(mv)
        sign = 1 if wsc >= 700 else (-1 if wsc <= -700 else 0)
        win_run = win_run + 1 if (sign != 0 and sign == last_sign) else (1 if sign != 0 else 0)
        last_sign = sign
        if win_run >= 6 and adjudicate: return end(1.0 if sign > 0 else 0.0, "win")
        draw_run = draw_run + 1 if (abs(wsc) <= 10 and len(moves) >= 80) else 0
        if draw_run >= 8 and adjudicate is True: return end(0.5, "draw")
        if len(moves) > 400: return end(0.5, "len")                      # 400 plies, counted from the start position


def worker(args):
    a, bpath, optA, optB, opening, mode, save = args[:7]
    adj = args[7] if len(args) > 7 else True
    for attempt in range(4):                                         # a stalled start (loaded or paused machine) is retried, not fatal
        try:
            A = Eng(a, optA)
        except RuntimeError:
            time.sleep(2); continue
        try:
            B = Eng(bpath, optB); break
        except RuntimeError:
            A.quit(); time.sleep(2)
    else:
        A = Eng(a, optA); B = Eng(bpath, optB)                       # last try: an error now ends the match with a message
    ra, rb = ({}, {}) if save else (None, None)
    try:
        r1, w1 = play(A, B, opening, mode, ra, adj)                  # A white
        x, w2 = play(B, A, opening, mode, rb, adj); r2 = 1.0 - x     # A black -> A's score
    finally:
        A.quit(); B.quit()
    return r1, r2, w1, w2, ra, rb


def make_openings(n, seed, ref, plies=8, cp=90):
    """n distinct random openings of `plies` plies whose depth-7 score (reference engine) is within +-cp"""
    rnd = random.Random(seed); out = []; seen = set(); e = Eng(ref); tries = 0
    while len(out) < n:
        tries += 1
        if tries > 400 * n + 2000:
            e.quit(); raise SystemExit(f"cannot find {n} distinct openings of {plies} plies with |depth-7 score| <= {cp} cp (found {len(out)}): relax --open-cp or raise --plies")
        b = chess.Board(); mv = []
        for _ in range(plies):
            l = list(b.legal_moves)
            if not l: break
            m = rnd.choice(l); b.push(m); mv.append(m.uci())
        if b.is_game_over() or len(mv) < plies or tuple(mv) in seen: continue
        seen.add(tuple(mv))
        e.newgame(); e.send("position startpos moves " + " ".join(mv)); e.send("go depth 7")
        sc = None
        end = time.time() + 20
        while time.time() < end:
            l = e.readline()
            if l.startswith("info") and " score cp " in l:
                t = l.split(); sc = int(t[t.index("cp") + 1])
            if l.startswith("bestmove"): break
        if sc is not None and abs(sc) <= cp: out.append(mv)
    e.quit(); return out


def epd_starts(path, pairs, seed):
    """a seeded sample of `pairs` legal start positions of a FEN / EPD file (all of them, shuffled, if it has fewer)"""
    fens = []
    for line in open(path):
        line = line.split("#", 1)[0].strip()
        if not line: continue
        t = line.split()
        fen = " ".join(t[:4] + (t[4:6] if len(t) >= 6 and t[4].isdigit() and t[5].isdigit() else ["0", "1"]))
        try:
            if chess.Board(fen).is_valid(): fens.append(fen)
        except ValueError:
            pass
    if not fens: raise SystemExit(f"no usable position in {path}")
    rnd = random.Random(seed)
    return rnd.sample(fens, pairs) if len(fens) > pairs else rnd.sample(fens, len(fens))


BOOK = os.path.join(HERE, "books", "noob_3moves.epd")


def book_starts(path, pairs, seed):
    """a seeded random sample of `pairs` legal start positions of an EPD / FEN book (one position per line; all of them, shuffled, if
    the book has fewer).  Only the positions drawn are checked, so a large book costs little."""
    lines = []
    with open(path) as fh:
        for line in fh:
            line = line.split("#", 1)[0].strip()
            if line: lines.append(line)
    if not lines: raise SystemExit(f"no position in the book {path}")
    order = list(range(len(lines)))
    random.Random(seed).shuffle(order)
    out = []
    for i in order:
        t = lines[i].split()
        fen = " ".join(t[:4] + (t[4:6] if len(t) >= 6 and t[4].isdigit() and t[5].isdigit() else ["0", "1"]))
        try:
            if chess.Board(fen).is_valid(): out.append(fen)
        except ValueError:
            pass
        if len(out) == pairs: break
    if not out: raise SystemExit(f"no usable position in the book {path}")
    return out


def get_openings(pairs, pool, seed, ref, plies, cp):
    """Opening list for one match.  Pools are cached per seed in tests/openings_<seed>.json (default plies/cp; otherwise with a suffix).
    With --pool larger than the number of pairs the match uses a random subset (also seeded) of the pool."""
    name = f"openings_{seed}.json" if (plies, cp) == (8, 90) else f"openings_{seed}_p{plies}_c{cp}.json"
    cache = os.path.join(HERE, name)
    want = max(pairs, pool)
    try:
        with open(cache) as fh: ops = json.load(fh) if os.path.exists(cache) else []
    except (OSError, ValueError):
        ops = []                                                       # unreadable / corrupt cache file: rebuild it
    if len(ops) < want:
        ops = make_openings(want, seed, ref, plies, cp)
        with open(cache + ".tmp", "w") as fh: json.dump(ops, fh)
        os.replace(cache + ".tmp", cache)                              # atomic: an interrupted run cannot leave a half-written pool
    if len(ops) > pairs:
        ops = random.Random(seed).sample(ops, pairs) if pool > pairs else ops[:pairs]
    return ops


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="see the module docstring")
    ap.add_argument("A"); ap.add_argument("B")
    ap.add_argument("--games", type=int, default=400, help="maximum number of games (2 per opening)")
    ap.add_argument("--no-adj", action="store_true", help="no win / draw adjudication (games end by the rules or the 400-ply cap): labels for Texel tuning then do not come from the engine's own score")
    ap.add_argument("--no-draw-adj", action="store_true", help="no draw adjudication (win adjudication kept): drawn labels then come from the rules only")
    ap.add_argument("--movetime", type=int, default=40)
    ap.add_argument("--nodes", type=int, default=0, help="fixed nodes per move (overrides --movetime)")
    ap.add_argument("--tc", default="", help="clock, e.g. 10+0.1 (seconds base + increment); overrides --movetime/--nodes")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--elo0", type=float, default=-3.0)
    ap.add_argument("--elo1", type=float, default=3.0)
    ap.add_argument("--alpha", type=float, default=0.05)
    ap.add_argument("--beta", type=float, default=0.05)
    ap.add_argument("--sprt", choices=("penta", "tri", "none"), default="penta")
    ap.add_argument("--nosprt", action="store_true", help="same as --sprt none")
    ap.add_argument("--seed", type=int, default=0, help="opening seed; 0 = draw a fresh one (printed)")
    ap.add_argument("--pool", type=int, default=0, help="random openings: size of the opening pool to draw the pairs from (default = number of pairs)")
    ap.add_argument("--plies", type=int, default=8, help="random openings: random plies per opening")
    ap.add_argument("--open-cp", type=int, default=90, help="random openings: reject openings with |depth-7 score| above this")
    ap.add_argument("--optA", default="", help='UCI options for A, e.g. "Threads=4;Hash=64"')
    ap.add_argument("--optB", default="")
    ap.add_argument("--min-pairs", type=int, default=50, help="no SPRT stop before this many pairs")
    ap.add_argument("--epd", default="", help="start positions from this FEN / EPD file instead of random openings")
    ap.add_argument("--book", default="auto", help="auto (default): tests/books/noob_3moves.epd if present, else random openings; random; or an EPD book file")
    ap.add_argument("--save-games", default="", metavar="FILE.jsonl",
                    help="append one JSON line per game: opening, adjudication, and every engine move as [fen, white-view cp, uci, is_capture, gives_check] "
                         "(for Texel tuning: keep quiet positions, i.e. is_capture=0 and gives_check=0, and drop adj=\"fail\" games)")
    a = ap.parse_args()
    if a.nosprt: a.sprt = "none"
    if a.tc: mode = ("tc",) + parse_tc(a.tc); desc = f"tc {a.tc}"
    elif a.nodes: mode = ("nodes", a.nodes); desc = f"nodes {a.nodes}"
    else: mode = ("movetime", a.movetime); desc = f"movetime {a.movetime} ms"
    seed = a.seed or (int(time.time()) % 1000000 + 1000)
    pairs = a.games // 2
    cpus = os.cpu_count() or 1
    thr = max([1] + [int(kv.partition("=")[2]) for o in (a.optA, a.optB) for kv in o.split(";") if kv.strip().lower().startswith("threads=")])
    if a.workers * thr > cpus and mode[0] != "nodes":
        print(f"WARNING: {a.workers} worker(s) x {thr} thread(s) > {cpus} CPUs: timings are distorted (use --workers 1 or --nodes)", flush=True)
    for path, opts in ((a.A, a.optA), (a.B, a.optB)):                  # pre-flight: both engines must start and answer uci / isready (clean message instead of a worker traceback)
        try: Eng(path, opts).quit()
        except (RuntimeError, OSError) as ex: sys.stderr.write(f"ERROR: cannot run the engine {path!r}: {ex}\n"); sys.exit(2)
    book = "" if a.epd or a.book == "random" else (a.book if a.book != "auto" else (BOOK if os.path.isfile(BOOK) else ""))
    if book and not os.path.isfile(book): sys.stderr.write(f"ERROR: book {book!r} not found\n"); sys.exit(2)
    if a.epd: ops = epd_starts(a.epd, pairs, seed)
    elif book: ops = book_starts(book, pairs, seed)
    else: ops = get_openings(pairs, a.pool, seed, a.B, a.plies, a.open_cp)
    src = f" from {a.epd}" if a.epd else (f" from book {os.path.basename(book)}" if book else " | random openings")
    print(f"match: {a.A} vs {a.B} | {desc} | seed {seed} | {len(ops)} pairs" + src + (" | no adjudication" if a.no_adj else (" | no draw adjudication" if a.no_draw_adj else "")) + f" | sprt {a.sprt} [{a.elo0:+g},{a.elo1:+g}] alpha {a.alpha} beta {a.beta}", flush=True)

    lo = math.log(a.beta / (1 - a.alpha)); hi = math.log((1 - a.beta) / a.alpha)
    W = D = L = F = 0; P = [0] * 5
    N = 0; elo = err = llr = 0.0
    with Pool(a.workers) as pool:
        # ordered imap: the SPRT must look at pairs in the order they were started (imap_unordered would favour short games)
        it = pool.imap(worker, [(a.A, a.B, a.optA, a.optB, o, mode, bool(a.save_games), False if a.no_adj else ("win" if a.no_draw_adj else True)) for o in ops])
        gf = open(a.save_games, "a") if a.save_games else None
        for n, (r1, r2, w1, w2, ra, rb) in enumerate(it, 1):
            if gf:
                for res, rec, who in ((r1, ra, "A_white"), (1.0 - r2, rb, "A_black")):
                    rec.update({"result_white": res, "who": who, "mode": list(mode), "movetime": mode[1] if mode[0] == "movetime" else None, "A": a.A, "B": a.B})
                    gf.write(json.dumps(rec) + "\n")
                gf.flush()
            F += (w1 == "fail") + (w2 == "fail")
            for r in (r1, r2):
                if r == 1.0: W += 1
                elif r == 0.0: L += 1
                else: D += 1
            P[int(round(2 * (r1 + r2)))] += 1
            N = W + D + L
            if a.sprt == "tri":
                c = [L, D, W]; elo, err = sprt.elo_and_error(c, sprt.TRI_X); llr = sprt.trinomial_llr(c, a.elo0, a.elo1)
            else:
                elo, err = sprt.elo_and_error(P, sprt.PENTA_X); llr = sprt.pentanomial_llr(P, a.elo0, a.elo1)
            if n % 5 == 0 or n == len(ops):
                print(f"games={N:5d} +{W} ={D} -{L}  pairs={P}  score={(W + 0.5 * D) / N:.3f}  Elo={elo:+.1f} +-{err:.1f}  LLR={llr:+.2f} [{lo:.2f},{hi:.2f}]"
                      + (f"  fails={F}" if F else ""), flush=True)
            if a.sprt != "none" and n >= a.min_pairs and (llr >= hi or llr <= lo):
                pool.terminate(); break
    verdict = "" if a.sprt == "none" else (" H1 accepted" if llr >= hi else (" H0 accepted" if llr <= lo else " (no decision)"))
    te, terr = sprt.elo_and_error([L, D, W], sprt.TRI_X)
    print(f"FINAL games={N} +{W} ={D} -{L} pairs={P} Elo={elo:+.1f} +-{err:.1f} (pairs; trinomial +-{terr:.1f}) LLR={llr:+.2f}{verdict} seed={seed} {desc}"
          + (f" fails={F}" if F else ""))
