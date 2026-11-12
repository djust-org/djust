"""One render path per component tag (S3, decided 2026-11-12): the first slice.

A ``djust_components`` tag without a kept native twin renders under the Rust engine through the
``{% load %}`` bridge: the engine runs the Python tag's own node, so the Rust engine and Django's
engine give the same markup by construction. The kept twins (``rust_handlers.NATIVE_*_HANDLERS``:
the core layout tags, the inclusion tags a benchmark keeps, the children-reading block tags the
bridge cannot serve) are pinned against the Python tags, their known divergences listed.

What each class proves:

* ``TestRegistration``: every tag is registered as a bridge handler of the right kind or as its kept
  twin, and ``RAW_BODY_TAGS`` is exactly the set of BRIDGED block tags whose node reads its children
  (derived from the tags' source, both directions).
* ``TestKeptTwins``: each kept twin either renders exactly what its Python tag renders over the
  parity harness's spread of inputs, or its divergence is listed here with its measured class and
  aspects (the parity ratchet holds the measurement).
* ``TestLayoutParity``: the native layout handlers render the Python tags' markup for every argument
  value in a spread, and the one documented difference is still the only one.
* ``TestTheParityIssuesAreGone``: the reproductions of the twin-drift issues whose twins are gone.
* ``TestStableRenders``: every bridged tag renders the same bytes twice in a row (no per-render
  ids: a changing id is a patch on every LiveView turn), and a bridged tag's argument change is a
  small patch, not a subtree replacement.
* ``TestRawBodyNesting`` / ``TestGallery``: nested raw-body tags; every gallery example of a bridged
  tag renders the same under both engines.
"""

from __future__ import annotations

import collections
import inspect
import re
import uuid

import pytest
from django.template import engines
from django.utils.safestring import mark_safe

from djust import _rust
from djust import template_libraries as tl
from djust.components import rust_handlers as rh
from djust.components.templatetags import (
    _advanced,
    _charts,
    _dev_tools,
    _forms,
    djust_components as lib,
)

pytestmark = pytest.mark.components

LAYOUT = {"app_shell", "app_sidebar", "app_header", "app_content"}
NATIVE = {n for n, _h in rh.NATIVE_INLINE_HANDLERS} | {n for n, _e, _h in rh.NATIVE_BLOCK_HANDLERS}
BRIDGED = sorted(set(lib.register.tags) - NATIVE)
_MODULES = (lib, _forms, _advanced, _charts, _dev_tools)


def _django(src: str, ctx: dict) -> str:
    return engines["django"].from_string("{% load djust_components %}" + src).render(dict(ctx))


def _rust_render(src: str, ctx: dict) -> str:
    return str(_rust.render_template(src, dict(ctx)))


@pytest.fixture(autouse=True)
def _registered():
    rh.register_with_rust_engine()


def _uuid_free(html: str) -> str:
    """Tags that draw a random id (``uuid4``) or a process-wide counter differ render to render,
    under either engine."""
    html = re.sub(r"[0-9a-f]{8}(-?[0-9a-f]{4}){0,3}(-?[0-9a-f]{12})?", "<id>", html)
    html = re.sub(r"(?<=[-_])[0-9a-f]{6,12}\b", "<id>", html)
    return re.sub(r"(?<=-tip-)\d+", "<n>", html)


