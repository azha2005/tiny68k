"""
cmp_fp32.py -- how much the integer oracle loses against the float original.

  1. Teacher-forced: every question in questions.txt plus TINY.C's own answer
     is fed through both ref_tiny (float) and oracle_tiny (integer). At every
     answer position: logit correlation, top-10 overlap, argmax agreement.
  2. Free-running: greedy answers from the oracle vs TINY.C's answers
     (ref/answers_orig.txt), character for character.

Pass criteria (same spirit as amiga260k): min correlation > 0.99, mean top-10
overlap >= 8/10, and every greedy answer identical to TINY.C's.

    python cmp_fp32.py
"""
import os
import sys
import numpy as np

import fx
import oracle_tiny as O
import ref_tiny as R
from convert_tiny import load_t68

ROOT = os.path.dirname(os.path.abspath(__file__))


def main():
    fm = R.read_tiny(os.path.join(ROOT, "orig", "TINY.BIN"))
    im = load_t68(os.path.join(ROOT, "tiny.t68"))
    qs = [R.clean(l) for l in open(os.path.join(ROOT, "questions.txt"))]
    qs = [q for q in qs if q]
    want = [l.rstrip("\n") for l in open(os.path.join(ROOT, "ref", "answers_orig.txt"))]

    corrs, ovs, agree, n = [], [], 0, 0
    for q, a in zip(qs, want):
        toks = [ord(c) - 30 for c in q] + [1] + [ord(c) - 30 for c in a]
        sf, si = R.StateF(fm), O.State(im)
        for p, t in enumerate(toks):
            f = R.step(fm, sf, t, p)
            i = np.array(O.step(im, si, t, p), dtype=np.float64) / fx.ONE
            if p < len(q):
                continue                    # only score answer positions
            corrs.append(np.corrcoef(f, i)[0, 1])
            ovs.append(len(set(np.argsort(-f)[:10]) & set(np.argsort(-i)[:10])))
            agree += int(np.argmax(f) == np.argmax(i))
            n += 1
    print(f"teacher-forced, {n} answer positions over {len(qs)} questions:")
    print(f"  logit correlation  min {min(corrs):.5f}  mean {np.mean(corrs):.5f}")
    print(f"  top-10 overlap     min {min(ovs)}/10  mean {np.mean(ovs):.2f}/10")
    print(f"  argmax agreement   {agree}/{n}")

    same = 0
    for q, a in zip(qs, want):
        got = O.think(im, q)
        if got == a:
            same += 1
        else:
            print(f"  DIFF {q!r}: oracle={got!r} orig={a!r}")
    print(f"greedy answers identical to TINY.C: {same}/{len(want)}")

    ok = min(corrs) > 0.99 and np.mean(ovs) >= 8 and same == len(want)
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
