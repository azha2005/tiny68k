#!/usr/bin/env python3
"""
scripts/m68k_gate.py -- the REAL 68000 code against the oracle.

verify.py checks tiny68k.c compiled by gcc. That cannot see a vbcc
miscompile (the SMW port hit three: P38, P47, P62) or a bug in matmul68k.s.
This gate compiles the engine with vbcc +kick13 exactly as the Amiga build
does (-DTINY_ENGINE_ONLY drops only libc-facing code), links it raw at a
fixed address with vlink, loads it and tiny.t68 into a 68000 emulator, calls
tiny_step() / tiny_think() and diffs against oracle_tiny.py:

  * every score and layer activity of the verify.py probes
  * greedy answers to the first questions, and one sampled run

for both kernels: matmul68k.s (asm) and the C dot product.

Engines: musashi (machine68k; also counts 68000 cycles, with no DMA waits,
so a LOWER bound for a real A500) or unicorn (QEMU, faster, no cycles).

    python scripts/m68k_gate.py                 # both kernels, musashi
    python scripts/m68k_gate.py --kernel asm --quick
"""
import argparse
import os
import re
import struct
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

VBCC = os.environ.get("VBCC", r"C:\Users\JC\vbcc")
BUILD = os.path.join(ROOT, "build")
CPU_HZ = 7093790                 # PAL 68000

BASE = 0x010000                  # engine (code, data, bss)
BLOB = 0x020000                  # tiny.t68
KVMEM = 0x080000                 # key/value caches
STR = 0x0C0000                   # question in, answer out (after KV + tables)
STACK = 0x0F0000
RET = 0x0FFFF0                   # magic return address
MEMKB = 1024

N_THINK = 6                      # greedy questions run on the 68000
THINK_SAMPLED = (5, 7)           # one sampled run: (creativity, seed)


def die(msg):
    print(msg, file=sys.stderr)
    sys.exit(1)


def run(cmd):
    e = dict(os.environ)
    e["VBCC"] = VBCC
    e["PATH"] = os.path.join(VBCC, "bin") + os.pathsep + e.get("PATH", "")
    exe = os.path.join(VBCC, "bin", cmd[0] + (".exe" if os.name == "nt" else ""))
    if os.path.isfile(exe):          # Windows does not search the child's PATH
        cmd = [exe] + cmd[1:]
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, env=e)
    if r.returncode != 0:
        die("BUILD FAILED: " + " ".join(cmd) + "\n" + r.stdout + r.stderr)


def build(kernel, extra=(), tag=""):
    """vbcc + vlink -> raw binary at BASE, and its global symbols."""
    os.makedirs(BUILD, exist_ok=True)
    obj = os.path.join(BUILD, f"eng_{kernel}{tag}.o")
    out = os.path.join(BUILD, f"eng_{kernel}{tag}.bin")
    mp = os.path.join(BUILD, f"eng_{kernel}{tag}.map")
    flags = (["-DUSE_ASM"] if kernel == "asm" else []) + list(extra)
    run(["vc", "+kick13", "-c99", "-O2", "-DTINY_ENGINE_ONLY"] + flags +
        ["-c", os.path.join("src", "tiny68k.c"), "-o", obj])
    objs = [obj]
    if kernel == "asm":
        mobj = os.path.join(BUILD, "matmul68k.o")
        run(["vasmm68k_mot", "-quiet", "-Fhunk", "-m68000", "-no-opt",
             os.path.join("src", "matmul68k.s"), "-o", mobj])
        objs.append(mobj)
    run(["vlink", "-brawbin1", "-Ttext", hex(BASE), "-M" + mp, "-o", out] + objs +
        ["-L" + os.path.join(VBCC, "targets", "m68k-kick13", "lib"), "-lvc"])
    syms = {}
    for ln in open(mp):
        m = re.match(r"\s+0x([0-9a-f]+) (_\w+): global", ln)
        if m:
            syms[m.group(2)] = int(m.group(1), 16)
    need = ("_tiny_init", "_tiny_step", "_tiny_prefill", "_tiny_think", "_tiny_score",
            "_tiny_layer_act", "_tiny_kv_bytes", "_tiny_prefix_always")
    for s in need:
        if s not in syms:
            die(f"symbol {s} not in {mp}")
    return open(out, "rb").read(), syms


