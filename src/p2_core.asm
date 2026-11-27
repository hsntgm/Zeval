
%if UNIT == 0
section .data
c_lmr:      dd 0.2135
c_075:      dd 0.75
%ifdef TUNE
c_i10:      dd 10
c_i1000:    dd 1000
%endif

section .text
global _start

; ============================================================
; output buffer helpers
; ============================================================
ob_flush:
    mov rdx, [optr]
    sub rdx, obuf
    jz .e
    mov eax, 1
    mov edi, 1
    mov esi, obuf
    syscall
    mov qword [optr], obuf
.e: ret

ob_str:                         ; rsi = src, ecx = len
    mov rdi, [optr]
    rep movsb
    mov [optr], rdi
    ret

ob_ch:                          ; al = char
    mov rdi, [optr]
    mov [rdi], al
    inc rdi
    mov [optr], rdi
    ret

ob_num:                         ; rax = signed value
    push rbx
    mov rdi, [optr]
    test rax, rax
    jns .p
    mov byte [rdi], '-'
    inc rdi
    neg rax
.p: mov ebx, 10
    xor ecx, ecx
.d: xor edx, edx
    div rbx
    add edx, '0'
    push rdx
    inc ecx
    test rax, rax
    jnz .d
.w: pop rdx
    mov [rdi], dl
    inc rdi
    dec ecx
    jnz .w
    mov [optr], rdi
    pop rbx
    ret
%endif                          ; UNIT == 0

%macro OBS 2                    ; literal string + length
    mov esi, %1
    mov ecx, %2
    call ob_str
%endmacro

%if UNIT == 0
; ob_move(edi = move) - call while the position is still the one the move is played from
ob_move:
    mov rax, [optr]
    mov ecx, edi
    and ecx, 127
    mov esi, ecx                ; from
    mov edx, ecx
    and edx, 7
    add dl, 'a'
    mov [rax], dl
    shr ecx, 4
    add cl, '1'
    mov [rax+1], cl
    mov ecx, edi
    shr ecx, 7
    and ecx, 127                ; to
    mov r8d, ecx
    mov edx, ecx
    and edx, 7
    add dl, 'a'
    mov [rax+2], dl
    shr ecx, 4
    add cl, '1'
    mov [rax+3], cl
    add rax, 4
    movzx edx, byte [board+rsi]
    and edx, 7
    cmp edx, 1
    jne .done
    mov ecx, r8d
    and ecx, 0xF0
    cmp ecx, 0x70
    je .pr
    test ecx, ecx
    jnz .done
.pr:
    mov ecx, edi
    shr ecx, 14
    movzx edx, byte [promochars+rcx]
    mov [rax], dl
    inc rax
.done:
    mov [optr], rax
    ret
section .data
promochars: db "nbrq"
section .text

gettime_ms:                     ; -> rax = monotonic milliseconds
    mov eax, 228
    mov edi, 1
    mov esi, tsbuf
    syscall
    mov rax, [tsbuf]
    imul rax, 1000
    mov rcx, rax
    mov rax, [tsbuf+8]
    xor edx, edx
    mov esi, 1000000
    div rsi
    add rax, rcx
    ret

; ============================================================
; one-time initialisation
; ============================================================
init_zob:
    mov rax, 0x123456789ABCDEF1
    mov r8, 0x9E3779B97F4A7C15
    mov r9, 0xBF58476D1CE4E5B9
    mov r10, 0x94D049BB133111EB
    xor ecx, ecx
.l: add rax, r8
    mov rdx, rax
    mov rsi, rdx
    shr rsi, 30
    xor rdx, rsi
    imul rdx, r9
    mov rsi, rdx
    shr rsi, 27
    xor rdx, rsi
    imul rdx, r10
    mov rsi, rdx
    shr rsi, 31
    xor rdx, rsi
    mov [zobp+rcx*8], rdx
    inc ecx
    cmp ecx, 2048+16+8+1
    jl .l
    ret

; lmrtab[d*64+m] = floor(0.75 + log2(d)*log2(m)*0.2135)
init_lmr:
    push rbx
    push rbp
    mov ebx, 1
.d: mov ebp, 1
.m: fld1
    mov [tsbuf], ebx
    fild dword [tsbuf]
    fyl2x
    fld1
    mov [tsbuf], ebp
    fild dword [tsbuf]
    fyl2x
    fmulp
%ifdef TUNE
    TUSE 71
    TUSE 72
    fimul dword [tunetab+72*4]      ; -DTUNE: * T72 / 10000 + T71 / 1000 (defaults 2135 and 750: the curve below)
    fidiv dword [c_i10]
    fiadd dword [tunetab+71*4]
    fidiv dword [c_i1000]
%else
    fmul dword [c_lmr]
    fadd dword [c_075]
%endif
    fisttp dword [tsbuf]
    mov eax, [tsbuf]
%ifdef TUNE
    test eax, eax                   ; -DTUNE: any T71 / T72 gives a table entry in 0 .. 63
    jns .t0
    xor eax, eax
.t0:cmp eax, 63
    jle .t1
    mov eax, 63
.t1:
%endif
    mov ecx, ebx
    shl ecx, 6
    add ecx, ebp
    mov [lmrtab+rcx], al
    inc ebp
    cmp ebp, 64
    jl .m
    inc ebx
    cmp ebx, 64
    jl .d
    pop rbp
    pop rbx
    ret

