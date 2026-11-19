#!/usr/bin/env python3
"""pp_ok check (pre-make pruning).

pp_ok (src/p2b_bb.asm) decides "legal?" and "gives check?" for a quiet move WITHOUT making it.  The PPVERIFY test build asks it for
every quiet move of every node that is not in check and compares the answer with make + attacked() + in_check().  A difference
makes the engine exit with code 3.  This script runs that build on many positions (start position, perft positions, random
playouts, positions with many pins / sliders) and reports any search that ended with exit code 3 or without a bestmove.

Build the test engine (Linux, one unit):
    sh tests/build1.sh ./zeval_ppverify -DPPVERIFY
Run:    python3 tests/t_ppverify.py ./zeval_ppverify [--random 600] [--nodes 150000] [--seed 5]
        python3 tests/t_ppverify.py --selftest      (builds pp_ok six + one ways broken; every broken build must be caught)
Needs python-chess.
"""
import os, random, re, shutil, subprocess, sys, tempfile
import chess

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = ["p1_data.asm", "eval.asm", "p2_core.asm", "p2b_bb_data.inc", "p2b_bb.asm", "p3_cmd.asm", "p4_search.asm", "p5_main.asm", "p6_win.asm", "p7_units.asm"]

FIXED = [
    chess.STARTING_FEN,
    "r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1",
    "8/2p5/3p4/KP5r/1R3p1k/8/4P1P1/8 w - - 0 1",
    "r3k2r/Pppp1ppp/1b3nbN/nP6/BBP1P3/q4N2/Pp1P2PP/R2Q1RK1 w kq - 0 1",
    "rnbq1k1r/pp1Pbppp/2p5/8/2B5/8/PPP1NnPP/RNBQK2R w KQ - 1 8",
    "r4rk1/1pp1qppp/p1np1n2/2b1p1B1/2B1P1b1/P1NP1N2/1PP1QPPP/R4RK1 w - - 0 10",
    "4k3/8/8/8/8/8/4P3/4K3 w - - 0 1",
    "n1n5/PPPk4/8/8/8/8/4Kppp/5N1N b - - 0 1",
    # pins and discovered checks
    "4k3/4r3/8/8/8/8/4N3/4K3 w - - 0 1",            # knight pinned on the e-file
    "4k3/8/8/8/1b6/8/3B4/4K3 w - - 0 1",            # bishop pinned on a diagonal, can move along it
    "4r1k1/8/8/8/8/8/4R3/4K3 w - - 0 1",            # rook pinned, moves along the file
    "4k3/8/8/8/8/8/3PPP2/R3K2R w KQ - 0 1",
    "6k1/8/8/8/8/2N5/8/R3K3 w Q - 0 1",             # discovered check by moving the knight
    "4k3/8/8/4B3/8/8/8/4R1K1 w - - 0 1",            # discovered check by the bishop moving off the e-file
]

def random_positions(n, seed):
    rnd = random.Random(seed)
    out = list(FIXED)
    while len(out) < n + len(FIXED):
        b = chess.Board()
        for _ in range(rnd.randint(4, 110)):
            ms = list(b.legal_moves)
            if not ms or b.is_game_over():
                break
            # prefer quiet moves a bit: pins and discovered checks need pieces to stay on the board
            q = [m for m in ms if not b.is_capture(m)]
            b.push(rnd.choice(q) if q and rnd.random() < 0.7 else rnd.choice(ms))
        if b.is_game_over() or b.is_check():
            continue
        out.append(b.fen())
    return out

