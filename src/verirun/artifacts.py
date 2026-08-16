"""Content-addressed local artifact storage for v0.1."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from verirun.canonical import canonical_json_bytes, sha256_bytes
from verirun.models import ArtifactRef


class ArtifactIntegrityError(RuntimeError):
    """Raised when stored artifact bytes do not match their reference."""


class ArtifactStore:
    """A local SHA-256 store; S3-compatible storage is intentionally deferred."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def put_bytes(self, *, kind: str, payload: bytes, media_type: str) -> ArtifactRef:
        digest = sha256_bytes(payload)
        relative = Path("sha256") / digest[:2] / digest
        destination = self.root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            existing = destination.read_bytes()
            if existing != payload:
                raise ArtifactIntegrityError(f"artifact collision at {relative.as_posix()}")
        else:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{digest}.", dir=destination.parent
            )
            temporary = Path(temporary_name)
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
        return ArtifactRef(
            kind=kind,
            sha256=digest,
            size_bytes=len(payload),
            media_type=media_type,
            relative_path=relative.as_posix(),
        )

    def put_text(self, *, kind: str, text: str, media_type: str = "text/plain") -> ArtifactRef:
        return self.put_bytes(kind=kind, payload=text.encode("utf-8"), media_type=media_type)

    def put_json(self, *, kind: str, value: Any) -> ArtifactRef:
        return self.put_bytes(
            kind=kind,
            payload=canonical_json_bytes(value) + b"\n",
            media_type="application/json",
        )

    def read_bytes(self, reference: ArtifactRef) -> bytes:
        path = (self.root / reference.relative_path).resolve()
        if not path.is_relative_to(self.root):
            raise ArtifactIntegrityError("artifact reference escaped store root")
        try:
            payload = path.read_bytes()
        except FileNotFoundError as exc:
            raise ArtifactIntegrityError(f"missing artifact {reference.sha256}") from exc
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
