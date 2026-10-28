# Testing Guide

This repository uses three test layers. Only the live HTTP layer should be treated as the source of truth for LibreChat compatibility.

## Test Layers

```text
tests/
├── unit/         # mocked component logic
├── integration/  # in-process FastAPI wiring and contract checks
├── functional/   # live HTTP coverage against a running API
└── snapshots/    # reference payloads
```

### `tests/unit/`

- Mock Redis, S3, sandboxing, and other infrastructure.
- Fast feedback for service logic.
- No external stack required.

### `tests/integration/`

- Exercise the FastAPI app in-process via `TestClient`.
- Validate routing, auth, request parsing, response shape, and thin wiring.
- `contract_only` tests live here.
- These tests are not end-to-end compatibility proof.

### `tests/functional/`

- Exercise a running API over real HTTP.
- Cover sessions, files, downloads, state continuity, PTC, and concurrency.
- `client_replay` tests mirror the current `librechat-agents` runtime flow.
- This layer is the primary compatibility signal for LibreChat support.

## Markers

- `contract_only`: in-process contract and wiring checks.
- `live_api`: any test that hits a running API.
- `client_replay`: runtime-faithful client replay against the live API.
- `slow`: heavier scenarios for dedicated CI jobs.

## Running Tests

### Prerequisites

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install pytest pytest-asyncio pytest-cov pytest-mock
```

For live API coverage:

```bash
cp .env.example .env
docker compose up -d
```

### Fast Feedback

```bash
pytest tests/unit/ tests/integration/
```

### Full Live Compatibility Coverage

```bash
API_BASE="http://localhost:8000" \
API_KEY="your-secure-api-key-here-change-this-in-production" \
pytest tests/functional/ -m live_api -v
```

### Runtime-Faithful Client Replay Only

```bash
API_BASE="http://localhost:8000" \
API_KEY="your-secure-api-key-here-change-this-in-production" \
pytest tests/functional/ -m client_replay -v
```

### Multi-Tenant File Isolation

This live test creates two temporary managed API keys, assigns them to
different tenants, and verifies that tenant B cannot list, download, inspect,
or execute with a file uploaded by tenant A. The keys are revoked during test
cleanup.

```bash
API_BASE="http://localhost:8000" \
MASTER_API_KEY="your-secure-master-key" \
pytest tests/functional/test_tenant_file_isolation.py -v
```

The running API must be built from the current checkout; the published image
does not contain uncommitted local tenant-isolation changes.

For a fast application-only rebuild, reuse the published runtime image and
overlay the current `src/` directory:

```bash
MASTER_API_KEY="local-e2e-master-key" \
docker compose -f docker-compose.yml -f docker-compose.e2e.yml \
  up --build -d

API_BASE="http://localhost:8000" \
MASTER_API_KEY="local-e2e-master-key" \
pytest tests/functional/test_tenant_file_isolation.py -v
```

`Dockerfile.dev` is only an iteration image. Use the main multi-stage
`Dockerfile` for release and runtime-layer changes.

### Tenant Quota Atomicity

The quota tests use a real local Redis instance. The race case launches ten
coroutines against a tenant with one available slot, where exactly one request
must win. A second case verifies that a tenant override takes precedence over
environment defaults and that deleting it restores those defaults.

```bash
docker compose up -d redis
RUN_REDIS_TESTS=1 \
pytest tests/functional/test_tenant_quota_redis.py -v
```

The normal test suite skips this test because it requires external Redis.

### Marker-Based Runs

```bash
pytest -m contract_only
pytest -m slow
pytest -m "live_api and not slow"
```

## Current Source of Truth

- Direct `/exec` and file lifecycle compatibility: `tests/functional/test_client_replay.py`
- PTC runtime replay: `tests/functional/test_client_replay.py`
- File contracts and reuse: `tests/functional/test_files.py`
- Generated artifact integrity: `tests/functional/test_generated_artifacts.py`
- Mounted file edit persistence: `tests/functional/test_mounted_file_edits.py`

If a mocked integration test passes but a `live_api` or `client_replay` test fails, treat the live failure as authoritative.

## CI/CD Test Tiers

GitHub Actions uses two workflow tiers:

- `ci.yml`: required PR checks for static analysis, unit tests, and integration tests
- `release.yml`: publishes the multi-arch app image consumed by `docker-compose.yml` (default `API_IMAGE`) after per-arch smoke validation
