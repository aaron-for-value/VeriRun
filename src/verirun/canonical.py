"""Canonical serialization and content identity helpers."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel


class CanonicalizationError(ValueError):
    """Raised when a value cannot be represented by VeriRun canonical JSON."""


def _normalize(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _normalize(value.model_dump(mode="python", exclude_none=False))
    if isinstance(value, Enum):
        return _normalize(value.value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise CanonicalizationError("naive datetimes are not canonical")
        normalized = value.astimezone(UTC).isoformat(timespec="microseconds")
        return normalized.replace("+00:00", "Z")
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise CanonicalizationError("canonical JSON mappings require string keys")
        return {key: _normalize(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_normalize(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise CanonicalizationError(f"unsupported canonical JSON type: {type(value).__name__}")


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize a value to deterministic UTF-8 JSON without trailing whitespace."""

    try:
        payload = json.dumps(
            _normalize(value),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise CanonicalizationError(str(exc)) from exc
    return payload.encode("utf-8")


def sha256_bytes(payload: bytes) -> str:
    """Return the lowercase SHA-256 digest for bytes."""

    return hashlib.sha256(payload).hexdigest()


def content_hash(value: Any) -> str:
    """Return the SHA-256 identity of a canonical JSON value."""

    return sha256_bytes(canonical_json_bytes(value))


def write_canonical_json(path: Path, value: Any) -> None:
    """Atomically write canonical JSON followed by one newline."""

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical_json_bytes(value) + b"\n"
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)
