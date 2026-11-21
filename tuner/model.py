"""The engine's evaluation as a LINEAR model (the form a Texel-style logistic-regression tuner needs).

Engine eval (src/eval.asm), seen from White:
    mg   = sum over pieces  +-(pst_mg[piece, sq] + matmg[piece])   + BP_MG * (white has >= 2 bishops) - BP_MG * (black ...)
           + sum over the terms of the full evaluation  (count of the feature for White - count for Black) * evp_mg[term]
    eg   = the same with pst_eg / mateg / BP_EG / evp_eg
    ph   = min(phase, 24),   phase = sum(N 1, B 1, R 2, Q 4) over both colours
    sf   = endgame scale factor 0..64 of the position (64 = unscaled; it depends on the position only, never on a parameter)
    e    = trunc( (64 * mg * ph + sf * eg * (24 - ph)) / 1536 )      (truncation towards zero)
    eval(side to move) = (e  if White to move  else  -e) + TEMPO

so the score of a position is  X . w  with
    X = per-parameter counts (white - black), one row per position,
    w = NF parameters x (mg, eg):  6 piece types x 64 squares (material is MERGED into the PST: pst + mat, exactly like the engine's
        mgt / egt tables, so there is no material-vs-PST redundancy for the optimiser), the bishop pair, and the EV_N terms of the full
        evaluation (layout TERMS below = evp in src/eval.asm),
    and one extra scalar: tempo (not tapered, sign = side to move).
A black piece on table square k is a white-table entry at k ^ 56 with coefficient -1 (the engine mirrors the rank); the black side of the
full evaluation is computed on the vertically mirrored board, exactly as the engine does.

The evaluation switches of the build (src/eval.asm, bit mask EV_CFG, console command "evalcfg") decide which terms exist: CFG below, read from the
defaults in src/eval.asm, changed with set_cfg() / set_switches().  Positions that a non-linear special evaluation handles (special = True) are not part
of the linear model: int_eval returns None for them and extract.py leaves them out.

Pure python + numpy (no engine, no python-chess).  This is a second, independent implementation of the evaluation on purpose: check_eval.py compares two
implementations; a feature dump from the engine itself would only compare the engine with itself.
"""
import os, re
from collections import namedtuple
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
ASM = os.path.join(PKG, "src", "eval.asm")

NPST = 384                      # 6 piece types x 64 squares
BP = 384                        # bishop-pair parameter
EVP0 = 385                      # first term of the full evaluation
# (name, number of entries) in the order of evp in src/eval.asm
TERMS = [("mob_n", 9), ("mob_b", 14), ("mob_r", 15), ("mob_q", 28), ("passed", 6), ("pp_own_king", 8), ("pp_enemy_king", 8),
         ("doubled", 1), ("isolated", 1), ("supported", 1), ("phalanx", 1), ("rook_open", 1), ("rook_semi", 1), ("shield", 1),
         ("threat_pawn", 1), ("outpost", 1), ("king_attack", 32),
         ("thr_minor", 5), ("thr_rook", 5), ("thr_king", 1), ("hanging", 1), ("weak_qprot", 1), ("restricted", 1), ("thr_safe_pawn", 1),
         ("thr_pawn_push", 1), ("knight_on_queen", 1), ("slider_on_queen", 1),
         ("kdanger", 64), ("passed_file", 1), ("pp_king_them_w", 1), ("pp_king_us_w", 1), ("pp_king_us2_w", 1), ("passed_free", 4),
         ("passed_free_def", 1),
         ("backward", 1), ("weak_unopposed", 1), ("weak_lever", 1), ("connected", 6), ("blocked56", 2),
         ("shelter", 28), ("storm", 28), ("blocked_storm", 7), ("king_pawn_dist", 1),
         ("bishop_pawns", 1), ("long_diag", 1), ("bishop_outpost", 1), ("reach_outpost", 1), ("minor_behind_pawn", 1), ("king_protector_n", 1),
         ("king_protector_b", 1), ("rook_closed", 1), ("trapped_rook", 1), ("rook_7th", 1), ("rooks_connected", 1), ("rook_behind_passer", 1),
         ("weak_queen", 1), ("imb_ours", 21), ("imb_theirs", 21), ("space", 1),
         ("mob_n_pins", 9), ("mob_b_pins", 14), ("mob_r_pins", 15), ("mob_q_pins", 28), ("kl_open", 1), ("kl_storm", 1), ("kl_heavy", 1)]
TERM_OFS = {}
_o = 0
for _n, _c in TERMS:
    TERM_OFS[_n] = _o; _o += _c
EV_N = _o
NF = EVP0 + EV_N                # number of tapered parameters (each has an mg and an eg value)
NPAR = 2 * NF + 1               # flat vector: [mg(NF) | eg(NF) | tempo]
TEMPO_IDX = 2 * NF
PIECE_NAMES = "PNBRQK"
PIECE_CODE = {c: (i + 1, True) for i, c in enumerate("PNBRQK")}
PIECE_CODE.update({c: (i + 1, False) for i, c in enumerate("pnbrqk")})
PHASE_W = {1: 0, 2: 1, 3: 1, 4: 2, 5: 4, 6: 0}
# groups for --free
GROUPS = {"p": 1, "n": 2, "b": 3, "r": 4, "q": 5, "k": 6}
TERM_GROUPS = {"mob": ["mob_n", "mob_b", "mob_r", "mob_q"],
               "mob_pins": ["mob_n_pins", "mob_b_pins", "mob_r_pins", "mob_q_pins"],
               "pawns": ["doubled", "isolated", "supported", "phalanx"],
               "passed": ["passed", "pp_own_king", "pp_enemy_king"],
               "rooks": ["rook_open", "rook_semi"],
               "king": ["shield", "king_attack"],
               "pieces": ["threat_pawn", "outpost"],
               "threats": ["thr_minor", "thr_rook", "thr_king", "hanging", "weak_qprot", "restricted", "thr_safe_pawn", "thr_pawn_push",
                           "knight_on_queen", "slider_on_queen"],
               "kdanger": ["kdanger"],
               "passed2": ["passed_file", "pp_king_them_w", "pp_king_us_w", "pp_king_us2_w", "passed_free", "passed_free_def"],
               "pawns2": ["backward", "weak_unopposed", "weak_lever", "connected", "blocked56"],
               "shelter": ["shelter", "storm", "blocked_storm", "king_pawn_dist"],
               "pieces2": ["bishop_pawns", "long_diag", "bishop_outpost", "reach_outpost", "minor_behind_pawn", "king_protector_n",
                           "king_protector_b", "rook_closed", "trapped_rook", "rook_7th", "rooks_connected", "rook_behind_passer", "weak_queen"],
               "imbalance": ["imb_ours", "imb_theirs"],
               "space": ["space"],
               "klines": ["kl_open", "kl_storm", "kl_heavy"],
               "full": [n for n, _ in TERMS]}


# ------------------------------------------------------------------------------------------------ evaluation switches
FLAG_BITS = ["EVAL_FULL", "EV_TUNEMODE", "EV_ENDG", "EV_SCALE", "EV_LAZY", "EV_THRMIN", "EV_THRHANG", "EV_THRPAWN", "EV_THRRESTR", "EV_THRQUEEN",
             "EV_PINS", "EV_KDANGER", "EV_PASSED2", "EV_PAWNS2", "EV_SHELTER", "EV_PBISH", "EV_PMINOR", "EV_PROOK", "EV_PQUEEN", "EV_IMBAL",
             "EV_SPACE", "EV_INIT", "EV_GRAIN", "EV_SCALE6", "EV_SCALE2", "EV_ENDG2", "EV_KLINES"]     # bit i of EV_CFG = FLAG_BITS[i]
CFG = {}


