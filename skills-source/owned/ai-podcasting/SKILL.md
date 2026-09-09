---
name: ai-podcasting
description: Submit AI Podcasting episodes and update show notes, supporting episode copy, intro, title, and thumbnails through the scoped WIN client API. Use when clients want agent-driven episode operations without the GUI, including checking access, listing TCR episodes with rich metadata and published-state filters, retry-safe episode submission, patching post-creation show notes or intro copy, uploading local inputs, or disambiguating an episode operation.
---

# AI Podcasting

Use this skill for client-facing, agent-driven episode operations in this repository.

## What This Skill Runs

Run the main CLI at `scripts/ai_podcasting_client.py` for episode operations:

1. `doctor`:
   Verify the credential, client identity, TCR show grant, and required operation scopes.
2. `list-episodes`:
   List `TCR` episodes with rich per-episode summaries and filters for `published`, `unpublished`,
   or `all`.
3. `submit-episode`:
   Create a retry-safe new episode via `/client/v1/episodes`.
4. `update-intro-copy`:
   Patch intro/title/thumbnail/outro assets for an existing unpublished episode via
   `/client/v1/episodes/{sourceId}/intro`.
5. `update-episode-copy`:
   Patch `showNotes`, supporting `assetUrls`, or `guests` after an episode already exists via
   `/client/v1/episodes/{sourceId}/copy`.

This skill calls the versioned WIN client API at `https://api.aipodcast.ing/client/v1/...`
directly. Do not send agent operations through the frontend or automate the browser UI.
WIN enforces the authenticated principal's operation scopes and allowed shows server-side.
The scripts read the token only from `~/.secrets/aipodcasting/env` by default, or from the
credential file path selected by `AIPODCASTING_CLIENT_API_KEY_FILE`. They do not accept secret
flags or secret-value environment fallbacks.

## Auth Setup

Never paste the actual API key into this skill, a payload JSON file, a command argument, or chat.
Adi shares the key out-of-band. Store it in the user's local environment before running the client.

Recommended local setup:

```bash
mkdir -p ~/.secrets/aipodcasting
printf 'AIPODCASTING_CLIENT_API_KEY=<key-from-Adi>\n' > ~/.secrets/aipodcasting/env
chmod 600 ~/.secrets/aipodcasting/env
```

If the secret file lives somewhere else, set `AIPODCASTING_CLIENT_API_KEY_FILE` to that absolute
path. This variable selects a file; it never contains the credential itself.
For direct raw API usage, send the same key as `Authorization: Bearer <key-from-Adi>`.
For a customer install or upgrade, follow `references/client-setup.md` and require a successful
`doctor` result before running mutations.

Use `scripts/aip_local_upload_helper.py` only when the user gives a local file path for a file-like
field and no source URL is available. For TCR main episode submissions, prefer a Descript web URL
copied from Descript for the main source when one exists; do not export or upload an MP4 just to
create a source link. The helper requests a purpose-specific temporary upload intent and returns
an S3 `cache/` URL for the main CLI to use. Callers cannot choose a raw lifecycle prefix.
Keep this implicit in chat unless the user asks.

## Media Source Rule For TCR

When a Descript project/composition/source URL exists, use that URL as the source. Do not export,
upload, or submit an MP4 just to create a source file.

The expected Descript web URL shape is:

```text
https://web.descript.com/<project-id>/<composition-id>
```

Copy the actual URL from Descript. Do not fabricate IDs, and do not require an exact path shape if
Descript provides a slightly different `web.descript.com` source URL.

Use these shapes:

```json
{
  "mainSourceUrl": "https://web.descript.com/01234567-89ab-4cde-8f01-23456789abcd"
}
```

For intro updates, use:

```json
{
  "introSourceUrl": "https://web.descript.com/01234567-89ab-4cde-8f01-23456789abcd"
}
```

The client normalizes these source fields to the backend payload internally. Do not send `raw`,
`recordingLink`, or `introFile` in agent payloads.

MP4 URLs and local MP4 paths are fallback inputs only. If both a Descript URL and an MP4 are known,
submit the Descript URL and omit the MP4.

All script and reference paths in this skill are relative to the skill directory itself, not the
repository root. Do not run `scripts/...` from the repo root unless you first `cd` into this skill
directory. When in doubt, use the absolute skill path shown by the harness.

