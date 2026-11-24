%if UNIT == 0                   ; the UCI / console front end exists once (unit 0)

; ============================================================
; commands / main loop
; ============================================================
section .data
pat_depth:    db "depth",0
pat_movetime: db "movetime",0
pat_infinite: db "infinite",0
pat_wtime:    db "wtime",0
pat_btime:    db "btime",0
pat_winc:     db "winc",0
pat_binc:     db "binc",0
pat_mtg:      db "movestogo",0
pat_moves:    db "moves",0
pat_nodes:    db "nodes",0
pat_searchm:  db "searchmoves",0
o_hash:       db "hash",0
o_clear:      db "clear hash",0
o_threads:    db "threads",0
o_moh:        db "move overhead",0
%ifdef TUNE
align 4
; ---- -DTUNE test build: T0 .. T31.  index: 0 RFP margin per ply (85), 1 RFP improving credit (35), 2 NMP base R (3), 3 NMP eval divisor (200), 4 futility base (110),
; ---- 5 futility per ply (90), 6 LMR_CUT extra R (1), 7 LMR_HIST threshold (8000), 8 RAZOR base (130), 9 RAZOR per ply^2 (250; per ply with -DRAZOR_SQ=0), 10 LMR_RES deeper margin (60),
; ---- 11 LMR_RES shallower margin (8), 12 TM_FALL per cp vs previous search (24), 13 TM_FALL per cp vs previous iteration (12), 14 TM_STAB factor /1024 (780),
; ---- 15 TM_EFFORT factor /1024 (780), 16 TM_INST per change /1024 (1100), 17 EVALDIFF_HIST scale (4), 18 NMP_VER min depth (14), 19 IIR_CUT min depth (6)
tune_def:     dd 85, 35, NMP_RB, NMP_EDIV, 110, 90, LMR_CUTP, LMR_HT, RAZOR_B, RAZOR_M, LMR_DEEPM, LMR_SHALM, 24, 12, 780, 780, 1100, EDH_M, NMP_VERD, IIR_MIND
              ; 20 .. 39: evaluation constants (src/eval.asm, p1_data.asm EV_R50C) for SPSA
              dd KD_WEAK, KD_QCHK, KD_RCHK, KD_BCHK, KD_NCHK, KD_UNSAFE, KD_PIN, KD_KNDEF, EV_LAZYT, EV_LAZYT2, EV_R50C
              dd IN_PASSED, IN_PAWNS, IN_OUTFL, IN_BOTH, IN_INFIL, IN_NONPM, IN_UNWIN, IN_BIAS, IN_MGADD
              ; 40 .. 57: EV_SCALE2 constants (src/eval.asm)
              dd SC_M1, SC_M2, SC_OB0, SC_OB1, SC_OC0, SC_OC1, SC_OC2, SC_RK, SC_Q0, SC_Q1, SC_G0, SC_G1, SC_FL, SC_G6FL
              dd SC_RKP, SC_QKP, SC_RPR, SC_MPM
              ; 58: qsearch delta margin (p1_data.asm DELTA_M); 59: LMR_HIST threshold with CONTHIST and CH_LMR (LMR_HTC)
              dd DELTA_M, LMR_HTC
              ; 60, 61: continuation-history pruning (CH_PM, CH_PD)
              dd CH_PM, CH_PD
              ; 62 singular margin per ply (SE_M), 63 singular TT depth slack (SE_TTD), 64 SEE pruning of captures per depth^2 (SEE_CM),
              ; 65, 66 continuation bonus per ply and maximum (CH_BA, CH_BMAX), 67, 68 quiet history bonus per depth^2 and maximum (16, HIST_BMAX),
              ; 69, 70 capture history bonus per ply and maximum (CAP_BA, CAP_BMAX), 71, 72 LMR table: 1000 x base, 10000 x factor (750, 2135;
              ; the table is rebuilt when either is set), 73 first aspiration half-width (ASP_W), 74 depth limit of the late quiet move pruning
              ; (PR_MAXD), 75 depth limit of reverse futility pruning (RFP_MAXD), 76 constant of the late move count (LMP_B)
              dd SE_M, SE_TTD, SEE_CM, CH_BA, CH_BMAX, 16, HIST_BMAX, CAP_BA, CAP_BMAX, 750, 2135, ASP_W, PR_MAXD, RFP_MAXD, LMP_B
              times TUNEN-77 dd 0
%endif
c_quit:       db "quit",0
c_q:          db "q",0
c_uci:        db "uci",0
c_isready:    db "isready",0
c_newg:       db "ucinewgame",0
c_new:        db "new",0
c_pos:        db "position",0
c_go:         db "go",0
c_stop:       db "stop",0
c_d:          db "d",0
c_perft:      db "perft",0
c_eval:       db "eval",0
c_evalcfg:    db "evalcfg",0
%ifdef CHSHTEST
c_chsum:      db "chsum",0
%endif
%ifdef SESTAT
c_sestat:     db "sestat",0
%endif
%ifdef PCVERIFY
c_pcstat:     db "pcstat",0
%endif
%ifdef HSTAT
c_hstat:      db "hstat",0
%endif
%ifdef ORDSTAT
c_ordstat:    db "ordstat",0
s_ordstat:    db "ordstat "
%endif
%ifdef EVALPROF
c_evalprof:   db "evalprof",0
s_evprof:     db "evalprof calls "
s_evprof2:    db " cycles "
s_evprof3:    db " phash probes "
s_evprof4:    db " hits "
%endif
c_verify:     db "verify",0
c_time:       db "time",0
c_setopt:     db "setoption",0
c_colon:      db ": "
%ifdef SEETEST
c_see:        db "see",0
%endif
section .text

; cmdcmp(rdi = pattern) -> eax = 1 if linebuf starts with that word; rsi -> char after the word
cmdcmp:
    mov rsi, linebuf
.l: movzx eax, byte [rdi]
    test eax, eax
    jz .end
    cmp al, [rsi]
    jne .no
    inc rdi
    inc rsi
    jmp .l
.end:
    movzx eax, byte [rsi]
    test eax, eax
    jz .yes
    cmp al, ' '
    jne .no
.yes:
    mov eax, 1
    ret
.no:xor eax, eax
    ret

