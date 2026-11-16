"""
oracle_tiny.py -- the integer forward pass of TINY 2.1, in Python.

THIS IS THE SPECIFICATION. tiny68k.c (and any 68000 assembly) must produce
these exact int32 scores. Everything is Q16.16 in s32, built only from the
primitives in fx.py. numpy appears only to do exact integer dot products of
int8 weights with int16 activations (int64 accumulators, checked to fit s32).

How it maps onto TINY.C's step():

  TINY.C (float)                      here (integer)
  ----------------------------------  -------------------------------------
  activations, float                  Q16.16; int16 + power-of-two shift
                                      when fed to a matmul (quantize16)
  matvec: sum(w*x) * scale[r]         int32 dot, then ms(acc, mant, shift-xsh)
  norm(): 1/sqrt(mean(x^2)+1e-5)      fx.rmsnorm_tiny (scaled squares, no eps)
  store(): int8, absmax/127           fx.store_kv (same rounding rule)
  q.k * ks / sqrt(HD)                 int32 dot(k8, q16), ms() by a per-
                                      position scale from the header constant
  exp(s - max), /sum                  exp table, one fxdiv per head
  sum_j (e_j*vs_j/sum) * v_j          weights w_j in Q24 -> int16, int32 dot
  ReLU                                ReLU
  scores = emb * h                    same tied table

    python oracle_tiny.py tiny.t68 "what is the capital of peru"
"""
import sys
import numpy as np

import fx
from convert_tiny import load_t68

S32 = 1 << 31


def chk(v):
    """A value the C holds in an s32. Overflow is a spec error, not a wrap."""
    if not (-S32 <= v < S32):
        raise OverflowError(f"s32 overflow: {v}")
    return v


def matmul(t, xq, xsh):
    acc = t.w @ xq
    if acc.size and (acc.max() >= S32 or acc.min() < -S32):
        raise OverflowError("dot product exceeds s32")
    return [fx.ms(int(acc[r]), t.mant[r], t.shift[r] - xsh) for r in range(t.rows)]


def matmul_bp(t, xq, xsh, planes=8):
    """Bit-plane matmul: top `planes` of u = w + 128, exact at planes=8.

    For planes < 8 (drop = 8 - planes), runs Horner over k = 7..drop,
    shifts left by drop, and subtracts the exact integer midpoint bias
    ((257 - (1 << drop)) * sum(xq)) >> 1.
    """
    if planes >= 8:
        return matmul(t, xq, xsh)
    drop = 8 - planes
    u_top = ((t.w + 128) >> drop) << drop
    acc_u = u_top @ xq
    sum_x = chk(int(xq.sum()))
    bias = chk(((257 - (1 << drop)) * sum_x) >> 1)
    acc = acc_u - bias
    if acc.size and (acc.max() >= S32 or acc.min() < -S32):
        raise OverflowError("dot product exceeds s32")
    return [fx.ms(int(acc[r]), t.mant[r], t.shift[r] - xsh) for r in range(t.rows)]


class State:
    def __init__(self, m):
        L, C, D = m.L, m.ctx, m.D
        self.kc = np.zeros((L, C, D), dtype=np.int64)
        self.vc = np.zeros((L, C, D), dtype=np.int64)
        self.kscale = [[(0, 0)] * C for _ in range(L)]   # (m, sh) per position
        self.vscale = [[(0, 0)] * C for _ in range(L)]
        self.layer_act = [0] * L                          # Q16.16, for the UI


def attention(m, st, l, q, p):
    HD = m.HD
    qq, qsh = fx.quantize16(q)
    qq = np.array(qq, dtype=np.int64)
    out = [0] * m.D
    for h in range(m.H):
        o = h * HD
        acc = st.kc[l, :p + 1, o:o + HD] @ qq[o:o + HD]
        sc = []
        for j in range(p + 1):
            mk, sk = st.kscale[l][j]
            sc.append(fx.ms(int(acc[j]), mk, sk - qsh))
        mx = max(sc)
        e = [fx.fxexp_neg(m.exp_tab, mx - s) for s in sc]
        tot = chk(sum(e))
        inv = fx.fxdiv(fx.ONE, tot)
        w = []
        for j in range(p + 1):
            pj = fx.fxmul(e[j], inv)
            mv, sv = st.vscale[l][j]
            w.append(fx.ms(pj, mv, sv))                   # Q24
        wq, wsh = fx.quantize16(w)
        wq = np.array(wq, dtype=np.int64)
        acc = wq @ st.vc[l, :p + 1, o:o + HD]
        s = 8 - wsh
        for i in range(HD):
            a = chk(int(acc[i]))
            out[o + i] = a >> s if s >= 0 else chk(a << (-s))
    return out


