/* tinyui.c -- TINY.C's full-screen interface, for the Amiga 500.
 *
 * Ported from TINY 2.1's TINY.C (the DOS screen part), which has no
 * licence: keep this file private unless its author agrees.
 *
 * The drawing code is TINY.C's, written against conio68k (the Turbo C conio
 * subset over a cell buffer), so the screen is laid out exactly as on DOS.
 * What changed:
 *   - no floating point: layer activity, confidence and their meters are
 *     integer (the 68000 has no FPU; the engine gives Q16.16);
 *   - the engine is tiny68k.c (tiny_think with tiny_on_step/on_letter);
 *   - colours come from 4-pen themes (render68k.c), F5 cycles them;
 *   - the machine is behind tui_sys.h (tui_amiga.c, tui_host.c);
 *   - sound is TINY.C's calls with Paula/stub timings (sys_tone);
 *   - during the question the engine skips the last layer (prefill), so
 *     its meter and the confidence are not redrawn then.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "conio68k.h"
#include "render68k.h"
#include "tui_sys.h"

/* The UI's own generator, so a /dump draws the same screen on every
   machine (libc rand() differs between vbcc and the host). */
static unsigned long ui_seed = 12345;
static int ui_rand(void)
{
    ui_seed = ui_seed * 1103515245UL + 12345UL;
    return (int)((ui_seed >> 16) & 0x7FFF);
}

#ifdef A5_HOST
typedef int s32;
typedef unsigned int u32;
#else
typedef long s32;
typedef unsigned long u32;
#endif
typedef unsigned char u8;
typedef signed char s8;

/* the engine (tiny68k.c built with -DTINY_ENGINE_ONLY) */
extern s32 tiny_layer_act[];
extern void (*tiny_on_letter)(int c);
extern void (*tiny_on_step)(int i, int n);
long tiny_kv_bytes(const u8 *blob);
const char *tiny_init(u8 *blob, long size, s8 *kv);
int tiny_think(const char *q, char *out, int creativity, u32 seed);
s32 tiny_confidence(int tok);

#define CTX    128
#define MAXQ   72
#define MAXL   8

static int L, D;
static int creativity = 5;
static int conf1000;                    /* confidence of the last letter, 0..1000 */
static int prefill;                     /* reading the question */

/* ------------------------------------------------------------ helpers */

static int is_word(const char *q, const char *w)
{
    int i;
    for (i = 0; q[i] && w[i]; i++)
        if ((q[i] | 32) != w[i]) return 0;
    return q[i] == 0 && w[i] == 0;
}

static int clean(const char *line, char *q)
{
    int i, n = 0, lower = 0, upper = 0;
    for (i = 0; line[i] && n < MAXQ; i++)
        if (line[i] >= 32 && line[i] <= 126)
            q[n++] = line[i];
    while (n > 0 && q[n - 1] == ' ')
        n--;
    q[n] = 0;
    for (i = 0; i < n; i++) {
        if (q[i] >= 'a' && q[i] <= 'z') lower++;
        if (q[i] >= 'A' && q[i] <= 'Z') upper++;
    }
    if (upper > 0 && lower == 0)
        for (i = 0; i < n; i++)
            if (q[i] >= 'A' && q[i] <= 'Z') q[i] += 'a' - 'A';
    return n;
}

/* ================================================================ screen */

#define CHAT_L 3                /* chat window, inside its frame */
#define CHAT_T 3
#define CHAT_R 56
#define CHAT_B 20
#define CHATW  (CHAT_R - CHAT_L + 1)
#define MON_X  61               /* neural monitor panel          */
#define IN_Y   23

#define C_FRAME   11
#define C_FRAME2  3
#define C_TITLE   0x1B
#define C_TITLE2  0x1F
#define C_PANEL   0x3F
#define C_YOU     13
#define C_QTEXT   14
#define C_TINY    11
#define C_ANSWER  10
#define C_GLOW    15
#define C_SPEAK   0x2F
#define C_DIM     8
#define C_TEXT    7
#define C_BRIGHT  15
#define C_BAR     0x30
#define C_BARKEY  0x3F
#define C_BARON   0x3E
#define C_HOT     12

static int theme, sound_on = 1, voice_on = 1, demo, dump, dumpq = 4, shots, keytest;
static int chat_x = 1, chat_y = 1;
static char last_q[MAXQ + 1];
static s32 act_max[MAXL];
static int heat[18], heat_n;
static long speed10;
static int pend = -1;                   /* a key read early (while thinking) */

