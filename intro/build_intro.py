#!/usr/bin/env python3
"""
intro/build_intro.py -- Build the standalone AMIGA 500 startup intro test ADF
and cycle-exact WinUAE config:
  emu/intro_test.adf
  emu/intro_test.uae
"""
import os
import shutil
import subprocess
import sys
import tempfile

INTRO = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(INTRO)
EMU = os.path.join(ROOT, "emu")
VBCC = os.environ.get("VBCC", r"C:\Users\JC\vbcc")
ROM = os.path.join(ROOT, "..", "amiga260k", "emu", "kick12.rom")
ADF = os.path.join(EMU, "intro_test.adf")
CFG = os.path.join(EMU, "intro_test.uae")


def env():
    e = dict(os.environ)
    e.setdefault("VBCC", VBCC)
    e["PATH"] = os.path.join(VBCC, "bin") + os.pathsep + e.get("PATH", "")
    return e


def run(cmd):
    print("+", " ".join(cmd))
    r = subprocess.run(cmd, cwd=INTRO, env=env())
    if r.returncode != 0:
        sys.exit(r.returncode)


def xdf(*cmds):
    run([sys.executable, "-m", "amitools.tools.xdftool", "-f", ADF] + list(cmds))


def main():
    exe = ".exe" if os.name == "nt" else ""
    vc = os.path.join(VBCC, "bin", "vc" + exe)
    vasm = os.path.join(VBCC, "bin", "vasmm68k_mot" + exe)

    run([vc, "+kick13", "-c99", "-O2", "-c", "intro.c", "-o", "intro.o"])
    run([vasm, "-quiet", "-Fhunk", "-m68000", "-no-opt", "intro68k.s", "-o", "intro68k.o"])
    run([vc, "+kick13", "-o", "intro", "intro.o", "intro68k.o"])

    tmp = tempfile.mkdtemp()
    ss = os.path.join(tmp, "startup-sequence")
    with open(ss, "w", newline="\n") as f:
        f.write("intro\n")
    if os.path.exists(ADF):
        os.remove(ADF)
    xdf("create")
    xdf("format", "AMIGA500", "ofs")
    xdf("write", os.path.join(INTRO, "intro"))
    xdf("makedir", "s")
    xdf("write", ss, "s/startup-sequence")
    xdf("boot", "install", "boot1x")
    shutil.rmtree(tmp)

    cfg = [
        "config_description=AMIGA 500 Startup Intro Test (A500, KS 1.2, 512K + 512K A501)",
        "use_gui=no",
        "chipset=ocs",
        "chipset_compatible=A500",
        "cpu_type=68000",
        "cpu_compatible=true",
        "cpu_speed=real",
        "cycle_exact=true",
        "blitter_cycle_exact=true",
        "immediate_blits=false",
        "chipmem_size=1",
        "bogomem_size=2",
        "fastmem_size=0",
        "kickstart_rom_file=%s" % os.path.abspath(ROM),
        "floppy0=%s" % os.path.abspath(ADF),
        "floppy0type=0",
        "floppy0wp=false",
        "nr_floppies=1",
        "floppy_speed=800",
        "gfx_width=720",
        "gfx_height=568",
        "gfx_lores=true",
        "gfx_linemode=double",
        "gfx_framerate=1",
        "sound_output=exact",
        "sound_stereo=mixed",
        "win32.start_not_captured=true",
        "win32.inactive_pause=false",
        "win32.inactive_nosound=false",
        "win32.nonotificationicon=true",
    ]
    with open(CFG, "w", newline="\n") as f:
        f.write("\n".join(cfg) + "\n")
    print(f"Built {ADF} and {CFG}")


if __name__ == "__main__":
    main()
