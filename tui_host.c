/* tui_host.c -- tinyui's backend on the host (gcc), for tests.
 *
 * Draws with the Amiga renderer (render68k.c) into planes in memory, with
 * topaz 8 read from a Kickstart ROM file (TINY_ROM, default the WinUAE
 * kick12.rom; the font is not copied into this repository). Keys come from
 * TINY_KEYS ("what is dos{ENTER}{F5}{ESC}"), Esc when they run out.
 * sys_shot(name) writes shot_<name>.bin: for each theme the 4 colours and
 * the two planes, then the cell buffer; scripts/shot_png.py makes PNGs.
 * It also reports any visible glyph drawn with equal ink and paper pens. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include "conio68k.h"
#include "render68k.h"
#include "tui_sys.h"

static unsigned char p0[PLANE_BYTES], p1[PLANE_BYTES];
static int theme, cursor_on;
static const char *keys;

static const char *load_topaz(void)
{
    const char *path = getenv("TINY_ROM");
    static unsigned char rom[262144];
    static unsigned long loc[256];
    FILE *f;
    long n, o;
    if (!path) path = "../amiga260k/emu/kick12.rom";
    f = fopen(path, "rb");
    if (!f) return "no Kickstart ROM for topaz (set TINY_ROM)";
    n = (long)fread(rom, 1, sizeof rom, f);
    fclose(f);
    for (o = 0; o + 24 < n; o += 2) {
        /* TextFont from tf_YSize: 8, style, flags, xsize 8, baseline 6,
           boldsmear 1, accessors, lochar 32, hichar 255 */
        if (rom[o] == 0 && rom[o + 1] == 8 && rom[o + 4] == 0 && rom[o + 5] == 8 &&
            rom[o + 6] == 0 && rom[o + 7] == 6 && rom[o + 8] == 0 && rom[o + 9] == 1 &&
            rom[o + 12] == 32 && rom[o + 13] == 255) {
            unsigned long cd = (unsigned long)rom[o + 14] << 24 | rom[o + 15] << 16 | rom[o + 16] << 8 | rom[o + 17];
            int mod = rom[o + 18] << 8 | rom[o + 19];
            unsigned long cl = (unsigned long)rom[o + 20] << 24 | rom[o + 21] << 16 | rom[o + 22] << 8 | rom[o + 23];
            unsigned long base = 0x1000000UL - (unsigned long)n;   /* $FC0000 for 256 KB */
            int c;
            if (cd < base || cl < base) continue;
            for (c = 0; c < 224; c++) {
                const unsigned char *e = rom + (cl - base) + 4 * c;
                loc[c] = (unsigned long)e[0] << 24 | (unsigned long)e[1] << 16 | e[2] << 8 | e[3];
            }
            font_from_topaz(rom + (cd - base), mod, loc, 32, 255);
            font_finish();
            return NULL;
        }
    }
    return "topaz 8 not found in the ROM";
}

const char *sys_init(int t)
{
    theme = t;
    keys = getenv("TINY_KEYS");
    if (!keys) keys = "";
    return load_topaz();
}

void sys_takeover(void) {}
void sys_done(void) { sys_flush(); }

void sys_flush(void)
{
    int x, y;
    con_cursor(&x, &y);
    render_flush(p0, p1, theme, cursor_on, x, y);
}

void sys_theme(int t)
{
    theme = t;
    con_touch_all();
    sys_flush();
}

void sys_cursor(int on) { cursor_on = on; }

static int next_key(int take)
{
    static const struct { const char *name; int k; } named[] = {
        { "{ENTER}", K_ENTER }, { "{ESC}", K_ESC }, { "{BS}", K_BS }, { "{UP}", K_UP },
        { "{F1}", K_F1 }, { "{F2}", K_F2 }, { "{F3}", K_F3 }, { "{F4}", K_F4 }, { "{F5}", K_F5 },
    };
    int i;
    if (!*keys) return -1;
    for (i = 0; i < (int)(sizeof named / sizeof named[0]); i++) {
        size_t l = strlen(named[i].name);
        if (strncmp(keys, named[i].name, l) == 0) {
            if (take) keys += l;
            return named[i].k;
        }
    }
    i = (unsigned char)*keys;
    if (take) keys++;
    return i;
}

int sys_kbhit(void) { sys_flush(); return next_key(0) >= 0; }

int sys_getch(void)
{
    int k;
    sys_flush();
    k = next_key(1);
    return k < 0 ? K_ESC : k;
}

long sys_ticks(void) { return (long)((double)clock() * 50.0 / CLOCKS_PER_SEC); }
void sys_delay(unsigned ms) { (void)ms; sys_flush(); }
void sys_gettime(int *h, int *m) { *h = 12; *m = 0; }
void sys_tone(unsigned hz, unsigned ms) { (void)hz; (void)ms; }

static void put16(FILE *f, unsigned v) { fputc((int)(v >> 8) & 255, f); fputc((int)v & 255, f); }

void sys_shot(const char *name)
{
    static unsigned char q0[PLANE_BYTES], q1[PLANE_BYTES];
    char fn[64];
    FILE *f;
    int t, x, y, cx, cy, bad = 0;
    sys_flush();
    sprintf(fn, "shot_%s.bin", name);
    f = fopen(fn, "wb");
    if (!f) return;
    fwrite("TSH1", 1, 4, f);
    put16(f, NTHEME);
    con_cursor(&cx, &cy);
    for (t = 0; t < NTHEME; t++) {
        con_touch_all();
        render_flush(q0, q1, t, cursor_on, cx, cy);
        for (x = 0; x < 4; x++) put16(f, theme_rgb[t][x]);
        fwrite(q0, 1, PLANE_BYTES, f);
        fwrite(q1, 1, PLANE_BYTES, f);
        for (y = 0; y < SCRH; y++)
            for (x = 0; x < SCRW; x++) {
                int pens = theme_pens(t, con_at[y][x]), r, ink = 0;
                for (r = 0; r < 8; r++) ink |= font8[con_ch[y][x]][r];
                if (ink && (pens & 3) == (pens >> 2)) {
                    if (bad++ < 5)
                        fprintf(stderr, "%s, %s: invisible char %d (attr %02X) at %d,%d\n",
                                name, theme_name[t], con_ch[y][x], con_at[y][x], x + 1, y + 1);
                }
            }
    }
    fwrite(con_ch, 1, SCRW * SCRH, f);
    fwrite(con_at, 1, SCRW * SCRH, f);
    fclose(f);
    con_touch_all();
    sys_flush();
    if (bad) fprintf(stderr, "%s: %d invisible glyphs\n", name, bad);
}
