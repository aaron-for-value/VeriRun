from __future__ import annotations

from typing import Any, cast

import pytest
from minio.error import S3Error

from verirun.artifacts import ArtifactIntegrityError
from verirun.s3_artifacts import S3ArtifactStore


def missing_error() -> S3Error:
    return S3Error(cast(Any, None), "NoSuchKey", "missing", None, None, None)


class FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.closed = False
        self.released = False

    def read(self) -> bytes:
        return self.payload

    def close(self) -> None:
        self.closed = True

    def release_conn(self) -> None:
        self.released = True


class FakeMinio:
    def __init__(self) -> None:
        self.buckets: set[str] = set()
        self.objects: dict[tuple[str, str], bytes] = {}

    def bucket_exists(self, bucket: str) -> bool:
        return bucket in self.buckets

    def make_bucket(self, bucket: str) -> None:
        self.buckets.add(bucket)

    def stat_object(self, bucket: str, object_name: str) -> object:
        if (bucket, object_name) not in self.objects:
            raise missing_error()
        return object()

    def put_object(
        self,
        bucket: str,
        object_name: str,
        stream: Any,
        length: int,
        *,
        content_type: str,
    ) -> object:
        del content_type
        payload = stream.read()
        assert len(payload) == length
        self.objects[(bucket, object_name)] = payload
        return object()

    def get_object(self, bucket: str, object_name: str) -> FakeResponse:
        try:
            return FakeResponse(self.objects[(bucket, object_name)])
        except KeyError as exc:
            raise missing_error() from exc


def test_s3_store_is_content_addressed_and_idempotent() -> None:
    client = FakeMinio()
    store = S3ArtifactStore(cast(Any, client), "verirun", create_bucket=True)

    reference, metadata = store.put_text(kind="stdout", text="hello")
    repeated, repeated_metadata = store.put_text(kind="stdout", text="hello")

    assert reference == repeated
    assert metadata.sha256 == reference.sha256
    assert repeated_metadata.storage_uri == metadata.storage_uri
    assert store.read_text(reference) == "hello"
    assert len(client.objects) == 1


def test_s3_store_rejects_tampered_content() -> None:
    client = FakeMinio()
    store = S3ArtifactStore(cast(Any, client), "verirun", create_bucket=True)
    reference, _ = store.put_text(kind="stdout", text="hello")
    client.objects[("verirun", reference.relative_path)] = b"tampered"

    with pytest.raises(ArtifactIntegrityError, match="size mismatch"):
        store.read_bytes(reference)
