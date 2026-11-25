
; ---- STOPTEST: "has the search been stopped?".  Unit 0 (and every unit of a build without SMP) looks at the dword stopflag only: the stop request of
; ---- the main thread (GUI stop / quit / EOF, time, node limit).  A helper thread looks at the qword [stopflag] = stopflag OR hstop (the dword right
; ---- behind it, same cache line): the main thread sets hstop when it ends the search itself or stops the helper threads.  Both flags only ever go
; ---- 0 -> 1 while a search runs (they are cleared when no helper thread exists).
%ifdef ORDSTAT
; ---- ORDSTAT (test build, tests/t_ordering.py): move-ordering statistics of the MAIN thread (unit 0); console command "ordstat" prints ordc[0..63].
; ---- Counters only: the search does not read them, the plain build has none of this code (same machine code, same node counts).
; ----   ordc[0]      fail-high nodes of search()               ordc[1..8]   position of the cutoff move among the moves picked: 1 2 3 4 5 6-8 9-16 17+
; ----   ordc[9..12]  cutoff move: TT move / capture or promotion / killer / other quiet move
; ----   ordc[13..19] rank among the quiet moves picked (pruned ones count) of a killer / other quiet cutoff move: 1 2 3 4 5-8 9-16 17+
; ----   ordc[20]     search() nodes with a TT move that pick a first move    ordc[21] of them: the first move picked is the TT move
; ----   ordc[24]     fail-high nodes of qsearch()              ordc[25..28] position of the cutoff move: 1 2 3 4+
; ----   ordc[29]     qsearch() fail-highs in check (evasions)  ordc[62]     of them on the first move
; ----   ordc[30]     fail-highs of search() at depth >= 5      ordc[31]     of them on the first move
; ----   depth classes b = 0..5 for depth 1 2 3 4 5-7 8+:
; ----   ordc[32+b]   fail-highs                                ordc[38+b]   of them on the first move
; ----   ordc[44+b]   fail-highs at a node with a TT move       ordc[50+b]   of them on the first move
; ----   node types t = 0 PV, 1 cut, 2 all (neither):  ordc[56+t] fail-highs, ordc[59+t] of them on the first move
; ----   L_ORDQ (frame slot 132, ORDSTAT only) counts the quiet moves picked at the node.
; ---- ORDCAP (ORDSTAT): the captures at a cutoff.  The scores of the moves picked so far are L_SCORES[0 .. L_IDX - 1] (the cutoff move last);
; ----   good capture >= 0x1000000 (not the TT move 0x40000000), bad capture <= -200000.
; ----   64 cutoffs by a good capture   65 ... by the first good capture picked   66 ... sum of the good captures tried before it
; ----   67 cutoffs by a bad capture    68 ... sum of their positions
; ----   69 other cutoffs (killer, quiet, under-promotion) after at least one good capture   70 ... sum of the good captures tried before them
; ----   71 depth >= 5: cutoffs by a good capture   72 ... by the first good capture
%macro ORDCAP 0
%if UNIT == 0
    push rax
    push rcx
    push rdx
    push rsi
    push rdi
    mov ecx, [rbp+L_IDX]
    mov esi, [rbp+rcx*4+L_SCORES-4]
    xor edx, edx
    xor eax, eax
%%l:lea edi, [rcx-1]
    cmp eax, edi
    jge %%e
    mov edi, [rbp+rax*4+L_SCORES]
    cmp edi, 0x1000000
    jl %%n
    cmp edi, 0x40000000
    jge %%n
    inc edx
%%n:inc eax
    jmp %%l
%%e:cmp esi, 0x40000000
    je %%x
    cmp esi, 0x1000000
    jl %%b
    inc qword [ordc+64*8]
    add [ordc+66*8], rdx
    xor edi, edi
    test edx, edx
    sete dil
    add [ordc+65*8], rdi
    cmp dword [rbp+L_DEPTH], 5
    jl %%x
    inc qword [ordc+71*8]
    add [ordc+72*8], rdi
    jmp %%x
%%b:cmp esi, -200000
    jg %%q
    inc qword [ordc+67*8]
    add [ordc+68*8], rcx
    jmp %%x
%%q:test edx, edx
    jz %%x
    inc qword [ordc+69*8]
    add [ordc+70*8], rdx
%%x:pop rdi
    pop rsi
    pop rdx
    pop rcx
    pop rax
%endif
%endmacro
%macro ORDREC 0
%if UNIT == 0
    push rax
    push rcx
    push rdx
    push rsi
    inc qword [ordc]
    mov eax, [rbp+L_IDX]
    mov ecx, eax
    cmp eax, 5
    jbe %%h
    mov ecx, 6
    cmp eax, 8
    jbe %%h
    mov ecx, 7
    cmp eax, 16
    jbe %%h
    mov ecx, 8
%%h:inc qword [ordc+rcx*8]
    xor esi, esi                    ; esi = 1 for a first-move cutoff
    cmp dword [rbp+L_IDX], 1
    sete sil
    cmp dword [rbp+L_DEPTH], 5
    jl %%nd
    inc qword [ordc+30*8]
    add [ordc+31*8], rsi
%%nd:
    mov eax, [rbp+L_DEPTH]          ; depth class
    lea ecx, [rax-1]
    cmp eax, 1
    jge %%d1
    xor ecx, ecx
%%d1:cmp eax, 5
    jl %%d2
    mov ecx, 4
    cmp eax, 8
    jl %%d2
    mov ecx, 5
%%d2:inc qword [ordc+32*8+rcx*8]
    add [ordc+38*8+rcx*8], rsi
    cmp dword [rbp+L_TTMV], 0
    je %%nt
    inc qword [ordc+44*8+rcx*8]
    add [ordc+50*8+rcx*8], rsi
%%nt:
    xor ecx, ecx                    ; node type
    cmp dword [rbp+L_PV], 0
    jne %%ty
    mov ecx, 1
    cmp dword [rbp+L_CUT], 0
    jne %%ty
    mov ecx, 2
%%ty:inc qword [ordc+56*8+rcx*8]
    add [ordc+59*8+rcx*8], rsi
    mov eax, [rbp+L_MV]
    mov ecx, 9
    cmp eax, [rbp+L_TTMV]
    je %%c
    mov ecx, 10
    cmp dword [rbp+L_ISQ], 0
    je %%c
    mov edx, [ply]
    lea rdx, [killers+rdx*8]
    mov ecx, 11
    cmp eax, [rdx]
    je %%c
    cmp eax, [rdx+4]
    je %%c
    mov ecx, 12
%%c:inc qword [ordc+rcx*8]
    cmp ecx, 11
    jb %%done
    mov eax, [rbp+L_ORDQ]
    mov ecx, eax
    cmp eax, 4
    jbe %%q
    mov ecx, 5
    cmp eax, 8
    jbe %%q
    mov ecx, 6
    cmp eax, 16
    jbe %%q
    mov ecx, 7
%%q:inc qword [ordc+12*8+rcx*8]
%%done:
    pop rsi
    pop rdx
    pop rcx
    pop rax
%endif
%endmacro
; ORDFIRST: after the pick of a move in search() (eax = the move, L_IDX already counts it): the first move of a node with a TT move should be the TT move
%macro ORDFIRST 0
%if UNIT == 0
    cmp dword [rbp+L_IDX], 1
    jne %%x
    cmp dword [rbp+L_TTMV], 0
    je %%x
    inc qword [ordc+20*8]
    cmp eax, [rbp+L_TTMV]
    jne %%x
    inc qword [ordc+21*8]
%%x:
%endif
%endmacro
%macro ORDQ 0
%if UNIT == 0
    push rax
    push rcx
    inc qword [ordc+24*8]
    mov eax, [rbp+Q_IDX]
    mov ecx, eax
    cmp eax, 4
    jbe %%h
    mov ecx, 4
%%h:inc qword [ordc+24*8+rcx*8]
    cmp dword [rbp+Q_INCHK], 0      ; 0 / not 0 once the move loop runs
    je %%x
    inc qword [ordc+29*8]
    cmp dword [rbp+Q_IDX], 1
    jne %%x
    inc qword [ordc+62*8]
%%x:pop rcx
    pop rax
%endif
%endmacro
%endif
; ---- SESTAT (test build, tests/t_singular.py): counters of the singular test of the main thread, console command "sestat" prints sestc[0..23].
; ----   0 TT move first at a node that may test   1 + depth >= SE_MIND   2 + lower / exact bound   3 + TT depth   4 + no mate score, ply limit
; ----   5 exclusion searches (TT move legal)   6 singular   7 multi-cut   8 neither   9 nodes of the exclusion searches
; ----   12 + 3 * c + k: depth class c (6-7, 8-9, 10-12, 13+), k = 0 searches, 1 singular, 2 multi-cut
; ---- PCVERIFY (test build, ProbCut): counters of the main thread, console command "pcstat" prints pcstc[0..15]:
; ----   0 nodes that tried ProbCut   1 captures searched   2 captures past the quiescence check   3 cuts (would-be cuts in PCVERIFY)
; ----   4 would-be cuts confirmed by the full search   5 refuted (the node did not fail high)   8 + 2 * c / 9 + 2 * c: cuts / refuted by depth
; ----   class c (5-6, 7-8, 9-11, 12+)
%macro PCCNT 1
%ifdef PCVERIFY
%if UNIT == 0
    inc qword [pcstc+%1*8]
%endif
%endif
%endmacro
%macro PCDCNT 1
%ifdef PCVERIFY
%if UNIT == 0
    push rax
    push rcx
    mov eax, [rbp+L_DEPTH]
    xor ecx, ecx
    cmp eax, 7
    jl %%c
    inc ecx
    cmp eax, 9
    jl %%c
    inc ecx
    cmp eax, 12
    jl %%c
    inc ecx
%%c:lea ecx, [rcx*2+%1]
    inc qword [pcstc+rcx*8]
    pop rcx
    pop rax
%endif
%endif
%endmacro
; ---- HSTAT (test build): history-pruning counters of the main thread, console command "hstat" prints hstc[0..17].
; ----   0 CH_PRUNE tests   1 CH_PRUNE prunes   2 .. 8 row(ply - 1) + row(ply - 2) at the test: < -16000, < -8000, < -3000, < 0, < 3000,
; ----   < 8000, >= 8000   9 LMR_HIST steps tested   10 history step -1   11 history step +1   12 .. 17 histtab at that step: < -8000,
; ----   < -2000, < 0, < 2000, < 8000, >= 8000
%macro HSCNT 1
%ifdef HSTAT
%if UNIT == 0
    inc qword [hstc+%1*8]
%endif
%endif
%endmacro
%macro HSB7 1                       ; HSTAT: counter 2 + class of %1 (-16000 -8000 -3000 0 3000 8000)
%ifdef HSTAT
%if UNIT == 0
    push rcx
    mov ecx, 2
    cmp %1, -16000
    jl %%d
    inc ecx
    cmp %1, -8000
    jl %%d
    inc ecx
    cmp %1, -3000
    jl %%d
    inc ecx
    cmp %1, 0
    jl %%d
    inc ecx
    cmp %1, 3000
    jl %%d
    inc ecx
    cmp %1, 8000
    jl %%d
    inc ecx
%%d:inc qword [hstc+rcx*8]
    pop rcx
%endif
%endif
%endmacro
%macro HSB6 1                       ; HSTAT: counter 12 + class of %1 (-8000 -2000 0 2000 8000)
%ifdef HSTAT
%if UNIT == 0
    push rcx
    mov ecx, 12
    cmp %1, -8000
    jl %%d
    inc ecx
    cmp %1, -2000
    jl %%d
    inc ecx
    cmp %1, 0
    jl %%d
    inc ecx
    cmp %1, 2000
    jl %%d
    inc ecx
    cmp %1, 8000
    jl %%d
    inc ecx
%%d:inc qword [hstc+rcx*8]
    pop rcx
%endif
%endif
%endmacro
%macro SECNT 1
%ifdef SESTAT
%if UNIT == 0
    inc qword [sestc+%1*8]
%endif
%endif
%endmacro
%macro SEDCNT 1
%ifdef SESTAT
%if UNIT == 0
    push rax
    push rcx
    mov eax, [rbp+L_DEPTH]
    xor ecx, ecx
    cmp eax, 8
    jl %%c
    mov ecx, 1
    cmp eax, 10
    jl %%c
    mov ecx, 2
    cmp eax, 13
    jl %%c
    mov ecx, 3
%%c:lea ecx, [rcx*2+rcx+12+%1]
    inc qword [sestc+rcx*8]
    pop rcx
    pop rax
%endif
%endif
%endmacro
%macro STOPTEST 0
%if HELPER
    cmp qword [stopflag], 0
%else
    cmp dword [stopflag], 0
%endif
%endmacro
; ---- NODES_ALL: rax = nodes searched so far by ALL threads (the main thread adds the counters of the running helper threads).  Clobbers rax only.
%macro NODES_ALL 0
%if SMP && UNIT == 0
    call smp_nodes
%else
    mov rax, [nodes]
%endif
%endmacro

%if UNIT == 0
; ============================================================
; time control / stdin polling
; ============================================================
poll_input:
    mov rax, [ipos]
    cmp rax, [ilen]
    jb .have
    mov dword [pollfd], 0
    mov word [pollfd+4], 1
    mov word [pollfd+6], 0
    mov eax, 7
    mov edi, pollfd
    mov esi, 1
    mov edx, [polltmo]
    syscall
    test eax, eax
    jle .no
    movzx eax, word [pollfd+6]
    test eax, 1
    jnz .have
    test eax, 0x18              ; POLLERR/POLLHUP without data: finish this search, then exit
    jz .no
    jmp .eof
.no:ret
.have:
    call readline
    cmp eax, -1
    je .eof
    cmp dword [linebuf], 'isre'
    je .rdy
    cmp dword [pendflag], 0     ; script order: once a command is waiting, stop/quit wait behind it ...
    je .imm
    cmp dword [infflag], 0      ; ... unless an infinite search could never end without them
    je .enq
.imm:
    cmp dword [linebuf], 'stop'
    je .stop
    cmp dword [linebuf], 'quit'
    je .quit
.enq:
    test eax, eax               ; empty line: ignore
    jz .no
    ; any other command (position, go, ...) must not be lost: append it to the queue for the main loop.
    ; The queue is read first-in first-out and never blocks the input: a later "stop" is still seen.
    mov ecx, [pendlen]
    lea edx, [rcx+rax+1]
    cmp edx, PENDMAX
    ja .no                      ; queue full: drop the line
    mov [pendlen], edx
    lea edi, [pendq+rcx]
    lea ecx, [rax+1]
    mov esi, linebuf
    rep movsb
    mov dword [pendflag], 1
    ret
.rdy:
    OBS s_readyok, s_readyok_l
    call ob_flush
    ret
.eof:
    mov dword [quitflag], 1     ; EOF: let a bounded search finish, then exit ...
    cmp dword [infflag], 0
    je .no
    mov dword [stopflag], 1     ; ... but nobody is left to send "stop" after "go infinite"
    ret
.quit:
    mov dword [quitflag], 1
    mov dword [pendflag], 0     ; an explicit quit discards whatever is still queued
    mov dword [pendlen], 0
.stop:
    mov dword [stopflag], 1
    ret
%endif                          ; UNIT == 0 (poll_input)

check_time:
%if HELPER
    ret                         ; a helper thread never looks at the clock, the node limit or stdin: the main thread does, and sets stopflag
%else
    NODES_ALL                   ; go nodes: the limit is a limit of the TOTAL over all threads
    cmp rax, [nodelim]
    jb .nn
    mov dword [stopflag], 1     ; go nodes
.nn:cmp dword [canstop], 0
    je .poll
    call gettime_ms
    sub rax, [starttime]
    cmp rax, [hardlim]
    jl .poll
    mov dword [stopflag], 1
.poll:
    cmp dword [uci_mode], 0
    je .r
    call poll_input
.r: ret
%endif                          ; HELPER == 0 (check_time)

; ============================================================
; transposition table helpers.  Entry (16 bytes): key field 0, data qword 8: move 8 (16 bits), static eval code 10 (u16: eval + 32768,
; 0 = none; EVCACHE), score(i16) 12, depth(u8) 14, flag(u8) 15 = bound (bits 0-1: 1 lower, 2 upper, 3 exact) | age << 2.
; LOCK-FREE: the key field holds  key XOR data.  A writer stores the data qword first (one aligned 8-byte store), then
; key XOR data.  A reader loads the data qword ONCE, loads the key field and accepts the entry only if  key field XOR data == key;
; an entry whose two halves come from different writes does not match and counts as a miss.  Aligned 8-byte stores / loads are atomic
; on x86-64 and stores are not reordered with other stores.  An empty entry is (0, 0).
; Two entries form a bucket (same 32-byte line): index = key & ttmaskv, entries i and i+1.
; The table (ttbase, ttmaskv) is allocated by tt_init / tt_resize (option Hash).
; ============================================================
; ---- Test-only switches (tests/t_ttrace.py, tests/t_ttfuzz.py; none of them is part of the release, with all of them off the code is the plain lock-free table):
; ----   -DTTRACE  TTWINDOW: between the two stores of tt_store a pause loop and sched_yield, so that the other thread runs exactly there
; ----             (makes the race window big enough to be hit even on a one-core machine)
; ----   -DTTPLAIN the lock-free scheme is replaced by a plain one (key stored plainly, four separate data stores): the comparison that must FAIL
; ----   -DTTFUZZ  tt_probe returns a random 16-bit move instead of the stored one: a corrupt table move must never be played
%macro TTWINDOW 0
%ifdef TTRACE
    push rax
    push rcx
    push r11
    mov ecx, 200
%%s:pause
    dec ecx
    jnz %%s
    mov eax, 24                 ; sched_yield (the Windows shim answers ENOSYS: only the pause loop acts there)
    syscall
    pop r11
    pop rcx
    pop rax
%endif
%endmacro
%ifdef TTFUZZ
section .data
ttfz:       dd 2463534242       ; xorshift32 state (every unit has its own; think resets it, so that searches are reproducible)
section .text
%endif

