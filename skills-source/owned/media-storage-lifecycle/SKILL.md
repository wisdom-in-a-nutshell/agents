---
name: media-storage-lifecycle
description: Use when adding, changing, reviewing, or cleaning media and object storage across WIN, modal_functions, aipodcasting, Remotion, local transcription, agent media tooling, or shared upload scripts. Covers S3 cache/share/permanent choices, Modal local/internal artifacts and persistent caches, browser uploads, provider-owned handoffs, object ownership and keys, expiry and reacquisition, replacement/deletion cleanup, orphan prevention, lifecycle rules, and storage audits.
---

# Media Storage Lifecycle

## Purpose

Choose the shortest-lived storage boundary that satisfies the next real
consumer, and make ownership, expiry, replacement, and deletion explicit. This
skill is the cross-repo routing contract; implementation details remain in each
owning repository's docs.

The current shared S3 provider is native Versity on the Mac mini, bucket `assets`,
with public base `https://storage.aipodcast.ing/assets`. Existing R2 originals are
retained only for deliberate recovery, not a runtime fallback. Prefixes express
ownership and retention intent; verify the active cleanup implementation instead
of assuming the former R2 lifecycle rules still run on native storage.

## Required Workflow

1. Read the active repo's `AGENTS.md` and the relevant storage references listed
   in [references/decision-guide.md](references/decision-guide.md).
2. Name the next consumer before choosing storage:
   - same process/container;
   - another Modal container;
   - WIN or another background job;
   - browser or HTTP-only provider;
   - user-facing deliverable;
   - durable product inventory; or
   - external provider that becomes authoritative.
3. Choose the first target in the decision table that satisfies that consumer.
4. Define an owner and stable key family for any public object. A lifecycle
   prefix alone is not ownership.
5. Define what happens when the object expires, is replaced, or its owner is
   deleted.
6. Add focused proof for the selected boundary and update the owning repo's
   durable docs when behavior changes.

## Storage Decision Table

| Boundary | Use when | Required behavior |
| --- | --- | --- |
| Container-local file | Producer and consumer run in the same container invocation | Delete in `finally`; never serialize or return the path remotely |
| Modal internal artifact | Another Modal container or bounded retry needs the bytes | Use typed `MediaArtifactRef`; validate capability/manifest; let the 72-hour artifact lifecycle or exact run cleanup remove it |
| S3 `cache/` | A browser, independent caller, background job, or HTTP-only consumer needs a URL | Treat as temporary transport; persist provenance; validate before reuse and reacquire or regenerate when missing |
| S3 `share/` | A deliberately time-bounded, user-facing or cross-machine deliverable must outlive ordinary cache | Require a named product owner and documented expiry; never use as an unowned processing default |
| S3 `permanent/` | The object is canonical durable inventory or a retained product asset | Use an owner-stable key, authoritative reference, replacement cleanup, and owner-deletion cleanup |
| Provider-owned storage | Ghost, YouTube, Transistor, Frame.io, or another destination accepts and owns the final bytes | Upload directly where possible; persist the provider identity needed by the product; do not retain a duplicate S3 final without a separate consumer |

Use local or Modal-internal storage for every intermediate in an all-Modal
subgraph. Materialize publicly only at the first consumer that genuinely needs
an HTTP URL.

WIN orchestration and asynchronous job boundaries can pass persisted artifact
references without reading the bytes. They do not alone justify publication.
Keep Source identity/provenance separate from temporary location; link through
the existing artifact cache rather than storing Modal URIs in HTTP URL fields.
Background uploads still transfer the same bytes: select retained outputs
explicitly. Native S3 on the Mac is still an outbound destination for Modal.
Read Modal's `docs/references/media-transfer-policy.md` for dated billing facts
and WIN's `docs/architecture/media-source-identity-and-storage.md` for the planned
acquisition migration; do not treat the target as an already-shipped schema.

## Ownership And Cleanup Rules

- Generic public uploads default to `cache/`; `share/` and `permanent/` require
  an explicit owning workflow. Internal consumers use internal artifacts.
- Browser APIs expose purpose/upload intents, not raw lifecycle-folder choice.
- Every durable key should identify its owner, for example
  `permanent/ads/<source_id>/...` or
  `permanent/channel-assets/<channel>/...`.
- Replace safely: commit the new authoritative reference first, then delete the
  previous exact managed object only when no other record shares it.
- Delete completely: remove dependent records/configuration and the exact owned
  object; missing media is an idempotent no-op.
- If post-commit cleanup fails, keep the new reference valid, log the orphan,
  and let inventory reporting detect it. Do not roll back to a dead URL.
- A `cache/` or `share/` URL stored in Mongo is never durable merely because the
  string remains. Active work must validate it and reacquire, regenerate, or
  fail with a clear re-upload requirement.
- Preserve original provider/source provenance whenever temporary storage may
  expire.
- Never bulk-delete from a prefix without resolving its exact candidate set and
  protected references first. Record before/after counts and bytes for approved
  destructive cleanup.

## Repo Routing

- For Modal entrypoints or artifact contracts, also use `$modal-function-sync`.
- For WIN/AIP public job or generated frontend contracts, also use
  `$win-aip-contract-sync`.
- Keep cross-repo decisions here; keep exact functions, schemas, paths,
  retention configuration, and commands in the owning repo docs linked from
  the decision guide.
- Put deterministic inventory, orphan detection, and lifecycle validation in
  the owning repository's scripts/checks. A skill teaches the workflow; it is
  not the enforcement mechanism.

## Validation Checklist

- The selected target matches the next consumer and no earlier public boundary
  remains.
- No new function or browser upload defaults to `share/` or `permanent/`.
- Expiring references have validation and recovery behavior.
- Replacement and deletion tests prove ordering and shared-reference safety.
- Provider uploads prove that no unnecessary S3 final copy is created.
- Scheduled cleanup covers new Modal cache namespaces and abandoned multipart
  uploads when applicable.
- Storage behavior is recorded in durable architecture/reference docs, not only
  an active project tracker.
