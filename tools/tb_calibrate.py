#!/usr/bin/env python3
"""Calibrate an engine's evaluation against Syzygy WDL, class by class (no games).

For each material class (e.g. KRvKB, KRPvKR) it draws random legal positions, labels them with the Syzygy WDL, asks the engine for
  - the static eval ('eval' command; only on QUIET positions: no capture for either side, not in check), and optionally
  - a shallow search score ('go nodes N'),
and reports how well the score separates decisive from drawn positions and whether its sign is right.
The same seed gives the same positions for every engine, so two builds can be compared side by side.

usage:  tb_calibrate.py --tb DIR --engine ./zeval [--engine ./zeval_cand] [--classes KRvKB,KRvKN,KRPvKR] [--n 400] [--seed 1] [--nodes 0]
        tb_calibrate.py --dry-run ...   (no tablebase: only prints the engine's own score distribution per class; NO verdict)
Needs python-chess; Syzygy WDL files (*.rtbw) of every class in DIR (3-5 men: about 1 GB for all of them).

WDL labels (Syzygy, side to move): +2 win, +1 cursed win (win only without the 50-move rule), 0 draw, -1 blessed loss, -2 loss.
+-1 are counted as DRAWN here because the engine plays under the 50-move rule; they are reported separately as 'cursed'."""
import argparse, random, subprocess, sys, statistics, re
import chess
import chess.syzygy

PT = {"K": chess.KING, "Q": chess.QUEEN, "R": chess.ROOK, "B": chess.BISHOP, "N": chess.KNIGHT, "P": chess.PAWN}
DEFAULT = "KRvKB,KRvKN,KRvKP,KQvKR,KQvKP,KBPvKB,KBPvKN,KRPvKR,KPvKP,KNNvKP,KBNvK,KRPvKB"


def parse(cls):
    w, b = cls.split("v")
    return w, b


def random_position(rnd, cls):
    w, b = parse(cls)
    if rnd.random() < 0.5: w, b = b, w                      # the stronger side gets both colours
    for _ in range(1000):
        board = chess.Board(None); used = set(); ok = True
        for col, spec in ((chess.WHITE, w), (chess.BLACK, b)):
            for ch in spec:
                for _ in range(100):
                    sq = rnd.randrange(64)
                    if sq in used: continue
                    if ch == "P" and not (8 <= sq <= 55): continue
                    used.add(sq); board.set_piece_at(sq, chess.Piece(PT[ch], col)); break
                else: ok = False
        if not ok: continue
        board.turn = rnd.random() < 0.5
        if board.is_valid(): return board
    raise RuntimeError("cannot place " + cls)


def quiet(board):
    """no capture available to the side to move and (after a null move) none to the other side; not in check"""
    if board.is_check(): return False
    if any(board.is_capture(m) for m in board.legal_moves): return False
    board.push(chess.Move.null())
    q = not any(board.is_capture(m) for m in board.legal_moves)
    board.pop()
    return q


