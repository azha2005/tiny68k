;-----------------------------------------------------------------------------
; tui68k.s -- the keyboard interrupt of tui_amiga.c (hardware taken over)
;
; Level 2 (PORTS): one byte from the keyboard through CIA-A's serial port.
; Raw code = ~SDR rotated right one bit, bit 7 set = key released. The
; handshake holds SPMODE at output for at least 85 us (three raster line
; changes, as in the SMW port's kb_int). Codes go to a 64-byte ring buffer
; that tui_amiga.c empties: kb_head is written here, kb_tail there.
;-----------------------------------------------------------------------------

        XDEF    _kb_int
        XDEF    _kb_buf
        XDEF    _kb_head
        XDEF    _kb_tail
        XDEF    _render_row_asm

CIAA_SDR    equ $bfec01
CIAA_ICR    equ $bfed01
CIAA_CRA    equ $bfee01
VHPOSR      equ $dff006
INTREQ      equ $dff09c

        SECTION code,CODE

_kb_int:
        movem.l d0-d1/a0,-(sp)
        move.b  CIAA_ICR,d0             ; reading it clears it
        btst    #3,d0                   ; SP: a byte arrived
        beq.s   .x
        move.b  CIAA_SDR,d0
        or.b    #$40,CIAA_CRA           ; SPMODE output: handshake starts
        not.b   d0
        ror.b   #1,d0
        lea     _kb_buf,a0
        move.w  _kb_head,d1
        move.b  d0,0(a0,d1.w)
        addq.w  #1,d1
        and.w   #63,d1
        cmp.w   _kb_tail,d1
        beq.s   .hs                     ; full: drop it
        move.w  d1,_kb_head
.hs:    moveq   #3-1,d0                 ; 3 line changes: >= 2 whole lines
.l:     move.b  VHPOSR,d1
.w:     cmp.b   VHPOSR,d1
        beq.s   .w
        dbf     d0,.l
        and.b   #$bf,CIAA_CRA           ; SPMODE input
.x:     move.w  #$0008,INTREQ
        move.w  #$0008,INTREQ
        movem.l (sp)+,d0-d1/a0
        rte

;-----------------------------------------------------------------------------
; void render_row_asm(a0 = u8 *p0, a1 = u8 *p1, a2 = const u8 *ch,
;                     a3 = const u8 *at, a4 = u8 *dirty,
;                     d0 = const u8 *font8, d1 = const u8 *pentab,
;                     d2 = int cur_col)
;
; Draws any dirty cells on one 80-column text row into the two 80-byte-stride
; bitplanes and clears dirty[x]. cur_col is 0..79 or -1.
;-----------------------------------------------------------------------------
_render_row_asm:
        movem.l d2-d7/a2-a6,-(sp)
        move.l  d1,-(sp)                ; save pentab at (sp)
        move.l  d0,a5                   ; a5 = font8
        moveq   #-1,d6
        tst.l   d2
        blt.s   .noc
        moveq   #79,d6
        sub.w   d2,d6                   ; cursor matches when d7 == 79 - cur_col
.noc:   moveq   #79,d7
.col:   tst.b   (a4)+
        bne.s   .dirty
        addq.l  #1,a0
        addq.l  #1,a1
        addq.l  #1,a2
        addq.l  #1,a3
        dbra    d7,.col
        bra     .done

.dirty: clr.b   -1(a4)
        moveq   #0,d0
        move.b  (a3)+,d0                ; attribute byte
        move.l  (sp),a6                 ; pentab
        move.b  0(a6,d0.w),d0           ; pens = fg | (bg << 2)
        lsl.w   #2,d0
        lea     pen_masks,a6
        add.w   d0,a6
        move.b  (a6)+,d2                ; m0x = m0f ^ m0b
        move.b  (a6)+,d3                ; m0b
        move.b  (a6)+,d4                ; m1x = m1f ^ m1b
        move.b  (a6)+,d5                ; m1b

        moveq   #0,d0
        move.b  (a2)+,d0                ; char byte
        lsl.w   #3,d0                   ; * 8
        lea     0(a5,d0.w),a6           ; font8[ch]

        ; row 0
        move.b  (a6)+,d0
        move.b  d0,d1
        and.b   d2,d1
        eor.b   d3,d1
        move.b  d1,(a0)
        and.b   d4,d0
        eor.b   d5,d0
        move.b  d0,(a1)

        ; row 1
        move.b  (a6)+,d0
        move.b  d0,d1
        and.b   d2,d1
        eor.b   d3,d1
        move.b  d1,80(a0)
        and.b   d4,d0
        eor.b   d5,d0
        move.b  d0,80(a1)

        ; row 2
        move.b  (a6)+,d0
        move.b  d0,d1
        and.b   d2,d1
        eor.b   d3,d1
        move.b  d1,160(a0)
        and.b   d4,d0
        eor.b   d5,d0
        move.b  d0,160(a1)

        ; row 3
        move.b  (a6)+,d0
        move.b  d0,d1
        and.b   d2,d1
        eor.b   d3,d1
        move.b  d1,240(a0)
        and.b   d4,d0
        eor.b   d5,d0
        move.b  d0,240(a1)

        ; row 4
        move.b  (a6)+,d0
        move.b  d0,d1
        and.b   d2,d1
        eor.b   d3,d1
        move.b  d1,320(a0)
        and.b   d4,d0
        eor.b   d5,d0
        move.b  d0,320(a1)

        ; row 5
        move.b  (a6)+,d0
        move.b  d0,d1
        and.b   d2,d1
        eor.b   d3,d1
        move.b  d1,400(a0)
        and.b   d4,d0
        eor.b   d5,d0
        move.b  d0,400(a1)

        ; row 6
        move.b  (a6)+,d0
        move.b  d0,d1
        and.b   d2,d1
        eor.b   d3,d1
        move.b  d1,480(a0)
        and.b   d4,d0
        eor.b   d5,d0
        move.b  d0,480(a1)

        ; row 7 (underline cursor)
        move.b  (a6)+,d0
        cmp.w   d6,d7
        bne.s   .r7
        moveq   #-1,d0
.r7:    move.b  d0,d1
        and.b   d2,d1
        eor.b   d3,d1
        move.b  d1,560(a0)
        and.b   d4,d0
        eor.b   d5,d0
        move.b  d0,560(a1)

        addq.l  #1,a0
        addq.l  #1,a1
        dbra    d7,.col

.done:  addq.w  #4,sp
        movem.l (sp)+,d2-d7/a2-a6
        rts

; (m0f^m0b, m0b, m1f^m1b, m1b) for pens = fg | (bg << 2), 0..15
pen_masks:
        dc.b    $00,$00,$00,$00
        dc.b    $ff,$00,$00,$00
        dc.b    $00,$00,$ff,$00
        dc.b    $ff,$00,$ff,$00
        dc.b    $ff,$ff,$00,$00
        dc.b    $00,$ff,$00,$00
        dc.b    $ff,$ff,$ff,$00
        dc.b    $00,$ff,$ff,$00
        dc.b    $00,$00,$ff,$ff
        dc.b    $ff,$00,$ff,$ff
        dc.b    $00,$00,$00,$ff
        dc.b    $ff,$00,$00,$ff
        dc.b    $ff,$ff,$ff,$ff
        dc.b    $00,$ff,$ff,$ff
        dc.b    $ff,$ff,$00,$ff
        dc.b    $00,$ff,$00,$ff

        SECTION data,DATA
_kb_head:
        dc.w    0
_kb_tail:
        dc.w    0
_kb_buf:
        ds.b    64

        END
