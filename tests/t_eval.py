#!/usr/bin/env python3
"""Static-eval consistency tests.  Uses the engine's own `eval` command (prints "eval <cp>",
side-to-move view, tempo included) -- no special build needed.

  1. mirror      eval(P) == eval(mirror(P))   mirror = flip ranks, swap colours, swap side to move, castling rights, e.p. square
  2. incremental eval after `position startpos moves ...` (incrementally updated mg/eg scores, cnt, phase, bitboards)
                 == eval of the same position set up from its FEN (computed from scratch)
  3. tempo       eval(P, white to move) + eval(P, black to move) == 2 * TEMPO (%define TEMPO of src/eval.asm)   [P without e.p. square; with EV_ENDG
                 the king + pawn against king positions are left out, their bitbase value depends on the side to move]
  4. selftest    builds mutants of the eval (needs nasm + ld) and checks that tests 1-3 notice them   (nasm / ld missing = SKIP; a mutant that does not build = FAIL)

usage: python3 tests/t_eval.py [--engine ./zeval] [--n 1500] [--seed 7] [--selftest]
needs python-chess.  New eval terms must keep tests 1-3 green; an asymmetric term shows up here at once.
"""
import argparse, os, random, re, shlex, subprocess, sys, tempfile
import chess
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _eng import ENGINE, resolve

HERE = os.path.dirname(os.path.abspath(__file__))

def _default_tempo():
    """TEMPO of the source (src/eval.asm, %define TEMPO n, written by tuner/apply_params.py) - 10 if the define is absent"""
    try:
        m = re.search(r"^%define\s+TEMPO\s+(-?\d+)", open(os.path.join(HERE, "..", "src", "eval.asm")).read(), re.M)
        return int(m.group(1)) if m else 10
    except OSError:
        return 10


TEMPO = _default_tempo()

EXTRA_FENS = [
    chess.STARTING_FEN,
    "8/8/8/4k3/8/8/4P3/4K3 w - - 0 1",                        # KPK
    "4k3/8/8/8/8/8/8/4KQ2 w - - 0 1",                         # KQK
    "4k3/8/8/8/8/8/8/R3K2R w KQ - 0 1",
    "r3k2r/8/8/8/8/8/8/R3K2R b KQkq - 0 1",
    "4k3/1P6/8/8/8/8/6p1/4K3 w - - 0 1",                      # pawns on 7th/2nd
    "rnbqkbnr/ppp1pppp/8/3pP3/8/8/PPPP1PPP/RNBQKBNR w KQkq d6 0 3",   # e.p.
    "QQQQQQQQ/QQQQQQQQ/8/3k4/8/8/8/4K3 w - - 0 1",            # many queens (piece-count table bounds)
    "4k3/8/8/8/8/8/8/4K1NN w - - 0 1",                        # two knights
    "2b1kb2/8/8/8/8/8/8/2B1KB2 w - - 0 1",                    # bishop pairs both sides
    "2b1k3/8/8/8/8/8/8/2B1KB2 w - - 0 1",                     # bishop pair for white only
    "4k3/8/8/8/8/8/8/4K3 w - - 0 1",                          # bare kings
    "6k1/5ppp/8/1P6/8/8/5PPP/6K1 w - - 0 1",                  # passed pawn, king shields
    "r3k2r/pp3ppp/2n5/3p4/3P4/2N5/PP3PPP/R3K2R w KQkq - 0 1", # open file, isolated pawns
    "4k3/8/8/2pPp3/8/8/8/4K3 w - c6 0 1",                     # e.p. square, passed pawn
    "1k6/ppq5/8/8/8/8/5PPP/3QR1K1 b - - 0 1",                 # queens on: king attack
]


def run_engine(engine, lines):
    """send all lines, return the list of ints printed by `eval` commands, in order"""
    data = "\n".join(["uci"] + lines + ["quit"]) + "\n"
    out = subprocess.run(engine, input=data.encode(), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=300).stdout.decode()
    return [int(m.group(1)) for m in re.finditer(r"^eval (-?\d+)\s*$", out, re.M)]


