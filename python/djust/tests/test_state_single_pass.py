"""The Rust state sync's single walk equals the three walks it replaces.

``djust.state_pass`` folds change detection's structural fingerprint
(``deep_fingerprint``), the SafeString scan (``_collect_safe_keys``) and the
conversion for the Rust renderer (``normalize_django_value``) into one
traversal. It is a performance change only, so the contract is equality:

* the fingerprint and ``truncated`` flag are ``deep_fingerprint``'s, always;
* when the walk converts a value, its safe paths and rendered value are the
  two original functions' outputs exactly (types included, element for
  element), and when it declines it says so (``None``) and the sync runs the
  originals.

``TestCorpus`` checks that over a generated corpus of state values: nested
dicts / lists / tuples / sets, SafeStrings, ``Decimal`` / ``UUID`` / the
datetime family, lazy strings, models, dataclasses, subclasses, odd dict keys,
cycles, values deeper than the fingerprint's cap and lists past its budget.
``TestSync`` drives ``_sync_state_to_rust`` itself, with the walk and with the
three original passes, and compares what reaches the Rust view.
"""

from __future__ import annotations

import dataclasses
import enum
import logging
import math
import random
import uuid
from collections import OrderedDict, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any, List

import pytest
from django.utils.safestring import SafeString, mark_safe
from django.utils.translation import gettext_lazy

from djust import state_pass
from djust.change_detection import deep_fingerprint
from djust.mixins import rust_bridge
from djust.mixins.rust_bridge import _collect_safe_keys
from djust.serialization import normalize_django_value

SEED = 3578
CORPUS_SIZE = 4000


class _Color(enum.IntEnum):
    RED = 1


class _MyStr(str):
    pass


class _MyList(list):
    pass


class _MyDict(dict):
    pass


class _Plain:
    def __str__(self) -> str:
        return "plain"


@dataclasses.dataclass
class _Point:
    x: int
    y: int


def _model():
    from django.contrib.auth.models import Group

    return Group(name="g")


def _leaf(rng: random.Random) -> Any:
    pick = rng.randrange(34)
    return [
        lambda: rng.randrange(-5, 5),
        lambda: rng.choice([0, 1, True, False]),
        lambda: rng.choice([0.0, -0.0, 1.5, math.inf, math.nan, 1e300]),
        lambda: rng.choice(["", "a", "ü", "<b>x</b>", "a.b"]),
        lambda: None,
        lambda: b"raw",
        lambda: mark_safe("<i>%d</i>" % rng.randrange(9)),
        lambda: mark_safe(""),
        lambda: SafeString("s"),
        lambda: _MyStr("sub"),
        lambda: _Color.RED,
        lambda: Decimal(rng.choice(["1.10", "12345678901234567890.123456789", "NaN", "-0"])),
        lambda: uuid.UUID(int=rng.getrandbits(128)),
        lambda: datetime(2026, 10, 21, 12, 30, 15, 123456, tzinfo=timezone.utc),
        lambda: datetime(2026, 1, 2, 3, 4, 5),
        lambda: date(2026, 2, 3),
        lambda: time(4, 5, 6),
        lambda: timedelta(rng.randrange(3)),
        lambda: {rng.randrange(5), "s"},
        lambda: frozenset({1, 2}),
        lambda: set(),
        lambda: gettext_lazy("Hello"),
        lambda: _Plain(),
        lambda: _Point(1, 2),
        lambda: len,
        lambda: _model(),
        lambda: OrderedDict(a=1),
        lambda: defaultdict(list, a=[1]),
        lambda: _MyDict(a=mark_safe("<b/>")),
        lambda: _MyList([1, mark_safe("x")]),
        lambda: complex(1, 2),
        lambda: rng.getrandbits(80),
        lambda: rng.choice(["x" * 3, "row number %d" % rng.randrange(300)]),
        lambda: rng.random(),
    ][pick]()


def _key(rng: random.Random) -> Any:
    return rng.choice(
        [
            "a",
            "b",
            "content",
            "",
            "0",
            0,
            1,
            -1,
            None,
            True,
            2.5,
            ("t", 1),
            "k%d" % rng.randrange(50),
        ]
    )