; ---- transposition table: memory comes from the OS (mmap / VirtualAlloc), size set by option Hash
; tt_alloc(ebx = MB) -> rax = base address or 0.  Fresh memory is zero.  Clobbers rcx, rdx, rsi, rdi, r8 - r11 (as the plain mmap does).
tt_alloc:
%if TT_HUGE
%ifndef WIN
    ; Linux, TT_HUGE 1: a table of 2 MB or more starts on a 2 MB boundary and is marked MADV_HUGEPAGE, so the kernel can back it with transparent
    ; huge pages (one TLB entry per 2 MB instead of one per 4 KB).  mmap len + 2 MB, give the unaligned head and the tail back with munmap
    ; (tt_free then unmaps exactly [base, base + len) as before), madvise(base, len, MADV_HUGEPAGE).  Fallbacks: no mapping of len + 2 MB ->
    ; the plain mmap below; madvise refused (THP "never", old kernel) -> the aligned table with normal pages.  The contents are the same (zero).
    cmp ebx, 2
    jb .plain
    push r12
    push r14
    push r15
    mov r15d, ebx
    shl r15, 20                     ; r15 = len
    lea rsi, [r15+0x200000]
    mov eax, 9                      ; mmap(0, len + 2 MB, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_ANONYMOUS, -1, 0)
    xor edi, edi
    mov edx, 3
    mov r10d, 0x22
    mov r8, -1
    xor r9d, r9d
    syscall
    cmp rax, -4096
    ja .hfail
    mov r12, rax                    ; r12 = start of the mapping
    lea r14, [rax+0x1FFFFF]
    and r14, -0x200000              ; r14 = base, 2 MB aligned, base + len <= start + len + 2 MB
    mov rdi, r12
    mov rsi, r14
    sub rsi, r12                    ; head = base - start
    jz .nohead
    mov eax, 11                     ; munmap(start, head)
    syscall
.nohead:
    lea rdi, [r14+r15]              ; tail = [base + len, start + len + 2 MB): 2 MB - head bytes, never empty
    lea rsi, [r12+r15+0x200000]
    sub rsi, rdi
    mov eax, 11                     ; munmap(base + len, tail)
    syscall
    mov rdi, r14
    mov rsi, r15
    mov edx, 14                     ; MADV_HUGEPAGE; the result does not matter
    mov eax, 28                     ; madvise
    syscall
    mov rax, r14
    pop r15
    pop r14
    pop r12
    ret
.hfail:
    pop r15
    pop r14
    pop r12
.plain:
%endif
%endif
    mov esi, ebx
    shl rsi, 20
    mov eax, 9                  ; mmap(0, len, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_ANONYMOUS, -1, 0)
    xor edi, edi
    mov edx, 3
    mov r10d, 0x22
    mov r8, -1
    xor r9d, r9d
    syscall
    cmp rax, -4096
    jbe .ok
    xor eax, eax
.ok:ret

; tt_free: give the current table back (the static fallback table is never freed)
tt_free:
    mov rdi, [ttbase]
    test rdi, rdi
    jz .r
    cmp rdi, ttfb
    je .r
    mov rsi, [ttbytes]
    mov eax, 11                 ; munmap
    syscall
.r: mov qword [ttbase], 0
    ret

; tt_set(rax = base, ebx = MB, a power of two)
tt_set:
    mov [ttbase], rax
    mov [ttmb], ebx
    mov eax, ebx
    shl eax, 16                 ; entries = MB * 65536 (16 bytes each)
    sub eax, 2                  ; bucket index mask: (entries - 1) & ~1
    mov [ttmaskv], eax
    mov eax, ebx
    shl rax, 20
    mov [ttbytes], rax
    ret

; tt_init: default size; if the system refuses, try smaller tables, finally the 1 MB static one
tt_init:
    mov ebx, HASHDEF
.t: call tt_alloc
    test rax, rax
    jnz .s
    shr ebx, 1
    jnz .t
    mov rax, ttfb
    mov ebx, 1
.s: jmp tt_set

; tt_resize(edi = requested MB, 1..HASHMAX): rounded down to a power of two.  If the new size cannot be
; allocated the old size is restored (or the static table) and a UCI "info string" says so.
tt_resize:
    push rbx
    push r12
    push r13
    bsr ecx, edi
    mov ebx, 1
    shl ebx, cl
    cmp ebx, [ttmb]
    je .done                    ; already that size
    mov r12d, [ttmb]
    xor r13d, r13d
    call tt_free
    call tt_alloc
    test rax, rax
    jnz .set
    mov r13d, 1
    mov ebx, r12d
    call tt_alloc
    test rax, rax
    jnz .set
    mov rax, ttfb
    mov ebx, 1
.set:
    call tt_set
    test r13d, r13d
    jz .done
    OBS s_hashfail, s_hashfail_l
    mov eax, [ttmb]
    call ob_num
    OBS s_hashmb, 4
    call ob_flush
.done:
    pop r13
    pop r12
    pop rbx
    ret

tt_clear:
    mov rdi, [ttbase]
    mov rcx, [ttbytes]
    shr rcx, 3
    xor eax, eax
    rep stosq
    ret

; clear_tables: transposition table, history and killers (also what the UCI button "Clear Hash" does)
clear_tables:
    mov dword [tm_prevok], 0    ; a new game: no score of a previous search
    call tt_clear
    mov edi, histtab
    mov ecx, (2*128*128*2)/8
    xor eax, eax
    rep stosq
    mov edi, killers
    mov ecx, MAXPLY*2
    rep stosd
%if CAPHIST
    mov edi, captab
    mov ecx, (16*128*8*2)/8
    rep stosq
%endif
%if EV_CORR
    mov edi, corrtab
    mov ecx, (2 << CORR_BITS) / 4
    rep stosq
%endif
%if CONTHIST
    push rbx                    ; continuation history: only the units that searched since the last clear (1.2 MB each)
    xor ebx, ebx
.ch:bt dword [chdirty], ebx
    jnc .chn
    mov rax, [unit_tab+rbx*8]
    lea rdi, [rax + (chtab - tlb_0)]
    mov ecx, (768*768*2)/8
    xor eax, eax
    rep stosq
.chn:inc ebx
    cmp ebx, NUNITS
    jb .ch
    mov dword [chdirty], 0
    pop rbx
%endif
%if NUNITS > 1
    push rbx                    ; the helper units: their history and killers are cleared too (same layout in every unit)
    mov ebx, 1
.cu:mov rax, [unit_tab+rbx*8]
    lea rdi, [rax + (histtab - tlb_0)]
    mov ecx, (2*128*128*2)/8
    xor eax, eax
    rep stosq
    mov rax, [unit_tab+rbx*8]
    lea rdi, [rax + (killers - tlb_0)]
    mov ecx, MAXPLY*2
    xor eax, eax
    rep stosd
