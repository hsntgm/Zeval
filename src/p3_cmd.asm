
%if UNIT == 0
; ============================================================
; input
; ============================================================
readline:                       ; -> eax = length, or -1 at EOF; line in linebuf (NUL terminated)
    ; The line is normalised while it is read (UCI: "arbitrary white space between tokens"): tabs and other
    ; control characters count as blanks, runs of blanks collapse to one, leading and trailing blanks are
    ; dropped, CR is ignored.  Characters beyond the buffer size are discarded up to the end of the line.
    mov rdi, linebuf
.next:
    mov rax, [ipos]
    cmp rax, [ilen]
    jb .have
    push rdi
    xor eax, eax
    xor edi, edi
    mov esi, ibuf
    mov edx, 65536
    syscall
    pop rdi
    test rax, rax
    jle .eof
    mov [ilen], rax
    mov qword [ipos], 0
    xor eax, eax
.have:
    movzx ecx, byte [ibuf+rax]
    inc rax
    mov [ipos], rax
    cmp cl, 10
    je .eol
    cmp cl, 13
    je .next
    cmp cl, ' '
    ja .st
    cmp rdi, linebuf            ; blank: not at the start of the line ...
    je .next
    cmp byte [rdi-1], ' '       ; ... and not right after another blank
    je .next
    mov cl, ' '
.st:cmp rdi, linebuf+LINEMAX-6
    jae .next                   ; line too long: discard the rest of it
    mov [rdi], cl
    inc rdi
    jmp .next
.eol:
    cmp rdi, linebuf            ; strip one trailing blank (there is never more than one)
    je .z
    cmp byte [rdi-1], ' '
    jne .z
    dec rdi
.z: mov byte [rdi], 0
    mov rax, rdi
    sub rax, linebuf
    ret
.eof:
    mov qword [ilen], 0
    mov qword [ipos], 0
    cmp rdi, linebuf
    jne .eol
    mov rax, -1
    ret

; find_tok(rsi = text, rdi = NUL-terminated pattern) -> rax = pointer just after the token, or 0
find_tok:
    mov r8, rsi
    mov r9, rsi
.p: movzx eax, byte [r9]
    test eax, eax
    jz .no
    cmp r9, r8
    je .st
    cmp byte [r9-1], ' '
    jne .adv
.st:xor ecx, ecx
.m: movzx edx, byte [rdi+rcx]
    test edx, edx
    jz .end
    cmp dl, [r9+rcx]
    jne .adv
    inc ecx
    jmp .m
.end:
    movzx edx, byte [r9+rcx]
    test edx, edx
    jz .yes
    cmp edx, ' '
    je .yes
.adv:
    inc r9
    jmp .p
.yes:
    lea rax, [r9+rcx]
    ret
.no:xor eax, eax
    ret

; parse_num(rsi) -> eax = unsigned number, rsi advanced (skips leading spaces)
parse_num:
.s: cmp byte [rsi], ' '
    jne .d
    inc rsi
    jmp .s
.d: xor eax, eax
.l: movzx ecx, byte [rsi]
    sub ecx, '0'
    cmp ecx, 9
    ja .r
    imul eax, 10
    add eax, ecx
    inc rsi
    jmp .l
.r: ret
%endif                          ; UNIT == 0 (readline, find_tok, parse_num)

; isdraw -> eax = 1 for repetition, insufficient material, 50-move rule (R50MATE: 2 for the 50-move rule, the caller looks for a mate)
isdraw:
    mov eax, [halfm]
    cmp eax, 100
%if R50MATE
    jge .r50
%else
    jge .yes
%endif
    mov ecx, [gply]
    mov edx, eax
    cmp edx, ecx
    jle .lim
    mov edx, ecx
.lim:
    mov r8, [hashk]
    mov esi, 2
.rp:cmp esi, edx
    jg .mat
    mov eax, ecx
    sub eax, esi
    shl eax, 5
    cmp [ustack+rax], r8
    je .yes
    add esi, 2
    jmp .rp
.mat:
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
%if R50MATE
.r50:
    mov eax, 2
    ret
%endif

; in_check -> eax = 1 if the side to move is in check
in_check:
    mov eax, [stm]
    mov esi, eax
    shr eax, 3
    mov edi, [ksq+rax*4]
    xor esi, 8
    jmp attacked

%if UNIT == 0 || R50MATE
; has_legal -> eax = 1 if the side to move has a legal move (every unit with R50MATE: search calls it at half-move clock >= 100 in check)
has_legal:
    push rbx
    push rbp
    sub rsp, MOVEBUF
    mov rdi, rsp
    xor esi, esi
    call gen
    mov rbx, rax
    mov rbp, rsp
