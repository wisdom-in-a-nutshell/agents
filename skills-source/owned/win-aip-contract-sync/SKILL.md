---
name: win-aip-contract-sync
description: Maintains the local WIN-to-aipodcasting interface with automatically generated job paths and frontend-imported public response DTOs. Use when job paths change, a frontend feature consumes a changed API contract, generated output drifts, or deciding whether a backend change needs frontend work. Backend-only provider/model/request changes do not require frontend generation.
---

# WIN AIP Contract Sync

## Boundary

WIN owns the complete API and validates requests/responses. AIP needs a small
job-path catalog plus the public response types its code imports. Keep path
discovery automatic; do not introduce a handwritten list or flags marking which
APIs the frontend needs. Some unused generated path names are acceptable.

Detailed job schemas remain in WIN's API/OpenAPI rather than an unused full JSON
copy in AIP. A model/provider change such as adding MAI transcription, with no
path or frontend-consumed DTO change, requires no frontend generation, edits, or
checks. Continue the backend task.

## Workflow

1. Read both repos' guidance and inspect their git status. Trace the affected
   interface: frontend caller/import, proxy, WIN handler, and response model.
   Generated-file entries and documentation examples are not actual consumers.
2. Keep backend database models internal and map to public DTOs explicitly.
   `PUBLIC_DTOS` in WIN's `scripts/contracts/export_public_dto_contracts.py` lists
   response types actually imported by frontend code. Do not remove backend
   response models merely because their generated frontend copies are unused.
3. When registered job paths/identities or frontend-imported DTOs change, run
   from WIN:
   ```bash
   scripts/local/sync_aip_contracts.sh
   ```
   The script reads WIN locally and writes directly into the sibling AIP repo.
   It discovers job paths automatically and refreshes selected public DTOs.
   Git commits, pushes, and remote CI are not part of this file generation.
4. Inspect the diff; do not hand-edit `aipodcasting/lib/aip/contracts/**`.
   Submit frontend jobs through `submitJobById`/`submitJob`, using generated IDs.
   IDs are path slugs: strip slashes and replace `/` and `-` with `_`. Read the
   generated ID rather than guessing. Remove temporary proxy exceptions or
   casts once a registered endpoint is generated. Synchronous AIP routes remain
   thin HTTP proxies; do not add frontend database access.
5. Validate proportionally:
   - Focused WIN tests for changed handlers/exporters.
   - `scripts/local/check_aip_contracts_drift.sh` checks output without rewriting AIP.
   - If frontend code or consumed types changed, run AIP `pnpm type-check`,
     `pnpm lint`, and relevant feature/proxy tests.
   - If backend-only changes leave frontend contracts unchanged, do not create
     frontend work or run unrelated frontend checks.
6. Report the affected interface and evidence. The generic submit helper accepts
   `unknown` payloads; do not claim it provides frontend request-schema validation.
   WIN owns that validation.

## Cleanup and delivery

Verify imports, re-exports, dynamic reads, and request paths before pruning a
file. Remove unused exports from their generator and regenerate, so they do not
return. Keep backend models and active frontend consumers. Prefer small tests
that prove model-only changes leave path output identical and new job paths are
discovered automatically.

Local Git automation handles history and remote backup. Local production
reconciliation builds and activates clean committed releases separately; editing
or generating files does not activate live services. Do not bypass that release
boundary or manually commit/push unless explicitly requested.

Exact contracts and commands live in WIN's
`docs/references/api-endpoint-implementation.md` and AIP's
`docs/references/aip-backend-integration.md`.
