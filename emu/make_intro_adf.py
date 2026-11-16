#!/usr/bin/env python3
"""
emu/make_intro_adf.py -- the TINY disk with the CD32-style "AMIGA 500" intro
playing while tinygui loads.

    python emu/make_intro_adf.py SRC.adf [DST.adf] [--tinygui EXE] [--intro DIR] [--run]

SRC (e.g. TINY21-A500.adf) is not modified. DST (default
emu/TINY21-A500-intro.adf) is a copy with:
  intro       DIR/intro_bg: cd32intro `python build.py exe 500 -DBG`
  intro.dat   DIR/intro.dat: cd32intro `python tools/pack500.py`
  tinygui     EXE: a tinygui that knows the intro's "TINY.INTRO" port
              (default ../tiny68k-emb-intro/tinygui)
  s/startup-sequence: `intro`, then SRC's own lines (e.g. `tinygui /exact`).
DIR defaults to ../cd32intro/build500. The boot block and tiny.t68 stay.
--run boots DST in cycle-exact WinUAE (A500, KS 1.2, 512K + 512K, real floppy).
"""
import os
import shutil
import subprocess
import sys
import tempfile

EMU = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(EMU)
UP = os.path.dirname(ROOT)
sys.path.insert(0, EMU)
import run_test as R  # noqa: E402


def xdf(adf, *cmds):
    r = subprocess.run([sys.executable, "-m", "amitools.tools.xdftool", "-f", adf] + list(cmds),
                       capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit("xdftool %s: %s%s" % (" ".join(cmds), r.stdout, r.stderr))
    return r.stdout


def opt(args, name, default):
    if name in args:
        i = args.index(name)
        v = args[i + 1]
        del args[i:i + 2]
        return os.path.abspath(v)
    return default


def main():
    args = sys.argv[1:]
    run = "--run" in args
    args = [a for a in args if a != "--run"]
    tinygui = opt(args, "--tinygui", os.path.join(UP, "tiny68k-emb-intro", "tinygui"))
    idir = opt(args, "--intro", os.path.join(UP, "cd32intro", "build500"))
    if not args:
        sys.exit(__doc__)
    src = os.path.abspath(args[0])
    dst = os.path.abspath(args[1]) if len(args) > 1 else os.path.join(EMU, "TINY21-A500-intro.adf")
    if src == dst:
        sys.exit("DST must differ from SRC")

    shutil.copyfile(src, dst)
    tmp = tempfile.mkdtemp()
    ss = os.path.join(tmp, "startup-sequence")
    xdf(dst, "read", "s/startup-sequence", ss)
    old = open(ss, "rb").read().decode("latin-1").replace("\r", "")
    lines = [l for l in old.split("\n") if l.strip() and l.strip().split()[0] != "intro"]
    with open(ss, "w", newline="\n") as f:
        f.write("\n".join(["intro"] + lines) + "\n")
    names = xdf(dst, "list")
    for n in ("intro", "intro.dat", "tinygui"):
        if "\n  %s " % n in names:
            xdf(dst, "delete", n)
    xdf(dst, "write", tinygui, "tinygui")
    xdf(dst, "write", os.path.join(idir, "intro_bg"), "intro")
    xdf(dst, "write", os.path.join(idir, "intro.dat"), "intro.dat")
    xdf(dst, "delete", "s/startup-sequence")
    xdf(dst, "write", ss, "s/startup-sequence")
    print(open(ss).read(), end="")
    shutil.rmtree(tmp)
    print(xdf(dst, "list"))
    print("Built", dst)

    if run:
        R.ADF = dst
        R.CFG = os.path.splitext(dst)[0] + ".uae"
        R.write_config(R.ROM, True)
        cfg = [l for l in open(R.CFG).read().splitlines()
               if not l.startswith(("win32.start_not_captured", "gfx_framerate="))]
        cfg += ["gfx_framerate=1", "win32.start_not_captured=false"]
        with open(R.CFG, "w", newline="\n") as f:
            f.write("\n".join(cfg) + "\n")
        subprocess.Popen([R.WINUAE, "-f", R.CFG], cwd=EMU,
                         creationflags=0x00000008 if os.name == "nt" else 0)
        print("WinUAE started (cycle-exact, real floppy speed)")


if __name__ == "__main__":
    main()
