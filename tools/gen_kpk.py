#!/usr/bin/env python3
"""Generates the king + pawn vs king bitbase of src/eval.asm (table kpk_bb) by retrograde analysis.

Index of a position with the pawn side as White (the engine mirrors other positions into this form):
    idx = ((stm * 64 + wk) * 64 + bk) * 24 + (pawn_rank - 1) * 4 + pawn_file
    stm 0 = White (pawn side) to move, 1 = Black; squares a1 = 0 .. h8 = 63; pawn on files a..d, ranks 2..7.
Bit idx of the table is 1 when White wins, 0 when the position is a draw or impossible.
Rules: a promotion wins when the queen cannot be taken (the black king is not next to the promotion square, or the white king defends it); a black
king that takes the pawn draws; no legal move for Black is a draw (stalemate) unless Black is in check (mate).  Unresolved positions are draws.

usage:  python3 tools/gen_kpk.py            prints the asm block (the lines between "kpk_bb:" and "kpk_bb_end:" in src/eval.asm)
        python3 tools/gen_kpk.py --check    compares with src/eval.asm, exit code 0 = identical, 1 = different
"""
import os, re, sys

N = 2 * 64 * 64 * 24
UNKNOWN, WIN, DRAW, INVALID = 0, 1, 2, 3


def sq(f, r): return r * 8 + f
def dist(a, b): return max(abs((a & 7) - (b & 7)), abs((a >> 3) - (b >> 3)))


def kmoves(s):
    f, r = s & 7, s >> 3
    return [sq(f + df, r + dr) for df in (-1, 0, 1) for dr in (-1, 0, 1)
            if (df or dr) and 0 <= f + df < 8 and 0 <= r + dr < 8]


KM = [kmoves(s) for s in range(64)]
PSQ = [sq(f, r) for r in range(1, 7) for f in range(4)]                 # pawn index -> square
PIDX = {s: i for i, s in enumerate(PSQ)}


def idx(stm, wk, bk, p): return ((stm * 64 + wk) * 64 + bk) * 24 + PIDX[p]


def pawn_att(p):
    f = p & 7
    return {p + 7} if f == 7 else ({p + 9} if f == 0 else {p + 7, p + 9})


def generate():
    res = bytearray(N)
    for stm in (0, 1):
        for wk in range(64):
            for bk in range(64):
                for p in PSQ:
                    i = idx(stm, wk, bk, p)
                    if wk == bk or wk == p or bk == p or dist(wk, bk) <= 1:
                        res[i] = INVALID; continue
                    att = pawn_att(p)
                    if stm == 0:
                        if bk in att:
                            res[i] = INVALID; continue          # Black in check with White to move
                        if p >> 3 == 6:
                            q = p + 8
                            if q != wk and q != bk and (dist(bk, q) > 1 or dist(wk, q) == 1):
                                res[i] = WIN
                    else:
                        moves = [t for t in KM[bk] if t != wk and dist(t, wk) > 1 and t not in att]
                        if p in moves and dist(wk, p) > 1:
                            res[i] = DRAW                       # the pawn falls
                        elif not moves:
                            res[i] = WIN if bk in att else DRAW
    changed = True
    while changed:
        changed = False
        for stm in (0, 1):
            for wk in range(64):
                for bk in range(64):
                    for p in PSQ:
                        i = idx(stm, wk, bk, p)
                        if res[i] != UNKNOWN:
                            continue
                        if stm == 0:
                            kids = []
                            for t in KM[wk]:
                                if t != p and dist(t, bk) > 1:
                                    kids.append(res[idx(1, t, bk, p)])
                            q = p + 8
                            if q != wk and q != bk:
                                if q >> 3 == 7:
                                    kids.append(DRAW)           # promotion that loses the queen (the winning ones are set above)
                                else:
                                    kids.append(res[idx(1, wk, bk, q)])
                                    if p >> 3 == 1 and q + 8 != wk and q + 8 != bk:
                                        kids.append(res[idx(1, wk, bk, q + 8)])
                            if WIN in kids:
                                res[i] = WIN; changed = True
                            elif kids and all(k == DRAW or k == INVALID for k in kids):
                                res[i] = DRAW; changed = True
                        else:
                            att = pawn_att(p)
                            kids = [res[idx(0, wk, t, p)] for t in KM[bk] if t != wk and dist(t, wk) > 1 and t not in att and t != p]
                            if DRAW in kids:
                                res[i] = DRAW; changed = True
                            elif kids and all(k == WIN for k in kids):
                                res[i] = WIN; changed = True
    return res


def asm_block(res):
    q = []
    for w in range(N // 64):
        v = 0
        for b in range(64):
            if res[w * 64 + b] == WIN:
                v |= 1 << b
        q.append(v)
    lines = []
    for i in range(0, len(q), 4):
        lines.append("    dq " + ", ".join(f"0x{x:016X}" for x in q[i:i + 4]))
    return "\n".join(lines) + "\n"


def main():
    res = generate()
    blk = asm_block(res)
    wins = sum(1 for i in range(N) if res[i] == WIN)
    valid = sum(1 for i in range(N) if res[i] != INVALID)
    if "--check" in sys.argv:
        src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "eval.asm")).read()
        m = re.search(r"^kpk_bb:[^\n]*\n(.*?)^kpk_bb_end:", src, re.M | re.S)
        same = bool(m) and m.group(1) == blk
        print(f"kpk bitbase: {valid} legal positions, {wins} wins; src/eval.asm table {'IDENTICAL' if same else 'DIFFERENT'}")
        sys.exit(0 if same else 1)
    sys.stdout.write(blk)
    sys.stderr.write(f"kpk bitbase: {valid} legal positions, {wins} wins\n")


if __name__ == "__main__":
    main()
