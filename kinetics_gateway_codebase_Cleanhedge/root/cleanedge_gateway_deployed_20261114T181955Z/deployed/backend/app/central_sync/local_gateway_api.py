from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from .config import LocalGatewayApiConfig


class LocalGatewayApiError(RuntimeError):
    pass


class LocalGatewayApiClient:
    """Authenticated loopback client used by Central Sync.

    The client talks only to the already-running gateway REST API. It never
    performs Modbus I/O itself, which keeps Central Sync isolated from the
    southbound polling/control implementation.
    """

    def __init__(self, config: LocalGatewayApiConfig, client: httpx.AsyncClient | Any | None = None) -> None:
        self.config = config
        self._token: str | None = config.resolved_token()
        self._owns_client = client is None
        self._snapshot_lock = asyncio.Lock()
        self._snapshot_cache: dict[str, Any] | None = None
        self._snapshot_cache_mono = 0.0
        self._normalized_endpoint_available: bool | None = None
        self.client = client or httpx.AsyncClient(
            verify=config.verify_tls,
            timeout=httpx.Timeout(config.timeout_sec, connect=config.connect_timeout_sec),
            headers={"User-Agent": config.user_agent},
        )

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def health(self) -> dict[str, Any]:
        return await self.get_json(self.config.health_path)

    async def telemetry_snapshot(self) -> dict[str, Any]:
        if self._snapshot_cache is not None and time.monotonic() - self._snapshot_cache_mono < 0.75:
            return self._snapshot_cache
        async with self._snapshot_lock:
            if self._snapshot_cache is not None and time.monotonic() - self._snapshot_cache_mono < 0.75:
                return self._snapshot_cache
            last_error: Exception | None = None
            for attempt in range(3):
                try:
                    snapshot = await self.get_json(self.config.telemetry_snapshot_path)
                    self._snapshot_cache = snapshot
                    self._snapshot_cache_mono = time.monotonic()
                    return snapshot
                except LocalGatewayApiError as exc:
                    last_error = exc
                    if "HTTP 503" not in str(exc) or attempt == 2:
                        raise
                    await asyncio.sleep(0.2 * (attempt + 1))
            raise last_error or LocalGatewayApiError("telemetry snapshot unavailable")

    async def normalized_snapshot(self) -> dict[str, Any]:
        if self._normalized_endpoint_available is not False:
            try:
                result = await self.get_json(self.config.normalized_snapshot_path)
                self._normalized_endpoint_available = True
                return result
            except LocalGatewayApiError as exc:
                if "HTTP 404" not in str(exc):
                    raise
                self._normalized_endpoint_available = False
        if self._normalized_endpoint_available is False:
            # CleanEdge keeps the plant cache endpoint but intentionally does not
            # add Kalpa vendor assumptions to the gateway process. Normalize here.
            from .cleanedge_normalized import build_cleanedge_normalized_snapshot
            return build_cleanedge_normalized_snapshot(await self.telemetry_snapshot())

    async def alarms_history(self, **params: Any) -> Any:
        return await self.get_json_any(self.config.alarms_history_path, params=params)

    async def commands_audit(self, **params: Any) -> Any:
        return await self.get_json_any(self.config.commands_audit_path, params=params)

    async def control_status_all(self) -> dict[str, Any]:
        return await self.get_json(self.config.control_status_all_path)

    async def control_status_compact(self) -> dict[str, Any]:
        return await self.get_json(self.config.control_status_compact_path)

    async def platform_readiness(self) -> dict[str, Any]:
        try:
            return await self.get_json(self.config.platform_readiness_path)
        except LocalGatewayApiError as exc:
            if "HTTP 404" not in str(exc):
                raise
            # CleanEdge exposes readiness within its existing health/control
            # surfaces; absence of Kalpa's convenience endpoint is not a reason
            # to fail the read-only S10 observer.
            health = await self.health()
            control = health.get("control_sequence") or {}
            return {
                "ready": control.get("ready"),
                "commissioning": control.get("commissioning_status"),
                "source": "cleanedge_health_fallback",
            }

    async def get_json(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        body = await self.get_json_any(path, params=params)
        if not isinstance(body, dict):
            raise LocalGatewayApiError(f"gateway API {path} response must be a JSON object")
        return body

    async def get_json_any(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        token = await self._ensure_token()
        url = self.config.base_url.rstrip("/") + (path if path.startswith("/") else f"/{path}")
        response = await self.client.get(url, params=params, headers={"Authorization": f"Bearer {token}"})
        if response.status_code == 401:
            self._token = None
            token = await self._ensure_token(force_login=True)
            response = await self.client.get(url, params=params, headers={"Authorization": f"Bearer {token}"})
        if response.status_code != 200:
            raise LocalGatewayApiError(f"GET {path} returned HTTP {response.status_code}: {response.text[:500]}")
        try:
            return response.json()
        except Exception as exc:
            raise LocalGatewayApiError(f"gateway API {path} response is not valid JSON: {exc}") from exc

    async def post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        token = await self._ensure_token()
        url = self.config.base_url.rstrip("/") + (path if path.startswith("/") else f"/{path}")
        response = await self.client.post(url, json=payload, headers={"Authorization": f"Bearer {token}"})
        if response.status_code == 401:
            self._token = None
            token = await self._ensure_token(force_login=True)
            response = await self.client.post(url, json=payload, headers={"Authorization": f"Bearer {token}"})
        if not (200 <= response.status_code < 300):
            raise LocalGatewayApiError(f"POST {path} returned HTTP {response.status_code}: {response.text[:500]}")
        try:
            body = response.json()
        except Exception as exc:
            raise LocalGatewayApiError(f"gateway API {path} response is not valid JSON: {exc}") from exc
        if not isinstance(body, dict):
            raise LocalGatewayApiError(f"gateway API {path} response must be a JSON object")
        return body

    async def _ensure_token(self, *, force_login: bool = False) -> str:
        if self._token and not force_login:
            return self._token
        static_token = self.config.resolved_token()
        if static_token and not force_login:
            self._token = static_token
            return self._token
        password = self.config.resolved_password()
        if not password:
            raise LocalGatewayApiError(
                "local gateway API credentials not configured: set "
                f"{self.config.password_env} or {self.config.token_env}"
            )
        response = await self.client.post(
            self.config.login_url,
            json={"username": self.config.username, "password": password},
        )
        if response.status_code != 200:
            raise LocalGatewayApiError(
                f"local gateway login failed HTTP {response.status_code}: {response.text[:500]}"
            )
        body = response.json()
        token = str(body.get("access_token") or "").strip()
        if not token:
            raise LocalGatewayApiError("local gateway login response did not contain access_token")
        self._token = token
        return token

