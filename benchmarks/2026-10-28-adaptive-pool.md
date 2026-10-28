# Adaptive Sandbox Pool Benchmark — 2026-10-28

This report evaluates a load-aware replenishment controller against the fixed
Python REPL pool measured in the stage 2 report. Raw JSON is stored under the
ignored `benchmark_results/stage3/` directory.

## Controller Design

The adaptive controller adds four behaviors:

1. Count both acquired sandboxes and sandbox acquisitions still in progress.
2. Defer background replenishment while execution load exceeds a configured
   threshold.
3. Re-check load between every warm-up batch so a batch already in progress
   can stop when user traffic arrives.
4. Increase the idle target from recent misses, cap it at a configured maximum,
   remove dead REPL processes, and shrink excess idle capacity after pressure
   leaves the miss window.

The detailed health endpoint exposes `in_use`, `pending_acquisitions`,
`replenishment_deferred`, and per-language dynamic target sizes.

## Environment and Workload

- API container limit: 2 CPUs and 2 GiB RAM
- Base Python pool: 2
- Adaptive maximum: 4
- Warm-up batch size: 1
- Miss window: 60 seconds
- Replenishment active threshold: 0
- Workload: light Python execution
- Per run: 30 requests at concurrency 3
- Repetitions: 3
- Server resource metrics sampled from `code-interpreter-api`

## Results

| Configuration | RPS | p50 | p95 | p99 | Hit rate | API memory |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Fixed replenishment | 1.33 | 2877 ms | 3503 ms | 3570 ms | 32.22% | 550.43 MiB |
| Adaptive replenishment | 1.48 | 2139 ms | 2379 ms | 2422 ms | 6.67% | 362.70 MiB |

All 180 requests across the baseline and adaptive groups succeeded. Each group
contains three runs of 30 requests.

Relative to fixed replenishment, the adaptive controller produced:

- 11.28% higher mean throughput
- 25.67% lower mean p50 latency
- 32.09% lower mean p95 latency
- 32.17% lower mean p99 latency
- 34.11% lower mean API-container memory usage

The adaptive runs were:

| Run | RPS | p50 | p95 | p99 | Hits / misses | CPU avg | Memory avg |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 1.37 | 2287 ms | 2486 ms | 2540 ms | 2 / 28 | 196.52% | 342.94 MiB |
| 2 | 1.46 | 2045 ms | 2438 ms | 2510 ms | 0 / 30 | 199.44% | 508.96 MiB |
| 3 | 1.62 | 2084 ms | 2212 ms | 2215 ms | 4 / 26 | 202.36% | 236.19 MiB |

## Interpretation

The lower hit rate is intentional. On a two-CPU container, creating replacement
REPLs competes directly with user executions. Fixed replenishment obtained more
hits but kept the CPU saturated with foreground execution and background pool
creation at the same time. The adaptive policy gives foreground work priority,
which improves throughput and tail latency even though fewer requests find an
idle REPL.

This demonstrates why hit rate cannot be the only pool objective. A useful
controller must balance hit rate, foreground latency, CPU pressure, and idle
memory. For this constrained environment, protecting foreground execution is
the better operating point.

## Scope and Next Step

The load generator and Docker Desktop share one laptop, so these are local
engineering measurements rather than production capacity guarantees. The next
experiment should move replenishment to a separate worker resource class or
apply CPU shares so background warm-up cannot consume the foreground CPU
budget. A longer sustained test should then verify that the latency improvement
holds beyond short bursts.
