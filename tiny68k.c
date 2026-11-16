/* tiny68k.c -- TINY 2.1 inference for the Amiga 500 (68000, no FPU)
 *
 * An implementation of oracle_tiny.py, which is the specification: the two
 * must produce identical int32 scores (python scripts/verify.py).
 *
 * Everything is Q16.16 fixed point. No floating point, no 64-bit types, no
 * libm. Products are built from 16x16 multiplies so they map onto MULU/MULS.
 *
 * Host test build (little-endian, C kernel):
 *   gcc -O2 -Wall -DA5_HOST -o tiny68k tiny68k.c
 *
 *   tiny68k tiny.t68                      interactive, plain text
 *   tiny68k tiny.t68 -c 5 -s 42           creativity 5, seed 42
 *   tiny68k tiny.t68 -ask < questions     one answer per input line
 *   tiny68k tiny.t68 -dump "QUESTION" "ANSWER"
 *        teacher-forced scores for verify.py: per position, VOCAB scores
 *        then L layer activities, one integer per line
 *
 * -DTINY_ENGINE_ONLY drops everything that needs libc (files, stdio,
 * malloc, main) and leaves the engine and its tiny_* entry points. That is
 * what scripts/m68k_gate.py links raw at a fixed address and runs in a 68000
 * emulator against the oracle.
 */

#if !defined(TINY_ENGINE_ONLY) || defined(A5_HOST)
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#endif
#if !defined(TINY_ENGINE_ONLY) && defined(A5_HOST)
#include <time.h>
#endif
#if !defined(TINY_ENGINE_ONLY) && !defined(A5_HOST)
/* dos.library DateStamp (LVO -192), declared here because the NDK headers
   are not installed. vbcc's kick13 clock() reads 0, so this is the clock.
   DOSBase is opened by vbcc's startup code. */
struct TinyDateStamp { long days, minute, tick; };
extern void *DOSBase;
void tiny_datestamp(__reg("a6") void *base, __reg("d1") struct TinyDateStamp *ds) =
    "\tjsr\t-192(a6)";
#endif

typedef signed char    s8;
typedef unsigned char  u8;
typedef short          s16;
typedef unsigned short u16;

/* s32 must be exactly 32 bits: `long` on the Amiga, `int` on a 64-bit host. */
#ifdef A5_HOST
typedef int            s32;
typedef unsigned int   u32;
#else
typedef long           s32;
typedef unsigned long  u32;
#endif

#define ONE        65536L
#define EXP_BITS   6
#define EXP_MAX    16
#define EXP_N      ((EXP_MAX << EXP_BITS) + 1)

#define VOCAB      97          /* 0 pad, 1 newline, 2.. letters ' '..'~' */
#define CTX        128
#define MAXQ       72
#define MAXD       128
#define MAXHID     512
#define MAXL       8
#define CUTOFF     9830L       /* 0.15 in Q16.16 */

#define NIL ((void *)0)

#ifdef A5_HOST
static u16 be16(const void *p) { const u8 *b = (const u8 *)p; return (u16)((b[0] << 8) | b[1]); }
static s32 be32(const void *p)
{
    const u8 *b = (const u8 *)p;
    return (s32)(((u32)b[0] << 24) | ((u32)b[1] << 16) | ((u32)b[2] << 8) | b[3]);
}
static void fixup32(s32 *a, long n) { long i; for (i = 0; i < n; i++) a[i] = be32(&a[i]); }
static void fixup16(u16 *a, long n) { long i; for (i = 0; i < n; i++) a[i] = be16(&a[i]); }
#else
#define be16(p)      (*(const u16 *)(p))
#define fixup32(a, n)                /* big-endian already: nothing to do */
#define fixup16(a, n)
#endif

/* ---------------------------------------------------------- fixed point */

#ifdef USE_ASM
/* matmul68k.s: register calls (vbcc __reg), d2-d7/a2-a6 preserved. */
s32 fxmul(__reg("d0") s32 a, __reg("d1") s32 b);
s32 mulshift(__reg("d0") s32 acc, __reg("d1") u16 m, __reg("d2") s32 sh);
#else
/* Q16.16 * Q16.16 -> Q16.16, four 16x16 multiplies. */
static s32 fxmul(s32 a, s32 b)
{
    int neg = 0;
    u32 ua, ub, ah, al, bh, bl, r;
    if (a < 0) { a = -a; neg ^= 1; }
    if (b < 0) { b = -b; neg ^= 1; }
    ua = (u32)a; ub = (u32)b;
    ah = ua >> 16; al = ua & 0xFFFFUL;
    bh = ub >> 16; bl = ub & 0xFFFFUL;
    r = ((ah * bh) << 16) + ah * bl + al * bh + ((al * bl) >> 16);
    return neg ? -(s32)r : (s32)r;
}

/* (acc * m) >> sh with an exact 48-bit intermediate, two MULUs.
 * sh in [-15, 47]; negative shifts left. amiga260k's version stopped at 31;
 * TINY's attention scales need up to 47 (fx.MULSHIFT_MAX). */
