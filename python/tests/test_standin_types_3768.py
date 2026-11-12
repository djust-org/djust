"""A component tag gets a tuple / UUID / Path as the value it is (#3768).

The Rust engine handed a native handler the operand as TEXT (a scalar) or JSON (a list, a dict): a
``tuple`` arrived as the JSON list ``[1, 2]``, a ``UUID`` or a ``Path`` as its ``str``. A Python tag
tells them apart: ``{% approval_gate reject_label=v %}`` over ``v = (1, 2)`` prints ``(1, 2)`` under
Django and printed ``[1, 2]`` natively. ``scripts/lib/template_library_parity/typed_values.py``
measured 495 such cells over 208 tags.

Since 2.0 the native handlers are gone (one render path per component tag): the Rust engine renders
the Python tag's own node through the ``{% load %}`` bridge, so the operand reaches the tag as the
value it is, by construction. What is left here pins that against Django's engine, and pins the
engine-level typed-operand channel (``TYPED_ARGS``), which the remaining native handlers (the core
layout tags, ``{% live_render %}``) still use.

Engine path (CLAUDE.md "engine-path declaration"): both. Every render here is compared with the
Django engine's own tag over one source and one context.

Refs #3768, #3756, #3666, #1646, #1468.
"""

from __future__ import annotations

import pathlib
import re
import uuid
from collections import namedtuple
from pathlib import Path
from typing import Any

import pytest
from django.template import Context, Engine

from djust.components import rust_handlers
from djust.template_tags import TYPED_ARGS_KEY

COMPONENTS = "djust.components.templatetags.djust_components"
LIBRARIES = {"djust_components": COMPONENTS, "theme_tags": "djust.theming.templatetags.theme_tags"}


@pytest.fixture(scope="module")
def engines():
    from djust.template.backend import DjustTemplateBackend
    from djust.theming import rust_handlers as theme_handlers

    rust_handlers.register_with_rust_engine()
    theme_handlers.register_with_rust_engine()
    rust = DjustTemplateBackend(
        {"NAME": "standin-3768", "DIRS": [], "APP_DIRS": True, "OPTIONS": {}}
    )
    django = Engine(
        dirs=[str(Path(rust_handlers.__file__).resolve().parent / "templates")],
        libraries=dict(LIBRARIES),
    )
    return rust, django


def render(engines, which: str, src: str, context: dict[str, Any]) -> str:
    rust, django = engines
    if which == "rust":
        out = rust.from_string(src).render(dict(context))
    else:
        out = django.from_string(src).render(Context(dict(context)))
    return re.sub(r"\s+", " ", str(out)).strip()


# --------------------------------------------------------------------------- #
# _parse_args: the one reader


# --------------------------------------------------------------------------- #
# Registration: every component handler asks for its operands typed


# --------------------------------------------------------------------------- #
# Rendered, against the Django engine's own tag


INLINE_TUPLE = "{% load djust_components %}{% approval_gate reject_label=v %}"
BLOCK_TUPLE = "{% load djust_components %}{% popconfirm confirm_label=v %}BODY{% endpopconfirm %}"
UUID_COUNTER = "{% load djust_components %}{% live_counter value=v %}"
UUID_BADGE = "{% load djust_components %}{% notification_badge count=v %}"


class TestRenderedLikeDjango:
    @pytest.mark.parametrize(
        ("src", "value", "needle"),
        [
            (INLINE_TUPLE, (1, 2), "(1, 2)"),
            (INLINE_TUPLE, ((1, "a"), (2, "b")), "((1, &#x27;a&#x27;), (2, &#x27;b&#x27;))"),
            (INLINE_TUPLE, (7,), "(7,)"),
            (BLOCK_TUPLE, (1, 2), 'confirm">(1, 2)</button>'),
            (UUID_COUNTER, uuid.UUID(int=7), 'data-value="7"'),
            (UUID_BADGE, uuid.UUID(int=7), ">7<"),
        ],
        ids=[
            "inline tuple",
            "inline nested",
            "inline one",
            "block tuple",
            "UUID counter",
            "UUID badge",
        ],
    )
    def test_the_native_tag_prints_what_the_python_tag_prints(self, engines, src, value, needle):
        django = render(engines, "django", src, {"v": value})
        rust = render(engines, "rust", src, {"v": value})
        assert needle in django, django
        assert rust == django, f"\n  django {django}\n  rust   {rust}"

    def test_the_same_word_as_a_string_literal_changes_nothing(self, engines):
        """A control: nothing to recover from a literal, so nothing changes."""
        src = "{% load djust_components %}{% approval_gate reject_label='(1, 2)' %}"
        assert render(engines, "rust", src, {}) == render(engines, "django", src, {})