static void color(int a) { textattr(a); }
static void delay(unsigned ms) { if (!dump) sys_delay(ms); else sys_flush(); }
static int kbhit(void) { return pend >= 0 || sys_kbhit(); }
static int key(void)
{
    int k;
    if (pend >= 0) { k = pend; pend = -1; return k; }
    return sys_getch();
}

static void status_bar(const char *right);

static void next_theme(void)
{
    theme = (theme + 1) % NTHEME;
    sys_theme(theme);
}

/* while the network works: F5 now, any other key kept for later */
static void poll_keys(void)
{
    while (sys_kbhit()) {
        int k = sys_getch();
        if (k == K_F5) next_theme();
        else if (pend < 0) pend = k;
    }
}

/* ---- sound (TINY.C's calls; sys_tone plays or just waits) ---- */

static void tone(unsigned hz, unsigned ms)
{
    if (!sound_on) return;
    if (!dump) sys_tone(hz, ms);
}

static void quiet(unsigned ms) { delay(ms); }

static void sweep(unsigned from, unsigned to, unsigned ms)
{
    unsigned t;
    for (t = 0; t < ms; t += 3)
        tone((unsigned)(from + ((long)to - (long)from) * (long)t / (long)ms), 3);
}

static void snd_boot(void)
{
    static unsigned notes[] = { 392, 523, 659, 784, 1047, 1319 };
    int i;
    for (i = 0; i < 6; i++) tone(notes[i], 55);
    tone(1568, 140);
}

static void snd_ready(void)  { tone(1319, 40); tone(1760, 70); }
static void snd_click(void)  { tone(1800, 2); }
static void snd_error(void)  { tone(140, 120); }
static void snd_bye(void)    { sweep(1600, 200, 360); }

static void buzz(unsigned f1, unsigned f2, unsigned ms, unsigned shift)
{
    f1 = (unsigned)((long)f1 * shift / 100);
    f2 = (unsigned)((long)f2 * shift / 100);
    (void)f2;
    if (!sound_on) { delay(ms); return; }
    tone(f1, ms);
}

static void hiss(unsigned ms)
{
    if (!sound_on) { delay(ms); return; }
    tone(2500 + ui_rand() % 4500, ms);
}

static void say_letter(int c, unsigned shift)
{
    if (c >= 'A' && c <= 'Z') c += 32;
    switch (c) {
    case 'a': buzz(730, 1090, 64, shift); break;
    case 'e': buzz(530, 1840, 56, shift); break;
    case 'i': case 'y': buzz(300, 2290, 52, shift); break;
    case 'o': buzz(570, 840, 64, shift); break;
    case 'u': buzz(320, 870, 56, shift); break;
    case 's': case 'z': case 'c': case 'x': hiss(46); break;
    case 'f': case 'h': case 'v': case 'j': hiss(30); break;
    case 'p': case 't': case 'k': case 'q':
        tone(3200, 2); quiet(18); break;
    case 'b': case 'd': case 'g':
        buzz(180, 260, 12, shift); quiet(10); break;
    case 'm': case 'n': buzz(210, 300, 40, shift); break;
    case 'l': case 'r': case 'w': buzz(400, 1100, 36, shift); break;
    case ' ': quiet(45); break;
    case ',': case '-': quiet(110); break;
    case '.': case '!': case '?': case ':': quiet(200); break;
    default:
        if (c >= '0' && c <= '9')
            buzz(420 + (c - '0') * 60, 900 + (c - '0') * 90, 70, shift);
        else
            quiet(20);
    }
}

/* ---- drawing helpers ---- */

static void full(void) { window(1, 1, SCRW, 25); }

static void hline(int x, int y, int n, int ch)
{
    gotoxy(x, y);
    while (n-- > 0) putch(ch);
}

static void put_at(int x, int y, int attr, const char *s)
{
    gotoxy(x, y);
    color(attr);
    cputs(s);
}

