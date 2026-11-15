#!/usr/bin/env python3
"""
emu/run_test.py -- the real Amiga executables under WinUAE, Kickstart 1.2,
A500 with 512K chip + 512K A501 slow RAM.

    python emu/run_test.py            # fast: both kernels, A/B + vs oracle
    python emu/run_test.py --exact    # cycle-exact A500: asm kernel, timing

Builds a bootable OFS floppy (amitools xdftool) with tiny68k, tiny68k_c,
tiny.t68 and a startup-sequence that runs `-ask -time` with output to DF0:,
boots WinUAE, polls the floppy for a done marker, then reads the outputs
back. This checks what m68k_gate.py cannot: the hunk executable itself,
vbcc's kick13 startup and libc, file loading, malloc of 386 KB on a 1 MB
machine, and (with --exact) real A500 timing including DMA contention.

Needs: WinUAE, a Kickstart 1.2 ROM (default: ../amiga260k/emu/kick12.rom,
never copied into this repo), `python build.py amiga` and `amiga-c` done.
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EMU = os.path.join(ROOT, "emu")
WINUAE = r"C:\Program Files\WinUAE\winuae64.exe"
ROM = os.path.join(ROOT, "..", "amiga260k", "emu", "kick12.rom")
ADF = os.path.join(EMU, "tiny68k-test.adf")
CFG = os.path.join(EMU, "tiny68k.uae")

QUESTIONS = ["what is the capital of peru", "is a whale a fish"]


def xdf(adf, *cmds, check=True):
    argv = [sys.executable, "-m", "amitools.tools.xdftool", "-f", adf] + list(cmds)
    r = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True)
    if check and r.returncode != 0:
        sys.exit("xdftool failed: " + " ".join(cmds) + "\n" + r.stdout + r.stderr)
    return r


def build_disk(exact):
    files = ["tiny68k", "tiny.t68"] + ([] if exact else ["tiny68k_c"])
    for f in files:
        if not os.path.exists(os.path.join(ROOT, f)):
            sys.exit(f"missing {f}: run python build.py amiga / amiga-c / convert")
    qs = QUESTIONS[:1] if exact else QUESTIONS
    tmp = tempfile.mkdtemp()
    qf = os.path.join(tmp, "q.txt")
    with open(qf, "w", newline="\n") as f:
        f.write("\n".join(qs) + "\n")
    lines = ["tiny68k <q.txt >DF0:out_asm.txt tiny.t68 -ask -time"]
    if not exact:
        lines.append("tiny68k_c <q.txt >DF0:out_c.txt tiny.t68 -ask -time")
    lines.append('echo >DF0:done.txt "done"')
    ss = os.path.join(tmp, "startup-sequence")
    with open(ss, "w", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    if os.path.exists(ADF):
        os.remove(ADF)
    xdf(ADF, "create")
    xdf(ADF, "format", "TINYTEST", "ofs")
    for f in files:
        xdf(ADF, "write", os.path.join(ROOT, f))
    xdf(ADF, "write", qf, "q.txt")
    xdf(ADF, "makedir", "s")
    xdf(ADF, "write", ss, "s/startup-sequence")
    xdf(ADF, "boot", "install", "boot1x")
    shutil.rmtree(tmp)
    return qs


def write_config(rom, exact):
    cfg = [
        "config_description=tiny68k test (A500, KS 1.2, 512K + 512K A501)",
        "use_gui=no",
        "chipset=ocs",
        "chipset_compatible=A500",
        "cpu_type=68000",
        "cpu_compatible=true" if exact else "cpu_compatible=false",
        "cpu_speed=real" if exact else "cpu_speed=max",
        "cycle_exact=true" if exact else "cycle_exact=false",
        "blitter_cycle_exact=true" if exact else "blitter_cycle_exact=false",
        "immediate_blits=false" if exact else "immediate_blits=true",
        "chipmem_size=1",
        "bogomem_size=2",
        "fastmem_size=0",
        "kickstart_rom_file=%s" % os.path.abspath(rom),
        "floppy0=%s" % ADF,
        "floppy0type=0",
        "floppy0wp=false",
        "nr_floppies=1",
        "floppy_speed=800" if not exact else "floppy_speed=100",
        "win32.start_not_captured=true",
        "win32.nonotificationicon=true",
    ]
    with open(CFG, "w", newline="\n") as f:
        f.write("\n".join(cfg) + "\n")


def read_back(name, dst_dir):
    """Copy the ADF first: WinUAE may still have it open."""
    snap = os.path.join(dst_dir, "snap.adf")
    shutil.copyfile(ADF, snap)
    dst = os.path.join(dst_dir, name)
    if os.path.exists(dst):
        os.remove(dst)
    r = xdf(snap, "read", name, dst, check=False)
    if r.returncode != 0 or not os.path.exists(dst):
        return None
    return open(dst, "rb").read().decode("latin-1")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exact", action="store_true", help="cycle-exact A500, timing")
    ap.add_argument("--rom", default=ROM)
    ap.add_argument("--timeout", type=int, default=0)
    args = ap.parse_args()
    if not os.path.exists(args.rom):
        sys.exit("no Kickstart ROM at " + args.rom)
    if not os.path.exists(WINUAE):
        sys.exit("WinUAE not found at " + WINUAE)
    timeout = args.timeout or (900 if args.exact else 600)

    qs = build_disk(args.exact)
    write_config(args.rom, args.exact)
    out = os.path.join(EMU, "out")
    os.makedirs(out, exist_ok=True)

    print(f"WinUAE: A500 KS1.2 512K+512K, {'cycle-exact' if args.exact else 'fast'}, "
          f"{len(qs)} question(s), timeout {timeout}s")
    p = subprocess.Popen([WINUAE, "-f", CFG], cwd=EMU)
    t0 = time.time()
    done = None
    while time.time() - t0 < timeout and p.poll() is None:
        time.sleep(15)
        done = read_back("done.txt", out)
        if done:
            break
    elapsed = time.time() - t0
    time.sleep(3)
    if p.poll() is None:
        p.kill()
        time.sleep(2)
    print(f"emulator ran {elapsed:.0f}s" + ("" if done else " -- NO DONE MARKER (timeout)"))

    sys.path.insert(0, ROOT)
    import oracle_tiny as O
    from convert_tiny import load_t68
    m = load_t68(os.path.join(ROOT, "tiny.t68"))
    want = [O.think(m, q) for q in qs]

    ok = True
    outs = {}
    for name in (["out_asm.txt"] + ([] if args.exact else ["out_c.txt"])):
        txt = read_back(name, out)
        if txt is None:
            print(f"{name}: not on the floppy")
            ok = False
            continue
        lines = [l.rstrip("\r") for l in txt.split("\n") if l.strip()]
        answers = [l for l in lines if not l.lstrip().startswith("[")]
        timing = [l.strip() for l in lines if l.lstrip().startswith("[")]
        outs[name] = answers
        same = answers == want
        ok &= same
        print(f"{name}: {'answers match the oracle' if same else 'MISMATCH'}")
        for q, a, t in zip(qs, answers, timing + [""] * len(qs)):
            print(f"   {q!r} -> {a!r} {t}")
        if not same:
            print(f"   oracle: {want}")
    if len(outs) == 2:
        ab = outs["out_asm.txt"] == outs["out_c.txt"]
        ok &= ab
        print("asm vs C kernel: " + ("IDENTICAL" if ab else "DIFFERENT"))
    print("OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
