"""Tests for _cached_extract_template_variables in djust.mixins.jit."""

import hashlib
from unittest.mock import patch

import pytest

from djust.mixins.jit import (
    _cached_extract_template_variables,
    _variable_extraction_cache,
    _VARIABLE_EXTRACTION_CACHE_MAX,
)


@pytest.fixture(autouse=True)
def clear_caches():
    """Clear the extraction cache before each test."""
    _variable_extraction_cache.clear()
    yield
    _variable_extraction_cache.clear()


TEMPLATE_A = "<div>{{ user.name }}</div>"
TEMPLATE_B = "<div>{{ project.title }}</div>"


class TestCachedExtractTemplateVariables:
    """Tests for the _cached_extract_template_variables function."""

    @patch("djust.mixins.jit.extract_template_variables")
    def test_cache_miss_calls_extractor(self, mock_extract):
        """Cache miss should call extract_template_variables and store the result."""
        expected = {"user": ["user.name"]}
        mock_extract.return_value = expected

        result = _cached_extract_template_variables(TEMPLATE_A)

        assert result == expected
        mock_extract.assert_called_once_with(TEMPLATE_A)
        # Verify it was stored in the cache
        content_hash = hashlib.sha256(TEMPLATE_A.encode()).hexdigest()[:8]
        assert _variable_extraction_cache[content_hash] == expected

    @patch("djust.mixins.jit.extract_template_variables")
    def test_cache_hit_skips_extractor(self, mock_extract):
        """Cache hit should return cached result without calling Rust again."""
        expected = {"user": ["user.name"]}
        mock_extract.return_value = expected

        # First call — cache miss
        result1 = _cached_extract_template_variables(TEMPLATE_A)
        # Second call — cache hit
        result2 = _cached_extract_template_variables(TEMPLATE_A)

        assert result1 == expected
        assert result2 == expected
        # Rust extractor should only be called once
        mock_extract.assert_called_once()

    @patch("djust.mixins.jit.extract_template_variables", None)
    def test_returns_none_when_extractor_unavailable(self):
        """Should return None when extract_template_variables is None (Rust unavailable)."""
        result = _cached_extract_template_variables(TEMPLATE_A)
        assert result is None

    @patch("djust.mixins.jit.extract_template_variables")
    def test_different_content_different_cache_entries(self, mock_extract):
        """Different template content should produce different cache entries."""
        result_a = {"user": ["user.name"]}
        result_b = {"project": ["project.title"]}
        mock_extract.side_effect = [result_a, result_b]

        got_a = _cached_extract_template_variables(TEMPLATE_A)
        got_b = _cached_extract_template_variables(TEMPLATE_B)

        assert got_a == result_a
        assert got_b == result_b
        assert mock_extract.call_count == 2

        hash_a = hashlib.sha256(TEMPLATE_A.encode()).hexdigest()[:8]
        hash_b = hashlib.sha256(TEMPLATE_B.encode()).hexdigest()[:8]
        assert hash_a != hash_b
        assert _variable_extraction_cache[hash_a] == result_a
        assert _variable_extraction_cache[hash_b] == result_b

    @patch("djust.mixins.jit.extract_template_variables")
    def test_same_content_same_cache_entry(self, mock_extract):
        """Same template content (even different str objects) should hit the same cache entry."""
        expected = {"user": ["user.name"]}
        mock_extract.return_value = expected

        # Create two distinct string objects with identical content.
        # Use bytearray round-trip to defeat Python string interning.
        base = "<div>{{ x }}</div>"
        content1 = bytearray(base.encode()).decode()
        content2 = bytearray(base.encode()).decode()
        assert content1 is not content2  # different objects
        assert content1 == content2  # same content

        result1 = _cached_extract_template_variables(content1)
        result2 = _cached_extract_template_variables(content2)

        assert result1 == expected
        assert result2 == expected
        mock_extract.assert_called_once()

    @patch("djust.mixins.jit.extract_template_variables")
    def test_cache_cleared_when_exceeding_max(self, mock_extract):
        """Cache should be cleared when it exceeds _VARIABLE_EXTRACTION_CACHE_MAX entries."""
        mock_extract.return_value = {}

        # Fill cache to the max
        for i in range(_VARIABLE_EXTRACTION_CACHE_MAX):
            content = f"<div>{{{{ var_{i} }}}}</div>"
            _cached_extract_template_variables(content)

        assert len(_variable_extraction_cache) == _VARIABLE_EXTRACTION_CACHE_MAX

        # Next new entry should trigger a clear + add the new entry
        new_content = "<div>{{ overflow }}</div>"
        _cached_extract_template_variables(new_content)

        # Cache was cleared then the new entry was added
        assert len(_variable_extraction_cache) == 1
        new_hash = hashlib.sha256(new_content.encode()).hexdigest()[:8]
        assert new_hash in _variable_extraction_cache

    @patch("djust.mixins.jit.extract_template_variables")
    def test_extractor_exception_returns_none_and_caches(self, mock_extract):
        """If extract_template_variables raises, result should be None and cached."""
        mock_extract.side_effect = RuntimeError("Rust FFI failed")

        result = _cached_extract_template_variables(TEMPLATE_A)

        assert result is None
        content_hash = hashlib.sha256(TEMPLATE_A.encode()).hexdigest()[:8]
        assert _variable_extraction_cache[content_hash] is None
