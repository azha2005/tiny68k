;-----------------------------------------------------------------------------
; intro/intro68k.s -- Level-2 PORTS keyboard handler + fast Chip RAM clear
;-----------------------------------------------------------------------------
        XDEF    _intro_kb_int
        XDEF    _intro_last_key

CIAA_SDR    equ $bfec01
CIAA_ICR    equ $bfed01
CIAA_CRA    equ $bfee01
VHPOSR      equ $dff006
INTREQ      equ $dff09c

        SECTION code,CODE

_intro_kb_int:
        movem.l d0-d1,-(sp)
        move.b  CIAA_ICR,d0
        btst    #3,d0
        beq.s   .x
        move.b  CIAA_SDR,d0
        or.b    #$40,CIAA_CRA
        not.b   d0
        ror.b   #1,d0
        btst    #7,d0                   ; ignore key up
        bne.s   .hs
        move.b  d0,_intro_last_key
.hs:    moveq   #2,d0
.l:     move.b  VHPOSR,d1
.w:     cmp.b   VHPOSR,d1
        beq.s   .w
        dbf     d0,.l
        and.b   #$bf,CIAA_CRA
.x:     move.w  #$0008,INTREQ
        move.w  #$0008,INTREQ
        movem.l (sp)+,d0-d1
        rte

        SECTION bss,BSS
_intro_last_key:
        ds.b    2
