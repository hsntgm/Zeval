#!/usr/bin/env python3
"""Tactics smoke test over UCI (python-chess): 12 small positions with a known best move (mate in 1 / 2, hanging queen, promotion, ...) at 600 ms each.
This is the SMALL one (seconds).  The big tactical suite is tactics.sh / tactics/ (1,146 positions); it is a diagnostic, this one is a quick tripwire.
  ENGINE=./zeval python3 tests/t_tactics.py          (Windows: set ENGINE=zeval.exe)          needs python-chess
Exit code: 0 = every position with an expected move was solved and every move was legal,
1 = a wrong or illegal move, 2 = the engine could not be run.  Positions without an expected move (None) only print their result.
The set includes two mate-in-2 positions (the first move is the only forced one, verified by brute force) and lists the alternative mating moves (a7a8r, ...)."""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _eng import ENGINE
import chess, chess.engine
try:
    e = chess.engine.SimpleEngine.popen_uci(ENGINE)
except (OSError, chess.engine.EngineError, chess.engine.EngineTerminatedError) as ex:
    sys.stderr.write(f"ERROR: cannot start the engine {ENGINE}: {ex}\n"); sys.exit(2)
def go(fen, ms=500):
    b = chess.Board(fen); t = time.time()
    r = e.play(b, chess.engine.Limit(time=ms/1000), info=chess.engine.INFO_ALL)
    return b, r.move, r.info, time.time()-t
tests = [
 ("mate in 1", "6k1/5ppp/8/8/8/8/5PPP/R5K1 w - - 0 1", {"a1a8"}),
 ("mate in 1 (black)", "r5k1/5ppp/8/8/8/8/5PPP/6K1 b - - 0 1", {"a8a1"}),
 ("back rank mate in 1", "6k1/5ppp/8/8/8/8/1q3PPP/3R2K1 w - - 0 1", {"d1d8"}),
 ("back rank mate in 2 (Ra8+ Rc8 Rxc8#)", "6k1/5ppp/8/8/8/8/2r2PPP/R5K1 w - - 0 1", {"a1a8"}),
 ("win queen (fork)", "rnbqkb1r/pppp1ppp/5n2/4p3/4P3/3Q4/PPPP1PPP/RNB1KBNR b KQkq - 0 1", None),
 ("capture hanging queen", "rnb1kbnr/pppp1ppp/8/4p3/4P2q/5N2/PPPP1PPP/RNBQKB1R w KQkq - 0 1", {"f3h4"}),
 ("mate in 1, not stalemate (KQ v K: Qg7#, Qg6 is stalemate)", "7k/8/5K2/8/8/8/8/6Q1 w - - 0 1", {"g1g7"}),
 ("promote (a7a8q and a7a8r both mate)", "8/P7/8/8/8/8/k1K5/8 w - - 0 1", {"a7a8q", "a7a8r"}),
 ("castle? (O-O wins nothing)", "r3k2r/pppqbppp/2np1n2/4p3/2B1P3/2NP1N2/PPP2PPP/R1BQK2R w KQkq - 0 1", None),
 ("en passant tactic", "4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 1", None),
 ("smothered mate in 1 (Nf7#)", "6rk/6pp/8/6N1/8/8/8/6K1 w - - 0 1", {"g5f7"}),
 ("smothered mate in 2 (Qg8+ Rxg8 Nf7#)", "5r1k/6pp/7N/8/8/8/Q7/6K1 w - - 0 1", {"a2g8"}),
]
bad = 0
try:
    for name, fen, exp in tests:
        b, mv, info, dt = go(fen, 600)
        legal = mv in b.legal_moves
        ok = "" if exp is None else ("OK " if mv.uci() in exp else "FAIL")
        if not legal or ok == "FAIL": bad += 1
        sc = info.get("score")
        print(f"{ok:4s}{name:32.32s} -> {mv.uci():6s} score={sc} depth={info.get('depth')} nps={info.get('nps')} legal={legal}")
except (chess.engine.EngineTerminatedError, chess.engine.EngineError) as ex:
    sys.stderr.write(f"ERROR: the engine died during the test: {ex}\n"); sys.exit(2)
e.quit()
print("ALL PASSED" if not bad else f"{bad} FAILED")
sys.exit(1 if bad else 0)
