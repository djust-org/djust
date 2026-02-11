"""Tests for context processor resolution caching in ContextMixin."""

from unittest.mock import patch, MagicMock

import pytest

from djust.mixins.context import (
    ContextMixin,
    _resolved_processors_cache,
    _context_processors_cache,
    _clear_processor_caches,
)


@pytest.fixture(autouse=True)
def clear_cache():
    """Ensure the caches are empty before each test."""
    _resolved_processors_cache.clear()
    _context_processors_cache.clear()
    yield
    _resolved_processors_cache.clear()
    _context_processors_cache.clear()


class TestResolvedProcessorsCaching:
    """Tests for ContextMixin._get_resolved_processors caching behavior."""

    @patch("django.utils.module_loading.import_string")
    def test_import_string_called_once_per_processor(self, mock_import):
        """import_string() is called only once per processor across multiple calls."""
        proc_a = MagicMock(name="proc_a")
        proc_b = MagicMock(name="proc_b")
        mock_import.side_effect = [proc_a, proc_b]

        paths = ["app.processors.a", "app.processors.b"]

        result1 = ContextMixin._get_resolved_processors(paths)
        result2 = ContextMixin._get_resolved_processors(paths)

        assert result1 == [proc_a, proc_b]
        assert result2 == [proc_a, proc_b]
        # import_string should only be called twice total (once per path),
        # NOT four times (twice per call)
        assert mock_import.call_count == 2

    @patch("django.utils.module_loading.import_string")
    def test_failed_import_not_cached(self, mock_import):
        """Failed imports don't poison the cache -- retry is attempted next call."""
        proc_a = MagicMock(name="proc_a")

        # First call: proc_a succeeds, proc_b fails
        mock_import.side_effect = [proc_a, ImportError("no module named b")]
        paths = ["app.processors.a", "app.processors.b"]

        result1 = ContextMixin._get_resolved_processors(paths)
        assert result1 == [proc_a]
        # Should NOT be cached since one import failed
        assert tuple(paths) not in _resolved_processors_cache

        # Second call: both succeed (module became available)
        proc_b = MagicMock(name="proc_b")
        mock_import.side_effect = [proc_a, proc_b]

        result2 = ContextMixin._get_resolved_processors(paths)
        assert result2 == [proc_a, proc_b]
        # NOW it should be cached since all succeeded
        assert tuple(paths) in _resolved_processors_cache

    @patch("django.utils.module_loading.import_string")
    def test_all_succeed_result_is_cached(self, mock_import):
        """When all imports succeed, the result is cached for subsequent calls."""
        proc_a = MagicMock(name="proc_a")
        mock_import.return_value = proc_a

        paths = ["app.processors.a"]

        ContextMixin._get_resolved_processors(paths)
        assert tuple(paths) in _resolved_processors_cache
        assert _resolved_processors_cache[tuple(paths)] == [proc_a]

        # Subsequent call should NOT call import_string again
        mock_import.reset_mock()
        result = ContextMixin._get_resolved_processors(paths)
        assert result == [proc_a]
        mock_import.assert_not_called()

    @patch("django.utils.module_loading.import_string")
    def test_different_processor_lists_cached_separately(self, mock_import):
        """Different processor path lists produce different cache entries."""
        proc_a = MagicMock(name="proc_a")
        proc_b = MagicMock(name="proc_b")
        proc_c = MagicMock(name="proc_c")

        mock_import.side_effect = [proc_a, proc_b, proc_c]

        paths_1 = ["app.processors.a"]
        paths_2 = ["app.processors.b", "app.processors.c"]

        result1 = ContextMixin._get_resolved_processors(paths_1)
        result2 = ContextMixin._get_resolved_processors(paths_2)

        assert result1 == [proc_a]
        assert result2 == [proc_b, proc_c]
        assert tuple(paths_1) in _resolved_processors_cache
        assert tuple(paths_2) in _resolved_processors_cache
        assert len(_resolved_processors_cache) == 2


class TestSettingChangedSignalClearsCaches:
    """Tests that setting_changed signal clears processor caches."""

    def test_templates_setting_clears_both_caches(self):
        """When TEMPLATES setting changes, both caches are cleared."""
        # Populate both caches
        _resolved_processors_cache[("a.b",)] = ["fake_callable"]
        _context_processors_cache[("some.backend",)] = ["processor.path"]

        assert len(_resolved_processors_cache) == 1
        assert len(_context_processors_cache) == 1

        # Simulate what Django does during @override_settings(TEMPLATES=...)
        _clear_processor_caches(setting="TEMPLATES")

        assert len(_resolved_processors_cache) == 0
        assert len(_context_processors_cache) == 0

    def test_non_templates_setting_does_not_clear_caches(self):
        """Changing a setting other than TEMPLATES leaves caches intact."""
        _resolved_processors_cache[("a.b",)] = ["fake_callable"]
        _context_processors_cache[("some.backend",)] = ["processor.path"]

        _clear_processor_caches(setting="DEBUG")

        assert len(_resolved_processors_cache) == 1
        assert len(_context_processors_cache) == 1

    def test_signal_handler_connected(self):
        """Verify the signal handler is actually connected to setting_changed."""
        from django.test.signals import setting_changed

        # Check that _clear_processor_caches is among the receivers
        receivers = [ref for ref in setting_changed.receivers]
        handler_connected = any(
            getattr(r[1](), "__name__", None) == "_clear_processor_caches"
            for r in receivers
            if r[1]() is not None
        )
        assert (
            handler_connected
        ), "_clear_processor_caches is not connected to setting_changed signal"