def load_cfg(asm=ASM):
    """the default switch values of src/eval.asm (%ifndef NAME / %define NAME value)"""
    src = open(asm).read()

    def value(n, depth=0):
        m = re.search(r"^%ifndef\s+" + n + r"\s*\n%define\s+" + n + r"\s+(\w+)", src, re.M)
        if not m:
            return 0
        v = m.group(1)
        if v.isdigit():
            return int(v)
        return value(v, depth + 1) if depth < 4 else 0      # a sub-switch defaults to its group switch (EV_THRMIN = EV_THREAT2 ...)
    for n in FLAG_BITS:
        CFG[n] = value(n)
    return dict(CFG)


def asm_define(name, default, asm=ASM):
    """value of "%define NAME n" in src/eval.asm (the first one)"""
    m = re.search(r"^%define\s+" + name + r"\s+(-?\d+)", open(asm).read(), re.M)
    return int(m.group(1)) if m else default


EV_LAZYT = asm_define("EV_LAZYT", 1000)
EV_LAZYT2 = asm_define("EV_LAZYT2", 700)
EV_SPACENPM = asm_define("EV_SPACENPM", 40)
EV_GRAINS = asm_define("EV_GRAINS", 8)
IN = {n: asm_define(n, d) for n, d in (("IN_PASSED", 4), ("IN_PAWNS", 6), ("IN_OUTFL", 4), ("IN_BOTH", 10), ("IN_INFIL", 12), ("IN_NONPM", 25),
                                       ("IN_UNWIN", 21), ("IN_BIAS", 53), ("IN_MGADD", 24))}


def sgn(x):
    return (x > 0) - (x < 0)


def initiative(mg, eg, aux):
    """EV_INIT (src/eval.asm INITIATIVE): mg / eg (White's view) -> the corrected pair"""
    wk, bk = aux["wk"], aux["bk"]
    outfl = abs((wk & 7) - (bk & 7)) - abs((wk >> 3) - (bk >> 3))
    both = bool(aux["pawns"] & QSIDE and aux["pawns"] & KSIDE)
    c = (IN["IN_PASSED"] * aux["npass"] + IN["IN_PAWNS"] * popcount(aux["pawns"]) + IN["IN_OUTFL"] * outfl
         + IN["IN_INFIL"] * ((wk >> 3) > 3 or (bk >> 3) < 4) + IN["IN_BOTH"] * both)
    if not both and outfl < 0 and not aux["npass"]:
        c -= IN["IN_UNWIN"]
    if not aux["pieces"]:
        c += IN["IN_NONPM"]
    c -= IN["IN_BIAS"]
    u = sgn(mg) * min(max(c + IN["IN_MGADD"], -abs(mg)), 0)
    v = sgn(eg) * max(c, -abs(eg))
    return mg + u, eg + v
CENTER = 0x0000001818000000
SPACE_MASK = 0x000000003C3C3C00


def adjf(x):
    return (((x & NOT_H) << 1) | ((x & NOT_A) >> 1)) & M64


KD = {n: asm_define(n, d) for n, d in (("KD_WEAK", 2), ("KD_QCHK", 6), ("KD_RCHK", 5), ("KD_BCHK", 3), ("KD_NCHK", 4), ("KD_UNSAFE", 1),
                                     ("KD_PIN", 2), ("KD_KNDEF", 3))}


def passb_on():
    return any(CFG.get(n) for n in ("EV_THRMIN", "EV_THRHANG", "EV_THRPAWN", "EV_THRRESTR", "EV_THRQUEEN", "EV_KDANGER", "EV_PASSED2",
                                    "EV_SPACE", "EV_KLINES"))


def klines_terms(P, E, RQ, tk):
    """EV_KLINES (src/eval.asm, second pass): (open files, storming pawns, heavy pieces) against their king on tk; our frame (we move north)"""
    tr = tk >> 3
    cf = min(max(tk & 7, 1), 6)
    below = (1 << (tr * 8)) - 1
    pb, eb = P & below, E & below
    op = st = hv = 0
    for f in (cf - 1, cf, cf + 1):
        fm = FILE_A << f
        if not eb & fm:
            hv += popcount(RQ & fm)
            if not pb & fm:
                op += 1
        b = pb & fm
        if b:
            q = b.bit_length() - 1
            if tr - (q >> 3) <= 3 and not (E >> (q + 8)) & 1:
                st += 1
    return op, st, hv


def cfg_mask():
    return sum(1 << i for i, n in enumerate(FLAG_BITS) if CFG.get(n))


def set_cfg(mask):
    for i, n in enumerate(FLAG_BITS):
        CFG[n] = (int(mask) >> i) & 1


GROUP_SWITCHES = {"EV_THREAT2": ["EV_THRMIN", "EV_THRHANG", "EV_THRPAWN", "EV_THRRESTR", "EV_THRQUEEN"],      # as in src/eval.asm
                  "EV_PIECES2": ["EV_PBISH", "EV_PMINOR", "EV_PROOK", "EV_PQUEEN"]}


def set_switches(spec):
    """spec = "NAME=1,NAME=0,..." (or empty); EV_THREAT2 / EV_PIECES2 set their sub-switches"""
    for item in [x.strip() for x in (spec or "").split(",") if x.strip()]:
        n, _, v = item.partition("=")
        for m in GROUP_SWITCHES.get(n, [n]):
            if m not in FLAG_BITS:
                raise SystemExit(f"unknown evaluation switch '{m}' (known: {', '.join(FLAG_BITS + list(GROUP_SWITCHES))})")
            CFG[m] = int(v or 1)


def cfg_text():
    return ", ".join(f"{n}={CFG.get(n, 0)}" for n in FLAG_BITS)


load_cfg()


def term_range(name):
    o = TERM_OFS[name]
    return EVP0 + o, EVP0 + o + dict(TERMS)[name]


def group_mask(free):
    """free = comma list of: all, pst (all six piece tables), p n b r q k, bp, tempo, full (every term of the full evaluation),
    mob, pawns, passed, rooks, king, pieces, or a single term name (see TERMS)  ->  boolean mask over the flat parameter vector"""
    m = np.zeros(NPAR, bool)
    def tapered(lo, hi):
        m[lo:hi] = True; m[NF + lo:NF + hi] = True
    for g in [x.strip().lower() for x in free.split(",") if x.strip()]:
        if g == "all":
            m[:] = True
        elif g == "pst":
            tapered(0, NPST)
        elif g in GROUPS:
            t = GROUPS[g]; tapered((t - 1) * 64, t * 64)
        elif g == "bp":
            tapered(BP, BP + 1)
        elif g == "tempo":
            m[TEMPO_IDX] = True
        elif g in TERM_GROUPS:
            for n in TERM_GROUPS[g]: tapered(*term_range(n))
        elif g in TERM_OFS:
            tapered(*term_range(g))
        else:
            raise SystemExit(f"unknown parameter group '{g}' (use: all, pst, p n b r q k, bp, tempo, {', '.join(TERM_GROUPS)}, or a term: {', '.join(TERM_OFS)})")
    for i in never_free():
        m[i] = m[NF + i] = False
    return m


def never_free():
    """flat (mg) indices the tuner never moves: imb_ours (pair x pair) and (bishop x pair) repeat the bishop-pair term (both stay 0 in
    src/eval.asm); the diagonal of imb_theirs cancels between the two sides (no information in any data)"""
    o, t = term_range("imb_ours")[0], term_range("imb_theirs")[0]
    return [o + 0, o + 6] + [t + d for d in (0, 2, 5, 9, 14, 20)]


# ------------------------------------------------------------------------------------------------ the asm tables
def _ints(text):
    return [int(x) for x in re.findall(r"-?\d+", re.sub(r";.*", "", text))]


def _block(src, label, nxt, op="dw"):
    i = src.index(label + ":"); j = src.index(nxt + ":", i)
    vals = []
    for ln in src[i + len(label) + 1:j].splitlines():
        s = re.sub(r";.*", "", ln).strip()
        if s.startswith(op + " ") or s.startswith(op + "\t"):
            vals += _ints(s[len(op):])
    return vals


