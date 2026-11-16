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
;   s32 build_bptab(a0 = const s16 *x, a1 = s32 *tab, d0 = n)
;   void bp_dots(a0 = const u8 *w, a1 = const s32 *tab, d0 = groups, d1 = s32 *out,
;                d2 = nblk, d3 = bias, d4 = const u8 *spill)
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
        XDEF    _build_bptab
        XDEF    _bp_dots
        XDEF    _bp_dots_p
        XDEF    _dots_i8_x16
        XDEF    _dots_i8_x16_sparse
        XDEF    _dots_i8_x16_sparse_T
        XDEF    _mulshift_rows
        XDEF    _fxdiv
        XDEF    _quantize16
        XDEF    _rms_sumsq
        XDEF    _rms_apply
        XDEF    _kv_quant
        XDEF    _att_qk_head
        XDEF    _att_softmax_wgt
        XDEF    _att_wv_head

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
        ; a zero high half makes its two products zero: skip them (87% of
        ; calls on real data have one, 30% both; a MULU is ~54 cycles)
        tst.w   d3
        beq.s   .nobh
        mulu.w  d3,d0                   ; al * bh
        add.l   d0,d4
        tst.w   d2
        beq.s   .sum
        mulu.w  d2,d3                   ; ah * bh
        swap    d3
        clr.w   d3                      ; << 16
        add.l   d3,d4
        bra.s   .ahbl
.nobh:  tst.w   d2
        beq.s   .sum
.ahbl:  mulu.w  d1,d2                   ; ah * bl
        add.l   d2,d4
.sum:   move.l  d4,d0
        tst.l   d5
        bpl.s   .d
        neg.l   d0
.d:     rts

;---------------------------------------------------------------- rms_sumsq
; Compute k and sum_j (|x[j]| >> k)^2 where k is smallest >= 0 with
; (amax >> k) < 4096. Returns ss, stores k in *out_k.
;   a0 = const s32 *x, d0 = s32 n, a1 = s32 *out_k -> d0 = u32 ss
_rms_sumsq:
        tst.l   d0
        ble.s   .zero
        movem.l d2-d4,-(sp)
        move.l  a0,d4                   ; keep x
        move.w  d0,d3
        subq.w  #1,d3                   ; n - 1
        move.w  d3,d2
        moveq   #0,d1                   ; amax
.max:   move.l  (a0)+,d0
        bpl.s   .m1
        neg.l   d0
.m1:    cmp.l   d1,d0
        bls.s   .m2
        move.l  d0,d1
.m2:    dbra    d2,.max
        tst.l   d1
        beq.s   .z2
        moveq   #0,d2                   ; k
        move.l  #4096,d0
.sh:    cmp.l   d0,d1
        bcs.s   .ssgo
        lsr.l   #1,d1
        addq.l  #1,d2
        bra.s   .sh
.ssgo:  move.l  d2,(a1)
        move.l  d4,a0                   ; x
        move.w  d3,d1                   ; n - 1
        moveq   #0,d3                   ; ss
.ss:    move.l  (a0)+,d0
        bpl.s   .s1
        neg.l   d0
.s1:    lsr.l   d2,d0
        mulu.w  d0,d0
        add.l   d0,d3
        dbra    d1,.ss
        move.l  d3,d0
        movem.l (sp)+,d2-d4
        rts
.z2:    clr.l   (a1)
        moveq   #0,d0
        movem.l (sp)+,d2-d4
        rts
.zero:  clr.l   (a1)
        moveq   #0,d0
        rts

;---------------------------------------------------------------- rms_apply
; The last loop of rmsnorm(): out[j] = fxmul(g[j], fxmul(x[j], inv)).
; inv is hoisted out of the loop and both fxmuls are inlined.
;   a0 = s32 *out, a1 = const s32 *x, d0 = n, d1 = inv, d2 = const s32 *g
_rms_apply:
        tst.l   d0
        ble     .done
        movem.l d2-d7/a2-a4,-(sp)
        move.l  d2,a2                   ; g
        move.l  d1,a3                   ; inv (for sign)
        tst.l   d1
        bpl.s   .ip
        neg.l   d1
.ip:    move.l  d1,d6                   ; |inv|, d6.w = bl
        swap    d1
        move.w  d1,a4                   ; inv_h
        move.w  d0,d7
        subq.w  #1,d7
.lp:    move.l  (a1)+,d0                ; x[j]
        move.l  a3,d5
        eor.l   d0,d5                   ; sign1
        tst.l   d0
        bpl.s   .x1
        neg.l   d0
.x1:    move.l  d0,d2
        swap    d2                      ; ah
        move.w  d0,d3
        mulu.w  d6,d3                   ; al * bl
        clr.w   d3
        swap    d3
        move.w  a4,d4                   ; inv_h
        beq.s   .nobh1
        mulu.w  d4,d0                   ; al * bh
        add.l   d0,d3
        tst.w   d2
        beq.s   .sum1
        move.w  d2,d0
        mulu.w  d4,d0                   ; ah * bh
        swap    d0
        clr.w   d0
        add.l   d0,d3
        bra.s   .ahbl1
.nobh1: tst.w   d2
        beq.s   .sum1
.ahbl1: mulu.w  d6,d2                   ; ah * bl
        add.l   d2,d3
.sum1:  move.l  d3,d0
        tst.l   d5
        bpl.s   .s1
        neg.l   d0
.s1:    move.l  (a2)+,d1                ; g[j]
        move.l  d0,d5
        eor.l   d1,d5                   ; sign2
        tst.l   d0
        bpl.s   .a2
        neg.l   d0