; ============================================================
; state recomputation (used after FEN loading and by the verifier)
; ============================================================
recompute_all:
%if BB
    push rdi
    xor eax, eax
    mov ecx, 18
    mov edi, bbp                ; bbp (16 qwords) and bbc (2 qwords) are contiguous
    rep stosq
%endif
    mov qword [cnt], 0
    mov qword [cnt+8], 0
    xor r8d, r8d
    xor r9d, r9d                ; mg
    xor r10d, r10d              ; eg
    xor r11d, r11d              ; phase
    xor ecx, ecx
.l: test cl, 0x88
    jnz .n
    movzx eax, byte [board+rcx]
    test eax, eax
    jz .n
    inc byte [cnt+rax]
%if BB
    mov edi, ecx
    and edi, 7
    lea esi, [rcx+rdi]
    shr esi, 1                  ; 0..63
    xor edx, edx
    bts rdx, rsi
    or [bbp+rax*8], rdx
    mov edi, eax
    and edi, 8
    or [bbc+rdi], rdx
%endif
    mov edx, eax
    shl edx, 7
    add edx, ecx
    xor r8, [zobp+rdx*8]
    movsx esi, word [mgt+rdx*2]
    add r9d, esi
    movsx esi, word [egt+rdx*2]
    add r10d, esi
    movzx esi, byte [phasew+rax]
    add r11d, esi
    cmp eax, 6
    jne .k2
    mov [ksq], ecx
.k2:cmp eax, 14
    jne .n
    mov [ksq+4], ecx
.n: inc ecx
    cmp ecx, 128
    jl .l
    mov eax, [castle]
    xor r8, [zobc+rax*8]
    mov eax, [epsq]
    test eax, eax
    jz .ne
    and eax, 7
    xor r8, [zobe+rax*8]
.ne:cmp dword [stm], 0
    je .w
    xor r8, [zobs]
.w: mov [hashk], r8
    mov [mgsc], r9d
    mov [egsc], r10d
    mov [phase], r11d
%if BB
    pop rdi
%endif
    ret

verify_state:
    push rbx
    push rbp
    push r12
    push r13
    push r14
    push r15
    mov r12, [hashk]
    mov r13d, [mgsc]
    mov r14d, [egsc]
    mov r15d, [phase]
    mov ebx, [ksq]
    mov ebp, [ksq+4]
    push qword [cnt]
    push qword [cnt+8]
%if BB
    mov esi, bbp
    mov edi, bbsave
    mov ecx, 18
    rep movsq                   ; keep the incrementally maintained bitboards
%endif
    call recompute_all
    pop rax
    pop rdx
    cmp rax, [cnt+8]
    jne .bad
    cmp rdx, [cnt]
    jne .bad
    cmp r12, [hashk]
    jne .bad
    cmp r13d, [mgsc]
    jne .bad
    cmp r14d, [egsc]
    jne .bad
    cmp r15d, [phase]
    jne .bad
    cmp ebx, [ksq]
    jne .bad
    cmp ebp, [ksq+4]
    jne .bad
%if BB
    mov esi, bbp
    mov edi, bbsave
    mov ecx, 18
    repe cmpsq                  ; incremental bitboards == bitboards rebuilt from the mailbox
    jne .bad
    call verify_gen             ; bitboard gen == gen_88 (move sets), see_ge monotone
    test eax, eax
    jnz .bad
    xor ebx, ebx                ; every square, both colours: bitboard attacked() == mailbox attacked_88()
.xc:test bl, 0x88
    jnz .xn
    xor ebp, ebp
.xk:mov edi, ebx
    mov esi, ebp
    call attacked_88
    mov r12d, eax
    mov edi, ebx
    mov esi, ebp
    call attacked
    cmp eax, r12d
    jne .bad
    add ebp, 8
    cmp ebp, 16
    jl .xk
.xn:inc ebx
    cmp ebx, 128
    jl .xc
%endif
    pop r15
    pop r14
    pop r13
    pop r12
    pop rbp
    pop rbx
    ret
.bad:
    OBS s_verfail, s_verfail_l
    call ob_flush
%ifdef WIN
    mov eax, 60
%else
    mov eax, 231                ; exit_group (see p5_main .exit)
%endif
    mov edi, 2
    syscall

; ============================================================
; FEN loading.  rsi = zero-terminated string; returns rsi after the last field read
; ============================================================
setfen:
    push rbx
%if TM_CURVE
    mov dword [fullm0], 0       ; game ply of the position (TM_CURVE): 0 unless the FEN has a fullmove number
%endif
    ; clear board
    xor eax, eax
    mov ecx, 16
    mov edi, board
    rep stosq
    xor ebx, ebx                ; square
    mov r8d, 7                  ; rank
.pl:movzx eax, byte [rsi]
    inc rsi
    cmp al, ' '
    je .side
    test al, al
    jz .side
    cmp al, '/'
    jne .nsl
    dec r8d
    xor ebx, ebx
    jmp .pl
.nsl:
    cmp al, '1'
    jb .piece
    cmp al, '8'
    ja .piece
    sub eax, '0'
    add ebx, eax
    jmp .pl
.piece:
    xor ecx, ecx
    mov edx, eax
    cmp al, 'a'
    jb .upper
    sub edx, 32
    mov ecx, 8
