#!/usr/bin/env python3
"""Own training positions: random positions of self-play games, each searched to a fixed depth by the engine, the moves of its principal
variation played on the board; the end of the PV is the training position, the game result its label.  The point is that the positions
are ones the engine itself reaches when it is allowed to play on.

usage: python3 tuner/resolve.py --engine zeval.exe GAMES.jsonl [...] --out own.epd [--depth 12] [--per-game 10] [--min-plies 20]
          [--workers N] [--seed 1] [--hash 16] [--drop-adj] [--max-cp 2000] [--no-qsearch]

Input: game files of tests/match.py --save-games / tuner/gen_data.py (one JSON game per line; the recorded plies are engine moves, the
opening is not part of them).  A folder stands for its *.jsonl files; patterns (sp_*.jsonl) are expanded here, so they work in cmd too.  Games with an engine failure (adj "fail") and games with fewer than --min-plies recorded plies are
skipped; --drop-adj also skips adjudicated games (win / draw / move cap: their labels partly come from the engine's own score).
Per game, --per-game distinct plies are drawn (seeded by --seed, the file name and the line: a run is reproducible).
For each drawn position: "ucinewgame", "go depth D"; the PV of the last completed depth is played on a python-chess board, each move
checked for legality (an illegal move ends the playback; a PV cut short by a TT hit is played as far as it goes).  Zeval's PV has no
quiescence moves, and about a fifth of the PV ends lie inside an exchange: the end position is then resolved to the end of its
capture-only quiescence PV with the evaluation of the engine build (tuner/extract.py qsearch_leaf, the switches read from the engine with
"evalcfg"; needs numpy).  --no-qsearch skips this (then use tuner/extract.py --qsearch later).
Kept: end positions not in check, not game over, |score| below --max-cp (no mate scores).

Output (--out): one line per position, "FEN [result] game=FILE:LINE" with the result from White's view (the format tuner/extract.py
reads; game= keeps the positions of one game in one group for the train / validation split); duplicates stay (extract.py merges them
and averages their labels).  Side file <out>.info (tab separated): source file, line, ply, PV length,
depth-D score (White's view), quiescence plies, end FEN.  Progress file <out>.done: one line per finished game; a stopped run continues where it stopped
when started again with the same arguments (a game cut off mid-way is redone: at worst its positions appear twice, with one label).
At the end: kept / dropped counts, the share of each result, and the piece-count histogram of the kept positions (phase balance).

Speed per worker (one core): depth 12 about 17 positions/s, the python quiescence search about 10/s, together about 6/s; 1,000,000
positions on 10 workers about 5 hours.  Needs python-chess and numpy (pip install chess numpy).  Windows and Linux."""
import argparse, glob, json, os, random, sys, time, zlib
from multiprocessing import Pool
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, "..", "tests"))
import chess                       # noqa: E402
from match import Eng              # noqa: E402

MATE = 30000
_eng = None
_cfg = None
_qs = None


def pick_plies(n_plies, k, seed, src, line):
    """the indices of k distinct recorded plies of a game (all of them when it has k or fewer), reproducible from seed / file / line"""
    rng = random.Random(seed * 1000003 + zlib.crc32(f"{os.path.basename(src)}:{line}".encode()))
    idx = list(range(n_plies))
    return sorted(rng.sample(idx, k)) if k < n_plies else idx


def parse_info(line):
    """an 'info' line -> (depth, score cp from the side to move (mate = +-MATE), bound or "", pv list) or None without depth / score / pv"""
    t = line.split()
    if "depth" not in t or "score" not in t or "pv" not in t: return None
    try:
        d = int(t[t.index("depth") + 1])
        i = t.index("score")
        kind, v = t[i + 1], int(t[i + 2])
    except (ValueError, IndexError):
        return None
    sc = v if kind == "cp" else (MATE if v > 0 else -MATE)
    bound = t[i + 3] if i + 3 < len(t) and t[i + 3] in ("lowerbound", "upperbound") else ""
    return d, sc, bound, t[t.index("pv") + 1:]


def search_pv(eng, fen, depth, timeout=120.0):
    """(score cp side to move, pv) of the last completed depth: the last exact-score info line before bestmove"""
    eng.send("ucinewgame"); eng.send("isready"); eng.wait("readyok", 60)
    eng.send("position fen " + fen); eng.send(f"go depth {depth}")
    best = None; end = time.time() + timeout; stopped = False
    while True:
        if time.time() > end and not stopped:
            eng.send("stop"); stopped = True; end = time.time() + 10
        elif time.time() > end:
            raise RuntimeError("no bestmove")
        l = eng.readline()
        if not l: continue
        if l.startswith("info"):
            r = parse_info(l)
            if r and not r[2]: best = r
        elif l.startswith("bestmove"):
            return (best[1], best[3], best[0]) if best else (None, [], 0)


