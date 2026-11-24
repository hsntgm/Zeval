
; ============================================================================================
; Thread units.  See p1_data.asm for the idea: the search code exists once per unit, every unit with its own
; state block (the thread-local symbols, label tlb_<k>).  This file holds
;   * the unit table (unit 0 only): the address of every unit's state block, used to reach another unit's block
;     (clear_tables clears the history of all units; unit_import / unit_export copy between a helper unit and unit 0),
;   * unit_import / unit_export (every unit): the hand-over of the position to a helper unit and of the result back,
;   * the build -DUNITTEST test hook (off in the release): run a search in a helper thread of unit k and compare with unit 0.
; Lazy SMP builds on exactly these pieces.
; ============================================================================================

%if UNIT == 0
%if NUNITS > 1
%assign unit_i 1
%rep NUNITS-1
extern tlb_ %+ unit_i
%assign unit_i unit_i+1
%endrep
%endif
section .data
align 8
unit_tab:                       ; address of the state block of every unit (index = unit number)
%assign unit_i 0
%rep NUNITS
    dq tlb_ %+ unit_i
%assign unit_i unit_i+1
%endrep
section .text
%endif

; ============================================================================================
; Lazy SMP (release builds: SMP = 1, see p1_data.asm).  Threads = n: the main thread (unit 0) searches as
; with one thread and n - 1 helper threads search the same position with the units 1 .. n-1 (HELPER flavour of the search code).  They share only the
; transposition table (lock-free) and the stop flags; every helper has its own state block, killers and history.
;   think (unit 0) calls   smp_start  at its .go point (after every early exit: no legal move, single move),
;                          smp_stop   at .waitinf and at .done = on EVERY way out of think (idempotent): sets hstop and waits for every helper,
;   smp_nodes              total node count over the running threads (go nodes in check_time, nodes / nps in print_info).
; The root position is PUSHED into the helper blocks by the main thread before the clone (smp_push): a helper never reads unit 0's block while
; unit 0 is already making moves in it.  SMP_POOL 0: helper threads are created for every search and joined at its end; SMP_POOL 1 (default):
; they are created once and wait between searches (see the SMP_POOL block below).  Their stacks
; (1 MB + guard page) are mapped on first use and kept.  A helper never writes any shared variable except the table (tests/t_smp.py audits it).
; Test build -DSMPTEST (tests/t_smp_equiv.py): smp_stop does not set hstop (the helpers run to their own end, so use depth-limited searches) and
; prints one line "info string smpunit <k> <nodes> <rootscore> <rootbest> <depth>" for unit 0 and for every helper.
; ============================================================================================
%if SMP
%if UNIT == 0
%assign unit_i 1
%rep NUNITS-1
extern helper_main_ %+ unit_i
%assign unit_i unit_i+1
%endrep
section .data
align 8
helper_main_tab:                ; entry point of the helper thread of every unit (unit 0 has none)
    dq 0
%assign unit_i 1
%rep NUNITS-1
    dq helper_main_ %+ unit_i
%assign unit_i unit_i+1
%endrep
%ifdef SMPTEST
s_smpunit:  db "info string smpunit "
%endif
%ifdef VOTETEST
s_vote:     db "info string vote "
s_voteb:    db "info string votebest "
%endif
section .text

; smp_nodes -> rax = nodes of this thread + the nodes of every running helper thread (each counter is an aligned qword: a racy read gives a slightly
; old value, never a torn one).  Clobbers rax only.
smp_nodes:
    mov rax, [nodes]
    cmp dword [smp_run], 0
    je .r
    push rcx
    push rdx
    push r8
    mov ecx, [smp_run]
.l: bsf edx, ecx
    jz .d
    mov r8, [unit_tab+rdx*8]
    add rax, [r8 + nodes - tlb_0]
    btr ecx, edx
    jmp .l
.d: pop r8
    pop rdx
    pop rcx
.r: ret

; smp_push(edi = k): hand the root position (board .. bbc and ustack[0 .. gply]) to unit k and clear its node counter.  Unit k is not running.
; Clobbers rax rcx rdx rsi rdi.
smp_push:
    mov edi, edi
    mov rdx, [unit_tab+rdi*8]
    lea rdi, [rdx + pos_start - tlb_0]
    mov esi, pos_start
    mov ecx, (pos_end - pos_start) / 8
    rep movsq
    mov eax, [gply]                 ; ustack[0 .. gply] (index = absolute game ply, isdraw scans it for repetitions)
    inc eax
    shl eax, 5
    mov ecx, eax
    lea rdi, [rdx + ustack - tlb_0]
    mov esi, ustack
    rep movsb
    mov qword [rdx + nodes - tlb_0], 0  ; the main thread adds the counters up: the value of the last search must not be seen
%if SMP_TMBMC && TM_INST
    mov dword [rdx + S_TOTBMC - tlb_0], 0   ; nor its best-move-change count (smp_bmc may read it before the helper has started)
%endif
    ret

%if SMP_TMBMC && TM_INST
; smp_bmc(eax = the main thread's best-move-change count) -> eax = the mean count over the main thread and the helpers of this search (TM_INST of
; tm_iter: the summed changes of all threads divided by their number).  Each helper keeps its count
; in its own block (think, after every iteration); an aligned dword: a racy read is old, never torn.  Clobbers rcx, rdx.
smp_bmc:
    mov ecx, [smp_run]
    test ecx, ecx
    jz .r
    push r8
    push r9
    mov r9d, 1                      ; threads counted
.l: bsf edx, ecx
    jz .d
    btr ecx, edx
    mov r8, [unit_tab+rdx*8]
    add eax, [r8 + S_TOTBMC - tlb_0]
    inc r9d
    jmp .l
.d: xor edx, edx
    div r9d
    pop r9
    pop r8
.r: ret
%endif

%if SMP_POOL == 0
%ifndef WIN
; smp_spawn(edi = k) -> eax = 0 on success: raw clone of the helper thread of unit k, entry helper_main_<k>.  Stack: 1 MB + a guard page at the
; low end, mapped the first time and kept.  The thread ends with exit (60) = only itself; the kernel clears its tid word and wakes the futex.
; Clobbers rax rcx rdx rsi rdi r8 r9 r10 r11.
smp_spawn:
    push rbx
    push r12
    mov ebx, edi
    mov r12, [smp_stk+rbx*8]
    test r12, r12
    jnz .have
    mov eax, 9                      ; mmap(0, 1 MB + guard, RW, PRIVATE|ANONYMOUS, -1, 0)
    xor edi, edi
    mov esi, 0x101000
    mov edx, 3
    mov r10d, 0x22
    mov r8, -1
    xor r9d, r9d
    syscall
    cmp rax, -4096
    ja .fail
    mov r12, rax
    mov [smp_stk+rbx*8], r12
    mov eax, 10                     ; mprotect(base, 4096, PROT_NONE): guard page
    mov rdi, r12
    mov esi, 4096
    xor edx, edx
    syscall
