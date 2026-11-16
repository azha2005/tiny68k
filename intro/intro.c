/* intro/intro.c -- AMIGA 500 CD32-style startup test (OCS PAL, KS 1.2)
 *
 * 320x256 PAL, 4 bitplanes (16 colors), Copper rainbow/nebula scanline
 * sweeps, Blitter double-buffered 3D 3.5" floppy animation, starfield,
 * and 4-channel Paula synth chimes + pad.
 *
 * Controls during intro:
 *   Space / Left Mouse: trigger disc-exit wipe & restart intro
 *   Esc: exit back to CLI cleanly
 */
#include <string.h>
#include "intro_data.c"

typedef unsigned char  u8;
typedef signed char    s8;
typedef unsigned short u16;
typedef short          s16;
typedef unsigned long  u32;
typedef long           s32;

/* ---------------------------------------------------------- OS calls */
extern void *SysBase;
void *os_allocmem(__reg("a6") void *b, __reg("d0") u32 size, __reg("d1") u32 req) = "\tjsr\t-198(a6)";
void os_freemem(__reg("a6") void *b, __reg("a1") void *p, __reg("d0") u32 size) = "\tjsr\t-210(a6)";
void os_forbid(__reg("a6") void *b) = "\tjsr\t-132(a6)";
void os_permit(__reg("a6") void *b) = "\tjsr\t-138(a6)";
void *os_openlib(__reg("a6") void *b, __reg("a1") const char *name, __reg("d0") u32 v) = "\tjsr\t-552(a6)";
void os_closelib(__reg("a6") void *b, __reg("a1") void *lib) = "\tjsr\t-414(a6)";
void gfx_loadview(__reg("a6") void *b, __reg("a1") void *view) = "\tjsr\t-222(a6)";
void gfx_waittof(__reg("a6") void *b) = "\tjsr\t-270(a6)";

#define MEMF_CHIP  2UL
#define MEMF_CLEAR 0x10000UL

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
#define BLTCON0 0x040
#define BLTCON1 0x042
#define BLTAFWM 0x044
#define BLTALWM 0x046
#define BLTAPTH 0x050
#define BLTDPTH 0x054
#define BLTSIZE 0x058
#define BLTAMOD 0x064
#define BLTDMOD 0x066
#define COP1LC  0x080
#define COPJMP1 0x088
#define DMACON  0x096
#define INTENA  0x09A
#define INTREQ  0x09C
#define AUD0LC  0x0A0
#define AUD0LEN 0x0A4
#define AUD0PER 0x0A6
#define AUD0VOL 0x0A8

#define CIAAPRA  (*(volatile u8 *)0xBFE001UL)
#define CIAB_PRB (*(volatile u8 *)0xBFD100UL)
#define VEC_LEVEL2 (*(void *volatile *)0x68UL)

extern void intro_kb_int(void);
extern volatile u8 intro_last_key;

/* ---------------------------------------------------------- buffers */
#define BPR         40
#define PLANE_BYTES (BPR * INTRO_H)            /* 10240 */
#define FB_BYTES    (4L * PLANE_BYTES)         /* 40960 */
#define LOGO_BYTES  (4L * (LOGO_W / 8) * LOGO_H)
#define DISK_FBYTES (4L * (DISK_W / 8) * DISK_H)
#define COP_BYTES   4096L

static u8  *fb[2], *logo_chip, *disk_chip;
static s8  *bell_chip, *pad_chip;
static u16 *cop[2];
static u16 *cop_neb_ptr[2][20];
static u16 *cop_logo_rb[2][22];
static u16 *cop_rb_ptr[2][42];
static u16 *cop_logo_col[2][4];
static u16 *cop_bpl_ptr[2][4];

/* ---------------------------------------------------------- helpers */
static void blt_wait(void)
{
    volatile u16 dummy = REG16(DMACONR);
    (void)dummy;
    while (REG16(DMACONR) & 0x4000) ;
}

