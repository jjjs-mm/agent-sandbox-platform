from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.services import tenant_quota as quota_module
from src.services.tenant_quota import (
    TenantQuotaExceeded,
    TenantQuotaLease,
    TenantQuotaLimits,
    TenantQuotaService,
)
from src.models.errors import ServiceUnavailableError


def quota_settings(**overrides):
    values = {
        "tenant_quota_enabled": True,
        "tenant_max_concurrent_executions": 2,
        "tenant_daily_execution_limit": 10,
        "tenant_daily_execution_seconds": 60,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_acquire_reserves_aggregate_tenant_capacity(monkeypatch):
    redis = SimpleNamespace(eval=AsyncMock(return_value=[1, 2, 7, 1500]))
    monkeypatch.setattr(quota_module, "settings", quota_settings())
    service = TenantQuotaService(redis_client=redis)

    lease = await service.acquire("tenant-a", requested_timeout_seconds=20)

    assert lease.tenant_id == "tenant-a"
    assert lease.reserved is True
    args = redis.eval.await_args.args
    assert args[1] == 4
    # All keys use the same Redis Cluster hash tag for this tenant.
    hash_tags = {key.split("{")[1].split("}")[0] for key in args[2:6]}
    assert len(hash_tags) == 1
    assert args[-5:-2] == (2, 10, 60)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("redis_code", "quota_name"),
    [
        (-1, "concurrent_executions"),
        (-2, "daily_executions"),
        (-3, "daily_execution_seconds"),
    ],
)
async def test_acquire_rejects_each_quota(monkeypatch, redis_code, quota_name):
    quota_result = [redis_code, 2, 10, 60_000]
    redis = SimpleNamespace(eval=AsyncMock(return_value=quota_result))
    monkeypatch.setattr(quota_module, "settings", quota_settings())
    service = TenantQuotaService(redis_client=redis)

    with pytest.raises(TenantQuotaExceeded) as exc_info:
        await service.acquire("tenant-a", requested_timeout_seconds=20)

    assert exc_info.value.status_code == 429
    assert exc_info.value.details[0].field == quota_name


@pytest.mark.asyncio
async def test_release_removes_lease_and_charges_elapsed_time(monkeypatch):
    redis = SimpleNamespace(eval=AsyncMock(return_value=250))
    monkeypatch.setattr(quota_module, "settings", quota_settings())
    monkeypatch.setattr(quota_module.time, "monotonic", lambda: 10.25)
    service = TenantQuotaService(redis_client=redis)
    lease = TenantQuotaLease(
        tenant_id="tenant-a",
        lease_id="lease-a",
        acquired_at=10.0,
    )

    await service.release(lease)

    args = redis.eval.await_args.args
    assert args[1] == 2
    assert args[-3] == "lease-a"
    assert args[-2] == 250


@pytest.mark.asyncio
async def test_disabled_quota_does_not_touch_redis(monkeypatch):
    redis = SimpleNamespace(eval=AsyncMock())
    monkeypatch.setattr(
        quota_module,
        "settings",
        quota_settings(tenant_quota_enabled=False),
    )
    service = TenantQuotaService(redis_client=redis)

    lease = await service.acquire("tenant-a", requested_timeout_seconds=20)
    await service.release(lease)

    assert lease.reserved is False
    redis.eval.assert_not_awaited()


@pytest.mark.asyncio
async def test_admission_fails_closed_when_redis_is_unavailable(monkeypatch):
    redis_error = ConnectionError("offline")
    redis = SimpleNamespace(eval=AsyncMock(side_effect=redis_error))
    monkeypatch.setattr(quota_module, "settings", quota_settings())
    service = TenantQuotaService(redis_client=redis)

    with pytest.raises(ServiceUnavailableError) as exc_info:
        await service.acquire("tenant-a", requested_timeout_seconds=20)

    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_status_reports_usage_and_limits(monkeypatch):
    redis = SimpleNamespace(eval=AsyncMock(return_value=[2, 7, 12_345, 3, 25, 90, 1]))
    monkeypatch.setattr(quota_module, "settings", quota_settings())
    service = TenantQuotaService(redis_client=redis)

    status = await service.get_status("tenant-a")

    assert status["active_executions"] == 2
    assert status["daily_executions"] == 7
    assert status["daily_execution_seconds"] == 12.345
    assert status["limits"] == {
        "max_concurrent_executions": 3,
        "daily_execution_limit": 25,
        "daily_execution_seconds": 90,
    }
    assert status["customized"] is True


@pytest.mark.asyncio
async def test_set_limits_persists_override_and_returns_status(monkeypatch):
    expected_status = {"tenant_id": "tenant-a", "customized": True}
    redis = SimpleNamespace(hset=AsyncMock())
    service = TenantQuotaService(redis_client=redis)
    service.get_status = AsyncMock(return_value=expected_status)
    limits = TenantQuotaLimits(
        max_concurrent_executions=8,
        daily_execution_limit=200,
        daily_execution_seconds=600,
    )

    result = await service.set_limits("tenant-a", limits)

    redis.hset.assert_awaited_once()
    assert redis.hset.await_args.kwargs["mapping"] == limits.to_dict()
    service.get_status.assert_awaited_once_with("tenant-a")
    assert result == expected_status


@pytest.mark.asyncio
async def test_reset_limits_deletes_override_and_returns_defaults():
    expected_status = {"tenant_id": "tenant-a", "customized": False}
    redis = SimpleNamespace(delete=AsyncMock())
    service = TenantQuotaService(redis_client=redis)
    service.get_status = AsyncMock(return_value=expected_status)

    result = await service.reset_limits("tenant-a")

    redis.delete.assert_awaited_once()
    service.get_status.assert_awaited_once_with("tenant-a")
    assert result == expected_status
