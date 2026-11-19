#!/usr/bin/env python3
"""Labelled positions -> sparse feature dataset (.npz) for tune.py.

Inputs (any mix):
  * game files written by  tests/match.py --save-games g.jsonl   (one JSON game per line: every engine move as
    [fen, white-view cp, uci, is_capture, gives_check]; label = game result)
  * labelled position files, one position per line:  FEN + result marker, e.g.
        rnbqkbnr/... w KQkq - 0 1; [1.0]          (result in brackets: also [0.5] [0.0] [0.62])
        rnbqkbnr/... w KQkq - c9 "1-0";           (result as an EPD opcode: 1-0 / 1/2-1/2 / 0-1)
    --stm-wdl: the label is from the side-to-move's view (default: White's).
  * a sources file (csv):   --sources sources.csv   lines "path,wdl_from_side_to_move(0/1),limit"  (# = comment)

--limit N / the 3rd csv column count INPUT LINES per file, not positions:  a .jsonl file = N GAMES (each gives ~80 positions), an EPD file = N positions.
The first N lines are taken (no sampling): for an ordered file, shuffle it first.

usage: python3 tuner/extract.py OUT.npz  A.jsonl B.epd ...  [--sources s.csv] [--stm-wdl] [--limit N]
          [--min-pieces 6] [--max-cp 3000] [--per-game 0] [--drop-adjudicated] [--seed 1] [--workers N]

Filters for game data (quiet positions only, as the Texel method needs):
  * the side to move is not in check (= the previous move did not give check; the first recorded ply of a game is dropped)
  * the engine's chosen move is not a capture and gives no check;  games with an engine failure / flag fall (adj "fail") are dropped
  * |score| < --max-cp (mate scores out);  fewer than --min-pieces pieces on the board
  * --qsearch: resolve each position to the end of its capture-only quiescence PV with the engine's own eval (for data that is not quiet); --filter-in-check for EPD files
  * duplicates (same placement + side + castling + e.p.) are merged, their labels averaged
  * --per-game N: keep N random positions per game (less correlated data; 0 = all)
Labelled EPD files are NOT filtered further unless asked (public labelled datasets are normally already quiet): --filter-in-check / --qsearch need python-chess.

Stored per position: sparse (white - black) feature counts (piece-square entries, bishop pair, terms of the full evaluation), phase, side to move, label (White's view, 0 / 0.5 / 1), the engine's
white-view cp score (NaN for EPD data), a group id (a game, or a single position) used to split train / validation."""
import argparse, hashlib, json, os, random, re, sys, time
from multiprocessing import Pool
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import model

RES = {"1-0": 1.0, "0-1": 0.0, "1/2-1/2": 0.5}
_Q = re.compile(r'"(1-0|0-1|1/2-1/2)"')
_R = re.compile(r"(?<![\w./-])(1-0|0-1|1/2-1/2)(?![\w./-])")
_B = re.compile(r"\[\s*(1(?:\.0+)?|0(?:\.\d+)?)\s*\]")
_N = re.compile(r"(?<![\w./-])(1\.0+|0\.\d+)(?![\w./-])")


def parse_label(rest):
    """label from the text after the 4th FEN field (or None).  Quoted / bare result, [x] or a bare decimal (0.5, 1.0, 0.62)."""
    m = _Q.search(rest) or _R.search(rest)
    if m:
        return RES[m.group(1)]
    m = _B.search(rest)
    if m:
        return float(m.group(1))
    m = _N.search(rest)
    return float(m.group(1)) if m else None


def key_hash(fen):
    f = fen.split()
    k = " ".join(f[:4])
    return int.from_bytes(hashlib.blake2b(k.encode(), digest_size=8).digest(), "little")


# ------------------------------------------------------------------------------------------------ qsearch (optional, needs python-chess)
_TAB = {}


def _tabs():
    """the tables of the CURRENT src/eval.asm (the quiescence search's static eval)"""
    if not _TAB:
        _TAB["t"] = model.load_tables()
    return _TAB["t"]


