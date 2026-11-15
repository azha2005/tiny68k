#!/usr/bin/env python3
"""
build.py -- builds and checks for tiny68k. No shell required.

    python build.py ref        build ref/harness (the original TINY.C, float)
    python build.py convert    orig/TINY.BIN -> tiny.t68
    python build.py check      ref vs TINY.C, then oracle vs float (stage 1 gate)
    python build.py host       build tiny68k (host, C kernel)
    python build.py verify     scripts/verify.py: C == oracle, exactly

Stage 3 will add: amiga, amiga-c (vbcc +kick13) and a 68000-emulator gate.
"""
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
EXE = ".exe" if os.name == "nt" else ""
MSYS = r"C:\msys64\ucrt64\bin"


def env():
    """On this Windows machine gcc only works with the MSYS2 bin dir first on
    PATH (otherwise cc1 dies silently on a DLL from elsewhere)."""
    e = dict(os.environ)
    if os.name == "nt" and os.path.isdir(MSYS):
        e["PATH"] = MSYS + os.pathsep + e.get("PATH", "")
    return e


def run(cmd):
    print("+", " ".join(cmd))
    r = subprocess.run(cmd, cwd=ROOT, env=env())
    if r.returncode != 0:
        sys.exit(r.returncode)


def main():
    what = sys.argv[1] if len(sys.argv) > 1 else ""
    if what == "ref":
        if not os.path.isfile(os.path.join(ROOT, "orig", "TINY.C")):
            sys.exit("orig/TINY.C missing: copy TINY.C and TINY.BIN from "
                     "https://github.com/WollyMonjac/Tiny-2.1 into orig/")
        cc = shutil.which("gcc", path=env()["PATH"]) or "gcc"
        run([cc, "-O2", "-o", os.path.join("ref", "harness" + EXE),
             os.path.join("ref", "harness.c"), "-lm"])
    elif what == "convert":
        run([sys.executable, "convert_tiny.py", "orig/TINY.BIN", "tiny.t68"])
    elif what == "host":
        cc = shutil.which("gcc", path=env()["PATH"]) or "gcc"
        run([cc, "-O2", "-Wall", "-DA5_HOST", "-o", "tiny68k" + EXE, "tiny68k.c"])
    elif what == "verify":
        run([sys.executable, os.path.join("scripts", "verify.py")])
    elif what == "check":
        run([sys.executable, "check_ref.py"])
        run([sys.executable, "cmp_fp32.py"])
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
