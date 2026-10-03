"""An HBM model for FHE_Accelerator_Sim, simulated at command level.

FHE_Accelerator_Sim moves every HBM transfer as a sequence of chunks (4 MiB by
default) through one first-come-first-served HBM resource. By default a chunk
takes ``bytes / hbm_gbps``: the peak bandwidth, or a flat "bandwidth x
efficiency" if you derate ``hbm_gbps``. Passing an ``HBMChunkModel`` as
``Accelerator(memory=...)`` replaces that with the time this simulator gives
for the same chunk:

* the chunk is a contiguous address range, interleaved over all pseudo-channels
  by the address mapping; channels are identical and independent, so one channel
  is simulated with its share of the bursts;
* it follows the previous chunk without a gap, so a direction change (a
  ciphertext write after key reads) pays the bus turnaround and write recovery,
  and the rows the previous chunk left open must be closed;
* refresh is included at its average rate: the chunk is simulated without
  refresh and stretched by the fraction of time refresh costs a long sequential
  stream on the same channel (measured once, with refresh on and off). Simulating
  each chunk with refresh would make short chunks see either none or a whole one.

Results are memoised on (bursts per channel, direction, previous direction), so
a whole bootstrap costs a few dozen command-level simulations.

The model's peak is scaled to the accelerator's ``hbm_gbps`` (the number of
pseudo-channels is ``hbm_gbps / channel peak``); its efficiency comes from the
DRAM timing, scheduler, page policy and mapping.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .controller import Channel, Request
from .mapping import AddressMapping
from .timing import Timing, hbm2e_pc

TAIL = 64          # bursts of the previous chunk simulated before this one


@dataclass(eq=False)
class HBMChunkModel:
    timing: Timing = field(default_factory=lambda: hbm2e_pc(channels=1))
    scheduler: str = "frfcfs"
    page: str = "open"
    scheme: str = "RoRaBaCoBgCh"
    queue: int = 32
    refresh: bool = True
    name: str = "Memory_System_Sim HBM"
    _cache: dict = field(default_factory=dict, repr=False)

    def __post_init__(self):
        if self.timing.channels != 1:
            self.timing = self.timing.with_(channels=1)

    def channel_cycles(self, bursts: int, write: bool, prev_write: bool | None) -> float:
        """Cycles one channel needs for ``bursts`` sequential bursts after a previous chunk."""
        key = (bursts, write, prev_write)
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        t = self.timing
        m = AddressMapping(t, self.scheme)
        a = t.access_bytes
        reqs = []
        if prev_write is not None:
            # the tail of the previous chunk, in another region of memory
            base = m.capacity // 2
            reqs += [Request(base + k * a, write=prev_write) for k in range(TAIL)]
        n_tail = len(reqs)
        reqs += [Request(k * a, write=write) for k in range(bursts)]
        for k, r in enumerate(reqs):
            r.id = k
            d = m.decode(r.addr)
            r.ch, r.ra, r.bg, r.ba, r.ro = d.ch, d.ra, d.bg, d.ba, d.ro
        ch = Channel(t, self.scheduler, self.page, self.queue, refresh=False)
        ch.run(reqs)
        start = max(r.done for r in reqs[:n_tail]) if n_tail else 0
        cycles = (max(r.done for r in reqs[n_tail:]) - start) / (1.0 - self.refresh_loss())
        self._cache[key] = cycles
        return cycles

    def refresh_loss(self) -> float:
        """Fraction of a long sequential stream's time lost to refresh (0 with refresh off)."""
        if not self.refresh:
            return 0.0
        hit = self._cache.get("refresh")
        if hit is None:
            t = self.timing
            n = 8 * t.REFI // t.burst_cycles                       # about eight refresh intervals
            m = AddressMapping(t, self.scheme)
            times = []
            for ref in (False, True):
                reqs = [Request(k * t.access_bytes) for k in range(n)]
                for k, r in enumerate(reqs):
                    d = m.decode(r.addr)
                    r.id, r.ch, r.ra, r.bg, r.ba, r.ro = k, d.ch, d.ra, d.bg, d.ba, d.ro
                Channel(t, self.scheduler, self.page, self.queue, refresh=ref).run(reqs)
                times.append(max(r.done for r in reqs))
            hit = self._cache["refresh"] = 1.0 - times[0] / times[1]
        return hit

    def chunk_time(self, nbytes: int, write: bool, prev_write: bool | None, peak_gbps: float) -> float:
        """Seconds to move ``nbytes`` through HBM whose peak is ``peak_gbps`` (FHE_Accelerator_Sim protocol)."""
        t = self.timing
        channels = peak_gbps / t.channel_peak_gbps()
        bursts = max(1, round(nbytes / t.access_bytes / channels))
        return self.channel_cycles(bursts, write, prev_write) * t.tck_ns * 1e-9

    def efficiency(self, nbytes: int, write: bool = False, prev_write: bool | None = None,
                   peak_gbps: float = 1000.0) -> float:
        return nbytes / (peak_gbps * 1e9) / self.chunk_time(nbytes, write, prev_write, peak_gbps)
