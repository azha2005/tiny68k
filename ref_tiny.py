"""
ref_tiny.py -- floating-point reference for TINY 2.1, and the TINY.BIN reader.

This is TINY.C's step() transcribed to numpy (float64), including the parts
that are not textbook transformer: the int8 key/value cache with one absmax
scale per vector, and the tied embedding used as the output layer. It exists
so the integer port can be compared against something readable; it is in turn
checked against the original C (ref/harness.c) by check_ref.py.

    python ref_tiny.py orig/TINY.BIN "what is the capital of peru"
"""
import struct
import sys
import numpy as np

VOCAB = 97          # 0 pad, 1 newline (end), 2.. the letters ' '..'~'
CTX = 128
MAXQ = 72


class Table:
    __slots__ = ("scale", "w", "rows", "cols")

    def real(self):
        return self.w.astype(np.float64) * self.scale[:, None]


class Tiny:
    pass


def read_tiny(path):
    """TINY.BIN (version 2, little-endian) -> Tiny with int8 tables + scales."""
    raw = open(path, "rb").read()
    if raw[:4] != b"TNY2":
        raise ValueError("not a TNY2 file")
    m = Tiny()
    m.D, m.L, m.H, m.HID = struct.unpack_from("<4H", raw, 4)
    m.HD = m.D // m.H
    off = 12

    def table(rows, cols):
        nonlocal off
        t = Table()
        t.rows, t.cols = rows, cols
        t.scale = np.zeros(rows, dtype=np.float32)
        t.w = np.zeros((rows, cols), dtype=np.int8)
        for r in range(rows):
            t.scale[r] = struct.unpack_from("<f", raw, off)[0]
            off += 4
            t.w[r] = np.frombuffer(raw, dtype=np.int8, count=cols, offset=off)
            off += cols
        return t

    def gains():
        nonlocal off
        g = np.frombuffer(raw, dtype="<f4", count=m.D, offset=off).copy()
        off += 4 * m.D
        return g

    m.emb = table(VOCAB, m.D)
    m.pos = table(CTX, m.D)
    m.g1, m.g2, m.layers = [], [], []
    for _ in range(m.L):
        m.g1.append(gains())
        ly = {k: table(m.D, m.D) for k in ("wq", "wk", "wv", "wo")}
        m.g2.append(gains())
        ly["f1"] = table(m.HID, m.D)
        ly["f2"] = table(m.D, m.HID)
        m.layers.append(ly)
    m.gf = gains()
    if off != len(raw):
        raise ValueError(f"TINY.BIN has the wrong size ({off} vs {len(raw)})")
    m.real = {}
    return m


def store(v):
    """TINY.C store(): int8 with one absmax scale, rounding half away from 0."""
    big = max(float(np.abs(v).max()), 1e-20)
    x = v * (127.0 / big)
    q = np.where(x < 0, np.ceil(x - 0.5), np.floor(x + 0.5)).astype(np.int8)
    return q, big / 127.0


class StateF:
    def __init__(self, m):
        self.kc = np.zeros((m.L, CTX, m.D), dtype=np.int8)
        self.vc = np.zeros((m.L, CTX, m.D), dtype=np.int8)
        self.ks = np.zeros((m.L, CTX))
        self.vs = np.zeros((m.L, CTX))
        self.layer_act = [0.0] * m.L
        self.stats = {}


def _stat(st, k, v):
    v = float(v)
    if v > st.stats.get(k, 0.0):
        st.stats[k] = v


def _mat(m, key, t):
    r = m.real.get(key)
    if r is None:
        r = m.real[key] = t.real()
    return r


def norm(x, g):
    s = 1.0 / np.sqrt(np.dot(x, x) / len(x) + 1e-5)
    return x * s * g


def step(m, st, tok, p):
    D, HD = m.D, m.HD
    x = (m.emb.w[tok].astype(np.float64) * m.emb.scale[tok]
         + m.pos.w[p].astype(np.float64) * m.pos.scale[p])
    for l, ly in enumerate(m.layers):
        h = norm(x, m.g1[l])
        k = _mat(m, ("wk", l), ly["wk"]) @ h
        v = _mat(m, ("wv", l), ly["wv"]) @ h
        q = _mat(m, ("wq", l), ly["wq"]) @ h
        _stat(st, "kbig", np.abs(k).max())
        _stat(st, "vbig", np.abs(v).max())
        _stat(st, "q", np.abs(q).max())
        st.kc[l, p], st.ks[l, p] = store(k)
        st.vc[l, p], st.vs[l, p] = store(v)
        a = np.zeros(D)
        for hh in range(m.H):
            sl = slice(hh * HD, (hh + 1) * HD)
            kp = st.kc[l, :p + 1, sl].astype(np.float64)
            s = (kp @ q[sl]) * st.ks[l, :p + 1] / np.sqrt(HD)
            _stat(st, "score", np.abs(s).max())
            e = np.exp(s - s.max())
            e[s - s.max() < -60] = 0.0
            wgt = e * st.vs[l, :p + 1] / e.sum()
            a[sl] = wgt @ st.vc[l, :p + 1, sl].astype(np.float64)
        x = x + _mat(m, ("wo", l), ly["wo"]) @ a
        _stat(st, "x", np.abs(x).max())
        h = norm(x, m.g2[l])
        u = np.maximum(_mat(m, ("f1", l), ly["f1"]) @ h, 0.0)
        _stat(st, "hid", u.max())
        st.layer_act[l] = u.sum() / m.HID
        x = x + _mat(m, ("f2", l), ly["f2"]) @ u
        _stat(st, "x", np.abs(x).max())
    h = norm(x, m.gf)
    return _mat(m, "emb", m.emb) @ h


def clean(line):
    q = "".join(c for c in line if 32 <= ord(c) <= 126)[:MAXQ].rstrip(" ")
    if any(c.isupper() for c in q) and not any("a" <= c <= "z" for c in q):
        q = q.lower()
    return q


def answer_greedy(m, question, step_fn=step, state=None):
    """TINY.C think() with creativity 0."""
    st = state if state is not None else StateF(m)
    q = clean(question)
    p = 0
    for c in q:
        step_fn(m, st, ord(c) - 30, p)
        p += 1
    sc = step_fn(m, st, 1, p)
    p += 1
    out = []
    while p < CTX:
        tok = int(np.argmax(sc))          # first maximum, like TINY.C
        if tok <= 1:
            break
        out.append(chr(tok + 30))
        sc = step_fn(m, st, tok, p)
        p += 1
    return "".join(out)


if __name__ == "__main__":
    m = read_tiny(sys.argv[1] if len(sys.argv) > 1 else "orig/TINY.BIN")
    print(f"D={m.D} L={m.L} H={m.H} HID={m.HID}")
    for qq in sys.argv[2:] or ["what is the capital of peru"]:
        print(qq, "->", answer_greedy(m, qq))
