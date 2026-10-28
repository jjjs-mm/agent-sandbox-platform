"""Programmatic Tool Calling (PTC) API endpoint.

Provides POST /exec/programmatic for executing code that can call
externally-defined tools during execution.
"""

import math
from typing import Optional

import structlog
from fastapi import APIRouter, HTTPException, Request

from ..models.programmatic import (
    ProgrammaticExecRequest,
    ProgrammaticExecResponse,
)
from ..services.programmatic import ProgrammaticService
from ..dependencies.services import (
    SessionServiceDep,
    get_file_service,
    get_tenant_quota_service,
)
from ..models import SessionCreate
from ..utils.id_generator import generate_request_id

logger = structlog.get_logger(__name__)
router = APIRouter()

# Module-level service instance (initialized on first use)
_ptc_service: Optional[ProgrammaticService] = None


def _get_ptc_service() -> ProgrammaticService:
    """Get or create the PTC service singleton."""
    global _ptc_service
    if _ptc_service is None:
        _ptc_service = ProgrammaticService(
            file_service=get_file_service(),
            tenant_quota_service=get_tenant_quota_service(),
        )
    return _ptc_service


def _timeout_ms_to_seconds(timeout_ms: Optional[int]) -> Optional[int]:
    """Convert the public millisecond timeout contract to internal seconds."""
    if timeout_ms is None:
        return None
    return math.ceil(timeout_ms / 1000)


@router.post("/exec/programmatic", response_model=ProgrammaticExecResponse)
async def execute_programmatic(
    request: ProgrammaticExecRequest,
    http_request: Request,
    session_service: SessionServiceDep,
) -> ProgrammaticExecResponse:
    """Execute code with programmatic tool calling support.

    Supports two modes:
    1. Initial execution: provide code + tools
    2. Continuation: provide continuation_token + tool_results

    Args:
        request: PTC execution request
        session_service: Session service for session management

    Returns:
        ProgrammaticExecResponse with status and optional tool_calls
    """
    request_id = generate_request_id()[:8]
    ptc_service = _get_ptc_service()
    tenant_id = getattr(http_request.state, "tenant_id", "default")

    # Continuation mode
    if request.continuation_token:
        paused_session_id = ptc_service.get_paused_session_id(
            request.continuation_token
        )
        if paused_session_id and not await session_service.validate_session_access(
            paused_session_id, tenant_id=tenant_id
        ):
            raise HTTPException(status_code=404, detail="Session not found")
        logger.info(
            "PTC continuation request",
            request_id=request_id,
            continuation_token=request.continuation_token[:12],
            tool_results_count=len(request.tool_results),
        )

        response = await ptc_service.continue_execution(
            continuation_token=request.continuation_token,
            tool_results=request.tool_results,
        )

        logger.info(
            "PTC continuation completed",
            request_id=request_id,
            status=response.status,
        )

        return response

    # Initial execution mode
    if not request.code:
        return ProgrammaticExecResponse(
            status="error",
            error="Either 'code' or 'continuation_token' must be provided",
        )

    # Get or create session
    session_id = request.session_id
    if session_id and not await session_service.validate_session_access(
        session_id, tenant_id=tenant_id
    ):
        raise HTTPException(status_code=404, detail="Session not found")
    for file_ref in request.files:
        if not await session_service.validate_session_access(
            file_ref.session_id, tenant_id=tenant_id
        ):
            raise HTTPException(status_code=404, detail="File not found")
    if not session_id:
        metadata = {"tenant_id": tenant_id}
        if request.entity_id:
            metadata["entity_id"] = request.entity_id
        if request.user_id:
            metadata["user_id"] = request.user_id

        session = await session_service.create_session(SessionCreate(metadata=metadata))
        session_id = session.session_id

    logger.info(
        "PTC execution request",
        request_id=request_id,
        session_id=session_id[:12],
        lang=request.lang,
        code_length=len(request.code),
        tools_count=len(request.tools),
    )

    response = await ptc_service.start_execution(
        code=request.code,
        tools=request.tools,
        session_id=session_id,
        timeout=_timeout_ms_to_seconds(request.timeout),
        files=request.files,
        lang=request.lang,
        tenant_id=tenant_id,
    )

    # Ensure session_id is set in response
    if not response.session_id:
        response.session_id = session_id

    logger.info(
        "PTC execution completed",
        request_id=request_id,
        session_id=session_id[:12],
        status=response.status,
    )

    return response