.l: cmp rbp, rbx
    jae .no
    mov edi, [rbp]
    add rbp, 4
    call domove
    mov r8d, eax
    push r8
    call undomove
    pop r8
    test r8d, r8d
    jz .l
    mov eax, 1
    jmp .r
.no:xor eax, eax
.r: add rsp, MOVEBUF
    pop rbp
    pop rbx
    ret
%endif

%if UNIT == 0
; apply_text_move(rsi = "e2e4[qrbn]") -> eax = move applied (0 if illegal / unparsable)
apply_text_move:
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
    ; promotion field matters only for pawn moves to the last rank
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
.go:mov edi, r15d
    call domove
    test eax, eax
    jnz .ok
    call undomove
    jmp .bad
.ok:mov ecx, [gply]
    cmp ecx, MAXGAME-300        ; keep gply + search plies (<= MAXPLY) + perft depth inside ustack: drop the old history,
    jb .ok2                     ; only the last 256 records (> the 100 plies of the 50-move window) are needed for repetitions
    sub ecx, 256
    shl ecx, 5
    lea rsi, [ustack+rcx]
    mov edi, ustack
    mov ecx, 256*32/8
    rep movsq
    mov dword [gply], 256
.ok2:
    mov eax, r15d
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

; ============================================================
; output of positions
; ============================================================
printboard:
    push rbx
    push rbp
    mov al, 10
    call ob_ch
    mov ebx, 7
.r: mov eax, ebx
    add eax, '1'
    call ob_ch
    mov al, ' '
    call ob_ch
    xor ebp, ebp
.f: mov eax, ebx
    shl eax, 4
    add eax, ebp
    movzx eax, byte [board+rax]
    movzx eax, byte [pchars+rax]
    call ob_ch
    mov al, ' '
    call ob_ch
    inc ebp
    cmp ebp, 8
    jl .f
    mov al, 10
    call ob_ch
    dec ebx
    jns .r
    OBS s_foot, s_foot_l
    pop rbp
    pop rbx
    ret

ob_fen:
    push rbx
    push rbp
    push r12
    mov ebx, 7
.r: xor ebp, ebp
    xor r12d, r12d              ; empty counter
.f: mov eax, ebx
    shl eax, 4
    add eax, ebp
    movzx eax, byte [board+rax]
    test eax, eax
    jnz .pc
    inc r12d
    jmp .nx
.pc:test r12d, r12d
    jz .np
    lea eax, [r12+'0']
    call ob_ch
    xor r12d, r12d
.np:mov eax, ebx
    shl eax, 4
    add eax, ebp
    movzx eax, byte [board+rax]
    movzx eax, byte [pchars+rax]
    call ob_ch
.nx:inc ebp
    cmp ebp, 8
    jl .f
    test r12d, r12d
    jz .nz
    lea eax, [r12+'0']
    call ob_ch
.nz:cmp ebx, 0
    je .done
    mov al, '/'
    call ob_ch
    dec ebx
    jmp .r
.done:
    mov al, ' '
    call ob_ch
    mov al, 'w'
    cmp dword [stm], 0
    je .sw
    mov al, 'b'
.sw:call ob_ch
    mov al, ' '
    call ob_ch
    mov r12d, [castle]
    test r12d, r12d
    jnz .cs
    mov al, '-'
    call ob_ch
    jmp .ep
.cs:test r12d, 1
    jz .c1
    mov al, 'K'
    call ob_ch
.c1:test r12d, 2
    jz .c2
    mov al, 'Q'
    call ob_ch
.c2:test r12d, 4
    jz .c3
    mov al, 'k'
    call ob_ch
.c3:test r12d, 8
    jz .ep
    mov al, 'q'
    call ob_ch
.ep:mov al, ' '
    call ob_ch
    mov eax, [epsq]
    test eax, eax
    jnz .e1
    mov al, '-'
    call ob_ch
    jmp .hm
.e1:mov ecx, eax
    and eax, 7
    add al, 'a'
    call ob_ch
    mov eax, ecx
    shr eax, 4
    add al, '1'
    call ob_ch
.hm:mov al, ' '
    call ob_ch
    mov eax, [halfm]
    call ob_num
    mov al, ' '
    call ob_ch
    mov eax, [gply]
    shr eax, 1
    inc eax
    call ob_num
    pop r12
    pop rbp
    pop rbx
    ret
%endif                          ; UNIT == 0 (apply_text_move .. ob_fen)