def load_tables(asm=ASM):
    """parse pst_mg / pst_eg / matmg / mateg / evp and the BP_MG / BP_EG / TEMPO defaults from src/eval.asm"""
    src = open(asm).read()
    pst_mg = np.array(_block(src, "pst_mg", "pst_eg"), np.int64)
    pst_eg = np.array(_block(src, "pst_eg", "evp"), np.int64)
    assert pst_mg.size == NPST and pst_eg.size == NPST, (pst_mg.size, pst_eg.size)
    ev = _block(src, "evp", "evp_end", "SC")
    assert len(ev) == 2 * EV_N, (len(ev), 2 * EV_N)
    mat = {}
    for name in ("matmg", "mateg"):
        m = re.search(r"^" + name + r":\s*dw\s*([^;\n]*)", src, re.M)
        mat[name] = np.array(_ints(m.group(1)), np.int64)
        assert mat[name].size == 7
    def define(n, default):
        m = re.search(r"^%define\s+" + n + r"\s+(-?\d+)", src, re.M)
        return int(m.group(1)) if m else default
    return {"pst_mg": pst_mg, "pst_eg": pst_eg, "matmg": mat["matmg"], "mateg": mat["mateg"],
            "evp_mg": np.array(ev[0::2], np.int64), "evp_eg": np.array(ev[1::2], np.int64),
            "bp_mg": define("BP_MG", 28), "bp_eg": define("BP_EG", 45), "tempo": define("TEMPO", 10)}


def merged_from_tables(t):
    """(NF, 2) array: [(piece-1)*64 + k] = pst + material, [384] = bishop pair, [385 ..] = terms of the full evaluation;  tempo separate"""
    w = np.zeros((NF, 2), np.float64)
    for p in range(1, 7):
        lo = (p - 1) * 64
        w[lo:lo + 64, 0] = t["pst_mg"][lo:lo + 64] + t["matmg"][p]
        w[lo:lo + 64, 1] = t["pst_eg"][lo:lo + 64] + t["mateg"][p]
    w[BP] = (t["bp_mg"], t["bp_eg"])
    w[EVP0:, 0] = t["evp_mg"]; w[EVP0:, 1] = t["evp_eg"]
    return w


def flat(w, tempo):
    v = np.empty(NPAR, np.float64)
    v[:NF] = w[:, 0]; v[NF:2 * NF] = w[:, 1]; v[TEMPO_IDX] = tempo
    return v


def unflat(v):
    return np.stack([v[:NF], v[NF:2 * NF]], 1), v[TEMPO_IDX]


def initial_flat(asm=ASM):
    t = load_tables(asm)
    return flat(merged_from_tables(t), t["tempo"]), t


def tables_from_merged(w, tempo, t):
    """inverse of merged_from_tables with ROUNDED integers: new pst = round(merged) - material (material stays as in the asm)"""
    out = {"pst_mg": np.zeros(NPST, np.int64), "pst_eg": np.zeros(NPST, np.int64)}
    for p in range(1, 7):
        lo = (p - 1) * 64
        out["pst_mg"][lo:lo + 64] = np.rint(w[lo:lo + 64, 0]).astype(np.int64) - t["matmg"][p]
        out["pst_eg"][lo:lo + 64] = np.rint(w[lo:lo + 64, 1]).astype(np.int64) - t["mateg"][p]
    out["bp_mg"] = int(np.rint(w[BP, 0])); out["bp_eg"] = int(np.rint(w[BP, 1])); out["tempo"] = int(np.rint(tempo))
    out["evp_mg"] = np.rint(w[EVP0:, 0]).astype(np.int64); out["evp_eg"] = np.rint(w[EVP0:, 1]).astype(np.int64)
    return out


def param_name(j):
    """name of tapered parameter j (0 .. NF-1)"""
    if j < NPST:
        k = j % 64
        return f"{PIECE_NAMES[j // 64]} {'abcdefgh'[k % 8]}{8 - k // 8}"
    if j == BP:
        return "bishop pair"
    i = j - EVP0
    for n, c in TERMS:
        o = TERM_OFS[n]
        if o <= i < o + c:
            return n if c == 1 else f"{n}[{i - o}]"
    raise IndexError(j)


# ------------------------------------------------------------------------------------------------ bitboards (a1 = 0 ... h8 = 63)
M64 = (1 << 64) - 1
FILE_A = 0x0101010101010101
NOT_A = 0xFEFEFEFEFEFEFEFE
NOT_H = 0x7F7F7F7F7F7F7F7F
RANKS_2_7 = 0x00FFFFFFFFFFFF00
RANKS_4_6 = 0x0000FFFFFF000000


def _steps(sq, deltas):
    f, r = sq & 7, sq >> 3
    m = 0
    for df, dr in deltas:
        if 0 <= f + df < 8 and 0 <= r + dr < 8:
            m |= 1 << ((r + dr) * 8 + f + df)
    return m


KNIGHT = [_steps(s, [(1, 2), (2, 1), (2, -1), (1, -2), (-1, -2), (-2, -1), (-2, 1), (-1, 2)]) for s in range(64)]
KING = [_steps(s, [(1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1)]) for s in range(64)]


def _slide(sq, occ, dirs):
    f0, r0 = sq & 7, sq >> 3
    m = 0
    for df, dr in dirs:
        f, r = f0 + df, r0 + dr
        while 0 <= f < 8 and 0 <= r < 8:
            b = 1 << (r * 8 + f)
            m |= b
            if occ & b:
                break
            f += df; r += dr
    return m


def rook_att(sq, occ):
    return _slide(sq, occ, ((1, 0), (-1, 0), (0, 1), (0, -1)))


def bishop_att(sq, occ):
    return _slide(sq, occ, ((1, 1), (1, -1), (-1, 1), (-1, -1)))


def popcount(x):
    return bin(x).count("1")


def bits(x):
    while x:
        b = x & -x
        yield b.bit_length() - 1
        x ^= b


def nfill(x):
    x |= (x << 8) & M64; x |= (x << 16) & M64; x |= (x << 32) & M64
    return x


def sfill(x):
    x |= x >> 8; x |= x >> 16; x |= x >> 32
    return x


def mirror(x):
    """vertical mirror of a bitboard (rank 1 <-> rank 8): the engine's bswap"""
    return int.from_bytes(x.to_bytes(8, "little"), "big")


def dist(a, b):
    return max(abs((a & 7) - (b & 7)), abs((a >> 3) - (b >> 3)))


CASTLE = {}                                             # castling rights of the position being evaluated: {1: White's bits, -1: Black's}


def shelter_terms(P, E, ksq):
    """EV_SHELTER: {feature index: count} of the shelter / storm terms for our king on ksq (src/eval.asm SHELTERV)"""
    T = TERM_OFS
    out = {}
    front = (M64 << (ksq & 0x38)) & M64
    ours_p, theirs_p = P & front, E & front
    cf = min(max(ksq & 7, 1), 6)
    for fl in range(cf - 1, cf + 2):
        fm = FILE_A << fl
        d = min(fl, 7 - fl)
        b = ours_p & fm
        o = ((b & -b).bit_length() - 1) >> 3 if b else 0
        i = T["shelter"] + 7 * d + o
        out[i] = out.get(i, 0) + 1
        b = theirs_p & fm
        th = ((b & -b).bit_length() - 1) >> 3 if b else 0
        i = T["blocked_storm"] + th if o and o == th - 1 else T["storm"] + 7 * d + th
        out[i] = out.get(i, 0) + 1
    return out


def shelter_mg(d):
    """middlegame value of a shelter feature set with the tables of src/eval.asm (the engine compares the packed values by their mg part)"""
    t = asm_tables()
    v = sum(n * int(t["evp_mg"][i]) for i, n in d.items())
    return ((v + 32768) & 0xFFFF) - 32768