class TestRegistration:
    def test_every_tag_is_bridged_except_the_kept_twins(self):
        assert LAYOUT <= NATIVE
        assert NATIVE <= set(lib.register.tags)
        for name, compile_func in lib.register.tags.items():
            kind, handler = rh.bridged_handler(name, compile_func, lib.register)
            if name in NATIVE:
                inline = name in dict(rh.NATIVE_INLINE_HANDLERS)
                assert (_rust.has_tag_handler if inline else _rust.has_block_tag_handler)(name)
                assert not _rust.has_raw_block_tag_handler(name), name
                continue
            registered = {
                "tag": _rust.has_tag_handler,
                "block": _rust.has_block_tag_handler,
                "raw": _rust.has_raw_block_tag_handler,
            }
            assert registered[kind](name), (name, kind)
            for other, check in registered.items():
                if other != kind:
                    assert not check(name), f"{name} is also registered as a {other} handler"
            assert isinstance(
                handler,
                (tl.LibraryTagHandler, tl.LibraryRawBlockTagHandler),
            ), name
            assert handler.compile_func is compile_func

    def test_a_reregistration_hands_the_engine_the_same_handlers(self):
        first = rh._bridged_handlers(lib.register)
        rh.register_with_rust_engine()
        assert rh._bridged_handlers(lib.register) is first

    def test_raw_body_tags_are_exactly_the_bridged_tags_that_read_their_children(self):
        """Derived from the source of each body-consuming tag's node (both directions).

        A node reads its children when it iterates its nodelist (``for n in self.nodelist``),
        renders its body in a context it pushes, reads back a value a child wrote into the context
        while the body rendered, or parses more than one body segment. Rendering the body first
        (the rendered-body route) would lose exactly those children.
        """
        derived = set()
        for name, compile_func in lib.register.tags.items():
            if name in NATIVE:
                continue
            if not (tl._classify(compile_func) == "raw" and tl._consumes_body(compile_func)):
                continue
            source = inspect.getsource(compile_func)
            if len(re.findall(r"parser\.parse\(", source)) > 1:
                derived.add(name)
                continue
            for node_name in re.findall(r"(\w+Node)\(", source):
                cls = next((getattr(m, node_name) for m in _MODULES if hasattr(m, node_name)), None)
                if cls is None:
                    continue
                node_source = inspect.getsource(cls)
                if (
                    re.search(r"in self\.nodelist\b(?!\.)", node_source)
                    or "context.push" in node_source
                    or re.search(r"context\.get\(_\w+_key", node_source)
                ):
                    derived.add(name)
        assert derived == set(rh.RAW_BODY_TAGS)

    def test_every_other_block_tag_renders_its_body_as_a_wrapper(self):
        """A rendered-body tag (outside ``RAW_BODY_TAGS``) passes the bridge's own wrapper probe
        (ADR-030): one body segment, rendered as it is, in the context it was given. A tag that
        renders its body only when it is open (``modal``, ``sheet``, ``bottom_sheet``, ...) is
        probed open, since the probe otherwise hands every operand ``""``."""
        for name, compile_func in lib.register.tags.items():
            kind, _handler = rh.bridged_handler(name, compile_func, lib.register)
            if kind != "block" or name in NATIVE:
                continue
            reason = tl._wrapper_refusal(name, compile_func)
            if reason is not None and "does not render its body" in reason:
                # Quoted: the probe's context answers every name (``True`` too) with "".
                reason = tl._wrapper_refusal(name, compile_func, ['open="y"', 'is_open="y"'])
            # A child tag drawn by its parent renders nothing on its own, under either engine.
            if reason is not None and name in _CHILD_TAGS:
                continue
            assert reason is None, (name, reason)


#: Child tags whose own node renders nothing: the parent draws them.
_CHILD_TAGS: set[str] = set()


# --------------------------------------------------------------------------- #
# The native layout handlers against the Python tags

_EVIL = "<script>x</script>\"'&"
_VALUES = {
    "literal": '"main shell"',
    "hostile": "evil",
    "safe": "safe_attr",
    "missing": "undefined_var",
    "none": "none",
    "number": "num",
    "zero": "zero",
    "true": "True",
    "false": "False",
    "empty": '""',
    "tuple": "pair",
}
_CTX = {
    "evil": _EVIL,
    "safe_attr": mark_safe('ZQ" onmouseover="alert(1)'),
    "none": None,
    "num": 7,
    "zero": 0,
    "pair": (1, 2),
}
_BODY = (
    "{% app_sidebar %}<nav>{{ evil }}</nav>{% endapp_sidebar %}"
    "{% app_header %}<h1>T</h1>{% endapp_header %}"
    "{% app_content %}<p>{{ num }}</p>{% endapp_content %}"
)


class TestLayoutParity:
    @pytest.mark.parametrize("value", sorted(_VALUES))
    @pytest.mark.parametrize("param", ["id", "class", "sidebar_collapsed"])
    def test_app_shell_matches_the_python_tag(self, param, value):
        src = "{% app_shell " + f"{param}={_VALUES[value]}" + " %}" + _BODY + "{% endapp_shell %}"
        assert _rust_render(src, _CTX) == _django(src, _CTX)

    @pytest.mark.parametrize("tag", ["app_sidebar", "app_header", "app_content"])
    @pytest.mark.parametrize("body", ["", "<b>x</b>", "{{ evil }}", "{{ safe_attr }} {{ num }}"])
    def test_the_slots_match_the_python_tags_inside_a_shell(self, tag, body):
        src = "{% app_shell %}{% " + tag + " %}" + body + "{% end" + tag + " %}{% endapp_shell %}"
        assert _rust_render(src, _CTX) == _django(src, _CTX)

    def test_an_attribute_value_is_escaped_whatever_it_is_marked_as(self):
        """#4009 on the kept twin and on the Python tag: a marked-safe ``class``/``id`` cannot
        leave its attribute."""
        for param in ("id", "class"):
            src = "{% app_shell " + param + "=safe_attr %}{% endapp_shell %}"
            for html in (_rust_render(src, _CTX), _django(src, _CTX)):
                assert 'onmouseover="' not in html, html
                assert "&quot; onmouseover=&quot;" in html, html

    def test_the_one_documented_difference_is_body_content_outside_the_slots(self):
        """The native ``app_shell`` keeps body content that is not inside a slot tag, in template
        order (it is what the root-proof protocol copies); the Python tag drops it. Nothing else
        differs: with that content removed the two agree (the parametrized cases above)."""
        src = "{% app_shell %}stray {{ num }}" + _BODY + "{% endapp_shell %}"
        native, python = _rust_render(src, _CTX), _django(src, _CTX)
        assert "stray 7" in native and "stray" not in python
        assert native.replace("stray 7", "") == python