.have:
    lea rsi, [r12+0x101000-8]       ; child stack: the entry address on top
    mov rax, [helper_main_tab+rbx*8]
    mov [rsi], rax
    lea rdx, [smp_ctid+rbx*4]
    mov dword [rdx], 0
    mov edi, 0x350F00               ; CLONE_VM|FS|FILES|SIGHAND|THREAD|SYSVSEM|PARENT_SETTID|CHILD_CLEARTID
    mov r10, rdx
    xor r8d, r8d
    mov eax, 56
    syscall
    test rax, rax
    jz .child
    js .fail
    xor eax, eax
    jmp .out
.fail:
    mov eax, 1
.out:
    pop r12
    pop rbx
    ret
.child:
    pop rax
    call rax
    mov eax, 60
    xor edi, edi
    syscall
%else
; smp_spawn(edi = k) -> eax = 0 on success: CreateThread(NULL, 1 MB, helper_main_<k>, NULL, STACK_SIZE_PARAM_IS_A_RESERVATION, NULL) = the call that ran on real
; (the image's stack commit size, 4 MB, is larger than the 1 MB reservation, so Windows reserves and commits 4 MB per thread: commit charge only,
; pages that are not touched take no memory)
; Windows 11 in the unit test hook (conc_spawn).  The handle is kept in smp_hnd[k] for smp_stop.  The engine keeps no 16-byte alignment: aligned here.
; Preserves rbx, rbp, rsi, rdi, r12 .. r15 (Windows ABI); clobbers rax rcx rdx r8 .. r11 (a subset of what the Linux version clobbers).
smp_spawn:
    push rbp
    mov rbp, rsp
    push rbx
    mov ebx, edi
    sub rsp, 72
    and rsp, -16
    xor ecx, ecx
    mov edx, 0x100000
    mov r8, [helper_main_tab+rbx*8]
    xor r9d, r9d
    mov dword [rsp+32], 0x10000     ; STACK_SIZE_PARAM_IS_A_RESERVATION
    mov qword [rsp+40], 0           ; lpThreadId = NULL
    call CreateThread
    mov [smp_hnd+rbx*8], rax
    test rax, rax
    setz al
    movzx eax, al                   ; 0 = success, 1 = no thread
    mov rbx, [rbp-8]
    mov rsp, rbp
    pop rbp
    ret
%endif

