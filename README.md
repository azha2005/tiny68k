# tiny68k: TINY 2.1 on an Amiga 500

[TINY 2.1](https://github.com/WollyMonjac/Tiny-2.1) is a 367,000-parameter
transformer that answers questions letter by letter, written for DOS and
Turbo C with floating point. This project ports it to a 68000 at 7.09 MHz with
no FPU, in integer arithmetic, using the method of `../amiga260k`.

Status: **stage 1 done**. The integer specification gives the same answers as
the original on every test question. The C engine for the 68000 is next.

## Setup

TINY 2.1 has no licence, so its files are not in this repo. Copy `TINY.C`,
`TINY.BIN` and `README.TXT` from the upstream repository (commit in
`orig/UPSTREAM_COMMIT`) into `orig/`, then:

```
python build.py ref        # the original TINY.C as a host tool
python build.py convert    # TINY.BIN -> tiny.t68
python build.py check      # both stage 1 gates
```

## The model

From the TINY.BIN header: D=96, 4 layers, 4 heads of 24, ReLU feed-forward
of 256, learned positions (128), 97 letters, output layer tied to the
embedding. Weights are int8 with one scale per row; the key/value cache is int8
with one scale per vector.

## How the integer version works

- Weights stay int8, bit for bit. Each float row scale becomes a 16-bit
  mantissa and a shift.
- Activations are Q16.16 and enter each matmul as **int16** with a
  power-of-two shift. The 68000's MULS is 16x16 anyway, so this costs the same
  as int8 and keeps about 14 bits.
- RMSNorm squares `x >> k`, because the residual stream reaches 170 and
  170² does not fit Q16.16.
- The key/value cache uses the same int8 absmax rounding as TINY.C. Attention
  scores are int8·int16 dot products rescaled by a per-position (mantissa,
  shift), so all of attention runs through the same kernel as the matmuls.
- exp comes from a table in the model file, and `1/sqrt(24)` is a header
  constant.

## Accuracy (stage 1)

Over 44 questions and 772 answer positions with teacher forcing, compared to
float:

| | |
|---|---|
| logit correlation | min 0.99987, mean 0.99998 |
| top-10 overlap | min 9/10, mean 9.96/10 |
| argmax agreement | 772/772 |
| greedy answers identical to TINY.C | 44/44 |

## Expected cost on the 68000

353,376 int8 multiply-accumulates per letter, plus 768 per earlier letter in
attention. That is about 3.9 s per letter at the start and about 4.5 s near
the end of the context, or around 4 minutes per question and answer. These
are estimates from the amiga260k kernel timings and have not been measured.
Memory is about 470 KB, so it needs the 512 KB A501 expansion.
