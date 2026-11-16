/* render68k.h -- the 80 x 25 cell buffer (conio68k) drawn into two hires
 * bitplanes (640 x 200, 80 bytes a line), with a 4-colour theme.
 *
 * Portable C: the Amiga backend points it at chip RAM planes, the host
 * backend at plain memory it then writes out for a PNG. One renderer for
 * both, so a host render is what the Amiga shows. */
#ifndef RENDER68K_H
#define RENDER68K_H

#define NTHEME 3
#define PLANE_BYTES (80 * 200)

extern const char *const theme_name[NTHEME];
extern const unsigned short theme_rgb[NTHEME][4];   /* $0RGB, Amiga colour registers */

/* The glyphs, by CP437 code: 8 rows, bit 7 = leftmost pixel. Fill ASCII and
   Latin-1 from topaz 8 with font_from_topaz(), then font_finish() adds the
   CP437 graphics TINY uses (boxes, blocks, shades, arrows) drawn here. */
extern unsigned char font8[256][8];
void font_from_topaz(const unsigned char *chardata, int modulo,
                     const unsigned long *charloc, int lochar, int hichar);
void font_finish(void);

/* fg | bg << 2 for DOS attribute `attr` in `theme`. */
int theme_pens(int theme, int attr);

/* Draw every dirty cell (and the cursor) into the planes. */
void render_flush(unsigned char *plane0, unsigned char *plane1, int theme,
                  int cursor_on, int cursor_x, int cursor_y);

#endif
