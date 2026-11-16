#!/usr/bin/env python3
"""
scripts/asm_unit.py -- every routine of matmul68k.s against the C fallback.

m68k_gate.py checks the asm through the whole engine, which only reaches the
shifts, signs and lengths that TINY happens to use. This drives each routine
directly in Musashi with random and edge-case register arguments and compares
d0 with a Python transcription of the C fallback in tiny68k.c (u32
wrap-around included). It also checks that d2-d7/a2-a6 come back unchanged,
since vbcc's __reg calls rely on that.

    python scripts/asm_unit.py            # 20000 random cases per routine
    python scripts/asm_unit.py -n 200000
"""
import argparse
import os
import random
import re
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
import m68k_gate as G

M32 = 0xFFFFFFFF


def s32(v):
    v &= M32
    return v - (1 << 32) if v & 0x80000000 else v


# ---- Python transcriptions of the C fallbacks in tiny68k.c
def c_fxmul(a, b):
    neg = (a < 0) ^ (b < 0)
    ua, ub = (-a) & M32 if a < 0 else a, (-b) & M32 if b < 0 else b
    ah, al, bh, bl = ua >> 16, ua & 0xFFFF, ub >> 16, ub & 0xFFFF
    r = (((ah * bh) << 16) + ah * bl + al * bh + ((al * bl) >> 16)) & M32
    return s32(-r if neg else r)


def c_mulshift(acc, m, sh):
    neg = acc < 0
    ua = (-acc) & M32 if neg else acc
    lo = (ua & 0xFFFF) * m
    hi = (ua >> 16) * m
    rl = (lo + (hi << 16)) & M32
    rh = ((hi >> 16) + (1 if rl < lo else 0)) & M32
    if sh >= 32:
        rl = rh >> (sh - 32)
    elif sh > 0:
        rl = ((rl >> sh) | (rh << (32 - sh))) & M32
    elif sh < 0:
        rl = (rl << -sh) & M32
    return s32(-rl if neg else rl)


