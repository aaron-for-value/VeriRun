"""S3-compatible content-addressed artifact storage for the M3 control plane."""

from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO
from typing import Any

from minio import Minio
from minio.error import S3Error

from verirun.artifacts import ArtifactIntegrityError
from verirun.canonical import canonical_json_bytes, sha256_bytes
from verirun.control_plane import ArtifactMetadataRecord
from verirun.models import ArtifactRef


class S3ArtifactStore:
    """Store immutable artifacts by SHA-256 in an S3-compatible bucket."""

    def __init__(self, client: Minio, bucket: str, *, create_bucket: bool = False) -> None:
        if not bucket:
            raise ValueError("bucket cannot be empty")
        self.client = client
        self.bucket = bucket
        if create_bucket and not self.client.bucket_exists(bucket):
            self.client.make_bucket(bucket)

    @classmethod
    def connect(
        cls,
        *,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        secure: bool = True,
        create_bucket: bool = False,
    ) -> S3ArtifactStore:
        client = Minio(
            endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
        )
        return cls(client, bucket, create_bucket=create_bucket)

    @staticmethod
    def _object_name(digest: str) -> str:
        return f"sha256/{digest[:2]}/{digest}"

    def _read_object(self, object_name: str) -> bytes:
        try:
            response = self.client.get_object(self.bucket, object_name)
        except S3Error as exc:
            if exc.code in {"NoSuchKey", "NoSuchObject", "NoSuchBucket"}:
                raise ArtifactIntegrityError(f"missing S3 artifact {object_name}") from exc
            raise
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()

    def put_bytes(
        self,
        *,
        kind: str,
        payload: bytes,
        media_type: str,
        now: datetime | None = None,
    ) -> tuple[ArtifactRef, ArtifactMetadataRecord]:
        digest = sha256_bytes(payload)
        object_name = self._object_name(digest)
        try:
            self.client.stat_object(self.bucket, object_name)
        except S3Error as exc:
            if exc.code not in {"NoSuchKey", "NoSuchObject"}:
                raise
            self.client.put_object(
                self.bucket,
                object_name,
                BytesIO(payload),
                len(payload),
                content_type=media_type,
            )
        else:
            existing = self._read_object(object_name)
            if existing != payload:
                raise ArtifactIntegrityError(f"artifact collision at {object_name}")

        reference = ArtifactRef(
            kind=kind,
            sha256=digest,
            size_bytes=len(payload),
            media_type=media_type,
            relative_path=object_name,
        )
        metadata = ArtifactMetadataRecord(
            sha256=digest,
            kind=kind,
            size_bytes=len(payload),
            media_type=media_type,
            storage_uri=f"s3://{self.bucket}/{object_name}",
            created_at=now or datetime.now(UTC),
        )
        return reference, metadata

    def put_text(
        self,
        *,
        kind: str,
        text: str,
        media_type: str = "text/plain",
        now: datetime | None = None,
    ) -> tuple[ArtifactRef, ArtifactMetadataRecord]:
        return self.put_bytes(
            kind=kind,
            payload=text.encode("utf-8"),
            media_type=media_type,
            now=now,
        )

    def put_json(
        self,
        *,
        kind: str,
        value: Any,
        now: datetime | None = None,
    ) -> tuple[ArtifactRef, ArtifactMetadataRecord]:
        return self.put_bytes(
            kind=kind,
            payload=canonical_json_bytes(value) + b"\n",
            media_type="application/json",
            now=now,
        )

    def read_bytes(self, reference: ArtifactRef) -> bytes:
        expected_name = self._object_name(reference.sha256)
        if reference.relative_path != expected_name:
            raise ArtifactIntegrityError("artifact path does not match its content digest")
        payload = self._read_object(expected_name)
        if len(payload) != reference.size_bytes:
            raise ArtifactIntegrityError(f"size mismatch for artifact {reference.sha256}")
        actual = sha256_bytes(payload)
        if actual != reference.sha256:
            raise ArtifactIntegrityError(
                f"hash mismatch for artifact {reference.sha256}: got {actual}"
            )
        return payload

    def read_text(self, reference: ArtifactRef) -> str:
        return self.read_bytes(reference).decode("utf-8")

    def verify(self, reference: ArtifactRef) -> None:
        self.read_bytes(reference)