static void box(int x1, int y1, int x2, int y2, int attr, const char *title)
{
    int y;
    color(attr);
    gotoxy(x1, y1); putch(201); hline(x1 + 1, y1, x2 - x1 - 1, 205); putch(187);
    for (y = y1 + 1; y < y2; y++) {
        gotoxy(x1, y); putch(186);
        gotoxy(x2, y); putch(186);
    }
    gotoxy(x1, y2); putch(200); hline(x1 + 1, y2, x2 - x1 - 1, 205);
    gotoxy(x2, y2); putch(188);
    if (title) {
        put_at(x1 + 2, y1, attr, "\xB5");
        put_at(x1 + 3, y1, C_PANEL, title);
        color(attr);
        putch(198);
    }
}

static void meter(int x, int y, int w, int v, int attr)
{
    int i, n = (int)((long)v * w * 2 / 1000);
    gotoxy(x, y);
    for (i = 0; i < w; i++) {
        if (n >= 2 * i + 2) { color(attr); putch(219); }
        else if (n == 2 * i + 1) { color(attr); putch(221); }
        else { color(C_DIM); putch(250); }
    }
}

static char status_right[16];

static void status_bar(const char *right)
{
    char buf[20];
    full();
    color(C_BAR);
    gotoxy(1, 25);
    cputs(" ");
    color(C_BARKEY); cputs("F1"); color(C_BAR); cputs(" Help  ");
    color(C_BARKEY); cputs("F2"); color(C_BAR); cputs(" Sound ");
    color(C_BARON); cputs(sound_on ? "ON " : "OFF"); color(C_BAR); cputs("  ");
    color(C_BARKEY); cputs("F3"); color(C_BAR); cputs(" Voice ");
    color(C_BARON); cputs(voice_on ? "ON " : "OFF"); color(C_BAR); cputs("  ");
    color(C_BARKEY); cputs("F4"); color(C_BAR); cputs(" Creativity ");
    color(C_BARON); putch('0' + creativity); color(C_BAR); cputs("  ");
    color(C_BARKEY); cputs("Esc"); color(C_BAR); cputs(" Quit ");
    if (right) { strncpy(status_right, right, 15); status_right[15] = 0; }
    sprintf(buf, "%-15.15s", status_right);
    cputs(buf);                                 /* ends at column 79 */
}

static void title_bar(void)
{
    char buf[12];
    int h, m;
    full();
    color(C_TITLE);
    hline(1, 1, SCRW, ' ');
    put_at(2, 1, C_TITLE, "\x11\x10");
    put_at(5, 1, C_TITLE2, "T I N Y");
    put_at(13, 1, C_TITLE, "\xFA  NEURAL INTERFACE 2.1  \xFA  367,000 WEIGHTS");
    if (dump) h = 12, m = 0;               /* tests compare whole screens */
    else sys_gettime(&h, &m);
    sprintf(buf, "%02d:%02d", h, m);
    put_at(62, 1, 0x1A, "\xFE");
    put_at(64, 1, C_TITLE, "ONLINE");
    put_at(73, 1, C_TITLE2, buf);
}

/* ---- the neural monitor (right panel) ---- */

static void monitor_static(void)
{
    int l;
    char buf[8];
    put_at(MON_X, 3, C_DIM, "LAYER ACTIVITY");
    for (l = 0; l < L && l < 6; l++) {
        sprintf(buf, "L%d", l + 1);
        put_at(MON_X, 4 + l, C_FRAME, buf);
        meter(MON_X + 3, 4 + l, 15, 0, C_ANSWER);
    }
    put_at(MON_X, 10, C_DIM, "CONFIDENCE");
    meter(MON_X, 11, 13, 0, C_ANSWER);
    put_at(MON_X, 12, C_DIM, "LAST LETTERS");
    put_at(MON_X, 14, C_DIM, "SPEED");
    put_at(MON_X, 16, C_DIM, "NETWORK");
    sprintf(buf, "%d", L);
    put_at(MON_X, 17, C_TEXT, buf); cputs(" layers \xFA ");
    sprintf(buf, "%d", D); cputs(buf); cputs(" wide");
    put_at(MON_X, 19, C_DIM, "MODE");
}

