"""Nightly sweep: load-latency curves for every pattern x scheduler x page policy, to sweep.csv."""

import csv

from memsim import hbm2e_pc, open_loop, random_uniform, simulate, streaming

t = hbm2e_pc(channels=1)
with open("sweep.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["pattern", "scheduler", "page", "offered_load", "achieved_efficiency", "latency_mean_ns",
                "latency_p99_ns", "row_hit_rate"])
    cases = (("stream", lambda: streaming(t, 6000), 0.95),
             ("random", lambda: random_uniform(t, 4000, 1 << 30, seed=1), 0.3))
    for pat, gen, top in cases:
        for sched in ("fcfs", "frfcfs"):
            for page in ("open", "closed"):
                for k in range(1, 11):
                    load = top * k / 10
                    reqs = open_loop(gen(), t, load * t.peak_gbps(), seed=k)
                    s = simulate(t, reqs, scheduler=sched, page=page).summary()
                    w.writerow([pat, sched, page, f"{load:.3f}", f"{s['efficiency']:.4f}",
                                f"{s['latency_mean_ns']:.1f}", f"{s['latency_p99_ns']:.1f}",
                                f"{s['row_hit_rate']:.3f}"])
print(open("sweep.csv").read())
