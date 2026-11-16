#!/usr/bin/env python3
"""
quant_explore.py -- how many weight bits can each tensor lose?

An experiment, not part of the spec: it never touches tiny.t68 or the
oracle. It re-quantises chosen tensors of TINY.BIN to b bits (signed, range
[-2^(b-1), 2^(b-1)-1], one scale per row as now), writes a temporary .t68,
and runs the cmp_fp32 metrics on it against the float original:
teacher-forced correlation / top-10 / argmax, and the 44 greedy answers.

With the bit-plane kernel the cost of wq wk wv wo f1 is linear in the
number of planes, so b bits instead of 8 cuts those dots to b/8.

Methods (per row, error weighted by calibration activations):
  rtn   round to nearest, absmax scale
  clip  round to nearest, scale searched over clip ratios (MSE, AWQ-style
        weighting by E[x^2] per input column)
  gptq  GPTQ (Frantar et al. 2022): column-by-column with error
        compensation using H = X^T X from the calibration run

    python scripts/quant_explore.py gptq wq,wk,wv,wo,f1=4
    python scripts/quant_explore.py clip wq,wk,wv=5 wo=6 f1@0=6 f1=4
  spec item: names=bits, optional @layer (later items override earlier)
"""
import os
import sys
import tempfile
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import fx
import oracle_tiny as O
import ref_tiny as R
import convert_tiny as C


class Rec:
    """Stands in for a weight matrix inside ref_tiny.step; records inputs."""
    def __init__(self, w, sink):
        self.w, self.sink = w, sink

    def __matmul__(self, x):
        self.sink.append(np.array(x, dtype=np.float64))
        return self.w @ x


def load_set():
    qs = [R.clean(l) for l in open(os.path.join(ROOT, "questions.txt"))]
    qs = [q for q in qs if q]
    want = [l.rstrip("\n") for l in open(os.path.join(ROOT, "ref", "answers_orig.txt"))]
    return qs, want


def toks_of(q, a):
    return [ord(c) - 30 for c in q] + [1] + [ord(c) - 30 for c in a]


def calibrate(fm, qs, want):
    """Inputs to every layer matrix over all question+answer positions."""
    sinks = {}
    for l, ly in enumerate(fm.layers):
        for k, t in ly.items():
            sinks[(k, l)] = []
            fm.real[(k, l)] = Rec(t.real(), sinks[(k, l)])
    for q, a in zip(qs, want):
        st = R.StateF(fm)
        for p, t in enumerate(toks_of(q, a)):
            R.step(fm, st, t, p)
    fm.real = {}
    return {k: np.array(v) for k, v in sinks.items()}


def quant_rows(wf, b, X, method):
    lo, hi = -(1 << (b - 1)), (1 << (b - 1)) - 1
    rows, cols = wf.shape
    amax = np.maximum(np.abs(wf).max(axis=1), 1e-12)
    if method == "rtn":
        c = amax / hi
        return np.clip(np.rint(wf / c[:, None]), lo, hi), c
    if method == "clip":
        imp = (X ** 2).mean(axis=0) + 1e-8
        best_e = np.full(rows, np.inf)
        best_q = np.zeros_like(wf)
        best_c = np.zeros(rows)
        for r in np.linspace(0.5, 1.0, 26):
            c = r * amax / hi
            q = np.clip(np.rint(wf / c[:, None]), lo, hi)
            e = (((wf - q * c[:, None]) ** 2) * imp).sum(axis=1)
            m = e < best_e
            best_e[m], best_q[m], best_c[m] = e[m], q[m], c[m]
        return best_q, best_c
    if method == "gptq":
        H = X.T @ X
        H += np.eye(cols) * (0.01 * np.mean(np.diag(H)) + 1e-8)
        # scale per row: the clip search above, then GPTQ rounding with it
        _, c = quant_rows(wf, b, X, "clip")
        Hinv = np.linalg.cholesky(np.linalg.inv(H)).T     # upper
        W = wf.copy()
        Q = np.zeros_like(W)
        for j in range(cols):
            q = np.clip(np.rint(W[:, j] / c), lo, hi)
            Q[:, j] = q
            err = (W[:, j] - q * c) / Hinv[j, j]
            W[:, j + 1:] -= np.outer(err, Hinv[j, j + 1:])
        return Q, c
    raise ValueError(method)


