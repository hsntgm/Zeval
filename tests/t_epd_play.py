#!/usr/bin/env python3
"""Play-out test on an EPD suite with known results: does the engine reach the theoretical result from each position?

Each engine plays both sides of every position (self-play).  The EPD gives the theoretical result for the side to move in c0 ("win" or
"draw"; e.g. tablebase suites such as Sergei Markoff's 4-5 men set):
  win  -> success when the side to move wins (mates, or the opponent has nothing left that can win and loses on the fifty-move / mate rule)
  draw -> success when the side to move does not lose; a win of the side to move is counted separately (the defence went wrong)
Every engine gets the same positions and the same fixed search budget per move (--nodes, deterministic; or --movetime), so two builds compare
directly.  A position is played out to at most --maxplies plies, then counted as a draw.
usage: python3 tests/t_epd_play.py suite.epd ENGINE [ENGINE ...] [--nodes 100000 | --movetime 50] [--workers 2] [--maxplies 300]
Prints per engine: wins converted / win positions, draws held / draw positions, and the positions where the engines disagree.  Needs python-chess.
"""
import argparse, os, re, sys
from multiprocessing import Pool

import chess

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import match  # noqa: E402


def load(path):
    out = []
    for line in open(path):
        line = line.strip()
        if not line:
            continue
        m = re.search(r'c0 "(win|draw)"', line)
        if not m:
            continue
        fen = " ".join(line.split()[:4]) + " 0 1"
        pid = re.search(r'id "([^"]*)"', line)
        out.append((pid.group(1) if pid else str(len(out)), fen, m.group(1)))
    return out


def play(args):
    engine, fen, budget, maxplies = args
    b = chess.Board(fen)
    stm = b.turn
    try:
        e = match.Eng(engine)
    except RuntimeError:
        e = match.Eng(engine)
    try:
        e.send("ucinewgame")
        moves = []
        while not b.is_game_over(claim_draw=True) and len(moves) < maxplies:
            e.send(f"position fen {fen}" + (" moves " + " ".join(moves) if moves else ""))
            e.send(f"go {budget}")
            mv = e.wait("bestmove", 60).split()[1]
            m = chess.Move.from_uci(mv)
            if m not in b.legal_moves:
                return "illegal"
            b.push(m); moves.append(mv)
    finally:
        e.quit()
    if b.is_checkmate():
        return "win" if b.turn != stm else "loss"
    return "draw"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("epd"); ap.add_argument("engines", nargs="+")
    ap.add_argument("--nodes", type=int, default=100000)
    ap.add_argument("--movetime", type=int, default=0)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--maxplies", type=int, default=300)
    a = ap.parse_args()
    pos = load(a.epd)
    if not pos:
        print("no position with c0 \"win\" / \"draw\""); sys.exit(1)
    budget = f"movetime {a.movetime}" if a.movetime else f"nodes {a.nodes}"
    res = {}
    with Pool(a.workers) as pool:
        for eng in a.engines:
            r = pool.map(play, [(eng, fen, budget, a.maxplies) for _, fen, _ in pos], chunksize=1)
            res[eng] = r
            wins = [x for (_, _, c), x in zip(pos, r) if c == "win"]
            draws = [x for (_, _, c), x in zip(pos, r) if c == "draw"]
            print(f"{os.path.basename(eng):14s} go {budget:14s}  win positions converted {wins.count('win'):3d}/{len(wins)}   "
                  f"draw positions held {len(draws) - draws.count('loss'):3d}/{len(draws)} (won anyway {draws.count('win')})   "
                  f"illegal {r.count('illegal')}", flush=True)
    if len(a.engines) > 1:
        print("\npositions where the result differs (success = + ):")
        for i, (pid, fen, c) in enumerate(pos):
            ok = {e: (res[e][i] == "win") if c == "win" else (res[e][i] != "loss") for e in a.engines}
            if len(set(ok.values())) > 1:
                print(f"  {pid:40s} {c:4s}  " + "  ".join(f"{os.path.basename(e)}:{'+' if ok[e] else '-'}{res[e][i]}" for e in a.engines))


if __name__ == "__main__":
    main()