static s32 mulshift(s32 acc, u16 m, int sh)
{
    int neg = 0;
    u32 ua, lo, hi, rl, rh;
#ifdef A5_HOST
    if (sh < -15 || sh > 47) { fprintf(stderr, "mulshift shift %d\n", sh); exit(3); }
#endif
    if (acc < 0) { acc = -acc; neg = 1; }
    ua = (u32)acc;
    lo = (ua & 0xFFFFUL) * m;
    hi = (ua >> 16) * m;
    rl = lo + (hi << 16);
    rh = (hi >> 16) + ((rl < lo) ? 1UL : 0UL);
    if (sh >= 32)
        rl = rh >> (sh - 32);
    else if (sh > 0)
        rl = (rl >> sh) | (rh << (32 - sh));
    else if (sh < 0)
        rl = rl << (-sh);
    return neg ? -(s32)rl : (s32)rl;
}
#endif /* USE_ASM */

static u16 isqrt32(u32 v)
{
    u32 rem = 0, root = 0;
    int i;
    for (i = 0; i < 16; i++) {
        root <<= 1;
        rem = (rem << 2) | ((v >> 30) & 3);
        v <<= 2;
        if (root < rem) { root++; rem -= root; root++; }
    }
    return (u16)(root >> 1);
}

#ifdef USE_ASM
s32 fxdiv(__reg("d0") s32 a, __reg("d1") s32 b);              /* matmul68k.s */
#else
/* (a << 16) / b in Q16.16, restoring long division, no 64-bit type. */
static s32 fxdiv(s32 a, s32 b)
{
    int neg = 0, i;
    u32 nh, nl, q = 0, rem = 0, bit;
    if (b == 0) return 0x7FFFFFFFL;
    if (a < 0) { a = -a; neg ^= 1; }
    if (b < 0) { b = -b; neg ^= 1; }
    nh = (u32)a >> 16;
    nl = (u32)a << 16;
    for (i = 0; i < 64; i++) {
        if (i < 32) { bit = (nh >> 31) & 1; nh <<= 1; }
        else        { bit = (nl >> (63 - i)) & 1; }
        rem = (rem << 1) | bit;
        q <<= 1;
        if (rem >= (u32)b) { rem -= (u32)b; q |= 1; }
    }
    return neg ? -(s32)q : (s32)q;
}
#endif

static s32 fxrsqrt(s32 ss)
{
    s32 r;
    if (ss <= 0) return 0;
    r = (s32)isqrt32((u32)ss) << 8;
    if (r == 0) return 0x7FFFFFFFL;
    r = (r + fxdiv(ss, r)) >> 1;
    if (r == 0) return 0x7FFFFFFFL;
    return fxdiv(ONE, r);
}

static s32 fxexp_neg(const s32 *tab, s32 u)
{
    s32 a, b, frac;
    long idx;
    if (u <= 0) return ONE;
    if (u >= ((s32)EXP_MAX << 16)) return 0;
    idx  = u >> (16 - EXP_BITS);
    frac = (u << EXP_BITS) & 0xFFFFL;
    a = tab[idx]; b = tab[idx + 1];
    return a + (((b - a) * frac) >> 16);
}

#ifdef USE_ASM
int quantize16(__reg("a0") const s32 *x, __reg("d0") int n, __reg("a1") s16 *out);
#else
/* s32 vector -> int16 with a power-of-two shift, truncating toward zero. */
static int quantize16(const s32 *x, int n, s16 *out)
{
    u32 amax = 0, a;
    int i, sh = 0;
    for (i = 0; i < n; i++) { a = (u32)(x[i] < 0 ? -x[i] : x[i]); if (a > amax) amax = a; }
    while ((amax >> sh) > 32767UL) sh++;
    for (i = 0; i < n; i++)
        out[i] = (s16)(x[i] >= 0 ? (x[i] >> sh) : -((-x[i]) >> sh));
    return sh;
}
#endif

/* big * (cm / 2^cs) as a (mantissa, shift) pair for mulshift. fx.scale_of. */
static void scale_of(s32 big, u16 cm, u16 cs, u16 *m, s16 *sh)
{
    u32 v = (u32)big, p;
    int be = 0, t;
    while (v > 0xFFFFUL) { v >>= 1; be++; }
    while (v < 0x8000UL) { v <<= 1; be--; }
    p = (u32)(u16)v * (u32)cm;                       /* one MULU */
    if (p >= 0x80000000UL) { *m = (u16)(p >> 16); t = 16; }
    else                   { *m = (u16)(p >> 15); t = 15; }
    *sh = (s16)((int)cs - be - t);
}

#ifdef USE_ASM
void kv_quant(__reg("a0") const s32 *v, __reg("d0") s32 n, __reg("a1") s8 *q, __reg("d1") s32 f);
void rms_apply(__reg("a0") s32 *out, __reg("a1") const s32 *x, __reg("d0") s32 n,
               __reg("d1") s32 inv, __reg("d2") const s32 *g);
#else
/* The loops of store_kv() and rmsnorm() (one asm call each on the Amiga). */
static void kv_quant(const s32 *v, s32 n, s8 *q, s32 f)
{
    s32 i, a, t, r;
    for (i = 0; i < n; i++) {
        a = v[i] < 0 ? -v[i] : v[i];
        t = fxmul(a, f);
        r = (t + 0x8000L) >> 16;
        if (r > 127) r = 127;
        q[i] = (s8)(v[i] < 0 ? -r : r);
    }
}