.a2:    tst.l   d1
        bpl.s   .b2
        neg.l   d1
.b2:    move.l  d0,d2
        swap    d2                      ; ah
        move.l  d1,d3
        swap    d3                      ; bh
        move.w  d0,d4
        mulu.w  d1,d4                   ; al * bl
        clr.w   d4
        swap    d4
        tst.w   d3
        beq.s   .nobh2
        mulu.w  d3,d0                   ; al * bh
        add.l   d0,d4
        tst.w   d2
        beq.s   .sum2
        mulu.w  d2,d3                   ; ah * bh
        swap    d3
        clr.w   d3
        add.l   d3,d4
        bra.s   .ahbl2
.nobh2: tst.w   d2
        beq.s   .sum2
.ahbl2: mulu.w  d1,d2                   ; ah * bl
        add.l   d2,d4
.sum2:  move.l  d4,d0
        tst.l   d5
        bpl.s   .s2
        neg.l   d0
.s2:    move.l  d0,(a0)+
        dbra    d7,.lp
        movem.l (sp)+,d2-d7/a2-a4
.done:  rts

;----------------------------------------------------------------- kv_quant
; The loop of store_kv(): a = |v|, r = (fxmul(a, f) + $8000) >> 16,
; r = min(r, 127), q = v < 0 ? -r : r. f is hoisted out of the loop.
;   a0 = const s32 *v, d0 = n, a1 = s8 *q, d1 = f
_kv_quant:
        tst.l   d0
        ble.s   .done
        movem.l d2-d7/a2-a3,-(sp)
        move.l  d1,a2                   ; f (sign bit 31)
        tst.l   d1
        bpl.s   .fp
        neg.l   d1
.fp:    move.l  d1,d6                   ; |f|, d6.w = bl
        move.l  d1,d4
        swap    d4                      ; d4.w = bh
        move.w  d0,d7
        subq.w  #1,d7
.lp:    move.l  (a0)+,d0
        move.l  d0,d5                   ; sign of v
        bpl.s   .p
        neg.l   d0
.p:     move.l  a2,d1
        eor.l   d0,d1                   ; bit 31 = sign(a) ^ sign(f) (handles -$80000000)
        move.l  d1,a3
        move.l  d0,d2
        swap    d2                      ; ah
        move.w  d0,d3
        mulu.w  d6,d3                   ; al * bl
        clr.w   d3
        swap    d3
        tst.w   d4
        beq.s   .nobh
        mulu.w  d4,d0                   ; al * bh
        add.l   d0,d3
        tst.w   d2
        beq.s   .sum
        move.w  d2,d0
        mulu.w  d4,d0                   ; ah * bh
        swap    d0
        clr.w   d0
        add.l   d0,d3
        bra.s   .ahbl
.nobh:  tst.w   d2
        beq.s   .sum
.ahbl:  mulu.w  d6,d2                   ; ah * bl
        add.l   d2,d3
.sum:   move.l  a3,d1
        bpl.s   .fs
        neg.l   d3
.fs:    add.l   #$8000,d3
        swap    d3
        ext.l   d3                      ; >> 16, arithmetic
        moveq   #127,d1
        cmp.l   d1,d3
        ble.s   .c
        move.l  d1,d3
.c:     tst.l   d5
        bpl.s   .s
        neg.l   d3
.s:     move.b  d3,(a1)+
        dbra    d7,.lp
        movem.l (sp)+,d2-d7/a2-a3
.done:  rts

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
        move.l  d1,d6
        moveq   #16,d1
        add.l   d1,d6                   ; xsh + 16
        subq.w  #1,d7
        ; mscore's sh >= 16 path inline (88% of rows); the rest call mscore
.row:   move.l  (a0),d0
        move.w  (a1)+,d1                ; m
        move.w  (a2)+,d2
        sub.w   d6,d2                   ; n = shift - xsh - 16, in [-63, 31]
        bmi.s   .gen
        move.l  d0,d5                   ; bit 31 = sign
        bpl.s   .p
        neg.l   d0
.p:     move.l  d0,d3
        swap    d3                      ; ah
        mulu.w  d1,d0                   ; lo = al * m
        mulu.w  d1,d3                   ; hi = ah * m
        clr.w   d0
        swap    d0                      ; lo >> 16
        add.l   d3,d0                   ; < 2^31, as in mscore
        lsr.l   d2,d0
        tst.l   d5
        bpl.s   .q
        neg.l   d0
.q:     move.l  d0,(a0)+
        dbra    d7,.row
        bra.s   .done
.gen:   ext.l   d2
        moveq   #16,d3
        add.l   d3,d2                   ; sh = shift - xsh
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

;-------------------------------------------------------------- build_bptab
; T-MAC group tables: for each group of 6 inputs and every 6-bit b,
; tab[64 g + b] = sum of the inputs whose bit is set in b. In "ruler"
; order: T[b] = T[b-1] + D[ctz(b)], D[t] = x[t] - (x[0] + .. + x[t-1]),
; D0..D5 in d0..d5, fully unrolled: 20 cycles per entry, 10.7 entries per
; input. Returns the sum of all inputs.
;   a0 = const s16 *x, a1 = s32 *tab, d0 = n (multiple of 6) -> d0 = sum x
_build_bptab:
        movem.l d2-d7/a2-a4,-(sp)
        suba.l  a3,a3                   ; sum of x
        move.l  d0,d1
        add.l   d1,d1
        lea     0(a0,d1.l),a4           ; end of x
        cmpa.l  a4,a0
        beq     .done
