#!/usr/bin/env python3
"""Self-play training data: several tests/match.py runs, each with a NEW seed (= new random openings), every engine move saved.

usage: python3 tuner/gen_data.py --engine zeval.exe [--rounds 10] [--games 2000] [--movetime 50 | --tc 2+0.02] [--workers N] [--seed0 401]
          [--out tuner/data] [--pool 0] [--opt "Hash=16"] [--book random|auto|FILE] [--no-adj | --no-draw-adj]
Writes  <out>/sp_<seed>.jsonl  (one file per round, appended to if it exists) and prints the match.py summary of each round.
The engine plays itself (A = B = --engine): the point is the labelled positions, not the score.  Use seeds that your later SPRTs do not use
(the SPRT rule "a NEW seed for every match" also applies the other way round: do not test a tuned engine on the openings it was tuned on).
Ctrl-C is safe: finished rounds stay on disk; start again with a higher --seed0.
Rule of thumb: 1,000,000 quiet positions ~ 12,000-15,000 games (~80 kept positions per game in a measured sample).  --workers = physical cores, one thread per engine.
Measured on a 2-core test machine: 30 ms/move, ~25 games per minute (so 8 cores at 50 ms: roughly 60 per minute, 15,000 games ~ 4 hours: run it overnight).
Own data: --tc 2+0.02 --book auto (noob_3moves), then tuner/resolve.py on the game files.  A clock game takes about 6-7 s at 2+0.02."""
import argparse, os, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__))

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--engine", required=True); ap.add_argument("--rounds", type=int, default=10); ap.add_argument("--games", type=int, default=2000)
    ap.add_argument("--movetime", type=int, default=50); ap.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    ap.add_argument("--tc", default="", help="clock instead of --movetime, e.g. 2+0.02 (seconds base + increment)")
    ap.add_argument("--book", default="random", help="start positions: random (default, 8 random plies), auto (tests/books/noob_3moves.epd), or an EPD book")
    ap.add_argument("--seed0", type=int, default=401); ap.add_argument("--out", default=os.path.join(HERE, "data"))
    ap.add_argument("--pool", type=int, default=0); ap.add_argument("--opt", default="")
    ap.add_argument("--no-adj", action="store_true", help="play every game to its real end (no win / draw adjudication): slower, but the labels are not the engine's own opinion")
    ap.add_argument("--no-draw-adj", action="store_true", help="no draw adjudication, win adjudication kept")
    a = ap.parse_args()
    if a.no_adj and a.no_draw_adj: raise SystemExit("--no-adj already switches off the draw adjudication")
    os.makedirs(a.out, exist_ok=True)
    match = os.path.join(HERE, "..", "tests", "match.py")
    for r in range(a.rounds):
        seed = a.seed0 + r
        out = os.path.join(a.out, f"sp_{seed}.jsonl")
        cmd = [sys.executable, match, a.engine, a.engine, "--nosprt", "--games", str(a.games), "--movetime", str(a.movetime), "--workers", str(a.workers),
               "--seed", str(seed), "--save-games", out, "--book", a.book]       # default random openings: the training data keeps its variety
        if a.tc: cmd += ["--tc", a.tc]
        if a.pool: cmd += ["--pool", str(a.pool)]
        if a.no_adj: cmd += ["--no-adj"]
        if a.no_draw_adj: cmd += ["--no-draw-adj"]
        if a.opt: cmd += ["--optA", a.opt, "--optB", a.opt]
        print(f"=== round {r + 1}/{a.rounds}: seed {seed} -> {out}", flush=True)
        subprocess.run(cmd, check=False)

if __name__ == "__main__":
    main()