static void monitor_live(void)
{
    int l, v, i;
    char buf[20];
    full();
    for (l = 0; l < L && l < 6; l++) {
        s32 a = tiny_layer_act[l];
        if (prefill && l == L - 1) continue;    /* not computed while reading */
        if (a > act_max[l]) act_max[l] = a;
        v = act_max[l] > 0 ? (int)((long)(a >> 4) * 1000L / (long)((act_max[l] >> 4) + 1)) : 0;
        meter(MON_X + 3, 4 + l, 15, v, l % 2 ? C_FRAME : C_ANSWER);
        act_max[l] -= act_max[l] / 200;         /* TINY.C: *= 0.995 */
    }
    if (!prefill) {
        v = conf1000;
        meter(MON_X, 11, 13, v, v > 800 ? C_ANSWER : v > 500 ? C_QTEXT : C_HOT);
        sprintf(buf, " %3d%%", v / 10);
        put_at(MON_X + 13, 11, C_BRIGHT, buf);
    }
    gotoxy(MON_X, 13);
    for (i = 0; i < 18; i++) {
        if (i >= heat_n) { color(C_DIM); putch(250); continue; }
        v = heat[i];
        color(v > 800 ? C_ANSWER : v > 500 ? C_QTEXT : C_HOT);
        putch(v > 900 ? 219 : v > 700 ? 178 : v > 450 ? 177 : 176);
    }
    sprintf(buf, "%ld.%ld letters/s  ", speed10 / 10, speed10 % 10);
    put_at(MON_X, 15, C_TEXT, buf);
    sprintf(buf, "creativity %d     ", creativity);
    put_at(MON_X, 20, C_TEXT, buf);
}

/* ---- frame ---- */

static void frame(void)
{
    full();
    color(C_TEXT);
    clrscr();
    title_bar();
    box(1, 2, 58, 21, C_FRAME, " CONVERSATION ");
    box(59, 2, 80, 21, C_FRAME2, " NEURAL MONITOR ");
    box(1, 22, 80, 24, C_FRAME2, " ASK TINY ");
    put_at(3, IN_Y, C_YOU, "\x10");
    put_at(59, 22, C_FRAME2, "\xD1");
    put_at(59, 24, C_FRAME2, "\xCF");
    put_at(59, IN_Y, C_FRAME2, "\xB3");
    put_at(62, IN_Y, C_DIM, "\x18 last  F5 theme");
    monitor_static();
    monitor_live();
    status_bar(NULL);
}

/* ---- chat window ---- */

static void chat_win(void)
{
    window(CHAT_L, CHAT_T, CHAT_R, CHAT_B);
    gotoxy(chat_x, chat_y);
}

static void chat_save(void)
{
    chat_x = wherex();
    chat_y = wherey();
    full();
}

static void chat_nl(void) { cputs("\r\n"); }

static void chat_room(int lines)
{
    int h = CHAT_B - CHAT_T + 1, y = wherey(), x = wherex();
    while (y > h - lines + 1 && y > 1) {
        gotoxy(1, h);
        cputs("\n");
        y--;
    }
    gotoxy(x, y);
}

static void chat_text(int attr, const char *s, int indent)
{
    int i;
    color(attr);
    for (i = 0; s[i]; i++) {
        if (wherex() >= CHATW) {
            chat_nl();
            gotoxy(indent, wherey());
        }
        putch(s[i]);
    }
}

/* ---- boot screen ---- */

static const char *logo[5] = {
    "#####  ###  #   #  #   #",
    "  #     #   ##  #   # # ",
    "  #     #   # # #    #  ",
    "  #     #   #  ##    #  ",
    "  #    ###  #   #    #  "
};
static int logo_col[5] = { 11, 11, 9, 13, 13 };
static int boot_y;
#define LOGO_X 16
#define LOGO_Y 2

static int wait_or_key(unsigned ms)
{
    if (dump) return 0;
    delay(ms);
    if (kbhit()) { key(); return 1; }
    return 0;
}

static void rain(void)
{
    int col[SCRW], spd[SCRW], f, x, y;
    for (x = 0; x < SCRW; x++) { col[x] = -(ui_rand() % 25); spd[x] = 1 + ui_rand() % 2; }
    for (f = 0; f < 46; f++) {
        for (x = 0; x < SCRW - 1; x += 2) {
            y = col[x];
            if (y >= 1 && y <= 25) {
                gotoxy(x + 1, y);
                color(f < 40 ? C_ANSWER : C_DIM);
                putch(33 + ui_rand() % 90);
            }
            if (y + 1 >= 1 && y + 1 <= 25) {
                gotoxy(x + 1, y + 1);
                color(C_BRIGHT);
                putch(33 + ui_rand() % 90);
            }
            if (y - 7 >= 1 && y - 7 <= 25) {
                gotoxy(x + 1, y - 7);
                putch(' ');
            }
            col[x] += spd[x];
            if (col[x] > 32) col[x] = -(ui_rand() % 10);
        }
        if (f % 4 == 0) tone(3000 + ui_rand() % 3000, 2);
        if (wait_or_key(28)) break;
    }
    color(C_TEXT);
    clrscr();
}

