#!/usr/bin/env python3
"""EV_SFAST / EV_KLBITS / EV_BRSIGN check: three speed-only switches of the evaluation, on by default (see the header of src/eval.asm).

All three keep every evaluation value, so:
  1. golden    the default, all three off, each switch alone, and the default with -DHWPOPCNT=0 reproduce golden_depth8.json (t_golden.py).
  2. eval      the static eval ("eval" command) of the build with all three off and of the default (HWPOPCNT 2 and 0) is the
               same on every position: positions of random games, random positions of the material classes of the scale rules (rook,
               bishop, queen and minor-piece endings, pawns on one flank, on both, on a rook file) and middlegame positions with the kings
               anywhere (king-line terms).
  3. rule 6    EV_SFAST takes its pawn threshold from SC_G0 / SC_G1: with other constants of rule 6 (text patches of a temporary copy)
               the eval with EV_SFAST still equals the eval without it.
  4. verify    -DEV_SFAST=1 -DSFASTVERIFY: wherever EV_SFAST takes 64, ev_scale is called as well and must give 64 (exit code 17 =
               it does not).  The eval of the positions of check 2 and searches of random positions.
  --selftest   breaks each switch several ways; check 4 must catch the EV_SFAST breaks (exit code 17), check 2 the others.
Linux only: needs nasm, ld, python-chess.   usage:  python3 tests/t_evspeed.py [--positions 20000] [--nodes 8000] [--selftest]
Exit code 0 = everything passed, 1 = a check failed, 2 = a tool could not be run."""
import os, random, subprocess, sys, tempfile, shutil
import chess
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
args = sys.argv[1:]
def opt(name, default):
    if name in args:
        i = args.index(name); v = type(default)(args[i + 1]); del args[i:i + 2]; return v
    return default
NPOS = opt("--positions", 20000); NODES = opt("--nodes", 8000); SELFTEST = "--selftest" in args
import t_ttfirst as T          # build(), positions(), run() ; importing does not run its checks
ALL = ["-DEV_SFAST=1", "-DEV_KLBITS=1", "-DEV_BRSIGN=1"]     # the default
OFF = ["-DEV_SFAST=0", "-DEV_KLBITS=0", "-DEV_BRSIGN=0"]
VFY = ["-DEV_SFAST=1", "-DSFASTVERIFY"]
fails = 0
def check(ok, msg):
    global fails
    print(("PASS " if ok else "FAIL ") + msg, flush=True)
    if not ok: fails += 1

def golden(engine):
    r = subprocess.run([sys.executable, os.path.join(HERE, "t_golden.py"), engine], capture_output=True, text=True)
    return r.returncode == 0 and "IDENTICAL" in r.stdout, (r.stdout.strip().splitlines() or ["?"])[-1]

def evals(engine, fens):
    """eval of each position; None when the engine ends early (exit code != 0) or prints fewer values"""
    inp = "".join(f"position fen {f}\neval\n" for f in fens) + "quit\n"
    p = subprocess.run([engine], input=inp, capture_output=True, text=True, timeout=3600)
    v = [int(l.split()[1]) for l in p.stdout.splitlines() if l.startswith("eval ")]
    return (v if p.returncode == 0 and len(v) == len(fens) else None), p.returncode

def place(rng, white, black):
    """random legal position: white / black = (pieces other than king and pawns, number of pawns, allowed pawn files or None)"""
    for _ in range(200):
        b = chess.Board(None); free = set(range(64))
        def put(piece, squares):
            for s in squares:
                if s in free:
                    b.set_piece_at(s, piece); free.discard(s); return True
            return False
        ok = put(chess.Piece(chess.KING, chess.WHITE), rng.sample(range(64), 64)) and \
             put(chess.Piece(chess.KING, chess.BLACK), rng.sample(range(64), 64))
        for colour, (pieces, pawns, files) in ((chess.WHITE, white), (chess.BLACK, black)):
            for p in pieces:
                ok = ok and put(chess.Piece(chess.Piece.from_symbol(p).piece_type, colour), rng.sample(range(64), 64))
            for _ in range(pawns):
                cand = [r * 8 + f for r in range(1, 7) for f in (files or range(8))]
                ok = ok and put(chess.Piece(chess.PAWN, colour), rng.sample(cand, len(cand)))
        if ok:
            b.turn = rng.random() < 0.5
            if b.is_valid():
                return b.fen()
    return None

