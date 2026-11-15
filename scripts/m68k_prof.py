#!/usr/bin/env python3
"""
scripts/m68k_prof.py -- where the 68000 cycles go, per function.

Builds the engine like m68k_gate.py but with -Dstatic= and inlining off, so
every function keeps a symbol (vbcc inlines statics and drops their names:
SMW P37). The numbers are therefore those of a slightly less optimised
build, which is fine for finding hot spots but not for timing. Runs
tiny_step() in Musashi, stops every SLICE cycles and attributes the slice to
the function containing the PC: a statistical profile weighted by cycles.

    python scripts/m68k_prof.py               # positions 0 and 96
    python scripts/m68k_prof.py --pos 0 40
"""
import argparse
import bisect
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
import m68k_gate as G

SLICE = 64


def symbols_all(mp):
    """every global code symbol from the vlink map, sorted"""
    syms = []
    for ln in open(mp):
        m = re.match(r"\s+0x([0-9a-f]+) (_\w+): global", ln)
        if m:
            syms.append((int(m.group(1), 16), m.group(2)[1:]))
    syms.sort()
    return syms


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pos", type=int, nargs="*", default=[0, 96])
    ap.add_argument("--kernel", choices=("asm", "c"), default="asm")
    args = ap.parse_args()

    from verify import PROBES
    code, sy = G.build(args.kernel, ["-Dstatic=", "-inline-size=0"], tag="_prof")
    syms = symbols_all(os.path.join(G.BUILD, f"eng_{args.kernel}_prof.map"))
    addrs = [a for a, _ in syms]

    cpu = G.Musashi()
    blob = open(os.path.join(G.ROOT, "tiny.t68"), "rb").read()
    cpu.write(G.BASE, code)
    cpu.write(G.BLOB, blob)
    err, _ = cpu.call(sy["_tiny_init"], G.BLOB, len(blob), G.KVMEM)
    assert err == 0
    q, a = PROBES[1]
    toks = [ord(c) - 30 for c in q] + [1] + [ord(c) - 30 for c in a]
    M = cpu.M

    for p in range(max(args.pos) + 1):
        if p not in args.pos:
            cpu.call(sy["_tiny_step"], toks[p], p)
            continue
        # same call, but sliced
        sp = G.STACK - 12
        cpu.mem.w32(sp, G.RET)
        cpu.mem.w32(sp + 4, toks[p])
        cpu.mem.w32(sp + 8, p)
        cpu.cpu.w_reg(M.Register.A7, sp)
        cpu.cpu.w_pc(sy["_tiny_step"])
        cpu.done = False
        hist = {}
        total = 0
        while not cpu.done:
            pc = cpu.cpu.r_pc()
            c = cpu.m.execute(SLICE).cycles
            i = bisect.bisect_right(addrs, pc) - 1
            name = syms[i][1] if i >= 0 else "?"
            hist[name] = hist.get(name, 0) + c
            total += c
        print(f"\nposition {p}: {total:,} cycles ({total / G.CPU_HZ:.2f} s at 7.09 MHz, "
              f"profiling build, no DMA waits)")
        for name, c in sorted(hist.items(), key=lambda kv: -kv[1]):
            if c / total < 0.003:
                continue
            print(f"  {100 * c / total:5.1f}%  {c / 1e6:7.2f}M  {name}")


if __name__ == "__main__":
    main()