; tt_probe(rax = key) -> eax = 1 on hit with rdx = a COPY of the entry's data qword (taken once; the callers decode it, they never look at the
; table again), eax = 0 on miss (then rdx is undefined).  Data qword layout = the entry bytes 8..15: move bits 0-15, eval code 16-31, score (i16) 32-47, depth (u8) 48-55,
; flag (u8) 56-63 = bound (bits 0-1) | age << 2.  clobbers ecx, rdx, r8
tt_probe:
%ifdef NOTT                      ; test builds only (tests/t_units_conc.py): table off, every probe misses, so concurrent searches are deterministic
    xor eax, eax
    ret
%endif
    mov ecx, eax
    and ecx, [ttmaskv]
    shl rcx, 4
    add rcx, [ttbase]           ; rcx = first entry of the bucket
%ifdef TTPLAIN
    cmp [rcx], rax              ; (test build) plain scheme: plain key, the data is read after the key matched
    jne .p1
    mov rdx, [rcx+8]
    jmp .hit
.p1:cmp [rcx+16], rax
    jne .miss
    mov rdx, [rcx+24]
    jmp .hit
%else
    mov rdx, [rcx+8]            ; data qword, loaded ONCE: the check and the copy returned to the caller come from this register
    mov r8, [rcx]
    xor r8, rdx
    cmp r8, rax
    je .hit
    mov rdx, [rcx+24]           ; second entry
    mov r8, [rcx+16]
    xor r8, rdx
    cmp r8, rax
    je .hit
%endif
%ifdef TTPLAIN
.miss:
%endif
    xor eax, eax
    ret
.hit:
%ifdef TTFUZZ
    mov r8d, [ttfz]             ; (test build) replace the move by garbage (16 bits, the range of real moves), keep score / depth / flag
    mov ecx, r8d
    shl ecx, 13
    xor r8d, ecx
    mov ecx, r8d
    shr ecx, 17
    xor r8d, ecx
    mov ecx, r8d
    shl ecx, 5
    xor r8d, ecx
    mov [ttfz], r8d
    shr rdx, 32
    shl rdx, 32
    movzx r8d, r8w
    or rdx, r8
%endif
    mov eax, 1
    ret

; tt_store(rax = key, edi = move | eval code << 16, edx = score (mate-adjusted), esi = depth, ecx = bound 1..3)
; same position: keep the old move if the move is 0 and the old eval code if the new one is 0; keep a clearly deeper entry of the current search.
; new position: replace the less valuable entry of the bucket (older search / smaller depth).
tt_store:
%ifdef NOTT
    ret
%endif
    push rbx
    push rbp
    mov r8d, eax
    and r8d, [ttmaskv]
    shl r8, 4
    add r8, [ttbase]
    movzx r10d, byte [ttage]
%ifdef TTPLAIN
    cmp [r8], rax               ; (test build) plain scheme: plain key compare
    je .pl0
    lea r9, [r8+16]
    cmp [r9], rax
    jne .victim
    mov r8, r9
.pl0:mov r11, [r8+8]
%else
    mov r11, [r8+8]             ; data qword of the first entry (one load); the key field must be  key XOR data
    mov rbx, [r8]
    xor rbx, r11
    cmp rbx, rax
    je .match
    lea r9, [r8+16]
    mov r11, [r9+8]             ; second entry
    mov rbx, [r9]
    xor rbx, r11
    cmp rbx, rax
    jne .victim
    mov r8, r9
%endif
.match:                         ; r8 = the entry of this position, r11 = its data qword as loaded above
    test di, di
    jnz .m1
    movzx ebx, r11w             ; keep the old move
    or edi, ebx
.m1:test edi, 0xFFFF0000
    jnz .m2
    mov ebx, r11d               ; keep the old eval code
    and ebx, 0xFFFF0000
    or edi, ebx
.m2:mov rbx, r11
    shr rbx, 56                 ; flag byte
    shr ebx, 2                  ; age
    cmp ebx, r10d
    jne .write                  ; entry of an older search: replace
    mov rbx, r11
    shr rbx, 48
    movzx ebx, bl               ; depth
    lea ebp, [rsi+2]
    cmp ebx, ebp
    jle .write
    jmp .out                    ; keep the deeper entry
.victim:
    movzx ebx, byte [r8+14]
    movzx ebp, byte [r8+15]
    shr ebp, 2
    cmp ebp, r10d
    je .v0
    sub ebx, 128
.v0:movzx r11d, byte [r9+14]
    movzx ebp, byte [r9+15]
    shr ebp, 2
    cmp ebp, r10d
    je .v1
    sub r11d, 128
.v1:cmp r11d, ebx
    jge .write
    mov r8, r9
.write:
    shl r10d, 2
    or ecx, r10d                ; flag byte = bound | age << 2
%ifdef TTPLAIN
    mov [r8], rax               ; (test build) plain scheme: the key, then four separate stores of the data
    TTWINDOW
    mov [r8+8], edi
    mov [r8+12], dx
    mov [r8+14], sil
    mov [r8+15], cl
%else
    mov r9d, edi                ; build the data qword: move | score << 32 | depth << 48 | flag << 56
    movzx edx, dx
    shl rdx, 32
    or r9, rdx
    movzx esi, sil
    shl rsi, 48
    or r9, rsi
    shl rcx, 56
    or r9, rcx
    mov [r8+8], r9              ; 1. the data, one aligned 8-byte store ...
    TTWINDOW
    xor r9, rax
    mov [r8], r9                ; 2. ... then key XOR data (a reader that sees only one of the two stores finds no match)
%endif
.out:
    pop rbp
    pop rbx
    ret

%if CONTHIST
; ch_row(edi = move) -> rax = the row of chtab for (piece on from(move), to(move)) in the current position.  clobbers ecx, edx
ch_row:
    mov ecx, edi
    and ecx, 127
    movzx ecx, byte [board+rcx]
    movzx eax, word [ch_pofs+rcx*2]
    mov edx, edi
    shr edx, 7
    and edx, 127
    mov ecx, edx
    and ecx, 7
    add edx, ecx
    shr edx, 1                      ; 0x88 -> 0..63: (to + file) / 2
    add eax, edx
    imul eax, eax, 1536             ; 768 entries of 2 bytes
%if HELPER && CH_SHARED
    add rax, [chbase]               ; CH_SHARED: the helper works in the main thread's table
%else
    add rax, chtab
%endif
    ret

; ch_update(edi = move, esi = bonus): the history update (gravity, +-16000) of (piece on from(move), to(move)) in the rows of ply - 1 and
; ply - 2 (chp[ply + 1], chp[ply]); chdummy is left alone.  Keeps esi, edi.  clobbers eax, ecx, edx, r8, r9, r10
ch_update:
    mov ecx, edi
    and ecx, 127
    movzx ecx, byte [board+rcx]
    movzx r9d, word [ch_pofs+rcx*2]
    mov edx, edi
    shr edx, 7
    and edx, 127
    mov ecx, edx
    and ecx, 7
    add edx, ecx
    shr edx, 1
    add r9d, edx                    ; r9 = piece * 64 + sq64(to)
    mov eax, [ply]
    mov r10, [chp+rax*8+8]          ; row of the previous move
    call .one
    mov eax, [ply]
    mov r10, [chp+rax*8]            ; row of the move before it; falls into .one, whose ret returns to the caller
.one:
    cmp r10, chdummy
    je .r
    movsx edx, word [r10+r9*2]
    mov r8d, esi
    test r8d, r8d
    jns .p
    neg r8d
.p: imul r8d, edx
    sar r8d, 14
    mov eax, esi
    sub eax, r8d
    add edx, eax
    cmp edx, 16000
    jle .c1
    mov edx, 16000
.c1:cmp edx, -16000
    jge .c2
    mov edx, -16000
.c2:mov [r10+r9*2], dx
.r: ret
%endif
; ============================================================
; move ordering helpers
; ============================================================
; score_moves(rdi = moves, rsi = scores, edx = n, ecx = ttmove)
%if CAPHIST
; caph_add: esi += captab of the move [r8 + rbx * 4] (score_moves).  Clobbers eax ecx edx edi.
caph_add:
    mov edi, [r8+rbx*4]
    call cap_index
    test eax, eax
    js .n
    movsx eax, word [captab+rax*2]
    add esi, eax
.n: ret
%endif
score_moves:
    push rbx
    push rbp
    push r12
    push r13
%if CONTHIST
    push r14
    push r15
%endif
    mov r8, rdi
    mov r9, rsi
    mov r10d, edx
    mov r11d, ecx
    mov eax, [ply]
    mov r12d, [killers+rax*8]
    mov r13d, [killers+rax*8+4]
%if CONTHIST
    mov r14, [chp+rax*8+8]          ; row of the previous move (ply - 1)
    mov r15, [chp+rax*8]            ; row of the move before it (ply - 2)
%endif
    xor ebx, ebx
.l: cmp ebx, r10d
    jge .done
    mov edi, [r8+rbx*4]
    cmp edi, r11d
    jne .nott
    mov dword [r9+rbx*4], 0x40000000
    jmp .next
.nott:
    mov ecx, edi
    and ecx, 127                ; from
    mov edx, edi
    shr edx, 7
    and edx, 127                ; to
    movzx eax, byte [board+rcx]
    movzx esi, byte [board+rdx] ; victim code
    mov ebp, eax
    and ebp, 7                  ; mover type
    test esi, esi
    jnz .cap
    cmp ebp, 1
    jne .quiet
    mov eax, edx
    and eax, 0xF0
    cmp eax, 0x70
    je .promo
    test eax, eax
    jz .promo
    mov eax, ecx
    and eax, 7
    mov esi, edx
    and esi, 7
    cmp eax, esi
    je .quiet
    mov esi, 1                  ; en passant: victim = pawn
    jmp .cap
.promo:
    mov eax, edi
    shr eax, 14
    cmp eax, 3
    jne .under
    mov dword [r9+rbx*4], 0x1100000
    jmp .next
.under:
%if CONTHIST
    mov dword [r9+rbx*4], -70000
%else
    mov dword [r9+rbx*4], -50000
%endif
    jmp .next
.cap:
    cmp ebp, 1
    jne .cap2
    mov eax, edx
    and eax, 0xF0
    cmp eax, 0x70
    je .pc
    test eax, eax
    jnz .cap2
.pc:mov eax, edi
    shr eax, 14
    cmp eax, 3
    jne .under
.cap2:
    movzx eax, byte [board+rcx]
    mov eax, [pvalue+rax*4]     ; attacker value
    mov edx, [pvalue+rsi*4]     ; victim value
    lea esi, [rdx*8]
    add esi, esi                ; victim*16
    sub esi, eax                ; MVV-LVA
%if SEE_ORD && CAPHIST >= 2
    push r8                     ; CAPHIST 2: good / bad by SEE >= CAP_SEEB - (captab >> CAP_SEESH)
    push r9
    push r10
    push r11
    push rsi
    mov r11d, eax               ; attacker value (r8 .. r11 are saved above)
    mov r10d, edx               ; victim value
    mov edi, [r8+rbx*4]
    call cap_index
    xor esi, esi
    test eax, eax
    js .ch0
    movsx esi, word [captab+rax*2]
.ch0:
%if CAP_SEEPOS == 0
    mov eax, esi                ; only bad history moves the threshold
    sar eax, 31
    and esi, eax
%endif
    cmp r11d, r10d
    jg .chs
    test esi, esi
    jns .chg                    ; attacker worth no more than the victim and no bad history: good without SEE
.chs:
%ifdef TUNE
    mov ecx, [tunetab+79*4]     ; T79 CAP_SEESH, T78 CAP_SEEB
    sar esi, cl
    neg esi
    add esi, [tunetab+78*4]
%else
    sar esi, CAP_SEESH
    neg esi
    add esi, CAP_SEEB           ; good history lowers the threshold, bad history raises it
%endif
    mov edi, [r8+rbx*4]
    call see_ge
    pop rsi
    pop r11
    pop r10
    pop r9
    pop r8
    test eax, eax
    jz .bad
    jmp .good
.chg:
    pop rsi
    pop r11
    pop r10
    pop r9
    pop r8
    jmp .good
%elif SEE_ORD
    cmp eax, edx
    jle .good                   ; attacker worth no more than the victim: the capture cannot lose material
    push r8
    push r9
    push r10
    push r11
    push rsi
    mov edi, [r8+rbx*4]
    xor esi, esi
    call see_ge                 ; static exchange >= 0 ?
    pop rsi
    pop r11
    pop r10
    pop r9
    pop r8
    test eax, eax
    jz .bad
%else
    add edx, 50
    cmp eax, edx
    jg .bad
%endif
.good:
%if CAPHIST
    call caph_add
%endif
    add esi, 0x1000000
    mov [r9+rbx*4], esi
    jmp .next
.bad:
%if CAPHIST
    call caph_add
%endif
%if SEE_BADLAST
    sub esi, 0x40000            ; behind every quiet move (history stays within +-16000)
%else
    add esi, 0x200000
%endif
    mov [r9+rbx*4], esi
    jmp .next
.quiet:
    cmp edi, r12d
    jne .k2
    mov dword [r9+rbx*4], 0x800000
    jmp .next
.k2:cmp edi, r13d
    jne .hist
    mov dword [r9+rbx*4], 0x7FFFFF
    jmp .next
.hist:
%if CONTHIST
    movzx esi, byte [board+rcx]     ; continuation: row(ply - 1)[piece][to] + row(ply - 2)[piece][to]
    movzx esi, word [ch_pofs+rsi*2]
    mov edi, edx
    and edi, 7
    add edi, edx
    shr edi, 1
    add esi, edi
    movsx edi, word [r14+rsi*2]
    movsx esi, word [r15+rsi*2]
    add esi, edi
%endif
    mov eax, [stm]
    shl eax, 11
    shl ecx, 7
    add eax, ecx
    add eax, edx
    movsx eax, word [histtab+rax*2]
%if CONTHIST
    add eax, eax                    ; 2 * history + the two continuation values: +-64000, above under-promotions (-70000) and bad captures,
    add eax, esi                    ; below the killers
%endif
    mov [r9+rbx*4], eax
.next:
    inc ebx
    jmp .l
.done:
    lea rax, [r9+r10*4]
    mov dword [rax], 0x80000000   ; pick() reads up to 3 entries past n
    mov dword [rax+4], 0x80000000
    mov dword [rax+8], 0x80000000
    mov dword [rax+12], 0x80000000
%if CONTHIST
    pop r15
    pop r14
%endif
    pop r13
    pop r12
    pop rbp
    pop rbx
    ret

%if SIMDPICK >= 3
; pick(rdi = moves, rsi = scores, edx = idx, ecx = n) -> eax = best remaining move (swapped into idx)
; PICK1P name, sse41: SSE2 (0) or SSE4.1 pmaxsd / pminsd (1).  ONE pass over scores[idx..n) that carries, per lane, the running maximum and the index
; where it was first reached (strict '>' keeps the first one); then the maximum over the four lanes and the LOWEST index among the lanes
; that hold it = the first index holding the global maximum, the result of the scalar loop (no second scan loop).
; Needs scores[n .. n+2] = INT_MIN (written by score_moves).  Clobbers r8-r11, xmm0-xmm5 only (volatile in the Win64 ABI too:
; a Windows helper thread returns to the system, which expects xmm6-xmm15 unchanged).
%macro PICK1P 2
%1:
    lea r9, [rsi+rdx*4]
    lea r10, [rsi+rcx*4]
    movdqu xmm0, [r9]               ; running maximum per lane
    movd xmm3, edx
    pshufd xmm3, xmm3, 0
    paddd xmm3, [.c0123]            ; index of the maximum per lane
    movdqa xmm4, xmm3               ; index of the vector being examined
    lea r11, [r9+16]
.m: cmp r11, r10
    jae .h
    paddd xmm4, [.c4]
    movdqu xmm1, [r11]
    movdqa xmm2, xmm1
    pcmpgtd xmm2, xmm0              ; lanes where the new score is larger
%if %2
    pmaxsd xmm0, xmm1
%else
    pxor xmm1, xmm0                 ; maximum: xmm0 ^= (xmm0 ^ new) & mask
    pand xmm1, xmm2
    pxor xmm0, xmm1
%endif
    movdqa xmm5, xmm4               ; index: xmm3 ^= (xmm3 ^ xmm4) & mask
    pxor xmm5, xmm3
    pand xmm5, xmm2
    pxor xmm3, xmm5
    add r11, 16
    jmp .m
.h:
%if %2
    pshufd xmm1, xmm0, 0x4E
    pmaxsd xmm1, xmm0
    pshufd xmm2, xmm1, 0xB1
    pmaxsd xmm2, xmm1               ; all four lanes = maximum
    pcmpeqd xmm2, xmm0              ; lanes that hold it
    pandn xmm2, [.cbig]             ; the other lanes: index + 0x40000000
    paddd xmm3, xmm2
    pshufd xmm1, xmm3, 0x4E
    pminsd xmm1, xmm3
    pshufd xmm2, xmm1, 0xB1
    pminsd xmm2, xmm1               ; lowest candidate index
%else
    pshufd xmm1, xmm0, 0x4E
    movdqa xmm2, xmm1
    pcmpgtd xmm2, xmm0
    pxor xmm1, xmm0
    pand xmm1, xmm2
    pxor xmm1, xmm0                 ; max of the lane pairs
    pshufd xmm2, xmm1, 0xB1
    movdqa xmm4, xmm2
    pcmpgtd xmm4, xmm1
    pxor xmm2, xmm1
    pand xmm2, xmm4
    pxor xmm2, xmm1                 ; all four lanes = maximum
    pcmpeqd xmm2, xmm0              ; lanes that hold it
    pandn xmm2, [.cbig]             ; the other lanes: index + 0x40000000
    paddd xmm3, xmm2
    pshufd xmm1, xmm3, 0x4E
    movdqa xmm2, xmm3
    pcmpgtd xmm2, xmm1              ; lanes where the other half is smaller
    pxor xmm1, xmm3
    pand xmm1, xmm2
    pxor xmm1, xmm3                 ; min of the lane pairs
    pshufd xmm2, xmm1, 0xB1
    movdqa xmm4, xmm1
    pcmpgtd xmm4, xmm2
    pxor xmm2, xmm1
    pand xmm2, xmm4
    pxor xmm2, xmm1                 ; lowest candidate index