def step(m, st, tok, p, full=True, prefill_planes=8):
    """Feed letter `tok` at position `p`; return VOCAB Q16.16 scores.

    When full is False (prefill), stops after the last layer stores K/V and
    uses `prefill_planes` (1..8) in matmul_bp for wq/wk/wv/wo/f1.
    """
    D = m.D
    planes = 8 if full else prefill_planes
    e, ps = m.emb, m.pos
    x = [chk(fx.ms(int(e.w[tok, j]), e.mant[tok], e.shift[tok] - 16)
             + fx.ms(int(ps.w[p, j]), ps.mant[p], ps.shift[p] - 16))
         for j in range(D)]

    for l in range(m.L):
        ly = m.layers[l]
        h = fx.rmsnorm_tiny(x, m.g1[l])
        hq, hsh = fx.quantize16(h)
        hq = np.array(hq, dtype=np.int64)
        k = matmul_bp(ly["wk"], hq, hsh, planes)
        v = matmul_bp(ly["wv"], hq, hsh, planes)

        kq, kbig = fx.store_kv(k)
        vq, vbig = fx.store_kv(v)
        st.kc[l, p] = kq
        st.vc[l, p] = vq
        st.kscale[l][p] = fx.scale_of(kbig, m.kc_m, m.kc_s)
        st.vscale[l][p] = fx.scale_of(vbig, m.vc_m, m.vc_s)
        if not full and l == m.L - 1:
            return None

        q = matmul_bp(ly["wq"], hq, hsh, planes)
        a = attention(m, st, l, q, p)
        aq, ash = fx.quantize16(a)
        t = matmul_bp(ly["wo"], np.array(aq, dtype=np.int64), ash, planes)
        x = [chk(x[j] + t[j]) for j in range(D)]

        h = fx.rmsnorm_tiny(x, m.g2[l])
        hq, hsh = fx.quantize16(h)
        u = matmul_bp(ly["f1"], np.array(hq, dtype=np.int64), hsh, planes)
        u = [v if v > 0 else 0 for v in u]
        st.layer_act[l] = chk(sum(u)) // m.HID
        uq, ush = fx.quantize16(u)
        t = matmul(ly["f2"], np.array(uq, dtype=np.int64), ush)
        x = [chk(x[j] + t[j]) for j in range(D)]

    h = fx.rmsnorm_tiny(x, m.gf)
    hq, hsh = fx.quantize16(h)
    return matmul(m.emb, np.array(hq, dtype=np.int64), hsh)


# ------------------------------------------------------------- choosing

CUTOFF = 9830          # 0.15 in Q16.16: TINY.C never picks letters below it


def confidence(m, score, tok):
    """exp(s_tok - best) / sum_i exp(s_i - best), Q16.16. For the UI."""
    best = max(score)
    e = [fx.fxexp_neg(m.exp_tab, best - s) for s in score]
    return fx.fxdiv(e[tok], chk(sum(e)))


def pick(m, score, creativity, rng):
    """TINY.C pick(). creativity 0 = argmax (first maximum).
    Otherwise exp((s - best) * 5 / creativity), entries below 0.15 dropped,
    and a 16-bit draw from the xorshift32 RNG walks the cumulative sum."""
    best = 0
    for i in range(1, len(score)):
        if score[i] > score[best]:
            best = i
    if creativity <= 0:
        return best
    e = []
    for s in score:
        u = chk((score[best] - s) * 5) // creativity       # both >= 0
        v = fx.fxexp_neg(m.exp_tab, u)
        e.append(v if v >= CUTOFF else 0)
    r = fx.ms(chk(sum(e)), rng.next() >> 16, 16)
    for i, v in enumerate(e):
        r -= v
        if r < 0 and v > 0:
            return i
    return best


def think(m, question, creativity=0, seed=1, st=None, prefill_planes=8):
    """TINY.C think(): read the question, then letters until newline or CTX.
    `question` must already be clean (ref_tiny.clean)."""
    st = st if st is not None else State(m)
    rng = fx.XorShift32(seed)
    p = 0
    for c in question:
        step(m, st, ord(c) - 30, p, full=False, prefill_planes=prefill_planes)
        p += 1
    score = step(m, st, 1, p)
    p += 1
    out = []
    while p < m.ctx:
        tok = pick(m, score, creativity, rng)
        if tok <= 1:
            break
        out.append(chr(tok + 30))
        score = step(m, st, tok, p)
        p += 1
    return "".join(out)


if __name__ == "__main__":
    from ref_tiny import clean
    m = load_t68(sys.argv[1] if len(sys.argv) > 1 else "tiny.t68")
    for qq in sys.argv[2:] or ["what is the capital of peru"]:
        print(qq, "->", think(m, clean(qq)))
