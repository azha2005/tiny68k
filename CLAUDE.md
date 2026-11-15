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

Stage 1 results (2026-11-15): reference error 1.3e-6; oracle min correlation
0.99987, top-10 mean 9.96/10, argmax 772/772, greedy answers 44/44 identical
to TINY.C.

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
| `build.py` | `ref`, `convert`, `check` |

## Environment note

On this Windows machine gcc only runs with `C:\msys64\ucrt64\bin` first on
PATH; `build.py` sets that up. In Bash: `export PATH=/c/msys64/ucrt64/bin:$PATH`.

## Roadmap

1. **Done**: float reference, converter, integer oracle, accuracy gate.
2. `tiny68k.c` host build + `scripts/verify.py` (probe: a question plus its
   answer, all scores, plus greedy answers to questions.txt).
3. kick13 cross build, plain text in a CON: window, `-c 0` A/B of the asm and
   C kernels in WinUAE, cycle-exact timing per letter.
4. Amiga UI: neon screen, neural monitor, Paula robot voice (or
   narrator.device), processing letters while the user types.
5. Optional: own data generator + trainer for a bigger model (see README).
