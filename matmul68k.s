;-----------------------------------------------------------------------------
; matmul68k.s -- the hot inner functions of tiny68k.c for the 68000
;
; All of these use vbcc register calls (__reg in tiny68k.c), not the stack:
; arguments in the registers named below, result in D0. D0/D1/A0/A1 are
; scratch; everything else is preserved, including any D2 used for an
; argument. Every one is an exact copy of the C fallback in tiny68k.c, and
; scripts/m68k_gate.py checks the whole engine built with them against the
; oracle in Musashi. Assemble with vasm:
;     vasmm68k_mot -Fhunk -m68000 -no-opt matmul68k.s -o matmul68k.o
;
;   s32 dot_i8_x16(a0 = const s8 *w, a1 = const s16 *x, d0 = n)
;   s32 dot_x16_i8s(a0 = const s16 *w, a1 = const s8 *v, d0 = n, d1 = stride)
;   s32 dot_i8_x16_sparse(a0 = const s8 *w, a1 = const s16 *pairs, d0 = k)
;   s32 fxmul(d0 = a, d1 = b)                  Q16.16 * Q16.16
;   s32 mulshift(d0 = acc, d1.w = m, d2 = sh)  (acc * m) >> sh, 48-bit exact
;   void build_tab(a0 = const s16 *x, a1 = s32 *tab, d0 = n)
;   void tab_dots(a0 = const u8 *w, a1 = const s32 *tab, d0 = cols, d1 = s32 *out,
;                 d2 = nblk)
;
; Cost per MAC in dot_i8_x16, 68000 @ 7.09 MHz:
;     MOVE.B (A0)+,D1     8
;     EXT.W   D1          4
;     MULS.W (A1)+,D1    38 + 2n (n = bit transitions in the source) + 4
;     ADD.L   D1,D0       8
;-----------------------------------------------------------------------------

        XDEF    _dot_i8_x16
        XDEF    _dot_x16_i8s
        XDEF    _dot_i8_x16_sparse
        XDEF    _fxmul
        XDEF    _mulshift
        XDEF    _build_tab
        XDEF    _tab_dots

        SECTION code,CODE

;--------------------------------------------------------------- dot_i8_x16
_dot_i8_x16:
        movem.l d2-d3,-(sp)
        move.l  d0,d3                   ; n
        moveq   #0,d0                   ; accumulator

        move.l  d3,d2
        lsr.l   #3,d2                   ; blocks of eight
        beq     .rem
        subq.w  #1,d2

.loop8:
        move.b  (a0)+,d1
        ext.w   d1
        muls.w  (a1)+,d1
        add.l   d1,d0

        move.b  (a0)+,d1
        ext.w   d1
        muls.w  (a1)+,d1
        add.l   d1,d0

        move.b  (a0)+,d1
        ext.w   d1
        muls.w  (a1)+,d1
        add.l   d1,d0

        move.b  (a0)+,d1
        ext.w   d1
        muls.w  (a1)+,d1
        add.l   d1,d0

        move.b  (a0)+,d1
        ext.w   d1
        muls.w  (a1)+,d1
        add.l   d1,d0

        move.b  (a0)+,d1
        ext.w   d1
        muls.w  (a1)+,d1
        add.l   d1,d0

        move.b  (a0)+,d1
        ext.w   d1
        muls.w  (a1)+,d1
        add.l   d1,d0

        move.b  (a0)+,d1
        ext.w   d1
        muls.w  (a1)+,d1
        add.l   d1,d0

        dbra    d2,.loop8

.rem:
        move.l  d3,d2
        and.w   #7,d2
        beq     .done
        subq.w  #1,d2
.loop1:
        move.b  (a0)+,d1
        ext.w   d1
        muls.w  (a1)+,d1
        add.l   d1,d0
        dbra    d2,.loop1

.done:
        movem.l (sp)+,d2-d3
        rts