static void blt_clear(u8 *dst, int w_words, int h)
{
    blt_wait();
    REG16(BLTCON0) = 0x0100;
    REG16(BLTCON1) = 0x0000;
    REG16(BLTDMOD) = (u16)(BPR - w_words * 2);
    REG32(BLTDPTH) = (u32)dst;
    REG16(BLTSIZE) = (u16)((h << 6) | w_words);
}

static void blt_copy(const u8 *src, u8 *dst, int src_w_words, int dst_w_words, int h,
                     int shift, u16 fwm, u16 lwm)
{
    blt_wait();
    REG16(BLTCON0) = (u16)((shift << 12) | 0x09F0);
    REG16(BLTCON1) = 0x0000;
    REG16(BLTAFWM) = fwm;
    REG16(BLTALWM) = lwm;
    REG16(BLTAMOD) = (u16)(src_w_words * 2 - dst_w_words * 2);
    REG16(BLTDMOD) = (u16)(BPR - dst_w_words * 2);
    REG32(BLTAPTH) = (u32)src;
    REG32(BLTDPTH) = (u32)dst;
    REG16(BLTSIZE) = (u16)((h << 6) | dst_w_words);
}

static void wait_vbl(void)
{
    while ((REG32(VPOSR) & 0x1FF00UL) != 0x10000UL) ;
    while ((REG32(VPOSR) & 0x1FF00UL) == 0x10000UL) ;
}

static u16 scale_rgb444(u16 c, int level16)
{
    int r = ((c >> 8) & 15) * level16 >> 4;
    int g = ((c >> 4) & 15) * level16 >> 4;
    int b = (c & 15) * level16 >> 4;
    return (u16)((r << 8) | (g << 4) | b);
}

static u16 lerp_rgb444(u16 a, u16 b, int t16)
{
    int ar = (a >> 8) & 15, ag = (a >> 4) & 15, ab = a & 15;
    int br = (b >> 8) & 15, bg = (b >> 4) & 15, bb = b & 15;
    int r = ar + (((br - ar) * t16) >> 4);
    int g = ag + (((bg - ag) * t16) >> 4);
    int bl = ab + (((bb - ab) * t16) >> 4);
    return (u16)((r << 8) | (g << 4) | bl);
}

/* ---------------------------------------------------------- stars */
#define N_STARS 110
typedef struct { u16 x, y; u8 phase, speed, big; } Star;
static Star stars[N_STARS];

static void init_stars(void)
{
    u32 rng = 0x13579BDFUL;
    int i;
    for (i = 0; i < N_STARS; i++) {
        rng = rng * 1664525UL + 1013904223UL;
        stars[i].x = (u16)(4 + ((rng >> 16) % (INTRO_W - 8)));
        rng = rng * 1664525UL + 1013904223UL;
        stars[i].y = (u16)(6 + ((rng >> 16) % (INTRO_H - 12)));
        stars[i].phase = (u8)(i * 9);
        stars[i].speed = (u8)(1 + (i & 3));
        stars[i].big = (u8)((i % 7) == 0);
    }
}

static void draw_px(u8 *base, int x, int y, int col)
{
    int off = y * BPR + (x >> 3);
    u8 bit = (u8)(0x80 >> (x & 7));
    if (col & 1) base[off] |= bit;
    if (col & 2) base[PLANE_BYTES + off] |= bit;
}

static void draw_stars(u8 *base, int t, int level16)
{
    int i;
    if (level16 <= 0) return;
    for (i = 0; i < N_STARS; i++) {
        int tw = (t * stars[i].speed + stars[i].phase) & 63;
        int c = tw < 16 ? 3 : tw < 36 ? 2 : 1;
        if (level16 < 8 && c > 1) c--;
        if (level16 < 4 && (i & 1)) continue;
        draw_px(base, stars[i].x, stars[i].y, c);
        if (stars[i].big && c == 3) {
            draw_px(base, stars[i].x - 1, stars[i].y, 2);
            draw_px(base, stars[i].x + 1, stars[i].y, 2);
            draw_px(base, stars[i].x, stars[i].y - 1, 2);
            draw_px(base, stars[i].x, stars[i].y + 1, 2);
        }
    }
}

