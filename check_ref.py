"""
check_ref.py -- ref_tiny.py against the original TINY.C (ref/harness.exe).

Two checks:
  1. logits for a probe sequence agree to float precision
  2. greedy answers to questions.txt are identical to the original's

Also prints the largest magnitudes seen inside the network, which is what
the fixed-point ranges in oracle_tiny.py are designed around.

    python check_ref.py
"""
import os
import subprocess
import sys
import numpy as np

import ref_tiny as R

ROOT = os.path.dirname(os.path.abspath(__file__))
MODEL = os.path.join(ROOT, "orig", "TINY.BIN")
HARNESS = os.path.join(ROOT, "ref", "harness" + (".exe" if os.name == "nt" else ""))
PROBE = "what is the capital of peru"


def probe_tokens():
    return [ord(c) - 30 for c in PROBE] + [1]


def main():
    m = R.read_tiny(MODEL)
    toks = probe_tokens()
    r = subprocess.run([HARNESS, MODEL, "logits"] + [str(t) for t in toks],
                       capture_output=True, text=True, check=True)
    c = np.array([float(v) for v in r.stdout.split()]).reshape(len(toks), R.VOCAB)
    st = R.StateF(m)
    worst = 0.0
    for p, t in enumerate(toks):
        a = R.step(m, st, t, p)
        rel = np.abs(a - c[p]).max() / (np.abs(c[p]).max() + 1e-9)
        worst = max(worst, rel)
    print(f"logits vs TINY.C: worst relative error {worst:.2e} over {len(toks)} positions")

    qs = [l.rstrip("\n") for l in open(os.path.join(ROOT, "questions.txt"))]
    qs = [q for q in qs if R.clean(q)]
    want = [l.rstrip("\n") for l in open(os.path.join(ROOT, "ref", "answers_orig.txt"))]
    st = R.StateF(m)
    same = 0
    for q, w in zip(qs, want):
        got = R.answer_greedy(m, q, state=st)
        if got == w:
            same += 1
        else:
            print(f"  DIFF {q!r}: ref={got!r} orig={w!r}")
    print(f"answers identical to TINY.C: {same}/{len(want)}")
    print("ranges:", {k: round(v, 3) for k, v in sorted(st.stats.items())})
    return 0 if worst < 1e-3 and same == len(want) else 1


if __name__ == "__main__":
    sys.exit(main())