;-------------------------------------------------------------- dot_x16_i8s
; sum_j w[j] * v[j * stride]: the attention value pass.
_dot_x16_i8s:
        movem.l d2-d3,-(sp)
        move.l  d0,d3                   ; n
        moveq   #0,d0
        tst.l   d3
        beq     .done
        subq.w  #1,d3
.loop:
        move.b  (a1),d2
        ext.w   d2
        muls.w  (a0)+,d2
        add.l   d2,d0
        adda.l  d1,a1
        dbra    d3,.loop
.done:
        movem.l (sp)+,d2-d3
        rts

;-------------------------------------------------------- dot_i8_x16_sparse
; pairs = k (column, value) words; sum of w[column] * value.
_dot_i8_x16_sparse:
        movem.l d2-d3,-(sp)
        move.l  d0,d3                   ; k
        moveq   #0,d0
        tst.l   d3
        beq     .done
        subq.w  #1,d3
.loop:
        move.w  (a1)+,d2                ; column
        move.b  0(a0,d2.w),d1
        ext.w   d1
        muls.w  (a1)+,d1                ; * value
        add.l   d1,d0
        dbra    d3,.loop
.done:
        movem.l (sp)+,d2-d3
        rts

;-------------------------------------------------------------------- fxmul
; |a| * |b| from four MULUs, >> 16, sign put back: the C fxmul() exactly,
; wrap-around included.
_fxmul:
        movem.l d2-d4,-(sp)
        move.l  d0,d2
        eor.l   d1,d2
        move.l  d2,a0                   ; bit 31 = sign of the result
        tst.l   d0
        bpl.s   .a
        neg.l   d0
.a:     tst.l   d1
        bpl.s   .b
        neg.l   d1
.b:     move.l  d0,d2
        swap    d2                      ; d2.w = ah
        move.l  d1,d3
        swap    d3                      ; d3.w = bh
        move.w  d0,d4
        mulu.w  d1,d4                   ; al * bl
        clr.w   d4
        swap    d4                      ; >> 16
        mulu.w  d3,d0                   ; al * bh
        add.l   d0,d4
        move.w  d2,d0
        mulu.w  d1,d0                   ; ah * bl
        add.l   d0,d4
        mulu.w  d3,d2                   ; ah * bh
        swap    d2
        clr.w   d2                      ; << 16
        add.l   d2,d4
        move.l  d4,d0
        move.l  a0,d1
        bpl.s   .d
        neg.l   d0
.d:     movem.l (sp)+,d2-d4
        rts

;----------------------------------------------------------------- mulshift
; (|acc| * m) as 48 bits in d3:d0, shifted by sh in [-15, 47] (negative =
; left), sign put back: the C mulshift() exactly.
_mulshift:
        movem.l d2-d4,-(sp)
        move.l  d0,a0                   ; bit 31 = sign
        tst.l   d0
        bpl.s   .p
        neg.l   d0
.p:     move.l  d0,d3
        swap    d3                      ; d3.w = ah
        mulu.w  d1,d0                   ; lo = al * m
        mulu.w  d1,d3                   ; hi = ah * m
        move.l  d3,d4
        swap    d4
        clr.w   d4                      ; hi << 16
        clr.w   d3
        swap    d3                      ; hi >> 16
        add.l   d4,d0                   ; rl, X = carry
        moveq   #0,d4                   ; (MOVEQ leaves X alone)
        addx.l  d4,d3                   ; rh
        tst.l   d2
        beq.s   .s
        bmi.s   .l
        moveq   #32,d4
        cmp.l   d4,d2
        bge.s   .h
        lsr.l   d2,d0                   ; 0 < sh < 32
        sub.l   d2,d4
        lsl.l   d4,d3
        or.l    d3,d0
        bra.s   .s
.h:     sub.l   d4,d2                   ; sh >= 32
        move.l  d3,d0
        lsr.l   d2,d0
        bra.s   .s
.l:     neg.l   d2                      ; sh < 0
        lsl.l   d2,d0
