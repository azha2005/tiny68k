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
        XDEF    _dots_i8_x16
        XDEF    _dots_i8_x16_sparse
        XDEF    _mulshift_rows
        XDEF    _fxdiv
        XDEF    _quantize16
        XDEF    _rms_apply
        XDEF    _kv_quant

        SECTION code,CODE

;--------------------------------------------------------------- dot_i8_x16
_dot_i8_x16:
        movem.l d2-d3,-(sp)
        bsr     dotcore
        movem.l (sp)+,d2-d3
        rts

;-------------------------------------------------------------- dots_i8_x16
; out[r] = dot_i8_x16(w + r cols, x, cols) for r < rows: a whole matmul's
; raw dots in one call (no C call per row).
;   a0 = w, a1 = x, d0 = cols, d1 = s32 *out, d2 = rows
_dots_i8_x16:
        movem.l d2-d7/a2-a3,-(sp)
        move.l  a1,a2                   ; x
        move.l  d1,a3                   ; out
        move.l  d0,d4                   ; cols
        move.l  d2,d5                   ; rows
        beq.s   .done
        subq.w  #1,d5
.row:   move.l  a2,a1
        move.l  d4,d0
        bsr     dotcore                 ; a0 ends at the next row
        move.l  d0,(a3)+
        dbra    d5,.row
.done:  movem.l (sp)+,d2-d7/a2-a3
        rts

; a0 = w (advanced by n), a1 = x (advanced), d0 = n -> d0 = dot.
; Clobbers d1-d3.
dotcore:
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
        bsr     sparsecore
        movem.l (sp)+,d2-d3
        rts

;------------------------------------------------------- dots_i8_x16_sparse
; out[r] = dot_i8_x16_sparse(w + r cols, pairs, k) for r < rows.
;   a0 = w, a1 = pairs, d0 = k, d1 = s32 *out, d2 = rows, d3 = cols
_dots_i8_x16_sparse:
        movem.l d2-d7/a2-a4,-(sp)
        move.l  a1,a2                   ; pairs
        move.l  d1,a3                   ; out
        move.l  a0,a4                   ; row
        move.l  d0,d4                   ; k
        move.l  d3,d6                   ; cols
        move.l  d2,d5                   ; rows
        beq.s   .done
        subq.w  #1,d5
.row:   move.l  a4,a0
        move.l  a2,a1
        move.l  d4,d0
        bsr     sparsecore
        move.l  d0,(a3)+
        adda.l  d6,a4
        dbra    d5,.row
.done:  movem.l (sp)+,d2-d7/a2-a4
        rts

; a0 = row, a1 = pairs (advanced), d0 = k -> d0. Clobbers d1-d3.
; Unrolled by two to halve the DBRA.
sparsecore:
        move.l  d0,d3                   ; k
        moveq   #0,d0
        lsr.l   #1,d3                   ; pairs of pairs, C = odd one
        bcc.s   .even
        move.w  (a1)+,d2
        move.b  0(a0,d2.w),d1
        ext.w   d1
        muls.w  (a1)+,d1
        add.l   d1,d0
.even:  tst.l   d3
        beq.s   .done
        subq.w  #1,d3
.loop:  move.w  (a1)+,d2                ; column
        move.b  0(a0,d2.w),d1
        ext.w   d1
        muls.w  (a1)+,d1                ; * value
        add.l   d1,d0
        move.w  (a1)+,d2
        move.b  0(a0,d2.w),d1
        ext.w   d1
        muls.w  (a1)+,d1
        add.l   d1,d0
        dbra    d3,.loop
.done:  rts

;-------------------------------------------------------------------- fxmul
; |a| * |b| from four MULUs, >> 16, sign put back: the C fxmul() exactly,
; wrap-around included.
_fxmul:
        movem.l d2-d5,-(sp)
        bsr     fxcore
        movem.l (sp)+,d2-d5
        rts

; d0 = a, d1 = b -> d0 = fxmul(a, b). Clobbers d1-d5.
fxcore:
        move.l  d0,d5
        eor.l   d1,d5                   ; bit 31 = sign of the result
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
        tst.l   d5
        bpl.s   .d
        neg.l   d0
.d:     rts

;---------------------------------------------------------------- rms_apply
; The last loop of rmsnorm(): out[j] = fxmul(g[j], fxmul(x[j], inv)).
;   a0 = s32 *out, a1 = const s32 *x, d0 = n, d1 = inv, d2 = const s32 *g
_rms_apply:
        movem.l d2-d7/a2,-(sp)
        move.l  d2,a2
        move.l  d1,d6                   ; inv
        move.l  d0,d7
        beq.s   .done
        subq.w  #1,d7
.lp:    move.l  (a1)+,d0
        move.l  d6,d1
        bsr     fxcore
        move.l  (a2)+,d1
        bsr     fxcore                  ; fxmul is symmetric in its arguments
        move.l  d0,(a0)+
        dbra    d7,.lp
