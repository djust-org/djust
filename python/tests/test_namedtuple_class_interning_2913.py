"""#2913 — a ``Value::NamedTuple`` crosses back to Python as ONE class per
``(name, fields)`` shape, not a new class per value.

Every bridged tag call converts the whole context (`build_py_context`), so a
page holding ten ``{% regroup %}`` rows and a ``{% url %}`` per sidebar link
minted ~1 760 ``collections.namedtuple`` classes per render — 5 277 ``exec``
calls over three renders, ~50 ms per render. The class is now interned by shape.
"""

from __future__ import annotations

import collections

import django
from django.conf import settings

if not settings.configured:
    settings.configure(
        SECRET_KEY="test-2913",
        INSTALLED_APPS=[],
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "DIRS": [],
                "APP_DIRS": False,
                "OPTIONS": {},
            }
        ],
    )
    django.setup()

from django import template as dj_template  # noqa: E402

from djust._rust import RustLiveView, render_template  # noqa: E402

Row = collections.namedtuple("Row", ["label", "n"])
Other = collections.namedtuple("Row", ["label", "m"])  # same name, different fields
Point = collections.namedtuple("Point", ["x", "y"])
Size = collections.namedtuple("Size", ["x", "y"])  # same fields, different name


def _round_trip(value):
    view = RustLiveView("{{ x }}")
    view.update_state({"x": value})
    return view.get_state()["x"]


class TestOneClassPerShape:
    def test_same_shape_shares_one_class_across_conversions(self):
        a = _round_trip(Row("a", 1))
        b = _round_trip(Row("b", 2))
        assert type(a) is type(b), "one class per (name, fields), as Python itself gives"
        assert a._fields == ("label", "n") and a.label == "a" and a[1] == 1
        assert a == Row("a", 1)

    def test_different_fields_stay_different_classes(self):
        a = _round_trip(Row("a", 1))
        o = _round_trip(Other("a", 1))
        assert type(a) is not type(o)
        assert o._fields == ("label", "m")

    def test_same_fields_different_name_stay_different_classes(self):
        """Review M3: a key on the fields alone would render every ``Size`` as
        ``Point(x=…, y=…)``."""
        p = _round_trip(Point(1, 2))
        s = _round_trip(Size(3, 4))
        assert type(p) is not type(s)
        assert type(s).__name__ == "Size" and type(p).__name__ == "Point"

    def test_module_is_deterministic(self):
        a = _round_trip(Row("a", 1))
        assert type(a).__module__ == "djust._rust"

    def test_rows_inside_a_list_share_the_class(self):
        rows = _round_trip([Row("a", 1), Row("b", 2), Row("c", 3)])
        assert len({type(r) for r in rows}) == 1


class TestBridgedTagsDoNotRemint:
    def test_two_bridged_calls_see_the_same_class(self):
        """The #2913 shape: ``{% regroup %}`` binds Rust-born namedtuples
        (``GroupedResult``) into the context; a bridged tag inside the loop
        receives them converted — the class must not be minted per call, and
        every call must see ONE class. Counted by spying on
        ``collections.namedtuple`` itself. (A namedtuple passed in from Python
        reaches the tag through the raw sidecar as its ORIGINAL class, so it
        would not exercise this arm.)"""
        register = dj_template.Library()
        seen: list = []

        @register.simple_tag(takes_context=True)
        def probe(context):
            seen.append(type(context["groups"][0]))
            return ""

        from djust import template_libraries as tl
        from djust._rust import unregister_tag_handler

        tl._bridge_library("lib2913", register)
        try:
            self._render(seen)
        finally:
            unregister_tag_handler("probe")

    def _render(self, seen):
        minted: list = []
        real = collections.namedtuple

        def spy(*a, **k):
            minted.append(a[0])
            return real(*a, **k)

        collections.namedtuple = spy
        try:
            render_template(
                "{% regroup rows by cat as groups %}{% for i in items %}{% probe %}{% endfor %}",
                {"rows": [{"cat": "a", "n": 1}, {"cat": "b", "n": 2}], "items": [1, 2, 3, 4, 5]},
            )
        finally:
            collections.namedtuple = real
        assert len(seen) == 5
        assert len(set(seen)) == 1, "five bridged calls, one class"
        assert seen[0].__name__ == "GroupedResult"
        assert seen[0].__module__ == "djust._rust", "a Rust-born namedtuple, not the sidecar's"
        assert minted.count("GroupedResult") <= 1, f"class minted per call: {minted}"

    def test_the_spy_is_not_vacuous(self):
        """The counting mechanism sees a real mint."""
        minted: list = []
        real = collections.namedtuple

        def spy(*a, **k):
            minted.append(a[0])
            return real(*a, **k)

        collections.namedtuple = spy
        try:
            real("Probe2913", ["x"])
            collections.namedtuple("Probe2913b", ["x"])
        finally:
            collections.namedtuple = real
        assert minted == ["Probe2913b"]
