# Client Setup And Upgrade

Use this when installing or replacing the AI Podcasting skill on a customer's agent machine.

## Upgrade Contract

Customers already using `AIPODCASTING_CLIENT_API_KEY` keep the same credential. This parity
upgrade changes the installed skill and the server-side grant; it does not require a new key.

1. Replace the complete `ai-podcasting` skill directory as one unit. Do not copy only
   `SKILL.md`; the scripts, examples, tests, and metadata are one versioned bundle.
2. Remove any old `AIPODCASTING_API_KEY` or `AIPODCASTING_API_KEYS` entry from the customer's
   AI Podcasting credential file. Those names belonged to the retired frontend proxy contract.
3. Write the customer-scoped credential to `~/.secrets/aipodcasting/env`:

   ```text
   AIPODCASTING_CLIENT_API_KEY=<key-shared-out-of-band>
   ```

4. Set the file mode to owner-only (`chmod 600 ~/.secrets/aipodcasting/env`). Never put the key in
   a command flag, ordinary environment variable, payload file, agent prompt, or chat.
5. Run:

   ```bash
   python3 scripts/ai_podcasting_client.py --json --no-input doctor
   ```

6. Accept the upgrade only when `status` is `ok`, `data.ready` is `true`, `TCR` is in
   `data.allowed_shows`, and the five episode/upload scopes are present, including
   `episodes:copy:write`.

## What Changed

- The skill calls `https://api.aipodcast.ing/client/v1/**` directly.
- It no longer calls `app.aipodcast.ing/api/**` or needs a Cloudflare browser session.
- The credential can access only its configured shows and operations. It cannot call WIN finance,
  personal, jobs, operations, or other internal endpoints.
- Episode submission is idempotent. Preserve the JSON envelope's `meta.request_id` after an
  uncertain result and retry with `--request-id <same-id>`. A new request ID means a deliberately
  new episode submission.
- Local files use purpose-based S3 `cache/` upload intents. These URLs are transport references,
  not permanent client-owned storage.

## Failure Triage

- `E_AUTH`: check the credential file name, key name, mode, and the server-side client grants.
- `E_IDEMPOTENCY_IN_PROGRESS`: retry shortly with the same request ID.
- `E_IDEMPOTENCY_CONFLICT`: the request ID was reused with a different payload; use the original
  payload or intentionally choose a new request ID.
- `E_NETWORK` or `E_UPSTREAM`: keep the same request ID for a submit retry after service recovery.