def side_terms(us, them, uk, tk, us_all, occ, acc, sign):
    """add sign * (feature counts of "us", who moves north) to acc (key = index into evp).
    us / them = [P, N, B, R, Q] bitboards, uk / tk = king squares, us_all = our pieces (kings included), occ = all pieces"""
    P, N, B, R, Q = us
    E, TN, TB, TR, TQ = them
    def add(i, n=1):
        if n: acc[i] = acc.get(i, 0) + sign * n
    T = TERM_OFS
    pa = (((P & NOT_A) << 7) | ((P & NOT_H) << 9)) & M64
    ea = ((E & NOT_A) >> 9) | ((E & NOT_H) >> 7)
    area = ~(us_all | ea) & M64
    pinned = 0
    if CFG.get("EV_PINS") or CFG.get("EV_KDANGER"):
        them_all = occ & ~us_all
        for att, sl in ((rook_att, TR | TQ), (bishop_att, TB | TQ)):
            ka = att(uk, occ)
            blk = ka & us_all
            pinners = att(uk, occ ^ blk) & ~ka & sl & them_all
            for s in bits(pinners):
                pinned |= att(s, occ) & ka & us_all
    if CFG.get("EV_PINS"):
        low = P & ((occ >> 8) | 0xFFFF00)
        area = ~((1 << uk) | Q | pinned | low | ea) & M64
    am = {"P": pa, "N": 0, "B": 0, "R": 0, "Q": 0, "K": KING[uk]}
    am["A2"] = ((((P & NOT_A) << 7) & ((P & NOT_H) << 9)) | (am["K"] & pa)) & M64
    am["ALL"] = pa | am["K"]
    am["area"] = area
    sfx = "_pins" if CFG.get("EV_PINS") else ""        # EV_PINS: own mobility tables
    am["PIN"] = pinned
    kdon = CFG.get("EV_KDANGER")
    if kdon:
        cf, cr = min(max(tk & 7, 1), 6), min(max(tk >> 3, 1), 6)
        c = cr * 8 + cf
        ring = KING[c] | (1 << c)
        ring &= ~(((E & NOT_A) >> 9) & ((E & NOT_H) >> 7))
        am["RING"], am["TKZ"], am["KDW"], am["KDA"] = ring, KING[tk], 0, 0
    def amap(k, a):
        am["A2"] |= am["ALL"] & a; am["ALL"] |= a; am[k] |= a
        if kdon:
            if a & am["RING"]: am["KDW"] += {"N": 2, "B": 2, "R": 3, "Q": 5}[k]
            am["KDA"] += popcount(a & am["TKZ"])
    zone = KING[tk] | (1 << tk)
    add(T["doubled"], popcount(P & nfill((P << 8) & M64)))
    files = nfill(P) | sfill(P)
    adj = ((files & NOT_H) << 1 | (files & NOT_A) >> 1) & M64
    add(T["isolated"], popcount(P & ~adj))
    add(T["supported"], popcount(P & pa))
    add(T["phalanx"], popcount(P & ((((P & NOT_H) << 1) | ((P & NOT_A) >> 1)) & M64)))
    if CFG.get("EV_PAWNS2"):
        backward = P & ~adjf(nfill(P)) & (ea >> 8)
        add(T["backward"], popcount(backward))
        iso = P & ~adjf(nfill(P) | sfill(P))
        add(T["weak_unopposed"], popcount((iso | backward) & ~sfill(E >> 8)))
        add(T["weak_lever"], popcount(P & ((E & NOT_A) >> 9) & ((E & NOT_H) >> 7) & ~pa))
        for sq in bits(P & (adjf(P) | pa)):
            add(T["connected"] + (sq >> 3) - 1)
        bl = P & (E >> 8)
        add(T["blocked56"], popcount(bl & 0x000000FF00000000))
        add(T["blocked56"] + 1, popcount(bl & 0x0000FF0000000000))
    s = sfill(E >> 8)
    blk = s | ((s & NOT_A) >> 1) | (((s & NOT_H) << 1) & M64)
    npass = 0
    ppb = P & ~blk & RANKS_2_7
    am["PPB"] = ppb
    for sq in bits(ppb):
        npass += 1
        add(T["passed"] + (sq >> 3) - 1)
        stop = sq + 8
        add(T["pp_own_king"] + dist(stop, uk))
        add(T["pp_enemy_king"] + dist(stop, tk))
    add(T["threat_pawn"], popcount(pa & (TN | TB | TR | TQ)))
    if CFG.get("EV_SHELTER"):
        best = shelter_terms(P, E, uk)
        cr = CASTLE.get(sign, 0)
        for bit, sq in ((1, 6), (2, 2)):                    # g1 / c1 in our frame when castling is in reach (src/eval.asm SHELTERALT)
            if cr & bit:
                alt = shelter_terms(P, E, sq)
                if shelter_mg(alt) > shelter_mg(best):
                    best = alt
        for i, n in best.items():
            add(i, n)
        if P:
            add(T["king_pawn_dist"], min(7, min(dist(uk, s) for s in bits(P))))
    else:
        k = 1 << uk
        l = k | ((k & NOT_H) << 1) | ((k & NOT_A) >> 1)
        add(T["shield"], popcount(P & (((l << 8) | (l << 16)) & M64)))
    outp = ~sfill(ea) & pa & RANKS_4_6 & M64
    add(T["outpost"], popcount(outp & N))
    if CFG.get("EV_PMINOR"):
        add(T["minor_behind_pawn"], popcount(((N | B) << 8) & (P | E) & M64))
    units = 0
    for sq in bits(N):
        a = KNIGHT[sq]
        amap("N", a)
        if CFG.get("EV_PMINOR"):
            add(T["king_protector_n"], dist(sq, uk))
            if not (outp >> sq) & 1 and outp & a & ~us_all:
                add(T["reach_outpost"])
        if a & zone: units += 2
        add(T["mob_n" + sfx] + popcount(a & area))
    for sq in bits(B):
        a = bishop_att(sq, occ)
        amap("B", a)
        if CFG.get("EV_PBISH"):
            add(T["bishop_pawns"], popcount(P & (DARK if (DARK >> sq) & 1 else ~DARK & M64)))
            if (outp >> sq) & 1:
                add(T["bishop_outpost"])
            if popcount(bishop_att(sq, P | E) & CENTER) >= 2:
                add(T["long_diag"])
        if CFG.get("EV_PMINOR"):
            add(T["king_protector_b"], dist(sq, uk))
        if a & zone: units += 2
        add(T["mob_b" + sfx] + popcount(a & area))
    for sq in bits(R):
        f = FILE_A << (sq & 7)
        prook = CFG.get("EV_PROOK")
        if prook:
            if (ppb & f) >> sq >> 1:
                add(T["rook_behind_passer"])
            if sq >> 3 == 6 and (tk >> 3 == 7 or E & 0x00FF000000000000):
                add(T["rook_7th"])
        if not f & P:
            add(T["rook_open"] if not f & E else T["rook_semi"])
        elif prook and P & (occ >> 8) & f:
            add(T["rook_closed"])
        a = rook_att(sq, occ)
        amap("R", a)
        if prook:
            add(T["rooks_connected"], popcount(a & R))
        if a & zone: units += 3
        mob = popcount(a & area)
        add(T["mob_r" + sfx] + mob)
        if prook and f & P and mob <= 3:
            kf = uk & 7
            if (kf < 4) == ((sq & 7) < kf):
                add(T["trapped_rook"])
    for sq in bits(Q):
        if CFG.get("EV_PQUEEN"):
            weakq = False
            for att, sl in ((rook_att, TR), (bishop_att, TB)):
                qa = att(sq, occ)
                if att(sq, occ ^ (qa & occ)) & ~qa & sl:
                    weakq = True
            if weakq:
                add(T["weak_queen"])
        a = rook_att(sq, occ) | bishop_att(sq, occ)
        amap("Q", a)
        if a & zone: units += 5
        add(T["mob_q" + sfx] + popcount(a & area))
    if not Q:
        units = 0
    if not kdon:
        add(T["king_attack"] + min(units, 31))
    return npass, am


