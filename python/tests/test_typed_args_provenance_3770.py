"""The typed-args channel keeps an operand's safety and escaping, byte for byte (#3770 CI).

``provenance::tests::provenance_flatten_site_inventory`` pins every site in the Rust tree that can
fold a value or rendered output into plain text, so each is classified on purpose. The typed-args
channel (``renderer.rs::typed_operands``) adds two such lines: it inserts each resolved operand
into a ``Value::Object`` the handler receives. The question the pin asks of them: can either drop
the marker that says "this string is already markup" (``SafeString`` / ``|safe`` / ``mark_safe``)
or the authored-output provenance, so that a ``TYPED_ARGS`` handler escapes what Django does not,
or does not escape what Django does?

Two different things travel through a tag, and only one is in play here:

* the handler's OUTPUT is wrapped by the renderer (``escape_handler_return``, then the output
  type's own ``from(String)``): a custom tag's output is never "authored", and nothing in this
  change touches that path;
* the OPERAND the handler reads. ``typed_operands`` used ``get_value``, which resolves the
  expression and DISCARDS the runtime-safe flag, so ``data=evil|safe`` reached the handler as a
  plain ``str`` and ``json_viewer`` escaped what Django leaves raw. Over-escaping, not a leak, and
  a real byte difference from Django: the fix carries the flag through the channel
  (``get_value_safe`` then ``Value::SafeString``).

What each class proves:

* ``TestTheMarkerSurvivesTheChannel``: ``mark_safe`` values, ``|safe``, ``|escape``, ``|upper``
  (re-taints), a quoted literal, a context value marked safe by the view, a safe string nested in
  a dict / list and as a dict key, under ``{% autoescape off %}`` and a loop / ``with`` binding,
  for hostile strings (``<script>``, quotes, ``&``): identical to Django byte for byte.
* ``TestAHostileValueStaysEscaped``: the same values are escaped in the tree and in the raw
  ``<script>`` copy, and a ``</script>`` in a value cannot close the raw block.
* ``TestRandomised``: a seeded differential over operand spellings x hostile values.

Refs #3756, #3770, #2416, #1646, #1468.
"""

from __future__ import annotations

import random
import re
from pathlib import Path

import pytest
from django.template import Context, Engine
from django.utils.safestring import mark_safe

pytestmark = pytest.mark.pr_tier

LIBRARIES = {
    "djust_components": "djust.components.templatetags.djust_components",
    "theme_tags": "djust.theming.templatetags.theme_tags",
}
LOAD = "{% load djust_components %}"
EVIL = "<script>alert(1)</script>\"'&<img src=x onerror=alert(2)>"
EVIL_CLOSE = "</script><b>x</b>"


@pytest.fixture(scope="module")
def engines():
    from djust.components import rust_handlers as component_handlers
    from djust.template.backend import DjustTemplateBackend
    from djust.theming import rust_handlers as theme_handlers

    component_handlers.register_with_rust_engine()
    theme_handlers.register_with_rust_engine()
    rust = DjustTemplateBackend({"NAME": "prov-3770", "DIRS": [], "APP_DIRS": True, "OPTIONS": {}})
    django = Engine(
        dirs=[str(Path(component_handlers.__file__).resolve().parent / "templates")],
        libraries=dict(LIBRARIES),
    )
    return rust, django


def _render(engines, which: str, src: str, ctx: dict):
    rust, django = engines
    try:
        if which == "rust":
            out = rust.from_string(LOAD + src).render(dict(ctx))
        else:
            out = django.from_string(LOAD + src).render(Context(dict(ctx)))
    except Exception as exc:  # noqa: BLE001 - the exception IS the observation
        return ("err", type(exc).__name__, str(exc)[:200])
    return ("ok", re.sub(r"\s+", " ", str(out)).strip())


def _context():
    return {
        "evil": EVIL,
        "close": EVIL_CLOSE,
        "safe_evil": mark_safe(EVIL),
        "safe_close": mark_safe(EVIL_CLOSE),
        "row": {"k": EVIL, "s": mark_safe(EVIL), "<k>": "v"},
        "skeys": {mark_safe("<k>"): "v"},
        "rows": [EVIL, mark_safe(EVIL), {"n": mark_safe(EVIL_CLOSE)}],
        "n": 3,
    }


#: operand spellings, each valid as ``{% json_viewer data=<spelling> %}`` under BOTH engines. The
#: Python tag resolves ``data=`` with ``template.Variable``, which has no filter syntax, so a
#: filtered operand is compared through a ``{% with p=<filtered> %}`` binding instead
#: (``FILTERED``): the binding is Django's own, and the tag then sees a plain ``p``.
SPELLINGS = [
    "evil",
    "safe_evil",
    "close",
    "safe_close",
    "row",
    "row.k",
    "row.s",
    "rows",
    "rows.0",
    "rows.1",
    "rows.2",
    "rows.2.n",
    "'<b>lit</b>'",
    '"<script>"',
    "n",
]