def eval_fens(engine, fens):
    cmds = []
    for f in fens:
        cmds += [f"position fen {f}", "eval"]
    r = run_engine(engine, cmds)
    if len(r) != len(fens):
        raise SystemExit(f"engine returned {len(r)} eval lines for {len(fens)} positions (is `eval` supported / is a FEN rejected?)")
    return r


def random_positions(n, seed):
    rnd = random.Random(seed)
    out = []
    while len(out) < n:
        b = chess.Board()
        target = rnd.choice([0, 4, 10, 20, 30, 40, 60, 80, 120, 200])
        moves = []
        for _ in range(target):
            l = list(b.legal_moves)
            if not l: break
            # bias towards captures / promotions late in the game so that material actually drops
            caps = [m for m in l if b.is_capture(m) or m.promotion]
            m = rnd.choice(caps) if (caps and len(moves) > 30 and rnd.random() < 0.5) else rnd.choice(l)
            b.push(m); moves.append(m.uci())
        out.append((b, moves))
    return out


def test_mirror(engine, boards):
    fens = [b.fen() for b in boards]
    mir = [b.mirror().fen() for b in boards]
    a, c = eval_fens(engine, fens), eval_fens(engine, mir)
    bad = [(f, m, x, y) for f, m, x, y in zip(fens, mir, a, c) if x != y]
    return len(boards), bad


def test_incremental(engine, items):
    cmds, fens = [], []
    for b, mv in items:
        cmds += ["position startpos" + (" moves " + " ".join(mv) if mv else ""), "eval",
                 f"position fen {b.fen()}", "eval"]
        fens.append((b.fen(), mv))
    r = run_engine(engine, cmds)
    if len(r) != 2 * len(items): raise SystemExit("eval count mismatch in incremental test")
    bad = [(fens[i][0], " ".join(fens[i][1][-12:]), r[2 * i], r[2 * i + 1]) for i in range(len(items)) if r[2 * i] != r[2 * i + 1]]
    return len(items), bad


def engine_cfg(engine):
    """EV_CFG mask printed by the `evalcfg` command (0 when the command is absent)"""
    data = "uci\nevalcfg\nquit\n"
    out = subprocess.run(engine, input=data.encode(), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=60).stdout.decode()
    m = re.search(r"^evalcfg (\d+)", out, re.M)
    return int(m.group(1)) if m else 0


def is_kpk(b):
    """a side with only its king: with EV_ENDG the special evaluation applies (the KPK bitbase depends on the side to move, a draw is exactly 0
    without tempo), so eval(white to move) + eval(black to move) == 2 * tempo does not hold there"""
    return any(bin(b.occupied_co[c]).count("1") == 1 for c in (chess.WHITE, chess.BLACK))


def test_tempo(engine, boards):
    if engine_cfg(engine) & 4:
        boards = [b for b in boards if not is_kpk(b)]
    fw, fb = [], []
    for b in boards:
        c = b.copy(); c.ep_square = None
        c.turn = chess.WHITE; fw.append(c.fen())
        c.turn = chess.BLACK; fb.append(c.fen())
    w, k = eval_fens(engine, fw), eval_fens(engine, fb)
    bad = [(a, b2, x, y) for a, b2, x, y in zip(fw, fb, w, k) if x + y != 2 * TEMPO]
    return len(boards), bad


def run_all(engine, n, seed, quiet=False):
    items = random_positions(n, seed)
    boards = [b for b, _ in items] + [chess.Board(f) for f in EXTRA_FENS]
    ok = True
    for name, fn, arg in (("mirror", test_mirror, boards), ("incremental", test_incremental, items), ("tempo", test_tempo, boards)):
        total, bad = fn(engine, arg)
        if not quiet:
            print(("PASS " if not bad else "FAIL ") + f"{name}: {total} positions, {len(bad)} mismatches")
            for x in bad[:3]: print("     ", x)
        ok = ok and not bad
    return ok