.grp:   move.w  (a0)+,d0
        ext.l   d0
        adda.l  d0,a3
        move.l  d0,d7                   ; D0 = x0, P = x0
        move.w  (a0)+,d1
        ext.l   d1
        adda.l  d1,a3
        sub.l   d7,d1                 ; D1 = x1 - P
        add.l   d7,d7
        add.l   d1,d7                 ; P += x1  (= 2P + D1)
        move.w  (a0)+,d2
        ext.l   d2
        adda.l  d2,a3
        sub.l   d7,d2                 ; D2 = x2 - P
        add.l   d7,d7
        add.l   d2,d7                 ; P += x2  (= 2P + D2)
        move.w  (a0)+,d3
        ext.l   d3
        adda.l  d3,a3
        sub.l   d7,d3                 ; D3 = x3 - P
        add.l   d7,d7
        add.l   d3,d7                 ; P += x3  (= 2P + D3)
        move.w  (a0)+,d4
        ext.l   d4
        adda.l  d4,a3
        sub.l   d7,d4                 ; D4 = x4 - P
        add.l   d7,d7
        add.l   d4,d7                 ; P += x4  (= 2P + D4)
        move.w  (a0)+,d5
        ext.l   d5
        adda.l  d5,a3
        sub.l   d7,d5                 ; D5 = x5 - P
        moveq   #0,d7
        move.l  d7,(a1)+                ; T[0] = 0
        add.l   d0,d7
        move.l  d7,(a1)+
        add.l   d1,d7
        move.l  d7,(a1)+
        add.l   d0,d7
        move.l  d7,(a1)+
        add.l   d2,d7
        move.l  d7,(a1)+
        add.l   d0,d7
        move.l  d7,(a1)+
        add.l   d1,d7
        move.l  d7,(a1)+
        add.l   d0,d7
        move.l  d7,(a1)+
        add.l   d3,d7
        move.l  d7,(a1)+
        add.l   d0,d7
        move.l  d7,(a1)+
        add.l   d1,d7
        move.l  d7,(a1)+
        add.l   d0,d7
        move.l  d7,(a1)+
        add.l   d2,d7
        move.l  d7,(a1)+
        add.l   d0,d7
        move.l  d7,(a1)+
        add.l   d1,d7
        move.l  d7,(a1)+
        add.l   d0,d7
        move.l  d7,(a1)+
        add.l   d4,d7
        move.l  d7,(a1)+
        add.l   d0,d7
        move.l  d7,(a1)+
        add.l   d1,d7
        move.l  d7,(a1)+
        add.l   d0,d7
        move.l  d7,(a1)+
        add.l   d2,d7
        move.l  d7,(a1)+
        add.l   d0,d7
        move.l  d7,(a1)+
        add.l   d1,d7
        move.l  d7,(a1)+
        add.l   d0,d7
        move.l  d7,(a1)+
        add.l   d3,d7
        move.l  d7,(a1)+
        add.l   d0,d7
        move.l  d7,(a1)+
        add.l   d1,d7
        move.l  d7,(a1)+
        add.l   d0,d7
        move.l  d7,(a1)+
        add.l   d2,d7
        move.l  d7,(a1)+
        add.l   d0,d7
        move.l  d7,(a1)+
        add.l   d1,d7
        move.l  d7,(a1)+
        add.l   d0,d7
        move.l  d7,(a1)+
        add.l   d5,d7
        move.l  d7,(a1)+
        add.l   d0,d7
        move.l  d7,(a1)+
        add.l   d1,d7
        move.l  d7,(a1)+
        add.l   d0,d7
        move.l  d7,(a1)+
        add.l   d2,d7
        move.l  d7,(a1)+
        add.l   d0,d7
        move.l  d7,(a1)+
        add.l   d1,d7
        move.l  d7,(a1)+
        add.l   d0,d7
        move.l  d7,(a1)+
        add.l   d3,d7
        move.l  d7,(a1)+
        add.l   d0,d7
        move.l  d7,(a1)+
        add.l   d1,d7
        move.l  d7,(a1)+
        add.l   d0,d7
        move.l  d7,(a1)+
        add.l   d2,d7
        move.l  d7,(a1)+
        add.l   d0,d7
        move.l  d7,(a1)+
        add.l   d1,d7
        move.l  d7,(a1)+
        add.l   d0,d7
        move.l  d7,(a1)+
        add.l   d4,d7
        move.l  d7,(a1)+
        add.l   d0,d7
        move.l  d7,(a1)+
        add.l   d1,d7
        move.l  d7,(a1)+
        add.l   d0,d7
        move.l  d7,(a1)+
        add.l   d2,d7
        move.l  d7,(a1)+
        add.l   d0,d7
        move.l  d7,(a1)+
        add.l   d1,d7
        move.l  d7,(a1)+
        add.l   d0,d7
        move.l  d7,(a1)+
        add.l   d3,d7
        move.l  d7,(a1)+
        add.l   d0,d7
        move.l  d7,(a1)+
        add.l   d1,d7
        move.l  d7,(a1)+
        add.l   d0,d7
        move.l  d7,(a1)+
        add.l   d2,d7
        move.l  d7,(a1)+
        add.l   d0,d7
        move.l  d7,(a1)+
        add.l   d1,d7
        move.l  d7,(a1)+
        add.l   d0,d7
        move.l  d7,(a1)+
        cmpa.l  a4,a0
        bne     .grp
.done:  move.l  a3,d0
        movem.l (sp)+,d2-d7/a2-a4
        rts