def parse_spec(items, L):
    bits = {}
    for it in items:
        names, b = it.split("=")
        b = int(b)
        names, _, lay = names.partition("@")
        layers = [int(lay)] if lay else range(L)
        for n in names.split(","):
            for l in layers:
                bits[(n, l)] = b
    return bits


def requant(fm, bits, calib, method):
    m = R.read_tiny(os.path.join(ROOT, "orig", "TINY.BIN"))
    for (k, l), b in bits.items():
        t = m.layers[l][k]
        q, c = quant_rows(t.real(), b, calib[(k, l)], method)
        t.w = q.astype(np.int8)
        t.scale = c.astype(np.float32)
    return m


def evaluate(fm, im, qs, want, verbose=True):
    corrs, ovs, agree, n = [], [], 0, 0
    for q, a in zip(qs, want):
        sf, si = R.StateF(fm), O.State(im)
        for p, t in enumerate(toks_of(q, a)):
            f = R.step(fm, sf, t, p)
            i = np.array(O.step(im, si, t, p), dtype=np.float64) / fx.ONE
            if p < len(q):
                continue
            corrs.append(np.corrcoef(f, i)[0, 1])
            ovs.append(len(set(np.argsort(-f)[:10]) & set(np.argsort(-i)[:10])))
            agree += int(np.argmax(f) == np.argmax(i))
            n += 1
    same, diffs = 0, []
    for q, a in zip(qs, want):
        got = O.think(im, q)
        if got == a:
            same += 1
        else:
            diffs.append((q, got, a))
    return min(corrs), float(np.mean(ovs)), agree, n, same, diffs


def main():
    method, items = sys.argv[1], sys.argv[2:]
    fm = R.read_tiny(os.path.join(ROOT, "orig", "TINY.BIN"))
    qs, want = load_set()
    bits = parse_spec(items, fm.L)
    cq, ca = qs, want
    cf = os.environ.get("QX_CALIB")       # calibrate on other questions (no leak)
    if cf:
        base = C.load_t68(os.path.join(ROOT, "tiny.t68"))
        cq = [q for q in (R.clean(l) for l in open(cf)) if q]
        ca = [O.think(base, q) for q in cq]
    calib = calibrate(fm, cq, ca) if method != "rtn" else {k: None for k in bits}
    qm = requant(fm, bits, calib, method)
    tmp = os.environ.get("QX_T68") or os.path.join(tempfile.gettempdir(), "quant_explore.t68")
    C.read_tiny = lambda src: qm
    C.convert(None, tmp)
    im = C.load_t68(tmp)
    cmin, ov, agree, n, same, diffs = evaluate(fm, im, qs, want)
    for q, got, a in diffs:
        print(f"  DIFF {q!r}: got={got!r} orig={a!r}")
    planes = sum(bits.get((k, l), 8) * fm.layers[l][k].rows * fm.layers[l][k].cols
                 for l in range(fm.L) for k in ("wq", "wk", "wv", "wo", "f1"))
    full = 8 * sum(fm.layers[l][k].rows * fm.layers[l][k].cols
                   for l in range(fm.L) for k in ("wq", "wk", "wv", "wo", "f1"))
    ho = os.environ.get("QX_HELDOUT")
    if ho:
        base = C.load_t68(os.path.join(ROOT, "tiny.t68"))
        hq = [R.clean(l) for l in open(ho)]
        hq = [q for q in hq if q]
        hs = 0
        for q in hq:
            a0, a1 = O.think(base, q), O.think(im, q)
            hs += a0 == a1
            if a0 != a1:
                print(f"  HELDOUT {q!r}: got={a1!r} int8={a0!r}")
        print(f"held-out greedy identical to the int8 oracle: {hs}/{len(hq)}")
    print(f"{method} {' '.join(items)}: corr min {cmin:.5f}  top10 {ov:.2f}  "
          f"argmax {agree}/{n}  greedy {same}/{len(want)}  bp work {planes / full:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