; smp_start: start Threads - 1 helper threads on the position of unit 0 (called once per search from think's .go point).  A helper that cannot be
; started (out of memory) is simply not there: the search goes on with fewer threads.  Keeps rbx, rbp and the registers think holds (r12 .. r15).
smp_start:
    push rbx
    push rbp
    mov dword [smp_run], 0
    mov ebp, [nthreads]
    cmp ebp, 2
    jb .d                           ; Threads = 1 (or never set): nothing to do
    cmp ebp, NUNITS
    jbe .c
    mov ebp, NUNITS
.c: mov dword [hstop], 0            ; no helper is running: safe to clear
    mov ebx, 1
.l: cmp ebx, ebp
    jae .d
    mov edi, ebx
    call smp_push
    mov edi, ebx
    call smp_spawn
    test eax, eax
    jnz .n
    bts dword [smp_run], ebx
%if CONTHIST && CH_SHARED == 0
    bts dword [chdirty], ebx        ; its chtab is written now (ucinewgame clears it; CH_SHARED: the helper writes the main thread's table)
%endif
.n: inc ebx
    jmp .l
.d: pop rbp
    pop rbx
    ret

%ifndef WIN
; smp_stop: stop the helper threads (hstop) and wait until every one of them has ended.  Idempotent; a no-op when none runs.  Keeps rax and
; every register except rcx, rdx, rsi, rdi, r8, r9, r10, r11 (what the syscalls clobber).
smp_stop:
    cmp dword [smp_run], 0
    je .r
    push rbx
    push rax
%ifndef SMPTEST
    mov dword [hstop], 1
%endif
    mov ebx, 1
.k: bt dword [smp_run], ebx
    jnc .nx
.w: mov edx, [smp_ctid+rbx*4]
    test edx, edx
    jz .nx
    lea rdi, [smp_ctid+rbx*4]       ; futex(&tid, FUTEX_WAIT, tid, NULL): returns when the kernel clears the word at the thread's exit
    xor esi, esi
    xor r10d, r10d
    mov eax, 202
    syscall
    jmp .w
.nx:inc ebx
    cmp ebx, NUNITS
    jb .k
%ifdef SMPTEST
    call smp_report
%endif
    mov ecx, [smp_run]
    mov [smp_last], ecx             ; the helpers of this search, for smp_vote
    mov dword [smp_run], 0
    pop rax
    pop rbx
.r: ret
%else
; smp_stop (Windows): hstop, then WaitForSingleObject(handle, INFINITE) + CloseHandle for every helper that was started (no handle is left per go).
; Idempotent; keeps rax and every register except rcx, rdx, r8 .. r11.
smp_stop:
    cmp dword [smp_run], 0
    je .r
    push rbp
    mov rbp, rsp
    push rbx
    push rax
    sub rsp, 48
    and rsp, -16
%ifndef SMPTEST
    mov dword [hstop], 1
%endif
    mov ebx, 1
.k: bt dword [smp_run], ebx
    jnc .nx
    mov rcx, [smp_hnd+rbx*8]
    mov edx, -1                     ; INFINITE: the helper ends as soon as it sees hstop (a few thousand cycles at most)
    call WaitForSingleObject
    mov rcx, [smp_hnd+rbx*8]
    call CloseHandle
    mov qword [smp_hnd+rbx*8], 0
.nx:inc ebx
    cmp ebx, NUNITS
    jb .k
%ifdef SMPTEST
    call smp_report
%endif
    mov ecx, [smp_run]
    mov [smp_last], ecx             ; the helpers of this search, for smp_vote
    mov dword [smp_run], 0
    mov rax, [rbp-16]
    mov rbx, [rbp-8]
    mov rsp, rbp
    pop rbp
.r: ret
%endif
%else                           ; SMP_POOL
; ---- SMP_POOL 1: persistent helper threads.  The helper thread of unit k is created the first time a search needs it and then waits between
; ---- searches instead of ending: smp_start wakes it, smp_stop waits until it has finished its search.  The search itself is the same code
; ---- (helper_main_<k> -> think); only thread creation and the join are replaced by a wake-up and a wait.  The process ends with exit_group /
; ---- ExitProcess, which ends the waiting threads as well.  Handshake (written only by unit 0 code: smp_start / smp_stop and pool_loop):
; ----   Linux:   pool_gen[k]  search generation, raised by smp_start after the push of the position; the thread runs one search per raise
; ----            pool_busy[k] 1 from smp_start until the thread has finished that search (cleared by the thread, futex wake)
; ----            both are private futex words; x86 stores are seen in program order, so the thread that sees the new generation also sees
; ----            the pushed position, and smp_stop that sees busy = 0 also sees every result of the search.
; ----   Windows: two auto-reset events per thread, pool_evgo[k] (smp_start -> thread) and pool_evdone[k] (thread -> smp_stop).
%ifndef WIN
; pool_spawn(edi = k) -> eax = 0 on success: raw clone of the pool thread of unit k, entry pool_loop (edi = k).  Stack as smp_spawn (1 MB + guard
; page, mapped once).  No tid words: the thread never ends.  Clobbers rax rcx rdx rsi rdi r8 r9 r10 r11.
pool_spawn:
    push rbx
    push r12
    mov ebx, edi
    mov r12, [smp_stk+rbx*8]
    test r12, r12
    jnz .have
    mov eax, 9                      ; mmap(0, 1 MB + guard, RW, PRIVATE|ANONYMOUS, -1, 0)
    xor edi, edi
    mov esi, 0x101000
    mov edx, 3
    mov r10d, 0x22
    mov r8, -1
    xor r9d, r9d
    syscall
    cmp rax, -4096
    ja .fail
    mov r12, rax
    mov [smp_stk+rbx*8], r12
    mov eax, 10                     ; mprotect(base, 4096, PROT_NONE): guard page
    mov rdi, r12
    mov esi, 4096
    xor edx, edx
    syscall
.have:
    lea rsi, [r12+0x101000-16]      ; child stack: entry address, then k
    lea rax, [pool_loop]
    mov [rsi], rax
    mov [rsi+8], rbx
    mov edi, 0x50F00                ; CLONE_VM|FS|FILES|SIGHAND|THREAD|SYSVSEM
    xor edx, edx
    xor r10d, r10d
    xor r8d, r8d
    mov eax, 56
    syscall
    test rax, rax
    jz .child
    js .fail
    xor eax, eax
    jmp .out
.fail:
    mov eax, 1
.out:
    pop r12
    pop rbx
    ret
.child:
    pop rax
    pop rdi
    call rax                        ; pool_loop never returns

; pool_loop(edi = k): the life of a pool thread.  Waits for a new generation, runs the helper search of unit k, reports the end, waits again.
pool_loop:
    mov ebx, edi
    xor r12d, r12d                  ; r12d = the generation already searched
.w: mov eax, [pool_gen+rbx*4]
    cmp eax, r12d
    jne .go
    lea rdi, [pool_gen+rbx*4]       ; futex(&gen, FUTEX_WAIT_PRIVATE, seen, NULL): sleeps only while gen is still the old value
    mov esi, 128
    mov edx, r12d
    xor r10d, r10d
    mov eax, 202
    syscall
    jmp .w
.go:mov r12d, eax
    call [helper_main_tab+rbx*8]    ; helper_main_<k>: think (HELPER flavour); keeps rbx, r12
    mov dword [pool_busy+rbx*4], 0
    lea rdi, [pool_busy+rbx*4]      ; futex(&busy, FUTEX_WAKE_PRIVATE, 1)
    mov esi, 129
    mov edx, 1
    mov eax, 202
    syscall
    jmp .w

; pool_wake(ebx = k): a new search for the thread of unit k.  Clobbers rax rcx rdx rsi rdi r10 r11.
pool_wake:
    mov dword [pool_busy+rbx*4], 1
    lock inc dword [pool_gen+rbx*4]
    bt dword [pool_alive], ebx
    jnc .r                          ; not created yet: smp_start creates it, it sees the new generation at once
    lea rdi, [pool_gen+rbx*4]       ; futex(&gen, FUTEX_WAKE_PRIVATE, 1)
    mov esi, 129
    mov edx, 1
    mov eax, 202
    syscall
.r: ret
%else
; pool_spawn(edi = k) -> eax = 0 on success: the two events of unit k (created once) and CreateThread(NULL, 1 MB, pool_loop, k,
; STACK_SIZE_PARAM_IS_A_RESERVATION, NULL); the handle is closed at once (the thread lives on).  Aligns the stack itself (Windows ABI).
; Preserves rbx, rbp, rsi, rdi, r12 .. r15; clobbers rax rcx rdx r8 .. r11.
pool_spawn:
    push rbp
    mov rbp, rsp
    push rbx
    mov ebx, edi
    sub rsp, 72
    and rsp, -16
    cmp qword [pool_evgo+rbx*8], 0
    jne .ev
    xor ecx, ecx                    ; CreateEventA(NULL, auto-reset, not signaled, NULL)
    xor edx, edx
    xor r8d, r8d
    xor r9d, r9d
    call CreateEventA
    test rax, rax
    jz .fail
    mov [pool_evgo+rbx*8], rax
.ev:cmp qword [pool_evdone+rbx*8], 0
    jne .th
    xor ecx, ecx
    xor edx, edx
    xor r8d, r8d
    xor r9d, r9d
    call CreateEventA
    test rax, rax
    jz .fail
    mov [pool_evdone+rbx*8], rax
.th:mov rcx, [pool_evgo+rbx*8]      ; the first search of the new thread
    call SetEvent
    xor ecx, ecx
    mov edx, 0x100000
    lea r8, [pool_loop]
    mov r9, rbx                     ; lpParameter = k
    mov dword [rsp+32], 0x10000     ; STACK_SIZE_PARAM_IS_A_RESERVATION
    mov qword [rsp+40], 0
    call CreateThread
    test rax, rax
    jz .fail
    mov rcx, rax
    call CloseHandle
    xor eax, eax
    jmp .out
.fail:
    mov eax, 1
.out:
    mov rbx, [rbp-8]
    mov rsp, rbp
    pop rbp
    ret

; pool_loop(rcx = k): thread start routine.  Waits for pool_evgo[k], runs the helper search of unit k, signals pool_evdone[k], waits again.
pool_loop:
    push rbx                        ; entry rsp = 8 mod 16: now aligned
    sub rsp, 32                     ; shadow space
    mov ebx, ecx
.w: mov rcx, [pool_evgo+rbx*8]
    mov edx, -1                     ; INFINITE
    call WaitForSingleObject
    call [helper_main_tab+rbx*8]    ; keeps rbx
    mov rcx, [pool_evdone+rbx*8]
    call SetEvent
    jmp .w

; pool_wake(ebx = k): a new search for the thread of unit k (SetEvent; a thread not created yet gets it from pool_spawn).  Keeps rbx.
pool_wake:
    bt dword [pool_alive], ebx
    jnc .r
    push rbp
    mov rbp, rsp
    sub rsp, 32
    and rsp, -16
    mov rcx, [pool_evgo+rbx*8]
    call SetEvent
    mov rsp, rbp
    pop rbp
.r: ret
%endif

; smp_start (SMP_POOL): wake (or create) Threads - 1 helper threads on the position of unit 0.  A helper that cannot be created is not there.
; Keeps rbx, rbp and the registers think holds (r12 .. r15).
smp_start:
    push rbx
    push rbp
    mov dword [smp_run], 0
    mov ebp, [nthreads]
    cmp ebp, 2
    jb .d
    cmp ebp, NUNITS
    jbe .c
    mov ebp, NUNITS
.c: mov dword [hstop], 0            ; no helper is searching: safe to clear
    mov ebx, 1
.l: cmp ebx, ebp
    jae .d
    mov edi, ebx
    call smp_push                   ; the thread of unit k waits: its block may be written
    call pool_wake
    bt dword [pool_alive], ebx
    jc .on
    mov edi, ebx
    call pool_spawn
    test eax, eax
    jnz .n
    bts dword [pool_alive], ebx
.on:bts dword [smp_run], ebx
%if CONTHIST && CH_SHARED == 0
    bts dword [chdirty], ebx        ; its chtab is written now (ucinewgame clears it; CH_SHARED: the helper writes the main thread's table)
%endif
.n: inc ebx
    jmp .l
.d: pop rbp
    pop rbx
    ret

%ifndef WIN
; smp_stop (SMP_POOL): hstop, then wait until every helper of this search has cleared its busy word.  Idempotent; keeps rax and every register
; except rcx, rdx, rsi, rdi, r8, r9, r10, r11.
smp_stop:
    cmp dword [smp_run], 0
    je .r
    push rbx
    push rax
%ifndef SMPTEST
    mov dword [hstop], 1
%endif
    mov ebx, 1
.k: bt dword [smp_run], ebx
    jnc .nx
.w: cmp dword [pool_busy+rbx*4], 0
    je .nx
    lea rdi, [pool_busy+rbx*4]      ; futex(&busy, FUTEX_WAIT_PRIVATE, 1, NULL)
    mov esi, 128
    mov edx, 1
    xor r10d, r10d
    mov eax, 202
    syscall
    jmp .w
.nx:inc ebx
    cmp ebx, NUNITS
    jb .k
%ifdef SMPTEST
    call smp_report
%endif
    mov ecx, [smp_run]
    mov [smp_last], ecx             ; the helpers of this search, for smp_vote
    mov dword [smp_run], 0
    pop rax
    pop rbx
.r: ret
%else
; smp_stop (SMP_POOL, Windows): hstop, then WaitForSingleObject(pool_evdone[k], INFINITE) for every helper of this search.  Idempotent; keeps rax
; and every register except rcx, rdx, r8 .. r11.
smp_stop:
    cmp dword [smp_run], 0
    je .r
    push rbp
    mov rbp, rsp
    push rbx
    push rax
    sub rsp, 48
    and rsp, -16
%ifndef SMPTEST
    mov dword [hstop], 1
%endif
    mov ebx, 1
.k: bt dword [smp_run], ebx
    jnc .nx
    mov rcx, [pool_evdone+rbx*8]
    mov edx, -1
    call WaitForSingleObject
.nx:inc ebx
    cmp ebx, NUNITS
    jb .k
%ifdef SMPTEST
    call smp_report
%endif
    mov ecx, [smp_run]
    mov [smp_last], ecx             ; the helpers of this search, for smp_vote
    mov dword [smp_run], 0
    mov rax, [rbp-16]
    mov rbx, [rbp-8]
    mov rsp, rbp
    pop rbp
.r: ret
%endif
%endif                          ; SMP_POOL
%if SMP_VOTE
; smp_vote (unit 0, think's .done, after smp_stop: no helper runs): pick the result of the search from all threads that took part instead of the
; main thread alone (Lazy SMP "best thread").  Candidates: the main thread and every helper
; of smp_last with a root result of THIS search (rpvlen > 0: think clears it, pv_saveroot sets it with rootbest).  Not for "go depth" (golimd):
; a fixed-depth search stays the main thread's own result.
;   vote(move) = sum over the candidates that chose that move of (rootscore - lowest rootscore + 14)
;   best = the first candidate (the main thread when it has a result), then for every other candidate t:
;     best has a mate score (|score| > MATEB):  t replaces it only with a higher score (a shorter mate, or a longer way to be mated)
;     otherwise:  t replaces it when t has a winning mate score, or when t's score is not a losing mate score and vote(t) > vote(best), or the votes
;                 are equal and t's root line is longer.  (A losing mate score never replaces a normal score: at the root it can be a fail-high
;                 bound.)
; When a helper wins, its rootbest / rootscore / root line / seldepth are copied into unit 0 (bestmove, ponder move and the last info line agree), and
; tm_prev takes its score.  Keeps rbx, rbp, r12 .. r15.  Clears smp_last.
smp_vote:
    mov ecx, [smp_last]
    mov dword [smp_last], 0
    test ecx, ecx
    jz .r0
    cmp dword [golimd], 0
    jne .r0
    push rbx
    push rbp
    push r12
    push r13
    push r14
    push r15
    mov r12d, ecx
    or r12d, 1                      ; r12d = candidate mask: the main thread and the helpers of the search
    mov r15d, ecx                   ; r15d = the helpers (for the node count of the info line)
    xor ebx, ebx
.f: bt r12d, ebx
    jnc .fn
    mov rbp, [unit_tab+rbx*8]
    cmp dword [rbp + rpvlen - tlb_0], 0
    jne .fn
    btr r12d, ebx                   ; no root result in this search
.fn:inc ebx
    cmp ebx, NUNITS
    jb .f
    test r12d, r12d
    jz .out
    mov r13d, INF                   ; r13d = lowest rootscore of the candidates
    mov ecx, r12d
.m: bsf ebx, ecx
    jz .md
    btr ecx, ebx
    mov rbp, [unit_tab+rbx*8]
    mov eax, [rbp + rootscore - tlb_0]
    cmp eax, r13d
    jge .m
    mov r13d, eax
    jmp .m
.md:
%ifdef VOTETEST
    call .dump                      ; (test build) the candidates, see tests/t_smp_vote.py
%endif
    bsf r14d, r12d                  ; r14d = best candidate so far
    mov ecx, r12d
    btr ecx, r14d                   ; ecx = the others, in unit order
.t: bsf ebx, ecx
    jz .chosen
    btr ecx, ebx
    push rcx
    mov rbp, [unit_tab+rbx*8]       ; rbp = block of t
    mov rsi, [unit_tab+r14*8]       ; rsi = block of best
    mov eax, [rbp + rootscore - tlb_0]   ; eax = score(t)
    mov edx, [rsi + rootscore - tlb_0]   ; edx = score(best)
    mov edi, edx                    ; |score(best)| > MATEB ?
    neg edi
    cmovl edi, edx
    cmp edi, MATEB
    jle .nd
    cmp eax, edx                    ; best is decided by a mate score: only a higher score replaces it
    jle .keep
    mov edi, eax                    ; ... and only another mate score
    neg edi
    cmovl edi, eax
    cmp edi, MATEB
    jle .keep
    jmp .take
.nd:cmp eax, MATEB                  ; t has a winning mate score
    jg .take
    cmp eax, -MATEB                 ; t has a losing mate score: never over a normal score
    jl .keep
    mov edi, [rbp + rootbest - tlb_0]
    call .votes
    mov r8, rax                     ; r8 = vote(t)
    mov edi, [rsi + rootbest - tlb_0]
    call .votes                     ; rax = vote(best)
    cmp r8, rax
    jg .take
    jl .keep
    mov eax, [rbp + rpvlen - tlb_0]
    cmp eax, [rsi + rpvlen - tlb_0]
    jle .keep
.take:
    mov r14d, ebx
.keep:
    pop rcx
    jmp .t
.chosen:
%ifdef VOTETEST
    OBS s_voteb, 21
    mov eax, r14d
    call ob_num
    mov al, 10
    call ob_ch
    call ob_flush
%endif
    test r14d, r14d
    jz .out                         ; the main thread's own result: nothing to do
    mov rbp, [unit_tab+r14*8]
    mov eax, [rbp + rootbest - tlb_0]
    mov [rootbest], eax
    mov eax, [rbp + rootscore - tlb_0]
    mov [rootscore], eax
    mov ecx, [rbp + rpvlen - tlb_0]
    mov [rpvlen], ecx
    lea rsi, [rbp + rpv - tlb_0]
    mov edi, rpv
    rep movsd
%if TM_ANY
    mov eax, [rootscore]
    mov [tm_prev], eax              ; the previous score of the next search (time management) is the score that was played
%endif
    mov [smp_run], r15d             ; the info line counts the nodes of every thread of this search (the helpers have ended)
    mov eax, [rbp + seldep - tlb_0]
    mov [seldep], eax               ; depth, seldepth, score and line of the info line all come from the chosen thread
    mov edi, [rbp + itdepth - tlb_0]
    mov esi, [rootscore]
    call print_info
    mov dword [smp_run], 0
.out:
    pop r15
    pop r14
    pop r13
    pop r12
    pop rbp
    pop rbx
.r0:ret
%ifdef VOTETEST
; .dump: "info string vote <unit> <move> <rootscore> <rpvlen>" for every candidate of r12d (test build -DVOTETEST).  Keeps the callee-saved registers.
.dump:
    push rbx
    push rbp
    push r15
    mov r15d, r12d
.dl:bsf ebx, r15d
    jz .dd
    btr r15d, ebx
    mov rbp, [unit_tab+rbx*8]
    OBS s_vote, 17
    mov eax, ebx
    call ob_num
    mov al, ' '
    call ob_ch
    mov eax, [rbp + rootbest - tlb_0]
    call ob_num
    mov al, ' '
    call ob_ch
    movsxd rax, dword [rbp + rootscore - tlb_0]
    call ob_num
    mov al, ' '
    call ob_ch
    mov eax, [rbp + rpvlen - tlb_0]
    call ob_num
    mov al, 10
    call ob_ch
    jmp .dl
.dd:call ob_flush
    pop r15
    pop rbp
    pop rbx
    ret
%endif
; .votes(edi = move) -> rax = the vote of that move over the candidates r12d (lowest score r13d).  Clobbers rax, rdx, r9, r10, r11.
.votes:
    xor eax, eax
    mov r11d, r12d
.v: bsf r9d, r11d
    jz .vr
    btr r11d, r9d
    mov r10, [unit_tab+r9*8]
    cmp edi, [r10 + rootbest - tlb_0]
    jne .v
    movsxd rdx, dword [r10 + rootscore - tlb_0]
    add rax, rdx
    movsxd rdx, r13d
    sub rax, rdx
    add rax, 14
    jmp .v
.vr:ret
%endif
%ifdef SMPTEST
; smp_report: (test build) one line per unit that took part: unit 0 and every joined helper.  The helpers are not running any more.
smp_report:
    push rbx
    push rbp
    push r12
    xor ebx, ebx
    mov r12d, [smp_run]
    or r12d, 1                      ; bit 0 = the main thread
.u: bt r12d, ebx
    jnc .nx
    mov rbp, [unit_tab+rbx*8]
    OBS s_smpunit, 20
    mov eax, ebx
    call ob_num
    mov al, ' '
    call ob_ch
    mov rax, [rbp + nodes - tlb_0]
    call ob_num
    mov al, ' '
    call ob_ch
    movsxd rax, dword [rbp + rootscore - tlb_0]
    call ob_num
    mov al, ' '
    call ob_ch
    mov eax, [rbp + rootbest - tlb_0]
    call ob_num
    mov al, ' '
    call ob_ch
    mov eax, [rbp + itdepth - tlb_0]
    call ob_num
    mov al, 10
    call ob_ch
.nx:inc ebx
    cmp ebx, NUNITS
    jb .u
    call ob_flush
    pop r12
    pop rbp
    pop rbx
    ret
%endif
%else
; ---- helper_main_<k>: entry point of the helper thread of unit k (the position was pushed by unit 0 before the clone)
%define HMAIN helper_main_ %+ UNIT
global HMAIN
HMAIN:
    push rbx
    push rbp
    push r12
    push r13
    push r14
    push r15
%ifdef WIN
    push rsi                    ; Windows ABI: a thread start routine preserves rbx rbp rsi rdi r12 .. r15
    push rdi
%endif
    call think                  ; HELPER flavour: no output, no clock, no stdin; ends when stopflag / hstop is set (or at maxdepth / a proven mate)
%ifdef WIN
    pop rdi
    pop rsi
%endif
    pop r15
    pop r14
    pop r13
    pop r12
    pop rbp
    pop rbx
%ifdef WIN
    xor eax, eax                ; thread exit code 0
%endif
    ret
%endif
%endif                          ; SMP

%ifdef UNITTEST
; ---- unit_main_<k>: entry point of a helper thread running unit k.  The position comes from unit 0, the root result goes back to it.
%define UMAIN unit_main_ %+ UNIT
global UMAIN
UMAIN:
    push rbx
    push rbp
    push r12
    push r13
    push r14
    push r15
    call unit_import
    call think
    call unit_export
    pop r15
    pop r14
    pop r13
    pop r12
    pop rbp
    pop rbx
    ret

; ---- conc_main_<k>: entry point for the concurrency test (option Conc / ConcOnly, see conc_run below).  Every participating unit takes
; ---- the position of unit 0, plays its first k legal moves (first legal move in generation order: every unit gets its own position),
; ---- announces itself (ut_ready), waits for the common start signal (ut_go) and searches.  Unlike UMAIN nothing is copied back into unit 0:
; ---- the result stays in the unit's own block, where conc_run reads it.  Plain spin (pause) while waiting: no system call is needed in a helper unit.
%if UNIT != 0
extern ut_ready, ut_go
%endif
%define CMAIN conc_main_ %+ UNIT
global CMAIN
CMAIN:
    push rbx
    push rbp
    push r12
    push r13
    push r14
    push r15
    call unit_import
    mov ebx, UNIT
.sh:test ebx, ebx
    jz .shd
    call legal_info
    test eax, eax
    jz .shd
    mov edi, eax
    call domove
    dec ebx
    jmp .sh
.shd:
    mov dword [ply], 0
    lock inc dword [ut_ready]
.w: cmp dword [ut_go], 0
    jne .go
    pause
    jmp .w
.go:
    call think
    pop r15
    pop r14
    pop r13
    pop r12
    pop rbp
    pop rbx
    ret

; ---- ttr_main_<k>: entry point of the TT race test (option TTRace, see ttr_run below).  The unit hammers the shared transposition table with
; ---- stores and probes on a handful of buckets (every key maps to one of 4 buckets, 64 keys per bucket), using ITS OWN copy of tt_store /
; ---- tt_probe.  The data of a key is a function of the key (ttr_fields), so every probe hit can be checked: a hit whose data is not the data
; ---- of that key is a corrupt (torn) entry.  Result row of the unit: ut_ttr + UNIT*64 = stores, probes, hits, bad (qwords).
%if UNIT != 0
extern ut_ttr, ut_iters
%endif
; ttr_fields(rax = key) -> edi = move, edx = score, esi = depth, ecx = bound (the arguments of tt_store).  keeps rax.  clobbers r8, r9
ttr_fields:
    mov r8, 0x9E3779B97F4A7C15
    imul r8, rax
    mov r9, r8
    shr r9, 29
    xor r8, r9
    mov edi, r8d
    or edi, 1                   ; a move is never 0
    mov rdx, r8
    shr rdx, 32
    and edx, 0x3FFF             ; score 0 .. 16383
    mov rsi, r8
    shr rsi, 48
    and esi, 63
    inc esi                     ; depth 1 .. 64
    mov rcx, r8
    shr rcx, 54
    mov r9d, ecx
    shr r9d, 1
    and ecx, 1
    and r9d, 1
    lea ecx, [rcx+r9+1]         ; bound 1 .. 3
    ret

; TTR_KEY: advance the xorshift64 state r13, rax = a key (bucket = low 2 bits of the state, 64 keys per bucket)
%macro TTR_KEY 0
    mov rax, r13
    shl rax, 13
    xor r13, rax
    mov rax, r13
    shr rax, 7
    xor r13, rax
    mov rax, r13
    shl rax, 17
    xor r13, rax
    mov rax, r13
    and eax, 3
    add eax, eax
    mov rcx, r13
    shr rcx, 8
    and ecx, 63
    inc ecx
    shl rcx, 32
    or rax, rcx
%endmacro

; TTR_PROBE: probe a random key; on a hit compare the returned data qword with the data that key must have
%macro TTR_PROBE 0
    TTR_KEY
    mov rdi, rax
    call tt_probe
    inc rbx                     ; probes
    test eax, eax
    jz %%nohit
    inc rbp                     ; hits
    mov r10, rdx                ; the copy returned by tt_probe
    mov rax, rdi
    call ttr_fields
    mov r8d, edi                ; expected data = move | score << 32 | depth << 48 | (bound | age << 2) << 56
    shl rdx, 32
    or r8, rdx
    shl rsi, 48
    or r8, rsi
    mov r9d, [ttage]
    shl r9d, 2
    or ecx, r9d
    shl rcx, 56
    or r8, rcx
    cmp r8, r10
    je %%nohit
    inc r15                     ; bad: the entry does not belong to the key it was found under
%%nohit:
%endmacro

%define TTRMAIN ttr_main_ %+ UNIT
global TTRMAIN
TTRMAIN:
    push rbx
    push rbp
    push r12
    push r13
    push r14
    push r15
    mov r12, [ut_iters]
    mov r13, 0x2545F4914F6CDD1D
    mov rax, UNIT
    imul rax, rax, 7919
    xor r13, rax                ; every unit has its own random sequence
    lock inc dword [ut_ready]
.w: cmp dword [ut_go], 0
    jne .go
    pause
    jmp .w
.go:
    xor r14d, r14d              ; stores
    xor ebx, ebx                ; probes
    xor ebp, ebp                ; hits
    xor r15d, r15d              ; bad
.l: TTR_KEY
    call ttr_fields
    call tt_store
    inc r14
    TTR_PROBE
    TTR_PROBE
    dec r12
    jnz .l
    mov [ut_ttr + UNIT*64], r14
    mov [ut_ttr + UNIT*64 + 8], rbx
    mov [ut_ttr + UNIT*64 + 16], rbp
    mov [ut_ttr + UNIT*64 + 24], r15
    pop r15
    pop r14
    pop r13
    pop r12
    pop rbp
    pop rbx
    ret
%endif

; unit_import: copy the position of unit 0 (board .. bbc, the used part of ustack) into this unit.  clobbers rax rcx rsi rdi.
; History and killers are NOT touched (they are per-unit state that lives on between searches; clear_tables clears them).
%if UNIT != 0
extern tlb_0
%endif
unit_import:
%if UNIT != 0 && SMP == 0          ; (the Lazy SMP helpers get their position PUSHED by unit 0, smp_push: they must never read unit 0's block)
%ifdef UNITBREAK                  ; self-test of tests/t_units.py: a unit that does not receive the position must make the golden test fail
    ret
%endif
    mov esi, tlb_0 + (pos_start - TLBL)
    mov edi, pos_start
    mov ecx, (pos_end - pos_start) / 8
    rep movsq
    mov eax, [gply]                         ; ustack[0 .. gply] (index = absolute game ply)
    inc eax
    shl eax, 5
    mov ecx, eax
    mov esi, tlb_0 + (ustack - TLBL)
    mov edi, ustack
    rep movsb
%endif
    mov dword [ply], 0
    ret

; unit_export: copy the root result of this unit (rootbest, rootscore, rpvlen, rpv) into unit 0.  clobbers rcx rsi rdi.
unit_export:
%if UNIT != 0 && SMP == 0          ; (a Lazy SMP helper writes nothing outside its own block)
    mov esi, rootbest
    mov edi, tlb_0 + (rootbest - TLBL)
    mov ecx, (rpv_end - rootbest) / 4
    rep movsd
%endif
    ret

%if UNIT == 0
%ifdef UNITTEST
; ============================================================================================
; Test hook (build -DUNITTEST only).  UCI option "Unit" (setoption name Unit value k): "go" runs the search in a helper thread of
; unit k (k = 0 means: as before, on the main thread).  The main thread waits for the thread, so unit k's own output (info lines) and
; the result in unit 0 are what the unchanged tests see.
; ============================================================================================
section .data
align 8
unit_main_tab:                  ; entry point of every unit's helper thread
%assign unit_i 0
%rep NUNITS
    dq unit_main_ %+ unit_i
%assign unit_i unit_i+1
%endrep
o_unit:     db "unit",0
o_conc:     db "conc",0
o_conco:    db "conconly",0
o_ttrace:   db "ttrace",0
s_conc:     db "info string conc "
s_ttr:      db "info string ttrace "
conc_main_tab:                  ; entry point of every unit for the concurrency test
%assign unit_i 0
%rep NUNITS
    dq conc_main_ %+ unit_i
%assign unit_i unit_i+1
%endrep
ttr_main_tab:                   ; entry point of every unit for the TT race test
%assign unit_i 0
%rep NUNITS
    dq ttr_main_ %+ unit_i
%assign unit_i unit_i+1
%endrep
ut_tabp:    dq conc_main_tab    ; the table conc_spawn takes the entry point from (ttr_run switches it to ttr_main_tab and back)
section .bss
alignb 8
utest_unit: resd 1
utest_conc: resd 1                          ; option Conc: units 0 .. n-1 search at the same time (0 = off)
utest_only: resd 1                          ; option ConcOnly: unit k only (stored as k+1, 0 = off)
global ut_ready, ut_go, ut_ttr, ut_iters
ut_ready:   resd 1                          ; number of helper units that have taken over their position
ut_go:      resd 1                          ; start signal
ut_save:    resd 1
alignb 64
ut_ttr:     resq 8*32                       ; TT race test: one row of 64 bytes per unit (stores, probes, hits, bad)
ut_iters:   resq 1                          ; TT race test: iterations per unit
ut_tid:     resd 1                          ; Linux: thread id, cleared by the kernel when the thread ends (CLONE_CHILD_CLEARTID)
ut_ctid:    resd 32                         ; the same for the concurrency test, one per unit
ut_stk:     resq 32                         ; Linux: stack mapping of every helper
ut_hnd:     resq 32                         ; Windows: thread handle of every helper
section .text
%assign unit_i 1
%rep NUNITS-1
extern unit_main_ %+ unit_i
extern conc_main_ %+ unit_i
extern ttr_main_ %+ unit_i
%assign unit_i unit_i+1
%endrep

think_dispatch:
    mov eax, [utest_conc]
    or eax, [utest_only]
    jnz conc_run
    mov eax, [utest_unit]
    test eax, eax
    jnz .helper
    jmp think
.helper:
    mov rdi, [unit_main_tab+rax*8]
    jmp os_thread_run

; ============================================================================================
; conc_run: the concurrency test.  Option Conc n: the units 0 .. n-1 search AT THE SAME TIME, each its own position (conc_main_<k>), unit 0 on the
; main thread, the others in helper threads, started together by the signal ut_go; option ConcOnly k: unit k alone (the reference run, same
; code path).  Output of the units is switched off meanwhile (uci_mode = 0: the output buffer and stdin polling belong to unit 0).  When all have
; finished, one line per unit goes to the output, read from the unit's own block:
;     info string conc <k> <nodes> <rootscore> <rootbest> <rpvlen> <rpv moves, raw numbers>
; tests/t_units_conc.py compares the concurrent run with the reference runs.  Build with -DNOTT (table off) the node counts are deterministic
; and must be identical; with the table on, only crash, hang and plausibility can be checked.
; ============================================================================================
%macro CSP 0
    mov al, ' '
    call ob_ch
%endmacro
conc_run:
    push rbx
    push rbp
    push r12
    push r13
    push r14
    push r15
    mov eax, [uci_mode]
    mov [ut_save], eax
    mov dword [uci_mode], 0
    mov dword [ut_ready], 0
    mov dword [ut_go], 0
    mov ecx, [utest_only]
    test ecx, ecx
    jz .cnt
    dec ecx
    mov r12d, 1
    shl r12d, cl                    ; only unit k
    jmp .have
.cnt:
    mov ecx, [utest_conc]           ; units 0 .. n-1
    mov r12d, -1
    cmp ecx, 32
    jae .have
    mov r12d, 1
    shl r12d, cl
    dec r12d
.have:
    xor r13d, r13d                  ; helpers started
    mov ebx, 1
.sp:cmp ebx, NUNITS
    jae .spd
    bt r12d, ebx
    jnc .spn
    mov edi, ebx
    call conc_spawn
    test eax, eax
    jnz .spf
    inc r13d
    jmp .spn
.spf:
    btr r12d, ebx                   ; could not start unit k: it gets no result line
.spn:
    inc ebx
    jmp .sp
.spd:
.wr:mov eax, [ut_ready]
    cmp eax, r13d
    jae .rdy
    mov eax, 24                     ; sched_yield (Linux; the Windows shim answers ENOSYS, which is fine)
    syscall
    jmp .wr
.rdy:
    mov dword [ut_go], 1
    test r12d, 1
    jz .nm
    call conc_main_0                ; unit 0 searches on the main thread, at the same time as the helpers
.nm:
    mov ebx, 1
.jn:cmp ebx, NUNITS
    jae .jd
    bt r12d, ebx
    jnc .jnn
    mov edi, ebx
    call conc_join
.jnn:
    inc ebx
    jmp .jn
.jd:
    mov eax, [ut_save]
    mov [uci_mode], eax
    xor ebx, ebx
.pl:cmp ebx, NUNITS
    jae .pd
    bt r12d, ebx
    jnc .pn
    OBS s_conc, 17
    mov eax, ebx
    call ob_num
    CSP
    mov r14, [unit_tab+rbx*8]
    mov rax, [r14 + nodes - tlb_0]
    call ob_num
    CSP
    movsxd rax, dword [r14 + rootscore - tlb_0]
    call ob_num
    CSP
    mov eax, [r14 + rootbest - tlb_0]
    call ob_num
    CSP
    mov r15d, [r14 + rpvlen - tlb_0]
    mov eax, r15d
    call ob_num
    xor ebp, ebp
.pv:cmp ebp, r15d
    jae .pvd
    CSP
    mov eax, [r14 + rbp*4 + rpv - tlb_0]
    call ob_num
    inc ebp
    jmp .pv
.pvd:
    mov al, 10
    call ob_ch
.pn:inc ebx
    jmp .pl
.pd:
    pop r15
    pop r14
    pop r13
    pop r12
    pop rbp
    pop rbx
    ret

; ============================================================================================
; ttr_run(rax = iterations per unit): the TT race test.  Option "TTRace value n" (n * 1000 iterations; the number of units is option Conc, default 2):
; the units 0 .. u-1 hammer the shared transposition table at the same time (ttr_main_<k>), unit 0 on the main thread.  One line per unit:
;     info string ttrace <k> <stores> <probes> <hits> <bad>
; "bad" must be 0 with the lock-free table; with -DTTPLAIN (plain, not lock-free) it is not (tests/t_ttrace.py).  The units print nothing themselves;
; the table and ttage are left in a state that the next ucinewgame cleans.
; ============================================================================================
ttr_run:
    push rbx
    push rbp
    push r12
    push r13
    push r14
    push r15
    mov [ut_iters], rax
    mov dword [ttage], 5
    mov dword [ut_ready], 0
    mov dword [ut_go], 0
    mov qword [ut_tabp], ttr_main_tab
    mov r12d, [utest_conc]
    cmp r12d, 2
    jae .n1
    mov r12d, 2
.n1:cmp r12d, NUNITS
    jbe .n2
    mov r12d, NUNITS
.n2:mov r14d, 1                 ; bit mask of the units that run (unit 0 on the main thread)
    xor r13d, r13d              ; helpers started
    mov ebx, 1
.sp:cmp ebx, r12d
    jae .spd
    mov edi, ebx
    call conc_spawn
    test eax, eax
    jnz .spn
    bts r14d, ebx
    inc r13d
.spn:inc ebx
    jmp .sp
.spd:
.wr:mov eax, [ut_ready]
    cmp eax, r13d
    jae .rdy
    mov eax, 24
    syscall
    jmp .wr
.rdy:
    mov dword [ut_go], 1
    call ttr_main_0
    mov ebx, 1
.jn:cmp ebx, NUNITS
    jae .jd
    bt r14d, ebx
    jnc .jnn
    mov edi, ebx
    call conc_join
.jnn:inc ebx
    jmp .jn
.jd:mov qword [ut_tabp], conc_main_tab
    xor ebx, ebx
.pl:cmp ebx, NUNITS
    jae .pd
    bt r14d, ebx
    jnc .pn
    OBS s_ttr, 19
    mov eax, ebx
    call ob_num
    mov r15, rbx
    shl r15, 6
    CSP
    mov rax, [ut_ttr + r15]
    call ob_num
    CSP
    mov rax, [ut_ttr + r15 + 8]
    call ob_num
    CSP
    mov rax, [ut_ttr + r15 + 16]
    call ob_num
    CSP
    mov rax, [ut_ttr + r15 + 24]
    call ob_num
    mov al, 10
    call ob_ch
.pn:inc ebx
    jmp .pl
.pd:call ob_flush
    pop r15
    pop r14
    pop r13
    pop r12
    pop rbp
    pop rbx
    ret

%ifndef WIN
; conc_spawn(edi = k) -> eax = 0 on success: thread of unit k (own 1 MB stack + guard page, raw clone as in os_thread_run), entry conc_main_<k>
conc_spawn:
    push rbx
    push r12
    mov ebx, edi
    mov eax, 9
    xor edi, edi
    mov esi, 0x101000
    mov edx, 3
    mov r10d, 0x22
    mov r8, -1
    xor r9d, r9d
    syscall
    cmp rax, -4096
    ja .fail
    mov [ut_stk+rbx*8], rax
    mov r12, rax
    mov eax, 10                     ; mprotect(base, 4096, PROT_NONE)
    mov rdi, r12
    mov esi, 4096
    xor edx, edx
    syscall
    lea rsi, [r12+0x101000-8]
    mov rax, [ut_tabp]
    mov rax, [rax+rbx*8]
    mov [rsi], rax
    lea rdx, [ut_ctid+rbx*4]
    mov dword [rdx], 0
    mov edi, 0x350F00               ; CLONE_VM|FS|FILES|SIGHAND|THREAD|SYSVSEM|PARENT_SETTID|CHILD_CLEARTID
    mov r10, rdx
    xor r8d, r8d
    mov eax, 56
    syscall
    test rax, rax
    jz .child
    js .fail
    xor eax, eax
    jmp .out
.fail:
    mov eax, 1
.out:
    pop r12
    pop rbx
    ret
.child:
    pop rax
    call rax
    mov eax, 60
    xor edi, edi
    syscall

; conc_join(edi = k): wait until thread k has ended (futex on its tid word), free its stack
conc_join:
    push rbx
    mov ebx, edi
.w: mov edx, [ut_ctid+rbx*4]
    test edx, edx
    jz .u
    lea rdi, [ut_ctid+rbx*4]
    xor esi, esi
    xor r10d, r10d
    mov eax, 202
    syscall
    jmp .w
.u: mov eax, 11
    mov rdi, [ut_stk+rbx*8]
    mov esi, 0x101000
    syscall
    pop rbx
    ret
%else
; Windows: conc_spawn(edi = k) -> eax = 0 on success (CreateThread, 1 MB stack reservation, entry conc_main_<k>); conc_join(edi = k): WaitForSingleObject.
conc_spawn:
    push rbp
    mov rbp, rsp
    push rbx
    mov ebx, edi
    sub rsp, 72
    and rsp, -16
    xor ecx, ecx
    mov edx, 0x100000
    mov r8, [ut_tabp]
    mov r8, [r8+rbx*8]
    xor r9d, r9d
    mov dword [rsp+32], 0x10000     ; STACK_SIZE_PARAM_IS_A_RESERVATION
    mov qword [rsp+40], 0
    call CreateThread
    mov [ut_hnd+rbx*8], rax
    test rax, rax
    setz al
    movzx eax, al
    mov rbx, [rbp-8]
    mov rsp, rbp
    pop rbp
    ret
conc_join:
    push rbp
    mov rbp, rsp
    push rbx
    mov ebx, edi
    sub rsp, 72
    and rsp, -16
    mov rcx, [ut_hnd+rbx*8]
    mov edx, -1
    call WaitForSingleObject
    mov rbx, [rbp-8]
    mov rsp, rbp
    pop rbp
    ret
%endif

%ifndef WIN
; os_thread_run(rdi = entry): start a thread with its own 1 MB stack (+ guard page) that calls entry, wait until it has finished.
; Raw clone, as the later Lazy SMP code will do.  The thread ends with exit (60), which ends only the calling thread.
os_thread_run:
    push rbx
    push r12
    mov r12, rdi
    mov eax, 9                      ; mmap(0, 1 MB + guard, RW, PRIVATE|ANONYMOUS, -1, 0)
    xor edi, edi
    mov esi, 0x101000
    mov edx, 3
    mov r10d, 0x22
    mov r8, -1
    xor r9d, r9d
    syscall
    cmp rax, -4096
    ja .fail
    mov rbx, rax
    mov eax, 10                     ; mprotect(base, 4096, PROT_NONE): guard page at the low end
    mov rdi, rbx
    mov esi, 4096
    xor edx, edx
    syscall
    lea rsi, [rbx+0x101000-8]       ; child stack: the entry address on top
    mov [rsi], r12
    mov dword [ut_tid], 0
    mov edi, 0x350F00               ; CLONE_VM|FS|FILES|SIGHAND|THREAD|SYSVSEM|PARENT_SETTID|CHILD_CLEARTID
    mov edx, ut_tid                 ; parent tid
    mov r10d, ut_tid                ; child tid (cleared + futex wake at thread exit)
    xor r8d, r8d
    mov eax, 56
    syscall
    test rax, rax
    jz .child
    js .unmap
.wait:
    mov edx, [ut_tid]
    test edx, edx
    jz .unmap
    mov edi, ut_tid                 ; futex(&ut_tid, FUTEX_WAIT, tid, NULL)
    xor esi, esi
    xor r10d, r10d
    mov eax, 202
    syscall
    jmp .wait
.unmap:
    mov eax, 11
    mov rdi, rbx
    mov esi, 0x101000
    syscall
.fail:
    pop r12
    pop rbx
    ret
.child:
    pop rax
    call rax
    mov eax, 60
    xor edi, edi
    syscall
%else
; os_thread_run(rdi = entry), Windows: CreateThread with a 1 MB stack reservation, WaitForSingleObject until it ends.  (The handle is
; not closed: test hook only.)  The thread routine is called as a Windows thread start routine (rsp = 8 mod 16).
os_thread_run:
    push rbp
    mov rbp, rsp
    push rbx
    sub rsp, 72
    and rsp, -16
    xor ecx, ecx
    mov edx, 0x100000
    mov r8, rdi
    xor r9d, r9d
    mov dword [rsp+32], 0x10000     ; STACK_SIZE_PARAM_IS_A_RESERVATION
    mov qword [rsp+40], 0
    call CreateThread
    test rax, rax
    jz .out
    mov rcx, rax
    mov edx, -1
    call WaitForSingleObject
.out:
    mov rbx, [rbp-8]
    mov rsp, rbp
    pop rbp
    ret
%endif
%endif                          ; UNITTEST
%endif                          ; UNIT == 0
