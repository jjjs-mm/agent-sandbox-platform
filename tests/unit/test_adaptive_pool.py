"""Unit tests for load-aware sandbox-pool replenishment."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.services.sandbox.pool import SandboxPool


@pytest.fixture
def pool() -> SandboxPool:
    manager = SimpleNamespace(destroy_sandbox=Mock())
    return SandboxPool(manager)


def configure_adaptive(monkeypatch, *, maximum: int = 4) -> None:
    from src.services.sandbox import pool as pool_module

    monkeypatch.setattr(pool_module.settings, "sandbox_pool_py", 2)
    monkeypatch.setattr(pool_module.settings, "sandbox_pool_adaptive_enabled", True)
    monkeypatch.setattr(pool_module.settings, "sandbox_pool_adaptive_max_size", maximum)
    monkeypatch.setattr(
        pool_module.settings,
        "sandbox_pool_adaptive_miss_window_seconds",
        30,
    )
    monkeypatch.setattr(pool_module.settings, "sandbox_pool_replenish_max_active", 1)


def test_recent_misses_raise_target_up_to_cap(monkeypatch, pool: SandboxPool) -> None:
    configure_adaptive(monkeypatch, maximum=4)

    assert pool._target_size("py") == 2

    pool._record_recent_miss("py")
    assert pool._target_size("py") == 3

    pool._record_recent_miss("py")
    pool._record_recent_miss("py")
    assert pool._target_size("py") == 4


def test_replenishment_is_deferred_above_active_threshold(
    monkeypatch, pool: SandboxPool
) -> None:
    configure_adaptive(monkeypatch)

    pool._in_use.update({"sandbox-1", "sandbox-2"})
    assert pool._should_defer_replenishment() is True

    pool._in_use.remove("sandbox-2")
    assert pool._should_defer_replenishment() is False

    pool._pending_acquisitions = 2
    assert pool._should_defer_replenishment() is True


@pytest.mark.asyncio
async def test_destroy_releases_active_capacity(monkeypatch, pool: SandboxPool) -> None:
    configure_adaptive(monkeypatch)
    sandbox = SimpleNamespace(sandbox_id="sandbox-1")
    pool._in_use.add(sandbox.sandbox_id)

    await pool.destroy_sandbox(sandbox)

    assert sandbox.sandbox_id not in pool._in_use
    pool._sandbox_manager.destroy_sandbox.assert_called_once_with(sandbox)


def test_runtime_state_exposes_adaptive_decisions(
    monkeypatch, pool: SandboxPool
) -> None:
    configure_adaptive(monkeypatch)
    pool._warmup_languages.add("py")
    pool._record_recent_miss("py")
    pool._in_use.add("sandbox-1")
    pool._replenishment_deferred = True

    state = pool.get_runtime_state()

    assert state == {
        "adaptive_enabled": True,
        "in_use": 1,
        "pending_acquisitions": 0,
        "replenishment_deferred": True,
        "target_sizes": {"py": 3},
    }


@pytest.mark.asyncio
async def test_prune_unhealthy_sandboxes_keeps_only_live_repls(
    pool: SandboxPool,
) -> None:
    live = SimpleNamespace(
        repl_enabled=True,
        repl_process=SimpleNamespace(process=SimpleNamespace(returncode=None)),
    )
    dead = SimpleNamespace(
        repl_enabled=True,
        repl_process=SimpleNamespace(process=SimpleNamespace(returncode=1)),
    )
    queue = asyncio.Queue()
    await queue.put(live)
    await queue.put(dead)
    pool._destroy_pooled_sandbox = AsyncMock()

    await pool._prune_unhealthy_sandboxes(queue)

    assert queue.qsize() == 1
    assert await queue.get() is live
    pool._destroy_pooled_sandbox.assert_awaited_once_with(dead)


@pytest.mark.asyncio
async def test_shrink_queue_destroys_excess_idle_capacity(
    pool: SandboxPool,
) -> None:
    queue = asyncio.Queue()
    sandboxes = [SimpleNamespace(name=f"sandbox-{i}") for i in range(3)]
    for sandbox in sandboxes:
        await queue.put(sandbox)
    pool._destroy_pooled_sandbox = AsyncMock()

    await pool._shrink_queue(queue, 2)

    assert queue.qsize() == 1
    assert pool._destroy_pooled_sandbox.await_count == 2
