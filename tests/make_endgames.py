#!/usr/bin/env python3
"""Build a set of endgame start positions for tests/match.py --epd (endgame features are measured where they act).

Reads labelled EPD / FEN files (the tuner's data sets: any line whose first 4 fields are a FEN), keeps QUIET positions (side to move not in
check, no capture for either side) with 6 or more men (5 men and fewer are tablebase territory: tests/t_epd_play.py and tools/tb_calibrate.py),
at least one pawn and at most --maxpieces pieces besides kings and pawns, sorts them into families by material, draws a seeded sample of
each family and keeps only the positions a reference engine scores within +-cp at a shallow depth (contested positions decide games):
  pawn      kings and pawns only
  rook      one rook each, nothing else
  minor     only bishops / knights, at most two each, not opposite-coloured single bishops
  ocb       one bishop each on opposite colours (other pieces allowed)
  rminor    rooks and minor pieces, at least one minor piece, equal piece counts
  queen     a queen on the board
  imbal     unequal pieces without a queen (rook against a minor piece, two minors against a rook ...)
usage: make_endgames.py OUT.epd DATA.epd [DATA2.epd ...] --engine ./zeval [--per 250] [--cp 200] [--depth 6] [--maxpieces 4] [--seed 1]
       [--scan 3000000]   (lines read per input file at most)
Writes one FEN per line with the family as a comment ('FEN  # family').  Needs python-chess."""
import argparse, random, subprocess, sys, collections
import chess

FAMILIES = ("pawn", "rook", "minor", "ocb", "rminor", "queen", "imbal")


def family(b):
    c = {col: {pt: len(b.pieces(pt, col)) for pt in (chess.PAWN, chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN)} for col in (chess.WHITE, chess.BLACK)}
    pcs = {col: sum(c[col][pt] for pt in (chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN)) for col in c}
    if c[chess.WHITE][chess.QUEEN] or c[chess.BLACK][chess.QUEEN]:
        return "queen"
    if pcs[chess.WHITE] == 0 and pcs[chess.BLACK] == 0:
        return "pawn"
    wb, bb = b.pieces(chess.BISHOP, chess.WHITE), b.pieces(chess.BISHOP, chess.BLACK)
    if len(wb) == 1 and len(bb) == 1 and bool(wb & chess.BB_DARK_SQUARES) != bool(bb & chess.BB_DARK_SQUARES):
        return "ocb"
    rk = {col: c[col][chess.ROOK] for col in c}
    mi = {col: c[col][chess.KNIGHT] + c[col][chess.BISHOP] for col in c}
    if rk[chess.WHITE] == 1 and rk[chess.BLACK] == 1 and mi[chess.WHITE] == 0 and mi[chess.BLACK] == 0:
        return "rook"
    if rk[chess.WHITE] == 0 and rk[chess.BLACK] == 0 and mi[chess.WHITE] <= 2 and mi[chess.BLACK] <= 2 and mi[chess.WHITE] == mi[chess.BLACK]:
        return "minor"
    if rk[chess.WHITE] == rk[chess.BLACK] and mi[chess.WHITE] == mi[chess.BLACK]:
        return "rminor"
    return "imbal"


def quiet(b):
    if b.is_check() or any(b.is_capture(m) for m in b.legal_moves):
        return False
    b.push(chess.Move.null())
    q = not any(b.is_capture(m) for m in b.legal_moves)
    b.pop()
    return q


class Eng:
    def __init__(self, path):
        self.p = subprocess.Popen([path], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
        self.w("uci"); self.until("uciok")

    def w(self, s): self.p.stdin.write(s + "\n"); self.p.stdin.flush()

    def until(self, tok):
        while True:
            l = self.p.stdout.readline()
            if not l: raise RuntimeError("engine died")
            if l.startswith(tok): return l

    def score(self, fen, depth):
        self.w("ucinewgame"); self.w("position fen " + fen); self.w(f"go depth {depth}")
        sc = None
        while True:
            l = self.p.stdout.readline()
            if not l: raise RuntimeError("engine died")
            if l.startswith("info") and " score " in l:
                t = l.split(); i = t.index("score")
                sc = int(t[i + 2]) if t[i + 1] == "cp" else 30000
            if l.startswith("bestmove"): return sc

    def close(self):
        try: self.w("quit")
        except Exception: pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out"); ap.add_argument("data", nargs="+")
    ap.add_argument("--engine", required=True); ap.add_argument("--per", type=int, default=250)
    ap.add_argument("--cp", type=int, default=200); ap.add_argument("--depth", type=int, default=6)
    ap.add_argument("--maxpieces", type=int, default=4); ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--scan", type=int, default=3000000)
    a = ap.parse_args()
    rnd = random.Random(a.seed)
    pool = collections.defaultdict(list); cnt = collections.Counter(); seen = set(); cap = 20 * a.per
    for path in a.data:
        with open(path, errors="replace") as fh:
            for n, line in enumerate(fh):
                if n >= a.scan: break
                t = line.split()
                if len(t) < 4: continue
                key = " ".join(t[:4])
                if key in seen: continue
                men = sum(ch.isalpha() for ch in t[0])
                pieces = sum(ch in "nbrqNBRQ" for ch in t[0])
                if men < 6 or pieces > a.maxpieces or not any(ch in "pP" for ch in t[0]): continue
                try: b = chess.Board(key + " 0 1")
                except ValueError: continue
                if not b.is_valid() or not quiet(b): continue
                seen.add(key)
                f = family(b); lst = pool[f]; cnt[f] += 1
                if len(lst) < cap: lst.append(key + " 0 1")
                else:
                    j = rnd.randrange(cnt[f])                          # reservoir sampling: the sample is spread over the whole input
                    if j < cap: lst[j] = key + " 0 1"
    e = Eng(a.engine); out = []
    for f in FAMILIES:
        cand = pool.get(f, []); rnd.shuffle(cand); kept = 0
        for fen in cand:
            if kept >= a.per: break
            sc = e.score(fen, a.depth)
            if sc is not None and abs(sc) <= a.cp:
                out.append(f"{fen}  # {f}"); kept += 1
        print(f"{f:7s} candidates {len(cand):6d}  kept {kept}", flush=True)
    e.close()
    with open(a.out, "w") as fh:
        fh.write("# endgame start positions (tests/make_endgames.py " + " ".join(sys.argv[1:]).replace("\n", " ") + ")\n")
        fh.write("\n".join(out) + "\n")
    print(f"{len(out)} positions -> {a.out}")


if __name__ == "__main__":
    main()
