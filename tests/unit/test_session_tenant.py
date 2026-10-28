from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.models.session import Session
from src.services.session import SessionService
from src.models.errors import CodeInterpreterException
from src.models.exec import ExecRequest, RequestFile
from src.services.orchestrator import ExecutionContext, ExecutionOrchestrator

@pytest.mark.asyncio
async def test_session_rejects_different_tenant() -> None:
    session = Session(
        session_id="session-a",
        expires_at=datetime.now(timezone.utc),
        metadata={"tenant_id": "tenant-a"},
    )
    service = SessionService(redis_client=MagicMock())
    service.get_session = AsyncMock(return_value=session)

    allowed = await service.validate_session_access(
        "session-a", tenant_id="tenant-b"
    )

    assert allowed is False

@pytest.mark.asyncio
async def test_exec_rejects_foreign_session_id() -> None:
    foreign_session = Session(
        session_id="session-a",
        expires_at=datetime.now(timezone.utc),
        metadata={"tenant_id": "tenant-a"},
    )
    session_service = MagicMock()
    session_service.get_session = AsyncMock(return_value=foreign_session)
    session_service.validate_session_access = AsyncMock(return_value=False)
    orchestrator = ExecutionOrchestrator(
        session_service=session_service,
        file_service=MagicMock(),
        execution_service=MagicMock(),
        state_service=MagicMock(),
    )
    context = ExecutionContext(
        request=ExecRequest(
            code="print(1)", lang="py", session_id="session-a"
        ),
        request_id="request-b",
        tenant_id="tenant-b",
    )

    with pytest.raises(CodeInterpreterException) as error:
        await orchestrator._get_or_create_session(context)

    assert error.value.status_code == 403

@pytest.mark.asyncio
async def test_new_session_records_authenticated_tenant() -> None:
    session_service = MagicMock()
    session_service.create_session = AsyncMock(
        return_value=Session(
            session_id="new-session",
            expires_at=datetime.now(timezone.utc),
        )
    )
    orchestrator = ExecutionOrchestrator(
        session_service=session_service,
        file_service=MagicMock(),
        execution_service=MagicMock(),
        state_service=MagicMock(),
    )
    context = ExecutionContext(
        request=ExecRequest(code="print(1)", lang="py"),
        request_id="request-a",
        tenant_id="tenant-a",
    )

    await orchestrator._get_or_create_session(context)

    created_request = session_service.create_session.await_args.args[0]
    assert created_request.metadata["tenant_id"] == "tenant-a"

@pytest.mark.asyncio
async def test_entity_id_does_not_reuse_foreign_tenant_session() -> None:
    foreign_session = Session(
        session_id="foreign-session",
        expires_at=datetime.now(timezone.utc),
        metadata={"tenant_id": "tenant-a", "entity_id": "shared-agent"},
    )
    session_service = MagicMock()
    session_service.list_sessions_by_entity = AsyncMock(
        return_value=[foreign_session]
    )
    session_service.validate_session_access = AsyncMock(return_value=False)
    session_service.create_session = AsyncMock(
        return_value=Session(
            session_id="new-session",
            expires_at=datetime.now(timezone.utc),
        )
    )
    orchestrator = ExecutionOrchestrator(
        session_service=session_service,
        file_service=MagicMock(),
        execution_service=MagicMock(),
        state_service=MagicMock(),
    )
    context = ExecutionContext(
        request=ExecRequest(
            code="print(1)", lang="py", entity_id="shared-agent"
        ),
        request_id="request-b",
        tenant_id="tenant-b",
    )

    session_id = await orchestrator._get_or_create_session(context)

    assert session_id == "new-session"


@pytest.mark.asyncio
async def test_file_reference_does_not_reuse_foreign_tenant_session() -> None:
    foreign_session = Session(
        session_id="foreign-session",
        expires_at=datetime.now(timezone.utc),
        metadata={"tenant_id": "tenant-a", "user_id": "same-user"},
    )
    session_service = MagicMock()
    session_service.get_session = AsyncMock(return_value=foreign_session)
    session_service.validate_session_access = AsyncMock(return_value=False)
    session_service.create_session = AsyncMock(
        return_value=Session(
            session_id="new-session",
            expires_at=datetime.now(timezone.utc),
        )
    )
    orchestrator = ExecutionOrchestrator(
        session_service=session_service,
        file_service=MagicMock(),
        execution_service=MagicMock(),
        state_service=MagicMock(),
    )
    context = ExecutionContext(
        request=ExecRequest(
            code="print(1)",
            lang="py",
            user_id="same-user",
            files=[
                RequestFile(
                    id="file-a",
                    session_id="foreign-session",
                    name="data.txt",
                )
            ],
        ),
        request_id="request-b",
        tenant_id="tenant-b",
    )

    session_id = await orchestrator._get_or_create_session(context)

    assert session_id == "new-session"
    session_service.validate_session_access.assert_awaited_once_with(
        "foreign-session", tenant_id="tenant-b"
    )


@pytest.mark.asyncio
async def test_execute_preserves_tenant_and_authorization_error() -> None:
    orchestrator = ExecutionOrchestrator(
        session_service=MagicMock(),
        file_service=MagicMock(),
        execution_service=MagicMock(),
        state_service=MagicMock(),
    )
    observed_tenants = []

    async def reject_session(context: ExecutionContext) -> str:
        observed_tenants.append(context.tenant_id)
        raise CodeInterpreterException(message="Session access denied", status_code=403)

    orchestrator._get_or_create_session = reject_session

    with pytest.raises(CodeInterpreterException) as error:
        await orchestrator.execute(
            ExecRequest(code="print(1)", lang="py"), tenant_id="tenant-b"
        )

    assert observed_tenants == ["tenant-b"]
    assert error.value.status_code == 403
