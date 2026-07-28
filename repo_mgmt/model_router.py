"""Bounded OpenRouter client for RAMS.

The router reuses a tiny HTTP connection pool, applies task-specific token caps,
records response usage, and performs at most one configured retry plus one
secondary-model fallback.  No model request is made by :meth:`warmup`.
"""

from __future__ import annotations

import asyncio
import logging
import random
import threading
import time
from collections.abc import Mapping
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from repo_mgmt.config import Settings

logger = logging.getLogger(__name__)

_RETRY_STATUSES = {408, 409, 425, 429, 500, 502, 503, 504}
_JSON_MODE_UNSUPPORTED_STATUSES = {400, 422}


class ModelError(Exception):
    """Raised when an OpenRouter model call fails."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds


def _json_mode_unsupported(exc: ModelError) -> bool:
    """Return True when a provider appears to reject ``response_format``."""
    if exc.status_code not in _JSON_MODE_UNSUPPORTED_STATUSES:
        return False
    message = str(exc).lower()
    return "response_format" in message or "json" in message


def _retry_after_seconds(response: httpx.Response) -> float | None:
    """Parse Retry-After as seconds or an HTTP date, returning a safe bound."""
    raw = response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return max(0.0, min(float(raw), 60.0))
    except ValueError:
        try:
            when = parsedate_to_datetime(raw)
            return max(0.0, min(when.timestamp() - time.time(), 60.0))
        except (TypeError, ValueError, OverflowError):
            return None


class ModelRouter:
    """Route RAMS requests through a small, reusable OpenRouter client pool."""

    def __init__(self, cfg: Settings) -> None:
        self._cfg = cfg
        self._api_base = cfg.openrouter_api_base.rstrip("/")
        self._api_key = cfg.openrouter_api_key
        self._primary = cfg.openrouter_primary_model
        self._secondary = cfg.openrouter_secondary_model
        self._triage_model = cfg.openrouter_triage_model
        self._timeout = httpx.Timeout(
            connect=cfg.rms_openrouter_connect_timeout_seconds,
            read=cfg.rms_openrouter_read_timeout_seconds,
            write=cfg.rms_openrouter_write_timeout_seconds,
            pool=cfg.rms_openrouter_pool_timeout_seconds,
        )
        self._limits = httpx.Limits(
            max_connections=cfg.rms_openrouter_max_connections,
            max_keepalive_connections=cfg.rms_openrouter_max_keepalive_connections,
            keepalive_expiry=cfg.rms_openrouter_keepalive_expiry_seconds,
        )
        self._sync_client: httpx.Client | None = None
        self._async_client: httpx.AsyncClient | None = None
        self._client_lock = threading.Lock()
        self._usage_lock = threading.Lock()
        self._run_id: str | None = None
        self._usage: dict[str, Any] = self._empty_usage()

    @staticmethod
    def _empty_usage() -> dict[str, Any]:
        return {
            "requests": 0,
            "fallbacks": 0,
            "jsonModeRecoveries": 0,
            "promptTokens": 0,
            "completionTokens": 0,
            "reasoningTokens": 0,
            "cachedTokens": 0,
            "cost": 0.0,
            "models": {},
            "providers": {},
        }

    def warmup(self) -> dict[str, object]:
        """Initialise local clients only; never contact OpenRouter."""
        self._get_sync_client()
        self._get_async_client()
        return {
            "ready": True,
            "maxConnections": self._cfg.rms_openrouter_max_connections,
            "maxKeepaliveConnections": self._cfg.rms_openrouter_max_keepalive_connections,
        }

    @property
    def client_ready(self) -> bool:
        return self._sync_client is not None and self._async_client is not None

    def start_run(self, run_id: str) -> None:
        """Reset usage accounting for a newly admitted RAMS run."""
        with self._usage_lock:
            self._run_id = run_id
            self._usage = self._empty_usage()

    def usage_summary(self) -> dict[str, Any]:
        """Return a JSON-safe copy of aggregate OpenRouter usage."""
        with self._usage_lock:
            data = dict(self._usage)
            data["models"] = dict(self._usage.get("models", {}))
            data["providers"] = dict(self._usage.get("providers", {}))
            data["runId"] = self._run_id
            return data

    def complete(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 4096,
        json_mode: bool = False,
    ) -> str:
        """Synchronously call primary, then secondary only on retryable failure."""
        primary_tokens = min(max_tokens, self._cfg.rms_primary_max_tokens)
        try:
            return self._complete_model_sync(
                self._primary,
                prompt,
                system,
                primary_tokens,
                json_mode,
                self._cfg.rms_primary_temperature,
            )
        except ModelError as first_err:
            if not first_err.retryable:
                raise
            self._mark_fallback()
            logger.warning("model_router: primary failed retryably; using secondary")
        secondary_tokens = min(max_tokens, self._cfg.rms_secondary_max_tokens)
        try:
            return self._complete_model_sync(
                self._secondary,
                prompt,
                system,
                secondary_tokens,
                json_mode,
                self._cfg.rms_primary_temperature,
            )
        except ModelError as second_err:
            raise ModelError(
                f"Both primary and secondary models failed. Last error: {second_err}",
                status_code=second_err.status_code,
                retryable=second_err.retryable,
                retry_after_seconds=second_err.retry_after_seconds,
            ) from second_err

    async def complete_async(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 4096,
        json_mode: bool = False,
    ) -> str:
        """Asynchronously call primary, then secondary only on retryable failure."""
        primary_tokens = min(max_tokens, self._cfg.rms_primary_max_tokens)
        try:
            return await self._complete_model_async(
                self._primary,
                prompt,
                system,
                primary_tokens,
                json_mode,
                self._cfg.rms_primary_temperature,
            )
        except ModelError as first_err:
            if not first_err.retryable:
                raise
            self._mark_fallback()
            logger.warning("model_router: primary failed retryably; using secondary")
        secondary_tokens = min(max_tokens, self._cfg.rms_secondary_max_tokens)
        try:
            return await self._complete_model_async(
                self._secondary,
                prompt,
                system,
                secondary_tokens,
                json_mode,
                self._cfg.rms_primary_temperature,
            )
        except ModelError as second_err:
            raise ModelError(
                f"Both primary and secondary models failed. Last error: {second_err}",
                status_code=second_err.status_code,
                retryable=second_err.retryable,
                retry_after_seconds=second_err.retry_after_seconds,
            ) from second_err

    def triage(self, prompt: str, max_tokens: int = 256) -> str:
        tokens = min(max_tokens, self._cfg.rms_triage_max_tokens)
        return self._complete_model_sync(
            self._triage_model,
            prompt,
            "",
            tokens,
            False,
            self._cfg.rms_triage_temperature,
        )

    async def complete_with_model_async(
        self,
        model: str,
        prompt: str,
        system: str = "",
        max_tokens: int = 2048,
        json_mode: bool = False,
        temperature: float = 0.0,
    ) -> str:
        """Call one explicitly selected council model with the normal bounded transport."""
        if not str(model or "").strip():
            raise ModelError("Explicit council model is empty")
        return await self._complete_model_async(
            str(model).strip(), prompt, system, min(max_tokens, 4096), json_mode, temperature
        )

    async def triage_async(self, prompt: str, max_tokens: int = 256) -> str:
        tokens = min(max_tokens, self._cfg.rms_triage_max_tokens)
        return await self._complete_model_async(
            self._triage_model,
            prompt,
            "",
            tokens,
            False,
            self._cfg.rms_triage_temperature,
        )

    def _complete_model_sync(
        self,
        model: str,
        prompt: str,
        system: str,
        max_tokens: int,
        json_mode: bool,
        temperature: float,
    ) -> str:
        try:
            return self._request_with_retries_sync(
                model, prompt, system, max_tokens, json_mode, temperature
            )
        except ModelError as exc:
            if json_mode and _json_mode_unsupported(exc):
                self._mark_json_recovery()
                logger.warning(
                    "model_router: JSON mode rejected; retrying same model without it"
                )
                return self._request_with_retries_sync(
                    model, prompt, system, max_tokens, False, temperature
                )
            raise

    async def _complete_model_async(
        self,
        model: str,
        prompt: str,
        system: str,
        max_tokens: int,
        json_mode: bool,
        temperature: float,
    ) -> str:
        try:
            return await self._request_with_retries_async(
                model, prompt, system, max_tokens, json_mode, temperature
            )
        except ModelError as exc:
            if json_mode and _json_mode_unsupported(exc):
                self._mark_json_recovery()
                logger.warning(
                    "model_router: JSON mode rejected; retrying same model without it"
                )
                return await self._request_with_retries_async(
                    model, prompt, system, max_tokens, False, temperature
                )
            raise

    def _request_with_retries_sync(
        self,
        model: str,
        prompt: str,
        system: str,
        max_tokens: int,
        json_mode: bool,
        temperature: float,
    ) -> str:
        attempts = self._cfg.rms_openrouter_max_retries + 1
        last_error: ModelError | None = None
        for attempt in range(attempts):
            try:
                return self._call_sync(
                    model, prompt, system, max_tokens, json_mode, temperature
                )
            except ModelError as exc:
                last_error = exc
                if not exc.retryable or attempt + 1 >= attempts:
                    raise
                time.sleep(self._retry_delay(attempt, exc.retry_after_seconds))
        raise last_error or ModelError("OpenRouter request failed")

    async def _request_with_retries_async(
        self,
        model: str,
        prompt: str,
        system: str,
        max_tokens: int,
        json_mode: bool,
        temperature: float,
    ) -> str:
        attempts = self._cfg.rms_openrouter_max_retries + 1
        last_error: ModelError | None = None
        for attempt in range(attempts):
            try:
                return await self._call_async(
                    model, prompt, system, max_tokens, json_mode, temperature
                )
            except ModelError as exc:
                last_error = exc
                if not exc.retryable or attempt + 1 >= attempts:
                    raise
                await asyncio.sleep(self._retry_delay(attempt, exc.retry_after_seconds))
        raise last_error or ModelError("OpenRouter request failed")

    def _retry_delay(self, attempt: int, retry_after: float | None) -> float:
        if retry_after is not None:
            return float(min(retry_after, self._cfg.rms_openrouter_retry_max_seconds))
        base = self._cfg.rms_openrouter_retry_base_seconds * (2**attempt)
        jitter = random.uniform(0.0, min(base * 0.2, 1.0))
        return float(min(base + jitter, self._cfg.rms_openrouter_retry_max_seconds))

    def _call_sync(
        self,
        model: str,
        prompt: str,
        system: str,
        max_tokens: int,
        json_mode: bool,
        temperature: float,
    ) -> str:
        url, headers, payload = self._request_parts(
            model, prompt, system, max_tokens, json_mode, temperature
        )
        started = time.monotonic()
        try:
            response = self._get_sync_client().post(url, headers=headers, json=payload)
        except httpx.TimeoutException as exc:
            raise ModelError(
                f"OpenRouter request timed out: {exc}", retryable=True
            ) from exc
        except httpx.RequestError as exc:
            raise ModelError(
                f"OpenRouter request error: {exc}", retryable=True
            ) from exc
        return self._parse_response(response, model, time.monotonic() - started)

    async def _call_async(
        self,
        model: str,
        prompt: str,
        system: str,
        max_tokens: int,
        json_mode: bool,
        temperature: float,
    ) -> str:
        url, headers, payload = self._request_parts(
            model, prompt, system, max_tokens, json_mode, temperature
        )
        started = time.monotonic()
        try:
            response = await self._get_async_client().post(
                url, headers=headers, json=payload
            )
        except httpx.TimeoutException as exc:
            raise ModelError(
                f"OpenRouter request timed out: {exc}", retryable=True
            ) from exc
        except httpx.RequestError as exc:
            raise ModelError(
                f"OpenRouter request error: {exc}", retryable=True
            ) from exc
        return self._parse_response(response, model, time.monotonic() - started)

    def _request_parts(
        self,
        model: str,
        prompt: str,
        system: str,
        max_tokens: int,
        json_mode: bool,
        temperature: float,
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "top_p": self._cfg.rms_top_p,
            "provider": {
                "sort": self._cfg.rms_openrouter_provider_sort,
                "allow_fallbacks": self._cfg.rms_openrouter_allow_fallbacks,
                "data_collection": self._cfg.rms_openrouter_data_collection,
            },
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
            provider = payload["provider"]
            if isinstance(provider, dict):
                provider["require_parameters"] = True
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "X-Title": self._cfg.openrouter_app_name,
        }
        if self._cfg.openrouter_http_referer.strip():
            headers["HTTP-Referer"] = self._cfg.openrouter_http_referer.strip()
        if self._cfg.rms_openrouter_log_prompts:
            logger.debug("model_router: prompt logging enabled length=%d", len(prompt))
        return f"{self._api_base}/chat/completions", headers, payload

    def _parse_response(
        self, response: httpx.Response, requested_model: str, duration: float
    ) -> str:
        if response.status_code in _RETRY_STATUSES:
            raise ModelError(
                f"Retryable HTTP {response.status_code} from {requested_model}: {response.text[:200]}",
                status_code=response.status_code,
                retryable=True,
                retry_after_seconds=_retry_after_seconds(response),
            )
        if response.status_code >= 400:
            raise ModelError(
                f"HTTP {response.status_code} from {requested_model}: {response.text[:200]}",
                status_code=response.status_code,
                retryable=False,
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise ModelError(
                f"OpenRouter returned invalid JSON for {requested_model}"
            ) from exc
        try:
            content = str(data["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise ModelError(
                f"Unexpected response shape from {requested_model}: {exc}"
            ) from exc
        self._record_usage(data, requested_model, duration)
        return content

    def _record_usage(
        self, data: Mapping[str, Any], requested_model: str, duration: float
    ) -> None:
        usage = data.get("usage")
        usage_map = usage if isinstance(usage, Mapping) else {}
        actual_model = str(data.get("model") or requested_model)
        prompt_details = usage_map.get("prompt_tokens_details")
        prompt_details_map = (
            prompt_details if isinstance(prompt_details, Mapping) else {}
        )
        completion_details = usage_map.get("completion_tokens_details")
        completion_details_map = (
            completion_details if isinstance(completion_details, Mapping) else {}
        )
        prompt_tokens = self._safe_int(usage_map.get("prompt_tokens"))
        completion_tokens = self._safe_int(usage_map.get("completion_tokens"))
        reasoning_tokens = self._safe_int(
            usage_map.get("reasoning_tokens")
            or completion_details_map.get("reasoning_tokens")
        )
        cached_tokens = self._safe_int(
            usage_map.get("cached_tokens") or prompt_details_map.get("cached_tokens")
        )
        provider = str(data.get("provider") or "").strip()
        cost_raw = usage_map.get("cost", data.get("cost", 0.0))
        try:
            cost = float(cost_raw or 0.0)
        except (TypeError, ValueError):
            cost = 0.0
        with self._usage_lock:
            self._usage["requests"] += 1
            self._usage["promptTokens"] += prompt_tokens
            self._usage["completionTokens"] += completion_tokens
            self._usage["reasoningTokens"] += reasoning_tokens
            self._usage["cachedTokens"] += cached_tokens
            self._usage["cost"] = round(float(self._usage["cost"]) + cost, 10)
            models = self._usage["models"]
            if isinstance(models, dict):
                item = models.setdefault(
                    actual_model, {"requests": 0, "durationSeconds": 0.0}
                )
                item["requests"] += 1
                item["durationSeconds"] = round(
                    float(item["durationSeconds"]) + duration, 4
                )
            providers = self._usage["providers"]
            if provider and isinstance(providers, dict):
                providers[provider] = int(providers.get(provider, 0)) + 1
        if self._cfg.rms_openrouter_log_usage:
            cost_text = f" cost={cost:.8f}" if self._cfg.rms_openrouter_log_cost else ""
            logger.info(
                "model_router: model=%s prompt_tokens=%d completion_tokens=%d duration=%.3fs%s",
                actual_model,
                prompt_tokens,
                completion_tokens,
                duration,
                cost_text,
            )

    @staticmethod
    def _safe_int(value: object) -> int:
        """Coerce optional OpenRouter usage values without breaking a run report."""
        if not isinstance(value, (str, bytes, bytearray, int, float)):
            return 0
        try:
            return int(value or 0)
        except (TypeError, ValueError, OverflowError):
            return 0

    def _mark_fallback(self) -> None:
        with self._usage_lock:
            self._usage["fallbacks"] += 1

    def _mark_json_recovery(self) -> None:
        with self._usage_lock:
            self._usage["jsonModeRecoveries"] += 1

    def _get_sync_client(self) -> httpx.Client:
        if self._sync_client is None:
            with self._client_lock:
                if self._sync_client is None:
                    self._sync_client = httpx.Client(
                        timeout=self._timeout, limits=self._limits
                    )
        return self._sync_client

    def _get_async_client(self) -> httpx.AsyncClient:
        if self._async_client is None:
            with self._client_lock:
                if self._async_client is None:
                    self._async_client = httpx.AsyncClient(
                        timeout=self._timeout, limits=self._limits
                    )
        return self._async_client

    def close(self) -> None:
        client = self._sync_client
        self._sync_client = None
        if client is not None:
            client.close()

    async def aclose(self) -> None:
        async_client = self._async_client
        self._async_client = None
        if async_client is not None:
            await async_client.aclose()
        self.close()