static void logo_in(void)
{
    static unsigned char done[5][24];
    int i, k, y, x, n = 0, total = 0;
    for (y = 0; y < 5; y++)
        for (x = 0; x < 24; x++) {
            done[y][x] = 0;
            if (logo[y][x] == '#') total++;
        }
    for (k = 0; n < total && k < 400; k++) {
        for (i = 0; i < 5; i++) {
            y = ui_rand() % 5; x = ui_rand() % 24;
            if (logo[y][x] != '#' || done[y][x]) continue;
            done[y][x] = 1; n++;
            if (!(y < 4 && x < 23 && logo[y + 1][x + 1] == '#')) {
                gotoxy(LOGO_X + 2 * x + 2, LOGO_Y + y + 1);
                color(1);
                putch(177); putch(177);
            }
            gotoxy(LOGO_X + 2 * x, LOGO_Y + y);
            color(logo_col[y]);
            putch(219); putch(219);
        }
        for (i = 0; i < 6; i++) {
            y = ui_rand() % 5; x = ui_rand() % 24;
            if (logo[y][x] != '#' || done[y][x]) continue;
            gotoxy(LOGO_X + 2 * x, LOGO_Y + y);
            color(C_DIM);
            putch(176 + ui_rand() % 3); putch(176 + ui_rand() % 3);
        }
        if (k % 3 == 0) tone(200 + n * 12, 3);
        delay(12);
    }
    for (y = 0; y < 5; y++)
        for (x = 0; x < 24; x++)
            if (logo[y][x] == '#') {
                gotoxy(LOGO_X + 2 * x, LOGO_Y + y);
                color(logo_col[y]);
                putch(219); putch(219);
            }
    for (x = 0; x < 26 && !dump; x++) {
        for (y = 0; y < 5; y++) {
            if (x < 24 && logo[y][x] == '#') {
                gotoxy(LOGO_X + 2 * x, LOGO_Y + y);
                color(C_BRIGHT); putch(219); putch(219);
            }
            if (x >= 2 && logo[y][x - 2] == '#') {
                gotoxy(LOGO_X + 2 * (x - 2), LOGO_Y + y);
                color(logo_col[y]); putch(219); putch(219);
            }
        }
        delay(14);
    }
}

static void boot_bar(int done, int total)
{
    int i, n = (int)(36L * done / total);
    char buf[24];
    full();
    gotoxy(22, 15);
    color(C_FRAME);
    putch(222);
    for (i = 0; i < 36; i++) {
        color(i < n ? (i * 3 < n ? C_FRAME : C_ANSWER) : C_DIM);
        putch(i < n ? 219 : 250);
    }
    color(C_FRAME);
    putch(221);
    sprintf(buf, " %3d%%", (int)(100L * done / total));
    color(C_BRIGHT);
    cputs(buf);
    if (done == 2 || (done > 2 && (done - 2) % 6 == 0)) {
        if (done == 2) sprintf(buf, "letters + places");
        else sprintf(buf, "layer %d of %d", (done - 2) / 6, L);
        gotoxy(26, boot_y);
        color(C_YOU); cputs("\x10 ");
        color(C_TEXT); cputs(buf);
        cputs(" ");
        i = wherex();
        color(C_DIM); hline(i, boot_y, 50 - i, 250);
        color(C_ANSWER); cputs(" ONLINE");
        boot_y++;
        tone(880 + 110 * (boot_y % 6), 12);
    }
    sys_flush();
}

/* The model file, read in 2 + 6 L pieces so the bar moves like TINY.C's
   (one step per weight table). */