def play_pv(fen, pv):
    """the board after the legal prefix of pv, and the number of moves played"""
    b = chess.Board(fen); n = 0
    for u in pv:
        try: m = chess.Move.from_uci(u)
        except ValueError: break
        if m not in b.legal_moves: break
        b.push(m); n += 1
    return b, n


def label_text(r):
    return "[1.0]" if r == 1.0 else ("[0.0]" if r == 0.0 else "[0.5]")


def _init(cfg):
    global _eng, _cfg, _qs
    _cfg = cfg
    _eng = Eng(cfg["engine"], f"Hash={cfg['hash']};Threads=1")
    if cfg["evalcfg"] is not None:
        import extract, model
        model.set_cfg(cfg["evalcfg"]); _qs = extract.qsearch_leaf


def work(job):
    """job = (src, line, game dict) -> (src, line, [epd lines], [info lines], counters)"""
    src, line, g = job
    cnt = {"drawn": 0, "kept": 0, "check": 0, "over": 0, "mate": 0, "nopv": 0}
    out, info = [], []
    res = g["result_white"]
    for i in pick_plies(len(g["plies"]), _cfg["per_game"], _cfg["seed"], src, line):
        fen = g["plies"][i][0]
        cnt["drawn"] += 1
        sc, pv, d = search_pv(_eng, fen, _cfg["depth"])
        if sc is None or not pv: cnt["nopv"] += 1; continue
        b, n = play_pv(fen, pv)
        stm0 = fen.split()[1]
        scw = sc if stm0 == "w" else -sc
        if abs(sc) >= _cfg["max_cp"]: cnt["mate"] += 1; continue
        q = 0
        if _qs is not None and not b.is_check():
            k0 = len(b.move_stack); b = _qs(b); q = len(b.move_stack) - k0
        if b.is_game_over(claim_draw=False): cnt["over"] += 1; continue
        if b.is_check(): cnt["check"] += 1; continue
        cnt["kept"] += 1
        e = b.fen()
        out.append(f"{e} {label_text(res)} game={os.path.basename(src).replace(' ', '_')}:{line}")
        info.append(f"{os.path.basename(src)}\t{line}\t{i}\t{n}\t{scw}\t{q}\t{e}")
    return src, line, out, info, cnt


def games(paths, done, min_plies, drop_adj, skip):
    for src in paths:
        with open(src) as fh:
            for line, txt in enumerate(fh, 1):
                if (os.path.basename(src), line) in done: continue
                try: g = json.loads(txt)
                except ValueError: skip["bad"] += 1; continue
                if g.get("adj") == "fail": skip["fail"] += 1; continue
                if drop_adj and g.get("adj") in ("win", "draw", "len"): skip["adj"] += 1; continue
                if len(g.get("plies", [])) < min_plies: skip["short"] += 1; continue
                if g.get("result_white") not in (0.0, 0.5, 1.0): skip["bad"] += 1; continue
                yield src, line, g


def npieces(fen):
    return sum(c.isalpha() for c in fen.split()[0])


