%if UNIT == 0

%ifdef WIN
; ============================================================================================
; Windows x64 platform layer.  Assembled only with -DWIN; the Linux build never sees this file.
;
; The engine talks to the OS through eight Linux system calls.  p1_data.asm redefines the
; mnemonic "syscall" as "call os_syscall", so the engine core (movegen, search, eval, UCI) is
; the same code as on Linux.  os_syscall emulates the calls on top
; of kernel32.dll (present on every Windows installation; no C runtime, no other DLL):
;
;      0 read           -> data from the stdin reader thread (ReadFile runs there)
;      1 write          -> WriteFile (loops until everything is written)
;      7 poll           -> look at the reader thread's flags / WaitForSingleObject for the timeout
;      9 mmap           -> VirtualAlloc (anonymous read/write memory: the transposition table, option Hash)
;     11 munmap         -> VirtualFree
;     16 ioctl TCGETS   -> "is stdin a console?"  (GetConsoleMode)
;     60 exit           -> ExitProcess
;    228 clock_gettime  -> QueryPerformanceCounter (monotonic, sub-microsecond)
;
; Why a reader thread: the engine must notice "stop"/"quit" while it searches.  A blocking
; ReadFile in a helper thread works the same for pipes (GUIs), consoles and redirected files and
; does not depend on PeekNamedPipe (which not every pipe type supports).  While searching, the
; engine's poll costs two memory reads - no system call at all.
;
; Register contract of os_syscall = Linux syscall contract: every register is preserved
; except rax (result), rcx and r11.  (Windows API calls destroy rdx, r8-r11, so those are
; saved here; rbx, rbp, rsi, rdi, r12-r15 are preserved by the Windows ABI itself.)
; The engine does not keep the stack 16-byte aligned, so os_syscall aligns it itself and
; reserves the 32-byte shadow space required by the Windows x64 calling convention.
; ============================================================================================
extern GetStdHandle, ReadFile, WriteFile, ExitProcess, GetConsoleMode
extern QueryPerformanceCounter, QueryPerformanceFrequency
extern CreateEventA, CreateThread, SetEvent, WaitForSingleObject, CloseHandle
extern VirtualAlloc, VirtualFree
global win_entry

section .bss
w_hin:      resq 1              ; stdin handle
w_hout:     resq 1              ; stdout handle
w_qpf:      resq 1              ; QueryPerformanceFrequency (ticks per second)
w_iscon:    resd 1              ; 1 if stdin is a console
w_n:        resd 1              ; DWORD out parameter shared by the API calls
w_rdn:      resd 1              ; bytes read by the reader thread's ReadFile
w_rdsz:     resd 1              ; reader chunk size (console reads stay small)
w_slen:     resd 1              ; bytes in w_stage            (valid while w_avail = 1)
w_spos:     resd 1              ; bytes of w_stage already handed to the engine
w_avail:    resd 1              ; 1: w_stage holds unread input
w_eof:      resd 1              ; 1: end of file / broken pipe / read error reached
w_pfd:      resq 1              ; pollfd pointer of the poll call in progress
w_evdata:   resq 1              ; auto-reset event: reader -> engine, "w_avail or w_eof was set"
w_evfree:   resq 1              ; auto-reset event: engine -> reader, "w_stage was consumed"
w_tmp:      resq 2              ; LARGE_INTEGER scratch
alignb 16
w_stage:    resb 65536          ; hand-over buffer between reader thread and engine
section .text

; --------------------------------------------------------------------------------------------
; Process entry point (linker option /ENTRY:win_entry).
; The loader enters with rsp = 8 (mod 16) and a return address on the stack.
win_entry:
    sub rsp, 56                 ; 32 bytes shadow space + 2 stack args + alignment
    mov ecx, -10                ; STD_INPUT_HANDLE
    call GetStdHandle
    mov [w_hin], rax
    mov ecx, -11                ; STD_OUTPUT_HANDLE
    call GetStdHandle
    mov [w_hout], rax
    lea rcx, [w_qpf]
    call QueryPerformanceFrequency
    mov dword [w_rdsz], 65536
    mov rcx, [w_hin]
    lea rdx, [w_n]
    call GetConsoleMode
    test eax, eax
    jz .nc
    mov dword [w_iscon], 1      ; interactive console
    mov dword [w_rdsz], 4096
