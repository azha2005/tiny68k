#!/usr/bin/env python3
"""
emu/play_gui.py -- boot tinygui in WinUAE to use it by hand.

    python emu/play_gui.py            # cycle-exact A500, real speed, sound on
    python emu/play_gui.py --fast     # as fast as the host allows

Cycle-exact CPU and chipset (so the answer comes at real A500 speed), but a
turbo floppy: loading tiny.t68 at the real 5 KB/s would take over a minute
and changes nothing once it is in memory. The startup-sequence runs
`tinygui` with any extra arguments (e.g. `/neon`, a creativity digit).
WinUAE is left running; close its window when done.
"""
import os
import subprocess
import sys

EMU = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, EMU)
import gui_test as G  # noqa: E402

R = G.R
R.ADF = os.path.join(EMU, "tinygui-play.adf")
R.CFG = os.path.join(EMU, "tinygui-play.uae")


def main():
    args = sys.argv[1:]
    fast = "--fast" in args
    flags = [a for a in args if a != "--fast"]
    G.build_disk(flags)
    R.write_config(R.ROM, not fast)
    cfg = open(R.CFG).read().splitlines()
    cfg = [l for l in cfg if not l.startswith(("floppy_speed=", "win32.start_not_captured",
                                               "gfx_framerate="))]
    cfg += ["floppy_speed=800", "gfx_framerate=1", "win32.start_not_captured=false"]
    with open(R.CFG, "w", newline="\n") as f:
        f.write("\n".join(cfg) + "\n")
    flagsw = 0x00000008 if os.name == "nt" else 0          # DETACHED_PROCESS
    subprocess.Popen([R.WINUAE, "-f", R.CFG], cwd=EMU, creationflags=flagsw)
    print("WinUAE started:", "fast" if fast else "cycle-exact A500", "- tinygui", " ".join(flags))


if __name__ == "__main__":
    main()
