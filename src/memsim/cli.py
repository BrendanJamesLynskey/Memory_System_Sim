"""memsim: run an access pattern through a DRAM model and print a summary.

    memsim --preset hbm --pattern random --n 20000 --scheduler fcfs
    memsim --preset ddr4 --pattern stride --stride 131072 --xor-bank --json
"""

from __future__ import annotations

import argparse
import json
import time

from .controller import simulate
from .mapping import AddressMapping
from .patterns import interleaved_streams, open_loop, random_uniform, streaming, strided
from .timing import PRESETS


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="memsim", description=__doc__.splitlines()[0])
    p.add_argument("--preset", choices=sorted(PRESETS), default="hbm")
    p.add_argument("--channels", type=int, default=1)
    p.add_argument("--pattern", choices=["stream", "random", "stride", "streams"], default="stream")
    p.add_argument("--n", type=int, default=10000, help="requests")
    p.add_argument("--stride", type=int, default=4096, help="bytes, for --pattern stride")
    p.add_argument("--writes", type=float, default=0.0, help="write fraction (random) or 1/k (stream)")
    p.add_argument("--scheduler", choices=["fcfs", "frfcfs"], default="frfcfs")
    p.add_argument("--page", choices=["open", "closed"], default="open")
    p.add_argument("--mapping", default="RoRaBaCoBgCh", choices=sorted(AddressMapping.SCHEMES))
    p.add_argument("--xor-bank", action="store_true")
    p.add_argument("--queue", type=int, default=32)
    p.add_argument("--no-refresh", action="store_true")
    p.add_argument("--load", type=float, help="offered bandwidth as a fraction of peak (open loop)")
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)

    t = PRESETS[a.preset](channels=a.channels)
    if a.pattern == "stream":
        reqs = streaming(t, a.n, write_every=round(1 / a.writes) if a.writes else 0)
    elif a.pattern == "random":
        reqs = random_uniform(t, a.n, 1 << 30, write_frac=a.writes)
    elif a.pattern == "stride":
        reqs = strided(t, a.n, a.stride)
    else:
        reqs = interleaved_streams(t, a.n, 4, 1 << 24)
    if a.load:
        open_loop(reqs, t, a.load * t.peak_gbps())
    t0 = time.perf_counter()
    mapping = AddressMapping(t, a.mapping, a.xor_bank)
    res = simulate(t, reqs, mapping, a.scheduler, a.page, a.queue, not a.no_refresh)
    s = res.summary()
    s["sim_seconds"] = time.perf_counter() - t0
    if a.json:
        print(json.dumps(s, indent=1))
        return
    print(f"{t.name}, {a.channels} channel(s), peak {t.peak_gbps():.1f} GB/s; {a.pattern}, {a.scheduler}, "
          f"{a.page} page, {a.mapping}{' + XOR' if a.xor_bank else ''}")
    print(f"  bandwidth   {s['bandwidth_GBps']:.2f} GB/s  (efficiency {s['efficiency']:.3f})")
    print(f"  latency     mean {s['latency_mean_ns']:.0f} ns, p50 {s['latency_p50_ns']:.0f}, "
          f"p99 {s['latency_p99_ns']:.0f}")
    print(f"  row hits    {s['row_hit_rate']:.3f}   {s['kinds']}")
    print(f"  commands    {s['commands']}")
    print(f"  simulated {s['requests']} requests in {s['sim_seconds']:.2f} s")


if __name__ == "__main__":
    main()
