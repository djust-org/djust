"""Tests for the `limit` kwarg on StreamsMixin.stream() and stream_prune()."""

from __future__ import annotations

from typing import List

import pytest

from djust.mixins.streams import StreamsMixin


class _FakeItem:
    def __init__(self, pk: int, label: str = "") -> None:
        self.id = pk
        self.label = label or f"item-{pk}"


class _Harness(StreamsMixin):
    """Minimal StreamsMixin host for unit testing."""

    def __init__(self) -> None:
        self._streams: dict = {}
        self._stream_operations: list = []


def _op_types(harness: _Harness) -> List[str]:
    return [op["type"] for op in harness._stream_operations]


def test_stream_limit_emits_prune_top_on_append() -> None:
    """Appending with limit=N emits a prune op with edge='top'."""
    h = _Harness()
    h.stream("feed", [_FakeItem(1), _FakeItem(2), _FakeItem(3)], limit=2)
    prunes = [op for op in h._stream_operations if op["type"] == "stream_prune"]
    assert len(prunes) == 1
    assert prunes[0]["edge"] == "top"
    assert prunes[0]["limit"] == 2
    assert prunes[0]["stream"] == "feed"


def test_stream_limit_emits_prune_bottom_on_prepend() -> None:
    """Prepending (at=0) with limit=N prunes from the bottom edge."""
    h = _Harness()
    h.stream("feed", [_FakeItem(1)], at=0, limit=10)
    prunes = [op for op in h._stream_operations if op["type"] == "stream_prune"]
    assert len(prunes) == 1
    assert prunes[0]["edge"] == "bottom"
    assert prunes[0]["limit"] == 10


def test_stream_without_limit_has_no_prune_op() -> None:
    h = _Harness()
    h.stream("feed", [_FakeItem(1)])
    assert "stream_prune" not in _op_types(h)


def test_stream_prune_explicit_call() -> None:
    h = _Harness()
    h.stream("feed", [_FakeItem(1)])
    h.stream_prune("feed", limit=50, edge="top")
    prunes = [op for op in h._stream_operations if op["type"] == "stream_prune"]
    assert prunes[-1] == {
        "type": "stream_prune",
        "stream": "feed",
        "limit": 50,
        "edge": "top",
    }


def test_stream_prune_requires_initialized_stream() -> None:
    h = _Harness()
    with pytest.raises(ValueError, match="not initialized"):
        h.stream_prune("missing", limit=10)


def test_stream_prune_rejects_negative_limit() -> None:
    h = _Harness()
    h.stream("feed", [_FakeItem(1)])
    with pytest.raises(ValueError, match="limit must be >= 0"):
        h.stream_prune("feed", limit=-1)


def test_stream_prune_rejects_invalid_edge() -> None:
    h = _Harness()
    h.stream("feed", [_FakeItem(1)])
    with pytest.raises(ValueError, match="edge must be"):
        h.stream_prune("feed", limit=5, edge="sideways")


def test_stream_limit_zero_is_allowed() -> None:
    """limit=0 is a legitimate "prune everything older" request."""
    h = _Harness()
    h.stream("feed", [_FakeItem(1), _FakeItem(2)], limit=0)
    prunes = [op for op in h._stream_operations if op["type"] == "stream_prune"]
    assert prunes[0]["limit"] == 0


def test_stream_ops_order_insert_then_prune() -> None:
    """Prune must follow inserts so the new items are preserved client-side."""
    h = _Harness()
    h.stream("feed", [_FakeItem(10), _FakeItem(11)], limit=5)
    types = _op_types(h)
    # Two inserts then one prune.
    assert types == ["stream_insert", "stream_insert", "stream_prune"]
