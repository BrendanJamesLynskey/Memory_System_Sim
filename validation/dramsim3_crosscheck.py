"""Cross-check memsim against DRAMsim3 on the same traces.

DRAMsim3 (Li et al., IEEE CAL 2020; https://github.com/umd-memsys/DRAMsim3, MIT licence)
is a widely used cycle-accurate DRAM simulator. Its configs/DDR4_8Gb_x8_3200.ini
has the same timing parameters as memsim's ddr4_3200 preset (in tCK); this script
reduces it to one rank (channel_size 8192 MB), sets the same address mapping in
both simulators, and runs identical traces:

* saturation runs: every request available at cycle 0; compare the requests
  served within C cycles (throughput, as a fraction of the data-bus peak);
* a sparse run: random reads 1,000 cycles apart; compare mean read latency.

The controllers differ by design (DRAMsim3 has per-bank command queues of depth 8
and a 32-entry transaction queue, staggered refresh and its own write-drain
policy; memsim has one 32-entry queue), so agreement is expected in trend and
rough magnitude, not to the cycle.

    DRAMSIM3=/path/to/DRAMsim3 python validation/dramsim3_crosscheck.py   # prints a Markdown table
"""

from __future__ import annotations

import json
import os
import random
import subprocess
import sys
import tempfile
from pathlib import Path

from memsim import AddressMapping, Request, ddr4_3200
from memsim.controller import Channel

C = 60000                          # cycles per saturation run
MAPS = {"rochrababgco": "ro,ch,ra,ba,bg,co",     # DRAMsim3's default: a row's bursts stay in one bank group
        "rochrabacobg": "ro,ch,ra,ba,co,bg"}     # bank group below column: bursts alternate groups


def traces(t, m: AddressMapping) -> dict[str, list[tuple[int, bool, int]]]:
    a, rng = t.access_bytes, random.Random(7)
    n = C // t.burst_cycles + 2000
    row = 1 << m.shift["ro"]
    span = 1 << 30
    out = {
        "stream reads": [(k * a, False, 0) for k in range(n)],
        "stream, 1 in 3 writes": [(k * a, k % 3 == 2, 0) for k in range(n)],
        "random reads (1 GiB)": [(rng.randrange(span // a) * a, False, 0) for _ in range(n)],
        "random, 1 in 3 writes": [(rng.randrange(span // a) * a, rng.random() < 1 / 3, 0) for _ in range(n)],
        "same bank, next row each time": [(k * row, False, 0) for k in range(n // 8)],
    }
    pos = [0, 0, 0, 0]
    inter = []
    for _ in range(n):
        s = rng.randrange(4)
        inter.append((s * (1 << 26) + pos[s] * a, False, 0))
        pos[s] += 1
    out["4 interleaved streams"] = inter
    out["sparse random reads (latency)"] = [(rng.randrange(span // a) * a, False, 1000 * k) for k in range(400)]
    return out


def run_dramsim3(root: Path, ini: Path, trace, cycles: int, tmp: Path) -> dict:
    trc = tmp / "t.trc"
    trc.write_text("".join(f"{hex(addr)} {'WRITE' if w else 'READ'} {arr}\n" for addr, w, arr in trace))
    out = tmp / "out"
    out.mkdir(exist_ok=True)
    subprocess.run([str(root / "build" / "dramsim3main"), str(ini), "-c", str(cycles), "-t", str(trc), "-o", str(out)],
                   check=True, capture_output=True)
    return json.loads((out / "dramsim3.json").read_text())["0"]


def run_memsim(t, m: AddressMapping, trace, cycles: int) -> tuple[int, float]:
    reqs = []
    for k, (addr, w, arr) in enumerate(trace):
        r = Request(addr, write=w, arrival=arr, id=k)
        d = m.decode(addr)
        r.ch, r.ra, r.bg, r.ba, r.ro = d.ch, d.ra, d.bg, d.ba, d.ro
        reqs.append(r)
    Channel(t, "frfcfs", "open", 32, refresh=True).run(reqs)
    served = sum(r.done <= cycles for r in reqs)
    rd = [r.done - r.arrival for r in reqs if not r.write]
    return served, sum(rd) / len(rd)


def main() -> int:
    root = Path(os.environ.get("DRAMSIM3", ""))
    if not (root / "build" / "dramsim3main").exists():
        print("set DRAMSIM3 to a built DRAMsim3 checkout", file=sys.stderr)
        return 2
    commit = subprocess.run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"], capture_output=True,
                            text=True, check=True).stdout.strip()
    t = ddr4_3200()
    base = (root / "configs" / "DDR4_8Gb_x8_3200.ini").read_text()
    base = base.replace("channel_size = 16384", "channel_size = 8192")
    rows = []
    for dmap, order in MAPS.items():
        m = AddressMapping(t, order)
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            ini = tmp / "ddr4.ini"
            ini.write_text(base.replace("address_mapping = rochrababgco", f"address_mapping = {dmap}"))
            for name, tr in traces(t, m).items():
                sparse = name.startswith("sparse")
                cycles = tr[-1][2] + 2000 if sparse else C
                d = run_dramsim3(root, ini, tr, cycles, tmp)
                served_m, lat_m = run_memsim(t, m, tr, cycles)
                if sparse:
                    rows.append([dmap, name, "mean read latency (cycles)", f"{d['average_read_latency']:.1f}",
                                 f"{lat_m:.1f}", f"{100 * (lat_m / d['average_read_latency'] - 1):+.1f}%"])
                else:
                    served_d = d["num_reads_done"] + d["num_writes_done"]
                    e_d, e_m = served_d * t.burst_cycles / C, served_m * t.burst_cycles / C
                    rows.append([dmap, name, "throughput / peak", f"{e_d:.3f}", f"{e_m:.3f}",
                                 f"{100 * (e_m / e_d - 1):+.1f}%"])
    print(f"DRAMsim3 commit {commit}, configs/DDR4_8Gb_x8_3200.ini with one rank; {C:,} cycles per saturation run.\n")
    print("| DRAMsim3 mapping | trace | metric | DRAMsim3 | memsim | memsim vs DRAMsim3 |")
    print("|---|---|---|---|---|---|")
    for r in rows:
        print("| " + " | ".join(r) + " |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