_PV = {1: 100, 2: 300, 3: 300, 4: 500, 5: 900, 6: 0}


def board_eval_stm(board):
    """static eval of a python-chess board, side-to-move view: the engine's eval (model.int_eval).  A position of a special evaluation
    (KQ-KR, KR-KB, bare king ...: model.int_eval gives None, it is outside the linear model) gets the plain material balance, so that the
    quiescence search still sees the captures there; such positions are never training positions anyway (fen_features marks them special)."""
    e = model.int_eval(board.fen(), _tabs())
    if e is None:
        e = sum(_PV[p.piece_type] * (1 if p.color == board.turn else -1) for p in board.piece_map().values())
    return e


def _qs(board, alpha, beta, ply, pv):
    """captures-only alpha-beta with stand pat and MVV-LVA ordering; pv = list of the best line (filled)"""
    stand = board_eval_stm(board)
    if stand >= beta or ply >= 40:
        return stand
    if stand > alpha:
        alpha = stand
    moves = list(board.generate_legal_captures())
    if not moves:
        return alpha
    def mvv(m):
        victim = _PV[1] if board.is_en_passant(m) else _PV[board.piece_at(m.to_square).piece_type]
        return (victim << 16) - _PV[board.piece_at(m.from_square).piece_type]
    moves.sort(key=mvv, reverse=True)
    best = alpha
    for m in moves:
        board.push(m); child = []
        sc = -_qs(board, -beta, -alpha, ply + 1, child)
        board.pop()
        if sc > best:
            best = sc
            if sc > alpha:
                alpha = sc; pv[:] = [m] + child
                if sc >= beta:
                    break
    return best


def qsearch_leaf(board):
    """the position at the end of the quiescence PV (used by --qsearch and --keep-noisy)"""
    pv = []
    _qs(board, -1 << 20, 1 << 20, 0, pv)
    for m in pv:
        board.push(m)
    return board


class Acc:
    def __init__(self):
        self.lens = []; self.idx = []; self.val = []; self.phase = []; self.stm = []; self.y = []; self.cp = []; self.grp = []; self.h = []; self.sf = []

    def add(self, fen, y, cp, grp):
        """False (nothing stored) for a position of a special evaluation: it is outside the linear model"""
        F = model.fen_features(fen)
        if F.special:
            return False
        self.lens.append(len(F.cols)); self.idx.extend(F.cols); self.val.extend(F.vals)
        self.phase.append(F.phase); self.stm.append(1 if F.wtm else -1); self.y.append(y); self.cp.append(cp); self.grp.append(grp)
        self.h.append(key_hash(fen)); self.sf.append(F.sf)
        return True

    def arrays(self):
        return {"lens": np.array(self.lens, np.int32), "indices": np.array(self.idx, np.int16), "data": np.array(self.val, np.int16),
                "phase": np.array(self.phase, np.uint8), "stm": np.array(self.stm, np.int8), "y": np.array(self.y, np.float32),
                "cp": np.array(self.cp, np.float32), "group": np.array(self.grp, np.int64), "hash": np.array(self.h, np.uint64),
                "sf": np.array(self.sf, np.uint8)}


def npieces(fen):
    n = 0
    for ch in fen.split(" ", 1)[0]:
        if ch.isalpha(): n += 1
    return n


