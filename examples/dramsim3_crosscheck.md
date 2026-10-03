DRAMsim3 commit 2981759, configs/DDR4_8Gb_x8_3200.ini with one rank; 60,000 cycles per saturation run.

| DRAMsim3 mapping | trace | metric | DRAMsim3 | memsim | memsim vs DRAMsim3 |
|---|---|---|---|---|---|
| rochrababgco | stream reads | throughput / peak | 0.670 | 0.619 | -7.6% |
| rochrababgco | stream, 1 in 3 writes | throughput / peak | 0.676 | 0.578 | -14.5% |
| rochrababgco | random reads (1 GiB) | throughput / peak | 0.450 | 0.440 | -2.2% |
| rochrababgco | random, 1 in 3 writes | throughput / peak | 0.445 | 0.442 | -0.7% |
| rochrababgco | same bank, next row each time | throughput / peak | 0.052 | 0.052 | +0.0% |
| rochrababgco | 4 interleaved streams | throughput / peak | 0.310 | 0.476 | +53.5% |
| rochrababgco | sparse random reads (latency) | mean read latency (cycles) | 73.0 | 72.1 | -1.2% |
| rochrabacobg | stream reads | throughput / peak | 0.955 | 0.958 | +0.3% |
| rochrabacobg | stream, 1 in 3 writes | throughput / peak | 0.903 | 0.911 | +1.0% |
| rochrabacobg | random reads (1 GiB) | throughput / peak | 0.450 | 0.438 | -2.8% |
| rochrabacobg | random, 1 in 3 writes | throughput / peak | 0.443 | 0.441 | -0.4% |
| rochrabacobg | same bank, next row each time | throughput / peak | 0.052 | 0.052 | +0.0% |
| rochrabacobg | 4 interleaved streams | throughput / peak | 0.814 | 0.684 | -15.9% |
| rochrabacobg | sparse random reads (latency) | mean read latency (cycles) | 70.7 | 69.9 | -1.2% |
