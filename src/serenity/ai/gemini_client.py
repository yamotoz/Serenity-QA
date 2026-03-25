"""Gemini API client — thin async wrapper with retry, rate-limiting, and
multimodal support.

The official ``google.generativeai`` SDK is synchronous; we bridge to async
via :func:`asyncio.to_thread` so the event-loop is never blocked.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("serenity.ai.gemini")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MODEL_CANDIDATES = ("gemini-2.0-flash", "gemini-1.5-flash")
_MAX_RETRIES = 3
_BACKOFF_SECONDS = (2, 4, 8)
_RATE_LIMIT_RPM = 15  # requests per minute


class GeminiClient:
    """Async-friendly wrapper around the Google Generative AI SDK.

    Features
    --------
    * Automatic model selection (prefers ``gemini-2.0-flash``, falls back to
      ``gemini-1.5-flash``).
    * Multimodal calls — text-only or image + text.
    * Exponential-backoff retry (3 attempts).
    * Simple token-bucket rate-limiter (max 15 RPM).
    """

    def __init__(self, api_key: str, *, model_name: str | None = None) -> None:
        self._api_key = api_key
        self._model_name = model_name
        self._model: Any = None  # lazy-initialised
        self._init_lock = asyncio.Lock()

        # Rate-limiting state
        self._request_timestamps: list[float] = []
        self._rate_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Lazy initialisation
    # ------------------------------------------------------------------

    async def _ensure_model(self) -> Any:
        """Configure the SDK and resolve the generative model (once)."""
        if self._model is not None:
            return self._model

        async with self._init_lock:
            # Double-check after acquiring the lock
            if self._model is not None:
                return self._model

            import google.generativeai as genai

            genai.configure(api_key=self._api_key)

            chosen = self._model_name
            if chosen is None:
                # Try preferred model first, fall back gracefully
                for candidate in _MODEL_CANDIDATES:
                    try:
                        model = genai.GenerativeModel(candidate)
                        # Quick validation — list_models is cheap
                        await asyncio.to_thread(
                            lambda m=model: m.count_tokens("ping")
                        )
                        chosen = candidate
                        break
                    except Exception:
                        logger.debug("Model %s unavailable, trying next", candidate)
                        continue

                if chosen is None:
                    # Last resort — use the first candidate anyway
                    chosen = _MODEL_CANDIDATES[0]

            self._model = genai.GenerativeModel(chosen)
            logger.info("Gemini client initialised with model %s", chosen)
            return self._model

    # ------------------------------------------------------------------
    # Rate limiting
    # ------------------------------------------------------------------

    async def _wait_for_rate_limit(self) -> None:
        """Block until we are within the RPM budget."""
        async with self._rate_lock:
            now = time.monotonic()
            # Purge timestamps older than 60 s
            self._request_timestamps = [
                ts for ts in self._request_timestamps if now - ts < 60.0
            ]
            if len(self._request_timestamps) >= _RATE_LIMIT_RPM:
                oldest = self._request_timestamps[0]
                wait = 60.0 - (now - oldest) + 0.1  # small buffer
                if wait > 0:
                    logger.debug("Rate limit reached — sleeping %.1fs", wait)
                    await asyncio.sleep(wait)
            self._request_timestamps.append(time.monotonic())

    # ------------------------------------------------------------------
    # Core generation
    # ------------------------------------------------------------------

    async def generate(
        self,
        prompt: str,
        image_path: str | None = None,
    ) -> str:
        """Send a prompt (optionally with an image) and return the text reply.

        Parameters
        ----------
        prompt:
            The text prompt to send.
        image_path:
            Optional path to an image file for multimodal requests.

        Returns
        -------
        str
            The model's text response, or an empty string on failure.
        """
        model = await self._ensure_model()
        contents = self._build_contents(prompt, image_path)

        last_error: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                await self._wait_for_rate_limit()
                response = await asyncio.to_thread(
                    model.generate_content, contents
                )
                text: str = response.text or ""
                return text.strip()
            except Exception as exc:
                last_error = exc
                if attempt < _MAX_RETRIES - 1:
                    delay = _BACKOFF_SECONDS[attempt]
                    logger.warning(
                        "Gemini request failed (attempt %d/%d): %s — retrying in %ds",
                        attempt + 1,
                        _MAX_RETRIES,
                        exc,
                        delay,
                    )
                    await asyncio.sleep(delay)

        logger.error("Gemini request failed after %d attempts: %s", _MAX_RETRIES, last_error)
        return ""

    async def generate_json(
        self,
        prompt: str,
        image_path: str | None = None,
    ) -> dict[str, Any]:
        """Generate a response and parse it as JSON.

        The model is asked for JSON but sometimes wraps its output in markdown
        fences — we strip those before parsing.  On parse failure an empty dict
        is returned so callers never see raw exceptions.
        """
        raw = await self.generate(prompt, image_path)
        if not raw:
            return {}

        return self._parse_json_response(raw)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_contents(prompt: str, image_path: str | None) -> list[Any]:
        """Build the ``contents`` list for the generative model."""
        if image_path is None:
            return [prompt]

        path = Path(image_path)
        if not path.exists():
            logger.warning("Image not found at %s — sending text-only", image_path)
            return [prompt]

        try:
            import PIL.Image

            img = PIL.Image.open(path)
            return [prompt, img]
        except Exception:
            logger.warning("Failed to load image %s — sending text-only", image_path)
            return [prompt]

    @staticmethod
    def _parse_json_response(raw: str) -> dict[str, Any]:
        """Parse a JSON response, handling markdown fences and minor quirks."""
        text = raw.strip()

        # Strip markdown code fences (```json ... ``` or ``` ... ```)
        if text.startswith("```"):
            lines = text.split("\n")
            # Remove first line (```json or ```)
            lines = lines[1:]
            # Remove last line if it's a closing fence
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()

        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
            if isinstance(parsed, list):
                return {"items": parsed}
            return {"value": parsed}
        except json.JSONDecodeError as exc:
            logger.warning("Failed to parse Gemini JSON response: %s", exc)
            logger.debug("Raw response was: %.500s", raw)
            return {}
