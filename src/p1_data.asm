; zeval.asm - UCI chess engine in pure x86-64 assembly (no libc, no C runtime): Linux (raw syscalls) and Windows (kernel32 only)
;
; 0x88 board (master) + magic bitboards (mirror, default build; -DBB=0 = pure 0x88) | full FIDE rules (castling, en passant,
; under-promotion, 50-move, repetition, insufficient material) | legal-move search with check detection
; Movegen: bitboard pseudo-legal generator (pawn shifts, knight/king tables, classic magic lookups for sliders), legality by
;          make + attacked(); SEE on bitboards (attackers-to, x-rays by recomputing sliders when a piece leaves the occupancy)
; Search : iterative deepening, aspiration windows, PVS, lock-free transposition table (Zobrist, mate-score aware, 2-way buckets, aging,
;          also used in quiescence), null-move pruning, razoring, reverse futility, futility + late-move pruning, LMR, killers, history and
;          continuation history, SEE ordering and pruning, quiescence with evasions + delta pruning, Lazy SMP (up to 32 threads)
; Ext    : in-check extension (+1 ply for the node that is in check), singular extension of the TT move
; Eval   : tapered evaluation (src/eval.asm)
; Time   : movetime / wtime+btime(+inc, movestogo) / depth / nodes / infinite, stdin polled for "stop"
; I/O    : UCI protocol, plus a plain console mode: type moves like e2e4
;
; Build:  Linux  : nasm -f elf64 zeval.asm -o zeval.o && ld zeval.o -o zeval && ./zeval
;         Windows: nasm -f win64 -DWIN zeval.asm -o zeval_win.obj  (+ link, see build/build.sh and build/build.bat)
; Piece codes: P=1 N=2 B=3 R=4 Q=5 K=6, black = +8.  Side to move: 0 white, 8 black.
bits 64
default abs

; ---- Thread units: one copy of the search code per thread, each copy with its own state block.
; ---- The engine is assembled once per unit: nasm -DUNIT=k (k = 0 .. NUNITS-1) and the objects are linked together (build/build.sh).
; ---- Unit 0 is the whole engine (UCI, I/O, tables, the transposition table, the search code).  Units k >= 1 contain only the search
; ---- code and their own state block; everything shared is declared SHARED below (global in unit 0, extern in the others).
; ---- Plain "nasm zeval.asm" (no -D) = unit 0 alone, NUNITS = 1: the single-thread engine, same code.
%ifndef UNIT
%define UNIT 0
%endif
%ifndef NUNITS
%define NUNITS 1
%endif

; ---- Lazy SMP (release builds of both platforms).
; ---- SMP = 1: unit 0 starts helper threads (UCI option Threads) and the units k >= 1 are assembled in the HELPER flavour.
; ---- HELPER = 1: this copy of the search code runs in a helper thread: no output, no clock, no stdin, and no write to any shared variable
; ----            except the transposition table contents.  Decided at assembly time (no run-time flag: unit 0 pays nothing for it).
; ---- SMP = 0 (the -DUNITTEST test builds, and -DNOSMP = the single-thread engine on either OS): every unit keeps the full think()
; ----            (the hooks of tests/t_units.py and tests/t_units_conc.py rely on it) and the option Threads stays at 1.
; ---- Windows: the helper threads are CreateThread threads (smp_spawn / smp_stop in p7_units.asm); everything else is the Linux code.
%assign SMP 1
%ifdef UNITTEST
%assign SMP 0
%endif
%ifdef NOSMP
%assign SMP 0
%endif
%assign HELPER 0
%if SMP && UNIT != 0
%assign HELPER 1
%endif
%if SMP
%assign SMPMAX NUNITS
%else
%assign SMPMAX 1
%endif
%if NUNITS > 32
%error "at most 32 thread units (the helper mask is one dword)"
%endif
%defstr SMPMAX_S SMPMAX
; ---- SMP_POOL 1: persistent helper threads, created when a search first needs them and waiting between searches (no thread creation and
; ---- join per "go"; waiting threads use no CPU time).  Same search.  0 = a new thread per helper and search.
%ifndef SMP_POOL
%define SMP_POOL 1
%endif
; ---- PV_FINAL 1 (UCI output only): when a search ends inside an iteration whose root line changed after the last info line (time or node
; ---- limit, stop), one more "info depth ... pv" line with the root line of the bestmove is printed before "bestmove", so the last pv starts
; ---- with the bestmove and its second move is the ponder move.  0 = no extra line.  No effect on the search.
%ifndef PV_FINAL
%define PV_FINAL 1
%endif
; ---- SMP_VOTE 1: the bestmove of a Lazy SMP search is chosen by a vote over the root results of all threads (smp_vote, p7_units.asm)
; ---- instead of the main thread alone; never for "go depth".  0 = the main thread's result.
%ifndef SMP_VOTE
%define SMP_VOTE 1
%endif
; ---- SMP_TMBMC 1: the best-move-change count of the time management (TM_INST) is the mean over all threads of the search (every helper
; ---- keeps its own count, smp_bmc averages).  Threads = 1: no change.  0 = the main thread's count.
%ifndef SMP_TMBMC
%define SMP_TMBMC 1
%endif
%ifndef SMP_ASPSTEP
%define SMP_ASPSTEP 2               ; helper unit k starts its aspiration window with half-width 20 + (k mod 8) * SMP_ASPSTEP (0 = same as the main thread)
%endif

%ifdef WIN
; ---- Windows build (nasm -f win64 -DWIN).  The engine core is unchanged: every Linux
; ---- "syscall" instruction is routed to a small kernel32 shim, see p6_win.asm.
%define syscall call os_syscall
section .text align=16
section .data align=64
section .bss  align=64
%endif