def work_games(args):
    lines, base_group, o = args
    model.set_cfg(o["cfg"])
    acc = Acc(); st = dict(games=0, failed=0, adj=0, plies=0, kept=0, special=0)
    for gi, line in enumerate(lines):
        try:
            g = json.loads(line)
        except ValueError:
            continue
        st["games"] += 1
        if g.get("adj") == "fail":
            st["failed"] += 1; continue
        if o["drop_adj"] and g.get("adj") in ("win", "draw"):
            st["adj"] += 1; continue
        res = float(g["result_white"]); pl = g["plies"]
        cand = []
        for i in range(1, len(pl)):
            fen, wsc, uci, cap, chk = pl[i]
            st["plies"] += 1
            if pl[i - 1][4] or ((cap or chk) and not o["keep_noisy"]):          # in check / noisy move (--keep-noisy keeps the noisy ones, they are resolved below)
                continue
            if abs(wsc) >= o["max_cp"] or npieces(fen) < o["min_pieces"]:
                continue
            cand.append(i)
        if o["per_game"] and len(cand) > o["per_game"]:
            cand = sorted(random.Random(o["seed"] * 1000003 + base_group + gi).sample(cand, o["per_game"]))
        for i in cand:
            fen = pl[i][0]
            noisy = bool(pl[i][3] or pl[i][4])
            if o["qsearch"] or (o["keep_noisy"] and noisy):
                import chess
                fen = qsearch_leaf(chess.Board(fen)).fen()
                if noisy: st["noisy_resolved"] = st.get("noisy_resolved", 0) + 1
            if acc.add(fen, res, float(pl[i][1]), base_group + gi): st["kept"] += 1
            else: st["special"] += 1
    return acc.arrays(), st


def work_epd(args):
    lines, base_group, o = args
    model.set_cfg(o["cfg"])
    acc = Acc(); st = dict(bad=0, small=0, kept=0, special=0)
    for li, line in enumerate(lines):
        f = line.split(None, 4)
        if len(f) < 4:
            st["bad"] += 1; continue
        fen = " ".join(f[:4])
        y = parse_label(f[4] if len(f) > 4 else "")             # text after the 4th field
        if y is None:
            st["bad"] += 1; continue
        try:
            wtm = f[1] == "w"
            if o["stm"] and not wtm: y = 1.0 - y
            if npieces(fen) < o["min_pieces"]:
                st["small"] += 1; continue
            if o["filter_check"] or o["qsearch"]:
                import chess
                b = chess.Board(fen + " 0 1")
                if o["filter_check"] and b.is_check():
                    st["in_check"] = st.get("in_check", 0) + 1; continue
                if o["qsearch"]:
                    fen = qsearch_leaf(b).fen()
            if acc.add(fen, y, float("nan"), base_group + li): st["kept"] += 1
            else: st["special"] += 1
        except (ValueError, KeyError, IndexError):
            st["bad"] += 1
    return acc.arrays(), st


