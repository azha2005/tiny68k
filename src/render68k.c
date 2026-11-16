/* render68k.c -- see render68k.h. */
#include "conio68k.h"
#include "render68k.h"

/* ------------------------------------------------------------- themes
   TINY.C picks colours by DOS attribute (C_FRAME = 11, C_ANSWER = 10, ...).
   A 2-plane screen has 4 pens, so each theme maps the 16 DOS foregrounds to
   pens, in three classes of background: none (DOS bg 0), the bars and
   panel names (bg 1, 3..7) and the "word being spoken" highlight (bg 2). */

const char *const theme_name[NTHEME] = { "Workbench 1.3", "Workbench 2.0", "Neon" };

const unsigned short theme_rgb[NTHEME][4] = {
    { 0x05A, 0xFFF, 0x002, 0xF80 },     /* blue, white, black, orange */
    { 0xAAA, 0x000, 0xFFF, 0x68B },     /* grey, black, white, blue */
    { 0x001, 0x4FA, 0x0CF, 0xF5A },     /* navy, mint, cyan, pink */
};

/* background pen per class: none, bar, highlight */
static const unsigned char bg_pen[NTHEME][3] = {
    { 0, 1, 3 },
    { 0, 3, 2 },
    { 0, 2, 3 },
};

/* foreground pen per class and DOS colour 0..15:
   0 black 1 blue 2 green 3 cyan 4 red 5 magenta 6 brown 7 grey 8 dark grey
   9 light blue 10 mint (answers, meters) 11 cyan (frames, TINY) 12 red
   (unsure) 13 pink (YOU) 14 amber (question) 15 white (bright, newest) */
static const unsigned char fg_pen[NTHEME][3][16] = {
    {   /* Workbench 1.3: white frames and answers, black text, orange accents */
        { 0, 2, 1, 1, 3, 3, 3, 1, 2, 1, 1, 1, 3, 3, 2, 3 },
        { 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 3, 2, 3, 3, 3, 0 },
        { 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 1 },
    },
    {   /* Workbench 2.0: black on grey, white dim text, blue accents */
        { 0, 2, 1, 1, 3, 3, 3, 1, 2, 3, 1, 1, 3, 3, 1, 3 },
        { 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 1, 2, 1, 1, 1, 1 },
        { 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 3 },
    },
    {   /* Neon: mint text, cyan frames, pink for you and the question */
        { 0, 2, 1, 2, 3, 3, 3, 1, 2, 2, 1, 2, 3, 3, 3, 1 },
        { 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0 },
        { 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0 },
    },
};

int theme_pens(int theme, int attr)
{
    int bg = (attr >> 4) & 7, cls = bg == 0 ? 0 : bg == 2 ? 2 : 1;
    return fg_pen[theme][cls][attr & 15] | bg_pen[theme][cls] << 2;
}

/* --------------------------------------------------------------- font */

unsigned char font8[256][8];

void font_from_topaz(const unsigned char *chardata, int modulo,
                     const unsigned long *charloc, int lochar, int hichar)
{
    int c, r;
    for (c = lochar; c <= hichar && c < 256; c++) {
        unsigned long loc = charloc[c - lochar];
        int off = (int)(loc >> 16), w = (int)(loc & 0xFFFF);
        for (r = 0; r < 8; r++) {
            int b, v = 0;
            for (b = 0; b < 8 && b < w; b++) {
                int bit = off + b;
                if (chardata[r * modulo + (bit >> 3)] & (0x80 >> (bit & 7))) v |= 0x80 >> b;
            }
            font8[c][r] = (unsigned char)v;
        }
    }
}

static unsigned char *g;
static void hl(int y, int x0, int x1) { int x; for (x = x0; x <= x1; x++) g[y] |= (unsigned char)(0x80 >> x); }
static void vl(int x, int y0, int y1) { int y; for (y = y0; y <= y1; y++) g[y] |= (unsigned char)(0x80 >> x); }
static void rows(int c, const unsigned char *r) { int i; for (i = 0; i < 8; i++) font8[c][i] = r[i]; }
static void start(int c) { int i; g = font8[c]; for (i = 0; i < 8; i++) g[i] = 0; }

/* CP437 TINY uses, in topaz's style: double lines on rows/columns 2 and 5,
   single ones on 3. Codes below 32 and above 126 that are not drawn here
   or mapped to a Latin-1 topaz glyph are blank. */
