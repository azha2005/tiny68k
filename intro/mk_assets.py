#!/usr/bin/env python3
"""
intro/mk_assets.py -- Generate OCS planar assets, Copper palettes, and Paula
synth waveforms for the AMIGA 500 CD32-style startup intro test ADF.

Outputs:
  intro/intro_data.c   (C arrays for planar bitmaps, palette tables, audio)
  scratch/intro_preview.png  (preview composite for visual inspection)
"""
import math
import os
import sys
import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INTRO = os.path.dirname(os.path.abspath(__file__))

W, H = 320, 256
DISK_W, DISK_H = 208, 72     # 192px art + 16px right pad for zero-artifact Blitter shift
DISK_FRAMES = 8              # 8 smooth perspective steps (ping-ponged in C -> 14-step cycle)
LOGO_W, LOGO_H = 288, 64     # multiple of 16 px wide (36 bytes/row)

# 16-color OCS palette layout:
#  0: space black ($000)
#  1..3: stars (dim blue, medium cyan-white, bright white)
#  4..7: diskette body & label (dark navy/slate, mid slate-blue, light grey, cream-white)
#  8..11: metallic shutter + rainbow sheen (Copper overrides per scanline!)
#  12..15: AMIGA 500 logo (dark shadow, coral red, bright orange/gold, silver-white)
BASE_PAL = [
    (0x00, 0x00, 0x00),  # 0 bg
    (0x44, 0x55, 0x88),  # 1 star dim
    (0x99, 0xBB, 0xEE),  # 2 star mid
    (0xFF, 0xFF, 0xFF),  # 3 star bright
    (0x11, 0x22, 0x44),  # 4 disk dark
    (0x22, 0x44, 0x77),  # 5 disk mid blue
    (0x44, 0x66, 0x99),  # 6 disk light blue / edge
    (0xEE, 0xEE, 0xDD),  # 7 label cream-white
    (0x55, 0x55, 0x66),  # 8 metal dark
    (0x88, 0x99, 0xAA),  # 9 metal mid
    (0xCC, 0xDD, 0xEE),  # 10 metal bright
    (0xFF, 0xAA, 0x55),  # 11 metal rainbow highlight
    (0x66, 0x11, 0x00),  # 12 logo shadow
    (0xEE, 0x44, 0x22),  # 13 AMIGA coral-red
    (0xFF, 0x88, 0x44),  # 14 AMIGA highlight
    (0xEE, 0xEE, 0xFF),  # 15 500 silver-white
]


def to_rgb444(r, g, b):
    return ((r >> 4) << 8) | ((g >> 4) << 4) | (b >> 4)


