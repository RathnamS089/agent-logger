"""Tests for summarizer.py — model path, llama-cpp path, deterministic fallback."""

import os
from unittest.mock import MagicMock, patch

import pytest

import agent_logger.summarizer as summarizer
from agent_logger.summarizer import (
    _fallback_summary,
    generate_summary,
)


class TestFallbackSummary:
    def test_modified_and_new(self):
        s = _fallback_summary(5, 3, ["a.py", "b.py"], ["c.py"], "/repo", "main")
        assert "Modified 2 files" in s
        assert "created 1 new file" in s
        assert "/repo" in s
        assert "main" in s

    def test_only_modified(self):
        s = _fallback_summary(0, 2, ["a.py"], [], "/repo", "dev")
        assert "Modified 1 file" in s

    def test_only_new(self):
        s = _fallback_summary(0, 1, [], ["n.py"], "/repo", "main")
        assert "Created 1 new file" in s

    def test_no_changes(self):
        s = _fallback_summary(0, 0, [], [], "/repo", "main")
        assert "No code changes" in s

    def test_singular_plural(self):
        s = _fallback_summary(0, 1, ["one.py"], [], "/repo", "main")
        assert "Modified 1 file " in s or "Modified 1 file." in s

    def test_activity_count(self):
        s = _fallback_summary(7, 1, ["a.py"], [], "/repo", "main")
        assert "7 events" in s

    def test_truncates_long_file_lists(self):
        files = [f"f{i}.py" for i in range(20)]
        s = _fallback_summary(0, 20, files, [], "/repo", "main")
        assert "f15.py" not in s  # only first 5 shown
        assert "Modified 20 files" in s


class TestGenerateSummaryRouting:
    def test_ollama_prefix_routes_to_ollama(self):
        with patch.object(summarizer, "_try_ollama", return_value="  model says hi  "):
            result = generate_summary(
                3, 2, ["a.py"], ["b.py"], "/repo", "main", model_name="ollama:phi"
            )
        assert result == "model says hi"

    def test_ollama_failure_falls_back(self):
        with patch.object(summarizer, "_try_ollama", return_value=None):
            result = generate_summary(
                3, 2, ["a.py"], ["b.py"], "/repo", "main", model_name="ollama:phi"
            )
        assert result.startswith("Session summary:")

    def test_bare_name_without_ollama_prefix_skips_ollama(self):
        """model_name='phi' (no 'ollama:' prefix) should not call Ollama API
        in this build; it goes to llama-cpp check then fallback."""
        with patch.object(summarizer, "_try_ollama") as m:
            result = generate_summary(
                1, 1, ["a.py"], [], "/repo", "main", model_name="phi"
            )
        m.assert_not_called()
        assert result.startswith("Session summary:")

    def test_llama_cpp_used_when_model_file_exists(self):
        fake_instance = MagicMock(
            return_value={"choices": [{"text": " llama output "}]}
        )
        fake_llm_ctor = MagicMock(return_value=fake_instance)  # Llama(...) → instance
        fake_mod = MagicMock()
        fake_mod.Llama = fake_llm_ctor

        with patch.object(
            summarizer, "_try_import_llama_cpp", return_value=fake_mod
        ), patch("os.path.exists", return_value=True):
            result = generate_summary(1, 1, ["a.py"], [], "/repo", "main", model_name=None)
        assert result == "llama output"
        fake_llm_ctor.assert_called_once()

    def test_llama_cpp_error_falls_back(self):
        with patch.object(summarizer, "_try_import_llama_cpp", return_value=None):
            result = generate_summary(1, 1, ["a.py"], [], "/repo", "main", model_name=None)
        assert result.startswith("Session summary:")

    def test_ollama_tried_before_llama_cpp(self):
        """When model_name has ollama: prefix and Ollama succeeds, llama-cpp
        should not be attempted."""
        fake_mod = MagicMock()
        with patch.object(
            summarizer, "_try_ollama", return_value="from ollama"
        ), patch.object(summarizer, "_try_import_llama_cpp") as m:
            result = generate_summary(
                1, 1, ["a.py"], [], "/repo", "main", model_name="ollama:phi"
            )
        m.assert_not_called()
        assert result == "from ollama"

    def test_prompt_never_contains_diff_content(self):
        """Privacy: only file paths and counts go into the prompt."""
        with patch.object(summarizer, "_try_ollama") as m:
            generate_summary(
                2,
                2,
                ["secret_password.py"],
                ["new_secret.py"],
                "/repo",
                "main",
                model_name="ollama:phi",
            )
        prompt = m.call_args[0][1]
        assert "SECRET" not in prompt  # no diff content, just paths
        assert "secret_password.py" in prompt  # paths are fine


class TestTryOllama:
    def test_unreachable_returns_none(self):
        # Nothing listening on this port; must return None, not raise.
        with patch.dict(os.environ, {"OLLAMA_HOST": "http://127.0.0.1:9"}):
            result = summarizer._try_ollama("phi", "prompt", timeout=1)
        assert result is None

    def test_http_error_returns_none(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        with patch("httpx.post", return_value=mock_resp):
            assert summarizer._try_ollama("phi", "p") is None

    def test_success_returns_text(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"response": "hello"}
        with patch("httpx.post", return_value=mock_resp):
            assert summarizer._try_ollama("phi", "p") == "hello"
