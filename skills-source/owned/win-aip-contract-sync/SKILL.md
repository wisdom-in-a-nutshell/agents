---
name: win-aip-contract-sync
description: Maintains contracts for actual aipodcasting frontend consumers of WIN job paths and public response DTOs. Use when wiring or removing a frontend feature, changing a consumed API contract, investigating generated-contract drift, or deciding whether a WIN change needs frontend work. Backend-only provider, model, or schema changes do not require frontend sync.
---

# WIN AIP Contract Sync

## Consumer boundary

WIN owns the complete backend API. AIP exports only what its application code
consumes. Before syncing, identify the actual frontend caller or type import;
entries in generated files and documentation examples are not consumers.

- A backend-only change, such as adding MAI transcription or changing a model's
  settings, requires no AIP edits, generation, or frontend checks when no frontend
  feature consumes that contract. Continue the backend task.
- A new frontend job caller adds its path to
  `aipodcasting/lib/aip/job-endpoints.json`; removal of the last caller removes it.
  This list is intentional frontend scope, not a copy of WIN's endpoint registry.
- A public DTO belongs in WIN's `scripts/contracts/export_public_dto_contracts.py`
  `PUBLIC_DTOS` only when frontend code imports that generated type. Remove unused
  exports with their last imports; keep backend response models needed by WIN.

## Workflow

1. Inspect both repos' `AGENTS.md`, git status, and the concrete caller/import.
   Preserve unrelated work. See WIN's `docs/references/api-endpoint-implementation.md`
   and AIP's `docs/references/aip-backend-integration.md` for exact contracts.
2. Update the owning backend endpoint or public DTO and its real frontend caller.
   Keep DB models internal and map to public DTOs explicitly. AIP synchronous
   routes remain thin HTTP proxies; do not add frontend database access.
3. Update the consumer list or `PUBLIC_DTOS` only as required by those callers.
   Submit jobs through `submitJobById`/`submitJob`. Do not add handwritten proxy
   allowlist exceptions, arbitrary raw-path wrappers, or type casts to skip sync.
4. If a consumed contract changed, run from WIN:
   ```bash
   scripts/local/sync_aip_contracts.sh
   ```
   It validates listed paths against registered POST jobs, generates their path
   constants and proxy allowlist, and refreshes selected public DTOs. Missing or
   duplicate paths fail export. It does not copy all backend request/result
   schemas or generate an unused job-type enum.
5. Inspect the generated diff. Do not hand-edit `aipodcasting/lib/aip/contracts/**`.
   Endpoint IDs are path slugs: strip slashes and replace `/` and `-` with `_`.
   Read the generated ID rather than guessing. Do not invent frontend work when
   sync produces no relevant change.
6. Validate the change:
   - Run focused WIN tests for changed handlers/exporters.
   - Run `scripts/local/check_aip_contracts_drift.sh` to check generated output
     without rewriting AIP.
   - When frontend code or consumed types changed, run AIP `pnpm type-check`,
     `pnpm lint`, and relevant feature/proxy tests.
   - Report the consumer affected and verification. Do not claim frontend schema
     enforcement: the generic submit helper's payload is `unknown`; WIN validates it.

## Cleanup and delivery

Prune exports from their owning consumer list/generator, then regenerate; deleting
only generated output leaves the cause in place. Prefer a focused behavioral test
that prevents recurrence, such as backend-only changes leaving frontend output
identical or the proxy rejecting an unconsumed path. Keep details in the owning
repo docs. Use normal repo lifecycle automation for git sync; do not commit or
push manually unless explicitly requested.