.upper:
    ; edx = uppercase letter
    xor edi, edi
.fp:cmp byte [pchars+rdi], dl
    je .got
    inc edi
    cmp edi, 8
    jl .fp
    jmp .pl
.got:
    or edi, ecx                 ; piece code
    cmp ebx, 8                  ; ignore squares outside the board (malformed FEN)
    jae .skip
    cmp r8d, 7
    ja .skip
    mov eax, r8d
    shl eax, 4
    add eax, ebx
    mov [board+rax], dil
.skip:
    inc ebx
    jmp .pl
.side:
    movzx eax, byte [rsi]
    xor ecx, ecx
    cmp al, 'b'
    jne .sd
    mov ecx, 8
.sd:mov [stm], ecx
    add rsi, 2
    ; castling
    xor edx, edx
.cs:movzx eax, byte [rsi]
    inc rsi
    cmp al, ' '
    je .ep
    test al, al
    jz .ep
    cmp al, 'K'
    jne .c1
    or edx, 1
.c1:cmp al, 'Q'
    jne .c2
    or edx, 2
.c2:cmp al, 'k'
    jne .c3
    or edx, 4
.c3:cmp al, 'q'
    jne .cs
    or edx, 8
    jmp .cs
.ep:mov [castle], edx
    xor eax, eax
    mov [epsq], eax
    movzx ecx, byte [rsi]
    cmp cl, '-'
    je .epd
    cmp cl, 'a'
    jb .epd
    sub ecx, 'a'
    cmp ecx, 7                  ; file a..h
    ja .epd
    movzx edx, byte [rsi+1]
    mov eax, [stm]              ; the ep square lies on rank 6 when White is to move, on rank 3 when Black is
    shr eax, 3
    lea eax, [rax+rax*2]
    neg eax
    add eax, '6'
    cmp edx, eax
    jne .epd                    ; anything else would index outside the board in gen / domove: ignored
    sub edx, '1'
    shl edx, 4
    add edx, ecx
    mov [epsq], edx
    inc rsi
.epd:
    inc rsi
    cmp byte [rsi], ' '
    jne .hm
    inc rsi
.hm:
    xor eax, eax
    mov [halfm], eax
    movzx ecx, byte [rsi]
    sub ecx, '0'
    cmp ecx, 9
    ja .fin
.hl:movzx ecx, byte [rsi]
    sub ecx, '0'
    cmp ecx, 9
    ja .hd
    imul eax, 10
    add eax, ecx
    inc rsi
    jmp .hl
.hd:mov [halfm], eax
    cmp byte [rsi], ' '
    jne .fin
    inc rsi
%if TM_CURVE
    xor edx, edx                ; the fullmove number -> game ply of the position (fullm0, TM_CURVE)
.fl:movzx ecx, byte [rsi]
    sub ecx, '0'
    cmp ecx, 9
    ja .flz
    inc rsi
    cmp edx, 100000
    jae .fl                     ; (absurd numbers: the digits are skipped, the value stays bounded)
    imul edx, 10
    add edx, ecx
    jmp .fl
.flz:
    test edx, edx
    jz .fin
    dec edx
    add edx, edx                ; 2 * (fullmove - 1)
    mov eax, [stm]
    shr eax, 3
    add edx, eax                ; + 1 with Black to move
    mov [fullm0], edx
%else
.fl:movzx ecx, byte [rsi]
    sub ecx, '0'
    cmp ecx, 9
    ja .fin
    inc rsi
    jmp .fl
%endif
.fin:
    mov dword [gply], 0
    mov dword [ply], 0
    call recompute_all
    pop rbx
    ret
%endif                          ; UNIT == 0 (ob_move .. setfen)

; ============================================================
; attacked(edi = square, esi = attacking colour 0/8) -> eax = 1 if attacked
; clobbers: eax ecx edx esi r8 r9 r10 r11
; ============================================================
%if (UNIT == 0) || (BB == 0)       ; BB=1: attacked_88 is the verify reference (unit 0 only); BB=0: it is attacked() itself
%if BB
attacked_88:                    ; mailbox version, kept for the verify cross-check
%else
attacked:
%endif
    lea r9d, [rsi+1]            ; pawn code of attacker
    test esi, esi
    jnz .bp
    lea ecx, [rdi-15]
    test cl, 0x88
    jnz .w2
    cmp byte [board+rcx], r9b
    je .yes
.w2:lea ecx, [rdi-17]
    test cl, 0x88
    jnz .np
    cmp byte [board+rcx], r9b
    je .yes
    jmp .np
.bp:lea ecx, [rdi+15]
    test cl, 0x88
    jnz .b2
    cmp byte [board+rcx], r9b
    je .yes
.b2:lea ecx, [rdi+17]
    test cl, 0x88
    jnz .np
    cmp byte [board+rcx], r9b
    je .yes
.np:
    lea r9d, [rsi+2]            ; knight
    xor edx, edx
.kn:movsx eax, byte [knightoff+rdx]
    lea ecx, [rdi+rax]
    test cl, 0x88
    jnz .kn2
    cmp byte [board+rcx], r9b
    je .yes
.kn2:
    inc edx
    cmp edx, 8
    jl .kn
    lea r9d, [rsi+6]            ; king
    xor edx, edx