def selftest(engine_unused, n, seed):
    """mutate the eval in a temporary copy of the source (src/ parts concatenated as build/build.sh does), rebuild, and require that the tests fail"""
    parts = "p1_data.asm eval.asm p2_core.asm p2b_bb_data.inc p2b_bb.asm p3_cmd.asm p4_search.asm p5_main.asm p6_win.asm p7_units.asm".split()
    src = "".join(open(os.path.join(HERE, "..", "src", p)).read() for p in parts)
    # the mutations are applied to the full evaluation only: from the bishop-pair macro BPAIR (used by the full evaluation) to the
    # EVAL_FULL = 0 variant behind it, which has its own copies of the bishop pair and the tempo
    i = src.index("\n%macro BPAIR 0"); j = src.index("(EVAL_FULL = 0: material", i)
    head, src_eval, tail = src[:i], src[i:j], src[j:]
    mutants = {      # name: list of (old, new) text replacements; every old text must occur exactly once in the full evaluation
        "black bishop-pair bonus BP_MG -> BP_MG-1 (asymmetric term)": [("    sub eax, BP_MG\n    sub ecx, BP_EG\n", "    sub eax, BP_MG-1\n    sub ecx, BP_EG\n")],
        "tempo TEMPO -> TEMPO+1": [("%endif\n    add eax, TEMPO\n    add rsp, 2*EVS+32\n", "%endif\n    add eax, TEMPO+1\n    add rsp, 2*EVS+32\n")],
        "black view built from the wrong king square (asymmetric full evaluation)": [("    mov [rsp+EVS+EV_KSQ], edx\n", "    mov [rsp+EVS+EV_KSQ], eax\n"),
                                                                                   ("    mov [rsp+EVS+EV_TKSQ], eax\n", "    mov [rsp+EVS+EV_TKSQ], edx\n")],
    }
    ok = True
    for name, reps in mutants.items():
        if any(src_eval.count(a) != 1 for a, _ in reps):
            print(f"FAIL selftest: mutation site for '{name}' not found exactly once"); ok = False; continue
        m = src_eval
        for a, b in reps: m = m.replace(a, b)
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "m.asm"), "w").write(head + m + tail)
            try:
                subprocess.run(["nasm", "-f", "elf64", os.path.join(d, "m.asm"), "-o", os.path.join(d, "m.o")], check=True)
                subprocess.run(["ld", os.path.join(d, "m.o"), "-o", os.path.join(d, "m")], check=True)
            except OSError as e:                                  # nasm / ld not installed: skip
                print("SKIP selftest (nasm/ld not available):", e); return True
            except subprocess.CalledProcessError as e:            # a mutant that does not assemble / link is a broken selftest, not a skip
                print(f"FAIL selftest: mutant '{name}' did not assemble / link: {e}"); ok = False; continue
            caught = not run_all([os.path.join(d, "m")], n, seed, quiet=True)
        print(("PASS " if caught else "FAIL ") + f"selftest: mutant '{name}' is detected")
        ok = ok and caught
    return ok


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", default="", help="default: environment variable ENGINE, else ./zeval (zeval.exe on Windows), like the other tests")
    ap.add_argument("--n", type=int, default=1500)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--tempo", type=int, default=None, help=f"tempo value the tempo test expects (default: %%define TEMPO of src/eval.asm = {TEMPO})")
    ap.add_argument("--selftest", action="store_true", help="also check that deliberately broken evals are caught (needs nasm, ld)")
    a = ap.parse_args()
    if a.tempo is not None: TEMPO = a.tempo
    engine = resolve(a.engine) if a.engine else ENGINE
    ok = run_all(engine, a.n, a.seed)
    if a.selftest:
        ok = selftest(engine, min(a.n, 600), a.seed) and ok
    print("ALL PASSED" if ok else "FAILED")
    sys.exit(0 if ok else 1)