# --------------------------------------------------------------------------- #
# The kept twins against the Python tags

#: Every kept twin that does not render its Python tag's markup, with the measured class and
#: aspects of ``scripts/template-library-parity-baseline.csv`` (the harness renders a spread of
#: keyword, hostile, marked-safe, literal, missing, ``None``, positional and no-argument calls).
#: These are the known divergences, kept on purpose with their twin (see ``rust_handlers``), and
#: owed a convergence: the native twin is the faster render (``badge`` ... ``data_table``) or the
#: one that can render a Rust-only tag in its body (the children-reading block tags). A twin that
#: starts or stops diverging fails here.
KNOWN_DIVERGENCES = {
    "accordion": "DIFFERENT",
    "accordion_item": "DIFFERENT",
    "app_shell": "DIFFERENT",  # keeps body content outside the slot tags (root-proof copies it)
    "avatar": "DIFFERENT",
    "badge": "DIFFERENT",
    "data_table": "DIFFERENT",  # incl. the native-only features (#3869)
    "filter_bar": "DIFFERENT",  # draws what the body renders, not only direct children (#3878)
    "filter_date_range": "DIFFERENT-escape-policy-only",
    "filter_search": "DIFFERENT",
    "filter_select": "DIFFERENT-escape-policy-only",
    "form_array": "DIFFERENT",
    "nav_item": "DIFFERENT",  # draws a third level (#3873)
    "nav_menu": "DIFFERENT",  # draws what the body renders (#3878)
    "page_header": "DIFFERENT-escape-policy-only",
    "pagination": "DIFFERENT",
    "progress": "DIFFERENT",
    "sidebar": "DIFFERENT",
    "sidebar_item": "DIFFERENT",
    "sidebar_section": "DIFFERENT-escape-policy-only",
    "toolbar": "DIFFERENT",
}


def _baseline() -> dict[str, str]:
    import csv
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "scripts" / "template-library-parity-baseline.csv"
    with path.open(encoding="utf-8") as f:
        return {
            row["tag"].split(":", 1)[1]: row["class"]
            for row in csv.DictReader(f)
            if row["tag"].startswith("djust_components:")
        }


class TestKeptTwins:
    @pytest.mark.parametrize("name", sorted(NATIVE))
    def test_a_kept_twin_matches_its_python_tag_or_its_divergence_is_listed(self, name):
        measured = _baseline()[name]
        assert measured == KNOWN_DIVERGENCES.get(name, "IDENTICAL"), (name, measured)

    def test_every_listed_divergence_is_a_kept_twin(self):
        assert set(KNOWN_DIVERGENCES) <= NATIVE

    def test_every_bridged_tag_is_identical(self):
        baseline = _baseline()
        assert {n: baseline[n] for n in BRIDGED if baseline.get(n) != "IDENTICAL"} == {}


# --------------------------------------------------------------------------- #
# The reproductions of the twin-drift issues whose twins are gone

_P = collections.namedtuple("P", "x y")


class TestTheParityIssuesAreGone:
    @pytest.mark.parametrize(
        "value",
        [(1, 2), _P(1, 2), uuid.UUID(int=5), b"ab", {3}, frozenset({4})],
        ids=["tuple", "namedtuple", "uuid", "bytes", "set", "frozenset"],
    )
    def test_a_value_reaches_the_tag_as_its_type_3768_3849(self, value):
        for src in ("{% dj_tag label=v %}", "{% stat_card value=v %}", "{% empty_state title=v %}"):
            assert _rust_render(src, {"v": value}) == _django(src, {"v": value}), src

    @pytest.mark.parametrize("text", ['"x"', "'x'", "false", "007", "1e3", "1_000", "0", "None"])
    def test_a_resolved_string_is_never_read_again_as_a_literal_3883(self, text):
        for src in (
            "{% dj_tag label=v %}",
            "{% dj_button label=v confirm=v %}",
            "{% card title=v %}b{% endcard %}",
        ):
            assert _rust_render(src, {"v": text}) == _django(src, {"v": text}), (src, text)

    def test_a_value_that_names_a_variable_is_not_looked_up_again_3882(self):
        ctx = {"v": "name", "name": "n", "w": "items", "items": [1, 2]}
        for src in ("{% dj_button event=v %}", "{% dj_button label=w %}", "{% dj_tag label=w %}"):
            assert _rust_render(src, ctx) == _django(src, ctx), src
        assert 'dj-click="name"' in _rust_render("{% dj_button event=v %}", ctx)