;------------------------------------------------------------------ bp_dots
; Dots from bit-plane weights (tiny68k.c bitplane_blocks): per block of 8
; rows, plane k = 7..0 of u = w ^ $80, group g of 6 columns, row r: one
; byte holding 4 * (6 bits), a ready offset into the group's table.
; _bp_dots is the 7-arg wrapper (planes = 8); _bp_dots_p takes d5 = planes (1..8).
;   a0 = w, a1 = tab, d0 = groups, d1 = s32 *out, d2 = nblk, d3 = bias,
;   d4 = spill, d5 = planes (1..8)
_bp_dots:
        movem.l d2-d7/a2-a6,-(sp)
        moveq   #8,d5
        bra.s   bp_ent
_bp_dots_p:
        movem.l d2-d7/a2-a6,-(sp)
bp_ent:
        tst.l   d2
        ble     .bp_ret
        tst.l   d0
        ble     .bp_ret
        cmp.l   #1,d5
        bge.s   .pl_lo
        moveq   #1,d5
.pl_lo: cmp.l   #8,d5
        ble.s   .pl_ok
        moveq   #8,d5
.pl_ok: lea     -32(sp),sp
        move.l  d1,a5                   ; out
        move.l  d2,4(sp)                ; nblk
        move.l  d3,8(sp)                ; bias
        move.l  d4,12(sp)               ; spill
        moveq   #8,d1
        sub.l   d5,d1
        move.w  d1,24(sp)               ; drop = 8 - planes
        move.w  d0,d1
        subq.w  #1,d1
        move.w  d1,26(sp)               ; groups - 1
        moveq   #6,d6
        cmp.l   d6,d5
        ble.s   .wp
        move.l  d5,d7
        sub.l   d6,d7                   ; sp_planes = planes - 6 (1 or 2)
        bra.s   .ws
.wp:    move.l  d5,d6                   ; w_planes = planes
        moveq   #0,d7                   ; sp_planes = 0
.ws:    move.w  d6,20(sp)               ; w_planes
        move.w  d7,22(sp)               ; sp_planes
        moveq   #0,d1
        cmp.w   #1,d7                   ; planes == 7 -> skip 1 unused spill plane per block
        bne.s   .sskip
        move.l  d0,d1
        lsl.l   #3,d1
.sskip: move.l  d1,28(sp)               ; sp_skip = (planes == 7) ? groups * 8 : 0
        moveq   #6,d1
        sub.l   d6,d1                   ; 6 - w_planes
        mulu.w  d0,d1
        lsl.l   #3,d1                   ; w_skip = (6 - w_planes) * groups * 8
        move.l  d1,16(sp)
        moveq   #0,d1                   ; d1 upper 24 bits stay 0 throughout
        cmp.l   #16,d0
        bne     .gen_blk

; Fast path for groups == 16 (D = 96): unrolled 16 groups, plane 7 skips zeroing/doubling.
.p16_blk:
        move.w  20(sp),(sp)             ; active w_planes
        move.w  22(sp),2(sp)            ; saved sp_planes
        suba.l  a6,a6                   ; 0 = in w, 1 = in spill
        move.l  a1,a2                   ; tab group 0
        move.b  (a0)+,d1
        move.l  0(a2,d1.w),d2
        move.b  (a0)+,d1
        move.l  0(a2,d1.w),d3
        move.b  (a0)+,d1
        move.l  0(a2,d1.w),d4
        move.b  (a0)+,d1
        move.l  0(a2,d1.w),d5
        move.b  (a0)+,d1
        move.l  0(a2,d1.w),d6
        move.b  (a0)+,d1
        move.l  0(a2,d1.w),d7
        move.b  (a0)+,d1
        movea.l 0(a2,d1.w),a3
        move.b  (a0)+,d1
        movea.l 0(a2,d1.w),a4
        bra.s   .p16_g1

.p16_next:
        add.l   d2,d2
        add.l   d3,d3
        add.l   d4,d4
        add.l   d5,d5
        add.l   d6,d6
        add.l   d7,d7
        adda.l  a3,a3
        adda.l  a4,a4
        move.l  a1,a2
        move.b  (a0)+,d1
        add.l   0(a2,d1.w),d2
        move.b  (a0)+,d1
        add.l   0(a2,d1.w),d3
        move.b  (a0)+,d1
        add.l   0(a2,d1.w),d4
        move.b  (a0)+,d1
        add.l   0(a2,d1.w),d5
        move.b  (a0)+,d1
        add.l   0(a2,d1.w),d6
        move.b  (a0)+,d1
        add.l   0(a2,d1.w),d7
        move.b  (a0)+,d1
        adda.l  0(a2,d1.w),a3
        move.b  (a0)+,d1
        adda.l  0(a2,d1.w),a4

.p16_g1:
        REPT    15
        lea     256(a2),a2
        move.b  (a0)+,d1
        add.l   0(a2,d1.w),d2
        move.b  (a0)+,d1
        add.l   0(a2,d1.w),d3
        move.b  (a0)+,d1
        add.l   0(a2,d1.w),d4
        move.b  (a0)+,d1
        add.l   0(a2,d1.w),d5
        move.b  (a0)+,d1
        add.l   0(a2,d1.w),d6
        move.b  (a0)+,d1
        add.l   0(a2,d1.w),d7
        move.b  (a0)+,d1
        adda.l  0(a2,d1.w),a3
        move.b  (a0)+,d1
        adda.l  0(a2,d1.w),a4
        ENDR
        subq.w  #1,(sp)
        bgt     .p16_next
        tst.w   2(sp)
        beq.s   .p16_bend
        move.w  2(sp),(sp)
        clr.w   2(sp)
        addq.l  #1,a6                   ; phase = spill
        move.l  a0,d0
        move.l  12(sp),a0
        move.l  d0,12(sp)
        bra     .p16_next