def eval_positions(n, seed):
    rng = random.Random(seed); fens = []
    while len(fens) < n // 3:                                   # positions of random games, opening to endgame
        b = chess.Board()
        for ply in range(rng.randrange(10, 160)):
            ms = list(b.legal_moves)
            if not ms: break
            caps = [m for m in ms if b.is_capture(m)]
            b.push(rng.choice(caps) if caps and rng.random() < 0.35 else rng.choice(ms))
            if ply % 3 == 0 and not b.is_game_over(): fens.append(b.fen())
    qside, kside = [0, 1, 2, 3], [4, 5, 6, 7]
    sets = [("R", "R"), ("B", "B"), ("BN", "BR"), ("Q", "RB"), ("Q", "Q"), ("N", "B"), ("", ""), ("RR", "RB"), ("QR", "QN"),
            ("BB", "B"), ("B", ""), ("R", "")]
    flanks = [(None, None), (qside, None), (kside, None), (qside, qside), (kside, kside), ([0], None), ([7], None), (qside, kside), (None, [0])]
    while len(fens) < 2 * n // 3:                               # material classes of the scale rules
        (pw, pb), (fw, fb) = rng.choice(sets), rng.choice(flanks)
        w, bl = (pw, rng.randrange(7), fw), (pb, rng.randrange(7), fb)
        f = place(rng, *((w, bl) if rng.random() < 0.5 else (bl, w)))
        if f: fens.append(f)
    while len(fens) < n:                                        # middlegame material, kings anywhere (king-line terms)
        f = place(rng, (rng.choice(["RQ", "RRQ", "RRBN", "QBN", "RB", "Q"]), rng.randrange(3, 8), None),
                       (rng.choice(["RQ", "RRQ", "RRBN", "QBN", "RN", "R"]), rng.randrange(3, 8), None))
        if f: fens.append(f)
    return fens

def same_evals(a, b, fens):
    va, rca = evals(a, fens); vb, rcb = evals(b, fens)
    if va is None or vb is None: return False, f"an engine ended early (exit codes {rca} / {rcb})"
    d = [i for i in range(len(fens)) if va[i] != vb[i]]
    return not d, (f"{len(d)} differ, first {fens[d[0]]}: {va[d[0]]} / {vb[d[0]]}" if d else f"{len(fens)} positions, 0 differ")

def verify(engine, fens, sfens):
    v, rc = evals(engine, fens)
    if v is None: return False, rc, "eval of the positions"
    ok, rc, why = T.verify_run(engine, sfens, NODES)
    return ok, rc, why

RULE6 = "%define SC_G0    36                 ; 6. min(64, SC_G0 + SC_G1 * pawns of s) - SC_G6FL on one flank\n%define SC_G1    8\n"
def rule6(g0, g1):
    return ("eval.asm", RULE6, RULE6.replace("SC_G0    36", f"SC_G0    {g0}").replace("SC_G1    8", f"SC_G1    {g1}"))

BREAKS_SF = [   # caught by the verify build (exit code 17)
    ("the queen test left out", "    cmp al, 1\n    je %%slow\n", "    cmp al, 1\n"),
    ("pawns on the king side not required", "    test rax, [ev_kside]\n    jz %%slow\n    mov al, [cnt+5]", "    mov al, [cnt+5]"),
    ("one pawn fewer than the threshold", "    cmp byte [cnt+r9+1], EV_SFPMIN\n", "    cmp byte [cnt+r9+1], EV_SFPMIN - 1\n"),
    ("opposite bishops taken as same-coloured", "    cmp al, dl\n    jne %%slow\n%%fast:", "    cmp al, dl\n    je %%slow\n%%fast:"),
    ("the pawns of the weak side tested", "    mov rax, [bbp+r9*8+8]\n", "    mov eax, r9d\n    xor eax, 8\n    mov rax, [bbp+rax*8+8]\n"),
]
BREAKS_EV = [   # caught by the eval comparison
    ("storm window of two ranks", "    shr r8, 24                      ; squares more", "    shr r8, 16                      ; squares more"),
    ("storm pawn blocked by their pawn counted", "    and r11, r12                    ; not blocked", "    nop                             ; not blocked"),
    ("storm: nearest pawn over one rank only", "    shr rdx, 16\n    or rax, rdx\n    not rax\n", "    not rax\n"),
    ("heavy pieces counted on closed files", "    or rax, [rbx+EV_QB]\n    and rax, rdx\n", "    or rax, [rbx+EV_QB]\n    and rax, rdi\n"),
    ("side-to-move sign inverted", "    cmovne eax, ecx\n%else", "    cmove eax, ecx\n%else"),
]