def _value(rng: random.Random, depth: int) -> Any:
    if depth <= 0 or rng.random() < 0.25:
        return _leaf(rng)
    kind = rng.randrange(6)
    n = rng.randrange(0, 6)
    if kind in (0, 1):
        return {_key(rng): _value(rng, depth - 1) for _ in range(n)}
    if kind in (2, 3):
        return [_value(rng, depth - 1) for _ in range(n)]
    if kind == 4:
        return tuple(_value(rng, depth - 1) for _ in range(n))
    # A plain table-row list, the shape that motivated the walk.
    return [
        {"id": i, "name": "row %d" % i, "active": i % 2 == 0, "tags": ["x", str(i)]}
        for i in range(n * 3)
    ]


def _special_values() -> List[Any]:
    out: List[Any] = []
    cyc_d: dict = {"a": 1}
    cyc_d["self"] = cyc_d
    cyc_l: list = [1]
    cyc_l.append(cyc_l)
    out += [cyc_d, cyc_l, {"x": [cyc_l]}]
    deep: Any = mark_safe("bottom")
    for i in range(40):  # deeper than the fingerprint's depth cap (32)
        deep = [deep] if i % 2 else {"d": deep}
    out.append(deep)
    shared = {"s": mark_safe("<b>shared</b>")}
    out.append([shared, shared, {"again": shared}])  # repeated, not cyclic
    # Past the default budget (20 000 nodes): truncated fingerprint.
    out.append([{"id": i, "v": "x", "s": mark_safe("y")} for i in range(6000)])
    out.append({"rows": [[i, str(i), i * 0.5] for i in range(8000)]})
    out.append([])
    out.append({})
    out.append(())
    out.append({None: {0: {"": mark_safe("falsy prefix")}}})
    out.append({"": [mark_safe("under an empty key")]})
    return out


def _corpus() -> List[Any]:
    rng = random.Random(SEED)
    return [_value(rng, rng.randrange(1, 6)) for _ in range(CORPUS_SIZE)] + _special_values()


def _same(a: Any, b: Any) -> bool:
    """Equal, with the same types, all the way down (NaN equals itself)."""
    if a is b:
        return True
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return list(a.keys()) == list(b.keys()) and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, (list, tuple)):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    if isinstance(a, float) and math.isnan(a):
        return math.isnan(b)
    if isinstance(a, Decimal) and a.is_nan():
        return b.is_nan()
    return a == b


@pytest.fixture(autouse=True)
def _silence_fallback_warnings():
    # normalize_django_value warns once per non-serializable leaf; the corpus
    # has thousands on purpose.
    logger = logging.getLogger("djust.serialization")
    level = logger.level
    logger.setLevel(logging.CRITICAL)
    yield
    logger.setLevel(level)


def _originals(value: Any, prefix: str):
    """The two passes the walk replaces, or the exception they raise."""
    try:
        return _collect_safe_keys(value, prefix), normalize_django_value(value)
    except RecursionError:
        return "RecursionError"