def render_logo():
    """Render 'AMIGA 500' in classic Commodore italic serif + Eurostile-style 500."""
    # Render at 2x supersampled grayscale/mask then downsample cleanly to indexed 12..15
    S = 2
    img = Image.new("L", (LOGO_W * S, LOGO_H * S), 0)
    d = ImageDraw.Draw(img)

    # Load Georgia Bold Italic (closest to Commodore AMIGA serif italic) & Impact/Arial Black for 500
    f_amiga = ImageFont.truetype(r"C:\Windows\Fonts\georgiaz.ttf", 40 * S)
    f_500 = ImageFont.truetype(r"C:\Windows\Fonts\arialbd.ttf", 34 * S)
    f_tm = ImageFont.truetype(r"C:\Windows\Fonts\arialbd.ttf", 10 * S)

    # Draw shadow first, then main mask separately
    m_amiga = Image.new("L", (LOGO_W * S, LOGO_H * S), 0)
    da = ImageDraw.Draw(m_amiga)
    da.text((6 * S, 12 * S), "AMIGA", font=f_amiga, fill=255)

    m_500 = Image.new("L", (LOGO_W * S, LOGO_H * S), 0)
    d5 = ImageDraw.Draw(m_500)
    # Slightly widen/stretch 500 horizontally for Eurostile CD32 feel
    tmp = Image.new("L", (140 * S, 48 * S), 0)
    dt = ImageDraw.Draw(tmp)
    dt.text((2 * S, 2 * S), "500", font=f_500, fill=255)
    tmp = tmp.resize((int(tmp.width * 1.15), tmp.height), Image.BILINEAR)
    m_500.paste(tmp, (164 * S, 12 * S))
    d5.text((254 * S, 12 * S), "TM", font=f_tm, fill=255)

    # Downsample crisp
    a_arr = np.asarray(m_amiga.resize((LOGO_W, LOGO_H), Image.BILINEAR))
    n_arr = np.asarray(m_500.resize((LOGO_W, LOGO_H), Image.BILINEAR))

    idx = np.zeros((LOGO_H, LOGO_W), dtype=np.uint8)
    # Shadow offset (+2, +2)
    a_sh = np.zeros_like(a_arr)
    a_sh[2:, 2:] = a_arr[:-2, :-2]
    n_sh = np.zeros_like(n_arr)
    n_sh[2:, 2:] = n_arr[:-2, :-2]

    idx[(a_sh > 90) | (n_sh > 90)] = 12

    # AMIGA body (13) with top/left bevel highlight (14)
    a_mask = a_arr > 110
    idx[a_mask] = 13
    # Top edge bevel on AMIGA
    a_up = np.zeros_like(a_mask)
    a_up[1:, :] = a_mask[:-1, :]
    a_bevel = a_mask & (~a_up)
    idx[a_bevel] = 14

    # 500 body (15) with bevel
    n_mask = n_arr > 110
    idx[n_mask] = 15
    return idx


