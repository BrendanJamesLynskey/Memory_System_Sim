"""DRAM organisation and timing parameters, in clock cycles (tCK).

The parameter *names* follow the JEDEC standards (JESD79-4 for DDR4, JESD235 for
HBM); **the values here are illustrative**, chosen to sit in the range of a
DDR4-3200 speed bin and an HBM2E-class pseudo-channel. They are not quoted from
any standard or datasheet, which define them per speed bin and device density.
Use a vendor datasheet for real numbers.

Timing parameters (all in tCK):

=========  ==================================================================
CL         read command to first data beat (CAS latency)
CWL        write command to first data beat
RCD        ACT to a column command (RD/WR) in the same bank
RP         PRE to the next ACT in the same bank
RAS        ACT to PRE in the same bank (the row must stay open this long)
RC         ACT to ACT in the same bank (= RAS + RP)
RRD_S/L    ACT to ACT in different banks of one rank: other / same bank group
FAW        at most four ACTs in any window of FAW (power delivery limit)
CCD_S/L    column command to column command: other / same bank group
WR         end of write data to PRE (write recovery)
WTR_S/L    end of write data to a read command: other / same bank group
RTP        read to PRE
RTRW       idle bus cycles when the data bus turns from reads to writes
RFC        refresh command duration (the rank is unavailable)
REFI       average interval between refresh commands
=========  ==================================================================

A column command moves ``bl`` beats of ``bus_bytes`` each, on both clock edges,
so it occupies the data bus for bl/2 cycles.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Timing:
    name: str
    tck_ns: float
    bus_bytes: int        # data bus width per channel, bytes
    bl: int               # burst length, beats
    channels: int
    ranks: int
    bankgroups: int
    banks: int            # per bank group
    rows: int
    row_bytes: int        # page size per rank, bytes
    CL: int
    CWL: int
    RCD: int
    RP: int
    RAS: int
    RRD_S: int
    RRD_L: int
    FAW: int
    CCD_S: int
    CCD_L: int
    WR: int
    WTR_S: int
    WTR_L: int
    RTP: int
    RFC: int
    REFI: int
    RTRW: int = 2

    @property
    def RC(self) -> int:
        return self.RAS + self.RP

    @property
    def burst_cycles(self) -> int:
        return self.bl // 2

    @property
    def access_bytes(self) -> int:
        return self.bus_bytes * self.bl

    @property
    def columns(self) -> int:
        """Column accesses (bursts) per row."""
        return self.row_bytes // self.access_bytes

    @property
    def total_banks(self) -> int:
        return self.bankgroups * self.banks

    def channel_peak_gbps(self) -> float:
        """Peak data rate of one channel, GB/s (two beats per clock)."""
        return self.bus_bytes * 2 / self.tck_ns

    def peak_gbps(self) -> float:
        return self.channels * self.channel_peak_gbps()

    def ns(self, cycles: float) -> float:
        return cycles * self.tck_ns

    def with_(self, **kw) -> "Timing":
        return replace(self, **kw)


def _ck(ns: float, tck: float, min_ck: int = 0) -> int:
    """Nanoseconds to whole clock cycles (rounded up, as controllers do), with a floor in tCK."""
    return max(min_ck, math.ceil(ns / tck - 1e-9))


def ddr4_3200(channels: int = 1, ranks: int = 1) -> Timing:
    """A DDR4-3200-like x64 channel (8 Gb x8 devices, 8 KiB page). Illustrative values."""
    t = 0.625
    return Timing(
        name="DDR4-3200-like (illustrative)", tck_ns=t, bus_bytes=8, bl=8, channels=channels, ranks=ranks,
        bankgroups=4, banks=4, rows=65536, row_bytes=8192,
        CL=22, CWL=16, RCD=22, RP=22, RAS=_ck(32, t), RRD_S=_ck(2.5, t, 4), RRD_L=_ck(4.9, t, 4),
        FAW=_ck(21, t, 20), CCD_S=4, CCD_L=_ck(5, t, 5), WR=_ck(15, t), WTR_S=_ck(2.5, t, 2),
        WTR_L=_ck(7.5, t, 4), RTP=_ck(7.5, t, 4), RFC=_ck(350, t), REFI=_ck(7800, t))


def hbm2e_pc(channels: int = 16) -> Timing:
    """An HBM2E-class pseudo-channel (64-bit, BL4, 3.2 Gb/s per pin, 1 KiB page). Illustrative values.

    16 pseudo-channels make one stack of about 410 GB/s.
    """
    t = 0.625
    return Timing(
        name="HBM2E-class pseudo-channel (illustrative)", tck_ns=t, bus_bytes=8, bl=4, channels=channels, ranks=1,
        bankgroups=4, banks=4, rows=16384, row_bytes=1024,
        CL=_ck(14, t), CWL=_ck(4, t), RCD=_ck(14, t), RP=_ck(14, t), RAS=_ck(33, t), RRD_S=_ck(2, t, 2),
        RRD_L=_ck(4, t, 4), FAW=_ck(16, t), CCD_S=2, CCD_L=4, WR=_ck(16, t), WTR_S=_ck(2.5, t, 2),
        WTR_L=_ck(7.5, t, 4), RTP=_ck(5, t, 4), RFC=_ck(350, t), REFI=_ck(3900, t))


PRESETS = {"ddr4": ddr4_3200, "hbm": hbm2e_pc}
