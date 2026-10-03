"""Physical address -> (channel, rank, bank group, bank, row, column).

A mapping is a list of fields from the most significant bit to the least, as in
Ramulator's naming (``RoBaRaCoCh`` = row, bank, rank, column, channel). The
lowest log2(access bytes) bits address bytes within one burst and are dropped.
Where a field sits decides which access patterns spread across channels and
banks (parallelism) and which stay in one row (row hits):

* channel bits low: consecutive bursts go to different channels;
* bank-group bits next (DDR4, HBM2): consecutive bursts alternate bank groups, so
  back-to-back column commands need only tCCD_S, not the longer tCCD_L;
* column bits low, then bank bits: a sequential stream fills a row before moving on;
* row bits just above the column bits: a stride of one row lands in the same bank,
  a different row each time (the worst case for an open-page controller).

``xor_bank`` permutes the bank (and bank group) with low row bits, so strides that
would all hit one bank are spread over all of them (permutation-based page
interleaving, Zhang, Zhu and Zhang, MICRO 2000).
"""

from __future__ import annotations

from dataclasses import dataclass

from .timing import Timing

FIELDS = ("ch", "ra", "bg", "ba", "ro", "co")


def _log2(x: int) -> int:
    if x < 1 or x & (x - 1):
        raise ValueError(f"{x} is not a power of two")
    return x.bit_length() - 1


@dataclass(frozen=True)
class Decoded:
    ch: int
    ra: int
    bg: int
    ba: int
    ro: int
    co: int


class AddressMapping:
    SCHEMES = {
        "RoRaBaCoBgCh": ("ro", "ra", "ba", "co", "bg", "ch"),    # default: a stream rotates over bank groups
        "RoRaBgBaCoCh": ("ro", "ra", "bg", "ba", "co", "ch"),    # a stream stays in one bank group (tCCD_L)
        "RoCoRaBgBaCh": ("ro", "co", "ra", "bg", "ba", "ch"),    # consecutive bursts rotate over banks
        "ChRaBgBaRoCo": ("ch", "ra", "bg", "ba", "ro", "co"),    # one bank per huge region (worst parallelism)
    }

    def __init__(self, t: Timing, scheme: str = "RoRaBaCoBgCh", xor_bank: bool = False):
        order = self.SCHEMES.get(scheme, None) or tuple(scheme.split(","))
        if sorted(order) != sorted(FIELDS):
            raise ValueError(f"mapping must order exactly {FIELDS}")
        self.t, self.scheme, self.xor_bank = t, scheme, xor_bank
        self.width = {"ch": _log2(t.channels), "ra": _log2(t.ranks), "bg": _log2(t.bankgroups),
                      "ba": _log2(t.banks), "ro": _log2(t.rows), "co": _log2(t.columns)}
        self.offset = _log2(t.access_bytes)
        self.shift: dict[str, int] = {}
        s = self.offset
        for f in reversed(order):
            self.shift[f] = s
            s += self.width[f]
        self.capacity = 1 << s

    def field(self, addr: int, f: str) -> int:
        return (addr >> self.shift[f]) & ((1 << self.width[f]) - 1)

    def decode(self, addr: int) -> Decoded:
        addr %= self.capacity
        g = {f: self.field(addr, f) for f in FIELDS}
        if self.xor_bank:
            g["ba"] ^= g["ro"] & (self.t.banks - 1)
            g["bg"] ^= (g["ro"] >> self.width["ba"]) & (self.t.bankgroups - 1)
        return Decoded(g["ch"], g["ra"], g["bg"], g["ba"], g["ro"], g["co"])
