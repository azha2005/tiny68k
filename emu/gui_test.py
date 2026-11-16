#!/usr/bin/env python3
"""
emu/gui_test.py -- tinygui (the GUI, hardware taken over) in WinUAE.

    python emu/gui_test.py              # fast A500, /dump1, all three themes
    python emu/gui_test.py --exact      # cycle-exact
    python emu/gui_test.py --keys       # typed question + F5 through the keyboard

--keys runs `tinygui /keytest` and types into the WinUAE window (SendInput,
PC scancodes; WinUAE turns them into Amiga raw keys, so the keyboard
interrupt, the keymap with shift, and F5 all run). The host gets the same
keys through TINY_KEYS. The shot is taken at "quit" and carries the theme on
screen in its header, so F5 is checked too; log.txt must show the 50 Hz
timer counted. It steals the focus for about a minute and stops if the
WinUAE window loses it.

Boots a floppy whose startup-sequence runs `tinygui /dump1 <theme>`: the
boot screen while tiny.t68 loads, the takeover, one demo question typed and
answered, then the screen kept and written to DF0:shot_screen.bin once the
OS is back. The same flags on the host (tinyui, tui_host.c) give the
expected file, which must be identical byte for byte: same cells, same
attributes, same pixels in every theme. The file appearing at all shows the
OS came back (DOS writes it after sys_done()).

Needs what run_test.py needs, plus `python build.py gui` and `gui-host`.
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time

EMU = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(EMU)
sys.path.insert(0, EMU)
import run_test as R  # noqa: E402

R.ADF = os.path.join(EMU, "tinygui-test.adf")
R.CFG = os.path.join(EMU, "tinygui.uae")
SHOT = "shot_screen.bin"
SHOT_BYTES = 6 + 3 * (8 + 2 * 16000) + 2 * 80 * 25


def host_shot(flags, work, keys=None):
    exe = os.path.join(ROOT, "build", "tinyui" + (".exe" if os.name == "nt" else ""))
    if not os.path.exists(exe):
        sys.exit("missing tinyui: run python build.py gui-host")
    shutil.copyfile(os.path.join(ROOT, "tiny.t68"), os.path.join(work, "tiny.t68"))
    e = R_env()
    e["TINY_ROM"] = os.path.abspath(R.ROM)
    if keys:
        e["TINY_KEYS"] = keys
    r = subprocess.run([exe] + flags, cwd=work, env=e, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit("host tinyui failed:\n" + r.stdout + r.stderr)
    return open(os.path.join(work, SHOT), "rb").read()


def R_env():
    sys.path.insert(0, ROOT)
    import build
    return build.env()


def build_disk(flags, redirect=""):
    for f in (os.path.join("build", "tinygui"), "tiny.t68"):
        if not os.path.exists(os.path.join(ROOT, f)):
            sys.exit(f"missing {f}: run python build.py gui / convert")
    tmp = tempfile.mkdtemp()
    ss = os.path.join(tmp, "startup-sequence")
    with open(ss, "w", newline="\n") as f:
        f.write("tinygui " + (redirect + " " if redirect else "") + " ".join(flags) + "\n")
    if os.path.exists(R.ADF):
        os.remove(R.ADF)
    R.xdf(R.ADF, "create")
    R.xdf(R.ADF, "format", "TINYGUI", "ofs")
    for f in (os.path.join("build", "tinygui"), "tiny.t68"):
        R.xdf(R.ADF, "write", os.path.join(ROOT, f))
    R.xdf(R.ADF, "makedir", "s")
    R.xdf(R.ADF, "write", ss, "s/startup-sequence")
    R.xdf(R.ADF, "boot", "install", "boot1x")
    shutil.rmtree(tmp)


def diff(a, b):
    if len(a) != len(b):
        return f"sizes differ: {len(a)} vs {len(b)}"
    if a[:6] != b[:6]:
        return f"headers differ (theme on screen {a[4]} vs {b[4]})"
    names = ["Workbench 1.3", "Workbench 2.0", "Neon"]
    for t in range(3):
        o = 6 + t * 32008
        if a[o:o + 8] != b[o:o + 8]:
            return f"{names[t]}: colours differ"
        pa, pb = a[o + 8:o + 32008], b[o + 8:o + 32008]
        if pa != pb:
            bad = [i for i in range(len(pa)) if pa[i] != pb[i]]
            i = bad[0] % 16000
            return (f"{names[t]}: {len(bad)} plane bytes differ, first at "
                    f"text cell {i % 80 + 1},{i // 640 + 1}")
    o = 6 + 3 * 32008
    ca, cb = a[o:o + 2000], b[o:o + 2000]
    if ca != cb:
        i = next(i for i in range(2000) if ca[i] != cb[i])
        return f"characters differ, first at {i % 80 + 1},{i // 80 + 1}: {ca[i]} vs {cb[i]}"
    return "attributes differ"


# ---- typing into WinUAE ----
SC = dict(zip("qwertyuiop", range(0x10, 0x1A)))
SC.update(zip("asdfghjkl", range(0x1E, 0x27)))
SC.update(zip("zxcvbnm", range(0x2C, 0x33)))
SC.update({" ": 0x39, "{ENTER}": 0x1C, "{F5}": 0x3F})
QUESTION = "What is the capital of peru"


def winuae_window(pid):
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(h, _):
        p = wintypes.DWORD()
        u.GetWindowThreadProcessId(h, ctypes.byref(p))
        if p.value == pid and u.IsWindowVisible(h):
            found.append(h)
        return True
    u.EnumWindows(cb, 0)
    return found[0] if found else None


class Typist:
    def __init__(self, hwnd):
        import ctypes
        from ctypes import wintypes
        self.c, self.u, self.hwnd = ctypes, ctypes.windll.user32, hwnd

        class KI(ctypes.Structure):
            _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                        ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

        class INPUT(ctypes.Structure):
            _fields_ = [("type", wintypes.DWORD), ("ki", KI), ("pad", ctypes.c_byte * 8)]
        self.KI, self.INPUT = KI, INPUT

    def focus(self):
        u = self.u
        u.keybd_event(0x12, 0, 0, 0)            # Alt: lets SetForegroundWindow work
        u.SetForegroundWindow(self.hwnd)
        u.keybd_event(0x12, 0, 2, 0)
        time.sleep(1.0)
        return u.GetForegroundWindow() == self.hwnd

    def key(self, sc, up):
        if self.u.GetForegroundWindow() != self.hwnd:
            raise RuntimeError("WinUAE lost the focus: stopped typing")
        i = self.INPUT(type=1, ki=self.KI(0, sc, 8 | (2 if up else 0), 0, 0))
        self.u.SendInput(1, self.c.byref(i), self.c.sizeof(i))
        time.sleep(0.06)

    def type(self, text):
        i = 0
        while i < len(text):
            tok = text[i]
            if text[i] == "{":
                tok = text[i:text.index("}", i) + 1]
            shift = tok.isupper() and len(tok) == 1
            if shift:
                self.key(0x2A, False)
            self.key(SC[tok.lower() if len(tok) == 1 else tok], False)
            self.key(SC[tok.lower() if len(tok) == 1 else tok], True)
            if shift:
                self.key(0x2A, True)
            time.sleep(0.08)
            i += len(tok)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exact", action="store_true")
    ap.add_argument("--rom", default=R.ROM)
    ap.add_argument("--timeout", type=int, default=0)
    ap.add_argument("--theme", default="", help="/wb13, /wb20 or /neon")
    ap.add_argument("--keys", action="store_true", help="type a question and F5 (steals focus)")
    args = ap.parse_args()
    R.ROM = args.rom
    if not os.path.exists(args.rom):
        sys.exit("no Kickstart ROM at " + args.rom)
    timeout = args.timeout or (900 if args.exact else 600)
    flags = ["/keytest" if args.keys else "/dump1"] + ([args.theme] if args.theme else [])
    keys = QUESTION + "{ENTER}{WAIT}{F5}quit{ENTER}"

    work = tempfile.mkdtemp()
    want = host_shot(flags, work, keys if args.keys else None)
    build_disk(flags, ">DF0:log.txt" if args.keys else "")
    R.write_config(args.rom, args.exact)
    out = os.path.join(EMU, "out")
    os.makedirs(out, exist_ok=True)

    print(f"WinUAE: tinygui {' '.join(flags)}, {'cycle-exact' if args.exact else 'fast'}, "
          f"timeout {timeout}s")
    p = subprocess.Popen([R.WINUAE, "-f", R.CFG], cwd=EMU)
    t0 = time.time()
    got = None
    if args.keys:
        try:
            typist = None
            for _ in range(20):
                time.sleep(1)
                h = winuae_window(p.pid)
                if h:
                    typist = Typist(h)
                    break
            if not typist:
                raise RuntimeError("no WinUAE window")
            time.sleep(30)                      # boot, load, takeover
            if not typist.focus():
                raise RuntimeError("could not give WinUAE the focus")
            typist.type(QUESTION + "{ENTER}")
            time.sleep(40 if not args.exact else 240)   # the answer, spoken
            typist.type("{F5}")
            time.sleep(2)
            typist.type("quit{ENTER}")
        except RuntimeError as e:
            print("typing:", e)
            p.kill()
            print("FAIL")
            return 1
    while time.time() - t0 < timeout and p.poll() is None:
        time.sleep(5)
        txt = R.read_back(SHOT, out)
        if txt is not None and len(txt) >= SHOT_BYTES:
            got = txt.encode("latin-1")
            break
    elapsed = time.time() - t0
    time.sleep(3)
    if p.poll() is None:
        p.kill()
    shutil.rmtree(work, ignore_errors=True)
    print(f"emulator ran {elapsed:.0f}s")
    if got is None:
        print("no shot_screen.bin on the floppy: tinygui failed, hung, or the OS did not come back")
        print("FAIL")
        return 1
    with open(os.path.join(out, SHOT), "wb") as f:
        f.write(got)
    if args.keys:
        log = R.read_back("log.txt", out) or ""
        print("log.txt:", log.strip().replace("\n", " | "))
        ticks = [int(l.split()[1]) for l in log.splitlines() if l.startswith("ticks ")]
        if not ticks or ticks[0] < 50 * 20:
            print("FAIL: the 50 Hz timer did not count (sys_ticks)")
            return 1
    if got == want:
        print(f"{SHOT}: identical to the host ({len(got)} bytes, 3 themes)")
        print("OK")
        return 0
    print(f"{SHOT}: DIFFERENT from the host -- {diff(got, want)}")
    with open(os.path.join(out, "host_" + SHOT), "wb") as f:
        f.write(want)
    print("FAIL (both files in emu/out/; scripts/shot_png.py renders them)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