def run_batch(engine, fens, nodes, timeout=120):
    inp = "uci\nisready\n" + "".join(f"ucinewgame\nposition fen {f}\ngo nodes {nodes}\n" for f in fens) + "quit\n"
    try:
        p = subprocess.run(engine, input=inp, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return -9, 0                                    # a hung engine is a failure (rc -9)
    except OSError as e:
        sys.stderr.write(f"ERROR: cannot run the engine {engine}: {e}\n"); sys.exit(2)
    return p.returncode, p.stdout.count("bestmove ")

def search(engine, fens, nodes, chunk=20):
    """Runs every position; on failure bisects to the first failing position.  Returns (done, failing fen list, why)"""
    bad = []; done = 0
    for i in range(0, len(fens), chunk):
        part = fens[i:i + chunk]
        rc, nb = run_batch(engine, part, nodes)
        if rc == 0 and nb == len(part):
            done += len(part); continue
        culprits = 0
        for f in part:                                  # find the culprit(s)
            rc1, nb1 = run_batch(engine, [f], nodes)
            if rc1 != 0 or nb1 != 1:
                bad.append(f); culprits += 1; print(f"  engine ended (exit code {rc1}) at position: {f}")
            else:
                done += 1
        if not culprits:                                # the chunk failed but every position passes alone: a state-dependent failure, NOT a pass
            msg = f"positions {i}..{i + len(part) - 1} failed TOGETHER (exit code {rc}, {nb}/{len(part)} bestmoves) but each one passes alone: state-dependent (hash / history / stack)"
            bad.append(msg); print("  " + msg)
        if bad: break
    return done, bad, ""

def build(flags, out, patch=None):
    tmp = tempfile.mkdtemp()
    try:
        parts = []
        for f in SRC:
            s = open(os.path.join(ROOT, "src", f), encoding="utf-8", errors="surrogateescape").read()
            if patch and f == "p2b_bb.asm":
                rx, new = patch
                s2, k = re.subn(rx, new, s, count=1, flags=re.S)
                if k != 1:
                    raise SystemExit(f"selftest patch did not apply: {rx!r}")
                s = s2
            parts.append(s)
        cat = os.path.join(tmp, "c.asm")
        open(cat, "w", encoding="utf-8", errors="surrogateescape").write("".join(parts))
        subprocess.check_call(["nasm", "-f", "elf64", "-DUNIT=0", "-DNUNITS=1"] + flags + [cat, "-o", os.path.join(tmp, "c.o")])
        subprocess.check_call(["ld", os.path.join(tmp, "c.o"), "-o", out])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

def selftest(seed):
    sp = r"\s+"
    patches = [
        ("bishop-pin legality test removed",   r"(PPLOOK btab, rdi, r10\s+test rax, rdx\s+)jnz \.ill", r"\1nop"),
        ("rook-pin legality test removed",     r"(PPLOOK rtab, rdi, r10\s+test rax, rdx\s+)jnz \.ill", r"\1nop"),
        ("bishop / queen check test removed",  r"(PPLOOK btab, rdi, r10\s+test rax, rdx\s+)jnz \.chk", r"\1nop"),
        ("rook / queen check test removed",    r"(PPLOOK rtab, rdi, r10\s+test rax, rdx\s+)jnz \.chk", r"\1nop"),
        ("knight check test removed",          r"(\.kn:mov rax, \[knight_att\+rcx\]\s+bt rax, r9\s+)jc \.chk", r"\1nop"),
        ("pawn check test removed",            r"(mov rax, \[patt_to\+rax\][^\n]*\n\s+bt rax, r9\s+)jc \.chk", r"\1nop"),
        ("moving slider not swapped in the check test", r"\.tb:btc rdx, r8[^\n]*\n\s+btc rdx, r9", r".tb:nop\n    nop"),
    ]
    fens = random_positions(200, seed + 7)
    tmp = tempfile.mkdtemp()
    allok = True
    try:
        e = os.path.join(tmp, "good")
        build(["-DPPVERIFY"], e)
        done, bad, _ = search(e, fens, 60000)
        print(f"selftest (unbroken build passes on {done} positions): {'ok' if not bad else 'FAILED'}")
        allok = allok and not bad
        for name, rx, new in patches:
            e = os.path.join(tmp, "broken")
            build(["-DPPVERIFY"], e, (rx, new))
            done, bad, _ = search(e, fens, 60000)
            print(f"selftest ({name}): caught = {bool(bad)}")
            allok = allok and bool(bad)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("SELFTEST", "PASSED" if allok else "FAILED")
    return 0 if allok else 1

def main():
    a = sys.argv[1:]
    def opt(name, default):
        if name in a:
            i = a.index(name); v = type(default)(a[i + 1]); del a[i:i + 2]; return v
        return default
    n = opt("--random", 600); nodes = opt("--nodes", 150000); seed = opt("--seed", 5)
    if "--selftest" in a:
        sys.exit(selftest(seed))
    if not a:
        print(__doc__); sys.exit(2)
    engine = [a[0]]
    if not os.path.exists(a[0]): sys.stderr.write(f"ERROR: {a[0]} not found (build it first, see the header of this file)\n"); sys.exit(2)
    fens = random_positions(n, seed)
    print(f"{len(fens)} positions, go nodes {nodes}")
    done, bad, _ = search(engine, fens, nodes)
    print(f"searches completed: {done}/{len(fens)}   failures: {len(bad)}")
    sys.exit(1 if bad else 0)

if __name__ == "__main__":
    main()