static void rms_apply(s32 *out, const s32 *x, s32 n, s32 inv, const s32 *g)
{
    s32 j;
    for (j = 0; j < n; j++) out[j] = fxmul(g[j], fxmul(x[j], inv));
}
#endif

/* y = x / sqrt(mean(x^2)) * g. fx.rmsnorm_tiny: squares of x >> k so that
 * |x| up to 32767.0 never overflows, no eps. */
static void rmsnorm(s32 *out, const s32 *x, const s32 *g, int n)
{
    u32 amax = 0, a, ss = 0, mean;
    int j, k = 0, e = 0, s;
    s32 inv;
    for (j = 0; j < n; j++) { a = (u32)(x[j] < 0 ? -x[j] : x[j]); if (a > amax) amax = a; }
    if (amax == 0) { for (j = 0; j < n; j++) out[j] = 0; return; }
    while ((amax >> k) >= 4096UL) k++;
    for (j = 0; j < n; j++) {
        a = (u32)(x[j] < 0 ? -x[j] : x[j]) >> k;
        ss += (u32)(u16)a * (u16)a;                   /* MULU, < 2^24 */
    }
    mean = ss / (u32)n;
    if (mean == 0) mean = 1;
    while (mean >= (1UL << 18)) { mean >>= 2; e++; }
    while (mean < (1UL << 16))  { mean <<= 2; e--; }
    inv = fxrsqrt((s32)mean);
    s = 8 - k - e;
    inv = s >= 0 ? (s32)((u32)inv << s) : inv >> (-s);
    rms_apply(out, x, n, inv, g);
}

/* TINY.C store(): int8, one absmax scale per vector. fx.store_kv. */
static s32 store_kv(const s32 *v, int n, s8 *q)
{
    s32 big = 0, a, f;
    int i;
    for (i = 0; i < n; i++) { a = v[i] < 0 ? -v[i] : v[i]; if (a > big) big = a; }
    if (big < 256) big = 256;
    f = fxdiv(127L << 16, big);
    kv_quant(v, n, q, f);
    return big;
}

/* -------------------------------------------------------------- model */

typedef struct { u16 rows, cols; u16 *mant, *shift; s8 *w; u8 *spill; } QT;

#define T_WQ 0
#define T_WK 1
#define T_WV 2
#define T_WO 3
#define T_F1 4
#define T_F2 5
#define NT   6

typedef struct {
    int D, L, H, HID, HD;
    u16 kc_m, kc_s, vc_m, vc_s;
    s32 *exp_tab, *g1, *g2, *gf;
    QT emb, pos;
    QT t[MAXL][NT];
    int tab;                  /* wq wk wv wo f1 stored as bit planes, matmul_bp() */
    u8 *blob;
} Model;

static u8 *cursor;

static s32 *take_i32(long n)
{
    s32 *p = (s32 *)cursor;
    cursor += 4 * n;
    fixup32(p, n);
    return p;
}

static void take_qt(QT *t)
{
    long n;
    t->rows = be16(cursor);
    t->cols = be16(cursor + 2);
    cursor += 4;
    t->mant = (u16 *)cursor;  fixup16(t->mant, t->rows);  cursor += 2L * t->rows;
    t->shift = (u16 *)cursor; fixup16(t->shift, t->rows); cursor += 2L * t->rows;
    n = (long)t->rows * t->cols;
    t->w = (s8 *)cursor;
    cursor += n + (n & 1);
}

/* The model lives where the caller loaded it; nothing is copied. */
static const char *parse_model(Model *m, u8 *blob, long size)
{
    long l, k;
    m->blob = blob;
    if (size < 26 || blob[0] != 'T' || blob[1] != '6' || blob[2] != '8' ||
        (blob[3] != 'A' && blob[3] != 'C') || be16(blob + 4) != 1)
        return "not a version 1 .t68 file";
    m->D   = be16(m->blob + 6);
    m->L   = be16(m->blob + 8);
    m->H   = be16(m->blob + 10);
    m->HID = be16(m->blob + 12);
    if (be16(m->blob + 14) != VOCAB || be16(m->blob + 16) != CTX)
        return "model has the wrong vocabulary or context size";
    if (m->D > MAXD || m->L > MAXL || m->HID > MAXHID || m->H < 1 || m->D % m->H)
        return "model size not supported";
    m->HD = m->D / m->H;
    m->kc_m = be16(m->blob + 18); m->kc_s = be16(m->blob + 20);
    m->vc_m = be16(m->blob + 22); m->vc_s = be16(m->blob + 24);
    cursor = m->blob + 26;
    m->exp_tab = take_i32(EXP_N);
    m->g1 = take_i32((long)m->L * m->D);
    m->g2 = take_i32((long)m->L * m->D);
    m->gf = take_i32(m->D);
    take_qt(&m->emb);
    take_qt(&m->pos);
    for (l = 0; l < m->L; l++)
        for (k = 0; k < NT; k++)
            take_qt(&m->t[l][k]);
    if (cursor != m->blob + size) return "model file has the wrong size";
    return NIL;
}

/* ------------------------------------------------------------ kernels */

