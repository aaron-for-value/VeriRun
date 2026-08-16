from __future__ import annotations

from datetime import UTC, datetime

import pytest

from verirun.canonical import (
    CanonicalizationError,
    canonical_json_bytes,
    content_hash,
)


def test_mapping_order_does_not_change_hash() -> None:
    left = {"b": [2, 1], "a": {"value": True}}
    right = {"a": {"value": True}, "b": [2, 1]}

    assert canonical_json_bytes(left) == canonical_json_bytes(right)
    assert content_hash(left) == content_hash(right)


def test_datetime_is_normalized_to_utc() -> None:
    value = {"at": datetime(2026, 7, 22, 12, 30, tzinfo=UTC)}

    assert canonical_json_bytes(value) == b'{"at":"2026-07-22T12:30:00.000000Z"}'


def test_naive_datetime_is_rejected() -> None:
    with pytest.raises(CanonicalizationError, match="naive datetimes"):
        canonical_json_bytes({"at": datetime(2026, 7, 22, 12, 30)})


def test_non_finite_number_is_rejected() -> None:
    with pytest.raises(CanonicalizationError):
        canonical_json_bytes({"score": float("nan")})
