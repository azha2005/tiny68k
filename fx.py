"""
Fixed-point primitives, Q16.16 throughout.

Every routine here is written the way the 68000 will execute it: no 64-bit
types, no floating point, all products built from 16x16->32 multiplies (MULU).
Inherited from amiga260k (unchanged above the TINY section at the end). The C
in tiny68k.c will mirror this line for line, and scripts/verify.py will assert
the two agree exactly.
"""

ONE = 1 << 16          # 1.0 in Q16.16
EXP_BITS = 6           # exp table spacing = 1/64
EXP_MAX = 16           # table covers u in [0, 16]
EXP_N = (EXP_MAX << EXP_BITS) + 1


def i32(v):
    """Wrap to signed 32-bit, the way a 68000 D-register does."""
    v &= 0xFFFFFFFF
    return v - 0x100000000 if v & 0x80000000 else v


def fxmul(a, b):
    """Q16.16 * Q16.16 -> Q16.16, built from four 16x16 multiplies."""
    neg = 0
    if a < 0:
        a = -a
        neg ^= 1
    if b < 0:
        b = -b
        neg ^= 1
    a &= 0xFFFFFFFF
    b &= 0xFFFFFFFF
    ah, al = a >> 16, a & 0xFFFF
    bh, bl = b >> 16, b & 0xFFFF
    r = ((ah * bh) << 16) + ah * bl + al * bh + ((al * bl) >> 16)
    r &= 0xFFFFFFFF
    return i32(-r if neg else r)


def mulshift(acc, m, sh):
    """(acc * m) >> sh with a 48-bit intermediate. acc int32, m uint16.

    sh may be negative, meaning shift left. This is the matmul rescale, run
    once per output element, so two MULUs here cost nothing next to the dot
    product itself.
    """
    neg = 0
    if acc < 0:
        acc = -acc
        neg = 1
    lo = (acc & 0xFFFF) * m
    hi = (acc >> 16) * m
    full = (hi << 16) + lo            # 48 bits, exact
    r = (full >> sh) if sh >= 0 else (full << -sh)
    r &= 0xFFFFFFFF
    return i32(-r if neg else r)


def isqrt32(v):
    """Integer square root, restoring shift-subtract. 16 iterations."""
    if v <= 0:
        return 0
    rem = 0
    root = 0
    for i in range(16):
        root <<= 1
        rem = (rem << 2) | ((v >> 30) & 3)
        v = (v << 2) & 0xFFFFFFFF
        if root < rem:
            root += 1
            rem -= root
            root += 1
    return root >> 1


def fxdiv(a, b):
    """(a << 16) / b in Q16.16, long division on a 64-bit-free numerator."""
    if b == 0:
        return 0x7FFFFFFF
    neg = 0
    if a < 0:
        a = -a
        neg ^= 1
    if b < 0:
        b = -b
        neg ^= 1
    nh, nl = a >> 16, (a << 16) & 0xFFFFFFFF
    q = 0
    rem = 0
    for i in range(64):
        bit = (nh >> 31) & 1 if i < 32 else (nl >> (63 - i)) & 1
        if i < 32:
            nh = (nh << 1) & 0xFFFFFFFF
        rem = (rem << 1) | bit
        q <<= 1
        if rem >= b:
            rem -= b
            q |= 1
    q &= 0xFFFFFFFF
    return i32(-q if neg else q)


def fxrsqrt(ss):
    """1/sqrt(ss) in Q16.16. isqrt estimate plus one Newton refinement."""
    if ss <= 0:
        return 0
    r = isqrt32(ss) << 8                       # sqrt(ss) in Q16.16, coarse
    if r == 0:
        return 0x7FFFFFFF
    r = (r + fxdiv(ss, r)) >> 1                # Newton on sqrt
    if r == 0:
        return 0x7FFFFFFF
    return fxdiv(ONE, r)


def build_exp_table():
    """exp(-u) in Q16.16 for u = 0 .. EXP_MAX, spaced 1/64. Shipped in the model."""
    import math
    return [int(round(math.exp(-i / (1 << EXP_BITS)) * ONE)) for i in range(EXP_N)]


def fxexp_neg(tab, u):
    """exp(-u) for u >= 0, Q16.16, linear interpolation between table entries."""
    if u <= 0:
        return ONE
    if u >= (EXP_MAX << 16):
        return 0
    idx = u >> (16 - EXP_BITS)
    frac = (u << EXP_BITS) & 0xFFFF
    a = tab[idx]
    b = tab[idx + 1]
    return i32(a + (((b - a) * frac) >> 16))


def build_sigmoid_table():
    """sigmoid(u) in Q16.16 for u = 0 .. EXP_MAX. Shipped in the model so the
    68000 never has to divide inside SiLU."""
    import math
    return [int(round((1.0 / (1.0 + math.exp(-i / (1 << EXP_BITS)))) * ONE))
            for i in range(EXP_N)]


def fxsigmoid(tab, x):
    """1/(1+exp(-x)) in Q16.16, table lookup only. tab is the sigmoid table."""
    neg = x < 0
    if neg:
        x = -x
    if x >= (EXP_MAX << 16):
        v = ONE
    else:
        idx = x >> (16 - EXP_BITS)
        frac = (x << EXP_BITS) & 0xFFFF
        a = tab[idx]
        b = tab[idx + 1]
        v = i32(a + (((b - a) * frac) >> 16))
    return i32(ONE - v) if neg else v


