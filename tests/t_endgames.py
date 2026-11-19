#!/usr/bin/env python3
"""Endgame conversion test: does an engine win the won bare-king endgames and hold the drawn ones?

Plays each engine against itself from random positions of a few classes and counts the results:
  KQK, KRK, KBNK                      won for the strong side: success = mate before the fifty-move rule
  KPK won (bitbase of src/eval.asm)   won: success = the pawn side wins (promotes and mates)
  KPK drawn (bitbase)                 drawn: success = no win for the pawn side
  KBPK wrong bishop (rook pawn, the bishop misses the promotion square, the defending king in the corner)  drawn: success = no win
Every class uses the same seeded positions for every engine, so two builds (e.g. -DEV_ENDG=1 and the default) compare directly.
usage: python3 tests/t_endgames.py ENGINE [ENGINE2 ...] [--n 30] [--movetime 50] [--workers 2] [--seed 1]
Prints per class and engine: successes / games and the mean number of moves of the successful wins.  Needs python-chess.
"""
import argparse, os, random, sys
from multiprocessing import Pool

import chess

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "tuner"))
import match  # noqa: E402
import model  # noqa: E402

MAXPLIES = 200


def kpk_win(b):
    """bitbase verdict for K+P vs K (pawn side's view): True = won"""
    pc = b.pieces(chess.PAWN, chess.WHITE) or b.pieces(chess.PAWN, chess.BLACK)
    white = bool(b.pieces(chess.PAWN, chess.WHITE))
    p = next(iter(pc)); sk = b.king(white); wk = b.king(not white)
    if not white:
        p, sk, wk = p ^ 56, sk ^ 56, wk ^ 56
    if (p & 7) >= 4:
        p, sk, wk = p ^ 7, sk ^ 7, wk ^ 7
    return bool(model.kpk_win(b.turn == white, sk, wk, p))


def random_pos(rng, cls):
    while True:
        b = chess.Board(None)
        strong = rng.choice((chess.WHITE, chess.BLACK))
        sq = rng.sample(range(64), 5)
        b.set_piece_at(sq[0], chess.Piece(chess.KING, strong))
        b.set_piece_at(sq[1], chess.Piece(chess.KING, not strong))
        if cls == "KQK":
            b.set_piece_at(sq[2], chess.Piece(chess.QUEEN, strong))
        elif cls == "KRK":
            b.set_piece_at(sq[2], chess.Piece(chess.ROOK, strong))
        elif cls == "KBNK":
            b.set_piece_at(sq[2], chess.Piece(chess.BISHOP, strong)); b.set_piece_at(sq[3], chess.Piece(chess.KNIGHT, strong))
        elif cls.startswith("KPK"):
            if not 8 <= sq[2] < 56:
                continue
            b.set_piece_at(sq[2], chess.Piece(chess.PAWN, strong))
        elif cls == "KBPK":
            f = rng.choice((0, 7))
            promo = f + (56 if strong == chess.WHITE else 0)
            r = rng.randint(2, 5) if strong == chess.WHITE else rng.randint(2, 5)
            psq = f + 8 * (r if strong == chess.WHITE else 7 - r)
            dark_promo = bool(model.DARK >> promo & 1)
            bsq = rng.choice([s for s in range(64) if bool(model.DARK >> s & 1) != dark_promo and s not in (psq,)])
            ksq = rng.choice([s for s in range(64) if chess.square_distance(s, promo) <= 1])
            b = chess.Board(None)
            b.set_piece_at(ksq, chess.Piece(chess.KING, not strong))
            b.set_piece_at(psq, chess.Piece(chess.PAWN, strong))
            b.set_piece_at(bsq, chess.Piece(chess.BISHOP, strong))
            free = [s for s in range(64) if b.piece_at(s) is None and chess.square_distance(s, ksq) > 1]
            b.set_piece_at(rng.choice(free), chess.Piece(chess.KING, strong))
        b.turn = rng.choice((chess.WHITE, chess.BLACK))
        if not b.is_valid() or b.is_game_over():
            continue
        if cls == "KPK_won" and not kpk_win(b):
            continue
        if cls == "KPK_drawn" and kpk_win(b):
            continue
        return b.fen(), strong


WANT_WIN = {"KQK": True, "KRK": True, "KBNK": True, "KPK_won": True, "KPK_drawn": False, "KBPK": False}


def play_one(args):
    engine, fen, strong, movetime, cls = args
    b = chess.Board(fen)
    try:
        e = match.Eng(engine)
    except RuntimeError:                      # a start that timed out on a busy machine: one retry
        e = match.Eng(engine)
    try:
        e.send("ucinewgame")
        moves = []
        while not b.is_game_over(claim_draw=True) and len(moves) < MAXPLIES:
            e.send(f"position fen {fen}" + (" moves " + " ".join(moves) if moves else ""))
            e.send(f"go movetime {movetime}")
            line = e.wait("bestmove")
            mv = line.split()[1]
            m = chess.Move.from_uci(mv)
            if m not in b.legal_moves:
                return cls, False, 0, "illegal"
            b.push(m); moves.append(mv)
    finally:
        e.quit()
    won = b.is_checkmate() and b.turn != strong
    ok = won if WANT_WIN[cls] else not won
    return cls, ok, (len(moves) + 1) // 2, b.result(claim_draw=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("engines", nargs="+")
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--movetime", type=int, default=50)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    rng = random.Random(a.seed)
    pos = [(cls,) + random_pos(rng, cls) for cls in WANT_WIN for _ in range(a.n)]
    with Pool(a.workers) as pool:
        for eng in a.engines:
            res = pool.map(play_one, [(eng, fen, strong, a.movetime, cls) for cls, fen, strong in pos])
            print(f"== {eng}")
            for cls in WANT_WIN:
                r = [x for x in res if x[0] == cls]
                ok = [x for x in r if x[1]]
                moves = [x[2] for x in ok if WANT_WIN[cls]]
                extra = f"  mean moves to mate {sum(moves) / len(moves):.1f}" if moves else ""
                print(f"   {cls:10s} {len(ok):3d} / {len(r):3d} {'won' if WANT_WIN[cls] else 'held'}{extra}", flush=True)


if __name__ == "__main__":
    main()
