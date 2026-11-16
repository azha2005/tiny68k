/* tui_amiga.c -- tinyui's backend on the A500 (KS 1.2), vbcc +kick13.
 *
 * Phase 1, sys_init(): the OS keeps running (AmigaDOS still has to read
 * tiny.t68) but the display is ours: LoadView(NULL), then our copper list
 * (640 x 200 hires, 2 planes, centred in PAL, no sprites) in chip RAM.
 * topaz 8 comes from the ROM through OpenFont.
 * Phase 2, sys_takeover(): Forbid, every interrupt off except PORTS with our
 * keyboard handler (tui68k.s), DMA = bitplanes + copper only, drive motor
 * off. 2 hires planes leave the 68000 all its bus slots.
 * sys_done() puts everything back, so the CLI returns.
 *
 * Time: CIA-A's TOD counts vertical syncs (50 Hz) with or without the OS;
 * short waits count raster lines (64 us). The clock in the title bar is a
 * DateStamp taken at start plus TOD.
 *
 * No NDK headers here: OS calls are declared with vbcc's __reg syntax and
 * the few structure offsets are written out (KS 1.2 values). */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "conio68k.h"
#include "render68k.h"
#include "tui_sys.h"

typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned long u32;

/* ---------------------------------------------------------- OS calls */
extern void *SysBase, *DOSBase;
void *os_allocmem(__reg("a6") void *b, __reg("d0") u32 size, __reg("d1") u32 req) = "\tjsr\t-198(a6)";
void os_freemem(__reg("a6") void *b, __reg("a1") void *p, __reg("d0") u32 size) = "\tjsr\t-210(a6)";
void os_forbid(__reg("a6") void *b) = "\tjsr\t-132(a6)";
void os_permit(__reg("a6") void *b) = "\tjsr\t-138(a6)";
void *os_openlib(__reg("a6") void *b, __reg("a1") const char *name, __reg("d0") u32 v) = "\tjsr\t-552(a6)";
void os_closelib(__reg("a6") void *b, __reg("a1") void *lib) = "\tjsr\t-414(a6)";
void gfx_loadview(__reg("a6") void *b, __reg("a1") void *view) = "\tjsr\t-222(a6)";
void gfx_waittof(__reg("a6") void *b) = "\tjsr\t-270(a6)";
void *gfx_openfont(__reg("a6") void *b, __reg("a0") void *textattr) = "\tjsr\t-72(a6)";
void gfx_closefont(__reg("a6") void *b, __reg("a1") void *font) = "\tjsr\t-78(a6)";
struct ui_ds { long days, minute, tick; };
void dos_datestamp(__reg("a6") void *b, __reg("d1") struct ui_ds *ds) = "\tjsr\t-192(a6)";

#define MEMF_CHIP  2UL
#define MEMF_CLEAR 0x10000UL

/* GfxBase: ActiView 34, copinit 38, LOFlist 50, SHFlist 54.
   TextFont: tf_LoChar 32, tf_HiChar 33, tf_CharData 34, tf_Modulo 38,
   tf_CharLoc 40. */
#define GB_ACTIVIEW 34
#define GB_COPINIT  38
#define GB_LOFLIST  50
#define GB_SHFLIST  54

/* --------------------------------------------------------- hardware */
#define REG16(o) (*(volatile u16 *)(0xDFF000UL + (o)))
#define REG32(o) (*(volatile u32 *)(0xDFF000UL + (o)))
#define DMACONR 0x002
#define VPOSR   0x004
#define INTENAR 0x01C
#define COP1LC  0x080
#define COPJMP1 0x088
#define DMACON  0x096
#define INTENA  0x09A
#define INTREQ  0x09C
#define CIAA_TODLO (*(volatile u8 *)0xBFE801UL)
#define CIAA_TODMID (*(volatile u8 *)0xBFE901UL)
#define CIAA_TODHI (*(volatile u8 *)0xBFEA01UL)
#define CIAB_PRB  (*(volatile u8 *)0xBFD100UL)
#define VEC_LEVEL2 (*(void *volatile *)0x68UL)