; ---- SEE (static exchange evaluation) switches.  Build time: nasm -DSEE_QS=0 ...   0 = off; all of them 0 = a search without SEE.
%ifndef SEE_QS
%define SEE_QS 1            ; quiescence: skip captures with SEE < 0 (never when in check)
%endif
%ifndef SEE_ORD
%define SEE_ORD 1           ; capture ordering: good / bad split by SEE (asked only when the attacker is worth more than the victim)
%endif
%ifdef DEV8SEARCH
%ifndef CAPHIST
%define CAPHIST 0           ; the reference search (DEV8SEARCH) has no capture history
%endif
%endif
%ifndef CAPHIST
%define CAPHIST 2           ; capture history (see "Capture history" above); Zeval 1.0 = 0
%endif
%ifndef CAP_BA
%define CAP_BA 120
%endif
%ifndef CAP_BMAX
%define CAP_BMAX 1200
%endif
%ifndef CAP_LIM
%define CAP_LIM 8000
%endif
%ifndef CAP_SH
%define CAP_SH 13
%endif
%ifndef CAP_SEEB
%define CAP_SEEB -10
%endif
%ifndef CAP_SEESH
%define CAP_SEESH 6
%endif
%ifndef CAP_LMR
%define CAP_LMR 0              ; CAPHIST: 1 = a late capture (same conditions as LMR) with captab < CAP_LMRT is searched one ply shallower
%endif
%ifndef CAP_LMRT
%define CAP_LMRT 0
%endif
%ifndef CAP_SEEPOS
%define CAP_SEEPOS 1           ; CAPHIST 2: 0 = only bad history moves the SEE threshold
%endif
%ifndef SEE_BADLAST
%define SEE_BADLAST 1       ; losing captures are searched after the quiet moves
%endif
%ifndef SEE_PC
%define SEE_PC 1            ; main search: skip captures with SEE < -SEE_CM*depth^2 (non-PV, not in check, depth <= SEE_MAXD)
%endif
%ifndef DELTA_M
%define DELTA_M 200         ; qsearch delta pruning: skip a capture when stand pat + captured value (+ 800 for a promotion) + DELTA_M <= alpha
%endif
%ifndef SEE_CM
%define SEE_CM 20
%endif
%ifndef SEE_PQ
%define SEE_PQ 0            ; main search: drop quiet moves with SEE < -SEE_QM*depth (not when giving check)
%endif
%ifndef SEE_QM
%define SEE_QM 64
%endif
%ifndef SEE_MAXD
%define SEE_MAXD 6
%endif
; ---- CHK_EXT (default 0): extra extension of checking moves.  A node in check already gets +1 ply (search, .noext), so a checking move costs
; ---- no depth; CHK_EXT adds a SECOND ply on top (net +1), only while ply < 2 * (depth of the current iteration).
; ----   0 = off     1 = checking move whose destination square is not attacked by the checked side ("safe check")     2 = every checking move
%ifndef CHK_EXT
%define CHK_EXT 0
%endif

%ifdef SEETEST
%define SEE_ANY 1
%elif SEE_QS || SEE_ORD || SEE_PC || SEE_PQ
%define SEE_ANY 1
%else
%define SEE_ANY 0
%endif

; ---- BB 1 (default): magic bitboards mirror the 0x88 mailbox; attacked(), gen and see_ge use them (p2b_bb.asm).  0 = the pure 0x88 engine
; ---- (another move order: other fixed-depth node counts; with -DDEV8SEARCH the reference tests/golden_depth8_0x88.json).
%ifndef BB
%define BB 1
%endif

; ---- PREPRUNE 1 (speed only, same tree): a late quiet move that LMP, futility or SEE_PQ may prune is judged by pp_ok (p2b_bb.asm), which
; ---- answers "legal? gives check?" from the bitboards without making the move; king moves and castling are made, tested and unmade.
; ---- Needs BB=1.  0 = every such move is made, tested and unmade.
; ---- -DPPVERIFY (test build): every quiet move at a non-check node is checked, pp_ok against make + attacked + in_check
; ---- (tests/t_ppverify.py); a difference prints "PPVERIFY FAILED" and exits with code 3.
%ifndef PREPRUNE
%define PREPRUNE 1
%endif

; ---- R50MATE 1: the 50-move rule does not hide a checkmate.  A node at half-move clock >= 100 is a draw unless the side to move is
; ---- in check without a legal move: then it is mated (a mate given on the 100th half-move ends the game before the draw can be claimed).
; ---- 0 = every node at half-move clock >= 100 is a draw.
%ifndef R50MATE
%define R50MATE 1
%endif

; ---- Speed-only switches (the search tree is identical, fixed-depth node counts do not change):
; ---- SIMDPICK  pick() = the best remaining move (the FIRST index holding the maximum score) and the same swap as the scalar loop in every variant:
; ----           5 (default) = ONE pass that carries the running maximum and its index per lane (no second scan loop), SSE4.1 (pmaxsd / pminsd)
; ----             when the CPU has it, else SSE2: the choice is made at start-up (CPUID leaf 1, ECX bit 19; -DFORCE_SSE2 = test build that
; ----             always takes the SSE2 copy).  3 = that pass with SSE2 only, 4 = with SSE4.1 only (needs a CPU with SSE4.1).
; ----           1 = SSE2 maximum, then a scan for its first index, 2 = the same with pmaxsd (SSE4.1), 0 = the scalar loop.
; ----           score_moves writes 4 sentinels (INT_MIN) behind the score list.  -DPICKVERIFY (Linux test build) runs the scalar loop in every
; ----           pick() call and compares the index (exit code 6 on a difference).
; ---- INCHKHINT 1 (default) = the child node does not ask in_check again: search() takes r8d, qsearch() edx = "side to move is in check"
; ----           (0 / 1, -1 = not known = compute), the parents pass what they already know (L_GIVES of the move just made, 0 after a null move).
; ----           0 = every node asks in_check itself.  -DINCHKVERIFY (Linux test build) asks in_check anyway and compares (exit code 4 / 5).
%ifndef SIMDPICK
%define SIMDPICK 5
%endif
%ifndef INCHKHINT
%define INCHKHINT 1
%endif