%endif
    movd r8d, xmm2
%ifdef PICKVERIFY
    push rbx                    ; test build: the scalar loop must find the same index
    push rax
    mov eax, [r9]
    mov rbx, r9
    lea r11, [r9+4]
.vl:cmp r11, r10
    jae .vd
    cmp [r11], eax
    jle .vn
    mov eax, [r11]
    mov rbx, r11
.vn:add r11, 4
    jmp .vl
.vd:sub rbx, rsi
    shr ebx, 2
    cmp ebx, r8d
    jne .pickfail
    pop rax
    pop rbx
%endif
    mov eax, [rdi+r8*4]
    mov r10d, [rdi+rdx*4]
    mov [rdi+rdx*4], eax
    mov [rdi+r8*4], r10d
    mov r11d, [rsi+rdx*4]
    mov r10d, [rsi+r8*4]
    mov [rsi+rdx*4], r10d
    mov [rsi+r8*4], r11d
    ret
align 16
.c0123: dd 0, 1, 2, 3
.c4:    dd 4, 4, 4, 4
.cbig:  dd 0x40000000, 0x40000000, 0x40000000, 0x40000000
%ifdef PICKVERIFY
.pickfail:
%ifdef WIN
%error "PICKVERIFY is a Linux test build"
%endif
    mov eax, 231                ; exit_group
    mov edi, 6
    syscall
%endif
%endmacro
%if SIMDPICK == 5
; SIMDPICK 5: both copies; cpu_sse41 (CPUID leaf 1, ECX bit 19, set once at start-up) selects one.  The branch has the same outcome in
; every call, so it costs next to nothing.
pick:
    cmp byte [cpu_sse41], 0
    jne pick_s41
PICK1P pick_s2, 0
PICK1P pick_s41, 1
%else
PICK1P pick, SIMDPICK == 4
%endif

%elif SIMDPICK
; pick(rdi = moves, rsi = scores, edx = idx, ecx = n) -> eax = best remaining move (swapped into idx)
; SSE2: max over scores[idx..n) four at a time, then the FIRST index holding that max (same result as the scalar loop with strict '>').
; Needs scores[n .. n+3] = INT_MIN (written by score_moves).  Clobbers r8-r11, xmm0-xmm2.
pick:
    lea r9, [rsi+rdx*4]
    lea r10, [rsi+rcx*4]
    movdqu xmm0, [r9]
    lea r11, [r9+16]
%if SIMDPICK >= 2
.m: cmp r11, r10                ; SSE4.1: pmaxsd = the whole compare / blend sequence in one instruction
    jae .h
    movdqu xmm1, [r11]
    pmaxsd xmm0, xmm1
    add r11, 16
    jmp .m
.h: pshufd xmm1, xmm0, 0x4E
    pmaxsd xmm0, xmm1
    pshufd xmm1, xmm0, 0xB1
    pmaxsd xmm0, xmm1           ; all four lanes = max
%else
.m: cmp r11, r10
    jae .h
    movdqu xmm1, [r11]
    movdqa xmm2, xmm1
    pcmpgtd xmm2, xmm0
    pand xmm1, xmm2
    pandn xmm2, xmm0
    por xmm1, xmm2
    movdqa xmm0, xmm1
    add r11, 16
    jmp .m
.h: pshufd xmm1, xmm0, 0x4E
    movdqa xmm2, xmm1
    pcmpgtd xmm2, xmm0
    pand xmm1, xmm2
    pandn xmm2, xmm0
    por xmm1, xmm2
    pshufd xmm0, xmm1, 0xB1
    movdqa xmm2, xmm0
    pcmpgtd xmm2, xmm1
    pand xmm0, xmm2
    pandn xmm2, xmm1
    por xmm0, xmm2              ; all four lanes = max
%endif
    mov r11, r9
.f: movdqu xmm1, [r11]
    pcmpeqd xmm1, xmm0
    pmovmskb eax, xmm1
    test eax, eax
    jnz .got
    add r11, 16
    jmp .f
.got:
    bsf eax, eax
    shr eax, 2
    sub r11, rsi
    shr r11d, 2
    lea r8d, [rax+r11]
%ifdef PICKVERIFY
    push rbx                    ; test build: the scalar loop must find the same index
    push rax
    mov eax, [r9]
    mov rbx, r9
    lea r11, [r9+4]
.vl:cmp r11, r10
    jae .vd
    cmp [r11], eax
    jle .vn
    mov eax, [r11]
    mov rbx, r11
.vn:add r11, 4
    jmp .vl
.vd:sub rbx, rsi
    shr ebx, 2
    cmp ebx, r8d
    jne .pickfail
    pop rax
    pop rbx
%endif
    mov eax, [rdi+r8*4]
    mov r10d, [rdi+rdx*4]
    mov [rdi+rdx*4], eax
    mov [rdi+r8*4], r10d
    mov r11d, [rsi+rdx*4]
    mov r10d, [rsi+r8*4]
    mov [rsi+rdx*4], r10d
    mov [rsi+r8*4], r11d
    ret
%ifdef PICKVERIFY
.pickfail:
%ifdef WIN
%error "PICKVERIFY is a Linux test build"
%endif
    mov eax, 231                ; exit_group
    mov edi, 6
    syscall
%endif

%else
; pick(rdi = moves, rsi = scores, edx = idx, ecx = n) -> eax = best remaining move (swapped into idx)   [SIMDPICK = 0: the scalar loop]
pick:
    mov r8d, edx
    mov eax, [rsi+rdx*4]
    lea r9d, [rdx+1]
.l: cmp r9d, ecx
    jge .sw
    cmp [rsi+r9*4], eax
    jle .n
    mov eax, [rsi+r9*4]
    mov r8d, r9d
.n: inc r9d
    jmp .l
.sw:
    mov eax, [rdi+r8*4]
    mov r10d, [rdi+rdx*4]
    mov [rdi+rdx*4], eax
    mov [rdi+r8*4], r10d
    mov r11d, [rsi+rdx*4]
    mov r10d, [rsi+r8*4]
    mov [rsi+rdx*4], r10d
    mov [rsi+r8*4], r11d
    ret

%endif
; hist_update(edi = move, esi = bonus)  side = stm (side that made the move)
hist_update:
    mov eax, edi
    and eax, 127
    shl eax, 7
    mov ecx, edi
    shr ecx, 7
    and ecx, 127
    add eax, ecx
    mov edx, [stm]
    shl edx, 11
    add eax, edx
    movsx edx, word [histtab+rax*2]
    mov r8d, esi
    test r8d, r8d
    jns .p
    neg r8d
.p: imul r8d, edx
    sar r8d, 14
    sub esi, r8d
    add edx, esi
    cmp edx, 16000
    jle .c1
    mov edx, 16000
.c1:cmp edx, -16000
    jge .c2
    mov edx, -16000
.c2:mov [histtab+rax*2], dx
    ret
%if CAPHIST
; cap_index(edi = move) -> eax = index into captab, -1 when the move is no capture (quiet, push promotion, under-promotion).  The board is the
; position before the move.  Clobbers eax ecx edx edi only.
cap_index:
    mov ecx, edi
    shr ecx, 7
    and ecx, 127                    ; to
    movzx eax, byte [board+rcx]
    and eax, 7                      ; victim type
    mov edx, edi
    and edx, 127                    ; from
    movzx edx, byte [board+rdx]     ; mover code
    test eax, eax
    jnz .vic
    mov eax, edx                    ; empty target: en passant only (a pawn changing its file)
    and eax, 7
    cmp eax, 1
    jne .no
    mov eax, edi
    xor eax, ecx
    and eax, 7
    jz .no
    mov eax, 1
.vic:
    push rdx
    and edx, 7
    cmp edx, 1                      ; a pawn reaching the last rank promotes: only the queen promotion has an entry
    jne .vq
    mov edx, ecx
    and edx, 0x70
    jz .vp
    cmp edx, 0x70
    jne .vq
.vp:mov edx, edi
    shr edx, 14
    cmp edx, 3
    je .vq
    pop rdx
.no:mov eax, -1
    ret
.vq:pop rdx
    shl edx, 10
    shl ecx, 3
    add eax, ecx
    add eax, edx
    ret
; cap_update(edi = move, esi = bonus): the gravity update of hist_update with its own shift and bounds.  Keeps esi; clobbers eax ecx edx edi r8.
cap_update:
    call cap_index
    test eax, eax
    js .r
    movsx edx, word [captab+rax*2]
    mov r8d, esi
    test r8d, r8d
    jns .p
    neg r8d
.p: imul r8d, edx
    sar r8d, CAP_SH
    mov ecx, esi
    sub ecx, r8d
    add edx, ecx
    cmp edx, CAP_LIM
    jle .c1
    mov edx, CAP_LIM
.c1:cmp edx, -CAP_LIM
    jge .c2
    mov edx, -CAP_LIM
.c2:mov [captab+rax*2], dx
.r: ret
%endif

; ============================================================
; quiescence search: qsearch(edi = alpha, esi = beta) -> eax
; ============================================================
%define Q_ALPHA  0
%define Q_BETA   4
%define Q_BEST   8
%define Q_INCHK  12
%define Q_N      16
%define Q_IDX    20
%define Q_MV     24
%define Q_STAND  28
%define Q_SC     32
%define Q_OALPHA 36
%define Q_TTMV   40
%define Q_BMV    44
%define Q_TTEV   48             ; eval code of this position from the table (EVCACHE): eval + 32768, 0 = unknown
; EVGET slot: eax = static eval of the node; with EVCACHE taken from the code in [rbp+slot] if known, else computed (and its code kept in the slot)
%macro EVGET 1
%if EVCACHE
    mov eax, [rbp+%1]
    test eax, eax
    jz %%c
    sub eax, 32768
    jmp %%h
%%c:call eval
    lea ecx, [rax+32768]
    mov [rbp+%1], ecx
%%h:
%else
    call eval
%endif
%endmacro
; EVADJ: eax = static eval -> the eval the search uses (EV_CORR: + pawn correction history, EV_R50: fifty-move fade).  clobbers rcx, rdx, rsi
%macro EVADJ 0
%if EV_CORR
    push rdi
    call corr_ptr
    movsx ecx, word [rdi]
    sar ecx, 3
    add eax, ecx
    pop rdi
%endif
%if EV_R50
    mov esi, eax
    imul eax, [halfm]
    cdq
    LOADT ecx, 30, EV_R50C
    idiv ecx
    sub esi, eax
    mov eax, esi
%endif
%endmacro
%if EV_CORR
; corr_ptr -> rdi = address of the correction entry of the side to move and the pawn structure.  clobbers rcx
corr_ptr:
    mov rdi, [bbp+72]
    rol rdi, 32
    xor rdi, [bbp+8]
    mov rcx, 0x9E3779B97F4A7C15
    imul rdi, rcx
    shr rdi, 64-CORR_BITS
    mov ecx, [stm]
    shl ecx, CORR_BITS-3            ; 0 / 8 -> 0 / 2^CORR_BITS
    add edi, ecx
    lea rdi, [corrtab+rdi*2]
    ret
%endif
%define Q_MOVES  64
%define Q_SCORES 1088
%define QFRAME   2144

qsearch:
    push rbp
    push rbx
    push r12
    push r13
    push r14
    push r15
    sub rsp, QFRAME
    mov rbp, rsp
    mov [rbp+Q_ALPHA], edi
    mov [rbp+Q_BETA], esi
    mov [rbp+Q_OALPHA], edi
%if INCHKHINT
    mov [rbp+Q_INCHK], edx          ; in-check hint (-1 = not known)
%else
    mov dword [rbp+Q_INCHK], -1
%endif
    mov dword [rbp+Q_TTMV], 0
    mov dword [rbp+Q_TTEV], 0
    mov dword [rbp+Q_BMV], 0
    inc qword [nodes]
    mov eax, [ply]                  ; seldepth: deepest ply reached
    cmp eax, [seldep]
    jle .qsd
    mov [seldep], eax
.qsd:
    mov rax, [nodes]
    test eax, 2047
    jnz .nt
    call check_time
.nt:STOPTEST
    jne .ret0
    cmp dword [ply], MAXPLY-2
    jge .retstatic
    ; ---- transposition table probe (cutoffs only in null-window nodes)
    mov rax, [hashk]
    call tt_probe
    test eax, eax
    jz .nottq
    movzx ecx, dx                   ; rdx = copy of the data qword: move = bits 0-15
    mov [rbp+Q_TTMV], ecx
%if EVCACHE
    mov eax, edx                    ; eval code = bits 16-31
    shr eax, 16
    mov [rbp+Q_TTEV], eax
%endif
    mov eax, [rbp+Q_BETA]
    sub eax, [rbp+Q_ALPHA]
    cmp eax, 1
    jg .nottq
    mov rax, rdx                    ; score = bits 32-47 (i16)
    shr rax, 32
    movsx eax, ax
    cmp eax, MATEB
    jle .qm1
    sub eax, [ply]
    jmp .qm2
.qm1:cmp eax, -MATEB
    jge .qm2
    add eax, [ply]
.qm2:mov rcx, rdx                    ; flag = bits 56-63, bound = its low 2 bits
    shr rcx, 56
    and ecx, 3
    cmp ecx, 3
    je .retv
    cmp ecx, 1
    jne .qup
    cmp eax, [rbp+Q_BETA]
    jge .retv
    jmp .nottq
.qup:
    cmp eax, [rbp+Q_ALPHA]
    jle .retv
.nottq:
    mov eax, [rbp+Q_INCHK]
    test eax, eax
    jns .qichave
    call in_check
    mov [rbp+Q_INCHK], eax
    jmp .qicdone
.qichave:
%ifdef INCHKVERIFY
    call in_check
    cmp eax, [rbp+Q_INCHK]
    jne .qicfail
%endif
    mov eax, [rbp+Q_INCHK]
.qicdone:
    test eax, eax
    jnz .evasion
    EVGET Q_TTEV
    EVADJ
    mov [rbp+Q_STAND], eax
    mov [rbp+Q_BEST], eax
    cmp eax, [rbp+Q_BETA]
    jge .done
    cmp eax, [rbp+Q_ALPHA]
    jle .g
    mov [rbp+Q_ALPHA], eax
.g: lea rdi, [rbp+Q_MOVES]
    mov esi, 1
    call gen
    jmp .gd
.evasion:
    mov eax, -MATE
    add eax, [ply]
    mov [rbp+Q_BEST], eax
    lea rdi, [rbp+Q_MOVES]
    xor esi, esi
    call gen
.gd:lea rcx, [rbp+Q_MOVES]
    sub rax, rcx
    shr eax, 2
    mov [rbp+Q_N], eax
    lea rdi, [rbp+Q_MOVES]
    lea rsi, [rbp+Q_SCORES]
    mov edx, [rbp+Q_N]
    mov ecx, [rbp+Q_TTMV]
    call score_moves
    mov dword [rbp+Q_IDX], 0
.loop:
    mov edx, [rbp+Q_IDX]
    cmp edx, [rbp+Q_N]
    jge .done
    lea rdi, [rbp+Q_MOVES]
    lea rsi, [rbp+Q_SCORES]
    mov ecx, [rbp+Q_N]
    call pick
    mov [rbp+Q_MV], eax
    inc dword [rbp+Q_IDX]
    cmp dword [rbp+Q_INCHK], 0
    jne .make
    ; delta pruning
    mov eax, [rbp+Q_MV]
    mov ecx, eax
    shr ecx, 7
    and ecx, 127
    movzx edx, byte [board+rcx]
    mov edx, [pvalue+rdx*4]
    test edx, edx
    jnz .dp0
    mov edx, 100
.dp0:
    and eax, 127
    movzx eax, byte [board+rax]
    and eax, 7
    cmp eax, 1
    jne .dp2
    mov eax, ecx
    and eax, 0xF0
    cmp eax, 0x70
    je .dpr
    test eax, eax
    jnz .dp2
.dpr:
    add edx, 800
.dp2:
%ifdef TUNE
    add edx, [tunetab+58*4]         ; delta margin (T58)
%else
    add edx, DELTA_M
%endif
    add edx, [rbp+Q_STAND]
    cmp edx, [rbp+Q_ALPHA]
    jle .loop
%if SEE_QS
    mov edi, [rbp+Q_MV]         ; static exchange: a capture that loses material is not searched
    xor esi, esi
    call see_ge
    test eax, eax
    jz .loop
%endif
.make:
%if CONTHIST
    mov edi, [rbp+Q_MV]             ; chp[ply + 2] = row of this move (a check evasion below orders quiet moves with it)
    call ch_row
    mov ecx, [ply]
    mov [chp+rcx*8+16], rax
%endif
    mov edi, [rbp+Q_MV]
    call domove
    test eax, eax
    jz .ill
    inc dword [ply]
    mov edi, [rbp+Q_BETA]
    neg edi
    mov esi, [rbp+Q_ALPHA]
    neg esi
    mov edx, -1
    call qsearch
    dec dword [ply]
    neg eax
    mov [rbp+Q_SC], eax
    call undomove
    STOPTEST
    jne .ret0
    mov eax, [rbp+Q_SC]
    cmp eax, [rbp+Q_BEST]
    jle .loop
    mov [rbp+Q_BEST], eax
    cmp eax, [rbp+Q_ALPHA]
    jle .loop
    mov ecx, [rbp+Q_MV]
    mov [rbp+Q_BMV], ecx
    mov [rbp+Q_ALPHA], eax
    cmp eax, [rbp+Q_BETA]
%ifdef ORDSTAT
    jl .ordqnc
    ORDQ
    jmp .done