#ifdef USE_ASM
s32 dot_i8_x16(__reg("a0") const s8 *w, __reg("a1") const s16 *x, __reg("d0") s32 n);
s32 dot_x16_i8s(__reg("a0") const s16 *w, __reg("a1") const s8 *v, __reg("d0") s32 n,
                __reg("d1") s32 stride);
s32 dot_i8_x16_sparse(__reg("a0") const s8 *w, __reg("a1") const s16 *pairs, __reg("d0") s32 k);
s32 build_bptab(__reg("a0") const s16 *x, __reg("a1") s32 *tab, __reg("d0") s32 n);
void dots_i8_x16(__reg("a0") const s8 *w, __reg("a1") const s16 *x, __reg("d0") s32 cols,
                 __reg("d1") s32 *out, __reg("d2") s32 rows);
void dots_i8_x16_sparse(__reg("a0") const s8 *w, __reg("a1") const s16 *pairs, __reg("d0") s32 k,
                        __reg("d1") s32 *out, __reg("d2") s32 rows, __reg("d3") s32 cols);
void mulshift_rows(__reg("a0") s32 *out, __reg("a1") const u16 *mant, __reg("d0") s32 rows,
                   __reg("d1") s32 xsh, __reg("d2") const u16 *shift);
void bp_dots(__reg("a0") const u8 *w, __reg("a1") const s32 *tab, __reg("d0") s32 groups,
             __reg("d1") s32 *out, __reg("d2") s32 nblk, __reg("d3") s32 bias,
             __reg("d4") const u8 *spill);
#else
static s32 dot_i8_x16(const s8 *w, const s16 *x, s32 n)
{
    s32 acc = 0;
    s32 i;
    for (i = 0; i < n; i++) acc += (s32)w[i] * (s32)x[i];
    return acc;
}

/* sum_j w[j] * v[j * stride]: the attention value pass, int16 x int8. */
static s32 dot_x16_i8s(const s16 *w, const s8 *v, s32 n, s32 stride)
{
    s32 acc = 0;
    s32 j;
    for (j = 0; j < n; j++, v += stride) acc += (s32)w[j] * (s32)*v;
    return acc;
}

/* sum over k (column, value) pairs of w[column] * value. */
static s32 dot_i8_x16_sparse(const s8 *w, const s16 *pairs, s32 k)
{
    s32 acc = 0;
    for (; k > 0; k--, pairs += 2) acc += (s32)w[pairs[0]] * (s32)pairs[1];
    return acc;
}

/* T-MAC style group tables (Wei et al., arXiv 2407.00088). For each group
   of 6 inputs x[6g..6g+5] and every 6-bit pattern b: tab[64 g + b] = sum of
   the x[6g+i] whose bit i is set in b. Built in "ruler" order, one addition
   per entry: T[b] = T[b-1] + D[ctz(b)], D[t] = x[t] - (x[0] + .. + x[t-1]).
   Returns the sum of all n inputs (for the bias of bp_dots). n % 6 == 0.
   Groups of 6, not 8: the weight byte then holds 4 * b, a ready byte offset
   into the table, so the 68000 needs no MOVEQ and no x4 per look-up. */
static s32 build_bptab(const s16 *x, s32 *tab, s32 n)
{
    s32 g, b, t, sum = 0, d[6], p;
    for (g = 0; g < n; g += 6, x += 6, tab += 64) {
        for (p = 0, t = 0; t < 6; t++) { d[t] = x[t] - p; p += x[t]; }
        sum += p;
        tab[0] = 0;
        for (b = 1; b < 64; b++) {
            for (t = 0; !(b & (1 << t)); t++) ;
            tab[b] = tab[b - 1] + d[t];
        }
    }
    return sum;
}

/* Raw dots of nblk blocks of 8 rows from bit-plane weights (bitplane_blocks):
   with u = w + 128 (the byte w ^ 0x80), dot = sum_k 2^k S_k - 128 sum(x),
   S_k = sum over groups of tab[64 g + (pattern of bit k of u, 6 columns)].
   Horner over the planes, 7 first; bias = 128 sum(x). Exact mod 2^32.
   Per block, planes 7..2 are at w and planes 1..0 at spill. */
static void bp_dots(const u8 *w, const s32 *tab, s32 groups, s32 *out, s32 nblk, s32 bias,
                    const u8 *spill)
{
    u32 acc[8];
    s32 k, g, r;
    const s32 *t;
    const u8 *p;
    for (; nblk > 0; nblk--, out += 8) {
        for (r = 0; r < 8; r++) acc[r] = 0;
        for (k = 0; k < 8; k++) {
            p = k < 6 ? w : spill;
            for (r = 0; r < 8; r++) acc[r] += acc[r];
            for (g = 0, t = tab; g < groups; g++, t += 64)
                for (r = 0; r < 8; r++) acc[r] += (u32)t[*p++ >> 2];
            if (k < 6) w = p; else spill = p;
        }
        for (r = 0; r < 8; r++) out[r] = (s32)(acc[r] - (u32)bias);
    }
}

/* A whole matmul's raw dots, row after row (one asm call in the Amiga build). */
static void dots_i8_x16(const s8 *w, const s16 *x, s32 cols, s32 *out, s32 rows)
{
    for (; rows > 0; rows--, w += cols) *out++ = dot_i8_x16(w, x, cols);
}

