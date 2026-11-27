#!/usr/bin/env python3
"""TTFIRST check: the TT move searched before any move is generated (see TTFIRST in src/p1_data.asm).

TTFIRST 1 changes the search on purpose (the other moves are scored after the subtree of the TT move, so they see its history updates);
what must NOT change is everything else.  Four checks, each against a different way of getting it wrong:
  1. plumbing  -DTTFIRST=1 -DTTFEAGER: the one-move list, the singular test, the hand-over to the full list and the pick permutation are
     exercised exactly as in TTFIRST 1, but the list is generated and scored at the start of the node as always.  This build must reproduce
     golden_depth8.json bit for bit (tests/t_golden.py).  A wrong MCNT / index / swap / skipped move shows up as a different node count.
  2. validator -DTTFIRST=1 -DTTFVERIFY: at every node tt_pseudo is compared with gen: for every move of the node, its 16 one-bit flips and 64
     random 16-bit values, "accepted" must mean "in gen's list" (exit code 12 = unsound: a move would be made without being generated) and
     "rejected" must mean "not in the list" except for castling moves (exit code 13 = incomplete: only the speed-up is lost).
  3. garbage   -DTTFIRST=1 -DTTFUZZ: every table move is a random 16-bit number.  bestmove is always legal, no crash, no hang.
  4. reference tests/golden_depth8_ttfirst.json is the search of the default-off switch turned on (build -DTTFIRST=1): it must be reproduced
     exactly (same kind of check as the other golden_depth8_*.json files).
  --selftest   breaks tt_pseudo twelve ways (text patches of a temporary copy); check 2 must catch every one of them.
Linux only: needs nasm, ld, python-chess.   usage:  python3 tests/t_ttfirst.py [--positions 120] [--nodes 5000] [--quick] [--selftest]
Exit code 0 = everything passed, 1 = a check failed, 2 = a tool could not be run.
"""
import os, random, re, shutil, subprocess, sys, tempfile
import chess

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
args = sys.argv[1:]
def opt(name, default):
    if name in args:
        i = args.index(name); v = type(default)(args[i + 1]); del args[i:i + 2]; return v
    return default
NPOS = opt("--positions", 120); NODES = opt("--nodes", 5000)
QUICK = "--quick" in args; SELFTEST = "--selftest" in args
PARTS = ["p1_data.asm", "eval.asm", "p2_core.asm", "p2b_bb_data.inc", "p2b_bb.asm", "p3_cmd.asm", "p4_search.asm", "p5_main.asm", "p6_win.asm", "p7_units.asm"]
fails = 0
def check(ok, msg):
    global fails
    print(("PASS " if ok else "FAIL ") + msg, flush=True)
    if not ok: fails += 1

