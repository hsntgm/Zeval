#!/usr/bin/env python3
"""SEE check: the engine's see_ge (test build with -DSEETEST, console command "see <move> <threshold>") against an
independent reference written with python-chess (full recursive exchange search with stand-pat, x-rays by removing
pieces, least valuable attacker first, the king may only capture when nothing of the other side still attacks the
square).  Pins are ignored on purpose (as in the engine); promotions and en passant count as 0 (as in the engine).

Build the test engine:   sh tests/build1.sh ./zeval_see -DSEETEST
Run:                     python3 tests/t_see.py ./zeval_see [positions] [seed] [playout|dense]
When several attackers of the same type exist the order in which they are lifted can change the value (x-rays), so the
reference collects every value that any order gives; the engine must fall inside that set (ties are counted separately).
Also checks after every position that the board was left untouched ("d" -> Fen).
Exit code 0 = no mismatch, 1 = mismatches / board changed, 2 = the engine crashed / hung / is not a -DSEETEST build
(the first query is a pre-flight check and every read has a timeout).
"""
import sys, os, random, subprocess, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _eng import LineReader, EngineGone, start, finish
import chess

VAL = {chess.PAWN: 100, chess.KNIGHT: 320, chess.BISHOP: 330, chess.ROOK: 500, chess.QUEEN: 900, chess.KING: 0}
ORDER = [chess.PAWN, chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN, chess.KING]


def best(board, sq, side, cur):
    """set of possible values (>= 0, stand-pat allowed) for `side` to move, the piece worth `cur` sits on sq"""
    atk = board.attackers_mask(side, sq)
    if not atk:
        return {0}
    out = set()
    for pt in ORDER:
        m = atk & board.pieces_mask(pt, side)
        if not m:
            continue
        for a in chess.scan_forward(m):
            piece = board.remove_piece_at(a)
            if pt == chess.KING:
                if board.attackers_mask(not side, sq):
                    sub = None                      # illegal king capture: not possible
                else:
                    sub = {0}
            else:
                sub = best(board, sq, not side, VAL[pt])
            board.set_piece_at(a, piece)
            if sub is None:
                out.add(0)
            else:
                for s in sub:
                    out.add(max(0, cur - s))
        return out
    return {0}


def ref_values(board, mv):
    """set of possible SEE values of mv in `board`, and a flag telling whether the engine treats it as special (=0)"""
    mover = board.piece_at(mv.from_square)
    if mv.promotion or board.is_en_passant(mv):
        return {0}
    vic = board.piece_at(mv.to_square)
    vv = VAL[vic.piece_type] if vic else 0
    piece = board.remove_piece_at(mv.from_square)
    sub = best(board, mv.to_square, not mover.color, VAL[mover.piece_type])
    board.set_piece_at(mv.from_square, piece)
    return {vv - s for s in sub}


class Eng:
    def __init__(self, path):
        self.p = start([path], bufsize=1)
        self.lr = LineReader(self.p.stdout)
        self.send("uci"); self.p.stdin.flush()
        while self.line(30).strip() != "uciok":
            pass
        # pre-flight: only a -DSEETEST build answers "see <move> <threshold>" with 0 / 1 (a release build treats it as a move and prints "Illegal move")
        self.send("position startpos"); self.send("see e2e4 0"); self.p.stdin.flush()
        try: r = self.line(3).strip()
        except EngineGone: raise EngineGone("no answer to 'see e2e4 0': this engine is not a -DSEETEST build (see the header of this file)")
        if r not in ("0", "1"):
            raise EngineGone(f"'see e2e4 0' was answered with {r!r}: this engine is not a -DSEETEST build (see the header of this file)")

    def line(self, timeout=60):
        """next output line; EOF or silence -> EngineGone (the process is killed on silence)"""
        l = self.lr.get(timeout)
        if l is None:
            try: self.p.kill()
            except OSError: pass
            raise EngineGone(f"engine gave no answer within {timeout:g} s (killed)")
        return l + "\n"

    def send(self, s):
        self.p.stdin.write(s + "\n")

    def batch(self, lines, nreply):
        self.send("\n".join(lines)); self.p.stdin.flush()
        return [self.line().strip() for _ in range(nreply)]

    def fen(self):
        self.send("d"); self.p.stdin.flush()
        while True:
            l = self.line()
            if l.startswith("Fen: "):
                return l[5:].strip()