%if CAPHIST
    mov rax, [unit_tab+rbx*8]
    lea rdi, [rax + (captab - tlb_0)]
    mov ecx, (16*128*8*2)/8
    xor eax, eax
    rep stosq
%endif
%if EV_CORR
    mov rax, [unit_tab+rbx*8]
    lea rdi, [rax + (corrtab - tlb_0)]
    mov ecx, (2 << CORR_BITS) / 4
    xor eax, eax
    rep stosq
%endif
    inc ebx
    cmp ebx, NUNITS
    jb .cu
    pop rbx
%endif
    ret

newgame:
    call clear_tables
    mov rsi, startfen
    call setfen
    ret

; insufficient material test (same rule as isdraw) -> eax
is_insuf:
    movzx eax, byte [cnt+1]
    or al, [cnt+9]
    or al, [cnt+4]
    or al, [cnt+12]
    or al, [cnt+5]
    or al, [cnt+13]
    jnz .no
    movzx eax, byte [cnt+2]
    add al, [cnt+3]
    add al, [cnt+10]
    add al, [cnt+11]
    cmp eax, 1
    jle .yes
.no:xor eax, eax
    ret
.yes:
    mov eax, 1
    ret

; rep3 -> eax = 1 if the current position has occurred at least three times
rep3:
    mov eax, [halfm]
    mov ecx, [gply]
    mov edx, eax
    cmp edx, ecx
    jle .lim
    mov edx, ecx
.lim:
    mov r8, [hashk]
    mov esi, 2
    xor r9d, r9d
.rp:cmp esi, edx
    jg .end
    mov eax, ecx
    sub eax, esi
    shl eax, 5
    cmp [ustack+rax], r8
    jne .nx
    inc r9d
.nx:add esi, 2
    jmp .rp
.end:
    xor eax, eax
    cmp r9d, 2
    setae al
    ret

; game_over -> eax = 1 if the game has ended (message printed)
game_over:
    call has_legal
    test eax, eax
    jnz .alive
    call in_check
    test eax, eax
    jz .stale
    cmp dword [stm], 0
    je .bw
    OBS s_mate_w, s_mate_w_l
    jmp .over
.bw:OBS s_mate_b, s_mate_b_l
    jmp .over
.stale:
    OBS s_stale, s_stale_l
    jmp .over
.alive:
    cmp dword [halfm], 100
    jl .n50
    OBS s_50, s_50_l
    jmp .over
.n50:
    call is_insuf
    test eax, eax
    jz .nins
    OBS s_insuf, s_insuf_l
    jmp .over
.nins:
    call rep3
    test eax, eax
    jz .no
    OBS s_rep, s_rep_l
.over:
    mov eax, 1
    ret
.no:xor eax, eax
    ret

; setup_go: parse "go" limits from linebuf into maxdepth/softlim/hardlim/canstop
setup_go:
    push rbx
    push r12
    push r13
    push r14
    mov dword [maxdepth], 64
    mov dword [golimd], 0
    mov dword [canstop], 0
    mov dword [infflag], 0
    mov dword [tmon], 0         ; set below only for a real clock (wtime / btime)
    mov rax, 0x10000000000
    mov [hardlim], rax
    mov [softlim], rax
    mov qword [nodelim], -1
    mov dword [nsm], 0
    xor ebx, ebx
    mov rsi, linebuf            ; go nodes <n>
    mov edi, pat_nodes
    call find_tok
    test rax, rax
    jz .nnod
    mov rsi, rax
    call parse_num64
    test rax, rax
    jnz .nn1
    mov eax, 1
.nn1:mov [nodelim], rax
.nnod:
    mov rsi, linebuf            ; go searchmoves <move> ...  (the list ends at the first token that is no move)
    mov edi, pat_searchm
    call find_tok
    test rax, rax
    jz .nsm
    mov r12, rax
    xor r13d, r13d
.sm1:cmp byte [r12], ' '
    jne .sm2
    inc r12
    jmp .sm1
.sm2:movzx eax, byte [r12]
    sub eax, 'a'
    cmp eax, 7
    ja .smd
    movzx eax, byte [r12+1]
    sub eax, '1'
    cmp eax, 7
    ja .smd
    movzx eax, byte [r12+2]
    sub eax, 'a'
    cmp eax, 7
    ja .smd
    movzx eax, byte [r12+3]
    sub eax, '1'
    cmp eax, 7
    ja .smd
    mov rsi, r12
    call find_text_move
    test eax, eax
    jz .sm3
    cmp r13d, 64
    jae .sm3
    mov [smoves+r13*4], eax
    inc r13d
.sm3:inc r12
    cmp byte [r12], ' '
    je .sm1
    cmp byte [r12], 0
    jne .sm3
.smd:mov [nsm], r13d
.nsm:
    mov rsi, linebuf
    mov edi, pat_depth
    call find_tok
    test rax, rax
    jz .nd
    mov rsi, rax
    call parse_num
    cmp eax, 64
    jbe .d1
    mov eax, 64
.d1:test eax, eax
    jnz .d2
    mov eax, 1
.d2:mov [maxdepth], eax
    mov dword [golimd], 1
    mov ebx, 1
.nd:
    mov rsi, linebuf
    mov edi, pat_infinite
    call find_tok
    test rax, rax
    jz .ni
    cmp dword [uci_mode], 0     ; console mode cannot be interrupted: ignore 'infinite' there
    je .ni
    mov ebx, 1
    mov dword [infflag], 1
.ni:
    mov rsi, linebuf
    mov edi, pat_movetime
    call find_tok
    test rax, rax
    jz .nmt
    mov rsi, rax
    call parse_num
    mov eax, eax
    mov rcx, rax
    call ovh_adjust
    mov [hardlim], rcx
    add rax, rax
    mov [softlim], rax
    mov dword [canstop], 1
    mov ebx, 1
.nmt:
    test ebx, ebx
    jnz .out
    ; clock based
    mov edi, pat_wtime
    cmp dword [stm], 0
    je .c1
    mov edi, pat_btime
.c1:mov rsi, linebuf
    call find_tok
    test rax, rax
    jz .nclock
    mov rsi, rax
    call parse_num
    mov r12d, eax               ; time left
    xor r13d, r13d              ; increment
    mov edi, pat_winc
    cmp dword [stm], 0
    je .c2
    mov edi, pat_binc
