/*
 * bench68k.c -- where does real hardware differ from Musashi?
 *
 * Calls each matmul68k.s routine many times on fixed pseudo-random data
 * shaped like TINY's (288 x 96 tables, 96 x 96 dense, 48-of-256 sparse).
 * Built two ways from this one file:
 *   - an AmigaDOS executable that times every case with DateStamp
 *     (emu/bench.py runs it in cycle-exact WinUAE on KS 1.2), and
 *   - with -DBENCH_ENGINE_ONLY, raw code that scripts/bench_musashi.py runs
 *     in Musashi for the cycle count of the identical calls.
 * The ratio real/Musashi per case says whether the gap is in the
 * instruction stream (differs per routine) or the OS (same for all,
 * including the register-only "idle" loop).
 */
#ifndef BENCH_ENGINE_ONLY
#include <stdio.h>
#endif

typedef long s32;
typedef unsigned long u32;
typedef short s16;
typedef unsigned short u16;
typedef signed char s8;
typedef unsigned char u8;

void tab_dots(__reg("a0") const u8 *w, __reg("a1") const s32 *tab, __reg("d0") s32 cols,
              __reg("d1") s32 *out, __reg("d2") s32 nblk);
void build_tab(__reg("a0") const s16 *x, __reg("a1") s32 *tab, __reg("d0") s32 n);
void dots_i8_x16(__reg("a0") const s8 *w, __reg("a1") const s16 *x, __reg("d0") s32 cols,
                 __reg("d1") s32 *out, __reg("d2") s32 rows);
void dots_i8_x16_sparse(__reg("a0") const s8 *w, __reg("a1") const s16 *pairs, __reg("d0") s32 k,
                        __reg("d1") s32 *out, __reg("d2") s32 rows, __reg("d3") s32 cols);
void mulshift_rows(__reg("a0") s32 *out, __reg("a1") const u16 *mant, __reg("d0") s32 rows,
                   __reg("d1") s32 xsh, __reg("d2") const u16 *shift);

#define NCASE 6
static const char *const names[NCASE] = {
    "tab_dots 288x96", "build_tab 96", "dots_i8_x16 96x96",
    "sparse 96x(48/256)", "mulshift_rows 96", "idle C loop"
};
/* iterations per case: a few seconds each on a 7 MHz 68000 */
const long bench_iters[NCASE] = { 24, 60, 40, 60, 1500, 400 };

static s8 w8[288 * 256];
static s16 x16[256];
static s16 pairs[2 * 48];
static s32 tab[96 * 256];
static s32 out[288];
static u16 mant[96], shf[96];
static u32 seed = 12345;

static u32 rnd(void) { seed ^= seed << 13; seed ^= seed >> 17; seed ^= seed << 5; return seed; }

void bench_setup(void)
{
    s32 i, k = 0;
    for (i = 0; i < 288L * 256; i++) w8[i] = (s8)(rnd() & 0xFF);
    /* activations roughly like quantize16 output: a few large, most smaller */
    for (i = 0; i < 256; i++) {
        s32 v = (s32)(rnd() & 0x7FFF) >> (rnd() & 3);
        x16[i] = (s16)((rnd() & 1) ? -v : v);
    }
    for (i = 0; i < 256 && k < 48; i += 5) { pairs[2 * k] = (s16)i; pairs[2 * k + 1] = x16[i]; k++; }
    for (i = 0; i < 96; i++) { mant[i] = (u16)(0x8000 | (rnd() & 0x7FFF)); shf[i] = (u16)(28 + (rnd() & 7)); }
}

s32 bench_sink;

void bench_run(int c, long n)
{
    long i;
    volatile s32 v = 0;
    for (i = 0; i < n; i++) {
        switch (c) {
        case 0: tab_dots((const u8 *)w8, tab, 96, out, 36); break;
        case 1: build_tab(x16, tab, 96); break;
        case 2: dots_i8_x16(w8, x16, 96, out, 96); break;
        case 3: dots_i8_x16_sparse(w8, pairs, 48, out, 96, 256); break;
        case 4: { s32 r; for (r = 0; r < 96; r++) out[r] = (r + 1) << 12;
                  mulshift_rows(out, mant, 96, 8, shf); } break;
        default: { s32 j; for (j = 0; j < 1000; j++) v += j; } break;
        }
    }
    bench_sink = out[0] + v;
}

#ifndef BENCH_ENGINE_ONLY
struct BDateStamp { long days, minute, tick; };
extern void *DOSBase;
void b_datestamp(__reg("a6") void *base, __reg("d1") struct BDateStamp *ds) = "\tjsr\t-192(a6)";

static long ticks(void)
{
    static long buf[4];
    struct BDateStamp *ds = (struct BDateStamp *)(((unsigned long)buf + 3) & ~3UL);
    b_datestamp(DOSBase, ds);
    return (ds->days * 1440L + ds->minute) * 3000L + ds->tick;
}

/* "nodma": bitplane + sprite DMA off during the run (screen goes blank),
   to separate display contention from OS overhead. Interrupts stay on:
   DateStamp needs them. */
#define DMACON (*(volatile unsigned short *)0xDFF096)

int main(int argc, char **argv)
{
    int c, nodma = argc > 1 && argv[1][0] == 'n';
    long t0, t;
    bench_setup();
    if (nodma) DMACON = 0x0120;          /* clear BPLEN | SPREN */
    for (c = 0; c < NCASE; c++) {
        t0 = ticks();
        bench_run(c, bench_iters[c]);
        t = ticks() - t0;
        printf("%d %ld %ld %s%s\n", c + (nodma ? 10 : 0), bench_iters[c], t, names[c],
               nodma ? " (no display DMA)" : "");   /* t in 1/50 s */
    }
    if (nodma) DMACON = 0x8120;
    return 0;
}
#endif
