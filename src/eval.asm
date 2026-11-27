
; ============================================================================================
; Static evaluation.  Everything the evaluation uses lives in this file: the parameters (material, piece-square tables, the
; terms of the full evaluation, bishop pair, tempo), init_eval and the eval routine itself.
;
; Score of a position, White's view, then negated for Black to move and the tempo added:
;   mg = sum of material + PST (kept incrementally in mgsc by domove)  + bishop pair  + terms of the full evaluation (mg part)
;   eg = the same with the endgame values
;   e  = trunc((64 * mg * ph + sf * eg * (24 - ph)) / 1536),  ph = min(phase, 24), sf = endgame scale 0..64 (64 = unscaled; with sf = 64
;        this is trunc((mg * ph + eg * (24 - ph)) / 24)).  The division is a multiplication by a reciprocal, exact for every int32 numerator
;        (tests/t_div.py checks the constant over the whole numerator range).
;   eval = (White to move ? e : -e) + TEMPO
; Every term is LINEAR in its parameters: tuner/model.py reproduces this routine exactly (tuner/check_eval.py) and tunes all of them.
;
; Terms of the full evaluation (EVAL_FULL = 1, needs BB = 1), each computed for both sides, White minus Black:
;   mobility        knights, bishops, rooks, queens: one value per number of attacked squares that are neither occupied by an own piece nor
;                   attacked by an enemy pawn (table index = that count; sliders see through nothing, queens = rook + bishop lines)
;   pawn structure  doubled (an own pawn behind it on the file), isolated (no own pawn on an adjacent file), supported (defended by an own pawn),
;                   phalanx (an own pawn beside it on the same rank)
;   passed pawns    no enemy pawn in front on the same or an adjacent file (ranks 2..7): one value per rank, plus one value per king distance
;                   (Chebyshev, 0..7) of the own king and of the enemy king to the square in front of the pawn
;   rooks           on a file without pawns (open) / without own pawns (semi-open)
;   king shield     own pawns on the three files around the own king, one and two ranks in front of it
;   king attack     attack units against the enemy king zone (king square + its neighbours): knight / bishop 2, rook 3, queen 5 for every piece
;                   that attacks the zone; counted only while the attacking side has a queen; one value per unit total 0..31 (larger = 31)
;   threats         own pawn attacks an enemy knight, bishop, rook or queen (per attacked piece)
;   knight outpost  knight on ranks 4..6, defended by an own pawn, on a square no enemy pawn can ever attack
; The black side is evaluated with the same code on the vertically mirrored bitboards (colours exchanged), so the evaluation is
; colour-symmetric by construction (tests/t_eval.py checks it).
;
; Build switches:
;   EVAL_FULL  1 (default with BB = 1): full evaluation above.   0: material + PST + bishop pair + tempo only (forced with BB = 0).
;   HWPOPCNT   2 (default): both variants are built and eval picks one at start-up (CPUID: the POPCNT instruction when the CPU has it, otherwise
;              plain x86-64 code).  0: plain x86-64 code only.  1: POPCNT only (needs a CPU with POPCNT).  Same results in every case.
;   EVCACHE    1 (default): the static eval of a node is kept in the transposition table entry (bits 16-31 of the data qword, see p4_search.asm)
;              and taken from there on a hit instead of being computed again.  Same values, same search tree.  0: always computed.
;   Three speed-only switches, default 1 (0 = the code they replace; same values, same search tree; tests/t_evspeed.py).  Together
;   -1.5 % time at the same nodes on the target machine:
;   EV_SFAST   1: EVTOTAL takes the scale factor 64 without calling ev_scale when no rule of EV_SCALE2 can give less: the strong side has
;              pawns on both flanks and at least EV_SFPMIN of them (the fewest for which rule 6 reaches 64; 4 with the constants below),
;              the board does not hold exactly one queen, and there is no pair of opposite-coloured bishops.  Acts only with EV_SCALE2 and
;              constants fixed at assembly (not in a -DTUNE build).  A new scale rule must keep this test exact; -DSFASTVERIFY (Linux)
;              calls ev_scale anyway and ends the program with exit code 17 when it does not give 64.
;   EV_KLBITS  1: the king-line terms (EV_KLOPEN, EV_KLHEAVY, EV_KLSTORM) are counted with bitboard operations instead of a loop over
;              the three files.
;   EV_BRSIGN  1: the strong side of EV_SCALE and the side-to-move sign of the result are chosen without branches.
;   EV_TUNEMODE 0 (default).  1: every approximation of the evaluation that is not part of the linear model is off (lazy evaluation and the
;              other non-linear parts as they are added); the build for check_eval / data generation of a tuning run that uses them.
;   EV_ENDG    1 (default; 0 = without).  Special evaluations when one side has only its king (checked before everything else; not part of the linear model,
;              the tuner skips these positions):
;                strong side has a queen or rook, or mating minor pieces (bishop + knight, bishops of both colours, three knights) -> known win:
;                  EV_KNOWNWIN + endgame material + 20 * centre distance of the bare king + 20 * (7 - king distance); bishop + knight alone: instead of the
;                  centre distance 30 * (7 - distance of the bare king to the nearest corner of the bishop's colour)
;                minor pieces that cannot mate (no pawns) -> 0;  king + pawn -> bitbase kpk_bb: win = EV_KNOWNWIN + pawn eg value + 15 * pawn rank, else 0;
;                only pawns, all on the a-file (or all on the h-file) with the bare king next to or on the promotion square -> 0;
;                bishops (no knight) + pawns all on one rook file, no bishop on the colour of the promotion square, the bare king on / next to
;                it -> 0 (wrong bishop);  any other position with pawns -> the normal evaluation.
;              A win is from the strong side, signed for the side to move, plus TEMPO; a draw is exactly 0 without TEMPO (as the search scores
;              a draw).
;   EV_ENDG2   = EV_ENDG (default; needs EV_ENDG).  1: special evaluation of the pawnless endings with one piece each KQ-KR, KR-KB, KR-KN (ev_spec2):
;                KQ-KR  EG2_QR + EG2_QEDGE * centre distance of the weak king + EG2_QCLOSE * (7 - king distance)
;                KR-KB  EG2_RB + EG2_REDGE * centre distance of the weak king;   KR-KN  EG2_RN + EG2_REDGE * centre distance + EG2_RNFAR * distance
;                weak king - knight.  For the strong side, signed for the side to move, plus TEMPO.  Constants fitted to Syzygy WDL verdicts
;                (tuner/fit_special.py).
;   EV_SCALE   1 (default; 0 = without).  Endgame scale factor sf (0..64) of the taper's eg part.  The strong side s is the side the eg score favours
;              (eg <= 0: Black), as the full score of EVTOTAL decides it (o = the other side); sf = the value of the first rule that applies to s
;              (64 if none).  EV_SCALE6=0 leaves out rule 6:
;                1. s has no pawns and npm(s) - npm(o) <= 3:  npm(s) < 5 -> 0,  npm(o) <= 3 -> 4,  else 14            (npm = N 3, B 3, R 5, Q 9)
;                2. s has one bishop and pawns only on the a-file (or only on the h-file), the bishop does not cover the promotion square, and o's king
;                   stands on or next to it -> 0
;                3. one bishop each, of opposite colours: no other pieces -> 18 + 4 * passed pawns of s, else 22 + 3 * pieces of s (at most 64)
;                4. one rook each and no other pieces, pawn counts differ by at most 1, all pawns on one flank (files a-d or e-h) -> 36
;                5. exactly one queen on the board and s has it -> 37 + 3 * minor pieces of o
;                6. otherwise min(64, 36 + 7 * pawns of s), minus 4 unless there are pawns on both flanks
;   EV_SCALE2  = EV_SCALE (default; needs EV_SCALE).  1: the second rule set (ev_rule2) instead of the rules above (0 = the rules above).
;              The same strong side; the first rule that applies; constants SC_* fitted to game results and Syzygy verdicts
;              (tuner/fit_scale.py; options T40 .. T57 of a -DTUNE build for SPSA).  one = all pawns on one flank (or none):
;                1. s has no pawns and npm(s) - npm(o) <= 3:  npm(s) < 5 -> 0,  npm(o) <= 3 -> SC_M1,  else SC_M2
;                2. wrong bishop with rook-file pawns, o's king at the promotion square -> 0 (rule 2 above)
;                class rules of single-pawn endings:  rook alone against one pawn escorted by its king (pawn on its 4th rank or beyond, o's king
;                  next to it, s's king >= 3 from the square in front of it and not ahead on its file) -> SC_RKP;  queen alone against a rook or bishop
;                  pawn on its 7th rank with o's king next to it -> SC_QKP;  rook + pawn against rook with o's king in front of the pawn on its
;                  file, or next to the promotion square while the pawn is on its 5th rank or lower -> SC_RPR;  minor + pawn against a minor piece
;                  with o's king in front of the pawn on its file -> SC_MPM
;                3. one bishop each, opposite colours: no other pieces -> SC_OB0 + SC_OB1 * passed pawns of s, else SC_OC0 + SC_OC1 * pieces of s
;                   + SC_OC2 * pawns of s
;                4. one rook each, nothing else, pawns(s) - pawns(o) <= 1, the pawns of s on exactly one flank, o's king next to one of its pawns
;                   -> SC_RK
;                5. exactly one queen on the board (either side) -> SC_Q0 + SC_Q1 * minor pieces of the side without it
;                6. otherwise min(64, SC_G0 + SC_G1 * pawns of s) - SC_G6FL * one
;                rules 3 - 6: - SC_FL * one, then 0..64.
;   EV_SCMG    1 (default).  The scale factor acts on the mg part too, the more the less material is on the board:
;              e = trunc((m * mg * ph + sf * eg * (24 - ph)) / 1536),  m = sf + (64 - sf) * ph / 24 (integer division; 64 with sf = 64).
;              0: m = 64, the mg part is never scaled, so in an ending with little material (KR+B - KR: ph 5) a rule that gives sf = 0
;              leaves ph / 24 of the mg score standing.  Positions with sf = 64 and positions with ph >= 24 have the same value either way.
;              The SC_* constants below were fitted with 0; a refit with 1 (tuner/fit_scale.py) belongs to the next retune of the tables.
;   EV_ATTMAPS 0 (default; forced to 1 by every term that needs it).  1: ev_side also builds the attack maps of its side (pawns, knights, bishops,
;              rooks, queens, king, all pieces, squares attacked at least twice; sliders see through nothing) and eval hands every side the maps of the
;              other side in its own frame (EV_T* fields).  The terms that need both maps run in a second pass (ev_sideb) after both sides.
;              Same values.
;   EV_PHASH   0 (default).  1: pawn cache: the pawn-only part of ev_side (doubled, isolated, supported, phalanx and the passed pawn bitboard, for
;              both sides) is kept in a table of 2^EV_PHBITS entries of 64 bytes per thread unit, keyed by both pawn bitboards (exact match; the
;              empty entry is the position without pawns, whose pawn part is 0).  Nothing that depends on kings or pieces enters it.  Same values,
;              same search tree.
;   EV_LAZY    0 (default; forced to 0 by EV_TUNEMODE).  1: lazy evaluation: when |trunc((mg0 * ph + eg0 * (24 - ph)) / 24)| > EV_LAZYT (mg0 / eg0 =
;              material + PST + bishop pair, White's view; the position alone decides, no search bound), eval returns that value (signed for the side
;              to move, plus TEMPO) without the terms of the full evaluation.  Changes values.
;              With EV_LAZY and a second-pass term on, a second stage skips the second pass when the score after the first pass (taper with sf)
;              exceeds EV_LAZYT2 (700) in absolute value.
;   Threats (second pass; "strongly protected" = attacked by an enemy pawn, or attacked twice by them and not twice by us; "weak" = an enemy piece
;   or pawn, not the king, that we attack and that is not strongly protected; EV_THREAT2=1 (default) sets all five):
;   EV_THRMIN   per piece type P N B R Q: enemy pieces (defended non-pawns or weak ones) attacked by our knights or bishops; weak pieces attacked by
;               our rooks; weak pieces attacked by our king (one value)
;   EV_THRHANG  hanging: weak pieces they do not attack at all, or non-pawns we attack twice; weak pieces only their queen defends
;   EV_THRPAWN  enemy non-pawns attacked by our pawns that stand on safe squares (not attacked by them, or attacked by us); enemy non-pawns our
;               pawns would attack after a safe push (one step, or two from rank 2, onto an empty square their pawns do not attack)
;   EV_THRRESTR squares they attack, we attack, and they do not protect strongly (restricted mobility)
;   EV_THRQUEEN with exactly one enemy queen: our knight attacks / slider attacks (the latter on squares we attack twice) on the squares from which
;               the queen would be attacked, inside our mobility area and not strongly protected; count x2 when it is the only queen on the board
;   EV_PINS    1 (default; 0 = the plain mobility area).  1: pinned pieces (our pieces that alone stand between our king and an enemy slider of
;              that line) and the mobility area: every square except our king, our queens, our pinned pieces, our pawns that are blocked or on
;              ranks 2-3, and the squares their pawns attack (squares of our other pieces count).  The mobility values come from their own tables
;              (mob_*_pins, EV_MOBNP ..); the plain tables are used with EV_PINS = 0.
;   EV_KDANGER 1 (default; 0 = the king attack table).  1: king danger (second pass), replaces the king attack term.  King ring of the enemy king: its square moved inside
;              files b-g / ranks 2-7, with the neighbours, minus the squares two of their pawns defend.  danger = sum of the weights (N 2, B 2, R 3,
;              Q 5) of our pieces that attack the ring + our attacks on the squares next to their king (per piece) + KD_WEAK * weak ring squares
;              (we attack, they defend at most once and only with king / queen) + KD_QCHK / KD_RCHK / KD_BCHK / KD_NCHK for a safe check of that
;              piece type + KD_UNSAFE for an unsafe check + KD_PIN * their pinned pieces - KD_KNDEF when their knight guards a square next to their
;              king; 0 without our queen.  Value = kdanger[min(63, max(0, danger))] (64 tuned entries; the weights are build constants for SPSA).
;   EV_PASSED2 1 (default).  1: passed pawns, second pass: edge distance of the file (every passed pawn); from rank 4 on, with w = 5 * rank - 13
;              (rank 1..8 = 4..7 -> 7, 12, 17, 22): w * min(5, distance of their king / our king to the square in front), w * min(5, our king to the
;              square two in front) (not from rank 7), and with an empty square in front: w for the safety class of the path (no unsafe square in
;              the pawn's span, only squares our pawns defend, none on its own file, not the square in front) and w when the square in front is
;              defended by us or our rook / queen stands behind the pawn.  Unsafe = attacked by them or occupied by them; with an enemy rook / queen
;              behind the pawn every span square is unsafe.
;   Off by default (terms of the linear model unless noted; exact definitions: the code below and tuner/model.py, which reproduces every term):
;   EV_PAWNS2  more pawn terms (pawn cache part): backward, weak unopposed, weak lever, connected per rank, blocked on rank 5 / 6.
;   EV_SHELTER king shelter / pawn storm tables on the three files around the own king (replace the king shield; with castling rights the
;              better of the king square and g1 / c1), plus the distance of the own king to its nearest pawn.
;   EV_PIECES2 = EV_PBISH (bishop pawns, long diagonal, bishop outpost), EV_PMINOR (reachable outpost, minor behind pawn, king protectors),
;              EV_PROOK (closed file, trapped rook, rook on the 7th, connected rooks, rook behind a passer), EV_PQUEEN (weak queen).
;   EV_IMBAL   quadratic material imbalance over the piece counts of both sides (42 entries).  (pair x pair) and (bishop x pair) stay 0:
;              they would repeat the bishop-pair term.
;   EV_SPACE   space in the middlegame: safe central squares behind the own pawns, weighted by the number of own pieces.
;   EV_KLINES  1 (default since Zeval 1.1).  Lines against their king (second pass, attacker's view).  Files: their king's file clamped to b..g, with its two
;              neighbours; "below" = the ranks between their king and us.  kl_open per file without a pawn of either side below their
;              king; kl_storm per file where our pawn nearest to their king stands at most 3 ranks below it and is not blocked by their
;              pawn; kl_heavy per rook / queen of ours on a file without their pawn below their king.  Fitted on the tuning data
;              (Zeval 1.1): mg only.
;   EV_INIT    initiative / complexity correction of mg and eg (not linear, outside the tuner; forced to 0 by EV_TUNEMODE).  'Almost
;              unwinnable' = no passed pawn, outflanking < 0 and pawns on one flank only.
;   EV_GRAIN   0 (default; forced to 0 by EV_TUNEMODE).  1: the tapered score is truncated to a multiple of EV_GRAINS (8) before sign and tempo.
;   -DTUNE: KD_*, EV_LAZYT, EV_LAZYT2, IN_* (and EV_R50C of the search) are read from the option table T20 .. T39 instead of immediates.
;   EVALPROF   test builds only (-DEVALPROF): the main thread counts eval calls and their rdtsc cycles; console command "evalprof" prints them.
; Console command "evalcfg" prints the evaluation switches of the build (bit mask EV_CFG below; tuner/check_eval.py reads it).
;   BP_MG / BP_EG / TEMPO  bishop pair (mg, eg; per side with >= 2 bishops, before the taper) and side-to-move bonus (after the taper).
; tuner/apply_params.py writes tuned values into pst_mg / pst_eg, evp and the BP_MG / BP_EG / TEMPO defaults below.
; ============================================================================================
%ifndef EVAL_FULL
%define EVAL_FULL 1
%endif
%if BB == 0
%undef EVAL_FULL
%define EVAL_FULL 0
%endif
%ifndef HWPOPCNT
%define HWPOPCNT 2
%endif
%ifndef EV_SCMG
%define EV_SCMG 1                   ; the scale factor acts on the mg part too at low phase (header); 0: on the eg part only
%endif
%ifndef EV_SFAST
%define EV_SFAST 1                  ; speed only: the scale factor 64 of the common case without ev_scale (header)
%endif
%ifndef EV_KLBITS
%define EV_KLBITS 1                 ; speed only: EV_KLINES counted with bitboard operations
%endif
%ifndef EV_BRSIGN
%define EV_BRSIGN 1                 ; speed only: strong side and side-to-move sign without branches
%endif
%ifndef EVCACHE
%define EVCACHE 1
%endif
%ifndef EV_TUNEMODE
%define EV_TUNEMODE 0
%endif
%ifndef EV_ENDG
%define EV_ENDG 1
%endif
%ifndef EV_SCALE
%define EV_SCALE 1
%endif
%ifndef EV_SCALE6
%define EV_SCALE6 1                 ; EV_SCALE rule 6 (few pawns); 0 = without it
%endif
%ifndef EV_ENDG2
%define EV_ENDG2 EV_ENDG            ; special evaluation of KQ-KR, KR-KB, KR-KN without pawns (ev_spec2, constants EG2_*)
%endif
; EV_ENDG2 constants (fitted to the Syzygy verdicts by tuner/fit_special.py)
%define EG2_QR     391              ; KQ-KR: EG2_QR + EG2_QEDGE * centre distance of the weak king + EG2_QCLOSE * (7 - king distance)
%define EG2_QEDGE  8
%define EG2_QCLOSE 0
%define EG2_RB     4               ; KR-KB: EG2_RB + EG2_REDGE * centre distance of the weak king
%define EG2_RN     0               ; KR-KN: EG2_RN + EG2_REDGE * centre distance + EG2_RNFAR * distance weak king - knight
%define EG2_REDGE  4
%define EG2_RNFAR  12
%ifndef EV_SCALE2
%define EV_SCALE2 EV_SCALE          ; EV_SCALE with the second rule set (ev_rule2, constants SC_*)
%endif
; EV_SCALE2 constants (fitted by tuner/fit_scale.py; SPSA: options T40 .. T57 of a -DTUNE build)
%define SC_M1    0                  ; 1. no pawns, the other side has at most a minor piece
%define SC_M2    0                 ; 1. no pawns, otherwise
%define SC_OB0   11                 ; 3. opposite bishops alone: SC_OB0 + SC_OB1 * passed pawns
%define SC_OB1   9
%define SC_OC0   28                 ; 3. opposite bishops with other pieces: SC_OC0 + SC_OC1 * pieces + SC_OC2 * pawns
%define SC_OC1   6
%define SC_OC2   3
%define SC_RK    34                 ; 4. rook ending, pawns of s on one flank, o's king at its pawns
%define SC_Q0    44                 ; 5. the only queen: SC_Q0 + SC_Q1 * minor pieces of the side without it
%define SC_Q1    1
%define SC_G0    36                 ; 6. min(64, SC_G0 + SC_G1 * pawns of s) - SC_G6FL on one flank
%define SC_G1    8
%define SC_FL    10                  ; rules 3 - 6: minus SC_FL when all pawns are on one flank
%define SC_G6FL  0
%define SC_RKP   15                 ; class rules: rook against an escorted pawn
%define SC_QKP   0                  ;   queen against a rook / bishop pawn on its 7th rank
%define SC_RPR   2                 ;   rook and pawn against rook, defending king in front / at the promotion square
%define SC_MPM   0                  ;   minor and pawn against a minor piece, defending king in front of the pawn
%ifndef EV_ATTMAPS
%define EV_ATTMAPS 0
%endif
%ifndef EV_PHASH
%define EV_PHASH 0
%endif
%ifndef EV_PHBITS
%define EV_PHBITS 13                ; pawn cache: 2^13 entries of 64 bytes = 512 KiB per thread unit
%endif
%ifndef EV_LAZY
%define EV_LAZY 0
%endif
%ifndef EV_LAZYT
%define EV_LAZYT 1000               ; lazy evaluation threshold (centipawns, material + PST + bishop pair)
%endif
%if EV_TUNEMODE
%undef EV_LAZY
%define EV_LAZY 0
%endif
%if EV_TUNEMODE
%undef EV_INIT
%define EV_INIT 0
%undef EV_GRAIN
%define EV_GRAIN 0
%endif
%ifndef EV_THREAT2
%define EV_THREAT2 1                ; 1 = all five threat switches below (each can also be set alone)
%endif
%ifndef EV_THRMIN
%define EV_THRMIN EV_THREAT2
%endif
%ifndef EV_THRHANG
%define EV_THRHANG EV_THREAT2
%endif
%ifndef EV_THRPAWN
%define EV_THRPAWN EV_THREAT2
%endif
%ifndef EV_THRRESTR
%define EV_THRRESTR EV_THREAT2
%endif
%ifndef EV_THRQUEEN
%define EV_THRQUEEN EV_THREAT2
%endif
%ifndef EV_PINS
%define EV_PINS 1
%endif
%ifndef EV_KDANGER
%define EV_KDANGER 1
%endif
%ifndef EV_PASSED2
%define EV_PASSED2 1
%endif
; king danger weights (EV_KDANGER; the index of the table kdanger = their weighted sum, clamped to 0..63; meant for SPSA, see -DTUNE)
%ifndef KD_WEAK
%define KD_WEAK 2                   ; per weak square of the king ring
%endif
%ifndef KD_QCHK
%define KD_QCHK 6                   ; safe queen check (any)
%endif
%ifndef KD_RCHK
%define KD_RCHK 5                   ; safe rook check
%endif
%ifndef KD_BCHK
%define KD_BCHK 3                   ; safe bishop check
%endif
%ifndef KD_NCHK
%define KD_NCHK 4                   ; safe knight check
%endif
%ifndef KD_UNSAFE
%define KD_UNSAFE 1                 ; unsafe check (any)
%endif
%ifndef KD_PIN
%define KD_PIN 2                    ; per pinned piece of the defender
%endif
%ifndef KD_KNDEF
%define KD_KNDEF 3                  ; minus, when a defending knight guards a square next to its king
%endif
%ifndef EV_PAWNS2
%define EV_PAWNS2 0
%endif
%ifndef EV_SHELTER
%define EV_SHELTER 0
%endif
%ifndef EV_PIECES2
%define EV_PIECES2 0                ; 1 = the four piece-term switches below
%endif
%ifndef EV_PBISH
%define EV_PBISH EV_PIECES2
%endif
%ifndef EV_PMINOR
%define EV_PMINOR EV_PIECES2
%endif
%ifndef EV_PROOK
%define EV_PROOK EV_PIECES2
%endif
%ifndef EV_PQUEEN
%define EV_PQUEEN EV_PIECES2
%endif
%ifndef EV_IMBAL
%define EV_IMBAL 0
%endif
%ifndef EV_SPACE
%define EV_SPACE 0
%endif
%ifndef EV_KLINES
%define EV_KLINES 1
%endif
%ifndef EV_OSCALE
%define EV_OSCALE 1024              ; test builds only: the score of the normal evaluation * EV_OSCALE / 1024 (special endgames unscaled)
%endif
%ifndef EV_SPACENPM
%define EV_SPACENPM 40              ; space only with at least this much non-pawn material on the board (N 3, B 3, R 5, Q 9, both sides)
%endif
%ifndef EV_INIT
%define EV_INIT 0
%endif
%ifndef EV_GRAIN
%define EV_GRAIN 0
%endif
%ifndef EV_GRAINS
%define EV_GRAINS 8                 ; output grain (centipawns) with EV_GRAIN
%endif
; initiative / complexity weights (EV_INIT; fitted by tuner/fit_init.py; -DTUNE options T31 .. T39)
%ifndef IN_PASSED
%define IN_PASSED 2
%endif
%ifndef IN_PAWNS
%define IN_PAWNS 1
%endif
%ifndef IN_OUTFL
%define IN_OUTFL 1
%endif
%ifndef IN_BOTH
%define IN_BOTH 7
%endif
%ifndef IN_INFIL
%define IN_INFIL -5
%endif
%ifndef IN_NONPM
%define IN_NONPM 35
%endif
%ifndef IN_UNWIN
%define IN_UNWIN 23
%endif
%ifndef IN_BIAS
%define IN_BIAS 8
%endif
%ifndef IN_MGADD
%define IN_MGADD -2
%endif
%ifndef EV_LAZYT2
%define EV_LAZYT2 700               ; lazy evaluation, second stage: threshold after the first pass (centipawns)
%endif
%if EVAL_FULL == 0
%undef EV_THRMIN
%define EV_THRMIN 0
%undef EV_THRHANG
%define EV_THRHANG 0
%undef EV_THRPAWN
%define EV_THRPAWN 0
%undef EV_THRRESTR
%define EV_THRRESTR 0
%undef EV_THRQUEEN
%define EV_THRQUEEN 0
%undef EV_PINS
%define EV_PINS 0
%undef EV_KDANGER
%define EV_KDANGER 0
%undef EV_PASSED2
%define EV_PASSED2 0
%undef EV_PAWNS2
%define EV_PAWNS2 0
%undef EV_SHELTER
%define EV_SHELTER 0
%undef EV_PBISH
%define EV_PBISH 0
%undef EV_PMINOR
%define EV_PMINOR 0
%undef EV_PROOK
%define EV_PROOK 0
%undef EV_PQUEEN
%define EV_PQUEEN 0
%undef EV_IMBAL
%define EV_IMBAL 0
%undef EV_SPACE
%define EV_SPACE 0
%undef EV_KLINES
%define EV_KLINES 0
%undef EV_INIT
%define EV_INIT 0
%undef EV_GRAIN
%define EV_GRAIN 0
%undef EV_ENDG2
%define EV_ENDG2 0
%undef EV_SCALE2
%define EV_SCALE2 0
%endif
%if EV_ENDG2 && EV_ENDG == 0
%error "EV_ENDG2 needs EV_ENDG=1"
%endif
%if EV_SCALE2 && EV_SCALE == 0
%error "EV_SCALE2 needs EV_SCALE=1"
%endif
; EV_SFAST acts where its test is exact: rule set 2 with constants fixed at assembly (not -DTUNE) and rule 6 reaching 64.
; EV_SFPMIN = the fewest pawns of s for which rule 6 gives 64 (at least 2: pawns on both flanks)
%assign EV_SFAST_ON 0
%if EV_SFAST && EV_SCALE2
%ifndef TUNE
%if SC_G0 >= 64
%assign EV_SFAST_ON 1
%assign EV_SFPMIN 2
%elif SC_G1 > 0
%assign EV_SFAST_ON 1
%assign EV_SFPMIN (64 - SC_G0 + SC_G1 - 1) / SC_G1
%if EV_SFPMIN < 2
%assign EV_SFPMIN 2
%endif
%endif
%endif
%endif
%ifdef SFASTVERIFY
%ifdef WIN
%error "SFASTVERIFY is a Linux test build"
%endif
%if EV_SFAST_ON == 0
%error "SFASTVERIFY needs EV_SFAST=1 in a configuration where it acts (EV_SCALE2, no -DTUNE)"
%endif
%endif
%assign EV_PASSB EV_THRMIN | EV_THRHANG | EV_THRPAWN | EV_THRRESTR | EV_THRQUEEN | EV_KDANGER | EV_PASSED2 | EV_SPACE | EV_KLINES     ; a second-pass term (ev_sideb) is on
%assign EV_PINCALC EV_PINS | EV_KDANGER                                                                         ; pinned pieces are needed
%if EV_PASSB
%undef EV_ATTMAPS
%define EV_ATTMAPS 1
%endif
%if EVAL_FULL == 0
%undef EV_ATTMAPS
%define EV_ATTMAPS 0
%undef EV_PHASH
%define EV_PHASH 0
%undef EV_LAZY
%define EV_LAZY 0
%undef EV_ENDG
%define EV_ENDG 0
%undef EV_SCALE
%define EV_SCALE 0
%endif
%define EV_KNOWNWIN 10000           ; base of a "known win" of a special evaluation (|eval| stays below 20000 < MATEB - MAXPLY)
%if EVAL_FULL == 0
%undef HWPOPCNT
%define HWPOPCNT 0
%endif
%ifndef BP_MG
%define BP_MG 17
%endif
%ifndef BP_EG
%define BP_EG 68
%endif
%ifndef TEMPO
%define TEMPO 18
%endif

; ---- parameter layout of evp (one packed dword per entry: mg + 65536 * eg, written with SC mg, eg); tuner/model.py has the same list
%define EV_MOBN     0           ; 9   knight mobility 0..8
%define EV_MOBB     9           ; 14  bishop mobility 0..13
%define EV_MOBR     23          ; 15  rook mobility 0..14
%define EV_MOBQ     38          ; 28  queen mobility 0..27
%define EV_PASSED   66          ; 6   passed pawn on rank 2..7
%define EV_PPOWN    72          ; 8   passed pawn: own king distance 0..7 to the square in front
%define EV_PPTHEM   80          ; 8   passed pawn: enemy king distance 0..7 to the square in front
%define EV_DOUBLED  88
%define EV_ISOLATED 89
%define EV_SUPPORT  90
%define EV_PHALANX  91
%define EV_ROPEN    92
%define EV_RSEMI    93
%define EV_SHIELD   94
%define EV_THREATP  95
%define EV_OUTPOST  96
%define EV_KATT     97          ; 32  king attack units 0..31
%define EV_TMINOR   129         ; 5   enemy P N B R Q attacked by our minor pieces (EV_THRMIN)
%define EV_TROOK    134         ; 5   weak enemy P N B R Q attacked by our rooks
%define EV_TKING    139         ; weak enemy pieces attacked by our king
%define EV_HANG     140         ; hanging (EV_THRHANG)
%define EV_WQP      141         ; weak pieces defended only by their queen
%define EV_RESTR    142         ; restricted squares (EV_THRRESTR)
%define EV_TSAFEP   143         ; safe pawn threats (EV_THRPAWN)
%define EV_TPUSH    144         ; pawn push threats
%define EV_KONQ     145         ; knight on queen (EV_THRQUEEN)
%define EV_SONQ     146         ; slider on queen
%define EV_KDANG    147         ; 64  king danger 0..63 (EV_KDANGER)
%define EV_PFILE    211         ; passed pawn: edge distance of its file (EV_PASSED2)
%define EV_PKTHEM   212         ; w * their king distance to the square in front
%define EV_PKUS     213         ; w * our king distance to the square in front
%define EV_PKUS2    214         ; w * our king distance to the square two in front
%define EV_PFREE    215         ; 4   w per safety class of the path
%define EV_PFDEF    219         ; w when the square in front is defended
%define EV_BACKW    220         ; backward pawns (EV_PAWNS2)
%define EV_WUNOPP   221         ; weak unopposed pawns
%define EV_WLEVER   222         ; weak levers
%define EV_CONN     223         ; 6   connected pawns on rank 2..7
%define EV_BLOCK56  229         ; 2   pawns blocked on rank 5, rank 6
%define EV_SHELT    231         ; 28  shelter [edge distance 0..3][rank 0..6] (EV_SHELTER)
%define EV_STORM    259         ; 28  storm [edge distance][their rank 0..6]
%define EV_BSTORM   287         ; 7   blocked storm [their rank]
%define EV_KPDIST   294         ; king distance to the nearest own pawn
%define EV_BPAWNS   295         ; bishop pawns (EV_PBISH)
%define EV_LDIAG    296         ; long diagonal
%define EV_BOUTP    297         ; bishop outpost
%define EV_REACH    298         ; reachable outpost (EV_PMINOR)
%define EV_MBP      299         ; minor behind pawn
%define EV_KPROTN   300         ; knight distance to the own king
%define EV_KPROTB   301         ; bishop distance to the own king
%define EV_RCLOSED  302         ; rook on a closed file (EV_PROOK)
%define EV_TRAPR    303         ; trapped rook
%define EV_R7       304         ; rook on rank 7
%define EV_RCONN    305         ; connected rooks
%define EV_RBP      306         ; rook behind a passed pawn
%define EV_WEAKQ    307         ; weak queen (EV_PQUEEN)
%define EV_IMBO     308         ; 21  imbalance with own pieces (EV_IMBAL)
%define EV_IMBT     329         ; 21  imbalance with their pieces
%define EV_SPACE_T  350         ; space (EV_SPACE)
%define EV_MOBNP    351         ; 9   knight mobility with EV_PINS (its own tables: the area differs)
%define EV_MOBBP    360         ; 14  bishop mobility with EV_PINS
%define EV_MOBRP    374         ; 15  rook mobility with EV_PINS
%define EV_MOBQP    389         ; 28  queen mobility with EV_PINS
%define EV_KLOPEN   417         ; open files at their king (EV_KLINES)
%define EV_KLSTORM  418         ; our storming pawns
%define EV_KLHEAVY  419         ; our rooks / queens on files without their shelter pawn
%define EV_N        420
%if EV_PINS                     ; mobility tables in use
%define EV_MOBN_U EV_MOBNP
%define EV_MOBB_U EV_MOBBP
%define EV_MOBR_U EV_MOBRP
%define EV_MOBQ_U EV_MOBQP
%else
%define EV_MOBN_U EV_MOBN
%define EV_MOBB_U EV_MOBB
%define EV_MOBR_U EV_MOBR
%define EV_MOBQ_U EV_MOBQ
%endif
%macro SC 2
    dd (%2)*65536 + (%1)
%endmacro

%if UNIT == 0
section .data
align 16
matmg:      dw 0,82,337,365,477,1025,0
mateg:      dw 0,94,281,297,512,936,0

; The values below were tuned with tuner/tune.py (about 4 million quiet positions, K 3.154); the tables hold pst = tuned(pst + material)
; - material.  Fitted separately: EG2_* (tuner/fit_special.py), SC_* (tuner/fit_scale.py), IN_* (tuner/fit_init.py).  imb_* were tuned
; alone (--free imbalance --from-zero, prior 100).  Hand-set: KD_*, EV_LAZYT*.
; piece-square tables: rank 8 first, files a..h, White's point of view
pst_mg:
    dw    0,    0,    0,    0,    0,    0,    0,    0
    dw  120,   70,   95,  111,  144,   95,   47,   20
    dw   -3,   -2,   13,   20,   32,   69,   28,    6
    dw   -7,  -11,   -8,    7,   14,   24,    0,   -7
    dw  -18,  -22,   -9,   -7,   -1,    3,  -12,  -21
    dw  -21,  -24,  -18,  -17,   -7,  -13,   -4,  -17
    dw  -21,  -18,  -18,  -12,  -15,   12,   10,  -12
    dw    0,    0,    0,    0,    0,    0,    0,    0
    dw -177,  -73,  -63,  -34,   31, -106,   -8, -101
    dw  -21,  -25,   32,   26,   57,   41,  -10,  -13
    dw  -25,    1,   31,   39,   50,   69,   19,   25
    dw    4,   20,   36,   44,   38,   54,   37,   34
    dw   -2,   15,   24,   30,   34,   32,   41,   12
    dw  -15,   -4,    5,   13,   16,   11,   12,   -8
    dw  -35,  -19,   -7,    1,    3,   -1,   -1,   -5
    dw  -47,  -15,  -38,  -19,  -12,  -12,  -13,  -42
    dw  -26,  -52,  -57,  -82,  -74, -102,  -29,  -46
    dw  -33,  -17,   -3,   -9,  -11,   18,  -37,  -37
    dw   -1,   10,   31,   23,   42,   48,   28,   12
    dw  -11,   20,   21,   38,   27,   35,   23,    5
    dw   -2,   15,   17,   24,   31,   18,   25,   15
    dw    4,   18,   11,   18,   16,   22,   22,   17
    dw   15,   14,   22,    5,    9,   21,   30,   22
    dw   17,    9,    3,   -7,   -5,   -2,    0,   14
    dw   23,   16,    1,    8,   26,   43,   55,   48
    dw   -2,  -12,    1,   27,   18,   33,   26,   35
    dw   -9,   22,   11,   35,   53,   49,   70,   21
    dw   -7,    4,   14,   40,   36,   32,   42,   12
    dw  -22,  -17,  -19,   -5,   -3,   -4,    5,   -7
    dw  -27,  -17,  -21,  -13,  -12,  -12,    6,  -18
    dw  -36,  -14,  -13,   -5,   -7,    3,    5,  -47
    dw   -9,   -7,   -1,    4,    6,    5,    2,   -3
    dw    2,   20,   30,   34,   40,   90,   66,   45
    dw    8,   -3,   27,   -6,   21,   61,    4,   70
    dw   18,   26,   35,   46,   69,   60,   78,   43
    dw   23,   37,   33,   23,   39,   55,   54,   50
    dw   29,   40,   38,   32,   35,   49,   51,   49
    dw   33,   41,   41,   36,   36,   43,   52,   42
    dw   36,   39,   45,   44,   45,   61,   63,   49
    dw   44,   35,   40,   44,   50,   33,   46,   43
    dw   28,   46,   59,   22,   30,   37,   38,   68
    dw  -10,    4,   -7,   11,   12,   67,  -16,  -24
    dw   17,   37,  -14,  -28,  -24,   41,   33,  -17
    dw  -78,  -27,  -50,  -74,  -54,  -21,   -3,  -70
    dw  -43,  -13,  -36,  -78,  -66,  -55,  -25,  -64
    dw  -16,  -22,  -39,  -74,  -48,  -37,  -23,  -33
    dw    3,  -14,  -27,  -85,  -56,  -50,    0,   11
    dw    0,   18,   -8,  -89,  -21,  -64,    8,   15
pst_eg:
    dw    0,    0,    0,    0,    0,    0,    0,    0
    dw  129,  159,  163,  153,  148,  148,  172,  153
    dw   64,   65,   59,   52,   57,   48,   58,   57
    dw   50,   44,   39,   25,   29,   25,   40,   37
    dw   36,   36,   22,   21,   21,   21,   23,   22
    dw   27,   23,   28,   35,   34,   31,   14,   14
    dw   33,   27,   37,   43,   47,   39,   18,    8
    dw    0,    0,    0,    0,    0,    0,    0,    0
    dw  -37,    0,   16,   16,    6,   37,  -10,  -51
    dw  -14,    3,   -6,   21,   14,   -4,   -8,   -6
    dw   -2,    5,   25,   24,   25,   29,   14,   -6
    dw   -2,    7,   23,   40,   47,   33,   22,    6
    dw    1,   11,   29,   35,   40,   33,   15,   12
    dw  -26,   -1,    5,   22,   23,    5,   -2,   -9
    dw   -4,    0,  -13,    2,    6,  -12,    8,   -2
    dw  -44,  -34,   -6,    9,    1,   -3,  -27,   -2
    dw   35,   35,   35,   51,   45,   52,   37,   36
    dw   33,   35,   27,   36,   29,   25,   38,   31
    dw   27,   28,   28,   27,   31,   44,   37,   31
    dw   26,   30,   28,   38,   40,   28,   39,   33
    dw   18,   20,   35,   37,   36,   34,   22,   14
    dw   14,   18,   26,   28,   31,   23,   12,   19
    dw   20,    6,    7,   21,   22,    7,   12,  -14
    dw    1,   17,   11,   21,   17,   25,   15,    8
    dw   68,   76,   80,   76,   76,   79,   76,   70
    dw   70,   80,   78,   72,   75,   68,   76,   66
    dw   72,   60,   67,   55,   49,   63,   54,   66
    dw   63,   63,   65,   54,   57,   59,   57,   65
    dw   57,   65,   68,   57,   59,   63,   61,   51
    dw   40,   48,   48,   43,   41,   43,   41,   37
    dw   32,   29,   38,   29,   29,   21,   23,   37
    dw   31,   37,   36,   32,   30,   38,   35,   13
    dw  122,  135,  144,  138,  139,  113,  132,  143
    dw  128,  135,  134,  180,  163,  143,  182,  115
    dw  113,  125,  143,  132,  147,  137,  122,  130
    dw  136,  150,  127,  163,  167,  162,  151,  130
    dw  122,  120,  123,  151,  149,  131,  117,  113
    dw   75,   98,  117,  107,  110,  111,   78,   74
    dw   70,   69,   67,   84,   92,   28,   12,   62
    dw   48,   74,   65,   94,   77,   56,   41,   55
    dw -143,  -54,  -45,  -13,  -21,   -9,  -47, -131
    dw  -34,   14,   20,   17,   28,   26,   33,  -12
    dw  -11,   24,   39,   48,   46,   55,   39,   15
    dw    4,   20,   45,   57,   54,   53,   37,   18
    dw  -19,    8,   37,   52,   53,   46,   27,    2
    dw  -26,    4,   20,   40,   35,   28,   10,   -9
    dw  -26,   -6,    9,   24,   18,   15,   -5,  -28
    dw  -76,  -35,  -21,   -6,  -41,   -7,  -35,  -79

evp:                            ; terms of the full evaluation, layout: EV_* above
; mob_n
    SC  -25,   -6
    SC  -10,   40
    SC   -5,   71
    SC   -2,   86
    SC    2,   93
    SC    6,  101
    SC   10,  101
    SC   16,   94
    SC   22,   82
; mob_b
    SC  -37,    1
    SC  -33,   32
    SC  -24,   46
    SC  -20,   61
    SC  -14,   74
    SC  -11,   85
    SC  -10,   91
    SC   -9,   95
    SC   -7,   98
    SC   -4,   96
    SC   -3,   94
    SC   17,   88
    SC   23,   96
    SC   37,   74
; mob_r
    SC  -25,   66
    SC  -19,   94
    SC  -17,  100
    SC  -15,  106
    SC  -16,  118
    SC  -10,  121
    SC  -11,  130
    SC   -6,  130
    SC   -5,  135
    SC   -2,  139
    SC    0,  142
    SC    2,  144
    SC   10,  142
    SC   16,  135
    SC   33,  127
; mob_q
    SC   10,    0
    SC    9,   34
    SC   11,   66
    SC   12,   97
    SC   13,  119
    SC   16,  128
    SC   17,  143
    SC   18,  157
    SC   20,  164
    SC   21,  172
    SC   24,  176
    SC   25,  182
    SC   26,  184
    SC   26,  189
    SC   27,  192
    SC   26,  195
    SC   21,  205
    SC   29,  199
    SC   34,  194
    SC   27,  203
    SC   42,  194
    SC   42,  189
    SC   56,  173
    SC   80,  165
    SC   96,  155
    SC   81,  160
    SC   80,  133
    SC   84,  133
; passed
    SC  -14,    5
    SC  -16,   13
    SC  -14,   31
    SC   10,   41
    SC   32,   89
    SC  116,   -8
; pp_own_king
    SC   -5,   43
    SC   20,   22
    SC    7,   15
    SC   -3,    7
    SC    0,    2
    SC   14,   -1
    SC   27,  -10
    SC   12,  -10
; pp_enemy_king
    SC  -40,   24
    SC   38,    4
    SC   38,   -3
    SC   22,    4
    SC   16,    6
    SC   10,    7
    SC   -6,   18
    SC   -5,    8
; doubled
    SC  -10,  -14
; isolated
    SC   -7,  -11
; supported
    SC   12,   11
; phalanx
    SC    5,    5
; rook_open
    SC   35,    8
; rook_semi
    SC   17,    3
; shield
    SC   12,    0
; threat_pawn
    SC   13,   46
; outpost
    SC   22,   17
; king_attack
    SC  -44,  -54
    SC    0,    1
    SC  -47,  -14
    SC  -37,  -49
    SC  -29,   24
    SC  -42,  -22
    SC   34,  -32
    SC  -17,   18
    SC    4,  -10
    SC   39,   69
    SC   68,   33
    SC  107,   37
    SC  157,   58
    SC  151,   24
    SC  159,   23
    SC  113,   15
    SC  128,   16
    SC  144,   17
    SC  162,   18
    SC  180,   19
    SC  200,   20
    SC  220,   21
    SC  242,   22
    SC  264,   23
    SC  288,   24
    SC  312,   25
    SC  338,   26
    SC  364,   27
    SC  392,   28
    SC  400,   29
    SC  400,   30
    SC  400,   31
; thr_minor
    SC    0,   14
    SC   19,   42
    SC   21,   34
    SC   40,   10
    SC   32,    8
; thr_rook
    SC   -1,   13
    SC    8,   22
    SC   11,   27
    SC   -1,   -1
    SC   36,    0
; thr_king
    SC  -45,   39
; hanging
    SC   10,   18
; weak_qprot
    SC    2,    5
; restricted
    SC    3,    0
; thr_safe_pawn
    SC   31,  -11
; thr_pawn_push
    SC   15,   21
; knight_on_queen
    SC    3,   -4
; slider_on_queen
    SC    9,   -4
; kdanger
    SC -161,   -8
    SC -162,    8
    SC -160,    4
    SC -159,   17
    SC -154,    5
    SC -154,   13
    SC -151,   23
    SC -145,   12
    SC -142,   13
    SC -135,   18
    SC -132,   15
    SC -123,   24
    SC -114,   21
    SC -104,   23
    SC  -91,   30
    SC  -76,   29
    SC  -68,   39
    SC  -35,   27
    SC  -25,   34
    SC    2,   16
    SC   14,   26
    SC   70,   -3
    SC   85,   11
    SC  140,  -16
    SC  165,  -19
    SC  198,  -32
    SC  240,  -51
    SC  281,  -50
    SC  317,  -88
    SC  396, -105
    SC  406,  -86
    SC  338,  -34
    SC  368,  -22
    SC  381,   13
    SC  354,   13
    SC  361,   26
    SC  236,   -2
    SC  380,    8
    SC  288,   68
    SC  452,  132
    SC  411,   74
    SC  259,   -9
    SC  290,   12
    SC  320,   22
    SC  363,   39
    SC  375,   41
    SC  345,    9
    SC  375,   15
    SC  383,   16
    SC  426,   18
    SC  400,   16
    SC  400,   17
    SC  400,   17
    SC  400,   17
    SC  400,   18
    SC  400,   18
    SC  400,   18
    SC  400,   19
    SC  400,   19
    SC  400,   19
    SC  400,   20
    SC  400,   20
    SC  400,   20
    SC  400,   21
; passed_file
    SC   -6,   -6
; pp_king_them_w
    SC    0,    3
; pp_king_us_w
    SC   -2,   -1
; pp_king_us2_w
    SC    1,   -1
; passed_free
    SC  -18,   15
    SC   -4,    9
    SC   -3,    7
    SC    0,    4
; passed_free_def
    SC    1,    3
; backward
    SC   -4,   -3
; weak_unopposed
    SC   -5,   -7
; weak_lever
    SC   -5,  -13
; connected
    SC   -1,   -6
    SC    0,   -3
    SC   -1,   -4
    SC    5,    5
    SC   28,   25
    SC  144,   18
; blocked56
    SC   -9,   -8
    SC   -8,   -9
; shelter
    SC  -16,    2
    SC   15,  -22
    SC   18,  -10
    SC    4,   -1
    SC    6,    2
    SC   -5,    9
    SC   64,   -5
    SC  -26,   -3
    SC   12,  -11
    SC   13,   -8
    SC  -12,    0
    SC   -7,    0
    SC   -1,    5
    SC    8,   11
    SC  -17,   -4
    SC   11,   -3
    SC    2,   -1
    SC   -9,    0
    SC   -2,   -3
    SC   -4,    0
    SC   19,   12
    SC  -27,   -1
    SC    9,   -3
    SC   -1,   -3
    SC  -10,    4
    SC   -7,    5
    SC   -4,    6
    SC  -33,   21
; storm
    SC  -18,   -6
    SC   13,   56
    SC  -26,   29
    SC  -16,    2
    SC   -6,   -3
    SC   -2,   -8
    SC   -7,   -9
    SC  -13,   -3
    SC  -50,   60
    SC  -22,   25
    SC   -6,    3
    SC   -1,    2
    SC   12,   -4
    SC   10,   -5
    SC    1,  -10
    SC  -36,   32
    SC  -27,   11
    SC    6,   -6
    SC   10,   -6
    SC   11,   -7
    SC   18,  -10
    SC    2,   -7
    SC  -50,   10
    SC  -39,    1
    SC   -4,  -15
    SC    8,  -11
    SC   21,   -8
    SC   20,   -8
; blocked_storm
    SC    0,    0
    SC    0,    0
    SC   -1,  -29
    SC    6,  -15
    SC   13,  -21
    SC    0,  -17
    SC    7,  -23
; king_pawn_dist
    SC   -4,   -3
; bishop_pawns
    SC   -1,   -5
; long_diag
    SC    6,    3
; bishop_outpost
    SC   27,    3
; reach_outpost
    SC   10,    9
; minor_behind_pawn
    SC    2,   20
; king_protector_n
    SC   -1,   -2
; king_protector_b
    SC    0,    1
; rook_closed
    SC   -4,    0
; trapped_rook
    SC   -2,  -14
; rook_7th
    SC   -6,    4
; rooks_connected
    SC    0,    6
; rook_behind_passer
    SC    5,    2
; weak_queen
    SC  -11,   -4
; imb_ours
    SC    0,    0
    SC   -6,   10
    SC    1,    4
    SC   -2,    6
    SC   16,    7
    SC   -4,  -25
    SC    0,    0
    SC   15,    1
    SC  -12,  -17
    SC  -56,   16
    SC    9,    3
    SC   13,    8
    SC  -17,  -25
    SC  -17,    5
    SC   -2,  -23
    SC   -8,   -7
    SC   27,   12
    SC  -71,  -74
    SC  -65,  -31
    SC  -62, -142
    SC -164, -127
; imb_theirs
    SC    0,    0
    SC   -8,    9
    SC    0,    0
    SC   -4,    0
    SC   17,   12
    SC    0,    0
    SC   -2,    4
    SC   13,   12
    SC   11,  -19
    SC    0,    0
    SC   -9,  -11
    SC   12,   18
    SC   -5,    5
    SC  -21,   19
    SC    0,    0
    SC  -40,  -24
    SC   33,   49
    SC   52,   38
    SC   34,   49
    SC   -8,   60
    SC    0,    0
; space
    SC    5,  -16
; mob_n_pins
    SC  -32,   39
    SC  -16,  103
    SC   -9,  145
    SC   -3,  163
    SC    0,  173
    SC    2,  184
    SC    7,  184
    SC   12,  182
    SC   19,  170
; mob_b_pins
    SC  -40,   43
    SC  -38,   92
    SC  -25,  110
    SC  -21,  128
    SC  -13,  142
    SC  -10,  156
    SC   -7,  166
    SC   -6,  173
    SC   -7,  179
    SC   -1,  180
    SC   -3,  181
    SC   19,  172
    SC   30,  181
    SC   27,  162
; mob_r_pins
    SC  -27,  132
    SC  -22,  196
    SC  -14,  204
    SC  -15,  216
    SC  -14,  226
    SC  -13,  236
    SC  -11,  245
    SC   -8,  247
    SC   -6,  253
    SC   -4,  257
    SC   -3,  264
    SC   -2,  268
    SC    3,  269
    SC    7,  265
    SC   33,  252
; mob_q_pins
    SC   -7,  -11
    SC   56,  -63
    SC   49,   76
    SC   47,  181
    SC   51,  228
    SC   51,  269
    SC   54,  281
    SC   59,  291
    SC   61,  304
    SC   64,  317
    SC   68,  322
    SC   71,  325
    SC   73,  328
    SC   76,  330
    SC   76,  332
    SC   76,  334
    SC   70,  343
    SC   76,  337
    SC   75,  337
    SC   72,  335
    SC   73,  340
    SC   85,  321
    SC   88,  312
    SC  133,  275
    SC  104,  281
    SC  138,  278
    SC   88,  221
    SC  162,  187
; kl_open, kl_storm, kl_heavy (EV_KLINES)
    SC   14,    0
    SC    5,    0
    SC    8,    0
evp_end:
%if EVAL_FULL
align 8
ev_k55:     dq 0x5555555555555555   ; population count constants
ev_k33:     dq 0x3333333333333333
ev_k0f:     dq 0x0F0F0F0F0F0F0F0F
ev_k01:     dq 0x0101010101010101
ev_filea:   dq 0x0101010101010101   ; file a
ev_outr:    dq 0x0000FFFFFF000000   ; ranks 4..6
ev_hw:      db 0                    ; 1 = the CPU has POPCNT (set by init_eval; read by eval when HWPOPCNT = 2)
ev_dark:    dq 0xAA55AA55AA55AA55   ; dark squares (a1 is dark)
ev_qside:   dq 0x0F0F0F0F0F0F0F0F   ; files a-d
ev_kside:   dq 0xF0F0F0F0F0F0F0F0   ; files e-h
ev_r23:     dq 0x0000000000FFFF00   ; ranks 2-3
ev_r5:      dq 0x000000FF00000000   ; rank 5
ev_r6:      dq 0x0000FF0000000000   ; rank 6
ev_r7:      dq 0x00FF000000000000   ; rank 7
ev_center:  dq 0x0000001818000000   ; d4 e4 d5 e5
ev_space:   dq 0x000000003C3C3C00   ; files c-f, ranks 2-4
ev_dtab:                            ; Chebyshev distance of two squares 0..63: ev_dtab[a * 64 + b]
%assign EVDA 0
%rep 64
%assign EVDB 0
%rep 64
%assign EVDF ((EVDA & 7) - (EVDB & 7))
%if EVDF < 0
%assign EVDF -EVDF
%endif
%assign EVDR ((EVDA >> 3) - (EVDB >> 3))
%if EVDR < 0
%assign EVDR -EVDR
%endif
%if EVDF > EVDR
    db EVDF
%else
    db EVDR
%endif
%assign EVDB EVDB + 1
%endrep
%assign EVDA EVDA + 1
%endrep
%endif
%if EV_ENDG
align 8
; king + pawn vs king bitbase, generated by tools/gen_kpk.py (bit = 1: the pawn side wins).
; index ((stm * 64 + wk) * 64 + bk) * 24 + (pawn rank - 1) * 4 + pawn file, pawn side White, pawn on files a..d, stm 0 = pawn side to move
kpk_bb:
    dq 0xFFFF000000000000, 0xFFFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFF, 0xFFFB000000000000
    dq 0xFFFFFFFFFFFFF7FF, 0xFFFFFFFFFFFFFFFF, 0xFF00FFFF00FFFF00, 0xEEFFFF66FFFF22FF
    dq 0xFFFFFFFFFFFFFFFF, 0xF000FFF000FFF000, 0x6EFFF026FFF002FF, 0xFFFFFFFFF7FFFFF3
    dq 0x0000FF0000FF0000, 0x6EFF1026FF0002FF, 0xFFFFFFFF77FFFF33, 0x0000F00000F80000
    dq 0x6EF31026F10002F0, 0xFFFFFFFF77FFF733, 0x0000800000C80000, 0x6E73102631000210
    dq 0xFFFFFFFF77FFF733, 0x000E80000EC8000E, 0x6E73102E31000E10, 0xFFFFFFFF77FFF733
    dq 0x0000000000000000, 0xFFFFFFFFFFFFFF00, 0xFFFFFFFFFFFFFFFF, 0x0000000000000000
    dq 0xFFFFFFFFFFFFF700, 0xFFFFFFFFFFFFFFFF, 0xFF00FFFF00FFFF48, 0xEEFFFF66FFFF22FF
    dq 0xFFFFFFFFFFFFFFFF, 0xF000FFF000FFF008, 0x6EFFF026FFF002FF, 0xFFFFFFFFF7FFFFF3
    dq 0x0000FF0000FF0008, 0x6EFF1026FF0002FF, 0xFFFFFFFF77FFFF33, 0x0000F00000F80008
    dq 0x6EF31026F10002F0, 0xFFFFFFFF77FFF733, 0x0000800000C80008, 0x6E73102631000210
    dq 0xFFFFFFFF77FFF733, 0x000E80000EC8000E, 0x6E73102E31000E10, 0xFFFFFFFF77FFF733
    dq 0x0000000000FFFFFF, 0xFFFFFFFF00000000, 0xFFFFFFFFFFFFFFFF, 0x0000000000FFFFFE
    dq 0xFFFFFFFF00000000, 0xFFFFFFFFFFFFFFFF, 0xFF00FFFF80FFFFCC, 0xEEFFFF66FFFF20FF
    dq 0xFFFFFFFFFFFFFFFF, 0xF000FFF000FFF08C, 0x6EFFF026FFF000FF, 0xFFFFFFFFF7FFFFF3
    dq 0x0000FF0000FF008C, 0x6EFF1026FF0000FF, 0xFFFFFFFF77FFFF33, 0x0000F00000F8008C
    dq 0x6EF31026F10000F0, 0xFFFFFFFF77FFF733, 0x0000800000C8008C, 0x6E73102631000010
    dq 0xFFFFFFFF77FFF733, 0x000E80000EC8008E, 0x6E73102E31000E10, 0xFFFFFFFF77FFF733
    dq 0x0000FFFFFFFFFFFF, 0xFF00000000000000, 0xFFFFFFFFFFFFFFFF, 0x0000FFFFFDFFFFFE
    dq 0xFF00000000000000, 0xFFFFFFFFFFFFFFFF, 0xFF00FFFF88FFFFCC, 0xEEFFFF42FFFF00FF
    dq 0xFFFFFFFFFFFFFFFF, 0xF000FFF008FFF08C, 0x6EFFF002FFF000FF, 0xFFFFFFFFF7FFFFF3
    dq 0x0000FF0008FF008C, 0x6EFF1002FF0000FF, 0xFFFFFFFF77FFFF33, 0x0000F00008F8008C
    dq 0x6EF31002F10000F0, 0xFFFFFFFF77FFF733, 0x0000800008C8008C, 0x6E73100231000010
    dq 0xFFFFFFFF77FFF733, 0x000E80000EC8008E, 0x6E73100E31000E10, 0xFFFFFFFF77FFF733
    dq 0xFFFFFFFFFFFFFFFF, 0x00000000000000FF, 0xFFFFFFFFFFFF0000, 0xFFFBFFFFFDFFFFFE
    dq 0x00000000000000FF, 0xFFFFFFFFFFFF0000, 0xFF00FFFF88FFFFCC, 0xA6FFFF00FFFF00FF
    dq 0xFFFFFFFFFFFFFFFF, 0xF000FFF008FFF08C, 0x26FFF000FFF000FF, 0xFFFFFFFFF7FFFFF1
    dq 0x0000FF0008FF008C, 0x26FF1000FF0000FF, 0xFFFFFFFF77FFFF31, 0x0000F00008F8008C
    dq 0x26F31000F10000F0, 0xFFFFFFFF77FFF731, 0x0000800008C8008C, 0x2673100031000010
    dq 0xFFFFFFFF77FFF731, 0x000E80000EC8008E, 0x2E73100E31000E10, 0xFFFFFFFF77FFF731
    dq 0xFFFFFFFFFFFFFFFF, 0x00000000FFFFFFFF, 0xFFFFFF0000000000, 0xFFFBFFFFFDFFFFFE
    dq 0x00000000FFFFF7FF, 0xFFFFFF0000000000, 0xFF00FFFF88FFFFCC, 0x00FFFF00FFFF00FF
    dq 0xFFFFFFFFFF7FFFFF, 0xF000FFF008FFF08C, 0x00FFF000FFF000FF, 0xFFFFFFFFF37FFFF1
    dq 0x0000FF0008FF008C, 0x00FF1000FF0000FF, 0xFFFFFFFF737FFF31, 0x0000F00008F8008C
    dq 0x00F31000F10000F0, 0xFFFFFFFF737FF731, 0x0000800008C8008C, 0x0073100031000010
    dq 0xFFFFFFFF737FF731, 0x000E80000EC8008E, 0x0E73100E31000E10, 0xFFFFFFFF737FF731
    dq 0xFFFFFFFFFFFFFFFF, 0x00FFFFFFFFFFFFFF, 0x0000000000000000, 0xFFFBFFFFFDFFFFFE
    dq 0x00FFFFFFFFFFF7FF, 0x0000000000000000, 0xFF00FFFF88FFFF88, 0x00FFFF00FFFF00FF
    dq 0xFFFFFFFFFF11FFFF, 0xF000FFF008FFF088, 0x00FFF000FFF000FF, 0xFFF7FFFFF311FFF1
    dq 0x0000FF0008FF0088, 0x00FF1000FF0000FF, 0xFFF7FFFF7311FF31, 0x0000F00008F80088
    dq 0x00F31000F10000F0, 0xFFF7FFFF7311F731, 0x0000800008C80088, 0x0073100031000010
    dq 0xFFF7FFFF7311F731, 0x000C80000CC8008C, 0x0E73100C31000C10, 0xFFF7FFFF731FF731
    dq 0xFFFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFF, 0x000000000000FFFF, 0xFFFBFFFFFDFFFFFE
    dq 0xFFFFFFFFFFFFF7FF, 0x000000000000FFFF, 0xFF00FFFF00FFFF88, 0x00FFFF00FFFF00FF
    dq 0xFFFF33FFFF11FFFF, 0xF000FFF000FFF088, 0x00FFF000FFF000FF, 0xFFF733FFF311FFF1
    dq 0x0000FF0000FF0088, 0x00FF1000FF0000FF, 0xFFF733FF7311FF31, 0x0000F00000F80088
    dq 0x00F31000F10000F0, 0xFFF733FF7311F731, 0x0000800000C80088, 0x0073100031000010
    dq 0xFFF733FF7311F731, 0x0008800008C80088, 0x0873100831000810, 0xFFF73FFF731FF731
    dq 0xFFFE000000000000, 0xFEFFFFFEFFFFFEFF, 0xFFFFFEFFFFFEFFFF, 0xFFFA000000000000
    dq 0xFEFFFFFEFFFFF6FF, 0xFFFFFEFFFFFEFFFF, 0xFF20000000000000, 0xFEFFFF76FFFF72FF
    dq 0xFFFFFEFFFFFEFFFF, 0xF002FFF000FFF000, 0xEEFFF666FFF226FF, 0xFFFFFEFFFFFEFFFF
    dq 0x0002FF000EFF000E, 0xEEFF1266FF0026FF, 0xFFFFFEFF7FFEFF37, 0x0002F00000F80000
    dq 0xEEF31266F10026F0, 0xFFFFFEFF7FFEF737, 0x000E80000EC8000E, 0xEE73126E31002E10
    dq 0xFFFFFEFF7FFEF737, 0x000E80000EC8000E, 0xEE73126E31002E10, 0xFFFFFEFF7FFEF737
    dq 0x0000000000000000, 0xFDFFFFFDFFFFFD00, 0xFFFFFDFFFFFDFFFF, 0x0000000000000000
    dq 0xFDFFFFFDFFFFF500, 0xFFFFFDFFFFFDFFFF, 0x0000000000000000, 0xFDFFFFF5FFFF7100
    dq 0xFFFFFDFFFFFDFFFF, 0xF000FFF000FFF48C, 0xECFFF66CFFF224FF, 0xFFFFFDFFFFFDFFFF
    dq 0x000CFF000CFF008C, 0xECFF126CFF0024FF, 0xFFFFFDFF7FFDFF37, 0x0000F00000F8008C
    dq 0xECF3126CF10024F0, 0xFFFFFDFF7FFDF737, 0x000C80000CC8008C, 0xEC73126C31002C10
    dq 0xFFFFFDFF7FFDF737, 0x000C80000CC8008C, 0xEC73126C31002C10, 0xFFFFFDFF7FFDF737
    dq 0x0000000000FFFFFB, 0xFBFFFFFB00000000, 0xFFFFFBFFFFFBFFFF, 0x0000000000FFFFFA
    dq 0xFBFFFFFB00000000, 0xFFFFFBFFFFFBFFFF, 0x0000000000FFFFE8, 0xFBFFFFF300000000
    dq 0xFFFFFBFFFFFBFFFF, 0xF000FFF808FFFCCA, 0xEAFFF66AFFF202FF, 0xFFFFFBFFFFFBFFFF
    dq 0x000AFF000AFF08CA, 0xEAFF126AFF000AFF, 0xFFFFFBFF7FFBFF37, 0x0000F00008F808CA
    dq 0xEAF3126AF10002F0, 0xFFFFFBFF7FFBF737, 0x000A80000AC808CA, 0xEA73126A31000A10
    dq 0xFFFFFBFF7FFBF737, 0x000A80000AC808CA, 0xEA73126A31000A10, 0xFFFFFBFF7FFBF737
    dq 0x0000FFFFF7FFFFF7, 0xF700000000000000, 0xFFFFF7FFFFF7FFFF, 0x0000FFFFF5FFFFF6
    dq 0xF700000000000000, 0xFFFFF7FFFFF7FFFF, 0x0000FFFFC0FFFFE4, 0xF700000000000000
    dq 0xFFFFF7FFFFF7FFFF, 0xF000FFF884FFFCC6, 0xE6FFF426FFF000FF, 0xFFFFF7FFFFF7FFFF
    dq 0x0006FF0084FF08C6, 0xE6FF1026FF0006FF, 0xFFFFF7FF7FF7FF37, 0x0000F00084F808C6
    dq 0xE6F31026F10000F0, 0xFFFFF7FF7FF7F737, 0x0006800086C808C6, 0xE673102631000610
    dq 0xFFFFF7FF7FF7F737, 0x0006800086C808C6, 0xE673102631000610, 0xFFFFF7FF7FF7F737
    dq 0xFFFFFFFFFFFFFFFF, 0x00000000000000FF, 0xFFFFFFFFFFFF0000, 0xFFFBFFFFFDFFFFFE
    dq 0x00000000000000FF, 0xFFFFFFFFFFFF0000, 0xFF80FFFFC8FFFFCC, 0x00000000000000FF
    dq 0xFFFFFFFFFFFF0000, 0xF008FFF88CFFFCCC, 0x6EFFF000FFF000FF, 0xFFFFFFFFFFFFFFFB
    dq 0x0008FF008CFF08CC, 0x6EFF100EFF000EFF, 0xFFFFFFFF7FFFFF33, 0x0008F0008CF808CC
    dq 0x6EF31000F10000F0, 0xFFFFFFFF7FFFF733, 0x000E80008EC808CE, 0x6E73100E31000E10
    dq 0xFFFFFFFF7FFFF733, 0x000E80008EC808CE, 0x6E73100E31000E10, 0xFFFFFFFF7FFFF733
    dq 0xFFFFFFFFFFFFFFFF, 0x00000000FFFFFFFF, 0xFFFFFF0000000000, 0xFFFBFFFFFDFFFFFE
    dq 0x00000000FFFFF7FF, 0xFFFFFF0000000000, 0xFF80FFFF88FFFFCC, 0x00000000FFFF00FF
    dq 0xFFFFFF0000000000, 0xF008FFF888FFFCCC, 0x00FFF000FFF000FF, 0xFFFFFFFFF7FFFFF1
    dq 0x0008FF0088FF08CC, 0x0EFF100EFF0000FF, 0xFFFFFFFF77FFFF31, 0x0008F00088F808CC
    dq 0x00F31000F10000F0, 0xFFFFFFFF77FFF731, 0x000C80008CC808CC, 0x0E73100E31000C10
    dq 0xFFFFFFFF77FFF731, 0x000E80008EC808CE, 0x0E73100E31000E10, 0xFFFFFFFF77FFF731
    dq 0xFFFFFFFFFFFFFFFF, 0x00FFFFFFFFFFFFFF, 0x0000000000000000, 0xFFFBFFFFFDFFFFFE
    dq 0x00FFFFFFFFFFF7FF, 0x0000000000000000, 0xFF00FFFF88FFFF88, 0x00FFFF00FFFF00FF
    dq 0x0000000000000000, 0xF000FFF888FFF888, 0x00FFF000FFF000FF, 0xFFFFFFFFF311FFF1
    dq 0x0000FF0088FF0888, 0x0EFF1000FF0000FF, 0xFFFFFFFF731FFF31, 0x0000F00088F80888
    dq 0x00F31000F10000F0, 0xFFFFFFFF7311F731, 0x0008800088C80888, 0x0E73100831000810
    dq 0xFFFFFFFF731FF731, 0x000C80008CC8088C, 0x0E73100C31000C10, 0xFFFFFFFF731FF731
    dq 0xFFFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFF, 0x000000000000FFFF, 0xFFFBFFFFFDFFFFFE
    dq 0xFFFFFFFFFFFFF7FF, 0x000000000000FFFF, 0xFF00FFFF00FFFF88, 0x00FFFF00FFFF00FF
    dq 0x000000000000FFFF, 0xF000FFF000FFF888, 0x00FFF000FFF000FF, 0xFFF733FFF311FFF1
    dq 0x0000FF0000FF0888, 0x00FF1000FF0000FF, 0xFFF73FFF731FFF31, 0x0000F00000F80888
    dq 0x00F31000F10000F0, 0xFFF733FF7311F731, 0x0000800000C80888, 0x0073100031000010
    dq 0xFFF73FFF731FF731, 0x0008800008C80888, 0x0873100831000810, 0xFFF73FFF731FF731
    dq 0xFFEFFFFFEFFFFFEF, 0xEFFFFFEFFFFFEFFF, 0xFFFFEFFFFFEFFFFF, 0xFFEB000000000000
    dq 0xEFFFFFEFFFFFE7FF, 0xFFFFEFFFFFEFFFFF, 0xFF20000000000000, 0xEFFFFF67FFFF63FF
    dq 0xFFFFEFFFFFEFFFFF, 0xF202000000000000, 0xEFFFF767FFF727FF, 0xFFFFEFFFFFEFFFFF
    dq 0x0022FF000EFF0000, 0xEEFF7666FF2266FF, 0xFFFFEFFFFFEFFFFF, 0x0026F000EEF800EE
    dq 0xEEF33666F10266F0, 0xFFFFEFFFFFEFF77F, 0x002E80000EC8000E, 0xEE73366E31026E10
    dq 0xFFFFEFFFFFEFF77F, 0x00EE8000EEC800EE, 0xEE7336EE3102EE10, 0xFFFFEFFFFFEFF77F
    dq 0xFFDFFFFFDFFFFFDF, 0xDFFFFFDFFFFFDFFF, 0xFFFFDFFFFFDFFFFF, 0x0000000000000000
    dq 0xDFFFFFDFFFFFD700, 0xFFFFDFFFFFDFFFFF, 0x0000000000000000, 0xDFFFFFD7FFFF5300
    dq 0xFFFFDFFFFFDFFFFF, 0x0000000000000000, 0xDFFFFF5FFFF71700, 0xFFFFDFFFFFDFFFFF
    dq 0x000EFF0000FF48CE, 0xCEFF76CEFF2246FF, 0xFFFFDFFFFFDFFFFF, 0x00CEF000CEF808CE
    dq 0xCEF336CEF1024EF0, 0xFFFFDFFFFFDFF77F, 0x000E80000EC808CE, 0xCE7336CE31024E10
    dq 0xFFFFDFFFFFDFF77F, 0x00CE8000CEC808CE, 0xCE7336CE3102CE10, 0xFFFFDFFFFFDFF77F
    dq 0xFFBFFFFFBFFFFFBF, 0xBFFFFFBFFFFFBFFF, 0xFFFFBFFFFFBFFFFF, 0x0000000000FFFFBE
    dq 0xBFFFFFBF00000000, 0xFFFFBFFFFFBFFFFF, 0x0000000000FFFFAC, 0xBFFFFFB700000000
    dq 0xFFFFBFFFFFBFFFFF, 0x0000000000FFFE8E, 0xBFFFFF3F00000000, 0xFFFFBFFFFFBFFFFF
    dq 0x0000FF808EFFCCAE, 0xAEFF76AEFF202EFF, 0xFFFFBFFFFFBFFFFF, 0x00AEF000AEF88CAE
    dq 0xAEF336AEF100AEF0, 0xFFFFBFFFFFBFF77F, 0x000E80008EC88CAE, 0xAE7336AE31002E10
    dq 0xFFFFBFFFFFBFF77F, 0x00AE8000AEC88CAE, 0xAE7336AE3100AE10, 0xFFFFBFFFFFBFF77F
    dq 0xFF7FFFFF7FFFFF7F, 0x7FFFFF7FFFFF7FFF, 0xFFFF7FFFFF7FFFFF, 0x0000FFFF7DFFFF7E
    dq 0x7F00000000000000, 0xFFFF7FFFFF7FFFFF, 0x0000FFFF48FFFF6C, 0x7F00000000000000
    dq 0xFFFF7FFFFF7FFFFF, 0x0000FFFC0CFFFE4E, 0x7F00000000000000, 0xFFFF7FFFFF7FFFFF
    dq 0x000EFF884CFFCC6E, 0x6EFF526EFF0000FF, 0xFFFF7FFFFF7FFFFF, 0x006EF0084EF88C6E
    dq 0x6EF3126EF1006EF0, 0xFFFF7FFFFF7FF77F, 0x000E80084EC88C6E, 0x6E73126E31000E10
    dq 0xFFFF7FFFFF7FF77F, 0x006E80086EC88C6E, 0x6E73126E31006E10, 0xFFFF7FFFFF7FF77F
    dq 0xFFFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFF, 0xFFFBFFFFFDFFFFFE
    dq 0x00000000000000FF, 0xFFFFFFFFFFFF0000, 0xFF80FFFFC8FFFFCC, 0x00000000000000FF
    dq 0xFFFFFFFFFFFF0000, 0xF808FFFC8CFFFCCC, 0x00000000000000FF, 0xFFFFFFFFFFFF0000
    dq 0x0088FF88CCFFCCCC, 0xEEFF1000FF000EFF, 0xFFFFFFFFFFFFFFB7, 0x008CF008CCF88CCC
    dq 0xEEF310EEF100EEF0, 0xFFFFFFFFFFFFF737, 0x008E8008CEC88CCE, 0xEE73100E31000E10
    dq 0xFFFFFFFFFFFFF737, 0x00EE8008EEC88CEE, 0xEE7310EE3100EE10, 0xFFFFFFFFFFFFF737
    dq 0xFFFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFF, 0xFFFBFFFFFDFFFFFE
    dq 0x00000000FFFFF7FF, 0xFFFFFF0000000000, 0xFF80FFFF88FFFFCC, 0x00000000FFFF00FF
    dq 0xFFFFFF0000000000, 0xF808FFF888FFFCCC, 0x00000000FFF000FF, 0xFFFFFF0000000000
    dq 0x0088FF8888FFCCCC, 0x00FF100EFF0000FF, 0xFFFFFFFF7FFFFF31, 0x0088F00888F88CCC
    dq 0xEEF310EEF10008F0, 0xFFFFFFFF7FFFF731, 0x008C80088CC88CCC, 0x0E73100E31000C10
    dq 0xFFFFFFFF7FFFF731, 0x00CE8008CEC88CCE, 0xEE7310EE3100CE10, 0xFFFFFFFF7FFFF731
    dq 0xFFFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFF, 0xFFFBFFFFFDFFFFFE
    dq 0x00FFFFFFFFFFF7FF, 0x0000000000000000, 0xFF00FFFF88FFFF88, 0x00FFFF00FFFF00FF
    dq 0x0000000000000000, 0xF000FFF888FFF888, 0x00FFF000FFF000FF, 0x0000000000000000
    dq 0x0000FF8888FF8888, 0x0EFF1000FF0000FF, 0xFFFFFFFF7311FF31, 0x0000F00888F88888
    dq 0xEEF31000F10000F0, 0xFFFFFFFF73FFF731, 0x0008800888C88888, 0x0E73100831000810
    dq 0xFFFFFFFF731FF731, 0x008C80088CC8888C, 0xEE73108C31008C10, 0xFFFFFFFF73FFF731
    dq 0xFFFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFF, 0xFFFBFFFFFDFFFFFE
    dq 0xFFFFFFFFFFFFF7FF, 0x000000000000FFFF, 0xFF00FFFF00FFFF88, 0x00FFFF00FFFF00FF
    dq 0x000000000000FFFF, 0xF000FFF000FFF888, 0x00FFF000FFF000FF, 0x000000000000FFF1
    dq 0x0000FF0000FF8888, 0x00FF1000FF0000FF, 0xFFF733FF731FFF31, 0x0000F00000F88888
    dq 0x00F31000F10000F0, 0xFFF7FFFF73FFF731, 0x0000800000C88888, 0x0073100031000010
    dq 0xFFF73FFF731FF731, 0x0008800008C88888, 0x0873100831000810, 0xFFF7FFFF73FFF731
    dq 0xFEFFFFFEFFFFFEFF, 0xFFFFFEFFFFFEFFFF, 0xFFFEFFFFFEFFFFFE, 0xFEFBFFFEFDFFFEFE
    dq 0xFFFFFEFFFFFEF7FF, 0xFFFEFFFFFEFFFFFE, 0xFE31000000000000, 0xFFFFFE77FFFE73FF
    dq 0xFFFEFFFFFEFFFFFE, 0xF202000000000000, 0xFFFFF677FFF637FF, 0xFFFEFFFFFEFFFFFE
    dq 0x2022000000000000, 0xFFFF7677FF7277FF, 0xFFFEFFFFFEFFFFFE, 0x0226F000EEF8000E
    dq 0xFFF77666F32666F0, 0xFFFEFFFFFEFFFFFE, 0x026E880EEECC8EEE, 0xFF77766E33266E10
    dq 0xFFFEFFFFFEFFFFFE, 0x02EE8800EECC80EE, 0xFF7776EE3326EE10, 0xFFFEFFFFFEFFFFFE
    dq 0xFDFFFFFDFFFFFDFF, 0xFFFFFDFFFFFDFFFF, 0xFFFDFFFFFDFFFFFD, 0xFDFBFFFDFDFFFDFE
    dq 0xFFFFFDFFFFFDF7FF, 0xFFFDFFFFFDFFFFFD, 0x0000000000000000, 0xFFFFFDF7FFFD7300
    dq 0xFFFDFFFFFDFFFFFD, 0x0000000000000000, 0xFFFFFD7FFFF53700, 0xFFFDFFFFFDFFFFFD
    dq 0x0000000000000000, 0xFFFFF5FFFF717700, 0xFFFDFFFFFDFFFFFD, 0x00EEF0000EFC8CEE
    dq 0xFFF77CEEF3246EF0, 0xFFFDFFFFFDFFFFFD, 0x0CEE880CEECC8CEE, 0xFF777CEE3324EE10
    dq 0xFFFDFFFFFDFFFFFD, 0x00EE8800EECC8CEE, 0xFF777CEE3324EE10, 0xFFFDFFFFFDFFFFFD
    dq 0xFBFFFFFBFFFFFBFF, 0xFFFFFBFFFFFBFFFF, 0xFFFBFFFFFBFFFFFB, 0xFBFBFFFBFDFFFBFE
    dq 0xFFFFFBFFFFFBF7FF, 0xFFFBFFFFFBFFFFFB, 0x0000000000FFFBEC, 0xFFFFFBF700000000
    dq 0xFFFBFFFFFBFFFFFB, 0x0000000000FFFACE, 0xFFFFFB7F00000000, 0xFFFBFFFFFBFFFFFB
    dq 0x0000000000FFE8EE, 0xFFFFF3FF00000000, 0xFFFBFFFFFBFFFFFB, 0x000EF808EEFCCAEE
    dq 0xFFF77AEEF302EEF0, 0xFFFBFFFFFBFFFFFB, 0x0AEE880AEECCCAEE, 0xFF777AEE330AEE10
    dq 0xFFFBFFFFFBFFFFFB, 0x00EE8808EECCCAEE, 0xFF777AEE3302EE10, 0xFFFBFFFFFBFFFFFB
    dq 0xF7FFFFF7FFFFF7FF, 0xFFFFF7FFFFF7FFFF, 0xFFF7FFFFF7FFFFF7, 0xF7FBFFF7FDFFF7FE
    dq 0xFFFFF7FFFFF7F7FF, 0xFFF7FFFFF7FFFFF7, 0x0000FFF7C8FFF7EC, 0xFF00000000000000
    dq 0xFFF7FFFFF7FFFFF7, 0x0000FFF48CFFF6CE, 0xFF00000000000000, 0xFFF7FFFFF7FFFFF7
    dq 0x0000FFC0CCFFE4EE, 0xFF00000000000000, 0xFFF7FFFFF7FFFFF7, 0x00EEF884CEFCC6EE
    dq 0xFFF736EEF1000EF0, 0xFFF7FFFFF7FFFFF7, 0x06EE8884EECCC6EE, 0xFF7736EE3306EE10
    dq 0xFFF7FFFFF7FFFFF7, 0x00EE8884EECCC6EE, 0xFF7736EE3300EE10, 0xFFF7FFFFF7FFFFF7
    dq 0xFFFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFF, 0xFFFBFFFFFDFFFFFE
    dq 0xFFFFFFFFFFFFF7FF, 0xFFFFFFFFFFFFFFFF, 0xFF80FFFFC8FFFFCC, 0x00000000000000FF
    dq 0xFFFFFFFFFFFF0000, 0xF808FFFC8CFFFCCC, 0x00000000000000FF, 0xFFFFFFFFFFFF0000
    dq 0x8088FFC8CCFFCCCC, 0x00000000000000FF, 0xFFFFFFFFFFFF0000, 0x088CF88CCCFCCCCC
    dq 0xFFF3100EF100EEF0, 0xFFFFFFFFFFFFFF7F, 0x08CE888CCECCCCCE, 0xFF773EEE330EEE10
    dq 0xFFFFFFFFFFFFFF7F, 0x08EE888CEECCCCEE, 0xFF7730EE3300EE10, 0xFFFFFFFFFFFFFF7F
    dq 0xFFFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFF, 0xFFFBFFFFFDFFFFFE
    dq 0xFFFFFFFFFFFFF7FF, 0xFFFFFFFFFFFFFFFF, 0xFF80FFFF88FFFFCC, 0x00000000FFFF00FF
    dq 0xFFFFFF0000000000, 0xF808FFF888FFFCCC, 0x00000000FFF000FF, 0xFFFFFF0000000000
    dq 0x8088FF8888FFCCCC, 0x00000000FF0000FF, 0xFFFFFF0000000000, 0x0888F88888FCCCCC
    dq 0x0EF310EEF10008F0, 0xFFFFFFFFFFFFF731, 0x088C88888CCCCCCC, 0xEE771EEE31008C10
    dq 0xFFFFFFFFFFFFFF7F, 0x08CE8888CECCCCCE, 0xEE7710EE3100CE10, 0xFFFFFFFFFFFFFF71
    dq 0xFFFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFF, 0xFFFBFFFFFDFFFFFE
    dq 0xFFFFFFFFFFFFF7FF, 0xFFFFFFFFFFFFFFFF, 0xFF00FFFF88FFFF88, 0x00FFFF00FFFF00FF
    dq 0x0000000000000000, 0xF000FFF888FFF888, 0x00FFF000FFF000FF, 0x0000000000000000
    dq 0x0000FF8888FF8888, 0x00FF1000FF0000FF, 0x0000000000000000, 0x0000F88888F88888
    dq 0xEEF31000F10000F0, 0xFFFFFFFF731FF731, 0x0008888888C88888, 0xEE73100831000810
    dq 0xFFFFFFFFFFFFFF3F, 0x008C88888CC8888C, 0xEE73108C31008C10, 0xFFFFFFFFF3FFFF31
    dq 0xFFFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFF, 0xFFFBFFFFFDFFFFFE
    dq 0xFFFFFFFFFFFFF7FF, 0xFFFFFFFFFFFFFFFF, 0xFF00FFFF00FFFF88, 0x00FFFF00FFFF00FF
    dq 0x000000000000FFFF, 0xF000FFF000FFF888, 0x00FFF000FFF000FF, 0x000000000000FFF1
    dq 0x0000FF0000FF8888, 0x00FF1000FF0000FF, 0x000000000000FF31, 0x0000F00000F88888
    dq 0x00F31000F10000F0, 0xFFF73FFF73FFF731, 0x0000800000C88888, 0x0073100031000010
    dq 0xFFFFFFFF7FFFF731, 0x0008800008C88888, 0x0873100831000810, 0xFFF7FFFF73FFF731
    dq 0xEFFFFFEFFFFFEFFF, 0xFFFFEFFFFFEFFFFF, 0xFFEFFFFFEFFFFFEF, 0xEFFBFFEFFDFFEFFE
    dq 0xFFFFEFFFFFEFF7FF, 0xFFEFFFFFEFFFFFEF, 0xEF31FFEF18FFEFEC, 0xFFFFEF77FFEF73FF
    dq 0xFFEFFFFFEFFFFFEF, 0xE302000000000000, 0xFFFFE777FFE737FF, 0xFFEFFFFFEFFFFFEF
    dq 0x2022000000000000, 0xFFFF6777FF6377FF, 0xFFEFFFFFEFFFFFEF, 0x0226000000000000
    dq 0xFFF76777F72777F2, 0xFFEFFFFFEFFFFFEF, 0x226E880EEECCC0EE, 0xFF77677F33666E10
    dq 0xFFEFFFFFEFFFFFEF, 0x66EEACEEEECEEEEE, 0xFF7767FF3766EE52, 0xFFEFFFFFEFFFFFEF
    dq 0xDFFFFFDFFFFFDFFF, 0xFFFFDFFFFFDFFFFF, 0xFFDFFFFFDFFFFFDF, 0xDFFBFFDFFDFFDFFE
    dq 0xFFFFDFFFFFDFF7FF, 0xFFDFFFFFDFFFFFDF, 0xDF31FFDFD8FFDFEC, 0xFFFFDFF7FFDF73FF
    dq 0xFFDFFFFFDFFFFFDF, 0x0000000000000000, 0xFFFFDF7FFFD73700, 0xFFDFFFFFDFFFFFDF
    dq 0x0000000000000000, 0xFFFFD7FFFF537700, 0xFFDFFFFFDFFFFFDF, 0x0000000000000000
    dq 0xFFFF5FFFF7177F00, 0xFFDFFFFFDFFFFFDF, 0x0EEE8880EECCCEEE, 0xFF77DFFF3346EE10
    dq 0xFFDFFFFFDFFFFFDF, 0xCEEEACCEEECECEEE, 0xFF7FDFFFB7CEEE5A, 0xFFDFFFFFDFFFFFDF
    dq 0xBFFFFFBFFFFFBFFF, 0xFFFFBFFFFFBFFFFF, 0xFFBFFFFFBFFFFFBF, 0xBFFBFFBFFDFFBFFE
    dq 0xFFFFBFFFFFBFF7FF, 0xFFBFFFFFBFFFFFBF, 0xBFB1FFBFC8FFBFEC, 0xFFFFBFF7FFBF73FF
    dq 0xFFBFFFFFBFFFFFBF, 0x0000000000FFBECE, 0xFFFFBF7F00000000, 0xFFBFFFFFBFFFFFBF
    dq 0x0000000000FFACEE, 0xFFFFB7FF00000000, 0xFFBFFFFFBFFFFFBF, 0x0000000000FE8EEE
    dq 0xFFFF3FFF00000000, 0xFFBFFFFFBFFFFFBF, 0x00EE888EEECCAEEE, 0xFF77BFFF332EEE10
    dq 0xFFBFFFFFBFFFFFBF, 0xAEEEACAEEECEAEEE, 0xFF7FBFFFB7AEEE5A, 0xFFBFFFFFBFFFFFBF
    dq 0x7FFFFF7FFFFF7FFF, 0xFFFF7FFFFF7FFFFF, 0xFF7FFFFF7FFFFF7F, 0x7FFBFF7FFDFF7FFE
    dq 0xFFFF7FFFFF7FF7FF, 0xFF7FFFFF7FFFFF7F, 0x7F81FF7FC8FF7FEC, 0xFFFF7FF7FF7F73FF
    dq 0xFF7FFFFF7FFFFF7F, 0x0000FF7C8CFF7ECE, 0xFF00000000000000, 0xFF7FFFFF7FFFFF7F
    dq 0x0000FF48CCFF6CEE, 0xFF00000000000000, 0xFF7FFFFF7FFFFF7F, 0x0000FC0CCEFE4EEE
    dq 0xFF00000000000000, 0xFF7FFFFF7FFFFF7F, 0x0EEE884CEECC6EEE, 0xFF777FFF3320EE10
    dq 0xFF7FFFFF7FFFFF7F, 0x6EEEAC6EEECE6EEE, 0xFF7F7FFFB76EEE5A, 0xFF7FFFFF7FFFFF7F
    dq 0xFFFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFF, 0xFFFBFFFFFDFFFFFE
    dq 0xFFFFFFFFFFFFF7FF, 0xFFFFFFFFFFFFFFFF, 0xFF80FFFFC8FFFFCC, 0xFFFFFFF7FFFF03FF
    dq 0xFFFFFFFFFFFFFFFF, 0xF808FFFC8CFFFCCC, 0x00000000000000FF, 0xFFFFFFFFFFFF0000
    dq 0x8088FFC8CCFFCCCC, 0x00000000000000FF, 0xFFFFFFFFFFFF0000, 0x088CFC8CCCFCCCCC
    dq 0x00000000000000F8, 0xFFFFFFFFFFFF0000, 0x88CE88CCCECCCCCE, 0xFF7770EE330EEE10
    dq 0xFFFFFFFFFFFFFFFF, 0xCCEE8CCCEECCCCEE, 0xFF7FFEEEB7EEEE58, 0xFFFFFFFFFFFFFFFF
    dq 0xFFFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFF, 0xFFFBFFFFFDFFFFFE
    dq 0xFFFFFFFFFFFFF7FF, 0xFFFFFFFFFFFFFFFF, 0xFF80FFFF88FFFFCC, 0xFFFFFF07FFFF00FF
    dq 0xFFFFFFFFFFFFFFFF, 0xF808FFF888FFFCCC, 0x00000000FFF000FF, 0xFFFFFF0000000000
    dq 0x8088FF8888FFCCCC, 0x00000000FF0000FF, 0xFFFFFF0000000000, 0x0888F88888FCCCCC
    dq 0x00000000F10008F8, 0xFFFFFF0000000000, 0x888C88888CCCCCCC, 0xEE771EEE31008C10
    dq 0xFFFFFFFFFFFFFFF1, 0x88CE8888CECCCCCE, 0xEE7FFEEEB188CE18, 0xFFFFFFFFFFFFFFFF
    dq 0xFFFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFF, 0xFFFBFFFFFDFFFFFE
    dq 0xFFFFFFFFFFFFF7FF, 0xFFFFFFFFFFFFFFFF, 0xFF00FFFF88FFFF88, 0x0FFFFF00FFFF00FF
    dq 0xFFFFFFFFFFFFFFFF, 0xF000FFF888FFF888, 0x00FFF000FFF000FF, 0x0000000000000000
    dq 0x0000FF8888FF8888, 0x00FF1000FF0000FF, 0x0000000000000000, 0x0000F88888F88888
    dq 0x00F31000F10000F0, 0x0000000000000000, 0x0008888888C88888, 0xEE73100831000810
    dq 0xFFFFFFFFF3FFFF3F, 0x008C88888CC8888C, 0xEE73108C31008C10, 0xFFFFFFFFFFFFFFFF
    dq 0xFFFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFF, 0xFFFBFFFFFDFFFFFE
    dq 0xFFFFFFFFFFFFF7FF, 0xFFFFFFFFFFFFFFFF, 0xFF00FFFF00FFFF88, 0x00FFFF00FFFF00FF
    dq 0xFFFFFFFFFF1FFFFF, 0xF000FFF000FFF888, 0x00FFF000FFF000FF, 0x000000000000FFF1
    dq 0x0000FF0000FF8888, 0x00FF1000FF0000FF, 0x000000000000FF31, 0x0000F00000F88888
    dq 0x00F31000F10000F0, 0x000000000000F731, 0x0000800000C88888, 0x0073100031000010
    dq 0xFFF7FFFF7FFFF731, 0x0008800008C88888, 0x0873100831000810, 0xFFFFFFFFFFFFF731
    dq 0xFFFFFEFFFFFEFFFF, 0xFFFEFFFFFEFFFFFE, 0xFEFFFFFEFFFFFEFF, 0xFFFBFEFFFDFEFFFE
    dq 0xFFFEFFFFFEFFF7FE, 0xFEFFFFFEFFFFFEFF, 0xFF11FEFF08FEFFCC, 0xFFFEFF77FEFF33FE
    dq 0xFEFFFFFEFFFFFEFF, 0xF300FEF100FEFE00, 0xFFFEF777FEF733FE, 0xFEFFFFFEFFFFFEFF
    dq 0x3022000000000000, 0xFFFE7777FE7377FE, 0xFEFFFFFEFFFFFEFF, 0x0222000000000000
    dq 0xFFF67777F63777F2, 0xFEFFFFFEFFFFFEFF, 0x226E000000000000, 0xFF76777F72777F30
    dq 0xFEFFFFFEFFFFFEFF, 0x66EE8EEEEECCEEEE, 0xFF7677FF3677FF52, 0xFEFFFFFEFFFFFEFF
    dq 0xFFFFFDFFFFFDFFFF, 0xFFFDFFFFFDFFFFFD, 0xFDFFFFFDFFFFFDFF, 0xFFFBFDFFFDFDFFFE
    dq 0xFFFDFFFFFDFFF7FD, 0xFDFFFFFDFFFFFDFF, 0xFF11FDFF88FDFFCC, 0xFFFDFF77FDFF33FD
    dq 0xFDFFFFFDFFFFFDFF, 0xF311FDFD00FDFECC, 0xFFFDFF77FDF733FD, 0xFDFFFFFDFFFFFDFF
    dq 0x0000000000000000, 0xFFFDF7FFFD737700, 0xFDFFFFFDFFFFFDFF, 0x0000000000000000
    dq 0xFFFD7FFFF5377700, 0xFDFFFFFDFFFFFDFF, 0x0000000000000000, 0xFFF5FFFF7177FF00
    dq 0xFDFFFFFDFFFFFDFF, 0xEEEEA8EEEECCEEEE, 0xFF7DFFFFB5FFFF1D, 0xFDFFFFFDFFFFFDFF
    dq 0xFFFFFBFFFFFBFFFF, 0xFFFBFFFFFBFFFFFB, 0xFBFFFFFBFFFFFBFF, 0xFFFBFBFFFDFBFFFE
    dq 0xFFFBFFFFFBFFF7FB, 0xFBFFFFFBFFFFFBFF, 0xFF11FBFF88FBFFCC, 0xFFFBFF77FBFF33FB
    dq 0xFBFFFFFBFFFFFBFF, 0xFB00FBFC88FBFECC, 0xFFFBFF77FBF733FB, 0xFBFFFFFBFFFFFBFF
    dq 0x0000000000FBECEE, 0xFFFBF7FF00000000, 0xFBFFFFFBFFFFFBFF, 0x0000000000FACEEE
    dq 0xFFFB7FFF00000000, 0xFBFFFFFBFFFFFBFF, 0x0000000000E8EEEE, 0xFFF3FFFF00000000
    dq 0xFBFFFFFBFFFFFBFF, 0xEEEE8AEEEEC8EEEE, 0xFF7BFFFF3BFFFF51, 0xFBFFFFFBFFFFFBFF
    dq 0xFFFFF7FFFFF7FFFF, 0xFFF7FFFFF7FFFFF7, 0xF7FFFFF7FFFFF7FF, 0xFFFBF7FFFDF7FFFE
    dq 0xFFF7FFFFF7FFF7F7, 0xF7FFFFF7FFFFF7FF, 0xFF01F7FF88F7FFCC, 0xFFF7FF77F7FF33F7
    dq 0xF7FFFFF7FFFFF7FF, 0xF800F7FC88F7FECC, 0xFFF7FF77F7F700F7, 0xF7FFFFF7FFFFF7FF
    dq 0x0000F7C8CCF7ECEE, 0xFF00000000000000, 0xF7FFFFF7FFFFF7FF, 0x0000F48CCCF6CEEE
    dq 0xFF00000000000000, 0xF7FFFFF7FFFFF7FF, 0x0000C0CCEEE4EEEE, 0xFF00000000000000
    dq 0xF7FFFFF7FFFFF7FF, 0xEEEEA4EEEEC6EEEE, 0xFF77FFFFB3EEEE16, 0xF7FFFFF7FFFFF7FF
    dq 0xFFFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFF, 0xFFFBFFFFFDFFFFFE
    dq 0xFFFFFFFFFFFFF7FF, 0xFFFFFFFFFFFFFFFF, 0xFF00FFFF88FFFFCC, 0xFFFFFF77FFFF03FF
    dq 0xFFFFFFFFFFFFFFFF, 0xF800FFFC88FFFCCC, 0xFFFFFF00FFF000FF, 0xFFFFFFFFFFFFFFFF
    dq 0x8088FFC8CCFFCCCC, 0x00000000000000FF, 0xFFFFFFFFFFFF0000, 0x0888FC8CCCFCCCCC
    dq 0x00000000000000F8, 0xFFFFFFFFFFFF0000, 0x88CEC8CCCECCCCCE, 0x0000000000000090
    dq 0xFFFFFFFFFFFF0000, 0xCCEE8CCCEECCCCEE, 0xFF77FEEE3FEEEE58, 0xFFFFFFFFFFFFFFFF
    dq 0xFFFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFF, 0xFFFBFFFFFDFFFFFE
    dq 0xFFFFFFFFFFFFF7FF, 0xFFFFFFFFFFFFFFFF, 0xFF00FFFF88FFFFCC, 0xFFFFFF07FFFF00FF
    dq 0xFFFFFFFFFFFFFFFF, 0xF800FFF888FFFCCC, 0x00FFF000FFF000FF, 0xFFFFFFFFFFFFFFFF
    dq 0x8088FF8888FFCCCC, 0x00000000FF0000FF, 0xFFFFFF0000000000, 0x0888F88888FCCCCC
    dq 0x00000000F10000F8, 0xFFFFFF0000000000, 0x888C88888CCCCCCC, 0x0000000031008C90
    dq 0xFFFFFF0000000000, 0x88CE8888CECCCCCE, 0xEE7FFEEEB188CE18, 0xFFFFFFFFFFFFFFFF
    dq 0xFFFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFF, 0xFFFBFFFFFDFFFFFE
    dq 0xFFFFFFFFFFFFF7FF, 0xFFFFFFFFFFFFFFFF, 0xFF00FFFF88FFFF88, 0x0FFFFF00FFFF00FF
    dq 0xFFFFFFFFFFFFFFFF, 0xF000FFF888FFF888, 0x00FFF000FFF000FF, 0xFFFFFFFFFF11FFF1
    dq 0x0000FF8888FF8888, 0x00FF1000FF0000FF, 0x0000000000000000, 0x0000F88888F88888
    dq 0x00F31000F10000F0, 0x0000000000000000, 0x0008888888C88888, 0x0073100831000810
    dq 0x0000000000000000, 0x008C88888CC8888C, 0xEE73108C31008C10, 0xFFFFFFFFFFFFFFFF
    dq 0xFFFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFF, 0xFFFBFFFFFDFFFFFE
    dq 0xFFFFFFFFFFFFF7FF, 0xFFFFFFFFFFFFFFFF, 0xFF00FFFF00FFFF88, 0x00FFFF00FFFF00FF
    dq 0xFFFFFFFFFF1FFFFF, 0xF000FFF000FFF888, 0x00FFF000FFF000FF, 0xFFFF33FFF311FFF1
    dq 0x0000FF0000FF8888, 0x00FF1000FF0000FF, 0x000000000000FF31, 0x0000F00000F88888
    dq 0x00F31000F10000F0, 0x000000000000F731, 0x0000800000C88888, 0x0073100031000010
    dq 0x000000000000F731, 0x0008800008C88888, 0x0873100831000810, 0xFFFFFFFFFFFFF731
    dq 0xFFFFEFFFFFEFFFFF, 0xFFEFFFFFEFFFFFEF, 0xEFFFFFEFFFFFEFFF, 0xFFFBEFFFFDEFFFFE
    dq 0xFFEFFFFFEFFFF7EF, 0xEFFFFFEFFFFFEFFF, 0xFF00EFFF00EFFF00, 0x77EFFF33EFFF11EF
    dq 0xEFFFFFEFFFFFEFFF, 0xF100EFF000EFFC00, 0x77EFF733EFF311EF, 0xEFFFFFEFFFFFEFFF
    dq 0x3000EF1000EFE000, 0xF7EF7773EF7331EF, 0xEFFFFFEFFFFFEFFF, 0x0222000000000000
    dq 0xFFE77777E73777E3, 0xEFFFFFEFFFFFEFFF, 0x2222000000000000, 0xFF67777763777720
    dq 0xEFFFFFEFFFFFEFFF, 0x66EE000000000000, 0xFF6777FF2777FF42, 0xEFFFFFEFFFFFEFFF
    dq 0xFFFFDFFFFFDFFFFF, 0xFFDFFFFFDFFFFFDF, 0xDFFFFFDFFFFFDFFF, 0xFFFBDFFFFDDFFFFE
    dq 0xFFDFFFFFDFFFF7DF, 0xDFFFFFDFFFFFDFFF, 0xFF00DFFF00DFFF88, 0x77DFFF33DFFF11DF
    dq 0xDFFFFFDFFFFFDFFF, 0xF100DFF800DFFC88, 0x77DFF733DFF311DF, 0xDFFFFFDFFFFFDFFF
    dq 0x3100DFD000DFECC8, 0xF7DFF773DF7331DF, 0xDFFFFFDFFFFFDFFF, 0x0000000000000000
    dq 0xFFDF7FFFD7377700, 0xDFFFFFDFFFFFDFFF, 0x0000000000000000, 0xFFD7FFFF53777700
    dq 0xDFFFFFDFFFFFDFFF, 0x0000000000000000, 0xFF5FFFFF97FFFF00, 0xDFFFFFDFFFFFDFFF
    dq 0xFFFFBFFFFFBFFFFF, 0xFFBFFFFFBFFFFFBF, 0xBFFFFFBFFFFFBFFF, 0xFFFBBFFFFDBFFFFE
    dq 0xFFBFFFFFBFFFF7BF, 0xBFFFFFBFFFFFBFFF, 0xFF00BFFF00BFFF88, 0x77BFFF33BFFF11BF
    dq 0xBFFFFFBFFFFFBFFF, 0xF100BFF800BFFC88, 0x77BFF733BFF311BF, 0xBFFFFFBFFFFFBFFF
    dq 0xB000BFC880BFECC8, 0xF7BFF773BF7331BF, 0xBFFFFFBFFFFFBFFF, 0x0000000000BECEEE
    dq 0xFFBF7FFF00000000, 0xBFFFFFBFFFFFBFFF, 0x0000000000ACEEEE, 0xFFB7FFFF00000000
    dq 0xBFFFFFBFFFFFBFFF, 0x00000000008EEEEE, 0xFF3FFFFF00000000, 0xBFFFFFBFFFFFBFFF
    dq 0xFFFF7FFFFF7FFFFF, 0xFF7FFFFF7FFFFF7F, 0x7FFFFF7FFFFF7FFF, 0xFFFB7FFFFD7FFFFE
    dq 0xFF7FFFFF7FFFF77F, 0x7FFFFF7FFFFF7FFF, 0xFF007FFF007FFF88, 0x777FFF337FFF007F
    dq 0x7FFFFF7FFFFF7FFF, 0xF0007FF8007FFC88, 0x777FF7337FF3007F, 0x7FFFFF7FFFFF7FFF
    dq 0x80007FC8807FECC8, 0xF77FF7737F70007F, 0x7FFFFF7FFFFF7FFF, 0x00007C8CCC7ECEEE
    dq 0xFF00000000000000, 0x7FFFFF7FFFFF7FFF, 0x000048CCCC6CEEEE, 0xFF00000000000000
    dq 0x7FFFFF7FFFFF7FFF, 0x00002CEEEE4EEEEE, 0xFF00000000000000, 0x7FFFFF7FFFFF7FFF
    dq 0xFFFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFF, 0xFFFBFFFFFDFFFFFE
    dq 0xFFFFFFFFFFFFF7FF, 0xFFFFFFFFFFFFFFFF, 0xFF00FFFF00FFFF88, 0x77FFFF00FFFF00FF
    dq 0xFFFFFFFFFFFFFFFF, 0xF000FFF800FFFC88, 0x77FFF700FFF000FF, 0xFFFFFFFFFFFFFFFF
    dq 0x8000FFC880FFCCC8, 0xF7FFF000FF0000FF, 0xFFFFFFFFFFFFFFFF, 0x0888FC8CCCFCCCCC
    dq 0x00000000000000F8, 0xFFFFFFFFFFFF0000, 0x8888C8CCCCCCCCCC, 0x0000000000000090
    dq 0xFFFFFFFFFFFF0000, 0xCCEE8CCCEECCCCEE, 0x0000000000000058, 0xFFFFFFFFFFFF0000
    dq 0xFFFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFF, 0xFFFBFFFFFDFFFFFE
    dq 0xFFFFFFFFFFFFF7FF, 0xFFFFFFFFFFFFFFFF, 0xFF00FFFF00FFFF88, 0x00FFFF00FFFF00FF
    dq 0xFFFFFFFFFFFFFFFF, 0xF000FFF800FFFC88, 0x00FFF000FFF000FF, 0xFFFFFFFFFFFFFFFF
    dq 0x8000FF8880FFCCC8, 0x00FF1000FF0000FF, 0xFFFFFFFFFFFFFFF1, 0x0888F88888FCCCCC
    dq 0x00000000F10000F8, 0xFFFFFF0000000000, 0x8888888888CCCCCC, 0x0000000031000090
    dq 0xFFFFFF0000000000, 0x88CE8888CECCCCCE, 0x00000000B188CE18, 0xFFFFFF0000000000
    dq 0xFFFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFF, 0xFFFBFFFFFDFFFFFE
    dq 0xFFFFFFFFFFFFF7FF, 0xFFFFFFFFFFFFFFFF, 0xFF00FFFF00FFFF88, 0x00FFFF00FFFF00FF
    dq 0xFFFFFFFFFF11FFFF, 0xF000FFF800FFF888, 0x00FFF000FFF000FF, 0xFFFFFFFFFF11FFF1
    dq 0x0000FF8880FF8888, 0x00FF1000FF0000FF, 0xFFFFFFFFF311FF31, 0x0000F88888F88888
    dq 0x00F31000F10000F0, 0x0000000000000000, 0x0000888888C88888, 0x0073100031000010
    dq 0x0000000000000000, 0x008C88888CC8888C, 0x0073108C31008C10, 0x0000000000000000
    dq 0xFFFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFF, 0xFFFBFFFFFDFFFFFE
    dq 0xFFFFFFFFFFFFF7FF, 0xFFFFFFFFFFFFFFFF, 0xFF00FFFF00FFFF88, 0x00FFFF00FFFF00FF
    dq 0xFFFF33FFFF11FFFF, 0xF000FFF000FFF888, 0x00FFF000FFF000FF, 0xFFFF33FFF311FFF1
    dq 0x0000FF0000FF8888, 0x00FF1000FF0000FF, 0xFFF733FF7311FF31, 0x0000F00000F88888
    dq 0x00F31000F10000F0, 0x000000000000F731, 0x0000800000C88888, 0x0073100031000010
    dq 0x000000000000F731, 0x0008800008C88888, 0x0873100831000810, 0x000000000000F731
    dq 0xFFFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFF, 0xFFFBFFFFFDFFFFFE
    dq 0xFFFFFFFFFFFFF7FF, 0xFFFFFFFFFFFFFFFF, 0xFF00FFFF00FFFF00, 0x33FFFF11FFFF00FF
    dq 0xFFFFFFFFFF77FFFF, 0xF000FFF000FFF000, 0x33FFF311FFF100FF, 0xFFFFFFFFFF77FFF7
    dq 0x1000FF0000FFC000, 0x33FF7311FF3100FF, 0xFFFFFFFFFF77FFF7, 0x0000F00000FE0000
    dq 0x73F77731F73300F3, 0xFFFFFFFFFFF7FFFF, 0x2222000000000000, 0xFF77777773777720
    dq 0xFFFFFFFFFFFFFFFF, 0x2222000000000000, 0xFF77777737777702, 0xFFFFFFFFFFFFFFFF
    dq 0xFFFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFF, 0xFFFBFFFFFDFFFFFE
    dq 0xFFFFFFFFFFFFF7FF, 0xFFFFFFFFFFFFFFFF, 0xFF00FFFF00FFFF00, 0x33FFFF11FFFF00FF
    dq 0xFFFFFFFFFF77FFFF, 0xF000FFF000FFF800, 0x33FFF311FFF100FF, 0xFFFFFFFFFF77FFF7
    dq 0x1000FF8000FFC800, 0x33FF7311FF3100FF, 0xFFFFFFFFFF77FFF7, 0x1000FD0000FECC80
    dq 0x73FF7731F73300F3, 0xFFFFFFFFFFF7FFFF, 0x0000000000000000, 0xFFF7FFFF73777700
    dq 0xFFFFFFFFFFFFFFFF, 0x0000000000000000, 0xFF7FFFFF37777700, 0xFFFFFFFFFFFFFFFF
    dq 0xFFFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFF, 0xFFFBFFFFFDFFFFFE
    dq 0xFFFFFFFFFFFFF7FF, 0xFFFFFFFFFFFFFFFF, 0xFF00FFFF00FFFF00, 0x33FFFF11FFFF00FF
    dq 0xFFFFFFFFFF77FFFF, 0xF000FFF000FFF800, 0x33FFF311FFF100FF, 0xFFFFFFFFFF77FFF7
    dq 0x1000FF8000FFC800, 0x33FF7311FF3100FF, 0xFFFFFFFFFF77FFF7, 0x0000FC8800FECC80
    dq 0x73FF7731F73300FB, 0xFFFFFFFFFFF7FFFF, 0x0000000000ECEEEE, 0xFFF7FFFF00000000
    dq 0xFFFFFFFFFFFFFFFF, 0x0000000000CEEEEE, 0xFF7FFFFF00000000, 0xFFFFFFFFFFFFFFFF
    dq 0xFFFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFF, 0xFFFBFFFFFDFFFFFE
    dq 0xFFFFFFFFFFFFF7FF, 0xFFFFFFFFFFFFFFFF, 0xFF00FFFF00FFFF00, 0x33FFFF11FFFF00FF
    dq 0xFFFFFFFFFF77FFFF, 0xF000FFF000FFF800, 0x33FFF311FFF000FF, 0xFFFFFFFFFF77FFF7
    dq 0x0000FF8000FFC800, 0x33FF7311FF3000FF, 0xFFFFFFFFFF77FFF7, 0x0000FC8800FECC80
    dq 0x73FF7731F70000F8, 0xFFFFFFFFFFF7FFFF, 0x0000C8CCCCECEEEE, 0xFF00000000000000
    dq 0xFFFFFFFFFFFFFFFF, 0x00008CCCCCCCEEEE, 0xFF00000000000000, 0xFFFFFFFFFFFFFFFF
    dq 0xFFFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFF, 0xFFFBFFFFFDFFFFFE
    dq 0xFFFFFFFFFFFFF7FF, 0xFFFFFFFFFFFFFFFF, 0xFF00FFFF00FFFF00, 0x33FFFF00FFFF00FF
    dq 0xFFFFFFFFFF77FFFF, 0xF000FFF000FFF800, 0x33FFF000FFF000FF, 0xFFFFFFFFFF77FFF7
    dq 0x0000FF8000FFC800, 0x33FF7000FF0000FF, 0xFFFFFFFFFF77FFF7, 0x0000FC8800FCCC80
    dq 0x73FF1000F10000F8, 0xFFFFFFFFFFF7FFFF, 0x8888C8CCCCCCCCCC, 0x0000000000000090
    dq 0xFFFFFFFFFFFF0000, 0x88888CCCCCCCCCCC, 0x0000000000000018, 0xFFFFFFFFFFFF0000
    dq 0xFFFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFF, 0xFFFBFFFFFDFFFFFE
    dq 0xFFFFFFFFFFFFF7FF, 0xFFFFFFFFFFFFFFFF, 0xFF00FFFF00FFFF00, 0x00FFFF00FFFF00FF
    dq 0xFFFFFFFFFF77FFFF, 0xF000FFF000FFF800, 0x00FFF000FFF000FF, 0xFFFFFFFFFF77FFF1
    dq 0x0000FF8000FFC800, 0x00FF1000FF0000FF, 0xFFFFFFFFFF77FFF1, 0x0000F88800FCCC80
    dq 0x00F31000F10000F8, 0xFFFFFFFFFFF7FF31, 0x8888888888CCCCCC, 0x0000000031000090
    dq 0xFFFFFF0000000000, 0x8888888888CCCCCC, 0x0000000031000018, 0xFFFFFF0000000000
    dq 0xFFFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFF, 0xFFFBFFFFFDFFFFFE
    dq 0xFFFFFFFFFFFFF7FF, 0xFFFFFFFFFFFFFFFF, 0xFF00FFFF00FFFF00, 0x00FFFF00FFFF00FF
    dq 0xFFFFFFFFFF11FFFF, 0xF000FFF000FFF800, 0x00FFF000FFF000FF, 0xFFFFFFFFF311FFF1
    dq 0x0000FF8000FF8800, 0x00FF1000FF0000FF, 0xFFFFFFFFF311FF31, 0x0000F88800F88880
    dq 0x00F31000F10000F0, 0xFFFFFFFF7311F731, 0x0000888888C88888, 0x0073100031000010
    dq 0x0000000000000000, 0x0000888888C88888, 0x0073100031000010, 0x0000000000000000
    dq 0xFFFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFF, 0xFFFFFFFFFFFFFFFF, 0xFFFBFFFFFDFFFFFE
    dq 0xFFFFFFFFFFFFF7FF, 0xFFFFFFFFFFFFFFFF, 0xFF00FFFF00FFFF00, 0x00FFFF00FFFF00FF
    dq 0xFFFF33FFFF11FFFF, 0xF000FFF000FFF800, 0x00FFF000FFF000FF, 0xFFF733FFF311FFF1
    dq 0x0000FF0000FF8800, 0x00FF1000FF0000FF, 0xFFF733FF7311FF31, 0x0000F00000F88880
    dq 0x00F31000F10000F0, 0xFFF733FF7311F731, 0x0000800000C88888, 0x0073100031000010
    dq 0x000000000000F731, 0x0000800000C88888, 0x0073100031000010, 0x000000000000F731
    dq 0xFFF3000000000000, 0xFFFFFFF7FFFFF3FF, 0xFFFFFFFFFFFFFFFF, 0xFF00000000000000
    dq 0x6EFFFF26FFFF02FF, 0xFFFFFFFFFFEEFFFF, 0xF000FFF000FFF000, 0x26FFF002FFF000FF
    dq 0xFFF7FFFFF36EFFF0, 0x0000FF0000FF0000, 0x26FF0002FF0000FF, 0xFF77FFFF336EFF10
    dq 0x0000F00000F00000, 0x26F10002F00000F0, 0xFF77FFF7336EF310, 0x0000000000800000
    dq 0x2631000210000000, 0xFF77FFF7336E7310, 0x0000000000800000, 0x2631000210000000
    dq 0xFF77FFF7336E7310, 0x0000000000800000, 0x2631000210000000, 0xFF77FFF7336E7310
    dq 0x0000000000000000, 0xFFFFFFF7FFFFF700, 0xFFFFFFFFFFFFFFFF, 0x0000000000000000
    dq 0x6EFFFF26FFFF0200, 0xFFFFFFFFFFEEFFFF, 0xF000FFF000FFF000, 0x26FFF002FFF000FF
    dq 0xFFF7FFFFF36EFFF0, 0x0000FF0000FF0000, 0x26FF0002FF0000FF, 0xFF77FFFF336EFF10
    dq 0x0000F00000F00000, 0x26F10002F00000F0, 0xFF77FFF7336EF310, 0x0000000000800000
    dq 0x2631000210000000, 0xFF77FFF7336E7310, 0x0000000000800000, 0x2631000210000000
    dq 0xFF77FFF7336E7310, 0x0000000000800000, 0x2631000210000000, 0xFF77FFF7336E7310
    dq 0x0000000000FFFFFE, 0xFFFFFFFF00000000, 0xFFFFFFFFFFFFFFFF, 0x0000000000FFFF84
    dq 0x6EFFFF2400000000, 0xFFFFFFFFFFEEFFFF, 0xF000FFF000FFF000, 0x26FFF000FFF000FF
    dq 0xFFF7FFFFF36EFFF0, 0x0000FF0000FF0000, 0x26FF0000FF0000FF, 0xFF77FFFF336EFF10
    dq 0x0000F00000F00000, 0x26F10000F00000F0, 0xFF77FFF7336EF310, 0x0000000000800000
    dq 0x2631000010000000, 0xFF77FFF7336E7310, 0x0000000000800000, 0x2631000010000000
    dq 0xFF77FFF7336E7310, 0x0000000000800000, 0x2631000010000000, 0xFF77FFF7336E7310
    dq 0x0000FFFFFCFFFFFC, 0xFF00000000000000, 0xFFFFFFFFFFFFFFFF, 0x0000FFFF08FFFF8C
    dq 0x4A00000000000000, 0xFFFFFFFFFFEEFFFF, 0xF000FFF000FFF008, 0x02FFF000FFF000FF
    dq 0xFFF7FFFFF36EFFF0, 0x0000FF0000FF0008, 0x02FF0000FF0000FF, 0xFF77FFFF336EFF10
    dq 0x0000F00000F00008, 0x02F10000F00000F0, 0xFF77FFF7336EF310, 0x0000000000800008
    dq 0x0231000010000000, 0xFF77FFF7336E7310, 0x0000000000800008, 0x0231000010000000
    dq 0xFF77FFF7336E7310, 0x0000000000800008, 0x0231000010000000, 0xFF77FFF7336E7310
    dq 0xFFF9FFFFF8FFFFFC, 0x00000000000000FF, 0xFFFFFFFFFFFF0000, 0xFF00FFFF08FFFF8C
    dq 0x00000000000000FF, 0xFFFFFFFFFFA60000, 0xF000FFF000FFF008, 0x00FFF000FFF000FF
    dq 0xFFF7FFFFF126FFF0, 0x0000FF0000FF0008, 0x00FF0000FF0000FF, 0xFF77FFFF3126FF10
    dq 0x0000F00000F00008, 0x00F10000F00000F0, 0xFF77FFF73126F310, 0x0000000000800008
    dq 0x0031000010000000, 0xFF77FFF731267310, 0x0000000000800008, 0x0031000010000000
    dq 0xFF77FFF731267310, 0x0000000000800008, 0x0031000010000000, 0xFF77FFF731267310
    dq 0xFFF1FFFFF8FFFFFC, 0x00000000FFFFF3FF, 0xFFFFFF0000000000, 0xFF00FFFF08FFFF8C
    dq 0x00000000FFFF00FF, 0xFFFF7F0000000000, 0xF000FFF000FFF008, 0x00FFF000FFF000FF
    dq 0xFFF37FFFF100FFF0, 0x0000FF0000FF0008, 0x00FF0000FF0000FF, 0xFF737FFF3100FF10
    dq 0x0000F00000F00008, 0x00F10000F00000F0, 0xFF737FF73100F310, 0x0000000000800008
    dq 0x0031000010000000, 0xFF737FF731007310, 0x0000000000800008, 0x0031000010000000
    dq 0xFF737FF731007310, 0x0000000000800008, 0x0031000010000000, 0xFF737FF731007310
    dq 0xFFF1FFFFF8FFFFFC, 0x00FFFFF7FFFFF3FF, 0x0000000000000000, 0xFF00FFFF08FFFF88
    dq 0x00FFFF00FFFF00FF, 0x0000000000000000, 0xF000FFF000FFF008, 0x00FFF000FFF000FF
    dq 0xFFF311FFF100FFF0, 0x0000FF0000FF0008, 0x00FF0000FF0000FF, 0xFF7311FF3100FF10
    dq 0x0000F00000F00008, 0x00F10000F00000F0, 0xFF7311F73100F310, 0x0000000000800008
    dq 0x0031000010000000, 0xFF7311F731007310, 0x0000000000800008, 0x0031000010000000
    dq 0xFF7311F731007310, 0x0000000000800008, 0x0031000010000000, 0xFF7311F731007310
    dq 0xFFF1FFFFF8FFFFFC, 0xFFFFFFF7FFFFF3FF, 0x000000000000FFFF, 0xFF00FFFF00FFFF00
    dq 0x00FFFF00FFFF00FF, 0x000000000000FFFF, 0xF000FFF000FFF000, 0x00FFF000FFF000FF
    dq 0xFFF311FFF100FFF0, 0x0000FF0000FF0000, 0x00FF0000FF0000FF, 0xFF7311FF3100FF10
    dq 0x0000F00000F00000, 0x00F10000F00000F0, 0xFF7311F73100F310, 0x0000000000800000
    dq 0x0031000010000000, 0xFF7311F731007310, 0x0000000000800000, 0x0031000010000000
    dq 0xFF7311F731007310, 0x0000000000800000, 0x0031000010000000, 0xFF7311F731007310
    dq 0xFFF2000000000000, 0xFEFFFFF6FFFFF2FF, 0xFFFFFEFFFFFEFFFF, 0xFF22000000000000
    dq 0x7EFFFF76FFFF22FF, 0xFFFFFEFFFFFEFFFF, 0xF000000000000000, 0x66FFF266FFF022FF
    dq 0xFFFFFEFFFFEEFFF6, 0x0000FF0000FF0000, 0x66FF0026FF0002FF, 0xFF7FFEFF37EEFF12
    dq 0x0000F00000F00000, 0x66F10026F00002F0, 0xFF7FFEF737EEF312, 0x0000000000800000
    dq 0x6631002610000200, 0xFF7FFEF737EE7312, 0x0000000000800000, 0x6631002610000200
    dq 0xFF7FFEF737EE7312, 0x000E00000E80000E, 0x6E31002E10000E00, 0xFF7FFEF737EE7312
    dq 0x0000000000000000, 0xFDFFFFF5FFFFF500, 0xFFFFFDFFFFFDFFFF, 0x0000000000000000
    dq 0xFDFFFF75FFFF7500, 0xFFFFFDFFFFFDFFFF, 0x0000000000000000, 0xECFFF264FFF02000
    dq 0xFFFFFDFFFFECFFF6, 0x0000FF0000FF0000, 0x6CFF0024FF0000FF, 0xFF7FFDFF37ECFF12
    dq 0x0000F00000F00000, 0x6CF10024F00000F0, 0xFF7FFDF737ECF312, 0x0000000000800000
    dq 0x6C31002410000000, 0xFF7FFDF737EC7312, 0x0000000000800000, 0x6C31002410000000
    dq 0xFF7FFDF737EC7312, 0x000C00000C80000C, 0x6C31002C10000C00, 0xFF7FFDF737EC7312
    dq 0x0000000000FFFFFA, 0xFBFFFFFB00000000, 0xFFFFFBFFFFFBFFFF, 0x0000000000FFFFEA
    dq 0xFBFFFFFB00000000, 0xFFFFFBFFFFFBFFFF, 0x0000000000FFF848, 0xEAFFF24200000000
    dq 0xFFFFFBFFFFEAFFF6, 0x0000FF0000FF0008, 0x6AFF0002FF0000FF, 0xFF7FFBFF37EAFF12
    dq 0x0000F00000F00008, 0x6AF10002F00000F0, 0xFF7FFBF737EAF312, 0x0000000000800008
    dq 0x6A31000210000000, 0xFF7FFBF737EA7312, 0x0000000000800008, 0x6A31000210000000
    dq 0xFF7FFBF737EA7312, 0x000A00000A80000A, 0x6A31000A10000A00, 0xFF7FFBF737EA7312
    dq 0x0000FFFFF4FFFFF4, 0xF700000000000000, 0xFFFFF7FFFFF7FFFF, 0x0000FFFFC4FFFFC4
    dq 0xF700000000000000, 0xFFFFF7FFFFF7FFFF, 0x0000FFF080FFF8C4, 0xA600000000000000
    dq 0xFFFFF7FFFFE6FFF4, 0x0000FF0000FF0084, 0x26FF0000FF0000FF, 0xFF7FF7FF37E6FF10
    dq 0x0000F00000F00084, 0x26F10000F00000F0, 0xFF7FF7F737E6F310, 0x0000000000800084
    dq 0x2631000010000000, 0xFF7FF7F737E67310, 0x0000000000800084, 0x2631000010000000
    dq 0xFF7FF7F737E67310, 0x0006000006800086, 0x2631000610000600, 0xFF7FF7F737E67310
    dq 0xFFF9FFFFF8FFFFFC, 0x00000000000000FF, 0xFFFFFFFFFFFF0000, 0xFF88FFFF88FFFFCC
    dq 0x00000000000000FF, 0xFFFFFFFFFFFF0000, 0xF000FFF088FFF8CC, 0x00000000000000FF
    dq 0xFFFFFFFFFB6E0000, 0x0000FF0008FF008C, 0x00FF0000FF0000FF, 0xFF7FFFFF336EFF10
    dq 0x0000F00008F0008C, 0x00F10000F00000F0, 0xFF7FFFF7336EF310, 0x000000000880008C
    dq 0x0031000010000000, 0xFF7FFFF7336E7310, 0x000000000880008C, 0x0031000010000000
    dq 0xFF7FFFF7336E7310, 0x000E00000E80008E, 0x0E31000E10000E00, 0xFF7FFFF7336E7310
    dq 0xFFF1FFFFF8FFFFFC, 0x00000000FFFFF3FF, 0xFFFFFF0000000000, 0xFF00FFFF88FFFF8C
    dq 0x00000000FFFF00FF, 0xFFFFFF0000000000, 0xF000FFF088FFF888, 0x00000000FFF000FF
    dq 0xFFF7FF0000000000, 0x0000FF0008FF0088, 0x00FF0000FF0000FF, 0xFF77FFFF3100FF10
    dq 0x0000F00008F00088, 0x00F10000F00000F0, 0xFF77FFF73100F310, 0x0000000008800088
    dq 0x0031000010000000, 0xFF77FFF731007310, 0x0000000008800088, 0x0031000010000000
    dq 0xFF77FFF731007310, 0x000C00000C80008C, 0x0E31000C10000C00, 0xFF77FFF7310E7310
    dq 0xFFF1FFFFF8FFFFFC, 0x00FFFFF7FFFFF3FF, 0x0000000000000000, 0xFF00FFFF08FFFF88
    dq 0x00FFFF00FFFF00FF, 0x0000000000000000, 0xF000FFF000FFF888, 0x00FFF000FFF000FF
    dq 0x0000000000000000, 0x0000FF0000FF0088, 0x00FF0000FF0000FF, 0xFF7311FF3100FF10
    dq 0x0000F00000F00088, 0x00F10000F00000F0, 0xFF7311F73100F310, 0x0000000000800088
    dq 0x0031000010000000, 0xFF7311F731007310, 0x0000000000800088, 0x0031000010000000
    dq 0xFF7311F731007310, 0x0008000008800088, 0x0831000810000800, 0xFF731FF7310E7310
    dq 0xFFF1FFFFF8FFFFFC, 0xFFFFFFF7FFFFF3FF, 0x000000000000FFFF, 0xFF00FFFF00FFFF00
    dq 0x00FFFF00FFFF00FF, 0x000000000000FFFF, 0xF000FFF000FFF000, 0x00FFF000FFF000FF
    dq 0x000000000000FFF0, 0x0000FF0000FF0000, 0x00FF0000FF0000FF, 0xFF7311FF3100FF10
    dq 0x0000F00000F00000, 0x00F10000F00000F0, 0xFF7311F73100F310, 0x0000000000800000
    dq 0x0031000010000000, 0xFF7311F731007310, 0x0000000000800000, 0x0031000010000000
    dq 0xFF7311F731007310, 0x0000000000800000, 0x0031000010000000, 0xFF731FF731007310
    dq 0xFFE3FFFFEBFFFFEF, 0xEFFFFFE7FFFFE3FF, 0xFFFFEFFFFFEFFFFF, 0xFF22000000000000
    dq 0x6FFFFF67FFFF22FF, 0xFFFFEFFFFFEFFFFF, 0xF222000000000000, 0x67FFF767FFF222FF
    dq 0xFFFFEFFFFFEFFFF7, 0x0002000000000000, 0x66FF2666FF0222FF, 0xFFFFEFFFFFEEFF76
    dq 0x0002F00000F0000E, 0x66F10266F00022F0, 0xFFFFEFF77FEEF336, 0x0002000000800000
    dq 0x6631026610002200, 0xFFFFEFF77FEE7336, 0x000600000680000E, 0x6631026610002600
    dq 0xFFFFEFF77FEE7336, 0x000E00000E80000E, 0x6E31026E10002E00, 0xFFFFEFF77FEE7336
    dq 0xFFD7FFFFDFFFFFDF, 0xDFFFFFD7FFFFD7FF, 0xFFFFDFFFFFDFFFFF, 0x0000000000000000
    dq 0xDFFFFF57FFFF5700, 0xFFFFDFFFFFDFFFFF, 0x0000000000000000, 0xDFFFF757FFF75700
    dq 0xFFFFDFFFFFDFFFFF, 0x0000000000000000, 0xCEFF2646FF020600, 0xFFFFDFFFFFCEFF7E
    dq 0x0000F0000EF00000, 0xCEF10246F00006F0, 0xFFFFDFF77FCEF336, 0x0000000000800000
    dq 0xCE31024610000600, 0xFFFFDFF77FCE7336, 0x000E00000E80000E, 0xCE31024E10000E00
    dq 0xFFFFDFF77FCE7336, 0x000E00000E80000E, 0xCE31024E10000E00, 0xFFFFDFF77FCE7336
    dq 0xFFBFFFFFBEFFFFBE, 0xBFFFFFBFFFFFBFFF, 0xFFFFBFFFFFBFFFFF, 0x0000000000FFFFAE
    dq 0xBFFFFFBF00000000, 0xFFFFBFFFFFBFFFFF, 0x0000000000FFFEAE, 0xBFFFFFBF00000000
    dq 0xFFFFBFFFFFBFFFFF, 0x0000000000FF848E, 0xAEFF242E00000000, 0xFFFFBFFFFFAEFF7E
    dq 0x000EF00000F0008E, 0xAEF1002EF00000F0, 0xFFFFBFF77FAEF336, 0x000000000080008E
    dq 0xAE31002E10000000, 0xFFFFBFF77FAE7336, 0x000E00000E80008E, 0xAE31002E10000E00
    dq 0xFFFFBFF77FAE7336, 0x000E00000E80008E, 0xAE31002E10000E00, 0xFFFFBFF77FAE7336
    dq 0xFF7DFFFF7CFFFF7C, 0x7FFFFF7FFFFF7FFF, 0xFFFF7FFFFF7FFFFF, 0x0000FFFF4CFFFF4C
    dq 0x7F00000000000000, 0xFFFF7FFFFF7FFFFF, 0x0000FFFC4CFFFC4C, 0x7F00000000000000
    dq 0xFFFF7FFFFF7FFFFF, 0x0000FF080CFF8C4C, 0x6E00000000000000, 0xFFFF7FFFFF6EFF5A
    dq 0x0000F0000CF0084C, 0x6EF10000F0000EF0, 0xFFFF7FF77F6EF312, 0x000000000C80084C
    dq 0x6E31000010000000, 0xFFFF7FF77F6E7312, 0x000E00000E80084E, 0x6E31000E10000E00
    dq 0xFFFF7FF77F6E7312, 0x000E00000E80084E, 0x6E31000E10000E00, 0xFFFF7FF77F6E7312
    dq 0xFFF9FFFFF8FFFFFC, 0xFFFFFFFFFFFFFBFF, 0xFFFFFFFFFFFFFFFF, 0xFF88FFFF88FFFFCC
    dq 0x00000000000000FF, 0xFFFFFFFFFFFF0000, 0xF888FFF888FFFCCC, 0x00000000000000FF
    dq 0xFFFFFFFFFFFF0000, 0x0008FF0888FF8CCC, 0x00000000000000FF, 0xFFFFFFFFB7EE0000
    dq 0x0008F00088F008CC, 0x00F1000EF00000F0, 0xFFFFFFF737EEF310, 0x00080000888008CC
    dq 0x0031000010000000, 0xFFFFFFF737EE7310, 0x000C00008C8008CC, 0x0E31000E10000C00
    dq 0xFFFFFFF737EE7310, 0x000E00008E8008CE, 0x0E31000E10000E00, 0xFFFFFFF737EE7310
    dq 0xFFF1FFFFF8FFFFFC, 0xFFFFFFF7FFFFF3FF, 0xFFFFFFFFFFFFFFFF, 0xFF00FFFF88FFFF8C
    dq 0x00000000FFFF00FF, 0xFFFFFF0000000000, 0xF000FFF888FFF888, 0x00000000FFF000FF
    dq 0xFFFFFF0000000000, 0x0000FF0888FF8888, 0x00000000FF0000FF, 0xFF7FFF0000000000
    dq 0x0000F00088F00888, 0x0EF10000F00000F0, 0xFF7FFFF73100F310, 0x0000000088800888
    dq 0x0031000010000000, 0xFF7FFFF731007310, 0x0008000088800888, 0x0E31000810000800
    dq 0xFF7FFFF7310E7310, 0x000C00008C80088C, 0x0E31000C10000C00, 0xFF7FFFF7310E7310
    dq 0xFFF1FFFFF8FFFFFC, 0xFFFFFFF7FFFFF3FF, 0xFFFFFFFFFFFFFFFF, 0xFF00FFFF08FFFF88
    dq 0x00FFFF00FFFF00FF, 0x0000000000000000, 0xF000FFF000FFF888, 0x00FFF000FFF000FF
    dq 0x0000000000000000, 0x0000FF0000FF8888, 0x00FF0000FF0000FF, 0x0000000000000000
    dq 0x0000F00000F00888, 0x00F10000F00000F0, 0xFF7311F7310EF310, 0x0000000000800888
    dq 0x0031000010000000, 0xFF7311F731007310, 0x0000000000800888, 0x0031000010000000
    dq 0xFF731FF7310E7310, 0x0008000008800888, 0x0831000810000800, 0xFF731FF7310E7310
    dq 0xFFF1FFFFF8FFFFFC, 0xFFFFFFF7FFFFF3FF, 0xFFFFFFFFFFFFFFFF, 0xFF00FFFF00FFFF00
    dq 0x00FFFF00FFFF00FF, 0x000000000000FFFF, 0xF000FFF000FFF000, 0x00FFF000FFF000FF
    dq 0x000000000000FFF0, 0x0000FF0000FF0000, 0x00FF0000FF0000FF, 0x000000000000FF10
    dq 0x0000F00000F00000, 0x00F10000F00000F0, 0xFF731FF73100F310, 0x0000000000800000
    dq 0x0031000010000000, 0xFF7313F731007310, 0x0000000000800000, 0x0031000010000000
    dq 0xFF731FF731007310, 0x0000000000800000, 0x0031000010000000, 0xFF731FF731007310
    dq 0xFEF1FFFEF8FFFEFC, 0xFFFFFEF7FFFEF3FF, 0xFFFEFFFFFEFFFFFE, 0xFE31FFFE38FFFEFC
    dq 0x7FFFFE77FFFE33FF, 0xFFFEFFFFFEFFFFFE, 0xF220000000000000, 0x77FFF677FFF222FF
    dq 0xFFFEFFFFFEFFFFF6, 0x2222000000000000, 0x77FF7677FF2222FF, 0xFFFEFFFFFEFFFF76
    dq 0x0022000000000000, 0x66F36666F02222F0, 0xFFFEFFFFFEFFF776, 0x00220000028000EE
    dq 0x6633266610022200, 0xFFFEFFFFFEFF7776, 0x002600000680000E, 0x6633266610022600
    dq 0xFFFEFFFFFEFF7776, 0x006E00006E8800EE, 0x6E33266E10026E00, 0xFFFEFFFFFEFF7776
    dq 0xFDF1FFFDF8FFFDFC, 0xFFFFFDF7FFFDF3FF, 0xFFFDFFFFFDFFFFFD, 0xFD71FFFDF8FFFDFC
    dq 0xFFFFFD77FFFD73FF, 0xFFFDFFFFFDFFFFFD, 0x0000000000000000, 0xFFFFF577FFF57300
    dq 0xFFFDFFFFFDFFFFFD, 0x0000000000000000, 0xFFFF7577FF757700, 0xFFFDFFFFFDFFFFFD
    dq 0x0000000000000000, 0xEEF36466F0206600, 0xFFFDFFFFFDFFF7FC, 0x00060000EE80000E
    dq 0xEE33246610006600, 0xFFFDFFFFFDFF777C, 0x000E00000E80000E, 0xEE33246E10006E00
    dq 0xFFFDFFFFFDFF777C, 0x00EE0000EE8800EE, 0xEE3324EE1000EE00, 0xFFFDFFFFFDFF777C
    dq 0xFBF1FFFBF8FFFBFC, 0xFFFFFBF7FFFBF3FF, 0xFFFBFFFFFBFFFFFB, 0xFBF1FFFBE8FFFBEC
    dq 0xFFFFFBF7FFFBF3FF, 0xFFFBFFFFFBFFFFFB, 0x0000000000FFFAEC, 0xFFFFFBF700000000
    dq 0xFFFBFFFFFBFFFFFB, 0x0000000000FFEAEE, 0xFFFFFBFF00000000, 0xFFFBFFFFFBFFFFFB
    dq 0x0000000000F848EE, 0xEEF342EE00000000, 0xFFFBFFFFFBFFF7FA, 0x00EE00000E8808EE
    dq 0xEE3302EE10000E00, 0xFFFBFFFFFBFF777A, 0x000E00000E8808EE, 0xEE3302EE10000E00
    dq 0xFFFBFFFFFBFF777A, 0x00EE0000EE8808EE, 0xEE3302EE1000EE00, 0xFFFBFFFFFBFF777A
    dq 0xF7F1FFF7F8FFF7FC, 0xFFFFF7F7FFF7F3FF, 0xFFF7FFFFF7FFFFF7, 0xF7C1FFF7C8FFF7CC
    dq 0xFFFFF7F7FFF7F3FF, 0xFFF7FFFFF7FFFFF7, 0x0000FFF4C8FFF4CC, 0xFF00000000000000
    dq 0xFFF7FFFFF7FFFFF7, 0x0000FFC4CCFFC4CC, 0xFF00000000000000, 0xFFF7FFFFF7FFFFF7
    dq 0x0000F080CCF8C4CC, 0xEE00000000000000, 0xFFF7FFFFF7FFF7B6, 0x000C0000CC8884CC
    dq 0xEE31000E1000EE00, 0xFFF7FFFFF7FF7736, 0x000E0000CE8884CE, 0xEE31000E10000E00
    dq 0xFFF7FFFFF7FF7736, 0x00EE0000EE8884EE, 0xEE3300EE1000EE00, 0xFFF7FFFFF7FF7736
    dq 0xFFF1FFFFF8FFFFFC, 0xFFFFFFF7FFFFF3FF, 0xFFFFFFFFFFFFFFFF, 0xFF80FFFF88FFFFCC
    dq 0xFFFFFFF7FFFF82FF, 0xFFFFFFFFFFFFFFFF, 0xF880FFF888FFFCCC, 0x00000000000000FF
    dq 0xFFFFFFFFFFFF0000, 0x8888FF8888FFCCCC, 0x00000000000000FF, 0xFFFFFFFFFFFF0000
    dq 0x0088F08888F8CCCC, 0x00000000000000F0, 0xFFFFFFFF7FFF0000, 0x0088000888888CCC
    dq 0x0E3100EE10000800, 0xFFFFFFFF7FFF7310, 0x008C00088C888CCC, 0x0E31000E10000C00
    dq 0xFFFFFFFF7FFF7310, 0x00CE0008CE888CCE, 0xEE3300EE1000CE00, 0xFFFFFFFF7FFF7730
    dq 0xFFF1FFFFF8FFFFFC, 0xFFFFFFF7FFFFF3FF, 0xFFFFFFFFFFFFFFFF, 0xFF00FFFF88FFFF8C
    dq 0xFFFFFF04FFFF00FF, 0xFFFFFFFFFFFFFFFF, 0xF000FFF888FFF888, 0x00000000FFF000FF
    dq 0xFFFFFF0000000000, 0x0000FF8888FF8888, 0x00000000FF0000FF, 0xFFFFFF0000000000
    dq 0x0000F08888F88888, 0x00000000F00000F0, 0xFFFFFF0000000000, 0x0000000888888888
    dq 0xEE31000010000000, 0xFFFFFFF7310E7310, 0x0008000888888888, 0x0E31000810000800
    dq 0xFFFFFFF7310E7310, 0x008C00088C88888C, 0xEE31008C10008C00, 0xFFFFFFFF71EE7710
    dq 0xFFF1FFFFF8FFFFFC, 0xFFFFFFF7FFFFF3FF, 0xFFFFFFFFFFFFFFFF, 0xFF00FFFF08FFFF88
    dq 0x08FFFF00FFFF00FF, 0xFFFFFFFFFFFFFFFF, 0xF000FFF000FFF888, 0x00FFF000FFF000FF
    dq 0x0000000000000000, 0x0000FF0000FF8888, 0x00FF0000FF0000FF, 0x0000000000000000
    dq 0x0000F00000F88888, 0x00F10000F00000F0, 0x0000000000000000, 0x0000000000888888
    dq 0x0031000010000000, 0xFF731FF731EE7310, 0x0000000000888888, 0x0031000010000000
    dq 0xFF731FF7310E7310, 0x0008000008888888, 0x0831000810000800, 0xFFF3FFFF31EE7310
    dq 0xFFF1FFFFF8FFFFFC, 0xFFFFFFF7FFFFF3FF, 0xFFFFFFFFFFFFFFFF, 0xFF00FFFF00FFFF00
    dq 0x00FFFF00FFFF00FF, 0xFFFFFFFFFF00FFFF, 0xF000FFF000FFF000, 0x00FFF000FFF000FF
    dq 0x000000000000FFF0, 0x0000FF0000FF0000, 0x00FF0000FF0000FF, 0x000000000000FF10
    dq 0x0000F00000F00000, 0x00F10000F00000F0, 0x000000000000F310, 0x0000000000800000
    dq 0x0031000010000000, 0xFF73FFF731007310, 0x0000000000800000, 0x0031000010000000
    dq 0xFF733FF731007310, 0x0000000000800000, 0x0031000010000000, 0xFF73FFF731007310
    dq 0xEFF1FFEFF8FFEFFC, 0xFFFFEFF7FFEFF3FF, 0xFFEFFFFFEFFFFFEF, 0xEF11FFEF08FFEF0C
    dq 0x7FFFEF77FFEF33FF, 0xFFEFFFFFEFFFFFEF, 0xE300FFE300FFEF0C, 0x77FFE777FFE322FF
    dq 0xFFEFFFFFEFFFFFE7, 0x2200000000000000, 0x77FF6777FF2222FF, 0xFFEFFFFFEFFFFF67
    dq 0x2222000000000000, 0x77F76777F22222F2, 0xFFEFFFFFEFFFF767, 0x0222000000000000
    dq 0x7737666612222200, 0xFFEFFFFFEFFF7767, 0x02260000268C0EEE, 0x7737666612222600
    dq 0xFFEFFFFFEFFF7767, 0x026E00006E8C00EE, 0x7F37666E12226E00, 0xFFEFFFFFEFFF7767
    dq 0xDFF1FFDFF8FFDFFC, 0xFFFFDFF7FFDFF3FF, 0xFFDFFFFFDFFFFFDF, 0xDF11FFDF08FFDFCC
    dq 0xFFFFDF77FFDF33FF, 0xFFDFFFFFDFFFFFDF, 0xD711FFDF08FFDFCC, 0xFFFFD777FFD733FF
    dq 0xFFDFFFFFDFFFFFDF, 0x0000000000000000, 0xFFFF5777FF573300, 0xFFDFFFFFDFFFFFDF
    dq 0x0000000000000000, 0xFFF75777F7577700, 0xFFDFFFFFDFFFFFDF, 0x0000000000000000
    dq 0xFF37466612066600, 0xFFDFFFFFDFFF7FDF, 0x006E080EEE8C80EE, 0xFF37466E12066E00
    dq 0xFFDFFFFFDFFF7FDF, 0x00EE0800EE8C80EE, 0xFF3746EE1206EE00, 0xFFDFFFFFDFFF7FDF
    dq 0xBFF1FFBFF8FFBFFC, 0xFFFFBFF7FFBFF3FF, 0xFFBFFFFFBFFFFFBF, 0xBF01FFBF88FFBFCC
    dq 0xFFFFBF77FFBF33FF, 0xFFBFFFFFBFFFFFBF, 0xBF01FFBE88FFBECC, 0xFFFFBF77FFBF33FF
    dq 0xFFBFFFFFBFFFFFBF, 0x0000000000FFAECC, 0xFFFFBF7700000000, 0xFFBFFFFFBFFFFFBF
    dq 0x0000000000FEAEEE, 0xFFFFBFFF00000000, 0xFFBFFFFFBFFFFFBF, 0x00000000008C8EEE
    dq 0xFF372EEE00000000, 0xFFBFFFFFBFFF7FBF, 0x0EEE0800EE8C8EEE, 0xFF372EEE1200EE00
    dq 0xFFBFFFFFBFFF7FBF, 0x00EE0800EE8C8EEE, 0xFF372EEE1200EE00, 0xFFBFFFFFBFFF7FBF
    dq 0x7FF1FF7FF8FF7FFC, 0xFFFF7FF7FF7FF3FF, 0xFF7FFFFF7FFFFF7F, 0x7F01FF7F88FF7FCC
    dq 0xFFFF7F77FF7F03FF, 0xFF7FFFFF7FFFFF7F, 0x7C00FF7C88FF7CCC, 0xFFFF7F77FF7F03FF
    dq 0xFF7FFFFF7FFFFF7F, 0x0000FF4C88FF4CCC, 0xFF00000000000000, 0xFF7FFFFF7FFFFF7F
    dq 0x0000FC4CCCFC4CCC, 0xFF00000000000000, 0xFF7FFFFF7FFFFF7F, 0x0000080CCC8C4CCC
    dq 0xFF00000000000000, 0xFF7FFFFF7FFF7F7F, 0x00CE080CCE8C4CCE, 0xFF3720EE120EEE00
    dq 0xFF7FFFFF7FFF7F7F, 0x00EE080CEE8C4CEE, 0xFF3720EE1200EE00, 0xFF7FFFFF7FFF7F7F
    dq 0xFFF1FFFFF8FFFFFC, 0xFFFFFFF7FFFFF3FF, 0xFFFFFFFFFFFFFFFF, 0xFF00FFFF88FFFFCC
    dq 0xFFFFFF07FFFF02FF, 0xFFFFFFFFFFFFFFFF, 0xF800FFF888FFFCCC, 0xFFFFFF07FFF800FF
    dq 0xFFFFFFFFFFFFFFFF, 0x8800FF8888FFCCCC, 0x00000000000000FF, 0xFFFFFFFFFFFF0000
    dq 0x8888F88888FCCCCC, 0x00000000000000F8, 0xFFFFFFFFFFFF0000, 0x08880888888CCCCC
    dq 0x0000000000000000, 0xFFFFFFFFFFFF0000, 0x088C08888C8CCCCC, 0xEE370EEE10008C00
    dq 0xFFFFFFFFFFFF7F70, 0x08CE0888CE8CCCCE, 0xEE3700EE1000CE00, 0xFFFFFFFFFFFF7F70
    dq 0xFFF1FFFFF8FFFFFC, 0xFFFFFFF7FFFFF3FF, 0xFFFFFFFFFFFFFFFF, 0xFF00FFFF88FFFF8C
    dq 0x0FFFFF04FFFF00FF, 0xFFFFFFFFFFFFFFFF, 0xF000FFF888FFF888, 0x0FFFF000FFF000FF
    dq 0xFFFFFFFFFFFFFFFF, 0x0000FF8888FF8888, 0x00000000FF0000FF, 0xFFFFFF0000000000
    dq 0x0000F88888F88888, 0x00000000F00000F0, 0xFFFFFF0000000000, 0x0000088888888888
    dq 0x0000000010000000, 0xFFFFFF0000000000, 0x0008088888888888, 0xEE31000810000800
    dq 0xFFFFFFFFF1EE7F1E, 0x008C08888C88888C, 0xEE31008C10008C00, 0xFFFFFFFFF1EE7F10
    dq 0xFFF1FFFFF8FFFFFC, 0xFFFFFFF7FFFFF3FF, 0xFFFFFFFFFFFFFFFF, 0xFF00FFFF08FFFF88
    dq 0x08FFFF00FFFF00FF, 0xFFFFFFFFFF0FFFFF, 0xF000FFF000FFF888, 0x00FFF000FFF000FF
    dq 0xFFFFFFFFFF0FFFF0, 0x0000FF0000FF8888, 0x00FF0000FF0000FF, 0x0000000000000000
    dq 0x0000F00000F88888, 0x00F10000F00000F0, 0x0000000000000000, 0x0000000000888888
    dq 0x0031000010000000, 0x0000000000000000, 0x0000000000888888, 0x0031000010000000
    dq 0xFFF3FFFF3FEE7310, 0x0008000008888888, 0x0831000810000800, 0xFFF3FFFF33EE7310
    dq 0xFFF1FFFFF8FFFFFC, 0xFFFFFFF7FFFFF3FF, 0xFFFFFFFFFFFFFFFF, 0xFF00FFFF00FFFF00
    dq 0x00FFFF00FFFF00FF, 0xFFFF1FFFFF00FFFF, 0xF000FFF000FFF000, 0x00FFF000FFF000FF
    dq 0xFFFF1FFFF100FFF0, 0x0000FF0000FF0000, 0x00FF0000FF0000FF, 0x000000000000FF10
    dq 0x0000F00000F00000, 0x00F10000F00000F0, 0x000000000000F310, 0x0000000000800000
    dq 0x0031000010000000, 0x0000000000007310, 0x0000000000800000, 0x0031000010000000
    dq 0xFF7FFFF731007310, 0x0000000000800000, 0x0031000010000000, 0xFF77FFF731007310
    dq 0xFFF1FEFFF8FEFFFC, 0xFFFEFFF7FEFFF3FE, 0xFEFFFFFEFFFFFEFF, 0xFF01FEFF08FEFF0C
    dq 0x7FFEFF37FEFF13FE, 0xFEFFFFFEFFFFFEFF, 0xF100FEF000FEF000, 0x77FEF733FEF300FE
    dq 0xFEFFFFFEFFFFFEF7, 0x3000FE3000FEF000, 0x77FE7733FE3200FE, 0xFEFFFFFEFFFFFE77
    dq 0x2000000000000000, 0x77F67773F22220F2, 0xFEFFFFFEFFFFF677, 0x2222000000000000
    dq 0x7776777732222232, 0xFEFFFFFEFFFF7677, 0x2222000000000000, 0x7776777732222212
    dq 0xFEFFFFFEFFFF7677, 0x226E20226E8EEEEE, 0x7F76777F32226E12, 0xFEFFFFFEFFFF7677
    dq 0xFFF1FDFFF8FDFFFC, 0xFFFDFFF7FDFFF3FD, 0xFDFFFFFDFFFFFDFF, 0xFF01FDFF08FDFF8C
    dq 0x7FFDFF37FDFF13FD, 0xFDFFFFFDFFFFFDFF, 0xF100FDF000FDFC00, 0x77FDF733FDF311FD
    dq 0xFDFFFFFDFFFFFDFF, 0x7100FDF000FDFC00, 0x77FD7733FD7311FD, 0xFDFFFFFDFFFFFDFF
    dq 0x0000000000000000, 0xF7F57773F5733100, 0xFDFFFFFDFFFFFDFF, 0x0000000000000000
    dq 0xFF75777775777700, 0xFDFFFFFDFFFFFDFF, 0x0000000000000000, 0xFF75777735666600
    dq 0xFDFFFFFDFFFFFDFF, 0x66EE8CEEEEA8EEEE, 0xFF7577FF3566EE70, 0xFDFFFFFDFFFFFDFF
    dq 0xFFF1FBFFF8FBFFFC, 0xFFFBFFF7FBFFF3FB, 0xFBFFFFFBFFFFFBFF, 0xFF01FBFF08FBFF8C
    dq 0x7FFBFF37FBFF13FB, 0xFBFFFFFBFFFFFBFF, 0xF000FBF800FBFC88, 0x77FBF733FBF300FB
    dq 0xFBFFFFFBFFFFFBFF, 0xF000FBE800FBEC88, 0x77FBF733FBF300FB, 0xFBFFFFFBFFFFFBFF
    dq 0x0000000000FAECC8, 0xF7FBF77300000000, 0xFBFFFFFBFFFFFBFF, 0x0000000000EAEEEE
    dq 0xFFFBFFFF00000000, 0xFBFFFFFBFFFFFBFF, 0x0000000000C8EEEE, 0xFF7BFFFF00000000
    dq 0xFBFFFFFBFFFFFBFF, 0xEEEEE0EEEECAEEEE, 0xFF7BFFFFF1EEEE1A, 0xFBFFFFFBFFFFFBFF
    dq 0xFFF1F7FFF8F7FFFC, 0xFFF7FFF7F7FFF3F7, 0xF7FFFFF7FFFFF7FF, 0xFF01F7FF08F7FF8C
    dq 0x7FF7FF37F7FF03F7, 0xF7FFFFF7FFFFF7FF, 0xF000F7F800F7FC88, 0x77F7F700F7F000F7
    dq 0xF7FFFFF7FFFFF7FF, 0xC000F7C800F7CC88, 0x77F7F700F7F000F7, 0xF7FFFFF7FFFFF7FF
    dq 0x0000F4C880F4CCC8, 0xF700000000000000, 0xF7FFFFF7FFFFF7FF, 0x0000C4CCCCC4CCCC
    dq 0xFF00000000000000, 0xF7FFFFF7FFFFF7FF, 0x000084CCCCC4CCCC, 0xFF00000000000000
    dq 0xF7FFFFF7FFFFF7FF, 0xCCEE84CCEEC4CCEE, 0xFFF3EEEE36EEEEC0, 0xF7FFFFF7FFFFF7FF
    dq 0xFFF1FFFFF8FFFFFC, 0xFFFFFFF7FFFFF3FF, 0xFFFFFFFFFFFFFFFF, 0xFF00FFFF08FFFF8C
    dq 0x7FFFFF07FFFF02FF, 0xFFFFFFFFFFFFFFFF, 0xF000FFF800FFFC88, 0x00FFF000FFF000FF
    dq 0xFFFFFFFFFFFFFFFF, 0x8000FF8800FFCC88, 0x00FFF000FF8000FF, 0xFFFFFFFFFFFFFFFF
    dq 0x8000F88880FCCCC8, 0x00000000000000F8, 0xFFFFFFFFFFFF0000, 0x8888888888CCCCCC
    dq 0x0000000000000088, 0xFFFFFFFFFFFF0000, 0x8888888888CCCCCC, 0x0000000000000008
    dq 0xFFFFFFFFFFFF0000, 0x88CE8888CECCCCCE, 0xEE7FEEEE9088CE08, 0xFFFFFFFFFFFFF7FE
    dq 0xFFF1FFFFF8FFFFFC, 0xFFFFFFF7FFFFF3FF, 0xFFFFFFFFFFFFFFFF, 0xFF00FFFF08FFFF8C
    dq 0x0FFFFF04FFFF00FF, 0xFFFFFFFFFFFFFFFF, 0xF000FFF800FFF888, 0x00FFF000FFF000FF
    dq 0xFFFFFFFFFF00FFF0, 0x0000FF8800FF8888, 0x00FF0000FF0000FF, 0xFFFFFFFFFF00FFF0
    dq 0x0000F88880F88888, 0x00000000F00000F0, 0xFFFFFF0000000000, 0x0000888888888888
    dq 0x0000000010000000, 0xFFFFFF0000000000, 0x0000888888888888, 0x0000000010000000
    dq 0xFFFFFF0000000000, 0x008C88888C88888C, 0xEE31008C10008C00, 0xFFFFFFFFFFEEFFFE
    dq 0xFFF1FFFFF8FFFFFC, 0xFFFFFFF7FFFFF3FF, 0xFFFFFFFFFFFFFFFF, 0xFF00FFFF08FFFF88
    dq 0x08FFFF00FFFF00FF, 0xFFFFFFFFFF0FFFFF, 0xF000FFF000FFF888, 0x00FFF000FFF000FF
    dq 0xFFFF11FFF100FFF0, 0x0000FF0000FF8888, 0x00FF0000FF0000FF, 0xFFFF11FFF100FF10
    dq 0x0000F00000F88888, 0x00F10000F00000F0, 0x0000000000000000, 0x0000000000888888
    dq 0x0031000010000000, 0x0000000000000000, 0x0000000000888888, 0x0031000010000000
    dq 0x0000000000000000, 0x0008000008888888, 0x0831000810000800, 0xFFFFFFFFFFEE7310
    dq 0xFFF1FFFFF8FFFFFC, 0xFFFFFFF7FFFFF3FF, 0xFFFFFFFFFFFFFFFF, 0xFF00FFFF00FFFF00
    dq 0x00FFFF00FFFF00FF, 0xFFFF1FFFFF00FFFF, 0xF000FFF000FFF000, 0x00FFF000FFF000FF
    dq 0xFFF311FFF100FFF0, 0x0000FF0000FF0000, 0x00FF0000FF0000FF, 0xFFF311FF3100FF10
    dq 0x0000F00000F00000, 0x00F10000F00000F0, 0x000000000000F310, 0x0000000000800000
    dq 0x0031000010000000, 0x0000000000007310, 0x0000000000800000, 0x0031000010000000
    dq 0x0000000000007310, 0x0000000000800000, 0x0031000010000000, 0xFFFFFFF731007310
    dq 0xFFF1EFFFF8EFFFFC, 0xFFEFFFF7EFFFF3EF, 0xEFFFFFEFFFFFEFFF, 0xFF00EFFF00EFFF00
    dq 0x33EFFF11EFFF00EF, 0xEFFFFFEFFF77EFFF, 0xF000EFF000EFF000, 0x33EFF311EFF100EF
    dq 0xEFFFFFEFFF77EFF7, 0x1000EF0000EF0000, 0x33EF7311EF3000EF, 0xEFFFFFEFFF77EF77
    dq 0x0000E30000EF0000, 0x33E77311E32000E3, 0xEFFFFFEFFF77E777, 0x0000000000000000
    dq 0x7367773122220022, 0xEFFFFFEFFFF76777, 0x2222000000000000, 0x7767777722222222
    dq 0xEFFFFFEFFFFF6777, 0x2222000000000000, 0x7767777722222222, 0xEFFFFFEFFFFF6777
    dq 0xFFF1DFFFF8DFFFFC, 0xFFDFFFF7DFFFF3DF, 0xDFFFFFDFFFFFDFFF, 0xFF00DFFF00DFFF00
    dq 0x33DFFF11DFFF00DF, 0xDFFFFFDFFF77DFFF, 0xF000DFF000DFF800, 0x33DFF311DFF100DF
    dq 0xDFFFFFDFFF77DFF7, 0x1000DF0000DFC000, 0x33DF7311DF3100DF, 0xDFFFFFDFFF77DFF7
    dq 0x1000DF0000DFC000, 0x33D77311D73100D7, 0xDFFFFFDFFF77DFF7, 0x0000000000000000
    dq 0x7357773157330000, 0xDFFFFFDFFFF7DFFF, 0x0000000000000000, 0xFF57777757777700
    dq 0xDFFFFFDFFFFFDFFF, 0x0000000000000000, 0xFF57777757777700, 0xDFFFFFDFFFFFDFFF
    dq 0xFFF1BFFFF8BFFFFC, 0xFFBFFFF7BFFFF3BF, 0xBFFFFFBFFFFFBFFF, 0xFF00BFFF00BFFF00
    dq 0x33BFFF11BFFF00BF, 0xBFFFFFBFFF77BFFF, 0xF000BFF000BFF800, 0x33BFF311BFF100BF
    dq 0xBFFFFFBFFF77BFF7, 0x0000BF8000BFC800, 0x33BF7311BF3000BF, 0xBFFFFFBFFF77BFF7
    dq 0x0000BE8000BEC800, 0x33BF7311BF3000BF, 0xBFFFFFBFFF77BFF7, 0x0000000000AECC80
    dq 0x73BF773100000000, 0xBFFFFFBFFFF7BFFF, 0x0000000000AEEEEE, 0xFFBFFFFF00000000
    dq 0xBFFFFFBFFFFFBFFF, 0x0000000000ACEEEE, 0xFFBFFFFF00000000, 0xBFFFFFBFFFFFBFFF
    dq 0xFFF17FFFF87FFFFC, 0xFF7FFFF77FFFF37F, 0x7FFFFF7FFFFF7FFF, 0xFF007FFF007FFF00
    dq 0x337FFF007FFF007F, 0x7FFFFF7FFF777FFF, 0xF0007FF0007FF800, 0x337FF3007FF0007F
    dq 0x7FFFFF7FFF777FF7, 0x00007F80007FC800, 0x337F70007F00007F, 0x7FFFFF7FFF777FF7
    dq 0x00007C80007CC800, 0x337F70007F00007C, 0x7FFFFF7FFF777FF7, 0x00004C88004CCC80
    dq 0x7300000000000000, 0x7FFFFF7FFFF77FFF, 0x00004CCCCC4CCCCC, 0xFF00000000000000
    dq 0x7FFFFF7FFFFF7FFF, 0x00004CCCCC4CCCCC, 0xFF00000000000000, 0x7FFFFF7FFFFF7FFF
    dq 0xFFF1FFFFF8FFFFFC, 0xFFFFFFF7FFFFF3FF, 0xFFFFFFFFFFFFFFFF, 0xFF00FFFF00FFFF00
    dq 0x00FFFF00FFFF00FF, 0xFFFFFFFFFF77FFFF, 0xF000FFF000FFF800, 0x00FFF000FFF000FF
    dq 0xFFFFFFFFFF77FFF7, 0x0000FF8000FFC800, 0x00FF0000FF0000FF, 0xFFFFFFFFFF77FFF0
    dq 0x0000F88000FCC800, 0x00FF0000F80000F8, 0xFFFFFFFFFF77FFF0, 0x0000888800CCCC80
    dq 0x0000000000000088, 0xFFFFFFFFFFF70000, 0x8888888888CCCCCC, 0x0000000000000088
    dq 0xFFFFFFFFFFFF0000, 0x8888888888CCCCCC, 0x0000000000000088, 0xFFFFFFFFFFFF0000
    dq 0xFFF1FFFFF8FFFFFC, 0xFFFFFFF7FFFFF3FF, 0xFFFFFFFFFFFFFFFF, 0xFF00FFFF00FFFF00
    dq 0x00FFFF00FFFF00FF, 0xFFFFFFFFFF00FFFF, 0xF000FFF000FFF800, 0x00FFF000FFF000FF
    dq 0xFFFFFFFFFF00FFF0, 0x0000FF8000FF8800, 0x00FF0000FF0000FF, 0xFFFFFFFFF100FF10
    dq 0x0000F88000F88800, 0x00F10000F00000F0, 0xFFFFFFFFF100FF10, 0x0000888800888880
    dq 0x0000000010000000, 0xFFFFFF0000000000, 0x0000888888888888, 0x0000000010000000
    dq 0xFFFFFF0000000000, 0x0000888888888888, 0x0000000010000000, 0xFFFFFF0000000000
    dq 0xFFF1FFFFF8FFFFFC, 0xFFFFFFF7FFFFF3FF, 0xFFFFFFFFFFFFFFFF, 0xFF00FFFF00FFFF00
    dq 0x00FFFF00FFFF00FF, 0xFFFF11FFFF00FFFF, 0xF000FFF000FFF800, 0x00FFF000FFF000FF
    dq 0xFFFF11FFF100FFF0, 0x0000FF0000FF8800, 0x00FF0000FF0000FF, 0xFFF311FF3100FF10
    dq 0x0000F00000F88800, 0x00F10000F00000F0, 0xFFF311FF3100F310, 0x0000000000888880
    dq 0x0031000010000000, 0x0000000000000000, 0x0000000000888888, 0x0031000010000000
    dq 0x0000000000000000, 0x0000000000888888, 0x0031000010000000, 0x0000000000000000
    dq 0xFFF1FFFFF8FFFFFC, 0xFFFFFFF7FFFFF3FF, 0xFFFFFFFFFFFFFFFF, 0xFF00FFFF00FFFF00
    dq 0x00FFFF00FFFF00FF, 0xFFFF11FFFF00FFFF, 0xF000FFF000FFF000, 0x00FFF000FFF000FF
    dq 0xFFF311FFF100FFF0, 0x0000FF0000FF0000, 0x00FF0000FF0000FF, 0xFF7311FF3100FF10
    dq 0x0000F00000F00000, 0x00F10000F00000F0, 0xFF7311F73100F310, 0x0000000000800000
    dq 0x0031000010000000, 0x0000000000007310, 0x0000000000800000, 0x0031000010000000
    dq 0x0000000000007310, 0x0000000000800000, 0x0031000010000000, 0x0000000000007310
    dq 0xFFF1FFFFF8FFFFFC, 0xFFFFFFF7FFFFF3FF, 0xFFFFFFFFFFFFFFFF, 0xFF00FFFF00FFFF00
    dq 0x11FFFF00FFFF00FF, 0xFFFF77FFFF33FFFF, 0xF000FFF000FFF000, 0x11FFF100FFF000FF
    dq 0xFFFF77FFF733FFF3, 0x0000FF0000FF0000, 0x11FF3100FF1000FF, 0xFFFF77FFF733FF73
    dq 0x0000F00000F00000, 0x11F73100F30000F0, 0xFFFF77FFF733F773, 0x0000200000F00000
    dq 0x1177310022000020, 0xFFFF77FFF7337773, 0x0000000000000000, 0x3177730022200020
    dq 0xFFFFF7FFFF737777, 0x2222000000000000, 0x7777777722222222, 0xFFFFFFFFFFFF7777
    dq 0xFFF1FFFFF8FFFFFC, 0xFFFFFFF7FFFFF3FF, 0xFFFFFFFFFFFFFFFF, 0xFF00FFFF00FFFF00
    dq 0x11FFFF00FFFF00FF, 0xFFFF77FFFF33FFFF, 0xF000FFF000FFF000, 0x11FFF100FFF000FF
    dq 0xFFFF77FFF733FFF3, 0x0000FF0000FF8000, 0x11FF3100FF1000FF, 0xFFFF77FFF733FF73
    dq 0x0000F00000FC0000, 0x11F73100F31000F1, 0xFFFF77FFF733FF73, 0x0000F00000FC0000
    dq 0x1177310073100071, 0xFFFF77FFF733FF73, 0x0000000000000000, 0x3177730073300000
    dq 0xFFFFF7FFFF73FFF7, 0x0000000000000000, 0xFF77777777777700, 0xFFFFFFFFFFFFFFFF
    dq 0xFFF1FFFFF8FFFFFC, 0xFFFFFFF7FFFFF3FF, 0xFFFFFFFFFFFFFFFF, 0xFF00FFFF00FFFF00
    dq 0x11FFFF00FFFF00FF, 0xFFFF77FFFF33FFFF, 0xF000FFF000FFF000, 0x11FFF100FFF000FF
    dq 0xFFFF77FFF733FFF3, 0x0000FF0000FF8000, 0x11FF3100FF1000FF, 0xFFFF77FFF733FF73
    dq 0x0000F80000FC8000, 0x11F73100F30000F0, 0xFFFF77FFF733FF73, 0x0000E80000EC8000
    dq 0x11F73100F30000F0, 0xFFFF77FFF733FF73, 0x0000000000ECC800, 0x31F7730000000000
    dq 0xFFFFF7FFFF73FFF7, 0x0000000000ECEEEE, 0xFFFFFFFF00000000, 0xFFFFFFFFFFFFFFFF
    dq 0xFFF1FFFFF8FFFFFC, 0xFFFFFFF7FFFFF3FF, 0xFFFFFFFFFFFFFFFF, 0xFF00FFFF00FFFF00
    dq 0x11FFFF00FFFF00FF, 0xFFFF77FFFF33FFFF, 0xF000FFF000FFF000, 0x11FFF000FFF000FF
    dq 0xFFFF77FFF733FFF3, 0x0000FF0000FF8000, 0x11FF3000FF0000FF, 0xFFFF77FFF733FF73
    dq 0x0000F80000FC8000, 0x11F70000F00000F0, 0xFFFF77FFF733FF73, 0x0000C80000CC8000
    dq 0x11F70000F00000C0, 0xFFFF77FFF733FF73, 0x0000C88000CCC800, 0x3100000000000000
    dq 0xFFFFF7FFFF73FFF7, 0x0000CCCCCCCCCCCC, 0xFF00000000000000, 0xFFFFFFFFFFFFFFFF
    dq 0xFFF1FFFFF8FFFFFC, 0xFFFFFFF7FFFFF3FF, 0xFFFFFFFFFFFFFFFF, 0xFF00FFFF00FFFF00
    dq 0x00FFFF00FFFF00FF, 0xFFFF77FFFF33FFFF, 0xF000FFF000FFF000, 0x00FFF000FFF000FF
    dq 0xFFFF77FFF733FFF0, 0x0000FF0000FF8000, 0x00FF0000FF0000FF, 0xFFFF77FFF733FF70
    dq 0x0000F80000FC8000, 0x00F10000F00000F0, 0xFFFF77FFF733FF10, 0x0000880000CC8000
    dq 0x00F1000090000080, 0xFFFF77FFF733FF10, 0x0000888000CCC800, 0x0000000000000080
    dq 0xFFFFF7FFFF730000, 0x8888888888CCCCCC, 0x0000000000000088, 0xFFFFFFFFFFFF0000
    dq 0xFFF1FFFFF8FFFFFC, 0xFFFFFFF7FFFFF3FF, 0xFFFFFFFFFFFFFFFF, 0xFF00FFFF00FFFF00
    dq 0x00FFFF00FFFF00FF, 0xFFFF77FFFF00FFFF, 0xF000FFF000FFF000, 0x00FFF000FFF000FF
    dq 0xFFFF77FFF100FFF0, 0x0000FF0000FF8000, 0x00FF0000FF0000FF, 0xFFFF77FFF100FF10
    dq 0x0000F80000F88000, 0x00F10000F00000F0, 0xFFFF77FF3100F310, 0x0000880000888000
    dq 0x0031000010000000, 0xFFFF77FF3100F310, 0x0000888000888800, 0x0000000010000000
    dq 0xFFFFF70000000000, 0x0000888888888888, 0x0000000010000000, 0xFFFFFF0000000000
    dq 0xFFF1FFFFF8FFFFFC, 0xFFFFFFF7FFFFF3FF, 0xFFFFFFFFFFFFFFFF, 0xFF00FFFF00FFFF00
    dq 0x00FFFF00FFFF00FF, 0xFFFF11FFFF00FFFF, 0xF000FFF000FFF000, 0x00FFF000FFF000FF
    dq 0xFFF311FFF100FFF0, 0x0000FF0000FF8000, 0x00FF0000FF0000FF, 0xFFF311FF3100FF10
    dq 0x0000F00000F88000, 0x00F10000F00000F0, 0xFF7311F73100F310, 0x0000000000888000
    dq 0x0031000010000000, 0xFF7311F731007310, 0x0000000000888800, 0x0031000010000000
    dq 0x0000000000000000, 0x0000000000888888, 0x0031000010000000, 0x0000000000000000
    dq 0xFFF1FFFFF8FFFFFC, 0xFFFFFFF7FFFFF3FF, 0xFFFFFFFFFFFFFFFF, 0xFF00FFFF00FFFF00
    dq 0x00FFFF00FFFF00FF, 0xFFFF11FFFF00FFFF, 0xF000FFF000FFF000, 0x00FFF000FFF000FF
    dq 0xFFF311FFF100FFF0, 0x0000FF0000FF0000, 0x00FF0000FF0000FF, 0xFF7311FF3100FF10
    dq 0x0000F00000F00000, 0x00F10000F00000F0, 0xFF7311F73100F310, 0x0000000000800000
    dq 0x0031000010000000, 0xFF7311F731007310, 0x0000000000800000, 0x0031000010000000
    dq 0x0000000000007310, 0x0000000000800000, 0x0031000010000000, 0x0000000000007310
kpk_bb_end:
%endif
section .text
; builds mgt/egt: contribution of piece p on square s (white point of view)
init_eval:
    push rbx
%if EVAL_FULL && HWPOPCNT == 2
    mov eax, 1                      ; CPUID leaf 1: ECX bit 23 = POPCNT
    cpuid
    bt ecx, 23
    setc byte [ev_hw]
%endif
    mov ebx, 1                  ; type
.t: xor r8d, r8d                ; rank
.r: xor r9d, r9d                ; file
.f: mov r10d, r8d
    shl r10d, 4
    add r10d, r9d               ; 0x88 square
    mov ecx, 7
    sub ecx, r8d
    shl ecx, 3
    add ecx, r9d                ; white index in pst (rank 8 first)
    mov edx, r8d
    shl edx, 3
    add edx, r9d                ; black index (mirrored)
    lea r11d, [rbx-1]
    shl r11d, 6                 ; (type-1)*64
    ; --- midgame
    lea esi, [r11+rcx]
    movsx eax, word [pst_mg+rsi*2]
    movsx edi, word [matmg+rbx*2]
    add eax, edi
    mov esi, ebx
    shl esi, 7
    add esi, r10d
    mov [mgt+rsi*2], ax
    lea esi, [r11+rdx]
    movsx eax, word [pst_mg+rsi*2]
    add eax, edi
    neg eax
    lea esi, [rbx+8]
    shl esi, 7
    add esi, r10d
    mov [mgt+rsi*2], ax
    ; --- endgame
    lea esi, [r11+rcx]
    movsx eax, word [pst_eg+rsi*2]
    movsx edi, word [mateg+rbx*2]
    add eax, edi
    mov esi, ebx
    shl esi, 7
    add esi, r10d
    mov [egt+rsi*2], ax
    lea esi, [r11+rdx]
    movsx eax, word [pst_eg+rsi*2]
    add eax, edi
    neg eax
    lea esi, [rbx+8]
    shl esi, 7
    add esi, r10d
    mov [egt+rsi*2], ax
    inc r9d
    cmp r9d, 8
    jl .f
    inc r8d
    cmp r8d, 8
    jl .r
    inc ebx
    cmp ebx, 6
    jle .t
    pop rbx
    ret
%endif                          ; UNIT == 0 (parameters, init_eval)

%if EVAL_FULL
SHARED evp, ev_k55, ev_k33, ev_k0f, ev_k01, ev_filea, ev_outr, ev_hw, ev_dark, ev_qside, ev_kside, ev_r23, ev_r5, ev_r6, ev_r7, ev_center, ev_space, ev_dtab
%endif
%if EV_ENDG
SHARED kpk_bb, mateg
%endif

; ---- TAPER [sf32]: eax = mg, ecx = eg -> eax = trunc((64 * mg * ph + sf * eg * (24 - ph)) / 1536); sf = the register, or 64.  clobbers rcx, rdx, rsi
%macro TAPER 0-1
    mov edx, [phase]
    cmp edx, 24
    jle %%ph
    mov edx, 24
%%ph:
    imul eax, edx
    mov esi, 24
    sub esi, edx
    imul ecx, esi
    shl eax, 6
%if %0 == 1
    imul ecx, %1
%else
    shl ecx, 6
%endif
    add eax, ecx
    DIV1536
%endmacro
; ---- TAPER2 mf32, sf32: eax = mg, ecx = eg -> eax = trunc((mf * mg * ph + sf * eg * (24 - ph)) / 1536).  clobbers rcx, rdx, rsi
%macro TAPER2 2
    mov edx, [phase]
    cmp edx, 24
    jle %%ph
    mov edx, 24
%%ph:
    imul eax, edx
    mov esi, 24
    sub esi, edx
    imul ecx, esi
    imul eax, %1
    imul ecx, %2
    add eax, ecx
    DIV1536
%endmacro
; ---- DIV1536: eax = trunc(eax / 1536) (signed, towards zero): |n| * 0xAAAAAAAB >> 42 is floor(|n| / 1536) for every |n| < 2^33.  clobbers rdx, rsi
%define EV_RCP   0xAAAAAAAB
%define EV_RSH   42
%macro DIV1536 0
    movsxd rax, eax
    cqo
    xor rax, rdx
    sub rax, rdx
    mov rsi, EV_RCP
    imul rax, rsi
    shr rax, EV_RSH
    xor rax, rdx
    sub rax, rdx
%endmacro
; ---- EV_CFG: evaluation switches of this build (console command "evalcfg"; tuner/model.py FLAG_BITS has the same bits)
%assign EV_CFG EVAL_FULL | (EV_TUNEMODE << 1) | (EV_ENDG << 2) | (EV_SCALE << 3) | (EV_LAZY << 4) | (EV_THRMIN << 5) | (EV_THRHANG << 6) | (EV_THRPAWN << 7) | (EV_THRRESTR << 8) | (EV_THRQUEEN << 9) | (EV_PINS << 10) | (EV_KDANGER << 11) | (EV_PASSED2 << 12) | (EV_PAWNS2 << 13) | (EV_SHELTER << 14) | (EV_PBISH << 15) | (EV_PMINOR << 16) | (EV_PROOK << 17) | (EV_PQUEEN << 18) | (EV_IMBAL << 19) | (EV_SPACE << 20) | (EV_INIT << 21) | (EV_GRAIN << 22) | (EV_SCALE6 << 23) | (EV_SCALE2 << 24) | (EV_ENDG2 << 25) | (EV_KLINES << 26) | (EV_SCMG << 27)

%if EVAL_FULL
; ---- helpers of the full evaluation
; POPC dst64, src64, tmp64: dst = number of set bits of src (src is kept unless it is dst)
%macro POPC 3
%if EVHW
%ifnidni %1, %2
    xor %1d, %1d                    ; breaks the false output dependency of popcnt on some CPUs
%endif
    popcnt %1, %2
%else
%ifnidni %1, %2
    mov %1, %2
%endif
    mov %3, %1
    shr %3, 1
    and %3, [ev_k55]
    sub %1, %3
    mov %3, %1
    shr %3, 2
    and %1, [ev_k33]
    and %3, [ev_k33]
    add %1, %3
    mov %3, %1
    shr %3, 4
    add %1, %3
    and %1, [ev_k0f]
    imul %1, [ev_k01]
    shr %1, 56
%endif
%endmacro
; NFILL / SFILL reg64, tmp64: every square on the file north / south of a set bit is set too (the bit itself stays)
%macro NFILL 2
    mov %2, %1
    shl %2, 8
    or %1, %2
    mov %2, %1
    shl %2, 16
    or %1, %2
    mov %2, %1
    shl %2, 32
    or %1, %2
%endmacro
%macro SFILL 2
    mov %2, %1
    shr %2, 8
    or %1, %2
    mov %2, %1
    shr %2, 16
    or %1, %2
    mov %2, %1
    shr %2, 32
    or %1, %2
%endmacro
; SLATK table, square64: rax = slider attacks from that square (rook / bishop table) through the occupancy in rbp.  clobbers rcx, rdx
%macro SLATK 2
    mov rdx, %2
    shl edx, 5
    mov rax, rbp
    and rax, [%1+rdx]
    imul rax, [%1+rdx+8]
    mov ecx, [%1+rdx+24]
    shr rax, cl
    mov rdx, [%1+rdx+16]
    mov rax, [rdx+rax*8]
%endmacro
; DIST sq32 (kept), sq32 (clobbered): eax = Chebyshev distance of two squares 0..63.  clobbers r8d, r10d
%macro DIST 2
    mov eax, %1
    and eax, 7
    mov r8d, %2
    and r8d, 7
    sub eax, r8d
    mov r8d, eax
    neg r8d
    cmovs r8d, eax
    mov eax, %1
    shr eax, 3
    shr %2, 3
    sub eax, %2
    mov r10d, eax
    neg r10d
    cmovs r10d, eax
    mov eax, r8d
    cmp eax, r10d
    cmovl eax, r10d
%endmacro
; TERM index, count64: r15d += evp[index] * count.  clobbers the count register (32-bit part)
%macro TERM 2
    imul %2d, [evp+(%1)*4]
    add r15d, %2d
%endmacro
; ATTMAP field: adds the attacks in rax to our map of that piece type, to "all" and (where already attacked) to "attacked twice".  clobbers rdx
%macro ATTMAP 1
%if EV_ATTMAPS
    or [rbx+%1], rax
    mov rdx, [rbx+EV_AALL]
    and rdx, rax
    or [rbx+EV_A2], rdx
    or [rbx+EV_AALL], rax
%endif
%endmacro
; PATT pawns64, tmp64: pawns -> the squares they attack (north)
%macro PATT 2
    mov %2, %1
    and %2, [k_nfh]
    shl %2, 9
    and %1, [k_nfa]
    shl %1, 7
    or %1, %2
%endmacro
; PINS table: adds to [rbx+EV_PIN] our pieces pinned on the lines of that table (rtab / btab) by the enemy sliders in r8; r9 = our king square
; (kept), rbp = occupancy (kept).  clobbers rax rcx rdx rdi r10 r11
%macro PINS 1
    test r8, r8
    jz %%d
    SLATK %1, r9
    mov r11, rax                    ; the king's lines up to the first piece
    mov r10, rax
    and r10, [rbx+EV_ALL]           ; our first pieces on them
    xor rbp, r10
    SLATK %1, r9                    ; the lines through those pieces
    xor rbp, r10
    mov r10, r11
    not r10
    and r10, rax
    and r10, r8                     ; pinners: enemy sliders right behind one of our pieces
%%l:test r10, r10
    jz %%d
    bsf rdi, r10
    lea rax, [r10-1]
    and r10, rax
    SLATK %1, rdi
    and rax, r11
    and rax, [rbx+EV_ALL]           ; the piece between them
    or [rbx+EV_PIN], rax
    jmp %%l
%%d:
%endmacro
; ADJF reg64, tmp64: reg = the squares on the files next to its set bits (same ranks)
%macro ADJF 2
    mov %2, %1
    and %2, [k_nfh]
    shl %2, 1
    and %1, [k_nfa]
    shr %1, 1
    or %1, %2
%endmacro
; KMUL reg32, tune index, default: reg *= the constant (option table with -DTUNE).  KCMP reg32, index, default: compare with it.  KSUB / KADD.
%macro KMUL 3
%ifdef TUNE
    TUSE %2
    imul %1, [tunetab+(%2)*4]
%else
    imul %1, %1, %3
%endif
%endmacro
%macro KCMP 3
%ifdef TUNE
    TUSE %2
    cmp %1, [tunetab+(%2)*4]
%else
    cmp %1, %3
%endif
%endmacro
%macro KSUB 3
%ifdef TUNE
    TUSE %2
    sub %1, [tunetab+(%2)*4]
%else
    sub %1, %3
%endif
%endmacro
%macro KADD 3
%ifdef TUNE
    TUSE %2
    add %1, [tunetab+(%2)*4]
%else
    add %1, %3
%endif
%endmacro
; ABSV reg32, tmp32: reg = |reg|
%macro ABSV 2
    mov %2, %1
    neg %2
    cmovns %1, %2
%endmacro
; SHELTERV: [rsp+16] = packed shelter / storm value for our king standing on eax (0..63, our frame); rsi = our pawns, rbx = view.
; clobbers rax rcx rdx rdi r8 r9 r10 r11 (EV_SHELTER)
%macro SHELTERV 0
    mov dword [rsp+16], 0
    mov ecx, eax
    and ecx, 0x38
    mov r9, -1
    shl r9, cl                      ; ranks from the king's rank up
    mov r10, rsi
    and r10, r9                     ; our pawns not behind the king
    mov r11, [rbx+EV_TP]
    and r11, r9                     ; their pawns not behind the king
    and eax, 7
    mov edx, 1
    cmp eax, edx
    cmovl eax, edx
    mov edx, 6
    cmp eax, edx
    cmovg eax, edx
    lea r8d, [rax-1]                ; file
    lea edi, [rax+2]                ; end
%%sh:mov ecx, r8d
    mov r9, [ev_filea]
    shl r9, cl
    mov edx, 7
    sub edx, r8d
    cmp edx, r8d
    cmovg edx, r8d
    imul edx, edx, 7                ; 7 * edge distance
    xor ecx, ecx
    mov rax, r10
    and rax, r9
    bsf rax, rax
    jz %%sh1
    mov ecx, eax
    shr ecx, 3                      ; ours
%%sh1:lea eax, [rdx+rcx]
    mov eax, [evp+EV_SHELT*4+rax*4]
    add [rsp+16], eax
    mov rax, r11
    and rax, r9
    bsf rax, rax
    jz %%sh2
    shr eax, 3                      ; theirs
    jmp %%sh3
%%sh2:xor eax, eax
%%sh3:test ecx, ecx
    jz %%sh4
    inc ecx
    cmp ecx, eax
    jne %%sh4
    mov eax, [evp+EV_BSTORM*4+rax*4]
    add [rsp+16], eax
    jmp %%sh5
%%sh4:add eax, edx
    mov eax, [evp+EV_STORM*4+rax*4]
    add [rsp+16], eax
%%sh5:inc r8d
    cmp r8d, edi
    jb %%sh
%endmacro
; SHELTERALT sq: [rsp+24] = the shelter of square sq when its middlegame value is larger (castling in reach)
%macro SHELTERALT 1
    mov eax, %1
    SHELTERV
    mov eax, [rsp+16]
    movsx ecx, ax
    movsx edx, word [rsp+24]
    cmp ecx, edx
    jle %%no
    mov [rsp+24], eax
%%no:
%endmacro
; FILEFOLD bb, tmp: low byte of bb = files that have a bit in bb
%macro FILEFOLD 2
    mov %2, %1
    shr %2, 32
    or %1, %2
    mov %2, %1
    shr %2, 16
    or %1, %2
    mov %2, %1
    shr %2, 8
    or %1, %2
%endmacro
; KDACC weight: king danger counters of a piece whose attacks are in rax (kept).  clobbers r10, r11
%macro KDACC 1
%if EV_KDANGER
    test rax, [rbx+EV_RING]
    jz %%n
    add dword [rbx+EV_KDW], %1
%%n:mov r10, rax
    and r10, [rbx+EV_TKZ]
    POPC r10, r10, r11
    add [rbx+EV_KDA], r10d
%endif
%endmacro
; BPAIR: eax += BP_MG, ecx += BP_EG for White with two bishops, minus the same for Black
%macro BPAIR 0
    cmp byte [cnt+3], 2
    jl %%nw
    add eax, BP_MG
    add ecx, BP_EG
%%nw:
    cmp byte [cnt+11], 2
    jl %%nb
    sub eax, BP_MG
    sub ecx, BP_EG
%%nb:
%endmacro

; view of one side for ev_side: "us" moves north (the black side is passed mirrored vertically, see eval)
%define EV_P     0              ; our pawns, knights, bishops, rooks, queens, king (bitboards)
%define EV_NB    8
%define EV_BB    16
%define EV_RB    24
%define EV_QB    32
%define EV_KB    40
%define EV_TP    48             ; their pawns, knights, bishops, rooks, queens, king
%define EV_TN    56
%define EV_TB    64
%define EV_TR    72
%define EV_TQ    80
%define EV_TK    88
%define EV_ALL   96             ; our pieces
%define EV_TALL  104            ; their pieces
%define EV_OCC   112            ; all pieces
%define EV_KSQ   120            ; dd our king square 0..63
%define EV_TKSQ  124            ; dd their king square 0..63
%define EV_NPASS 128            ; dd our passed pawns (output of ev_side, EV_SCALE)
%define EV_AP    144            ; our attack maps (EV_ATTMAPS): pawns, knights, bishops, rooks, queens, king, all, attacked at least twice
%define EV_AN    152
%define EV_AB    160
%define EV_AR    168
%define EV_AQ    176
%define EV_AK    184
%define EV_AALL  192
%define EV_A2    200
%define EV_TAP   208            ; their attack maps in our frame (same order; filled by eval after both first passes)
%define EV_TAN   216
%define EV_TAB   224
%define EV_TAR   232
%define EV_TAQ   240
%define EV_TAK   248
%define EV_TAALL 256
%define EV_TA2   264
%define EV_PSC   272            ; dd pawn part of our score (EV_PHASH)
%define EV_PHIT  276            ; dd 1 = EV_PSC / EV_PPB come from the pawn cache
%define EV_PPB   280            ; our passed pawns (bitboard)
%define EV_PIN   288            ; our pinned pieces (EV_PINS)
%define EV_MAREA 296            ; our mobility area (EV_THRQUEEN)
%define EV_RING  304            ; king ring of THEIR king (EV_KDANGER)
%define EV_TKZ   312            ; squares next to their king
%define EV_KDW   320            ; dd weights of our pieces that attack the ring
%define EV_KDA   324            ; dd our attacks on the squares next to their king
%define EV_TPIN  328            ; their pinned pieces in our frame
%define EV_CR    336            ; dd our castling rights: bit 0 short (king to g1 in our frame), bit 1 long (c1) (EV_SHELTER)
%define EVS      352

%if EV_ENDG
; ---- ev_bare (eax = colour of the strong side, 0 / 8; the other side has only its king): special evaluation, see EV_ENDG in the header.
; Jumps to ev_dispatch (the normal evaluation) for the positions that are not special.  Preserves every register except rax, rcx, rdx, rsi.
ev_bare:
    push rbx
    push rdi
    push r8
    push r9
    push r10
    push r11
    mov ebx, eax
    movzx r8d, byte [cnt+rbx+1]     ; pawns
    movzx r9d, byte [cnt+rbx+2]     ; knights
    movzx r10d, byte [cnt+rbx+3]    ; bishops
    movzx r11d, byte [cnt+rbx+4]    ; rooks
    movzx edi, byte [cnt+rbx+5]     ; queens
    mov eax, r11d
    or eax, edi
    jnz .kxk
    test r8d, r8d
    jnz .pawns
    test r9d, r9d                   ; minor pieces only
    jz .bish
    test r10d, r10d
    jz .knights
    cmp r9d, 1
    jne .kxk
    cmp r10d, 1
    jne .kxk
    jmp .kbnk
.knights:
    cmp r9d, 3
    jae .kxk
    jmp .draw
.bish:
    test r10d, r10d
    jz .draw                        ; king against king
    mov rax, [bbp+rbx*8+24]         ; bishops of both colours can mate
    mov rcx, [ev_dark]
    test rax, rcx
    jz .draw
    not rcx
    test rax, rcx
    jz .draw
    jmp .kxk
.pawns:
    test r9d, r9d
    jnz .normal                     ; knights and pawns: the normal evaluation
    test r10d, r10d
    jnz .kbp
    cmp r8d, 1
    je .kpk
    mov rax, [bbp+rbx*8+8]          ; only pawns: all on the a-file or all on the h-file, bare king on / next to the promotion square -> draw
    mov rdx, [ev_filea]
    xor ecx, ecx
    mov rsi, rdx
    not rsi
    test rax, rsi
    jz .kps1
    shl rdx, 7
    mov ecx, 7
    not rdx
    test rax, rdx
    jnz .normal
.kps1:
    test ebx, ebx
    jnz .kps2
    add ecx, 56                     ; promotion square of White
.kps2:
    call ev_bare_k                  ; edx = bare king square
    call ev_dist                    ; eax = distance(ecx, edx)
    cmp eax, 1
    jbe .draw
.normal:
    pop r11
    pop r10
    pop r9
    pop r8
    pop rdi
    pop rbx
    jmp ev_dispatch
.kbp:                               ; bishops and pawns: every pawn on the a-file or every pawn on the h-file, no bishop on the colour of the
    mov rax, [bbp+rbx*8+8]          ; promotion square, the bare king on / next to it -> draw (wrong bishop); otherwise the normal evaluation
    mov rdx, [ev_filea]
    xor ecx, ecx
    mov rsi, rdx
    not rsi
    test rax, rsi
    jz .kb2
    shl rdx, 7
    mov ecx, 7
    not rdx
    test rax, rdx
    jnz .normal
.kb2:
    test ebx, ebx
    jnz .kb3
    add ecx, 56
.kb3:
    mov rax, [bbp+rbx*8+24]         ; bishops
    mov rdx, [ev_dark]
    bt rdx, rcx
    jc .kb4
    not rdx
.kb4:
    test rax, rdx
    jnz .normal                     ; a bishop controls the promotion square
    call ev_bare_k
    call ev_dist
    cmp eax, 1
    ja .normal
.draw:                              ; a draw is exactly 0, as the search scores it (no tempo)
    xor eax, eax
    jmp .o2
.kxk:
    call ev_bare_mat
    add eax, EV_KNOWNWIN
    mov edi, eax
    call ev_bare_k                  ; 20 * centre distance of the bare king
    mov eax, edx
    and eax, 7
    lea ecx, [rax-4]
    neg eax
    add eax, 3
    cmp eax, ecx
    cmovl eax, ecx
    shr edx, 3
    lea ecx, [rdx-4]
    neg edx
    add edx, 3
    cmp edx, ecx
    cmovl edx, ecx
    add eax, edx
    imul eax, eax, 20
    add edi, eax
    jmp .close
.kbnk:
    call ev_bare_mat
    add eax, EV_KNOWNWIN
    mov edi, eax
    call ev_bare_k                  ; 30 * (7 - distance to the nearest corner of the bishop's colour)
    mov rax, [bbp+rbx*8+24]
    xor ecx, ecx                    ; dark: a1 / h8, light: h1 / a8
    mov esi, 63
    test rax, [ev_dark]
    jnz .kb1
    mov ecx, 7
    mov esi, 56
.kb1:
    call ev_dist
    mov r8d, eax
    mov ecx, esi
    call ev_dist
    cmp eax, r8d
    cmovg eax, r8d
    neg eax
    add eax, 7
    imul eax, eax, 30
    add edi, eax
.close:                             ; + 20 * (7 - distance of the two kings)
    call ev_bare_k
    mov eax, ebx
    shr eax, 3
    mov ecx, [ksq+rax*4]
    mov eax, ecx
    and eax, 7
    add ecx, eax
    shr ecx, 1
    call ev_dist
    neg eax
    add eax, 7
    imul eax, eax, 20
    add eax, edi
    mov ecx, 19000
    cmp eax, ecx
    cmovg eax, ecx
    jmp .out
.kpk:
    mov rax, [bbp+rbx*8+8]
    bsf rcx, rax                    ; pawn
    mov eax, ebx
    shr eax, 3
    mov edi, [ksq+rax*4]            ; strong king (0x88 -> 0..63)
    mov eax, edi
    and eax, 7
    add edi, eax
    shr edi, 1
    call ev_bare_k                  ; edx = bare king
    xor eax, eax                    ; stm index: 0 = the pawn side to move
    cmp ebx, [stm]
    setne al
    test ebx, ebx
    jz .kp1
    xor ecx, 56                     ; Black has the pawn: mirror the board vertically
    xor edi, 56
    xor edx, 56
.kp1:
    mov esi, ecx
    and esi, 7
    cmp esi, 4
    jb .kp2
    xor ecx, 7                      ; pawn on files e..h: mirror horizontally
    xor edi, 7
    xor edx, 7
.kp2:
    shl eax, 6
    add eax, edi
    shl eax, 6
    add eax, edx
    imul eax, eax, 24
    mov esi, ecx
    shr esi, 3                      ; pawn rank 1..6
    lea eax, [rax+rsi*4-4]
    mov edx, ecx
    and edx, 3
    add eax, edx                    ; bit index
    mov edx, eax
    shr edx, 6
    mov rdx, [kpk_bb+rdx*8]
    bt rdx, rax
    jnc .draw
    movzx eax, word [mateg+2]
    add eax, EV_KNOWNWIN
    imul esi, esi, 15
    add eax, esi
.out:                               ; eax = value for the strong side -> side to move, + TEMPO
    cmp ebx, [stm]
    je .o1
    neg eax
.o1:add eax, TEMPO
.o2:pop r11
    pop r10
    pop r9
    pop r8
    pop rdi
    pop rbx
    ret

; edx = square 0..63 of the bare king (the side that is not ebx).  clobbers eax
ev_bare_k:
    mov eax, ebx
    xor eax, 8
    shr eax, 3
    mov edx, [ksq+rax*4]
    mov eax, edx
    and eax, 7
    add edx, eax
    shr edx, 1
    ret

; eax = endgame material of the strong side (ebx; counts in r8d..r11d, edi).  clobbers ecx
ev_bare_mat:
    movzx eax, word [mateg+2]
    imul eax, r8d
    movzx ecx, word [mateg+4]
    imul ecx, r9d
    add eax, ecx
    movzx ecx, word [mateg+6]
    imul ecx, r10d
    add eax, ecx
    movzx ecx, word [mateg+8]
    imul ecx, r11d
    add eax, ecx
    movzx ecx, word [mateg+10]
    imul ecx, edi
    add eax, ecx
    ret
%endif

%if EV_ENDG2
; ---- ev_spec2: special evaluation of KQ-KR, KR-KB, KR-KN without pawns (EV_ENDG2 in the header); every other position goes to ev_dispatch.
; Side-to-move view plus TEMPO.  Preserves every register except rax, rcx, rdx, rsi.
ev_spec2:
    mov al, [cnt+1]
    or al, [cnt+9]
    jnz ev_dispatch                 ; pawns
    movzx eax, byte [cnt+2]
    add al, [cnt+3]
    add al, [cnt+4]
    add al, [cnt+5]
    cmp eax, 1
    jne ev_dispatch
    movzx eax, byte [cnt+10]
    add al, [cnt+11]
    add al, [cnt+12]
    add al, [cnt+13]
    cmp eax, 1
    jne ev_dispatch                 ; one piece each
    push rbx
    push rdi
    push r8
    xor ebx, ebx                    ; ebx = colour of the strong side, edi = class: 0 Q-R, 1 R-B, 2 R-N
    cmp byte [cnt+4], 1
    je .wr
    cmp byte [cnt+12], 1
    jne .no
    mov ebx, 8                      ; Black has the rook
.wr:mov eax, ebx
    xor eax, 8                      ; the other side
    cmp byte [cnt+rax+5], 1
    je .qr
    mov edi, 1
    cmp byte [cnt+rax+3], 1
    je .go
    mov edi, 2
    cmp byte [cnt+rax+2], 1
    je .go
    jmp .no
.qr:xor ebx, 8                      ; the queen side is the strong side
    xor edi, edi
.go:call ev_bare_k                  ; edx = weak king (0..63)
    mov r8d, edx
    mov eax, edx                    ; centre distance of the weak king
    and eax, 7
    lea ecx, [rax-4]
    neg eax
    add eax, 3
    cmp eax, ecx
    cmovl eax, ecx
    shr edx, 3
    lea ecx, [rdx-4]
    neg edx
    add edx, 3
    cmp edx, ecx
    cmovl edx, ecx
    add eax, edx
    test edi, edi
    jnz .rm
    imul esi, eax, EG2_QEDGE        ; Q-R
    mov eax, ebx
    shr eax, 3
    mov ecx, [ksq+rax*4]            ; strong king 0x88 -> 0..63
    mov eax, ecx
    and eax, 7
    add ecx, eax
    shr ecx, 1
    mov edx, r8d
    call ev_dist
    neg eax
    add eax, 7
    imul eax, eax, EG2_QCLOSE
    add eax, esi
    add eax, EG2_QR
    jmp .out
.rm:imul esi, eax, EG2_REDGE         ; R against a minor piece
    cmp edi, 1
    jne .rn
    lea eax, [rsi+EG2_RB]
    jmp .out
.rn:mov eax, ebx
    xor eax, 8
    mov rax, [bbp+rax*8+16]         ; the knight
    bsf rcx, rax
    mov edx, r8d
    call ev_dist
    imul eax, eax, EG2_RNFAR
    add eax, esi
    add eax, EG2_RN
.out:cmp ebx, [stm]
    je .o1
    neg eax
.o1:add eax, TEMPO
    pop r8
    pop rdi
    pop rbx
    ret
.no:pop r8
    pop rdi
    pop rbx
    jmp ev_dispatch
%endif

%if EV_IMBAL
; ---- ev_imbal(ecx = colour 0 / 8) -> eax = packed material imbalance of that side (EV_IMBAL).  clobbers rcx rdx rsi rdi r8 r9 r10 r11
ev_imbal:
    sub rsp, 56                     ; dwords: [rsp] ours (pair, P, N, B, R, Q), [rsp+24] theirs
    mov edi, ecx
    mov esi, ecx
    xor esi, 8
    xor edx, edx
    cmp byte [cnt+rdi+3], 2
    setge dl
    mov [rsp], edx
    xor edx, edx
    cmp byte [cnt+rsi+3], 2
    setge dl
    mov [rsp+24], edx
    mov ecx, 1
.c: movzx eax, byte [cnt+rdi+rcx]
    mov [rsp+rcx*4], eax
    movzx eax, byte [cnt+rsi+rcx]
    mov [rsp+24+rcx*4], eax
    inc ecx
    cmp ecx, 6
    jb .c
    xor eax, eax
    xor r8d, r8d                    ; i
    xor r10d, r10d                  ; i * (i + 1) / 2 + j
.i: mov r11d, [rsp+r8*4]
    xor r9d, r9d                    ; j
.j: mov edx, [rsp+r9*4]
    imul edx, r11d
    imul edx, [evp+EV_IMBO*4+r10*4]
    add eax, edx
    mov edx, [rsp+24+r9*4]
    imul edx, r11d
    imul edx, [evp+EV_IMBT*4+r10*4]
    add eax, edx
    inc r10d
    inc r9d
    cmp r9d, r8d
    jbe .j
    inc r8d
    cmp r8d, 6
    jb .i
    add rsp, 56
    ret
%endif

%if EV_ENDG || EV_SCALE || EV_PASSED2 || EV_SHELTER || EV_PMINOR
; eax = Chebyshev distance of the squares ecx and edx (0..63; both kept).  clobbers nothing else
ev_dist:
    mov eax, ecx
    shl eax, 6
    add eax, edx
    movzx eax, byte [ev_dtab+rax]
    ret
%endif

%if EV_SCALE
; ---- ev_scale(edi = passed pawns of White, esi = passed pawns of Black, r9d = colour 0 / 8 of the strong side) -> eax = endgame scale factor
; 0..64 (EV_SCALE in the header).  clobbers rcx rdx rsi rdi r8
; info block of a side (dwords): 0 pawns, 4 knights, 8 bishops, 12 rooks, 16 queens, 20 npm, 24 material, 28 passed pawns, 32 colour,
; 36 king square, 40 has a dark bishop, 44 has a light bishop, 48 (qword) pawns
ev_scale:
    sub rsp, 128
    mov [rsp+28], edi
    mov [rsp+64+28], esi
    mov rdi, rsp
    xor ecx, ecx
    call ev_info
    lea rdi, [rsp+64]
    mov ecx, 8
    call ev_info
    mov rdi, rsp                    ; s = the side the eg score favours (r9d = its colour), o = the other side
    lea rsi, [rsp+64]
    test r9d, r9d
    jz .s1
    xchg rdi, rsi
%if EV_SCALE2
.s1:call ev_rule2
%else
.s1:call ev_rule
%endif
    add rsp, 128
    ret

; ev_info(rdi = block, ecx = colour 0 / 8).  clobbers rax rdx rsi r8
ev_info:
    movzx eax, byte [cnt+rcx+1]
    mov [rdi], eax
    movzx edx, byte [cnt+rcx+2]
    mov [rdi+4], edx
    movzx esi, byte [cnt+rcx+3]
    mov [rdi+8], esi
    add edx, esi
    lea edx, [rdx+rdx*2]            ; 3 * (N + B)
    movzx esi, byte [cnt+rcx+4]
    mov [rdi+12], esi
    lea esi, [rsi+rsi*4]
    add edx, esi                    ; + 5 R
    movzx esi, byte [cnt+rcx+5]
    mov [rdi+16], esi
    lea esi, [rsi+rsi*8]
    add edx, esi                    ; + 9 Q
    mov [rdi+20], edx
    add edx, eax
    mov [rdi+24], edx
    mov [rdi+32], ecx
    mov eax, ecx
    shr eax, 3
    mov eax, [ksq+rax*4]
    mov edx, eax
    and edx, 7
    add eax, edx
    shr eax, 1
    mov [rdi+36], eax
    mov rax, [bbp+rcx*8+24]         ; bishops
    xor edx, edx
    test rax, [ev_dark]
    setnz dl
    mov [rdi+40], edx
    mov r8, [ev_dark]
    not r8
    xor edx, edx
    test rax, r8
    setnz dl
    mov [rdi+44], edx
    mov rax, [bbp+rcx*8+8]          ; pawns
    mov [rdi+48], rax
    ret

; ev_rule(rdi = candidate strong side s, rsi = other side o) -> eax = scale factor of the first rule that applies.  clobbers rcx rdx r8
ev_rule:
    cmp dword [rdi], 0              ; 1. s has no pawns and npm(s) - npm(o) <= 3
    jne .r2
    mov eax, [rdi+20]
    sub eax, [rsi+20]
    cmp eax, 3
    jg .r2
    xor eax, eax
    cmp dword [rdi+20], 5
    jl .ret
    mov eax, 4
    cmp dword [rsi+20], 3
    jle .ret
    mov eax, 14
    ret
.r2:cmp dword [rdi+8], 1            ; 2. one bishop, pawns on one rook file only, the bishop misses the promotion square, o's king next to it
    jne .r3
    mov eax, [rdi+4]
    or eax, [rdi+12]
    or eax, [rdi+16]
    jnz .r3
    mov rax, [rdi+48]
    test rax, rax
    jz .r3
    mov rdx, [ev_filea]
    xor r8d, r8d
    mov rcx, rdx
    not rcx
    test rax, rcx
    jz .r2p
    shl rdx, 7
    mov r8d, 7
    not rdx
    test rax, rdx
    jnz .r3
.r2p:
    cmp dword [rdi+32], 0
    jne .r2b
    add r8d, 56
.r2b:
    mov rax, [ev_dark]
    bt rax, r8
    jnc .r2l
    cmp dword [rdi+40], 0           ; dark promotion square, dark bishop covers it
    jne .r3
    jmp .r2k
.r2l:
    cmp dword [rdi+44], 0
    jne .r3
.r2k:
    mov ecx, r8d
    mov edx, [rsi+36]
    call ev_dist
    cmp eax, 1
    ja .r3
    xor eax, eax
    ret
.r3:cmp dword [rdi+8], 1            ; 3. one bishop each, opposite colours
    jne .r4
    cmp dword [rsi+8], 1
    jne .r4
    mov eax, [rdi+40]
    cmp eax, [rsi+40]
    je .r4
    cmp dword [rdi+20], 3
    jne .r3b
    cmp dword [rsi+20], 3
    jne .r3b
    mov eax, [rdi+28]
    lea eax, [rax*4+18]
    jmp .cap
.r3b:
    mov eax, [rdi+4]
    add eax, [rdi+8]
    add eax, [rdi+12]
    add eax, [rdi+16]
    lea eax, [rax+rax*2+22]
    jmp .cap
.r4:cmp dword [rdi+20], 5            ; 4. one rook each, no other pieces, pawn counts differ by <= 1, all pawns on one flank
    jne .r5
    cmp dword [rsi+20], 5
    jne .r5
    cmp dword [rdi+12], 1
    jne .r5
    cmp dword [rsi+12], 1
    jne .r5
    mov eax, [rdi]
    sub eax, [rsi]
    inc eax
    cmp eax, 2
    ja .r5
    mov rax, [rdi+48]
    or rax, [rsi+48]
    test rax, [ev_qside]
    jz .r4y
    test rax, [ev_kside]
    jnz .r5
.r4y:
    mov eax, 36
    ret
.r5:cmp dword [rdi+16], 1            ; 5. the only queen on the board is s's
    jne .r6
    cmp dword [rsi+16], 0
    jne .r6
    mov eax, [rsi+4]
    add eax, [rsi+8]
    lea eax, [rax+rax*2+37]
    jmp .cap
.r6:
%if EV_SCALE6 == 0
    mov eax, 64                     ; rule 6 off (EV_SCALE6=0)
    ret
%endif
    mov eax, [rdi]                  ; 6. min(64, 36 + 7 * pawns), - 4 without pawns on both flanks
    imul eax, eax, 7
    add eax, 36
    mov ecx, 64
    cmp eax, ecx
    cmovg eax, ecx
    mov rcx, [rdi+48]
    or rcx, [rsi+48]
    test rcx, [ev_qside]
    jz .r6m
    test rcx, [ev_kside]
    jnz .ret
.r6m:
    sub eax, 4
    ret
.cap:
    mov ecx, 64
    cmp eax, ecx
    cmovg eax, ecx
.ret:
    ret

%if EV_SCALE2
; ev_rule2(rdi = strong side s, rsi = other side o) -> eax = scale factor 0..64 of the second rule set (EV_SCALE2 in the header;
; tuner/model.py scale_kind2 / scale_value2).  clobbers rcx rdx r8 r9 r10
ev_rule2:
    cmp dword [rdi], 0              ; 1. s has no pawns and npm(s) - npm(o) <= 3
    jne .r2
    mov eax, [rdi+20]
    sub eax, [rsi+20]
    cmp eax, 3
    jg .r2
    xor eax, eax
    cmp dword [rdi+20], 5
    jl .ret
    cmp dword [rsi+20], 3
    jg .m2
    KADD eax, 40, SC_M1
    ret
.m2:KADD eax, 41, SC_M2
    ret
.r2:cmp dword [rdi+8], 1            ; 2. one bishop, pawns on one rook file only, the bishop misses the promotion square, o's king next to it
    jne .r3
    mov eax, [rdi+4]
    or eax, [rdi+12]
    or eax, [rdi+16]
    jnz .r3
    mov rax, [rdi+48]
    test rax, rax
    jz .r3
    mov rdx, [ev_filea]
    xor r8d, r8d
    mov rcx, rdx
    not rcx
    test rax, rcx
    jz .r2p
    shl rdx, 7
    mov r8d, 7
    not rdx
    test rax, rdx
    jnz .r3
.r2p:
    cmp dword [rdi+32], 0
    jne .r2b
    add r8d, 56
.r2b:
    mov rax, [ev_dark]
    bt rax, r8
    jnc .r2l
    cmp dword [rdi+40], 0           ; dark promotion square, dark bishop covers it
    jne .r3
    jmp .r2k
.r2l:
    cmp dword [rdi+44], 0
    jne .r3
.r2k:
    mov ecx, r8d
    mov edx, [rsi+36]
    call ev_dist
    cmp eax, 1
    ja .r3
    xor eax, eax
    ret
.r3:cmp dword [rdi], 0               ; class rules: drawn patterns of single-pawn endings
    jne .c3
    cmp dword [rsi], 1              ; s: rook or queen alone, o: one pawn
    jne .r3f
    cmp dword [rsi+20], 0
    jne .r3f
    mov eax, [rdi+4]
    or eax, [rdi+8]
    jnz .r3f
    bsf r8, [rsi+48]                ; r8 = o's pawn, r9d = its rank counted from o's side
    mov r9d, r8d
    shr r9d, 3
    cmp dword [rsi+32], 0
    je .c1w
    xor r9d, 7
.c1w:
    cmp dword [rdi+20], 5
    jne .c2
    cmp r9d, 3                      ; rook: pawn on its 4th rank or beyond, o's king next to it, s's king >= 3 from the square in front
    jb .r3f                         ; of the pawn and not on the pawn's file ahead of it
    mov ecx, r8d
    mov edx, [rsi+36]
    call ev_dist
    cmp eax, 1
    ja .r3f
    lea ecx, [r8+8]
    cmp dword [rsi+32], 0
    je .c1f
    lea ecx, [r8-8]
.c1f:
    mov edx, [rdi+36]
    call ev_dist
    cmp eax, 3
    jb .r3f
    mov eax, [rdi+36]
    mov ecx, eax
    xor ecx, r8d
    test ecx, 7
    jnz .c1y
    shr eax, 3
    cmp dword [rsi+32], 0
    je .c1r
    xor eax, 7
.c1r:
    cmp eax, r9d
    ja .r3f
.c1y:
    xor eax, eax
    KADD eax, 54, SC_RKP
    ret
.c2:cmp dword [rdi+20], 9            ; queen: rook or bishop pawn on its 7th rank, o's king next to it
    jne .r3f
    cmp r9d, 6
    jne .r3f
    mov eax, r8d
    and eax, 7
    mov ecx, 0xA5                   ; files a, c, f, h
    bt ecx, eax
    jnc .r3f
    mov ecx, r8d
    mov edx, [rsi+36]
    call ev_dist
    cmp eax, 1
    ja .r3f
    xor eax, eax
    KADD eax, 55, SC_QKP
    ret
.c3:cmp dword [rdi], 1               ; s: one pawn, o: no pawn
    jne .r3f
    cmp dword [rsi], 0
    jne .r3f
    bsf r8, [rdi+48]                ; r8 = s's pawn, r9d = its rank counted from s's side
    mov r9d, r8d
    shr r9d, 3
    cmp dword [rdi+32], 0
    je .c3w
    xor r9d, 7
.c3w:
    xor r10d, r10d                  ; r10d = 1: o's king on the pawn's file in front of it
    mov eax, [rsi+36]
    mov ecx, eax
    xor ecx, r8d
    test ecx, 7
    jnz .c3b
    shr eax, 3
    cmp dword [rdi+32], 0
    je .c3r
    xor eax, 7
.c3r:
    cmp eax, r9d
    jbe .c3b
    mov r10d, 1
.c3b:
    cmp dword [rdi+20], 5           ; rook and pawn against rook: o's king blocks, or it is next to the promotion square and the pawn
    jne .c4                         ; has not passed its 5th rank
    cmp dword [rdi+12], 1
    jne .r3f
    cmp dword [rsi+20], 5
    jne .r3f
    cmp dword [rsi+12], 1
    jne .r3f
    test r10d, r10d
    jnz .c3y
    cmp r9d, 4
    ja .r3f
    mov ecx, r8d
    and ecx, 7
    cmp dword [rdi+32], 0
    jne .c3q
    add ecx, 56
.c3q:
    mov edx, [rsi+36]
    call ev_dist
    cmp eax, 1
    ja .r3f
.c3y:
    xor eax, eax
    KADD eax, 56, SC_RPR
    ret
.c4:cmp dword [rdi+20], 3            ; minor piece and pawn against a minor piece: o's king blocks the pawn
    jne .r3f
    cmp dword [rsi+20], 3
    jne .r3f
    test r10d, r10d
    jz .r3f
    xor eax, eax
    KADD eax, 57, SC_MPM
    ret
.r3f:mov rax, [rdi+48]               ; r10d = 1: all pawns on one flank (or none)
    or rax, [rsi+48]
    mov r10d, 1
    test rax, [ev_qside]
    jz .r3a
    test rax, [ev_kside]
    jz .r3a
    xor r10d, r10d
.r3a:
    cmp dword [rdi+8], 1            ; 3. one bishop each, opposite colours
    jne .r4
    cmp dword [rsi+8], 1
    jne .r4
    mov eax, [rdi+40]
    cmp eax, [rsi+40]
    je .r4
    cmp dword [rdi+20], 3
    jne .oc
    cmp dword [rsi+20], 3
    jne .oc
    mov eax, [rdi+28]               ; bishops alone: SC_OB0 + SC_OB1 * passed pawns of s
    KMUL eax, 43, SC_OB1
    KADD eax, 42, SC_OB0
    jmp .fl
.oc:mov eax, [rdi+4]                ; with other pieces: SC_OC0 + SC_OC1 * pieces of s + SC_OC2 * pawns of s
    add eax, [rdi+8]
    add eax, [rdi+12]
    add eax, [rdi+16]
    KMUL eax, 45, SC_OC1
    mov ecx, [rdi]
    KMUL ecx, 46, SC_OC2
    add eax, ecx
    KADD eax, 44, SC_OC0
    jmp .fl
.r4:cmp dword [rdi+20], 5            ; 4. one rook each and nothing else, pawns(s) - pawns(o) <= 1, the pawns of s on exactly one flank,
    jne .r5                         ;    o's king next to one of its pawns
    cmp dword [rsi+20], 5
    jne .r5
    cmp dword [rdi+12], 1
    jne .r5
    cmp dword [rsi+12], 1
    jne .r5
    mov eax, [rdi]
    sub eax, [rsi]
    cmp eax, 1
    jg .r5
    mov rax, [rdi+48]
    xor ecx, ecx
    test rax, [ev_qside]
    setnz cl
    xor edx, edx
    test rax, [ev_kside]
    setnz dl
    cmp ecx, edx
    je .r5
    mov ecx, [rsi+36]
    mov rax, [king_att+rcx*8]
    test rax, [rsi+48]
    jz .r5
    xor eax, eax
    KADD eax, 47, SC_RK
    jmp .fl
.r5:mov eax, [rdi+16]                ; 5. exactly one queen on the board: SC_Q0 + SC_Q1 * minor pieces of the side without it
    add eax, [rsi+16]
    cmp eax, 1
    jne .r6
    mov r8, rsi
    cmp dword [rdi+16], 0
    jne .q1
    mov r8, rdi
.q1:mov eax, [r8+4]
    add eax, [r8+8]
    KMUL eax, 49, SC_Q1
    KADD eax, 48, SC_Q0
    jmp .fl
.r6:mov eax, [rdi]                   ; 6. min(64, SC_G0 + SC_G1 * pawns of s) - SC_G6FL on one flank
    KMUL eax, 51, SC_G1
    KADD eax, 50, SC_G0
    mov ecx, 64
    cmp eax, ecx
    cmovg eax, ecx
    mov ecx, r10d
    KMUL ecx, 53, SC_G6FL
    sub eax, ecx
.fl:mov ecx, r10d                    ; rules 3 - 6: - SC_FL on one flank, then 0 .. 64
    KMUL ecx, 52, SC_FL
    sub eax, ecx
    xor ecx, ecx
    cmp eax, ecx
    cmovl eax, ecx
    mov ecx, 64
    cmp eax, ecx
    cmovg eax, ecx
.ret:
    ret
%endif
%endif

; EVCODE hw: ev_side<hw> and ev_eval<hw>, the full evaluation with the population count of variant hw (0 plain code, 1 POPCNT)
%ifdef EVALPROF
%if UNIT == 0
section .data
align 8
evprof_n:   dq 0                    ; eval calls of the main thread (test builds only)
evprof_cyc: dq 0                    ; their rdtsc cycles
evprof_php: dq 0                    ; pawn cache probes / hits (EV_PHASH)
evprof_phh: dq 0
section .text
%endif
%endif
%if EV_PHASH
section .bss                        ; pawn cache of this thread unit: wp, bp, passed (White's view), passed (Black's view), score W, score B, 24 free
alignb 64
ev_ph:      resb 64 << EV_PHBITS
section .text
%endif
; INITIATIVE: eax = mg, ecx = eg (White's view) -> the same after the initiative / complexity correction (EV_INIT; the frame of ev_eval).
; clobbers rdx rsi r8 r9 r10 r11
%macro INITIATIVE 0
    mov r8d, eax
    mov r9d, ecx
    mov esi, [rsp+EV_NPASS]
    add esi, [rsp+EVS+EV_NPASS]
    KMUL esi, 31, IN_PASSED
    movzx eax, byte [cnt+1]
    movzx edx, byte [cnt+9]
    add eax, edx
    KMUL eax, 32, IN_PAWNS
    add esi, eax
    mov eax, [rsp+EV_KSQ]           ; outflanking = king file distance - king rank distance
    mov edx, [rsp+EV_TKSQ]
    mov r10d, eax
    and r10d, 7
    mov r11d, edx
    and r11d, 7
    sub r10d, r11d
    ABSV r10d, r11d
    shr eax, 3
    shr edx, 3
    mov r11d, eax
    sub r11d, edx
    ABSV r11d, ecx
    sub r10d, r11d
    mov ecx, r10d
    KMUL ecx, 33, IN_OUTFL
    add esi, ecx
    xor ecx, ecx                    ; infiltration: White's king on rank 5 or higher, or Black's on rank 4 or lower
    cmp eax, 3
    seta cl
    cmp edx, 4
    setb dl
    or cl, dl
    KMUL ecx, 35, IN_INFIL
    add esi, ecx
    mov rax, [bbp+8]                ; pawns on both flanks
    or rax, [bbp+72]
    xor ecx, ecx
    test rax, [ev_qside]
    jz %%nb
    test rax, [ev_kside]
    setnz cl
%%nb:
    mov edx, ecx
    KMUL ecx, 34, IN_BOTH
    add esi, ecx
    test edx, edx                   ; almost unwinnable: no passed pawn, outflanking < 0, pawns on one flank only
    jnz %%nu
    test r10d, r10d
    jns %%nu
    mov ecx, [rsp+EV_NPASS]
    or ecx, [rsp+EVS+EV_NPASS]
    jnz %%nu
    KSUB esi, 37, IN_UNWIN
%%nu:
    mov eax, [cnt+2]                ; no pieces at all (N B R Q of both sides)
    or eax, [cnt+10]
    jnz %%np
    KADD esi, 36, IN_NONPM
%%np:
    KSUB esi, 38, IN_BIAS           ; esi = complexity
    mov eax, esi                    ; mg: sign(mg) * clamp(c + IN_MGADD, -|mg|, 0)
    KADD eax, 39, IN_MGADD
    mov edx, r8d
    ABSV edx, ecx
    neg edx
    cmp eax, edx
    cmovl eax, edx
    xor edx, edx
    cmp eax, edx
    cmovg eax, edx
    test r8d, r8d
    jg %%m1
    jl %%m2
    xor eax, eax
    jmp %%m1
%%m2:neg eax
%%m1:add r8d, eax
    mov eax, esi                    ; eg: sign(eg) * max(c, -|eg|)
    mov edx, r9d
    ABSV edx, ecx
    neg edx
    cmp eax, edx
    cmovl eax, edx
    test r9d, r9d
    jg %%e1
    jl %%e2
    xor eax, eax
    jmp %%e1
%%e2:neg eax
%%e1:add r9d, eax
    mov eax, r8d
    mov ecx, r9d
%endmacro
; EVTOTAL: eax = White's score: packed side scores of the frame (White - Black) + material / PST + bishop pair, tapered (with EV_SCALE: the
; scale factor of the side the eg score favours).  clobbers rcx rdx rsi rdi r8 r9 (r10 r11 with EV_INIT, r14 r15 with EV_SCALE)
%macro EVTOTAL 0
    mov ecx, [rsp+2*EVS]
    sub ecx, [rsp+2*EVS+4]          ; packed White - Black
    movsx eax, cx                   ; mg part
    sub ecx, eax
    sar ecx, 16                     ; eg part
    add eax, [mgsc]
    add ecx, [egsc]
    BPAIR
%if EV_INIT
    INITIATIVE
%endif
%if EV_SCALE
    mov r14d, eax                   ; scale factor of the side the eg score favours (eg <= 0: Black)
    mov r15d, ecx
    xor r9d, r9d
    test ecx, ecx
%if EV_BRSIGN
    setle r9b
    shl r9d, 3
%else
    jg %%sw
    mov r9d, 8
%%sw:
%endif
%if EV_SFAST_ON
    ; EV_SFAST: factor 64 without ev_scale when s has pawns on both flanks (rules 1, 2, 4, the class rules and the one-flank terms
    ; cannot apply) and at least EV_SFPMIN of them (rule 6 gives 64), the board does not hold exactly one queen (rule 5) and there is
    ; no pair of opposite-coloured bishops (rule 3)
    cmp byte [cnt+r9+1], EV_SFPMIN
    jb %%slow
    mov rax, [bbp+r9*8+8]
    test rax, [ev_qside]
    jz %%slow
    test rax, [ev_kside]
    jz %%slow
    mov al, [cnt+5]
    add al, [cnt+13]
    cmp al, 1
    je %%slow
    cmp byte [cnt+3], 1
    jne %%fast
    cmp byte [cnt+11], 1
    jne %%fast
    mov rax, [bbp+24]
    test rax, [ev_dark]
    setnz al
    mov rdx, [bbp+88]
    test rdx, [ev_dark]
    setnz dl
    cmp al, dl
    jne %%slow
%%fast:
%ifdef SFASTVERIFY
    mov edi, [rsp+EV_NPASS]         ; test build: ev_scale must give 64 as well, else exit code 17
    mov esi, [rsp+EVS+EV_NPASS]
    call ev_scale
    cmp eax, 64
    je %%vok
    mov eax, 231                    ; exit_group
    mov edi, 17
    syscall
%%vok:
%endif
    mov r8d, 64
    jmp %%scaled
%%slow:
%endif
    mov edi, [rsp+EV_NPASS]
    mov esi, [rsp+EVS+EV_NPASS]
    call ev_scale
    mov r8d, eax
%if EV_SFAST_ON
%%scaled:
%endif
%if EV_SCMG
    mov r9d, 64                     ; EV_SCMG: factor of the mg part m = sf + (64 - sf) * ph / 24 (64 with sf = 64 or ph = 24)
    cmp r8d, 64
    je %%mgf
    mov eax, [phase]
    cmp eax, 24
    jge %%mgf
    mov ecx, 64
    sub ecx, r8d
    imul eax, ecx                   ; (64 - sf) * ph, 0 .. 1472
    xor edx, edx
    mov ecx, 24
    div ecx
    lea r9d, [rax+r8]
%%mgf:
    mov eax, r14d
    mov ecx, r15d
    TAPER2 r9d, r8d
%else
    mov eax, r14d
    mov ecx, r15d
    TAPER r8d
%endif
%else
    TAPER
%endif
%if EV_GRAIN
    cdq                             ; output grain: truncate to a multiple of EV_GRAINS
    mov ecx, EV_GRAINS
    idiv ecx
    imul eax, ecx
%endif
%endmacro
%macro EVCODE 1
%assign EVHW %1
; ev_side(rbx = view) -> eax = packed score (mg + 65536 * eg) of the full-evaluation terms for "us".
; clobbers rcx rdx rsi rdi rbp r8-r15 (eval saves them)
ev_side%1:
    sub rsp, 32                     ; [rsp] = squares our pawns attack, [rsp+8] = squares their pawns attack, [rsp+16] = outpost squares, [rsp+24] temp
    xor r15d, r15d                  ; packed score
%if EV_KDANGER == 0
    xor r12d, r12d                  ; king attack units
%endif
    mov rbp, [rbx+EV_OCC]
    mov rsi, [rbx+EV_P]             ; rsi = our pawns (kept)
    mov rdi, [rbx+EV_TP]            ; their pawns
    mov rax, rsi
    and rax, [k_nfa]
    shl rax, 7
    mov rcx, rsi
    and rcx, [k_nfh]
    shl rcx, 9
    or rax, rcx
    mov [rsp], rax
    mov rax, rdi
    and rax, [k_nfa]
    shr rax, 9
    mov rcx, rdi
    and rcx, [k_nfh]
    shr rcx, 7
    or rax, rcx
    mov [rsp+8], rax
    mov r14, [rbx+EV_ALL]
    or r14, rax
    not r14                         ; r14 = mobility area: not our piece, not attacked by their pawns
%if EV_KDANGER == 0
    mov ecx, [rbx+EV_TKSQ]
    mov r13, [king_att+rcx*8]
    bts r13, rcx                    ; r13 = their king zone
%endif
%if EV_ATTMAPS
    mov rax, [rsp]                  ; maps: pawns, king; squares attacked by two pawns or by a pawn and the king
    mov [rbx+EV_AP], rax
    mov ecx, [rbx+EV_KSQ]
    mov rdx, [king_att+rcx*8]
    mov [rbx+EV_AK], rdx
    mov rcx, rsi
    and rcx, [k_nfa]
    shl rcx, 7
    mov r8, rsi
    and r8, [k_nfh]
    shl r8, 9
    and rcx, r8
    and rdx, rax
    or rcx, rdx
    mov [rbx+EV_A2], rcx
    or rax, [rbx+EV_AK]
    mov [rbx+EV_AALL], rax
    xor eax, eax
    mov [rbx+EV_AN], rax
    mov [rbx+EV_AB], rax
    mov [rbx+EV_AR], rax
    mov [rbx+EV_AQ], rax
%endif
%if EV_KDANGER
    mov ecx, [rbx+EV_TKSQ]          ; king ring of their king
    mov rax, [king_att+rcx*8]
    mov [rbx+EV_TKZ], rax
    mov eax, ecx
    and eax, 7
    mov edx, 1
    cmp eax, edx
    cmovl eax, edx
    mov edx, 6
    cmp eax, edx
    cmovg eax, edx
    shr ecx, 3
    mov edx, 1
    cmp ecx, edx
    cmovl ecx, edx
    mov edx, 6
    cmp ecx, edx
    cmovg ecx, edx
    lea ecx, [rax+rcx*8]
    mov rax, [king_att+rcx*8]
    bts rax, rcx
    mov rdx, rdi                    ; minus the squares two of their pawns defend
    and rdx, [k_nfa]
    shr rdx, 9
    mov r8, rdi
    and r8, [k_nfh]
    shr r8, 7
    and rdx, r8
    not rdx
    and rax, rdx
    mov [rbx+EV_RING], rax
    mov dword [rbx+EV_KDW], 0
    mov dword [rbx+EV_KDA], 0
%endif
%if EV_PHASH
    cmp dword [rbx+EV_PHIT], 0      ; pawn part from the pawn cache
    je .pmiss
    mov r15d, [rbx+EV_PSC]
    mov r9, [rbx+EV_PPB]
    jmp .phit
.pmiss:
%endif
    ; ---- pawn structure
    mov rax, rsi                    ; doubled: an own pawn behind it on the file
    shl rax, 8
    NFILL rax, rcx
    and rax, rsi
    POPC r10, rax, r11
    TERM EV_DOUBLED, r10
    mov rax, rsi                    ; isolated: no own pawn on an adjacent file
    NFILL rax, rcx
    mov rdx, rsi
    SFILL rdx, rcx
    or rax, rdx                     ; files with an own pawn
    mov rdx, rax
    and rdx, [k_nfh]
    shl rdx, 1
    and rax, [k_nfa]
    shr rax, 1
    or rax, rdx                     ; the adjacent files
    not rax
    and rax, rsi
    POPC r10, rax, r11
    TERM EV_ISOLATED, r10
    mov rax, rsi                    ; supported: defended by an own pawn
    and rax, [rsp]
    POPC r10, rax, r11
    TERM EV_SUPPORT, r10
    mov rax, rsi                    ; phalanx: an own pawn beside it
    and rax, [k_nfh]
    shl rax, 1
    mov rdx, rsi
    and rdx, [k_nfa]
    shr rdx, 1
    or rax, rdx
    and rax, rsi
    POPC r10, rax, r11
    TERM EV_PHALANX, r10
%if EV_PAWNS2
    mov rax, rsi                    ; backward: no own pawn level or behind on an adjacent file, the square in front attacked by their pawn
    NFILL rax, rcx
    ADJF rax, rdx
    not rax
    and rax, rsi
    mov rdx, [rsp+8]
    shr rdx, 8
    and rax, rdx
    mov r8, rax
    POPC r10, rax, r11
    TERM EV_BACKW, r10
    mov rax, rsi                    ; weak unopposed: isolated or backward, no enemy pawn in front on the file
    NFILL rax, rcx
    mov rdx, rsi
    SFILL rdx, rcx
    or rax, rdx
    ADJF rax, rdx
    not rax
    and rax, rsi
    or r8, rax
    mov rax, rdi
    shr rax, 8
    SFILL rax, rcx
    not rax
    and r8, rax
    POPC r10, r8, r11
    TERM EV_WUNOPP, r10
    mov rax, rdi                    ; weak lever: attacked by two enemy pawns, not defended by an own pawn
    and rax, [k_nfa]
    shr rax, 9
    mov rdx, rdi
    and rdx, [k_nfh]
    shr rdx, 7
    and rax, rdx
    and rax, rsi
    mov rdx, [rsp]
    not rdx
    and rax, rdx
    POPC r10, rax, r11
    TERM EV_WLEVER, r10
    mov rax, rsi                    ; connected (supported or phalanx), per rank
    ADJF rax, rdx
    or rax, [rsp]
    and rax, rsi
    mov r8, rax
.cr:test r8, r8
    jz .crd
    bsf rcx, r8
    lea rax, [r8-1]
    and r8, rax
    shr ecx, 3
    add r15d, [evp+(EV_CONN-1)*4+rcx*4]
    jmp .cr
.crd:
    mov rax, rdi                    ; blocked by an enemy pawn on rank 5 / 6
    shr rax, 8
    and rax, rsi
    mov rdx, rax
    and rax, [ev_r5]
    POPC r10, rax, r11
    TERM EV_BLOCK56, r10
    and rdx, [ev_r6]
    POPC r10, rdx, r11
    TERM EV_BLOCK56+1, r10
%endif
    ; ---- passed pawns: no enemy pawn in front on the same or an adjacent file; ranks 2..7 only
    mov rax, rdi
    shr rax, 8
    SFILL rax, rcx                  ; squares behind (south of) an enemy pawn on its file
    mov rdx, rax
    and rdx, [k_nfa]
    shr rdx, 1
    mov rcx, rax
    and rcx, [k_nfh]
    shl rcx, 1
    or rax, rdx
    or rax, rcx
    not rax
    and rax, rsi
    and rax, [k_nr1]
    and rax, [k_nr8]
    mov r9, rax
%if EV_PHASH
    mov [rbx+EV_PSC], r15d
    mov [rbx+EV_PPB], r9
.phit:
%elif EV_PASSED2 || EV_PROOK
    mov [rbx+EV_PPB], r9
%endif
%if EV_SCALE || EV_INIT
    mov dword [rbx+EV_NPASS], 0
%endif
.pp:test r9, r9
    jz .ppd
    bsf rcx, r9
    lea rax, [r9-1]
    and r9, rax
    mov eax, ecx
    shr eax, 3                      ; rank 1..6
    add r15d, [evp+(EV_PASSED-1)*4+rax*4]
%if EV_SCALE || EV_INIT
    inc dword [rbx+EV_NPASS]
%endif
    add ecx, 8                      ; the square in front of the pawn
    mov edx, [rbx+EV_KSQ]
    DIST ecx, edx
    add r15d, [evp+EV_PPOWN*4+rax*4]
    mov edx, [rbx+EV_TKSQ]
    DIST ecx, edx
    add r15d, [evp+EV_PPTHEM*4+rax*4]
    jmp .pp
.ppd:
    ; ---- own pawn attacks an enemy knight, bishop, rook or queen
    mov rax, [rbx+EV_TN]
    or rax, [rbx+EV_TB]
    or rax, [rbx+EV_TR]
    or rax, [rbx+EV_TQ]
    and rax, [rsp]
    POPC r10, rax, r11
    TERM EV_THREATP, r10
%if EV_SHELTER
    ; ---- king shelter / storm on the three files around the king (the better one of the castled squares in reach, by middlegame value),
    ;      king distance to the nearest own pawn
    mov eax, [rbx+EV_KSQ]
    SHELTERV
    mov eax, [rsp+16]
    mov [rsp+24], eax
    test dword [rbx+EV_CR], 1
    jz .shk
    SHELTERALT 6                    ; g1
.shk:test dword [rbx+EV_CR], 2
    jz .shq
    SHELTERALT 2                    ; c1
.shq:mov eax, [rsp+24]
    add r15d, eax
    mov r8, rsi
    test r8, r8
    jz .kpd
    mov edx, [rbx+EV_KSQ]
    mov r9d, 1                      ; a pawn next to the king: distance 1, no loop
    test r8, [king_att+rdx*8]
    jnz .kpe
    mov r9d, 7
.kp:bsf rcx, r8
    lea rax, [r8-1]
    and r8, rax
    call ev_dist
    cmp eax, r9d
    cmovb r9d, eax
    test r8, r8
    jnz .kp
.kpe:imul r9d, [evp+EV_KPDIST*4]
    add r15d, r9d
.kpd:
%else
    ; ---- king shield: own pawns on the three files around the king, one and two ranks in front
    mov ecx, [rbx+EV_KSQ]
    xor eax, eax
    bts rax, rcx
    mov rdx, rax
    and rdx, [k_nfh]
    shl rdx, 1
    mov rcx, rax
    and rcx, [k_nfa]
    shr rcx, 1
    or rax, rdx
    or rax, rcx
    mov rdx, rax
    shl rax, 8
    shl rdx, 16
    or rax, rdx
    and rax, rsi
    POPC r10, rax, r11
    TERM EV_SHIELD, r10
%endif
    ; ---- knight outposts: ranks 4..6, defended by an own pawn, out of reach of every enemy pawn
    mov rax, [rsp+8]
    SFILL rax, rcx                  ; squares their pawns attack now or can attack later
    not rax
    and rax, [rsp]
    and rax, [ev_outr]
%if EV_PBISH || EV_PMINOR
    mov [rsp+16], rax
%endif
    and rax, [rbx+EV_NB]
    POPC r10, rax, r11
    TERM EV_OUTPOST, r10
%if EV_PMINOR
    mov rax, [rbx+EV_NB]            ; minor pieces with a pawn right in front
    or rax, [rbx+EV_BB]
    shl rax, 8
    mov rdx, rsi
    or rdx, [rbx+EV_TP]
    and rax, rdx
    POPC r10, rax, r11
    TERM EV_MBP, r10
%endif
%if EV_PINCALC
    mov qword [rbx+EV_PIN], 0       ; ---- pinned pieces
    mov r9d, [rbx+EV_KSQ]
    mov r8, [rbx+EV_TR]
    or r8, [rbx+EV_TQ]
    PINS rtab
    mov r8, [rbx+EV_TB]
    or r8, [rbx+EV_TQ]
    PINS btab
%endif
%if EV_PINS
    mov rax, rbp                    ; mobility area
    shr rax, 8
    or rax, [ev_r23]
    and rax, rsi                    ; our pawns: blocked, or on ranks 2-3
    or rax, [rbx+EV_KB]
    or rax, [rbx+EV_QB]
    or rax, [rbx+EV_PIN]
    or rax, [rsp+8]
    not rax
    mov r14, rax
%endif
%if EV_THRQUEEN
    mov [rbx+EV_MAREA], r14
%endif
    ; ---- knights
    mov rdi, [rbx+EV_NB]
.kn:test rdi, rdi
    jz .knd
    bsf rcx, rdi
    lea rax, [rdi-1]
    and rdi, rax
    mov rax, [knight_att+rcx*8]
    ATTMAP EV_AN
    KDACC 2
%if EV_PMINOR
    mov r8, rax
    mov edx, [rbx+EV_KSQ]           ; king protector
    call ev_dist
    imul eax, [evp+EV_KPROTN*4]
    add r15d, eax
    mov rax, [rsp+16]               ; reachable outpost (when not on one)
    bt rax, rcx
    jc .kn2
    and rax, r8
    mov rdx, [rbx+EV_ALL]
    not rdx
    test rax, rdx
    jz .kn2
    add r15d, [evp+EV_REACH*4]
.kn2:mov rax, r8
%endif
%if EV_KDANGER == 0
    test rax, r13
    jz .kn1
    add r12d, 2
%endif
.kn1:and rax, r14
    POPC r10, rax, r11
    add r15d, [evp+EV_MOBN_U*4+r10*4]
    jmp .kn
.knd:
    ; ---- bishops
    mov rdi, [rbx+EV_BB]
.bi:test rdi, rdi
    jz .bid
    bsf r9, rdi
    lea rax, [rdi-1]
    and rdi, rax
    SLATK btab, r9
    ATTMAP EV_AB
    KDACC 2
%if EV_PBISH
    mov r8, rax
    mov rax, [ev_dark]              ; own pawns on the bishop's colour
    bt rax, r9
    jc .bp1
    not rax
.bp1:and rax, rsi
    POPC r10, rax, r11
    TERM EV_BPAWNS, r10
    mov rax, [rsp+16]               ; outpost
    bt rax, r9
    jnc .bp2
    add r15d, [evp+EV_BOUTP*4]
.bp2:mov r10, rbp                   ; long diagonal: two centre squares through pawns only
    mov rbp, rsi
    or rbp, [rbx+EV_TP]
    SLATK btab, r9
    mov rbp, r10
    and rax, [ev_center]
    POPC r10, rax, r11
    cmp r10d, 2
    jb .bp3
    add r15d, [evp+EV_LDIAG*4]
.bp3:mov rax, r8
%endif
%if EV_PMINOR
    mov r8, rax
    mov ecx, r9d                    ; king protector
    mov edx, [rbx+EV_KSQ]
    call ev_dist
    imul eax, [evp+EV_KPROTB*4]
    add r15d, eax
    mov rax, r8
%endif
%if EV_KDANGER == 0
    test rax, r13
    jz .bi1
    add r12d, 2
%endif
.bi1:and rax, r14
    POPC r10, rax, r11
    add r15d, [evp+EV_MOBB_U*4+r10*4]
    jmp .bi
.bid:
    ; ---- rooks (file first: SLATK clobbers rcx / rdx)
    mov rdi, [rbx+EV_RB]
.ro:test rdi, rdi
    jz .rod
    bsf r9, rdi
    lea rax, [rdi-1]
    and rdi, rax
    mov ecx, r9d
    and ecx, 7
    mov rdx, [ev_filea]
    shl rdx, cl                     ; the rook's file
%if EV_PROOK
    xor r8d, r8d                    ; r8d = 1: own pawn on the file
    mov rax, [rbx+EV_PPB]           ; behind an own passed pawn
    and rax, rdx
    mov ecx, r9d
    shr rax, cl
    shr rax, 1
    jz .rb0
    add r15d, [evp+EV_RBP*4]
.rb0:mov eax, r9d                   ; rank 7, their king on rank 8 or their pawns on rank 7
    shr eax, 3
    cmp eax, 6
    jne .r7n
    mov eax, [rbx+EV_TKSQ]
    shr eax, 3
    cmp eax, 7
    je .r7y
    mov rax, [rbx+EV_TP]
    test rax, [ev_r7]
    jz .r7n
.r7y:add r15d, [evp+EV_R7*4]
.r7n:
%endif
    test rdx, rsi
%if EV_PROOK
    jnz .roc
%else
    jnz .ro2                        ; an own pawn on the file
%endif
    test rdx, [rbx+EV_TP]
    jnz .ro1
    add r15d, [evp+EV_ROPEN*4]
    jmp .ro2
.ro1:add r15d, [evp+EV_RSEMI*4]
%if EV_PROOK
    jmp .ro2
.roc:mov r8d, 1                     ; own pawn on the file: closed when that pawn is blocked
    mov rax, rbp
    shr rax, 8
    and rax, rsi
    test rax, rdx
    jz .ro2
    add r15d, [evp+EV_RCLOSED*4]
%endif
.ro2:SLATK rtab, r9
    ATTMAP EV_AR
    KDACC 3
%if EV_PROOK
    mov rdx, rax                    ; connected: attacks an own rook
    and rdx, [rbx+EV_RB]
    POPC r10, rdx, r11
    TERM EV_RCONN, r10
%endif
%if EV_KDANGER == 0
    test rax, r13
    jz .ro3
    add r12d, 3
%endif
.ro3:and rax, r14
    POPC r10, rax, r11
    add r15d, [evp+EV_MOBR_U*4+r10*4]
%if EV_PROOK
    test r8d, r8d                   ; trapped: own pawn on the file, mobility <= 3, on the far side of the king
    jz .ro
    cmp r10d, 3
    ja .ro
    mov eax, [rbx+EV_KSQ]
    and eax, 7
    mov ecx, r9d
    and ecx, 7
    xor edx, edx
    cmp eax, 4
    setb dl
    xor r11d, r11d
    cmp ecx, eax
    setb r11b
    cmp edx, r11d
    jne .ro
    add r15d, [evp+EV_TRAPR*4]
%endif
    jmp .ro
.rod:
    ; ---- queens
    mov rdi, [rbx+EV_QB]
.qu:test rdi, rdi
    jz .qud
    bsf r9, rdi
    lea rax, [rdi-1]
    and rdi, rax
    SLATK rtab, r9
    mov r8, rax
%if EV_PQUEEN
    xor r10d, r10d                  ; weak queen: an enemy rook behind exactly one piece on a rook line
    mov r11, r8
    and r11, rbp
    xor rbp, r11
    SLATK rtab, r9
    xor rbp, r11
    mov rdx, r8
    not rdx
    and rax, rdx
    test rax, [rbx+EV_TR]
    setnz r10b
%endif
    SLATK btab, r9
%if EV_PQUEEN
    mov [rsp+24], rax               ; or an enemy bishop on a bishop line
    mov r11, rax
    and r11, rbp
    xor rbp, r11
    SLATK btab, r9
    xor rbp, r11
    mov rdx, [rsp+24]
    not rdx
    and rax, rdx
    test rax, [rbx+EV_TB]
    jz .wq1
    mov r10d, 1
.wq1:imul r10d, [evp+EV_WEAKQ*4]
    add r15d, r10d
    mov rax, [rsp+24]
%endif
    or rax, r8
    ATTMAP EV_AQ
    KDACC 5
%if EV_KDANGER == 0
    test rax, r13
    jz .qu1
    add r12d, 5
%endif
.qu1:and rax, r14
    POPC r10, rax, r11
    add r15d, [evp+EV_MOBQ_U*4+r10*4]
    jmp .qu
.qud:
%if EV_KDANGER == 0
    ; ---- king attack: counted only while we have a queen; units 0..31 (EV_KDANGER replaces it)
    cmp qword [rbx+EV_QB], 0
    jne .kq
    xor r12d, r12d
.kq:mov eax, 31
    cmp r12d, eax
    cmova r12d, eax
    add r15d, [evp+EV_KATT*4+r12*4]
%endif
    mov eax, r15d
    add rsp, 32
    ret

%if EV_PASSB
; ev_sideb(rbx = view) -> eax = packed score of the second-pass terms for "us" (the attack maps of both sides are in the view).
; clobbers rcx rdx rsi rdi rbp r8-r15
ev_sideb%1:
    xor r15d, r15d
    mov r8, [rbx+EV_A2]
    not r8
    and r8, [rbx+EV_TA2]
    or r8, [rbx+EV_TAP]             ; r8 = squares they protect strongly
    mov r12, [rbx+EV_TALL]
    xor r12, [rbx+EV_TK]            ; r12 = their pieces and pawns without the king
    mov r13, r12
    xor r13, [rbx+EV_TP]            ; r13 = their pieces without pawns and king
    mov r9, r8
    not r9
    and r9, r12
    and r9, [rbx+EV_AALL]           ; r9 = weak: attacked by us, not strongly protected
%if EV_THRMIN
    mov r10, r13
    and r10, r8
    or r10, r9                      ; defended pieces and weak ones
    mov r11, [rbx+EV_AN]
    or r11, [rbx+EV_AB]
    and r10, r11                    ; attacked by our minor pieces
    xor ecx, ecx
.tm:mov rax, [rbx+EV_TP+rcx*8]      ; per type P N B R Q
    and rax, r10
    POPC r14, rax, rsi
    imul r14d, [evp+EV_TMINOR*4+rcx*4]
    add r15d, r14d
    mov rax, [rbx+EV_TP+rcx*8]
    and rax, r9
    and rax, [rbx+EV_AR]
    POPC r14, rax, rsi
    imul r14d, [evp+EV_TROOK*4+rcx*4]
    add r15d, r14d
    inc ecx
    cmp ecx, 5
    jb .tm
    mov rax, r9
    and rax, [rbx+EV_AK]
    POPC r14, rax, rsi
    TERM EV_TKING, r14
%endif
%if EV_THRHANG
    mov rax, r13
    and rax, [rbx+EV_A2]
    mov rdx, [rbx+EV_TAALL]
    not rdx
    or rax, rdx
    and rax, r9
    POPC r14, rax, rsi
    TERM EV_HANG, r14
    mov rax, r9
    and rax, [rbx+EV_TAQ]
    POPC r14, rax, rsi
    TERM EV_WQP, r14
%endif
%if EV_THRRESTR
    mov rax, r8
    not rax
    and rax, [rbx+EV_TAALL]
    and rax, [rbx+EV_AALL]
    POPC r14, rax, rsi
    TERM EV_RESTR, r14
%endif
%if EV_THRPAWN
    mov r10, [rbx+EV_TAALL]
    not r10
    or r10, [rbx+EV_AALL]           ; r10 = safe squares
    mov rax, [rbx+EV_P]
    and rax, r10
    PATT rax, rdx
    and rax, r13
    POPC r14, rax, rsi
    TERM EV_TSAFEP, r14
    mov rdx, [rbx+EV_OCC]
    not rdx                         ; empty squares
    mov rax, [rbx+EV_P]
    shl rax, 8
    and rax, rdx                    ; one step
    mov rcx, rax
    and rcx, [k_r3]
    shl rcx, 8
    and rcx, rdx                    ; two steps
    or rax, rcx
    mov rcx, [rbx+EV_TAP]
    not rcx
    and rax, rcx
    and rax, r10
    PATT rax, rdx
    and rax, r13
    POPC r14, rax, rsi
    TERM EV_TPUSH, r14
%endif
%if EV_THRQUEEN
    mov rax, [rbx+EV_TQ]            ; exactly one enemy queen
    test rax, rax
    jz .nq
    lea rdx, [rax-1]
    test rax, rdx
    jnz .nq
    bsf r9, rax
    xor r11d, r11d
    cmp qword [rbx+EV_QB], 0
    sete r11b
    inc r11d                        ; 1 + (the only queen on the board)
    mov r10, r8
    not r10
    and r10, [rbx+EV_MAREA]         ; safe: our mobility area, not strongly protected
    mov rax, [knight_att+r9*8]
    and rax, [rbx+EV_AN]
    and rax, r10
    POPC r14, rax, rsi
    imul r14d, r11d
    TERM EV_KONQ, r14
    mov rbp, [rbx+EV_OCC]
    SLATK btab, r9
    and rax, [rbx+EV_AB]
    mov rdi, rax
    SLATK rtab, r9
    and rax, [rbx+EV_AR]
    or rax, rdi
    and rax, r10
    and rax, [rbx+EV_A2]
    POPC r14, rax, rsi
    imul r14d, r11d
    TERM EV_SONQ, r14
.nq:
%endif
%if EV_KDANGER
    ; ---- king danger of their king (we attack)
    xor r10d, r10d
    cmp qword [rbx+EV_QB], 0
    je .kdv                         ; without our queen: index 0
    mov r10d, [rbx+EV_KDW]
    add r10d, [rbx+EV_KDA]
    mov r8, [rbx+EV_TA2]            ; r8 = weak: we attack, they defend at most once and only with king / queen (or not at all)
    not r8
    and r8, [rbx+EV_AALL]
    mov rax, [rbx+EV_TAK]
    or rax, [rbx+EV_TAQ]
    mov rdx, [rbx+EV_TAALL]
    not rdx
    or rax, rdx
    and r8, rax
    mov rax, r8
    and rax, [rbx+EV_RING]
    POPC r14, rax, rsi
    KMUL r14d, 20, KD_WEAK
    add r10d, r14d
    mov r9, r8                      ; r9 = safe for a check: not our piece; not attacked by them, or weak and attacked twice by us
    and r9, [rbx+EV_A2]
    mov rax, [rbx+EV_TAALL]
    not rax
    or r9, rax
    mov rax, [rbx+EV_ALL]
    not rax
    and r9, rax
    mov rbp, [rbx+EV_OCC]
    mov r12d, [rbx+EV_TKSQ]
    SLATK rtab, r12
    mov r11, rax                    ; r11 = rook lines from their king
    SLATK btab, r12
    mov r13, rax                    ; r13 = bishop lines
    mov rdi, r11
    and rdi, [rbx+EV_AR]
    mov rax, rdi
    and rax, r9                     ; safe rook checks
    mov r12, rax
    xor ecx, ecx
    test rax, rax
    setnz cl
    KMUL ecx, 22, KD_RCHK
    add r10d, ecx
    mov rax, r11
    or rax, r13
    and rax, [rbx+EV_AQ]
    and rax, r9
    mov rdx, [rbx+EV_TAQ]
    or rdx, r12
    not rdx
    and rax, rdx                    ; safe queen checks (not where their queen defends, not where a rook checks)
    mov rdx, rax
    xor ecx, ecx
    test rax, rax
    setnz cl
    KMUL ecx, 21, KD_QCHK
    add r10d, ecx
    mov rax, r13
    and rax, [rbx+EV_AB]
    or rdi, rax                     ; rdi = rook and bishop checks (safe or not)
    and rax, r9
    not rdx
    and rax, rdx                    ; safe bishop checks (not where a queen checks)
    xor ecx, ecx
    test rax, rax
    setnz cl
    KMUL ecx, 23, KD_BCHK
    add r10d, ecx
    mov ecx, [rbx+EV_TKSQ]
    mov rax, [knight_att+rcx*8]
    and rax, [rbx+EV_AN]
    or rdi, rax                     ; + knight checks
    and rax, r9
    xor ecx, ecx
    test rax, rax
    setnz cl
    KMUL ecx, 24, KD_NCHK
    add r10d, ecx
    mov rax, r9
    not rax
    and rdi, rax                    ; unsafe checks
    xor ecx, ecx
    test rdi, rdi
    setnz cl
    KMUL ecx, 25, KD_UNSAFE
    add r10d, ecx
    mov rax, [rbx+EV_TPIN]
    POPC r14, rax, rsi
    KMUL r14d, 26, KD_PIN
    add r10d, r14d
    mov rax, [rbx+EV_TAN]
    and rax, [rbx+EV_TKZ]
    jz .kd1
    KSUB r10d, 27, KD_KNDEF
.kd1:xor eax, eax
    cmp r10d, eax
    cmovl r10d, eax
    mov eax, 63
    cmp r10d, eax
    cmova r10d, eax
.kdv:add r15d, [evp+EV_KDANG*4+r10*4]
%endif
%if EV_SPACE
    ; ---- space
    movzx eax, byte [cnt+2]         ; non-pawn material of both sides
    movzx ecx, byte [cnt+3]
    add eax, ecx
    movzx ecx, byte [cnt+10]
    add eax, ecx
    movzx ecx, byte [cnt+11]
    add eax, ecx
    lea eax, [rax+rax*2]
    movzx ecx, byte [cnt+4]
    movzx edx, byte [cnt+12]
    add ecx, edx
    lea ecx, [rcx+rcx*4]
    add eax, ecx
    movzx ecx, byte [cnt+5]
    movzx edx, byte [cnt+13]
    add ecx, edx
    lea ecx, [rcx+rcx*8]
    add eax, ecx
    cmp eax, EV_SPACENPM
    jl .spd
    mov rax, [rbx+EV_TAP]
    or rax, [rbx+EV_P]
    not rax
    and rax, [ev_space]             ; safe
    mov rdx, [rbx+EV_P]
    mov rcx, rdx
    shr rcx, 8
    or rdx, rcx
    mov rcx, rdx
    shr rcx, 16
    or rdx, rcx                     ; up to three squares behind our pawns
    and rdx, rax
    mov rcx, [rbx+EV_TAALL]
    not rcx
    and rdx, rcx
    POPC r14, rax, rsi
    POPC r10, rdx, rsi
    add r14d, r10d                  ; bonus
    mov rax, [rbx+EV_ALL]
    POPC r10, rax, rsi              ; our pieces
    mov rax, [rbx+EV_P]
    shl rax, 8
    and rax, [rbx+EV_TP]
    POPC r11, rax, rsi
    add r11d, r11d                  ; blocked pawns of both sides
    mov eax, 9
    cmp r11d, eax
    cmova r11d, eax
    lea r10d, [r10+r11-3]           ; w
    imul r10d, r10d
    imul r14d, r10d
    shr r14d, 10
    TERM EV_SPACE_T, r14
.spd:
%endif
%if EV_KLINES
    ; ---- lines against their king (attacker's view)
    mov eax, [rbx+EV_TKSQ]
    mov ecx, eax
    shr ecx, 3                      ; ecx = rank of their king
    and eax, 7                      ; eax = its file, clamped to 1..6
    mov edx, 1
    cmp eax, edx
    cmovb eax, edx
    mov edx, 6
    cmp eax, edx
    cmova eax, edx
    mov ebp, ecx                    ; ebp = their king's rank
    shl ecx, 3
    mov r8d, 1
    shl r8, cl
    dec r8                          ; r8 = squares below their king
%if EV_KLBITS
    lea ecx, [rax-1]
    mov edi, 7
    shl edi, cl                     ; edi = the three files (one bit per file)
    mov r11, [rbx+EV_P]
    and r11, r8                     ; r11 = our pawns below their king
    mov r12, [rbx+EV_TP]
    mov rdx, r12
    and rdx, r8                     ; their pawns below their king
    FILEFOLD rdx, rcx
    not edx
    and edx, edi                    ; edx = files without their pawn below the king
    mov r13, r11
    FILEFOLD r13, rcx
    not r13d
    and r13d, edx                   ; ... and without ours
    POPC r14, r13, rsi
    TERM EV_KLOPEN, r14
    mov rcx, 0x0101010101010101
    imul rdx, rcx                   ; file bits -> whole files
    imul rdi, rcx
    mov rax, [rbx+EV_RB]
    or rax, [rbx+EV_QB]
    and rax, rdx
    POPC r14, rax, rsi
    TERM EV_KLHEAVY, r14
    shr r8, 24                      ; squares more than 3 ranks below their king
    not r8
    and r11, r8                     ; our pawns 1..3 ranks below
    mov rax, r11
    shr rax, 8
    mov rdx, r11
    shr rdx, 16
    or rax, rdx
    not rax
    and r11, rax                    ; the one nearest to the king on each file
    shr r12, 8
    not r12
    and r11, r12                    ; not blocked by their pawn
    and r11, rdi
    POPC r14, r11, rsi
    TERM EV_KLSTORM, r14
%else
    mov r9, [rbx+EV_P]
    and r9, r8                      ; r9 = our pawns below their king
    and r8, [rbx+EV_TP]             ; r8 = their pawns below their king
    mov r10, [rbx+EV_RB]
    or r10, [rbx+EV_QB]             ; r10 = our rooks and queens
    lea ecx, [rax-1]
    mov rdi, 0x0101010101010101
    shl rdi, cl                     ; rdi = first of the three files
    xor r11d, r11d                  ; kl_open
    xor r12d, r12d                  ; kl_storm
    xor r13d, r13d                  ; kl_heavy
    mov r14d, 3
.kl:test r8, rdi
    jnz .kl2                        ; their pawn below the king on this file
    mov rax, r10
    and rax, rdi
    POPC rax, rax, rsi
    add r13d, eax
    test r9, rdi
    jnz .kl2
    inc r11d                        ; no pawn of either side below their king
.kl2:mov rax, r9
    and rax, rdi
    jz .kl3
    bsr rax, rax                    ; our pawn nearest to their king
    mov edx, eax
    shr edx, 3
    neg edx
    add edx, ebp
    cmp edx, 3
    ja .kl3                         ; more than 3 ranks below their king
    mov rdx, [rbx+EV_TP]
    lea ecx, [rax+8]
    shr rdx, cl
    test edx, 1
    jnz .kl3                        ; blocked by their pawn
    inc r12d
.kl3:shl rdi, 1
    dec r14d
    jnz .kl
    TERM EV_KLOPEN, r11
    TERM EV_KLSTORM, r12
    TERM EV_KLHEAVY, r13
%endif
%endif
%if EV_PASSED2
    ; ---- passed pawns, second pass
    mov rdi, [rbx+EV_PPB]
.p2:test rdi, rdi
    jz .p2d
    bsf r9, rdi
    lea rax, [rdi-1]
    and rdi, rax
    mov eax, r9d                    ; edge distance of the file
    and eax, 7
    mov ecx, 7
    sub ecx, eax
    cmp eax, ecx
    cmovg eax, ecx
    imul eax, [evp+EV_PFILE*4]
    add r15d, eax
    mov r10d, r9d
    shr r10d, 3                     ; rank index 1..6 (rank 2..7)
    cmp r10d, 3
    jb .p2
    lea r11d, [r10+r10*4-13]        ; w
    lea r12d, [r9+8]                ; the square in front
    mov ecx, r12d
    mov edx, [rbx+EV_TKSQ]
    call ev_dist
    mov edx, 5
    cmp eax, edx
    cmova eax, edx
    imul eax, r11d
    imul eax, [evp+EV_PKTHEM*4]
    add r15d, eax
    mov edx, [rbx+EV_KSQ]
    call ev_dist
    mov r8d, 5
    cmp eax, r8d
    cmova eax, r8d
    imul eax, r11d
    imul eax, [evp+EV_PKUS*4]
    add r15d, eax
    cmp r10d, 6
    je .p2n
    lea ecx, [r12+8]
    call ev_dist
    cmp eax, r8d
    cmova eax, r8d
    imul eax, r11d
    imul eax, [evp+EV_PKUS2*4]
    add r15d, eax
.p2n:
    mov rax, [rbx+EV_OCC]
    bt rax, r12
    jc .p2                          ; the square in front is occupied
    mov ecx, r9d
    and ecx, 7
    mov r13, [ev_filea]
    shl r13, cl                     ; the pawn's file
    mov rdx, -2
    mov ecx, r9d
    shl rdx, cl                     ; squares above the pawn
    mov r14, r13
    and r13, rdx                    ; r13 = squares to queen
    not rdx
    and r14, rdx
    btr r14, r9                     ; r14 = squares behind the pawn on its file
    mov rax, r13                    ; span: the file and the adjacent files in front
    and rax, [k_nfh]
    shl rax, 1
    mov rdx, r13
    and rdx, [k_nfa]
    shr rdx, 1
    or rax, rdx
    or rax, r13
    mov rdx, [rbx+EV_TR]
    or rdx, [rbx+EV_TQ]
    test rdx, r14
    jnz .p2u                        ; their rook / queen behind: the whole span is unsafe
    mov rdx, [rbx+EV_TAALL]
    or rdx, [rbx+EV_TALL]
    and rax, rdx
.p2u:
    xor ecx, ecx                    ; class 0..3, 4 = none
    test rax, rax
    jz .p2c
    inc ecx
    mov rdx, [rbx+EV_AP]
    not rdx
    test rax, rdx
    jz .p2c
    inc ecx
    test rax, r13
    jz .p2c
    inc ecx
    bt rax, r12
    jnc .p2c
    inc ecx
.p2c:
    cmp ecx, 4
    je .p2e
    mov eax, r11d
    imul eax, [evp+EV_PFREE*4+rcx*4]
    add r15d, eax
.p2e:
    mov rax, [rbx+EV_RB]            ; defended square in front, or our rook / queen behind
    or rax, [rbx+EV_QB]
    test rax, r14
    jnz .p2f
    mov rax, [rbx+EV_AALL]
    bt rax, r12
    jnc .p2
.p2f:
    mov eax, r11d
    imul eax, [evp+EV_PFDEF*4]
    add r15d, eax
    jmp .p2
.p2d:
%endif
    mov eax, r15d
    ret
%endif

; ============================================================
; ev_eval%1 -> eax = static evaluation, side-to-move view (full evaluation).  Preserves every register except rax, rcx, rdx, rsi.
; ============================================================
ev_eval%1:
%if EV_LAZY
    mov eax, [mgsc]                 ; lazy evaluation: material + PST + bishop pair alone when far from 0
    mov ecx, [egsc]
    BPAIR
    TAPER
    mov edx, eax
    neg edx
    cmovs edx, eax
    KCMP edx, 28, EV_LAZYT
    jle .nlz
    cmp dword [stm], 0
    je .lzw
    neg eax
.lzw:add eax, TEMPO
    ret
.nlz:
%endif
    push rbx
    push rbp
    push rdi
    push r8
    push r9
    push r10
    push r11
    push r12
    push r13
    push r14
    push r15
    sub rsp, 2*EVS+32
    ; [rsp] = White's view; [rsp+EVS] = Black's view: the board mirrored vertically (bswap), colours exchanged
    xor ecx, ecx
.cp:mov rax, [bbp+8+rcx*8]          ; white P N B R Q K (piece codes 1..6)
    mov [rsp+EV_P+rcx*8], rax
    bswap rax
    mov [rsp+EVS+EV_TP+rcx*8], rax
    mov rax, [bbp+72+rcx*8]         ; black p n b r q k (piece codes 9..14)
    mov [rsp+EV_TP+rcx*8], rax
    bswap rax
    mov [rsp+EVS+EV_P+rcx*8], rax
    inc ecx
    cmp ecx, 6
    jb .cp
    mov rax, [bbc]
    mov [rsp+EV_ALL], rax
    bswap rax
    mov [rsp+EVS+EV_TALL], rax
    mov rdx, [bbc+8]
    mov [rsp+EV_TALL], rdx
    bswap rdx
    mov [rsp+EVS+EV_ALL], rdx
    or rax, rdx
    mov [rsp+EVS+EV_OCC], rax
    mov rax, [bbc]
    or rax, [bbc+8]
    mov [rsp+EV_OCC], rax
    mov eax, [ksq]                  ; king squares, 0x88 -> 0..63
    mov ecx, eax
    and ecx, 7
    add eax, ecx
    shr eax, 1
    mov edx, [ksq+4]
    mov ecx, edx
    and ecx, 7
    add edx, ecx
    shr edx, 1
    mov [rsp+EV_KSQ], eax
    mov [rsp+EV_TKSQ], edx
    xor eax, 56
    xor edx, 56
    mov [rsp+EVS+EV_KSQ], edx
%if EV_SHELTER
    mov ecx, [castle]               ; castling rights per view: White bits 0-1, Black bits 2-3
    mov esi, ecx
    and esi, 3
    mov [rsp+EV_CR], esi
    shr ecx, 2
    and ecx, 3
    mov [rsp+EVS+EV_CR], ecx
%endif
    mov [rsp+EVS+EV_TKSQ], eax
%if EV_PHASH
    mov rax, [bbp+8]                ; pawn cache probe: key = both pawn bitboards
    mov rdx, [bbp+72]
    mov rcx, rdx
    rol rcx, 32
    xor rcx, rax
    mov rsi, 0x9E3779B97F4A7C15
    imul rcx, rsi
    shr rcx, 64-EV_PHBITS
    shl ecx, 6
    lea rsi, [ev_ph+rcx]
    mov [rsp+2*EVS+8], rsi
    xor ecx, ecx
%if UNIT == 0
%ifdef EVALPROF
    inc qword [evprof_php]
%endif
%endif
    cmp rax, [rsi]
    jne .phm
    cmp rdx, [rsi+8]
    jne .phm
    inc ecx
%if UNIT == 0
%ifdef EVALPROF
    inc qword [evprof_phh]
%endif
%endif
    mov rax, [rsi+16]
    mov [rsp+EV_PPB], rax
    mov rax, [rsi+24]
    mov [rsp+EVS+EV_PPB], rax
    mov eax, [rsi+32]
    mov [rsp+EV_PSC], eax
    mov eax, [rsi+36]
    mov [rsp+EVS+EV_PSC], eax
.phm:
    mov [rsp+EV_PHIT], ecx
    mov [rsp+EVS+EV_PHIT], ecx
%endif
    mov rbx, rsp
    call ev_side%1
    mov [rsp+2*EVS], eax
    lea rbx, [rsp+EVS]
    call ev_side%1
    mov [rsp+2*EVS+4], eax
%if EV_IMBAL
    xor ecx, ecx
    call ev_imbal
    add [rsp+2*EVS], eax
    mov ecx, 8
    call ev_imbal
    add [rsp+2*EVS+4], eax
%endif
%if EV_PHASH
    cmp dword [rsp+EV_PHIT], 0      ; pawn cache store after a miss
    jne .phs
    mov rsi, [rsp+2*EVS+8]
    mov rax, [bbp+8]
    mov [rsi], rax
    mov rax, [bbp+72]
    mov [rsi+8], rax
    mov rax, [rsp+EV_PPB]
    mov [rsi+16], rax
    mov rax, [rsp+EVS+EV_PPB]
    mov [rsi+24], rax
    mov eax, [rsp+EV_PSC]
    mov [rsi+32], eax
    mov eax, [rsp+EVS+EV_PSC]
    mov [rsi+36], eax
.phs:
%endif
%if EV_ATTMAPS
    xor ecx, ecx                    ; each side gets the other side's attack maps in its own frame
.xm:mov rax, [rsp+EV_AP+rcx*8]
    bswap rax
    mov [rsp+EVS+EV_TAP+rcx*8], rax
    mov rax, [rsp+EVS+EV_AP+rcx*8]
    bswap rax
    mov [rsp+EV_TAP+rcx*8], rax
    inc ecx
    cmp ecx, 8
    jb .xm
%endif
%if EV_KDANGER
    mov rax, [rsp+EV_PIN]
    bswap rax
    mov [rsp+EVS+EV_TPIN], rax
    mov rax, [rsp+EVS+EV_PIN]
    bswap rax
    mov [rsp+EV_TPIN], rax
%endif
%if EV_PASSB
%if EV_LAZY
    EVTOTAL                         ; lazy evaluation, second stage
    mov edx, eax
    neg edx
    cmovs edx, eax
    KCMP edx, 29, EV_LAZYT2
    jg .fin
%endif
    mov rbx, rsp                    ; second pass: the terms that need the attack maps of both sides
    call ev_sideb%1
    add [rsp+2*EVS], eax
    lea rbx, [rsp+EVS]
    call ev_sideb%1
    add [rsp+2*EVS+4], eax
%endif
    EVTOTAL
.fin:
%if EV_OSCALE != 1024
    imul eax, eax, EV_OSCALE        ; test builds: White's score * EV_OSCALE / 1024, truncated towards zero (colour-symmetric)
    mov ecx, eax
    sar ecx, 31
    and ecx, 1023
    add eax, ecx
    sar eax, 10
%endif
%if EV_BRSIGN
    mov ecx, eax
    neg ecx
    cmp dword [stm], 0
    cmovne eax, ecx
%else
    cmp dword [stm], 0
    je .w
    neg eax
.w:
%endif
    add eax, TEMPO
    add rsp, 2*EVS+32
    pop r15
    pop r14
    pop r13
    pop r12
    pop r11
    pop r10
    pop r9
    pop r8
    pop rdi
    pop rbp
    pop rbx
    ret
%endmacro

; ============================================================
; eval -> eax = static evaluation, side-to-move view.  Preserves every register except rax, rcx, rdx, rsi.
; ============================================================
eval:
%ifdef EVALPROF
    rdtsc
    shl rdx, 32
    or rax, rdx
    push rax
    call eval_core
    mov esi, eax
    rdtsc
    shl rdx, 32
    or rax, rdx
    pop rcx
    sub rax, rcx
%if UNIT == 0
    add [evprof_cyc], rax
    inc qword [evprof_n]
%endif
    mov eax, esi
    ret
eval_core:
%endif
%if EV_ENDG
    mov rax, [bbc]                  ; a side with only its king: special evaluation (ev_bare)
    lea rcx, [rax-1]
    test rax, rcx
    jz .bw
    mov rax, [bbc+8]
    lea rcx, [rax-1]
    test rax, rcx
    jnz ev_nobare
    xor eax, eax
    jmp ev_bare
.bw:mov eax, 8
    jmp ev_bare
%endif
ev_nobare:
%if EV_ENDG2
    jmp ev_spec2
%endif
ev_dispatch:
%if HWPOPCNT == 2
    cmp byte [ev_hw], 0
    jne ev_eval1
    jmp ev_eval0
%elif HWPOPCNT == 1
    jmp ev_eval1
%else
    jmp ev_eval0
%endif
%if HWPOPCNT != 1
EVCODE 0
%endif
%if HWPOPCNT != 0
EVCODE 1
%endif

%else
; ============================================================
; eval -> eax = static evaluation, side-to-move view (EVAL_FULL = 0: material + PST + bishop pair + tempo).  clobbers rcx, rdx, rsi
; ============================================================
eval:
%ifdef EVALPROF
eval_core:
%endif
    mov eax, [mgsc]
    mov ecx, [egsc]
    cmp byte [cnt+3], 2
    jl .nwb
    add eax, BP_MG
    add ecx, BP_EG
.nwb:
    cmp byte [cnt+11], 2
    jl .nbb
    sub eax, BP_MG
    sub ecx, BP_EG
.nbb:
    TAPER
    cmp dword [stm], 0
    je .w
    neg eax
.w: add eax, TEMPO
    ret
%endif