DEPENDENT_SELECT = "{% load djust_components %}{% dependent_select name='n' parent='p' options=v %}"
SPARKLINE = "{% load djust_components %}{% sparkline data=v %}"
MULTI_SELECT = "{% load djust_components %}{% multi_select name='n' options=v %}"


class TestAListSlotKeepsWorking:
    """A tuple is an ordinary thing to pass where a tag takes a list (Django ``choices`` are
    tuples). Since 2.0 the Rust engine renders the Python tag's own node, so a tuple is whatever
    the Python tag makes of it, under both engines."""

    @pytest.mark.parametrize("src", [DEPENDENT_SELECT, SPARKLINE, MULTI_SELECT])
    @pytest.mark.parametrize(
        "items",
        [
            (1, 2),
            ("a", "b"),
            ({"value": "a", "label": "A"}, {"value": "b", "label": "B"}),
        ],
        ids=["ints", "strings", "dicts"],
    )
    def test_a_tuple_renders_as_django_renders_it(self, engines, src, items):
        rust = render(engines, "rust", src, {"v": items})
        assert rust == render(engines, "django", src, {"v": items})

    def test_dependent_select_options_match_django_for_a_tuple(self, engines):
        """The cell the first version of this fix broke: Django renders the options, and a real
        ``tuple`` made the native handler render none."""
        value = ({"value": "a", "label": "A"}, {"value": "b", "label": "B"})
        rust = render(engines, "rust", DEPENDENT_SELECT, {"v": value})
        assert rust == render(engines, "django", DEPENDENT_SELECT, {"v": value})
        assert rust.count("<option") == 3

    def test_the_members_of_a_tuple_are_lists_too(self, engines):
        """``heatmap`` reads each ROW with ``isinstance(row, list)``: a tuple of tuples must give
        it rows, so the conversion is recursive (a plain ``TupleValue(value)`` would keep the
        members real tuples and drop every row)."""
        src = "{% load djust_components %}{% heatmap data=v x_labels=xl y_labels=yl %}"
        ctx = {"xl": ["a", "b"], "yl": ["c", "d"]}
        as_tuple = render(engines, "rust", src, {**ctx, "v": ((1, 2), (3, 4))})
        assert as_tuple == render(engines, "django", src, {**ctx, "v": ((1, 2), (3, 4))})
        as_list = render(engines, "rust", src, {**ctx, "v": [[1, 2], [3, 4]]})
        assert as_list.count('class="dj-heatmap__cell"') == 4, as_list

    def test_a_tuple_inside_the_tuple_prints_as_python_prints_it(self, engines):
        """The one place a tuple and a list of the same items render differently, and the place
        Django agrees: ``('a', 'A')``, not ``['a', 'A']``."""
        value = (("a", "A"), ("b", "B"))
        rust = render(engines, "rust", DEPENDENT_SELECT, {"v": value})
        assert rust == render(engines, "django", DEPENDENT_SELECT, {"v": value})
        assert "(&#x27;a&#x27;, &#x27;A&#x27;)" in rust


# --------------------------------------------------------------------------- #
# Review round: depth, the narrow ask, the reserved key on the block path, list slots


def nested(depth: int) -> tuple:
    value: tuple = (1,)
    for _ in range(depth):
        value = (value,)
    return value


class TestADeeplyNestedTupleIsNotAnError:
    """A tuple nested 500 deep raised RecursionError in the native handlers' conversion; the
    Rust engine now renders the Python tag's node, which prints it as Django does."""

    @pytest.mark.parametrize("depth", [50, 300, 500])
    def test_the_native_tag_renders_it_as_django_does(self, engines, depth):
        """500 raised RecursionError before the walks were iterative. (Past ~1000 the engine's own
        context serializer, ``serialize_value``, raises on the UNCHANGED code, before any handler
        runs: not this change's.)"""
        src = "{% load djust_components %}{% approval_gate reject_label=v %}"
        rust = render(engines, "rust", src, {"v": nested(depth)})
        assert rust == render(engines, "django", src, {"v": nested(depth)})


