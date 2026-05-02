"""Regression tests for #1285 — snapshot truncation warning.

``_snapshot_assigns()`` truncates content fingerprinting for large
containers: lists ≥ 100 items store only ``(id, length)``; dicts ≥ 50
keys store only ``len(v)`` instead of a tuple of keys. In-place mutations
inside these containers are missed by change detection.

A one-shot ``logger.warning`` is emitted per view class to alert
developers that auto-diff won't detect mutations inside these containers.
"""

import logging

from djust import LiveView
from djust.websocket import _snapshot_assigns, _TRUNCATION_WARNED


class _ListView(LiveView):
    pass


class _DictView(LiveView):
    pass


class TestSnapshotTruncationWarning:
    """#1285: warning emitted on first truncation; suppressed on subsequent."""

    def setup_method(self):
        _TRUNCATION_WARNED.clear()

    def test_list_truncation_emits_warning(self, caplog):
        view = _ListView()
        view.items = [{"id": i} for i in range(150)]

        with caplog.at_level(logging.WARNING, logger="djust"):
            _snapshot_assigns(view)

        assert len(caplog.records) == 1
        assert "list 'items' has 150 items" in caplog.text
        assert "content fingerprint truncated" in caplog.text
        assert "set_changed_keys" in caplog.text

    def test_list_truncation_suppressed_on_second_call(self, caplog):
        view = _ListView()
        view.items = [{"id": i} for i in range(150)]

        with caplog.at_level(logging.WARNING, logger="djust"):
            _snapshot_assigns(view)
            _snapshot_assigns(view)

        assert len(caplog.records) == 1, (
            "#1285: truncation warning must fire only once per view class"
        )

    def test_list_below_threshold_no_warning(self, caplog):
        view = _ListView()
        view.items = [{"id": i} for i in range(99)]

        with caplog.at_level(logging.WARNING, logger="djust"):
            _snapshot_assigns(view)

        assert len(caplog.records) == 0

    def test_empty_list_no_warning(self, caplog):
        view = _ListView()
        view.items = []

        with caplog.at_level(logging.WARNING, logger="djust"):
            _snapshot_assigns(view)

        assert len(caplog.records) == 0

    def test_dict_truncation_emits_warning(self, caplog):
        view = _DictView()
        view.config = {str(i): i for i in range(60)}

        with caplog.at_level(logging.WARNING, logger="djust"):
            _snapshot_assigns(view)

        assert len(caplog.records) == 1
        assert "dict 'config' has 60 keys" in caplog.text
        assert "key fingerprint truncated" in caplog.text

    def test_dict_truncation_suppressed_on_second_call(self, caplog):
        view = _DictView()
        view.config = {str(i): i for i in range(60)}

        with caplog.at_level(logging.WARNING, logger="djust"):
            _snapshot_assigns(view)
            _snapshot_assigns(view)

        assert len(caplog.records) == 1

    def test_dict_below_threshold_no_warning(self, caplog):
        view = _DictView()
        view.config = {str(i): i for i in range(49)}

        with caplog.at_level(logging.WARNING, logger="djust"):
            _snapshot_assigns(view)

        assert len(caplog.records) == 0

    def test_different_view_classes_each_warn_once(self, caplog):
        """Each view class gets its own one-shot warning."""
        view1 = _ListView()
        view1.items = [{"id": i} for i in range(150)]

        view2 = _DictView()
        view2.config = {str(i): i for i in range(60)}

        with caplog.at_level(logging.WARNING, logger="djust"):
            _snapshot_assigns(view1)
            _snapshot_assigns(view2)

        assert len(caplog.records) == 2

    def test_exact_threshold_list_no_warning(self, caplog):
        """List at exactly 99 items is below threshold, no warning."""
        view = _ListView()
        view.items = [{"id": i} for i in range(99)]

        with caplog.at_level(logging.WARNING, logger="djust"):
            _snapshot_assigns(view)

        assert len(caplog.records) == 0

    def test_exact_threshold_dict_no_warning(self, caplog):
        """Dict at exactly 49 keys is below threshold, no warning."""
        view = _DictView()
        view.config = {str(i): i for i in range(49)}

        with caplog.at_level(logging.WARNING, logger="djust"):
            _snapshot_assigns(view)

        assert len(caplog.records) == 0
