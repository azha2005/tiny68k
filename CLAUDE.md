# TINY 2.1 on Amiga 500 (68000)

A port of TINY 2.1 (https://github.com/WollyMonjac/Tiny-2.1, a 367K-parameter
letter-by-letter question-answering transformer for DOS) to a 7.09 MHz 68000
with no FPU, in integer arithmetic. The method is copied from `../amiga260k`.
Read this before touching anything.

## The one rule

`oracle_tiny.py` is the specification. `tiny68k.c` (stage 2) is an
implementation of it. They must produce identical int32 scores, always.

Above the oracle sits the original: `orig/TINY.C`, built unmodified as
`ref/harness`. `ref_tiny.py` is checked against it, and the oracle is
measured against both.

## Gates

| Gate | Command | Must say |
|---|---|---|
| float reference = TINY.C | `python check_ref.py` | rel. error < 1e-3, 44/44 answers |
| integer oracle ~ float | `python cmp_fp32.py` | corr > 0.99, top-10 >= 8, 44/44 answers, PASS |
| C = oracle (stage 2) | `python scripts/verify.py` | OK, all scores identical |
| 68000 code = oracle (stage 3) | `python scripts/m68k_gate.py` | OK, both kernels |
| real executables on KS 1.2 | `python emu/run_test.py` | OK, asm vs C IDENTICAL |

Stage 1 results (2026-11-15): reference error 1.3e-6; oracle min correlation
0.99987, top-10 mean 9.96/10, argmax 772/772, greedy answers 44/44 identical
to TINY.C.

Stage 2 results (2026-11-15): verify.py OK. 19,493 scores and layer
activities over 3 probes identical, greedy answers 44/44 and sampled answers
(creativity 5 and 9) identical. Mutation check: changing the C's KV rounding
by one unit fails at position 36 of the long probe while every answer stays
the same, which is why the long probe exists. verify.py only builds with gcc.

Stage 3 results (2026-11-15): `m68k_gate.py` compiles the engine with vbcc
+kick13 (`-DTINY_ENGINE_ONLY`), links it raw at $10000 with vlink, and runs
it in Musashi with tiny.t68 in emulated memory. With `matmul68k.s` and with
the C kernel: 19,493 scores and layer activities identical, 6 greedy and 2
sampled answers identical. **This puts the asm kernel inside the
verification loop**, which amiga260k never had. Musashi cycles per letter
(no DMA waits, so a lower bound): asm kernel 35.7M at position 0 (5.0 s),
55.7M at position 96 (7.9 s); C kernel 97.6M (13.8 s). The estimate was
3.9 s: the difference is overhead outside the MACs, still to be profiled.

Optimisation round 1 (2026-11-15), all bit-exact, the oracle untouched:
`fxmul`, `mulshift`, the attention value pass and the dot products in asm
with vbcc `__reg` register calls; f2 skips the ReLU zeros (`matmul_q_sparse`,
~81% of its inputs); wk/wv/wq and f1 use **column tables** (`matmul_tab`:
`build_tab` precomputes w*x for all 256 int8 w per input column, then
`tab_dots` sums look-ups in 8 register accumulators, ~40 cycles per MAC
instead of ~76 with MULS). The weights of those tables are reordered at
`tiny_init` into 8-row column-major blocks, in place; the blob is then
marked `T68B`. Caller memory (`tiny_kv_bytes`) now also holds the tables,
D*1 KB. Musashi: position 0 5.03 -> 3.29 s, position 96 7.85 -> 4.81 s.
`scripts/asm_unit.py` tests each asm routine against its C fallback with
random/edge register arguments and checks d2-d7/a2-a6 are preserved.
`scripts/muls_bits.py`: as MULS source the int8 weights average 3.6 bit
transitions, the int16 activations 7.2 (MULS = 38+2n). Swapping operands
would save only ~4% net (one more load) and Musashi does not model it, so it
was not done; with the tables most MULS are gone anyway.

WinUAE (`emu/run_test.py`): it used to wait for a `done.txt` that KS 1.2
never wrote (no C: on the floppy, so no `echo`), and WinUAE throttled itself
while its window was inactive. Now it ends when every output has its timing
lines, fast mode disables inactive pause/priority drop, sound and most
frames, and it runs 1 question by default (`-n 2` for both): ~30 s for both
executables. `ticks()` read 0 because KS 1.x DateStamp() (BCPL) needs a
longword-aligned struct; fixed. Fast-mode times are not Amiga times; use
`--exact` for those. Never use JIT for this check: it needs a 68020, which
does not trap on odd-address accesses.

## matmul68k.s and vbcc

Unlike in amiga260k, **any change to `matmul68k.s` or to how vbcc compiles
the engine is checked by `scripts/m68k_gate.py`**. Run it after every change
to the C or the asm. It cannot see the OS side (file loading, malloc, the
hunk executable, KS 1.2): that is `emu/run_test.py` in WinUAE.

## Order of work for any numerical change