.p16_bend:
        move.w  a6,d0
        beq.s   .p16_skip
        adda.l  28(sp),a0               ; skip unused spill plane if planes == 7
        move.l  a0,d0
        move.l  12(sp),a0
        move.l  d0,12(sp)
.p16_skip:
        tst.l   16(sp)
        beq.s   .p16_sh
        adda.l  16(sp),a0
.p16_sh:
        move.w  24(sp),d0               ; drop
        beq.s   .p16_bias
        lsl.l   d0,d2
        lsl.l   d0,d3
        lsl.l   d0,d4
        lsl.l   d0,d5
        lsl.l   d0,d6
        lsl.l   d0,d7
        move.l  a3,d1
        lsl.l   d0,d1
        move.l  d1,a3
        move.l  a4,d1
        lsl.l   d0,d1
        move.l  d1,a4
        moveq   #0,d1                   ; restore d1 upper 24 bits = 0
.p16_bias:
        move.l  8(sp),d0
        sub.l   d0,d2
        sub.l   d0,d3
        sub.l   d0,d4
        sub.l   d0,d5
        sub.l   d0,d6
        sub.l   d0,d7
        suba.l  d0,a3
        suba.l  d0,a4
        movem.l d2-d7/a3-a4,(a5)
        lea     32(a5),a5
        subq.l  #1,4(sp)
        bne     .p16_blk
        bra     .bp_done

; General path for arbitrary groups != 16.
.gen_blk:
        moveq   #0,d2
        moveq   #0,d3
        moveq   #0,d4
        moveq   #0,d5
        moveq   #0,d6
        moveq   #0,d7
        suba.l  a3,a3
        suba.l  a4,a4
        move.w  20(sp),(sp)
        move.w  22(sp),2(sp)
        suba.l  a6,a6
.gen_pl:
        add.l   d2,d2
        add.l   d3,d3
        add.l   d4,d4
        add.l   d5,d5
        add.l   d6,d6
        add.l   d7,d7
        adda.l  a3,a3
        adda.l  a4,a4
        move.l  a1,a2
        move.w  26(sp),d0
.gen_grp:
        move.b  (a0)+,d1
        add.l   0(a2,d1.w),d2
        move.b  (a0)+,d1
        add.l   0(a2,d1.w),d3
        move.b  (a0)+,d1
        add.l   0(a2,d1.w),d4
        move.b  (a0)+,d1
        add.l   0(a2,d1.w),d5
        move.b  (a0)+,d1
        add.l   0(a2,d1.w),d6
        move.b  (a0)+,d1
        add.l   0(a2,d1.w),d7
        move.b  (a0)+,d1
        adda.l  0(a2,d1.w),a3
        move.b  (a0)+,d1
        adda.l  0(a2,d1.w),a4
        lea     256(a2),a2
        dbra    d0,.gen_grp
        subq.w  #1,(sp)
        bgt.s   .gen_pl
        tst.w   2(sp)
        beq.s   .gen_bend
        move.w  2(sp),(sp)
        clr.w   2(sp)
        addq.l  #1,a6
        move.l  a0,d0
        move.l  12(sp),a0
        move.l  d0,12(sp)
        bra.s   .gen_pl
.gen_bend:
        move.w  a6,d0
        beq.s   .gen_skip
        adda.l  28(sp),a0               ; skip unused spill plane if planes == 7
        move.l  a0,d0
        move.l  12(sp),a0
        move.l  d0,12(sp)
.gen_skip:
        tst.l   16(sp)
        beq.s   .gen_sh
        adda.l  16(sp),a0
.gen_sh:
        move.w  24(sp),d0
        beq.s   .gen_bias
        lsl.l   d0,d2
        lsl.l   d0,d3
        lsl.l   d0,d4
        lsl.l   d0,d5
        lsl.l   d0,d6
        lsl.l   d0,d7
        move.l  a3,d1
        lsl.l   d0,d1
        move.l  d1,a3
        move.l  a4,d1
        lsl.l   d0,d1
        move.l  d1,a4
        moveq   #0,d1
.gen_bias:
        move.l  8(sp),d0
        sub.l   d0,d2
        sub.l   d0,d3
        sub.l   d0,d4
        sub.l   d0,d5
        sub.l   d0,d6
        sub.l   d0,d7
        suba.l  d0,a3
        suba.l  d0,a4
        movem.l d2-d7/a3-a4,(a5)
        lea     32(a5),a5
        subq.l  #1,4(sp)
        bne     .gen_blk

.bp_done:
        lea     32(sp),sp
.bp_ret:
        movem.l (sp)+,d2-d7/a2-a6
        rts

;------------------------------------------------------- dots_i8_x16_sparse_T
; Transposed sparse matmul (for f2): wt is cols x rows (col-major, contiguous
; rows per col). pairs holds k (u16 col_byte_offset = col * rows, s16 val).
; Processes 8 rows at a time in registers d2-d7/a5-a6 with sequential (a0)+.
;   a0 = const s8 *wt, a1 = const s16 *pairs_off, d0 = s32 k,
;   d1 = s32 *out, d2 = s32 rows
_dots_i8_x16_sparse_T:
        tst.l   d2
        ble     .st_ret
        tst.l   d0
        bgt.s   .st_go
        move.l  d1,a0                   ; k <= 0: zero out[0..rows-1]
        move.w  d2,d1
        subq.w  #1,d1
        moveq   #0,d0
.st_z:  move.l  d0,(a0)+
        dbra    d1,.st_z