.c2:mov rsi, linebuf
    call find_tok
    test rax, rax
    jz .ni2
    mov rsi, rax
    call parse_num
    mov r13d, eax
.ni2:
    mov r14d, 28                ; moves to go
    mov rsi, linebuf
    mov edi, pat_mtg
    call find_tok
    test rax, rax
%if TM_CURVE
    jz .tmc
%else
    jz .nm2
%endif
    mov rsi, rax
    call parse_num
    test eax, eax
    jnz .m3
    mov eax, 1
.m3:cmp eax, 50
    jbe .m4
    mov eax, 50
.m4:mov r14d, eax
    inc r14d
%if TM_CURVE
    jmp .nm2
.tmc:
    call tm_curve               ; no movestogo: eax = softlim of the game curve (TM_CURVE)
    jmp .a2
%endif
.nm2:
    mov eax, r12d
    xor edx, edx
    div r14d                    ; time / mtg
    mov ecx, r13d
    imul ecx, 3
    shr ecx, 2
    add eax, ecx                ; alloc
    mov ecx, r12d
    shr ecx, 2
    cmp eax, ecx                ; never more than 1/4 of the clock
    jbe .a1
    mov eax, ecx
.a1:test eax, eax
    jnz .a2
    mov eax, 1
.a2:mov [softlim], rax
    lea rcx, [rax*4]            ; hard = 4 * alloc
    mov edx, r12d
    shr edx, 1                  ; ... but at most 9/16 (56 %) of the clock
    mov esi, r12d
    shr esi, 4
    add edx, esi
    cmp rcx, rdx
    jbe .h1
    mov rcx, rdx
.h1:call ovh_adjust
    mov [hardlim], rcx
    mov dword [canstop], 1
    mov dword [tmon], 1         ; clock based search: the TM_* switches (if any) may shorten / extend it
%ifdef TMCDBG
    OBS s_tmcdbg, s_tmcdbg_l    ; (test build) info string tmc <gameply> <clock> <inc> <softlim> <hardlim>
    mov eax, [fullm0]
    add eax, [gply]
    call ob_num
    mov al, ' '
    call ob_ch
    mov eax, r12d
    call ob_num
    mov al, ' '
    call ob_ch
    mov eax, r13d
    call ob_num
    mov al, ' '
    call ob_ch
    mov rax, [softlim]
    call ob_num
    mov al, ' '
    call ob_ch
    mov rax, [hardlim]
    call ob_num
    mov al, 10
    call ob_ch
    call ob_flush
%endif
    jmp .out
.nclock:
    cmp qword [nodelim], -1     ; go nodes without a clock: the node limit is the only limit
    jne .out
    mov eax, [movetime_def]
    mov rcx, rax
    call ovh_adjust
    mov [hardlim], rcx
    add rax, rax
    mov [softlim], rax
    mov dword [canstop], 1
.out:
    pop r14
    pop r13
    pop r12
    pop rbx
    ret

%ifdef TMCDBG
s_tmcdbg:   db "info string tmc "
s_tmcdbg_l  equ $-s_tmcdbg
%endif
%if TM_CURVE
; tm_curve (TM_CURVE, see p1_data.asm): r12d = clock (ms), r13d = increment (ms) -> eax = softlim (ms, >= 2).  clobbers rcx, rdx, rsi.
tm_curve:
    mov eax, r13d
    imul rax, rax, TMC_H - 1                ; inc * (H - 1)
    mov ecx, r12d
    add rax, rcx
    movsxd rcx, dword [moveoh]
    imul rcx, rcx, TMC_H
    sub rax, rcx                            ; rem = clock + inc * (H - 1) - overhead * H
    cmp rax, 1
    jge .r1
    mov eax, 1
.r1:mov rsi, rax
    mov eax, [fullm0]
    add eax, [gply]                         ; game ply
    cmp eax, TMC_PM
    jbe .p1
    mov eax, TMC_PM
.p1:imul eax, eax, TMC_F1 - TMC_F0
    xor edx, edx
    mov ecx, TMC_PM
    div ecx
    add eax, TMC_F0                         ; share (1/10000)
    imul rax, rsi
    xor edx, edx
    mov ecx, 10000
    div rcx                                 ; target = rem * share / 10000
    mov ecx, r12d
    xchg rax, rcx
    xor edx, edx
    mov esi, TMC_CAP
    div rsi                                 ; clock / TMC_CAP
    cmp rcx, rax
    jbe .c1
    mov rcx, rax
.c1:test rcx, rcx
    jnz .c2
    mov ecx, 1
.c2:lea eax, [rcx+rcx]                      ; softlim = 2 * target
    ret
%endif

; ovh_adjust: rcx = nominal time limit in ms -> rcx = hard limit after the safety margin.
; With the default Move Overhead (12): limit > 30 ? limit - 12 : limit.
; Another value shifts the margin by (overhead - 12); the result is never above the nominal limit, never below 1.
; clobbers r8, r9
ovh_adjust:
    mov r8, rcx
    cmp rcx, 30
    jbe .k
    sub rcx, 12
.k: movsxd r9, dword [moveoh]
    sub r9, MOVEOH
    sub rcx, r9
    cmp rcx, r8
    jle .u
    mov rcx, r8
.u: test rcx, rcx
    jg .r
    mov ecx, 1
.r: ret

; parse_num64(rsi) -> rax = unsigned number (saturates far below 2^63), rsi advanced; skips leading blanks
parse_num64:
.s: cmp byte [rsi], ' '
    jne .d
    inc rsi
    jmp .s
.d: xor eax, eax
    mov rdx, 0x0100000000000000
.l: movzx ecx, byte [rsi]
    sub ecx, '0'
    cmp ecx, 9
    ja .r
    cmp rax, rdx
    jae .k
    imul rax, rax, 10
    add rax, rcx
.k: inc rsi
    jmp .l
.r: ret

; find_text_move(rsi = "e2e4[qrbn]") -> eax = the generated move that matches (0 if none); the move is NOT played.
; Same matching rules as apply_text_move; legality is not checked here.
find_text_move:
    push rbx
    push rbp
    push r12
    push r13
    push r14
    push r15
    sub rsp, MOVEBUF
    movzx eax, byte [rsi]
    sub eax, 'a'
    cmp eax, 7
    ja .bad
    movzx ecx, byte [rsi+1]
    sub ecx, '1'
    cmp ecx, 7
    ja .bad
    shl ecx, 4
    add ecx, eax
    mov r12d, ecx               ; from
    movzx eax, byte [rsi+2]
    sub eax, 'a'
    cmp eax, 7
    ja .bad
    movzx ecx, byte [rsi+3]
    sub ecx, '1'
    cmp ecx, 7
    ja .bad
    shl ecx, 4
    add ecx, eax
    mov r13d, ecx               ; to
    mov r14d, 3                 ; promo default = queen
    movzx eax, byte [rsi+4]
    cmp al, 'n'
    jne .p1
    xor r14d, r14d
    jmp .pd
