"""Tests for the reproducible load-test harness."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from scripts.load_test.client import LoadTestClient
from scripts.load_test.analysis import VMSizingAnalyzer
from scripts.load_test.monitor import ResourceMonitor
from scripts.load_test.models import (
    ConcurrencyTestResult,
    ExecutionResult,
    LoadTestConfig,
    LoadTestReport,
    ScenarioSummary,
    SystemMetrics,
)
from scripts.load_test.runner import LoadTestRunner
from scripts.load_test.scenarios.base import BaseScenario


class TinyScenario(BaseScenario):
    id = "tiny"
    name = "Tiny"
    category = "cpu"
    language = "py"

    def get_code(self, iteration: int = 0) -> str:
        return f"print({iteration})"


@pytest.mark.asyncio
async def test_client_records_tenant_quota_rejection() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/exec"
        assert request.headers["x-api-key"] == "tenant-key"
        return httpx.Response(
            429,
            json={"detail": "Tenant concurrent execution limit reached"},
        )

    client = LoadTestClient(
        "http://sandbox.test",
        "tenant-key",
        transport=httpx.MockTransport(handler),
    )
    async with client:
        result = await client.execute_code("print(1)", scenario_id="quota_hold")

    assert result.success is False
    assert result.status_code == 429
    assert result.error == "Tenant concurrent execution limit reached"


@pytest.mark.asyncio
async def test_client_records_container_source() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/exec"
        return httpx.Response(
            200,
            json={
                "session_id": "session-1",
                "files": [],
                "stdout": "ok\n",
                "stderr": "",
                "container_source": "pool_miss",
            },
        )

    client = LoadTestClient(
        "http://sandbox.test",
        "tenant-key",
        transport=httpx.MockTransport(handler),
    )
    async with client:
        result = await client.execute_code("print(1)", scenario_id="tiny")

    assert result.success is True
    assert result.container_source == "pool_miss"


@pytest.mark.asyncio
async def test_health_probe_records_latency_and_status() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/health"
        return httpx.Response(200, json={"status": "healthy"})

    client = LoadTestClient(
        "http://sandbox.test",
        "tenant-key",
        transport=httpx.MockTransport(handler),
    )
    async with client:
        result = await client.probe_health()

    assert result.success is True
    assert result.status_code == 200
    assert result.latency_ms >= 0


@pytest.mark.asyncio
async def test_health_baseline_aggregates_concurrent_requests() -> None:
    runner = LoadTestRunner(
        LoadTestConfig(
            base_url="http://sandbox.test",
            api_key="tenant-key",
            max_concurrency=3,
            enable_sandbox_stats=False,
        )
    )
    runner.client.probe_health = AsyncMock(
        return_value=ExecutionResult(
            success=True,
            latency_ms=2.5,
            status_code=200,
            language="http",
            scenario_id="health_baseline",
        )
    )

    summary = await runner.run_health_baseline(concurrency=3, num_requests=8)

    result = summary.results[0]
    assert result.total_requests == 8
    assert result.successful_requests == 8
    assert result.status_codes == {200: 8}
    assert result.p95_latency_ms == 2.5


def test_result_report_exposes_quota_rejections() -> None:
    result = ConcurrencyTestResult(
        concurrency=5,
        scenario_id="quota_hold",
        total_requests=5,
        failed_requests=4,
        status_codes={200: 1, 429: 4},
    )

    payload = result.to_dict()

    assert result.quota_rejections == 4
    assert payload["quota_rejections"] == 4
    assert payload["status_codes"] == {"200": 1, "429": 4}


def test_control_plane_report_does_not_infer_vm_size() -> None:
    now = datetime.now(timezone.utc)
    result = ConcurrencyTestResult(
        concurrency=20,
        scenario_id="health_baseline",
        total_requests=100,
        successful_requests=100,
    )
    report = LoadTestReport(
        test_id="control-test",
        environment="local",
        start_time=now,
        end_time=now,
        config=LoadTestConfig(base_url="http://sandbox.test", api_key="key"),
        scenarios=[
            ScenarioSummary(
                scenario_id="health_baseline",
                scenario_name="Health Endpoint Baseline",
                category="control",
                language="http",
                results=[result],
            )
        ],
    )

    recommendation = VMSizingAnalyzer().analyze_report(report)

    assert recommendation.cpu_cores == 0
    assert recommendation.memory_gb == 0
    assert recommendation.bottleneck == "not_applicable"


def test_vm_sizing_is_disabled_without_server_metrics() -> None:
    now = datetime.now(timezone.utc)
    report = LoadTestReport(
        test_id="exec-test",
        environment="local",
        start_time=now,
        end_time=now,
        config=LoadTestConfig(base_url="http://sandbox.test", api_key="key"),
        scenarios=[
            ScenarioSummary(
                scenario_id="cpu_light",
                scenario_name="CPU Light",
                category="cpu",
                language="py",
                results=[],
            )
        ],
    )

    recommendation = VMSizingAnalyzer().analyze_report(report)

    assert recommendation.bottleneck == "not_applicable"
    assert "client-host metrics" in recommendation.notes[0]


def test_docker_size_parser_supports_binary_units() -> None:
    assert ResourceMonitor._size_to_mb("512MiB") == 512
    assert ResourceMonitor._size_to_mb("1.5GiB") == 1536
    assert ResourceMonitor._io_pair_to_mb("2MiB / 3MiB") == (2, 3)


@pytest.mark.asyncio
async def test_execution_result_includes_pool_metric_deltas() -> None:
    runner = LoadTestRunner(
        LoadTestConfig(
            base_url="http://sandbox.test",
            api_key="tenant-key",
            enable_sandbox_stats=False,
        )
    )
    runner.client = SimpleNamespace(
        execute_code=AsyncMock(
            return_value=ExecutionResult(
                success=True,
                latency_ms=10,
                status_code=200,
                language="py",
                scenario_id="tiny",
                container_source="pool_hit",
            )
        ),
        get_pool_metrics=AsyncMock(
            side_effect=[
                {
                    "pool_hits": 10,
                    "pool_misses": 2,
                    "exhaustion_events": 1,
                },
                {
                    "pool_hits": 12,
                    "pool_misses": 3,
                    "exhaustion_events": 2,
                },
            ]
        ),
    )
    runner.monitor = SimpleNamespace(
        start=AsyncMock(),
        stop=AsyncMock(return_value=SystemMetrics()),
        get_sandbox_summary=lambda: None,
    )

    result = await runner.run_scenario_at_concurrency(
        TinyScenario(), concurrency=2, num_requests=2
    )

    assert result.pool_hits == 2
    assert result.pool_misses == 1
    assert result.pool_hit_rate == pytest.approx(66.67, rel=0.01)
    assert result.pool_exhaustion_events == 1
    assert result.container_sources == {"pool_hit": 2}
    assert result.to_dict()["container_sources"] == {"pool_hit": 2}
    assert result.to_dict()["container_source_latency"]["pool_hit"] == {
        "count": 2,
        "avg_ms": 10,
        "p50_ms": 10,
        "p95_ms": 10,
    }