.st_ret:
        rts
.st_go: movem.l d2-d7/a2-a6,-(sp)
        lea     -16(sp),sp
        move.l  a1,(sp)                 ; 0(sp) = pairs_start
        move.l  d1,4(sp)                ; 4(sp) = out ptr
        lsl.l   #2,d0
        lea     0(a1,d0.l),a2           ; a2 = pairs_end
        move.l  a0,a4                   ; a4 = wt + r_base
        move.l  d2,d0
        lsr.l   #3,d0                   ; nblk = rows >> 3
        move.l  d0,8(sp)
        and.w   #7,d2
        move.w  d2,12(sp)               ; tail = rows & 7
        tst.l   d0
        beq.s   .st_tail
.st_blk:
        moveq   #0,d2
        moveq   #0,d3
        moveq   #0,d4
        moveq   #0,d5
        moveq   #0,d6
        moveq   #0,d7
        suba.l  a5,a5
        suba.l  a6,a6
        move.l  (sp),a1
.st_pr: moveq   #0,d0
        move.w  (a1)+,d0                ; byte offset col * rows (u16)
        lea     0(a4,d0.l),a0
        move.w  (a1)+,d0                ; xq[col] (s16)
        move.b  (a0)+,d1
        ext.w   d1
        muls.w  d0,d1
        add.l   d1,d2
        move.b  (a0)+,d1
        ext.w   d1
        muls.w  d0,d1
        add.l   d1,d3
        move.b  (a0)+,d1
        ext.w   d1
        muls.w  d0,d1
        add.l   d1,d4
        move.b  (a0)+,d1
        ext.w   d1
        muls.w  d0,d1
        add.l   d1,d5
        move.b  (a0)+,d1
        ext.w   d1
        muls.w  d0,d1
        add.l   d1,d6
        move.b  (a0)+,d1
        ext.w   d1
        muls.w  d0,d1
        add.l   d1,d7
        move.b  (a0)+,d1
        ext.w   d1
        muls.w  d0,d1
        adda.l  d1,a5
        move.b  (a0)+,d1
        ext.w   d1
        muls.w  d0,d1
        adda.l  d1,a6
        cmpa.l  a2,a1
        bne.s   .st_pr
        move.l  4(sp),a0
        movem.l d2-d7/a5-a6,(a0)
        lea     32(a0),a0
        move.l  a0,4(sp)
        addq.l  #8,a4
        subq.l  #1,8(sp)
        bne     .st_blk
.st_tail:
        move.w  12(sp),d5
        beq.s   .st_done
        subq.w  #1,d5
        move.l  4(sp),a3
.st_tr: moveq   #0,d2
        move.l  (sp),a1
.st_tp: moveq   #0,d0
        move.w  (a1)+,d0
        move.b  0(a4,d0.l),d1
        ext.w   d1
        muls.w  (a1)+,d1
        add.l   d1,d2
        cmpa.l  a2,a1
        bne.s   .st_tp
        move.l  d2,(a3)+
        addq.l  #1,a4
        dbra    d5,.st_tr
.st_done:
        lea     16(sp),sp
        movem.l (sp)+,d2-d7/a2-a6
        rts

;--------------------------------------------------------------- att_qk_head
; Compute att[j] = mulshift(dot_i8_x16(k + j*stride, q, hd), km[j], ks[j]-qsh)
; for j = 0 .. p1 - 1, returning max(att).
;   a0 = k, a1 = q, d0 = p1, d1 = hd, d2 = stride, d3 = qsh,
;   d4 = km (u16*), d5 = ks (s16*), d6 = att (s32*) -> d0 = mx
_att_qk_head:
        movem.l d2-d7/a2-a6,-(sp)
        lea     -12(sp),sp
        move.l  d0,(sp)                 ; 0(sp) = j count (p1)
        move.l  d1,4(sp)                ; 4(sp) = hd
        move.l  d2,8(sp)                ; 8(sp) = stride
        move.l  a1,a2                   ; q_base
        move.l  d4,a3                   ; km
        move.l  d5,a4                   ; ks
        move.l  d6,a5                   ; att
        move.l  d3,d6                   ; qsh
        move.l  #$80000000,d7           ; mx
        cmp.l   #24,d1
        bne     .qk_gen
        sub.l   d1,d2                   ; stride - 24
        move.l  d2,a6                   ; k_step
.qk_24: moveq   #0,d0
        REPT    24
        move.b  (a0)+,d1
        ext.w   d1
        muls.w  (a1)+,d1
        add.l   d1,d0
        ENDR
        adda.l  a6,a0
        move.l  a2,a1
        moveq   #0,d1
        move.w  (a3)+,d1                ; m
        move.w  (a4)+,d2
        ext.l   d2
        sub.l   d6,d2                   ; sh = (int)ks[j] - qsh
        moveq   #16,d4
        sub.l   d4,d2
        bmi.s   .qk_ms
        move.l  d0,d5                   ; inline sh >= 16 fast path
        bpl.s   .qk_p
        neg.l   d0
.qk_p:  move.l  d0,d3
        swap    d3
        mulu.w  d1,d0
        mulu.w  d1,d3
        clr.w   d0
        swap    d0
        add.l   d3,d0
        lsr.l   d2,d0
        tst.l   d5
        bpl.s   .qk_st
        neg.l   d0
        bra.s   .qk_st
.qk_ms: add.l   d4,d2
        bsr     mscore
.qk_st: move.l  d0,(a5)+
        cmp.l   d7,d0
        ble.s   .qk_nx
        move.l  d0,d7
