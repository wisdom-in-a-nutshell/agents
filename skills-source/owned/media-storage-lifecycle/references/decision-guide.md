# Media Storage Decision Guide

Use this reference for current portfolio examples and the canonical documents
that own exact implementation details. The `SKILL.md` decision table remains
the invariant; verify time-sensitive retention settings in the owning system.

## Current Patterns

| Workflow | Correct authority | Why |
| --- | --- | --- |
| Same-function FFmpeg segments and transforms | Container-local | No serialized consumer exists |
| Cross-container Modal processing and bounded retry | Modal internal artifact | Typed, validated handoff without a public S3 intermediate |
| Existing URL-based ingestion | S3 `cache/` plus original provenance | Current Source consumers require HTTP URLs; this is an implementation constraint, not a rule to upload every input |
| Proposed ingestion for Modal-only consumers | Modal internal artifact plus Source/cache linkage | Keep provenance in WIN and bytes near compute; migrate required Source URL consumers together |
| Independent media jobs returning browser-readable output | S3 `cache/` | Public transport is required; the result is reproducible |
| Ghost feature images | Ghost media library | Ghost is the durable publisher; S3 is only temporary input when needed |
| YouTube video/thumbnail publication | Direct YouTube upload | YouTube owns the published bytes |
| Transistor episode audio | Modal internal artifact streamed to Transistor's authorized URL | Transistor owns the published audio and WIN needs no S3 MP3 |
| Active ad creatives | `permanent/ads/<source_id>/...` | Durable mutable inventory with Source ownership and deletion lifecycle |
| Shared/channel transitions and show assets | `permanent/channel-assets/<owner>/...` | Durable reusable product assets outside campaign lifecycle |
| Generic browser or agent upload | S3 `cache/` | No durable owner exists |
| Remotion remote-input reuse | Modal `/cache/win-remotion-assets` | Performance-only cache; entries unused for 72 hours are pruned and redownloaded |
| Financial invoice record | Explicit owned `permanent/` exception | Legal/financial retention is intentional, not a media-processing default |

## Canonical Sources

| Repo | Read for |
| --- | --- |
| `win` | `docs/architecture/media-source-identity-and-storage.md`, `docs/references/media-processing-reference.md`, `publishing-workflows-reference.md`, `ingest-orchestration-reference.md`, and `content-creation-reference.md` |
| `modal_functions` | `docs/references/media-transfer-policy.md`, `media-artifact-transport.md`, `modal-cache-policy.md`, and `cache-volume-consistency.md` |
| `aipodcasting` | `docs/references/aip-backend-integration.md` and the consuming feature's contract |
| `scripts` | `docs/references/media-upload.md` for the machine-local shared uploader |
| `adithyan-ai-videos` | `docs/setup/cloud-render-modal.md` and `docs/references/media-storage.md` |

Always apply the active repo's `AGENTS.md` before these paths. If a referenced
document moves, follow that repo's docs index instead of adding a duplicate copy
to this skill.

## Failure Patterns To Reject

- Upload every intermediate to S3 because the helper already returns a URL.
- Use `permanent/` to avoid handling expiry.
- Store an expiring URL in Mongo and later treat the string as proof the object
  exists.
- Keep a final S3 copy after Ghost, YouTube, or Transistor owns the published
  media without naming another consumer.
- Replace a database URL and leave the prior exclusively owned object behind.
- Delete an object before the new authoritative reference commits.
- Let a generic frontend component choose raw lifecycle prefixes.
- Add a persistent Modal cache directory without age-bounding it.
- Treat prefix membership alone as enough evidence that an object is orphaned.

## When The Choice Is Unclear

Prefer the shortest-lived boundary and ask only for the missing product fact:
who must access the bytes, from where, and for how long? Do not select
`permanent/` merely to remove uncertainty.
