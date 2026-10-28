"""Tenant isolation at file upload, read and execution boundaries."""

from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from starlette.datastructures import UploadFile

from src.api.files import (
    download_file,
    get_session_object_metadata,
    list_files,
    upload_file,
    upload_files_batch,
)
from src.api import programmatic
from src.models.errors import CodeInterpreterException
from src.models.exec import ExecRequest, RequestFile
from src.models.programmatic import ProgrammaticExecRequest, ProgrammaticExecResponse
from src.services.orchestrator import ExecutionContext, ExecutionOrchestrator


def _upload() -> UploadFile:
    return UploadFile(file=BytesIO(b"content"), filename="data.txt")


def _services():
    files = MagicMock()
    files.validate_uploads.return_value = None
    files.store_uploaded_file = AsyncMock(return_value="file-1")
    files.get_file_info = AsyncMock()
    files.list_files = AsyncMock(return_value=[])
    files.get_file_content = AsyncMock()
    files.link_file_into_session = AsyncMock()
    sessions = MagicMock()
    sessions.create_session = AsyncMock(
        return_value=SimpleNamespace(session_id="upload-session")
    )
    sessions.validate_session_access = AsyncMock(return_value=False)
    return files, sessions


@pytest.mark.asyncio
async def test_single_upload_records_authenticated_tenant():
    files, sessions = _services()
    request = SimpleNamespace(state=SimpleNamespace(tenant_id="tenant-a"))

    await upload_file(
        request=request,
        file=_upload(),
        files=None,
        entity_id="agent-1",
        file_service=files,
        session_service=sessions,
    )

    metadata = sessions.create_session.await_args.args[0].metadata
    assert metadata == {"tenant_id": "tenant-a", "entity_id": "agent-1"}


@pytest.mark.asyncio
async def test_batch_upload_records_authenticated_tenant():
    files, sessions = _services()
    form = MagicMock()
    form.multi_items.return_value = [("file", _upload())]
    form.get.return_value = None
    request = SimpleNamespace(
        state=SimpleNamespace(tenant_id="tenant-b"),
        form=AsyncMock(return_value=form),
    )

    await upload_files_batch(
        request=request, file_service=files, session_service=sessions
    )

    metadata = sessions.create_session.await_args.args[0].metadata
    assert metadata == {"tenant_id": "tenant-b"}


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["list", "metadata", "download"])
async def test_file_reads_reject_foreign_tenant_before_storage(endpoint):
    files, sessions = _services()
    sessions.get_session = AsyncMock(return_value=SimpleNamespace(session_id="source"))
    request = SimpleNamespace(state=SimpleNamespace(tenant_id="tenant-b"))

    if endpoint == "list":
        result = await list_files(
            session_id="source",
            request=request,
            detail=None,
            kind=None,
            id=None,
            version=None,
            file_service=files,
            session_service=sessions,
        )
        assert result == []
    else:
        with pytest.raises(HTTPException) as error:
            if endpoint == "metadata":
                await get_session_object_metadata(
                    session_id="source",
                    file_id="file-1",
                    request=request,
                    file_service=files,
                    session_service=sessions,
                )
            else:
                await download_file(
                    session_id="source",
                    file_id="file-1",
                    request=request,
                    file_service=files,
                    session_service=sessions,
                )
        assert error.value.status_code == 404
    sessions.validate_session_access.assert_awaited_once_with(
        "source", tenant_id="tenant-b"
    )
    files.list_files.assert_not_awaited()
    files.get_file_info.assert_not_awaited()
    files.get_file_content.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_mount_rejects_foreign_tenant_before_file_lookup():
    files, sessions = _services()
    orchestrator = ExecutionOrchestrator(
        session_service=sessions,
        file_service=files,
        execution_service=MagicMock(),
    )
    context = ExecutionContext(
        request=ExecRequest(
            code="print(1)",
            lang="py",
            files=[RequestFile(id="file-1", session_id="source", name="data.txt")],
        ),
        request_id="request-1",
        session_id="target",
        tenant_id="tenant-b",
    )

    with pytest.raises(CodeInterpreterException) as error:
        await orchestrator._mount_explicit_files(context)

    assert error.value.status_code == 403
    sessions.validate_session_access.assert_awaited_once_with(
        "source", tenant_id="tenant-b"
    )
    files.get_file_info.assert_not_awaited()
    files.link_file_into_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_programmatic_file_reference_rejects_foreign_tenant(monkeypatch):
    _, sessions = _services()
    service = MagicMock()
    service.start_execution = AsyncMock()
    monkeypatch.setattr(programmatic, "_get_ptc_service", lambda: service)
    request = SimpleNamespace(state=SimpleNamespace(tenant_id="tenant-b"))

    with pytest.raises(HTTPException) as error:
        await programmatic.execute_programmatic(
            request=ProgrammaticExecRequest(
                code="print(1)",
                files=[{"session_id": "foreign", "id": "file-1", "name": "data.txt"}],
            ),
            http_request=request,
            session_service=sessions,
        )

    assert error.value.status_code == 404
    sessions.validate_session_access.assert_awaited_once_with(
        "foreign", tenant_id="tenant-b"
    )
    service.start_execution.assert_not_awaited()


@pytest.mark.asyncio
async def test_programmatic_new_session_records_tenant(monkeypatch):
    _, sessions = _services()
    service = MagicMock()
    service.start_execution = AsyncMock(
        return_value=ProgrammaticExecResponse(status="completed")
    )
    monkeypatch.setattr(programmatic, "_get_ptc_service", lambda: service)

    await programmatic.execute_programmatic(
        request=ProgrammaticExecRequest(code="print(1)"),
        http_request=SimpleNamespace(state=SimpleNamespace(tenant_id="tenant-a")),
        session_service=sessions,
    )

    metadata = sessions.create_session.await_args.args[0].metadata
    assert metadata == {"tenant_id": "tenant-a"}
    assert service.start_execution.await_args.kwargs["tenant_id"] == "tenant-a"


@pytest.mark.asyncio
async def test_programmatic_continuation_rejects_foreign_tenant(monkeypatch):
    _, sessions = _services()
    service = MagicMock()
    service.get_paused_session_id.return_value = "foreign"
    service.continue_execution = AsyncMock()
    monkeypatch.setattr(programmatic, "_get_ptc_service", lambda: service)

    with pytest.raises(HTTPException) as error:
        await programmatic.execute_programmatic(
            request=ProgrammaticExecRequest(continuation_token="token-1"),
            http_request=SimpleNamespace(state=SimpleNamespace(tenant_id="tenant-b")),
            session_service=sessions,
        )

    assert error.value.status_code == 404
    service.continue_execution.assert_not_awaited()