class Eng:
    def __init__(self, path):
        self.p = subprocess.Popen([path], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
        self.w("uci"); self.until("uciok"); self.w("isready"); self.until("readyok")

    def w(self, s): self.p.stdin.write(s + "\n"); self.p.stdin.flush()

    def until(self, tok):
        while True:
            l = self.p.stdout.readline()
            if not l: raise RuntimeError("engine died")
            if l.startswith(tok): return l

    def static(self, fen):
        self.w("position fen " + fen); self.w("eval")
        while True:
            l = self.p.stdout.readline()
            if not l: raise RuntimeError("engine died")
            m = re.match(r"eval (-?\d+)", l)
            if m: return int(m.group(1))

    def search(self, fen, nodes):
        self.w("ucinewgame"); self.w("position fen " + fen); self.w(f"go nodes {nodes}")
        sc = 0
        while True:
            l = self.p.stdout.readline()
            if not l: raise RuntimeError("engine died")
            if l.startswith("info") and " score " in l:
                t = l.split(); i = t.index("score")
                sc = int(t[i + 2]) if t[i + 1] == "cp" else (30000 if int(t[i + 2]) > 0 else -30000)
            if l.startswith("bestmove"): return sc

    def close(self):
        try: self.w("quit")
        except Exception: pass


def auc(pos_scores, neg_scores):
    """P(|score| of a decisive position > |score| of a drawn one), ties count 1/2 (Mann-Whitney)"""
    if not pos_scores or not neg_scores: return float("nan")
    allv = sorted([(s, 1) for s in pos_scores] + [(s, 0) for s in neg_scores])
    rank = {}; i = 0
    while i < len(allv):
        j = i
        while j < len(allv) and allv[j][0] == allv[i][0]: j += 1
        for k in range(i, j): rank.setdefault(k, (i + j + 1) / 2)
        i = j
    rsum = sum(rank[k] for k, (s, l) in enumerate(allv) if l == 1)
    n1, n0 = len(pos_scores), len(neg_scores)
    return (rsum - n1 * (n1 + 1) / 2) / (n1 * n0)


def pct(v, p):
    v = sorted(v); return v[min(len(v) - 1, int(p * len(v)))] if v else float("nan")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tb"); ap.add_argument("--engine", action="append", required=True)
    ap.add_argument("--classes", default=DEFAULT); ap.add_argument("--n", type=int, default=400)
    ap.add_argument("--seed", type=int, default=1); ap.add_argument("--nodes", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    tb = None if a.dry_run else chess.syzygy.open_tablebase(a.tb)
    engines = [Eng(p) for p in a.engine]
    kinds = ["static"] + (["search"] if a.nodes else [])
    for cls in a.classes.split(","):
        rnd = random.Random(f"{a.seed}-{cls}")
        data = []                                            # (fen, wdl or None)
        tries = 0
        while len(data) < a.n and tries < 50 * a.n:
            tries += 1
            b = random_position(rnd, cls)
            if not quiet(b): continue
            try: wdl = None if tb is None else tb.probe_wdl(b)
            except (KeyError, chess.syzygy.MissingTableError): raise SystemExit(f"missing table for {cls} in {a.tb}")
            data.append((b.fen(), wdl))
        print(f"\n=== {cls}   {len(data)} quiet positions" + ("" if tb is None else
              f"   decisive {sum(1 for _, w in data if w in (2, -2))}  drawn {sum(1 for _, w in data if w == 0)}  cursed/blessed {sum(1 for _, w in data if w in (1, -1))}"))
        for e, path in zip(engines, a.engine):
            for kind in kinds:
                sc = [(e.static(f) if kind == "static" else e.search(f, a.nodes), w) for f, w in data]
                if tb is None:
                    v = [abs(s) for s, _ in sc]
                    print(f"  {path:28s} {kind:6s} |score| median {pct(v, .5):6.0f}  p90 {pct(v, .9):6.0f}   (dry run: no tablebase, no verdict)")
                    continue
                dec = [(s, w) for s, w in sc if w in (2, -2)]; drw = [s for s, w in sc if w in (0, 1, -1)]
                sign_ok = sum(1 for s, w in dec if (s > 0) == (w > 0) and s != 0) / max(1, len(dec))
                false_draw = sum(1 for s, w in dec if abs(s) < 50) / max(1, len(dec))
                false_win = sum(1 for s in drw if abs(s) > 150) / max(1, len(drw))
                print(f"  {path:28s} {kind:6s} AUC {auc([abs(s) for s, _ in dec], [abs(s) for s in drw]):.3f}"
                      f" | decisive: sign right {100*sign_ok:5.1f}%  |score|<50 {100*false_draw:5.1f}%  median |s| {pct([abs(s) for s, _ in dec], .5):5.0f}"
                      f" | drawn: median |s| {pct([abs(s) for s in drw], .5):5.0f}  p90 {pct([abs(s) for s in drw], .9):5.0f}  |s|>150 {100*false_win:5.1f}%")
    for e in engines: e.close()


if __name__ == "__main__":
    main()
