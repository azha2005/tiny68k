/* conio68k.h -- the Turbo C 2.0 conio subset TINY.C's interface uses,
 * over an 80 x 25 buffer of (CP437 char, DOS attribute) cells.
 *
 * Portable C, no hardware: the backend (tui_host.c, tui_amiga.c) draws the
 * cells marked dirty when the UI calls sys_flush(). Same semantics as Turbo
 * C where TINY.C depends on them: 1-based window()/gotoxy(), putch() wraps
 * at the window's right edge and scrolls it when it wraps past the bottom,
 * '\n' moves down (scrolling at the bottom), '\r' to column 1, '\a' and
 * '\b' are not drawn. So the cell buffer after a demo run can be compared
 * with TINY.EXE's own SCREEN.DMP. */
#ifndef CONIO68K_H
#define CONIO68K_H

#define SCRW 80
#define SCRH 25

extern unsigned char con_ch[SCRH][SCRW];   /* CP437 code */
extern unsigned char con_at[SCRH][SCRW];   /* DOS attribute: fg | bg << 4 */
extern unsigned char con_dirty[SCRH][SCRW];
extern unsigned char con_rowdirty[SCRH];
extern int con_ndirty;                     /* any cell dirty since the last flush */

void window(int left, int top, int right, int bottom);
void gotoxy(int x, int y);
int  wherex(void);
int  wherey(void);
void textattr(int attr);
void putch(int c);
void cputs(const char *s);
void cprintf(const char *fmt, ...);
void clrscr(void);
void con_touch_all(void);                  /* everything dirty (theme change) */
void con_cursor(int *x, int *y);           /* cursor, 0-based screen cell */

#endif
