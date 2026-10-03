"""Access patterns: request streams for the controller (addresses in bytes, arrival in cycles).

``closed_loop`` patterns make every request available at cycle 0, so the queue
stays full and the result is the memory system's sustainable bandwidth.
``open_loop`` spaces arrivals at a given offered bandwidth, which gives the
latency a request sees at that load (the load-latency curve).
"""

from __future__ import annotations

import random

from .controller import Request
from .timing import Timing


def streaming(t: Timing, n: int, base: int = 0, write_every: int = 0) -> list[Request]:
    """Sequential bursts; every ``write_every``-th one a write (0: all reads)."""
    a = t.access_bytes
    return [Request(base + k * a, write=bool(write_every) and k % write_every == write_every - 1) for k in range(n)]


def strided(t: Timing, n: int, stride: int, base: int = 0) -> list[Request]:
    return [Request(base + k * stride) for k in range(n)]


def random_uniform(t: Timing, n: int, span: int, seed: int = 0, write_frac: float = 0.0) -> list[Request]:
    rng = random.Random(seed)
    a = t.access_bytes
    return [Request(rng.randrange(span // a) * a, write=rng.random() < write_frac) for _ in range(n)]


def interleaved_streams(t: Timing, n: int, streams: int, spacing: int, seed: int = 0) -> list[Request]:
    """``streams`` sequential streams ``spacing`` bytes apart, requests interleaved at random
    (several DMA engines sharing one memory)."""
    rng = random.Random(seed)
    a = t.access_bytes
    pos = [0] * streams
    out = []
    for _ in range(n):
        s = rng.randrange(streams)
        out.append(Request(s * spacing + pos[s] * a))
        pos[s] += 1
    return out


def open_loop(reqs: list[Request], t: Timing, offered_gbps: float, seed: int = 0,
              poisson: bool = True) -> list[Request]:
    """Give requests arrival times for an offered bandwidth (GB/s), Poisson or evenly spaced."""
    rng = random.Random(seed)
    mean_gap = t.access_bytes / offered_gbps / t.tck_ns      # cycles between requests
    now = 0.0
    for r in reqs:
        r.arrival = int(now)
        now += rng.expovariate(1.0 / mean_gap) if poisson else mean_gap
    return reqs
