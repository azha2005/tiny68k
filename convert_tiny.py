"""
convert_tiny.py -- TINY.BIN (little-endian, float32 scales)
                -> tiny.t68 (big-endian, integer only), and the .t68 reader.

The int8 weights are copied bit for bit: TINY.BIN is already int8 with one
float scale per row. What changes:

  * each row scale becomes a normalised uint16 mantissa and a shift, so the
    rescale after a dot product is one mulshift() and the 68000 never sees a
    float. Per-row shifts, so a small row keeps its 16 bits of precision.
  * the RMSNorm gains become Q16.16.
  * the exp table is built here and shipped in the file.
  * the two attention constants, 1/(127*sqrt(HD))/65536 for the scores and
    1/(127*256) for the value weights, are computed here ONCE, with float
    math, and stored. Both the oracle and the C read them from the file, so
    there is exactly one route to each (the amiga260k 1/sqrt(head_size) bug).

Layout (all big-endian):
  "T68A"  u16 version  u16 D L H HID VOCAB CTX
  u16 kc_m kc_s vc_m vc_s
  s32 exp_tab[EXP_N]
  s32 g1[L][D]  g2[L][D]  gf[D]
  table emb, table pos, then per layer: wq wk wv wo f1 f2
  table = u16 rows, u16 cols, u16 mant[rows], u16 shift[rows], s8 w[rows*cols]
          (+ one pad byte if rows*cols is odd)

Usage:  python convert_tiny.py orig/TINY.BIN tiny.t68
"""
import math
import struct
import sys
import numpy as np

import fx
from ref_tiny import read_tiny, VOCAB, CTX

MAGIC = b"T68A"
VERSION = 1
LAYER_TABLES = ("wq", "wk", "wv", "wo", "f1", "f2")


def norm_const(c):
    """c > 0 -> (m, s) with c ~= m / 2**s and m in [32768, 65535]."""
    s = 15 - math.floor(math.log2(c))
    m = int(round(c * (1 << s)))
    if m > 0xFFFF:
        s -= 1
        m = int(round(c * (1 << s)))
    assert 0x8000 <= m <= 0xFFFF, (c, m, s)
    return m, s


def row_scales(scale):
    mant = np.zeros(len(scale), dtype=np.int64)
    sh = np.zeros(len(scale), dtype=np.int64)
    for r, sc in enumerate(scale):
        mant[r], sh[r] = norm_const(float(sc))
    return mant, sh


def pack_table(t):
    mant, sh = row_scales(t.scale)
    out = struct.pack(">HH", t.rows, t.cols)
    out += mant.astype(">u2").tobytes() + sh.astype(">u2").tobytes()
    body = t.w.tobytes()
    if len(body) & 1:
        body += b"\0"
    return out + body


def q16(a):
    return np.rint(np.asarray(a, dtype=np.float64) * fx.ONE).astype(">i4").tobytes()


def attention_constants(hd):
    kc = norm_const(1.0 / (127.0 * math.sqrt(hd)) / 65536.0)
    vc = norm_const(1.0 / (127.0 * 256.0))
    return kc, vc


def convert(src, dst):
    m = read_tiny(src)
    (kcm, kcs), (vcm, vcs) = attention_constants(m.HD)
    blob = bytearray(MAGIC)
    blob += struct.pack(">7H", VERSION, m.D, m.L, m.H, m.HID, VOCAB, CTX)
    blob += struct.pack(">4H", kcm, kcs, vcm, vcs)
    blob += np.asarray(fx.build_exp_table(), dtype=">i4").tobytes()
    for l in range(m.L):
        blob += q16(m.g1[l])
    for l in range(m.L):
        blob += q16(m.g2[l])
    blob += q16(m.gf)
    blob += pack_table(m.emb)
    blob += pack_table(m.pos)
    for ly in m.layers:
        for k in LAYER_TABLES:
            blob += pack_table(ly[k])
    with open(dst, "wb") as f:
        f.write(blob)
    print(f"wrote {dst}: {len(blob)} bytes ({len(blob) / 1024:.1f} KB)")


# ------------------------------------------------------------------ reader

class QTable:
    __slots__ = ("rows", "cols", "mant", "shift", "w")


class Model:
    pass


def load_t68(path):
    raw = open(path, "rb").read()
    if raw[:4] != MAGIC:
        raise ValueError("not a .t68 file")
    ver, D, L, H, HID, vocab, ctx = struct.unpack_from(">7H", raw, 4)
    if ver != VERSION:
        raise ValueError(f".t68 version {ver}, expected {VERSION}")
    m = Model()
    m.D, m.L, m.H, m.HID, m.vocab, m.ctx = D, L, H, HID, vocab, ctx
    m.HD = D // H
    m.kc_m, m.kc_s, m.vc_m, m.vc_s = struct.unpack_from(">4H", raw, 18)
    off = 26

    def ints(n):
        nonlocal off
        a = np.frombuffer(raw, dtype=">i4", count=n, offset=off).astype(np.int64)
        off += 4 * n
        return a

    m.exp_tab = [int(v) for v in ints(fx.EXP_N)]
    m.g1 = [[int(v) for v in ints(D)] for _ in range(L)]
    m.g2 = [[int(v) for v in ints(D)] for _ in range(L)]
    m.gf = [int(v) for v in ints(D)]

    def table():
        nonlocal off
        t = QTable()
        t.rows, t.cols = struct.unpack_from(">HH", raw, off)
        off += 4
        t.mant = [int(v) for v in np.frombuffer(raw, ">u2", t.rows, off)]
        off += 2 * t.rows
        t.shift = [int(v) for v in np.frombuffer(raw, ">u2", t.rows, off)]
        off += 2 * t.rows
        n = t.rows * t.cols
        t.w = np.frombuffer(raw, np.int8, n, off).reshape(t.rows, t.cols).astype(np.int64)
        off += n + (n & 1)
        return t

    m.emb = table()
    m.pos = table()
    m.layers = [{k: table() for k in LAYER_TABLES} for _ in range(L)]
    if off != len(raw):
        raise ValueError(f"trailing bytes: {off} vs {len(raw)}")
    return m


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    convert(sys.argv[1], sys.argv[2])