def npm_total(us, them):
    return sum(3 * popcount(x[1]) + 3 * popcount(x[2]) + 5 * popcount(x[3]) + 9 * popcount(x[4]) for x in (us, them))


def imbalance(cw, cb, acc):
    """EV_IMBAL: counts (pair, P, N, B, R, Q) of White / Black -> acc (White - Black)"""
    for ours, theirs, sign in ((cw, cb, 1), (cb, cw, -1)):
        idx = 0
        for i in range(6):
            for j in range(i + 1):
                if ours[i] * ours[j]:
                    acc[TERM_OFS["imb_ours"] + idx] = acc.get(TERM_OFS["imb_ours"] + idx, 0) + sign * ours[i] * ours[j]
                if ours[i] * theirs[j]:
                    acc[TERM_OFS["imb_theirs"] + idx] = acc.get(TERM_OFS["imb_theirs"] + idx, 0) + sign * ours[i] * theirs[j]
                idx += 1


def patt(b):
    return (((b & NOT_A) << 7) | ((b & NOT_H) << 9)) & M64


def side_terms_b(us, them, uk, tk, us_all, occ, am, tm, acc, sign):
    """second pass (src/eval.asm ev_sideb): terms that need both attack maps.  am = our maps, tm = their maps in our frame"""
    P, N, B, R, Q = us
    E, TN, TB, TR, TQ = them
    def add(i, n=1):
        if n: acc[i] = acc.get(i, 0) + sign * n
    T = TERM_OFS
    sp = tm["P"] | (tm["A2"] & ~am["A2"] & M64)
    theirs = E | TN | TB | TR | TQ
    nonpawn = TN | TB | TR | TQ
    weak = theirs & ~sp & am["ALL"]
    if CFG.get("EV_THRMIN"):
        dm = ((nonpawn & sp) | weak) & (am["N"] | am["B"])
        for i, bb in enumerate((E, TN, TB, TR, TQ)):
            add(T["thr_minor"] + i, popcount(bb & dm))
            add(T["thr_rook"] + i, popcount(bb & weak & am["R"]))
        add(T["thr_king"], popcount(weak & am["K"]))
    if CFG.get("EV_THRHANG"):
        add(T["hanging"], popcount(weak & (~tm["ALL"] | (nonpawn & am["A2"])) & M64))
        add(T["weak_qprot"], popcount(weak & tm["Q"]))
    if CFG.get("EV_THRRESTR"):
        add(T["restricted"], popcount(tm["ALL"] & ~sp & am["ALL"]))
    if CFG.get("EV_THRPAWN"):
        safe = (~tm["ALL"] | am["ALL"]) & M64
        add(T["thr_safe_pawn"], popcount(patt(P & safe) & nonpawn))
        empty = ~occ & M64
        b = (P << 8) & empty
        b |= ((b & 0xFF0000) << 8) & empty
        b &= ~tm["P"] & safe
        add(T["thr_pawn_push"], popcount(patt(b) & nonpawn))
    if CFG.get("EV_KDANGER"):
        d = 0
        if Q:
            d = am["KDW"] + am["KDA"]
            weak = am["ALL"] & ~tm["A2"] & (tm["K"] | tm["Q"] | ~tm["ALL"]) & M64
            d += KD["KD_WEAK"] * popcount(weak & am["RING"])
            safe = ((weak & am["A2"]) | ~tm["ALL"]) & ~us_all & M64
            b1, b2 = rook_att(tk, occ), bishop_att(tk, occ)
            rchk = b1 & am["R"] & safe
            qchk = (b1 | b2) & am["Q"] & safe & ~(tm["Q"] | rchk)
            bchk = b2 & am["B"] & safe & ~qchk
            nall = KNIGHT[tk] & am["N"]
            d += KD["KD_RCHK"] * bool(rchk) + KD["KD_QCHK"] * bool(qchk) + KD["KD_BCHK"] * bool(bchk) + KD["KD_NCHK"] * bool(nall & safe)
            d += KD["KD_UNSAFE"] * bool(((b1 & am["R"]) | (b2 & am["B"]) | nall) & ~safe)
            d += KD["KD_PIN"] * popcount(tm["PIN"])
            if tm["N"] & KING[tk]:
                d -= KD["KD_KNDEF"]
            d = min(63, max(0, d))
        add(T["kdanger"] + d)
    if CFG.get("EV_KLINES"):
        op, st, hv = klines_terms(P, E, R | Q, tk)
        add(T["kl_open"], op); add(T["kl_storm"], st); add(T["kl_heavy"], hv)
    if CFG.get("EV_SPACE") and npm_total(us, them) >= EV_SPACENPM:
        safe = SPACE_MASK & ~(tm["P"] | P) & M64
        behind = P | (P >> 8)
        behind |= behind >> 16
        bonus = popcount(safe) + popcount(behind & safe & ~tm["ALL"])
        w = popcount(us_all) - 3 + min(9, 2 * popcount((P << 8) & E))
        add(T["space"], (bonus * w * w) >> 10)
    if CFG.get("EV_PASSED2"):
        for sq in bits(am["PPB"]):
            f = sq & 7
            add(T["passed_file"], min(f, 7 - f))
            r = sq >> 3
            if r < 3:
                continue
            w = 5 * r - 13
            blk = sq + 8
            add(T["pp_king_them_w"], w * min(5, dist(blk, tk)))
            add(T["pp_king_us_w"], w * min(5, dist(blk, uk)))
            if r != 6:
                add(T["pp_king_us2_w"], w * min(5, dist(blk + 8, uk)))
            if (occ >> blk) & 1:
                continue
            fl = FILE_A << f
            above = (M64 << (sq + 1)) & M64
            s2q = fl & above
            behind = fl & ~above & ~(1 << sq)
            span = s2q | ((s2q & NOT_H) << 1) & M64 | ((s2q & NOT_A) >> 1)
            unsafe = span
            if not ((TR | TQ) & behind):
                unsafe &= tm["ALL"] | E | TN | TB | TR | TQ | (1 << tk)
            if not unsafe:
                c = 0
            elif not unsafe & ~am["P"]:
                c = 1
            elif not unsafe & s2q:
                c = 2
            elif not (unsafe >> blk) & 1:
                c = 3
            else:
                c = 4
            if c < 4:
                add(T["passed_free"] + c, w)
            if ((R | Q) & behind) or (am["ALL"] >> blk) & 1:
                add(T["passed_free_def"], w)
    if CFG.get("EV_THRQUEEN") and popcount(TQ) == 1:
        s = TQ.bit_length() - 1
        mult = 2 if not Q else 1
        safe = am["area"] & ~sp
        add(T["knight_on_queen"], mult * popcount(KNIGHT[s] & am["N"] & safe))
        b = (bishop_att(s, occ) & am["B"]) | (rook_att(s, occ) & am["R"])
        add(T["slider_on_queen"], mult * popcount(b & safe & am["A2"]))


# ------------------------------------------------------------------------------------------------ special evaluations and scale factor
DARK = 0xAA55AA55AA55AA55
QSIDE = 0x0F0F0F0F0F0F0F0F
KSIDE = 0xF0F0F0F0F0F0F0F0
FILE_H = FILE_A << 7


def rook_file_promo(pawns, white):
    """promotion square when every pawn stands on the a-file or every pawn on the h-file, else None"""
    if pawns and not pawns & ~FILE_A:
        f = 0
    elif pawns and not pawns & ~FILE_H:
        f = 7
    else:
        return None
    return f + (56 if white else 0)