def c_fxdiv(a, b):
    if b == 0:
        return 0x7FFFFFFF
    neg = (a < 0) ^ (b < 0)
    ua = (-a) & M32 if a < 0 else a
    ub = (-b) & M32 if b < 0 else b
    q = ((ua << 16) // ub) & M32
    return s32(-q if neg else q)


def c_quantize16(x):
    amax = max([((-v) & M32 if v < 0 else v) for v in x] or [0])
    sh = 0
    while (amax >> sh) > 32767:
        sh += 1
    out = [(v >> sh) & 0xFFFF if v >= 0 else (-((s32(-v)) >> sh)) & 0xFFFF for v in x]
    return sh, out


def sx8(b):
    return b - 256 if b & 0x80 else b


def sx16(w):
    return w - 65536 if w & 0x8000 else w


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=20000)
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()
    rng = random.Random(args.seed)

    os.makedirs(G.BUILD, exist_ok=True)
    binf = os.path.join(G.BUILD, "matmul68k_unit.bin")
    lst = os.path.join(G.BUILD, "matmul68k_unit.lst")
    vasm = os.path.join(G.VBCC, "bin", "vasmm68k_mot.exe")
    if not os.path.exists(vasm):
        vasm = "vasmm68k_mot"
    subprocess.run([vasm, "-quiet", "-Fbin", "-m68000", "-no-opt", "-L", lst,
                    os.path.join(G.ROOT, "src", "matmul68k.s"), "-o", binf], check=True)
    code = open(binf, "rb").read()
    sym = {}
    for ln in open(lst, errors="replace"):
        m = re.match(r"\s*(_\w+)\s+LAB\s+0*([0-9A-Fa-f]+)", ln)
        if m:
            sym[m.group(1)] = int(m.group(2), 16)
    need = ["_dot_i8_x16", "_dot_x16_i8s", "_dot_i8_x16_sparse", "_fxmul", "_mulshift",
            "_build_bptab", "_bp_dots", "_bp_dots_p", "_dots_i8_x16", "_dots_i8_x16_sparse",
            "_dots_i8_x16_sparse_T", "_mulshift_rows", "_fxdiv", "_quantize16",
            "_rms_sumsq", "_rms_apply", "_kv_quant",
            "_att_qk_head", "_att_softmax_wgt", "_att_wv_head"]
    if not all(n in sym for n in need):
        # fall back: find the labels in the listing's symbol table
        for ln in open(lst, errors="replace"):
            for n in need:
                m = re.search(r"\b" + n + r"\b.*?([0-9A-Fa-f]{8})", ln)
                if m and n not in sym:
                    sym[n] = int(m.group(1), 16)
    missing = [n for n in need if n not in sym]
    if missing:
        G.die(f"symbols not found in {lst}: {missing}")

    cpu = G.Musashi()
    R = cpu.M.Register
    cpu.write(G.BASE, code)
    DATA_A, DATA_B = 0x40000, 0x60000
    KEEP = [R.D2, R.D3, R.D4, R.D5, R.D6, R.D7, R.A2, R.A3, R.A4, R.A5, R.A6]

    def call(name, regs):
        for r, v in regs.items():
            cpu.cpu.w_reg(r, v & M32)
        canary = {}
        for r in KEEP:
            if r in regs:
                canary[r] = regs[r] & M32
            else:
                canary[r] = rng.getrandbits(32)
                cpu.cpu.w_reg(r, canary[r])
        sp = G.STACK - 4
        cpu.mem.w32(sp, G.RET)
        cpu.cpu.w_reg(R.A7, sp)
        cpu.cpu.w_pc(G.BASE + sym[name])
        cpu.done = False
        while not cpu.done:
            cpu.m.execute(1_000_000)
        for r in KEEP:
            if cpu.cpu.r_reg(r) != canary[r]:
                G.die(f"{name} clobbered {r}")
        if cpu.cpu.r_reg(R.A7) != sp + 4:
            G.die(f"{name} unbalanced stack")
        return s32(cpu.cpu.r_reg(R.D0))

    def edge32():
        return rng.choice([0, 1, -1, 0x7FFFFFFF, -0x80000000, 0xFFFF, 0x10000,
                           -0x10000, 0x8000, -0x8000, rng.getrandbits(32) - (1 << 31),
                           rng.randint(-1 << 20, 1 << 20), rng.randint(-300, 300)])

    fails = 0

    def check(name, got, want, info):
        nonlocal fails
        if got != want:
            fails += 1
            if fails <= 10:
                print(f"  FAIL {name} {info}: asm {got} C {want}")

    n = args.n
    for _ in range(n):
        a, b = edge32(), edge32()
        check("fxmul", call("_fxmul", {R.D0: a, R.D1: b}), c_fxmul(a, b), (a, b))
    print(f"fxmul: {n} cases")

    for _ in range(n):
        acc = edge32()
        m = rng.choice([0, 1, 0xFFFF, 0x8000, rng.getrandbits(16)])
        sh = rng.randint(-15, 47)
        d1 = (rng.getrandbits(16) << 16) | m          # garbage in the high word
        check("mulshift", call("_mulshift", {R.D0: acc, R.D1: d1, R.D2: sh}),
              c_mulshift(acc, m, sh), (acc, m, sh))
    print(f"mulshift: {n} cases (sh in [-15, 47], garbage in d1.h)")

    nd = max(n // 20, 200)
    for _ in range(nd):
        cnt = rng.choice([0, 1, 7, 8, 9, 64, 128, 256, 511, rng.randint(0, 600)])
        w = bytes(rng.getrandbits(8) for _ in range(cnt))
        x = [rng.choice([rng.getrandbits(16), 0, 0x7FFF, 0x8000]) for _ in range(cnt)]
        cpu.write(DATA_A, w)
        cpu.write(DATA_B, b"".join(v.to_bytes(2, "big") for v in x))
        want = s32(sum(sx8(w[i]) * sx16(x[i]) for i in range(cnt)))
        check("dot_i8_x16", call("_dot_i8_x16", {R.A0: DATA_A, R.A1: DATA_B, R.D0: cnt}),
              want, cnt)

        stride = rng.choice([1, 2, 64, 128, 96])
        cnt2 = rng.randint(0, 200)
        v = bytes(rng.getrandbits(8) for _ in range(cnt2 * stride + 1))
        wv = [rng.getrandbits(16) for _ in range(cnt2)]
        cpu.write(DATA_A, v)
        cpu.write(DATA_B, b"".join(t.to_bytes(2, "big") for t in wv))
        want = s32(sum(sx16(wv[j]) * sx8(v[j * stride]) for j in range(cnt2)))
        check("dot_x16_i8s", call("_dot_x16_i8s", {R.A0: DATA_B, R.A1: DATA_A,
                                                    R.D0: cnt2, R.D1: stride}), want, (cnt2, stride))

        cols = rng.randint(1, 1024)
        k = rng.randint(0, min(cols, 400))
        wr = bytes(rng.getrandbits(8) for _ in range(cols))
        idx = sorted(rng.sample(range(cols), k))
        val = [rng.getrandbits(16) for _ in range(k)]
        pairs = b"".join(i.to_bytes(2, "big") + t.to_bytes(2, "big") for i, t in zip(idx, val))
        cpu.write(DATA_A, wr)
        cpu.write(DATA_B, pairs)
        want = s32(sum(sx8(wr[i]) * sx16(t) for i, t in zip(idx, val)))
        check("dot_i8_x16_sparse", call("_dot_i8_x16_sparse",
                                        {R.A0: DATA_A, R.A1: DATA_B, R.D0: k}), want, (cols, k))
    print(f"dot_i8_x16, dot_x16_i8s, dot_i8_x16_sparse: {nd} vectors each")

    def bitplanes(w, rows, cols):
        """tiny68k.c bitplane_blocks(): rows x cols int8 (bytes) -> (in place, spill)"""
        main, spill = bytearray(), bytearray()
        for b in range(rows // 8):
            u = [[w[(8 * b + r) * cols + c] ^ 0x80 for c in range(cols)] for r in range(8)]
            for k in range(7, -1, -1):
                for g in range(cols // 6):
                    for r in range(8):
                        v = sum(((u[r][6 * g + i] >> k) & 1) << i for i in range(6))
                        (main if k >= 2 else spill).append(4 * v)
        return bytes(main), bytes(spill)

    nt = max(n // 200, 30)
    for it in range(nt):
        cols = 6 * rng.choice([1, 2, 16, rng.randint(1, 21)])
        nblk = rng.choice([1, 2, 12, rng.randint(1, 36)])
        if it % 2:      # extremes
            x = [rng.choice([0x7FFF, 0x8000, 0, 1, 0xFFFF]) for _ in range(cols)]
        else:
            x = [rng.getrandbits(16) for _ in range(cols)]
        cpu.write(DATA_B, b"".join(v.to_bytes(2, "big") for v in x))
        TAB = 0x80000
        sx = call("_build_bptab", {R.A0: DATA_B, R.A1: TAB, R.D0: cols})
        check("build_bptab sum", sx, s32(sum(sx16(v) for v in x)), cols)
        raw = cpu.read(TAB, 256 * (cols // 6))
        tab = [s32(int.from_bytes(raw[4 * i:4 * i + 4], "big")) for i in range(64 * (cols // 6))]
        want_tab = [s32(sum(sx16(x[6 * g + i]) for i in range(6) if b >> i & 1))
                    for g in range(cols // 6) for b in range(64)]
        if tab != want_tab:
            check("build_bptab", 1, 0, cols)
            continue
        rows = 8 * nblk
        w = bytes(rng.getrandbits(8) for _ in range(rows * cols))
        main, spill = bitplanes(w, rows, cols)
        assert len(main) == rows * cols and len(spill) == rows * cols // 3
        SPILL = 0xC0000
        cpu.write(DATA_A, main)
        cpu.write(SPILL, spill)
        OUT = 0xB0000
        cpu.write(OUT, bytes(4 * rows))
        bias = s32(sx << 7)
        call("_bp_dots", {R.A0: DATA_A, R.A1: TAB, R.D0: cols // 6, R.D1: OUT, R.D2: nblk,
                          R.D3: bias, R.D4: SPILL})
        got = [s32(int.from_bytes(cpu.read(OUT + 4 * r, 4), "big")) for r in range(rows)]
        for r in range(rows):
            want = s32(sum(sx8(w[r * cols + j]) * sx16(x[j]) for j in range(cols)))
            check("bp_dots", got[r], want, (cols, nblk, r))
    print(f"build_bptab + bp_dots: {nt} cases")

    for _ in range(n):
        a = edge32()
        b = rng.choice([edge32(), rng.randint(1, 1 << 16), -0x80000000, 0x7FFFFFFF, 1, -1, 0])
        check("fxdiv", call("_fxdiv", {R.D0: a, R.D1: b}), c_fxdiv(a, b), (a, b))
    print(f"fxdiv: {n} cases")

    for it in range(nd):
        cnt = rng.choice([0, 1, 2, 96, 256, rng.randint(0, 300)])
        mag = rng.choice([1 << 10, 1 << 15, 1 << 20, 1 << 31])
        x = [rng.randint(-mag, mag - 1) for _ in range(cnt)]
        if cnt and it % 5 == 0:
            x[rng.randrange(cnt)] = rng.choice([-0x80000000, 0x7FFFFFFF, 32767, -32768, 32768])
        cpu.write(DATA_A, b"".join((v & M32).to_bytes(4, "big") for v in x))
        sh = call("_quantize16", {R.A0: DATA_A, R.D0: cnt, R.A1: DATA_B})
        raw = cpu.read(DATA_B, 2 * cnt)
        got = [int.from_bytes(raw[2 * i:2 * i + 2], "big") for i in range(cnt)]
        wsh, want = c_quantize16(x)
        check("quantize16 sh", sh, wsh, cnt)
        check("quantize16 out", got, want, cnt)
    print(f"quantize16: {nd} vectors")

    for it in range(max(nd // 4, 50)):
        cols, rows = rng.choice([(96, 96), (96, 97), (24, 5), (256, 96), (rng.randint(1, 300), rng.randint(1, 40))])
        w = bytes(rng.getrandbits(8) for _ in range(rows * cols))
        x = [rng.getrandbits(16) for _ in range(cols)]
        cpu.write(DATA_A, w)
        cpu.write(DATA_B, b"".join(v.to_bytes(2, "big") for v in x))
        OUT = 0xB0000
        call("_dots_i8_x16", {R.A0: DATA_A, R.A1: DATA_B, R.D0: cols, R.D1: OUT, R.D2: rows})
        raw = cpu.read(OUT, 4 * rows)
        for r in range(rows):
            want = s32(sum(sx8(w[r * cols + j]) * sx16(x[j]) for j in range(cols)))
            check("dots_i8_x16", s32(int.from_bytes(raw[4 * r:4 * r + 4], "big")), want, (cols, rows, r))
        k = rng.randint(0, cols)
        idx = sorted(rng.sample(range(cols), k))
        val = [rng.getrandbits(16) for _ in range(k)]
        cpu.write(DATA_B, b"".join(i.to_bytes(2, "big") + t.to_bytes(2, "big") for i, t in zip(idx, val)))
        call("_dots_i8_x16_sparse", {R.A0: DATA_A, R.A1: DATA_B, R.D0: k, R.D1: OUT,
                                     R.D2: rows, R.D3: cols})
        raw = cpu.read(OUT, 4 * rows)
        for r in range(rows):
            want = s32(sum(sx8(w[r * cols + i]) * sx16(t) for i, t in zip(idx, val)))
            check("dots_i8_x16_sparse", s32(int.from_bytes(raw[4 * r:4 * r + 4], "big")), want, (cols, k, r))
        acc = [edge32() for _ in range(rows)]
        mant = [rng.getrandbits(16) for _ in range(rows)]
        xsh = rng.randint(-10, 20)
        shf = [rng.randint(max(0, xsh - 15), xsh + 47) for _ in range(rows)]
        cpu.write(OUT, b"".join((v & M32).to_bytes(4, "big") for v in acc))
        cpu.write(DATA_A, b"".join(v.to_bytes(2, "big") for v in mant))
        cpu.write(DATA_B, b"".join(v.to_bytes(2, "big") for v in shf))
        call("_mulshift_rows", {R.A0: OUT, R.A1: DATA_A, R.D0: rows, R.D1: xsh, R.D2: DATA_B})
        raw = cpu.read(OUT, 4 * rows)
        for r in range(rows):
            want = c_mulshift(acc[r], mant[r], shf[r] - xsh)
            check("mulshift_rows", s32(int.from_bytes(raw[4 * r:4 * r + 4], "big")), want, (rows, r))
    print("dots_i8_x16, dots_i8_x16_sparse, mulshift_rows: row loops")

    def rd32(adr, k):
        raw = cpu.read(adr, 4 * k)
        return [s32(int.from_bytes(raw[4 * i:4 * i + 4], "big")) for i in range(k)]

    def wr32(adr, vals):
        cpu.write(adr, b"".join((v & M32).to_bytes(4, "big") for v in vals))

    for it in range(nd):
        k = rng.choice([0, 1, 96, rng.randint(0, 200)])
        x = [edge32() for _ in range(k)]
        g = [edge32() for _ in range(k)]
        inv = edge32()
        wr32(DATA_A, x)
        wr32(DATA_B, g)
        OUT = 0xB0000
        call("_rms_apply", {R.A0: OUT, R.A1: DATA_A, R.D0: k, R.D1: inv, R.D2: DATA_B})
        want = [c_fxmul(g[j], c_fxmul(x[j], inv)) for j in range(k)]
        check("rms_apply", rd32(OUT, k), want, k)

        f = rng.choice([edge32(), rng.randint(1, 127 << 16), 127 << 8])
        wr32(DATA_A, x)
        call("_kv_quant", {R.A0: DATA_A, R.D0: k, R.A1: OUT, R.D1: f})
        got = list(cpu.read(OUT, k))
        want = []
        for v in x:
            a = s32(-v) if v < 0 else v
            r = s32(c_fxmul(a, f) + 0x8000) >> 16
            r = min(r, 127)
            want.append((-r if v < 0 else r) & 0xFF)
        check("kv_quant", got, want, k)
    print(f"rms_apply, kv_quant: {nd} vectors")

    # ---- bp_dots_p (planes = 1..8, including groups == 16 fast path)
    for it in range(nt):
        cols = 6 * rng.choice([16, 16, 1, 2, rng.randint(1, 21)])
        nblk = rng.choice([1, 2, 12, rng.randint(1, 16)])
        planes = rng.randint(1, 8)
        x = [rng.choice([0x7FFF, 0x8000, 0, 1, 0xFFFF]) if it % 3 == 0 else rng.getrandbits(16)
             for _ in range(cols)]
        cpu.write(DATA_B, b"".join(v.to_bytes(2, "big") for v in x))
        TAB = 0x80000
        sx = call("_build_bptab", {R.A0: DATA_B, R.A1: TAB, R.D0: cols})
        rows = 8 * nblk
        w = bytes(rng.getrandbits(8) for _ in range(rows * cols))
        main, spill = bitplanes(w, rows, cols)
        SPILL = 0xC0000
        OUT = 0xB0000
        cpu.write(DATA_A, main)
        cpu.write(SPILL, spill if planes > 6 else bytes(len(spill)))  # ensure planes<=6 ignores spill
        drop = 8 - planes
        bias = s32(sx << 7) if planes >= 8 else (s32((257 - (1 << drop)) * sx) >> 1)
        call("_bp_dots_p", {R.A0: DATA_A, R.A1: TAB, R.D0: cols // 6, R.D1: OUT, R.D2: nblk,
                            R.D3: bias, R.D4: SPILL, R.D5: planes})
        got = rd32(OUT, rows)
        for r in range(rows):
            acc_u = sum((((w[r * cols + j] ^ 0x80) >> drop) << drop) * sx16(x[j]) for j in range(cols))
            want = s32(acc_u - bias)
            check(f"bp_dots_p(p={planes})", got[r], want, (cols, nblk, r))
    print(f"bp_dots_p (planes 1..8): {nt} cases")

    # ---- dots_i8_x16_sparse_T
    for it in range(max(nd // 4, 50)):
        cols, rows = rng.choice([(256, 96), (96, 96), (64, 15), (rng.randint(1, 260), rng.randint(1, 40))])
        wt = bytes(rng.getrandbits(8) for _ in range(rows * cols))
        k = rng.randint(0, min(cols, 65535 // rows))
        idx = sorted(rng.sample(range(cols), k))
        val = [rng.getrandbits(16) for _ in range(k)]
        pairs = b"".join(((i * rows) & 0xFFFF).to_bytes(2, "big") + t.to_bytes(2, "big") for i, t in zip(idx, val))
        cpu.write(DATA_A, wt)
        cpu.write(DATA_B, pairs)
        OUT = 0xB0000
        call("_dots_i8_x16_sparse_T", {R.A0: DATA_A, R.A1: DATA_B, R.D0: k, R.D1: OUT, R.D2: rows})
        got = rd32(OUT, rows)
        want = [s32(sum(sx8(wt[i * rows + r]) * sx16(t) for i, t in zip(idx, val))) for r in range(rows)]
        check("dots_i8_x16_sparse_T", got, want, (cols, rows, k))
    print("dots_i8_x16_sparse_T: row loops")

    # ---- rms_sumsq
    for it in range(nd):
        cnt = rng.choice([0, 1, 96, rng.randint(0, 200)])
        x = [edge32() for _ in range(cnt)]
        wr32(DATA_A, x)
        OUT_K = 0xB0000
        ss = call("_rms_sumsq", {R.A0: DATA_A, R.D0: cnt, R.A1: OUT_K}) & M32
        got_k = rd32(OUT_K, 1)[0]
        amax = max([((-v) & M32 if v < 0 else v) for v in x] or [0])
        wk = 0
        if amax != 0:
            while (amax >> wk) >= 4096:
                wk += 1
        wss = sum(((v < 0 and (-v) & M32 or v) >> wk) ** 2 for v in x) & M32
        check("rms_sumsq k", got_k, wk, cnt)
        check("rms_sumsq ss", ss, wss, cnt)
    print(f"rms_sumsq: {nd} vectors")

    # ---- attention helpers: att_qk_head, att_softmax_wgt, att_wv_head
    import math
    exp_tab = [int(round(math.exp(-i / 64.0) * 65536)) for i in range(16 * 64 + 1)]
    EXP_ADR = 0x70000
    wr32(EXP_ADR, exp_tab)
    for it in range(max(nd // 4, 50)):
        p1 = rng.choice([1, 2, 15, 97, rng.randint(1, 128)])
        hd = rng.choice([24, 24, 8, 13, rng.randint(1, 32)])
        stride = hd + rng.choice([0, 72, rng.randint(0, 80)])
        qsh = rng.randint(0, 12)
        kc = bytes(rng.getrandbits(8) for _ in range(p1 * stride))
        qq = [rng.getrandbits(16) for _ in range(hd)]
        km = [rng.getrandbits(16) for _ in range(p1)]
        ks = [rng.randint(max(0, qsh - 15), qsh + 47) for _ in range(p1)]
        cpu.write(DATA_A, kc)
        cpu.write(DATA_B, b"".join(v.to_bytes(2, "big") for v in qq))
        KM_ADR, KS_ADR, ATT_ADR = 0x80000, 0x81000, 0x82000
        cpu.write(KM_ADR, b"".join(v.to_bytes(2, "big") for v in km))
        cpu.write(KS_ADR, b"".join((v & 0xFFFF).to_bytes(2, "big") for v in ks))
        mx = call("_att_qk_head", {R.A0: DATA_A, R.A1: DATA_B, R.D0: p1, R.D1: hd, R.D2: stride,
                                   R.D3: qsh, R.D4: KM_ADR, R.D5: KS_ADR, R.D6: ATT_ADR})
        want_att = [c_mulshift(s32(sum(sx8(kc[j * stride + i]) * sx16(qq[i]) for i in range(hd))),
                               km[j], sx16(ks[j] & 0xFFFF) - qsh) for j in range(p1)]
        check("att_qk_head att", rd32(ATT_ADR, p1), want_att, (p1, hd))
        check("att_qk_head mx", mx, max(want_att), (p1, hd))

        # att_softmax_wgt
        att_in = [rng.randint(-12 << 16, 12 << 16) for _ in range(p1)]
        mx2 = max(att_in)
        vm = [rng.getrandbits(16) for _ in range(p1)]
        vs = [rng.randint(-5, 30) for _ in range(p1)]
        WGT_ADR, VM_ADR, VS_ADR = 0x84000, 0x85000, 0x86000
        wr32(ATT_ADR, att_in)
        cpu.write(VM_ADR, b"".join(v.to_bytes(2, "big") for v in vm))
        cpu.write(VS_ADR, b"".join((v & 0xFFFF).to_bytes(2, "big") for v in vs))
        call("_att_softmax_wgt", {R.A0: ATT_ADR, R.A1: WGT_ADR, R.D0: p1, R.D1: mx2,
                                  R.D2: EXP_ADR, R.D3: VM_ADR, R.D4: VS_ADR})
        want_e = []
        for s in att_in:
            u = mx2 - s
            if u <= 0:
                want_e.append(65536)
            elif u >= (16 << 16):
                want_e.append(0)
            else:
                idx = u >> 10
                frac = (u << 6) & 0xFFFF
                a, b = exp_tab[idx], exp_tab[idx + 1]
                want_e.append(a + (((b - a) * frac) >> 16))
        inv = c_fxdiv(65536, sum(want_e))
        want_wgt = [c_mulshift(c_fxmul(want_e[j], inv), vm[j], vs[j]) for j in range(p1)]
        check("att_softmax_wgt", rd32(WGT_ADR, p1), want_wgt, p1)

        # att_wv_head
        wq16 = [0 if rng.random() < 0.25 else rng.getrandbits(16) for _ in range(p1)]
        vc = bytes(rng.getrandbits(8) for _ in range(p1 * stride))
        s_sh = rng.randint(-6, 10)
        BA_ADR = 0x88000
        cpu.write(DATA_A, b"".join(v.to_bytes(2, "big") for v in wq16))
        cpu.write(DATA_B, vc)
        call("_att_wv_head", {R.A0: DATA_A, R.A1: DATA_B, R.D0: p1, R.D1: hd,
                              R.D2: stride, R.D3: s_sh, R.D4: BA_ADR})
        want_ba = []
        for i in range(hd):
            acc = s32(sum(sx16(wq16[j]) * sx8(vc[j * stride + i]) for j in range(p1)))
            want_ba.append(s32(acc >> s_sh) if s_sh >= 0 else s32(acc << (-s_sh)))
        check("att_wv_head", rd32(BA_ADR, hd), want_ba, (p1, hd, s_sh))
    print("att_qk_head, att_softmax_wgt, att_wv_head: attention cases")

    if fails:
        print(f"FAIL: {fails} mismatches")
        sys.exit(1)
    print("OK: every matmul68k.s routine matches its C fallback, registers preserved")


if __name__ == "__main__":
    main()
