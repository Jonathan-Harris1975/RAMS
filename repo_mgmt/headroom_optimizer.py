"""Fail-open Headroom context optimisation for RAMS OpenRouter requests.

RAMS uses Headroom's inline Python API rather than running a second proxy.  The
integration is deliberately conservative for the production eMicro runtime:

* system instructions are never rewritten;
* AnchorPatch/v1 planning/repair requests are never compressed because exact
  source text is required for deterministic anchors;
* the optional Kompress ML model is disabled by default, avoiding a model
  download and keeping RAM/CPU use bounded;
* compression failures always fall back to the original messages.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from repo_mgmt.config import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HeadroomOutcome:
    """Result metadata used by :class:`ModelRouter` usage accounting."""

    messages: list[dict[str, str]]
    attempted: bool = False
    compressed: bool = False
    tokens_before: int = 0
    tokens_after: int = 0
    tokens_saved: int = 0
    transforms: tuple[str, ...] = field(default_factory=tuple)
    skipped_reason: str = ""
    failed: bool = False


class HeadroomOptimizer:
    """Apply bounded Headroom compression without making it a reliability gate."""

    def __init__(self, cfg: "Settings") -> None:
        self._cfg = cfg
        self._compressor: Callable[..., Any] | None = None
        self._load_attempted = False

    def optimise(
        self,
        messages: list[dict[str, str]],
        *,
        model: str,
        exact_context: bool = False,
    ) -> HeadroomOutcome:
        """Return optimised messages, or originals when compression is unsuitable."""
        original = [dict(message) for message in messages]
        if not self._cfg.rms_headroom_enabled:
            return HeadroomOutcome(messages=original, skipped_reason="disabled")
        if exact_context:
            return HeadroomOutcome(messages=original, skipped_reason="exact_context")

        input_chars = sum(len(str(message.get("content", ""))) for message in original)
        if input_chars > self._cfg.rms_headroom_max_input_chars:
            return HeadroomOutcome(messages=original, skipped_reason="oversize")

        compressor = self._get_compressor()
        if compressor is None:
            return HeadroomOutcome(
                messages=original,
                attempted=True,
                failed=True,
                skipped_reason="unavailable",
            )

        try:
            result = compressor(
                original,
                model=model,
                compress_user_messages=True,
                compress_system_messages=False,
                target_ratio=self._cfg.rms_headroom_target_ratio,
                protect_recent=0,
                protect_analysis_context=True,
                min_tokens_to_compress=self._cfg.rms_headroom_min_tokens_to_compress,
                kompress_model=self._cfg.rms_headroom_kompress_model,
            )
            candidate = self._validated_messages(getattr(result, "messages", None), original)
            tokens_before = self._safe_int(getattr(result, "tokens_before", 0))
            tokens_after = self._safe_int(getattr(result, "tokens_after", 0))
            tokens_saved = max(0, self._safe_int(getattr(result, "tokens_saved", 0)))
            transforms_raw = getattr(result, "transforms_applied", ())
            transforms = tuple(str(item) for item in transforms_raw or ())

            # RAMS does not expose Headroom's CCR retrieval tool to its OpenRouter
            # models. Never forward a compression result that asks the model to
            # retrieve omitted source material; without that tool the transform
            # would be effectively lossy from RAMS's point of view.
            if self._contains_retrieval_marker(candidate):
                return HeadroomOutcome(
                    messages=original,
                    attempted=True,
                    tokens_before=tokens_before,
                    tokens_after=tokens_before or tokens_after,
                    transforms=transforms,
                    skipped_reason="retrieval_marker",
                )

            # Headroom already has an inflation guard. Keep a local guard as part
            # of RAMS's own trust boundary in case a future API version regresses.
            if tokens_before > 0 and tokens_after > tokens_before:
                logger.warning(
                    "headroom_optimizer: token inflation detected (%d -> %d); using originals",
                    tokens_before,
                    tokens_after,
                )
                return HeadroomOutcome(
                    messages=original,
                    attempted=True,
                    tokens_before=tokens_before,
                    tokens_after=tokens_before,
                    transforms=transforms,
                    skipped_reason="inflation_guard",
                )

            # Never accept a context rewrite unless Headroom can demonstrate a
            # positive token reduction. This also neutralises silent no-op/error
            # results that report zero token measurements.
            if candidate != original and tokens_saved <= 0:
                return HeadroomOutcome(
                    messages=original,
                    attempted=True,
                    tokens_before=tokens_before,
                    tokens_after=tokens_before or tokens_after,
                    transforms=transforms,
                    skipped_reason="no_verified_saving",
                )

            compressed = candidate != original and tokens_saved > 0
            return HeadroomOutcome(
                messages=candidate,
                attempted=True,
                compressed=compressed,
                tokens_before=tokens_before,
                tokens_after=tokens_after,
                tokens_saved=tokens_saved,
                transforms=transforms,
            )
        except Exception as exc:
            logger.warning(
                "headroom_optimizer: compression failed; using original messages: %s", exc
            )
            return HeadroomOutcome(
                messages=original,
                attempted=True,
                failed=True,
                skipped_reason="error",
            )

    def _get_compressor(self) -> Callable[..., Any] | None:
        if self._compressor is not None:
            return self._compressor
        if self._load_attempted:
            return None
        self._load_attempted = True
        try:
            module = import_module("headroom")
            compressor = getattr(module, "compress")
            if not callable(compressor):
                raise TypeError("headroom.compress is not callable")
            self._compressor = compressor
        except (ImportError, AttributeError, TypeError) as exc:
            logger.error(
                "headroom_optimizer: Headroom unavailable; OpenRouter requests will continue uncompressed: %s",
                exc,
            )
            return None
        return self._compressor

    @staticmethod
    def _validated_messages(
        candidate: object, original: list[dict[str, str]]
    ) -> list[dict[str, str]]:
        """Accept only the OpenAI-compatible role/content shape RAMS sends."""
        if not isinstance(candidate, list) or len(candidate) != len(original):
            raise ValueError("Headroom returned an invalid message list")
        validated: list[dict[str, str]] = []
        for index, raw in enumerate(candidate):
            if not isinstance(raw, Mapping):
                raise ValueError("Headroom returned a non-object message")
            role = str(raw.get("role", ""))
            content = raw.get("content")
            if role != original[index]["role"] or not isinstance(content, str):
                raise ValueError("Headroom changed the RAMS message contract")
            # System messages are configured as protected. Verify that invariant
            # locally rather than trusting a third-party transform blindly.
            if role == "system" and content != original[index]["content"]:
                raise ValueError("Headroom altered a protected system message")
            validated.append({"role": role, "content": content})
        return validated

    @staticmethod
    def _contains_retrieval_marker(messages: list[dict[str, str]]) -> bool:
        """Reject CCR output that RAMS cannot retrieve during a model call."""
        marker_fragments = ("<<ccr:", "retrieve more: hash=", "headroom_retrieve")
        return any(
            any(fragment in message.get("content", "").lower() for fragment in marker_fragments)
            for message in messages
        )

    @staticmethod
    def _safe_int(value: object) -> int:
        if not isinstance(value, (str, bytes, bytearray, int, float)):
            return 0
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError, OverflowError):
            return 0
