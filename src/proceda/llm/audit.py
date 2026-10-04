"""ABOUTME: Optional per-attempt LLM audit records without credentials or HTTP headers.
ABOUTME: Publishes write-ahead records exclusively and atomically replaces them on completion."""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PendingAudit:
    """Ownership of one exclusively created audit file, used for a single replacement."""

    path: Path
    device: int
    inode: int


def publish_record(
    path: Path, record: dict[str, Any], pending: PendingAudit | None = None
) -> PendingAudit:
    """Publish a complete JSON record without overwriting another call's audit.

    A temporary file is fully flushed before publication. Replacement requires the
    exact file created by the caller; a collision or repeated finalization fails.
    """
    payload = json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=".audit-", suffix=".tmp", delete=False
    ) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        if pending is None:
            os.link(temporary, path)  # Atomic publication, with exclusive-create semantics.
        else:
            current = path.stat()
            if pending.path != path or (current.st_dev, current.st_ino) != (
                pending.device,
                pending.inode,
            ):
                raise FileExistsError("Audit ownership changed; refusing replacement")
            os.replace(temporary, path)
        published = path.stat()
        return PendingAudit(path, published.st_dev, published.st_ino)
    finally:
        temporary.unlink(missing_ok=True)


_PRIVATE_FIELDS = {
    "api_key",
    "apikey",
    "x_api_key",
    "authorization",
    "proxy_authorization",
    "cookie",
    "set_cookie",
    "headers",
    "request_headers",
    "response_headers",
    "access_token",
    "api_token",
    "refresh_token",
    "id_token",
    "password",
    "secret",
    "client_secret",
}


def sanitize(value: Any, secrets: tuple[str, ...] = ()) -> Any:
    """Redact known credentials and credential/header fields, preserving other data."""
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[REDACTED]")
        return re.sub(
            r"(?i)(\b(?:authorization|proxy-authorization)\s*[:=]\s*"
            r"(?:bearer\s+|basic\s+)?)[^\s,;\"']+",
            r"\1[REDACTED]",
            value,
        )
    if isinstance(value, dict):
        return {
            sanitize(str(key), secrets): "[REDACTED]"
            if (
                str(key).lower().replace("-", "_") in _PRIVATE_FIELDS
                or str(key).lower().replace("-", "_").endswith("_headers")
            )
            else sanitize(item, secrets)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [sanitize(item, secrets) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    # Avoid repr(), which can expose transport objects and their headers.
    return {"unserialized_type": type(value).__name__}


def response_data(response: Any) -> dict[str, Any] | None:
    """Obtain response content, excluding transport object representations."""
    if response is None:
        return None
    if isinstance(response, dict):
        return response
    if callable(getattr(response, "model_dump", None)):
        result = response.model_dump()
        return result if isinstance(result, dict) else None
    if callable(getattr(response, "json", None)):
        try:
            result = response.json()
        except (TypeError, ValueError):
            return None
        return result if isinstance(result, dict) else None
    return None


def usage_data(response: Any, raw: dict[str, Any] | None) -> dict[str, Any] | None:
    """Keep reported usage; absence is unknown rather than a zero-token assertion."""
    usage = raw.get("usage") if raw else None
    if isinstance(usage, dict):
        return usage
    usage = getattr(response, "usage", None)
    if isinstance(usage, dict):
        return usage
    dump = getattr(usage, "model_dump", None)
    if callable(dump):
        return dump()
    if usage is not None:
        fields = ("prompt_tokens", "completion_tokens", "total_tokens")
        result = {name: getattr(usage, name) for name in fields if hasattr(usage, name)}
        return result or None
    return None


def write_attempt(
    directory: str,
    *,
    call_id: str,
    attempt: int,
    started_at: str,
    elapsed_ms: float,
    request: dict[str, Any],
    response: Any = None,
    error: BaseException | None = None,
    secrets: tuple[str, ...] = (),
    started: bool = False,
    pending: PendingAudit | None = None,
) -> PendingAudit:
    """Write an attempt before dispatch, or finalize its one existing record."""
    source = response if response is not None else getattr(error, "response", None)
    raw = response_data(source)
    usage = usage_data(source, raw)
    record = {
        "call_id": call_id,
        "attempt": attempt,
        "started_at": started_at,
        "finished_at": None if started else datetime.now(UTC).isoformat(),
        "elapsed_ms": elapsed_ms,
        "request": request,
        "requested_model": request["model"],
        "response_model": raw.get("model") if raw else getattr(source, "model", None),
        "response": raw,
        "usage": usage,
        "usage_reported": usage is not None,
        "status": "started" if started else "error" if error is not None else "success",
        "cost_known": isinstance(usage, dict)
        and all(
            type(usage.get(key)) is int and usage[key] >= 0
            for key in ("prompt_tokens", "completion_tokens")
        ),
        "error": {"type": type(error).__name__, "message": str(error)}
        if error is not None
        else None,
    }
    path = Path(directory) / f"{call_id}-{attempt:03d}.json"
    return publish_record(path, sanitize(record, secrets), pending)
