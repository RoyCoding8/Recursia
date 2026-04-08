"""Tests for _extract_json_from_text and _load_json_text fallback."""

from app.adapters.llm_client import (
    LLMClientRuntimeError,
    _extract_json_from_text,
    _load_json_text,
)
import pytest


class TestExtractJsonFromText:
    def test_plain_json_returns_parsed(self):
        assert _extract_json_from_text('{"key": "value"}') == {"key": "value"}

    def test_json_in_fenced_block(self):
        text = 'Here is the result:\n```json\n{"a": 1}\n```\nDone.'
        assert _extract_json_from_text(text) == {"a": 1}

    def test_json_in_plain_fenced_block(self):
        text = "Output:\n```\n{\"x\": true}\n```"
        assert _extract_json_from_text(text) == {"x": True}

    def test_json_embedded_in_prose(self):
        text = 'The answer is: {"decision": "BASE_CASE", "rationale": "test"} and that is final.'
        result = _extract_json_from_text(text)
        assert result == {"decision": "BASE_CASE", "rationale": "test"}

    def test_nested_braces(self):
        text = 'Result: {"outer": {"inner": 42}}'
        assert _extract_json_from_text(text) == {"outer": {"inner": 42}}

    def test_braces_in_strings_ignored(self):
        text = '{"msg": "use {x} here"}'
        assert _extract_json_from_text(text) == {"msg": "use {x} here"}

    def test_no_json_returns_none(self):
        assert _extract_json_from_text("no json here at all") is None

    def test_invalid_json_in_braces_returns_none(self):
        assert _extract_json_from_text("result: {not valid json}") is None


class TestLoadJsonTextFallback:
    def test_direct_json_works(self):
        assert _load_json_text('{"a": 1}', provider="test") == {"a": 1}

    def test_prose_wrapped_json_extracted(self):
        text = "Here is my response:\n```json\n{\"verdict\": \"pass\"}\n```"
        assert _load_json_text(text, provider="test") == {"verdict": "pass"}

    def test_no_json_raises(self):
        with pytest.raises(LLMClientRuntimeError, match="not valid JSON"):
            _load_json_text("just plain text", provider="bedrock")