## Quick Start

1. Check the installed credential and grants:

```bash
python3 scripts/ai_podcasting_client.py \
  --json doctor
```

2. List episodes to find the target ID:

```bash
python3 scripts/ai_podcasting_client.py \
  --json list-episodes
```

To include the sanitized upstream episode payload under each item:

```bash
python3 scripts/ai_podcasting_client.py \
  --json list-episodes \
  --publication-state published \
  --include-raw
```

To narrow to unpublished episodes in a date range:

```bash
python3 scripts/ai_podcasting_client.py \
  --json list-episodes \
  --publication-state unpublished \
  --start-date 2026-01-01 \
  --end-date 2026-01-31
```

3. Submit a new episode (creates a new episode; no `source_id` input needed):

```bash
python3 scripts/ai_podcasting_client.py \
  --json submit-episode \
  --payload-file references/submit-episode.example.json
```

4. Update intro copy for an existing episode (`source_id` required):

```bash
python3 scripts/ai_podcasting_client.py \
  --json update-intro-copy \
  --source-id <EPISODE_SOURCE_ID> \
  --payload-file references/update-intro-copy-tcr.example.json
```

5. Update show notes or other customer-owned episode copy after creation:

```bash
python3 scripts/ai_podcasting_client.py \
  --json update-episode-copy \
  --source-id <EPISODE_SOURCE_ID> \
  --payload-file references/update-episode-copy.example.json
```

6. Upload a local supporting file and get a temporary public URL:

```bash
python3 scripts/aip_local_upload_helper.py \
  --json --purpose thumbnail /absolute/path/to/file.png
```

## Interface Notes

- Fixed endpoint: `https://api.aipodcast.ing/client/v1`
- Fixed show: `TCR`
- Auth: bearer token from `~/.secrets/aipodcasting/env` or the file selected by
  `AIPODCASTING_CLIENT_API_KEY_FILE`
- The CLI does not accept base-url overrides or env-based base URL changes.
- The CLI does not accept show selection; all submit/list operations are locked to `TCR`.
- `submit-episode` sends `Idempotency-Key` using the command request ID. The JSON envelope returns
  that request ID, and `data.idempotency_key` repeats it on success. Retry an uncertain submission
  with `--request-id <same-id>`; use a new request ID only for an intentionally separate episode.
- Local uploads are purpose-scoped `cache/` transport objects. Use `episode_main`,
  `episode_asset`, `episode_intro`, `episode_outro`, or `thumbnail`; never treat their URLs as
  permanent inventory.
- Submit, intro, and episode-copy commands reject unknown fields before making a request. Never
  treat a successful response as evidence that an undocumented field was accepted.
- JSON is the default output contract. Use `--plain` or `--human` only for operator inspection.
- `list-episodes` returns a rich summary by default in JSON mode. Each item now includes
  fields such as `thumbnailText`, publishing metadata, preview text for long fields, normalized
  file links, and other lightweight episode context.
- `list-episodes` defaults to `--publication-state all`.
- `list-episodes` supports `--publication-state all|published|unpublished`, plus optional
  `--start-date` and `--end-date` filters.
- Results are sorted newest-first before `--limit` is applied, so `--limit 5` returns the latest
  five matching episodes.
- Use `--include-raw` when the agent needs the sanitized upstream episode object in addition to the
  default summary.
- JSON mode returns a stable envelope with:
  - `schema_version`
  - `command`
  - `status`
  - `data`
  - `error`
  - `meta`

## Required Vs Optional Inputs

1. `doctor`:
   Required: a credential file containing `AIPODCASTING_CLIENT_API_KEY`.
   Run after setup or rotation and before debugging a rejected operation.
2. `list-episodes`:
   Required: none.
   Optional: `--publication-state`, `--start-date`, `--end-date`, `--limit`, `--include-raw`.
   `--publication-state` choices:
   - `all` (default)
   - `published`
   - `unpublished`
   Default JSON output includes a rich per-episode summary with fields such as:
   - `source_id`, `title`, `show`, `status`, `thumbnailText`
   - `created_at`, `updated_at`, publishing metadata, and guest-review flags
   - preview text plus lengths for long copy fields like show notes or editor notes
   - normalized file links, artwork links, deliverable links, processed-asset links, ads, and
     other lightweight metadata when present
   Items are sorted newest-first before `limit` is applied.
   The `data` payload also echoes the applied `filters` object and `matched_count`.
   With `--include-raw`, each item also includes `raw_episode` containing the sanitized upstream
   payload.
