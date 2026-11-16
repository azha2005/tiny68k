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

        SECTION data,DATA
_kb_head:
        dc.w    0
_kb_tail:
        dc.w    0
_kb_buf:
        ds.b    64

        END