.kg:movsx eax, byte [kingoff+rdx]
    lea ecx, [rdi+rax]
    test cl, 0x88
    jnz .kg2
    cmp byte [board+rcx], r9b
    je .yes
.kg2:
    inc edx
    cmp edx, 8
    jl .kg
    lea r9d, [rsi+3]            ; bishop
    lea r10d, [rsi+5]           ; queen
    xor edx, edx
.bd:movsx eax, byte [bishoff+rdx]
    mov ecx, edi
.bs:add ecx, eax
    test cl, 0x88
    jnz .bn
    movzx r11d, byte [board+rcx]
    test r11d, r11d
    jz .bs
    cmp r11d, r9d
    je .yes
    cmp r11d, r10d
    je .yes
.bn:inc edx
    cmp edx, 4
    jl .bd
    lea r9d, [rsi+4]            ; rook
    xor edx, edx
.rd:movsx eax, byte [rookoff+rdx]
    mov ecx, edi
.rs:add ecx, eax
    test cl, 0x88
    jnz .rn
    movzx r11d, byte [board+rcx]
    test r11d, r11d
    jz .rs
    cmp r11d, r9d
    je .yes
    cmp r11d, r10d
    je .yes
.rn:inc edx
    cmp edx, 4
    jl .rd
    xor eax, eax
    ret
.yes:
    mov eax, 1
    ret
%endif

%if (SEE_ANY) && (BB == 0)      ; the BB build has its own see_ge (p2b_bb.asm)
; ============================================================
; see_lva(edi = square, esi = colour 0/8) -> eax = square of the least valuable piece of that colour attacking
; the square (-1 if none), edx = its piece code.  Pieces are read from the board as it is NOW, so pieces taken
; off the board by see_ge uncover x-ray attackers by themselves.  Order: P N B R Q K.
; clobbers: eax ecx edx r8 r9 r10 r11
; ============================================================
see_lva:
    lea r9d, [rsi+1]            ; own pawn code
    test esi, esi
    jnz .bp
    lea ecx, [rdi-15]
    test cl, 0x88
    jnz .w2
    cmp byte [board+rcx], r9b
    je .got
.w2:lea ecx, [rdi-17]
    test cl, 0x88
    jnz .kn
    cmp byte [board+rcx], r9b
    je .got
    jmp .kn
.bp:lea ecx, [rdi+15]
    test cl, 0x88
    jnz .b2
    cmp byte [board+rcx], r9b
    je .got
.b2:lea ecx, [rdi+17]
    test cl, 0x88
    jnz .kn
    cmp byte [board+rcx], r9b
    je .got
.kn:lea r9d, [rsi+2]            ; knight
    xor edx, edx
.k1:movsx eax, byte [knightoff+rdx]
    lea ecx, [rdi+rax]
    test cl, 0x88
    jnz .k2
    cmp byte [board+rcx], r9b
    je .got
.k2:inc edx
    cmp edx, 8
    jl .k1
    lea r9d, [rsi+3]            ; bishop
    lea r10d, [rsi+5]           ; queen
    mov r8d, -1                 ; first queen seen on a ray
    xor edx, edx
.bd:movsx eax, byte [bishoff+rdx]
    mov ecx, edi
.bs:add ecx, eax
    test cl, 0x88
    jnz .bn
    movzx r11d, byte [board+rcx]
    test r11d, r11d
    jz .bs
    cmp r11d, r9d
    je .got
    cmp r11d, r10d
    jne .bn
    mov r8d, ecx
.bn:inc edx
    cmp edx, 4
    jl .bd
    lea r9d, [rsi+4]            ; rook
    xor edx, edx
.rd:movsx eax, byte [rookoff+rdx]
    mov ecx, edi
.rs:add ecx, eax
    test cl, 0x88
    jnz .rn
    movzx r11d, byte [board+rcx]
    test r11d, r11d
    jz .rs
    cmp r11d, r9d
    je .got
    cmp r11d, r10d
    jne .rn
    mov r8d, ecx
.rn:inc edx
    cmp edx, 4
    jl .rd
    test r8d, r8d
    js .kg
    mov ecx, r8d                ; queen
    mov r9d, r10d
    jmp .got
.kg:lea r9d, [rsi+6]            ; king
    xor edx, edx
.g1:movsx eax, byte [kingoff+rdx]
    lea ecx, [rdi+rax]
    test cl, 0x88
    jnz .g2
    cmp byte [board+rcx], r9b
    je .got
.g2:inc edx
    cmp edx, 8
    jl .g1
    mov eax, -1
    ret
.got:
    mov eax, ecx
    mov edx, r9d
    ret

; ============================================================
; see_ge(edi = move, esi = threshold) -> eax = 1 if the static exchange value of the move is >= threshold.
; Swap loop in the null-window form, values from pvalue (P100 N320 B330 R500 Q900).
; Not modelled: pins (a pinned piece may "capture"), promotion and en passant (the move counts as 0).
; The captured pieces are taken off the board while the exchange is played out and put back before returning.
; Preserves everything except eax ecx edx esi edi r8-r11.
; ============================================================
see_ge:
    push rbx
    push rbp
    push r12
    push r13
    push r14
    push r15
    mov ebp, esi                ; threshold
    mov r12d, edi
    shr r12d, 7
    and r12d, 127               ; to
    and edi, 127                ; from
    xor ebx, ebx                ; number of pieces taken off the board (saved on the stack)
    movzx eax, byte [board+rdi] ; mover
    movzx ecx, byte [board+r12] ; victim
    mov edx, eax
    and edx, 7
    cmp edx, 1
    jne .norm
    mov edx, r12d
    and edx, 0xF0
    jz .special                 ; pawn reaching the last rank: promotion
    cmp edx, 0x70
    je .special
    test ecx, ecx
    jnz .norm
    mov edx, r12d
    sub edx, edi
    test dl, 1
    jnz .special                ; diagonal pawn step onto an empty square: en passant
