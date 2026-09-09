import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
MODULE_PATH = SCRIPTS_DIR / "ai_podcasting_client.py"

if str(SCRIPTS_DIR) not in sys.path:
  sys.path.insert(0, str(SCRIPTS_DIR))

SPEC = importlib.util.spec_from_file_location("ai_podcasting_client", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
  raise RuntimeError(f"Failed to load module spec for {MODULE_PATH}")

client = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(client)

import aip_local_upload_helper as upload_helper


class BuildTextPreviewTests(unittest.TestCase):
  def test_build_text_preview_strips_html_and_truncates(self) -> None:
    preview = client.build_text_preview("<p>Hello <strong>world</strong>&nbsp;again</p>", limit=12)

    self.assertEqual(preview["preview"], "Hello wor...")
    self.assertEqual(preview["length"], 17)


class ResolveOutputModeTests(unittest.TestCase):
  def test_resolve_output_mode_defaults_to_json(self) -> None:
    args = SimpleNamespace(json=False, human=False, plain=False)

    self.assertEqual(client.resolve_output_mode(args), "json")


class AuthHeaderTests(unittest.TestCase):
  def test_build_client_auth_headers_reads_secret_file(self) -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
      secret_path = Path(tmpdir) / "api-key"
      secret_path.write_text(
        "AIPODCASTING_CLIENT_API_KEY=client-secret\n",
        encoding="utf-8",
      )
      secret_path.chmod(0o600)

      with mock.patch.dict(
        os.environ,
        {"AIPODCASTING_CLIENT_API_KEY_FILE": str(secret_path)},
        clear=True,
      ):
        self.assertEqual(
          client.build_client_auth_headers(),
          {"Authorization": "Bearer client-secret"},
        )

  def test_build_client_auth_headers_rejects_secret_env_fallback(self) -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
      missing_secret_path = Path(tmpdir) / "missing-env"
      env = {
        "AIPODCASTING_CLIENT_API_KEY": "leaky-env-secret",
        "AIPODCASTING_CLIENT_API_KEY_FILE": str(missing_secret_path),
      }
      with mock.patch.dict(os.environ, env, clear=True):
        with self.assertRaises(upload_helper.UploadHelperError) as error_context:
          client.build_client_auth_headers()

    self.assertEqual(error_context.exception.code, "E_AUTH")


class RequestHeaderTests(unittest.TestCase):
  @staticmethod
  def _response(body: bytes = b"{}") -> mock.MagicMock:
    response = mock.MagicMock()
    response.__enter__.return_value.read.return_value = body
    return response

  def test_episode_client_sends_named_user_agent(self) -> None:
    response = self._response()

    with (
      mock.patch.object(client, "build_client_auth_headers", return_value={}),
      mock.patch.object(client.urlrequest, "urlopen", return_value=response) as urlopen,
    ):
      client.request_json("GET", "https://api.aipodcast.ing/client/v1/episodes", 30.0)

    request = urlopen.call_args.args[0]
    self.assertEqual(request.get_header("User-agent"), client.CLIENT_USER_AGENT)

  def test_upload_helper_sends_named_user_agent(self) -> None:
    response = self._response(
      b'{"upload_url":"https://upload.example","public_url":"https://file.example","object_key":"cache/client-api/tcr-agent/tcr/episode_asset/id/example.png"}'
    )

    with (
      mock.patch.object(upload_helper, "build_client_auth_headers", return_value={}),
      mock.patch.object(upload_helper.urlrequest, "urlopen", return_value=response) as urlopen,
    ):
      upload_helper.request_json(
        "POST",
        "https://api.aipodcast.ing/client/v1/uploads",
        30.0,
        {
          "show": "TCR",
          "purpose": "thumbnail",
          "filename": "example.png",
          "content_type": "image/png",
        },
      )

    request = urlopen.call_args.args[0]
    self.assertEqual(request.get_header("User-agent"), upload_helper.CLIENT_USER_AGENT)


class ClientBoundaryTests(unittest.TestCase):
  def test_doctor_verifies_required_grants(self) -> None:
    args = SimpleNamespace(timeout_seconds=30.0, request_id="request-1")
    response = {
      "api_version": "v1",
      "client_id": "tcr-agent",
      "allowed_shows": ["TCR"],
      "scopes": [
        "episodes:read",
        "episodes:submit",
        "episodes:intro:write",
        "episodes:copy:write",
        "uploads:create",
      ],
    }

    with mock.patch.object(client, "request_json", return_value=response) as request_json:
      result = client.run_doctor(args)

    self.assertTrue(result["ready"])
    self.assertEqual(result["client_id"], "tcr-agent")
    request_json.assert_called_once_with(
      "GET",
      "https://api.aipodcast.ing/client/v1/access",
      30.0,
      extra_headers={"X-Request-ID": "request-1"},
    )

  def test_submit_uses_request_id_as_idempotency_key(self) -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
      payload_path = Path(tmpdir) / "submit.json"
      payload_path.write_text(
        json.dumps(
          {
            "mainSourceUrl": "https://web.descript.com/project-1",
            "title": "Episode",
          }
        ),
        encoding="utf-8",
      )
      args = SimpleNamespace(
        payload_file=str(payload_path),
        dry_run=False,
        timeout_seconds=30.0,
        request_id="episode-request-1",
      )
      response = {
        "episode": {"source_id": "episode-1"},
        "main_media_job_id": "job-1",
        "replayed": False,
      }

      with mock.patch.object(client, "request_json", return_value=response) as request_json:
        result = client.run_submit_episode(args)

    self.assertEqual(result["source_id"], "episode-1")
    self.assertEqual(result["idempotency_key"], "episode-request-1")
    request_json.assert_called_once()
    self.assertEqual(
      request_json.call_args.kwargs["extra_headers"],
      {
        "Idempotency-Key": "episode-request-1",
        "X-Request-ID": "episode-request-1",
      },
    )

  def test_idempotency_in_progress_has_stable_retryable_error(self) -> None:
    error = client.classify_http_error(
      409,
      {
        "detail": {
          "code": "idempotency_in_progress",
          "message": "Still running.",
        }
      },
    )

    self.assertEqual(error.code, "E_IDEMPOTENCY_IN_PROGRESS")
    self.assertTrue(error.retryable)
    self.assertEqual(error.exit_code, 4)


class NormalizeEpisodeItemTests(unittest.TestCase):
  def test_normalize_episode_item_exposes_rich_summary_fields(self) -> None:
    item = {
      "source_id": "ep-123",
      "show": "TCR",
      "title": " Final title ",
      "thumbnailText": " Final thumbnail ",
      "needsGuestReview": True,
      "files": {
        "main": {
          "raw": " https://example.com/raw.mp3 ",
          "edited": " https://example.com/edited.mp3 ",
          "descript": " https://example.com/descript ",
        },
        "episode_outro": {"edited": " https://example.com/outro.mp3 "},
      },
      "submission": {
        "title": " Submission title ",
        "thumbnailText": " Draft thumbnail ",
        "showNotes": "<p>Hello <strong>world</strong>&nbsp;from notes</p>",
        "assetUrls": [" https://example.com/asset-1 ", "", None],
        "introTranscript": " Intro transcript ",
        "titleInspiration": " Big idea ",
        "editorInstructions": " Please cut the intro ",
        "customNewsletterDraftUrl": " https://ghost.example.com/ghost/#/editor/post/summary123 ",
        "guests": [
          {"name": " Ada Lovelace ", "email": " ada@example.com "},
          {"name": " "},
        ],
      },
      "production": {
        "editorNotes": " Tighten the opening. ",
        "priority": " high ",
        "editorName": " Sam ",
        "duration": 123.5,
        "tags": [" ai ", " podcast "],
      },
      "publishing": {
        "status": " To Publish ",
        "scheduledDate": "2026-04-01T10:00:00",
        "platforms": [
          {
            "name": " YouTube ",
            "url": " https://youtube.example/video ",
            "status": " queued ",
          }
        ],
        "currentJob": {
          "jobId": " job-1 ",
          "startedAt": "2026-03-31T09:00:00",
        },
      },
      "billing": {"consumed_hours": 2.5, "is_cross_post": False},
      "processed_assets": {
        "clips": [" clip-1 "],
        "transcript_html_url": " https://example.com/transcript.html ",
      },
      "deliverables": {
        "media": {
          "video": {"main": {"url": " https://example.com/video.mp4 "}},
        },
        "thumbnails": {
          "video": {
            "url": " https://example.com/video-thumb.png ",
            "design_source_url": " https://example.com/video-thumb.fig ",
            "variants": [
              {
                "url": " https://example.com/video-thumb-alt.png ",
                "source": " manual ",
              }
            ],
          },
          "audio": {"url": " https://example.com/audio-thumb.png "},
        },
        "links": {"newsletter": " https://example.com/newsletter "},
        "social": {
          "clip_ids": [" clip-1 "],
          "clip_urls": [" https://example.com/clips/1 "],
        },
      },
      "artwork": {
        "videoThumbnailUrl": " https://example.com/artwork-video.png ",
        "audioThumbnailUrl": " https://example.com/artwork-audio.png ",
      },
      "ads": {"midRollTimes": [" 00:10:00 "]},
      "shownotes": {"mainDescriptionHtml": "<p>Main description</p>"},
      "created_at": "2026-03-30T00:00:00",
      "updated_at": "2026-03-31T00:00:00",
    }

    normalized = client.normalize_episode_item(item)

    self.assertEqual(normalized["source_id"], "ep-123")
    self.assertEqual(normalized["status"], "To Publish")
    self.assertEqual(normalized["thumbnailText"], "Final thumbnail")
    self.assertEqual(normalized["submissionTitle"], "Submission title")
    self.assertTrue(normalized["needsGuestReview"])
    self.assertEqual(normalized["assetUrls"], ["https://example.com/asset-1"])
    self.assertEqual(
      normalized["copy"]["showNotesPreview"],
      "Hello world from notes",
    )
    self.assertEqual(normalized["copy"]["introTranscriptPreview"], "Intro transcript")
    self.assertEqual(normalized["copy"]["editorInstructionsPreview"], "Please cut the intro")
    self.assertEqual(
      normalized["customNewsletterDraftUrl"],
      "https://ghost.example.com/ghost/#/editor/post/summary123",
    )
    self.assertEqual(normalized["copy"]["editorNotesPreview"], "Tighten the opening.")
    self.assertEqual(normalized["production"]["priority"], "high")
    self.assertEqual(normalized["production"]["editorName"], "Sam")
    self.assertEqual(normalized["production"]["duration"], 123.5)
    self.assertEqual(normalized["production"]["tags"], ["ai", "podcast"])
    self.assertEqual(
      normalized["publishing"]["platforms"],
      [
        {
          "name": "YouTube",
          "url": "https://youtube.example/video",
          "status": "queued",
        }
      ],
    )
    self.assertEqual(
      normalized["publishing"]["currentJob"],
      {"jobId": "job-1", "startedAt": "2026-03-31T09:00:00"},
    )
    self.assertEqual(
      normalized["files"]["main"],
      {
        "raw": "https://example.com/raw.mp3",
        "edited": "https://example.com/edited.mp3",
        "descript": "https://example.com/descript",
      },
    )
    self.assertEqual(
      normalized["artwork"]["videoThumbnailUrl"],
      "https://example.com/artwork-video.png",
    )
    self.assertEqual(
      normalized["artwork"]["videoThumbnailVariants"],
      [
        {
          "url": "https://example.com/video-thumb-alt.png",
          "source": "manual",
        }
      ],
    )
    self.assertEqual(normalized["ads"], {"midRollTimes": ["00:10:00"]})
    self.assertEqual(
      normalized["shownotes"],
      {"mainDescriptionPreview": "Main description", "mainDescriptionLength": 16},
    )
    self.assertNotIn("billing", normalized)
    self.assertNotIn("raw_episode", normalized)

  def test_normalize_episode_item_include_raw_preserves_upstream_payload(self) -> None:
    item = {
      "source_id": "ep-raw",
      "show": "TCR",
      "title": "Raw episode",
      "submission": {},
      "production": {},
      "publishing": {"status": "To Publish"},
      "files": {"main": {"raw": "https://example.com/raw.mp3"}},
    }

    normalized = client.normalize_episode_item(item, include_raw=True)

    self.assertEqual(normalized["source_id"], "ep-raw")
    self.assertEqual(normalized["raw_episode"]["source_id"], "ep-raw")
    self.assertNotIn("billing", normalized["raw_episode"])


class RunListEpisodesTests(unittest.TestCase):
  def test_run_list_episodes_sorts_newest_first_before_limit(self) -> None:
    args = SimpleNamespace(
      publication_state="published",
      start_date="",
      end_date="",
      limit=1,
      include_raw=False,
      dry_run=False,
      timeout_seconds=30.0,
    )
    body = {
      "items": [
        {
          "source_id": "ep-older",
          "show": "TCR",
          "title": "Older published episode",
          "submission": {},
          "production": {},
          "publishing": {"status": "Published", "publishedDate": "2025-01-01T10:00:00"},
          "files": {"main": {"raw": "https://example.com/older.mp3"}},
        },
        {
          "source_id": "ep-newer",
          "show": "TCR",
          "title": "Newer published episode",
          "submission": {},
          "production": {},
          "publishing": {"status": "Published", "publishedDate": "2025-02-01T10:00:00"},
          "files": {"main": {"raw": "https://example.com/newer.mp3"}},
        },
      ]
    }

    with mock.patch.object(client, "request_json", return_value=body):
      data = client.run_list_episodes(args)

    self.assertEqual(data["count"], 1)
    self.assertEqual(data["matched_count"], 2)
    self.assertEqual(data["items"][0]["source_id"], "ep-newer")

  def test_run_list_episodes_filters_published_items(self) -> None:
    args = SimpleNamespace(
      publication_state="published",
      start_date="2026-01-01",
      end_date="2026-01-31",
      limit=50,
      include_raw=False,
      dry_run=False,
      timeout_seconds=30.0,
    )
    body = {
      "items": [
        {
          "source_id": "ep-published",
          "show": "TCR",
          "title": "Published episode",
          "submission": {},
          "production": {},
          "publishing": {"status": "Published"},
          "files": {"main": {"raw": "https://example.com/published.mp3"}},
        },
        {
          "source_id": "ep-unpublished",
          "show": "TCR",
          "title": "Unpublished episode",
          "submission": {},
          "production": {},
          "publishing": {"status": "To Publish"},
          "files": {"main": {"raw": "https://example.com/unpublished.mp3"}},
        },
      ]
    }

    with mock.patch.object(client, "request_json", return_value=body) as request_json:
      data = client.run_list_episodes(args)

    request_json.assert_called_once()
    self.assertEqual(request_json.call_args.args[0], "GET")
    self.assertIn("includePublished=true", request_json.call_args.args[1])
    self.assertIn("startDate=2026-01-01", request_json.call_args.args[1])
    self.assertIn("endDate=2026-01-31", request_json.call_args.args[1])
    self.assertEqual(data["count"], 1)
    self.assertEqual(data["matched_count"], 1)
    self.assertEqual(data["filters"]["publication_state"], "published")
    self.assertEqual(data["items"][0]["source_id"], "ep-published")

  def test_run_list_episodes_filters_unpublished_items(self) -> None:
    args = SimpleNamespace(
      publication_state="unpublished",
      start_date="",
      end_date="",
      limit=50,
      include_raw=False,
      dry_run=False,
      timeout_seconds=30.0,
    )
    body = {
      "items": [
        {
          "source_id": "ep-published",
          "show": "TCR",
          "title": "Published episode",
          "submission": {},
          "production": {},
          "publishing": {"status": "Published"},
          "files": {"main": {"raw": "https://example.com/published.mp3"}},
        },
        {
          "source_id": "ep-backlog",
          "show": "TCR",
          "title": "Backlog episode",
          "submission": {},
          "production": {},
          "publishing": {"status": "Backlog"},
          "files": {"main": {"raw": "https://example.com/backlog.mp3"}},
        },
      ]
    }

    with mock.patch.object(client, "request_json", return_value=body) as request_json:
      data = client.run_list_episodes(args)

    request_json.assert_called_once()
    self.assertIn("includePublished=false", request_json.call_args.args[1])
    self.assertEqual(data["count"], 1)
    self.assertEqual(data["matched_count"], 1)
    self.assertEqual(data["filters"]["publication_state"], "unpublished")
    self.assertEqual(data["items"][0]["source_id"], "ep-backlog")


class SubmitAndIntroDryRunTests(unittest.TestCase):
  def test_validate_submit_payload_rejects_main_mp3(self) -> None:
    with self.assertRaises(client.ClientError) as error_context:
      client.validate_submit_payload(
        {
          "show": "TCR",
          "mainSourceUrl": "https://example.com/exported-audio.mp3?download=1",
        }
      )

    self.assertEqual(error_context.exception.code, "E_VALIDATION")
    self.assertIn("cannot be an MP3 link", error_context.exception.message)

  def test_validate_submit_payload_rejects_legacy_source_fields(self) -> None:
    for legacy_payload in (
      {"show": "TCR", "mainEpisodeFile": "https://example.com/exported-main.mp4"},
      {"show": "TCR", "files": {"main": {"raw": "https://example.com/exported-main.mp4"}}},
    ):
      with self.subTest(legacy_payload=legacy_payload):
        with self.assertRaises(client.ClientError) as error_context:
          client.validate_submit_payload(legacy_payload)

        self.assertEqual(error_context.exception.code, "E_VALIDATION")
        self.assertIn("mainSourceUrl", error_context.exception.message)

  def test_validate_submit_payload_rejects_unknown_fields(self) -> None:
    with self.assertRaises(client.ClientError) as error_context:
      client.validate_submit_payload(
        {
          "mainSourceUrl": "https://web.descript.com/project-1",
          "unsupportedField": "must fail",
        }
      )

    self.assertEqual(error_context.exception.code, "E_VALIDATION")
    self.assertIn("unsupportedField", error_context.exception.message)

  def test_validate_intro_copy_payload_rejects_legacy_source_fields(self) -> None:
    for legacy_payload in (
      {"recordingLink": "https://web.descript.com/01234567-89ab-4cde-8f01-23456789abcd"},
      {"introFile": "https://web.descript.com/01234567-89ab-4cde-8f01-23456789abcd"},
      {"files": {"intro": {"raw": "https://web.descript.com/01234567-89ab-4cde-8f01-23456789abcd"}}},
    ):
      with self.subTest(legacy_payload=legacy_payload):
        with self.assertRaises(client.ClientError) as error_context:
          client.validate_intro_copy_payload(legacy_payload)

        self.assertEqual(error_context.exception.code, "E_VALIDATION")
        self.assertIn("introSourceUrl", error_context.exception.message)

  def test_validate_intro_copy_payload_routes_show_notes_to_copy_command(self) -> None:
    with self.assertRaises(client.ClientError) as error_context:
      client.validate_intro_copy_payload({"showNotes": "Updated notes"})

    self.assertEqual(error_context.exception.code, "E_VALIDATION")
    self.assertIn("showNotes", error_context.exception.message)
    self.assertIn("update-episode-copy", error_context.exception.hint)

  def test_validate_episode_copy_payload_rejects_unknown_fields(self) -> None:
    with self.assertRaises(client.ClientError) as error_context:
      client.validate_episode_copy_payload(
        {"showNotes": "Updated notes", "status": "Published"}
      )

    self.assertEqual(error_context.exception.code, "E_VALIDATION")
    self.assertIn("status", error_context.exception.message)

  def test_normalize_intro_copy_payload_maps_newsletter_draft_alias(self) -> None:
    payload, upload_records = client.normalize_intro_copy_payload(
      {"ghostNewsletterDraftUrl": " https://ghost.example.com/p/custom-draft "},
      timeout_seconds=30.0,
      dry_run=True,
    )

    self.assertEqual(upload_records, [])
    self.assertEqual(
      payload["customNewsletterDraftUrl"],
      "https://ghost.example.com/p/custom-draft",
    )

  def test_run_submit_episode_dry_run_uses_expected_request_shape(self) -> None:
    args = SimpleNamespace(
      payload_file=str(ROOT / "references" / "submit-episode.example.json"),
      dry_run=True,
      timeout_seconds=30.0,
    )

    data = client.run_submit_episode(args)

    self.assertTrue(data["dry_run"])
    self.assertEqual(data["request"]["method"], "POST")
    self.assertEqual(data["request"]["url"], "https://api.aipodcast.ing/client/v1/episodes")
    self.assertEqual(data["request"]["payload"]["show"], "TCR")
    self.assertEqual(
      data["request"]["payload"]["files"]["main"]["raw"],
      "https://web.descript.com/01234567-89ab-4cde-8f01-23456789abcd",
    )
    self.assertEqual(data["warnings"], [])
    self.assertNotIn("mainSourceUrl", data["request"]["payload"])
    self.assertEqual(
      len(data["request"]["payload"]["deliverables"]["thumbnails"]["options"]),
      2,
    )
    self.assertEqual(
      data["request"]["payload"]["customNewsletterDraftUrl"],
      "https://ghost.example.com/ghost/#/editor/post/submit123",
    )

  def test_run_submit_episode_dry_run_warns_on_mp4_main_source(self) -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
      payload_path = Path(tmpdir) / "submit-mp4.json"
      payload_path.write_text(
        json.dumps(
          {
            "show": "TCR",
            "mainSourceUrl": "https://storage.aipodcast.ing/assets/share/deliverables/exported-main.mp4",
          }
        ),
        encoding="utf-8",
      )
      args = SimpleNamespace(
        payload_file=str(payload_path),
        dry_run=True,
        timeout_seconds=30.0,
      )

      data = client.run_submit_episode(args)

    self.assertTrue(data["dry_run"])
    self.assertEqual(
      data["request"]["payload"]["files"]["main"]["raw"],
      "https://storage.aipodcast.ing/assets/share/deliverables/exported-main.mp4",
    )
    self.assertEqual(len(data["warnings"]), 1)
    self.assertEqual(data["warnings"][0]["code"], "W_TCR_MAIN_MP4_SOURCE")
    self.assertEqual(data["warnings"][0]["field"], "mainSourceUrl")

  def test_run_update_intro_copy_dry_run_uses_expected_request_shape(self) -> None:
    args = SimpleNamespace(
      source_id="ep-123",
      payload_file=str(ROOT / "references" / "update-intro-copy-tcr.example.json"),
      dry_run=True,
      timeout_seconds=30.0,
    )

    data = client.run_update_intro_copy(args)

    self.assertTrue(data["dry_run"])
    self.assertEqual(data["request"]["method"], "PATCH")
    self.assertEqual(
      data["request"]["url"],
      "https://api.aipodcast.ing/client/v1/episodes/ep-123/intro",
    )
    self.assertEqual(
      data["request"]["payload"]["title"],
      "Final publish-ready episode title",
    )
    self.assertEqual(
      data["request"]["payload"]["introFile"],
      "https://web.descript.com/01234567-89ab-4cde-8f01-23456789abcd",
    )
    self.assertNotIn("introSourceUrl", data["request"]["payload"])
    self.assertEqual(
      data["request"]["payload"]["deliverables"]["thumbnails"]["video"]["url"],
      "https://example.com/path/to/video-thumbnail-16x9-primary.png",
    )
    self.assertEqual(
      data["request"]["payload"]["files"]["episode_outro"]["edited"],
      "https://example.com/path/to/outro-music.mp3",
    )
    self.assertEqual(
      data["request"]["payload"]["customNewsletterDraftUrl"],
      "https://ghost.example.com/ghost/#/editor/post/intro123",
    )

  def test_run_update_episode_copy_dry_run_uses_expected_request_shape(self) -> None:
    args = SimpleNamespace(
      source_id="ep-123",
      payload_file=str(ROOT / "references" / "update-episode-copy.example.json"),
      dry_run=True,
      timeout_seconds=30.0,
    )

    data = client.run_update_episode_copy(args)

    self.assertTrue(data["dry_run"])
    self.assertEqual(data["request"]["method"], "PATCH")
    self.assertEqual(
      data["request"]["url"],
      "https://api.aipodcast.ing/client/v1/episodes/ep-123/copy",
    )
    self.assertEqual(
      data["request"]["payload"]["showNotes"],
      "Updated episode outline, links, and production notes.",
    )
    self.assertEqual(
      data["updated_fields"],
      ["assetUrls", "guests", "showNotes"],
    )

  def test_run_update_episode_copy_calls_scoped_route_and_returns_response(self) -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
      payload_path = Path(tmpdir) / "copy.json"
      payload_path.write_text(
        json.dumps({"showNotes": "Updated notes"}),
        encoding="utf-8",
      )
      args = SimpleNamespace(
        source_id="ep-123",
        payload_file=str(payload_path),
        dry_run=False,
        timeout_seconds=30.0,
        request_id="copy-request-1",
      )
      response = {
        "source_id": "ep-123",
        "submission": {"showNotes": "Updated notes"},
      }

      with mock.patch.object(client, "request_json", return_value=response) as request_json:
        data = client.run_update_episode_copy(args)

    self.assertEqual(data["source_id"], "ep-123")
    self.assertEqual(data["updated_fields"], ["showNotes"])
    self.assertEqual(data["response"]["submission"]["showNotes"], "Updated notes")
    request_json.assert_called_once_with(
      "PATCH",
      "https://api.aipodcast.ing/client/v1/episodes/ep-123/copy",
      30.0,
      {"showNotes": "Updated notes"},
      extra_headers={"X-Request-ID": "copy-request-1"},
    )


if __name__ == "__main__":
  unittest.main()