.p1:cmp al, 'b'
    jne .p2
    mov r14d, 1
    jmp .pd
.p2:cmp al, 'r'
    jne .pd
    mov r14d, 2
.pd:mov rdi, rsp
    xor esi, esi
    call gen
    mov rbx, rax
    mov rbp, rsp
.l: cmp rbp, rbx
    jae .bad
    mov r15d, [rbp]
    add rbp, 4
    mov eax, r15d
    and eax, 127
    cmp eax, r12d
    jne .l
    mov eax, r15d
    shr eax, 7
    and eax, 127
    cmp eax, r13d
    jne .l
    movzx eax, byte [board+r12]
    and eax, 7
    cmp eax, 1
    jne .go
    mov eax, r13d
    and eax, 0xF0
    cmp eax, 0x70
    je .pm
    test eax, eax
    jnz .go
.pm:mov eax, r15d
    shr eax, 14
    cmp eax, r14d
    jne .l
.go:mov eax, r15d
    jmp .ret
.bad:
    xor eax, eax
.ret:
    add rsp, MOVEBUF
    pop r15
    pop r14
    pop r13
    pop r12
    pop rbp
    pop rbx
    ret

; opt_num(rsi = text or 0) -> eax = unsigned value (saturated near 2^21), or -1 if there is no number
opt_num:
    test rsi, rsi
    jz .no
    movzx eax, byte [rsi]
    sub eax, '0'
    cmp eax, 9
    ja .no
    xor eax, eax
.v: movzx ecx, byte [rsi]
    sub ecx, '0'
    cmp ecx, 9
    ja .r
    cmp eax, 1<<21
    jae .s
    imul eax, eax, 10
    add eax, ecx
.s: inc rsi
    jmp .v
.r: ret
.no:mov eax, -1
    ret

; name_is(rdi = lowercase pattern, rsi = name start, rdx = name end) -> eax = 1 if equal, ASCII case ignored
name_is:
.l: movzx eax, byte [rdi]
    test eax, eax
    jz .e
    cmp rsi, rdx
    jae .no
    movzx ecx, byte [rsi]
    cmp ecx, 'A'
    jb .c
    cmp ecx, 'Z'
    ja .c
    or ecx, 0x20
.c: cmp eax, ecx
    jne .no
    inc rdi
    inc rsi
    jmp .l
.e: xor eax, eax
    cmp rsi, rdx
    sete al                     ; pattern used up: equal only if the name ends here too
    ret
.no:xor eax, eax
    ret

; --- setoption name <name> [value <v>]   (names are case-insensitive and may contain blanks)
do_setoption:
    push rbx
    push r12
    push r13
    mov rsi, linebuf
    add rsi, 9
.sk:cmp byte [rsi], ' '
    jne .nm
    inc rsi
    jmp .sk
.nm:cmp dword [rsi], 'name'
    jne .ret
    cmp byte [rsi+4], ' '
    jne .ret
    add rsi, 5
    mov rbx, rsi                ; start of the option name
    mov r12, rsi
    xor r13d, r13d              ; value text, 0 = none
.sc:movzx eax, byte [r12]
    test eax, eax
    jz .ne
    cmp eax, ' '
    jne .sn
    cmp dword [r12+1], 'valu'
    jne .sn
    cmp byte [r12+5], 'e'
    jne .sn
    movzx eax, byte [r12+6]
    test eax, eax
    jz .ne
    cmp eax, ' '
    jne .sn
    lea r13, [r12+7]            ; the name ends before " value ", the value text follows
    jmp .ne
.sn:inc r12
    jmp .sc
.ne:mov rdx, r12                ; name = [rbx, rdx)
    mov rsi, rbx
    mov edi, o_hash
    call name_is
    test eax, eax
    jnz .hash
    mov rdx, r12
    mov rsi, rbx
    mov edi, o_clear
    call name_is
    test eax, eax
    jnz .clear
    mov rdx, r12
    mov rsi, rbx
    mov edi, o_moh
    call name_is
    test eax, eax
    jnz .moh
%if SMP
    mov rdx, r12
    mov rsi, rbx
    mov edi, o_threads
    call name_is
    test eax, eax
    jnz .threads
%endif
%ifdef UNITTEST
    mov rdx, r12
    mov rsi, rbx
    mov edi, o_unit
    call name_is
    test eax, eax
    jnz .unit
    mov rdx, r12
    mov rsi, rbx
    mov edi, o_conc
    call name_is
    test eax, eax
    jnz .conc
    mov rdx, r12
    mov rsi, rbx
    mov edi, o_conco
    call name_is
    test eax, eax
    jnz .conco
    mov rdx, r12
    mov rsi, rbx
    mov edi, o_ttrace
    call name_is
    test eax, eax
    jnz .ttrace
%endif
%ifdef TUNE
    movzx eax, byte [rbx]           ; T<n>: tuning parameter n (0 .. TUNEN-1), see tune_def
    or eax, 0x20
    cmp eax, 't'
    jne .ret
    lea rcx, [rbx+1]
    xor edx, edx
    xor esi, esi
.td:cmp rcx, r12
    jae .tdd
    movzx eax, byte [rcx]
    sub eax, '0'
    cmp eax, 9
    ja .ret
    imul edx, edx, 10
    add edx, eax
    inc esi
    inc rcx
    jmp .td
.tdd:
    test esi, esi
    jz .ret
    cmp esi, 2
    ja .ret
    cmp edx, TUNEN
    jae .ret
    mov ebx, edx
    mov rsi, r13
    xor r8d, r8d
    test rsi, rsi
    jz .ret
    cmp byte [rsi], '-'             ; a tuning value may be negative (e.g. T35 IN_INFIL -5)
    jne .tpos
    inc rsi
    inc r8d
.tpos:
    call opt_num
    cmp eax, -1
    je .ret
    test r8d, r8d
    jz .tst
    neg eax