.s:     move.l  a0,d4
        bpl.s   .d
        neg.l   d0
.d:     movem.l (sp)+,d2-d4
        rts

;---------------------------------------------------------------- build_tab
; For each of the n inputs v = x[j]: 256 longs, v * w for w = 0..127 then
; w = -128..-1 (indexed by the weight's byte). Running sums, no multiply.
; About 20 cycles per entry, 5.2K per column.
_build_tab:
        movem.l d2-d4,-(sp)
        move.l  d0,d4                   ; n
        beq     .done
        subq.w  #1,d4
.col:   move.w  (a0)+,d1
        ext.l   d1                      ; v
        moveq   #0,d2                   ; running v * w
        moveq   #15,d3                  ; 16 x 8 = w 0..127
.up:    REPT    8
        move.l  d2,(a1)+
        add.l   d1,d2
        ENDR
        dbra    d3,.up
        neg.l   d2                      ; 128 v -> -128 v
        moveq   #15,d3                  ; w -128..-1
.dn:    REPT    8
        move.l  d2,(a1)+
        add.l   d1,d2
        ENDR
        dbra    d3,.dn
        dbra    d4,.col
.done:  movem.l (sp)+,d2-d4
        rts

;----------------------------------------------------------------- tab_dots
; nblk blocks of 8 rows; inside a block the weights are column-major
; (w[8 j + r]). Eight accumulators in d2-d7/a3/a4, one table per column:
;     out[r] = sum_j tab[256 j + w[8 j + r]]
; Per MAC: moveq 4 + move.b 8 + 2 x add.w 8 + add.l d8(An,Xn) 20 = 40 cycles
; (adda.l likewise), against ~76 for the MULS kernel.
_tab_dots:
        movem.l d2-d7/a2-a6,-(sp)
        move.l  d1,a5                   ; out
        move.l  d0,a6
        subq.l  #1,a6                   ; cols - 1, for dbra
        move.l  d2,-(sp)                ; block counter
.blk:   moveq   #0,d2
        moveq   #0,d3
        moveq   #0,d4
        moveq   #0,d5
        moveq   #0,d6
        moveq   #0,d7
        suba.l  a3,a3
        suba.l  a4,a4
        move.l  a1,a2                   ; table of column 0
        move.w  a6,d0
.col:   moveq   #0,d1
        move.b  (a0)+,d1
        add.w   d1,d1
        add.w   d1,d1
        add.l   0(a2,d1.w),d2
        moveq   #0,d1
        move.b  (a0)+,d1
        add.w   d1,d1
        add.w   d1,d1
        add.l   0(a2,d1.w),d3
        moveq   #0,d1
        move.b  (a0)+,d1
        add.w   d1,d1
        add.w   d1,d1
        add.l   0(a2,d1.w),d4
        moveq   #0,d1
        move.b  (a0)+,d1
        add.w   d1,d1
        add.w   d1,d1
        add.l   0(a2,d1.w),d5
        moveq   #0,d1
        move.b  (a0)+,d1
        add.w   d1,d1
        add.w   d1,d1
        add.l   0(a2,d1.w),d6
        moveq   #0,d1
        move.b  (a0)+,d1
        add.w   d1,d1
        add.w   d1,d1
        add.l   0(a2,d1.w),d7
        moveq   #0,d1
        move.b  (a0)+,d1
        add.w   d1,d1
        add.w   d1,d1
        adda.l  0(a2,d1.w),a3
        moveq   #0,d1
        move.b  (a0)+,d1
        add.w   d1,d1
        add.w   d1,d1
        adda.l  0(a2,d1.w),a4
        lea     1024(a2),a2             ; next column's table
        dbra    d0,.col
        movem.l d2-d7/a3-a4,(a5)        ; rows 0..7 in register order
        lea     32(a5),a5
        subq.l  #1,(sp)
        bne     .blk
        addq.l  #4,sp
        movem.l (sp)+,d2-d7/a2-a6
        rts

        END
