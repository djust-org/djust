"""#2914 — the context a bridged inline tag receives is memoised per frame.

Every bridged tag call converted the WHOLE context into a fresh Python dict
(`build_py_context`), O(context) per call — 75 % of a storybook render with a
`{% url %}` per sidebar row. A frame's contents change only through
`DerefMut`, which re-stamps it, so a converted frame is now reused until then.
What the tag sees is the same dict with the same names and values; what
changed is that two calls in one render receive the SAME converted objects.
"""

from __future__ import annotations

import gc

import django
import pytest
from django.conf import settings

if not settings.configured:
    settings.configure(
        SECRET_KEY="test-2914",
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
from django.template import Context, Engine  # noqa: E402
from django.utils.safestring import SafeString  # noqa: E402

from djust import template_libraries as tl  # noqa: E402
from djust._rust import render_template, unregister_tag_handler  # noqa: E402


@pytest.fixture
def lib():
    """A library of probe tags, bridged for the test and torn down after."""
    register = dj_template.Library()
    seen: dict = {"ids": [], "vals": [], "types": [], "bag": []}

    @register.simple_tag(takes_context=True)
    def probe_rows(context):
        # A RUST-BORN value (`{% regroup %}` output), held by reference: a
        # Python-supplied list reaches the tag through the raw sidecar as the
        # caller's own object on every path, and a freed dict's id() is reused
        # by the next conversion (#774) — either would pass without the memo.
        seen["ids"].append(context["groups"])
        return ""

    @register.simple_tag(takes_context=True)
    def probe_v(context):
        seen["vals"].append(context.get("v"))
        return ""

    @register.simple_tag(takes_context=True)
    def probe_i(context):
        seen["vals"].append(context["i"])
        return ""

    @register.simple_tag(takes_context=True)
    def probe_xy_type(context):
        seen["types"].append(type(context["x"]["y"]).__name__)
        return ""

    @register.simple_tag(takes_context=True)
    def push_bag(context):
        context["bag"].append(len(context["bag"]) + 1)
        seen["bag"].append(list(context["bag"]))
        return ""

    @register.simple_tag
    def join2(a, b):
        return f"{a}-{b}"

    names = ["probe_rows", "probe_i", "probe_xy_type", "push_bag", "join2", "probe_v"]
    tl._bridge_library("lib2914", register)
    yield register, seen
    for name in names:
        try:
            unregister_tag_handler(name)
        except Exception:  # noqa: BLE001 — best-effort teardown
            pass


class TestMemo:
    def test_unchanged_frame_is_handed_over_once(self, lib):
        """Five calls in a loop: the base frame is converted once, so the tag
        receives the same object each time. (Before #2914 every call got a
        fresh conversion — five distinct ids.)"""
        _, seen = lib
        render_template(
            "{% regroup rows by a as groups %}{% for i in items %}{% probe_rows %}{% endfor %}",
            {"rows": [{"a": 1}, {"a": 2}], "items": [1, 2, 3, 4, 5]},
        )
        assert len(seen["ids"]) == 5
        first = seen["ids"][0]
        assert first[0].grouper == 1
        assert all(g is first for g in seen["ids"]), "the base frame was re-converted per call"

    @pytest.mark.parametrize(
        ("tpl", "ctx", "calls"),
        [
            (
                "{% regroup rows by a as groups %}{% for i in items %}{% probe_rows %}{% endfor %}",
                {"rows": [{"a": 1}], "items": list(range(200))},
                200,
            ),
            (
                "{% regroup rows by a as groups %}"
                "{% for i in xs %}{% for j in ys %}{% probe_rows %}{% endfor %}{% endfor %}",
                {"rows": [{"a": 1}], "xs": list(range(20)), "ys": list(range(20))},
                400,
            ),
        ],
        ids=["200-items", "nested-20x20"],
    )
    def test_base_frame_is_converted_exactly_once_however_many_calls(self, lib, tpl, ctx, calls):
        """Review of #2916: the first cut cleared the memo past 64 entries, so a
        loop of 200 re-converted the base frame four times and a nested 20×20
        seven. Popped loop frames leave the memo on the next call instead, so
        it never holds more than the live stack and the base frame converts
        once."""
        _, seen = lib
        render_template(tpl, ctx)
        assert len(seen["ids"]) == calls
        first = seen["ids"][0]
        assert all(g is first for g in seen["ids"]), (
            f"base frame re-converted: {len({id(g) for g in seen['ids']})} distinct conversions"
        )

    def test_nothing_converted_for_a_render_outlives_the_render(self, lib):
        """Review of #2916 🔴: the memo held a full converted copy of the state
        (pinning any live Python object in it) until 64 more frames came by —
        per thread. Every render entry now clears the memo on exit, so the
        converted objects a tag received are collectable as soon as the tag
        lets go of them."""
        _, seen = lib
        render_template(
            "{% regroup rows by a as groups %}{% for i in items %}{% probe_rows %}{% endfor %}",
            {"rows": [{"a": 1}, {"a": 2}], "items": [1, 2]},
        )
        groups = seen["ids"][0]  # Rust-born; a memoised frame dict would hold it
        seen["ids"].clear()
        gc.collect()
        holders = [type(r).__name__ for r in gc.get_referrers(groups) if isinstance(r, dict)]
        assert holders == [], f"a converted frame dict still holds the value: {holders}"

    def test_loop_variable_is_fresh_every_iteration(self, lib):
        """The `{% for %}` frame is rebound each iteration — a miss by design."""
        _, seen = lib
        render_template("{% for i in items %}{% probe_i %}{% endfor %}", {"items": [1, 2, 3]})
        assert seen["vals"] == [1, 2, 3]

    def test_a_binding_into_a_cached_frame_invalidates_it(self, lib):
        """`{% join2 … as v %}` writes into the current frame through
        `DerefMut`, which re-stamps it; the next call must see `v`."""
        _, seen = lib
        render_template("{% probe_v %}{% join2 a b as v %}{% probe_v %}", {"a": "p", "b": "q"})
        assert seen["vals"] == [None, "p-q"]

    def test_nested_loop_with_as_var_is_byte_identical_to_django(self, lib):
        register, _ = lib
        tpl = (
            "{% for a in xs %}{% for b in ys %}{% join2 a b as v %}[{{ v }}]{% endfor %}"
            "{% with c=a %}{% join2 c b as w %}{{ w }}{% endwith %}{% endfor %}"
        )
        ctx = {"xs": ["p", "q"], "ys": [1, 2]}
        engine = Engine()
        engine.template_libraries["lib2914"] = register
        expected = engine.from_string("{% load lib2914 %}" + tpl).render(Context(ctx))
        assert render_template(tpl, dict(ctx)) == expected

    def test_a_scoped_safe_binding_does_not_mark_the_cached_nested_value(self, lib):
        """`{% with y=x.y|safe %}` grants `y`, not `x.y`: after the scope a tag
        still sees `x["y"]` as a plain str on the cached frame."""
        _, seen = lib
        render_template(
            "{% with y=x.y|safe %}{% probe_xy_type %}{% endwith %}{% probe_xy_type %}",
            {"x": {"y": "<b>"}},
        )
        assert seen["types"] == ["str", "str"]

    def test_a_render_global_dotted_safe_grant_is_the_same_on_every_call(self, lib):
        """The view's `mark_safe_keys(["x.y"])` is reminted in place on the
        handler dict each call; on a cached frame that lands on the same object
        twice, which is idempotent — both calls see a SafeString."""
        from djust._rust import RustLiveView

        _, seen = lib
        view = RustLiveView("{% for i in items %}{% probe_xy_type %}{% endfor %}")
        view.update_state({"x": {"y": "<b>"}, "items": [1, 2]})
        view.mark_safe_keys(["x.y"])
        view.render()
        assert seen["types"] == ["SafeString", "SafeString"]

    def test_nested_mutation_is_shared_within_a_render_as_django_does(self, lib):
        """The one visible change, pinned: a tag that mutates a nested value it
        received is seen by later tags in the same render — Django hands every
        tag the same object too. Rust state is not written back either way."""
        register, seen = lib
        bag: list = []
        render_template(
            "{% for i in items %}{% push_bag %}{% endfor %}", {"items": [1, 2, 3], "bag": bag}
        )
        assert seen["bag"] == [[1], [1, 2], [1, 2, 3]]
        assert bag == [], "no write reaches the caller's object"
        engine = Engine()
        engine.template_libraries["lib2914"] = register
        seen["bag"].clear()
        engine.from_string(
            "{% load lib2914 %}{% for i in items %}{% push_bag %}{% endfor %}"
        ).render(Context({"items": [1, 2, 3], "bag": []}))
        assert seen["bag"] == [[1], [1, 2], [1, 2, 3]], "Django's own behaviour"

    def test_safe_string_survives_the_memo(self, lib):
        out = render_template(
            "{% for i in items %}{{ s }}{% endfor %}", {"items": [1, 2], "s": SafeString("<b>")}
        )
        assert out == "<b><b>"