/* ---------------------------------------------------------- copper */
static void build_copper(int idx)
{
    u16 *c = cop[idx];
    u32 base = (u32)fb[idx];
    int i, y;

    *c++ = 0x0100; *c++ = 0x4200;               /* BPLCON0: 4 planes, lowres */
    *c++ = 0x0102; *c++ = 0x0000;
    *c++ = 0x0104; *c++ = 0x0000;
    *c++ = 0x0108; *c++ = 0x0000;
    *c++ = 0x010A; *c++ = 0x0000;
    *c++ = 0x008E; *c++ = 0x2C81;               /* DIWSTRT PAL 320x256 */
    *c++ = 0x0090; *c++ = 0x2CC1;               /* DIWSTOP */
    *c++ = 0x0092; *c++ = 0x0038;               /* DDFSTRT */
    *c++ = 0x0094; *c++ = 0x00D0;               /* DDFSTOP */

    for (i = 0; i < 4; i++) {
        u32 a = base + (u32)i * PLANE_BYTES;
        *c++ = (u16)(0x00E0 + 4 * i); cop_bpl_ptr[idx][i] = c; *c++ = (u16)(a >> 16);
        *c++ = (u16)(0x00E2 + 4 * i); *c++ = (u16)a;
    }
    for (i = 0; i < 12; i++) {
        *c++ = (u16)(0x0180 + 2 * i); *c++ = intro_pal[i];
    }
    for (i = 0; i < 4; i++) {
        *c++ = (u16)(0x0180 + 2 * (12 + i));
        cop_logo_col[idx][i] = c;
        *c++ = 0x0000;
    }

    /* Logo '500' metallic rainbow sheen band: y = 44..88 (2 scanlines/step) */
    for (i = 0; i < 22; i++) {
        y = 44 + i * 2;
        *c++ = (u16)(((0x2C + y) << 8) | 0x07); *c++ = 0xFFFE;
        *c++ = 0x019E; cop_logo_rb[idx][i] = c; *c++ = 0x0000;   /* COLOR15 */
    }

    /* Diskette metallic rainbow band: y = 114..198 (2 scanlines/step) */
    for (i = 0; i < 42; i++) {
        y = 114 + i * 2;
        *c++ = (u16)(((0x2C + y) << 8) | 0x07); *c++ = 0xFFFE;
        *c++ = 0x0190; cop_rb_ptr[idx][i] = c; *c++ = intro_pal[8];   /* COLOR08 */
        *c++ = 0x0192; *c++ = intro_pal[9];                            /* COLOR09 */
        *c++ = 0x0194; *c++ = intro_pal[10];                           /* COLOR10 */
        *c++ = 0x0196; *c++ = intro_pal[11];                           /* COLOR11 */
    }

    /* Bottom celestial nebula waves (like CD32 startup): y = 198..254 */
    for (i = 0; i < 20; i++) {
        int yhw = 0x2C + 198 + i * 3;
        if (yhw >= 256 && (0x2C + 198 + (i - 1) * 3) < 256) {
            *c++ = 0xFFDF; *c++ = 0xFFFE;       /* cross PAL line 255 */
        }
        *c++ = (u16)(((yhw & 0xFF) << 8) | 0x07); *c++ = 0xFFFE;
        *c++ = 0x0180; cop_neb_ptr[idx][i] = c; *c++ = 0x0000;
    }
    *c++ = 0xFFFF; *c++ = 0xFFFE;
}

