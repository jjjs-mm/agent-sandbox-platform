# Local Performance Baseline — 2026-10-27

This report records a reproducible local baseline for the multi-tenant Agent
Sandbox Control Plane. Raw JSON output is written to the ignored
`benchmark_results/` directory; the verified summary is kept in source control.

## Environment

- Host: Apple `Mac16,12`, arm64, 10 logical CPUs, 16 GiB RAM
- OS: macOS 26.2
- Docker Desktop engine: 29.8.2
- API image: `agent-sandbox-platform:e2e`, built from the current checkout
- Services: API + Redis 7 + Garage 2.3.0 through Docker Compose
- API container: no explicit Docker CPU or memory limit
- Tenant quota: enabled; default maximum concurrency 4
- Load generator and Docker services ran on the same laptop

The reported CPU and memory figures describe the load-generator host, not an
isolated API server. They are excluded from VM-sizing conclusions.

## Results

### HTTP Control-Plane Baseline

- Workload: `GET /health`
- Requests: 500
- Concurrency: 20
- Success rate: 100%
- Throughput: 585.69 requests/second
- Latency: p50 23.86 ms, p95 76.79 ms, p99 149.59 ms

### Real Python Execution Ramp

Workload per request:

```python
result = sum(range(10000))
print(result)
```

Each concurrency level executed 20 requests after five warm-up executions.

| Concurrency | Throughput | p50 | p95/p99 | Success |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 2.05 RPS | 171.66 ms | 1177.28 ms | 100% |
| 2 | 2.73 RPS | 693.39 ms | 1318.95 ms | 100% |
| 3 | 3.09 RPS | 1321.10 ms | 1601.86 ms | 100% |
| 4 | 3.06 RPS | 1599.22 ms | 1719.33 ms | 100% |

Throughput stopped increasing after concurrency 3 while tail latency continued
to rise. On this machine and workload, concurrency 3 is the preliminary local
saturation point.

### Fixed-Concurrency Execution Baseline

- Workload: the same light Python execution
- Requests: 100
- Concurrency: 3
- Success rate: 100%
- Duration: 34.24 seconds
- Throughput: 2.92 executions/second
- Latency: p50 1389.16 ms, p95 1844.92 ms, p99 2018.09 ms

### Tenant Quota Contention

- Temporary tenant limit: one concurrent execution
- Workload: ten one-second Python tasks submitted with client concurrency 5
- Result: one HTTP 200 and nine HTTP 429 responses
- Quota oversell: zero
- The tenant override was deleted after the test and defaults were restored

This validates the Redis Lua admission path under real HTTP contention: only
one request acquired the final available lease.

## Reproduction

Start the current source build:

```bash
MASTER_API_KEY=local-benchmark-master \
docker compose -f docker-compose.yml -f docker-compose.e2e.yml up --build -d
```

Run the control-plane baseline:

```bash
python -m scripts.load_test \
  --env http://127.0.0.1:8002 \
  --health-baseline --max-concurrency 20 --requests 500
```

Run the real-execution ramp:

```bash
python -m scripts.load_test \
  --env http://127.0.0.1:8002 --api-key "$API_KEY" \
  --scenario cpu_light --min-concurrency 1 --max-concurrency 4 \
  --step 1 --requests 20 --warmup 5
```

Run the quota-contention workload after setting the tenant concurrency limit
to one through the admin API:

```bash
python -m scripts.load_test \
  --env http://127.0.0.1:8002 --api-key "$API_KEY" \
  --scenario quota_hold --quick --min-concurrency 5 \
  --max-concurrency 5 --requests 10 --warmup 0
```

## Interpretation Scope

These numbers are a single-machine engineering baseline, not a production
capacity claim. The next benchmark stage should isolate the load generator from
the API host, apply explicit Docker CPU/memory limits, repeat each workload at
least three times, and collect container-side CPU, memory, pool hit rate, and
cold-start metrics.