.tst:
    mov [tunetab+rbx*4], eax
    cmp ebx, 71                     ; T71 / T72: the LMR table follows its curve
    jb .ret
    cmp ebx, 72
    ja .ret
    call init_lmr
%endif
    jmp .ret                    ; unknown options (and Threads in builds without SMP: Windows, the test builds): accepted, nothing to do
%ifdef UNITTEST
.unit:                          ; test build: setoption name Unit value k  = run "go" in a helper thread of unit k (0 = main thread)
    mov rsi, r13
    call opt_num
    cmp eax, NUNITS
    jae .ret                    ; (also catches -1)
    mov [utest_unit], eax
    jmp .ret
.conc:                          ; setoption name Conc value n: "go" runs the units 0 .. n-1 at the same time (0 = off), see p7_units.asm
    mov rsi, r13
    call opt_num
    cmp eax, 32
    ja .ret                     ; (also catches -1)
    mov [utest_conc], eax
    jmp .ret
.ttrace:                        ; setoption name TTRace value n: the TT race test with n * 1000 iterations per unit, see p7_units.asm
    mov rsi, r13
    call opt_num
    test eax, eax
    jle .ret                    ; (also catches -1)
    imul rax, rax, 1000
    call ttr_run
    jmp .ret
.conco:                         ; setoption name ConcOnly value k: "go" runs only unit k (reference run); k >= NUNITS = off
    mov rsi, r13
    call opt_num
    cmp eax, NUNITS
    jae .coff
    inc eax
    mov [utest_only], eax
    jmp .ret
.coff:
    mov dword [utest_only], 0
    jmp .ret
%endif
.hash:
    mov rsi, r13
    call opt_num
    cmp eax, -1
    je .ret
    test eax, eax
    jnz .h1
    mov eax, 1
.h1:cmp eax, HASHMAX
    jbe .h2
    mov eax, HASHMAX
.h2:mov edi, eax
    call tt_resize
    jmp .ret
.clear:
    call clear_tables
    jmp .ret
%if SMP
.threads:                       ; setoption name Threads value n: n search threads (1 .. SMPMAX), the main thread included.  Never a search running here.
    mov rsi, r13
    call opt_num
    cmp eax, -1
    je .ret
    test eax, eax
    jnz .t1
    mov eax, 1
.t1:cmp eax, SMPMAX
    jbe .t2
    mov eax, SMPMAX
.t2:mov [nthreads], eax
    jmp .ret
%endif
.moh:
    mov rsi, r13
    call opt_num
    cmp eax, -1
    je .ret
    cmp eax, 5000
    jbe .m1
    mov eax, 5000
.m1:mov [moveoh], eax
.ret:
    pop r13
    pop r12
    pop rbx
    ret

; --- position command
do_position:
    mov rsi, linebuf
    add rsi, 8
.sp:cmp byte [rsi], ' '
    jne .chk
    inc rsi
    jmp .sp
.chk:
    cmp dword [rsi], 'star'
    jne .fen
    mov rsi, startfen
    call setfen
    jmp .moves
.fen:
    cmp dword [rsi], 'fen '
    jne .ret
    add rsi, 4
    call setfen
.moves:
    mov rsi, linebuf
    mov edi, pat_moves
    call find_tok
    test rax, rax
    jz .ret
    mov rbx, rax
.ml:cmp byte [rbx], ' '
    jne .ch
    inc rbx
    jmp .ml
.ch:cmp byte [rbx], 0
    je .ret
    mov rsi, rbx
    call apply_text_move
    test eax, eax
    jz .ret
.adv:
    inc rbx
    cmp byte [rbx], ' '
    je .ml
    cmp byte [rbx], 0
    jne .adv
.ret:
    ret

%ifdef SEETEST
do_see:
    push rbx
    lea rsi, [linebuf+4]
    call find_text_move
    test eax, eax
    jz .bad
    mov ebx, eax
    lea rsi, [linebuf+4]
.sp:movzx ecx, byte [rsi]
    test ecx, ecx
    jz .bad
    cmp ecx, ' '
    je .th
    inc rsi
    jmp .sp
.th:cmp byte [rsi], ' '
    jne .tn
    inc rsi
    jmp .th
.tn:xor ecx, ecx
    cmp byte [rsi], '-'
    jne .num
    inc rsi
    mov ecx, 1
.num:
    push rcx
    call parse_num
    pop rcx
    test ecx, ecx
    jz .go
    neg eax
.go:mov esi, eax
    mov edi, ebx
    call see_ge
    add al, '0'
    call ob_ch
    mov al, 10
    call ob_ch
    call ob_flush
    pop rbx
    ret
.bad:
    mov al, '?'
    call ob_ch
    mov al, 10
    call ob_ch
    call ob_flush
    pop rbx
    ret
%endif

; --- perft N (divide)
do_perft:
    push rbx
    push rbp
    push r12
    push r13
    push r14
    push r15
    sub rsp, MOVEBUF
    mov rsi, linebuf
    add rsi, 5
    call parse_num
    test eax, eax
    jnz .dn
    mov eax, 1
.dn:cmp eax, MAXPLY-8        ; deeper than the ply arrays: clamp
    jbe .dc
    mov eax, MAXPLY-8
.dc:mov r12d, eax
    call gettime_ms
    mov [starttime], rax
    mov rdi, rsp
    xor esi, esi
    call gen
    mov r13, rax
    mov r14, rsp
    xor r15d, r15d
.l: cmp r14, r13
    jae .fin
    mov ebx, [r14]
    add r14, 4
    mov edi, ebx
    call domove
    test eax, eax
    jz .undo
    ; print move (needs pre-move position, so undo, print, redo)
    call undomove
    mov edi, ebx
    call ob_move
    mov al, ':'
    call ob_ch
    mov al, ' '
    call ob_ch
    mov edi, ebx
    call domove
    mov eax, 1
    cmp r12d, 1
    je .cnt
    lea edi, [r12-1]
    call perft
.cnt:
    add r15, rax
    call ob_num
    mov al, 10
    call ob_ch
    call ob_flush
.undo:
    call undomove
    jmp .l
.fin:
    OBS s_nodes_srch, 16
    mov rax, r15
    call ob_num
    mov al, 10
    call ob_ch
    call ob_flush
    add rsp, MOVEBUF
    pop r15
    pop r14
    pop r13
    pop r12
    pop rbp
    pop rbx
    ret

; --- "go" in UCI mode
do_go_uci:
    push rbx
    push rbp
    call setup_go