def sh(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode:
        sys.stderr.write("FAILED: " + " ".join(cmd) + "\n" + r.stdout[-800:] + r.stderr[-1500:] + "\n"); sys.exit(2)
    return r.stdout

def build(tmp, name, flags, patch=None):
    """patch = (old, new) in p2b_bb.asm, or (file, old, new): a text change of a temporary copy (the selftests)"""
    if patch and len(patch) == 2: patch = ("p2b_bb.asm",) + tuple(patch)
    asm = os.path.join(tmp, name + ".asm")
    with open(asm, "w", newline="") as o:
        for p in PARTS:
            t = open(os.path.join(ROOT, "src", p), newline="").read()
            if patch and p == patch[0]:
                assert t.count(patch[1]) == 1, f"selftest patch target not found or not unique: {patch[1]!r}"
                t = t.replace(patch[1], patch[2])
            o.write(t)
    ob = os.path.join(tmp, name + ".o")
    sh(["nasm", "-f", "elf64", "-DUNIT=0", "-DNUNITS=1"] + flags + [asm, "-o", ob])
    exe = os.path.join(tmp, name)
    sh(["ld", ob, "-o", exe])
    return exe

FIXED = [
    chess.STARTING_FEN,
    "r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1",      # castling both sides, many pieces
    "8/2p5/3p4/KP5r/1R3p1k/8/4P1P1/8 w - - 0 1",
    "r3k2r/Pppp1ppp/1b3nbN/nP6/BBP1P3/q4N2/Pp1P2PP/R2Q1RK1 w kq - 0 1",          # promotions, captures onto the last rank
    "rnbq1k1r/pp1Pbppp/2p5/8/2B5/8/PPP1NnPP/RNBQK2R w KQ - 1 8",
    "n1n5/PPPk4/8/8/8/8/4Kppp/5N1N b - - 0 1",                                      # promotions of both sides
    "rnbqkbnr/ppp1p1pp/8/3pPp2/8/8/PPPP1PPP/RNBQKBNR w KQkq f6 0 3",               # en passant available
    "rnbqkbnr/pppp1ppp/8/8/3Pp3/8/PPP1PPPP/RNBQKBNR b KQkq d3 0 3",                # en passant for black
    "4k3/4r3/8/8/8/8/4N3/4K3 w - - 0 1",                                            # pinned knight
    "4k3/8/8/8/8/8/3PPP2/R3K2R w KQ - 0 1",
    "6k1/5ppp/8/8/8/8/5PPP/R5K1 w - - 0 1",
    "4k3/8/8/8/8/8/8/4K2R b K - 0 1",
]

def positions(n, seed):
    rnd = random.Random(seed); out = list(FIXED)
    while len(out) < n + len(FIXED):
        b = chess.Board()
        for _ in range(rnd.randint(2, 110)):
            ms = list(b.legal_moves)
            if not ms: break
            q = [m for m in ms if not b.is_capture(m)]
            b.push(rnd.choice(q) if q and rnd.random() < 0.6 else rnd.choice(ms))
        if not b.is_game_over(): out.append(b.fen())
    return out

def run(engine, fens, go, timeout=900):
    inp = "uci\nisready\n" + "".join(f"ucinewgame\nposition fen {f}\n{go}\n" for f in fens) + "quit\n"
    try:
        p = subprocess.run([engine], input=inp, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return -9, ""
    return p.returncode, p.stdout

def verify_run(engine, fens, nodes, chunk=15):
    """True when every search of every chunk ended with exit code 0 and a bestmove; a failing chunk is bisected to its position"""
    for i in range(0, len(fens), chunk):
        part = fens[i:i + chunk]
        rc, out = run(engine, part, f"go nodes {nodes}")
        if rc == 0 and out.count("bestmove ") == len(part): continue
        for f in part:
            rc1, out1 = run(engine, [f], f"go nodes {nodes}")
            if rc1 != 0 or out1.count("bestmove ") != 1:
                return False, rc1, f
        return False, rc, f"state-dependent failure in positions {i}..{i + len(part) - 1}"
    return True, 0, ""

BREAKS = [   # (name, text of tt_pseudo, replacement): every one makes the validator accept a move that gen does not write, or reject one that it does
    ("destination: own piece allowed",       "    jz .no                      ; own piece on the destination", "    nop"),
    ("from square: wrong colour allowed",    "    jnz .no                     ; not a piece of the side to move", "    nop"),
    ("to square: 0x88 test removed",         "    test r8d, 0x88\n    jnz .no\n", "    test r8d, 0x88\n    nop\n"),
    ("promo field of a non-pawn allowed",    "    jnz .no                     ; only a promotion has a promo field", "    nop"),
    ("bishop also moves like a rook",        "    je .no                      ; a bishop has no more lines", "    nop"),
    ("double push: square between not checked", "    cmp byte [board+rax], 0\n    jne .no\n", "    cmp byte [board+rax], 0\n    nop\n"),
    ("double push: start rank not checked",  ".d1:cmp eax, edx\n    jne .no", ".d1:cmp eax, edx\n    nop"),
    ("en passant: any empty destination",    "    jne .no                     ; empty destination: only en passant", "    nop"),
    ("push onto an occupied square",         "    jnz .no                     ; the square in front is not empty", "    nop"),
    ("promo field on a non-last rank",       ".p1:cmp eax, edx\n    je .yes\n    test edi, edi\n    jnz .no", ".p1:cmp eax, edx\n    je .yes\n    test edi, edi\n    nop"),
    ("knight moves like a king",             "    mov rax, [knight_att+rcx*8]\n    jmp .tst", "    mov rax, [king_att+rcx*8]\n    jmp .tst"),
    ("white pawn capture to the right lost", "    cmp ecx, 17\n    je .cap", "    cmp ecx, 18\n    je .cap"),
]

def main():
    fens = positions(NPOS, 11)
    tmp = tempfile.mkdtemp()
    try:
        if SELFTEST:
            good = build(tmp, "good", ["-DTTFIRST=1", "-DTTFVERIFY"])
            ok, rc, why = verify_run(good, fens, NODES)
            check(ok, f"selftest: the unbroken validator passes on {len(fens)} positions")
            for name, old, new in BREAKS:
                e = build(tmp, "broken", ["-DTTFIRST=1", "-DTTFVERIFY"], (old, new))
                ok, rc, why = verify_run(e, fens, NODES)
                check(not ok and rc in (12, 13), f"selftest: caught  [{name}]  (exit code {rc})")
            print("SELFTEST", "FAILED" if fails else "PASSED"); return 1 if fails else 0

        # 1. plumbing: the eager build is the golden search
        eager = build(tmp, "eager", ["-DTTFIRST=1", "-DTTFEAGER"])
        r = subprocess.run([sys.executable, os.path.join(HERE, "t_golden.py"), eager], capture_output=True, text=True)
        check(r.returncode == 0 and "IDENTICAL" in r.stdout, "TTFEAGER reproduces golden_depth8.json (" + (r.stdout.strip().splitlines() or ["?"])[-1] + ")")
        # 2. validator against gen
        ver = build(tmp, "verify", ["-DTTFIRST=1", "-DTTFVERIFY"])
        ok, rc, why = verify_run(ver, fens, NODES)
        check(ok, f"tt_pseudo agrees with gen at every node of {len(fens)} positions (go nodes {NODES})" + ("" if ok else f": exit code {rc} at {why}"))
        # 3. garbage table moves
        fuzz = build(tmp, "fuzz", ["-DTTFIRST=1", "-DTTFUZZ"])
        bad = 0
        for f in positions(40 if QUICK else 100, 23):
            for go in ("go depth 1", "go depth 3", "go nodes 4000"):
                rc, out = run(fuzz, [f], go, timeout=60)
                m = re.search(r"^bestmove (\S+)", out, re.M)
                b = chess.Board(f)
                if rc != 0 or not m or chess.Move.from_uci(m[1]) not in b.legal_moves:
                    bad += 1; print("  bad:", f, go, rc, m and m[1])
        check(bad == 0, "TTFUZZ (every TT move garbage): legal bestmove, no crash")
        # 4. the lazy search against its own reference
        lazy = build(tmp, "lazy", ["-DTTFIRST=1"])
        gold = os.path.join(HERE, "golden_depth8_ttfirst.json")
        if os.path.exists(gold):
            r = subprocess.run([sys.executable, os.path.join(HERE, "t_golden.py"), lazy, gold], capture_output=True, text=True)
            check(r.returncode == 0 and "IDENTICAL" in r.stdout, "TTFIRST 1 reproduces golden_depth8_ttfirst.json (" + (r.stdout.strip().splitlines() or ["?"])[-1] + ")")
        else:
            check(False, "tests/golden_depth8_ttfirst.json is missing (record it with t_golden.py --make)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("ALL PASSED" if not fails else f"{fails} CHECK(S) FAILED")
    return 1 if fails else 0

if __name__ == "__main__":
    sys.exit(main())