FILTERED = [
    "evil|safe",
    "evil|escape",
    "evil|force_escape",
    "evil|upper",
    "evil|safe|upper",
    "safe_evil|upper",
    "safe_evil|escape",
    "safe_evil|lower|safe",
    "rows|first",
    "rows|last",
    "evil|default:'x'",
    "evil|truncatechars:12",
    "safe_evil|truncatechars:12",
    "safe_evil|cut:'a'",
    "evil|add:'<i>'",
    "safe_evil|add:'<i>'",
    "close|safe",
    "safe_close|force_escape",
]


class TestTheMarkerSurvivesTheChannel:
    @pytest.mark.parametrize("spelling", SPELLINGS)
    def test_identical_to_django(self, engines, spelling):
        body = "{% json_viewer data=" + spelling + " %}"
        django = _render(engines, "django", body, _context())
        rust = _render(engines, "rust", body, _context())
        assert django[0] == "ok", django
        assert rust == django, f"{spelling}\n  django {django}\n  rust   {rust}"

    @pytest.mark.parametrize("filtered", FILTERED)
    def test_a_filtered_operand_through_a_binding(self, engines, filtered):
        """``{% with p=<x|filter> %}{% json_viewer data=p %}``: the flag ``|safe`` / ``|escape``
        / ``|upper`` leave on ``p`` is the one the channel has to carry. Before it did, ``|safe``
        reached the handler as a plain ``str`` and was escaped where Django leaves it raw."""
        body = "{% with p=" + filtered + " %}{% json_viewer data=p %}{% endwith %}"
        django = _render(engines, "django", body, _context())
        rust = _render(engines, "rust", body, _context())
        assert django[0] == "ok", django
        assert rust == django, f"{filtered}\n  django {django}\n  rust   {rust}"

    @pytest.mark.parametrize("spelling", ["evil", "safe_evil", "row", "rows"])
    @pytest.mark.parametrize("autoescape", ["on", "off"])
    def test_under_autoescape(self, engines, spelling, autoescape):
        body = (
            "{% autoescape " + autoescape + " %}{% json_viewer data=" + spelling + " %}"
            "{% endautoescape %}"
        )
        django = _render(engines, "django", body, _context())
        rust = _render(engines, "rust", body, _context())
        assert django[0] == "ok", django
        assert rust == django, f"{autoescape} {spelling}\n  django {django}\n  rust   {rust}"

    @pytest.mark.parametrize(
        "body",
        [
            "{% for x in rows %}{% json_viewer data=x %}{% endfor %}",
            "{% with p=safe_evil %}{% json_viewer data=p %}{% endwith %}",
            "{% with p=evil|safe %}{% json_viewer data=p %}{% endwith %}",
            "{% with p=evil %}{% json_viewer data=p %}{% endwith %}",
            "{% json_viewer data=safe_evil collapsed_depth=0 root_label=evil %}",
            "{% json_viewer data=evil root_label=safe_evil %}",
            "{% json_viewer data=row class=evil %}",
        ],
    )
    def test_under_bindings_and_other_operands(self, engines, body):
        django = _render(engines, "django", body, _context())
        rust = _render(engines, "rust", body, _context())
        assert django[0] == "ok", django
        assert rust == django, f"{body}\n  django {django}\n  rust   {rust}"


class TestAHostileValueStaysEscaped:
    @pytest.mark.parametrize("spelling", ["evil", "close", "row.k", "rows.0"])
    def test_no_raw_markup_reaches_the_page(self, engines, spelling):
        """A NOT-safe hostile value. (``row.s`` / ``rows.1`` are ``mark_safe``d on purpose and
        are raw under Django too: the equality tests above own them.)"""
        out = _render(engines, "rust", "{% json_viewer data=" + spelling + " %}", _context())
        assert out[0] == "ok"
        html = out[1]
        assert "<script>alert" not in html
        assert "<img src=x" not in html
        assert "<b>x</b>" not in html
        # exactly one real <script> element: the raw JSON copy
        assert html.count("<script") == 1, html[:400]

    @pytest.mark.parametrize("spelling", ["close", "safe_close", "rows", "row"])
    def test_a_script_close_inside_a_value_cannot_end_the_raw_block(self, engines, spelling):
        out = _render(engines, "rust", "{% json_viewer data=" + spelling + " %}", _context())
        assert out[0] == "ok"
        block = out[1].split('class="dj-json-viewer__raw">', 1)[1]
        assert block.count("</script>") == 1 and block.endswith("</script></div>")


