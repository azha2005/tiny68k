# GUI.md: the TINY interface on the Amiga (stage 4 plan)

TINY.C's DOS interface (80x25 text, neon palette, live neural monitor, digital
rain boot, PC-speaker robot voice) brought to an A500 with KS 1.2. This is a
plan, not code yet. The pitfalls cited as Pnn are from the SMW port
(`../smw claude pa/smw-amiga-m/AGENTS.md` section 8), where they were learned the
hard way.

## 1. Display: hires 640x200, 2 bitplanes, Workbench colours

- **80x25 cells of 8x8 = 640x200 hires**, centred in the 256 PAL lines.
  Non-interlaced hires pixels are about twice as tall as they are wide, so an
  8x8 cell looks like the VGA 8x16 one: same proportions as the screenshot.
- **2 bitplanes (4 colours), not 4.** The 68000 only uses every other bus slot,
  and up to 2 hires planes leave those slots free: zero CPU loss. 3-4 hires
  planes take the CPU's slots during the visible area and the network would run
  2-3 times slower. The A501 slow RAM does not escape this, since it is on
  the chip bus (P29).
- Bitplanes: 2 x 16,000 bytes of chip RAM, 8-byte aligned (P4).

### Palette themes: 4 colour registers each, switchable at runtime (F5)

| theme | pen 0 (bg) | pen 1 | pen 2 | pen 3 (accent) |
|---|---|---|---|---|
| **Workbench 1.3** (default) | `$05A` blue | `$FFF` white | `$002` black | `$F80` orange |
| Workbench 2.0 | `$AAA` grey | `$000` black | `$FFF` white | `$68B` blue |
| Neon (optional) | `$001` navy | `$4FA` mint | `$0CF` cyan | `$F5A` pink, with copper palette changes per zone |

A theme switch writes 4 registers (or the copper list), so it costs nothing.
Only the neon theme needs copper changes, between the conversation panel and
the monitor and per row (every row of the original uses 4 colours or fewer).
Copper lists in chip RAM, 4-byte aligned (P3). Never put a WAIT at line 255
with h >= `$E0` (P59).

### Mapping TINY's 16 text attributes to 4 pens

TINY.C names its colours by role (C_FRAME, C_YOU, C_ANSWER, C_DIM, C_BAR...).
Each theme gets a role -> (fg pen, bg pen) table. Bars and panel titles are
inverse video (pens swapped), which is the Workbench look anyway. To decide
with a mockup before writing code.

## 2. Software: a conio for the Amiga, the UI code nearly unchanged

The whole TINY.C UI is written against the Turbo C conio subset:
`textattr gotoxy wherex wherey putch cputs cprintf clrscr window gettext
kbhit getch delay sound nosound gettime gettextinfo textmode`.
Plan: `conio68k.c` implements exactly that subset over an 80x25 cell buffer
(char + attribute), and the UI functions are ported from TINY.C with minimal
changes (`tinyui.c`). The UI then behaves like the original by construction,
and TINY's own `/dump` (SCREEN.DMP = the cell buffer) becomes a test: the
Amiga cell buffer must equal the DOS one for the same demo run.

Licence caveat: TINY.C has no licence. `tinyui.c` is derived from it, so this
repository stays private unless the author agrees.

### Text renderer

- Dirty-cell renderer: a cell = 8 bytes per plane. Fg/bg pens are expanded to
  per-plane masks, `plane = (glyph & fg) | (~glyph & bg)`. **In asm**: generic C
  drawing compiled by vbcc was 30x slower in SMW (P54). A full redraw (2000
  cells) is about 0.05 s, and a letter touches a few dozen cells.
- No blitter for text: a blit costs ~42 us of setup (P31), more than drawing
  an 8x8 cell with the CPU.

### Font

ASCII from the ROM's topaz 8, read at runtime with `OpenFont`. It looks
authentically Amiga and copies nothing. The CP437 glyphs TINY uses (box
drawing 179-218, shades 176-178, blocks 219-223, arrows 16/17/24, 175 », 250
·, 254 ■) are drawn by us in the same style, as an 8x8 table in the source.