.qk_nx: subq.l  #1,(sp)
        bne     .qk_24
        bra.s   .qk_dn

.qk_gen:
        move.l  a0,a6                   ; current k_row
.qk_gl: move.l  a6,a0
        move.l  a2,a1
        move.l  4(sp),d0                ; hd
        bsr     dotcore
        adda.l  8(sp),a6                ; k_row += stride
        moveq   #0,d1
        move.w  (a3)+,d1
        move.w  (a4)+,d2
        ext.l   d2
        sub.l   d6,d2
        bsr     mscore
        move.l  d0,(a5)+
        cmp.l   d7,d0
        ble.s   .qk_gn
        move.l  d0,d7
.qk_gn: subq.l  #1,(sp)
        bne.s   .qk_gl

.qk_dn: move.l  d7,d0
        lea     12(sp),sp
        movem.l (sp)+,d2-d7/a2-a6
        rts

;----------------------------------------------------------- att_softmax_wgt
; Pass 1: att[j] = fxexp_neg(exp_tab, mx - att[j]), tot = sum att[j].
; inv = fxdiv(ONE, tot).
; Pass 2: wgt[j] = mulshift(fxmul(att[j], inv), vm[j], vs[j]).
;   a0 = s32 *att, a1 = s32 *wgt, d0 = s32 p1, d1 = s32 mx,
;   d2 = const s32 *exp_tab, d3 = const u16 *vm, d4 = const s16 *vs
_att_softmax_wgt:
        tst.l   d0
        ble     .sw_ret
        movem.l d2-d7/a2-a6,-(sp)
        lea     -20(sp),sp
        move.l  a0,(sp)                 ; 0(sp) = att_start
        move.l  a1,4(sp)                ; 4(sp) = wgt_start
        move.l  d0,8(sp)                ; 8(sp) = p1
        move.l  d3,12(sp)               ; 12(sp) = vm
        move.l  d4,16(sp)               ; 16(sp) = vs
        move.l  d2,a2                   ; exp_tab
        move.l  d1,d6                   ; mx
        moveq   #0,d7                   ; tot
        move.w  d0,d5
        subq.w  #1,d5                   ; p1 - 1
        move.l  #1048576,d4             ; EXP_MAX << 16
        move.l  #65536,a3               ; ONE
.sw_e1: move.l  d6,d0
        sub.l   (a0),d0                 ; u = mx - att[j]
        ble.s   .sw_one
        cmp.l   d4,d0
        bge.s   .sw_z
        move.l  d0,d1
        moveq   #10,d2
        asr.l   d2,d1                   ; idx = u >> 10
        lsl.l   #2,d1
        lea     0(a2,d1.l),a4
        move.l  (a4)+,d2                ; a = tab[idx]
        move.l  (a4),d3                 ; b = tab[idx+1]
        sub.l   d2,d3                   ; diff = b - a
        lsl.l   #6,d0                   ; frac in d0.w (0..65535)
        tst.l   d3
        bmi.s   .sw_neg
        cmp.l   a3,d3
        bcc.s   .sw_big
        mulu.w  d0,d3                   ; diff * frac (both u16)
        clr.w   d3
        swap    d3
        add.l   d3,d2
        move.l  d2,d0
        bra.s   .sw_es
.sw_neg:
        neg.l   d3                      ; |diff| = a - b > 0
        cmp.l   a3,d3
        bcc.s   .sw_nbg
        mulu.w  d0,d3
        neg.l   d3
        swap    d3
        ext.l   d3                      ; arithmetic >> 16
        add.l   d3,d2
        move.l  d2,d0
        bra.s   .sw_es
.sw_nbg:
        move.l  d3,d1
        swap    d1
        mulu.w  d0,d1                   ; hi * frac
        mulu.w  d0,d3                   ; lo * frac
        clr.w   d3
        swap    d3
        add.l   d1,d3
        neg.l   d3
        add.l   d3,d2
        move.l  d2,d0
        bra.s   .sw_es
.sw_big:
        move.l  d3,d1
        swap    d1
        mulu.w  d0,d1
        mulu.w  d0,d3
        clr.w   d3
        swap    d3
        add.l   d1,d3
        add.l   d3,d2
        move.l  d2,d0
        bra.s   .sw_es
.sw_one:
        move.l  a3,d0
        bra.s   .sw_es
.sw_z:  moveq   #0,d0
.sw_es: move.l  d0,(a0)+
        add.l   d0,d7
        dbra    d5,.sw_e1

        move.l  a3,d0                   ; ONE
        move.l  d7,d1                   ; tot
        bsr     _fxdiv                  ; d0 = inv
        move.l  d0,d6                   ; inv
        move.l  (sp),a0                 ; att
        move.l  4(sp),a1                ; wgt
        move.l  12(sp),a2               ; vm
        move.l  16(sp),a3               ; vs
        move.w  10(sp),d7               ; p1 (low word of 8(sp))
        subq.w  #1,d7
.sw_w2: move.l  (a0)+,d0
        beq.s   .sw_wz
        move.l  d6,d1
        bsr     fxcore                  ; pj = fxmul(att[j], inv)
        moveq   #0,d1
        move.w  (a2)+,d1                ; vm[j]
        move.w  (a3)+,d2
        ext.l   d2                      ; vs[j]
        bsr     mscore
        move.l  d0,(a1)+
        dbra    d7,.sw_w2
        bra.s   .sw_dn
.sw_wz: clr.l   (a1)+
        addq.l  #2,a2
        addq.l  #2,a3
        dbra    d7,.sw_w2

.sw_dn: lea     20(sp),sp
        movem.l (sp)+,d2-d7/a2-a6