def quantize_act(x):
    """Q16.16 vector -> (int8 list, shift). Scale is a power of two, so the
    only work per element on the 68000 is an arithmetic shift."""
    amax = 0
    for v in x:
        a = -v if v < 0 else v
        if a > amax:
            amax = a
    if amax == 0:
        return [0] * len(x), 0
    sh = 0
    while (amax >> sh) > 127:
        sh += 1
    out = []
    for v in x:
        q = v >> sh if v >= 0 else -((-v) >> sh)
        if q > 127:
            q = 127
        elif q < -127:
            q = -127
        out.append(q)
    return out, sh


# ===================================================================== TINY
# Primitives added for TINY 2.1. Same rules: no floats, no 64-bit, products
# from 16x16 multiplies, and Python's >> on negatives matches the 68000's ASR
# (both floor), which is why every right shift of a signed value below is a
# plain >>, never // .

MULSHIFT_MIN, MULSHIFT_MAX = -15, 47     # the C mulshift must support all of
                                         # these (amiga260k's stops at 31)


def ms(acc, m, sh):
    """mulshift with its legal shift range enforced. Use this, not mulshift,
    so that a shift the C cannot do fails here, on the host, first."""
    if not (MULSHIFT_MIN <= sh <= MULSHIFT_MAX):
        raise OverflowError(f"mulshift shift {sh} out of range")
    if not (0 <= m <= 0xFFFF):
        raise OverflowError(f"mulshift mantissa {m} not uint16")
    return mulshift(acc, m, sh)


def quantize16(x):
    """s32 vector -> (int16 list, shift) with x ~= q << shift.

    Power-of-two scale chosen so that max|q| <= 32767; truncation toward zero,
    like quantize_act. The 68000 kernel multiplies int8 weights by 16-bit
    activations with one MULS either way, so 16 bits cost nothing over 8."""
    amax = 0
    for v in x:
        a = -v if v < 0 else v
        if a > amax:
            amax = a
    sh = 0
    while (amax >> sh) > 32767:
        sh += 1
    return [(v >> sh) if v >= 0 else -((-v) >> sh) for v in x], sh


def norm16(v):
    """v > 0 (up to 32 bits unsigned) -> (m, e), v ~= m * 2**e, m in [32768, 65535].
    Left shifts are exact; right shifts truncate."""
    if v <= 0:
        raise ValueError("norm16 needs v > 0")
    e = 0
    while v > 0xFFFF:
        v >>= 1
        e += 1
    while v < 0x8000:
        v <<= 1
        e -= 1
    return v, e


def scale_of(big, cm, cs):
    """A multiplier big * (cm / 2**cs) as (m, sh) for ms(x, m, sh).

    big is a positive s32, cm/cs a normalised constant from the model header.
    One 16x16 MULU (bm * cm) and two normalisations; no division."""
    bm, be = norm16(big)
    p = bm * cm                          # u32, in [2**30, 2**32)
    if p >= 0x80000000:
        pm, t = p >> 16, 16
    else:
        pm, t = p >> 15, 15
    return pm, cs - be - t


def rmsnorm_tiny(x, g):
    """y = x / sqrt(mean(x^2)) * g, Q16.16 in and out.

    The residual stream reaches |x| ~ 170 (11M in Q16.16), so x*x does not fit
    32 bits. Squares are taken of x >> k, with k chosen so that |x >> k| < 4096
    and the sum of n <= 128 squares stays below 2**31. TINY.C's eps of 1e-5 is
    dropped: mean(x^2) here is never within orders of magnitude of it."""
    n = len(x)
    amax = 0
    for v in x:
        a = -v if v < 0 else v
        if a > amax:
            amax = a
    if amax == 0:
        return [0] * n
    k = 0
    while (amax >> k) >= 4096:
        k += 1
    ss = 0
    for v in x:
        a = (-v if v < 0 else v) >> k
        ss += a * a                      # MULU, < 2**24
    mean = ss // n                       # unsigned divide: same in C
    if mean == 0:
        mean = 1
    e = 0
    while mean >= (1 << 18):
        mean >>= 2
        e += 1
    while mean < (1 << 16):
        mean <<= 2
        e -= 1
    inv = fxrsqrt(mean)                  # 2**24 / sqrt(mean)
    s = 8 - k - e
    inv = i32(inv << s) if s >= 0 else inv >> (-s)
    return [fxmul(g[j], fxmul(x[j], inv)) for j in range(n)]


def store_kv(v):
    """TINY.C store(): int8 with one absmax scale per vector.

    Returns (q, big) where big is max|v| in Q16.16, floored at 256 (~0.004) so
    that 127/big fits Q16.16. q = round_half_away(v * 127 / big), via one fxdiv
    per vector and one fxmul per element."""
    big = 0
    for a in v:
        a = -a if a < 0 else a
        if a > big:
            big = a
    if big < 256:
        big = 256
    f = fxdiv(127 << 16, big)            # 127/big_real, Q16.16
    q = []
    for a in v:
        neg = a < 0
        t = fxmul(-a if neg else a, f)   # |v| * 127/big, Q16.16
        r = (t + 0x8000) >> 16
        if r > 127:
            r = 127
        q.append(-r if neg else r)
    return q, big


class XorShift32:
    """The amiga260k sampler RNG: xorshift32 with 16 warm-up steps."""

    def __init__(self, seed):
        self.s = (seed & 0xFFFFFFFF) or 1
        for _ in range(16):
            self.next()

    def next(self):
        s = self.s
        s ^= (s << 13) & 0xFFFFFFFF
        s ^= s >> 17
        s ^= (s << 5) & 0xFFFFFFFF
        self.s = s
        return s