extern void kb_int(void);
extern volatile u8 kb_buf[64];
extern volatile u16 kb_head, kb_tail;

/* ---------------------------------------------------------- state */
#define COP_WORDS 80                          /* 68 used, the last 2 the empty sprite */
#define CHIP_BYTES (2L * PLANE_BYTES + 2L * COP_WORDS + 8)
static u8 *chip, *pl0, *pl1;
static u16 *cop, *cop_colour;               /* the 4 colour values in the list */
static void *gfx;
static void *old_view, *old_lof, *old_shf;
static u16 old_dmacon, old_intena;
static void *old_vec2;
static int phase, theme, cursor_on;
static u32 tod0;
static long minute0;

static void write_shot(void);

static u32 tod(void)
{
    u32 h = CIAA_TODHI, m = CIAA_TODMID, l = CIAA_TODLO;   /* HI latches, LO frees */
    return h << 16 | m << 8 | l;
}

/* wait n raster lines (64 us each), counting changes of the line number */
static void wait_lines(long n)
{
    u16 prev = (u16)(REG32(VPOSR) >> 8), now;
    while (n > 0) {
        now = (u16)(REG32(VPOSR) >> 8);
        if (now != prev) { prev = now; n--; }
    }
}

static void set_colours(const unsigned short *rgb)
{
    int i;
    for (i = 0; i < 4; i++) cop_colour[2 * i] = rgb[i];
}

static void build_copper(void)
{
    u16 *c = cop;
    u32 a0 = (u32)pl0, a1 = (u32)pl1, ns = (u32)(cop + COP_WORDS - 2);
    int i;
    *c++ = 0x0100; *c++ = 0xA200;           /* BPLCON0: hires, 2 planes, colour */
    *c++ = 0x0102; *c++ = 0x0000;
    *c++ = 0x0104; *c++ = 0x0000;
    *c++ = 0x0108; *c++ = 0x0000;           /* modulos */
    *c++ = 0x010A; *c++ = 0x0000;
    *c++ = 0x008E; *c++ = 0x4881;           /* DIWSTRT: 200 lines from $48 */
    *c++ = 0x0090; *c++ = 0x10C1;           /* DIWSTOP: line $110 */
    *c++ = 0x0092; *c++ = 0x003C;           /* DDFSTRT/STOP: 640 hires */
    *c++ = 0x0094; *c++ = 0x00D4;
    *c++ = 0x00E0; *c++ = (u16)(a0 >> 16);
    *c++ = 0x00E2; *c++ = (u16)a0;
    *c++ = 0x00E4; *c++ = (u16)(a1 >> 16);
    *c++ = 0x00E6; *c++ = (u16)a1;
    for (i = 0; i < 8; i++) {               /* every sprite: an empty one */
        *c++ = (u16)(0x0120 + 4 * i); *c++ = (u16)(ns >> 16);
        *c++ = (u16)(0x0122 + 4 * i); *c++ = (u16)ns;
    }
    cop_colour = c + 1;
    for (i = 0; i < 4; i++) { *c++ = (u16)(0x0180 + 2 * i); *c++ = 0; }
    *c++ = 0xFFFF; *c++ = 0xFFFE;
    cop[COP_WORDS - 2] = 0; cop[COP_WORDS - 1] = 0;     /* the empty sprite */
}

static const char *grab_topaz(void)
{
    static struct { const char *name; u16 ysize; u8 style, flags; } ta = { "topaz.font", 8, 0, 0 };
    u8 *f = (u8 *)gfx_openfont(gfx, &ta);
    static u32 loc[256];
    int lo, hi, c;
    const u32 *cl;
    if (!f) return "topaz.font 8 not available";
    lo = f[32]; hi = f[33];
    cl = *(const u32 **)(f + 40);
    for (c = 0; c <= hi - lo && c < 256; c++) loc[c] = cl[c];
    font_from_topaz(*(const u8 **)(f + 34), *(u16 *)(f + 38), loc, lo, hi);
    font_finish();
    gfx_closefont(gfx, f);
    return NULL;
}

