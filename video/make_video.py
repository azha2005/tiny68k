"""Cut the tiny68k A500 recording into the 60 s video (1080p30).

    python video/make_video.py REC1080.mp4 FLOPPY1080.mp4 [OUT.mp4]

The inputs are the two phone recordings (4K) scaled to 1080p30 first:
    ffmpeg -i VID_..._185125.mp4 -vf scale=1920:1080,fps=30 -c:v libx264 -crf 16 rec1080.mp4
    ffmpeg -i VID_..._185821.mp4 -vf scale=1920:1080,fps=30 -c:v libx264 -crf 16 floppy1080.mp4
Segment times below are seconds in those recordings. Every sped-up segment
gets a visible "x N" badge.
"""
import os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if len(sys.argv) < 3:
    sys.exit(__doc__)
REC, FLOP = os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2])
OUT = sys.argv[3] if len(sys.argv) > 3 else os.path.join(HERE, "tiny68k_a500_60s.mp4")
TOTAL = 60.0
SEG = os.path.join(tempfile.gettempdir(), "tiny68k_video_seg")
os.makedirs(SEG, exist_ok=True)

FONT = "C\\:/Windows/Fonts/consolab.ttf"

# (source, start, end, speed, caption, gain_db)
SEGMENTS = [
    (FLOP, 0.0, 3.13, 1, "TINY 2.1 (by WollyMonjac)  ->  Amiga 500", -6),
    (REC, 1.5, 23.3, 6, "Kickstart 1.2  |  68000 @ 7 MHz  |  no FPU", 0),
    (REC, 23.3, 36.3, 1, "", 12),                                   # AMIGA 500 floppy intro
    (REC, 69.5, 115.0, 20, "loading 367K weights from floppy", 0),
    (REC, 116.0, 118.0, 1, "loading 367K weights from floppy", 0),  # keyboard cutaway
    (REC, 122.0, 175.5, 20, "loading 367K weights from floppy", 0),
    (REC, 175.5, 186.0, 3, "F5: three themes", 0),
    (REC, 186.0, 196.0, 2.5, "", 0),                                # typing question 1
    (REC, 196.0, 221.0, 12, "F7: screen off while it thinks", 0),
    (REC, 221.0, 232.0, 4, "", 0),
    (REC, 232.0, 234.0, 1, "", 0),                                  # hold "Lima."
    (REC, 234.0, 256.0, 8, "", 0),
    (REC, 256.0, 266.0, 4, "reading the question", 0),
    (REC, 266.0, 325.0, 7, "integer math only: 16x16 multiplies", 0),
    (REC, 325.0, 327.0, 1, "", 0),                                  # hold final answer
]

END_LINES = [
    ("tiny68k", 64, 250),
    ("a port of TINY 2.1 by WollyMonjac", 40, 370),
    ("github.com/WollyMonjac/Tiny-2.1", 34, 430),
    ("on a stock Amiga 500  |  68000 @ 7 MHz  |  no FPU", 34, 530),
    ("integer arithmetic, bit-exact with its reference", 34, 590),
    ('real time: "Lima." 35.8 s  |  "I am a tiny neural network." 67.4 s', 28, 690),
    ("time-lapse sections are marked", 26, 750),
]


def esc(t):
    return t.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        print(" ".join(cmd)); print(r.stderr[-3000:]); sys.exit(1)


def atempo_chain(sp):
    parts = []
    while sp > 2.0:
        parts.append("atempo=2.0"); sp /= 2.0
    parts.append(f"atempo={sp:.6f}")
    return ",".join(parts)


files = []
used = 0.0
for i, (src, a, b, sp, cap, gain) in enumerate(SEGMENTS):
    dur = (b - a) / sp
    used += dur
    vf = [f"setpts=(PTS-STARTPTS)/{sp}", "fps=30", "scale=1920:1080", "setsar=1"]
    if sp != 1:
        badge = f">> x{sp:g}"
        vf.append(f"drawtext=fontfile='{FONT}':text='{esc(badge)}':x=w-tw-48:y=40:"
                  f"fontsize=46:fontcolor=white:box=1:boxcolor=black@0.6:boxborderw=14")
    if cap:
        vf.append(f"drawtext=fontfile='{FONT}':text='{esc(cap)}':x=(w-tw)/2:y=h-th-60:"
                  f"fontsize=40:fontcolor=white:box=1:boxcolor=black@0.6:boxborderw=14")
    fade = min(0.04, dur / 4)
    if sp <= 4:
        af = [atempo_chain(sp)] if sp != 1 else []
        af += [f"volume={gain}dB", f"afade=t=in:d={fade}",
               f"afade=t=out:st={dur - fade:.4f}:d={fade}",
               "aresample=48000", "aformat=channel_layouts=stereo"]
        acmd = ["-af", ",".join(af)]
        ain = []
        amap = ["-map", "0:a:0"]
    else:
        ain = ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
        acmd = []
        amap = ["-map", "1:a:0"]
    out = os.path.join(SEG, f"s{i:02d}.mp4")
    run(["ffmpeg", "-v", "error", "-y", "-ss", f"{a}", "-t", f"{b - a}", "-i", src, *ain,
         "-vf", ",".join(vf), "-map", "0:v:0", *amap, *acmd, "-t", f"{dur:.4f}",
         "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", out])
    files.append(out)
    print(f"s{i:02d} {a:7.2f}-{b:7.2f} x{sp:<4g} -> {dur:6.3f}s  (total {used:6.3f})")

end_dur = TOTAL - used
print(f"end card {end_dur:.3f}s")
assert end_dur > 2.0, "not enough room for the end card"
vf = []
for t, size, y in END_LINES:
    vf.append(f"drawtext=fontfile='{FONT}':text='{esc(t)}':x=(w-tw)/2:y={y}:"
              f"fontsize={size}:fontcolor=white")
vf.append(f"fade=t=in:d=0.4,fade=t=out:st={end_dur - 0.6:.3f}:d=0.6")
out = os.path.join(SEG, "s99.mp4")
run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=0x0b1f4a:s=1920x1080:r=30:d={end_dur:.4f}",
     "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-vf", ",".join(vf), "-t", f"{end_dur:.4f}",
     "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
     "-c:a", "aac", "-b:a", "192k", out])
files.append(out)

lst = os.path.join(SEG, "list.txt")
with open(lst, "w") as f:
    for p in files:
        f.write(f"file '{p.replace(os.sep, '/')}'\n")
# re-encode on concat so timestamps are clean; limit audio peaks
run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst,
     "-af", "alimiter=limit=0.9",   # no fade-in: frame 0 is the thumbnail
     "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-pix_fmt", "yuv420p",
     "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", OUT])
print("wrote", OUT)
