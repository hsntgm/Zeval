/* gen_magics.c - generates src/p2b_bb_data.inc for Zeval (-DBB build).
 *
 *   cc -O2 -o gen_magics tools/gen_magics.c && ./gen_magics > src/p2b_bb_data.inc
 *
 * The output is byte-for-byte the shipped src/p2b_bb_data.inc (fixed seed):  ./gen_magics | cmp - src/p2b_bb_data.inc
 * Needs GCC or Clang (__builtin_popcountll); takes a few seconds.  stdout is switched to binary mode on Windows (MinGW / clang-cl); otherwise
 * ">" under cmd.exe / PowerShell would write CRLF line ends and the file would differ from the shipped one (nasm does not mind, cmp does).
 *
 * Squares are 0..63, a1 = 0, b1 = 1, ... h8 = 63 (little-endian rank-file).
 * "Fancy" magic bitboards: every square has its own shift (64 - number of relevant occupancy bits) and its own
 * offset into one shared attack table (rook ~100K entries = 800 KiB, bishop ~5K entries = 41 KiB).
 * Magics are found by random search (sparse candidates, fixed seed, so the output is reproducible).
 * Record layout in the .inc file (32 bytes per square): mask, magic, pointer to the first table entry, shift (dword), pad.
 */
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#ifdef _WIN32
#include <io.h>
#include <fcntl.h>
#endif
typedef uint64_t U64;

static U64 rng_s = 0x9E3779B97F4A7C15ULL;
static U64 rnd(void) { rng_s ^= rng_s >> 12; rng_s ^= rng_s << 25; rng_s ^= rng_s >> 27; return rng_s * 0x2545F4914F6CDD1DULL; }
static U64 rnd_sparse(void) { return rnd() & rnd() & rnd(); }

static U64 slide(int sq, U64 occ, const int d[4][2]) {
    U64 a = 0;
    for (int i = 0; i < 4; i++) {
        int f = sq & 7, r = sq >> 3;
        for (;;) {
            f += d[i][0]; r += d[i][1];
            if (f < 0 || f > 7 || r < 0 || r > 7) break;
            a |= 1ULL << (r * 8 + f);
            if (occ >> (r * 8 + f) & 1) break;
        }
    }
    return a;
}
static const int RD[4][2] = {{1,0},{-1,0},{0,1},{0,-1}};
static const int BD[4][2] = {{1,1},{1,-1},{-1,1},{-1,-1}};

static U64 relevant_mask(int sq, const int d[4][2]) {
    U64 m = 0;
    for (int i = 0; i < 4; i++) {
        int f = sq & 7, r = sq >> 3;
        for (;;) {
            f += d[i][0]; r += d[i][1];
            int nf = f + d[i][0], nr = r + d[i][1];
            if (nf < 0 || nf > 7 || nr < 0 || nr > 7) break;   /* the last square of a ray is not relevant */
            m |= 1ULL << (r * 8 + f);
        }
    }
    return m;
}

static U64 occs[4096], refs[4096], used[4096];
static int stamp[4096];

static U64 find_magic(int sq, const int d[4][2], U64 mask, int bits) {
    int n = 0;
    U64 s = 0;
    do { occs[n] = s; refs[n] = slide(sq, s, d); n++; s = (s - mask) & mask; } while (s);
    int size = 1 << bits;
    memset(stamp, 0, sizeof stamp);
    for (int epoch = 1; epoch < 400000000; epoch++) {
        U64 magic = rnd_sparse();
        if (__builtin_popcountll((mask * magic) >> 56) < 6) continue;
        int ok = 1;
        for (int i = 0; i < n && ok; i++) {
            int idx = (int)((occs[i] * magic) >> (64 - bits));
            if (stamp[idx] != epoch) { stamp[idx] = epoch; used[idx] = refs[i]; }
            else if (used[idx] != refs[i]) ok = 0;
        }
        if (ok) return magic;
    }
    fprintf(stderr, "no magic for square %d\n", sq); exit(1);
    (void)size;
}