; ---- Search switches: build-time defines (-DNAME=value, e.g. NASMFLAGS="-DRAZOR=0" sh build/build.sh).  TTPREFETCH is speed only (same
; ---- tree); cutNode is carried through the search.
; ---- On by default, a group of 16 switches: TM_STAB TM_FALL TM_INST TM_EFFORT RAZOR RFP_EXT FUT_LMRD NMP_TTG TTCUT_COND LMR_CUT
; ----   LMR_IMPR(=1) LMR_HIST LMR_TTC LMR_RES EVAL_TT NMP_CUT.  -DDEV8SEARCH sets each of them that is not given on the command line to 0, and
; ----   CONTHIST and SING too: the reference search of tests/golden_depth8_dev8search.json.
; ---- Off by default: IIR_CUT, EVALDIFF_HIST, NMP_VER (acts only at depth >= 14), LMR_BADCAP, LMR_MINM, LMR_IMPR=2, CHK_EXT, SEE_PQ.
; ---- Description of the switches:
; ----   TTPREFETCH 1  domove / domove_null prefetch the transposition table bucket of the NEW hash key (same address arithmetic as tt_probe)
; ----   -DCUTVERIFY (test build): a PV node that gets cutNode = 1, or a cutNode value other than 0 / 1, exits with code 7
; ---- LMR modifiers (integer plies; each one only changes the reduction R of a late quiet move, R stays inside 0 .. newdepth-1):
; ----   LMR_CUT 1    cut node: R + LMR_CUTP (default 1)              LMR_IMPR 1  not improving: R + 1   (2: improving also R - 1)
; ----   LMR_HIST 1   quiet non-killer move: history > +LMR_HT R - 1, history < -LMR_HT R + 1 (LMR_HT 8000, history range +-16000)
; ----   LMR_TTC 1    the TT move is a capture: R + 1                 LMR_MINM n  first reduced move number (default 4; 3 = one move earlier)
; ----   LMR_BADCAP 1 captures that SEE sorted behind the quiet moves (bad captures) are reduced too (R from the same table, min 1)
; ----   LMR_RES 1    after a reduced search that beat alpha: score > best + LMR_DEEPM + 2 * newdepth -> re-search 1 ply deeper;
; ----                score < best + LMR_SHALM -> 1 ply shallower than the unreduced depth
; ---- Continuation history (CONTHIST 1): every unit keeps chtab[prev piece][prev to][piece][to] (12 x 64 rows of 12 x 64 int16, 1.2 MB).  chp[ply + 2]
; ----   = the row of the move made at ply on the current path (search, null move: chdummy, qsearch; plies -2 and -1: chdummy).  A quiet move is
; ----   ordered by 2 * histtab + row(ply - 1)[piece][to] + row(ply - 2)[piece][to] (+-64000; under-promotions -70000); a quiet cutoff gives the
; ----   bonus of the history to both rows and the malus to the rows of the quiet moves searched before it (same gravity and +-16000 bounds;
; ----   chdummy is never updated).  LMR_HIST: CH_LMR 0 histtab against +-LMR_HT as before, CH_LMR 1 the ordering score against +-LMR_HTC.
; ----   ucinewgame clears the rows of the units that searched since the last clear.
; ----   CH_PRUNE 1: a late quiet non-killer move of a non-PV node at depth <= CH_PD (3) is dropped when row(ply - 1) + row(ply - 2) < -CH_PM (3000) * depth.
; ----   CH_BL 1: the continuation bonus is min(CH_BA (150) * depth, CH_BMAX (1500)) instead of the history bonus min(16 * depth^2, 1600).
; ----   Defaults: CONTHIST 1, CH_PRUNE 1, CH_BL 1, CH_LMR 0.
; ---- Capture history (CAPHIST 1): every unit keeps captab[piece code][to][victim type] (16 x 128 x 8 int16, 0x88 squares, 32 KB).  A capture
; ----   cutoff gives the capture bonus min(CAP_BA * depth, CAP_BMAX) to the capture; the captures searched before the cutoff move get the malus
; ----   (also when a quiet move cuts).  Gravity h += b - h * |b| / 2^CAP_SH, bounds +-CAP_LIM.  The ordering adds captab to the MVV-LVA score of
; ----   good and bad captures (the bands stay apart: good above the killers, bad below every quiet move).  En passant counts as a pawn capture;
; ----   under-promotions are never scored or updated.  ucinewgame clears captab of every unit.
; ----   CAPHIST 2 also moves the good / bad split of score_moves (SEE_ORD): a capture is good when SEE >= CAP_SEEB - (captab >> CAP_SEESH) (-10 and
; ----   6: within +-125 cp; -10 makes BxN with the knight defended an even trade).  Good history keeps a capture that loses a little in front of the
; ----   quiet moves, bad history sends an even or slightly winning capture behind them.  A capture whose attacker is worth no more than the victim
; ----   skips SEE only when its history is not negative.  Default: CAPHIST 2 (Zeval 1.1; Zeval 1.0 = 0; -DDEV8SEARCH forces 0).
; ---- Null move:  NMP_CUT 1 only at cut nodes    NMP_TTG 1 skip when the TT proves score < beta (upper bound, depth >= depth - R)
; ----             NMP_RB / NMP_EDIV  R = NMP_RB + depth / 4 + min(599, eval - beta) / NMP_EDIV (defaults 3 / 200)
; ----             NMP_VER 1  at depth >= NMP_VERD (14): verification search with the null move disabled for 3/4 of the null-search depth (zugzwang safety)
; ---- Static eval / pruning:  EVAL_TT 1  eval used by RFP / NMP / futility = TT score when its bound allows it ("improving" keeps the raw eval)
; ----   RAZOR 1  non-PV, depth <= RAZOR_MAXD (3): eval + margin < alpha -> qsearch, return it if it stays below alpha.  margin = RAZOR_B + RAZOR_M * depth * depth
; ----            (RAZOR_SQ 1, default: 380 / 1130 / 2380 cp) or RAZOR_B + RAZOR_M * depth (RAZOR_SQ 0)
; ----   RFP_EXT 1  RFP only if there is no TT move or it is a capture, and returns (661 * beta + 363 * eval) / 1024 instead of eval - margin
; ----   IIR_CUT 1  IIR only at PV / cut nodes and depth >= IIR_MIND (6)       TTCUT_COND 1  TT cutoff needs (cutNode == (ttScore >= beta)) or depth > 4
; ----   FUT_LMRD 1 futility pruning on lmrDepth = depth - 1 - R(depth, move number) instead of the plain depth (also in the pre-make test)
; ----   EVALDIFF_HIST 1  history of the opponent's last quiet move is nudged by the change of the static eval, EDH_M per unit
; ---- Time management (main thread only, real clock only: go wtime / btime; movetime / depth / nodes / infinite are never touched):
; ----   TM_STAB 1  best move unchanged for TM_STABN (8) iterations -> shorter          TM_FALL 1  score falling against the previous iteration / search -> longer
; ----   TM_INST 1  best move changes (decaying count) -> longer                           TM_EFFORT 1  best move got >= 93 % of the iteration's nodes (depth >= 10) -> shorter
; ---- Static eval adjustments in the search (default 0; applied after the eval cache, so the cached eval stays a function of the position):
; ----   EV_R50 1   eval -= trunc(eval * halfmove clock / EV_R50C) (EV_R50C 200): the eval fades towards 0 as the fifty-move rule comes closer
; ----   EV_CORR 1  pawn correction history: per thread unit and side to move, 2^CORR_BITS (14) entries (int16, 1/8 cp) indexed by both pawn
; ----              bitboards; eval += entry / 8.  At the end of a search node (not in check, best move not a capture, no mate score, the bound
; ----              does not contradict the direction): bonus = clamp((best - eval) * min(depth, 8), +-CORR_BMAX), entry += bonus - entry * |bonus| /
; ----              CORR_LIMIT (clamped to +-CORR_LIMIT = 1024 = 128 cp).  Cleared with the other tables (ucinewgame / Clear Hash).
; ---- -DTUNE (test build, never the release): options T0 .. T84 (integers, negative ones too) overwrite the numeric parameters in the table
; ----   tune_def (p5_main.asm; the index is the second argument of LOADT at each use); with TUNE off the parameters are immediates.
; ----   "uci" declares the options of the slots this build uses (TUSE), so a slot behind a switch that is off is not offered.
; ----   One binary serves --optA / --optB of tests/match.py and SPSA.
%ifndef TTPREFETCH
%define TTPREFETCH 1
%endif
%ifndef EV_R50
%define EV_R50 0
%endif
%ifndef EV_R50C
%define EV_R50C 200
%endif
%ifndef EV_CORR
%define EV_CORR 0
%endif
%ifndef CORR_BITS
%define CORR_BITS 14
%endif
%define CORR_LIMIT 1024
%define CORR_BMAX 256
%ifdef DEV8SEARCH            ; reference search: the 16 switches of that group are 0 unless given explicitly with -D
%ifndef CONTHIST
%define CONTHIST 0           ; no continuation history in the reference search
%endif
%ifndef SING
%define SING 0               ; nor a singular extension
%endif
%ifndef TM_STAB
%define TM_STAB 0
%endif
%ifndef TM_FALL
%define TM_FALL 0
%endif
%ifndef TM_INST
%define TM_INST 0
%endif
%ifndef TM_EFFORT
%define TM_EFFORT 0
%endif
%ifndef RAZOR
%define RAZOR 0
%endif
%ifndef RFP_EXT
%define RFP_EXT 0
%endif
%ifndef FUT_LMRD
%define FUT_LMRD 0
%endif
%ifndef NMP_TTG
%define NMP_TTG 0
%endif
%ifndef TTCUT_COND
%define TTCUT_COND 0
%endif
%ifndef LMR_CUT
%define LMR_CUT 0
%endif
%ifndef LMR_IMPR
%define LMR_IMPR 0
%endif
%ifndef LMR_HIST
%define LMR_HIST 0
%endif
%ifndef LMR_TTC
%define LMR_TTC 0
%endif
%ifndef LMR_RES
%define LMR_RES 0
%endif
%ifndef EVAL_TT
%define EVAL_TT 0
%endif
%ifndef NMP_CUT
%define NMP_CUT 0
%endif
%endif
%ifndef LMR_CUT
%define LMR_CUT 1
%endif
%ifndef LMR_CUTP
%define LMR_CUTP 1
%endif
%ifndef LMR_IMPR
%define LMR_IMPR 1
%endif
%ifndef LMR_HIST
%define LMR_HIST 1
%endif
%ifndef LMR_HT
%define LMR_HT 8000
%endif
%ifndef LMR_TTC
%define LMR_TTC 1
%endif
%ifndef CONTHIST
%define CONTHIST 1          ; continuation history (0 = without): quiet moves are ordered by history + the history of
%endif                      ; (piece, to) after the previous move and after the move before it; see "Continuation history" below
%ifndef LMR_HTC
%define LMR_HTC 16000       ; LMR_HIST threshold with CONTHIST and CH_LMR 1 (the ordering score: range +-64000)
%endif
%ifndef CH_PRUNE
%define CH_PRUNE 1          ; CONTHIST: a late quiet move of a non-PV node at depth <= CH_PD is pruned when row(ply-1) + row(ply-2) < -CH_PM * depth
%endif
%ifndef CH_PD
%define CH_PD 3
%endif
%ifndef CH_PM
%define CH_PM 3000
%endif
%ifndef CH_BL
%define CH_BL 1             ; CONTHIST: 1 = the continuation bonus is min(CH_BA * depth, CH_BMAX) (0 = the history bonus min(16 * depth^2, 1600))
%endif
%ifndef CH_BA
%define CH_BA 150
%endif
%ifndef CH_BMAX
%define CH_BMAX 1500
%endif
%ifndef CH_LMR
%define CH_LMR 0            ; CONTHIST: 0 = LMR_HIST looks at histtab only (as without CONTHIST), 1 = at the whole ordering score
%endif
; ---- CH_SHARED (default 0): one continuation-history table for all threads of a Lazy SMP search: the helpers read and update the main thread's
; ---- chtab (address in chbase, as the transposition table through ttbase).  Updates are plain 16-bit stores (two threads may lose an update).
; ---- Threads = 1 searches exactly as with 0; -DSMPTEST (tests/t_smp_equiv.py) forces 0.  0 = every thread has its own table.
%ifndef CH_SHARED
%define CH_SHARED 0
%endif
; ---- TM_INCD (default 0; real clock only): when an iteration ends past half of the time to the stop of the time management, the next
; ---- iterations do not go deeper: each raises a search-again counter c and searches at max(1, depth - 3 * (c + 1) / 4).  Main thread only;
; ---- go depth / movetime / infinite are unchanged.  0 = every iteration one ply deeper.
%ifndef TM_INCD
%define TM_INCD 0
%endif
; ---- TM_CURVE 1 (real clock without movestogo only): the time per move follows the game ply.  0 = clock / 28 + 3/4 inc.
; ----   rem    = clock + inc * (TMC_H - 1) - overhead * TMC_H                (at least 1: the time of the next TMC_H moves)
; ----   share  = TMC_F0 + (TMC_F1 - TMC_F0) * min(gameply, TMC_PM) / TMC_PM    (1/10000 of rem; rises over the first TMC_PM plies of the game)
; ----   target = min(share * rem / 10000, clock / TMC_CAP), at least 1 ms;   softlim = 2 * target  (the stop rule stops at softlim / 2 * F)
; ---- The game ply is the FEN's fullmove number (2 * (n - 1), + 1 with Black to move) + the moves of the position command.  The shares do
; ---- not depend on the length of the clock.  The hard limit (4 * softlim, at most 56 % of the clock) and the TM_STAB / TM_FALL / TM_INST /
; ---- TM_EFFORT factors stay; movestogo, movetime, depth, nodes and infinite are not affected.
; ---- -DTMCDBG (test build): "info string tmc <gameply> <clock> <inc> <softlim> <hardlim>" for every clock based go (tests/t_tmcurve.py).
%ifndef TM_CURVE
%define TM_CURVE 1
%endif
%ifndef TMC_F0
%define TMC_F0 140
%endif
%ifndef TMC_F1
%define TMC_F1 260
%endif
%ifndef TMC_PM
%define TMC_PM 60
%endif
%ifndef TMC_H
%define TMC_H 40
%endif
%ifndef TMC_CAP
%define TMC_CAP 10
%endif
; ---- SING 2: singular extension of the TT move.  At a node that is not the root, not in check and not itself an exclusion search, when the
; ---- first move is the TT move, depth >= SE_MIND, the TT entry is a lower (or exact) bound with depth >= depth - SE_TTD and no mate score, and
; ---- ply < 2 * iteration depth: the same node is searched again WITHOUT the TT move (the "exclusion search", sexcl / L_EXCL) at depth
; ---- (depth - 1) / 2 on the null window around singularBeta = ttScore - SE_M * depth.  In the exclusion search: no TT cutoff, no TT store,
; ---- no EVAL_TT, no null move and no EVALDIFF_HIST (called with nullok = 0), no nested test; "no legal move left" returns alpha.
; ----   value < singularBeta              -> the TT move is singular: searched one ply deeper (SING 2)
; ----   singularBeta >= beta <= value     -> multi-cut: the node returns singularBeta at once (SING 2)
; ----   neither, NEG_EXT 1: ttScore >= beta -> the TT move is searched one ply SHALLOWER (negative extension); NEG_EXT 2: also at an
; ----   expected cut node (L_CUT) whatever ttScore.  0 = off.
; ---- 0 = off.  1 = the exclusion search runs but its result is not used (cost only).  2 = extension and multi-cut.
; ---- -DSESTAT (test build): counters of the main thread, console command "sestat" (tests/t_singular.py).  -DSEVERIFY (test build): an
; ---- exclusion search that is in check (exit code 8), makes the excluded move (9), reaches the TT store (10) or searches another position (11)
; ---- ends the process at once.
%ifndef SING
%define SING 2
%endif
%ifndef SE_MIND
%define SE_MIND 6
%endif
%ifndef SE_TTD
%define SE_TTD 2
%endif
%ifndef SE_M
%define SE_M 4
%endif
%ifndef SE_MC
%define SE_MC 1              ; SING 2: 0 = no multi-cut (the extension only; diagnosis)
%endif
; ---- PROBCUT 1: at a node that is not PV, not the root, not in check, not an exclusion search, depth >= PC_MIND and beta not a mate score:
; ---- every capture whose SEE reaches pcBeta - eval (pcBeta = beta + PC_M, eval = the raw static eval) is made and searched on the null
; ---- window around pcBeta, first by quiescence, then (if that holds) at depth - PC_R.  A result >= pcBeta that is not a mate score is
; ---- stored as a lower bound at depth - PC_R + 1 and the node returns result - PC_M.  Skipped when the TT entry has an upper bound below
; ---- pcBeta at depth >= depth - 3.  0 = off.  -DPCVERIFY (test build): no cut; the normal search runs and counts whether the node really
; ---- fails high (console command "pcstat", see PCVERIFY in p4_search.asm).
%ifndef PROBCUT
%define PROBCUT 0
%endif
%ifndef PC_MIND
%define PC_MIND 5
%endif
%ifndef PC_M
%define PC_M 50
%endif
%ifndef PC_R
%define PC_R 4
%endif
%ifndef NEG_EXT
%define NEG_EXT 0            ; SING 2: negative extension of a TT move that is neither singular nor a multi-cut (see above)
%endif
; ---- TTFIRST 1: a node (not the root, not an exclusion search) whose TT move passes tt_pseudo (p2b_bb.asm: "the move is in gen's list", checked
; ---- on the bitboards without generating anything) searches that move FIRST, before any move is generated or scored.  Only when it does not cut
; ---- (and the node goes on) the moves are generated and scored, the TT move is moved to index 0 exactly as pick() does it at a normal node, and
; ---- the loop goes on at index 1.  A TT move that cuts (about 14 % of the nodes that reach the move generation) costs no generation and no scoring.
; ---- The ORDER is not the one of TTFIRST 0: the other moves are scored AFTER the subtree of the TT move, so the history / capture history /
; ---- continuation history they see already contains what that subtree learned (the usual lazy move picker).  Node counts therefore differ
; ---- (tests/golden_depth8_ttfirst.json); it is a search change and needs a strength test (candidates/FLAGS.txt: S3_TTFIRST).  BB=1 only.
; ---- -DTTFEAGER (test build, needs TTFIRST 1): generate and score at the start as before and keep the list aside; the path through the one-move
; ---- list is the same.  That build must reproduce golden_depth8.json bit for bit: it proves everything except the later scoring.
; ---- -DTTFVERIFY (test build, needs TTFIRST 1): at every node that tt_pseudo judges, generate the list and compare: accepted but not in the
; ---- list = exit code 12 (unsound: the move would be made without being generated); rejected but in the list and not a castling move = exit
; ---- code 13 (incomplete: harmless for the result, but the speed-up is lost).  tests/t_ttfirst.py.
%ifndef TTFIRST
%define TTFIRST 0
%endif
; ---- CAPIDX 1 (speed only: the search is the same, tests/golden_depth8*.json are reproduced bit for bit): score_moves (CAPHIST 2 + SEE_ORD) asks
; ---- cap_index for the capture history index of a capture to find its SEE threshold, and then again (caph_add) to add the same table entry to the
; ---- score.  It keeps the table value from the first lookup in ebp instead (no table write happens in between).  Default 1 (0 = two lookups).
%ifndef CAPIDX
%define CAPIDX 1
%endif
; ---- QSSEE 1 (speed only, same search): quiescence prunes a capture with SEE < 0 (SEE_QS) AFTER score_moves has asked see_ge for the same capture
; ---- whether SEE >= t, t = CAP_SEEB - (captab >> CAP_SEESH).  When that answer is "no" and t <= 0, then SEE < t <= 0 and the capture is pruned
; ---- anyway: score_moves marks such a move (bit 16 of its entry in the move list, only while quiescence scores: [smq] = 1) and quiescence skips the
; ---- second see_ge for it.  Needs SEE_QS, SEE_ORD, CAPHIST 2, CAP_SEEPOS 1 and no TUNE build (the threshold is then a constant of the table value).
; ---- Default 1 (0 = quiescence asks see_ge for every capture).  CAPIDX and QSSEE together: -0.9 % time at the same nodes on the target machine.
%ifndef QSSEE
%define QSSEE 1
%endif
%ifdef SMPTEST
%undef CH_SHARED
%define CH_SHARED 0
%endif
%ifndef LMR_MINM
%define LMR_MINM 4
%endif
%ifndef LMR_BADCAP
%define LMR_BADCAP 0
%endif
%ifndef LMR_RES
%define LMR_RES 1
%endif
%ifndef LMR_DEEPM
%define LMR_DEEPM 60
%endif
%ifndef LMR_SHALM
%define LMR_SHALM 8
%endif
%ifndef PR_MAXD
%define PR_MAXD 6           ; late quiet moves of non-PV nodes: LMP / continuation-history / futility pruning up to this depth
%endif
%ifndef RFP_MAXD
%define RFP_MAXD 6          ; reverse futility pruning up to this depth
%endif
%ifndef LMP_SF
%define LMP_SF 0            ; 1 = late move count (3 + d^2), halved when not improving (0 = (d^2 + LMP_B) +- a quarter)
%endif
%ifndef LMP_B
%define LMP_B 5             ; LMP_SF 0: the constant of the late move count (T76)
%endif
%ifndef HIST_BMAX
%define HIST_BMAX 1600      ; quiet history bonus min(16 * depth^2, HIST_BMAX) (T68; the factor 16 is T67)
%endif
%ifndef ASP_W
%define ASP_W 20            ; first aspiration half-width (T73; helper units add (unit mod 8) * SMP_ASPSTEP)
%endif
%ifndef IIR_D
%define IIR_D 4             ; IIR_CUT 0: internal iterative reduction from this depth on (T77)
%endif
%ifndef NMP_CUT
%define NMP_CUT 1
%endif
%ifndef NMP_TTG
%define NMP_TTG 1
%endif
%ifndef NMP_RB
%define NMP_RB 3
%endif
%ifndef NMP_EDIV
%define NMP_EDIV 200
%endif
%ifndef NMP_VER
%define NMP_VER 0
%endif
%ifndef NMP_VERD
%define NMP_VERD 14
%endif
%ifndef EVAL_TT
%define EVAL_TT 1
%endif
%ifndef RAZOR
%define RAZOR 1
%endif
%ifndef RAZOR_SQ
%define RAZOR_SQ 1
%endif
%ifndef RAZOR_MAXD
%define RAZOR_MAXD 3
%endif
%ifndef RAZOR_B
%define RAZOR_B 130
%endif
%ifndef RAZOR_M
%define RAZOR_M 250
%endif
%ifndef RFP_EXT
%define RFP_EXT 1
%endif
%ifndef IIR_CUT
%define IIR_CUT 0
%endif
%ifndef IIR_MIND
%define IIR_MIND 6
%endif
%ifndef TTCUT_COND
%define TTCUT_COND 1
%endif
%ifndef FUT_LMRD
%define FUT_LMRD 1
%endif
%ifndef EVALDIFF_HIST
%define EVALDIFF_HIST 0
%endif
%ifndef EDH_M
%define EDH_M 4
%endif
%ifndef TM_STAB
%define TM_STAB 1
%endif
%ifndef TM_FALL
%define TM_FALL 1
%endif
%ifndef TM_INST
%define TM_INST 1
%endif
%ifndef TM_EFFORT
%define TM_EFFORT 1
%endif
%ifndef TM_STABN
%define TM_STABN 8
%endif
%assign TM_ANY (TM_STAB | TM_FALL | TM_INST | TM_EFFORT)
%assign NMP_ANYCUT (NMP_CUT | NMP_VER)

