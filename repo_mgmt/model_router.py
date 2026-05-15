"""
Model router for the Repo Management Suite.

Routes model requests to OpenRouter with primary-model execution and a single
secondary-model fallback only for retryable HTTP statuses: 429 or 5xx. The
production pipeline uses the async methods so model calls do not block the
FastAPI event loop.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from repo_mgmt.config import Settings

logger = logging.getLogger(__name__)

_RETRY_STATUSES = {429, 500, 502, 503, 504}
_TIMEOUT = 90.0
_REFERER = "repo-management-suite"
_JSON_MODE_UNSUPPORTED_STATUSES = {400, 422}


def _json_mode_unsupported(exc: "ModelError") -> bool:
    """Return True when a provider appears to reject response_format."""
    if exc.status_code not in _JSON_MODE_UNSUPPORTED_STATUSES:
        return False
    message = str(exc).lower()
    return "response_format" in message or "json" in message


class ModelError(Exception):
    """Raised when a model call fails."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
    ) -> None:
        """Initialise the error with retry metadata."""
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


class ModelRouter:
    """Routes LLM calls through OpenRouter with constrained fallback."""

    def __init__(self, cfg: "Settings") -> None:
        """Initialise the router from validated settings."""
        self._api_base = cfg.openrouter_api_base.rstrip("/")
        self._api_key = cfg.openrouter_api_key
        self._primary = cfg.openrouter_primary_model
        self._secondary = cfg.openrouter_secondary_model
        self._triage_model = cfg.openrouter_triage_model

    def complete(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 4096,
        json_mode: bool = False,
    ) -> str:
        """Synchronously call the primary model with retryable fallback."""
        try:
            return self._call(self._primary, prompt, system, max_tokens, json_mode)
        except ModelError as first_err:
            if json_mode and _json_mode_unsupported(first_err):
                logger.warning(
                    "model_router: provider rejected JSON mode (%s) - retrying without response_format",
                    first_err,
                )
                return self.complete(prompt, system, max_tokens, json_mode=False)
            if not first_err.retryable:
                raise
            logger.warning(
                "model_router: primary retryable failure (%s) - retrying secondary",
                first_err,
            )
        try:
            return self._call(self._secondary, prompt, system, max_tokens, json_mode)
        except ModelError as second_err:
            raise ModelError(
                f"Both primary and secondary models failed. Last error: {second_err}",
                status_code=second_err.status_code,
                retryable=second_err.retryable,
            ) from second_err

    async def complete_async(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 4096,
        json_mode: bool = False,
    ) -> str:
        """Asynchronously call the primary model with retryable fallback."""
        try:
            return await self._call_async(
                self._primary, prompt, system, max_tokens, json_mode
            )
        except ModelError as first_err:
            if json_mode and _json_mode_unsupported(first_err):
                logger.warning(
                    "model_router: provider rejected JSON mode (%s) - retrying without response_format",
                    first_err,
                )
                return await self.complete_async(
                    prompt, system, max_tokens, json_mode=False
                )
            if not first_err.retryable:
                raise
            logger.warning(
                "model_router: primary retryable failure (%s) - retrying secondary",
                first_err,
            )
        try:
            return await self._call_async(
                self._secondary, prompt, system, max_tokens, json_mode
            )
        except ModelError as second_err:
            raise ModelError(
                f"Both primary and secondary models failed. Last error: {second_err}",
                status_code=second_err.status_code,
                retryable=second_err.retryable,
            ) from second_err

    def triage(self, prompt: str, max_tokens: int = 256) -> str:
        """Synchronously call the triage model for classification."""
        return self._call(self._triage_model, prompt, "", max_tokens)

    async def triage_async(self, prompt: str, max_tokens: int = 256) -> str:
        """Asynchronously call the triage model for classification."""
        return await self._call_async(self._triage_model, prompt, "", max_tokens)

    def _call(
        self,
        model: str,
        prompt: str,
        system: str,
        max_tokens: int,
        json_mode: bool = False,
    ) -> str:
        """POST synchronously to OpenRouter /chat/completions for *model*."""
        url, headers, payload = self._request_parts(
            model, prompt, system, max_tokens, json_mode
        )
        try:
            response = httpx.post(url, headers=headers, json=payload, timeout=_TIMEOUT)
        except httpx.TimeoutException as exc:
            raise ModelError(f"Request timed out after {_TIMEOUT}s: {exc}") from exc
        except httpx.RequestError as exc:
            raise ModelError(f"HTTP request error: {exc}") from exc
        return self._parse_response(response, model)

    async def _call_async(
        self,
        model: str,
        prompt: str,
        system: str,
        max_tokens: int,
        json_mode: bool = False,
    ) -> str:
        """POST asynchronously to OpenRouter /chat/completions for *model*."""
        url, headers, payload = self._request_parts(
            model, prompt, system, max_tokens, json_mode
        )
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                response = await client.post(url, headers=headers, json=payload)
        except httpx.TimeoutException as exc:
            raise ModelError(f"Request timed out after {_TIMEOUT}s: {exc}") from exc
        except httpx.RequestError as exc:
            raise ModelError(f"HTTP request error: {exc}") from exc
        return self._parse_response(response, model)

    def _request_parts(
        self,
        model: str,
        prompt: str,
        system: str,
        max_tokens: int,
        json_mode: bool = False,
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        """Build URL, headers, and JSON payload for one OpenRouter request."""
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        return (
            f"{self._api_base}/chat/completions",
            {
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": _REFERER,
            },
            payload,
        )

    def _parse_response(self, response: httpx.Response, model: str) -> str:
        """Parse an OpenRouter response or raise ModelError with retry metadata."""
        if response.status_code in _RETRY_STATUSES:
            raise ModelError(
                f"Retryable HTTP {response.status_code} from {model}: {response.text[:200]}",
                status_code=response.status_code,
                retryable=True,
            )
        if response.status_code >= 400:
            raise ModelError(
                f"HTTP {response.status_code} from {model}: {response.text[:200]}",
                status_code=response.status_code,
                retryable=False,
            )
        data = response.json()
        try:
            return str(data["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise ModelError(
                f"Unexpected response shape from {model}: {exc}\n{data}"
            ) from exc