static void dots_i8_x16_sparse(const s8 *w, const s16 *pairs, s32 k, s32 *out, s32 rows, s32 cols)
{
    for (; rows > 0; rows--, w += cols) *out++ = dot_i8_x16_sparse(w, pairs, k);
}

/* out[r] = mulshift(out[r], mant[r], shift[r] - xsh), in place. */
static void mulshift_rows(s32 *out, const u16 *mant, s32 rows, s32 xsh, const u16 *shift)
{
    s32 r;
    for (r = 0; r < rows; r++) out[r] = mulshift(out[r], mant[r], (int)shift[r] - xsh);
}
#endif

static s16 xq[MAXHID];

/* out = table * x, x given as int16 << xsh. */
static void matmul(const QT *t, const s16 *x, int xsh, s32 *out)
{
    dots_i8_x16(t->w, x, t->cols, out, t->rows);
    mulshift_rows(out, t->mant, t->rows, xsh, t->shift);
}

/* The bit-plane table matmul (T-MAC). build_bptab() turns the int16 input
   into one 64-entry table per group of TG = 6 inputs; each row's dot is then
   8 look-ups per 6 weights (one per bit plane) and additions: no MULS. The
   same integer sums as matmul(), so exact. Rows in blocks of TB = 8. */
#define TB 8
#define TG 6
static s32 *ctab;                           /* D / 6 * 64 longs, after the KV caches */
static s32 cbias;                           /* 128 * sum of the input of ctab */

static void tab_input(const s16 *x, int n)
{
    cbias = (s32)((u32)build_bptab(x, ctab, n) << 7);
}

static void matmul_bp(const QT *t, int xsh, s32 *out)
{
    bp_dots((const u8 *)t->w, ctab, t->cols / TG, out, t->rows / TB, cbias, t->spill);
    mulshift_rows(out, t->mant, t->rows, xsh, t->shift);
}

/* Bytes of planes 1..0 of a table, which do not fit in its own space. */
static long spill_bytes(long rows, long cols) { return rows * cols / 3; }

/* Reorder a table's weights for bp_dots(): per block of 8 rows, per bit
   plane k = 7..0 of u = w ^ 0x80, per group of 6 columns, per row: one byte
   holding 4 * (bits k of u[row][6g .. 6g+5], bit i from column 6g+i).
   8 planes of 6 bits per byte take 4/3 of the int8 space: planes 7..2 fill
   the block's own 8 * cols bytes, planes 1..0 go to t->spill (8 * cols / 3
   bytes per block). Once, at load. */
static u8 tblk[TB * MAXHID];
static void bitplane_blocks(QT *t)
{
    long b, base, o;
    int r, k, g, i, cols = t->cols;
    u8 *w = (u8 *)t->w, *s = t->spill, *dst, v;
    for (b = 0; b < t->rows / TB; b++) {
        base = b * TB * cols;
        for (i = 0; i < TB * cols; i++) tblk[i] = (u8)(w[base + i] ^ 0x80);
        o = base;
        for (k = 7; k >= 0; k--)
            for (g = 0; g < cols / TG; g++)
                for (r = 0; r < TB; r++) {
                    for (v = 0, i = 0; i < TG; i++)
                        v |= (u8)(((tblk[(long)r * cols + TG * g + i] >> k) & 1) << i);
                    dst = k >= 2 ? w + o++ : s++;
                    *dst = (u8)(v << 2);
                }
    }
}

static void matmul_q(const QT *t, const s32 *x, s32 *out)
{
    int sh = quantize16(x, t->cols, xq);
    matmul(t, xq, sh, out);
}

/* matmul_q for an input that is mostly zero (after the ReLU, ~80%): the same
   sum, with the zero terms left out. Exact: a zero term adds nothing. */
static s16 pairs[2 * MAXHID];
static void matmul_q_sparse(const QT *t, const s32 *x, s32 *out)
{
    int j, k = 0, cols = t->cols, sh = quantize16(x, cols, xq);
    for (j = 0; j < cols; j++)
        if (xq[j]) { pairs[2 * k] = (s16)j; pairs[2 * k + 1] = xq[j]; k++; }
    dots_i8_x16_sparse(t->w, pairs, k, out, t->rows, cols);
    mulshift_rows(out, t->mant, t->rows, sh, t->shift);
}

/* -------------------------------------------------------------- state */

/* Key/value caches, L * CTX * D bytes each, in memory the caller provides
   (tiny_kv_bytes()). Sized to the model: static [MAXL][CTX][MAXD] arrays
   would take 256 KB where TINY needs 96 KB. */
static s8  *kc, *vc;
#define KV(m, l, j) (((long)(l) * CTX + (j)) * (m)->D)
static u16 km[MAXL][CTX], vm[MAXL][CTX];
static s16 ks[MAXL][CTX], vs[MAXL][CTX];
s32 tiny_layer_act[MAXL];                  /* Q16.16, for the UI */
#define layer_act tiny_layer_act

