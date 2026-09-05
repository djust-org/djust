"""Regression tests for #1285 / #2664 — snapshot truncation warning.

``_snapshot_assigns()`` fingerprints containers structurally through
``djust.change_detection.deep_fingerprint`` under a node budget
(``DEFAULT_BUDGET``). Past the budget the remainder of a value collapses to
``id()`` and in-place mutations inside it are missed, so a one-shot
``logger.warning`` per view class names the attribute (#1285's contract,
re-based on the budget instead of the old 100-item / 50-key thresholds).
"""

import logging

from djust import LiveView
from djust.change_detection import DEFAULT_BUDGET, deep_fingerprint
from djust.websocket import _snapshot_assigns


class _ListView(LiveView):
    pass


class _DictView(LiveView):
    pass


def _reset_truncation_sentinels(*classes):
    for cls in classes:
        if "_djust_warned_snapshot_truncated" in cls.__dict__:
            delattr(cls, "_djust_warned_snapshot_truncated")


def _huge_list():
    return list(range(DEFAULT_BUDGET + 10))


def _huge_dict():
    return {str(i): i for i in range(DEFAULT_BUDGET // 2 + 10)}


class TestSnapshotTruncationWarning:
    def setup_method(self):
        _reset_truncation_sentinels(_ListView, _DictView)

    def test_list_truncation_emits_warning(self, caplog):
        view = _ListView()
        view.items = _huge_list()

        with caplog.at_level(logging.WARNING, logger="djust"):
            _snapshot_assigns(view)

        assert len(caplog.records) == 1
        assert "list 'items' has %d items" % len(view.items) in caplog.text
        assert "fingerprint truncated" in caplog.text
        assert "set_changed_keys" in caplog.text

    def test_list_truncation_suppressed_on_second_call(self, caplog):
        view = _ListView()
        view.items = _huge_list()

        with caplog.at_level(logging.WARNING, logger="djust"):
            _snapshot_assigns(view)
            _snapshot_assigns(view)

        assert len(caplog.records) == 1, "truncation warning must fire only once per class"

    def test_list_within_budget_no_warning(self, caplog):
        """The old 100-item threshold is gone: a 150-dict list is fully
        fingerprinted, and a nested in-place edit inside it IS detected."""
        view = _ListView()
        view.items = [{"id": i} for i in range(150)]

        with caplog.at_level(logging.WARNING, logger="djust"):
            before = _snapshot_assigns(view)
        view.items[120]["id"] = -1
        after = _snapshot_assigns(view)

        assert len(caplog.records) == 0
        assert before["items"] != after["items"]

    def test_empty_list_no_warning(self, caplog):
        view = _ListView()
        view.items = []

        with caplog.at_level(logging.WARNING, logger="djust"):
            _snapshot_assigns(view)

        assert len(caplog.records) == 0

    def test_dict_truncation_emits_warning(self, caplog):
        view = _DictView()
        view.config = _huge_dict()

        with caplog.at_level(logging.WARNING, logger="djust"):
            _snapshot_assigns(view)

        assert len(caplog.records) == 1
        assert "dict 'config' has %d items" % len(view.config) in caplog.text
        assert "fingerprint truncated" in caplog.text

    def test_dict_within_budget_no_warning(self, caplog):
        """The old 50-key threshold is gone: a 60-key dict is fully seen."""
        view = _DictView()
        view.config = {str(i): i for i in range(60)}

        with caplog.at_level(logging.WARNING, logger="djust"):
            before = _snapshot_assigns(view)
        view.config["7"] = "changed"
        after = _snapshot_assigns(view)

        assert len(caplog.records) == 0
        assert before["config"] != after["config"]

    def test_different_view_classes_each_warn_once(self, caplog):
        view1 = _ListView()
        view1.items = _huge_list()
        view2 = _DictView()
        view2.config = _huge_dict()

        with caplog.at_level(logging.WARNING, logger="djust"):
            _snapshot_assigns(view1)
            _snapshot_assigns(view2)

        assert len(caplog.records) == 2

    def test_truncated_fingerprint_reports_truncation(self):
        fp, truncated = deep_fingerprint(_huge_list())
        assert truncated is True
        fp2, truncated2 = deep_fingerprint(list(range(10)))
        assert truncated2 is False
        assert fp != fp2