class TestCorpus:
    def test_corpus_outputs_equal_the_three_original_passes(self):
        corpus = _corpus()
        converted = declined = 0
        for i, value in enumerate(corpus):
            # An empty top-level key is legal, and changes how
            # ``_collect_safe_keys`` spells (and whether it keeps) a path.
            prefix = ("", "key", "a.b")[i % 3]
            for budget in (20_000, 37, 3):
                fp, truncated, conv = state_pass.fingerprint_and_convert(value, prefix, budget)
                assert (fp, truncated) == deep_fingerprint(value, budget), (i, budget)
                if conv is not None:
                    expected = _originals(value, prefix)
                    assert expected != "RecursionError", i
                    assert conv[0] == expected[0], (i, budget)
                    assert _same(conv[1], expected[1]), (i, budget)
            conv = state_pass.convert_for_render(value, prefix)
            if conv is None:
                declined += 1
            else:
                converted += 1
                expected = _originals(value, prefix)
                assert conv[0] == expected[0], i
                assert _same(conv[1], expected[1]), i
        # Not vacuous: most values take the walk, and some are declined.
        assert converted > len(corpus) // 4, (converted, declined)
        assert declined > 100, (converted, declined)

    def test_plain_table_rows_are_converted_not_declined(self):
        rows = [
            {"id": i, "name": "row %d" % i, "score": Decimal("1.5"), "html": mark_safe("<b/>")}
            for i in range(300)
        ]
        fp, truncated, conv = state_pass.fingerprint_and_convert(rows, "rows")
        assert conv is not None and not truncated
        assert conv[0] == _collect_safe_keys(rows, "rows")
        assert conv[0][:2] == ["rows.0.html", "rows.1.html"]
        assert _same(conv[1], normalize_django_value(rows))

    def test_cycles_and_overdeep_values_are_declined(self):
        cyc: dict = {}
        cyc["me"] = cyc
        assert state_pass.fingerprint_and_convert(cyc, "c")[2] is None
        assert state_pass.convert_for_render(cyc, "c") is None
        deep: Any = 1
        for _ in range(40):
            deep = [deep]
        assert state_pass.convert_for_render(deep, "d") is None

    def test_truncated_fingerprint_still_converts(self):
        big = [{"i": i, "s": mark_safe("x")} for i in range(8000)]
        fp, truncated, conv = state_pass.fingerprint_and_convert(big, "big")
        assert truncated
        assert (fp, truncated) == deep_fingerprint(big)
        assert conv is not None
        assert conv[0] == _collect_safe_keys(big, "big")
        assert _same(conv[1], normalize_django_value(big))


# --- the sync itself ---------------------------------------------------------


def _original_fingerprint_and_convert(value, prefix, budget=20_000):
    fp, truncated = deep_fingerprint(value, budget)
    return fp, truncated, None


def _original_convert_for_render(value, prefix):
    return None


class _Recorder:
    """Forwards to the real Rust view and records what the sync hands it."""

    def __init__(self, inner, log):
        self._inner = inner
        self._log = log

    def update_state(self, state):
        self._log.append(("update_state", state))
        return self._inner.update_state(state)

    def mark_safe_keys(self, keys):
        self._log.append(("mark_safe_keys", list(keys)))
        return self._inner.mark_safe_keys(keys)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _make_view():
    from django.contrib.auth.models import AnonymousUser
    from django.contrib.sessions.backends.signed_cookies import SessionStore
    from django.test import RequestFactory

    from djust import LiveView

    class _Page(LiveView):
        exposure_policy = "legacy"
        template = (
            "<div dj-root><h1>{{ title }}</h1>{{ banner }}"
            "{% for r in rows %}<p class='{{ r.cls }}'>{{ r.id }} {{ r.name }} {{ r.html }}"
            " {{ r.when|date:'Y' }} {{ r.price }} {{ r.tags|join:',' }}</p>{% endfor %}"
            "{% for k, v in meta.items %}<i>{{ k }}={{ v }}</i>{% endfor %}"
            "<b>{{ picked }}</b>{{ pair.0 }}{{ uid }}</div>"
        )

        def mount(self, request, **kwargs):
            self.title = "t"
            self.banner = mark_safe("<em>hi</em>")
            self.picked = 0
            self.rows = [
                {
                    "id": i,
                    "name": "row %d" % i,
                    "cls": "a" if i % 2 else "b",
                    "html": mark_safe("<u>%d</u>" % i),
                    "when": date(2026, 1, 1 + i % 28),
                    "price": Decimal("%d.10" % i),
                    "tags": ["x", str(i)],
                }
                for i in range(60)
            ]
            self.meta = {"owner": mark_safe("<s>o</s>"), "n": 3, "set": {1}}
            self.pair = ("left", mark_safe("<b>r</b>"))
            self.uid = uuid.UUID(int=7)
            self.odd = OrderedDict(z=1)  # declined by the walk: original passes

    request = RequestFactory().get("/p/")
    request.user = AnonymousUser()
    request.session = SessionStore()
    view = _Page()
    view.request = request
    view.mount(request)
    view._initialize_rust_view(request)
    return view