; ---- tunable parameters (-DTUNE): LOADT reg, index, default  =  mov reg, [tunetab + 4 * index]  (TUNE)  /  mov reg, default  (release).
; ---- The table is initialised from the defaults at start-up (p5_main) and overwritten by "setoption name T<index> value <n>".
; ---- TUSE index marks a slot as used by the code assembled into this build (every read of tunetab goes through it, so a slot whose code
; ---- sits behind a switch that is off stays unmarked); "uci" declares an option T<index> for each marked slot (tune_options, p5_main).
%define TUNEN 96
%macro TUSE 1
%ifdef TUNE
%assign TU_%1 1
%endif
%endmacro
%macro LOADT 3
%ifdef TUNE
    TUSE %2
    mov %1, [tunetab+(%2)*4]
%else
    mov %1, %3
%endif
%endmacro
; TCMP / TADD reg, index, default: cmp / add with the option (-DTUNE) or the immediate (release)
%macro TCMP 3
%ifdef TUNE
    TUSE %2
    cmp %1, [tunetab+(%2)*4]
%else
    cmp %1, %3
%endif
%endmacro
%macro TADD 3
%ifdef TUNE
    TUSE %2
    add %1, [tunetab+(%2)*4]
%else
    add %1, %3
%endif
%endmacro
%if BB == 0
%undef PREPRUNE
%define PREPRUNE 0
%undef TTFIRST
%define TTFIRST 0               ; tt_pseudo works on the bitboards
%endif
%ifdef TTFEAGER
%if TTFIRST == 0
%error "TTFEAGER needs TTFIRST=1"
%endif
%endif
%ifdef TTFVERIFY
%if TTFIRST == 0
%error "TTFVERIFY needs TTFIRST=1"
%endif
%endif
; CAPIDX / QSSEE act only in the configuration they are written for (the others keep the old code)
%assign CAPIDX_ON 0
%if CAPIDX && SEE_ORD && CAPHIST >= 2
%assign CAPIDX_ON 1
%endif
%assign QSSEE_ON 0
%if QSSEE && SEE_QS && SEE_ORD && CAPHIST >= 2 && CAP_SEEPOS == 1
%ifndef TUNE
%assign QSSEE_ON 1
%assign CAPIDX_ON 1             ; the mark uses the table value that CAPIDX keeps in ebp
%endif
%endif
%ifdef PPVERIFY
%if PREPRUNE == 0
%error "PPVERIFY needs PREPRUNE=1 and BB=1"
%endif
%endif
%if BB
; BBX piece64, square32, tmp64, tmp32 (same register), bit64: toggle the piece on a 0x88 square in bbp and bbc
%macro BBX 5
    mov %4, %2
    and %4, 7
    add %4, %2
    shr %4, 1                   ; 0x88 square -> 0..63
    xor %5, %5
    bts %5, %3
    xor [bbp+%1*8], %5
    mov %3, %1
    and %4, 8
    xor [bbc+%3], %5
