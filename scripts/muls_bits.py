#!/usr/bin/env python3
"""
scripts/muls_bits.py -- which MULS operand should be the source?

On the 68000, MULS.W <ea>,Dn takes 38 + 2n cycles, where n is the number of
'01' or '10' pairs in the 17-bit pattern formed by the <ea> word with a 0
appended below its LSB. Only the SOURCE operand counts. Musashi does not
model this, so the choice of which operand to put in <ea> is made here, on
the real weights and on the real activations of the probes, run through the
oracle. Bit-exact either way: the product is the same.

Reports average transitions per MAC for every matmul (and the attention key
dot), with the activation as source (what matmul68k.s does now) and with the
sign-extended int8 weight as source.

    python scripts/muls_bits.py
"""
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import oracle_tiny as O
from convert_tiny import load_t68

def _pairs(v):
    p = (v & 0xFFFF) << 1                    # 17 bits, 0 below the LSB
    return bin((p ^ (p >> 1)) & 0xFFFF).count("1")   # 16 adjacent pairs


_T = np.array([_pairs(v) for v in range(65536)], dtype=np.int64)
assert _pairs(0) == 0 and _pairs(1) == 2 and _pairs(0xFFFF) == 1 and _pairs(0x5555) == 16


def trans(v):
    """MULS transition count of 16-bit word(s) v (any int array)."""
    return _T[np.asarray(v, dtype=np.int64) & 0xFFFF]


def main():
    from verify import PROBES
    m = load_t68(os.path.join(ROOT, "tiny.t68"))
    stats = {}                                   # name -> [macs, n_x, n_w]

    def add(name, macs, nx, nw):
        s = stats.setdefault(name, [0, 0, 0])
        s[0] += macs
        s[1] += nx
        s[2] += nw

    names = {}
    for l, ly in enumerate(m.layers):
        for k, t in ly.items():
            names[id(t)] = k
    names[id(m.emb)] = "emb (scores)"

    orig = O.matmul

    def matmul(t, xq, xsh):
        name = names.get(id(t), "?")
        xq = np.asarray(xq, dtype=np.int64)
        if name == "f2":                     # sparse kernel: nonzero columns only
            nz = xq != 0
            w = t.w[:, nz]
            x = xq[nz]
        else:
            w, x = t.w, xq
        rows = w.shape[0]
        add(name, rows * x.size, rows * int(trans(x).sum()), int(trans(w).sum()))
        return orig(t, xq, xsh)

    O.matmul = matmul
    for q, a in PROBES:
        toks = [ord(c) - 30 for c in q] + [1] + [ord(c) - 30 for c in a]
        st = O.State(m)
        for p, tok in enumerate(toks[:m.ctx]):
            O.step(m, st, tok, p)

    tm = tx = tw = 0
    print(f"{'matmul':14s} {'MACs':>12s} {'x as src':>9s} {'w as src':>9s}   (transitions per MAC)")
    for name, (macs, nx, nw) in stats.items():
        print(f"{name:14s} {macs:12,d} {nx / macs:9.2f} {nw / macs:9.2f}")
        tm += macs
        tx += nx
        tw += nw
    print(f"{'all':14s} {tm:12,d} {tx / tm:9.2f} {tw / tm:9.2f}")
    print(f"\nMULS cost per MAC: x as source {38 + 2 * tx / tm:.1f} + 4 (ea fetch), "
          f"w as source {38 + 2 * tw / tm:.1f} (register)")


if __name__ == "__main__":
    main()