%ifdef UNITTEST
    call think_dispatch         ; test build: the search may run in a helper unit (option Unit)
%else
    call think
%endif
    OBS s_bestmove, 9
    mov edi, [bestmv]
    test edi, edi
    jnz .mv
    mov al, '0'
    call ob_ch
    mov al, '0'
    call ob_ch
    mov al, '0'
    call ob_ch
    mov al, '0'
    call ob_ch
    jmp .nl
.mv:mov ebx, edi
    call ob_move
    ; "ponder <move>": second move of the root line; if the line is only one move long (search stopped early,
    ; root fail-high) the table is asked for the opponent's reply.  Either way the move must be legal.
    mov edi, ebx
    call domove
    test eax, eax
    jz .pundo
    xor ebp, ebp
    cmp dword [rpvlen], 2
    jb .ptt
    cmp [rpv], ebx
    jne .ptt
    mov ebp, [rpv+4]
    jmp .pchk
.ptt:
    mov rax, [hashk]
    call tt_probe
    test eax, eax
    jz .pundo
    mov ebp, edx                ; move = low half of the data qword that tt_probe returns
.pchk:
    test ebp, ebp
    jz .pundo
    mov edi, ebp
    call move_is_legal
    test eax, eax
    jz .pundo
    OBS s_ponder, 8
    mov edi, ebp
    call ob_move
.pundo:
    call undomove
.nl:mov al, 10
    call ob_ch
    call ob_flush
    pop rbp
    pop rbx
    ret

; --- engine move in console mode
engine_move_console:
    call setup_go
    call think
    mov edi, [bestmv]
    test edi, edi
    jz .none
    mov ebx, edi
    OBS s_eng, s_eng_l
    mov edi, ebx
    call ob_move
    mov al, 10
    call ob_ch
    mov edi, ebx
    call domove
    call printboard
    call game_over
    ret
.none:
    call game_over
    ret

_start:
    mov qword [optr], obuf
%if SIMDPICK == 5
%ifndef FORCE_SSE2
    mov eax, 1                  ; CPUID leaf 1: ECX bit 19 = SSE4.1 (selects pick())
    cpuid
    bt ecx, 19
    setc byte [cpu_sse41]
%endif
%endif
    call init_zob
    call init_eval
    call init_lmr
%if BB
    call init_bb
%endif
    call tt_init
%ifdef TUNE
    mov esi, tune_def
    mov edi, tunetab
    mov ecx, TUNEN
    rep movsd
    call init_lmr                   ; again, now from T71 / T72
%endif
    mov dword [moveoh], MOVEOH
%if SMP
    mov dword [nthreads], 1
%endif
    mov qword [nodelim], -1
    mov dword [movetime_def], 1000
    mov dword [maxdepth], 64
    mov eax, 16                 ; ioctl(0, TCGETS, buf)
    xor edi, edi
    mov esi, 0x5401
    mov edx, linebuf
    syscall
    xor ecx, ecx
    test eax, eax
    jnz .nt
    mov ecx, 1
.nt:mov [istty], ecx
    call newgame
    cmp dword [istty], 0
    je .loop
    OBS s_banner, s_banner_l
    call printboard
    call ob_flush
.loop:
    cmp dword [istty], 0
    je .rd
    cmp dword [uci_mode], 0
    jne .rd
    OBS s_prompt, 2
    call ob_flush
.rd:cmp dword [pendflag], 0
    je .rd2
    mov esi, pendq              ; oldest queued line first
    mov edi, linebuf
.pc:lodsb
    stosb
    test al, al
    jnz .pc
    mov eax, esi
    sub eax, pendq              ; bytes consumed
    mov ecx, [pendlen]
    sub ecx, eax
    mov [pendlen], ecx
    jnz .pm
    mov dword [pendflag], 0
    jmp .got
.pm:mov edi, pendq              ; the rest of the queue moves to the front
    rep movsb
    jmp .got
.rd2:call readline
    cmp eax, -1
    je .exit
.got:
    cmp byte [linebuf], 0
    je .loop
    mov edi, c_quit
    call cmdcmp
    test eax, eax
    jnz .exit
    mov edi, c_q
    call cmdcmp
    test eax, eax
    jnz .exit
    mov edi, c_uci
    call cmdcmp
    test eax, eax
    jz .n1
    mov dword [uci_mode], 1
    OBS s_uciok, s_uciok_l
    call ob_flush
    jmp .loop
.n1:mov edi, c_isready
    call cmdcmp
    test eax, eax
    jz .n2
    OBS s_readyok, s_readyok_l
    call ob_flush
    jmp .loop
.n2:mov edi, c_newg
    call cmdcmp
    test eax, eax
    jnz .ng
    mov edi, c_new
    call cmdcmp
    test eax, eax
    jz .n3
.ng:call newgame
    cmp dword [uci_mode], 0
    jne .loop
    call printboard
    call ob_flush
    jmp .loop
.n3:mov edi, c_pos
    call cmdcmp
    test eax, eax
    jz .n4
    call do_position
    jmp .loop
.n4:mov edi, c_go
    call cmdcmp
    test eax, eax
    jz .n5
    cmp dword [uci_mode], 0
    je .gocon
    call do_go_uci
    cmp dword [quitflag], 0
    je .loop
    cmp dword [pendflag], 0     ; EOF seen during the search: queued commands still run before the exit
    jne .loop
    jmp .exit
.gocon:
    call game_over_quiet
    test eax, eax
    jnz .loop
    call engine_move_console
    call ob_flush
    jmp .loop
.n5:mov edi, c_stop
    call cmdcmp
    test eax, eax
    jnz .loop
    mov edi, c_setopt
    call cmdcmp
    test eax, eax
    jz .n5b
    call do_setoption
    jmp .loop
.n5b:
    mov edi, c_d
    call cmdcmp
    test eax, eax
    jz .n6
    call printboard
    OBS s_fenlbl, 5
    call ob_fen
    mov al, 10
    call ob_ch
    call ob_flush
    jmp .loop
.n6:mov edi, c_perft
    call cmdcmp
    test eax, eax
    jz .n7
    call do_perft
    jmp .loop
.n7:mov edi, c_eval
    call cmdcmp
    test eax, eax
    jz .n7c
    call eval
    movsxd rax, eax
    push rax
    OBS s_evallbl, 5
    pop rax
    call ob_num
    mov al, 10
    call ob_ch
    call ob_flush
    jmp .loop