.ordqnc:
%else
    jge .done
%endif
    jmp .loop
.ill:
    call undomove
    jmp .loop
%ifdef INCHKVERIFY
.qicfail:
    mov eax, 231
    mov edi, 5
    syscall
%endif
.done:
    mov eax, [rbp+Q_BEST]
    mov ecx, 1
    cmp eax, [rbp+Q_BETA]
    jge .qfl
    mov ecx, 3
    cmp eax, [rbp+Q_OALPHA]
    jg .qfl
    mov ecx, 2
.qfl:mov edx, eax
    cmp edx, MATEB
    jle .qs1
    add edx, [ply]
    jmp .qs2
.qs1:cmp edx, -MATEB
    jge .qs2
    sub edx, [ply]
.qs2:mov rax, [hashk]
    mov edi, [rbp+Q_BMV]
%if EVCACHE
    mov esi, [rbp+Q_TTEV]
    shl esi, 16
    or edi, esi
%endif
    xor esi, esi
    call tt_store
    mov eax, [rbp+Q_BEST]
    jmp .retv
.retstatic:
    call eval
    EVADJ
    jmp .retv
.ret0:
    xor eax, eax
.retv:
    add rsp, QFRAME
    pop r15
    pop r14
    pop r13
    pop r12
    pop rbx
    pop rbp
    ret