def main():
    tmp = tempfile.mkdtemp()
    try:
        if SELFTEST:
            fens = eval_positions(min(NPOS, 6000), 3); sfens = T.positions(60, 33)
            base = T.build(tmp, "base", OFF)
            good = T.build(tmp, "good", VFY)
            ok, rc, why = verify(good, fens, sfens)
            check(ok, f"selftest: the unbroken EV_SFAST passes ({len(fens)} evals, {len(sfens)} searches)")
            for name, old, new in BREAKS_SF:
                e = T.build(tmp, "broken", VFY, ("eval.asm", old, new))
                ok, rc, why = verify(e, fens, sfens)
                check(not ok and rc == 17, f"selftest: caught  [EV_SFAST: {name}]  (exit code {rc})")
            for name, old, new in BREAKS_EV:
                e = T.build(tmp, "broken", ALL, ("eval.asm", old, new))
                ok, why = same_evals(base, e, fens)
                check(not ok, f"selftest: caught  [{name}]  ({why.split(',')[0]})")
            print("SELFTEST", "FAILED" if fails else "PASSED"); return 1 if fails else 0
        for name, flags in (("the default (all three on)", []), ("all three off", OFF),
                            ("EV_SFAST alone", ["-DEV_SFAST=1", "-DEV_KLBITS=0", "-DEV_BRSIGN=0"]),
                            ("EV_KLBITS alone", ["-DEV_SFAST=0", "-DEV_KLBITS=1", "-DEV_BRSIGN=0"]),
                            ("EV_BRSIGN alone", ["-DEV_SFAST=0", "-DEV_KLBITS=0", "-DEV_BRSIGN=1"]),
                            ("the default, HWPOPCNT=0", ["-DHWPOPCNT=0"])):
            ok, line = golden(T.build(tmp, "g", flags))
            check(ok, f"{name} reproduces golden_depth8.json ({line})")
        fens = eval_positions(NPOS, 1)
        base = T.build(tmp, "base", OFF)
        for name, flags in (("the default", []), ("the default, HWPOPCNT=0", ["-DHWPOPCNT=0"])):
            ok, why = same_evals(base, T.build(tmp, "e", flags), fens)
            check(ok, f"{name}: the same eval as all three off ({why})")
        f6 = fens[len(fens) // 3: 2 * len(fens) // 3]
        for g0, g1 in ((30, 6), (40, 5), (64, 0), (50, 0)):
            b = T.build(tmp, "r6a", ["-DEV_SFAST=0"], rule6(g0, g1)); e = T.build(tmp, "r6b", ["-DEV_SFAST=1"], rule6(g0, g1))
            ok, why = same_evals(b, e, f6)
            check(ok, f"rule 6 with SC_G0 {g0}, SC_G1 {g1}: EV_SFAST gives the same eval ({why})")
        ok, rc, why = verify(T.build(tmp, "v", VFY), fens, T.positions(150, 31))
        check(ok, f"SFASTVERIFY: ev_scale gives 64 wherever EV_SFAST takes it ({len(fens)} evals, 150 searches, go nodes {NODES})"
                  + ("" if ok else f": exit code {rc}, {why}"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("ALL PASSED" if not fails else f"{fails} CHECK(S) FAILED")
    return 1 if fails else 0

if __name__ == "__main__":
    sys.exit(main())