3. `submit-episode`:
   Required: `--payload-file` with `mainSourceUrl`.
   Show handling: always forced to `TCR` by the CLI.
   `mainSourceUrl` may be either:
   - a public HTTP/HTTPS URL
   - a local file path, which the helper uploads first
   Prefer a Descript web URL in `mainSourceUrl` when a Descript project/composition/source is
   available. The app accepts MP4 URLs and local MP4 paths, but the CLI will warn because those are
   fallback inputs for TCR, not the preferred source. If both a Descript web URL and an exported
   MP4 are known, put the Descript web URL in `mainSourceUrl` and omit the MP4. Do not require a
   specific Descript path shape; use the Descript web URL the user or browser provides.
   TCR main episode submissions reject `.mp3` main-source inputs. Use the original recording,
   session, or video source link instead, such as Riverside, YouTube, or a direct non-MP3 media
   URL. This is not a Riverside-only allowlist.
   The client normalizes `mainSourceUrl` to the backend submit shape automatically.
   `assetUrls` may also be public URLs or local file paths; local paths are uploaded first.
   Optional: any additional backend-supported episode fields. Use `customNewsletterDraftUrl` for
   a client-provided Ghost newsletter draft, preview, editor, or slug URL. The client preserves
   richer payloads such as `deliverables.thumbnails.options`,
   `deliverables.thumbnails.video.variants`, and `files.episode_outro`.
4. `update-intro-copy`:
   Required (command): `--source-id`, `--payload-file`.
   Intended target: an existing unpublished episode.
   The client supports the current app intro payload directly.
   For conversation-driven usage, prefer these user-facing fields:
   There are no required patch fields beyond `source_id`.
   Common patch fields: `introSourceUrl`, `title`, `videoThumbnails`, `thumbnailText`,
   `transcript`, `instructionsToEditor`, `customNewsletterDraftUrl`, `audioThumbnailLink`,
   `outroMusicLink`.
   For TCR intro source updates, prefer a Descript web URL in `introSourceUrl` when one exists. Do
   not export, upload, or submit an MP4 for the intro source when a Descript URL is available.
   `customNewsletterDraftUrl` stores a client-provided Ghost newsletter draft link under episode
   submission metadata. It does not publish or replace the generated newsletter by itself.
   `videoThumbnails` may be either:
   - one public HTTP/HTTPS URL
   - a list of public HTTP/HTTPS URLs
   The client normalizes `videoThumbnails` into the app's thumbnail shape:
   - `deliverables.thumbnails.video.url` = first thumbnail URL
   - `deliverables.thumbnails.video.variants` = ordered list of all provided thumbnail URLs
   The client also accepts the full current app payload for non-source fields if the agent already
   has it, but source updates must still use `introSourceUrl`.
   Local paths are allowed for file-like fields. The helper uploads them and the client uses the
   returned public URLs.
5. `update-episode-copy`:
   Required (command): `--source-id`, `--payload-file`.
   Provide at least one of:
   - `showNotes`: HTML/plain text; use an empty string or `null` to clear it
   - `assetUrls`: public URLs or local paths; use `[]` or `null` to clear them
   - `guests`: guest objects; use `[]` or `null` to clear them
   This is the supported post-creation path for show notes. Do not send `showNotes` through
   `update-intro-copy`; that command rejects it instead of silently dropping it.

## Conversation Policy

When values are missing in chat context, follow this flow:

1. Before asking follow-up questions, scan the current chat thread and reuse any values the user already provided.
   Do not ask again for values that are already clear in context.
2. First disambiguate the operation when the user's wording does not make it clear whether they mean a new main episode submission, a post-creation show-notes/copy update, or an intro update.
   Do not assume that "submit", "update this episode", or similar phrasing selects one command.
   If the intent is ambiguous, ask exactly:
   "Do you want to:
   1. submit a new main episode source
   2. update show notes/supporting episode copy for an existing episode
   3. update intro/title/thumbnail assets for an existing episode

   Reply with 1, 2, or 3."
   Only continue into command-specific prompts after the user picks one.