%endmacro
%endif

%define MAXPLY   128
%define MATE     30000
%define MATEB    29800
%define INF      32000
%define MAXGAME  4096
%define LINEMAX  32768
%define PENDMAX  131072             ; queue for commands that arrive while a search runs
; ---- TT_HUGE 1 (Linux only, speed only): the transposition table (2 MB or more) is 2 MB aligned and marked madvise(MADV_HUGEPAGE), so the
; ---- kernel can back it with transparent huge pages (fewer TLB misses).  Acts when /sys/kernel/mm/transparent_hugepage/enabled is "always" or
; ---- "madvise".  The Windows build ignores the switch.  0 = the plain mmap.
%ifndef TT_HUGE
%define TT_HUGE 1
%endif
%define HASHDEF  32                 ; default transposition table size, MB
%define MOVEOH   12                 ; default 'Move Overhead', ms
%define HASHMAX  32768              ; Linux and Windows (zeval.exe is large-address-aware: the table may live above 4 GB)
%define HASHMAXS "32768"

%if UNIT == 0
section .data
align 16
knightoff:  db 33,31,18,14,-14,-18,-31,-33
kingoff:    db 1,-1,16,-16,15,17,-15,-17
bishoff:    db 15,17,-15,-17
rookoff:    db 1,-1,16,-16
ptab:       dq 0,0,knightoff,bishoff,rookoff,kingoff,kingoff
pcnt:       dd 0,0,8,4,4,8,8
phasew:     db 0,0,1,1,2,4,0,0, 0,0,1,1,2,4,0,0
pchars:     db ".PNBRQK..pnbrqk."
pvalue:     dd 0,100,320,330,500,900,0,0, 0,100,320,330,500,900,0,0
ch_pofs:    dw 0,0,64,128,192,256,320,0, 0,384,448,512,576,640,704,0   ; CONTHIST: piece code -> (piece index 0..11) * 64
%if CONTHIST
align 8
chbase:     dq chtab            ; CH_SHARED: the continuation table of the main thread (unit 0), used by the helper threads too
%endif

