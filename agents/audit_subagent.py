"""Audit subagent: unified LLM-call wrapper for the 3 reviewers.

Returns a standardized verdict dict. Handles:
- malformed JSON (retry once, then INFRA_ERROR)
- exceptions (timeout / network) → INFRA_ERROR (no retry; exceptions imply
  the LLM call infrastructure is unhealthy, not a transient parse error)
- captures error_type, attempt_count, raw_response_excerpt for audit_log
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 2


def _excerpt(text: str, limit: int = 200) -> str:
    if not isinstance(text, str):
        return repr(text)[:limit]
    return text[:limit]


def call_auditor(
    auditor_name: str,
    prompt: str,
    llm_client: Any,
    model: str = "deepseek-v4-pro",
) -> dict:
    """Call one reviewer subagent. Returns standardized verdict dict.

    Verdict dict always has 'verdict' key (one of APPROVE / REJECT /
    CONCERNS / INFRA_ERROR). On INFRA_ERROR also includes:
      error_type, attempt_count, last_error_at, raw_response_excerpt
    """
    last_raw = ""

    # exceptions: no retry (infra is sick)
    try:
        raw = llm_client.call(auditor_name, system="", user=prompt, model=model)
        last_raw = raw if isinstance(raw, str) else repr(raw)
    except TimeoutError as exc:
        return _infra_error("timeout", 1, _excerpt(str(exc)))
    except Exception as exc:
        return _infra_error(_classify_exception(exc), 1, _excerpt(str(exc)))

    # parse attempt 1
    parsed = _try_parse(last_raw)
    if parsed is not None:
        return _normalize(parsed)

    # retry once on malformed JSON
    try:
        raw2 = llm_client.call(auditor_name, system="", user=prompt, model=model)
        last_raw = raw2 if isinstance(raw2, str) else repr(raw2)
    except TimeoutError as exc:
        return _infra_error("timeout", 2, _excerpt(str(exc)))
    except Exception as exc:
        return _infra_error(_classify_exception(exc), 2, _excerpt(str(exc)))

    parsed = _try_parse(last_raw)
    if parsed is not None:
        return _normalize(parsed)

    return _infra_error("malformed_json", 2, _excerpt(last_raw))


def _try_parse(raw: str):
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        # also try stripping common LLM noise (```json fences, leading/trailing text)
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.lstrip("`").lstrip("json").strip()
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3].strip()
            try:
                return json.loads(cleaned)
            except (json.JSONDecodeError, TypeError):
                pass
        return None


def _normalize(parsed: dict) -> dict:
    """Ensure mandatory fields exist on a successfully-parsed verdict."""
    v = parsed.get("verdict")
    if v not in ("APPROVE", "REJECT", "CONCERNS"):
        # reviewer returned something unexpected — INFRA_ERROR semantics
        return _infra_error("invalid_verdict", 1, repr(v)[:200])
    parsed.setdefault("devil_advocate_points", [])
    parsed.setdefault("hard_reject_hits", [])
    parsed.setdefault("specific_evidence", [])
    return parsed


def _infra_error(error_type: str, attempt_count: int, excerpt: str) -> dict:
    from datetime import datetime, timezone
    return {
        "verdict": "INFRA_ERROR",
        "error_type": error_type,
        "attempt_count": attempt_count,
        "last_error_at": datetime.now(timezone.utc).isoformat(),
        "raw_response_excerpt": excerpt,
    }


def _classify_exception(exc: Exception) -> str:
    name = type(exc).__name__.lower()
    if "timeout" in name:
        return "timeout"
    if "auth" in name or "permission" in name:
        return "auth_failure"
    if "connection" in name or "network" in name:
        return "network"
    return "unknown_exception"
