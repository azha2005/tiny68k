#!/usr/bin/env python3
"""
scripts/verify.py -- the ground truth for this repo.

Exit 0 = tiny68k.c matches oracle_tiny.py exactly. Exit 1 = it does not.

Three checks, all exact:
  1. scores: teacher-forced question + answer probes, every int32 score and
     every layer activity at every position (one probe runs to position ~96,
     so constants that only bite late are covered: see amiga260k's bug)
  2. greedy answers to questions.txt
  3. sampled answers at creativity 5 and 9 with fixed seeds (covers pick()
     and the RNG)

    python scripts/verify.py
"""
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

MODEL = os.path.join(ROOT, "tiny.t68")
PROBES = [
    ("what is the capital of peru", "Lima."),
    ("name the continents", "Africa, Antarctica, Asia, Australia, Europe, "
                            "North America and South America."),
    ("tell me a joke", "Why did the PC go to the doctor? It had a virus."),
]
SAMPLED = [(5, 7), (9, 3)]          # (creativity, seed)
SAMPLED_QS = 8                      # first N questions


def die(msg):
    print(msg, file=sys.stderr)
    sys.exit(1)


def env():
    e = dict(os.environ)
    msys = r"C:\msys64\ucrt64\bin"
    if os.name == "nt" and os.path.isdir(msys):
        e["PATH"] = msys + os.pathsep + e.get("PATH", "")
    return e


def build():
    outdir = os.path.join(tempfile.gettempdir(), "tiny68k_build")
    os.makedirs(outdir, exist_ok=True)
    exe = os.path.join(outdir, "tiny68k" + (".exe" if os.name == "nt" else ""))
    cmd = ["gcc", "-O2", "-Wall", "-Werror", "-DA5_HOST", "-o", exe,
           os.path.join(ROOT, "tiny68k.c")]
    r = subprocess.run(cmd, capture_output=True, text=True, env=env())
    if r.returncode != 0:
        die("BUILD FAILED\n" + r.stdout + r.stderr)
    return exe


def c_run(exe, args, stdin=None):
    r = subprocess.run([exe, MODEL] + args, input=stdin, capture_output=True,
                       text=True, env=env())
    if r.returncode != 0:
        die(f"C binary failed ({' '.join(args[:1])})\n" + r.stderr)
    return r.stdout


