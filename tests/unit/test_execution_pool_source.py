"""Tests for accurate sandbox capacity-source reporting."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.services.execution.runner import CodeExecutionRunner


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["pool_hit", "pool_miss"])
async def test_get_sandbox_preserves_pool_source(monkeypatch, source: str) -> None:
    from src.services.execution import runner as runner_module

    monkeypatch.setattr(runner_module.settings, "sandbox_pool_enabled", True)
    sandbox = SimpleNamespace(labels={"pool_source": source})
    pool = SimpleNamespace(acquire=AsyncMock(return_value=sandbox))
    execution_runner = CodeExecutionRunner(
        sandbox_manager=SimpleNamespace(),
        sandbox_pool=pool,
    )

    acquired, actual_source = await execution_runner._get_sandbox("session-1", "py")

    assert acquired is sandbox
    assert actual_source == source