static s32 bx[MAXD], bh[MAXD], bq[MAXD], bk[MAXD], bv[MAXD], ba[MAXD], bt[MAXD];
static s32 bm[MAXHID], att[CTX], wgt[CTX];
static s16 qq[MAXD], wq16[CTX];
s32 tiny_score[VOCAB];                     /* Q16.16 scores of step() */
#define score tiny_score

static void attention(const Model *m, int l, int p)
{
    int h, i, j, o, HD = m->HD, qsh, wsh, s;
    s32 mx, tot, inv, acc;
    qsh = quantize16(bq, m->D, qq);
    for (h = 0; h < m->H; h++) {
        o = h * HD;
        for (j = 0; j <= p; j++)
            att[j] = mulshift(dot_i8_x16(kc + KV(m, l, j) + o, qq + o, HD),
                              km[l][j], (int)ks[l][j] - qsh);
        mx = att[0];
        for (j = 1; j <= p; j++) if (att[j] > mx) mx = att[j];
        tot = 0;
        for (j = 0; j <= p; j++) { att[j] = fxexp_neg(m->exp_tab, mx - att[j]); tot += att[j]; }
        inv = fxdiv(ONE, tot);
        for (j = 0; j <= p; j++)
            wgt[j] = mulshift(fxmul(att[j], inv), vm[l][j], vs[l][j]);   /* Q24 */
        wsh = quantize16(wgt, p + 1, wq16);
        s = 8 - wsh;
        for (i = 0; i < HD; i++) {
            acc = dot_x16_i8s(wq16, vc + KV(m, l, 0) + o + i, p + 1, m->D);
            ba[o + i] = s >= 0 ? acc >> s : (s32)((u32)acc << (-s));
        }
    }
}

/* Feed letter `tok` at position `p`; fills score[] (Q16.16). oracle step().
   full = 0 is prefill, for question letters whose scores nobody reads: it
   stops once the last layer has stored its key and value, so score[] and
   layer_act[L-1] are left as they were. Everything a later step reads (the
   KV caches) is identical to a full step's. */
static void step(const Model *m, int tok, int p, int full)
{
    int l, j, D = m->D;
    s32 sum;
    const QT *e = &m->emb, *ps = &m->pos;

    for (j = 0; j < D; j++)
        bx[j] = mulshift(e->w[(long)tok * D + j], e->mant[tok], (int)e->shift[tok] - 16)
              + mulshift(ps->w[(long)p * D + j], ps->mant[p], (int)ps->shift[p] - 16);

    for (l = 0; l < m->L; l++) {
        const QT *t = m->t[l];
        int sh;
        rmsnorm(bh, bx, m->g1 + (long)l * D, D);
        sh = quantize16(bh, D, xq);
        if (m->tab) {
            tab_input(xq, D);
            matmul_bp(&t[T_WK], sh, bk);
            matmul_bp(&t[T_WV], sh, bv);
        } else {
            matmul(&t[T_WK], xq, sh, bk);
            matmul(&t[T_WV], xq, sh, bv);
        }
        scale_of(store_kv(bk, D, kc + KV(m, l, p)), m->kc_m, m->kc_s, &km[l][p], &ks[l][p]);
        scale_of(store_kv(bv, D, vc + KV(m, l, p)), m->vc_m, m->vc_s, &vm[l][p], &vs[l][p]);
        if (!full && l == m->L - 1) return;     /* prefill: nothing else is read */
        if (m->tab) matmul_bp(&t[T_WQ], sh, bq);    /* ctab still holds xq's */
        else        matmul(&t[T_WQ], xq, sh, bq);

        attention(m, l, p);
        if (m->tab) {
            sh = quantize16(ba, D, xq);
            tab_input(xq, D);
            matmul_bp(&t[T_WO], sh, bt);
        } else
            matmul_q(&t[T_WO], ba, bt);
        for (j = 0; j < D; j++) bx[j] += bt[j];

        rmsnorm(bh, bx, m->g2 + (long)l * D, D);
        if (m->tab) {
            sh = quantize16(bh, D, xq);
            tab_input(xq, D);
            matmul_bp(&t[T_F1], sh, bm);
        } else
            matmul_q(&t[T_F1], bh, bm);
        sum = 0;
        for (j = 0; j < m->HID; j++) { if (bm[j] < 0) bm[j] = 0; sum += bm[j]; }
        layer_act[l] = sum / m->HID;
        matmul_q_sparse(&t[T_F2], bm, bt);
        for (j = 0; j < D; j++) bx[j] += bt[j];
    }
    rmsnorm(bh, bx, m->gf, D);
    matmul_q(&m->emb, bh, score);
}

/* ---------------------------------------------------------- choosing */

static u32 rng_state = 1;
static u32 rnd(void)
{
    rng_state ^= rng_state << 13;
    rng_state ^= rng_state >> 17;
    rng_state ^= rng_state << 5;
    return rng_state;
}

static void seed_rng(u32 seed)
{
    int i;
    rng_state = seed ? seed : 1;
    for (i = 0; i < 16; i++) rnd();             /* xorshift32 is weak on small seeds */
}

static s32 ev[VOCAB];

