"""Per-page CPU on the shared sync thread (many LiveView pages at once).

One process runs every WebSocket session's sync work on one thread, so N
pages mounting at once take about N times one page's CPU. Profiling 40 pages
(``scripts/bench_concurrent_liveviews.py --cprofile``) put most of that CPU in
two pure-Python walks, not in thread hops:

* ``change_detection._walk``: every event fingerprints the whole view state
  about seven times (pre/post snapshot, Rust sync, dirty tracking, context
  fingerprints), and the walk made a Python call per scalar leaf.
* ``_mask_for_root_search``: extracting a rendered page's root masked the
  whole page twice (root search, then close-tag balance), with a
  per-character Python loop over every tag.

These tests pin the cheaper forms and that they return the same results.
"""

from __future__ import annotations

import enum
import os
import subprocess
import sys
import threading

import pytest

from djust import change_detection
from djust.change_detection import _walk_general, deep_fingerprint
from djust.mixins import template as template_mod


# --------------------------------------------------------------------------- #
# change_detection: fast walk == general walk
# --------------------------------------------------------------------------- #


class _SafeStr(str):
    pass


class _Color(enum.IntEnum):
    RED = 1


class _DictSub(dict):
    pass


class _Marked:
    _djust_fingerprint_state = True
    fingerprint_fields = ("walked",)

    def __init__(self, state):
        self.state = state


def _general_fingerprint(value, budget=change_detection.DEFAULT_BUDGET):
    counter = [budget]
    return _walk_general(value, counter, 0, ()), counter[0] < 0


def _corpus():
    rows = [
        {"id": i, "name": f"row {i}", "score": i * 1.5, "active": i % 2 == 0, "tag": None}
        for i in range(60)
    ]
    cyclic: list = [1, "a"]
    cyclic.append(cyclic)
    deep: list = []
    cur = deep
    for _ in range(40):  # deeper than the depth cap
        nxt: list = []
        cur.append(nxt)
        cur = nxt
    shared = object()
    return [
        rows,
        {"rows": rows, "title": "t", "count": 3, b"k": b"v", 1: True, (1, 2): (3, [4])},
        [_SafeStr("safe"), _Color.RED, 2**80, float("inf"), shared, shared],
        {"sub": _DictSub(a=[1, 2]), "set": {1, "x", None}, "fs": frozenset({(1, 2)})},
        _Marked({"walked": [1, {"k": "v"}], "leaf": [1, 2], "n": 3}),
        [_Marked({"walked": rows[:3]})],
        cyclic,
        deep,
        (),
        {},
        "plain",
        None,
    ]


@pytest.mark.parametrize("budget", [change_detection.DEFAULT_BUDGET, 0, 1, 2, 5, 17, 100, 333])
def test_fast_walk_matches_general_walk(budget):
    """The fast path returns the general walk's fingerprint and truncation,
    node for node, including where a small budget runs out."""
    for value in _corpus():
        assert deep_fingerprint(value, budget) == _general_fingerprint(value, budget), value


def test_fast_walk_still_sees_in_place_mutation():
    """The point of the deep walk (#2664) survives: an append is a change."""
    rows = [{"id": 1, "done": False}]
    before = deep_fingerprint(rows)[0]
    rows[0]["done"] = True
    assert deep_fingerprint(rows)[0] != before
    rows.append({"id": 2, "done": False})
    assert deep_fingerprint(rows)[0] != deep_fingerprint(rows[:1])[0]


def test_fast_walk_makes_no_call_per_scalar_leaf(monkeypatch):
    """A list of N flat dicts costs N + 1 walk calls, not one per key and value.

    Before this change every key and every scalar value was its own ``_walk`` call:
    300 rows of 5 keys were 3,301 calls per fingerprint, about seven times per
    event, all on the shared sync thread.
    """
    rows = [
        {"id": i, "name": f"row {i}", "score": i, "active": True, "x": None} for i in range(300)
    ]
    calls = [0]
    original = change_detection._walk

    def counting(*args):
        calls[0] += 1
        return original(*args)

    monkeypatch.setattr(change_detection, "_walk", counting)
    deep_fingerprint(rows)
    assert calls[0] == len(rows) + 1


# --------------------------------------------------------------------------- #
# Root extraction: one mask per page, same output
# --------------------------------------------------------------------------- #


def _page(rows: int = 50) -> str:
    items = "".join(
        f'<li class="row" data-id="{i}" title="a &lt;b&gt; \'q\'"><span>{i}</span>'
        f'<button dj-click="pick" data-id="{i}">Pick</button></li>'
        for i in range(rows)
    )
    return (
        "<!DOCTYPE html><html><head><script>var s = '<div dj-root>';</script></head><body>"
        '<div dj-view="app.V" dj-root><!-- <div> --><ul>' + items + "</ul>"
        "<textarea>&lt;/div&gt; <div></textarea></div></body></html>"
    )


def _view():
    from djust import LiveView

    return LiveView()


def test_extract_masks_the_page_once(monkeypatch):
    """Root search and close-tag balance share one mask of the same string."""
    calls = [0]
    original = template_mod._mask_for_root_search_uncached

    def counting(html):
        calls[0] += 1
        return original(html)

    monkeypatch.setattr(template_mod, "_mask_for_root_search_uncached", counting)
    html = _page()
    inner = _view()._extract_liveview_content(html)
    assert calls[0] == 1
    assert inner.startswith("<!-- <div> --><ul>")
    assert inner.endswith("</textarea>")


