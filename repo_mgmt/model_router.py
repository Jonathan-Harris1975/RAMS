"""
Model router for the Repo Management Suite.

Routes LLM calls to OpenRouter, implementing:
  - Primary model with automatic fallback to secondary on 429 / 5xx
  - Triage model for cheap classification calls
  - Exponential back-off with up to 3 retries per attempt
  - Raises ModelRouterError on unrecoverable failure
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from repo_mgmt.config import Settings

logger = logging.getLogger(__name__)

_RETRY_STATUS_CODES = {429, 500, 502, 503, 504}
_MAX_RETRIES = 3
_BACKOFF_BASE = 2.0  # seconds


class ModelRouterError(Exception):
    """Raised when all model routing attempts are exhausted."""


class ModelRouter:
    """Routes LLM calls through OpenRouter with fallback and retry logic."""

    def __init__(self, cfg: "Settings") -> None:
        self._api_base = cfg.openrouter_api_base.rstrip("/")
        self._api_key = cfg.openrouter_api_key
        self._primary = cfg.openrouter_primary_model
        self._secondary = cfg.openrouter_secondary_model
        self._triage = cfg.openrouter_triage_model

    # ── Public API ─────────────────────────────────────────────────────────

    def complete(self, prompt: str, system: str = "", max_tokens: int = 4096) -> str:
        """
        Send *prompt* to the primary model; fall back to secondary on 429/5xx.

        Args:
            prompt: User-turn text.
            system: Optional system prompt.
            max_tokens: Maximum tokens to generate.

        Returns:
            Assistant response text.

        Raises:
            ModelRouterError: If both primary and secondary models fail.
        """
        try:
            return self._call(self._primary, prompt, system, max_tokens)
        except ModelRouterError as primary_exc:
            logger.warning(
                "model_router: primary model %r failed (%s) — trying secondary %r",
                self._primary,
                primary_exc,
                self._secondary,
            )
        return self._call(self._secondary, prompt, system, max_tokens)

    def triage(self, prompt: str, max_tokens: int = 32) -> str:
        """
        Send *prompt* to the cheap triage model for classification.

        Args:
            prompt: Classification prompt.
            max_tokens: Maximum tokens (default 32 — just one word needed).

        Returns:
            Model response text.

        Raises:
            ModelRouterError: If the triage model fails after retries.
        """
        return self._call(self._triage, prompt, "", max_tokens)

    # ── Internal ───────────────────────────────────────────────────────────

    def _call(
        self,
        model: str,
        prompt: str,
        system: str,
        max_tokens: int,
    ) -> str:
        """
        POST to the OpenRouter /chat/completions endpoint with exponential back-off.

        Args:
            model: Model identifier string.
            prompt: User-turn text.
            system: System prompt (may be empty).
            max_tokens: Maximum tokens to generate.

        Returns:
            Assistant message text.

        Raises:
            ModelRouterError: After *_MAX_RETRIES* failed attempts.
        """
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        last_exc: Exception | None = None
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                with httpx.Client(timeout=120.0) as client:
                    response = client.post(
                        f"{self._api_base}/chat/completions",
                        json=payload,
                        headers=headers,
                    )
                if response.status_code in _RETRY_STATUS_CODES:
                    wait = _BACKOFF_BASE ** attempt
                    logger.warning(
                        "model_router: %s returned %d on attempt %d/%d — waiting %.1fs",
                        model,
                        response.status_code,
                        attempt,
                        _MAX_RETRIES,
                        wait,
                    )
                    time.sleep(wait)
                    last_exc = ModelRouterError(
                        f"HTTP {response.status_code} from {model}"
                    )
                    continue
                response.raise_for_status()
                data = response.json()
                return str(data["choices"][0]["message"]["content"])
            except (httpx.HTTPStatusError, httpx.RequestError) as exc:
                wait = _BACKOFF_BASE ** attempt
                logger.warning(
                    "model_router: network error on attempt %d/%d: %s — waiting %.1fs",
                    attempt,
                    _MAX_RETRIES,
                    exc,
                    wait,
                )
                time.sleep(wait)
                last_exc = exc

        raise ModelRouterError(
            f"All {_MAX_RETRIES} attempts failed for model {model!r}: {last_exc}"
        )
