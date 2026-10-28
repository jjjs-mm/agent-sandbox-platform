"""Runtime-faithful Redis test for atomic tenant quota admission."""

import asyncio
import os
import uuid
from datetime import datetime, timezone

import pytest
import redis.asyncio as redis

from src.config import settings
from src.services.tenant_quota import (
    TenantQuotaExceeded,
    TenantQuotaLimits,
    TenantQuotaService,
)

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_REDIS_TESTS") != "1",
    reason="Set RUN_REDIS_TESTS=1 to run tests against local Redis",
)


@pytest.mark.asyncio
async def test_only_one_competing_request_gets_the_last_slot(monkeypatch):
    client = redis.from_url(settings.get_redis_url(), decode_responses=True)
    service = TenantQuotaService(redis_client=client)
    tenant_id = f"quota-race-{uuid.uuid4().hex}"
    monkeypatch.setattr(settings, "tenant_quota_enabled", True)
    monkeypatch.setattr(settings, "tenant_max_concurrent_executions", 1)
    monkeypatch.setattr(settings, "tenant_daily_execution_limit", 100)
    monkeypatch.setattr(settings, "tenant_daily_execution_seconds", 3600)

    async def compete():
        try:
            return await service.acquire(
                tenant_id,
                requested_timeout_seconds=5,
            )
        except TenantQuotaExceeded:
            return None

    leases = await asyncio.gather(*(compete() for _ in range(10)))
    winners = [lease for lease in leases if lease is not None]

    try:
        assert len(winners) == 1
        active_status = await service.get_status(tenant_id)
        assert active_status["active_executions"] == 1
        assert active_status["daily_executions"] == 1
    finally:
        for lease in winners:
            await service.release(lease)
        released_status = await service.get_status(tenant_id)
        assert released_status["active_executions"] == 0
        keys = service._keys(tenant_id, datetime.now(timezone.utc))
        await client.delete(*keys)
        await client.aclose()


@pytest.mark.asyncio
async def test_tenant_override_takes_precedence_over_defaults(monkeypatch):
    client = redis.from_url(settings.get_redis_url(), decode_responses=True)
    service = TenantQuotaService(redis_client=client)
    tenant_id = f"quota-override-{uuid.uuid4().hex}"
    monkeypatch.setattr(settings, "tenant_quota_enabled", True)
    monkeypatch.setattr(settings, "tenant_max_concurrent_executions", 4)
    monkeypatch.setattr(settings, "tenant_daily_execution_limit", 100)
    monkeypatch.setattr(settings, "tenant_daily_execution_seconds", 3600)

    first_lease = None
    try:
        status = await service.set_limits(
            tenant_id,
            TenantQuotaLimits(
                max_concurrent_executions=1,
                daily_execution_limit=5,
                daily_execution_seconds=30,
            ),
        )
        assert status["customized"] is True
        assert status["limits"]["max_concurrent_executions"] == 1

        first_lease = await service.acquire(tenant_id, requested_timeout_seconds=5)
        with pytest.raises(TenantQuotaExceeded) as exc_info:
            await service.acquire(tenant_id, requested_timeout_seconds=5)
        assert exc_info.value.details[0].field == "concurrent_executions"

        reset_status = await service.reset_limits(tenant_id)
        assert reset_status["customized"] is False
        assert reset_status["limits"]["max_concurrent_executions"] == 4
    finally:
        if first_lease is not None:
            await service.release(first_lease)
        keys = service._keys(tenant_id, datetime.now(timezone.utc))
        await client.delete(*keys)
        await client.aclose()
