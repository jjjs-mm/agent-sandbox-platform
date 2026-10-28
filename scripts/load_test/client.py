"""Async HTTP client for load testing the Code Interpreter API."""

import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx

from .models import ExecutionResult


class LoadTestClient:
    """Async HTTP client for load testing."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout_seconds: int = 60,
        max_connections: int = 100,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.max_connections = max_connections
        self.transport = transport
        self._session: Optional[httpx.AsyncClient] = None
        self._request_counter = 0

    async def __aenter__(self) -> "LoadTestClient":
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()

    async def start(self) -> None:
        """Initialize the HTTP session."""
        if self._session is None:
            self._session = httpx.AsyncClient(
                base_url=self.base_url,
                headers=self._get_headers(),
                timeout=httpx.Timeout(self.timeout_seconds),
                limits=httpx.Limits(
                    max_connections=self.max_connections,
                    max_keepalive_connections=self.max_connections,
                ),
                verify=False,
                transport=self.transport,
            )

    async def close(self) -> None:
        """Close the HTTP session."""
        if self._session:
            await self._session.aclose()
            self._session = None

    def _get_headers(self) -> Dict[str, str]:
        """Get request headers."""
        return {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
        }

    async def execute_code(
        self,
        code: str,
        language: str = "py",
        scenario_id: str = "unknown",
        session_id: Optional[str] = None,
        entity_id: Optional[str] = None,
    ) -> ExecutionResult:
        """Execute code on the API and return result."""
        if self._session is None:
            await self.start()

        self._request_counter += 1
        request_id = f"load-test-{self._request_counter}"

        payload = {
            "lang": language,
            "code": code,
            "entity_id": entity_id or request_id,
            "user_id": "load-tester",
        }
        if session_id:
            payload["session_id"] = session_id

        start_time = time.perf_counter()
        try:
            response = await self._session.post("/exec", json=payload)
            latency_ms = (time.perf_counter() - start_time) * 1000
            body = response.json()

            success = response.status_code == 200
            error = None
            if not success:
                raw_error = body.get("detail", body.get("error", ""))
                error = str(raw_error or f"HTTP {response.status_code}")

            return ExecutionResult(
                success=success,
                latency_ms=latency_ms,
                status_code=response.status_code,
                language=language,
                scenario_id=scenario_id,
                error=error,
                files_generated=len(body.get("files", [])),
                timestamp=datetime.now(timezone.utc),
                container_source=body.get("container_source"),
            )

        except httpx.TimeoutException:
            latency_ms = (time.perf_counter() - start_time) * 1000
            return ExecutionResult(
                success=False,
                latency_ms=latency_ms,
                status_code=0,
                language=language,
                scenario_id=scenario_id,
                error="Request timed out",
                timestamp=datetime.now(timezone.utc),
            )
        except httpx.RequestError as e:
            latency_ms = (time.perf_counter() - start_time) * 1000
            return ExecutionResult(
                success=False,
                latency_ms=latency_ms,
                status_code=0,
                language=language,
                scenario_id=scenario_id,
                error=str(e),
                timestamp=datetime.now(timezone.utc),
            )
        except Exception as e:
            latency_ms = (time.perf_counter() - start_time) * 1000
            return ExecutionResult(
                success=False,
                latency_ms=latency_ms,
                status_code=0,
                language=language,
                scenario_id=scenario_id,
                error=f"Unexpected error: {e}",
                timestamp=datetime.now(timezone.utc),
            )

    async def check_health(self) -> Dict[str, Any]:
        """Check API health status."""
        if self._session is None:
            await self.start()

        try:
            response = await self._session.get("/health")
            if response.status_code == 200:
                return response.json()
            return {"status": "unhealthy", "code": response.status_code}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def check_detailed_health(self) -> Dict[str, Any]:
        """Check detailed API health status."""
        if self._session is None:
            await self.start()

        try:
            response = await self._session.get("/health/detailed")
            if response.status_code == 200:
                return response.json()
            return {"status": "unhealthy", "code": response.status_code}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def get_metrics(self) -> Dict[str, Any]:
        """Get API metrics."""
        if self._session is None:
            await self.start()

        try:
            response = await self._session.get("/metrics")
            if response.status_code == 200:
                return response.json()
            return {"error": f"HTTP {response.status_code}"}
        except Exception as e:
            return {"error": str(e)}

    async def get_pool_metrics(self) -> Dict[str, Any]:
        """Get sandbox pool metrics."""
        if self._session is None:
            await self.start()

        try:
            response = await self._session.get("/metrics/pool")
            if response.status_code == 200:
                return response.json()
            return {"error": f"HTTP {response.status_code}"}
        except Exception as e:
            return {"error": str(e)}

    async def warmup(self, language: str = "py", count: int = 5) -> int:
        """Warm up the API with a few requests."""
        code = "print('warmup')"
        successful = 0
        for _ in range(count):
            result = await self.execute_code(code, language, "warmup")
            if result.success:
                successful += 1
        return successful

    async def probe_health(self) -> ExecutionResult:
        """Measure one lightweight control-plane request."""
        if self._session is None:
            await self.start()

        started_at = time.perf_counter()
        try:
            response = await self._session.get("/health")
            latency_ms = (time.perf_counter() - started_at) * 1000
            return ExecutionResult(
                success=response.status_code == 200,
                latency_ms=latency_ms,
                status_code=response.status_code,
                language="http",
                scenario_id="health_baseline",
                error=None if response.status_code == 200 else response.text[:200],
            )
        except httpx.TimeoutException:
            return ExecutionResult(
                success=False,
                latency_ms=(time.perf_counter() - started_at) * 1000,
                status_code=0,
                language="http",
                scenario_id="health_baseline",
                error="Request timed out",
            )
        except httpx.RequestError as exc:
            return ExecutionResult(
                success=False,
                latency_ms=(time.perf_counter() - started_at) * 1000,
                status_code=0,
                language="http",
                scenario_id="health_baseline",
                error=str(exc),
            )