EG2_NAMES = ("EG2_QR", "EG2_QEDGE", "EG2_QCLOSE", "EG2_RB", "EG2_RN", "EG2_REDGE", "EG2_RNFAR")
EG2_DEFAULT = dict(zip(EG2_NAMES, (600, 20, 10, 20, 30, 8, 4)))


def eg2_consts():
    return {n: asm_define(n, d) for n, d in EG2_DEFAULT.items()}


def spec2_class(bbw, bbb):
    """EV_ENDG2 (src/eval.asm ev_spec2): (class, strong side is White) for KQ-KR ('qr'), KR-KB ('rb'), KR-KN ('rn') without pawns, else None"""
    if bbw[1] or bbb[1]:
        return None
    w = [popcount(bbw[i]) for i in range(2, 6)]                     # N B R Q
    b = [popcount(bbb[i]) for i in range(2, 6)]
    if sum(w) != 1 or sum(b) != 1:
        return None
    if w[2]:
        rook_white = True
    elif b[2]:
        rook_white = False
    else:
        return None
    other = b if rook_white else w
    if other[3]: return "qr", not rook_white
    if other[1]: return "rb", rook_white
    if other[0]: return "rn", rook_white
    return None


def centre_dist(k):
    fx, ry = k & 7, k >> 3
    return max(3 - fx, fx - 4) + max(3 - ry, ry - 4)


def spec2_value(cls, sk, wk, knight, c):
    """EV_ENDG2 value for the strong side (before the side-to-move sign and the tempo)"""
    cd = centre_dist(wk)
    if cls == "qr":
        return c["EG2_QR"] + c["EG2_QEDGE"] * cd + c["EG2_QCLOSE"] * (7 - dist(sk, wk))
    if cls == "rb":
        return c["EG2_RB"] + c["EG2_REDGE"] * cd
    return c["EG2_RN"] + c["EG2_REDGE"] * cd + c["EG2_RNFAR"] * dist(wk, knight)


def is_special(bbw, bbb, wk, bk):
    """EV_ENDG: a side with only its king and a position the special evaluation handles (src/eval.asm ev_bare); EV_ENDG2: ev_spec2"""
    if CFG.get("EV_ENDG2") and spec2_class(bbw, bbb):
        return True
    wall = bbw[1] | bbw[2] | bbw[3] | bbw[4] | bbw[5] | bbw[6]
    ball = bbb[1] | bbb[2] | bbb[3] | bbb[4] | bbb[5] | bbb[6]
    if wall & (wall - 1) == 0:
        s, white, weak_k = bbb, False, wk
    elif ball & (ball - 1) == 0:
        s, white, weak_k = bbw, True, bk
    else:
        return False
    P, N, B, R, Q = (popcount(s[i]) for i in range(1, 6))
    if R or Q or not P:
        return True
    if N:
        return False
    if B:
        return wrong_bishop_draw(s, white, weak_k)
    if P == 1:
        return True
    q = rook_file_promo(s[1], white)
    return q is not None and dist(q, weak_k) <= 1


def wrong_bishop_draw(s, white, weak_k):
    """EV_ENDG: bishops + pawns all on one rook file, no bishop on the colour of the promotion square, bare king on / next to it"""
    q = rook_file_promo(s[1], white)
    if q is None:
        return False
    colour = DARK if (DARK >> q) & 1 else (~DARK & M64)
    return not (s[3] & colour) and dist(q, weak_k) <= 1


EV_KNOWNWIN = 10000
_KPK = None


def kpk_win(stm_pawn, wk, bk, p):
    """bitbase kpk_bb of src/eval.asm: pawn side White, pawn on files a..d, stm_pawn = the pawn side is to move"""
    global _KPK
    if _KPK is None:
        m = re.search(r"^kpk_bb:[^\n]*\n(.*?)^kpk_bb_end:", open(ASM).read(), re.M | re.S)
        _KPK = [int(x, 16) for x in re.findall(r"0x([0-9A-Fa-f]{16})", m.group(1))]
    i = (((0 if stm_pawn else 1) * 64 + wk) * 64 + bk) * 24 + ((p >> 3) - 1) * 4 + (p & 7)
    return (_KPK[i >> 6] >> (i & 63)) & 1


def special_eval(fen, t):
    """EV_ENDG special evaluation of a position with is_special() true (src/eval.asm ev_bare): side-to-move view, tempo included"""
    f = fen.split()
    bbw = [0] * 7; bbb = [0] * 7
    k = 0
    for ch in f[0]:
        if ch == "/": continue
        if "1" <= ch <= "8": k += ord(ch) - 48; continue
        ty, white = PIECE_CODE[ch]
        (bbw if white else bbb)[ty] |= 1 << ((7 - (k >> 3)) * 8 + (k & 7))
        k += 1
    wtm = (f[1] == "w") if len(f) > 1 else True
    wk, bk = bbw[6].bit_length() - 1, bbb[6].bit_length() - 1
    sc = spec2_class(bbw, bbb) if CFG.get("EV_ENDG2") else None
    if sc:
        cls, swhite = sc
        o = bbb if swhite else bbw
        v = spec2_value(cls, wk if swhite else bk, bk if swhite else wk, o[2].bit_length() - 1, eg2_consts())
        return (v if swhite == wtm else -v) + t["tempo"]
    if (bbw[1] | bbw[2] | bbw[3] | bbw[4] | bbw[5]) == 0:
        s, white, sk, weak = bbb, False, bk, wk
    else:
        s, white, sk, weak = bbw, True, wk, bk
    P, N, B, R, Q = (popcount(s[i]) for i in range(1, 6))
    mat = sum(int(t["mateg"][i]) * c for i, c in zip(range(1, 6), (P, N, B, R, Q)))
    if not R and not Q and not P and not (N >= 3 or (N >= 1 and B >= 1) or (N == 0 and B >= 2 and s[3] & DARK and s[3] & ~DARK & M64)):
        v = 0
    elif P == 1 and not (N or B or R or Q):
        p, a, b = bbw[1] | bbb[1], sk, weak
        p = p.bit_length() - 1
        if not white:
            p, a, b = p ^ 56, a ^ 56, b ^ 56
        if (p & 7) >= 4:
            p, a, b = p ^ 7, a ^ 7, b ^ 7
        v = EV_KNOWNWIN + int(t["mateg"][1]) + 15 * (p >> 3) if kpk_win(white == wtm, a, b, p) else 0
    elif P and not (N or R or Q):
        v = 0                                       # is_special: rook-file pawns (with a wrong bishop) and the bare king at the promotion square
    else:
        v = EV_KNOWNWIN + mat
        if N == 1 and B == 1 and not (R or Q or P):
            corners = (0, 63) if s[3] & DARK else (7, 56)
            v += 30 * (7 - min(dist(weak, c) for c in corners))
        else:
            fx, ry = weak & 7, weak >> 3
            v += 20 * (max(3 - fx, fx - 4) + max(3 - ry, ry - 4))
        v = min(19000, v + 20 * (7 - dist(sk, weak)))
    if v == 0:
        return 0                                    # a draw is exactly 0
    return (v if white == wtm else -v) + t["tempo"]


def side_info(bb, white, ksq, npass):
    P, N, B, R, Q = (popcount(bb[i]) for i in range(1, 6))
    npm = 3 * (N + B) + 5 * R + 9 * Q
    return dict(P=P, N=N, B=B, R=R, Q=Q, npm=npm, mat=npm + P, passed=npass, white=white, k=ksq,
                dark=bool(bb[3] & DARK), light=bool(bb[3] & ~DARK & M64), pawns=bb[1])


SC_NAMES = ("SC_M1", "SC_M2", "SC_OB0", "SC_OB1", "SC_OC0", "SC_OC1", "SC_OC2", "SC_RK", "SC_Q0", "SC_Q1", "SC_G0", "SC_G1", "SC_FL", "SC_G6FL",
            "SC_RKP", "SC_QKP", "SC_RPR", "SC_MPM")
