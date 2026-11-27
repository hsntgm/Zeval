#!/usr/bin/env python3
"""Does the tuner's model reproduce the ENGINE's static eval exactly?  (The tuner's eval is compared with the engine's own `eval` command.)
This is the proof that the feature extraction (piece-square counts and every term of the full evaluation), the integer truncation, the
tempo / bishop-pair handling and - after apply_params.py + a rebuild - the written tables are right.

usage: python3 tuner/check_eval.py [--engine ./zeval] [--asm src/eval.asm] [--n 3000] [--endgame 0] [--seed 5]
  --engine  default: environment variable ENGINE, else ./zeval (zeval.exe on Windows), like the tests
  --asm     the eval.asm the engine was BUILT from (default src/eval.asm): its parameter values are the model
  --endgame N more random positions with zero to four pieces besides kings and pawns (scale rules, pawn endings, special endgames,
            low phase), which the positions of random games reach only rarely
The evaluation switches are taken from the engine (console command evalcfg).  Positions of a non-linear special evaluation (model.int_eval = None) are checked against model.special_eval
and counted separately.
Exit code 0 = 0 mismatches.   needs python-chess (random positions only)."""
import argparse, os, random, re, subprocess, sys
import chess
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, "..", "tests"))
import model
from _eng import ENGINE

FIXED = [chess.STARTING_FEN, "8/8/8/4k3/8/8/4P3/4K3 w - - 0 1", "4k3/8/8/8/8/8/8/4KQ2 b - - 0 1", "r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1",
         "4k3/1P6/8/8/8/8/6p1/4K3 w - - 0 1", "QQQQQQQQ/QQQQQQQQ/8/3k4/8/8/8/4K3 w - - 0 1", "2b1kb2/8/8/8/8/8/8/2B1KB2 b - - 0 1",
         "2b1k3/8/8/8/8/8/8/2B1KB2 w - - 0 1", "4k3/8/8/8/8/8/8/4K3 w - - 0 1", "bbbbbbbb/8/8/3k4/8/8/8/4K3 b - - 0 1",
         "rnbqkbnr/ppp1pppp/8/3pP3/8/8/PPPP1PPP/RNBQKBNR w KQkq d6 0 3", "6k1/5ppp/8/1P6/8/8/5PPP/6K1 w - - 0 1",
         "r3k2r/pp3ppp/2n5/3p4/3P4/2N5/PP3PPP/R3K2R w KQkq - 0 1", "1k6/ppq5/8/8/8/8/5PPP/3QR1K1 b - - 0 1",
         "r1bq1rk1/pp3ppp/2n1pn2/2bp4/2B5/2N1PN2/PP3PPP/R1BQ1RK1 w - - 0 8", "8/5k2/8/3P4/8/8/2K5/8 w - - 0 1"]


def positions(n, seed):
    rnd = random.Random(seed)
    out = list(FIXED)
    while len(out) < n:
        b = chess.Board()
        for _ in range(rnd.choice([0, 6, 12, 20, 30, 45, 60, 90, 140, 200])):
            l = list(b.legal_moves)
            if not l: break
            caps = [m for m in l if b.is_capture(m) or m.promotion]
            b.push(rnd.choice(caps) if (caps and b.ply() > 30 and rnd.random() < 0.5) else rnd.choice(l))
        out.append(b.fen())
    return out


def endgame_positions(n, seed):
    """n random legal positions: kings, zero to four pieces and zero to six pawns, pieces and pawn files drawn at random"""
    rnd = random.Random(seed)
    PT = {"Q": chess.QUEEN, "R": chess.ROOK, "B": chess.BISHOP, "N": chess.KNIGHT}
    out = []
    while len(out) < n:
        b = chess.Board(None)
        free = list(range(64)); rnd.shuffle(free)
        b.set_piece_at(free.pop(), chess.Piece(chess.KING, chess.WHITE)); b.set_piece_at(free.pop(), chess.Piece(chess.KING, chess.BLACK))
        for _ in range(rnd.choice([0, 1, 1, 2, 2, 3, 4])):
            b.set_piece_at(free.pop(), chess.Piece(PT[rnd.choice("QRRBBNN")], rnd.random() < 0.5))
        files = rnd.choice([range(8), range(4), range(4, 8), [0], [7], [rnd.randrange(8)]])
        for _ in range(rnd.randint(0, 8)):
            sq = [s for s in free if 8 <= s < 56 and s % 8 in files]
            if sq:
                s = rnd.choice(sq); free.remove(s); b.set_piece_at(s, chess.Piece(chess.PAWN, rnd.random() < 0.5))
        b.turn = rnd.random() < 0.5
        if b.is_valid():
            out.append(b.fen())
    return out


def engine_cfg(engine):
    """evaluation switches of the engine build (console command evalcfg); None for an engine without the command"""
    out = subprocess.run(engine, input=b"evalcfg\nquit\n", stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=60).stdout.decode()
    m = re.search(r"^evalcfg (\d+)\s*$", out, re.M)
    return int(m.group(1)) if m else None


def engine_evals(engine, fens):
    data = "\n".join(["uci"] + [x for f in fens for x in (f"position fen {f}", "eval")] + ["quit"]) + "\n"
    out = subprocess.run(engine, input=data.encode(), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=600).stdout.decode()
    r = [int(m.group(1)) for m in re.finditer(r"^eval (-?\d+)\s*$", out, re.M)]
    if len(r) != len(fens):
        raise SystemExit(f"engine printed {len(r)} eval lines for {len(fens)} positions")
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", default="")
    ap.add_argument("--asm", default=model.ASM)
    ap.add_argument("--n", type=int, default=3000)
    ap.add_argument("--endgame", type=int, default=0)
    ap.add_argument("--seed", type=int, default=5)
    a = ap.parse_args()
    from _eng import resolve
    eng = resolve(a.engine) if a.engine else ENGINE
    t = model.load_tables(a.asm)
    c = engine_cfg(eng)
    if c is not None:
        model.set_cfg(c)
    fens = positions(a.n, a.seed) + endgame_positions(a.endgame, a.seed)
    ev = engine_evals(eng, fens)
    mv = [model.int_eval(f, t) for f in fens]
    nspec = sum(m is None for m in mv)
    mv = [model.special_eval(f, t) if m is None else m for f, m in zip(fens, mv)]
    bad = [(f, e, m) for f, e, m in zip(fens, ev, mv) if e != m]
    print(f"check_eval: {len(fens)} positions, {len(bad)} mismatches  (tables: {os.path.relpath(a.asm)}, BP {t['bp_mg']}/{t['bp_eg']}, tempo {t['tempo']};"
          f" switches {model.cfg_text()}{'' if c is not None else ' (engine without evalcfg: source defaults)'}"
          f"{f'; {nspec} of them by the special evaluation' if nspec else ''})")
    for f, e, m in bad[:5]:
        print("   engine", e, "model", m, f)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