; castling-rights mask indexed by square (applied for from and to squares)
cmask:      db 13,15,15,15,12,15,15,14, 15,15,15,15,15,15,15,15
            times 96 db 15
            db 7,15,15,15,3,15,15,11, 15,15,15,15,15,15,15,15

startfen:   db "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",0
s_banner:   db "Zeval 1.0 by Hasan CALISIR - x86-64 assembly chess engine. Type moves like e2e4 (promotion: e7e8q), 'go' = engine moves, 'new', 'time <ms>', 'quit'.",10
s_banner_l  equ $-s_banner
s_uciok:    db "id name Zeval 1.0",10,"id author Hasan CALISIR",10
            db "option name Hash type spin default ", "32" ," min 1 max ", HASHMAXS, 10
            db "option name Clear Hash type button",10
            db "option name Threads type spin default 1 min 1 max ", SMPMAX_S, 10
            db "option name Move Overhead type spin default 12 min 0 max 5000",10
%ifndef TUNE
            db "uciok",10
%endif
s_uciok_l   equ $-s_uciok
%ifdef TUNE
s_uciend:   db "uciok",10                ; -DTUNE: the T options (tune_options) come between the options above and uciok
s_uciend_l  equ $-s_uciend
s_topt1:    db "option name T"
s_topt1_l   equ $-s_topt1
s_topt2:    db " type spin default "
s_topt2_l   equ $-s_topt2
s_topt3:    db " min -1000000000 max 1000000000",10
s_topt3_l   equ $-s_topt3
%endif
s_readyok:  db "readyok",10
s_readyok_l equ $-s_readyok
s_illegal:  db "Illegal move, try again.",10
s_illegal_l equ $-s_illegal
s_foot:     db "  a b c d e f g h",10
s_foot_l    equ $-s_foot
s_eng:      db "Engine: "
s_eng_l     equ $-s_eng
s_mate_w:   db "Checkmate - White wins.",10
s_mate_w_l  equ $-s_mate_w
s_mate_b:   db "Checkmate - Black wins.",10
s_mate_b_l  equ $-s_mate_b
s_stale:    db "Stalemate - draw.",10
s_stale_l   equ $-s_stale
s_rep:      db "Draw by repetition.",10
s_rep_l     equ $-s_rep
s_50:       db "Draw by the fifty-move rule.",10
s_50_l      equ $-s_50
s_insuf:    db "Draw - insufficient material.",10
s_insuf_l   equ $-s_insuf
s_prompt:   db "> "
s_bestmove: db "bestmove "
s_info:     db "info depth "
s_score_cp: db " score cp "
s_score_mt: db " score mate "
s_nodes:    db " nodes "
s_nps:      db " nps "
s_time:     db " time "
s_pv:       db " pv "
s_seldepth: db " seldepth "
s_hashfull: db " hashfull "
s_tbhits:   db " tbhits 0"
s_currmove: db " currmove "
s_cmnum:    db " currmovenumber "
s_ponder:   db " ponder "
s_nodes_srch: db "Nodes searched: "
s_fenlbl:   db "Fen: "
s_verfail:  db "VERIFY FAILED",10
s_verfail_l equ $-s_verfail
s_evallbl:  db "eval "
s_hashfail: db "info string Hash allocation failed, now using "
s_hashfail_l equ $-s_hashfail
s_hashmb:   db " MB",10