.done:  movem.l (sp)+,d2-d7/a2
        rts

;----------------------------------------------------------------- kv_quant
; The loop of store_kv(): a = |v|, r = (fxmul(a, f) + $8000) >> 16,
; r = min(r, 127), q = v < 0 ? -r : r.
;   a0 = const s32 *v, d0 = n, a1 = s8 *q, d1 = f
_kv_quant:
        movem.l d2-d7/a2,-(sp)
        move.l  d1,d6                   ; f
        move.l  d0,d7
        beq.s   .done
        subq.w  #1,d7
.lp:    move.l  (a0)+,d0
        move.l  d0,a2                   ; sign of v (MOVEA keeps the flags)
        bpl.s   .p
        neg.l   d0
.p:     move.l  d6,d1
        bsr     fxcore
        add.l   #$8000,d0
        swap    d0
        ext.l   d0                      ; >> 16, arithmetic
        moveq   #127,d1
        cmp.l   d1,d0
        ble.s   .c
        move.l  d1,d0
.c:     move.l  a2,d1
        bpl.s   .s
        neg.l   d0
.s:     move.b  d0,(a1)+
        dbra    d7,.lp
.done:  movem.l (sp)+,d2-d7/a2
        rts

;----------------------------------------------------------------- mulshift
; (|acc| * m) as 48 bits in d3:d0, shifted by sh in [-15, 47] (negative =
; left), sign put back: the C mulshift() exactly.
_mulshift:
        movem.l d2-d5,-(sp)
        bsr     mscore
        movem.l (sp)+,d2-d5
        rts

;------------------------------------------------------------ mulshift_rows
; out[r] = mulshift(out[r], mant[r], (int)shift[r] - xsh) for r < rows,
; shift[] zero-extended like the C's (int) of a u16.
;   a0 = s32 *out (in place), a1 = const u16 *mant, d0 = rows, d1 = xsh,
;   d2 = const u16 *shift
_mulshift_rows:
        movem.l d2-d7/a2,-(sp)
        move.l  d2,a2
        move.l  d0,d7                   ; rows
        beq.s   .done
        move.l  d1,d6                   ; xsh
        subq.w  #1,d7
.row:   move.l  (a0),d0
        move.w  (a1)+,d1
        moveq   #0,d2
        move.w  (a2)+,d2
        sub.l   d6,d2
        bsr     mscore
        move.l  d0,(a0)+
        dbra    d7,.row
.done:  movem.l (sp)+,d2-d7/a2
        rts

; d0 = acc, d1.w = m, d2 = sh -> d0. Clobbers d1-d5.
mscore:
        move.l  d0,d5                   ; bit 31 = sign
        bpl.s   .p
        neg.l   d0
.p:     move.l  d0,d3
        swap    d3                      ; d3.w = ah
        mulu.w  d1,d0                   ; lo = al * m
        mulu.w  d1,d3                   ; hi = ah * m
        moveq   #16,d4
        cmp.l   d4,d2
        blt.s   .gen
        ; sh >= 16 (88% of calls): floor(P / 2^sh) with P = hi 2^16 + lo is
        ; (hi + (lo >> 16)) >> (sh - 16), and that sum is < 2^31.
        clr.w   d0
        swap    d0                      ; lo >> 16
        add.l   d3,d0
        sub.l   d4,d2
        lsr.l   d2,d0
        bra.s   .s
.gen:   move.l  d3,d4
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
.s:     tst.l   d5
        bpl.s   .d
        neg.l   d0
.d:     rts

;-------------------------------------------------------------------- fxdiv
; (a << 16) / b in Q16.16: the C fxdiv() exactly. Restoring division of the
; 48-bit |a| << 16 by |b|; the C's first 16 steps only shift in zeros, so
; they are skipped. Quotient mod 2^32, sign put back, b = 0 -> $7FFFFFFF.
;   d0 = a, d1 = b -> d0
_fxdiv:
        tst.l   d1
        bne.s   .go
        move.l  #$7FFFFFFF,d0
        rts
.go:    movem.l d2-d5,-(sp)
        move.l  d0,d5
        eor.l   d1,d5                   ; bit 31 = sign
        tst.l   d0
        bpl.s   .a
        neg.l   d0
.a:     tst.l   d1
        bpl.s   .b
        neg.l   d1
.b:     move.l  d1,d3                   ; |b|
        move.l  d0,d1                   ; dividend bits, MSB first
        moveq   #0,d2                   ; remainder
        moveq   #0,d4                   ; quotient
        REPT    32
        add.l   d1,d1
        addx.l  d2,d2                   ; never carries out: rem < |b| <= 2^31
        add.l   d4,d4
        cmp.l   d3,d2
        bcs.s   *+6
        sub.l   d3,d2                   ; 2 bytes
        addq.l  #1,d4                   ; 2 bytes
        ENDR
        REPT    16                      ; the 16 zero bits of << 16
        add.l   d2,d2
        add.l   d4,d4
        cmp.l   d3,d2
        bcs.s   *+6
        sub.l   d3,d2
        addq.l  #1,d4
        ENDR
        move.l  d4,d0
        tst.l   d5
        bpl.s   .d
        neg.l   d0
