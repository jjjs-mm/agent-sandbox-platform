from datetime import datetime, timezone
from types import SimpleNamespace
from src.models.api_key import ApiKeyRecord

from src.api.admin import (
    ApiKeyCreate,
    TenantQuotaUpdate,
    create_key,
    get_tenant_quota_status,
    reset_tenant_quota,
    update_tenant_quota,
)
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from src.services.api_key_manager import ApiKeyManagerService


def test_admin_can_specify_tenant_when_creating_key() -> None:
    data = ApiKeyCreate(name="research agent", tenant_id="tenant-a")

    assert data.tenant_id == "tenant-a"


def test_api_key_record_preserves_tenant_id() -> None:
    record = ApiKeyRecord(
        key_hash="abc",
        key_prefix="sk-test",
        name="test key",
        created_at=datetime.now(timezone.utc),
        tenant_id="tenant-a",
    )

    restored = ApiKeyRecord.from_redis_hash(record.to_redis_hash())

    assert restored.tenant_id == "tenant-a"


def test_old_api_key_record_uses_default_tenant() -> None:
    record = ApiKeyRecord(
        key_hash="old",
        key_prefix="sk-old",
        name="old key",
        created_at=datetime.now(timezone.utc),
    )
    old_data = record.to_redis_hash()
    old_data.pop("tenant_id")

    restored = ApiKeyRecord.from_redis_hash(old_data)

    assert restored.tenant_id == "default"


@pytest.mark.asyncio
async def test_create_key_stores_tenant_id() -> None:
    pipeline = MagicMock()
    pipeline.execute = AsyncMock()
    redis_client = MagicMock()
    redis_client.pipeline.return_value = pipeline

    manager = ApiKeyManagerService(redis_client)
    _, record = await manager.create_key(
        name="research agent",
        tenant_id="tenant-a",
    )

    assert record.tenant_id == "tenant-a"
    assert pipeline.hset.call_args.kwargs["mapping"]["tenant_id"] == "tenant-a"


@pytest.mark.asyncio
async def test_admin_create_key_passes_and_returns_tenant(monkeypatch) -> None:
    record = ApiKeyRecord(
        key_hash="hash-a",
        key_prefix="sk-a",
        name="research agent",
        created_at=datetime.now(timezone.utc),
        tenant_id="tenant-a",
    )
    manager = SimpleNamespace(create_key=AsyncMock(return_value=("sk-secret", record)))

    async def fake_get_manager():
        return manager

    monkeypatch.setattr("src.api.admin.get_api_key_manager", fake_get_manager)

    result = await create_key(
        ApiKeyCreate(name="research agent", tenant_id="tenant-a"),
        _="master-key",
    )

    manager.create_key.assert_awaited_once_with(
        name="research agent",
        rate_limits=None,
        metadata=None,
        tenant_id="tenant-a",
    )
    assert result["record"].tenant_id == "tenant-a"


@pytest.mark.asyncio
async def test_admin_can_inspect_tenant_quota(monkeypatch) -> None:
    status = {
        "tenant_id": "tenant-a",
        "enabled": True,
        "active_executions": 2,
        "daily_executions": 7,
        "daily_execution_seconds": 12.345,
        "limits": {
            "max_concurrent_executions": 4,
            "daily_execution_limit": 1000,
            "daily_execution_seconds": 3600,
        },
        "customized": False,
        "resets_at": "2026-10-27T00:00:00+00:00",
    }
    service = SimpleNamespace(get_status=AsyncMock(return_value=status))
    monkeypatch.setattr(
        "src.api.admin.get_tenant_quota_service",
        lambda: service,
    )

    result = await get_tenant_quota_status("tenant-a", _="master-key")

    service.get_status.assert_awaited_once_with("tenant-a")
    assert result == status


@pytest.mark.asyncio
async def test_admin_rejects_invalid_quota_tenant_id() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await get_tenant_quota_status("../tenant-a", _="master-key")

    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_admin_can_update_tenant_quota(monkeypatch) -> None:
    status = {"tenant_id": "tenant-a", "customized": True}
    service = SimpleNamespace(set_limits=AsyncMock(return_value=status))
    monkeypatch.setattr("src.api.admin.get_tenant_quota_service", lambda: service)
    update = TenantQuotaUpdate(
        max_concurrent_executions=8,
        daily_execution_limit=500,
        daily_execution_seconds=1800,
    )

    result = await update_tenant_quota("tenant-a", update, _="master-key")

    limits = service.set_limits.await_args.args[1]
    assert limits.max_concurrent_executions == 8
    assert limits.daily_execution_limit == 500
    assert limits.daily_execution_seconds == 1800
    assert result == status


@pytest.mark.asyncio
async def test_admin_can_reset_tenant_quota(monkeypatch) -> None:
    status = {"tenant_id": "tenant-a", "customized": False}
    service = SimpleNamespace(reset_limits=AsyncMock(return_value=status))
    monkeypatch.setattr("src.api.admin.get_tenant_quota_service", lambda: service)

    result = await reset_tenant_quota("tenant-a", _="master-key")

    service.reset_limits.assert_awaited_once_with("tenant-a")
    assert result == status
