"""Every number quoted in the README and in deck SimEng 04.

    python examples/results.py                    # writes examples/results.md
    DRAMSIM3=/path/to/DRAMsim3 python examples/results.py    # also re-runs the DRAMsim3 cross-check

All timing values are illustrative (see src/memsim/timing.py). Section 8 is the
FHE simulator's view of this HBM model; the bootstrap-level comparison is in
FHE_Accelerator_Sim's examples/results.md (section 20).
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
import time
from pathlib import Path

from memsim import (
    AddressMapping,
    Request,
    ddr4_3200,
    hbm2e_pc,
    interleaved_streams,
    open_loop,
    random_uniform,
    simulate,
    streaming,
    strided,
)
from memsim.fhe import HBMChunkModel

HERE = Path(__file__).parent
ROOT = HERE.parent
OUT: list[str] = []
DDR4, HBM = ddr4_3200(), hbm2e_pc(channels=1)
DEV = {"DDR4": DDR4, "HBM": HBM}


def h(title):
    OUT.append(f"\n## {title}\n")


def table(head, rows):
    OUT.append("| " + " | ".join(head) + " |")
    OUT.append("|" + "---|" * len(head))
    for r in rows:
        OUT.append("| " + " | ".join(str(x) for x in r) + " |")


def lat1(t, reqs, **kw):
    simulate(t, reqs, **kw)
    return reqs[-1].done - reqs[-1].arrival


# 1 ── devices ──────────────────────────────────────────────────────────
h("1. Device models (illustrative timing values, in tCK)")
keys = ["tck_ns", "bus_bytes", "bl", "bankgroups", "banks", "row_bytes", "CL", "CWL", "RCD", "RP", "RAS", "RC",
        "RRD_S", "RRD_L", "FAW", "CCD_S", "CCD_L", "WR", "WTR_S", "WTR_L", "RTP", "RFC", "REFI"]
table(["parameter"] + list(DEV), [[k] + [getattr(t, k) for t in DEV.values()] for k in keys] +
      [["peak per channel, GB/s"] + [f"{t.channel_peak_gbps():.1f}" for t in DEV.values()]])

# 2 ── analytic checks ──────────────────────────────────────────────────
h("2. Analytic checks: simulated against the closed-form value")
rows = []
for name, t in DEV.items():
    m = AddressMapping(t)
    same_row = t.access_bytes * t.bankgroups
    other_row = 1 << m.shift["ro"]
    checks = [
        ("read, bank closed (cycles)", lat1(t, [Request(0)]), t.RCD + t.CL + t.burst_cycles, "RCD + CL + BL/2"),
        ("write, bank closed (cycles)", lat1(t, [Request(0, write=True)]), t.RCD + t.CWL + t.burst_cycles,
         "RCD + CWL + BL/2"),
        ("read, row hit (cycles)", lat1(t, [Request(0), Request(same_row, arrival=1000)]), t.CL + t.burst_cycles,
         "CL + BL/2"),
        ("read, row conflict (cycles)", lat1(t, [Request(0), Request(other_row, arrival=1000)]),
         t.RP + t.RCD + t.CL + t.burst_cycles, "RP + RCD + CL + BL/2"),
    ]
    s_off = simulate(t, streaming(t, 12000), refresh=False).efficiency()
    s_on = simulate(t, streaming(t, 12000)).efficiency()
    stride = simulate(t, strided(t, 600, other_row), refresh=False).efficiency()
    rnd = simulate(t, random_uniform(t, 6000, 1 << 30, seed=3), page="closed", refresh=False).efficiency()
    checks += [
        ("streaming, no refresh (fraction of peak)", f"{s_off:.3f}", "1", "peak"),
        ("refresh loss on a stream", f"{1 - s_on / s_off:.3f}", f"{t.RFC / t.REFI:.3f}", "RFC / REFI"),
        ("same bank, next row each access", f"{stride:.3f}", f"{t.burst_cycles / t.RC:.3f}", "(BL/2) / RC"),
        ("random reads, closed page", f"{rnd:.3f}", f"{min(1, 4 * t.burst_cycles / t.FAW):.3f}",
         "4 (BL/2) / FAW (activate-limited bound)"),
    ]
    rows += [[name, c, sim, ref, f] for c, sim, ref, f in checks]
table(["device", "check", "simulated", "closed form", "formula"], rows)

# 3 ── patterns and policies ────────────────────────────────────────────
h("3. Access patterns against scheduling and page policy (one HBM pseudo-channel, closed loop)")
pats = {
    "streaming": lambda t: streaming(t, 8000),
    "streaming, 1 in 3 writes": lambda t: streaming(t, 8000, write_every=3),
    "random (1 GiB)": lambda t: random_uniform(t, 6000, 1 << 30, seed=1),
    "random, 1 in 3 writes": lambda t: random_uniform(t, 6000, 1 << 30, seed=1, write_frac=1 / 3),
    "4 interleaved streams": lambda t: interleaved_streams(t, 8000, 4, 1 << 24, seed=1),
    "stride 4 KiB": lambda t: strided(t, 6000, 4096),
}
rows = []
for pname, gen in pats.items():
    cells = [pname]
    for sched in ("fcfs", "frfcfs"):
        for page in ("open", "closed"):
            r = simulate(HBM, gen(HBM), scheduler=sched, page=page)
            cells.append(f"{r.efficiency():.3f} ({r.row_hit_rate():.2f})")
    rows.append(cells)
table(["pattern", "FCFS open", "FCFS closed", "FR-FCFS open", "FR-FCFS closed"], rows)
OUT.append("\nCells: achieved bandwidth as a fraction of peak (row-hit rate). Refresh on.")

# 4 ── address mapping ──────────────────────────────────────────────────
h("4. Address mapping (DDR4 channel, FR-FCFS, open page, no refresh)")
rows = []
for scheme in ("RoRaBaCoBgCh", "RoRaBgBaCoCh", "RoCoRaBgBaCh"):
    for xor in (False, True):
        m = AddressMapping(DDR4, scheme, xor)
        row = 1 << AddressMapping(DDR4, scheme).shift["ro"]
        cells = [scheme + (" + XOR" if xor else "")]
        for reqs in (streaming(DDR4, 6000), strided(DDR4, 1500, row), strided(DDR4, 3000, 8192),
                     random_uniform(DDR4, 4000, 1 << 30, seed=2)):
            cells.append(f"{simulate(DDR4, reqs, m, refresh=False).efficiency():.3f}")
        rows.append(cells)
table(["mapping (MSB to LSB)", "streaming", "stride = one row (same bank)", "stride 8 KiB", "random"], rows)

# 5 ── load-latency ─────────────────────────────────────────────────────
h("5. Load against latency (HBM pseudo-channel, FR-FCFS, open page, Poisson arrivals)")
rows = []
for load in (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.85, 0.9):
    cells = [f"{load:.2f}"]
    for gen, cap in ((lambda: streaming(HBM, 8000), 1.0), (lambda: random_uniform(HBM, 6000, 1 << 30, seed=5), 0.3)):
        if load > cap + 1e-9 and cap < 1.0:
            cells += ["", ""]
            continue
        r = simulate(HBM, open_loop(gen(), HBM, load * HBM.peak_gbps(), seed=9))
        s = r.summary()
        cells += [f"{s['latency_mean_ns']:.0f}", f"{s['latency_p99_ns']:.0f}"]
    rows.append(cells)
table(["offered load (fraction of peak)", "streaming mean ns", "streaming p99 ns", "random mean ns", "random p99 ns"],
      rows)
OUT.append("\nRandom traffic saturates near 0.27 of peak (section 3), so its curve stops at 0.3.")

# 6 ── one number? ─────────────────────────────────────────────────────
h("6. Why 'bandwidth x efficiency' is not one number")
effs = []
for pname, gen in pats.items():
    effs.append((pname, simulate(HBM, gen(HBM)).efficiency()))
lo, hi = min(e for _, e in effs), max(e for _, e in effs)
OUT.append(f"On one HBM pseudo-channel with one controller (FR-FCFS, open page), achieved efficiency ranges from "
           f"{lo:.2f} to {hi:.2f} across the six patterns of section 3. A single derating factor is right for at "
           f"most one traffic mix, one mapping and one load.")

# 7 ── cross-check ──────────────────────────────────────────────────────
h("7. Cross-check against DRAMsim3 (same traces, same timing, same mapping)")
dsim = os.environ.get("DRAMSIM3")
saved = HERE / "dramsim3_crosscheck.md"
if dsim:
    p = subprocess.run([sys.executable, str(ROOT / "validation" / "dramsim3_crosscheck.py")], capture_output=True,
                       text=True, env={**os.environ}, check=True)
    saved.write_text(p.stdout)
OUT.append(saved.read_text().strip() if saved.exists() else "(not run: set DRAMSIM3)")
OUT.append("\nRecorded locally with a DRAMsim3 build (validation/dramsim3_crosscheck.py); not run in CI.")

# 8 ── the HBM model as FHE_Accelerator_Sim sees it ────────────────────
h("8. HBM chunk efficiency as FHE_Accelerator_Sim sees it (HBMChunkModel, 1000 GB/s)")
rows = []
for scheme, sched in (("RoRaBaCoBgCh", "frfcfs"), ("RoRaBgBaCoCh", "frfcfs"), ("RoRaBaCoBgCh", "fcfs")):
    m = HBMChunkModel(scheme=scheme, scheduler=sched)
    for size in (4 << 20, 1 << 20, 256 << 10, 64 << 10):
        rows.append([scheme, sched, f"{size >> 10} KiB", f"{m.efficiency(size, False, False):.3f}",
                     f"{m.efficiency(size, True, False):.3f}", f"{m.efficiency(size, False, True):.3f}"])
table(["mapping", "scheduler", "chunk", "read after read", "write after read", "read after write"], rows)
OUT.append(f"\nRefresh costs a long stream {100 * HBMChunkModel().refresh_loss():.1f}% on this HBM model "
           f"(RFC / REFI = {100 * HBM.RFC / HBM.REFI:.1f}%).")

# 9 ── speed and tests ─────────────────────────────────────────────────
h("9. Simulator speed and test suite")
t0 = time.perf_counter()
n = 20000
simulate(HBM, random_uniform(HBM, n, 1 << 30, seed=1))
dt = time.perf_counter() - t0
OUT.append(f"Pure Python: {n / dt:,.0f} random requests per second (FR-FCFS, 32-entry queue) on this machine.")
p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=ROOT, capture_output=True,
                   text=True)
summary = [ln for ln in p.stdout.splitlines() if " passed" in ln or " failed" in ln][-1]
OUT.append(f"\n`pytest`: {summary.strip('= ')}.")
assert p.returncode == 0, p.stdout[-2000:]

OUT.insert(0, f"# Results (generated by examples/results.py)\n\nRecorded on {platform.machine()} Linux, Python "
              f"{platform.python_version()}. Every number in the README and in deck SimEng 04 comes from here.")
(HERE / "results.md").write_text("\n".join(OUT) + "\n")
print("\n".join(OUT))