static void update_copper(int idx, int t, int neb_lvl, int logo_lvl, int logo_hue)
{
    int i;
    for (i = 0; i < 4; i++) {
        u16 base_c = intro_pal[12 + i];
        u16 tint = copper_rainbow[(t * 2 + i * 8) & 63];
        u16 c = logo_hue > 0 ? lerp_rgb444(base_c, tint, logo_hue) : base_c;
        *cop_logo_col[idx][i] = scale_rgb444(c, logo_lvl);
    }
    /* Diagonal rainbow sheen on '500' (COLOR15), silver-tinted */
    for (i = 0; i < 22; i++) {
        u16 rb = copper_rainbow[(i * 2 + t) & 63];
        u16 c = lerp_rgb444(0xEEF, rb, 7);
        if (logo_hue > 0) c = lerp_rgb444(c, rb, logo_hue);
        *cop_logo_rb[idx][i] = scale_rgb444(c, logo_lvl);
    }
    /* Bottom celestial glow waves (shifts slowly from warm amber/rose to teal/violet) */
    for (i = 0; i < 20; i++) {
        int wave = (i * 3 - (t >> 1) + 1024) & 63;
        u16 nc = copper_nebula[wave];
        u16 warm = copper_rainbow[(wave + 10) & 63];
        u16 mix = lerp_rgb444(nc, scale_rgb444(warm, 5), ((t >> 2) & 31) < 16 ? ((t >> 2) & 15) : 15 - ((t >> 2) & 15));
        *cop_neb_ptr[idx][i] = neb_lvl > 0 ? scale_rgb444(mix, (neb_lvl * i) / 14) : 0x0000;
    }
    for (i = 0; i < 42; i++) {
        u16 *p = cop_rb_ptr[idx][i];
        int base_idx = (i + t) & 63;
        u16 c0 = copper_rainbow[base_idx];
        u16 c1 = copper_rainbow[(base_idx + 5) & 63];
        u16 c2 = copper_rainbow[(base_idx + 11) & 63];
        u16 c3 = lerp_rgb444(c2, 0xFFF, 8);
        p[0] = scale_rgb444(c0, 6);
        p[2] = scale_rgb444(c1, 10);
        p[4] = lerp_rgb444(c2, 0xEEF, 6);
        p[6] = c3;
    }
}

/* ---------------------------------------------------------- drawing */
static void draw_logo(u8 *dst_fb)
{
    int p;
    int lx = 16, ly = 34;
    for (p = 0; p < 4; p++) {
        const u8 *s = logo_chip + (u32)p * (LOGO_W / 8) * LOGO_H;
        u8 *d = dst_fb + (u32)p * PLANE_BYTES + ly * BPR + (lx >> 3);
        blt_copy(s, d, LOGO_W / 16, LOGO_W / 16, LOGO_H, 0, 0xFFFF, 0xFFFF);
    }
}

static void draw_disk(u8 *dst_fb, int frame, int x, int y)
{
    int p, wx, sh, src_skip, dst_w, vis_end;
    const u8 *fbase = disk_chip + (u32)frame * DISK_FBYTES;
    if (x >= INTRO_W || x + 192 <= 0) return;

    wx = x >> 4;
    sh = x & 15;
    src_skip = 0;
    if (wx < 0) {
        src_skip = -wx;
        wx = 0;
    }
    vis_end = wx + (DISK_W / 16) - src_skip;
    if (vis_end > 20) vis_end = 20;
    dst_w = vis_end - wx;
    if (dst_w <= 0) return;

    for (p = 0; p < 4; p++) {
        const u8 *s = fbase + (u32)p * (DISK_W / 8) * DISK_H + src_skip * 2;
        u8 *d = dst_fb + (u32)p * PLANE_BYTES + y * BPR + wx * 2;
        /* Always mask last word to 0 when shifting so barrel-shifter never leaks row+1 */
        u16 lwm = sh ? 0x0000 : 0xFFFF;
        blt_copy(s, d, DISK_W / 16, dst_w, DISK_H, sh, 0xFFFF, lwm);
    }
}

static void clear_backbuf(u8 *dst_fb, int clear_all)
{
    int p;
    for (p = 0; p < 4; p++) {
        if (clear_all)
            blt_clear(dst_fb + (u32)p * PLANE_BYTES, 20, INTRO_H);
        else {
            /* Clear stars above/below logo, keeping logo intact, or just clear all fast (~1.8 ms) */
            blt_clear(dst_fb + (u32)p * PLANE_BYTES, 20, INTRO_H);
        }
    }
}

