"""
Shared LiteLLM client helper.

Both LLM-backed identifiers (`identifiers/llm_name_role.py`,
`identifiers/llm_transcript_role.py`) need the exact same thing: call
an LLM with a structured-output contract and get back either a parsed
pydantic model or `None`, never an exception. Centralizing that here
means:

  - one place to point at a different provider/model (`LLM_MODEL` env
    var, default `fireworks_ai/accounts/fireworks/models/deepseek-v4-flash`
    - confirmed via `litellm.supports_response_schema()` to support
    strict JSON-schema-constrained output),
  - one fail-open policy: missing API key, timeout, malformed/empty
    response, schema-validation failure - all of these collapse to
    `None`, logged once as a warning, never raised. An identifier that
    can't reach its LLM must behave exactly like an identifier that
    simply has nothing to say this tick, never like a crashed
    subsystem - the rest of the belief pipeline (and every other
    identifier) must keep running regardless of API outages or a
    missing key in dev.
  - one small in-process response cache, keyed on (model, prompt
    hash), so an identifier that gets invoked repeatedly with
    unchanged input (e.g. the same short transcript window, or the
    same display name re-evaluated on a later heartbeat-triggered
    recompute) doesn't re-spend a real API call on a question it
    already asked. Deliberately unbounded-but-small: a single interview
    session only ever asks a bounded number of distinct questions, this
    is not meant to survive across sessions or be a real cache service.

Nothing here is Sherlock-specific beyond the env var names - this
could be lifted out to its own package if a second engine needed it.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Optional, Type, TypeVar

from pydantic import BaseModel

logger = logging.getLogger("engine.llm_client")

T = TypeVar("T", bound=BaseModel)

DEFAULT_MODEL = "fireworks_ai/accounts/fireworks/models/deepseek-v4-flash"
DEFAULT_TIMEOUT_SECONDS = 8.0

# Per-process cache: (model, sha256(system+user+schema-name)) -> parsed
# model instance (or the sentinel meaning "asked, got nothing usable").
# Not an LRU on purpose - see module docstring on why unbounded is fine
# at this scale.
_response_cache: dict[str, Optional[BaseModel]] = {}


def _model_name() -> str:
    return os.environ.get("LLM_MODEL", DEFAULT_MODEL)


def _timeout_seconds() -> float:
    try:
        return float(os.environ.get("LLM_REQUEST_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS))
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS


def _cache_key(model: str, system_prompt: str, user_prompt: str, schema: Type[BaseModel]) -> str:
    h = hashlib.sha256()
    h.update(model.encode())
    h.update(b"\0")
    h.update(system_prompt.encode())
    h.update(b"\0")
    h.update(user_prompt.encode())
    h.update(b"\0")
    h.update(schema.__name__.encode())
    return h.hexdigest()


async def structured_completion(
    *,
    system_prompt: str,
    user_prompt: str,
    schema: Type[T],
    use_cache: bool = True,
) -> Optional[T]:
    """Call the configured LLM, constrained to `schema`, and return a
    validated instance of it - or `None` on ANY failure (no API key
    configured, network/timeout error, provider error, or a response
    that doesn't validate against `schema`). Callers are expected to
    treat `None` exactly like "no evidence to emit this tick" - this
    function itself never raises.
    """
    model = _model_name()
    cache_key = _cache_key(model, system_prompt, user_prompt, schema)

    if use_cache and cache_key in _response_cache:
        cached = _response_cache[cache_key]
        return cached  # type: ignore[return-value]

    try:
        import litellm
    except ImportError:
        logger.warning("litellm not installed; skipping LLM call for schema=%s", schema.__name__)
        return None

    try:
        response = await litellm.acompletion(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format=schema,
            timeout=_timeout_seconds(),
            temperature=0.0,
        )
    except Exception:
        logger.warning("LLM call failed for schema=%s (model=%s)", schema.__name__, model, exc_info=True)
        if use_cache:
            _response_cache[cache_key] = None
        return None

    try:
        raw_content = response.choices[0].message.content
        if not raw_content:
            raise ValueError("empty completion content")
        parsed = schema.model_validate(json.loads(raw_content))
    except Exception:
        logger.warning(
            "LLM response failed to parse/validate for schema=%s (model=%s)",
            schema.__name__,
            model,
            exc_info=True,
        )
        if use_cache:
            _response_cache[cache_key] = None
        return None

    if use_cache:
        _response_cache[cache_key] = parsed
    return parsed
