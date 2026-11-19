
; ============================================================
; Bitboards (build with -DBB=1).  Stage 1-3 of the 0x88 -> magic bitboard migration:
;   * the 0x88 mailbox stays the master board; move format, tables and search are untouched
;   * bbp[piece code] / bbc[colour] mirror the mailbox, updated in domove / undomove / recompute_all
;   * attacked() looks sliders up in "fancy" magic tables (rook 800 KiB + bishop 41 KiB, built in init_bb)
; Bitboard squares: 0..63, a1 = 0 ... h8 = 63.   0x88 square s  ->  (s + (s & 7)) >> 1
; Data (rtab, btab, knight_att, king_att, patt_to, ATABN) is generated: tools/gen_magics.c -> p2b_bb_data.inc
; ============================================================
%if BB
%if UNIT == 0
section .bss
alignb 64
bbsave:     resq 18             ; verify_state: copy of bbp/bbc before the recomputation   (bbp / bbc: thread-local, p1_data.asm)
to7:        resd 64             ; square 0..63 -> (0x88 square) << 7, the "to" field of a move
vbufa:      resd 512            ; verify_gen: move lists of gen and gen_88
vbufb:      resd 512
alignb 64
atab:       resq ATABN

section .data
rdirs:      db 1,0, -1,0, 0,1, 0,-1
bdirs:      db 1,1, 1,-1, -1,1, -1,-1
align 8
k_nfa:      dq 0xFEFEFEFEFEFEFEFE   ; not file a
k_nfh:      dq 0x7F7F7F7F7F7F7F7F   ; not file h
k_r3:       dq 0x0000000000FF0000   ; rank 3
k_r6:       dq 0x0000FF0000000000   ; rank 6
k_r8:       dq 0xFF00000000000000   ; rank 8
k_r1:       dq 0x00000000000000FF   ; rank 1
k_nr8:      dq 0x00FFFFFFFFFFFFFF
k_nr1:      dq 0xFFFFFFFFFFFFFF00
vthr:       dd -900, -330, -100, -1, 0, 1, 100, 330, 900
%endif                          ; UNIT == 0

section .text
%if UNIT == 0
; ------------------------------------------------------------
; bb_slide(edi = square 0..63, rsi = occupancy, rdx = direction table: 4 pairs of (dfile, drank)) -> rax = attacked squares
; slow reference walk, used only to fill the attack tables.  clobbers rax rcx rdx r8-r11
; ------------------------------------------------------------
bb_slide:
    push rbx
    xor eax, eax
    mov r8d, 4
.d: movsx r9d, byte [rdx]
    movsx r10d, byte [rdx+1]
    add rdx, 2
    mov ecx, edi
    and ecx, 7                  ; file
    mov r11d, edi
    shr r11d, 3                 ; rank
.s: add ecx, r9d
    add r11d, r10d
    cmp ecx, 7
    ja .nx                      ; (unsigned: also catches -1)
    cmp r11d, 7
    ja .nx
    lea ebx, [r11*8+rcx]
    bts rax, rbx
    bt rsi, rbx
    jnc .s                      ; stop after the first blocker (which is included)
.nx:dec r8d
    jnz .d
    pop rbx
    ret

; ------------------------------------------------------------
; init_bb: fill the rook and bishop attack tables for every subset of every relevance mask
; ------------------------------------------------------------
init_bb:
    push rbx
    push rbp
    push r12
    push r13
    push r14
    push r15
    xor eax, eax
.t7:mov ecx, eax
    and ecx, 0x38
    add ecx, eax
    shl ecx, 7
    mov [to7+rax*4], ecx        ; 0x88 square << 7
    inc eax
    cmp eax, 64
    jl .t7
    xor r15d, r15d              ; 0 = rook, 1 = bishop
.tab:
    mov r14d, rtab
    mov r13d, rdirs
    test r15d, r15d
    jz .go
    mov r14d, btab
    mov r13d, bdirs