def test_mask_memo_is_per_string_object_and_per_thread():
    """A new string (even with the same text) is masked afresh, and the
    memo never hands one thread another thread's entry."""
    view = _view()
    html = _page(5)
    first = view._extract_liveview_content(html)
    same_text = "".join(list(html))  # equal, but a different object
    assert same_text is not html
    assert view._extract_liveview_content(same_text) == first

    other = _page(7)
    assert view._extract_liveview_content(other) != first

    seen = {}

    def worker():
        template_mod._mask_for_root_search(other)
        seen["source"] = template_mod._mask_memo.source

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    assert seen["source"] is other
    template_mod._mask_for_root_search(html)
    assert template_mod._mask_memo.source is html


@pytest.mark.parametrize(
    "html",
    [
        "<div a=b c='<x>' d=\"</div>\" e>",
        "<div\ta = 'v'\n/ b=c/>",
        "<p =x y==z>",
        "<a b='unterminated",
        "<div dj-root><span x=1>t</span></div>",
        "</div foo='>'>",
        "<é a=b>",
        "<div a=b",
    ],
)
def test_scan_html_tag_offsets(html):
    """The regex-based scanner keeps the documented (end, spans) contract."""
    end, attrs = template_mod._scan_html_tag(html, 0)
    if end >= 0:
        assert html[end - 1] == ">"
    for begin, name_end, value_start, attr_end in attrs:
        # A valueless attribute ends at its name; value_start may then sit
        # past it (after trailing white space), as before.
        assert 0 < begin < name_end <= attr_end <= len(html)
        assert name_end <= value_start <= len(html)


# --------------------------------------------------------------------------- #
# Mount-frame decorator config: no property is evaluated
# --------------------------------------------------------------------------- #


def _config_view_class():
    from functools import cached_property

    from djust import LiveView
    from djust.decorators import cache, debounce, event_handler, throttle

    class ConfigView(LiveView):
        template = '<div dj-view="x.ConfigView" dj-id="0">{{ rows|length }}</div>'
        reads: list = []

        @property
        def total(self):
            # An app property: an ORM read here raises on the event loop.
            type(self).reads.append("total")
            raise RuntimeError("You cannot call this from an async context")

        @cached_property
        def report(self):
            type(self).reads.append("report")
            return 1

        @event_handler()
        @debounce(wait=0.5)
        def search(self, **kwargs):
            pass

        @event_handler()
        @throttle(interval=0.25)
        def scroll(self, **kwargs):
            pass

        @event_handler()
        @cache(ttl=30)
        def lookup(self, **kwargs):
            pass

        @staticmethod
        @debounce(wait=0.1)
        def static_handler(**kwargs):
            pass

    return ConfigView


def _runtime():
    from djust.runtime import ViewRuntime
    from djust.tests.test_runtime_state_save_tt_1894 import MockTransport

    return ViewRuntime(MockTransport())


def test_decorator_config_survives_a_raising_property():
    """A public property that raises no longer drops the whole config, and no
    property or cached_property runs during the scan."""
    cls = _config_view_class()
    view = cls()
    runtime = _runtime()
    handler_config = runtime._extract_handler_config(view)
    cache_config = runtime._extract_cache_config(view)
    assert handler_config == {
        "search": {"debounce": {"wait": 0.5, "max_wait": None}},
        "scroll": {"throttle": {"interval": 0.25, "leading": True, "trailing": True}},
        "static_handler": {"debounce": {"wait": 0.1, "max_wait": None}},
    }
    assert cache_config == {"lookup": {"ttl": 30, "key_params": []}}
    assert cls.reads == []
    assert "report" not in vars(view)


def test_decorator_config_does_not_fingerprint_the_state(monkeypatch):
    """``changed_fields`` / ``is_dirty`` are properties that walk the whole
    view state; the mount-frame scan read both, twice per mount."""
    from djust.live_view import LiveView

    cls = _config_view_class()
    view = cls()
    view.rows = [{"id": i} for i in range(100)]
    view._capture_dirty_baseline()
    calls = [0]
    original = LiveView._dirty_fingerprint

    def counting(self):
        calls[0] += 1
        return original(self)

    monkeypatch.setattr(LiveView, "_dirty_fingerprint", counting)
    runtime = _runtime()
    runtime._extract_handler_config(view)
    runtime._extract_cache_config(view)
    assert calls[0] == 0


# --------------------------------------------------------------------------- #
# The benchmark script keeps running
# --------------------------------------------------------------------------- #


def test_benchmark_script_runs():
    """``scripts/bench_concurrent_liveviews.py`` (the measurement behind these fixes) runs
    end to end at a tiny size and reports both phases and the hop profile."""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    script = os.path.join(root, "scripts", "bench_concurrent_liveviews.py")
    env = {k: v for k, v in os.environ.items() if k != "DJANGO_SETTINGS_MODULE"}
    proc = subprocess.run(
        [sys.executable, script, "-n", "3", "--rows", "10", "--solo", "1", "--profile"],
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
        cwd=root,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    out = proc.stdout
    assert "3 pages at once:" in out
    assert "mount: " in out and "event: " in out
    assert "/session" in out