/* ---------------------------------------------------------- Paula audio */
static void set_voice(int ch, const s8 *data, u16 len_words, u16 per, u16 vol)
{
    u16 o = (u16)(ch * 16);
    REG32(AUD0LC + o) = (u32)data;
    REG16(AUD0LEN + o) = len_words;
    REG16(AUD0PER + o) = per;
    REG16(AUD0VOL + o) = vol;
}

static void audio_stop(void)
{
    REG16(INTENA) = 0x0780;
    REG16(DMACON) = 0x000F;
    REG16(AUD0VOL) = 0; REG16(AUD0VOL + 16) = 0;
    REG16(AUD0VOL + 32) = 0; REG16(AUD0VOL + 48) = 0;
}

static void audio_tick(int t)
{
    /* Chimes during star phase: E5 (1318Hz, per=42*64? wait: 3546895 / (64*freq))
       bell=64 samples: period = 3546895 / (64 * Hz) = 55420 / Hz */
    if (t == 8) {
        set_voice(0, bell_chip, 32, 106, 48);      /* ~523 Hz C5 */
        REG16(DMACON) = 0x8201;
    } else if (t == 24) {
        set_voice(1, bell_chip, 32, 84, 44);       /* ~659 Hz E5 */
        REG16(DMACON) = 0x8202;
    } else if (t == 40) {
        set_voice(0, bell_chip, 32, 70, 48);       /* ~784 Hz G5 */
        REG16(DMACON) = 0x8201;
    } else if (t > 8 && t < 68) {
        int v0 = 48 - ((t % 32) * 2);
        int v1 = 44 - (((t + 16) % 32) * 2);
        REG16(AUD0VOL) = (u16)(v0 > 0 ? v0 : 0);
        REG16(AUD0VOL + 16) = (u16)(v1 > 0 ? v1 : 0);
    }

    /* Lush 4-channel synth pad swell when disk + logo enter (t == 70):
       pad=128 samples: period = 3546895 / (128 * Hz) = 27710 / Hz */
    if (t == 70) {
        set_voice(0, pad_chip, 64, 212, 0);        /* C3 ~130.8 Hz */
        set_voice(1, pad_chip, 64, 141, 0);        /* G3 ~196.0 Hz */
        set_voice(2, pad_chip, 64, 106, 0);        /* E4 ~261.6*1.26? wait 27710/330 = 84; 106=C4 */
        set_voice(3, pad_chip, 64, 94, 0);         /* D4 ~293.7 Hz */
        REG16(DMACON) = 0x820F;
    } else if (t > 70 && t <= 134) {
        u16 v = (u16)((t - 70) * 44 / 64);
        REG16(AUD0VOL) = v;
        REG16(AUD0VOL + 16) = (u16)(v * 3 / 4);
        REG16(AUD0VOL + 32) = (u16)(v * 3 / 4);
        REG16(AUD0VOL + 48) = (u16)(v / 2);
    } else if (t == 190) {
        /* Shift upper voices to warm Fmaj9 -> Cmaj9 feel */
        REG16(AUD0PER) = 158;                      /* F3 */
        REG16(AUD0PER + 16) = 105;                 /* C4 */
        REG16(AUD0PER + 32) = 84;                  /* E4 */
        REG16(AUD0PER + 48) = 70;                  /* G4 */
    } else if (t == 280) {
        REG16(AUD0PER) = 212;                      /* C3 */
        REG16(AUD0PER + 16) = 141;                 /* G3 */
        REG16(AUD0PER + 32) = 84;                  /* E4 */
        REG16(AUD0PER + 48) = 94;                  /* D4 */
    } else if (t > 320 && t <= 420) {
        u16 v = (u16)((420 - t) * 44 / 100);
        REG16(AUD0VOL) = v;
        REG16(AUD0VOL + 16) = (u16)(v * 3 / 4);
        REG16(AUD0VOL + 32) = (u16)(v * 3 / 4);
        REG16(AUD0VOL + 48) = (u16)(v / 2);
    } else if (t == 421) {
        audio_stop();
    }
}

