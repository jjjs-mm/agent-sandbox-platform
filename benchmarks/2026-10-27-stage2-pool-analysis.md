# Stage 2 Sandbox Pool Analysis — 2026-10-27

This report evaluates the Python REPL sandbox pool under an explicit resource
limit. It intentionally records both the speedup from a genuine pool hit and
the capacity problem revealed by sustained load. Raw JSON is written to the
ignored `benchmark_results/stage2/` directory.

## Environment

- Host: Apple `Mac16,12`, arm64, 10 logical CPUs, 16 GiB RAM
- Docker Desktop engine: 29.8.2
- API image: `agent-sandbox-platform:e2e`, built from the current checkout
- Services: API + Redis 7 + Garage 2.3.0
- API limit: 2 CPUs and 2 GiB RAM
- Workload: light Python execution, 30 requests per run, concurrency 3
- Repetitions: three for each configuration
- Load generator and services ran on the same laptop
- Server metrics: sampled from `code-interpreter-api` with `docker stats`

## Instrumentation Correction

`SandboxPool.acquire()` can create a fresh sandbox when the queue is empty.
The old execution path labeled every successful call to `acquire()` as a pool
hit. The benchmark work now propagates the real source (`pool_hit` or
`pool_miss`) through `ExecResponse` and records per-source latency. This avoids
overstating the pool hit rate.

## Default Pool Results

Configuration: pool enabled, Python pool size 2, serial warm-up batch size 1,
two-second replenish interval, exhaustion-triggered replenishment enabled.

| Run | RPS | p50 | p95 | p99 | Hit rate | API CPU avg | API memory avg |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 1.25 | 2954 ms | 4132 ms | 4205 ms | 33.33% | 200.66% | 554.79 MiB |
| 2 | 1.33 | 2911 ms | 3289 ms | 3385 ms | 30.00% | 200.03% | 551.24 MiB |
| 3 | 1.40 | 2767 ms | 3087 ms | 3119 ms | 33.33% | 200.51% | 545.25 MiB |

Across 90 successful requests, 29 were pool hits and 61 were pool misses. The
three-run mean throughput was 1.33 RPS with a 5.66% coefficient of variation.
The API held the full two-CPU allocation in every run.

### Hit Versus Miss Latency

Within the same pool-enabled runs:

- Pool hit: 29 requests, weighted average 563.33 ms
- Pool miss: 61 requests, weighted average 3018.77 ms
- A hit reduced average request latency by 81.34%, a 5.36x speedup
- Mean per-run hit p95: 727.33 ms
- Mean per-run miss p95: 3537.79 ms

Pre-warming therefore works when capacity is available. The performance
failure is the low sustained hit rate, not the hit execution path itself.

## Pool-Disabled Control

With the pool disabled, all 90 responses were correctly labeled `pool_miss`
and all requests succeeded.

| Run | RPS | p50 | p95 | p99 | API CPU avg | API memory avg |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 4.25 | 336 ms | 2321 ms | 2469 ms | 200.27% | 811.07 MiB |
| 2 | 9.25 | 190 ms | 930 ms | 1085 ms | 198.25% | 1218.56 MiB |
| 3 | 12.49 | 137 ms | 884 ms | 954 ms | 201.07% | 1231.87 MiB |

The control has a high 47.92% throughput coefficient of variation because
host filesystem and runtime caches warm between runs. It is not used as a
stable resume claim. It does show that, on a two-CPU host, continuous pool
replenishment can cost more throughput than it saves when most requests miss.

## Additional Finding: Warm-Up Storm

Increasing the pool to ten while keeping the upstream parallel warm-up batch
of five caused repeated 15-second REPL ready timeouts under the two-CPU limit.
Reducing `SANDBOX_POOL_PARALLEL_BATCH` to one allowed the pool to reach its
target. Six idle REPL sandboxes then consumed approximately 1.06 GiB and 253
container processes. Pool size is therefore a capacity trade-off, not a free
latency optimization.

## Conclusions and Next Experiment

1. Genuine pool hits reduce average latency by 81.34% for this workload.
2. A fixed pool of two cannot sustain concurrency three; its measured hit rate
   is only 32.22% (29/90).
3. Replenishment saturates the two-CPU API container and increases tail
   latency for misses.
4. Large parallel warm-up batches can create a retry storm and should be
   bounded by available CPU.

The next implementation step is an adaptive replenishment controller: cap
warm-up concurrency, avoid replenishing while execution CPU is saturated, and
scale the target pool from recent queue pressure. The same three-run workload
will then be repeated to measure hit-rate, throughput, and p95 improvements.

## Scope

This is a local engineering experiment, not a production capacity claim. The
load generator shares the laptop with Docker Desktop, and cold-control results
show cache effects. Resume claims should use only the stable within-run finding
that a measured pool hit was 81.34% faster on average than a measured miss.