3. For submit flow, ask for the missing required submit value, but in that same first reply also
   surface the common optional fields the user may want to set up front.
   Required submit value:
   1. main episode source link as either a public HTTP/HTTPS URL or a local file path.
   Prefer a Descript web URL for TCR when available. MP4 URLs and local MP4 paths are accepted but
   should be described as fallback inputs, not the preferred source. Do not export, upload, or
   submit an MP4 when a Descript web URL is available.
   Common optional submit values to mention in the same first prompt:
   1. title
   2. showNotes
   3. assetUrls
   4. editorNotes
   5. thumbnailText
   6. priority
   7. scheduledDate
   8. needsGuestReview
   9. guests
   10. customNewsletterDraftUrl
4. Use this default submit prompt shape when the source is missing:
   "Send the main episode source as a Descript web URL if you have one. If not, send another
   public HTTP/HTTPS source URL or a local absolute file path.

   You can also include any of these optional fields now if you want them set on creation:
   1. title
   2. showNotes
   3. assetUrls
   4. editorNotes
   5. thumbnailText
   6. priority
   7. scheduledDate
   8. needsGuestReview
   9. guests
   10. customNewsletterDraftUrl

   If you send them together, I can submit the episode in one pass."
5. For copy updates, use `update-episode-copy`. If `source_id` is missing, list episodes using the
   publication/date context already supplied, then ask the user to select one. Ask only for
   `showNotes`, `assetUrls`, or `guests`; never route these fields through the intro command.
6. Intro updates are only for unpublished episodes.
7. For intro updates without `source_id`, immediately run `list-episodes` with
   `--publication-state unpublished`.
   If the user already provided `startDate` and `endDate`, include them.
   Do not ask the user for publication scope first.
8. Ask the user which episode to target using an enumerated list, not raw ids only.
   Render exactly:
   `1. <short title> — <source_id>`
   `2. <short title> — <source_id>`
   `...`
   Then ask: `Reply with the episode number or source_id.`
   If the user replies with a number (for example `4`), map that number to the corresponding `source_id` and continue without asking them to repeat the full id.
9. Ask only for the fields the user wants to change.
10. Enforce a strict two-step prompt sequence for intro updates when `source_id` is missing:
   - Step 1 message: episode list + `Reply with the episode number or source_id.`
   - Step 2 message (only after episode is selected): required/optional field collection.
11. For intro updates, use one prompt shape by default:
   "Episode selected: <source_id>.
   Provide any fields you want to update.

   Common fields:
   1. introSourceUrl (prefer a Descript web URL for TCR intro source updates)
   2. title
   3. videoThumbnails (give one URL or multiple URLs)
   4. thumbnailText
   5. transcript
   6. instructionsToEditor
   7. customNewsletterDraftUrl
   8. audioThumbnailLink
   9. outroMusicLink

   You only need to send the fields you want to change, and I will patch just those."
   Never ask the user to pick an episode id again after step 1 is completed.
12. If optional values are unclear, omit them instead of guessing.
13. Use `--dry-run` only if the user explicitly wants a preview before the write call.
    It is an internal preview/debug tool, not a normal client-facing step.
14. For `customNewsletterDraftUrl`, provide a public HTTP/HTTPS Ghost draft, preview, editor, or
   slug URL. It is not a local file upload field.
15. For file-type fields (`mainSourceUrl`, `introSourceUrl`, `videoThumbnails`, `audioThumbnailLink`, `outroMusicLink`, and submit/copy `assetUrls` entries):
   - The client accepts either public HTTP/HTTPS URLs or local file paths.
   - If the user provides a local file path, run `scripts/aip_local_upload_helper.py` first and use its returned public URL.
   - Do not pass unresolved local filesystem paths to the episode API payload.

## Resources

- `scripts/ai_podcasting_client.py`: Single client interface with subcommands.
- `scripts/aip_local_upload_helper.py`: Purpose-scoped local upload helper; returns temporary
  `cache/` URLs in the same stable JSON envelope.
- `references/submit-episode.example.json`: Example payload for submit flow.
- `references/update-episode-copy.example.json`: Example post-creation show-notes/copy payload.
- `references/update-intro-copy.example.json`: Example payload for intro/copy patch flow.
- `references/update-intro-copy-tcr.example.json`: Example payload for TCR-style final title/thumbnail updates.
- `references/client-setup.md`: Customer install, credential migration, doctor, and retry contract.