.go:xor ebp, ebp                ; square
.sq:mov r12, rbp
    shl r12, 5
    add r12, r14                ; record
    xor ebx, ebx                ; subset of the mask
.sub:
    mov edi, ebp
    mov rsi, rbx
    mov rdx, r13
    call bb_slide
    mov r8, rbx
    imul r8, [r12+8]
    mov ecx, [r12+24]
    shr r8, cl
    mov r9, [r12+16]
    mov [r9+r8*8], rax
    sub rbx, [r12]
    and rbx, [r12]              ; next subset (carry-rippler); back to 0 = done
    jnz .sub
    inc ebp
    cmp ebp, 64
    jl .sq
    inc r15d
    cmp r15d, 2
    jl .tab
    pop r15
    pop r14
    pop r13
    pop r12
    pop rbp
    pop rbx
    ret
%endif                          ; UNIT == 0 (bb_slide, init_bb)

; ------------------------------------------------------------
; attacked(edi = 0x88 square, esi = attacking colour 0/8) -> eax = 1 if attacked   (bitboard version)
; same contract as the mailbox version (attacked_88): clobbers eax ecx edx esi r8 r9 r10 r11, keeps edi and everything else
; ------------------------------------------------------------
attacked:
    mov eax, edi
    and eax, 7
    lea r8d, [rax+rdi]
    shr r8d, 1                  ; r8 = square 0..63
    mov r9d, esi
    shl r9d, 6                  ; 0 / 512: patt_to[attacker colour]
    mov rax, [patt_to+r9+r8*8]
    lea ecx, [rsi+1]
    and rax, [bbp+rcx*8]        ; attacker pawns
    jnz .yes
    mov rax, [knight_att+r8*8]
    lea ecx, [rsi+2]
    and rax, [bbp+rcx*8]
    jnz .yes
    mov rax, [king_att+r8*8]
    lea ecx, [rsi+6]
    and rax, [bbp+rcx*8]
    jnz .yes
    mov r10, [bbc]
    or r10, [bbc+8]             ; occupancy
    lea ecx, [rsi+5]
    mov r11, [bbp+rcx*8]        ; queens
    lea ecx, [rsi+3]
    mov r9, [bbp+rcx*8]
    or r9, r11                  ; bishops | queens
    lea ecx, [rsi+4]
    or r11, [bbp+rcx*8]         ; rooks | queens
    shl r8d, 5                  ; record offset
    mov rax, r10
    and rax, [btab+r8]
    imul rax, [btab+r8+8]
    mov ecx, [btab+r8+24]
    shr rax, cl
    mov rdx, [btab+r8+16]
    mov rax, [rdx+rax*8]
    and rax, r9
    jnz .yes
    mov rax, r10
    and rax, [rtab+r8]
    imul rax, [rtab+r8+8]
    mov ecx, [rtab+r8+24]
    shr rax, cl
    mov rdx, [rtab+r8+16]
    mov rax, [rdx+rax*8]
    and rax, r11
    jnz .yes
    xor eax, eax
    ret
.yes:
    mov eax, 1
    ret