void font_finish(void)
{
    static const unsigned char tri_r[8] = { 0x00, 0x60, 0x78, 0x7E, 0x78, 0x60, 0x00, 0x00 };
    static const unsigned char tri_l[8] = { 0x00, 0x06, 0x1E, 0x7E, 0x1E, 0x06, 0x00, 0x00 };
    static const unsigned char up[8]    = { 0x18, 0x3C, 0x7E, 0x18, 0x18, 0x18, 0x18, 0x00 };
    static const unsigned char sq[8]    = { 0x00, 0x00, 0x3C, 0x3C, 0x3C, 0x3C, 0x00, 0x00 };
    static const unsigned char lat_from[] = { 175, 250 }, lat_to[] = { 0xBB, 0xB7 };
    unsigned char keep[2][8];
    int c, i;
    for (i = 0; i < 2; i++) for (c = 0; c < 8; c++) keep[i][c] = font8[lat_to[i]][c];
    for (c = 0; c < 32; c++) start(c);
    for (c = 127; c < 256; c++) start(c);
    for (i = 0; i < 2; i++) rows(lat_from[i], keep[i]);
    rows(16, tri_r); rows(17, tri_l); rows(24, up); rows(254, sq);
    for (i = 0; i < 8; i++) {
        font8[219][i] = 0xFF;
        font8[221][i] = 0xF0;
        font8[222][i] = 0x0F;
        font8[176][i] = i & 1 ? 0x22 : 0x88;
        font8[177][i] = i & 1 ? 0xAA : 0x55;
        font8[178][i] = i & 1 ? 0xDD : 0x77;
    }
    start(205); hl(2, 0, 7); hl(5, 0, 7);                          /* ═ */
    start(186); vl(2, 0, 7); vl(5, 0, 7);                          /* ║ */
    start(201); hl(2, 2, 7); vl(2, 2, 7); hl(5, 5, 7); vl(5, 5, 7); /* ╔ */
    start(187); hl(2, 0, 5); vl(5, 2, 7); hl(5, 0, 2); vl(2, 5, 7); /* ╗ */
    start(200); vl(2, 0, 5); hl(5, 2, 7); vl(5, 0, 2); hl(2, 5, 7); /* ╚ */
    start(188); vl(5, 0, 5); hl(5, 0, 5); vl(2, 0, 2); hl(2, 0, 2); /* ╝ */
    start(181); vl(3, 0, 7); hl(2, 0, 3); hl(5, 0, 3);             /* ╡ */
    start(198); vl(3, 0, 7); hl(2, 3, 7); hl(5, 3, 7);             /* ╞ */
    start(209); hl(2, 0, 7); hl(5, 0, 7); vl(3, 5, 7);             /* ╤ */
    start(207); hl(2, 0, 7); hl(5, 0, 7); vl(3, 0, 2);             /* ╧ */
    start(179); vl(3, 0, 7);                                       /* │ */
    start(196); hl(3, 0, 7);                                       /* ─ */
}

/* ----------------------------------------------------------- renderer */

static int cur_x = -1, cur_y = -1;          /* where the cursor was drawn */

#ifdef USE_ASM
void render_row_asm(__reg("a0") unsigned char *p0,
                    __reg("a1") unsigned char *p1,
                    __reg("a2") const unsigned char *ch,
                    __reg("a3") const unsigned char *at,
                    __reg("a4") unsigned char *dirty,
                    __reg("d0") const unsigned char *font,
                    __reg("d1") const unsigned char *pentab,
                    __reg("d2") int cur_col);

static unsigned char pen_tab[NTHEME][256];
static int pen_tab_ready;

static void init_pen_tab(void)
{
    int t, a;
    for (t = 0; t < NTHEME; t++)
        for (a = 0; a < 256; a++)
            pen_tab[t][a] = (unsigned char)theme_pens(t, a);
    pen_tab_ready = 1;
}
#else
static void cell(unsigned char *p0, unsigned char *p1, int theme, int x, int y, int under)
{
    int pens = theme_pens(theme, con_at[y][x]), fg = pens & 3, bg = pens >> 2, r;
    unsigned char m0f = fg & 1 ? 0xFF : 0, m1f = fg & 2 ? 0xFF : 0;
    unsigned char m0b = bg & 1 ? 0xFF : 0, m1b = bg & 2 ? 0xFF : 0;
    const unsigned char *gl = font8[con_ch[y][x]];
    long o = (long)y * 8 * 80 + x;
    for (r = 0; r < 8; r++, o += 80) {
        unsigned char v = gl[r];
        if (under && r == 7) v = 0xFF;
        p0[o] = (unsigned char)((v & m0f) | (~v & m0b));
        p1[o] = (unsigned char)((v & m1f) | (~v & m1b));
    }
}
#endif

void render_flush(unsigned char *plane0, unsigned char *plane1, int theme,
                  int cursor_on, int cursor_x, int cursor_y)
{
    int y;
#ifdef USE_ASM
    long row_off;
#else
    int x;
#endif
    if (!cursor_on) cursor_x = cursor_y = -1;
    if (!con_ndirty && cur_x == cursor_x && cur_y == cursor_y) return;
#ifdef USE_ASM
    if (!pen_tab_ready) init_pen_tab();
#endif
    if ((cur_x != cursor_x || cur_y != cursor_y) && cur_x >= 0)
        con_dirty[cur_y][cur_x] = con_rowdirty[cur_y] = 1;
    if (cursor_x >= 0) con_dirty[cursor_y][cursor_x] = con_rowdirty[cursor_y] = 1;
#ifdef USE_ASM
    for (y = 0, row_off = 0; y < SCRH; y++, row_off += 640) {
        if (!con_rowdirty[y]) continue;
        con_rowdirty[y] = 0;
        render_row_asm(plane0 + row_off, plane1 + row_off,
                       con_ch[y], con_at[y], con_dirty[y],
                       (const unsigned char *)font8, pen_tab[theme],
                       y == cursor_y ? cursor_x : -1);
    }
#else
    for (y = 0; y < SCRH; y++) {
        if (!con_rowdirty[y]) continue;
        con_rowdirty[y] = 0;
        for (x = 0; x < SCRW; x++)
            if (con_dirty[y][x]) {
                cell(plane0, plane1, theme, x, y, x == cursor_x && y == cursor_y);
                con_dirty[y][x] = 0;
            }
    }
#endif
    cur_x = cursor_x; cur_y = cursor_y;
    con_ndirty = 0;
}