.sw_ret:
        rts

;--------------------------------------------------------------- att_wv_head
; Compute ba[i] = (sum_j wq16[j] * vc[j * stride + i]) >> s (or << -s)
; for i = 0 .. hd - 1. Outer j loop, 8 i-accumulators in registers, skips
; wq16[j] == 0.
;   a0 = const s16 *wq16, a1 = const s8 *vc, d0 = s32 p1, d1 = s32 hd,
;   d2 = s32 stride, d3 = s32 s, d4 = s32 *ba
_att_wv_head:
        tst.l   d0
        ble     .wv_ret
        tst.l   d1
        ble     .wv_ret
        movem.l d2-d7/a2-a6,-(sp)
        lea     -24(sp),sp
        move.l  a0,(sp)                 ; 0(sp) = wq16_start
        move.l  a1,4(sp)                ; 4(sp) = vc_ptr
        move.l  d0,8(sp)                ; 8(sp) = p1
        move.l  d2,12(sp)               ; 12(sp) = stride
        move.l  d3,16(sp)               ; 16(sp) = s
        move.l  d4,20(sp)               ; 20(sp) = ba_ptr
        lsl.l   #1,d0
        lea     0(a0,d0.l),a2           ; a2 = wq16_end
        move.l  d2,a6                   ; a6 = stride
        subq.l  #8,d2
        move.l  d2,a5                   ; a5 = stride - 8
        move.l  d1,d0
        lsr.l   #3,d0                   ; nblk = hd >> 3
        and.w   #7,d1
        move.w  d1,-(sp)                ; stack shifted +2: 0=tail, +2..24
        move.w  d0,-(sp)                ; stack shifted +4: 0=nblk, 2=tail, 4..26
        beq     .wv_tail
.wv_blk:
        moveq   #0,d2
        moveq   #0,d3
        moveq   #0,d4
        moveq   #0,d5
        moveq   #0,d6
        moveq   #0,d7
        suba.l  a3,a3
        suba.l  a4,a4
        move.l  4(sp),a0                ; wq16_start
        move.l  8(sp),a1                ; vc_ptr
.wv_j:  move.w  (a0)+,d0
        beq.s   .wv_sk
        move.b  (a1)+,d1
        ext.w   d1
        muls.w  d0,d1
        add.l   d1,d2
        move.b  (a1)+,d1
        ext.w   d1
        muls.w  d0,d1
        add.l   d1,d3
        move.b  (a1)+,d1
        ext.w   d1
        muls.w  d0,d1
        add.l   d1,d4
        move.b  (a1)+,d1
        ext.w   d1
        muls.w  d0,d1
        add.l   d1,d5
        move.b  (a1)+,d1
        ext.w   d1
        muls.w  d0,d1
        add.l   d1,d6
        move.b  (a1)+,d1
        ext.w   d1
        muls.w  d0,d1
        add.l   d1,d7
        move.b  (a1)+,d1
        ext.w   d1
        muls.w  d0,d1
        adda.l  d1,a3
        move.b  (a1)+,d1
        ext.w   d1
        muls.w  d0,d1
        adda.l  d1,a4
        adda.l  a5,a1
        cmpa.l  a2,a0
        bne     .wv_j
        bra.s   .wv_sh
.wv_sk: adda.l  a6,a1
        cmpa.l  a2,a0
        bne     .wv_j

.wv_sh: move.l  20(sp),d0               ; s
        bmi.s   .wv_lsl
        beq.s   .wv_st
        asr.l   d0,d2
        asr.l   d0,d3
        asr.l   d0,d4
        asr.l   d0,d5
        asr.l   d0,d6
        asr.l   d0,d7
        move.l  a3,d1
        asr.l   d0,d1
        move.l  d1,a3
        move.l  a4,d1
        asr.l   d0,d1
        move.l  d1,a4
        bra.s   .wv_st
.wv_lsl:
        neg.l   d0
        lsl.l   d0,d2
        lsl.l   d0,d3
        lsl.l   d0,d4
        lsl.l   d0,d5
        lsl.l   d0,d6
        lsl.l   d0,d7
        move.l  a3,d1
        lsl.l   d0,d1
        move.l  d1,a3
        move.l  a4,d1
        lsl.l   d0,d1
        move.l  d1,a4
.wv_st: move.l  24(sp),a0               ; ba_ptr
        movem.l d2-d7/a3-a4,(a0)
        lea     32(a0),a0
        move.l  a0,24(sp)
        addq.l  #8,8(sp)                ; vc_ptr += 8
        subq.w  #1,(sp)
        bne     .wv_blk

.wv_tail:
        move.w  2(sp),d5                ; tail (0..7)
        beq.s   .wv_dn
        subq.w  #1,d5
        move.l  24(sp),a3               ; ba_ptr
        move.l  8(sp),a4                ; vc_ptr
.wv_tl: move.l  4(sp),a0                ; wq16_start
        move.l  a4,a1                   ; vc_ptr
        move.l  12(sp),d0               ; p1
        move.l  a6,d1                   ; stride
        bsr     _dot_x16_i8s
        move.l  20(sp),d1               ; s
        bmi.s   .wv_tl2
        asr.l   d1,d0
        bra.s   .wv_ts
.wv_tl2:
        neg.l   d1
        lsl.l   d1,d0
.wv_ts: move.l  d0,(a3)+
        addq.l  #1,a4
        dbra    d5,.wv_tl

.wv_dn: lea     28(sp),sp
        movem.l (sp)+,d2-d7/a2-a6
.wv_ret:
        rts

        END