section .bss
alignb 64
; ---- the HOT line (64 bytes, a cache line of its own): what every search thread reads at (almost) every node.  It is written only when a search
; ---- starts (ttage, stopflag), when it is stopped (stopflag, hstop) and when the table is resized (ttbase, ttmaskv), never by a helper thread and
; ---- never together with unrelated variables, so the helper threads do not lose it to false sharing.  stopflag = the stop request of the main thread
; ---- (GUI stop / quit / EOF, time, node limit); hstop (the dword right behind it) stops only the helper threads.  A helper tests
; ---- cmp qword [stopflag], 0  = either of the two; unit 0 (and every unit of a build without SMP) tests the dword stopflag only.
stopflag:   resd 1
hstop:      resd 1
ttmaskv:    resd 1              ; (entries - 1) & ~1  : bucket index mask of the current table
ttage:      resd 1              ; age of the current search, 1..63
ttbase:     resq 1              ; transposition table (mmap / VirtualAlloc, or ttfb as a last resort)
cpu_sse41:  resb 1              ; 1 = the CPU has SSE4.1 (set at start-up; selects pick() with SIMDPICK 5)
            resb 39
chdirty:    resd 1              ; CONTHIST: bit k = unit k has searched since the last clear of its chtab (written only by the main thread)
alignb 64
; ---- shared state (unit 0 only): read by every unit.  Written only by the main thread while no search is running, except the
; ---- transposition table contents and the hot line above.
bestmv:     resd 1
canstop:    resd 1
quitflag:   resd 1
verifyflag: resd 1
uci_mode:   resd 1
hardlim:    resq 1
softlim:    resq 1
starttime:  resq 1
maxdepth:   resd 1
golimd:     resd 1              ; 1 = "go depth N" was given (smp_vote leaves a fixed-depth search to the main thread)
movetime_def: resd 1
fullm0:     resd 1
istty:      resd 1
pollfd:     resb 8
pendflag:   resd 1              ; 1: pendq holds command lines read during a search, to be run afterwards
pendlen:    resd 1              ; bytes used in pendq (NUL-terminated lines, oldest first)
nsm:        resd 1              ; go searchmoves: number of listed root moves (0 = unrestricted)
moveoh:     resd 1              ; option 'Move Overhead' (ms)
ttmb:       resd 1              ; current table size in MB (power of two)
nodelim:    resq 1              ; go nodes (all ones = no limit)
ttbytes:    resq 1
smoves:     resd 64             ; go searchmoves list (move codes as produced by gen)
infflag:    resd 1              ; go infinite: do not report bestmove before stop
polltmo:    resd 1              ; poll() timeout in ms (0 while searching)
tmon:       resd 1              ; time management: 1 = this go uses a real clock (wtime / btime): the TM_* switches may act
tm_prev:    resd 1              ; score of the previous search of this game (TM_FALL); tm_prevok = 0 after ucinewgame / Clear Hash
tm_prevok:  resd 1
%ifdef TUNE
tunetab:    resd TUNEN
%endif
zobp:       resq 2048
zobc:       resq 16
zobe:       resq 8
zobs:       resq 1
mgt:        resw 2048
egt:        resw 2048
lmrtab:     resb 64*64
optr:       resq 1
obuf:       resb 16384
linebuf:    resb LINEMAX
pendq:      resb PENDMAX
ibuf:       resb 65536
ipos:       resq 1
ilen:       resq 1
tsbuf:      resq 2
%if SMP
nthreads:   resd 1              ; option Threads (1 .. SMPMAX): search threads, the main thread included
smp_run:    resd 1              ; bit k set = the helper thread of unit k was started by the current search and is not joined yet
smp_last:   resd 1              ; the helpers of the search that smp_stop ended last (smp_vote reads and clears it)
%ifdef WIN
smp_hnd:    resq 32             ; thread handle of every helper (CreateThread; closed by smp_stop after the wait)
%else
smp_ctid:   resd 32             ; thread id word of every helper (the kernel clears it and wakes the waiter when the thread ends)
smp_stk:    resq 32             ; stack mapping of every helper: mapped on first use, kept (1 MB + guard page; only the pages used become resident)
%endif
%if SMP_POOL
pool_alive: resd 1              ; bit k set = the pool thread of unit k exists (it searches or waits)
%ifdef WIN
pool_evgo:  resq 32             ; auto-reset event: smp_start -> thread k, "search"
pool_evdone: resq 32            ; auto-reset event: thread k -> smp_stop, "search finished"
%else
pool_gen:   resd 32             ; futex word: search generation of thread k (raised by smp_start)
pool_busy:  resd 32             ; futex word: 1 while thread k searches (cleared by the thread)
%endif
%endif
%endif
alignb 64
ttfb:       resb 1<<20          ; 1 MB table, used only if the operating system refuses every allocation
%endif                          ; UNIT == 0