SC_DEFAULT = dict(zip(SC_NAMES, (4, 14, 18, 4, 22, 3, 0, 36, 37, 3, 36, 7, 0, 4, 16, 8, 16, 8)))


def rel_rank(side, sq):
    """rank of sq counted from side's own first rank (0..7)"""
    return (sq >> 3) if side["white"] else 7 - (sq >> 3)


def scale_class2(s, o):
    """EV_SCALE2 class rules: drawn patterns of single-pawn endings, checked after rules 1 and 2.  None if none applies"""
    if s["P"] == 0 and o["P"] == 1 and o["npm"] == 0 and s["N"] == 0 and s["B"] == 0:
        p = o["pawns"].bit_length() - 1
        r = rel_rank(o, p)
        if s["npm"] == 5 and s["R"] == 1:                                                 # rook against a pawn the defending king escorts
            front = p + 8 if o["white"] else p - 8
            ahead = (s["k"] & 7) == (p & 7) and rel_rank(o, s["k"]) > r
            if r >= 3 and dist(o["k"], p) <= 1 and dist(s["k"], front) >= 3 and not ahead:
                return "rp"
        if s["npm"] == 9 and s["Q"] == 1:                                                 # queen against a rook or bishop pawn on its 7th rank
            if r == 6 and (p & 7) in (0, 2, 5, 7) and dist(o["k"], p) <= 1:
                return "qp"
    if s["P"] == 1 and o["P"] == 0:
        p = s["pawns"].bit_length() - 1
        r = rel_rank(s, p)
        blocks = (o["k"] & 7) == (p & 7) and rel_rank(s, o["k"]) > r                      # the defending king on the pawn's file in front of it
        if s["npm"] == 5 and s["R"] == 1 and o["npm"] == 5 and o["R"] == 1:              # rook and pawn against rook
            q = (p & 7) + (56 if s["white"] else 0)
            if (dist(o["k"], q) <= 1 and r <= 4) or blocks:
                return "rpr"
        if s["N"] + s["B"] == 1 and s["npm"] == 3 and o["N"] + o["B"] == 1 and o["npm"] == 3 and blocks:   # minor and pawn against minor
            return "mpm"
    return None


def sc_consts():
    """EV_SCALE2 constants (%define SC_* of src/eval.asm)"""
    return {n: asm_define(n, d) for n, d in SC_DEFAULT.items()}


def scale_kind2(s, o):
    """EV_SCALE2 (src/eval.asm ev_rule2): the rule that applies to the strong side s and its inputs, independent of the constants:
    (kind, x1, x2, one_flank).  kinds: 'z' 0, 'm1', 'm2', 'ob' (x1 = passed pawns of s), 'oc' (x1 = pieces of s, x2 = pawns of s), 'rk', 'q' (x1 =
    minor pieces of the side without the queen), 'g' (x1 = pawns of s)"""
    if s["P"] == 0 and s["npm"] - o["npm"] <= 3:                                      # 1. no pawns, not enough material ahead
        return ("z" if s["npm"] < 5 else ("m1" if o["npm"] <= 3 else "m2")), 0, 0, 0
    if s["B"] == 1 and not (s["N"] or s["R"] or s["Q"]) and s["pawns"]:              # 2. wrong bishop with rook-file pawns
        q = rook_file_promo(s["pawns"], s["white"])
        if q is not None:
            covered = s["dark"] if (DARK >> q) & 1 else s["light"]
            if not covered and dist(q, o["k"]) <= 1:
                return "z", 0, 0, 0
    cr = scale_class2(s, o)
    if cr:
        return cr, 0, 0, 0
    allp = s["pawns"] | o["pawns"]
    one = 0 if (allp & QSIDE and allp & KSIDE) else 1
    if s["B"] == 1 and o["B"] == 1 and s["dark"] != o["dark"]:                         # 3. opposite-coloured bishops
        if s["npm"] == 3 and o["npm"] == 3:
            return "ob", s["passed"], 0, one
        return "oc", s["N"] + s["B"] + s["R"] + s["Q"], s["P"], one
    if (s["npm"] == 5 and o["npm"] == 5 and s["R"] == 1 and o["R"] == 1 and s["P"] - o["P"] <= 1   # 4. rook ending: s's pawns on one flank,
            and bool(s["pawns"] & QSIDE) != bool(s["pawns"] & KSIDE) and KING[o["k"]] & o["pawns"]):   # o's king next to one of its pawns
        return "rk", 0, 0, one
    if s["Q"] + o["Q"] == 1:                                                          # 5. the only queen (either side)
        w = o if s["Q"] else s
        return "q", w["N"] + w["B"], 0, one
    return "g", s["P"], 0, one                                                        # 6. generic: pawns of s


def scale_value2(kind, x1, x2, one, c):
    """EV_SCALE2 scale factor of (kind, inputs) with the constants c: 0..64"""
    if kind == "z": return 0
    if kind == "m1": return c["SC_M1"]
    if kind == "m2": return c["SC_M2"]
    if kind == "rp": return c["SC_RKP"]
    if kind == "qp": return c["SC_QKP"]
    if kind == "rpr": return c["SC_RPR"]
    if kind == "mpm": return c["SC_MPM"]
    if kind == "ob": v = c["SC_OB0"] + c["SC_OB1"] * x1
    elif kind == "oc": v = c["SC_OC0"] + c["SC_OC1"] * x1 + c["SC_OC2"] * x2
    elif kind == "rk": v = c["SC_RK"]
    elif kind == "q": v = c["SC_Q0"] + c["SC_Q1"] * x1
    else:
        v = min(64, c["SC_G0"] + c["SC_G1"] * x1) - one * c["SC_G6FL"]
    v -= one * c["SC_FL"]
    return max(0, min(64, v))


def scale_rule(s, o):
    if CFG.get("EV_SCALE2"):
        return scale_value2(*scale_kind2(s, o), sc_consts())
    if s["P"] == 0 and s["npm"] - o["npm"] <= 3:
        return 0 if s["npm"] < 5 else (4 if o["npm"] <= 3 else 14)
    if s["B"] == 1 and not (s["N"] or s["R"] or s["Q"]) and s["pawns"]:
        q = rook_file_promo(s["pawns"], s["white"])
        if q is not None:
            covered = s["dark"] if (DARK >> q) & 1 else s["light"]
            if not covered and dist(q, o["k"]) <= 1:
                return 0
    if s["B"] == 1 and o["B"] == 1 and s["dark"] != o["dark"]:
        if s["npm"] == 3 and o["npm"] == 3:
            return min(64, 18 + 4 * s["passed"])
        return min(64, 22 + 3 * (s["N"] + s["B"] + s["R"] + s["Q"]))
    allp = s["pawns"] | o["pawns"]
    if s["npm"] == 5 and o["npm"] == 5 and s["R"] == 1 and o["R"] == 1 and abs(s["P"] - o["P"]) <= 1 and (not allp & QSIDE or not allp & KSIDE):
        return 36
    if s["Q"] == 1 and o["Q"] == 0:
        return min(64, 37 + 3 * (o["N"] + o["B"]))
    if not CFG.get("EV_SCALE6", 1):
        return 64
    v = min(64, 36 + 7 * s["P"])
    if not (allp & QSIDE and allp & KSIDE):
        v -= 4
    return v


def scale_info(bbw, bbb, wk, bk, wpass, bpass):
    return side_info(bbw, True, wk, wpass), side_info(bbb, False, bk, bpass)


def scale_for(info, eg):
    """EV_SCALE (src/eval.asm ev_scale, called from EVTOTAL): the first applicable rule for the side the eg score favours (eg <= 0: Black)"""
    w, b = info
    return scale_rule(w, b) if eg > 0 else scale_rule(b, w)


_TABLES = None