/* oracle pick(): argmax at creativity 0, else the TINY.C cut-off sampler. */
static int pick(const Model *m, int creativity)
{
    int i, best = 0;
    s32 sum = 0, r, v;
    for (i = 1; i < VOCAB; i++) if (score[i] > score[best]) best = i;
    if (creativity <= 0) return best;
    for (i = 0; i < VOCAB; i++) {
        v = fxexp_neg(m->exp_tab, ((score[best] - score[i]) * 5) / creativity);
        ev[i] = v >= CUTOFF ? v : 0;
        sum += ev[i];
    }
    r = mulshift(sum, (u16)(rnd() >> 16), 16);
    for (i = 0; i < VOCAB; i++) {
        r -= ev[i];
        if (r < 0 && ev[i] > 0) return i;
    }
    return best;
}

/* oracle confidence(): how sure the network is of `tok`, Q16.16. */
static s32 confidence(const Model *m, int tok)
{
    int i, best = 0;
    s32 sum = 0;
    for (i = 1; i < VOCAB; i++) if (score[i] > score[best]) best = i;
    for (i = 0; i < VOCAB; i++) { ev[i] = fxexp_neg(m->exp_tab, score[best] - score[i]); sum += ev[i]; }
    return fxdiv(ev[tok], sum);
}

/* oracle think(): returns the number of letters written to out. */
/* Told about every letter as soon as it is chosen (the UI prints it then:
   at ~5 s a letter, waiting for the whole answer is not an option). */
void (*tiny_on_letter)(int c);
/* Told before each question letter is read (TINY.C's on_step): the UI's
   scanner. Display only; the numbers do not depend on it. */
void (*tiny_on_step)(int i, int n);

static int think(const Model *m, const char *q, char *out, int creativity)
{
    int p = 0, n = 0, tok, nq = 0;
    while (q[nq]) nq++;
    for (; q[p]; p++) {                              /* prefill */
        if (tiny_on_step) tiny_on_step(p, nq);
        step(m, q[p] - 30, p, 0);
    }
    step(m, 1, p++, 1);                              /* its scores pick letter 1 */
    for (; p < CTX; p++) {
        tok = pick(m, creativity);
        if (tok <= 1) break;
        out[n++] = (char)(tok + 30);
        if (tiny_on_letter) tiny_on_letter(tok + 30);
        step(m, tok, p, 1);
    }
    out[n] = 0;
    return n;
}

/* ------------------------------------------------------ entry points */

/* Non-static on purpose: vbcc inlines static functions and drops their
   symbols (SMW P37), and the 68000 gate calls these by address. */
static Model model;

/* Caller memory: the key/value caches (2 * L * CTX * D bytes), the group
   tables of matmul_bp() (D / 6 * 64 longs), then the spilled planes 1..0 of
   wq wk wv wo f1 (spill_bytes() of each, L * (4 D^2 + HID D) / 3). */
long tiny_kv_bytes(const u8 *blob)
{
    long D = be16(blob + 6), L = be16(blob + 8), HID = be16(blob + 12);
    return 2L * L * CTX * D + D / TG * 256 + L * (4 * spill_bytes(D, D) + spill_bytes(HID, D));
}

/* Reorders wq wk wv wo f1 for matmul_bp(), partly into the caller memory,
   and marks the blob ("T68C") so that a second tiny_init() on the same blob
   and the same kv memory does not reorder it again. The kv memory then holds
   model data (the spilled planes): never clear or replace all of it. */
const char *tiny_init(u8 *blob, long size, s8 *kv)
{
    int l, k;
    static const int bpt[5] = { T_WQ, T_WK, T_WV, T_WO, T_F1 };
    u8 *spill;
    const char *err = parse_model(&model, blob, size);
    if (err) return err;
    kc = kv;
    vc = kv + (long)model.L * CTX * model.D;
    ctab = (s32 *)(kv + 2L * model.L * CTX * model.D);
    spill = (u8 *)ctab + (long)model.D / TG * 256;
    model.tab = model.D % TG == 0 && model.D % TB == 0 && model.HID % TB == 0;
    if (model.tab)
        for (l = 0; l < model.L; l++)
            for (k = 0; k < 5; k++) {
                QT *t = &model.t[l][bpt[k]];
                t->spill = spill;
                spill += spill_bytes(t->rows, t->cols);
            }
    if (model.tab && blob[3] == 'A') {
        for (l = 0; l < model.L; l++)
            for (k = 0; k < 5; k++) bitplane_blocks(&model.t[l][bpt[k]]);
        blob[3] = 'C';
    } else if (!model.tab && blob[3] == 'C')
        return "blob already reordered";
    return NIL;
}

void tiny_step(int tok, int p) { step(&model, tok, p, 1); }
void tiny_prefill(int tok, int p) { step(&model, tok, p, 0); }

int tiny_think(const char *q, char *out, int creativity, u32 seed)
{
    seed_rng(seed);
    return think(&model, q, out, creativity);
}

s32 tiny_confidence(int tok) { return confidence(&model, tok); }

#ifndef TINY_ENGINE_ONLY

