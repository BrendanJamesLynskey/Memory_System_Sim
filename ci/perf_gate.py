"""Performance and behaviour gate against ci/perf_baseline.json.

* Behaviour: the efficiency of six reference workloads is deterministic, so any
  change at all is reported, and the gate fails if one moves by more than 1e-9
  (a model change must re-bless the baseline on purpose: --bless).
* Speed: simulated requests per second on a fixed trace must not fall more than
  --margin (default 25%) below the baseline.

    python ci/perf_gate.py [--margin 0.25] [--bless]
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from memsim import ddr4_3200, hbm2e_pc, interleaved_streams, random_uniform, simulate, streaming, strided

BASE = Path(__file__).parent / "perf_baseline.json"


def workloads():
    h, d = hbm2e_pc(channels=1), ddr4_3200()
    return {
        "hbm stream": (h, lambda: streaming(h, 6000)),
        "hbm random": (h, lambda: random_uniform(h, 4000, 1 << 30, seed=1)),
        "hbm 4 streams": (h, lambda: interleaved_streams(h, 6000, 4, 1 << 24, seed=1)),
        "ddr4 stream 1/3 writes": (d, lambda: streaming(d, 6000, write_every=3)),
        "ddr4 random": (d, lambda: random_uniform(d, 4000, 1 << 30, seed=2)),
        "ddr4 stride 8 KiB": (d, lambda: strided(d, 3000, 8192)),
    }


def measure() -> dict:
    out = {"efficiency": {}}
    for name, (t, gen) in workloads().items():
        out["efficiency"][name] = simulate(t, gen()).efficiency()
    h = hbm2e_pc(channels=1)
    best = 0.0
    for _ in range(3):
        reqs = random_uniform(h, 10000, 1 << 30, seed=3)
        t0 = time.perf_counter()
        simulate(h, reqs)
        best = max(best, len(reqs) / (time.perf_counter() - t0))
    out["requests_per_s"] = best
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--margin", type=float, default=0.25)
    ap.add_argument("--bless", action="store_true")
    a = ap.parse_args()
    now = measure()
    if a.bless:
        BASE.write_text(json.dumps(now, indent=1) + "\n")
        print("blessed", now)
        return 0
    base = json.loads(BASE.read_text())
    lines = ["# memsim performance gate", "", "| check | baseline | now | verdict |", "|---|---|---|---|"]
    bad = 0
    for k, b in base["efficiency"].items():
        n = now["efficiency"][k]
        ok = abs(n - b) <= 1e-9
        bad += not ok
        lines.append(f"| efficiency: {k} | {b:.6f} | {n:.6f} | {'ok' if ok else 'CHANGED'} |")
    b, n = base["requests_per_s"], now["requests_per_s"]
    ok = n >= (1 - a.margin) * b
    bad += not ok
    lines.append(f"| requests/s | {b:,.0f} | {n:,.0f} | {'ok' if ok else 'SLOWER'} (margin {a.margin:.0%}) |")
    Path("perf_report.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