const char *sys_init(int t)
{
    static long dsbuf[4];
    struct ui_ds *d = (struct ui_ds *)(((u32)dsbuf + 3) & ~3UL);   /* BCPL: aligned */
    const char *err;
    theme = t;
    gfx = os_openlib(SysBase, "graphics.library", 0);
    if (!gfx) return "no graphics.library";
    err = grab_topaz();
    if (err) return err;
    chip = (u8 *)os_allocmem(SysBase, CHIP_BYTES, MEMF_CHIP | MEMF_CLEAR);
    if (!chip) return "not enough chip memory for the screen";
    pl0 = (u8 *)(((u32)chip + 7) & ~7UL);
    pl1 = pl0 + PLANE_BYTES;
    cop = (u16 *)(pl1 + PLANE_BYTES);
    build_copper();
    set_colours(theme_rgb[theme]);
    dos_datestamp(DOSBase, d);
    minute0 = d->minute;
    tod0 = tod();
    old_view = *(void **)((u8 *)gfx + GB_ACTIVIEW);
    old_lof = *(void **)((u8 *)gfx + GB_LOFLIST);
    old_shf = *(void **)((u8 *)gfx + GB_SHFLIST);
    gfx_loadview(gfx, NULL);
    gfx_waittof(gfx);
    gfx_waittof(gfx);
    *(void **)((u8 *)gfx + GB_LOFLIST) = cop;          /* if the OS reloads it */
    *(void **)((u8 *)gfx + GB_SHFLIST) = cop;
    REG32(COP1LC) = (u32)cop;
    REG16(COPJMP1) = 0;
    phase = 1;
    return NULL;
}

void sys_takeover(void)
{
    if (phase != 1) return;
    os_forbid(SysBase);
    old_dmacon = REG16(DMACONR);
    old_intena = REG16(INTENAR);
    REG16(INTENA) = 0x7FFF;
    REG16(INTREQ) = 0x7FFF;
    REG16(DMACON) = 0x7FFF;
    REG32(COP1LC) = (u32)cop;
    REG16(COPJMP1) = 0;
    REG16(DMACON) = 0x83A0;                 /* MASTER | BPLEN | COPEN | SPREN: sprite
                                               DMA stays on so all 8 fetch the empty
                                               sprite (off, the last data would stick) */
    CIAB_PRB |= 0xF8;                       /* drive motor off: deselect all, */
    CIAB_PRB &= 0xF7;                       /* MTR high latched into df0 */
    CIAB_PRB |= 0x08;
    old_vec2 = VEC_LEVEL2;
    kb_head = kb_tail = 0;
    VEC_LEVEL2 = (void *)kb_int;
    REG16(INTREQ) = 0x0008;
    REG16(INTENA) = 0xC008;                 /* INTEN | PORTS */
    phase = 2;
}

void sys_done(void)
{
    if (!phase) return;
    sys_flush();
    if (phase == 2) {
        REG16(INTENA) = 0x7FFF;
        VEC_LEVEL2 = old_vec2;
        REG16(DMACON) = 0x7FFF;
        REG16(DMACON) = (u16)(0x8000 | old_dmacon);
        REG16(INTREQ) = 0x7FFF;
        REG16(INTENA) = (u16)(0x8000 | old_intena);
    }
    *(void **)((u8 *)gfx + GB_LOFLIST) = old_lof;
    *(void **)((u8 *)gfx + GB_SHFLIST) = old_shf;
    REG32(COP1LC) = *(u32 *)((u8 *)gfx + GB_COPINIT);
    REG16(COPJMP1) = 0;
    gfx_loadview(gfx, old_view);
    gfx_waittof(gfx);
    if (phase == 2) os_permit(SysBase);
    os_freemem(SysBase, chip, CHIP_BYTES);
    os_closelib(SysBase, gfx);
    phase = 0;
    write_shot();
}

void sys_flush(void)
{
    int x, y;
    con_cursor(&x, &y);
    render_flush(pl0, pl1, theme, cursor_on, x, y);
}

void sys_theme(int t)
{
    static const unsigned short blank[4] = { 0, 0, 0, 0 };
    theme = t;
    set_colours(blank);                     /* no frame of new pens in old colours */
    con_touch_all();
    sys_flush();
    set_colours(theme_rgb[theme]);
}