.norm:
    mov r13d, [pvalue+rcx*4]
    sub r13d, ebp               ; swap = value(victim) - threshold
    js .lose
    mov edx, [pvalue+rax*4]
    sub edx, r13d
    mov r13d, edx               ; swap = value(mover) - swap
    jle .win
    mov r15d, eax
    and r15d, 8                 ; colour of the mover
    shl eax, 8
    or eax, edi
    push rax                    ; remember (piece << 8 | square), then lift the mover off the board
    inc ebx
    mov byte [board+rdi], 0
    mov r14d, 1                 ; res
.loop:
    xor r15d, 8                 ; side to recapture
    mov edi, r12d
    mov esi, r15d
    call see_lva
    test eax, eax
    js .done                    ; no recapture: res stands
    xor r14d, 1
    mov ecx, edx
    and ecx, 7
    cmp ecx, 6
    je .king
    neg r13d
    add r13d, [pvalue+rdx*4]    ; swap = value(piece) - swap
    cmp r13d, r14d
    jl .done
    mov ecx, edx
    shl ecx, 8
    or ecx, eax
    push rcx
    inc ebx
    mov byte [board+rax], 0
    jmp .loop
.king:                          ; the king may only take when nothing of the other side still attacks the square
    mov ecx, edx
    shl ecx, 8
    or ecx, eax
    push rcx
    inc ebx
    mov byte [board+rax], 0
    mov edi, r12d
    mov esi, r15d
    xor esi, 8
    call attacked
    test eax, eax
    jz .done
    xor r14d, 1
    jmp .done
.special:                       ; promotion / en passant: static value taken as 0
    xor r14d, r14d
    test ebp, ebp
    setle r14b
    jmp .done
.lose:
    xor r14d, r14d
    jmp .done
.win:
    mov r14d, 1
.done:
    test ebx, ebx
    jz .ret
.rs:pop rax                     ; put every taken piece back
    mov ecx, eax
    and ecx, 255
    shr eax, 8
    mov [board+rcx], al
    dec ebx
    jnz .rs
.ret:
    mov eax, r14d
    pop r15
    pop r14
    pop r13
    pop r12
    pop rbp
    pop rbx
    ret
%endif

; ============================================================
; gen(rdi = out buffer of dwords, esi = mode) -> rax = end
; mode 0: all pseudo-legal moves; mode 1: captures, en passant, queen promotions
; move = from | to<<7 | promo<<14   (promo 0=N 1=B 2=R 3=Q)
; ============================================================
%macro EMITPROMO 0              ; from = ebx, to = ecx
    mov edx, ecx
    shl edx, 7
    or edx, ebx
    lea eax, [rdx+(3<<14)]
    mov [r15], eax
    add r15, 4
    test r14d, r14d
    jnz %%d
    lea eax, [rdx+(2<<14)]
    mov [r15], eax
    lea eax, [rdx+(1<<14)]
    mov [r15+4], eax
    mov [r15+8], edx
    add r15, 12
%%d:
%endmacro

%macro PAWNCAP 1
    lea ecx, [rbx+r8+%1]
    test cl, 0x88
    jnz %%no
    movzx edx, byte [board+rcx]
    test edx, edx
    jz %%ep
    and edx, 8
    cmp edx, r12d
    je %%no
    mov eax, ecx
    and eax, 0xF0
    cmp eax, r9d
    jne %%plain
    EMITPROMO
    jmp %%no
%%plain:
    mov edx, ecx
    shl edx, 7
    or edx, ebx
    mov [r15], edx
    add r15, 4
    jmp %%no
%%ep:
    mov eax, [epsq]
    test eax, eax
    jz %%no
    cmp ecx, eax
    jne %%no
    mov edx, ecx
    shl edx, 7
    or edx, ebx
    mov [r15], edx
    add r15, 4
%%no:
%endmacro

%macro CASTLEGEN 6              ; base, kbit, qbit, enemy colour, king code, rook code
    mov eax, [castle]
    test eax, %2
    jz %%q
    cmp byte [board+%1+5], 0
    jne %%q
    cmp byte [board+%1+6], 0
    jne %%q
    cmp byte [board+%1+4], %5
    jne %%q
    cmp byte [board+%1+7], %6
    jne %%q
    mov edi, %1+4
    mov esi, %4
    call attacked
    test eax, eax
    jnz %%q
    mov edi, %1+5
    mov esi, %4
    call attacked
    test eax, eax
    jnz %%q
    mov dword [r15], ((%1+6)<<7)|(%1+4)
    add r15, 4