; ============================================================
; main search: search(edi = depth, esi = alpha, edx = beta, ecx = allow-null) -> eax
; ============================================================
%define L_DEPTH  0
%define L_ALPHA  4
%define L_BETA   8
%define L_OALPHA 12
%define L_BEST   16
%define L_BESTMV 20
%define L_TTMV   24
%define L_MCNT   28
%define L_INCHK  32
%define L_SEVAL  36
%define L_PV     40
%define L_IDX    44
%define L_N      48
%define L_MV     52
%define L_NULLOK 56
%define L_NQ     60
%define L_ROOT   64
%define L_GIVES  68
%define L_NEWD   72
%define L_R      76
%define L_ISQ    80
%define L_SC     84
%define L_BONUS  88
%define L_TMP    92
%define L_IMPR   96
%define L_SEEB   100
%define L_PPR    104               ; PPVERIFY only: pp_ok's answer for the current move
%define L_PPL    108               ; PPVERIFY only: domove's answer
%define L_CUT    112               ; cutNode flag of this node (0 / 1; PV nodes and the root: 0)
%define L_TTB    116               ; bound of the TT hit of this node (0 = no hit, 1 lower, 2 upper, 3 exact), only when a switch needs it
%define L_TTSC   120               ; ... its score (mate-adjusted to this ply)
%define L_TTDP   124               ; ... its depth
%define L_TTCAP  128               ; the TT move is a capture (board of the node's own position)
%define L_ORDQ   132               ; ORDSTAT only: quiet moves picked at this node
%define L_NB     136               ; qword: [nodes] before the child search of a root move (TM_EFFORT)
%define L_TMP2   144               ; CONTHIST with CH_BL: the continuation bonus of a cutoff
%define L_EXCL   148               ; SING: the move this search excludes (0 = a normal search)
%define L_TTEV   152               ; eval code of this position from the table (EVCACHE), see Q_TTEV
%define L_SEXT   156               ; SING 2: added to the depth of the first move: 1 singular, -1 negative extension (NEG_EXT)
%define NOEVAL   0x40000000
%define L_MOVES  160
%define L_SCORES 1184
%define L_QUIETS 2208
%assign SF_TOP 2480                ; (%assign, not %define: each offset is fixed where it is defined)
%ifdef PCVERIFY
%assign L_PCW    SF_TOP            ; PCVERIFY only: ProbCut would have cut this node
%assign SF_TOP SF_TOP+16
%endif
%if CAPHIST
%assign L_NCAP   SF_TOP            ; CAPHIST: captures searched so far at this node (at most 16)
%assign L_CAPH   SF_TOP+4          ; CAPHIST: captab of the current move, read before it is made (CAP_LMR; no entry: 0x7FFFFFFF)
%assign L_CAPS   SF_TOP+16         ; CAPHIST: ... the moves
%assign SF_TOP SF_TOP+80
%endif
%assign SFRAME   SF_TOP
; scratch in the thread-local array sevals (indices MAXPLY .. MAXPLY+15, never used as a ply index)
%define S_BASE   sevals+MAXPLY*4
%define S_NMPMIN S_BASE+0           ; dd  NMP verification: no null move below this ply while a verification search runs (0 = off)
%define S_STAB   S_BASE+4           ; dd  iterations the best root move has been the same
%define S_ROOTEFF S_BASE+8          ; dq  nodes of the child search of the best root move of this iteration
%define S_ITSTART S_BASE+16         ; dq  [nodes] at the start of this iteration
%define S_TOTBMC S_BASE+24          ; dd  decaying best-move-change count (x16)
%define S_PREVIT S_BASE+28          ; dd  score of the previous iteration
%define S_ITBEST0 S_BASE+32         ; dd  best root move at the start of this iteration
%define S_PS     S_BASE+36          ; dd  score of the previous SEARCH of the game (copy of tm_prev at the start of think)
%define S_PSOK   S_BASE+40          ; dd
%define S_TMF    S_BASE+44          ; dd  time factor F of the iteration just completed (survives gettime_ms)
%define S_SAC    S_BASE+52          ; dd  TM_INCD: search-again counter (iterations started while the next one was not to go deeper)
%define S_INCD   S_BASE+56          ; dd  TM_INCD: 1 = the next iteration searches one ply deeper (set after every iteration with a real clock)
%define S_SD     S_BASE+60          ; dd  TM_INCD: the depth the current iteration searches with
%define S_NMPVP  S_BASE+48          ; dd  NMP verification: ply + 1 of the node that runs the verification search (0 = none); EVALDIFF_HIST must not run for that re-entry of the same node
%assign NEEDTTD (EVAL_TT | NMP_TTG | TTCUT_COND | (SING > 0))

; LMPLIM: ecx = depth -> eax = the late move count of the late quiet move pruning (LMP_SF; LMP_B = T76).  clobbers edx.
; Used by the pruning after the make and by the pre-make test (PREPRUNE), which must decide the same.
%macro LMPLIM 0
    mov eax, ecx
    imul eax, ecx
%if LMP_SF
    add eax, 3                      ; (3 + d^2), halved when the eval is not improving
    cmp dword [rbp+L_IMPR], 0
    jne %%d
    shr eax, 1
%else
    TADD eax, 76, LMP_B             ; (d^2 + LMP_B) +- a quarter
    mov edx, eax
    shr edx, 2
    cmp dword [rbp+L_IMPR], 0
    je %%n
    add eax, edx
    jmp %%d
%%n:sub eax, edx
%endif
%%d:
%endmacro
; FUTM: edx = L_SEVAL + 110 + 90 * ecx   (the futility threshold for 'ecx' plies; -DTUNE: T4 + T5 * ecx; ecx unchanged)
%macro FUTM 0
%ifdef TUNE
    mov edx, [tunetab+5*4]
    imul edx, ecx
    add edx, [tunetab+4*4]
%else
    imul edx, ecx, 90
    add edx, 110
%endif
    add edx, [rbp+L_SEVAL]
%endmacro
; LMRD reg: ecx = depth in  ->  ecx = lmrDepth = max(0, depth - 1 - lmrtab[depth][reg]);  reg = move number (32-bit register).  clobbers eax, r8d
%macro LMRD 1
    mov eax, ecx
    cmp eax, 63
    jle %%d
    mov eax, 63
%%d:mov r8d, %1
    cmp r8d, 63
    jle %%m
    mov r8d, 63
%%m:shl eax, 6
    add eax, r8d
    movzx eax, byte [lmrtab+rax]
    sub ecx, eax
    dec ecx
    jns %%o
    xor ecx, ecx
%%o:
%endmacro

search:
    push rbp
    push rbx
    push r12
    push r13
    push r14
    push r15
    sub rsp, SFRAME
    mov rbp, rsp
    mov [rbp+L_DEPTH], edi
    mov [rbp+L_ALPHA], esi
    mov [rbp+L_BETA], edx
    mov [rbp+L_NULLOK], ecx
    mov [rbp+L_CUT], r9d            ; cutNode
%ifdef PCVERIFY
    mov dword [rbp+L_PCW], 0
%endif
%if SING
    mov eax, [sexcl]                ; an exclusion search (SING): the excluded move; the children of this call see 0
    mov [rbp+L_EXCL], eax
    mov dword [sexcl], 0
    mov dword [rbp+L_SEXT], 0
%endif
%if INCHKHINT
    mov [rbp+L_INCHK], r8d          ; in-check hint of the caller (-1 = not known)
%else
    mov dword [rbp+L_INCHK], -1
%endif
    mov dword [rbp+L_BESTMV], 0
    mov dword [rbp+L_MCNT], 0
    mov dword [rbp+L_NQ], 0
%if CAPHIST
    mov dword [rbp+L_NCAP], 0
%endif
    mov dword [rbp+L_ROOT], 0
    cmp dword [ply], 0
    jne .nr
    mov dword [rbp+L_ROOT], 1
.nr:mov eax, edx
    sub eax, esi
    xor ecx, ecx
    cmp eax, 1
    setg cl
    mov [rbp+L_PV], ecx
%ifdef CUTVERIFY
    cmp dword [rbp+L_CUT], 1        ; test build: cutNode is 0 / 1, and a PV node is never a cut node (exit code 7)
    ja .cutfail
    test ecx, ecx
    jz .cutok
    cmp dword [rbp+L_CUT], 0
    jne .cutfail
.cutok:
%endif
    mov eax, [ply]
    mov [pvlen+rax*4], eax          ; this node's line starts empty (absolute index = ply)
    cmp eax, [seldep]               ; seldepth: deepest ply reached
    jle .sd
    mov [seldep], eax
.sd:inc qword [nodes]
    mov rax, [nodes]
    test eax, 2047
    jnz .nt
    call check_time
.nt:STOPTEST
    jne .ret0
    cmp dword [rbp+L_ROOT], 0
    jne .afterdraw
    call isdraw
    test eax, eax
%if R50MATE
    jz .nodraw
    cmp eax, 2
    jne .ret0
    call in_check                   ; 50-move rule: a draw unless the side to move is checkmated
    test eax, eax
    jz .ret0
    call has_legal
    test eax, eax
    jnz .ret0
    mov eax, -MATE
    add eax, [ply]
    jmp .retv
.nodraw:
%else
    jnz .ret0
%endif
    cmp dword [ply], MAXPLY-2
    jge .retstatic
    ; mate distance pruning
    mov eax, -MATE
    add eax, [ply]
    cmp [rbp+L_ALPHA], eax
    jge .mdp1
    mov [rbp+L_ALPHA], eax
.mdp1:
    mov eax, MATE-1
    sub eax, [ply]
    cmp [rbp+L_BETA], eax
    jle .mdp2
    mov [rbp+L_BETA], eax
.mdp2:
    mov eax, [rbp+L_ALPHA]
    cmp eax, [rbp+L_BETA]
    jge .retv
.afterdraw:
    mov eax, [rbp+L_INCHK]
    test eax, eax
    jns .ichave
    call in_check
    mov [rbp+L_INCHK], eax
    jmp .icdone
.ichave:
%ifdef INCHKVERIFY
    call in_check
    cmp eax, [rbp+L_INCHK]
    jne .icfail
%endif
    mov eax, [rbp+L_INCHK]
.icdone:
%ifdef SEVERIFY
    cmp dword [rbp+L_EXCL], 0       ; test build: an exclusion search is never in check, and it searches its parent's position
    je .sevok
    test eax, eax
    jnz .sevf8
    mov rcx, [hashk]
    cmp rcx, [sevhk]
    jne .sevf11
.sevok:
    mov eax, [rbp+L_INCHK]
%endif
    test eax, eax
    jz .noext
    inc dword [rbp+L_DEPTH]
.noext:
    cmp dword [rbp+L_DEPTH], 0
    jg .dosearch
    mov edi, [rbp+L_ALPHA]
    mov esi, [rbp+L_BETA]
    mov edx, [rbp+L_INCHK]          ; qsearch need not ask again
    call qsearch
    jmp .retv
.dosearch:
    ; ---- transposition table probe
    mov rax, [hashk]
    mov dword [rbp+L_TTMV], 0
    mov dword [rbp+L_TTEV], 0
%if NEEDTTD
    mov dword [rbp+L_TTB], 0
%endif
    call tt_probe
    test eax, eax
    jz .nott
    mov r12, rdx                    ; r12 = copy of the data qword (move 0-15, eval code 16-31, score 32-47, depth 48-55, flag 56-63)
    movzx ecx, r12w
    mov [rbp+L_TTMV], ecx
%if EVCACHE
    mov eax, r12d
    shr eax, 16
    mov [rbp+L_TTEV], eax
%endif
%if NEEDTTD
    mov rax, r12                    ; keep bound / score / depth of the hit for EVAL_TT, NMP_TTG, TTCUT_COND
    shr rax, 32
    movsx eax, ax
    cmp eax, MATEB
    jle .tq1
    sub eax, [ply]
    jmp .tq2
.tq1:cmp eax, -MATEB
    jge .tq2
    add eax, [ply]
.tq2:mov [rbp+L_TTSC], eax
    mov rax, r12
    shr rax, 56
    and eax, 3
    mov [rbp+L_TTB], eax
    mov rax, r12
    shr rax, 48
    movzx eax, al
    mov [rbp+L_TTDP], eax
%endif
    cmp dword [rbp+L_ROOT], 0
    jne .nott
    cmp dword [rbp+L_PV], 0
    jne .nott
%if SING
    cmp dword [rbp+L_EXCL], 0       ; exclusion search: the entry belongs to the node WITH the excluded move, no cutoff from it
    jne .nott
%endif
    mov rdx, r12
    shr rdx, 48
    movzx edx, dl                   ; depth
    cmp edx, [rbp+L_DEPTH]
    jl .nott
%if TTCUT_COND
    cmp dword [rbp+L_DEPTH], 4      ; TT cutoff at depth <= 4 only if the node type agrees: cutNode == (ttScore >= beta)
    jg .ttcok
    mov eax, [rbp+L_TTSC]
    xor ecx, ecx
    cmp eax, [rbp+L_BETA]
    setge cl
    cmp ecx, [rbp+L_CUT]
    jne .nott
.ttcok:
%endif
    mov rax, r12
    shr rax, 32
    movsx eax, ax                   ; score
    cmp eax, MATEB
    jle .m1
    sub eax, [ply]
    jmp .m2
.m1:cmp eax, -MATEB
    jge .m2
    add eax, [ply]
.m2:mov rcx, r12
    shr rcx, 56                     ; flag -> bound
    and ecx, 3
    cmp ecx, 3
    je .retv
    cmp ecx, 1
    jne .tup
    cmp eax, [rbp+L_BETA]
    jge .retv
    jmp .nott
.tup:
    cmp eax, [rbp+L_ALPHA]
    jle .retv
.nott:
    ; internal iterative reduction
    cmp dword [rbp+L_TTMV], 0
    jne .noiir
%if IIR_CUT
    LOADT eax, 19, IIR_MIND         ; not at all-nodes, depth >= 6
    cmp [rbp+L_DEPTH], eax
    jl .noiir
    cmp dword [rbp+L_PV], 0
    jne .iirok
    cmp dword [rbp+L_CUT], 0
    je .noiir
.iirok:
%else
%ifdef TUNE
    mov eax, [tunetab+77*4]         ; IIR from depth T77 (IIR_D) on
    cmp [rbp+L_DEPTH], eax
%else
    cmp dword [rbp+L_DEPTH], IIR_D
%endif
    jl .noiir
%endif
    dec dword [rbp+L_DEPTH]
.noiir:
%if LMR_TTC || RFP_EXT
    mov dword [rbp+L_TTCAP], 0      ; the TT move captures something (looked at on the node's own position, before any move is made)
    mov ecx, [rbp+L_TTMV]
    test ecx, ecx
    jz .ttcd
    shr ecx, 7
    and ecx, 127
    cmp byte [board+rcx], 0
    je .ttcd
    mov dword [rbp+L_TTCAP], 1
.ttcd:
%endif
    mov dword [rbp+L_SEVAL], 0
    mov dword [rbp+L_IMPR], 0
    mov ecx, [ply]
    mov dword [sevals+rcx*4], NOEVAL
    cmp dword [rbp+L_INCHK], 0
    jne .gen
    EVGET L_TTEV
    EVADJ
    mov [rbp+L_SEVAL], eax
    ; improving: static eval better than two (else four) plies ago; true if unknown
    mov ecx, [ply]
    mov [sevals+rcx*4], eax
    mov dword [rbp+L_IMPR], 1
    cmp ecx, 2
    jl .impdone
    mov edx, [sevals+rcx*4-8]
    cmp edx, NOEVAL
    jne .impcmp
    cmp ecx, 4
    jl .impdone
    mov edx, [sevals+rcx*4-16]
    cmp edx, NOEVAL
    je .impdone
.impcmp:
    cmp eax, edx
    jg .impdone
    mov dword [rbp+L_IMPR], 0
.impdone:
%if EVALDIFF_HIST
    ; the history of the opponent's last QUIET move is nudged by how much the static eval moved: evalDiff = clamp(-(parentEval + eval), -200, 156) + 58.
    ; Not at the root, not after a null move (nullok = 0), not when the parent was in check (no eval), not after a capture.  [sevals+ply*4] is the raw eval.
    cmp dword [ply], 0
    je .edh0
    cmp dword [rbp+L_NULLOK], 0
    je .edh0
%if NMP_VER
    mov eax, [ply]
    inc eax
    cmp eax, [S_NMPVP]              ; the verification search of an NMP node re-enters the same node: its parent's move was already counted
    je .edh0
%endif
    mov ecx, [ply]
    mov edx, [sevals+rcx*4-4]
    cmp edx, NOEVAL
    je .edh0
    mov eax, [gply]
    dec eax
    shl eax, 5
    cmp byte [ustack+rax+18], 0
    jne .edh0
    mov esi, [sevals+rcx*4]
    add esi, edx
    neg esi
    cmp esi, 156
    jle .edh1
    mov esi, 156
.edh1:
    cmp esi, -200
    jge .edh2
    mov esi, -200
.edh2:
    add esi, 58
    LOADT edx, 17, EDH_M
    imul esi, edx
    movzx edi, word [ustack+rax+16]
    xor dword [stm], 8              ; hist_update works on the side to move: the side that made the move is the other one
    call hist_update
    xor dword [stm], 8
.edh0:
%endif
%if EVAL_TT
    ; the eval that RFP / NMP / futility look at: the TT score when its bound says the true value is on that side of the static eval
%if SING
    cmp dword [rbp+L_EXCL], 0       ; (not in an exclusion search: the TT score includes the excluded move)
    jne .evtt0
%endif
    cmp dword [rbp+L_TTB], 0
    je .evtt0
    mov eax, [rbp+L_TTSC]
    cmp eax, MATEB
    jge .evtt0
    cmp eax, -MATEB
    jle .evtt0
    cmp eax, [rbp+L_SEVAL]
    jle .evtt1
    test byte [rbp+L_TTB], 1        ; score above the eval: needs a lower (or exact) bound
    jz .evtt0
    jmp .evtt2
.evtt1:
    test byte [rbp+L_TTB], 2        ; score below the eval: needs an upper (or exact) bound
    jz .evtt0
.evtt2:
    mov [rbp+L_SEVAL], eax
.evtt0:
%endif
    cmp dword [rbp+L_PV], 0
    jne .gen
%if RAZOR
    mov ecx, [rbp+L_DEPTH]
    cmp ecx, RAZOR_MAXD
    jg .nrazor
    LOADT edx, 9, RAZOR_M
    imul edx, ecx
%if RAZOR_SQ
    imul edx, ecx
%endif
    LOADT eax, 8, RAZOR_B
    add edx, eax
    add edx, [rbp+L_SEVAL]
    cmp edx, [rbp+L_ALPHA]
    jge .nrazor
    mov edi, [rbp+L_ALPHA]
    lea esi, [rdi+1]
    xor edx, edx                    ; not in check here
    call qsearch
    STOPTEST
    jne .ret0
    cmp eax, [rbp+L_ALPHA]
    jle .retv
.nrazor:
%endif
    ; reverse futility pruning
    mov ecx, [rbp+L_DEPTH]
    TCMP ecx, 75, RFP_MAXD
    jg .rfpx
%if RFP_EXT
    cmp dword [rbp+L_TTMV], 0       ; only when there is no TT move or it is a capture
    je .rfpt
    cmp dword [rbp+L_TTCAP], 0
    je .rfpx
.rfpt:
%endif
%ifdef TUNE
    mov edx, [tunetab+0*4]
    imul edx, ecx
    mov eax, [tunetab+1*4]
    imul eax, [rbp+L_IMPR]
%else
    imul edx, ecx, 85
    mov eax, [rbp+L_IMPR]
    imul eax, eax, 35
%endif
    sub edx, eax
    mov eax, [rbp+L_SEVAL]
    sub eax, edx
    cmp eax, [rbp+L_BETA]
    jl .rfpx
    cmp dword [rbp+L_BETA], -MATEB
    jle .rfpx
    cmp dword [rbp+L_BETA], MATEB
    jge .rfpx
%if RFP_EXT
    mov eax, [rbp+L_BETA]           ; (661 * beta + 363 * eval) / 1024: never below beta here (eval >= beta + margin)
    imul eax, eax, 661
    mov edx, [rbp+L_SEVAL]
    imul edx, edx, 363
    add eax, edx
    sar eax, 10
%endif
    jmp .retv                   ; eax = static eval - margin
.rfpx:
    ; null move pruning
    cmp dword [rbp+L_NULLOK], 0
    je .gen
    cmp dword [rbp+L_DEPTH], 3
    jl .gen
%if NMP_CUT
    cmp dword [rbp+L_CUT], 0        ; null move only at cut nodes
    je .gen
%endif
%if NMP_VER
    mov eax, [ply]
    cmp eax, [S_NMPMIN]             ; inside a verification search the null move is off below this ply
    jl .gen
%endif
    mov eax, [rbp+L_SEVAL]
    cmp eax, [rbp+L_BETA]
    jl .gen
    mov eax, [stm]
    movzx ecx, byte [cnt+rax+2]
    add cl, [cnt+rax+3]
    add cl, [cnt+rax+4]
    add cl, [cnt+rax+5]
    jz .gen
    mov eax, [rbp+L_DEPTH]
    shr eax, 2
%ifdef TUNE
    add eax, [tunetab+2*4]
%else
    add eax, NMP_RB
%endif
    mov ecx, [rbp+L_SEVAL]
    sub ecx, [rbp+L_BETA]
    cmp ecx, 600
    jl .nr2
    mov ecx, 599
.nr2:
    push rax
    mov eax, ecx
    xor edx, edx
%ifdef TUNE
    mov ecx, [tunetab+3*4]
    test ecx, ecx
    jnz .nrdiv
    mov ecx, 1
.nrdiv:
%else
    mov ecx, NMP_EDIV
%endif
    div ecx
    mov ecx, eax
    pop rax
    add eax, ecx
    mov [rbp+L_R], eax
%if NMP_TTG
    cmp dword [rbp+L_TTB], 0        ; the TT already proves "score < beta" for the depth the null search would have: it cannot fail high
    je .nottg
    test byte [rbp+L_TTB], 2
    jz .nottg
    mov eax, [rbp+L_TTSC]
    cmp eax, [rbp+L_BETA]
    jge .nottg
    mov eax, [rbp+L_DEPTH]
    sub eax, [rbp+L_R]
    cmp [rbp+L_TTDP], eax
    jl .nottg
    jmp .gen
.nottg:
%endif
%if CONTHIST
    mov ecx, [ply]
    mov qword [chp+rcx*8+16], chdummy   ; after a null move there is no previous move
%endif
    call domove_null
    inc dword [ply]
    mov edi, [rbp+L_DEPTH]
    dec edi
    sub edi, [rbp+L_R]
    mov esi, [rbp+L_BETA]
    neg esi
    lea edx, [rsi+1]
    xor ecx, ecx
    xor r8d, r8d                    ; after a null move the side to move is not in check
    xor r9d, r9d                    ; cutNode = 0 for the null move child
    call search
    dec dword [ply]
    neg eax
    mov [rbp+L_TMP], eax
    call undomove_null
    STOPTEST
    jne .ret0
    mov eax, [rbp+L_TMP]
    cmp eax, [rbp+L_BETA]
    jl .gen
    cmp eax, MATEB
    jl .nmpv
    mov eax, [rbp+L_BETA]
    mov [rbp+L_TMP], eax
.nmpv:
%if NMP_VER
    cmp dword [S_NMPMIN], 0         ; verification search (depth >= NMP_VERD): the same node again, null move disabled for the next
    jne .nmpret                     ; 3/4 of the null-search depth; only when the null move failed high
    LOADT ecx, 18, NMP_VERD
    cmp [rbp+L_DEPTH], ecx
    jl .nmpret
    mov eax, [rbp+L_DEPTH]
    sub eax, [rbp+L_R]
    mov edi, eax
    imul eax, eax, 3
    shr eax, 2
    add eax, [ply]
    mov [S_NMPMIN], eax
%if EVALDIFF_HIST
    mov eax, [ply]
    inc eax
    mov [S_NMPVP], eax              ; the verification search re-enters THIS node at the same ply (EVALDIFF_HIST would count the parent's move twice)
%endif
    mov esi, [rbp+L_BETA]
    mov edx, esi
    dec esi
    mov ecx, 1
    mov r8d, 0                      ; this node is not in check
    xor r9d, r9d
    call search
    mov dword [S_NMPMIN], 0
%if EVALDIFF_HIST
    mov dword [S_NMPVP], 0
%endif
    STOPTEST
    jne .ret0
    cmp eax, [rbp+L_BETA]
    jl .gen                         ; verification failed: search normally
.nmpret:
%endif
    mov eax, [rbp+L_TMP]
    jmp .retv
%if PROBCUT
    ; ---- ProbCut (PROBCUT, see p1_data.asm): a capture that wins enough by exchange and holds pcBeta = beta + PC_M at reduced depth cuts
.gen:
    cmp dword [rbp+L_PV], 0
    jne .gen2
    cmp dword [rbp+L_ROOT], 0
    jne .gen2
    cmp dword [rbp+L_INCHK], 0
    jne .gen2
%if SING
    cmp dword [rbp+L_EXCL], 0
    jne .gen2
%endif
    cmp dword [rbp+L_DEPTH], PC_MIND
    jl .gen2
    mov eax, [rbp+L_BETA]
    cmp eax, -MATEB
    jle .gen2
    cmp eax, MATEB - PC_M
    jge .gen2
    add eax, PC_M
    mov [rbp+L_OALPHA], eax         ; pcBeta (L_OALPHA is set again after the move generation)
%if NEEDTTD
    test byte [rbp+L_TTB], 2        ; an upper bound (or exact) below pcBeta, deep enough: the captures will not hold it
    jz .pc0
    cmp [rbp+L_TTSC], eax
    jge .pc0
    mov ecx, [rbp+L_DEPTH]
    sub ecx, 3
    cmp [rbp+L_TTDP], ecx
    jge .gen2
.pc0:
%endif
    PCCNT 0
    lea rdi, [rbp+L_MOVES]
    mov esi, 1                      ; captures (and promotions)
    call gen
    lea rcx, [rbp+L_MOVES]
    sub rax, rcx
    shr eax, 2
    mov [rbp+L_N], eax
    lea rdi, [rbp+L_MOVES]
    lea rsi, [rbp+L_SCORES]
    mov edx, [rbp+L_N]
    mov ecx, [rbp+L_TTMV]
    call score_moves
    mov dword [rbp+L_IDX], 0
.pcl:
    mov edx, [rbp+L_IDX]
    cmp edx, [rbp+L_N]
    jge .gen2
    lea rdi, [rbp+L_MOVES]
    lea rsi, [rbp+L_SCORES]
    mov ecx, [rbp+L_N]
    call pick
    mov [rbp+L_MV], eax
    inc dword [rbp+L_IDX]
    mov edi, eax
    mov ecx, [ply]
    mov esi, [rbp+L_OALPHA]
    sub esi, [sevals+rcx*4]         ; the capture must win at least pcBeta - eval by exchange (the raw static eval, not the TT-adjusted one)
    call see_ge
    test eax, eax
    jz .pcl
%if CONTHIST
    mov edi, [rbp+L_MV]
    call ch_row
    mov ecx, [ply]
    mov [chp+rcx*8+16], rax
%endif
    mov edi, [rbp+L_MV]
    call domove
    test eax, eax
    jz .pcill
    PCCNT 1
    call in_check
    mov [rbp+L_GIVES], eax
    inc dword [ply]
    xor edi, edi                    ; quiescence first
    mov esi, [rbp+L_OALPHA]
    neg esi
    lea edx, [rsi+1]
    xor ecx, ecx
    mov r8d, [rbp+L_GIVES]
    mov r9d, [rbp+L_CUT]
    xor r9d, 1
    call search
    neg eax
    cmp eax, [rbp+L_OALPHA]
    jl .pcu
    PCCNT 2
    mov edi, [rbp+L_DEPTH]          ; then the reduced search
    sub edi, PC_R
    mov esi, [rbp+L_OALPHA]
    neg esi
    lea edx, [rsi+1]
    mov ecx, 1
    mov r8d, [rbp+L_GIVES]
    mov r9d, [rbp+L_CUT]            ; cutNode of the child: !cutNode, as for the first move
    xor r9d, 1
    call search
    neg eax
.pcu:
    mov [rbp+L_TMP], eax
    dec dword [ply]
    call undomove
    STOPTEST
    jne .ret0
    mov eax, [rbp+L_TMP]
    cmp eax, [rbp+L_OALPHA]
    jl .pcl
    cmp eax, MATEB                  ; a mate score from a reduced search: no cut, the normal search decides
    jge .pcl
    PCCNT 3
    PCDCNT 8
%ifdef PCVERIFY
    mov dword [rbp+L_PCW], 1        ; test build: no cut; the normal search runs and .store counts whether the node really fails high
    jmp .gen2
%else
    mov rax, [hashk]                ; lower bound at depth - PC_R + 1 with the capture as move
    mov edi, [rbp+L_MV]
%if EVCACHE
    mov esi, [rbp+L_TTEV]
    shl esi, 16
    or edi, esi
%endif
    mov edx, [rbp+L_TMP]
    mov esi, [rbp+L_DEPTH]
    sub esi, PC_R - 1
    mov ecx, 1
    call tt_store
    mov eax, [rbp+L_TMP]
    sub eax, PC_M                   ; value - (pcBeta - beta): still >= beta
    jmp .retv
%endif
.pcill:
    call undomove
    jmp .pcl
.gen2:
%else
    ; ---- move generation
.gen:
%endif
    lea rdi, [rbp+L_MOVES]
    xor esi, esi
    call gen
    lea rcx, [rbp+L_MOVES]
    sub rax, rcx
    shr eax, 2
    cmp dword [rbp+L_ROOT], 0
    je .norf
    cmp dword [nsm], 0
    je .norf
    lea rdi, [rbp+L_MOVES]      ; go searchmoves: keep only the listed moves
    mov ecx, eax
    call root_filter
.norf:
    mov [rbp+L_N], eax
    lea rdi, [rbp+L_MOVES]
    lea rsi, [rbp+L_SCORES]
    mov edx, [rbp+L_N]
    mov ecx, [rbp+L_TTMV]
    call score_moves
    mov dword [rbp+L_IDX], 0
%ifdef ORDSTAT
    mov dword [rbp+L_ORDQ], 0
%endif
    mov dword [rbp+L_BEST], -INF
    mov eax, [rbp+L_ALPHA]
    mov [rbp+L_OALPHA], eax
.mloop:
    mov edx, [rbp+L_IDX]
    cmp edx, [rbp+L_N]
    jge .mdone
    lea rdi, [rbp+L_MOVES]
    lea rsi, [rbp+L_SCORES]
    mov ecx, [rbp+L_N]
    call pick
    mov [rbp+L_MV], eax
    inc dword [rbp+L_IDX]
%if SING
    cmp eax, [rbp+L_EXCL]           ; exclusion search: the excluded move is not searched and not counted
    je .mloop
%endif
%ifdef ORDSTAT
    ORDFIRST
%endif
    ; classify quiet / tactical
    mov edi, eax
    mov ecx, edi
    shr ecx, 7
    and ecx, 127                ; to
    mov dword [rbp+L_ISQ], 0
    cmp byte [board+rcx], 0
    jne .qdone
    mov edx, edi
    and edx, 127
    movzx edx, byte [board+rdx]
    and edx, 7
    cmp edx, 1
    jne .isq
    mov eax, ecx
    and eax, 0xF0
    cmp eax, 0x70
    je .qdone
    test eax, eax
    jz .qdone
    mov edx, edi
    and edx, 7
    mov eax, ecx
    and eax, 7
    cmp edx, eax
    jne .qdone
.isq:
    mov dword [rbp+L_ISQ], 1
%ifdef ORDSTAT
    inc dword [rbp+L_ORDQ]
%endif
.qdone:
%if CAPHIST && CAP_LMR
    mov dword [rbp+L_CAPH], 0x7FFFFFFF
    cmp dword [rbp+L_ISQ], 0
    jne .chq
    mov edi, [rbp+L_MV]             ; the capture history of this capture, while the board still shows the victim
    call cap_index
    test eax, eax
    js .chq
    movsx eax, word [captab+rax*2]
    mov [rbp+L_CAPH], eax
.chq:
%endif
    mov dword [rbp+L_SEEB], 0
%if SEE_PC || SEE_PQ
    cmp dword [rbp+L_PV], 0
    jne .nosee
    cmp dword [rbp+L_INCHK], 0
    jne .nosee
    cmp dword [rbp+L_MCNT], 1
    jl .nosee                   ; never before one legal move has been searched
    cmp dword [rbp+L_BEST], -MATEB
    jle .nosee
    mov ecx, [rbp+L_DEPTH]
    cmp ecx, SEE_MAXD
    jg .nosee
    cmp dword [rbp+L_ISQ], 0
    jne .seeq
%if SEE_PC
    mov eax, ecx
    imul eax, ecx
%ifdef TUNE
    mov esi, eax
    imul esi, [tunetab+64*4]
    neg esi                     ; threshold = -SEE_CM * depth^2 (T64)
%else
    imul esi, eax, -SEE_CM      ; threshold = -SEE_CM * depth^2
%endif
    mov edi, [rbp+L_MV]
    call see_ge
    test eax, eax
    jz .mloop                   ; hopeless capture: skip it
%endif
    jmp .nosee
.seeq:
%if SEE_PQ
    imul esi, ecx, -SEE_QM      ; threshold = -SEE_QM * depth
    mov edi, [rbp+L_MV]
    call see_ge
    test eax, eax
    jnz .nosee
    mov dword [rbp+L_SEEB], 1   ; quiet move walking into a losing exchange: dropped after the check test
%endif
.nosee:
%endif
%if PREPRUNE
%ifdef PPVERIFY
    ; test build: ask pp_ok for every quiet move of a node that is not in check, keep the answer, compare it after domove / in_check
    mov dword [rbp+L_PPR], 3
    cmp dword [rbp+L_ISQ], 0
    je .nopp
    cmp dword [rbp+L_INCHK], 0
    jne .nopp
    mov edi, [rbp+L_MV]
    call pp_ok
    mov [rbp+L_PPR], eax
%else
    ; Pre-make pruning: the tests of "pruning of late quiet moves" below do not depend on the move once it is known to be legal
    ; and not to give check (the make only adds one to MCNT).  So ask pp_ok first and skip make / in_check / unmake for a move that
    ; the tests below would drop.  Same decisions, same MCNT, same tree.  Gives check / not decided -> the make / unmake path.
    cmp dword [rbp+L_ISQ], 0
    je .nopp
    cmp dword [rbp+L_PV], 0
    jne .nopp
    cmp dword [rbp+L_INCHK], 0
    jne .nopp
    cmp dword [rbp+L_ROOT], 0
    jne .nopp
%if SEE_PQ
    cmp dword [rbp+L_SEEB], 0
    jne .ppc
%endif
    cmp dword [rbp+L_MCNT], 0
    jle .nopp                   ; the move would be the first legal one (MCNT 1 after the make): never pruned
    cmp dword [rbp+L_BEST], -MATEB
    jle .nopp
    mov ecx, [rbp+L_DEPTH]
    TCMP ecx, 74, PR_MAXD           ; the same depth limit and count as the pruning after the make
    jg .nopp
    LMPLIM
    cmp [rbp+L_MCNT], eax
    jge .ppc                    ; MCNT + 1 > limit: the post-make test "cmp MCNT, limit; jg .prune"
%if FUT_LMRD
    mov r9d, [rbp+L_MCNT]
    inc r9d                     ; the move number the post-make test will see
    LMRD r9d
%else
    cmp ecx, 4
    jg .nopp
%endif
    FUTM
    cmp edx, [rbp+L_ALPHA]
    jg .nopp
.ppc:
    mov edi, [rbp+L_MV]
    call pp_ok
    test eax, eax
    jz .mloop                   ; illegal: skipped and not counted, exactly like .illegal
    cmp eax, 1
    jne .nopp                   ; gives check (exempt from pruning) or not decided: make it
    inc dword [rbp+L_MCNT]      ; legal, no check, would be pruned: count it and go on
    jmp .mloop
%endif
.nopp:
%endif
%if SING
    ; ---- singular extension test (SING, see p1_data.asm): the first move is the TT move and nothing has been searched yet
    mov eax, [rbp+L_MV]
    cmp eax, [rbp+L_TTMV]
    jne .sex
    cmp dword [rbp+L_MCNT], 0
    jne .sex
    cmp dword [rbp+L_ROOT], 0
    jne .sex
    cmp dword [rbp+L_EXCL], 0       ; no test inside an exclusion search
    jne .sex
    cmp dword [rbp+L_INCHK], 0      ; a node in check already searches every move one ply deeper
    jne .sex
    SECNT 0
    mov ecx, [rbp+L_DEPTH]
    cmp ecx, SE_MIND
    jl .sex
    SECNT 1
    test byte [rbp+L_TTB], 1        ; lower or exact bound
    jz .sex
    SECNT 2
    mov eax, [rbp+L_TTDP]
    TADD eax, 63, SE_TTD
    cmp eax, ecx
    jl .sex
    SECNT 3
    mov eax, [rbp+L_TTSC]
    cmp eax, MATEB
    jge .sex
    cmp eax, -MATEB
    jle .sex
    mov eax, [itdepth]              ; no extension from ply 2 * (iteration depth) on
    add eax, eax
    cmp [ply], eax
    jge .sex
    SECNT 4
    mov edi, [rbp+L_MV]             ; the TT move must be legal (the exclusion search would else judge a move that is never played)
    call domove
    mov [rbp+L_TMP], eax
    call undomove
    cmp dword [rbp+L_TMP], 0
    je .sex
    SECNT 5
    SEDCNT 0
%ifdef TUNE
    mov eax, [rbp+L_DEPTH]
    imul eax, [tunetab+62*4]        ; SE_M
%else
    imul eax, [rbp+L_DEPTH], SE_M
%endif
    mov edx, [rbp+L_TTSC]
    sub edx, eax
    mov [rbp+L_TMP], edx            ; singularBeta
%ifdef SESTAT
%if UNIT == 0
    mov rax, [nodes]
    mov [rbp+L_NB], rax             ; (never the root here: L_NB is free)
%endif
%endif
    mov eax, [rbp+L_MV]
    mov [sexcl], eax
%ifdef SEVERIFY
    mov rax, [hashk]
    mov [sevhk], rax
%endif
    mov edi, [rbp+L_DEPTH]
    dec edi
    shr edi, 1                      ; (depth - 1) / 2
    lea esi, [rdx-1]
    xor ecx, ecx                    ; nullok = 0: no null move, no EVALDIFF_HIST in the exclusion search
    xor r8d, r8d                    ; this node is not in check
    mov r9d, [rbp+L_CUT]
    call search
%ifdef SESTAT
%if UNIT == 0
    mov rcx, [nodes]
    sub rcx, [rbp+L_NB]
    add [sestc+9*8], rcx
%endif
%endif
    STOPTEST
    jne .ret0
    cmp eax, [rbp+L_TMP]
    jge .senot
    SECNT 6                         ; every other move failed low: singular
    SEDCNT 1
%if SING == 2
    mov dword [rbp+L_SEXT], 1
%endif
    jmp .sex
.senot:
    mov eax, [rbp+L_TMP]
    cmp eax, [rbp+L_BETA]
    jl .se0
    SECNT 7                         ; another move reaches singularBeta >= beta: multi-cut
    SEDCNT 2
%if SING == 2 && SE_MC
%ifdef PCVERIFY
    cmp dword [rbp+L_PCW], 0        ; test build: a would-be ProbCut node that the multi-cut cuts: confirmed (singularBeta >= beta)
    je .pcvm
    PCCNT 4
.pcvm:
%endif
    jmp .retv                       ; eax = singularBeta
%endif
    jmp .sex
.se0:
    SECNT 8
%if SING == 2 && NEG_EXT
    mov eax, [rbp+L_TTSC]           ; negative extension: the TT move is not singular and the node did not cut
    cmp eax, [rbp+L_BETA]
    jge .sneg                       ; ttScore >= beta: other moves are about as good, the TT move gets one ply less
%if NEG_EXT == 2
    cmp dword [rbp+L_CUT], 0
    jne .sneg                       ; expected cut node: the same
%endif
    jmp .sex
.sneg:
    mov dword [rbp+L_SEXT], -1      ; the TT move is the first legal move: consumed at its depth below, never by another move
%endif
.sex:
%endif
%if CONTHIST
    mov edi, [rbp+L_MV]             ; chp[ply + 2] = row of this move (the children order their quiet moves with it)
    call ch_row
    mov ecx, [ply]
    mov [chp+rcx*8+16], rax
%endif
%ifdef SEVERIFY
    mov eax, [rbp+L_EXCL]           ; test build: the excluded move is never made by the exclusion search
    test eax, eax
    jz .sev9
    cmp eax, [rbp+L_MV]
    je .sevf9
.sev9:
%endif
    mov edi, [rbp+L_MV]
    call domove
%ifdef PPVERIFY
    mov [rbp+L_PPL], eax
    mov ecx, [rbp+L_PPR]
    cmp ecx, 3
    je .ppvd
    test eax, eax
    jnz .ppv1
    test ecx, ecx               ; make says illegal
    jnz .ppfail
    jmp .ppvd
.ppv1:
    call in_check
    inc eax                     ; 1 = legal / no check, 2 = legal / check
    cmp eax, [rbp+L_PPR]
    jne .ppfail
.ppvd:
    mov eax, [rbp+L_PPL]
%endif
    test eax, eax
    jz .illegal
    inc dword [rbp+L_MCNT]
    cmp dword [rbp+L_ROOT], 0
    je .nocm
    mov edi, [rbp+L_MV]             ; root: "info depth d currmove m currmovenumber n" after 3 s
    mov esi, [rbp+L_MCNT]
    call print_currmove
.nocm:
    call in_check
    mov [rbp+L_GIVES], eax
%if SEE_PQ
    cmp dword [rbp+L_SEEB], 0
    je .noseeb
    cmp dword [rbp+L_GIVES], 0
    jne .noseeb
    jmp .prune
.noseeb:
%endif
    ; ---- pruning of late quiet moves
    cmp dword [rbp+L_ISQ], 0
    je .nopr
    cmp dword [rbp+L_PV], 0
    jne .nopr
    cmp dword [rbp+L_INCHK], 0
    jne .nopr
    cmp dword [rbp+L_GIVES], 0
    jne .nopr
    cmp dword [rbp+L_MCNT], 1
    jle .nopr
    cmp dword [rbp+L_BEST], -MATEB
    jle .nopr
    mov ecx, [rbp+L_DEPTH]
    TCMP ecx, 74, PR_MAXD
    jg .nopr
    LMPLIM                          ; late move count
    cmp [rbp+L_MCNT], eax
    jg .prune
%if CONTHIST && CH_PRUNE
    LOADT eax, 61, CH_PD            ; continuation-history pruning: the move is made, [ply] is still this node's ply
    cmp ecx, eax
    jg .chp0
    mov eax, [rbp+L_IDX]
    cmp dword [rbp+rax*4+L_SCORES-4], 65000
    jg .chp0                        ; killer
    mov eax, [rbp+L_MV]
    shr eax, 7
    and eax, 127                    ; to: the moved piece stands there now (a quiet move is no promotion)
    movzx edx, byte [board+rax]
    movzx edx, word [ch_pofs+rdx*2]
    mov r8d, eax
    and r8d, 7
    add eax, r8d
    shr eax, 1
    add edx, eax                    ; entry index
    mov eax, [ply]
    mov r8, [chp+rax*8+8]
    movsx r9d, word [r8+rdx*2]
    mov r8, [chp+rax*8]
    movsx eax, word [r8+rdx*2]
    add r9d, eax                    ; row(ply - 1) + row(ply - 2)
    HSCNT 0
    HSB7 r9d
    LOADT eax, 60, CH_PM
    imul eax, ecx
    neg eax
    cmp r9d, eax
%ifdef HSTAT
    jge .chp0
    HSCNT 1
    jmp .prune
%else
    jl .prune
%endif
.chp0:
    mov ecx, [rbp+L_DEPTH]
%endif
%if FUT_LMRD
    mov r9d, [rbp+L_MCNT]
    LMRD r9d
%else
    cmp ecx, 4
    jg .nopr
%endif
    FUTM
    cmp edx, [rbp+L_ALPHA]
    jle .prune
    jmp .nopr
.prune:
    call undomove
    jmp .mloop
.nopr:
%if TM_EFFORT && HELPER == 0
    cmp dword [rbp+L_ROOT], 0
    je .nonb
    mov rax, [nodes]
    mov [rbp+L_NB], rax             ; root move: nodes before its search (the best move's share of the iteration, TM_EFFORT)
.nonb:
%endif
    mov eax, [rbp+L_DEPTH]
    dec eax
%if SING == 2
    add eax, [rbp+L_SEXT]           ; TT move: +1 singular, -1 negative extension (only the first move can have it)
    mov dword [rbp+L_SEXT], 0
%endif
    mov [rbp+L_NEWD], eax
    inc dword [ply]
%if CHK_EXT
    cmp dword [rbp+L_GIVES], 0
    je .noce
    mov eax, [ply]              ; guard against check chains: no extra ply from ply 2 * (iteration depth) on
    mov ecx, [itdepth]
    add ecx, ecx
    cmp eax, ecx
    jge .noce
%if CHK_EXT == 1
    mov edi, [rbp+L_MV]         ; safe check only: the destination square must not be attacked by the side that is now in check
    shr edi, 7
    and edi, 127
    mov esi, [stm]
    call attacked
    test eax, eax
    jnz .noce
%endif
    inc dword [rbp+L_NEWD]
.noce:
%endif
    cmp dword [rbp+L_MCNT], 1
    je .full
    xor r13d, r13d
    cmp dword [rbp+L_DEPTH], 3
    jl .nored
    cmp dword [rbp+L_MCNT], LMR_MINM
    jl .nored
%if LMR_BADCAP
    cmp dword [rbp+L_ISQ], 0
    jne .lmrq
    mov ecx, [rbp+L_IDX]            ; a capture is reduced only if SEE sorted it behind the quiet moves (score <= -200000)
    cmp dword [rbp+rcx*4+L_SCORES-4], -200000
    jg .nored
.lmrq:
%else
    cmp dword [rbp+L_ISQ], 0
%if CAPHIST && CAP_LMR
    je .caplmr
%else
    je .nored
%endif
%endif
    cmp dword [rbp+L_INCHK], 0
    jne .nored
    cmp dword [rbp+L_GIVES], 0
    jne .nored
    mov eax, [rbp+L_DEPTH]
    cmp eax, 63
    jle .d1
    mov eax, 63
.d1:mov ecx, [rbp+L_MCNT]
    cmp ecx, 63
    jle .m1b
    mov ecx, 63
.m1b:
    shl eax, 6
    add eax, ecx
    movzx r13d, byte [lmrtab+rax]
    cmp dword [rbp+L_PV], 0
    je .p1
    dec r13d
.p1:mov eax, [rbp+L_MV]
    mov ecx, [ply]
    cmp eax, [killers+rcx*8-8]  ; killers of this node (ply was incremented)
    je .kill
    cmp eax, [killers+rcx*8-4]
    jne .p1b
.kill:
    dec r13d
.p1b:
%if LMR_CUT
    cmp dword [rbp+L_CUT], 0        ; cut node: the move is expected to fail low, search it shallower
    je .lcut
    LOADT edx, 6, LMR_CUTP
    add r13d, edx
.lcut:
%endif
%if LMR_IMPR
    cmp dword [rbp+L_IMPR], 0
    jne .limp
    inc r13d                        ; not improving: reduce more
%if LMR_IMPR == 2
    jmp .limpd
.limp:
    dec r13d                        ; improving: reduce less
.limpd:
%else
.limp:
%endif
%endif
%if LMR_HIST
    mov ecx, [rbp+L_IDX]            ; history value of the move = the score pick() left behind (killers / TT move: far above 20000, bad captures: far below)
    mov eax, [rbp+rcx*4+L_SCORES-4]
%if CONTHIST
    cmp eax, 65000                  ; ordering score of a quiet move: +-64000 (killers / TT move far above, bad captures far below)
    jg .lh0
    cmp eax, -65000
    jl .lh0
%if CH_LMR
    LOADT edx, 59, LMR_HTC
%else
    mov ecx, [rbp+L_MV]             ; histtab only: the move is made, the side that made it is the one not to move
    mov eax, ecx
    and eax, 127
    shl eax, 7
    shr ecx, 7
    and ecx, 127
    add eax, ecx
    mov ecx, [stm]
    xor ecx, 8
    shl ecx, 11
    add eax, ecx
    movsx eax, word [histtab+rax*2]
    LOADT edx, 7, LMR_HT
%endif
%else
    cmp eax, 20000
    jg .lh0
    cmp eax, -20000
    jl .lh0
    LOADT edx, 7, LMR_HT
%endif
    HSCNT 9
    HSB6 eax
    cmp eax, edx
    jle .lhn
    HSCNT 10
    dec r13d
    jmp .lh0
.lhn:
    neg edx
    cmp eax, edx
    jge .lh0
    HSCNT 11
    inc r13d
.lh0:
%endif
%if LMR_TTC
    cmp dword [rbp+L_TTCAP], 0      ; the TT move is a capture: the quiet moves are less likely to be best
    je .ltc
    inc r13d
.ltc:
%endif
    test r13d, r13d
    jns .p2
    xor r13d, r13d
%if CAPHIST && CAP_LMR
    jmp .p2
.caplmr:                            ; CAP_LMR: a late capture with a capture history below CAP_LMRT is searched one ply shallower
    cmp dword [rbp+L_INCHK], 0
    jne .nored
    cmp dword [rbp+L_GIVES], 0
    jne .nored
    cmp dword [rbp+L_CAPH], CAP_LMRT
    jge .nored
    mov r13d, 1
%endif
.p2:mov eax, [rbp+L_NEWD]
    dec eax
    cmp r13d, eax
    jle .nored
    mov r13d, eax
.nored:
    mov [rbp+L_R], r13d
    mov edi, [rbp+L_NEWD]
    sub edi, r13d
    mov esi, [rbp+L_ALPHA]
    neg esi
    mov edx, esi
    dec esi
    mov ecx, 1
    mov r8d, [rbp+L_GIVES]
    mov r9d, [rbp+L_CUT]            ; cutNode of the child: !cutNode, but a reduced search is always a cut-node search
    xor r9d, 1
    test r13d, r13d
    jz .cn1
    mov r9d, 1
.cn1:
    call search
    neg eax
    mov [rbp+L_SC], eax
    cmp eax, [rbp+L_ALPHA]
    jle .childdone
    cmp dword [rbp+L_R], 0
    je .pvchk
    mov edi, [rbp+L_NEWD]
%if LMR_RES
    mov ecx, [rbp+L_BEST]           ; deeper / shallower re-search: the reduced search beat the best score by a lot -> search one ply deeper;
    LOADT edx, 10, LMR_DEEPM        ; it barely beat it -> one ply shallower than the full depth (and no re-search if that is the reduced depth)
    lea edx, [rdx+rdi*2]
    add edx, ecx
    cmp eax, edx
    jg .resdeep
    LOADT edx, 11, LMR_SHALM
    add ecx, edx
    cmp eax, ecx
    jge .resdo
    dec edi
    mov edx, [rbp+L_NEWD]
    sub edx, [rbp+L_R]
    cmp edi, edx
    jle .pvchk                      ; shallower than (or equal to) the reduced search: keep its score
    jmp .resdo
.resdeep:
    inc edi
.resdo:
%endif
    mov esi, [rbp+L_ALPHA]
    neg esi
    mov edx, esi
    dec esi
    mov ecx, 1
    mov r8d, [rbp+L_GIVES]
    mov r9d, [rbp+L_CUT]            ; re-search at full depth: !cutNode
    xor r9d, 1
    call search
    neg eax
    mov [rbp+L_SC], eax
    cmp eax, [rbp+L_ALPHA]
    jle .childdone
.pvchk:
    cmp eax, [rbp+L_BETA]
    jge .childdone
.full:
    mov edi, [rbp+L_NEWD]
    mov esi, [rbp+L_BETA]
    neg esi
    mov edx, [rbp+L_ALPHA]
    neg edx
    mov ecx, 1
    mov r8d, [rbp+L_GIVES]
    mov r9d, [rbp+L_CUT]            ; first move of a non-PV node: !cutNode; every PV search is a PV node (cutNode 0)
    xor r9d, 1
    cmp dword [rbp+L_PV], 0
    jne .fpv
    jmp .fcn
.fpv:
    xor r9d, r9d
.fcn:
    call search
    neg eax
    mov [rbp+L_SC], eax
.childdone:
    dec dword [ply]
    call undomove
    STOPTEST
    jne .ret0
    mov eax, [rbp+L_SC]
    cmp eax, [rbp+L_BEST]
    jle .nobest
    mov [rbp+L_BEST], eax
    cmp eax, [rbp+L_ALPHA]
    jle .nobest
    mov ecx, [rbp+L_MV]
    mov [rbp+L_BESTMV], ecx
%if TM_EFFORT && HELPER == 0
    cmp dword [rbp+L_ROOT], 0
    je .noeff
    mov rdx, [nodes]
    sub rdx, [rbp+L_NB]
    mov [S_ROOTEFF], rdx            ; nodes the new best root move cost (TM_EFFORT)
.noeff:
%endif
    mov [rbp+L_ALPHA], eax
    cmp dword [rbp+L_PV], 0         ; principal variation: only PV nodes (and the root) keep a line
    jne .pvu
    cmp dword [rbp+L_ROOT], 0
    je .nroot2
.pvu:
    call pv_update                  ; keeps eax, ecx
    cmp dword [rbp+L_ROOT], 0
    je .nroot2
    mov [rootbest], ecx
    mov [rootscore], eax
    call pv_saveroot                ; keeps eax, ecx: rpv always belongs to rootbest
.nroot2:
    cmp eax, [rbp+L_BETA]
    jge .cutoff
.nobest:
    cmp dword [rbp+L_ISQ], 0
%if CAPHIST
    je .ncap
%else
    je .mloop
%endif
    mov ecx, [rbp+L_NQ]
    cmp ecx, 64
    jge .mloop
    mov eax, [rbp+L_MV]
    mov [rbp+rcx*4+L_QUIETS], eax
    inc ecx
    mov [rbp+L_NQ], ecx
    jmp .mloop
%if CAPHIST
.ncap:
    mov ecx, [rbp+L_NCAP]           ; a capture (or a promotion: cap_update skips what has no entry) searched without a cutoff
    cmp ecx, 16
    jge .mloop
    mov eax, [rbp+L_MV]
    mov [rbp+rcx*4+L_CAPS], eax
    inc ecx
    mov [rbp+L_NCAP], ecx
    jmp .mloop
%endif
.illegal:
    call undomove
    jmp .mloop
%ifdef INCHKVERIFY
.icfail:
    mov eax, 231
    mov edi, 4
    syscall
%endif
%ifdef CUTVERIFY
.cutfail:
%ifdef WIN
%error "CUTVERIFY is a Linux test build"
%endif
    mov eax, 231                ; exit_group
    mov edi, 7
    syscall
%endif
%ifdef PPVERIFY
.ppfail:                        ; pp_ok and make / in_check disagree: exit code 3 (tests/t_ppverify.py)
%ifdef WIN
%error "PPVERIFY is a Linux test build"
%endif
    mov eax, 231                ; exit_group
    mov edi, 3
    syscall
%endif
.cutoff:
%ifdef ORDSTAT
    ORDREC
    ORDCAP
%endif
    cmp dword [rbp+L_ISQ], 0
%if CAPHIST
    je .capcut
%else
    je .store
%endif
    mov eax, [ply]
    lea rdx, [killers+rax*8]
    mov ecx, [rbp+L_MV]
    cmp [rdx], ecx
    je .k1
    mov esi, [rdx]
    mov [rdx+4], esi
    mov [rdx], ecx
.k1:mov eax, [rbp+L_DEPTH]
    imul eax, eax
%ifdef TUNE
    imul eax, [tunetab+67*4]        ; quiet history bonus: T67 * depth^2 (16), at most T68 (HIST_BMAX)
%else
    shl eax, 4
%endif
    TCMP eax, 68, HIST_BMAX
    jle .b1
    LOADT eax, 68, HIST_BMAX
.b1:mov [rbp+L_BONUS], eax
    mov edi, [rbp+L_MV]
    mov esi, eax
    call hist_update
%if CONTHIST
%if CH_BL
%ifdef TUNE
    mov eax, [rbp+L_DEPTH]
    imul eax, [tunetab+65*4]        ; continuation bonus: linear in depth (T65 CH_BA, T66 CH_BMAX)
%else
    imul eax, [rbp+L_DEPTH], CH_BA  ; continuation bonus: linear in depth
%endif
    TCMP eax, 66, CH_BMAX
    jle .cb1
    LOADT eax, 66, CH_BMAX
.cb1:mov [rbp+L_TMP2], eax
%else
    mov eax, [rbp+L_BONUS]
    mov [rbp+L_TMP2], eax
%endif
    mov edi, [rbp+L_MV]
    mov esi, [rbp+L_TMP2]
    call ch_update
%endif
    xor ebx, ebx
.mal:
    cmp ebx, [rbp+L_NQ]
%if CAPHIST
    jge .capmal
%else
    jge .store
%endif
    mov edi, [rbp+rbx*4+L_QUIETS]
    mov esi, [rbp+L_BONUS]
    neg esi
    call hist_update
%if CONTHIST
    mov edi, [rbp+rbx*4+L_QUIETS]
    mov esi, [rbp+L_TMP2]
    neg esi
    call ch_update
%endif
    inc ebx
    jmp .mal
%if CAPHIST
.capcut:                            ; a capture / promotion cut: its bonus, then the malus of the captures searched before it
    mov edi, [rbp+L_MV]
%ifdef TUNE
    mov esi, [rbp+L_DEPTH]
    imul esi, [tunetab+69*4]        ; capture history bonus: T69 CAP_BA per ply, at most T70 CAP_BMAX
%else
    imul esi, [rbp+L_DEPTH], CAP_BA
%endif
    TCMP esi, 70, CAP_BMAX
    jle .cc1
    LOADT esi, 70, CAP_BMAX
.cc1:call cap_update
.capmal:                            ; (also after a quiet cutoff)
%ifdef TUNE
    mov eax, [rbp+L_DEPTH]
    imul eax, [tunetab+69*4]        ; capture history bonus: T69 CAP_BA per ply, at most T70 CAP_BMAX
%else
    imul eax, [rbp+L_DEPTH], CAP_BA
%endif
    TCMP eax, 70, CAP_BMAX
    jle .cc2
    LOADT eax, 70, CAP_BMAX
.cc2:neg eax
    mov [rbp+L_TMP], eax
    xor ebx, ebx
.cml:
    cmp ebx, [rbp+L_NCAP]
    jge .store
    mov edi, [rbp+rbx*4+L_CAPS]
    mov esi, [rbp+L_TMP]
    call cap_update
    inc ebx
    jmp .cml
%endif
.mdone:
    cmp dword [rbp+L_MCNT], 0
    jne .store
%if SING
    cmp dword [rbp+L_EXCL], 0       ; exclusion search without another legal move: fail low (no mate, no stalemate)
    je .mdn
    mov eax, [rbp+L_ALPHA]
    jmp .retv
.mdn:
%endif
    cmp dword [rbp+L_INCHK], 0
    je .stale
    mov eax, -MATE
    add eax, [ply]
    jmp .retv
.stale:
    xor eax, eax
    jmp .retv
.store:
    STOPTEST
    jne .ret0
%ifdef PCVERIFY
    cmp dword [rbp+L_PCW], 0        ; test build: ProbCut wanted to cut here - did the full search fail high?
    je .pcv0
    mov eax, [rbp+L_BEST]
    cmp eax, [rbp+L_BETA]
    jl .pcvf
    PCCNT 4
    jmp .pcv0
.pcvf:
    PCCNT 5
    PCDCNT 9
.pcv0:
%endif
%if SING
    cmp dword [rbp+L_EXCL], 0       ; exclusion search: neither the table nor the correction history learn from it
    je .sto
    mov eax, [rbp+L_BEST]
    jmp .retv
.sto:
%endif
    mov eax, [rbp+L_BEST]
    mov ecx, 1
    cmp eax, [rbp+L_BETA]
    jge .fl
    mov ecx, 3
    cmp eax, [rbp+L_OALPHA]
    jg .fl
    mov ecx, 2
.fl:
%if EV_CORR
    push rax                        ; pawn correction history update (see EV_CORR in p1_data.asm)
    push rcx
    mov esi, [ply]
    mov esi, [sevals+rsi*4]         ; the static eval of this node (NOEVAL in check)
    cmp esi, NOEVAL
    je .cun
    mov edx, [rbp+L_BESTMV]
    test edx, edx
    jz .cu0
    shr edx, 7
    and edx, 127
    cmp byte [board+rdx], 0
    jne .cun                        ; the best move captures
.cu0:mov edx, eax
    neg edx
    cmovs edx, eax
    cmp edx, MATEB-MAXPLY
    jge .cun
    mov edx, eax
    sub edx, esi                    ; best - eval
    jz .cun
    jg .cup
    cmp ecx, 1
    je .cun                         ; lower bound below the eval: no information
    jmp .cuu
.cup:cmp ecx, 2
    je .cun                         ; upper bound above the eval
.cuu:mov esi, [rbp+L_DEPTH]
    mov eax, 8
    cmp esi, eax
    cmovg esi, eax
    imul edx, esi
    mov eax, CORR_BMAX
    cmp edx, eax
    cmovg edx, eax
    neg eax
    cmp edx, eax
    cmovl edx, eax                  ; bonus
    push rdi
    call corr_ptr
    movsx esi, word [rdi]
    mov eax, edx
    neg eax
    cmovs eax, edx
    imul eax, esi
    push rdx
    cdq
    mov ecx, CORR_LIMIT
    idiv ecx
    pop rdx
    add esi, edx
    sub esi, eax
    mov eax, CORR_LIMIT
    cmp esi, eax
    cmovg esi, eax
    neg eax
    cmp esi, eax
    cmovl esi, eax
    mov [rdi], si
    pop rdi
.cun:pop rcx
    pop rax
%endif
    mov edx, eax
    cmp edx, MATEB
    jle .s1
    add edx, [ply]
    jmp .s2
.s1:cmp edx, -MATEB
    jge .s2
    sub edx, [ply]
.s2:mov rax, [hashk]
    mov edi, [rbp+L_BESTMV]
%if EVCACHE
    mov esi, [rbp+L_TTEV]
    shl esi, 16
    or edi, esi
%endif
    mov esi, [rbp+L_DEPTH]
%ifdef SEVERIFY
    cmp dword [rbp+L_EXCL], 0       ; test build: an exclusion search never stores
    jne .sevf10
%endif
    call tt_store
    mov eax, [rbp+L_BEST]
    jmp .retv
%ifdef SEVERIFY
.sevf8:
    mov edi, 8
    jmp .sevx
.sevf9:
    mov edi, 9
    jmp .sevx
.sevf10:
    mov edi, 10
    jmp .sevx
.sevf11:
    mov edi, 11
.sevx:
    mov eax, 231                    ; exit_group
    syscall
%endif
.retstatic:
    call eval
    EVADJ
    jmp .retv
.ret0:
    xor eax, eax
.retv:
    add rsp, SFRAME
    pop r15
    pop r14
    pop r13
    pop r12
    pop rbx
    pop rbp
    ret

; ============================================================
; go searchmoves helpers
; ============================================================
; root_filter(rdi = move list, ecx = count) -> eax = new count; the list is compacted in place.
; clobbers ecx, edx, r8d, r9d
root_filter:
    xor eax, eax                ; moves kept
    xor edx, edx                ; read index
.rf:cmp edx, ecx
    jae .rd
    mov r8d, [rdi+rdx*4]
    inc edx
    xor r9d, r9d
.rs:cmp r9d, [nsm]
    jae .rf                     ; not listed: drop it
    cmp r8d, [smoves+r9*4]
    je .rk
    inc r9d
    jmp .rs
.rk:mov [rdi+rax*4], r8d
    inc eax
    jmp .rf
.rd:ret

; in_smoves(edi = move) -> eax = 1 if the move may be played at the root (listed, or no list).  clobbers ecx
in_smoves:
    mov ecx, [nsm]
    test ecx, ecx
    jz .yes
.l: cmp edi, [smoves+rcx*4-4]
    je .yes
    dec ecx
    jnz .l
    xor eax, eax
    ret
.yes:
    mov eax, 1
    ret

; ============================================================
; legal_info -> eax = first legal move (0 if none), edx = number of legal moves
; (only moves listed in go searchmoves count when a list is active)
; ============================================================
legal_info:
    push rbx
    push rbp
    push r12
    push r13
    sub rsp, MOVEBUF
    mov rdi, rsp
    xor esi, esi
    call gen
    mov rbx, rax
    mov rbp, rsp
    xor r12d, r12d
    xor r13d, r13d
.l: cmp rbp, rbx
    jae .d
    mov edi, [rbp]
    add rbp, 4
    call in_smoves
    test eax, eax
    jz .l
    mov edi, [rbp-4]
    call domove
    push rax
    call undomove
    pop rax
    test eax, eax
    jz .l
    test r12d, r12d
    jnz .c
    mov r12d, [rbp-4]
.c: inc r13d
    jmp .l
.d: mov eax, r12d
    mov edx, r13d
    add rsp, MOVEBUF
    pop r13
    pop r12
    pop rbp
    pop rbx
    ret

; ============================================================
; principal variation (triangular table).  Row p of pvtab holds the line found at ply p, stored at the
; absolute indices p .. pvlen[p]-1; every search() call starts its line empty (pvlen[p] = p).
; None of this feeds back into the search: node counts and move choice are unchanged.
; ============================================================
; pv_update(ecx = move that raised alpha at this node; [ply] = this node's ply): line = move + child's line
; keeps rax, rcx
pv_update:
    push rax
    push rcx
    mov eax, [ply]
    imul r8d, eax, MAXPLY
    lea r8, [pvtab+r8*4]            ; row of this ply
    mov [r8+rax*4], ecx
    lea r9, [r8+MAXPLY*4]           ; row of the child
    mov edx, [pvlen+rax*4+4]        ; end of the child's line
    lea r10d, [rax+1]
.c: cmp r10d, edx
    jge .d
    mov r11d, [r9+r10*4]
    mov [r8+r10*4], r11d
    inc r10d
    jmp .c
.d: mov [pvlen+rax*4], edx
    pop rcx
    pop rax
    ret

; pv_saveroot: copy row 0 (the new best line at the root) to rpv.  keeps rax, rcx
pv_saveroot:
    push rax
    push rcx
    push rsi
    push rdi
    mov ecx, [pvlen]                ; row 0 starts at index 0: its length is pvlen[0]
    cmp ecx, MAXPLY
    jbe .l
    mov ecx, MAXPLY
.l: mov [rpvlen], ecx
    mov esi, pvtab
    mov edi, rpv
    rep movsd
%if PV_FINAL
    inc dword [rpvgen]
%endif
    pop rdi
    pop rsi
    pop rcx
    pop rax
    ret

%if HELPER
print_currmove:                 ; (search calls it at the root; a helper thread prints nothing)
    ret
%else
; ob_move_made(edi = move): print a move that domove has just applied.  The from-square is empty by now, so the
; moving piece is put back from the undo record for the duration of ob_move (promotion letter).  keeps rbx, rbp, r12
ob_move_made:
    mov eax, [gply]
    dec eax
    shl eax, 5
    movzx edx, byte [ustack+rax+28]
    mov ecx, edi
    and ecx, 127
    mov [board+rcx], dl
    push rcx
    call ob_move
    pop rcx
    mov byte [board+rcx], 0
    ret

; print_currmove(edi = move just made at the root, esi = its number): UCI only, after 3 s of search
print_currmove:
    cmp dword [uci_mode], 0
    je .r
    push rbx
    push rbp
    mov ebx, edi
    mov ebp, esi
    call gettime_ms
    sub rax, [starttime]
    cmp rax, 3000
    jbe .o
    OBS s_info, 11
    mov eax, [itdepth]
    call ob_num
    OBS s_currmove, 10
    mov edi, ebx
    call ob_move_made
    OBS s_cmnum, 16
    mov eax, ebp
    call ob_num
    mov al, 10
    call ob_ch
    call ob_flush
.o: pop rbp
    pop rbx
.r: ret

; tt_hashfull -> eax = permille of the table in use by the current search (sample: first 1000 buckets = 2000 entries,
; counted when the entry belongs to the current search age)
tt_hashfull:
    mov r8, [ttbase]
    movzx r9d, byte [ttage]
    xor ecx, ecx
    xor edx, edx
%ifdef TTPLAIN
.l: cmp qword [r8], 0           ; (test build) plain key
%else
.l: cmp qword [r8+8], 0         ; empty entry = data 0 (the key field holds key XOR data; an entry in use always has a bound 1..3 in its data)
%endif
    je .n
    movzx eax, byte [r8+15]
    shr eax, 2
    cmp eax, r9d
    jne .n
    inc ecx
.n: add r8, 16
    inc edx
    cmp edx, 2000
    jb .l
    mov eax, ecx
    shr eax, 1
    ret

%if UNIT == 0
; move_is_legal(edi = move) -> eax = 1 if it is a legal move in the current position (generated, king not left in
; check).  The position is unchanged on return.
move_is_legal:
    push rbx
    push rbp
    push r12
    push r13
    sub rsp, MOVEBUF
    mov r12d, edi
    mov rdi, rsp
    xor esi, esi
    call gen
    mov rbx, rax
    mov rbp, rsp
    xor r13d, r13d
.l: cmp rbp, rbx
    jae .d
    cmp [rbp], r12d
    lea rbp, [rbp+4]
    jne .l
    mov edi, r12d
    call domove
    mov r13d, eax
    call undomove
.d: mov eax, r13d
    add rsp, MOVEBUF
    pop r13
    pop r12
    pop rbp
    pop rbx
    ret
%endif                          ; UNIT == 0 (move_is_legal)

; print_pv: the root line rpv (moves are played on the board to print them correctly, then taken back).
; Falls back to the bare best move when rpv does not belong to it.
print_pv:
    push rbx
    push rbp
    push r12
    xor ebp, ebp                    ; moves played
    mov r12d, [rpvlen]
    test r12d, r12d
    jz .fb
    mov eax, [rpv]
    cmp eax, [rootbest]
    jne .fb
.l: cmp ebp, r12d
    jae .u
    mov edi, [rpv+rbp*4]
    mov ebx, edi
    call domove
    test eax, eax
    jnz .ok
    call undomove                   ; cannot happen (the line was played during the search): stop here
    jmp .u
.ok:test ebp, ebp
    jz .f
    mov al, ' '
    call ob_ch
.f: mov edi, ebx
    call ob_move_made
    inc ebp
    jmp .l
.u: test ebp, ebp
    jz .fb
.ul:call undomove
    dec ebp
    jnz .ul
    jmp .x
.fb:mov edi, [rootbest]
    call ob_move
.x: pop r12
    pop rbp
    pop rbx
    ret

; ============================================================
; print_info(edi = depth, esi = score)   (UCI mode only)
; ============================================================
print_info:
    cmp dword [uci_mode], 0
    je .r0
%if PV_FINAL
    push rax
    mov eax, [rpvgen]
    mov [lastpvgen], eax            ; the root line of this info line (think's .done compares)
    pop rax
%endif
    push rbx
    push rbp
    push r12
    mov ebx, edi
    mov ebp, esi
    OBS s_info, 11
    mov eax, ebx
    call ob_num
    OBS s_seldepth, 10
    mov eax, [seldep]
    call ob_num
    cmp ebp, MATEB
    jg .mate
    cmp ebp, -MATEB
    jl .mated
    OBS s_score_cp, 10
    movsxd rax, ebp
    call ob_num
    jmp .rest
.mate:
    OBS s_score_mt, 12
    mov eax, MATE
    sub eax, ebp
    inc eax
    shr eax, 1
    call ob_num
    jmp .rest
.mated:
    OBS s_score_mt, 12
    mov eax, MATE
    add eax, ebp
    inc eax
    shr eax, 1
    neg rax
    call ob_num
.rest:
    NODES_ALL                   ; total over all threads (Threads > 1), read once: nodes and nps of the line agree
    mov rbx, rax                ; (rbx: the depth is printed)
    call gettime_ms
    sub rax, [starttime]
    mov r12, rax
    OBS s_nodes, 7
    mov rax, rbx
    call ob_num
    OBS s_nps, 5
    mov rax, rbx
    imul rax, 1000
    mov rcx, r12                ; nps = nodes * 1000 / time, time at least 1 ms
    test rcx, rcx
    jnz .tm
    inc ecx
.tm:xor edx, edx
    div rcx
    call ob_num
    OBS s_hashfull, 10
    call tt_hashfull
    call ob_num
    OBS s_tbhits, 9
    OBS s_time, 6
    mov rax, r12
    call ob_num
    OBS s_pv, 4
    call print_pv
    mov al, 10
    call ob_ch
    call ob_flush
    pop r12
    pop rbp
    pop rbx
.r0:ret

%endif                          ; HELPER == 0 (ob_move_made .. print_info: everything that prints)

%if TM_ANY && HELPER == 0
; ============================================================
; Dynamic time management (main thread, real clock only).  After every completed iteration think asks for a factor F (1/1024; 1024 = the plain
; rule "stop when elapsed >= softlim / 2"): the search stops when  elapsed * 2 * 1024 >= softlim * F.  Components: TM_STAB stable best move -> shorter, TM_INST best move changes -> longer,
; TM_FALL score falling against the previous iteration / the previous search -> longer, TM_EFFORT one root move took >= 93 % of the iteration's nodes -> shorter.
; tm_iter: ebp = score of the iteration just completed, r12d = its depth  ->  eax = F.  Updates the bookkeeping in S_*.  clobbers rcx, rdx.
; ============================================================
tm_iter:
    push rbx
    mov ebx, 1024
    mov eax, [rootbest]
    mov ecx, [S_TOTBMC]
    shr ecx, 1                          ; the count of best-move changes ages by half every iteration
    cmp r12d, 2
    jl .st1                             ; depth 1: no statistics
    cmp eax, [S_ITBEST0]
    je .same
    add ecx, 16
    mov dword [S_STAB], 0
    jmp .st1
.same:
    inc dword [S_STAB]
.st1:
    mov [S_TOTBMC], ecx
    mov [S_ITBEST0], eax
%if TM_STAB
    cmp dword [S_STAB], TM_STABN
    jl .nst
    LOADT eax, 14, 780
    imul ebx, eax
    shr ebx, 10
.nst:
%endif
%if TM_INST
    mov eax, [S_TOTBMC]
%if SMP && SMP_TMBMC
    call smp_bmc                        ; the mean count of the threads of this search
%endif
    LOADT edx, 16, 1100
    imul eax, edx
    shr eax, 4
    add eax, 1024
    imul ebx, eax
    shr ebx, 10
%endif
%if TM_FALL
    cmp r12d, 4
    jl .nfall
    mov eax, 1024
    cmp dword [S_PSOK], 0
    je .nops
    mov edx, [S_PS]
    sub edx, ebp                        ; previous search's score - this score (positive = it got worse)
    LOADT ecx, 12, 24
    imul edx, ecx
    add eax, edx
.nops:
    mov edx, [S_PREVIT]
    sub edx, ebp
    LOADT ecx, 13, 12
    imul edx, ecx
    add eax, edx
    cmp eax, 590
    jge .f1
    mov eax, 590
.f1:cmp eax, 1770
    jle .f2
    mov eax, 1770
.f2:imul ebx, eax
    shr ebx, 10
.nfall:
%endif
%if TM_EFFORT
    cmp r12d, 10
    jl .neff
    mov rax, [nodes]
    sub rax, [S_ITSTART]
    imul rax, rax, 93
    mov rdx, [S_ROOTEFF]
    imul rdx, rdx, 100
    cmp rdx, rax
    jl .neff
    LOADT eax, 15, 780
    imul ebx, eax
    shr ebx, 10
.neff:
%endif
    mov rax, [nodes]
    mov [S_ITSTART], rax
    mov qword [S_ROOTEFF], 0
    mov [S_PREVIT], ebp
    mov [tm_prev], ebp
    mov dword [tm_prevok], 1
    cmp ebx, 300
    jae .c1
    mov ebx, 300
.c1:cmp ebx, 4096
    jbe .c2
    mov ebx, 4096
.c2:mov eax, ebx
    pop rbx
    ret
%endif

; ============================================================
; think: iterative deepening.  Result in [bestmv] (0 = no legal move)
; limits in [maxdepth], [softlim], [hardlim], [canstop]
; ============================================================
think:
    push rbx
    push rbp
    push r12
    push r13
    push r14
    push r15
%if HELPER == 0
    mov eax, [ttage]                ; search age 1..63, never 0 (an empty entry has age 0, so it must never look "current")
    inc eax
    cmp eax, 63
    jbe .tga
    mov eax, 1
.tga:mov [ttage], eax
%endif
%ifdef TTFUZZ
    mov dword [ttfz], 2463534242    ; (test build) every search starts the fuzz sequence anew: searches stay reproducible (tests/t_uci.py)
%endif
%if HELPER == 0
    mov dword [stopflag], 0
%endif
    mov qword [nodes], 0
    mov dword [ply], 0
    mov dword [rpvlen], 0
%if PV_FINAL
    mov dword [rpvgen], 0
    mov dword [lastpvgen], 0
%endif
    mov dword [S_NMPMIN], 0         ; no verification search running
%if NMP_VER && EVALDIFF_HIST
    mov dword [S_NMPVP], 0
%endif
%if TM_ANY && HELPER == 0
    mov dword [S_STAB], 0
    mov dword [S_TOTBMC], 0
    mov dword [S_PREVIT], 0
    mov qword [S_ITSTART], 0
    mov qword [S_ROOTEFF], 0
    mov eax, [tm_prev]
    mov [S_PS], eax
    mov eax, [tm_prevok]
    mov [S_PSOK], eax
%endif
    mov ecx, MAXPLY*2
    xor eax, eax
    mov edi, killers
    rep stosd
%if TM_INCD && HELPER == 0
    mov dword [S_SAC], 0
    mov dword [S_INCD], 1
%endif
%if CONTHIST
    mov qword [chp], chdummy        ; plies -2 and -1: no previous move
    mov qword [chp+8], chdummy
%if HELPER == 0
    lock or dword [chdirty], 1 << UNIT  ; this unit writes its chtab (ucinewgame clears it); SMP helpers are marked by smp_start.  lock: in the
                                    ; UNITTEST builds all units run think at the same time (a plain or could lose bits: tests/t_units_conc)
%endif
%endif
%if HELPER == 0
    call gettime_ms
    mov [starttime], rax
%endif
    call legal_info
%if HELPER == 0
    test eax, eax
    jnz .lok
    cmp dword [nsm], 0
    je .lok
    mov dword [nsm], 0          ; none of the listed moves is legal: ignore the restriction (the main thread has done this before a helper starts)
    call legal_info
%endif
.lok:
    mov [rootbest], eax
%if TM_ANY && HELPER == 0
    mov [S_ITBEST0], eax
%endif
%if HELPER && SMP_TMBMC && TM_INST
    mov [S_ITBEST0], eax            ; SMP_TMBMC: the helper keeps its own best-move-change count (smp_push has cleared S_TOTBMC)
%endif
    test eax, eax
    jz .done
%if HELPER == 0
    cmp edx, 1
    jne .go
    cmp dword [canstop], 0
    jne .done
%endif
.go:
%if SMP && UNIT == 0
    call smp_start              ; Lazy SMP: Threads - 1 helper threads search this position too (after every early exit above; see p7_units.asm)
%endif
    xor r12d, r12d              ; depth
    xor r13d, r13d              ; last score
.iter:
    inc r12d
    cmp r12d, [maxdepth]
%if HELPER
    jg .done                    ; a helper has no "go infinite" wait: it ends when the main thread stops it (or at maxdepth)
%else
    jg .waitinf
%endif
    mov [itdepth], r12d
%if TM_INCD && HELPER == 0
    ; TM_INCD: the depth of this iteration.  When the last iteration ended past half of the time to the stop, the next one would rarely finish:
    ; then every new iteration raises the search-again counter and searches at max(1, depth - 3 * (counter + 1) / 4) (the
    ; info lines and the time-management statistics keep counting the iterations in r12d)
    cmp dword [S_INCD], 0
    jne .incd
    inc dword [S_SAC]
.incd:
    mov eax, [S_SAC]
    lea eax, [rax*2+rax+3]
    shr eax, 2
    mov ecx, r12d
    sub ecx, eax
    cmp ecx, 1
    jge .sdok
    mov ecx, 1
.sdok:
    mov [S_SD], ecx
%endif
    mov dword [seldep], 0
    mov r14d, -INF
    mov r15d, INF
%if HELPER
%ifdef TUNE
    mov ebx, [tunetab+73*4]     ; aspiration delta: T73 (ASP_W) + (unit mod 8) * SMP_ASPSTEP, at least 1 (0 would never widen)
    add ebx, (UNIT & 7) * SMP_ASPSTEP
    cmp ebx, 1
    jge .aw1
    mov ebx, 1
.aw1:
    cmp r12d, 5
    jl .asp
    mov r14d, r13d
    sub r14d, ebx
    lea r15d, [r13+rbx]
%else
%assign HASPW ASP_W + (UNIT & 7) * SMP_ASPSTEP  ; helper aspiration half-width (diversity between the threads, build switch SMP_ASPSTEP)
    mov ebx, HASPW              ; aspiration delta
    cmp r12d, 5
    jl .asp
    lea r14d, [r13-HASPW]
    lea r15d, [r13+HASPW]
%endif
%else
%ifdef TUNE
    mov ebx, [tunetab+73*4]     ; aspiration delta (T73, ASP_W), at least 1 (0 would never widen)
    cmp ebx, 1
    jge .aw0
    mov ebx, 1
.aw0:
    cmp r12d, 5
    jl .asp
    mov r14d, r13d
    sub r14d, ebx
    lea r15d, [r13+rbx]
%else
    mov ebx, ASP_W              ; aspiration delta
    cmp r12d, 5
    jl .asp
    lea r14d, [r13-ASP_W]
    lea r15d, [r13+ASP_W]
%endif
%endif
.asp:
%if TM_INCD && HELPER == 0
    mov edi, [S_SD]
%else
    mov edi, r12d
%endif
    mov esi, r14d
    mov edx, r15d
    xor ecx, ecx
    mov r8d, -1
    xor r9d, r9d                    ; the root is a PV node: cutNode 0
    call search
    STOPTEST
    jne .done
    mov ebp, eax
    cmp ebp, r14d
    jg .nlow
    cmp r14d, -INF
    jle .nlow
    sub r14d, ebx
    cmp r14d, -INF
    jge .lw
    mov r14d, -INF
.lw:add ebx, ebx
    cmp ebx, 400
    jle .asp
    mov r14d, -INF
    mov r15d, INF
    jmp .asp
.nlow:
    cmp ebp, r15d
    jl .ok
    cmp r15d, INF
    jge .ok
    add r15d, ebx
    cmp r15d, INF
    jle .hw
    mov r15d, INF
.hw:add ebx, ebx
    cmp ebx, 400
    jle .asp
    mov r14d, -INF
    mov r15d, INF
    jmp .asp
.ok:
    mov r13d, ebp
%if HELPER == 0
    mov edi, r12d
    mov esi, ebp
    call print_info
%endif
%if HELPER && SMP_TMBMC && TM_INST
    mov ecx, [S_TOTBMC]             ; SMP_TMBMC: the same count as tm_iter keeps for the main thread (halves every iteration, +16 for a new best
    shr ecx, 1                      ; root move from depth 2 on); the main thread averages it over the threads (smp_bmc)
    mov eax, [rootbest]
    cmp r12d, 2
    jl .hb1
    cmp eax, [S_ITBEST0]
    je .hb1
    add ecx, 16
.hb1:
    mov [S_TOTBMC], ecx
    mov [S_ITBEST0], eax
%endif
    mov eax, ebp                ; proven forced mate (either side): stop iterating
    cdq
    xor eax, edx
    sub eax, edx
    cmp eax, MATEB
    jle .nomate
    mov ecx, MATE
    sub ecx, eax
%if TM_INCD && HELPER == 0
    cmp [S_SD], ecx                 ; the depth that was searched
%else
    cmp r12d, ecx
%endif
%if HELPER
    jge .done
%else
    jge .waitinf
%endif
.nomate:
%if HELPER
    jmp .iter                   ; a helper has no clock: it runs until the main thread stops it
%else
    cmp dword [canstop], 0
    je .iter
%if TM_ANY
    cmp dword [tmon], 0
    je .tm0
    call tm_iter                ; eax = time factor F (1/1024)
    mov [S_TMF], eax            ; gettime_ms clobbers rcx (syscall / its own use): keep F in memory
    call gettime_ms
    sub rax, [starttime]
    imul rax, rax, 2048         ; elapsed * 2 * 1024
    mov rdx, [softlim]
    mov ecx, [S_TMF]
    imul rdx, rcx               ; softlim * F
    cmp rax, rdx
%if TM_INCD
    jge .done
    add rax, rax                ; TM_INCD: at most half of the time to the stop is gone -> the next iteration goes one ply deeper
    cmp rax, rdx
    setle al
    movzx eax, al
    mov [S_INCD], eax
    jmp .iter
%else
    jl .iter
    jmp .done
%endif
.tm0:
%endif
    call gettime_ms
    sub rax, [starttime]
    add rax, rax
    cmp rax, [softlim]
    jl .iter
    jmp .done
.waitinf:                       ; search finished by itself: after 'go infinite' wait for stop/quit
%if SMP && UNIT == 0
    call smp_stop               ; (the helper threads have nothing left to do: stop them and wait for them; idempotent)
%endif
    cmp dword [infflag], 0
    je .done
.wl:STOPTEST
    jne .done
    cmp dword [quitflag], 0
    jne .done
    mov dword [polltmo], 100
    call poll_input
    mov dword [polltmo], 0
    jmp .wl
%endif
.done:
%if HELPER == 0
%if SMP && UNIT == 0
    call smp_stop               ; EVERY way out of think passes here (or through .waitinf above): no helper thread may outlive the search
    mov r12d, [smp_last]        ; the helpers of this search (smp_vote clears smp_last): the node count of a final info line below
%if SMP_VOTE == 0
    mov dword [smp_last], 0     ; (no vote that clears it: a later single-thread search must not count these helpers)
%endif
%endif
%if SMP && UNIT == 0 && SMP_VOTE
    call smp_vote               ; the result of the search from all its threads (may replace rootbest / rootscore / rpv)
%endif
%if PV_FINAL
    mov eax, [rpvgen]           ; the root line changed after the last info line (the search ended inside an iteration): print the line of rootbest
    cmp eax, [lastpvgen]
    je .pvf
    cmp dword [rpvlen], 0
    je .pvf
    mov edi, [itdepth]
    mov esi, [rootscore]
%if SMP && UNIT == 0
    mov [smp_run], r12d         ; nodes / nps of all threads of the search, as in every other info line (the helpers have ended)
%endif
    call print_info
%if SMP && UNIT == 0
    mov dword [smp_run], 0
%endif
.pvf:
%endif
    mov eax, [rootbest]
    mov [bestmv], eax
%endif
    pop r15
    pop r14
    pop r13
    pop r12
    pop rbp
    pop rbx
    ret