def asm_tables():
    global _TABLES
    if _TABLES is None:
        _TABLES = load_tables()
    return _TABLES


# ------------------------------------------------------------------------------------------------ FEN -> features
Features = namedtuple("Features", "cols vals phase wtm npieces sf special passb aux")


def fen_features(fen):
    """Features of one FEN (the first two fields are read): cols/vals = non-zero (white - black) counts over the NF tapered parameters, phase,
    white to move, number of pieces, endgame scale factor sf (0..64), special (a non-linear special evaluation applies: not part of the model)"""
    f = fen.split()
    placement = f[0]
    cs = f[2] if len(f) > 2 else "-"
    CASTLE.clear()
    CASTLE[1] = ("K" in cs) * 1 + ("Q" in cs) * 2
    CASTLE[-1] = ("k" in cs) * 1 + ("q" in cs) * 2
    acc = {}
    k = 0
    phase = 0
    npieces = 0
    wb = bb = 0
    bbw = [0] * 7; bbb = [0] * 7                # piece bitboards by type 1..6
    for ch in placement:
        if ch == "/":
            continue
        if "1" <= ch <= "8":
            k += ord(ch) - 48
            continue
        if k >= 64:
            raise ValueError("bad FEN placement: " + placement)
        t, white = PIECE_CODE[ch]
        npieces += 1
        phase += PHASE_W[t]
        sq = (7 - (k >> 3)) * 8 + (k & 7)       # table index k (rank 8 first) -> square a1 = 0
        if white:
            c = (t - 1) * 64 + k
            acc[c] = acc.get(c, 0) + 1
            if t == 3: wb += 1
            bbw[t] |= 1 << sq
        else:
            c = (t - 1) * 64 + (k ^ 56)
            acc[c] = acc.get(c, 0) - 1
            if t == 3: bb += 1
            bbb[t] |= 1 << sq
        k += 1
    if k != 64:
        raise ValueError("bad FEN placement: " + placement)
    d = (wb >= 2) - (bb >= 2)
    if d:
        acc[BP] = d
    if not bbw[6] or not bbb[6]:
        raise ValueError("FEN without a king: " + placement)
    wk = bbw[6].bit_length() - 1                # several kings: the engine keeps the last one it finds (highest square)
    bk = bbb[6].bit_length() - 1
    wall = bball = 0
    for t in range(1, 7):
        wall |= bbw[t]; bball |= bbb[t]
    occ = wall | bball
    sf, special = 64, False
    passb = {}
    aux = {"wk": wk, "bk": bk, "pawns": bbw[1] | bbb[1], "npass": 0,
           "pieces": any(bbw[i] | bbb[i] for i in range(2, 6))}
    if CFG["EVAL_FULL"]:
        if CFG["EV_ENDG"] and is_special(bbw, bbb, wk, bk):
            special = True
        ev = {}
        wus, wthem = bbw[1:6], bbb[1:6]
        bus, bthem = [mirror(x) for x in bbb[1:6]], [mirror(x) for x in bbw[1:6]]
        wpass, wam = side_terms(wus, wthem, wk, bk, wall, occ, ev, 1)
        bpass, bam = side_terms(bus, bthem, bk ^ 56, wk ^ 56, mirror(bball), mirror(occ), ev, -1)
        aux["npass"] = wpass + bpass
        if CFG["EV_SCALE"]:
            aux["sinfo"] = scale_info(bbw, bbb, wk, bk, wpass, bpass)
        if CFG.get("EV_IMBAL"):
            cw = [int(popcount(bbw[3]) >= 2)] + [popcount(bbw[i]) for i in range(1, 6)]
            cb = [int(popcount(bbb[3]) >= 2)] + [popcount(bbb[i]) for i in range(1, 6)]
            imbalance(cw, cb, ev)
        if passb_on():
            evb = {}
            side_terms_b(wus, wthem, wk, bk, wall, occ, wam, {k: mirror(v) for k, v in bam.items()}, evb, 1)
            side_terms_b(bus, bthem, bk ^ 56, wk ^ 56, mirror(bball), mirror(occ), bam, {k: mirror(v) for k, v in wam.items()}, evb, -1)
            for i, v in evb.items():
                if v:
                    ev[i] = ev.get(i, 0) + v
                    passb[EVP0 + i] = passb.get(EVP0 + i, 0) + v
        for i, v in ev.items():
            if v:
                acc[EVP0 + i] = acc.get(EVP0 + i, 0) + v
    cols = [c for c, v in acc.items() if v]
    F = Features(cols, [acc[c] for c in cols], phase, (f[1] == "w") if len(f) > 1 else True, npieces, sf, special, passb, aux)
    if "sinfo" in aux and not special:                     # the data set keeps the sf of the engine with the tables of src/eval.asm
        mg, eg = eval_sums(F, asm_tables())
        if CFG.get("EV_INIT"):
            mg, eg = initiative(mg, eg, aux)
        F = F._replace(sf=scale_for(aux["sinfo"], eg))
    return F


def eval_sums(F, t):
    """(mg, eg) of the linear part: material + PST + bishop pair + every term, White's view"""
    mg = eg = 0
    for c, v in zip(F.cols, F.vals):
        if c == BP:
            mg += v * t["bp_mg"]; eg += v * t["bp_eg"]
        elif c >= EVP0:
            mg += v * int(t["evp_mg"][c - EVP0]); eg += v * int(t["evp_eg"][c - EVP0])
        else:
            p = c // 64 + 1
            mg += v * (int(t["pst_mg"][c]) + int(t["matmg"][p]))
            eg += v * (int(t["pst_eg"][c]) + int(t["mateg"][p]))
    return mg, eg


def int_eval(fen, t):
    """bit-exact copy of the engine's eval for a FEN: side-to-move view, tempo included (t = load_tables() result, or a tuned copy).
    None for a position of a special evaluation (outside the linear model)"""
    F = fen_features(fen)
    if F.special:
        return None
    phase, wtm, sf = F.phase, F.wtm, F.sf
    ph = min(phase, 24)
    if CFG.get("EV_LAZY"):
        mg = eg = 0                                         # lazy evaluation: material + PST + bishop pair, unscaled
        for c, v in zip(F.cols, F.vals):
            if c == BP:
                mg += v * t["bp_mg"]; eg += v * t["bp_eg"]
            elif c < NPST:
                p = c // 64 + 1
                mg += v * (int(t["pst_mg"][c]) + int(t["matmg"][p]))
                eg += v * (int(t["pst_eg"][c]) + int(t["mateg"][p]))
        num = 64 * mg * ph + 64 * eg * (24 - ph)
        e = -((-num) // 1536) if num < 0 else num // 1536
        if abs(e) > EV_LAZYT:
            return (e if wtm else -e) + t["tempo"]
    mg, eg = eval_sums(F, t)
    def total(m, g):                                        # src/eval.asm EVTOTAL: initiative, scale factor, taper, grain
        if CFG.get("EV_INIT"):
            m, g = initiative(m, g, F.aux)
        s = scale_for(F.aux["sinfo"], g) if "sinfo" in F.aux else sf
        num = 64 * m * ph + s * g * (24 - ph)
        e = -((-num) // 1536) if num < 0 else num // 1536  # truncation towards zero
        if CFG.get("EV_GRAIN"):
            e = int(e / EV_GRAINS) * EV_GRAINS
        return e
    if CFG.get("EV_LAZY") and F.passb:
        mgb = sum(v * int(t["evp_mg"][c - EVP0]) for c, v in F.passb.items())      # lazy evaluation, second stage: without the second pass
        egb = sum(v * int(t["evp_eg"][c - EVP0]) for c, v in F.passb.items())
        e = total(mg - mgb, eg - egb)
        if abs(e) > EV_LAZYT2:
            return (e if wtm else -e) + t["tempo"]
    e = total(mg, eg)
    return (e if wtm else -e) + t["tempo"]
