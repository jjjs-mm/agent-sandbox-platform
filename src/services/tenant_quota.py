"""Redis-backed aggregate execution quotas for tenants.

API-key rate limits protect a single credential. Tenant quotas protect shared
infrastructure even when one tenant owns several credentials. Acquisition is
performed by one Lua script so concurrent API processes cannot all observe an
available final slot and over-admit work.
"""

import hashlib
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import redis.asyncio as redis
import structlog

from ..config import settings
from ..core.pool import redis_pool
from ..models.errors import (
    CodeInterpreterException,
    ErrorDetail,
    ErrorType,
    ServiceUnavailableError,
)
from ..utils.id_generator import generate_request_id

logger = structlog.get_logger(__name__)


class TenantQuotaExceeded(CodeInterpreterException):
    """Raised when admitting work would exceed a tenant quota."""

    def __init__(self, quota: str, message: str):
        super().__init__(
            message=message,
            error_type=ErrorType.RATE_LIMITED,
            status_code=429,
            details=[
                ErrorDetail(
                    field=quota,
                    message=message,
                    code="tenant_quota_exceeded",
                )
            ],
        )


@dataclass(frozen=True)
class TenantQuotaLease:
    """A concurrency slot reserved for one execution."""

    tenant_id: str
    lease_id: str
    acquired_at: float
    reserved: bool = True


@dataclass(frozen=True)
class TenantQuotaLimits:
    """Effective execution limits for one tenant."""

    max_concurrent_executions: int
    daily_execution_limit: int
    daily_execution_seconds: int

    def to_dict(self) -> dict[str, int]:
        return {
            "max_concurrent_executions": self.max_concurrent_executions,
            "daily_execution_limit": self.daily_execution_limit,
            "daily_execution_seconds": self.daily_execution_seconds,
        }


