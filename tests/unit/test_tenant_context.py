from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

from src.middleware.security import SecurityMiddleware
from src.models.api_key import ApiKeyRecord, KeyValidationResult
from unittest.mock import MagicMock
from src.api.exec import execute_code
from src.models.exec import ExecRequest, ExecResponse
from src.services.orchestrator import ExecutionOrchestrator
from src.services.tenant_quota import TenantQuotaLease


@pytest.mark.asyncio
async def test_tenant_comes_from_validated_key_not_request_header(monkeypatch):
    record = ApiKeyRecord(
        key_hash="hash-a",
        key_prefix="sk-a",
        name="tenant A key",
        created_at=datetime.now(timezone.utc),
        tenant_id="tenant-a",
    )
    service = SimpleNamespace(
        check_rate_limit=AsyncMock(return_value=True),
        validate_api_key_full=AsyncMock(
            return_value=KeyValidationResult(
                is_valid=True, key_hash="hash-a", key_record=record
            )
        ),
        record_usage=AsyncMock(),
    )

    async def fake_get_auth_service():
        return service

    monkeypatch.setattr(
        "src.middleware.security.get_auth_service", fake_get_auth_service
    )
    scope = {
        "type": "http",
        "method": "GET",
        "scheme": "http",
        "path": "/exec",
        "server": ("testserver", 80),
        "headers": [
            (b"x-api-key", b"valid-key"),
            (b"x-tenant-id", b"tenant-b"),
        ],
    }

    await SecurityMiddleware(lambda *_: None)._authenticate_request(
        Request(scope), scope
    )

    assert scope["state"]["tenant_id"] == "tenant-a"


@pytest.mark.asyncio
async def test_exec_passes_authenticated_tenant_to_orchestrator(monkeypatch):
    fake_execute = AsyncMock(return_value=ExecResponse(session_id="session-a"))
    monkeypatch.setattr(ExecutionOrchestrator, "execute", fake_execute)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/exec",
        "headers": [],
        "state": {
            "tenant_id": "tenant-a",
            "api_key_hash": "hash-a",
            "is_env_key": False,
        },
    }
    dependency = MagicMock()
    quota_service = SimpleNamespace(
        acquire=AsyncMock(
            return_value=TenantQuotaLease(
                tenant_id="tenant-a",
                lease_id="lease-a",
                acquired_at=0,
                reserved=False,
            )
        ),
        release=AsyncMock(),
    )

    await execute_code(
        ExecRequest(code="print(1)", lang="py"),
        Request(scope),
        dependency,
        dependency,
        dependency,
        dependency,
        dependency,
        quota_service,
    )

    assert fake_execute.await_args.kwargs["tenant_id"] == "tenant-a"
    quota_service.acquire.assert_awaited_once_with(
        tenant_id="tenant-a",
        requested_timeout_seconds=120,
    )
    quota_service.release.assert_awaited_once()