.n7c:mov edi, c_evalcfg              ; evaluation switches of this build (EV_CFG, src/eval.asm)
    call cmdcmp
    test eax, eax
    jz .n7p
    OBS c_evalcfg, 7
    mov al, ' '
    call ob_ch
    mov eax, EV_CFG
    call ob_num
    mov al, 10
    call ob_ch
    call ob_flush
    jmp .loop
.n7p:
%ifdef ORDSTAT
    mov edi, c_ordstat              ; (test build) move-ordering counters of the main thread, see ORDSTAT in p4_search.asm
    call cmdcmp
    test eax, eax
    jz .n7q
    OBS s_ordstat, 8
    push rbx
    xor ebx, ebx
.ol:mov rax, [ordc+rbx*8]
    call ob_num
    mov al, ' '
    call ob_ch
    inc ebx
    cmp ebx, 80
    jb .ol
    pop rbx
    mov al, 10
    call ob_ch
    call ob_flush
    jmp .loop
.n7q:
%endif
%ifdef CHSHTEST
    mov edi, c_chsum                ; (test build, tests/t_chshared.py) non-zero entries of the continuation table of every unit
    call cmdcmp
    test eax, eax
    jz .n7r
    OBS c_chsum, 5
    push rbx
    xor ebx, ebx
.cl:mov al, ' '
    call ob_ch
    mov rsi, [unit_tab+rbx*8]
    lea rsi, [rsi + (chtab - tlb_0)]
    mov ecx, 768*768
    xor eax, eax
.cw:cmp word [rsi], 0
    je .cz
    inc eax
.cz:add rsi, 2
    dec ecx
    jnz .cw
    call ob_num
    inc ebx
    cmp ebx, NUNITS
    jb .cl
    pop rbx
    mov al, 10
    call ob_ch
    call ob_flush
    jmp .loop
.n7r:
%endif
%ifdef SESTAT
    mov edi, c_sestat               ; (test build) counters of the singular test of the main thread, see SESTAT in p4_search.asm
    call cmdcmp
    test eax, eax
    jz .n7s
    OBS c_sestat, 6
    push rbx
    xor ebx, ebx
.sl:mov al, ' '
    call ob_ch
    mov rax, [sestc+rbx*8]
    call ob_num
    inc ebx
    cmp ebx, 24
    jb .sl
    pop rbx
    mov al, 10
    call ob_ch
    call ob_flush
    jmp .loop
.n7s:
%endif
%ifdef HSTAT
    mov edi, c_hstat                ; (test build) history-pruning counters of the main thread, see HSTAT in p4_search.asm
    call cmdcmp
    test eax, eax
    jz .n7hs
    OBS c_hstat, 5
    push rbx
    xor ebx, ebx
.hl:mov al, ' '
    call ob_ch
    mov rax, [hstc+rbx*8]
    call ob_num
    inc ebx
    cmp ebx, 18
    jb .hl
    pop rbx
    mov al, 10
    call ob_ch
    call ob_flush
    jmp .loop
.n7hs:
%endif
%ifdef PCVERIFY
    mov edi, c_pcstat               ; (test build) ProbCut counters of the main thread, see PCVERIFY in p4_search.asm
    call cmdcmp
    test eax, eax
    jz .n7pc
    OBS c_pcstat, 6
    push rbx
    xor ebx, ebx
.pl:mov al, ' '
    call ob_ch
    mov rax, [pcstc+rbx*8]
    call ob_num
    inc ebx
    cmp ebx, 16
    jb .pl
    pop rbx
    mov al, 10
    call ob_ch
    call ob_flush
    jmp .loop
.n7pc:
%endif
%ifdef EVALPROF
    mov edi, c_evalprof             ; (test builds) eval calls and rdtsc cycles of the main thread
    call cmdcmp
    test eax, eax
    jz .n8
    OBS s_evprof, 15
    mov rax, [evprof_n]
    call ob_num
    OBS s_evprof2, 8
    mov rax, [evprof_cyc]
    call ob_num
    OBS s_evprof3, 14
    mov rax, [evprof_php]
    call ob_num
    OBS s_evprof4, 6
    mov rax, [evprof_phh]
    call ob_num
    mov al, 10
    call ob_ch
    call ob_flush
    jmp .loop
%endif
.n8:mov edi, c_verify
    call cmdcmp
    test eax, eax
    jz .n9
    xor dword [verifyflag], 1
    jmp .loop
.n9:mov edi, c_time
    call cmdcmp
    test eax, eax
%ifdef SEETEST
    jz .nsee
%else
    jz .mv
%endif
    call parse_num
    test eax, eax
    jz .loop
    mov [movetime_def], eax
    jmp .loop
%ifdef SEETEST
.nsee:                          ; test build only:  see <move> <threshold>  -> 1 / 0 (or ? for an unknown move)
    mov edi, c_see
    call cmdcmp
    test eax, eax
    jz .mv
    call do_see
    jmp .loop
%endif
.mv:
    ; plain move text (console mode only: in UCI mode an unknown line is ignored, whatever it looks like)
    cmp dword [uci_mode], 0
    jne .loop
    mov rsi, linebuf
    call apply_text_move
    test eax, eax
    jnz .ok
    OBS s_illegal, s_illegal_l
    call ob_flush
    jmp .loop
.ok:
    cmp dword [uci_mode], 0
    jne .loop
    call game_over
    test eax, eax
    jnz .okf
    call engine_move_console
.okf:
    call ob_flush
    jmp .loop
.exit:
    call ob_flush
%ifdef WIN
    mov eax, 60                 ; (the Windows shim maps exit to ExitProcess, which ends every thread, the helpers included)
%else
    mov eax, 231                ; exit_group: ends every thread of the process (exit = 60 would end only the calling thread)
%endif
    xor edi, edi
    syscall

; silent variant for "go": if the game is already over, print the message and return 1
game_over_quiet:
    call game_over
    ret
%endif                          ; UNIT == 0

%ifdef PCVERIFY
%if UNIT == 0
section .bss
alignb 64
pcstc:      resq 16
section .text
%endif
%endif
%ifdef SESTAT
%if UNIT == 0
section .bss
alignb 64
sestc:      resq 32
section .text
%endif
%endif
%ifdef HSTAT
%if UNIT == 0
section .bss
alignb 64
hstc:       resq 32
section .text
%endif
%endif
%ifdef ORDSTAT
%if UNIT == 0
section .bss
alignb 64
ordc:       resq 80
section .text
%endif
%endif