int main(void) {
#ifdef _WIN32
    _setmode(_fileno(stdout), _O_BINARY);
#endif
    U64 knight[64], king[64], patt_to[2][64];
    for (int sq = 0; sq < 64; sq++) {
        int f = sq & 7, r = sq >> 3;
        static const int kn[8][2] = {{1,2},{2,1},{2,-1},{1,-2},{-1,-2},{-2,-1},{-2,1},{-1,2}};
        static const int kg[8][2] = {{1,0},{1,1},{0,1},{-1,1},{-1,0},{-1,-1},{0,-1},{1,-1}};
        U64 a = 0, b = 0, w = 0, bl = 0;
        for (int i = 0; i < 8; i++) {
            int nf = f + kn[i][0], nr = r + kn[i][1];
            if (nf >= 0 && nf < 8 && nr >= 0 && nr < 8) a |= 1ULL << (nr * 8 + nf);
            nf = f + kg[i][0]; nr = r + kg[i][1];
            if (nf >= 0 && nf < 8 && nr >= 0 && nr < 8) b |= 1ULL << (nr * 8 + nf);
        }
        /* patt_to[c][sq] = squares from which a pawn of colour c attacks sq (c = 0 white, 1 black) */
        for (int df = -1; df <= 1; df += 2) {
            if (f + df >= 0 && f + df < 8) {
                if (r - 1 >= 0) w |= 1ULL << ((r - 1) * 8 + f + df);   /* white pawn stands one rank below */
                if (r + 1 < 8)  bl |= 1ULL << ((r + 1) * 8 + f + df);  /* black pawn stands one rank above */
            }
        }
        knight[sq] = a; king[sq] = b; patt_to[0][sq] = w; patt_to[1][sq] = bl;
    }

    U64 rmask[64], rmagic[64], bmask[64], bmagic[64];
    int rbits[64], bbits[64], roff[64], boff[64], tot = 0;
    for (int sq = 0; sq < 64; sq++) {
        rmask[sq] = relevant_mask(sq, RD); rbits[sq] = __builtin_popcountll(rmask[sq]);
        rmagic[sq] = find_magic(sq, RD, rmask[sq], rbits[sq]);
        roff[sq] = tot; tot += 1 << rbits[sq];
    }
    int rtot = tot;
    for (int sq = 0; sq < 64; sq++) {
        bmask[sq] = relevant_mask(sq, BD); bbits[sq] = __builtin_popcountll(bmask[sq]);
        bmagic[sq] = find_magic(sq, BD, bmask[sq], bbits[sq]);
        boff[sq] = tot; tot += 1 << bbits[sq];
    }

    printf("; ---- generated by tools/gen_magics.c - do not edit by hand ----\n%%if BB\n");
    printf("%%define ATABN %d                 ; attack table entries (qwords): rook %d + bishop %d\n", tot, rtot, tot - rtot);
    printf("%%if UNIT == 0                   ; shared tables: defined once (unit 0), the other units link against them\n");
    printf("section .data\nalign 64\n");
    printf("rtab:                           ; rook records: mask, magic, &table[offset], shift, 0\n");
    for (int sq = 0; sq < 64; sq++)
        printf("    dq 0x%016llX, 0x%016llX, atab+%d*8\n    dd %d, 0\n", (unsigned long long)rmask[sq], (unsigned long long)rmagic[sq], roff[sq], 64 - rbits[sq]);
    printf("btab:                           ; bishop records\n");
    for (int sq = 0; sq < 64; sq++)
        printf("    dq 0x%016llX, 0x%016llX, atab+%d*8\n    dd %d, 0\n", (unsigned long long)bmask[sq], (unsigned long long)bmagic[sq], boff[sq], 64 - bbits[sq]);
    printf("knight_att:\n");
    for (int sq = 0; sq < 64; sq++) printf("    dq 0x%016llX\n", (unsigned long long)knight[sq]);
    printf("king_att:\n");
    for (int sq = 0; sq < 64; sq++) printf("    dq 0x%016llX\n", (unsigned long long)king[sq]);
    printf("patt_to:                        ; [colour 0/1][sq] squares from which a pawn of that colour attacks sq\n");
    for (int c = 0; c < 2; c++)
        for (int sq = 0; sq < 64; sq++) printf("    dq 0x%016llX\n", (unsigned long long)patt_to[c][sq]);
    printf("%%endif                          ; UNIT == 0\n%%endif\n");
    return 0;
}