# --------------------------------------------------------------------------- #
# Stable renders: no per-render ids, small patches


def _call(name: str) -> str:
    """The gallery's first example of ``name``, or a bare call."""
    from djust.components.gallery.examples import EXAMPLES

    example = EXAMPLES.get(name, {}).get("variants") or []
    if example:
        return example[0]["template"]
    kind, _h = rh.bridged_handler(name, lib.register.tags[name], lib.register)
    return "{% " + name + " %}" + ("x{% end" + name + " %}" if kind != "tag" else "")


class TestStableRenders:
    @pytest.mark.parametrize("name", BRIDGED)
    def test_two_renders_of_the_same_call_are_the_same_bytes(self, name):
        """A LiveView re-renders every tag on every turn; a tag that draws a fresh id (``uuid4``,
        a process-wide counter) changes the page, and each change is a patch, every turn."""
        from djust.components.gallery.examples import EXAMPLES

        src = _call(name)
        ctx = dict((EXAMPLES.get(name, {}).get("variants") or [{}])[0].get("context", {}))
        try:
            first = _rust_render(src, ctx)
        except Exception:  # noqa: BLE001 - a bare call the tag refuses has nothing to compare
            pytest.skip("the tag refuses a bare call")
        assert _rust_render(src, ctx) == first

    @pytest.mark.parametrize(
        "src",
        [
            '{% tooltip text="Hint" %}<b>x</b>{% endtooltip %}',
            '{% otp_input name="code" %}',
            '{% tag_input name="skills" tags=t %}',
        ],
    )
    def test_the_tags_that_drew_a_fresh_id_draw_a_stable_one(self, src):
        ctx = {"t": ["a", "b"]}
        assert _rust_render(src, ctx) == _rust_render(src, ctx) == _django(src, ctx)

    def test_an_argument_change_is_a_small_patch_not_a_subtree(self):
        view = _rust.RustLiveView(
            '<div>{% dj_button label=l event="go" %}{% card title="T" %}<p>{{ l }}</p>{% endcard %}</div>'
        )
        view.update_state({"l": "A"})
        view.render_with_diff()
        view.update_state({"l": "B"})
        _html, patches, _version = view.render_with_diff()
        import json

        ops = json.loads(patches)
        assert 0 < len(ops) <= 2, ops
        assert {op["type"] for op in ops} == {"SetText"}, ops


# --------------------------------------------------------------------------- #
# Raw-body nesting and the shipped gallery


class TestRawBodyNesting:
    def test_a_raw_body_tag_nested_in_itself_renders_as_django_does(self):
        """#3878's parser half: the raw body ended at the FIRST end tag, so a nested ``{% tabs %}``
        left the outer end tag over as ``Invalid block tag``."""
        src = (
            '{% tabs id="o" active="a" %}{% tab id="a" label="A" %}{% tabs id="i" active="b" %}'
            '{% tab id="b" label="B" %}in{% endtab %}{% endtabs %}{% endtab %}{% endtabs %}'
        )
        assert _uuid_free(_rust_render(src, {})) == _uuid_free(_django(src, {}))


def _gallery_variants():
    from djust.components.gallery.examples import EXAMPLES

    for tag, example in EXAMPLES.items():
        for variant in example.get("variants", []):
            yield pytest.param(
                tag,
                variant["template"],
                dict(variant.get("context", {})),
                id=f"{tag}-{variant['name']}",
            )


class TestGallery:
    @pytest.mark.parametrize("tag, src, ctx", list(_gallery_variants()))
    def test_every_gallery_example_renders_under_both_engines(self, tag, src, ctx):
        """A bridged tag renders the Django engine's bytes; a kept twin renders (its divergence is
        ``KNOWN_DIVERGENCES``)."""
        try:
            expected = _django(src, ctx)
        except Exception as exc:  # noqa: BLE001 - the Rust engine must refuse it too
            with pytest.raises(Exception):
                _rust_render(src, ctx)
            pytest.skip(f"Django refuses this example too: {type(exc).__name__}")
        got = _rust_render(src, ctx)
        names = set(re.findall(r"\{% (\w+)", src))
        if names & set(KNOWN_DIVERGENCES):
            assert got
            return
        assert _uuid_free(got) == _uuid_free(expected)
