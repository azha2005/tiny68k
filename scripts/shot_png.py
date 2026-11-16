#!/usr/bin/env python3
"""
shot_png.py -- shot_<name>.bin (tui_host.c sys_shot) -> one PNG per theme.

Hires non-interlaced pixels are about twice as tall as wide, so each line
is drawn twice (640 x 400), which is how the screen looks on a monitor.

    python scripts/shot_png.py shot_screen.bin [outdir]
"""
import os
import struct
import sys
from PIL import Image

NAMES = ["wb13", "wb20", "neon"]
PB = 80 * 200


def main():
    src = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.dirname(src) or "."
    raw = open(src, "rb").read()
    assert raw[:4] == b"TSH1"
    n = struct.unpack_from(">H", raw, 4)[0]
    off = 6
    base = os.path.splitext(os.path.basename(src))[0]
    for t in range(n):
        rgb = struct.unpack_from(">4H", raw, off)
        off += 8
        p0 = raw[off:off + PB]
        p1 = raw[off + PB:off + 2 * PB]
        off += 2 * PB
        pal = []
        for c in rgb:
            pal += [((c >> 8) & 15) * 17, ((c >> 4) & 15) * 17, (c & 15) * 17]
        img = Image.new("P", (640, 400))
        img.putpalette(pal + [0] * (768 - len(pal)))
        px = bytearray(640 * 400)
        for y in range(200):
            row = bytearray(640)
            for xb in range(80):
                a, b = p0[y * 80 + xb], p1[y * 80 + xb]
                for i in range(8):
                    row[xb * 8 + i] = ((a >> (7 - i)) & 1) | (((b >> (7 - i)) & 1) << 1)
            px[(2 * y) * 640:(2 * y + 1) * 640] = row
            px[(2 * y + 1) * 640:(2 * y + 2) * 640] = row
        img.putdata(bytes(px))
        name = os.path.join(out, f"{base}_{NAMES[t] if t < len(NAMES) else t}.png")
        img.convert("RGB").save(name)
        print("wrote", name)


if __name__ == "__main__":
    main()