.nc:
    ; two auto-reset events (initially not signaled)
    xor ecx, ecx
    xor edx, edx
    xor r8d, r8d
    xor r9d, r9d
    call CreateEventA
    mov [w_evdata], rax
    xor ecx, ecx
    xor edx, edx
    xor r8d, r8d
    xor r9d, r9d
    call CreateEventA
    mov [w_evfree], rax
    cmp qword [w_evdata], 0     ; an event could not be created: behave like an empty stdin
    je .noth
    test rax, rax
    jz .noth
    ; stdin reader thread (256 KB stack reservation)
    xor ecx, ecx
    mov edx, 0x40000
    lea r8, [w_reader]
    xor r9d, r9d
    mov dword [rsp+32], 0x10000 ; STACK_SIZE_PARAM_IS_A_RESERVATION
    mov qword [rsp+40], 0
    call CreateThread
    test rax, rax
    jnz .th
.noth:
    mov dword [w_eof], 1        ; no thread: behave like an empty stdin
.th:
    ; Linux starts a process with the x87 control word 0x037F (64-bit mantissa); Windows starts
    ; with 0x027F (53-bit).  init_lmr builds the late-move-reduction table with x87 fyl2x, so the
    ; control word is pinned to the Linux value: the search tree stays identical to the Linux build.
    mov word [w_n], 0x037F
    fldcw word [w_n]
    add rsp, 56
    and rsp, -16                ; same alignment the Linux _start sees
    jmp _start

; --------------------------------------------------------------------------------------------
; stdin reader thread: ReadFile -> w_stage -> signal -> wait until the engine consumed it.
; Entered as a Windows thread start routine: rsp = 8 (mod 16).
w_reader:
    sub rsp, 56
.rl:mov rcx, [w_hin]
    lea rdx, [w_stage]
    mov r8d, [w_rdsz]
    lea r9, [w_rdn]
    mov qword [rsp+32], 0       ; lpOverlapped
    call ReadFile
    test eax, eax
    jz .re                      ; error / broken pipe -> end of input
    mov eax, [w_rdn]
    test eax, eax
    jz .re                      ; 0 bytes -> end of input
    mov [w_slen], eax
    mov dword [w_spos], 0
    mov dword [w_avail], 1      ; publish (x86 keeps the stores in order)
    mov rcx, [w_evdata]
    call SetEvent
    mov rcx, [w_evfree]
    mov edx, -1                 ; INFINITE: wait until the engine has taken the whole chunk
    call WaitForSingleObject
    jmp .rl
.re:mov dword [w_eof], 1
    mov rcx, [w_evdata]
    call SetEvent
    xor eax, eax
    add rsp, 56
    ret

; --------------------------------------------------------------------------------------------
; os_syscall: eax = Linux syscall number; arguments in rdi, rsi, rdx as on Linux.
os_syscall:
    push rbp
    mov rbp, rsp
    push rdx                    ; [rbp- 8] original rdx (byte count / timeout)
    push r8
    push r9
    push r10
    push rbx
    push r12                    ; [rbp-48]
    sub rsp, 64                 ; shadow space + stack arguments
    and rsp, -16
    cmp eax, 1
    je .write
    test eax, eax
    jz .read
    cmp eax, 7
    je .poll
    cmp eax, 9
    je .mmap
    cmp eax, 11
    je .munmap
    cmp eax, 228
    je .clock
    cmp eax, 60
    je .exit
    cmp eax, 16
    je .ioctl
    mov rax, -38                ; ENOSYS
.ret:
    lea rsp, [rbp-48]
    pop r12
    pop rbx
    pop r10
    pop r9
    pop r8
    pop rdx
    pop rbp
    ret

; write(fd=1, rsi=buf, rdx=len): loop until everything is written
.write:
    mov rbx, rsi
    mov r12, rdx
.w1:test r12, r12
    jle .wok
    mov rcx, [w_hout]
    mov rdx, rbx
    mov r8, r12
    cmp r8, 32768
    jbe .w2
    mov r8d, 32768
.w2:lea r9, [w_n]
    mov qword [rsp+32], 0       ; lpOverlapped
    call WriteFile
    test eax, eax
    jz .werr
    mov eax, [w_n]
    test eax, eax
    jz .werr
    add rbx, rax
    sub r12, rax
    jmp .w1
.wok:
    mov rax, [rbp-8]
    jmp .ret
.werr:
    mov rax, -5                 ; EIO (the engine ignores the result of write)
    jmp .ret