def chunks(path, size, limit):
    buf = []; n = 0
    with open(path, "r", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"): continue
            buf.append(line); n += 1
            if len(buf) >= size:
                yield buf; buf = []
            if limit and n >= limit: break
    if buf: yield buf


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out"); ap.add_argument("inputs", nargs="*")
    ap.add_argument("--sources", default="", help="csv: path,wdl_from_side_to_move(0/1),limit   (limit = input lines: games for .jsonl, positions for EPD)")
    ap.add_argument("--stm-wdl", action="store_true"); ap.add_argument("--limit", type=int, default=0, help="first N input LINES of every file (jsonl: N games; EPD: N positions)")
    ap.add_argument("--min-pieces", type=int, default=6); ap.add_argument("--max-cp", type=int, default=3000)
    ap.add_argument("--per-game", type=int, default=0); ap.add_argument("--drop-adjudicated", action="store_true")
    ap.add_argument("--seed", type=int, default=1); ap.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    ap.add_argument("--switches", default="", help="evaluation switches of the engine to be tuned, e.g. EV_THREATS=1,EV_KING2=1 (default: the defaults of "
                    "src/eval.asm; must match the build that the tuned values go into)")
    ap.add_argument("--compress", action="store_true")
    ap.add_argument("--qsearch", action="store_true", help="resolve every position to the end of its quiescence PV (python-chess, a few hundred positions/s per core).  "
                    "Needed only for data that is NOT already quiet.  Uses the eval of src/eval.asm")
    ap.add_argument("--keep-noisy", action="store_true", help="game data: do not drop positions whose chosen move is a capture / check; resolve them with the capture-only quiescence search instead (python-chess).  "
                    "Keeping such hard positions can improve the tuned eval; default off (positions are dropped)")
    ap.add_argument("--filter-in-check", action="store_true", help="EPD data: drop positions where the side to move is in check (python-chess)")
    a = ap.parse_args()
    model.set_switches(a.switches)
    print(f"evaluation switches: {model.cfg_text()}")
    srcs = [(p, a.stm_wdl, a.limit) for p in a.inputs]
    if a.sources:
        for ln in open(a.sources):
            ln = ln.strip()
            if not ln or ln.startswith("#"): continue
            c = [x.strip() for x in ln.split(",")]
            srcs.append((c[0], len(c) > 1 and c[1] == "1", int(c[2]) if len(c) > 2 else 0))
    if not srcs:
        raise SystemExit("no input files")
    t0 = time.time(); parts = []; tot = {}
    with Pool(a.workers) as pool:
        base = 0
        for path, stm, limit in srcs:
            is_games = path.endswith(".jsonl") or path.endswith(".json")
            o = dict(cfg=model.cfg_mask(), min_pieces=a.min_pieces, max_cp=a.max_cp, per_game=a.per_game, drop_adj=a.drop_adjudicated, seed=a.seed, stm=stm,
                     qsearch=a.qsearch, filter_check=a.filter_in_check, keep_noisy=a.keep_noisy)
            jobs = []
            for ch in chunks(path, 400 if is_games else 20000, limit):
                jobs.append((ch, base, o)); base += len(ch)
            fn = work_games if is_games else work_epd
            n = 0
            for arr, st in pool.imap(fn, jobs):
                parts.append(arr); n += len(arr["y"])
                for k, v in st.items(): tot[k] = tot.get(k, 0) + v
            print(f"{path}: {n} positions  [{time.time() - t0:.0f}s]", flush=True)
    cat = {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}
    N0 = len(cat["y"])
    # merge duplicate positions: the first occurrence's features are kept, label / cp are averaged over the duplicates
    uniq, first_idx, inv = np.unique(cat["hash"], return_index=True, return_inverse=True)
    cnt = np.bincount(inv).astype(np.float64)
    ymean = np.bincount(inv, weights=cat["y"].astype(np.float64)) / cnt
    cpv = cat["cp"].astype(np.float64); okc = ~np.isnan(cpv)
    cpn = np.bincount(inv, weights=okc.astype(np.float64))
    cpmean = np.where(cpn > 0, np.bincount(inv, weights=np.where(okc, cpv, 0.0)) / np.maximum(cpn, 1), np.nan)
    order = np.argsort(first_idx)                                       # unique ids in order of first appearance
    first = first_idx[order]; y = ymean[order]; cp = cpmean[order]
    offs = np.concatenate([[0], np.cumsum(cat["lens"], dtype=np.int64)])
    sel_len = cat["lens"][first]
    if len(first) < N0:
        idx = np.concatenate([cat["indices"][offs[i]:offs[i + 1]] for i in first])
        dat = np.concatenate([cat["data"][offs[i]:offs[i + 1]] for i in first])
    else:
        idx, dat = cat["indices"], cat["data"]
    out = dict(indptr=np.concatenate([[0], np.cumsum(sel_len, dtype=np.int64)]), indices=idx, data=dat,
               phase=cat["phase"][first], stm=cat["stm"][first], y=y.astype(np.float32), cp=np.asarray(cp, np.float32),
               group=cat["group"][first], sf=cat["sf"][first], cfg=np.array([model.cfg_mask()], np.int64))
    (np.savez_compressed if a.compress else np.savez)(a.out, **out)
    N = len(out["y"])
    print(f"stats: {tot}")
    print(f"{N0} positions -> {N} after merging {N0 - N} duplicates; mean label {out['y'].mean():.3f}; mean non-zero features {out['indices'].size / max(N, 1):.1f}; "
          f"games/groups {len(np.unique(out['group']))}  -> {a.out}  [{time.time() - t0:.0f}s]")


if __name__ == "__main__":
    main()
