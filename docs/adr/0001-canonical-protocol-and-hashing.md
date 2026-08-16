# ADR 0001: Canonical protocol models and content hashing

- Status: Accepted for v0.1
- Date: 2026-07-22
- Owners: VeriRun maintainers
- Related issue: #2

## Context

VeriRun must compare results across retries, processes, and future execution backends. Regular JSON serialization does not guarantee key order, datetime representation, non-finite number handling, or immutability. Hashing files by path would also make identity depend on local layout.

## Decision

v0.1 uses:

- immutable Pydantic v2 models with `extra=forbid`;
- schema version strings embedded in top-level protocol records;
- UTF-8 canonical JSON with sorted string keys and compact separators;
- UTC timestamps serialized with microseconds and a `Z` suffix;
- rejection of naive datetimes, non-finite numbers, non-string mapping keys, and unsupported types;
- lowercase SHA-256 over canonical bytes or raw artifact bytes;
- content-addressed artifact paths derived from SHA-256;
- algorithm-qualified external dataset digests (`md5:<hex>` or `sha256:<hex>`) so an upstream checksum is never mislabeled as a VeriRun SHA-256 artifact identity;
- a separate semantic replay payload that excludes attempt-local time and duration fields.

Canonical JSON used for hashing has no trailing newline. JSON files add exactly one newline after the hashed payload; the file artifact hash therefore identifies the complete file bytes, while `content_hash(model)` identifies the protocol value.

## Why Pydantic

Pydantic provides runtime validation, immutable models, closed fields, JSON parsing, and typed nested records without building a custom validation framework. It is the sole v0.1 core runtime dependency.

## Alternatives considered

### Standard-library dataclasses only

Rejected for v0.1 because nested runtime validation, closed-field parsing, and stable error reporting would require a custom layer. Dataclasses remain appropriate for internal trusted fixtures that are not wire contracts.

### Protobuf

Deferred. Protobuf provides strong schema evolution and binary identity but adds code generation and a second representation before the v0.1 field model is stable.

### Hashing formatted JSON files

Rejected because whitespace and writer settings would become semantic. Canonical value bytes and stored file bytes have deliberately distinct identities.

### Including every result field in replay equality

Rejected because attempt ID, timestamps, and duration are expected to differ. VeriRun preserves those fields for audit while comparing a documented semantic projection.

## Consequences

- Schema changes require a version change or compatibility rule.
- All public hash claims can be reproduced from explicit bytes.
- External benchmark digests retain the algorithm exposed by the pinned upstream adapter; VeriRun does not strengthen provenance by relabeling a weaker checksum.
- Artifact corruption is detected on read.
- Cross-language implementations must reproduce the canonicalization rules before claiming compatibility.
- SHA-256 provides content identity, not authenticity; signatures and remote-store trust are future work.