class TenantQuotaService:
    """Enforce aggregate per-tenant quotas with Redis atomic operations."""

    PREFIX = "tenant_quota"

    _ACQUIRE_SCRIPT = """
local active_key = KEYS[1]
local calls_key = KEYS[2]
local runtime_key = KEYS[3]
local config_key = KEYS[4]
local now_ms = tonumber(ARGV[1])
local lease_expiry_ms = tonumber(ARGV[2])
local lease_id = ARGV[3]
local max_concurrent = tonumber(redis.call('HGET', config_key, 'max_concurrent_executions') or ARGV[4])
local max_calls = tonumber(redis.call('HGET', config_key, 'daily_execution_limit') or ARGV[5])
local max_runtime_seconds = tonumber(redis.call('HGET', config_key, 'daily_execution_seconds') or ARGV[6])
local max_runtime_ms = max_runtime_seconds * 1000
local day_expiry = tonumber(ARGV[7])
local active_ttl = tonumber(ARGV[8])

redis.call('ZREMRANGEBYSCORE', active_key, '-inf', now_ms)
local active = redis.call('ZCARD', active_key)
local calls = tonumber(redis.call('GET', calls_key) or '0')
local runtime_ms = tonumber(redis.call('GET', runtime_key) or '0')

if active >= max_concurrent then
    return {-1, active, calls, runtime_ms}
end
if calls >= max_calls then
    return {-2, active, calls, runtime_ms}
end
if runtime_ms >= max_runtime_ms then
    return {-3, active, calls, runtime_ms}
end

redis.call('ZADD', active_key, lease_expiry_ms, lease_id)
redis.call('EXPIRE', active_key, active_ttl)
local new_calls = redis.call('INCR', calls_key)
redis.call('EXPIREAT', calls_key, day_expiry)
if runtime_ms == 0 then
    redis.call('SET', runtime_key, 0)
end
redis.call('EXPIREAT', runtime_key, day_expiry)
return {1, active + 1, new_calls, runtime_ms}
"""

    _RELEASE_SCRIPT = """
redis.call('ZREM', KEYS[1], ARGV[1])
local runtime_ms = redis.call('INCRBY', KEYS[2], tonumber(ARGV[2]))
redis.call('EXPIREAT', KEYS[2], tonumber(ARGV[3]))
return runtime_ms
"""

    _STATUS_SCRIPT = """
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', tonumber(ARGV[1]))
local active = redis.call('ZCARD', KEYS[1])
local calls = tonumber(redis.call('GET', KEYS[2]) or '0')
local runtime_ms = tonumber(redis.call('GET', KEYS[3]) or '0')
local max_concurrent = tonumber(redis.call('HGET', KEYS[4], 'max_concurrent_executions') or ARGV[2])
local max_calls = tonumber(redis.call('HGET', KEYS[4], 'daily_execution_limit') or ARGV[3])
local max_runtime_seconds = tonumber(redis.call('HGET', KEYS[4], 'daily_execution_seconds') or ARGV[4])
local customized = redis.call('EXISTS', KEYS[4])
return {active, calls, runtime_ms, max_concurrent, max_calls, max_runtime_seconds, customized}
"""

    def __init__(self, redis_client: Optional[redis.Redis] = None):
        self._redis = redis_client

    @property
    def redis(self) -> redis.Redis:
        if self._redis is None:
            self._redis = redis_pool.get_client()
        return self._redis

    @staticmethod
    def _tenant_token(tenant_id: str) -> str:
        # Hashing keeps user-controlled tenant IDs out of Redis key names. The
        # braces place all of one tenant's keys in the same Redis Cluster slot.
        return hashlib.sha256(tenant_id.encode("utf-8")).hexdigest()[:24]

    def _keys(self, tenant_id: str, now: datetime) -> tuple[str, str, str, str]:
        token = self._tenant_token(tenant_id)
        day = now.strftime("%Y%m%d")
        tag = f"{{{token}}}"
        return (
            f"{self.PREFIX}:{tag}:active",
            f"{self.PREFIX}:{tag}:calls:{day}",
            f"{self.PREFIX}:{tag}:runtime_ms:{day}",
            f"{self.PREFIX}:{tag}:config",
        )

    @staticmethod
    def default_limits() -> TenantQuotaLimits:
        return TenantQuotaLimits(
            max_concurrent_executions=settings.tenant_max_concurrent_executions,
            daily_execution_limit=settings.tenant_daily_execution_limit,
            daily_execution_seconds=settings.tenant_daily_execution_seconds,
        )

    @staticmethod
    def _next_day_epoch(now: datetime) -> int:
        tomorrow = (now + timedelta(days=1)).date()
        reset = datetime.combine(
            tomorrow,
            datetime.min.time(),
            tzinfo=timezone.utc,
        )
        # Retain a small grace period so counters remain inspectable at reset.
        return int(reset.timestamp()) + 300

    async def acquire(
        self,
        tenant_id: str,
        requested_timeout_seconds: int,
    ) -> TenantQuotaLease:
        """Atomically reserve concurrency and daily-call capacity."""
        now_monotonic = time.monotonic()
        lease_id = generate_request_id()
        if not settings.tenant_quota_enabled:
            return TenantQuotaLease(
                tenant_id=tenant_id,
                lease_id=lease_id,
                acquired_at=now_monotonic,
                reserved=False,
            )

        now = datetime.now(timezone.utc)
        now_ms = int(now.timestamp() * 1000)
        # The grace period covers cleanup and response serialization after the
        # configured execution deadline.
        lease_ttl = max(1, requested_timeout_seconds) + 30
        try:
            result = await self.redis.eval(
                self._ACQUIRE_SCRIPT,
                4,
                *self._keys(tenant_id, now),
                now_ms,
                now_ms + lease_ttl * 1000,
                lease_id,
                settings.tenant_max_concurrent_executions,
                settings.tenant_daily_execution_limit,
                settings.tenant_daily_execution_seconds,
                self._next_day_epoch(now),
                lease_ttl * 2,
            )
        except Exception as exc:
            logger.exception(
                "Tenant quota admission failed",
                tenant_id=tenant_id,
            )
            raise ServiceUnavailableError(
                service="Tenant quota",
                message="Tenant quota service is unavailable",
            ) from exc
        code = int(result[0])
        if code == -1:
            raise TenantQuotaExceeded(
                "concurrent_executions",
                "Tenant concurrent execution limit reached",
            )
        if code == -2:
            raise TenantQuotaExceeded(
                "daily_executions",
                "Tenant daily execution limit reached",
            )
        if code == -3:
            raise TenantQuotaExceeded(
                "daily_execution_seconds",
                "Tenant daily execution-time budget reached",
            )

        logger.info(
            "Tenant quota lease acquired",
            tenant_id=tenant_id,
            active_executions=int(result[1]),
            daily_executions=int(result[2]),
        )
        return TenantQuotaLease(
            tenant_id=tenant_id,
            lease_id=lease_id,
            acquired_at=now_monotonic,
        )

    async def release(self, lease: TenantQuotaLease) -> None:
        """Release concurrency capacity and charge measured execution time."""
        if not lease.reserved:
            return
        now = datetime.now(timezone.utc)
        active_key, _, runtime_key, _ = self._keys(lease.tenant_id, now)
        elapsed_ms = max(0, int((time.monotonic() - lease.acquired_at) * 1000))
        try:
            await self.redis.eval(
                self._RELEASE_SCRIPT,
                2,
                active_key,
                runtime_key,
                lease.lease_id,
                elapsed_ms,
                self._next_day_epoch(now),
            )
        except Exception:
            # The lease expires by score even if Redis briefly fails here. Do
            # not replace the user's execution result with accounting failure.
            logger.exception(
                "Failed to release tenant quota lease",
                tenant_id=lease.tenant_id,
                lease_id=lease.lease_id,
            )

    async def get_status(self, tenant_id: str) -> dict:
        """Return aggregate usage and configured limits for a tenant."""
        now = datetime.now(timezone.utc)
        result = await self.redis.eval(
            self._STATUS_SCRIPT,
            4,
            *self._keys(tenant_id, now),
            int(now.timestamp() * 1000),
            settings.tenant_max_concurrent_executions,
            settings.tenant_daily_execution_limit,
            settings.tenant_daily_execution_seconds,
        )
        return {
            "tenant_id": tenant_id,
            "enabled": settings.tenant_quota_enabled,
            "active_executions": int(result[0]),
            "daily_executions": int(result[1]),
            "daily_execution_seconds": round(int(result[2]) / 1000, 3),
            "limits": {
                "max_concurrent_executions": int(result[3]),
                "daily_execution_limit": int(result[4]),
                "daily_execution_seconds": int(result[5]),
            },
            "customized": bool(result[6]),
            "resets_at": datetime.fromtimestamp(
                self._next_day_epoch(now) - 300,
                tz=timezone.utc,
            ).isoformat(),
        }

    async def set_limits(
        self,
        tenant_id: str,
        limits: TenantQuotaLimits,
    ) -> dict:
        """Persist tenant-specific limits and return the resulting status."""
        now = datetime.now(timezone.utc)
        config_key = self._keys(tenant_id, now)[3]
        await self.redis.hset(config_key, mapping=limits.to_dict())
        logger.info(
            "Tenant quota limits updated", tenant_id=tenant_id, **limits.to_dict()
        )
        return await self.get_status(tenant_id)

    async def reset_limits(self, tenant_id: str) -> dict:
        """Remove tenant overrides so environment defaults apply again."""
        now = datetime.now(timezone.utc)
        config_key = self._keys(tenant_id, now)[3]
        await self.redis.delete(config_key)
        logger.info("Tenant quota limits reset", tenant_id=tenant_id)
        return await self.get_status(tenant_id)