.d:     movem.l (sp)+,d2-d5
        rts

;--------------------------------------------------------------- quantize16
; s32 x[n] -> s16 out[n] with a power-of-two shift sh, truncating toward
; zero (|x| >> sh, sign put back); sh is the smallest with amax >> sh
; <= 32767. Returns sh. The C quantize16() exactly.
;   a0 = const s32 *x, d0 = n, a1 = s16 *out -> d0 = sh
_quantize16:
        movem.l d2-d4,-(sp)
        move.l  d0,d3                   ; n
        beq.s   .zero
        move.l  a0,d4                   ; keep x
        moveq   #0,d1                   ; amax
        move.w  d3,d2
        subq.w  #1,d2
.max:   move.l  (a0)+,d0
        bpl.s   .m1
        neg.l   d0
.m1:    cmp.l   d1,d0
        bls.s   .m2
        move.l  d0,d1
.m2:    dbra    d2,.max
        moveq   #0,d2                   ; sh
.sh:    cmp.l   #32767,d1
        bls.s   .q
        lsr.l   #1,d1
        addq.l  #1,d2
        bra.s   .sh
.q:     move.l  d4,a0
        subq.w  #1,d3
.qq:    move.l  (a0)+,d0
        bmi.s   .neg
        asr.l   d2,d0
        move.w  d0,(a1)+
        dbra    d3,.qq
        bra.s   .out
.neg:   neg.l   d0
        asr.l   d2,d0
        neg.w   d0
        move.w  d0,(a1)+
        dbra    d3,.qq
.out:   move.l  d2,d0
        movem.l (sp)+,d2-d4
        rts
.zero:  moveq   #0,d0
        movem.l (sp)+,d2-d4
        rts

;---------------------------------------------------------------- build_tab
; For each of the n inputs v = x[j]: 256 longs, v * w for w = 0..127 then
; w = -128..-1 (indexed by the weight's byte). Running sums, no multiply.
; About 19 cycles per entry (MOVEM stores), 4.9K per column.
_build_tab:
        tst.l   d0
        beq     .none
        movem.l d2-d7/a2-a5,-(sp)
        move.l  d0,-(sp)                ; columns left
; Each column is filled backwards, 8 entries per MOVEM.L -(A1): the group
; at entries 8g..8g+7 holds d0..d7 in ascending order. High half first
; (entries 255..128 = w -1..-128), then the low half (127..0 = w 127..0).
.col:   lea     1024(a1),a1             ; end of this column's table
        lea     -512(a1),a4             ; end of the high half
        move.w  (a0)+,d0
        ext.l   d0
        move.l  d0,a5                   ; v
        move.l  d0,d1
        lsl.l   #3,d1
        move.l  d1,a2                   ; 8 v
        lsl.l   #5,d1
        move.l  d1,a3                   ; 256 v
        move.l  a5,d7
        neg.l   d7                      ; entries 248..255: -8v .. -1v
        move.l  d7,d6
        sub.l   a5,d6
        move.l  d6,d5
        sub.l   a5,d5
        move.l  d5,d4
        sub.l   a5,d4
        move.l  d4,d3
        sub.l   a5,d3
        move.l  d3,d2
        sub.l   a5,d2
        move.l  d2,d1
        sub.l   a5,d1
        move.l  d1,d0
        sub.l   a5,d0
.hi:    movem.l d0-d7,-(a1)
        sub.l   a2,d0
        sub.l   a2,d1
        sub.l   a2,d2
        sub.l   a2,d3
        sub.l   a2,d4
        sub.l   a2,d5
        sub.l   a2,d6
        sub.l   a2,d7
        cmpa.l  a4,a1
        bne.s   .hi
        add.l   a3,d0                   ; -136v.. -> 120v .. 127v
        add.l   a3,d1
        add.l   a3,d2
        add.l   a3,d3
        add.l   a3,d4
        add.l   a3,d5
        add.l   a3,d6
        add.l   a3,d7
        lea     -512(a4),a4             ; start of the column
.lo:    movem.l d0-d7,-(a1)
        sub.l   a2,d0
        sub.l   a2,d1
        sub.l   a2,d2
        sub.l   a2,d3
        sub.l   a2,d4
        sub.l   a2,d5
        sub.l   a2,d6
        sub.l   a2,d7
        cmpa.l  a4,a1
        bne.s   .lo
        lea     1024(a1),a1             ; next column's start
        subq.l  #1,(sp)
        bne     .col
        addq.l  #4,sp
        movem.l (sp)+,d2-d7/a2-a5
.none:  rts

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