class TestTheNarrowAsk:
    """A handler that declares ``TYPED_ARGS_STANDINS_ONLY`` is handed only the operands whose
    string form loses their type (a tuple, an object), so a large list or dict is not materialised
    a second time. One that declares only ``TYPED_ARGS`` still gets every operand (json_viewer)."""

    @pytest.fixture
    def seen(self):
        from djust import _rust

        seen: dict[str, Any] = {}

        def probe(name, **attrs):
            class H:
                def render(self, args, context):
                    seen[name] = dict(context.get(TYPED_ARGS_KEY, {}))
                    return ""

            for key, value in attrs.items():
                setattr(H, key, value)
            return H()

        class B:
            TYPED_ARGS = True
            TYPED_ARGS_STANDINS_ONLY = True

            def render(self, args, content, context):
                seen["block_narrow"] = dict(context.get(TYPED_ARGS_KEY, {}))
                return content

        class BAll:
            TYPED_ARGS = True

            def render(self, args, content, context):
                seen["block_all"] = dict(context.get(TYPED_ARGS_KEY, {}))
                return content

        _rust.register_tag_handler(
            "narrow_probe_3768", probe("narrow", TYPED_ARGS=True, TYPED_ARGS_STANDINS_ONLY=True)
        )
        _rust.register_tag_handler("all_probe_3768", probe("all", TYPED_ARGS=True))
        _rust.register_tag_handler(
            "flag_only_probe_3768", probe("flag_only", TYPED_ARGS_STANDINS_ONLY=True)
        )
        _rust.register_block_tag_handler("bnarrow_probe_3768", "endbnarrow_probe_3768", B())
        _rust.register_block_tag_handler("ball_probe_3768", "endball_probe_3768", BAll())
        try:
            yield seen
        finally:
            for name in ("narrow_probe_3768", "all_probe_3768", "flag_only_probe_3768"):
                _rust.unregister_tag_handler(name)
            for name in ("bnarrow_probe_3768", "ball_probe_3768"):
                _rust.unregister_block_tag_handler(name)

    CTX = {
        "t": (1, 2),
        "u": uuid.UUID(int=7),
        "p": pathlib.PurePosixPath("/a"),
        "rows": [{"a": 1}] * 3,
        "d": {"k": "v"},
        "n": 5,
        "s": "text",
        "nt": namedtuple("Pt", "x y")(1, 2),
    }
    OPERANDS = "t=t u=u p=p rows=rows d=d n=n s=s nt=nt"

    def test_the_narrow_handler_gets_a_tuple_and_objects_only(self, seen):
        from djust import _rust

        _rust.render_template("{% narrow_probe_3768 " + self.OPERANDS + " %}", dict(self.CTX))
        # A namedtuple is a tuple whose type the string argument loses too (#3849).
        assert set(seen["narrow"]) == {"t", "u", "p", "nt"}, seen["narrow"]
        assert seen["narrow"]["t"] == (1, 2) and type(seen["narrow"]["t"]) is tuple
        assert seen["narrow"]["u"] == self.CTX["u"]
        assert seen["narrow"]["nt"] == (1, 2) and type(seen["narrow"]["nt"]).__name__ == "Pt"

    def test_a_handler_declaring_only_typed_args_still_gets_every_operand(self, seen):
        from djust import _rust

        _rust.render_template("{% all_probe_3768 " + self.OPERANDS + " %}", dict(self.CTX))
        assert set(seen["all"]) == {"t", "u", "p", "rows", "d", "n", "s", "nt"}, seen["all"]

    def test_the_narrowing_flag_alone_declares_nothing(self, seen):
        from djust import _rust

        _rust.render_template("{% flag_only_probe_3768 " + self.OPERANDS + " %}", dict(self.CTX))
        assert seen["flag_only"] == {}

    def test_the_block_registry_reads_the_same_two_declarations(self, seen):
        from djust import _rust

        _rust.render_template(
            "{% bnarrow_probe_3768 " + self.OPERANDS + " %}x{% endbnarrow_probe_3768 %}"
            "{% ball_probe_3768 " + self.OPERANDS + " %}x{% endball_probe_3768 %}",
            dict(self.CTX),
        )
        assert set(seen["block_narrow"]) == {"t", "u", "p", "nt"}, seen["block_narrow"]
        assert set(seen["block_all"]) == {"t", "u", "p", "rows", "d", "n", "s", "nt"}


