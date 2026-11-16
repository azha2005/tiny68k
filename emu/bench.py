#!/usr/bin/env python3
"""
emu/bench.py -- real A500 (cycle-exact WinUAE, KS 1.2) vs Musashi, per routine.

Builds bench68k.c twice: as an AmigaDOS executable that times each case with
DateStamp, and as raw code for Musashi. Prints, per case, the real seconds,
the Musashi seconds for the identical calls, and the ratio. A ratio that
differs between routines points at the instruction stream (bus timing
Musashi does not model); the same ratio everywhere, including the
register-only idle loop, points at the OS (interrupts, task switching).

    python emu/bench.py            # both
    python emu/bench.py --musashi  # only the Musashi side
"""
import argparse
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import run_test as R
import m68k_gate as G

R.ADF = os.path.join(HERE, "bench.adf")
R.CFG = os.path.join(HERE, "bench.uae")
EXE = os.path.join(G.BUILD, "bench68k")


def build():
    os.makedirs(G.BUILD, exist_ok=True)
    mobj = os.path.join(G.BUILD, "matmul68k.o")
    G.run(["vasmm68k_mot", "-quiet", "-Fhunk", "-m68000", "-no-opt", os.path.join("src", "matmul68k.s"), "-o", mobj])
    # AmigaDOS executable
    G.run(["vc", "+kick13", "-c99", "-O2", os.path.join("src", "bench68k.c"), mobj, "-o", EXE])
    # raw code for Musashi
    obj = os.path.join(G.BUILD, "bench_raw.o")
    out = os.path.join(G.BUILD, "bench_raw.bin")
    mp = os.path.join(G.BUILD, "bench_raw.map")
    G.run(["vc", "+kick13", "-c99", "-O2", "-DBENCH_ENGINE_ONLY", "-c", os.path.join("src", "bench68k.c"), "-o", obj])
    G.run(["vlink", "-brawbin1", "-Ttext", hex(G.BASE), "-M" + mp, "-o", out, obj, mobj,
           "-L" + os.path.join(G.VBCC, "targets", "m68k-kick13", "lib"), "-lvc"])
    syms = {}
    for ln in open(mp):
        m = re.match(r"\s+0x([0-9a-f]+) (_\w+): global", ln)
        if m:
            syms[m.group(2)] = int(m.group(1), 16)
    return open(out, "rb").read(), syms


def musashi(code, sy):
    cpu = G.Musashi()
    if G.BASE + len(code) > G.STACK - 0x10000:
        G.die("bench code too large for the layout")
    cpu.write(G.BASE, code)
    cpu.call(sy["_bench_setup"])
    iters = [int.from_bytes(cpu.read(sy["_bench_iters"] + 4 * i, 4), "big") for i in range(6)]
    cyc = []
    for c in range(6):
        _, n = cpu.call(sy["_bench_run"], c, iters[c])
        cyc.append(n)
    return iters, cyc


def real(timeout):
    if os.path.exists(R.ADF):
        os.remove(R.ADF)
    import tempfile
    tmp = tempfile.mkdtemp()
    ss = os.path.join(tmp, "startup-sequence")
    with open(ss, "w", newline="\n") as f:
        f.write("bench68k >DF0:bench.txt\n")
        f.write("bench68k >DF0:bench2.txt nodma\n")
    R.xdf(R.ADF, "create")
    R.xdf(R.ADF, "format", "BENCH", "ofs")
    R.xdf(R.ADF, "write", EXE)
    R.xdf(R.ADF, "makedir", "s")
    R.xdf(R.ADF, "write", ss, "s/startup-sequence")
    R.xdf(R.ADF, "boot", "install", "boot1x")
    R.write_config(R.ROM, True)
    out = os.path.join(HERE, "out")
    os.makedirs(out, exist_ok=True)
    p = subprocess.Popen([R.WINUAE, "-f", R.CFG], cwd=HERE)
    t0 = time.time()
    lines = []
    while time.time() - t0 < timeout and p.poll() is None:
        time.sleep(5)
        txt = (R.read_back("bench.txt", out) or "") + (R.read_back("bench2.txt", out) or "")
        lines = [l for l in txt.splitlines() if l.strip()]
        if len(lines) >= 12:
            break
    time.sleep(2)
    if p.poll() is None:
        p.kill()
    res = {}
    for l in lines:
        f = l.split(None, 3)
        res[int(f[0])] = (int(f[1]), int(f[2]) / 50.0, f[3])
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--musashi", action="store_true")
    ap.add_argument("--timeout", type=int, default=900)
    args = ap.parse_args()
    code, sy = build()
    iters, cyc = musashi(code, sy)
    if args.musashi:
        for c in range(6):
            print(f"{c} {iters[c]:5d} iters  Musashi {cyc[c]:>12,} cycles = {cyc[c] / G.CPU_HZ:7.2f} s")
        return 0
    res = real(args.timeout)
    print(f"{'case':40s} {'real s':>8s} {'Musashi s':>10s} {'ratio':>6s}")
    for c in list(range(6)) + list(range(10, 16)):
        ms = cyc[c % 10] / G.CPU_HZ
        if c in res:
            n, t, name = res[c]
            print(f"{name:40s} {t:8.2f} {ms:10.2f} {t / ms:6.3f}")
        else:
            print(f"case {c}: no result from WinUAE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