class Musashi:
    cycles_ok = True

    def __init__(self):
        import machine68k as M
        self.M = M
        self.m = M.Machine(M.CPUType.M68000, MEMKB)
        self.cpu, self.mem = self.m.cpu, self.m.mem
        self.done = False

        def trap(op, pc):
            self.done = True
            self.m.abort_execute()
        tid = self.m.traps.alloc(trap)
        self.mem.w16(RET, 0xA000 | tid)

    def write(self, adr, data):
        self.mem.w_block(adr, bytes(data))

    def read(self, adr, n):
        return bytes(self.mem.r_block(adr, n))

    def call(self, adr, *args):
        """C call (args on the stack, vbcc ABI). Returns (d0, cycles)."""
        M = self.M
        sp = STACK - 4 * len(args) - 4
        self.mem.w32(sp, RET)
        for i, a in enumerate(args):
            self.mem.w32(sp + 4 + 4 * i, a & 0xFFFFFFFF)
        self.cpu.w_reg(M.Register.A7, sp)
        self.cpu.w_pc(adr)
        self.done = False
        cyc = 0
        while not self.done:
            cyc += self.m.execute(50_000_000).cycles
            if cyc > 100_000_000_000:        # a whole answer is ~5e9
                die("68000 call did not return")
        return self.cpu.r_reg(M.Register.D0), cyc - 34   # minus the line-A trap


class Unicorn:
    cycles_ok = False

    def __init__(self):
        from unicorn import Uc, UC_ARCH_M68K, UC_MODE_BIG_ENDIAN
        from unicorn.m68k_const import UC_CPU_M68K_M68000, UC_M68K_REG_A7, UC_M68K_REG_D0
        self.A7, self.D0 = UC_M68K_REG_A7, UC_M68K_REG_D0
        self.uc = Uc(UC_ARCH_M68K, UC_MODE_BIG_ENDIAN)
        self.uc.ctl_set_cpu_model(UC_CPU_M68K_M68000)
        self.uc.mem_map(0, MEMKB * 1024)
        self.uc.mem_write(RET, b"\x4e\x71")

    def write(self, adr, data):
        self.uc.mem_write(adr, bytes(data))

    def read(self, adr, n):
        return bytes(self.uc.mem_read(adr, n))

    def call(self, adr, *args):
        sp = STACK - 4 * len(args) - 4
        self.uc.mem_write(sp, struct.pack(">I", RET) +
                          b"".join(struct.pack(">I", a & 0xFFFFFFFF) for a in args))
        self.uc.reg_write(self.A7, sp)
        self.uc.emu_start(adr, RET)
        return self.uc.reg_read(self.D0), 0


def s32(v):
    return v - (1 << 32) if v & 0x80000000 else v