; read(fd=0, rsi=buf, rdx=len) -> bytes read; 0 at end of file / broken pipe / error
.read:
    mov r12, rsi                ; r12 = destination
    mov rbx, [rbp-8]            ; rbx = requested length
.rw:cmp dword [w_avail], 0
    jne .rhave
    cmp dword [w_eof], 0
    jne .reof
    mov rcx, [w_evdata]
    mov edx, -1                 ; INFINITE
    call WaitForSingleObject
    jmp .rw
.rhave:
    mov eax, [w_slen]
    sub eax, [w_spos]           ; bytes still in the stage
    cmp rax, rbx
    jae .rn
    mov rbx, rax                ; rbx = n = min(left, requested)
.rn:push rsi                    ; the engine expects rsi / rdi back unchanged
    push rdi
    mov rcx, rbx
    mov rdi, r12
    lea rsi, [w_stage]
    mov eax, [w_spos]
    add rsi, rax
    rep movsb
    pop rdi
    pop rsi
    add [w_spos], ebx
    mov eax, [w_spos]
    cmp eax, [w_slen]
    jb .rdone                   ; part of the chunk is left
    mov dword [w_avail], 0      ; chunk fully consumed:
    mov rcx, [w_evdata]         ;   clear a possibly stale "data" signal,
    xor edx, edx
    call WaitForSingleObject
    mov rcx, [w_evfree]         ;   and let the reader fetch the next chunk
    call SetEvent
.rdone:
    mov rax, rbx
    jmp .ret
.reof:
    xor eax, eax
    jmp .ret

; poll(rdi=pollfd[1], 1, timeout ms): revents (word at pollfd+6) = 1 POLLIN or 0x10 POLLHUP
.poll:
    mov [w_pfd], rdi
    movsxd r12, dword [rbp-8]   ; timeout (0 while searching, 100 while waiting for "stop")
.pl:cmp dword [w_avail], 0
    jne .pdata
    cmp dword [w_eof], 0
    jne .phup
    test r12, r12
    jz .pnone
    mov rcx, [w_evdata]
    mov edx, r12d               ; a negative timeout becomes INFINITE
    call WaitForSingleObject
    test eax, eax
    jnz .pnone                  ; WAIT_TIMEOUT
    xor r12d, r12d              ; woken by the reader: look at the flags once more
    jmp .pl
.pdata:
    mov rdx, [w_pfd]
    mov word [rdx+6], 1
    mov eax, 1
    jmp .ret
.phup:
    mov rdx, [w_pfd]
    mov word [rdx+6], 0x10
    mov eax, 1
    jmp .ret
.pnone:
    mov rdx, [w_pfd]
    mov word [rdx+6], 0
    xor eax, eax
    jmp .ret

; mmap(0, rsi=len, ...) -> zeroed read/write memory, or -ENOMEM
.mmap:
    xor ecx, ecx                ; lpAddress = NULL
    mov rdx, rsi                ; dwSize
    mov r8d, 0x3000             ; MEM_COMMIT | MEM_RESERVE
    mov r9d, 4                  ; PAGE_READWRITE
    call VirtualAlloc
    test rax, rax
    jnz .ret
    mov rax, -12                ; ENOMEM
    jmp .ret

; munmap(rdi=addr, rsi=len)
.munmap:
    mov rcx, rdi
    xor edx, edx                ; dwSize must be 0 with MEM_RELEASE
    mov r8d, 0x8000             ; MEM_RELEASE
    call VirtualFree
    xor eax, eax
    jmp .ret

; clock_gettime(CLOCK_MONOTONIC, rsi=timespec{sec, nsec})
.clock:
    mov rbx, rsi
    lea rcx, [w_tmp]
    call QueryPerformanceCounter
    mov rax, [w_tmp]
    xor edx, edx
    div qword [w_qpf]           ; rax = seconds, rdx = remaining ticks
    mov [rbx], rax
    mov rax, rdx
    mov ecx, 1000000000
    mul rcx
    div qword [w_qpf]           ; remaining ticks * 1e9 / freq: quotient < 1e9, no overflow
    mov [rbx+8], rax
    xor eax, eax
    jmp .ret

.exit:
    mov ecx, edi
    call ExitProcess

; ioctl(0, TCGETS): 0 if stdin is a console, -ENOTTY otherwise
.ioctl:
    mov eax, -25
    cmp dword [w_iscon], 0
    je .ret
    xor eax, eax
    jmp .ret
%endif
%endif                          ; UNIT == 0