static const char *load_model(const char *path)
{
    FILE *f = fopen(path, "rb");
    long size, got = 0, want;
    int total, done = 0;
    u8 *blob;
    s8 *kv;
    if (!f) return "cannot open tiny.t68";
    fseek(f, 0, SEEK_END); size = ftell(f); fseek(f, 0, SEEK_SET);
    blob = (u8 *)malloc(size);
    if (!blob) { fclose(f); return "not enough memory for the model"; }
    if (fread(blob, 1, 16, f) != 16) { fclose(f); return "tiny.t68 is too short"; }
    got = 16;
    D = blob[6] << 8 | blob[7];
    L = blob[8] << 8 | blob[9];
    if (L < 1 || L > MAXL) { fclose(f); return "not a TINY model"; }
    total = 2 + 6 * L;
    while (got < size) {
        done++;
        want = size * done / total - got;
        if (done == total) want = size - got;
        if (want > 0 && fread(blob + got, 1, (size_t)want, f) != (size_t)want) {
            fclose(f); return "tiny.t68 is too short";
        }
        if (want > 0) got += want;
        boot_bar(done, total);
    }
    fclose(f);
    kv = (s8 *)malloc(tiny_kv_bytes(blob));
    if (!kv) return "not enough memory for the key/value cache";
    return tiny_init(blob, size, kv);
}

static const char *boot(void)
{
    int x;
    const char *err;
    color(C_TEXT);
    clrscr();
    sys_cursor(0);
    rain();
    logo_in();
    put_at(24, 8, C_BRIGHT, "N E U R A L    I N T E R F A C E");
    put_at(29, 9, C_DIM, "version 2.1  \xFA  Amiga 500");
    color(C_FRAME2);
    hline(10, 11, 60, 196);
    put_at(22, 13, C_FRAME, "LOADING NEURAL WEIGHTS FROM TINY.T68");
    boot_y = 17;
    sys_flush();
    err = load_model("tiny.t68");
    if (err) return err;
    sys_takeover();
    snd_boot();
    for (x = 0; x < 6; x++) {
        put_at(30, boot_y + 1, x % 2 ? C_BRIGHT : C_ANSWER, "\x10 SYSTEM  READY \x11");
        delay(120);
    }
    if (shots) sys_shot("boot");
    return NULL;
}

/* ---- help ---- */

static void show_help(void)
{
    static const char *h[] = {
        "Ask a question and press Enter.",
        "Try: capital of peru \xFA is a whale a fish",
        "     what is 7 times 8 \xFA spell elephant",
        "     which countries border estonia",
        "     how do you say thank you in spanish",
        "F2 sound \xFA F3 voice \xFA F4 creativity \xFA F5 colours",
        "\x18 last question \xFA Esc quit \xFA CLS clears",
        NULL
    };
    int i;
    chat_win();
    if (wherex() > 1) chat_nl();
    chat_room(9);
    color(C_PANEL); cputs(" HELP "); chat_nl();
    for (i = 0; h[i]; i++) {
        color(C_TEXT); cputs(" "); cputs(h[i]); chat_nl();
    }
    chat_save();
}

/* ---- asking ---- */

static int ans_x0, ans_y0, ans_col;
static int spin, scr_prev;

static void ans_pos(int i, int *sx, int *sy)
{
    int per = CHATW - ans_x0;
    *sx = CHAT_L + ans_x0 - 1 + i % per;
    *sy = CHAT_T + ans_y0 - 1 + i / per;
}

static void ui_step(int i, int n)
{
    int k, head, sx, sy, d;
    full();
    head = spin % 18;
    if (head >= 9) head = 17 - head;
    ans_pos(0, &sx, &sy);
    gotoxy(sx, sy);
    for (k = 0; k < 10; k++) {
        d = head - k; if (d < 0) d = -d;
        color(d == 0 ? C_BRIGHT : d == 1 ? C_YOU : d == 2 ? 5 : C_DIM);
        putch(d == 0 ? 219 : d == 1 ? 178 : d == 2 ? 177 : 250);
    }
    color(C_DIM);
    cprintf(" reading %d%%", (int)(100L * (i + 1) / n));
    spin++;
    prefill = 1;
    monitor_live();
    if (i % 3 == 0) tone(2400 + (ui_rand() % 8) * 150, 3);
    sys_flush();
    poll_keys();
}

static void ui_letter(int c)
{
    int x, y;
    prefill = 0;
    conf1000 = (int)(((long)tiny_confidence(c - 30) * 1000L) >> 16);
    full();
    if (ans_col == 0) {
        ans_pos(0, &x, &y);
        gotoxy(x, y);
        color(C_TEXT);
        cputs("                       ");
    } else {
        ans_pos(ans_col - 1, &x, &y);
        gotoxy(x, y);
        color(C_ANSWER);
        putch(scr_prev);
    }
    ans_pos(ans_col, &x, &y);
    if (c != ' ') {
        gotoxy(x, y);
        color(C_FRAME);
        putch(33 + ui_rand() % 90);
        delay(10);
    }
    gotoxy(x, y);
    color(C_GLOW);
    putch(c);
    scr_prev = c;
    ans_col++;
    if (heat_n < 18)
        heat[heat_n++] = conf1000;
    else {
        int k;
        for (k = 0; k < 17; k++) heat[k] = heat[k + 1];
        heat[17] = conf1000;
    }
    monitor_live();
    if (!voice_on) tone(900 + (c % 16) * 70, 6);
    sys_flush();
    poll_keys();
}