%%q:
    mov eax, [castle]
    test eax, %3
    jz %%end
    cmp byte [board+%1+3], 0
    jne %%end
    cmp byte [board+%1+2], 0
    jne %%end
    cmp byte [board+%1+1], 0
    jne %%end
    cmp byte [board+%1+4], %5
    jne %%end
    cmp byte [board+%1+0], %6
    jne %%end
    mov edi, %1+4
    mov esi, %4
    call attacked
    test eax, eax
    jnz %%end
    mov edi, %1+3
    mov esi, %4
    call attacked
    test eax, eax
    jnz %%end
    mov dword [r15], ((%1+2)<<7)|(%1+4)
    add r15, 4
%%end:
%endmacro

%if (UNIT == 0) || (BB == 0)       ; BB=1: gen_88 is the verify reference (unit 0 only); BB=0: it is gen() itself
%if BB
gen_88:                         ; mailbox generator, reference for the verify cross-check (gen is in p2b_bb.asm)
%else
gen:
%endif
    push rbx
    push rbp
    push r12
    push r13
    push r14
    push r15
    mov r15, rdi
    mov r14d, esi
    mov r12d, [stm]
    xor ebx, ebx
.sq:test bl, 0x88
    jnz .nsq
    movzx eax, byte [board+rbx]
    test eax, eax
    jz .nsq
    mov ecx, eax
    and ecx, 8
    cmp ecx, r12d
    jne .nsq
    and eax, 7
    cmp eax, 1
    je .pawn
    mov r8d, eax
    mov r13, [ptab+rax*8]
    mov ebp, [pcnt+rax*4]
.dir:
    movsx eax, byte [r13]
    inc r13
    mov ecx, ebx
.step:
    add ecx, eax
    test cl, 0x88
    jnz .dnext
    movzx edx, byte [board+rcx]
    test edx, edx
    jz .empty
    and edx, 8
    cmp edx, r12d
    je .dnext
    mov edx, ecx
    shl edx, 7
    or edx, ebx
    mov [r15], edx
    add r15, 4
    jmp .dnext
.empty:
    test r14d, r14d
    jnz .nq
    mov edx, ecx
    shl edx, 7
    or edx, ebx
    mov [r15], edx
    add r15, 4
.nq:cmp r8d, 3
    jb .dnext
    cmp r8d, 5
    ja .dnext
    jmp .step
.dnext:
    dec ebp
    jnz .dir
    jmp .nsq
.pawn:
    mov r8d, 16
    mov r9d, 0x70
    mov r10d, 0x10
    test r12d, r12d
    jz .pw
    mov r8d, -16
    xor r9d, r9d
    mov r10d, 0x60
.pw:lea ecx, [rbx+r8]
    cmp byte [board+rcx], 0
    jne .pcap
    mov eax, ecx
    and eax, 0xF0
    cmp eax, r9d
    jne .quiet
    EMITPROMO
    jmp .pcap
.quiet:
    test r14d, r14d
    jnz .pcap
    mov edx, ecx
    shl edx, 7
    or edx, ebx
    mov [r15], edx
    add r15, 4
    mov eax, ebx
    and eax, 0xF0
    cmp eax, r10d
    jne .pcap
    add ecx, r8d
    cmp byte [board+rcx], 0
    jne .pcap
    mov edx, ecx
    shl edx, 7
    or edx, ebx
    mov [r15], edx
    add r15, 4
.pcap:
    PAWNCAP -1
    PAWNCAP 1
.nsq:
    inc ebx
    cmp ebx, 128
    jl .sq
    test r14d, r14d
    jnz .done
    test r12d, r12d
    jnz .blk
    CASTLEGEN 0, 1, 2, 8, 6, 4
    jmp .done
.blk:
    CASTLEGEN 0x70, 4, 8, 0, 14, 12
.done:
    mov rax, r15
    pop r15
    pop r14
    pop r13
    pop r12
    pop rbp
    pop rbx
    ret
%endif

; ============================================================
; make / unmake
; ============================================================
%macro PREM 0                   ; piece = esi, square = ecx
    mov rdx, rsi
    shl edx, 7
    add edx, ecx
    xor r12, [zobp+rdx*8]
    movsx eax, word [mgt+rdx*2]
    sub r13d, eax
    movsx eax, word [egt+rdx*2]
    sub r14d, eax
    movzx eax, byte [phasew+rsi]
    sub r15d, eax
    dec byte [cnt+rsi]
%if BB
    BBX rsi, ecx, rax, eax, rdx
%endif
    mov byte [board+rcx], 0
%endmacro
%macro PADD 0
    mov rdx, rsi
    shl edx, 7
    add edx, ecx
    xor r12, [zobp+rdx*8]
    movsx eax, word [mgt+rdx*2]
    add r13d, eax
    movsx eax, word [egt+rdx*2]
    add r14d, eax
    movzx eax, byte [phasew+rsi]
    add r15d, eax
    inc byte [cnt+rsi]
%if BB
    BBX rsi, ecx, rax, eax, rdx
%endif
    mov [board+rcx], sil
%endmacro

