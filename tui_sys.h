/* tui_sys.h -- what tinyui.c needs from the machine. Two backends:
 * tui_host.c (gcc, renders to files for tests) and tui_amiga.c (the A500,
 * hardware taken over after the model is loaded). */
#ifndef TUI_SYS_H
#define TUI_SYS_H

#define K_ESC   27
#define K_ENTER 13
#define K_BS    8
#define K_F1    (256 + 59)
#define K_F2    (256 + 60)
#define K_F3    (256 + 61)
#define K_F4    (256 + 62)
#define K_F5    (256 + 63)
#define K_UP    (256 + 72)

const char *sys_init(int theme);   /* screen up, OS still running (file loading) */
void sys_takeover(void);           /* after loading: keyboard, timer, no OS */
void sys_done(void);
void sys_flush(void);              /* draw what conio changed */
void sys_theme(int theme);         /* new colours, whole screen redrawn */
void sys_cursor(int on);
int  sys_kbhit(void);              /* both flush first */
int  sys_getch(void);              /* K_* or 32..126; waits */
long sys_ticks(void);              /* 1/50 s since start */
void sys_delay(unsigned ms);       /* flushes, then waits */
void sys_gettime(int *h, int *m);
void sys_tone(unsigned hz, unsigned ms);   /* hz 0 = silence; waits ms */
void sys_shot(const char *name);   /* tests: save the screen (host only) */

#endif