void sys_cursor(int on) { cursor_on = on; }

/* ------------------------------------------------------------ keys */

static const char kmap[2][0x41] = {
    "`1234567890-=\\\0000qwertyuiop[]\000123asdfghjkl;'\000\000456\000zxcvbnm,./\000.789 ",
    "~!@#$%^&*()_+|\0000QWERTYUIOP{}\000123ASDFGHJKL:\"\000\000456\000ZXCVBNM<>?\000.789 ",
};
static int shift, caps;

/* next key from the interrupt's buffer, or -1 */
static int poll_key(void)
{
    while (kb_tail != kb_head) {
        int raw = kb_buf[kb_tail], up = raw & 0x80, k = raw & 0x7F, c;
        kb_tail = (u16)((kb_tail + 1) & 63);
        if (k == 0x60 || k == 0x61) { shift = !up; continue; }
        if (k == 0x62) { caps = !up; continue; }   /* down = LED on, up = LED off */
        if (up) continue;
        if (k == 0x44 || k == 0x43) return K_ENTER;
        if (k == 0x41) return K_BS;
        if (k == 0x45) return K_ESC;
        if (k == 0x4C) return K_UP;
        if (k >= 0x50 && k <= 0x54) return K_F1 + (k - 0x50);
        if (k <= 0x40) {
            c = (unsigned char)kmap[shift][k];
            if (caps && c >= 'a' && c <= 'z') c -= 32;
            if (c >= 32 && c <= 126) return c;
        }
    }
    return -1;
}

static int held = -1;

int sys_kbhit(void)
{
    sys_flush();
    if (phase != 2) return 0;
    if (held < 0) held = poll_key();
    return held >= 0;
}

int sys_getch(void)
{
    int k;
    sys_flush();
    if (phase != 2) return K_ENTER;
    if (held >= 0) { k = held; held = -1; return k; }
    while ((k = poll_key()) < 0) ;
    return k;
}

long sys_ticks(void) { return (long)((tod() - tod0) & 0xFFFFFFUL); }

void sys_delay(unsigned ms)
{
    sys_flush();
    wait_lines((long)ms * 1000L / 64L);
}

void sys_gettime(int *h, int *m)
{
    long t = minute0 + sys_ticks() / 3000L;
    *h = (int)((t / 60) % 24);
    *m = (int)(t % 60);
}

void sys_tone(unsigned hz, unsigned ms) { (void)hz; sys_delay(ms); }

/* tests: keep the screen of the last shot, write it once the OS is back */
static u8 *shot;
static char shot_name[32];
#define SHOT_BYTES (6L + NTHEME * (8L + 2L * PLANE_BYTES) + 2L * SCRW * SCRH)

void sys_shot(const char *name)
{
    u8 *s;
    int t, i, cx, cy;
    if (!shot) shot = (u8 *)malloc(SHOT_BYTES);
    if (!shot) return;
    strncpy(shot_name, name, 31);
    s = shot;
    memcpy(s, "TSH1", 4); s[4] = (u8)theme; s[5] = NTHEME; s += 6;
    con_cursor(&cx, &cy);
    for (t = 0; t < NTHEME; t++) {
        for (i = 0; i < 4; i++) { *s++ = (u8)(theme_rgb[t][i] >> 8); *s++ = (u8)theme_rgb[t][i]; }
        con_touch_all();
        render_flush(s, s + PLANE_BYTES, t, cursor_on, cx, cy);
        s += 2L * PLANE_BYTES;
    }
    memcpy(s, con_ch, SCRW * SCRH); s += SCRW * SCRH;
    memcpy(s, con_at, SCRW * SCRH);
    con_touch_all();
    sys_flush();
}

/* sys_done() calls this once the OS is back */
static void write_shot(void)
{
    char fn[48];
    FILE *f;
    if (!shot) return;
    sprintf(fn, "shot_%s.bin", shot_name);
    f = fopen(fn, "wb");
    if (f) { fwrite(shot, 1, SHOT_BYTES, f); fclose(f); }
}