; ---- thread-local state block (every unit has its own; the symbols are local to the unit's object file).
; ---- Layout notes: [pos_start, pos_end) is what a helper unit copies from unit 0 in one go (unit_import); [rootbest, rpv_end)
; ---- is what it hands back (unit_export); bbp and bbc must stay adjacent (rep stosq in recompute_all).  The layout is the
; ---- same in every unit, so an offset measured in one unit is valid in all of them.
%define TLBL tlb_ %+ UNIT
section .bss
alignb 64
TLBL:
global TLBL
pos_start:
board:      resb 128
stm:        resd 1
castle:     resd 1
epsq:       resd 1
halfm:      resd 1
ksq:        resd 2
hashk:      resq 1
mgsc:       resd 1
egsc:       resd 1
phase:      resd 1
gply:       resd 1
cnt:        resb 16
%if BB
bbp:        resq 16             ; bitboard of each piece code (1..6 white, 9..14 black)
bbc:        resq 2              ; occupancy of each colour, indexed by the colour bits (0 = white at +0, 8 = black at +8)
%endif
pos_end:
ply:        resd 1
seldep:     resd 1                  ; deepest ply reached (search + quiescence) in the current iteration
%if QSSEE_ON
smq:        resd 1                  ; QSSEE: 1 while quiescence calls score_moves (it then marks the captures that quiescence prunes)
%endif
itdepth:    resd 1                  ; depth of the current iteration (for "info ... currmove")
alignb 8
nodes:      resq 1
rootbest:   resd 1
rootscore:  resd 1
rpvlen:     resd 1
sexcl:      resd 1                  ; SING: the move the next search() call excludes (read and cleared at its entry)
%ifdef SEVERIFY
alignb 8
sevhk:      resq 1                  ; SEVERIFY: hash key of the node that started the exclusion search
%endif
rpvgen:     resd 1                  ; PV_FINAL: raised by pv_saveroot every time the root line changes
lastpvgen:  resd 1                  ; PV_FINAL: rpvgen at the last "info ... pv" line printed (print_info)
rpv:        resd MAXPLY             ; root line belonging to [rootbest] (what "info ... pv" and "ponder" use)
rpv_end:
sevals:     resd MAXPLY+16          ; [MAXPLY .. MAXPLY+15]: scratch of the search switches (nmpmin, time management state), see S_* in p4_search.asm
; principal variation: triangular table, row p holds the line found at ply p in absolute indices p..pvlen[p]-1
pvlen:      resd MAXPLY+4
killers:    resd MAXPLY*2
alignb 64
histtab:    resw 2*128*128
%if CAPHIST
alignb 64
captab:     resw 16*128*8           ; capture history [piece code * 1024 + to * 8 + victim type] (CAPHIST)
%endif
alignb 64
pvtab:      resd (MAXPLY+2)*MAXPLY
alignb 64
ustack:     resb 32*MAXGAME
alignb 64
%if EV_CORR
corrtab:    resw 2 << CORR_BITS     ; pawn correction history of this unit (EV_CORR): [side to move][pawn index]
alignb 64
%endif
%if CONTHIST
chp:        resq MAXPLY+4           ; chp[ply + 2] = row of chtab of the move made at ply on the current path (CONTHIST)
alignb 64
chtab:      resw 768*768            ; continuation history [prev piece * 64 + prev to][piece * 64 + to], piece 0..11 (CONTHIST)
chdummy:    resw 768                ; the row of "no previous move" (plies -2 / -1, after a null move): stays 0, never updated
alignb 64
%endif
tlb_end:

; ---- Shared symbols: defined once in unit 0 (global) and referenced by the other units (extern).  A helper unit contains only the
; ---- search code, so this is the list of everything that code reads from the front end: tables, limits, flags, the output routines,
; ---- the transposition table pointers.  A symbol missing here is an assembler error ("symbol not defined") in the unit k >= 1 objects.
%if UNIT == 0
%define SHARED global
%else
%define SHARED extern
%endif
SHARED bestmv, canstop, hardlim, infflag, maxdepth, nodelim, nsm, polltmo, quitflag, smoves, softlim, starttime, stopflag
SHARED tmon, tm_prev, tm_prevok
%ifdef TUNE
SHARED tunetab
%endif
%if CONTHIST
SHARED chbase
%endif
SHARED ttage, ttbase, ttmaskv, cpu_sse41, ch_pofs, chdirty, uci_mode, lmrtab, mgt, egt, zobp, zobc, zobe, zobs, cmask, phasew, pvalue
SHARED gettime_ms, ob_ch, ob_flush, ob_move, ob_num, ob_str, poll_input
SHARED s_cmnum, s_currmove, s_hashfull, s_info, s_nodes, s_nps, s_pv, s_score_cp, s_score_mt, s_seldepth, s_tbhits, s_time
%if BB
SHARED atab, rtab, btab, knight_att, king_att, patt_to, to7, k_nfa, k_nfh, k_r1, k_r3, k_r6, k_r8, k_nr1, k_nr8
%else
SHARED knightoff, kingoff, bishoff, rookoff, ptab, pcnt
%endif

section .text                   ; everything that follows (all units) is code