; domove(edi = move) -> eax = 1 if the move was legal (own king not attacked)
; The move is ALWAYS applied; the caller must call undomove afterwards.
domove:
    push rbx
    push rbp
    push r12
    push r13
    push r14
    push r15
    mov r9d, edi
    mov ebx, edi
    and ebx, 127                ; from
    mov ebp, edi
    shr ebp, 7
    and ebp, 127                ; to
    mov r10d, edi
    shr r10d, 14                ; promo
    mov eax, [gply]
    shl eax, 5
    lea r11, [ustack+rax]
    mov r12, [hashk]
    mov r13d, [mgsc]
    mov r14d, [egsc]
    mov r15d, [phase]
    mov [r11], r12
    mov [r11+8], r13d
    mov [r11+12], r14d
    mov [r11+24], r15d
    mov [r11+16], r9w
    mov eax, [castle]
    mov [r11+19], al
    mov r8d, [epsq]
    mov [r11+20], r8b
    mov edx, [halfm]
    mov [r11+22], dx
    inc dword [gply]
    xor r12, [zobc+rax*8]
    test r8d, r8d
    jz .a
    mov ecx, r8d
    and ecx, 7
    xor r12, [zobe+rcx*8]
.a: mov dword [epsq], 0
    xor r12, [zobs]
    movzx esi, byte [board+rbx]
    mov [r11+28], sil
    movzx edi, byte [board+rbp]
    mov eax, esi
    and eax, 8
    mov r9d, eax                ; mover colour bits
    ; halfmove clock
    inc edx
    mov eax, esi
    and eax, 7
    cmp eax, 1
    je .reset
    test edi, edi
    jnz .reset
    mov [halfm], edx
    jmp .hm
.reset:
    mov dword [halfm], 0
.hm:
    cmp eax, 1
    jne .normcap
    test r8d, r8d
    jz .normcap
    cmp ebp, r8d
    jne .normcap
    ; en passant capture
    mov ecx, ebp
    test r9d, r9d
    jnz .epb
    sub ecx, 16
    jmp .epg
.epb:
    add ecx, 16
.epg:
    movzx esi, byte [board+rcx]
    mov [r11+18], sil
    PREM
    jmp .moveit
.normcap:
    mov byte [r11+18], 0
    test edi, edi
    jz .moveit
    mov [r11+18], dil
    mov esi, edi
    mov ecx, ebp
    PREM
.moveit:
    movzx esi, byte [r11+28]
    mov ecx, ebx
    PREM
    mov eax, esi
    and eax, 7
    cmp eax, 1
    jne .place
    mov eax, ebp
    and eax, 0xF0
    cmp eax, 0x70
    je .promo
    test eax, eax
    jnz .place
.promo:
    lea esi, [r10+2]
    or esi, r9d
.place:
    mov ecx, ebp
    PADD
    movzx eax, byte [r11+28]
    and eax, 7
    cmp eax, 6
    jne .nking
    mov eax, r9d
    shr eax, 3
    mov [ksq+rax*4], ebp
    mov eax, ebp
    sub eax, ebx
    cmp eax, 2
    je .ks
    cmp eax, -2
    jne .nking
    lea ecx, [rbx-4]
    lea r8d, [rbx-1]
    jmp .rk
.ks:lea ecx, [rbx+3]
    lea r8d, [rbx+1]
.rk:mov esi, 4
    or esi, r9d
    PREM
    mov ecx, r8d
    mov esi, 4
    or esi, r9d
    PADD
.nking:
    ; castling rights
    mov eax, [castle]
    movzx ecx, byte [cmask+rbx]
    and eax, ecx
    movzx ecx, byte [cmask+rbp]
    and eax, ecx
    mov [castle], eax
    xor r12, [zobc+rax*8]
    ; en passant square after a double push (only if an enemy pawn can capture)
    movzx eax, byte [r11+28]
    and eax, 7
    cmp eax, 1
    jne .noset
    mov eax, ebp
    sub eax, ebx
    cmp eax, 32
    je .dp
    cmp eax, -32
    jne .noset
.dp:mov eax, r9d
    xor eax, 8
    or eax, 1                   ; enemy pawn code
    lea ecx, [rbp-1]
    test cl, 0x88
    jnz .r
    cmp byte [board+rcx], al
    je .setep
.r: lea ecx, [rbp+1]
    test cl, 0x88
    jnz .noset
    cmp byte [board+rcx], al
    jne .noset
.setep:
    lea ecx, [rbx+rbp]
    shr ecx, 1
    mov [epsq], ecx
    and ecx, 7
    xor r12, [zobe+rcx*8]
.noset:
    mov [hashk], r12
    mov [mgsc], r13d
    mov [egsc], r14d
    mov [phase], r15d
%if TTPREFETCH
    mov eax, r12d                   ; start loading the transposition table bucket of the new position now (same index arithmetic as tt_probe);
    and eax, [ttmaskv]              ; the child node probes it a moment later.  A prefetch never faults, never changes the tree.
    shl rax, 4
    add rax, [ttbase]
    prefetcht0 [rax]
%endif
    xor dword [stm], 8
    mov eax, r9d
    shr eax, 3
    mov edi, [ksq+rax*4]
    mov esi, [stm]
    call attacked
    xor eax, 1
    pop r15
    pop r14
    pop r13
    pop r12
    pop rbp
    pop rbx
    ret

undomove:
    push rbx
    push rbp
%if BB
    push r10                    ; the bitboard updates use r10; the mailbox-only build does not