static void speak(const char *a)
{
    int i, j, k, sx, sy, n = (int)strlen(a), shot = shots;
    unsigned shift;
    full();
    for (i = 0; i < n; i = j) {
        for (j = i; j < n && a[j] != ' '; j++) ;
        for (; j < n && a[j] == ' '; j++) ;
        color(C_SPEAK);
        for (k = i; k < j && a[k] != ' '; k++) {
            ans_pos(k, &sx, &sy);
            gotoxy(sx, sy);
            putch(a[k]);
        }
        if (shot) { sys_shot("speak"); shot = 0; }
        shift = (unsigned)(115 - 30L * i / (n + 1));
        for (k = i; k < j; k++) {
            if (kbhit()) { shift = 0; break; }
            say_letter(a[k], shift);
        }
        color(C_ANSWER);
        for (k = i; k < j && a[k] != ' '; k++) {
            ans_pos(k, &sx, &sy);
            gotoxy(sx, sy);
            putch(a[k]);
        }
        if (shift == 0) break;
    }
}

static void ask(const char *q)
{
    static char out[CTX + 1];
    char info[40];
    long t0, tenths;
    int letters, sx, sy;

    chat_win();
    if (wherex() > 1) chat_nl();
    if (wherey() > 1) chat_nl();
    chat_room(6);
    color(C_YOU); cputs(" YOU \xAF ");
    chat_text(C_QTEXT, q, 8);
    chat_nl();
    color(C_TINY); cputs("TINY \xAF ");
    ans_x0 = wherex();
    ans_y0 = wherey();
    ans_col = 0;
    heat_n = 0;
    chat_save();
    sys_cursor(0);
    status_bar("thinking...");
    sys_flush();
    tiny_on_step = ui_step;
    tiny_on_letter = ui_letter;
    t0 = sys_ticks();
    letters = tiny_think(q, out, creativity, dump ? 2024UL : (u32)t0 ^ (u32)ui_rand());
    tenths = dump ? 0 : (sys_ticks() - t0) / 5;
    prefill = 0;
    if (letters == 0) { ui_letter('.'); out[0] = '.'; out[1] = 0; letters = 1; }
    ans_pos(ans_col - 1, &sx, &sy);
    gotoxy(sx, sy); color(C_ANSWER); putch(scr_prev);
    speed10 = tenths > 0 ? 100L * letters / tenths : 0;
    sprintf(info, "%d ltr %ld.%lds", letters, tenths / 10, tenths % 10);
    status_bar(info);
    monitor_live();
    if (sound_on && voice_on) {
        tone(1200, 8);
        speak(out);
    } else
        snd_ready();
    chat_x = 1;
    chat_y = sy - CHAT_T + 2;
    chat_win();
    chat_save();
    title_bar();
    sys_cursor(1);
}

/* ---- typing ---- */

static void draw_input(const char *buf, int n)
{
    int w = 54;
    full();
    gotoxy(5, IN_Y);
    color(C_BRIGHT);
    cputs(n > w ? buf + n - w : buf);
    color(C_TEXT);
    hline(5 + (n > w ? w : n), IN_Y, 55 - (n > w ? w : n), ' ');
    gotoxy(5 + (n > w ? w : n), IN_Y);
}

static int read_line(char *buf)
{
    int n = 0, c;
    buf[0] = 0;
    draw_input(buf, 0);
    for (;;) {
        c = key();
        if (c == K_ENTER) return n;
        if (c == K_ESC) return -1;
        if (c == K_BS) {
            if (n > 0) { buf[--n] = 0; snd_click(); }
        } else if (c == K_UP) {
            strcpy(buf, last_q);
            n = (int)strlen(buf);
        } else if (c == K_F1) {
            show_help();
        } else if (c == K_F2) {
            sound_on = !sound_on;
            snd_click();
        } else if (c == K_F3) {
            voice_on = !voice_on;
            snd_click();
        } else if (c == K_F4) {
            creativity = (creativity + 1) % 10;
            snd_click();
            monitor_live();
        } else if (c == K_F5) {
            next_theme();
            status_bar(theme_name[theme]);
            snd_click();
        } else if (c >= 32 && c <= 126) {
            if (n < MAXQ) { buf[n++] = (char)c; buf[n] = 0; snd_click(); }
            else snd_error();
        }
        status_bar(NULL);
        draw_input(buf, n);
    }
}