def summary(path):
    """counts per result and the piece-count histogram of an output file"""
    res = {"[1.0]": 0, "[0.5]": 0, "[0.0]": 0}; hist = {}
    with open(path) as fh:
        for l in fh:
            t = l.split()
            if len(t) < 7: continue
            res[t[6]] = res.get(t[6], 0) + 1
            b = min(28, (npieces(l) // 4) * 4); hist[b] = hist.get(b, 0) + 1
    return res, hist


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("games", nargs="+", help="game files (.jsonl) of tests/match.py --save-games / tuner/gen_data.py")
    ap.add_argument("--engine", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--depth", type=int, default=12); ap.add_argument("--per-game", type=int, default=10)
    ap.add_argument("--min-plies", type=int, default=20, help="skip games with fewer recorded plies (engine moves after the opening)")
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 1); ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--hash", type=int, default=16, help="engine hash (MB) per worker")
    ap.add_argument("--drop-adj", action="store_true", help="skip adjudicated games (win / draw / move cap)")
    ap.add_argument("--max-cp", type=int, default=2000, help="drop positions whose depth-D score is this large or larger (mate scores)")
    ap.add_argument("--no-qsearch", action="store_true", help="do not resolve the PV ends with the quiescence search (resolve later: extract.py --qsearch)")
    a = ap.parse_args()
    paths = []
    for p in a.games:
        got = sorted(glob.glob(os.path.join(p, "*.jsonl"))) if os.path.isdir(p) else (sorted(glob.glob(p)) if any(c in p for c in "*?[") else [p])
        if not got or not all(os.path.isfile(x) for x in got): raise SystemExit(f"{p}: no game file found")
        paths += [x for x in got if x not in paths]
    if len({os.path.basename(x) for x in paths}) != len(paths): raise SystemExit("two game files with the same name (the progress file keys on the name)")
    if a.per_game < 1 or a.depth < 1: raise SystemExit("--per-game and --depth must be at least 1")
    evalcfg = None
    if not a.no_qsearch:
        try: import numpy  # noqa: F401
        except ImportError: raise SystemExit("the quiescence resolution needs numpy (pip install numpy), or use --no-qsearch")
        from _eng import resolve as eng_cmd
        import check_eval
        evalcfg = check_eval.engine_cfg(eng_cmd(a.engine))
        if evalcfg is None: raise SystemExit(f"{a.engine}: no evalcfg answer (not a Zeval build?); use --no-qsearch")
    cfg = {"engine": a.engine, "hash": a.hash, "depth": a.depth, "per_game": a.per_game, "seed": a.seed, "max_cp": a.max_cp, "evalcfg": evalcfg}
    args_line = json.dumps({**{k: cfg[k] for k in ("depth", "per_game", "seed", "max_cp", "evalcfg")}, "min_plies": a.min_plies, "drop_adj": a.drop_adj})
    donef = a.out + ".done"; done = set()
    if os.path.exists(donef):
        with open(donef) as fh:
            first = fh.readline().strip()
            if first != args_line:
                raise SystemExit(f"{donef} was written with other arguments ({first}); use another --out or the same arguments")
            for l in fh:
                t = l.rstrip("\n").split("\t")
                if len(t) == 2: done.add((t[0], int(t[1])))
        print(f"resume: {len(done)} games already done", flush=True)
    else:
        with open(donef, "w") as fh: fh.write(args_line + "\n")
    skip = {"fail": 0, "adj": 0, "short": 0, "bad": 0}
    tot = {"drawn": 0, "kept": 0, "check": 0, "over": 0, "mate": 0, "nopv": 0}
    t0 = time.time(); ng = 0
    with open(a.out, "a") as fo, open(a.out + ".info", "a") as fi, open(donef, "a") as fd, \
            Pool(a.workers, initializer=_init, initargs=(cfg,)) as pool:
        for src, line, out, info, cnt in pool.imap_unordered(work, games(paths, done, a.min_plies, a.drop_adj, skip), chunksize=4):
            for l in out: fo.write(l + "\n")
            for l in info: fi.write(l + "\n")
            fo.flush(); fi.flush()
            fd.write(f"{os.path.basename(src)}\t{line}\n"); fd.flush()
            for k in tot: tot[k] += cnt[k]
            ng += 1
            if ng % 200 == 0:
                el = time.time() - t0
                print(f"games {ng}  positions {tot['kept']}  ({tot['drawn'] / el:.1f} searches/s)", flush=True)
    print(f"done: {ng} games this run; searched {tot['drawn']}, kept {tot['kept']}; dropped: in check {tot['check']}, game over {tot['over']}, "
          f"mate / |score| >= {a.max_cp} {tot['mate']}, no PV {tot['nopv']}; games skipped: fail {skip['fail']}, adjudicated {skip['adj']}, "
          f"short {skip['short']}, unreadable {skip['bad']}  ({time.time() - t0:.0f} s)", flush=True)
    res, hist = summary(a.out)
    n = sum(res.values()) or 1
    print(f"{a.out}: {n} positions  white wins {100 * res['[1.0]'] / n:.1f} %  draws {100 * res['[0.5]'] / n:.1f} %  black wins "
          f"{100 * res['[0.0]'] / n:.1f} %", flush=True)
    print("pieces  " + "  ".join(f"{k}-{k + 3 if k < 28 else 32}: {100 * hist[k] / n:.1f} %" for k in sorted(hist)), flush=True)


if __name__ == "__main__":
    main()