def positions(n, seed):
    rnd = random.Random(seed)
    while n > 0:
        b = chess.Board()
        target = rnd.randint(4, 90)
        capbias = rnd.random() * 0.7
        for _ in range(target):
            legal = list(b.legal_moves)
            if not legal:
                break
            caps = [m for m in legal if b.is_capture(m)]
            m = rnd.choice(caps) if caps and rnd.random() < capbias else rnd.choice(legal)
            b.push(m)
        if b.is_game_over():
            continue
        n -= 1
        yield b


def dense(n, seed):
    """random piece placements with many sliders: long x-ray exchange chains, kings may take, promotions, ep ..."""
    rnd = random.Random(seed)
    types = [chess.PAWN] * 4 + [chess.KNIGHT] * 2 + [chess.BISHOP] * 3 + [chess.ROOK] * 3 + [chess.QUEEN] * 2
    while n > 0:
        b = chess.Board(None)
        ks = rnd.sample(range(64), 2)
        if chess.square_distance(ks[0], ks[1]) < 2:
            continue
        b.set_piece_at(ks[0], chess.Piece(chess.KING, True)); b.set_piece_at(ks[1], chess.Piece(chess.KING, False))
        for _ in range(rnd.randint(8, 22)):
            sq = rnd.randrange(64); pt = rnd.choice(types)
            if b.piece_at(sq) or (pt == chess.PAWN and chess.square_rank(sq) in (0, 7)):
                continue
            b.set_piece_at(sq, chess.Piece(pt, rnd.random() < 0.5))
        b.turn = rnd.random() < 0.5
        if rnd.random() < 0.15:
            ep = rnd.randrange(8)
            b.ep_square = chess.square(ep, 5 if b.turn else 2)
        if not b.is_valid() or b.is_game_over():
            continue
        n -= 1
        yield b


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        sys.exit("usage: t_see.py ENGINE [positions] [seed] [playout|dense]   (a -DSEETEST build:  sh tests/build1.sh ./zeval_see -DSEETEST)")
    path = sys.argv[1]
    npos = int(sys.argv[2]) if len(sys.argv) > 2 else 3000
    seed = int(sys.argv[3]) if len(sys.argv) > 3 else 1
    mode = sys.argv[4] if len(sys.argv) > 4 else "playout"
    gen = dense if mode == "dense" else positions
    e = Eng(path)
    checks = bad = ties = caps = quiets = special = 0
    for b in gen(npos, seed):
        fen = b.fen()
        e.send(f"position fen {fen}")
        qs = []; exp = []
        for mv in b.legal_moves:
            vals = ref_values(b, mv)
            u = mv.uci()
            lo, hi = min(vals), max(vals)
            if len(vals) > 1:
                ties += 1
            if mv.promotion or b.is_en_passant(mv):
                special += 1
            elif b.is_capture(mv):
                caps += 1
            else:
                quiets += 1
            for t, want in ((lo, 1), (hi + 1, 0), (lo - 1, 1), (hi + 60, 0), (lo - 130, 1), (hi, None)):
                if want is None:
                    continue
                qs.append(f"see {u} {t}"); exp.append((u, t, want, sorted(vals)))
        rep = e.batch(qs, len(qs))
        for (u, t, want, vals), got in zip(exp, rep):
            checks += 1
            if got != str(want):
                bad += 1
                if bad <= 20:
                    print(f"MISMATCH fen={fen} move={u} thr={t} engine={got} expected={want} ref_values={vals}")
        if e.fen().split()[:4] != fen.split()[:4]:
            bad += 1
            print("BOARD CHANGED", fen)
    print(f"positions={npos} checks={checks} captures={caps} quiet={quiets} promo/ep={special} tie-positions={ties} mismatches={bad}")
    e.send("quit"); e.p.stdin.flush(); finish(e.p, 10)
    sys.exit(1 if bad else 0)


main()