## 3. Input and OS

System-friendly, not a hardware takeover: an Intuition custom screen (hires, 2
planes) with a borderless backdrop window. The engine still needs AmigaDOS to
load `tiny.t68`, and nothing here needs more than that. Keys arrive via IDCMP
RAWKEY. VANILLAKEY does not exist on KS 1.2, so a US keymap table translates
them. F1-F5, Esc, Up and Backspace are raw codes anyway. The neon theme's copper
changes go in a user copper list (`UCopList`, available on 1.2).

## 4. Sound: Paula instead of the PC speaker

- tone(hz, ms): a 32-byte square-wave sample in chip RAM, period =
  3546895 / (hz x 32). Samples are signed 8-bit (P7).
- buzz(f1, f2) (vowels): **both formants at once on two channels**. The PC
  speaker had to alternate them every 2 ms; Paula plays them together, so the
  robot voice sounds better than the original.
- hiss: a 256-byte noise sample at a random period.
- delay(): vertical blank ticks (20 ms) for animation and timer.device for
  short sound timings. Turbo C's ms delay() resolution is not reproducible, so
  rhythm is matched by ear.
- Optional, F3 cycles off / robot / speech: `narrator.device` and
  `translator.library` from the Workbench 1.2 disk give real speech. About 30
  KB of memory.

## 4b. Measured: the display is not free

`emu/bench.py` (cycle-exact): with the hires 2-plane screen on, the table
look-up loop runs 5.6% slower and the sparse loop 3% slower than with
bitplane DMA off. Options: blank or shrink the bitplane window while a
letter is being computed (the UI is idle then), and take over the OS
(interrupts, multitasking: ~5% of every loop) as amivideo does.

## 5. Reading while typing

The network is causal, so each letter of the question can go through step()
**while it is being typed**. Backspace rewinds the position and overwrites the
cache. A prefill letter costs ~2.0 s on a real A500 (round 3) and people
type ~3 letters a second, so this hides only the typing time (10-20 s of the
~54 s prefill of a 27-letter question), not the whole prefill. The numbers are unchanged: it is the same
sequence of step() calls, and verify.py still covers it.

Use `tiny_prefill()` for the typed letters (what `think()` does): it skips the
last layer after K/V and the scores, ~20% cheaper per letter. It leaves
`layer_act[L-1]` and `score[]` untouched, so the UI must not show those while
the user types. `clean()` depends on the whole line (all CAPS -> lower): on
Enter, compare the cleaned question with what was prefilled and re-run from
the first differing position. The separator step (token 1) is a full step.

## 6. Verification

1. The numerical engine stays behind `scripts/verify.py`. The UI never touches
   step(), and reads layer_act / confidence (already in the oracle).
2. A host build of `conio68k.c` + `tinyui.c` dumps the cell buffer, compared
   against TINY.EXE /dump (run in DOSBox) for the same demo questions.
3. WinUAE screenshot vs a host-rendered PNG of the same cell buffer. Visual
   checks can use the fast config, but **timing only counts in cycle-exact**
   (P30). Wait long enough before capturing (P41).
4. Check that `$C00000` responds before relying on slow RAM (P8).

## 7. Stage 3 note (from the same docs): running the real 68000 binary on the host

`unicorn` (2.1.4) and `machine68k` (Musashi) are installed here, and the SMW
port uses them in `tools/m68kverify.py` to run the vbcc-compiled 68000 code
against its oracle. This matters because **vbcc miscompiled C that gcc
compiled correctly**, several times (P38, P47, P62), and gcc-only
verification cannot see it. For tiny68k, stage 3 should load the kick13
binary's code (or a raw build of the engine) into Unicorn/Musashi, run
step() for the verify.py probes, and diff the scores. That also brings
`matmul68k.s` inside the verification loop and gives a lower bound on cycles
per letter (Musashi has no DMA waits; real numbers come from WinUAE
cycle-exact).
