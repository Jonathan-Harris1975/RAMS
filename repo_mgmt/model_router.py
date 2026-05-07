"""
Model router for the Repo Management Suite.

Routes LLM requests to OpenRouter with:
  - HTTP-Referer: repo-management-suite header on every request
  - Primary model first; retry once with secondary on 429 or 5xx
  - Triage model for issue_normaliser editorial classification only
  - 90-second timeout on all requests
  - Raises ModelError if both models fail
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


class ModelError(Exception):
    """Raised when all model routing attempts are exhausted."""


class ModelRouter:
    """Routes LLM calls through OpenRouter with primary/secondary fallback."""

    def __init__(self, cfg: "Settings") -> None:
        """
        Initialise ModelRouter from settings.

        Args:
            cfg: Validated RMS settings containing OpenRouter credentials.
        """
        self._api_base = cfg.openrouter_api_base.rstrip("/")
        self._api_key = cfg.openrouter_api_key
        self._primary = cfg.openrouter_primary_model
        self._secondary = cfg.openrouter_secondary_model
        self._triage_model = cfg.openrouter_triage_model

    # ── Public API ─────────────────────────────────────────────────────────

    def complete(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 4096,
    ) -> str:
        """
        Send *prompt* to the primary model; fall back to secondary on 429/5xx.

        Args:
            prompt: User-turn text.
            system: Optional system prompt text.
            max_tokens: Maximum tokens to generate.

        Returns:
            Raw assistant response text.

        Raises:
            ModelError: If both primary and secondary models fail.
        """
        try:
            return self._call(self._primary, prompt, system, max_tokens)
        except ModelError as first_err:
            logger.warning(
                "model_router: primary model failed (%s) — retrying with secondary", first_err
            )

        try:
            return self._call(self._secondary, prompt, system, max_tokens)
        except ModelError as second_err:
            raise ModelError(
                f"Both primary and secondary models failed. "
                f"Last error: {second_err}"
            ) from second_err

    def triage(
        self,
        prompt: str,
        max_tokens: int = 256,
    ) -> str:
        """
        Send *prompt* to the triage model for lightweight classification.

        Args:
            prompt: Classification prompt (expect JSON response).
            max_tokens: Maximum tokens for the triage response.

        Returns:
            Raw assistant response text.

        Raises:
            ModelError: If the triage model call fails.
        """
        return self._call(self._triage_model, prompt, "", max_tokens)

    # ── Internal ───────────────────────────────────────────────────────────

    def _call(
        self,
        model: str,
        prompt: str,
        system: str,
        max_tokens: int,
    ) -> str:
        """
        POST to OpenRouter /chat/completions for *model*.

        Args:
            model: Full OpenRouter model identifier string.
            prompt: User message content.
            system: System message content (omitted when empty).
            max_tokens: Token limit for the completion.

        Returns:
            Assistant response text.

        Raises:
            ModelError: On HTTP error or retryable status code.
        """
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        url = f"{self._api_base}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": _REFERER,
        }
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
        }

        try:
            response = httpx.post(url, headers=headers, json=payload, timeout=_TIMEOUT)
        except httpx.TimeoutException as exc:
            raise ModelError(f"Request timed out after {_TIMEOUT}s: {exc}") from exc
        except httpx.RequestError as exc:
            raise ModelError(f"HTTP request error: {exc}") from exc

        if response.status_code in _RETRY_STATUSES:
            raise ModelError(
                f"Retryable HTTP {response.status_code} from {model}: {response.text[:200]}"
            )

        if response.status_code >= 400:
            raise ModelError(
                f"HTTP {response.status_code} from {model}: {response.text[:200]}"
            )

        data = response.json()
        try:
            return str(data["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise ModelError(
                f"Unexpected response shape from {model}: {exc}\n{data}"
            ) from exc