def render_floppy_frame(tilt_rad, roll_rad, hub_ang):
    """Raycast/project a 3D 3.5-inch Amiga floppy diskette in perspective."""
    # Diskette local coordinates: x in [-1, 1], y in [-1.05, 1.05], thickness z in [-0.07, 0.07]
    # Standard 3.5" disk is ~90mm x 94mm; shutter at top (y < 0 in screen-top, let's put shutter facing camera/bottom or top)
    idx = np.zeros((DISK_H, DISK_W), dtype=np.uint8)

    # Supersample 2x for crisp edges (keep right 16px = 32px@2x blank for clean Blitter shift)
    SW, SH = DISK_W * 2, DISK_H * 2
    out = np.zeros((SH, SW), dtype=np.uint8)

    # Camera & plane parameters
    pitch = 1.18 + 0.07 * math.sin(tilt_rad)
    yaw = roll_rad
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)

    def rot(x, y, z):
        x1 = x * cy - y * sy
        y1 = x * sy + y * cy
        z1 = z
        y2 = y1 * cp - z1 * sp
        z2 = y1 * sp + z1 * cp
        return x1, y2, z2

    ux, uy, uz = rot(1, 0, 0)
    vx, vy, vz = rot(0, 1, 0)
    nx, ny, nz = rot(0, 0, 1)

    # Camera at (0, 0, -cam_z), looking toward +Z; disk center inside left 192px
    cam_z = 4.6
    focal = 192 * 2 * 1.65
    cx0, cy0 = 192 * 2 * 0.5, SH * 0.48

    # Grid of screen pixels
    px = (np.arange(SW, dtype=np.float32) - cx0) / focal
    py = (np.arange(SH, dtype=np.float32) - cy0) / focal
    PX, PY = np.meshgrid(px, py)

    # Ray: R = (PX, PY, 1) * t + (0, 0, -cam_z)
    # Intersect top face: (R - 0.05*N) . N = 0  => t = (0.05 + cam_z * nz) / (PX*nx + PY*ny + nz)
    def hit_plane(z_off):
        denom = PX * nx + PY * ny + nz
        denom = np.where(np.abs(denom) < 1e-4, 1e-4, denom)
        t = (z_off + cam_z * nz) / denom
        rx = PX * t
        ry = PY * t
        rz = t - cam_z
        # Project onto u, v
        u = rx * ux + ry * uy + rz * uz
        v = rx * vx + ry * vy + rz * vz
        return u, v, t

    def in_disk_shape(u, v):
        # Square [-1, 1] x [-1.04, 1.04] with chamfered bottom-right or top-right corner
        inside = (np.abs(u) <= 1.0) & (np.abs(v) <= 1.04)
        # Chamfered corner at (+u, -v) (back-right in perspective)
        inside &= ((u - v) <= 1.82)
        return inside

    # Draw 3D front rim thickness by stacking slices from bottom (-0.06) to top (+0.05)
    for z_slice in np.linspace(-0.075, 0.02, 5):
        u, v, _ = hit_plane(z_slice)
        m = in_disk_shape(u, v)
        # Front/side edge shading
        rim_col = np.where(u > 0.3, 4, 5)
        out = np.where(m, rim_col, out)

    # Top face (+0.04)
    u, v, _ = hit_plane(0.04)
    m = in_disk_shape(u, v)

    # Surface color assignment on top face:
    top = np.zeros_like(out)
    # Base Amiga floppy shell: index 5 (slate-blue), subtle sheen toward front/left
    top[:] = np.where(u + 0.4 * v < 0.0, 6, 5)
    # Recessed border/ridge
    ridge = (np.abs(u) > 0.94) | (np.abs(v) > 0.98)
    top = np.where(ridge, 4, top)

    # Label area (front/lower half: v in [0.10, 0.92], u in [-0.74, 0.74])
    lbl = (u >= -0.74) & (u <= 0.74) & (v >= 0.08) & (v <= 0.92)
    top = np.where(lbl, 7, top)
    # Commodore-style stripe on label (coral/red line + dark rule)
    stripe = (u >= -0.70) & (u <= 0.70) & (v >= 0.14) & (v <= 0.24)
    top = np.where(stripe, 13, top)
    rule1 = (u >= -0.66) & (u <= 0.66) & (np.abs(v - 0.44) < 0.022)
    rule2 = (u >= -0.66) & (u <= 0.40) & (np.abs(v - 0.62) < 0.022)
    top = np.where(rule1 | rule2, 5, top)

    # Metallic sliding shutter (top/back half: v in [-1.03, -0.14], u in [-0.46, 0.56])
    shutter = (u >= -0.46) & (u <= 0.56) & (v >= -1.03) & (v <= -0.14)
    # Diagonal rainbow reflection bands across the metal shutter (indices 8..11 -> Copper animated!)
    diag = u * 1.8 + v * 1.1 + 0.35 * math.sin(tilt_rad)
    sh_idx = 8 + (np.floor((diag + 2.5) * 2.2).astype(np.int32) % 4)
    # Add subtle cutout window on shutter
    cutout = (u >= 0.18) & (u <= 0.42) & (v >= -0.88) & (v <= -0.26)
    sh_idx = np.where(cutout, 4, sh_idx)
    # Bright metallic rim on shutter
    sh_rim = shutter & ((np.abs(u + 0.46) < 0.03) | (np.abs(v + 0.14) < 0.03))
    sh_idx = np.where(sh_rim, 11, sh_idx)
    top = np.where(shutter, sh_idx, top)

    # Central metallic hub / subtle spinning notch (visible if centered or subtle ring on top)
    # Write-protect notch at bottom-left (-0.86, +0.86)
    wp = (u >= -0.92) & (u <= -0.80) & (v >= 0.78) & (v <= 0.92)
    top = np.where(wp, 0, top)
    # Subtle spinning highlight dot/arc on shutter & disk front edge
    ang_pos = math.cos(hub_ang) * 0.14
    hub_glint = shutter & (np.abs(u - ang_pos) < 0.05) & (v > -0.75) & (v < -0.35)
    top = np.where(hub_glint, 11, top)

    out = np.where(m, top, out)

    # Nearest-neighbor 2x downsample (preserving sharp pixel indices, majority/center sample)
    idx = out[::2, ::2].copy()
    idx[:, 192:] = 0
    return idx