class TestTheReservedKeyOnTheBlockPath:
    """``__djust_typed_args__`` is the engine's. The Rust scrub in ``bridged_context_map`` is the
    only defence (nothing in Python re-checks), and the block path now reads the channel, so a
    BLOCK handler that declares ``TYPED_ARGS`` must get the engine's operands or nothing, never an
    application value under that name. ``test_typed_args_reserved_key_3773`` covers a block
    handler that does not declare it; this is the one that does."""

    @pytest.fixture
    def seen(self):
        from djust import _rust

        seen: dict[str, Any] = {}

        class Block:
            TYPED_ARGS = True

            def render(self, args, content, context):
                seen["has_key"] = TYPED_ARGS_KEY in context
                seen["value"] = context.get(TYPED_ARGS_KEY, "<absent>")
                return content

        _rust.register_block_tag_handler("reserved_block_3768", "endreserved_block_3768", Block())
        try:
            yield seen
        finally:
            _rust.unregister_block_tag_handler("reserved_block_3768")

    PLANTED = [
        {"data": {"evil": 1}},
        {},
        "a string",
        ["data"],
        7,
        None,
    ]

    @pytest.mark.parametrize(
        "planted", PLANTED, ids=["dict", "empty", "str", "list", "int", "none"]
    )
    @pytest.mark.parametrize(
        "src",
        [
            "{% reserved_block_3768 title='lit' %}x{% endreserved_block_3768 %}",
            "{% reserved_block_3768 %}x{% endreserved_block_3768 %}",
            "{% reserved_block_3768 a=missing %}x{% endreserved_block_3768 %}",
        ],
        ids=["literals-only", "no-operands", "unresolved"],
    )
    def test_no_operand_means_no_key_whatever_the_application_planted(self, seen, src, planted):
        from djust import _rust

        _rust.render_template(src, {TYPED_ARGS_KEY: planted})
        assert seen["has_key"] is False, f"an application value stood in: {seen['value']!r}"

    @pytest.mark.parametrize(
        "planted", PLANTED, ids=["dict", "empty", "str", "list", "int", "none"]
    )
    def test_a_resolved_operand_is_the_engines_not_the_applications(self, seen, planted):
        from djust import _rust

        _rust.render_template(
            "{% reserved_block_3768 u=u %}x{% endreserved_block_3768 %}",
            {TYPED_ARGS_KEY: planted, "u": uuid.UUID(int=7)},
        )
        assert seen["value"] == {"u": uuid.UUID(int=7)}, seen["value"]


class TestAUuidOrPathInAListParameter:
    """The visible behaviour change, stated: a ``UUID`` or ``Path`` given to a parameter that takes
    a list used to render (the handler got its text, which it iterated as characters) and now
    raises ``TypeError``, as Django's own tag does. 11 parameters of 8 tags, each for both types."""

    CELLS = [
        ("animated_number", "separator"),
        ("carousel", "images"),
        ("data_grid", "columns"),
        ("data_grid", "rows"),
        ("multi_select", "options"),
        ("multi_select", "selected"),
        ("notification_center", "notifications"),
        ("table_of_contents", "items"),
        ("tag_input", "suggestions"),
        ("tag_input", "tags"),
        ("tree_view", "nodes"),
    ]

    @pytest.mark.parametrize(
        "value", [uuid.UUID(int=7), pathlib.PurePosixPath("/a/b")], ids=["UUID", "Path"]
    )
    @pytest.mark.parametrize(("tag", "param"), CELLS)
    def test_both_engines_raise_the_same_error(self, engines, tag, param, value):
        src = "{% load djust_components %}{% " + tag + " " + param + "=v %}"
        outcomes = []
        for which in ("rust", "django"):
            try:
                outcomes.append(("ok", render(engines, which, src, {"v": value})))
            except Exception as exc:  # noqa: BLE001 - the exception IS the observation
                outcomes.append(("err", type(exc).__name__))
        assert outcomes[0] == outcomes[1] == ("err", "TypeError"), outcomes