/* ---------------------------------------------------------- main */
int main(void)
{
    void *gfx;
    void *old_view, *old_lof, *old_shf, *old_vec2;
    u16 old_dmacon, old_intena;
    int t = 0, exit_t = -1, buf = 0;

    fb[0] = (u8 *)os_allocmem(SysBase, FB_BYTES, MEMF_CHIP | MEMF_CLEAR);
    fb[1] = (u8 *)os_allocmem(SysBase, FB_BYTES, MEMF_CHIP | MEMF_CLEAR);
    cop[0] = (u16 *)os_allocmem(SysBase, COP_BYTES, MEMF_CHIP | MEMF_CLEAR);
    cop[1] = (u16 *)os_allocmem(SysBase, COP_BYTES, MEMF_CHIP | MEMF_CLEAR);
    logo_chip = (u8 *)os_allocmem(SysBase, LOGO_BYTES, MEMF_CHIP);
    disk_chip = (u8 *)os_allocmem(SysBase, (u32)DISK_FRAMES * DISK_FBYTES, MEMF_CHIP);
    bell_chip = (s8 *)os_allocmem(SysBase, sizeof wav_bell, MEMF_CHIP);
    pad_chip  = (s8 *)os_allocmem(SysBase, sizeof wav_pad, MEMF_CHIP);
    if (!fb[0] || !fb[1] || !cop[0] || !cop[1] ||
        !logo_chip || !disk_chip || !bell_chip || !pad_chip) {
        return 1;
    }

    memcpy(logo_chip, logo_planar, LOGO_BYTES);
    memcpy(disk_chip, disk_planar, (u32)DISK_FRAMES * DISK_FBYTES);
    memcpy(bell_chip, wav_bell, sizeof wav_bell);
    memcpy(pad_chip,  wav_pad,  sizeof wav_pad);

    init_stars();
    build_copper(0);
    build_copper(1);

    gfx = os_openlib(SysBase, "graphics.library", 0);
    if (!gfx) return 1;
    old_view = *(void **)((u8 *)gfx + GB_ACTIVIEW);
    old_lof  = *(void **)((u8 *)gfx + GB_LOFLIST);
    old_shf  = *(void **)((u8 *)gfx + GB_SHFLIST);
    gfx_loadview(gfx, 0);
    gfx_waittof(gfx);
    gfx_waittof(gfx);

    os_forbid(SysBase);
    old_dmacon = REG16(DMACONR);
    old_intena = REG16(INTENAR);
    REG16(INTENA) = 0x7FFF;
    REG16(INTREQ) = 0x7FFF;
    REG16(DMACON) = 0x7FFF;

    /* Floppy motor off */
    CIAB_PRB |= 0xF8; CIAB_PRB &= 0xF7; CIAB_PRB |= 0x08;

    old_vec2 = VEC_LEVEL2;
    intro_last_key = 0;
    VEC_LEVEL2 = (void *)intro_kb_int;
    REG16(INTREQ) = 0x0008;
    REG16(INTENA) = 0xC008;                 /* PORTS keyboard only */

    REG32(COP1LC) = (u32)cop[0];
    REG16(COPJMP1) = 0;
    REG16(DMACON) = 0x83C0;                 /* MASTER | BPLEN | COPEN | BLTEN */

    for (;;) {
        int disk_x, disk_y, disk_fr;
        int star_lvl, neb_lvl, logo_lvl, logo_hue;
        u8 k = intro_last_key;
        intro_last_key = 0;
        if (k == 0x45) break;               /* Esc -> exit */
        if ((k == 0x40 || !(CIAAPRA & 0x40)) && exit_t < 0 && t > 60) {
            exit_t = 0;                     /* Space or LMB -> disc-wipe transition */
        }

        /* Choreography (50 Hz) */
        star_lvl = t < 32 ? t / 2 : 16;

        {
            static const u8 pingpong[14] = { 0, 1, 2, 3, 4, 5, 6, 7, 6, 5, 4, 3, 2, 1 };
            if (t < 55) {
                disk_x = 340;
                disk_y = 124;
                disk_fr = 3;
            } else if (t < 135) {
                /* Ease-out fly-in from right (x: 320 -> 64) */
                int p = t - 55;                 /* 0..80 */
                int inv = 80 - p;
                disk_x = 64 + (int)((s32)inv * inv * 256L / 6400L);
                disk_y = 124 - (inv >> 4);
                disk_fr = pingpong[(p >> 2) % 14];
            } else {
                /* Gentle floating hover */
                static const s8 bob[16] = { 0, 1, 2, 3, 4, 3, 2, 1, 0, -1, -2, -3, -4, -3, -2, -1 };
                disk_x = 64;
                disk_y = 124 + bob[(t >> 2) & 15];
                disk_fr = pingpong[(t >> 2) % 14];
            }
        }

        if (exit_t >= 0) {
            /* Wipe-out: spin and fly left */
            disk_x -= exit_t * 6;
            disk_fr = exit_t % DISK_FRAMES;
            neb_lvl = 0;
            logo_lvl = exit_t < 24 ? (24 - exit_t) * 16 / 24 : 0;
            logo_hue = 0;
            if (exit_t > 65) {
                /* Restart loop smoothly */
                t = 0; exit_t = -1;
                audio_stop();
            } else {
                exit_t++;
            }
        } else {
            if (t < 45) {
                neb_lvl = 0; logo_lvl = 0; logo_hue = 0;
            } else if (t < 115) {
                int p = t - 45;             /* 0..70 */
                neb_lvl = p * 16 / 70;
                logo_lvl = p * 16 / 70;
                logo_hue = 16 - (p * 16 / 70);
            } else {
                neb_lvl = 16;
                logo_lvl = 16;
                logo_hue = 0;
            }
        }

        audio_tick(t);

        /* Render into backbuffer fb[buf] */
        clear_backbuf(fb[buf], 1);
        draw_stars(fb[buf], t, star_lvl);
        if (logo_lvl > 0) draw_logo(fb[buf]);
        draw_disk(fb[buf], disk_fr, disk_x, disk_y);
        blt_wait();

        update_copper(buf, t, neb_lvl, logo_lvl, logo_hue);

        wait_vbl();
        REG32(COP1LC) = (u32)cop[buf];
        buf ^= 1;
        t++;
    }

    audio_stop();
    REG16(INTENA) = 0x7FFF;
    VEC_LEVEL2 = old_vec2;
    REG16(DMACON) = 0x7FFF;
    REG16(DMACON) = (u16)(0x8000 | old_dmacon);
    REG16(INTREQ) = 0x7FFF;
    REG16(INTENA) = (u16)(0x8000 | old_intena);

    *(void **)((u8 *)gfx + GB_LOFLIST) = old_lof;
    *(void **)((u8 *)gfx + GB_SHFLIST) = old_shf;
    REG32(COP1LC) = *(u32 *)((u8 *)gfx + GB_COPINIT);
    REG16(COPJMP1) = 0;
    gfx_loadview(gfx, old_view);
    gfx_waittof(gfx);
    os_permit(SysBase);
    os_closelib(SysBase, gfx);

    os_freemem(SysBase, pad_chip, sizeof wav_pad);
    os_freemem(SysBase, bell_chip, sizeof wav_bell);
    os_freemem(SysBase, disk_chip, (u32)DISK_FRAMES * DISK_FBYTES);
    os_freemem(SysBase, logo_chip, LOGO_BYTES);
    os_freemem(SysBase, cop[1], COP_BYTES);
    os_freemem(SysBase, cop[0], COP_BYTES);
    os_freemem(SysBase, fb[1], FB_BYTES);
    os_freemem(SysBase, fb[0], FB_BYTES);
    return 0;
}