def to_planar_bytes(idx):
    """Convert H x W indexed image (0..15) into 4 sequential bitplanes (plane0..plane3)."""
    h, w = idx.shape
    assert w % 16 == 0
    bpr = w // 8
    planes = []
    for p in range(4):
        bits = (idx >> p) & 1
        packed = np.packbits(bits, axis=1).tobytes()
        assert len(packed) == h * bpr
        planes.append(packed)
    return b"".join(planes)


def gen_audio():
    """Generate Paula 8-bit signed PCM waveforms (~8-12 KB total):
       - chime: 2048-sample crystal bell waveform (1 cycle/envelope)
       - pad:   256-sample loopable warm synth pad cycle (detuned harmonics)
    """
    # Bell/chime: one-shot decaying waveform at ~256 samples/cycle? Wait: Paula loops any waveform!
    # For a clean loopable bell/pad, let's make:
    # 1) 64-sample crystal bell loop (software fades AUDxVOL at 50 Hz!)
    n_bell = 64
    t = np.arange(n_bell) / n_bell
    bell = 0.72 * np.sin(2 * math.pi * t) + 0.28 * np.sin(4 * math.pi * t) + 0.14 * np.sin(6 * math.pi * t)
    bell = np.clip(np.round(bell / np.max(np.abs(bell)) * 120), -127, 127).astype(np.int8)

    # 2) 128-sample warm 90s synth pad loop (rich fifth/saw-like warm harmonics, zero click at boundary)
    n_pad = 128
    t2 = np.arange(n_pad) / n_pad
    pad = (0.55 * np.sin(2 * math.pi * t2) +
           0.28 * np.sin(4 * math.pi * t2 + 0.4) +
           0.16 * np.sin(6 * math.pi * t2 + 0.9) +
           0.09 * np.sin(8 * math.pi * t2))
    pad = np.clip(np.round(pad / np.max(np.abs(pad)) * 120), -127, 127).astype(np.int8)
    return bell, pad