/* TINY.C clean(): printable letters only, no trailing spaces, CAPS -> lower. */
static int clean(const char *line, char *q)
{
    int i, n = 0, lower = 0, upper = 0;
    for (i = 0; line[i] && n < MAXQ; i++)
        if (line[i] >= 32 && line[i] <= 126) q[n++] = line[i];
    while (n > 0 && q[n - 1] == ' ') n--;
    q[n] = 0;
    for (i = 0; i < n; i++) {
        if (q[i] >= 'a' && q[i] <= 'z') lower++;
        if (q[i] >= 'A' && q[i] <= 'Z') upper++;
    }
    if (upper > 0 && lower == 0)
        for (i = 0; i < n; i++) if (q[i] >= 'A' && q[i] <= 'Z') q[i] += 'a' - 'A';
    return n;
}

/* --------------------------------------------------------------- main */

/* Wall time in 1/50 s ticks. */
static long ticks(void)
{
#ifdef A5_HOST
    return (long)((long)clock() * 50L / (long)CLOCKS_PER_SEC);
#else
    /* KS 1.x DOS is BCPL: DateStamp() turns the pointer into a BPTR, so the
       struct must be longword aligned. vbcc only promises word alignment. */
    static long dsbuf[4];
    struct TinyDateStamp *ds = (struct TinyDateStamp *)(((unsigned long)dsbuf + 3) & ~3UL);
    tiny_datestamp(DOSBase, ds);
    return (ds->days * 1440L + ds->minute) * 3000L + ds->tick;
#endif
}

static void show_letter(int c)
{
    putchar(c);
    fflush(stdout);
}

static const char *load_model(const char *path)
{
    FILE *f = fopen(path, "rb");
    long size;
    u8 *blob;
    s8 *kv;
    if (!f) return "cannot open the model file";
    fseek(f, 0, SEEK_END); size = ftell(f); fseek(f, 0, SEEK_SET);
    blob = (u8 *)malloc(size);
    if (!blob) { fclose(f); return "not enough memory for the model"; }
    if (fread(blob, 1, size, f) != (size_t)size) { fclose(f); return "model file too short"; }
    fclose(f);
    if (size < 26) return "not a version 1 .t68 file";
    kv = (s8 *)malloc(tiny_kv_bytes(blob));
    if (!kv) return "not enough memory for the key/value cache";
    return tiny_init(blob, size, kv);
}

/* Scores and layer activity per position. pre = 1 feeds the question
   through the prefill path and prints only from the separator on: those
   scores depend on the KV caches that prefill wrote. */
static int dump(const Model *m, const char *q, const char *a, int pre)
{
    char buf[CTX + 2];
    int n = 0, p, i, tok, nq;
    while (*q && n < CTX) buf[n++] = *q++;
    nq = n;
    if (n < CTX) buf[n++] = '\n';
    while (*a && n < CTX) buf[n++] = *a++;
    for (p = 0; p < n; p++) {
        tok = buf[p] == '\n' ? 1 : buf[p] - 30;
        step(m, tok, p, !(pre && p < nq));
        if (pre && p < nq) continue;
        for (i = 0; i < VOCAB; i++) printf("%ld\n", (long)score[i]);
        for (i = 0; i < m->L; i++) printf("%ld\n", (long)layer_act[i]);
    }
    return 0;
}

int main(int argc, char **argv)
{
    static char line[256], q[MAXQ + 1], out[CTX + 1];
    const char *mp = argc > 1 ? argv[1] : "tiny.t68", *err;
    int i, n, creativity = 0, ask = 0, timing = 0;
    u32 seed = 1;
    long t0, t1;

    for (i = 2; i < argc; i++) {
        if (!strcmp(argv[i], "-time")) timing = 1;
        else if (!strcmp(argv[i], "-c") && i + 1 < argc) creativity = atoi(argv[++i]);
        else if (!strcmp(argv[i], "-s") && i + 1 < argc) seed = (u32)atol(argv[++i]);
        else if (!strcmp(argv[i], "-ask")) ask = 1;
        else if ((!strcmp(argv[i], "-dump") || !strcmp(argv[i], "-dumppre")) && i + 2 < argc) {
            if ((err = load_model(mp)) != NULL) { fprintf(stderr, "tiny68k: %s\n", err); return 1; }
            return dump(&model, argv[i + 1], argv[i + 2], argv[i][5] == 'p');
        }
    }
    if ((err = load_model(mp)) != NULL) { fprintf(stderr, "tiny68k: %s\n", err); return 1; }

    if (!ask) {
        printf("TINY 2.1 for the 68000 - %d layers, %d wide, creativity %d.\n",
               model.L, model.D, creativity);
        printf("Ask a question, or QUIT.\n");
    }
    for (;;) {
        if (!ask) { printf("\n> "); fflush(stdout); }
        if (fgets(line, sizeof line, stdin) == NULL) break;
        if (clean(line, q) == 0) continue;
        if (!ask && (!strcmp(q, "quit") || !strcmp(q, "exit"))) break;
        if (!ask) { printf("  "); fflush(stdout); tiny_on_letter = show_letter; }
        t0 = ticks();
        n = tiny_think(q, out, creativity, seed);
        t1 = ticks() - t0;
        if (ask) printf("%s\n", out);
        if (!ask || timing)
            printf("%s   [%d letters, %d positions, %ld.%02ld s]\n", ask ? "" : "\n",
                   n, (int)strlen(q) + 1 + n, t1 / 50, (t1 % 50) * 2);
        fflush(stdout);
    }
    return 0;
}

#endif /* TINY_ENGINE_ONLY */