1. Change `oracle_tiny.py` (and `fx.py` if a primitive is involved) first.
2. `python cmp_fp32.py` must still PASS. Every greedy answer identical to
   TINY.C is part of the bar, not a nice-to-have.
3. Port the same change to `tiny68k.c`.
4. `python scripts/verify.py` must pass.

Never change the C first and then adjust the oracle to match it.

## Invariants (in addition to `../amiga260k/CLAUDE.md`, which all apply)

- `s32` is exactly 32 bits; no float, no double, no `long long` in the C.
- Every product decomposes into 16x16 multiplies.
- **Activations into a matmul are int16** with a power-of-two shift
  (`fx.quantize16`), not int8. The kernel `dot_i8_x16` multiplies an int8
  weight by a 16-bit word with one MULS either way, so this costs nothing and
  is what keeps the answers identical to the float original.
- **Use `fx.ms()`, never bare `mulshift()`** in the oracle: it enforces the
  shift range `[-15, 47]` and the uint16 mantissa. The C `mulshift` must
  support shifts 32..47 (amiga260k's stops at 31: extend it in stage 2).
- `chk()` in the oracle raises on s32 overflow. An overflow is a spec bug.
- Signed `>>` is arithmetic (floor) in Python, gcc and vbcc. Never use `//`
  on a value that can be negative.
- Constants that involve irrational numbers (1/sqrt(HD)) are computed once in
  `convert_tiny.py` and stored in the `.t68` header. Neither side recomputes.
- `.t68` is big-endian. Only the host build byte-swaps.

## Measured ranges the design depends on (`check_ref.py` prints them)

Residual stream |x| up to 170 (so x*x overflows: `rmsnorm_tiny` squares
`x >> k`); keys/values absmax up to ~6.4; scores up to ~12; ReLU outputs up
to ~14. A retrained or larger TINY.BIN must re-run `check_ref.py` and confirm
these still fit. The value-weight path assumes value absmax < 64.

## Files

| File | Role |
|---|---|
| `orig/` | upstream TINY.C, TINY.BIN, README.TXT (not in git, no licence) |
| `ref/harness.c` | the original TINY.C, included unmodified, as a host tool |
| `ref/answers_orig.txt` | TINY.C's greedy answers to `questions.txt` |
| `ref_tiny.py` | float reference + TINY.BIN reader |
| `fx.py` | Q16.16 primitives (amiga260k's, plus a TINY section) |
| `convert_tiny.py` | TINY.BIN -> `tiny.t68`, and the `.t68` reader |
| `oracle_tiny.py` | **the specification**: step(), pick(), think() |
| `check_ref.py` | ref_tiny vs TINY.C |
| `cmp_fp32.py` | oracle vs float, accuracy gate |
| `tiny68k.c` | the engine (stage 2): step(), pick(), think(), plain-text UI |
| `scripts/verify.py` | **the gate**: C == oracle (scores, layer activity, answers) |
| `GUI.md` | stage 4 plan: display, palettes, conio layer, sound, SMW lessons |
| `matmul68k.s` | 68000 hot routines: dot products, sparse dot, column tables, fxmul, mulshift (register calls) |
| `scripts/asm_unit.py` | each `matmul68k.s` routine vs its C fallback, register preservation |
| `scripts/muls_bits.py` | MULS operand bit-transition statistics on real data |
| `scripts/m68k_prof.py` | per-function cycle profile in Musashi |
| `scripts/m68k_gate.py` | vbcc-compiled engine in Musashi/Unicorn == oracle, + cycles |
| `emu/run_test.py` | the hunk executables in WinUAE, KS 1.2, 512K + 512K A501 |
| `build.py` | `ref convert check host verify amiga amiga-c gate uae` |

## Environment note

On this Windows machine gcc only runs with `C:\msys64\ucrt64\bin` first on
PATH; `build.py` sets that up. In Bash: `export PATH=/c/msys64/ucrt64/bin:$PATH`.
vbcc is in `C:\Users\JC\vbcc` (override with `VBCC`); its `bin` must be on
PATH because `vc` calls its sub-tools by name. The NDK headers are not
installed: declare any OS call by hand with vbcc's `__reg` inline syntax (see
`tiny_datestamp`). vbcc's kick13 `clock()` always reads 0. Heredocs in the
Bash tool break quotes and backslashes (SMW P56): write scripts with the
file tool.

## Roadmap

1. **Done**: float reference, converter, integer oracle, accuracy gate.
2. **Done**: `tiny68k.c` host build + `scripts/verify.py`.
3. **Done**: kick13 cross build (plain text, letters streamed as chosen),
   68000 gate in Musashi, executables on KS 1.2 in WinUAE.
   Optimisation round 1 done (3.29 s/letter at position 0 in Musashi).
   Next: cycle-exact timing per letter in WinUAE (`--exact`).
4. Amiga UI per `GUI.md`: hires 2 planes, Workbench palette themes, conio
   layer with TINY.C's UI code, Paula voice, reading while typing.
5. Optional: own data generator + trainer for a bigger model (see README).