def main():
    logo_idx = render_logo()
    frames = []
    for f in range(DISK_FRAMES):
        ph = (f / float(DISK_FRAMES - 1) - 0.5) * 2.0
        tilt = ph * 1.1
        roll = 0.14 * ph
        hub = ph * 1.6
        frames.append(render_floppy_frame(tilt, roll, hub))

    # Build preview PNG
    prev = Image.new("RGB", (W, H), (0, 0, 0))
    pix = prev.load()
    # Stars
    rng = np.random.default_rng(42)
    for _ in range(90):
        sx, sy = int(rng.integers(0, W)), int(rng.integers(0, H))
        c = int(rng.choice([1, 1, 2, 2, 3]))
        pix[sx, sy] = BASE_PAL[c]
    # Paste logo at (16, 40)
    lx0, ly0 = 16, 36
    for y in range(LOGO_H):
        for x in range(LOGO_W):
            v = int(logo_idx[y, x])
            if v:
                pix[lx0 + x, ly0 + y] = BASE_PAL[v]
    # Paste frame 0 floppy at (64, 126) with sample Copper rainbow on colors 8..11
    fx0, fy0 = 64, 126
    fr0 = frames[0]
    for y in range(DISK_H):
        # Sample rainbow per scanline for preview
        hue = (y / DISK_H) * 2 * math.pi
        rb = [
            (int(128 + 115 * math.sin(hue + k * 0.9)),
             int(128 + 115 * math.sin(hue + k * 0.9 + 2.1)),
             int(128 + 115 * math.sin(hue + k * 0.9 + 4.2)))
            for k in range(4)
        ]
        for x in range(DISK_W):
            v = int(fr0[y, x])
            if v:
                if 8 <= v <= 11:
                    pix[fx0 + x, fy0 + y] = rb[v - 8]
                else:
                    pix[fx0 + x, fy0 + y] = BASE_PAL[v]

    scratch = os.path.join(r"C:\Users\JC\.gemini\antigravity\brain\91a840a4-5cbf-4176-a3ab-866d7e59703f\scratch")
    os.makedirs(scratch, exist_ok=True)
    prev_path = os.path.join(scratch, "intro_preview.png")
    prev.save(prev_path)
    print("Preview saved:", prev_path)

    # Emit C data file intro/intro_data.c
    bell, pad = gen_audio()
    logo_bytes = to_planar_bytes(logo_idx)
    disk_bytes = b"".join(to_planar_bytes(fr) for fr in frames)

    def fmt_u8(name, data):
        lines = [f"const unsigned char {name}[{len(data)}] = {{"]
        for i in range(0, len(data), 16):
            chunk = ",".join(f"0x{b:02X}" for b in data[i:i+16])
            lines.append("    " + chunk + ",")
        lines.append("};")
        return "\n".join(lines)

    def fmt_s8(name, data):
        vals = [int(x) for x in data]
        lines = [f"const signed char {name}[{len(vals)}] = {{"]
        for i in range(0, len(vals), 16):
            chunk = ",".join(str(v) for v in vals[i:i+16])
            lines.append("    " + chunk + ",")
        lines.append("};")
        return "\n".join(lines)

    pal_words = [to_rgb444(*c) for c in BASE_PAL]
    # Rainbow copper LUT (64 RGB444 steps, smooth HSV)
    rainbow = []
    for i in range(64):
        a = 2 * math.pi * i / 64.0
        r = int(135 + 118 * math.sin(a))
        g = int(135 + 118 * math.sin(a + 2.094))
        b = int(135 + 118 * math.sin(a + 4.188))
        rainbow.append(to_rgb444(max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b))))

    # Subtle dark nebula copper LUT (deep teal/blue/amber waves like CD32 bottom aurora)
    nebula = []
    for i in range(64):
        a = 2 * math.pi * i / 64.0
        r = int(42 + 38 * math.sin(a + 1.2))
        g = int(26 + 24 * math.sin(a + 2.8))
        b = int(68 + 52 * math.sin(a))
        nebula.append(to_rgb444(max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b))))

    c_out = [
        "/* Auto-generated by intro/mk_assets.py */",
        "#define INTRO_W 320",
        "#define INTRO_H 256",
        f"#define LOGO_W {LOGO_W}",
        f"#define LOGO_H {LOGO_H}",
        f"#define DISK_W {DISK_W}",
        f"#define DISK_H {DISK_H}",
        f"#define DISK_FRAMES {DISK_FRAMES}",
        "const unsigned short intro_pal[16] = { " + ",".join(f"0x{w:03X}" for w in pal_words) + " };",
        "const unsigned short copper_rainbow[64] = { " + ",".join(f"0x{w:03X}" for w in rainbow) + " };",
        "const unsigned short copper_nebula[64] = { " + ",".join(f"0x{w:03X}" for w in nebula) + " };",
        fmt_u8("logo_planar", logo_bytes),
        fmt_u8("disk_planar", disk_bytes),
        fmt_s8("wav_bell", bell.tobytes()),
        fmt_s8("wav_pad", pad.tobytes()),
    ]
    out_c = os.path.join(INTRO, "intro_data.c")
    with open(out_c, "w", newline="\n") as f:
        f.write("\n\n".join(c_out) + "\n")
    print(f"Wrote {out_c}: logo={len(logo_bytes)}B disk={len(disk_bytes)}B bell={len(bell)}B pad={len(pad)}B")


if __name__ == "__main__":
    main()