def main():
    os.chdir(ROOT)
    if not os.path.isfile(MODEL):
        die("tiny.t68 missing: python build.py convert")
    import oracle_tiny as O
    from convert_tiny import load_t68
    from ref_tiny import clean
    m = load_t68(MODEL)
    exe = build()
    fails = 0

    # 1. scores
    total = 0
    for q, a in PROBES:
        st = O.State(m)
        text = [ord(c) - 30 for c in q] + [1] + [ord(c) - 30 for c in a]
        py = []
        for p, t in enumerate(text):
            py += O.step(m, st, t, p) + list(st.layer_act)
        c = c_run(exe, ["-dump", q, a]).split()
        py = [str(v) for v in py]
        total += len(py)
        if len(c) != len(py):
            print(f"FAIL {q!r}: oracle {len(py)} values, C {len(c)}", file=sys.stderr)
            fails += 1
            continue
        bad = [i for i, (x, y) in enumerate(zip(c, py)) if x != y]
        if bad:
            per = m.vocab + m.L
            i = bad[0]
            what = f"score {i % per}" if i % per < m.vocab else f"layer_act {i % per - m.vocab}"
            print(f"FAIL {q!r}: {len(bad)} values differ; first at position "
                  f"{i // per}, {what}: C={c[i]} oracle={py[i]}", file=sys.stderr)
            fails += 1
    print(f"scores: {total} values over {len(PROBES)} probes"
          + ("" if fails else ", all identical"))

    # 1b. the same probes with the question fed through the prefill path:
    # every score from the separator on depends on the KV caches prefill wrote.
    pfails = 0
    for q, a in PROBES:
        st = O.State(m)
        text = [ord(c) - 30 for c in q] + [1] + [ord(c) - 30 for c in a]
        py = []
        for p, t in enumerate(text):
            sc = O.step(m, st, t, p)
            if p >= len(q):
                py += sc + list(st.layer_act)
        c = c_run(exe, ["-dumppre", q, a]).split()
        py = [str(v) for v in py]
        if c != py:
            bad = [i for i, (x, y) in enumerate(zip(c, py)) if x != y]
            per = m.vocab + m.L
            where = (f"first at answer position {bad[0] // per}" if bad
                     else f"C {len(c)} values, oracle {len(py)}")
            print(f"FAIL prefill {q!r}: {where}", file=sys.stderr)
            pfails += 1
    fails += pfails
    print(f"prefill: {len(PROBES)} probes" + ("" if pfails else ", scores after the question identical"))

    # 1c. Fast Mode prefill_planes (5 and 6) against oracle_tiny.py
    ffails = 0
    for pl in (5, 6):
        q, a = PROBES[0]
        st = O.State(m)
        text = [ord(c) - 30 for c in q] + [1] + [ord(c) - 30 for c in a]
        py = []
        for p, t in enumerate(text):
            sc = O.step(m, st, t, p, full=(p >= len(q)), prefill_planes=pl)
            if p >= len(q):
                py += sc + list(st.layer_act)
        c = c_run(exe, ["-f", str(pl), "-dumppre", q, a]).split()
        if c != [str(v) for v in py]:
            print(f"FAIL fast prefill planes={pl} {q!r}", file=sys.stderr)
            ffails += 1
    fails += ffails
    print(f"fast prefill (planes 5, 6): 2 probes" + ("" if ffails else ", identical"))

    # 2./3. answers
    qs = [clean(l) for l in open(os.path.join(ROOT, "questions.txt"))]
    qs = [q for q in qs if q]
    runs = [(0, 1, qs)] + [(c, s, qs[:SAMPLED_QS]) for c, s in SAMPLED]
    heldout = os.path.join(ROOT, "scripts", "heldout.txt")
    if os.path.isfile(heldout):
        hqs = [clean(l) for l in open(heldout) if clean(l)]
        runs.append((0, 1, hqs))
    for cr, seed, qlist in runs:
        got = c_run(exe, ["-ask", "-c", str(cr), "-s", str(seed)],
                    stdin="\n".join(qlist) + "\n").splitlines()
        want = [O.think(m, q, cr, seed) for q in qlist]
        bad = [i for i in range(len(want)) if i >= len(got) or got[i] != want[i]]
        if bad:
            i = bad[0]
            print(f"FAIL answers c={cr} s={seed}: {len(bad)}/{len(want)} differ; "
                  f"first {qlist[i]!r}: C={got[i] if i < len(got) else None!r} "
                  f"oracle={want[i]!r}", file=sys.stderr)
            fails += 1
        else:
            print(f"answers c={cr} s={seed}: {len(want)}/{len(want)} identical")

    # 4. CLI -f 5 must keep tiny_prefix_always=0 and match oracle prefill_planes=5
    pqs = [
        "what is the capital of peru",
        "is a whale a fish",
        "how many legs does a spider have",
        "who invented the telephone",
        "which animal says hoot",
    ]
    got5 = c_run(exe, ["-f", "5", "-ask"], stdin="\n".join(pqs) + "\n").splitlines()
    want5 = [O.think(m, q, 0, 1, prefill_planes=5) for q in pqs]
    if got5 != want5:
        print(f"FAIL fast -f 5 answers: got={got5!r} oracle={want5!r}", file=sys.stderr)
        fails += 1
    else:
        print(f"answers -f 5 (prefix_always=0): {len(want5)}/{len(want5)} identical")

    if fails:
        print("Do not proceed. The C has drifted from oracle_tiny.py.", file=sys.stderr)
        return 1
    print("OK: tiny68k.c matches oracle_tiny.py exactly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