/* ---- demo mode ---- */

static const char *demo_q[] = {
    "what is the capital of estonia", "is a whale a fish", "what is 7 times 8",
    "which countries border estonia", "spell computer", "what is the symbol for gold",
    "how do you say thank you in spanish", "what is a group of lions called",
    "what comes after 199", "what time is it", "tell me a joke",
    "name the continents", NULL
};

static void demo_type(const char *q)
{
    char buf[MAXQ + 1];
    int n;
    for (n = 0; q[n]; n++) {
        buf[n] = q[n];
        buf[n + 1] = 0;
        draw_input(buf, n + 1);
        snd_click();
        delay(45 + ui_rand() % 50);
    }
    delay(250);
    draw_input("", 0);
}

static int ui_main(void)
{
    static char line[MAXQ + 1], q[MAXQ + 1];
    const char *err;
    int i, n;

    err = sys_init(theme);
    if (err) { printf("TINY: %s\n", err); return 1; }
    err = boot();
    if (err) { sys_done(); printf("TINY: %s\n", err); return 1; }
    if (!demo && !dump) {
        put_at(29, boot_y + 3, C_DIM, "press any key to begin");
        sys_flush();
        if (key() == K_ESC) { sys_done(); return 0; }
    }
    sweep(300, 1400, 120);
    frame();
    sys_cursor(1);
    chat_win();
    color(C_DIM);
    cputs("Hello! I am TINY, a neural network in your computer.");
    chat_nl();
    cputs("Ask about countries, animals, space, words or sums.");
    chat_nl();
    chat_save();
    snd_ready();

    if (demo || (dump && !keytest)) {
        for (i = 0; demo_q[i]; i++) {
            if (kbhit()) { key(); break; }
            demo_type(demo_q[i]);
            ask(demo_q[i]);
            if (shots && i == 0) { show_help(); sys_shot("help"); }
            if (dump && i == dumpq - 1) break;
            delay(1500);
        }
        if (dump) {
            sys_shot("screen");
            sys_done();
            return 0;
        }
    }
    for (;;) {
        n = read_line(line);
        if (n < 0) break;
        draw_input("", 0);
        if (clean(line, q) == 0) continue;
        if (is_word(q, "quit") || is_word(q, "exit")) break;
        if (is_word(q, "cls")) {
            frame();
            chat_x = chat_y = 1;
            continue;
        }
        if (is_word(q, "help")) { show_help(); continue; }
        strcpy(last_q, q);
        ask(q);
    }
    if (keytest) sys_shot("screen");
    status_bar("goodbye");
    sys_flush();
    snd_bye();
    sys_done();
    if (keytest) printf("ticks %ld\n", sys_ticks());     /* the 50 Hz timer ran */
    printf("TINY: goodbye!\n");
    return 0;
}

int main(int argc, char **argv)
{
    int i;
    for (i = 1; i < argc; i++) {
        if (argv[i][0] >= '0' && argv[i][0] <= '9')
            creativity = argv[i][0] - '0';
        else if (is_word(argv[i], "/demo") || is_word(argv[i], "-demo"))
            demo = 1;
        else if (is_word(argv[i], "/dump"))
            dump = 1;
        else if (is_word(argv[i], "/dump1"))      /* tests: one question */
            dump = 1, dumpq = 1;
        else if (is_word(argv[i], "/keytest"))    /* tests: a /dump driven by keys */
            dump = keytest = 1;
        else if (is_word(argv[i], "/shots"))
            dump = shots = 1;
        else if (is_word(argv[i], "/quiet") || is_word(argv[i], "-quiet"))
            sound_on = 0;
        else if (is_word(argv[i], "/wb13")) theme = 0;
        else if (is_word(argv[i], "/wb20")) theme = 1;
        else if (is_word(argv[i], "/neon")) theme = 2;
    }
    return ui_main();
}