class TestRandomised:
    def test_random_operands_render_like_django(self, engines):
        rng = random.Random(3770)
        names = ["evil", "safe_evil", "close", "safe_close", "row", "rows", "n"]
        filters = [
            "|safe",
            "|escape",
            "|upper",
            "|lower",
            "|force_escape",
            "|first",
            "|default:'z'",
        ]
        wrappers = [
            "{%% json_viewer data=%s %%}",
            "{%% autoescape off %%}{%% json_viewer data=%s %%}{%% endautoescape %%}",
            "{%% for x in rows %%}{%% json_viewer data=%s %%}{%% endfor %%}",
        ]
        cases = set()
        for _ in range(500):
            name = rng.choice(names)
            if rng.random() < 0.5:
                chain = "".join(rng.choice(filters) for _ in range(rng.randint(1, 2)))
                cases.add("{% with p=" + name + chain + " %}{% json_viewer data=p %}{% endwith %}")
            else:
                cases.add(rng.choice(wrappers) % name)
        assert len(cases) > 100
        bad = []
        for body in sorted(cases):
            django = _render(engines, "django", body, _context())
            rust = _render(engines, "rust", body, _context())
            if django[0] == "err" and rust[0] == "err":
                # Both raise (``first`` over an int or a dict): the engines word the error
                # differently, and that is not what this class measures.
                continue
            if rust != django:
                bad.append(f"{body}\n  django {django}\n  rust   {rust}")
        assert not bad, f"{len(bad)} mismatches:\n" + "\n".join(bad[:5])


class TestAKnownGapIsOnlyOverEscaping:
    @pytest.mark.xfail(
        strict=True,
        reason=(
            "known gap: a dict KEY carries no safe bit (ObjectKey is a plain string), so a "
            "mark_safe'd key (or a loop variable over such keys) is escaped where Django leaves "
            "it raw. Over-escaping only; the same representation limit as `{{ k }}` in a loop."
        ),
    )
    def test_a_safe_dict_key_is_raw_like_django(self, engines):
        body = "{% for k in skeys %}{% json_viewer data=k %}{% endfor %}"
        django = _render(engines, "django", body, _context())
        rust = _render(engines, "rust", body, _context())
        assert django[0] == "ok" and rust == django

    def test_the_gap_never_goes_the_other_way(self, engines):
        """Whatever differs from Django on a hostile operand must be Rust escaping MORE: no
        spelling here may put raw markup on the page that Django escapes."""
        for body in (
            "{% for k in skeys %}{% json_viewer data=k %}{% endfor %}",
            "{% json_viewer data=row %}",
        ):
            rust = _render(engines, "rust", body, _context())[1]
            django = _render(engines, "django", body, _context())[1]
            assert rust.count("<") <= django.count("<"), body


class TestTheOutputOfATypedHandlerIsNeverAuthored:
    """The pin's other question: does the channel mint or drop AUTHORITY (the authored-output
    intervals that make a ``dj-lazy`` element in the template live)? The handler's output comes
    back as a plain ``String``, so it can never be authored, whatever its operand was. Proved
    with the mechanism that decides it: a ``mark_safe``-equivalent operand (``|safe`` through a
    ``{% with %}``, the very case the safe-flag fix changed from escaped to raw, as Django does)
    carries a forged lazy element into the rendered bytes, and ``authored_lazy_elements`` must
    still find only the one the template itself wrote."""

    def test_a_forged_lazy_element_in_a_raw_operand_is_not_live(self):
        from djust._rust import RustLiveView, authored_lazy_elements
        from djust.components import rust_handlers
        from djust.tests.test_lazy_provenance_http_3252 import MOD, TAG

        rust_handlers.register_with_rust_engine()
        forged = '<div dj-view="' + MOD + '.Hidden" dj-lazy></div>'
        source = (
            TAG
            + "{% load djust_components %}"
            + "{% with p=q|safe %}{% json_viewer data=p %}{% endwith %}"
        )
        rust = RustLiveView(source, [])
        rust.update_state({"q": forged})
        html, spans = rust.render_with_provenance()
        assert forged in html, "the operand did not reach the page raw: the premise is gone"
        found = authored_lazy_elements(html, spans)
        assert len(found) == 1, found
        assert found[0][2].endswith(".Child")

    def test_the_same_operand_escaped_is_inert_text(self):
        from djust._rust import RustLiveView, authored_lazy_elements
        from djust.components import rust_handlers
        from djust.tests.test_lazy_provenance_http_3252 import MOD, TAG

        rust_handlers.register_with_rust_engine()
        forged = '<div dj-view="' + MOD + '.Hidden" dj-lazy></div>'
        source = TAG + "{% load djust_components %}{% json_viewer data=q %}"
        rust = RustLiveView(source, [])
        rust.update_state({"q": forged})
        html, spans = rust.render_with_provenance()
        assert forged not in html
        assert len(authored_lazy_elements(html, spans)) == 1
