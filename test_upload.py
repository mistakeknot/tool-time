#!/usr/bin/env python3
"""Tests for upload.py."""

import json
from datetime import datetime, timezone
from unittest import mock

import pytest

from upload import anonymize, ensure_token, load_config, main


SAMPLE_STATS = {
    "generated": "2026-01-30T15:11:10Z",
    "total_events": 161,
    "tools": {
        "Bash": {"calls": 71, "errors": 4, "rejections": 0},
        "Edit": {"calls": 23, "errors": 0, "rejections": 0},
    },
    "edit_without_read_count": 2,
    "model": "claude-opus-4-5-20251101",
    "skills": {
        "superpowers:brainstorming": {"calls": 5},
        "tool-time:tool-time": {"calls": 2},
    },
    "mcp_servers": {
        "chrome-devtools": {"calls": 15, "errors": 2},
    },
    "installed_plugins": [
        "superpowers@superpowers-marketplace",
        "tool-time@interagency-marketplace",
    ],
}


class TestAnonymize:
    def test_strips_to_allowlist(self):
        stats = {**SAMPLE_STATS, "extra_field": "should be removed"}
        result = anonymize(stats, "test-token")
        assert "extra_field" not in result
        assert set(result.keys()) == {
            "submission_token",
            "generated",
            "total_events",
            "tools",
            "edit_without_read",
            "model",
            "skills",
            "mcp_servers",
            "installed_plugins",
        }

    def test_truncates_timestamp_to_hour(self):
        result = anonymize(SAMPLE_STATS, "tok")
        assert result["generated"] == "2026-01-30T15:00:00Z"

    def test_preserves_tool_counts(self):
        result = anonymize(SAMPLE_STATS, "tok")
        assert result["tools"]["Bash"] == {"calls": 71, "errors": 4, "rejections": 0}

    def test_renames_edit_without_read(self):
        """Schema uses edit_without_read (not _count) for consistency."""
        result = anonymize(SAMPLE_STATS, "tok")
        assert result["edit_without_read"] == 2
        assert "edit_without_read_count" not in result

    def test_preserves_model(self):
        result = anonymize(SAMPLE_STATS, "tok")
        assert result["model"] == "claude-opus-4-5-20251101"

    def test_handles_missing_model(self):
        stats = {**SAMPLE_STATS}
        del stats["model"]
        result = anonymize(stats, "tok")
        assert result["model"] is None

    def test_empty_tools(self):
        stats = {**SAMPLE_STATS, "tools": {}}
        result = anonymize(stats, "tok")
        assert result["tools"] == {}

    def test_token_in_output(self):
        result = anonymize(SAMPLE_STATS, "my-token")
        assert result["submission_token"] == "my-token"


class TestEnsureToken:
    def test_generates_token_if_missing(self, tmp_path):
        config_file = tmp_path / "config.json"
        config = {}
        with mock.patch("upload.CONFIG_FILE", config_file):
            token = ensure_token(config)
        assert len(token) == 32  # 16 bytes hex
        assert config["submission_token"] == token
        assert "token_created_at" in config

    def test_preserves_existing_token(self, tmp_path):
        config = {"submission_token": "existing-token"}
        token = ensure_token(config)
        assert token == "existing-token"


class TestMain:
    def test_skips_if_not_opted_in(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"community_sharing": False}))
        with mock.patch("upload.CONFIG_FILE", config_file):
            assert main() == 0

    def test_skips_if_no_config(self, tmp_path):
        config_file = tmp_path / "nonexistent.json"
        with mock.patch("upload.CONFIG_FILE", config_file):
            assert main() == 0

    def test_skips_if_no_stats(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"community_sharing": True}))
        stats_file = tmp_path / "stats.json"
        with (
            mock.patch("upload.CONFIG_FILE", config_file),
            mock.patch("upload.STATS_FILE", stats_file),
        ):
            assert main() == 0

    def test_uploads_when_opted_in(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps({"community_sharing": True, "submission_token": "tok123"})
        )
        stats_file = tmp_path / "stats.json"
        stats_file.write_text(json.dumps(SAMPLE_STATS))
        with (
            mock.patch("upload.CONFIG_FILE", config_file),
            mock.patch("upload.STATS_FILE", stats_file),
            mock.patch("upload.upload", return_value=True) as mock_upload,
        ):
            assert main() == 0
            mock_upload.assert_called_once()
            payload = mock_upload.call_args[0][0]
            assert payload["submission_token"] == "tok123"
            assert payload["generated"] == "2026-01-30T15:00:00Z"

        # Check last_upload_at was saved
        saved = json.loads(config_file.read_text())
        assert "last_upload_at" in saved

    def test_handles_upload_failure(self, tmp_path):
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps({"community_sharing": True, "submission_token": "tok"})
        )
        stats_file = tmp_path / "stats.json"
        stats_file.write_text(json.dumps(SAMPLE_STATS))
        with (
            mock.patch("upload.CONFIG_FILE", config_file),
            mock.patch("upload.STATS_FILE", stats_file),
            mock.patch("upload.upload", return_value=False),
        ):
            assert main() == 0
            # last_upload_at should NOT be set on failure
            saved = json.loads(config_file.read_text())
            assert "last_upload_at" not in saved


class TestAnonymizeEcosystemFields:
    def test_skills_in_payload(self):
        result = anonymize(SAMPLE_STATS, "tok")
        assert result["skills"] == {
            "superpowers:brainstorming": 5,
            "tool-time:tool-time": 2,
        }

    def test_mcp_servers_in_payload(self):
        result = anonymize(SAMPLE_STATS, "tok")
        assert result["mcp_servers"] == {
            "chrome-devtools": {"calls": 15, "errors": 2},
        }

    def test_installed_plugins_in_payload(self):
        result = anonymize(SAMPLE_STATS, "tok")
        assert result["installed_plugins"] == [
            "superpowers@superpowers-marketplace",
            "tool-time@interagency-marketplace",
        ]

    def test_missing_skills_defaults_empty(self):
        stats = {k: v for k, v in SAMPLE_STATS.items() if k != "skills"}
        result = anonymize(stats, "tok")
        assert result["skills"] == {}

    def test_missing_mcp_servers_defaults_empty(self):
        stats = {k: v for k, v in SAMPLE_STATS.items() if k != "mcp_servers"}
        result = anonymize(stats, "tok")
        assert result["mcp_servers"] == {}

    def test_missing_plugins_defaults_empty(self):
        stats = {k: v for k, v in SAMPLE_STATS.items() if k != "installed_plugins"}
        result = anonymize(stats, "tok")
        assert result["installed_plugins"] == []

    def test_no_leakage_of_extra_skill_fields(self):
        """Only calls should survive, not any other fields on skills."""
        stats = {**SAMPLE_STATS, "skills": {"foo": {"calls": 3, "secret": "bad"}}}
        result = anonymize(stats, "tok")
        assert result["skills"] == {"foo": 3}