def gate(kernel, engine, quick, oracle_probes, oracle_answers, qs, m):
    code, sy = build(kernel)
    print(f"  binary size: {len(code)} bytes (limit < 64500)")
    if len(code) >= 64500:
        die(f"engine binary too large: {len(code)} >= 64500")
    cpu = Musashi() if engine == "musashi" else Unicorn()
    blob = open(os.path.join(ROOT, "tiny.t68"), "rb").read()
    if BASE + len(code) > BLOB or BLOB + len(blob) > KVMEM:
        die("memory layout overflow")
    cpu.write(BASE, code)
    cpu.write(BLOB, blob)
    kvb, _ = cpu.call(sy["_tiny_kv_bytes"], BLOB)
    if KVMEM + kvb > STR:
        die("key/value cache does not fit the layout")
    err, _ = cpu.call(sy["_tiny_init"], BLOB, len(blob), KVMEM)
    if err:
        die(f"tiny_init failed on the 68000 (d0={err:#x})")

    fails = 0
    per_pos = {}
    nval = 0
    probes = oracle_probes[:1] if quick else oracle_probes
    for name, toks, want in probes:
        got = []
        for p, t in enumerate(toks):
            _, cyc = cpu.call(sy["_tiny_step"], t, p)
            per_pos[p] = cyc
            got += [s32(v) for v in struct.unpack(f">{m.vocab}I", cpu.read(sy["_tiny_score"], 4 * m.vocab))]
            got += [s32(v) for v in struct.unpack(f">{m.L}I", cpu.read(sy["_tiny_layer_act"], 4 * m.L))]
        nval += len(want)
        bad = [i for i in range(len(want)) if got[i] != want[i]]
        if bad:
            per = m.vocab + m.L
            i = bad[0]
            print(f"  FAIL probe {name!r}: {len(bad)} values differ, first at position "
                  f"{i // per} slot {i % per}: 68000={got[i]} oracle={want[i]}", file=sys.stderr)
            fails += 1
    print(f"  scores: {nval} values" + ("" if fails else ", all identical"))

    # The question through tiny_prefill(), then full steps: every score from
    # the separator on reads the KV caches that prefill wrote.
    pre_cyc = {}
    pfails = 0
    per = m.vocab + m.L
    for name, toks, want in probes:
        nq = len(name)
        for p, t in enumerate(toks):
            if p < nq:
                _, pre_cyc[p] = cpu.call(sy["_tiny_prefill"], t, p)
                continue
            cpu.call(sy["_tiny_step"], t, p)
            got = [s32(v) for v in struct.unpack(f">{m.vocab}I", cpu.read(sy["_tiny_score"], 4 * m.vocab))]
            got += [s32(v) for v in struct.unpack(f">{m.L}I", cpu.read(sy["_tiny_layer_act"], 4 * m.L))]
            if got != want[p * per:(p + 1) * per]:
                print(f"  FAIL prefill probe {name!r}: position {p} differs", file=sys.stderr)
                pfails += 1
                break
    fails += pfails
    print(f"  prefill: {len(probes)} probes" + ("" if pfails else ", scores after the question identical"))

    # Fast Mode prefill_planes = 5, 6 against oracle_tiny.py on first probe
    if "_tiny_fast_planes" in sy:
        import oracle_tiny as O
        ffails = 0
        toks = probes[0][1]
        nq = len(probes[0][0])
        for pl in (5, 6):
            st = O.State(m)
            cpu.write(sy["_tiny_fast_planes"], struct.pack(">I", pl))
            for p, t in enumerate(toks):
                if p < nq:
                    O.step(m, st, t, p, full=False, prefill_planes=pl)
                    cpu.call(sy["_tiny_prefill"], t, p)
                    continue
                want = O.step(m, st, t, p) + list(st.layer_act)
                cpu.call(sy["_tiny_step"], t, p)
                got = [s32(v) for v in struct.unpack(f">{m.vocab}I", cpu.read(sy["_tiny_score"], 4 * m.vocab))]
                got += [s32(v) for v in struct.unpack(f">{m.L}I", cpu.read(sy["_tiny_layer_act"], 4 * m.L))]
                if got != want:
                    print(f"  FAIL fast prefill planes={pl} pos {p}", file=sys.stderr)
                    ffails += 1
                    break
        cpu.write(sy["_tiny_fast_planes"], struct.pack(">I", 8))
        fails += ffails
        print("  fast prefill (planes 5, 6):" + (" FAIL" if ffails else " identical"))

    extra_qs = ["how many legs does a spider have",
                "which animal says hoot",
                "who invented the telephone"]
    runs = [(0, 1, qs[:2] if quick else qs[:N_THINK] + extra_qs)]
    if not quick:
        runs.append((THINK_SAMPLED[0], THINK_SAMPLED[1], qs[:2]))
    for cr, seed, qlist in runs:
        same = 0
        for q in qlist:
            cpu.write(STR, q.encode("ascii") + b"\0")
            n, _ = cpu.call(sy["_tiny_think"], STR, STR + 0x100, cr, seed)
            got = cpu.read(STR + 0x100, n).decode("ascii")
            want = oracle_answers[(q, cr, seed)]
            if got == want:
                same += 1
            else:
                print(f"  FAIL think c={cr} {q!r}: 68000={got!r} oracle={want!r}", file=sys.stderr)
                fails += 1
        print(f"  answers c={cr} s={seed}: {same}/{len(qlist)} identical")

    if not quick and "_tiny_fast_planes" in sy and "_tiny_prefix_always" in sy:
        import oracle_tiny as O
        pq = "how many legs does a spider have"
        cpu.write(sy["_tiny_fast_planes"], struct.pack(">I", 5))
        cpu.write(sy["_tiny_prefix_always"], struct.pack(">I", 0))
        cpu.write(STR, pq.encode("ascii") + b"\0")
        n0, cyc0 = cpu.call(sy["_tiny_think"], STR, STR + 0x100, 0, 1)
        got0 = cpu.read(STR + 0x100, n0).decode("ascii")
        want0 = O.think(m, pq, 0, 1, prefill_planes=5)
        if got0 != want0:
            print(f"  FAIL fast prefix_always=0 {pq!r}: 68000={got0!r} oracle={want0!r}", file=sys.stderr)
            fails += 1
        cpu.call(sy["_tiny_prefill"], 2, 0)
        cpu.write(sy["_tiny_prefix_always"], struct.pack(">I", 1))
        cpu.write(STR, pq.encode("ascii") + b"\0")
        n1, cyc1 = cpu.call(sy["_tiny_think"], STR, STR + 0x100, 0, 1)
        got1 = cpu.read(STR + 0x100, n1).decode("ascii")
        if n1 == 0 or (cpu.cycles_ok and cyc1 >= cyc0):
            print(f"  FAIL fast prefix_always=1 {pq!r}: n={n1} cyc0={cyc0} cyc1={cyc1}", file=sys.stderr)
            fails += 1
        else:
            print(f"  fast prefix_always: 0->{got0!r} ({cyc0:,} cyc), 1->{got1!r} ({cyc1:,} cyc)")
        cpu.write(sy["_tiny_fast_planes"], struct.pack(">I", 8))
        cpu.write(sy["_tiny_prefix_always"], struct.pack(">I", 0))

    if cpu.cycles_ok and per_pos:
        lo, hi = min(per_pos), max(per_pos)
        print(f"  cycles per letter (Musashi, no DMA waits): position {lo}: "
              f"{per_pos[lo]:,} = {per_pos[lo] / CPU_HZ:.2f} s;  position {hi}: "
              f"{per_pos[hi]:,} = {per_pos[hi] / CPU_HZ:.2f} s")
        if pre_cyc:
            print(f"  cycles per question letter (prefill): position 0: {pre_cyc[0]:,} = "
                  f"{pre_cyc[0] / CPU_HZ:.2f} s")
    return fails, per_pos


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kernel", choices=("asm", "c", "both"), default="both")
    ap.add_argument("--engine", choices=("musashi", "unicorn"), default="musashi")
    ap.add_argument("--quick", action="store_true", help="one probe, two questions")
    args = ap.parse_args()

    import oracle_tiny as O
    from convert_tiny import load_t68
    from ref_tiny import clean
    from verify import PROBES
    if not os.path.isfile(os.path.join(ROOT, "tiny.t68")):
        die("tiny.t68 missing: python build.py convert")
    m = load_t68(os.path.join(ROOT, "tiny.t68"))

    t0 = time.time()
    oracle_probes = []
    for q, a in PROBES:
        st = O.State(m)
        toks = [ord(c) - 30 for c in q] + [1] + [ord(c) - 30 for c in a]
        want = []
        for p, t in enumerate(toks):
            want += O.step(m, st, t, p) + list(st.layer_act)
        oracle_probes.append((q, toks, want))
    qs = [clean(l) for l in open(os.path.join(ROOT, "questions.txt"))]
    qs = [q for q in qs if q]
    answers = {}
    for q in qs[:N_THINK] + ["how many legs does a spider have",
                             "which animal says hoot",
                             "who invented the telephone"]:
        answers[(q, 0, 1)] = O.think(m, q, 0, 1)
    for q in qs[:2]:
        answers[(q, THINK_SAMPLED[0], THINK_SAMPLED[1])] = O.think(m, q, *THINK_SAMPLED)
    print(f"oracle ready ({time.time() - t0:.0f} s)")

    kernels = ("asm", "c") if args.kernel == "both" else (args.kernel,)
    total = 0
    cycles = {}
    for k in kernels:
        t0 = time.time()
        print(f"kernel {k} ({'matmul68k.s' if k == 'asm' else 'C dot product'}), vbcc +kick13, {args.engine}:")
        f, cycles[k] = gate(k, args.engine, args.quick, oracle_probes, answers, qs, m)
        total += f
        print(f"  ({time.time() - t0:.0f} s)")
    if total:
        print("FAIL: the 68000 code does not match oracle_tiny.py", file=sys.stderr)
        return 1
    print("OK: the vbcc-compiled 68000 engine matches oracle_tiny.py exactly"
          + (" with both kernels" if len(kernels) == 2 else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