def _drive(monkeypatch, *, single_pass: bool):
    if not single_pass:
        monkeypatch.setattr(
            rust_bridge, "fingerprint_and_convert", _original_fingerprint_and_convert
        )
        monkeypatch.setattr(rust_bridge, "convert_for_render", _original_convert_for_render)
    view = _make_view()
    log: list = []
    view._rust_view = _Recorder(view._rust_view, log)
    html = []
    view._sync_state_to_rust()  # first render: every key sent
    html.append(view._rust_view.render())
    # An event that changes a name (explicit changed keys) ...
    view.picked += 1
    view._changed_keys = {"picked"}
    view._sync_state_to_rust()
    html.append(view._rust_view.render())
    # ... one that edits a row in place (found by fingerprint compare) ...
    view.rows[3]["name"] = "renamed"
    view.rows[3]["html"] = mark_safe("<u>new</u>")
    view._changed_keys = {"picked"}
    view._sync_state_to_rust()
    html.append(view._rust_view.render())
    # ... a reassignment with no changed keys, and a new key.
    view.meta = {"owner": mark_safe("<s>p</s>"), "n": 4, "when": time(1, 2)}
    view.extra = [mark_safe("<q>e</q>")]
    view._sync_state_to_rust()
    html.append(view._rust_view.render())
    monkeypatch.undo()
    return log, html


class TestSync:
    def test_sync_hands_rust_the_same_values_and_safe_keys(self, monkeypatch):
        new_log, new_html = _drive(monkeypatch, single_pass=True)
        old_log, old_html = _drive(monkeypatch, single_pass=False)
        assert [k for k, _ in new_log] == [k for k, _ in old_log]
        for (kind, new), (_, old) in zip(new_log, old_log):
            if kind == "mark_safe_keys":
                assert new == old
            else:
                # Each view gets its own random CSRF token.
                new, old = dict(new), dict(old)
                assert ("csrf_token" in new) == ("csrf_token" in old)
                new.pop("csrf_token", None)
                old.pop("csrf_token", None)
                assert _same(new, old), kind
        assert new_html == old_html
        # Not vacuous: the first sync sent rows with their safe paths, and the
        # in-place row edit was sent again.
        sent = [s for k, s in new_log if k == "update_state"]
        assert "rows" in sent[0] and "rows" in sent[2] and "rows" not in sent[1]
        safe = [s for k, s in new_log if k == "mark_safe_keys"]
        assert "rows.0.html" in safe[0] and "meta.owner" in safe[0] and "pair.1" in safe[0]
        assert "<u>new</u>" in new_html[2]

    def test_the_walk_actually_runs_in_the_sync(self, monkeypatch):
        calls = {"fused": [], "convert": []}
        real_fused, real_convert = (
            rust_bridge.fingerprint_and_convert,
            rust_bridge.convert_for_render,
        )

        def fused(*a, **k):
            calls["fused"].append(a[1])
            return real_fused(*a, **k)

        def convert(*a, **k):
            calls["convert"].append(a[1])
            return real_convert(*a, **k)

        monkeypatch.setattr(rust_bridge, "fingerprint_and_convert", fused)
        monkeypatch.setattr(rust_bridge, "convert_for_render", convert)
        view = _make_view()
        view._sync_state_to_rust()
        # First render: every structured key is fingerprinted, scanned and
        # converted in one walk, and none needs a second one.
        assert {"rows", "meta", "pair"} <= set(calls["fused"])
        assert not {"rows", "meta", "pair"} & set(calls["convert"])
        calls["fused"].clear()
        view.rows[0]["name"] = "changed"
        view._changed_keys = {"picked"}
        view._sync_state_to_rust()
        # An event: rows is fingerprinted to compare (no conversion, it may be
        # unchanged), found changed, then scanned and converted in one walk.
        assert "rows" not in calls["fused"]
        assert calls["convert"].count("rows") == 1
        assert "meta" not in calls["convert"]  # unchanged: never converted
