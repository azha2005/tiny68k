/* conio68k.c -- see conio68k.h. */
#include <stdarg.h>
#include <stdio.h>
#include "conio68k.h"

unsigned char con_ch[SCRH][SCRW];
unsigned char con_at[SCRH][SCRW];
unsigned char con_dirty[SCRH][SCRW];
unsigned char con_rowdirty[SCRH];
int con_ndirty;

static int wl = 1, wt = 1, wr = SCRW, wb = SCRH;    /* window, 1-based screen */
static int cx = 1, cy = 1;                          /* cursor, window-relative */
static int attr = 7;

static void set(int x, int y, int c, int a)        /* 0-based screen cell */
{
    if (con_ch[y][x] != c || con_at[y][x] != a) {
        con_ch[y][x] = (unsigned char)c;
        con_at[y][x] = (unsigned char)a;
        con_dirty[y][x] = 1;
        con_rowdirty[y] = 1;
        con_ndirty = 1;
    }
}

static void scroll_up(void)
{
    int x, y;
    for (y = wt - 1; y < wb - 1; y++)
        for (x = wl - 1; x < wr; x++)
            set(x, y, con_ch[y + 1][x], con_at[y + 1][x]);
    for (x = wl - 1; x < wr; x++) set(x, wb - 1, ' ', attr);
}

void window(int left, int top, int right, int bottom)
{
    if (left < 1 || top < 1 || right > SCRW || bottom > SCRH || left > right || top > bottom)
        return;
    wl = left; wt = top; wr = right; wb = bottom;
    cx = cy = 1;
}

void gotoxy(int x, int y)
{
    if (x < 1 || y < 1 || x > wr - wl + 1 || y > wb - wt + 1) return;
    cx = x; cy = y;
}

int wherex(void) { return cx; }
int wherey(void) { return cy; }
void textattr(int a) { attr = a & 0xFF; }

static void line_feed(void)
{
    if (cy < wb - wt + 1) cy++;
    else scroll_up();
}

void putch(int c)
{
    c &= 0xFF;
    switch (c) {
    case '\n': line_feed(); return;
    case '\r': cx = 1; return;
    case '\a': return;
    case '\b': if (cx > 1) cx--; return;
    }
    set(wl - 1 + cx - 1, wt - 1 + cy - 1, c, attr);
    if (++cx > wr - wl + 1) { cx = 1; line_feed(); }
}

void cputs(const char *s)
{
    while (*s) putch((unsigned char)*s++);
}

void cprintf(const char *fmt, ...)
{
    char buf[160];
    va_list ap;
    va_start(ap, fmt);
    vsprintf(buf, fmt, ap);
    va_end(ap);
    cputs(buf);
}

void clrscr(void)
{
    int x, y;
    for (y = wt - 1; y < wb; y++)
        for (x = wl - 1; x < wr; x++) set(x, y, ' ', attr);
    cx = cy = 1;
}

void con_touch_all(void)
{
    int x, y;
    for (y = 0; y < SCRH; y++) {
        for (x = 0; x < SCRW; x++) con_dirty[y][x] = 1;
        con_rowdirty[y] = 1;
    }
    con_ndirty = 1;
}

void con_cursor(int *x, int *y)
{
    *x = wl - 1 + cx - 1;
    *y = wt - 1 + cy - 1;
}