%if PREPRUNE
; ------------------------------------------------------------
; pp_ok(edi = move) -> eax:  0 = illegal (own king would be attacked)   1 = legal, gives no check
;                            2 = legal, gives check                      3 = not decided here: the caller makes the move
; Answers from the bitboards WITHOUT making the move ("pre-make pruning").  Contract: the side to move is NOT in check.
; Decided: quiet moves (empty destination) of knights, bishops, rooks, queens and non-promoting straight pawn pushes.
; Everything else (king moves incl. castling, captures, en passant, promotions) returns 3.
;   legality : the move is quiet and nothing attacks the king now, so only a slider can appear: look the sliders up from the own king
;              square with the occupancy AFTER the move (this covers pins, and a pinned piece that moves along its pin line)
;   check    : knight / pawn: the destination attacks the enemy king; any piece: a slider of ours attacks the enemy king through the
;              occupancy after the move (direct check by a slider, and discovered check; the moving slider's from / to bits are swapped)
; clobbers rax rcx rdx rsi rdi r8 r9 r10 r11 (as attacked(); rbx is saved).  Checked against make + in_check by tests/t_ppverify.py.
; ------------------------------------------------------------
%macro PPLOOK 3                 ; table, record offset register (square*32), occupancy register  ->  rax = slider attacks; clobbers rcx
    mov rax, %3
    and rax, [%1+%2]
    imul rax, [%1+%2+8]
    mov ecx, [%1+%2+24]
    shr rax, cl
    mov rcx, [%1+%2+16]
    mov rax, [rcx+rax*8]
%endmacro

pp_ok:
    push rbx
    mov esi, edi
    and esi, 127                ; from (0x88)
    shr edi, 7
    and edi, 127                ; to (0x88)
    movzx eax, byte [board+rsi]
    mov ebx, eax
    and ebx, 8                  ; own colour bits (0 / 8)
    mov r11d, ebx
    xor r11d, 8                 ; enemy colour bits
    and eax, 7                  ; piece type
    cmp eax, 6
    je .unk                     ; king (castling, king safety): not decided here
    cmp byte [board+rdi], 0
    jne .unk                    ; not quiet
    cmp eax, 1
    jne .sq
    mov ecx, edi
    xor ecx, esi
    test cl, 7
    jnz .unk                    ; a pawn that changes file: capture / en passant
    mov ecx, edi
    and ecx, 0xF0
    jz .unk                     ; promotion (rank 1)
    cmp ecx, 0x70
    je .unk                     ; promotion (rank 8)
.sq:mov ecx, esi
    and ecx, 7
    lea r8d, [rsi+rcx]
    shr r8d, 1                  ; from, 0..63
    mov ecx, edi
    and ecx, 7
    lea r9d, [rdi+rcx]
    shr r9d, 1                  ; to, 0..63
    mov r10, [bbc]
    or r10, [bbc+8]
    btr r10, r8
    bts r10, r9                 ; occupancy after the move
    mov esi, eax                ; esi = piece type from here on
    ; ---- legality: no enemy slider may reach our king through the new occupancy
    mov eax, ebx
    shr eax, 1                  ; 0 / 4 = byte offset of our king in ksq
    mov edi, [ksq+rax]
    mov ecx, edi
    and ecx, 7
    add edi, ecx                ; 0x88 square + file = 2 * (square 0..63) ...
    shl edi, 4                  ; ... so this is the record offset (square 0..63) * 32
    mov rdx, [bbp+r11*8+3*8]    ; enemy bishops | queens
    or rdx, [bbp+r11*8+5*8]
    PPLOOK btab, rdi, r10
    test rax, rdx
    jnz .ill
    mov rdx, [bbp+r11*8+4*8]    ; enemy rooks | queens
    or rdx, [bbp+r11*8+5*8]
    PPLOOK rtab, rdi, r10
    test rax, rdx
    jnz .ill
    ; ---- check: enemy king
    mov eax, r11d
    shr eax, 1
    mov edi, [ksq+rax]
    mov ecx, edi
    and ecx, 7
    add edi, ecx
    shl edi, 4                  ; record offset of the enemy king square
    cmp esi, 2
    jg .sl                      ; bishop, rook, queen: sliders only
    mov ecx, edi
    shr ecx, 2                  ; enemy king square * 8
    cmp esi, 1
    jne .kn
    mov eax, ebx
    shl eax, 6                  ; patt_to[own colour] (0 / 512)
    add eax, ecx
    mov rax, [patt_to+rax]      ; squares from which one of our pawns attacks the enemy king
    bt rax, r9
    jc .chk                     ; the pawn gives check
    jmp .sl
.kn:mov rax, [knight_att+rcx]
    bt rax, r9
    jc .chk                     ; the knight gives check
.sl:mov rdx, [bbp+rbx*8+3*8]    ; our bishops | queens
    or rdx, [bbp+rbx*8+5*8]
    cmp esi, 3
    je .tb
    cmp esi, 5
    jne .nb
.tb:btc rdx, r8                 ; the moving slider stands on "to" now
    btc rdx, r9
.nb:PPLOOK btab, rdi, r10
    test rax, rdx
    jnz .chk                    ; bishop / queen check, direct or discovered
    mov rdx, [bbp+rbx*8+4*8]    ; our rooks | queens
    or rdx, [bbp+rbx*8+5*8]
    cmp esi, 4
    je .tr
    cmp esi, 5
    jne .nr
.tr:btc rdx, r8
    btc rdx, r9
.nr:PPLOOK rtab, rdi, r10
    test rax, rdx
    jnz .chk                    ; rook / queen check, direct or discovered
    mov eax, 1
    jmp .ret
.ill:
    xor eax, eax
    jmp .ret
.chk:
    mov eax, 2
    jmp .ret
.unk:
    mov eax, 3
.ret:
    pop rbx
    ret
%endif                          ; PREPRUNE

; ============================================================
; gen(rdi = out buffer of dwords, esi = mode) -> rax = end            (bitboard version; mailbox version = gen_88)
; mode 0: all pseudo-legal moves; mode 1: captures, en passant, queen promotions.   move = from | to<<7 | promo<<14 (0x88 squares)
; Order: pawn moves (pushes, captures left / right, en passant), then N, B, R, Q, K (lowest square first), castling.
; Same move SET as gen_88 (checked at every node in verify mode), different order.
; Registers: r15 out, r14d mode, r12d colour, r13 target mask (mode 0: not own pieces, mode 1: enemy pieces), rbp occupancy
; ============================================================
; PEMIT delta, promo: rax = set of target squares (0..63).  from = to - delta (delta in 0x88 steps).  clobbers rax rcx rdx r8
%macro PEMIT 2
%%l:test rax, rax
    jz %%d
    bsf rcx, rax
    lea rdx, [rax-1]
    and rax, rdx
    mov edx, [to7+rcx*4]
    mov r8d, edx
    shr r8d, 7
    sub r8d, %1
    or edx, r8d
%if %2
    lea r8d, [rdx+(3<<14)]      ; queen first, like EMITPROMO
    mov [r15], r8d
    add r15, 4
    test r14d, r14d
    jnz %%l
    lea r8d, [rdx+(2<<14)]
    mov [r15], r8d
    lea r8d, [rdx+(1<<14)]
    mov [r15+4], r8d
    mov [r15+8], edx
    add r15, 12
%else
    mov [r15], edx
    add r15, 4
%endif
    jmp %%l
%%d:
%endmacro

%macro SLIDEATK 1               ; rax = attacks of the slider on square ebx (table %1), occupancy rbp.  clobbers rcx rdx r8
    mov r8d, ebx
    shl r8d, 5
    mov rax, rbp
    and rax, [%1+r8]
    imul rax, [%1+r8+8]
    mov ecx, [%1+r8+24]
    shr rax, cl
    mov rdx, [%1+r8+16]
    mov rax, [rdx+rax*8]
%endmacro
%macro ATK_N 0
    mov rax, [knight_att+rbx*8]
%endmacro
%macro ATK_K 0
    mov rax, [king_att+rbx*8]
%endmacro
%macro ATK_B 0
    SLIDEATK btab
%endmacro
%macro ATK_R 0
    SLIDEATK rtab
%endmacro
%macro ATK_Q 0
    SLIDEATK btab
    mov r9, rax
    SLIDEATK rtab
    or rax, r9
%endmacro

%macro GENPC 2                  ; piece type 2..6, attack macro
    lea ecx, [r12+%1]
    mov rdi, [bbp+rcx*8]
%%lp:
    test rdi, rdi
    jz %%end
    bsf rbx, rdi
    lea rax, [rdi-1]
    and rdi, rax
    %2
    and rax, r13
    jz %%lp
    mov r10d, ebx
    mov ecx, ebx
    and ecx, 0x38
    add r10d, ecx               ; from, 0x88
%%e:bsf rcx, rax
    lea rdx, [rax-1]
    and rax, rdx
    mov edx, [to7+rcx*4]
    or edx, r10d
    mov [r15], edx
    add r15, 4
    test rax, rax
    jnz %%e
    jmp %%lp
%%end:
%endmacro

gen:
    push rbx
    push rbp
    push r12
    push r13
    push r14
    push r15
    mov r15, rdi
    mov r14d, esi
    mov r12d, [stm]
    mov rbp, [bbc]
    or rbp, [bbc+8]             ; occupancy
    mov eax, r12d
    xor eax, 8
    test r14d, r14d
    jnz .cap
    mov r13, [bbc+r12]
    not r13                     ; mode 0: anything but own pieces
    jmp .tm
.cap:
    mov r13, [bbc+rax]          ; mode 1: enemy pieces
.tm:
    mov rdi, [bbc+rax]          ; enemy pieces (pawn captures)
    mov r11, rbp
    not r11                     ; empty squares
    test r12d, r12d
    jnz .black
; ---- white pawns
    mov rsi, [bbp+1*8]
    mov rbx, rsi
    shl rbx, 8
    and rbx, r11                ; single pushes
    mov rax, rbx
    and rax, [k_r8]
    PEMIT 16, 1                 ; push promotions
    test r14d, r14d
    jnz .wc
    mov rax, rbx
    and rax, [k_nr8]
    PEMIT 16, 0
    mov rax, rbx
    and rax, [k_r3]
    shl rax, 8
    and rax, r11
    PEMIT 32, 0                 ; double pushes
.wc:mov rax, rsi
    and rax, [k_nfa]
    shl rax, 7
    and rax, rdi
    mov rbx, rax
    and rax, [k_r8]
    PEMIT 15, 1
    mov rax, rbx
    and rax, [k_nr8]
    PEMIT 15, 0
    mov rax, rsi
    and rax, [k_nfh]
    shl rax, 9
    and rax, rdi
    mov rbx, rax
    and rax, [k_r8]
    PEMIT 17, 1
    mov rax, rbx
    and rax, [k_nr8]
    PEMIT 17, 0
    jmp .ep
; ---- black pawns
.black:
    mov rsi, [bbp+9*8]
    mov rbx, rsi
    shr rbx, 8
    and rbx, r11
    mov rax, rbx
    and rax, [k_r1]
    PEMIT -16, 1
    test r14d, r14d
    jnz .bc
    mov rax, rbx
    and rax, [k_nr1]
    PEMIT -16, 0
    mov rax, rbx
    and rax, [k_r6]
    shr rax, 8
    and rax, r11
    PEMIT -32, 0
.bc:mov rax, rsi
    and rax, [k_nfa]
    shr rax, 9
    and rax, rdi
    mov rbx, rax
    and rax, [k_r1]
    PEMIT -17, 1
    mov rax, rbx
    and rax, [k_nr1]
    PEMIT -17, 0
    mov rax, rsi
    and rax, [k_nfh]
    shr rax, 7
    and rax, rdi
    mov rbx, rax
    and rax, [k_r1]
    PEMIT -15, 1
    mov rax, rbx
    and rax, [k_nr1]
    PEMIT -15, 0
; ---- en passant: our pawns that attack the en passant square
.ep:mov eax, [epsq]
    test eax, eax
    jz .pcs
    mov ecx, eax
    and ecx, 7
    add ecx, eax
    shr ecx, 1                  ; ep square, 0..63
    mov edx, [to7+rcx*4]
    mov eax, r12d
    shl eax, 6
    mov rax, [patt_to+rax+rcx*8]
    and rax, rsi
.el:test rax, rax
    jz .pcs
    bsf rcx, rax
    lea r8, [rax-1]
    and rax, r8
    mov r8d, [to7+rcx*4]
    shr r8d, 7
    or r8d, edx
    mov [r15], r8d
    add r15, 4
    jmp .el
.pcs:
    GENPC 2, ATK_N
    GENPC 3, ATK_B
    GENPC 4, ATK_R
    GENPC 5, ATK_Q
    GENPC 6, ATK_K
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

%if SEE_ANY
; ============================================================
; see_att(ebx = square 0..63, r12 = occupancy) -> rax = every piece of BOTH colours in the occupancy that attacks the square
; clobbers rax rcx rdx r8 r9 r10 r11
; ============================================================
see_att:
    mov r8d, ebx
    mov rax, [patt_to+r8*8]
    and rax, [bbp+1*8]          ; white pawns
    mov rdx, [patt_to+512+r8*8]
    and rdx, [bbp+9*8]          ; black pawns
    or rax, rdx
    mov rdx, [knight_att+r8*8]
    mov rcx, [bbp+2*8]
    or rcx, [bbp+10*8]
    and rdx, rcx
    or rax, rdx
    mov rdx, [king_att+r8*8]
    mov rcx, [bbp+6*8]
    or rcx, [bbp+14*8]
    and rdx, rcx
    or rax, rdx
    mov r9, [bbp+5*8]
    or r9, [bbp+13*8]           ; queens
    mov r10, [bbp+3*8]
    or r10, [bbp+11*8]
    or r10, r9                  ; bishops | queens
    mov r11, [bbp+4*8]
    or r11, [bbp+12*8]
    or r11, r9                  ; rooks | queens
    shl r8d, 5
    mov rdx, r12
    and rdx, [btab+r8]
    imul rdx, [btab+r8+8]
    mov ecx, [btab+r8+24]
    shr rdx, cl
    mov rcx, [btab+r8+16]
    mov rdx, [rcx+rdx*8]
    and rdx, r10
    or rax, rdx
    mov rdx, r12
    and rdx, [rtab+r8]
    imul rdx, [rtab+r8+8]
    mov ecx, [rtab+r8+24]
    shr rdx, cl
    mov rcx, [rtab+r8+16]
    mov rdx, [rcx+rdx*8]
    and rdx, r11
    or rax, rdx
    and rax, r12                ; pieces that were taken off in the exchange are gone
    ret

; ============================================================
; see_ge(edi = move, esi = threshold) -> eax = 1 if the static exchange value of the move is >= threshold   (bitboard version)
; Same semantics as the mailbox see_ge of the BB=0 build: swap loop in the null-window form, least
; valuable attacker first (P N B R Q K), x-rays appear when a piece leaves the occupancy, the king may only recapture when nothing
; of the other side still attacks the square; no pins; promotion and en passant count as 0.  The board and bbp/bbc are not touched:
; the exchange is played on a local occupancy.   Preserves everything except eax ecx edx esi edi r8-r11.
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
    and r12d, 127               ; to  (0x88)
    and edi, 127                ; from (0x88)
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
    mov ecx, r12d
    and ecx, 7
    add ecx, r12d
    shr ecx, 1
    mov ebx, ecx                ; to, 0..63
    mov ecx, edi
    and ecx, 7
    add ecx, edi
    shr ecx, 1                  ; from, 0..63
    mov r12, [bbc]
    or r12, [bbc+8]
    btr r12, rcx                ; occupancy without the mover
    mov r14d, 1                 ; res
    call see_att                ; rax = attackers
.loop:
    xor r15d, 8                 ; side to recapture
    mov r8, [bbc+r15]
    and r8, rax                 ; its attackers
    jz .done                    ; no recapture: res stands
    xor r14d, 1
    lea ecx, [r15+1]
.lv:mov rdx, [bbp+rcx*8]
    and rdx, r8
    jnz .got
    inc ecx
    jmp .lv                     ; ends at the king at the latest
.got:
    mov r9d, ecx
    and r9d, 7                  ; piece type
    mov r8, rdx
    neg r8
    and rdx, r8                 ; lowest attacker of that type
    cmp r9d, 6
    je .king
    neg r13d
    add r13d, [pvalue+rcx*4]    ; swap = value(piece) - swap
    cmp r13d, r14d
    jl .done
    xor r12, rdx                ; lift it
    cmp r9d, 2
    jne .xr
    and rax, r12                ; a knight uncovers nothing
    jmp .loop
.xr:call see_att                ; x-rays behind the piece that left
    jmp .loop
.king:                          ; the king may only take when nothing of the other side still attacks the square
    xor r12, rdx
    call see_att
    mov edx, r15d
    xor edx, 8
    and rax, [bbc+rdx]
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
    mov eax, r14d
    pop r15
    pop r14
    pop r13
    pop r12
    pop rbp
    pop rbx
    ret
%endif

%if UNIT == 0
; ============================================================
; verify_gen -> eax = 0 if the bitboard gen (modes 0 and 1) produces the same move set as gen_88 and, for every move,
; the bitboard see_ge is monotone in the threshold.  Used by verify_state (verify mode).  (see_ge is NOT compared with the
; mailbox exchange: with several attackers of the same type the lifting order, and so the x-ray order, legitimately differs;
; tests/t_see.py checks it against a reference that accepts every order.)
; ============================================================
verify_gen:
    push rbx
    push rbp
    push r12
    push r13
    push r14
    push r15
    xor r12d, r12d              ; mode
.mode:
    mov edi, vbufa
    mov esi, r12d
    call gen
    mov r13, rax                ; end of list A
    mov edi, vbufb
    mov esi, r12d
    call gen_88
    mov r14, rax                ; end of list B
    sub r13, vbufa
    sub r14, vbufb
    cmp r13, r14
    jne .bad                    ; different number of moves
    xor ebx, ebx
.a: cmp rbx, r13
    jge .b
    mov eax, [vbufa+rbx]
    xor ecx, ecx
.f: cmp ecx, r14d
    jge .bad                    ; move of A not in B
    cmp eax, [vbufb+rcx]
    je .fn
    add ecx, 4
    jmp .f
.fn:add ebx, 4
    jmp .a
.b: xor ebx, ebx
.c: cmp rbx, r14
    jge .sc
    mov eax, [vbufb+rbx]
    xor ecx, ecx
.g: cmp ecx, r13d
    jge .bad                    ; move of B not in A
    cmp eax, [vbufa+rcx]
    je .gn
    add ecx, 4
    jmp .g
.gn:add ebx, 4
    jmp .c
.sc:
%if SEE_ANY
    test r12d, r12d
    jnz .nsee
    xor ebx, ebx
.s1:cmp rbx, r13
    jge .nsee
    xor ebp, ebp
    mov r15d, 1                 ; previous answer: see_ge(move, t) can only go from 1 to 0 as t grows
.s2:mov edi, [vbufa+rbx]
    mov esi, [vthr+rbp*4]
    call see_ge
    cmp eax, r15d
    ja .bad
    mov r15d, eax
    inc ebp
    cmp ebp, 9
    jl .s2
    add ebx, 4
    jmp .s1
.nsee:
%endif
    inc r12d
    cmp r12d, 2
    jl .mode
    xor eax, eax
    jmp .ret
.bad:
    mov eax, 1
.ret:
    pop r15
    pop r14
    pop r13
    pop r12
    pop rbp
    pop rbx
    ret

%endif                          ; UNIT == 0 (verify_gen)
%endif                          ; BB