%endif
    dec dword [gply]
    mov eax, [gply]
    shl eax, 5
    lea r11, [ustack+rax]
    movzx eax, word [r11+16]
    mov ebx, eax
    and ebx, 127
    mov ebp, eax
    shr ebp, 7
    and ebp, 127
    mov rax, [r11]
    mov [hashk], rax
    mov eax, [r11+8]
    mov [mgsc], eax
    mov eax, [r11+12]
    mov [egsc], eax
    mov eax, [r11+24]
    mov [phase], eax
    movzx eax, byte [r11+19]
    mov [castle], eax
    movzx r8d, byte [r11+20]
    mov [epsq], r8d
    movzx eax, word [r11+22]
    mov [halfm], eax
    xor dword [stm], 8
    movzx esi, byte [r11+28]
    movzx edi, byte [r11+18]
    mov r9d, esi
    and r9d, 8
    movzx edx, byte [board+rbp]
    cmp edx, esi
    je .nop
    dec byte [cnt+rdx]
    inc byte [cnt+rsi]
.nop:
%if BB
    BBX rdx, ebp, rax, eax, r10 ; the piece standing on 'to' (a promoted piece after a promotion) leaves ...
    BBX rsi, ebx, rax, eax, r10 ; ... and the mover returns to 'from'
%endif
    mov [board+rbx], sil
    mov eax, esi
    and eax, 7
    cmp eax, 1
    jne .normal
    test r8d, r8d
    jz .normal
    cmp ebp, r8d
    jne .normal
    mov byte [board+rbp], 0
    mov ecx, ebp
    test r9d, r9d
    jnz .epb
    sub ecx, 16
    jmp .epr
.epb:
    add ecx, 16
.epr:
    mov [board+rcx], dil
    inc byte [cnt+rdi]
%if BB
    BBX rdi, ecx, rax, eax, r10 ; en passant: the captured pawn comes back
%endif
    jmp .cast
.normal:
    mov [board+rbp], dil
    test edi, edi
    jz .cast
    inc byte [cnt+rdi]
%if BB
    BBX rdi, ebp, rax, eax, r10 ; the captured piece comes back
%endif
.cast:
    mov eax, esi
    and eax, 7
    cmp eax, 6
    jne .done
    mov eax, r9d
    shr eax, 3
    mov [ksq+rax*4], ebx
    mov eax, ebp
    sub eax, ebx
    cmp eax, 2
    je .ks
    cmp eax, -2
    jne .done
    lea ecx, [rbx-1]
    lea edx, [rbx-4]
    jmp .rk
.ks:lea ecx, [rbx+1]
    lea edx, [rbx+3]
.rk:mov byte [board+rcx], 0
    mov al, 4
    or al, r9b
    mov [board+rdx], al
%if BB
    mov esi, 4
    or esi, r9d                 ; rook code (the mover is no longer needed)
    BBX rsi, ecx, rax, eax, r10 ; rook leaves its castled square ...
    BBX rsi, edx, rax, eax, r10 ; ... and goes back to the corner
%endif
.done:
%if BB
    pop r10
%endif
    pop rbp
    pop rbx
    ret

domove_null:
    mov eax, [gply]
    shl eax, 5
    lea r11, [ustack+rax]
    mov rax, [hashk]
    mov [r11], rax
    mov edx, [epsq]
    mov [r11+20], dl
    mov eax, [halfm]
    mov [r11+22], ax
    inc dword [gply]
    mov rax, [hashk]
    xor rax, [zobs]
    test edx, edx
    jz .n
    mov ecx, edx
    and ecx, 7
    xor rax, [zobe+rcx*8]
.n: mov [hashk], rax
%if TTPREFETCH
    mov ecx, eax                    ; prefetch the bucket of the null-move position (rcx is free here)
    and ecx, [ttmaskv]
    shl rcx, 4
    add rcx, [ttbase]
    prefetcht0 [rcx]
%endif
    mov dword [epsq], 0
    inc dword [halfm]
    xor dword [stm], 8
    ret

undomove_null:
    dec dword [gply]
    mov eax, [gply]
    shl eax, 5
    lea r11, [ustack+rax]
    mov rax, [r11]
    mov [hashk], rax
    movzx eax, byte [r11+20]
    mov [epsq], eax
    movzx eax, word [r11+22]
    mov [halfm], eax
    xor dword [stm], 8
    ret

; ============================================================
; perft(edi = depth) -> rax = leaf count
; ============================================================
%define MOVEBUF 1280
%if UNIT == 0
perft:
    push rbx
    push rbp
    push r12
    push r13
    push r14
    push r15
    sub rsp, MOVEBUF
    mov r12d, edi
    mov eax, 1
    test edi, edi
    jz .ret
    mov rdi, rsp
    xor esi, esi
    call gen
    mov r13, rax
    mov r14, rsp
    xor r15d, r15d
.loop:
    cmp r14, r13
    jae .done
    mov edi, [r14]
    add r14, 4
    call domove
    test eax, eax
    jz .undo
    cmp dword [verifyflag], 0
    je .nv
    call verify_state
.nv:cmp r12d, 1
    je .leaf
    lea edi, [r12-1]
    call perft
    add r15, rax
    jmp .undo
.leaf:
    inc r15
.undo:
    call undomove
    jmp .loop
.done:
    mov rax, r15
.ret:
    add rsp, MOVEBUF
    pop r15
    pop r14
    pop r13
    pop r12
    pop rbp
    pop rbx
    ret
%endif                          ; UNIT == 0 (perft)
