# Memory_System_Sim

A **command-level DRAM/HBM timing simulator** in Python: per-bank state machines,
the JEDEC-style timing constraints (tRCD, tRP, tCL, tRAS, tRC, tRRD, tFAW, tCCD,
tWTR, tWR, tRTP, refresh), configurable address mapping, FCFS and FR-FCFS
scheduling, open and closed page policies, and an **independent protocol checker**
that re-verifies every command the controller issues. It plugs into
[FHE_Accelerator_Sim](https://github.com/BrendanJamesLynskey/FHE_Accelerator_Sim)
as an optional HBM model.

It is the companion code for deck 04 (modelling memory systems: DRAM and HBM) of the
[Simulation Engineering Toolkit](https://github.com/BrendanJamesLynskey/SimEng_Hub_Toolkit)
series.

The point is to replace "bandwidth × efficiency" with a model that *derives* the
efficiency, and to trust that model for stated reasons:

* **Analytic checks.** Unloaded latencies (row hit, miss, conflict) match their closed
  forms exactly. Streaming without refresh reaches peak. Refresh costs a stream
  RFC/REFI. A same-bank row stride runs at one access per tRC. Random closed-page
  traffic sits just under the tFAW activate bound.
* **An independent oracle.** `memsim.checker` replays the command log and checks every
  rule directly, sharing no code with the controller. Hypothesis generates random
  traces, policies and mappings, and every one must produce a legal command stream.
  This found a real bug: write-to-read turnaround (tWTR_L) was tracked only for the
  most recent write. The checker's own tests plant violations and run a controller
  with tFAW deliberately removed.
* **Cross-checked against [DRAMsim3](https://github.com/umd-memsys/DRAMsim3).**
  Identical traces and timing (its `DDR4_8Gb_x8_3200` config, one rank) and the same
  address mapping. With bank groups interleaved, streaming throughput agrees to
  within 0.3%, random reads to within 3%, a same-bank row stride exactly, and
  unloaded latency to within 1.2%. Interleaved streams differ by 16–54%: DRAMsim3 has
  per-bank queues and memsim one shared queue.

**All timing values are illustrative**, in the range of a DDR4-3200 speed bin and an
HBM2E-class pseudo-channel; parameter names follow JESD79-4 (DDR4) and JESD235 (HBM),
and values are not quoted from those standards. The DDR4 values coincide with DRAMsim3's
`DDR4_8Gb_x8_3200.ini`, which is what makes the cross-check like for like. All numbers
below come from [`examples/results.md`](examples/results.md).

---

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[test]"
pytest                                            # 31 tests, about 15 seconds
memsim --preset hbm --pattern random --n 20000    # one channel, FR-FCFS, open page
memsim --preset ddr4 --pattern stride --stride 131072 --xor-bank
python examples/results.py                        # regenerates examples/results.md
```

```python
from memsim import hbm2e_pc, random_uniform, simulate, check

t = hbm2e_pc(channels=1)
res = simulate(t, random_uniform(t, 10000, 1 << 30), scheduler="frfcfs", page="open", log=True)
print(res.summary()["efficiency"], res.summary()["latency_p99_ns"])
assert check(res.channels[0].log, t) == []        # the independent protocol checker
```

## Selected results

**Analytic checks** (§2), simulated against the closed form:

| device | check | simulated | closed form |
|---|---|---|---|
| DDR4 | read, row conflict (cycles) | 70 | RP + RCD + CL + BL/2 = 70 |
| DDR4 | same bank, next row each access | 0.054 | (BL/2) / RC = 0.054 |
| HBM | refresh loss on a stream | 0.094 | RFC / REFI = 0.090 |
| HBM | random reads, closed page | 0.304 | 4 (BL/2) / FAW = 0.308 |

**One pseudo-channel, six patterns** (§3): achieved bandwidth as a fraction of peak.

| pattern | FCFS, open page | FR-FCFS, open page |
|---|---|---|
| streaming | 0.563 | 0.925 |
| streaming, 1 in 3 writes | 0.147 | 0.804 |
| random (1 GiB) | 0.039 | 0.270 |
| 4 interleaved streams | 0.041 | 0.484 |

The same device delivers between 0.10 and 0.93 of its peak depending on the traffic
(§6), so no single derating factor is right.

**Address mapping matters as much as the scheduler** (§4, DDR4). If a row's bursts
stay in one bank group, back-to-back reads wait tCCD_L instead of tCCD_S, and
streaming drops from 0.998 to 0.526 of peak. XOR bank hashing turns a same-bank row
stride from 0.054 into 0.456.

**In FHE_Accelerator_Sim** (its `results.md` §20), the ARK-class bootstrap goes from
13.94 ms at peak bandwidth to 15.31 ms with this HBM model, and to 23.70 ms with an
FCFS controller. With all three of its acceleration techniques at 160 GB/s, peak
bandwidth calls the design MAC-bound; this model calls it memory-bound.

## How it works

| File | What |
|------|------|
| [`timing.py`](src/memsim/timing.py) | Organisation and timing parameters in tCK; DDR4-3200-like and HBM2E-class presets |
| [`mapping.py`](src/memsim/mapping.py) | Address → channel, rank, bank group, bank, row, column; named schemes and XOR bank hashing |
| [`controller.py`](src/memsim/controller.py) | Per-channel controller: bank and rank state, earliest-legal-time per command, FCFS / FR-FCFS, open / closed page, refresh; skips idle time |
| [`checker.py`](src/memsim/checker.py) | Independent protocol checker over the command log |
| [`patterns.py`](src/memsim/patterns.py) | Streaming, strided, random, interleaved streams; open-loop (Poisson) arrivals |
| [`fhe.py`](src/memsim/fhe.py) | `HBMChunkModel`: FHE_Accelerator_Sim's memory-model interface, memoised command-level simulation of each chunk |
| [`validation/dramsim3_crosscheck.py`](validation/dramsim3_crosscheck.py) | The DRAMsim3 comparison (needs a DRAMsim3 build) |

Pure Python runs at about 6,500 requests per second (§9), which is enough for the
patterns here and for FHE_Accelerator_Sim through memoisation; a Rust core (as in
[Rust_DES_Kernel](https://github.com/BrendanJamesLynskey/Rust_DES_Kernel)) is the next
step if it is not.

## Not modelled

Per-bank refresh and refresh postponement; power-down states; rank-to-rank switching
time (the presets have one rank); write queues with drain watermarks (writes are
scheduled like reads); command/address bus contention beyond one command per cycle;
on-die ECC; HBM's pseudo-channel sharing of the row/column command buses.

## CI

* **GitHub Actions** ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)): ruff, the
  test suite on Python 3.10 and 3.12, a CLI smoke test, and an FHE_Accelerator_Sim
  integration job.
* **Jenkins** ([`Jenkinsfile`](Jenkinsfile)): lint, tests with JUnit and Cobertura
  coverage, a behaviour-and-speed gate against
  [`ci/perf_baseline.json`](ci/perf_baseline.json), `results.md`, and a nightly
  load-latency sweep.

## References

* S. Rixner, W. J. Dally, U. J. Kapasi, P. Mattson, J. D. Owens, "Memory Access Scheduling", ISCA 2000 (FR-FCFS).
* Z. Zhang, Z. Zhu, X. Zhang, "A Permutation-based Page Interleaving Scheme to Reduce Row-buffer Conflicts", MICRO 2000 (XOR bank mapping).
* S. Li, Z. Yang, D. Reddy, A. Srivastava, B. Jacob, "DRAMsim3: a Cycle-accurate, Thermal-Capable DRAM Simulator", IEEE Computer Architecture Letters, 2020.
* Y. Kim, W. Yang, O. Mutlu, "Ramulator: A Fast and Extensible DRAM Simulator", IEEE Computer Architecture Letters, 2016 (doi:10.1109/LCA.2015.2414456).
* JEDEC JESD79-4 (DDR4 SDRAM) and JESD235 (High Bandwidth Memory): the standards that define the parameters (values here are not taken from them).

## Related

* [FHE_Accelerator_Sim](https://github.com/BrendanJamesLynskey/FHE_Accelerator_Sim): the simulator this plugs into.
* [Interview_SoC_Architecture](https://github.com/BrendanJamesLynskey/Interview_SoC_Architecture): SoC and memory-architecture interview questions.

## Licence

MIT.
