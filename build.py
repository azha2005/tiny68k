#!/usr/bin/env python3
"""
build.py -- builds and checks for tiny68k. No shell required.

    python build.py ref        build ref/harness (the original TINY.C, float)
    python build.py convert    orig/TINY.BIN -> tiny.t68
    python build.py check      ref vs TINY.C, then oracle vs float (stage 1 gate)
    python build.py host       build tiny68k (host, C kernel)
    python build.py verify     scripts/verify.py: C == oracle, exactly
    python build.py amiga      vbcc +kick13 executable, asm kernel -> tiny68k
    python build.py amiga-c    same with the C kernel -> tiny68k_c
    python build.py gate       scripts/m68k_gate.py: vbcc 68000 code == oracle
    python build.py gui        the GUI (tinyui.c, hardware takeover) -> tinygui
    python build.py gui-host   the GUI on the host, drawing to files -> tinyui
    python build.py gui-uae    emu/gui_test.py: tinygui in WinUAE vs the host
    python build.py gui-play   emu/play_gui.py: tinygui in WinUAE, to use by hand
    python build.py uae        emu/run_test.py: the executables in WinUAE
                               (add --exact for cycle-exact timing)
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
    e.setdefault("VBCC", VBCC)
    e["PATH"] = os.path.join(VBCC, "bin") + os.pathsep + e.get("PATH", "")
    if os.name == "nt" and os.path.isdir(MSYS):
        e["PATH"] = MSYS + os.pathsep + e.get("PATH", "")
    return e


VBCC = os.environ.get("VBCC", r"C:\Users\JC\vbcc")


def vbcc(tool):
    p = os.path.join(VBCC, "bin", tool + EXE)
    return p if os.path.isfile(p) else tool


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
    elif what in ("amiga", "amiga-c"):
        asm = what == "amiga"
        out = "tiny68k" if asm else "tiny68k_c"
        obj = out + ".o"
        run([vbcc("vc"), "+kick13", "-c99", "-O2"] + (["-DUSE_ASM"] if asm else []) +
            ["-c", "tiny68k.c", "-o", obj])
        objs = [obj]
        if asm:
            run([vbcc("vasmm68k_mot"), "-quiet", "-Fhunk", "-m68000", "-no-opt",
                 "matmul68k.s", "-o", "matmul68k.o"])
            objs.append("matmul68k.o")
        run([vbcc("vc"), "+kick13", "-o", out] + objs)
    elif what == "gui":
        for src in ("tiny68k", "conio68k", "render68k", "tinyui", "tui_amiga"):
            run([vbcc("vc"), "+kick13", "-c99", "-O2", "-DUSE_ASM", "-DTINY_ENGINE_ONLY",
                 "-c", src + ".c", "-o", "g_" + src + ".o"])
        for src in ("matmul68k", "tui68k"):
            run([vbcc("vasmm68k_mot"), "-quiet", "-Fhunk", "-m68000", "-no-opt",
                 src + ".s", "-o", src + ".o"])
        run([vbcc("vc"), "+kick13", "-o", "tinygui",
             "g_tiny68k.o", "g_conio68k.o", "g_render68k.o", "g_tinyui.o",
             "g_tui_amiga.o", "matmul68k.o", "tui68k.o"])
    elif what == "gui-host":
        cc = shutil.which("gcc", path=env()["PATH"]) or "gcc"
        run([cc, "-O2", "-Wall", "-Wno-format-overflow", "-DA5_HOST", "-DTINY_ENGINE_ONLY",
             "-o", "tinyui" + EXE, "tiny68k.c", "conio68k.c", "render68k.c", "tinyui.c",
             "tui_host.c"])
    elif what == "gui-play":
        run([sys.executable, os.path.join("emu", "play_gui.py")] + sys.argv[2:])
    elif what == "gui-uae":
        run([sys.executable, os.path.join("emu", "gui_test.py")] + sys.argv[2:])
    elif what == "gate":
        run([sys.executable, os.path.join("scripts", "m68k_gate.py")])
    elif what == "uae":
        run([sys.executable, os.path.join("emu", "run_test.py")] + sys.argv[2:])
    elif what == "check":
        run([sys.executable, "check_ref.py"])
        run([sys.executable, "cmp_fp32.py"])
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
