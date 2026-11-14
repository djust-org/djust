"""``djust_theming``: a callable ``url`` and the ``**attrs`` pass-through are checked too (#3749).

Two ways a URL reached a theming attribute without being judged by
``djust.components.utils.url_allowed`` (found in the review of #3747, which closed the
same hole for every URL the tags are handed by name):

1. **A callable on an item.** ``theme_nav`` / ``theme_nav_group`` / ``theme_breadcrumb`` /
   ``theme_sidebar_nav`` neutralise ``item.url`` BEFORE the template prints it, and
   the template engine calls a callable when it resolves ``{{ item.url }}``: the helper judged
   ``"<bound method ...>"`` (no scheme, allowed) and the engine then printed what the method
   returned. The check now resolves the value the way the template engine will (the same
   rules: ``do_not_call_in_templates``, ``alters_data``, a call that needs arguments), so it
   judges the string that is printed. A mapping that is not a ``dict`` is read the same way, and
   so is a callable ``items`` on a section.
2. **The attributes ``theme_button`` / ``theme_input`` / ``theme_select`` / ``theme_progress``
   pass through.** ``href`` / ``src`` / ``action`` / ``formaction`` were judged; ``ping``,
   ``poster``, ``srcset``, ``data``, ``background`` and the framework's own ``dj-navigate`` /
   ``dj-patch`` / ``dj-patch-reload`` were printed as written and are judged now. The set is an
   EXPLICIT one (PR #3765 narrowed a first version that judged any ``data-*`` / ``dj-*`` name
   containing ``link`` / ``image`` ...): every name the sweep scanner reads as a URL is either
   judged or in ``UNJUDGED_PASSTHROUGH`` with the reason, and the two tables are pinned against
   production and the scanner.
3. **A lazy value that is marked safe** (``lazy(lambda: mark_safe(...), str)()``) is not an
   instance of ``SafeData`` but prints raw: it is converted to plain text like a ``SafeString``.

What each class proves, and what it cannot: ``TestCallableUrl`` / ``TestEveryPassthroughTagAndAttribute``
drive the real tags through BOTH engines and read the FINAL attributes with the sweep's scanner
(never the sanitiser's own verdict, #1825); ``TestThePassthroughSetIsPinned`` is the derived
enumeration that makes a new pass-through tag or attribute a visible decision (the sweep in
``test_url_scheme_sweep_3665.py`` reaches the same sinks from the outside).

Refs #3749, #3747, #3677, #3665, #1825, #1646, #1468.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Mapping
from pathlib import Path

import pytest
from django.template import engines
from django.utils.functional import lazy
from django.utils.safestring import SafeData, mark_safe

from scripts.lib.template_library_parity import url_sweep

pytestmark = pytest.mark.theming

ROOT = Path(__file__).resolve().parents[2]
THEMING = ROOT / "python/djust/theming"

#: Spellings the allowlist rejects; each must become ``#`` wherever it is printed.
REJECTED = {
    "javascript": "javascript:alert(1)",
    "javascript-tab": "java\tscript:alert(1)",
    "javascript-lead-control": "\x01javascript:alert(1)",
    "vbscript": "vbscript:msgbox(1)",
    "data-html": "data:text/html,<script>alert(1)</script>",
    "livescript": "livescript:alert(1)",
    "blob": "blob:https://example.com/0b6d",
    "blob-lead-control": "\x01blob:https://example.com/x",
    "file": "file:///etc/passwd",
    "intent": "intent://example.com/x#Intent;scheme=https;end",
    "custom": "x-app://open/42",
}
#: Spellings a browser reads as a relative URL (a reference is not decoded inside a scheme).
INERT = {
    "entity-letter": "&#106;avascript:alert(1)",
    "entity-colon": "javascript&colon;alert(1)",
    "percent-colon": "javascript%3Aalert(1)",
}
#: What the allowlist keeps; printed escaped once.
PRESERVED = {
    "relative": "/items/42/",
    "anchor": "#section",
    "query": "?page=2",
    "https": "https://example.com/a?b=1&c=2",
    "mailto": "mailto:a@example.com?subject=hi",
    "tel": "tel:+15551234567",
}
#: A marked-safe value that is a script URL once a browser decodes the reference.
MARKED_SAFE = {
    "entity-letter": "&#x6A;avascript:x",
    "entity-colon": "javascript&colon;alert(1)",
    "entity-blob": "&#x62;lob:https://example.com/x",
}


@pytest.fixture(scope="module", params=["django", "djust"])
def engine(request):
    if request.param == "django":
        return engines["django"]
    from djust.template import DjustTemplateBackend

    return DjustTemplateBackend({"NAME": "djust", "DIRS": [], "APP_DIRS": True, "OPTIONS": {}})


def _render(engine, library: str, source: str, context: dict) -> str:
    return engine.from_string("{% load " + library + " %}" + source).render(context)


def _shown(url: str) -> str:
    return url.replace("&", "&amp;")


def _holds_no_url(html: str, url: str) -> None:
    assert url_sweep.findings(html) == [], html
    assert not url_sweep.carries(html, url), html


# --------------------------------------------------------------------------- #
# 1. A callable on an item


class _Item:
    """An object item whose ``url`` is a METHOD (what ``def url(self)`` on a model is)."""

    def __init__(self, label: str, url: str) -> None:
        self.label = label
        self._url = url

    def url(self) -> str:
        return self._url


def _dict_callable(url):
    return {"label": "L", "url": lambda: url}


def _object_method(url):
    return _Item("L", url)


def _mapping_not_a_dict(url):
    class Row(Mapping):
        def __init__(self, data):
            self._d = data

        def __getitem__(self, k):
            return self._d[k]

        def __iter__(self):
            return iter(self._d)

        def __len__(self):
            return len(self._d)

    return Row({"label": "L", "url": url})


def _callable_returning_safe(url):
    return {"label": "L", "url": lambda: mark_safe(url)}


def _lazy_safe(url):
    return {"label": "L", "url": lazy(lambda: mark_safe(url), str)()}


SHAPES = {
    "dict-callable": _dict_callable,
    "object-method": _object_method,
    "mapping-not-a-dict": _mapping_not_a_dict,
}

#: ``(library, source, context builder)``: every list tag that reads ``item.url``.
LIST_TAGS = {
    "theme_nav": ("{% theme_nav items=items %}", lambda item: {"items": [item]}),
    "theme_nav_group": (
        "{% theme_nav_group 'G' items=items %}",
        lambda item: {"items": [item]},
    ),
    "theme_breadcrumb": (
        "{% theme_breadcrumb items=items %}",
        lambda item: {"items": [item, {"label": "Here"}]},
    ),
    "theme_sidebar_nav": (
        "{% theme_sidebar_nav sections=sections %}",
        lambda item: {"sections": [{"title": "S", "items": [item]}]},
    ),
}


@pytest.mark.parametrize("tag", list(LIST_TAGS))
class TestCallableUrl:
    """The URL the engine will print is the one that is judged."""

    @pytest.mark.parametrize("shape", list(SHAPES))
    @pytest.mark.parametrize("url", list(REJECTED.values()), ids=list(REJECTED))
    def test_a_rejected_scheme_from_a_callable_becomes_a_hash(self, engine, tag, shape, url):
        source, context = LIST_TAGS[tag]
        html = _render(engine, "theme_components", source, context(SHAPES[shape](url)))
        _holds_no_url(html, url)
        assert 'href="#"' in html or 'dj-navigate="#"' in html, html

    @pytest.mark.parametrize("shape", list(SHAPES))
    @pytest.mark.parametrize("url", list(INERT.values()), ids=list(INERT))
    def test_an_encoded_scheme_from_a_callable_is_never_a_scheme(self, engine, tag, shape, url):
        source, context = LIST_TAGS[tag]
        html = _render(engine, "theme_components", source, context(SHAPES[shape](url)))
        assert url_sweep.findings(html) == [], html
        assert "&#106;avascript" not in html, html

    @pytest.mark.parametrize("url", list(MARKED_SAFE.values()), ids=list(MARKED_SAFE))
    def test_a_callable_returning_a_marked_safe_url_is_escaped(self, engine, tag, url):
        """The method's ``SafeString`` is judged AND printed as plain text, not raw."""
        source, context = LIST_TAGS[tag]
        html = _render(engine, "theme_components", source, context(_callable_returning_safe(url)))
        assert url_sweep.findings(html) == [], html
        assert url not in html, html

    @pytest.mark.parametrize("shape", list(SHAPES))
    @pytest.mark.parametrize("url", list(PRESERVED.values()), ids=list(PRESERVED))
    def test_an_allowed_url_from_a_callable_is_still_printed_once(self, engine, tag, shape, url):
        source, context = LIST_TAGS[tag]
        html = _render(engine, "theme_components", source, context(SHAPES[shape](url)))
        assert url_sweep.findings(html) == [], html
        assert 'href="%s"' % _shown(url) in html, html
        assert "&amp;amp;" not in html, html

    @pytest.mark.parametrize("url", ["javascript:alert(1)", "blob:https://e.com/x"])
    def test_a_lazy_marked_safe_url_is_plain_text(self, engine, tag, url):
        source, context = LIST_TAGS[tag]
        html = _render(engine, "theme_components", source, context(_lazy_safe(url)))
        _holds_no_url(html, url)

    def test_a_marked_safe_lazy_entity_is_escaped_not_printed_raw(self, engine, tag):
        """#3749 third gap: ``lazy(lambda: mark_safe('&#x6A;avascript:x'), str)()``."""
        url = "&#x6A;avascript:x"
        source, context = LIST_TAGS[tag]
        html = _render(engine, "theme_components", source, context(_lazy_safe(url)))
        assert url_sweep.findings(html) == [], html
        assert url not in html, html


class TestACallableIsResolvedLikeTheEngineDoesIt:
    """Only what the engine would call is called, once, and what it would not is left alone."""

    def _nav(self, item):
        return _render(
            engines["django"],
            "theme_components",
            "{% theme_nav items=items %}",
            {"items": [item]},
        )

    def test_a_callable_is_called_once(self):
        calls = []

        def url():
            calls.append(1)
            return "/once/"

        html = self._nav({"label": "L", "url": url})
        assert 'href="/once/"' in html and len(calls) == 1, (calls, html)

    def test_alters_data_is_never_called(self):
        calls = []

        def url():
            calls.append(1)
            return "javascript:alert(1)"

        url.alters_data = True
        html = self._nav({"label": "L", "url": url})
        assert calls == [], "the engine refuses to call alters_data; so must the check"
        assert url_sweep.findings(html) == [], html
        assert "javascript" not in html, html

    def test_do_not_call_in_templates_is_not_called(self):
        calls = []

        class Link:
            do_not_call_in_templates = True

            def __call__(self):
                calls.append(1)
                return "javascript:alert(1)"

            def __str__(self):
                return "/shown/"

        html = self._nav({"label": "L", "url": Link()})
        assert calls == [], calls
        assert 'href="/shown/"' in html, html

    def test_a_callable_that_needs_arguments_is_left_to_the_engine(self):
        def url(required):
            return "javascript:alert(1)"

        html = self._nav({"label": "L", "url": url})
        assert url_sweep.findings(html) == [], html
        assert "javascript" not in html, html

    def test_a_callable_items_on_a_section_is_resolved(self):
        """``sections=[{"items": callable}]``: the check read the callable, not what it returns."""
        html = _render(
            engines["django"],
            "theme_components",
            "{% theme_sidebar_nav sections=sections %}",
            {
                "sections": [
                    {"title": "S", "items": lambda: [{"label": "L", "url": "javascript:alert(1)"}]}
                ]
            },
        )
        assert url_sweep.findings(html) == [], html
        assert "javascript" not in html, html

    def test_the_callers_items_are_not_mutated(self):
        item = {"label": "L", "url": lambda: "javascript:alert(1)"}
        original = item["url"]
        self._nav(item)
        assert item["url"] is original


# --------------------------------------------------------------------------- #
# 2. The attributes the tags pass through

#: The tags whose ``**attrs`` reach the element as ``name="value"`` pairs, with a call that
#: reaches ``_passthrough_attrs`` (derived and compared with the AST below).
PASSTHROUGH_TAGS = {
    "theme_button": "{% theme_button 'Go' ATTR=u %}",
    "theme_input": "{% theme_input name='q' ATTR=u %}",
    "theme_select": "{% theme_select 'c' options=opts ATTR=u %}",
    "theme_progress": "{% theme_progress value=1 ATTR=u %}",
}

#: The pass-through attributes theming JUDGES besides ``href`` / ``src`` / ``action`` /
#: ``formaction`` (an explicit set, decided name by name, #3765), written as a caller writes
#: them (``dj_navigate`` -> ``dj-navigate``). ``ping`` / ``srcset`` are lists of URLs, ``poster`` /
#: ``data`` / ``background`` are fetched by a browser, ``dj-navigate`` / ``dj-patch`` /
#: ``dj-patch-reload`` are the framework's own URL-valued directives.
PASSTHROUGH_ATTRS = sorted(
    ["ping", "srcset", "poster", "data", "background", "dj_navigate", "dj_patch", "dj_patch_reload"]
)

#: Names the sweep scanner reads as URL attributes that theming deliberately does NOT judge, with
#: the decision. The table lives in the sweep library so the tests and
#: ``scripts/template-library-url-sweep.py`` excuse the same holes (#4130).
UNJUDGED_PASSTHROUGH = url_sweep.UNJUDGED_PASSTHROUGH

#: The HTML URL-named attributes among them (all but the author-defined ``data_url`` sample), in
#: the form the scanner lists them: the decision record that production's comment points to.
DECIDED_NOT_A_URL = {a for a in UNJUDGED_PASSTHROUGH if a != "data_url"}

#: Every name the sweep drives through the pass-through tags (judged or not), plus the four that
#: have always been judged (and raise for a script scheme).
DRIVEN_ATTRS = sorted(
    {*PASSTHROUGH_ATTRS, *UNJUDGED_PASSTHROUGH, "href", "src", "action", "formaction"}
)


def _passthrough(engine, tag: str, attr: str, value) -> str:
    source = PASSTHROUGH_TAGS[tag].replace("ATTR", attr)
    return _render(engine, "theme_components", source, {"u": value, "opts": []})


def _attribute_value(html: str, attr: str) -> str | None:
    name = attr.replace("_", "-")
    for a in url_sweep.attributes(html):
        if a.name == name:
            return a.value
    return None


def _check_rejected(engine, tag, attr, url):
    html = _passthrough(engine, tag, attr, url)
    _holds_no_url(html, url)
    value = _attribute_value(html, attr)
    if attr in ("srcset", "ping") and re.search(r"[\t\n\f\r ]", url):
        # a LIST: ``java<TAB>script:alert(1)`` is TWO entries as the HTML spec splits it:
        # the relative URL ``java`` and ``script:alert(1)``, an unknown scheme. In a
        # ``ping`` the second is rejected (``#``); in a ``srcset`` it is a descriptor of the
        # first candidate (an invalid one, so the browser drops the candidate), which is no
        # URL. Neither is the ``javascript:`` URL the payload spells in an ``href``.
        assert value != url, html
        if attr == "ping":
            assert "#" in value.split(), html
    else:
        assert value == "#", html


def _check_inert(engine, tag, attr, url):
    html = _passthrough(engine, tag, attr, url)
    assert url_sweep.findings(html) == [], html


def _check_marked_safe(engine, tag, attr, url):
    html = _passthrough(engine, tag, attr, mark_safe(url))
    assert url_sweep.findings(html) == [], html
    assert url not in html, html


def _check_preserved(engine, tag, attr, url):
    html = _passthrough(engine, tag, attr, url)
    assert url_sweep.findings(html) == [], html
    assert '%s="%s"' % (attr.replace("_", "-"), _shown(url)) in html, html
    assert "&amp;amp;" not in html, html


def _pick(table: dict[str, str], *labels: str) -> dict[str, str]:
    return {label: table[label] for label in labels}


#: The tags share ``_passthrough_attrs``. The full corpus covers every payload for
#: ``theme_input``, so the other tags only need a representative payload per family.
FULL_CORPUS_TAG = "theme_input"
PASSTHROUGH_CASES = [(attr, tag) for attr in PASSTHROUGH_ATTRS for tag in PASSTHROUGH_TAGS]
QUICK_PASSTHROUGH_CASES = [(attr, tag) for attr, tag in PASSTHROUGH_CASES if tag != FULL_CORPUS_TAG]
QUICK_REJECTED = _pick(REJECTED, "javascript", "blob-lead-control", "data-html", "custom")
QUICK_INERT = _pick(INERT, "entity-letter")
QUICK_MARKED_SAFE = _pick(MARKED_SAFE, "entity-letter")
QUICK_PRESERVED = _pick(PRESERVED, "relative", "https")


class TestEveryPassthroughTagAndAttribute:
    @pytest.mark.parametrize("attr,tag", QUICK_PASSTHROUGH_CASES)
    @pytest.mark.parametrize("url", list(QUICK_REJECTED.values()), ids=list(QUICK_REJECTED))
    def test_a_rejected_scheme_becomes_a_hash(self, engine, tag, attr, url):
        _check_rejected(engine, tag, attr, url)

    @pytest.mark.parametrize("attr,tag", QUICK_PASSTHROUGH_CASES)
    @pytest.mark.parametrize("url", list(QUICK_INERT.values()), ids=list(QUICK_INERT))
    def test_an_encoded_scheme_is_never_a_scheme(self, engine, tag, attr, url):
        _check_inert(engine, tag, attr, url)

    @pytest.mark.parametrize("attr,tag", QUICK_PASSTHROUGH_CASES)
    @pytest.mark.parametrize("url", list(QUICK_MARKED_SAFE.values()), ids=list(QUICK_MARKED_SAFE))
    def test_a_marked_safe_value_is_judged_and_escaped(self, engine, tag, attr, url):
        _check_marked_safe(engine, tag, attr, url)

    @pytest.mark.parametrize("attr,tag", QUICK_PASSTHROUGH_CASES)
    @pytest.mark.parametrize("url", list(QUICK_PRESERVED.values()), ids=list(QUICK_PRESERVED))
    def test_an_allowed_url_is_printed_escaped_once(self, engine, tag, attr, url):
        _check_preserved(engine, tag, attr, url)

    @pytest.mark.parametrize("attr,tag", PASSTHROUGH_CASES)
    def test_a_script_scheme_never_raises_here(self, engine, tag, attr):
        """Only ``href`` / ``src`` / ``action`` / ``formaction`` have always raised; these
        rendered anything before, so a page must not start to 500 on one."""
        html = _passthrough(engine, tag, attr, "javascript:alert(1)")
        assert "javascript" not in html, html


@pytest.mark.parametrize("attr", PASSTHROUGH_ATTRS)
class TestEveryPayloadThroughOneTag:
    """The whole corpus (every spelling of every rejected scheme) for each attribute."""

    TAG = FULL_CORPUS_TAG

    @pytest.mark.parametrize("url", list(REJECTED.values()), ids=list(REJECTED))
    def test_a_rejected_scheme_becomes_a_hash(self, engine, attr, url):
        _check_rejected(engine, self.TAG, attr, url)

    @pytest.mark.parametrize("url", list(INERT.values()), ids=list(INERT))
    def test_an_encoded_scheme_is_never_a_scheme(self, engine, attr, url):
        _check_inert(engine, self.TAG, attr, url)

    @pytest.mark.parametrize("url", list(MARKED_SAFE.values()), ids=list(MARKED_SAFE))
    def test_a_marked_safe_value_is_judged_and_escaped(self, engine, attr, url):
        _check_marked_safe(engine, self.TAG, attr, url)

    @pytest.mark.parametrize("url", list(PRESERVED.values()), ids=list(PRESERVED))
    def test_an_allowed_url_is_printed_escaped_once(self, engine, attr, url):
        _check_preserved(engine, self.TAG, attr, url)


class TestListValuedAttributes:
    """``srcset`` and ``ping`` hold SEVERAL URLs; each is judged, the others kept."""

    @pytest.mark.parametrize(
        "value",
        [
            "/a.png 1x, javascript:alert(1) 2x",
            "/a.png 1x,javascript:alert(1) 2x",
            "/a.png 10w ((), javascript:alert(1) 100w",
            "/a.png 1x,\x0cjavascript:alert(1) 2x",
            "/a.png 1x, blob:https://e.com/x 2x",
            "/a.png 1x, data:text/html,<script>alert(1)</script> 2x",
        ],
    )
    def test_a_rejected_candidate_in_a_srcset_is_neutralised(self, engine, value):
        html = _passthrough(engine, "theme_button", "srcset", value)
        assert url_sweep.findings(html) == [], html
        assert "javascript" not in html and "blob:" not in html and "<script" not in html, html
        assert "/a.png" in html, "the good candidate is kept: " + html

    def test_a_data_image_is_kept_in_a_srcset_and_a_poster(self, engine):
        uri = "data:image/png;base64,iVBORw0KGgo="
        assert uri in _passthrough(engine, "theme_button", "srcset", uri + " 1x")
        assert uri in _passthrough(engine, "theme_button", "poster", uri)

    def test_a_data_image_is_not_kept_where_no_image_loads(self, engine):
        uri = "data:image/png;base64,iVBORw0KGgo="
        for attr in ("ping", "data", "background"):
            assert uri not in _passthrough(engine, "theme_button", attr, uri), attr

    @pytest.mark.parametrize(
        "value",
        [
            "/p https://ok.example/ping javascript:alert(1)",
            "javascript:alert(1)\t/p",
            "/p\nblob:https://e.com/x",
            "/p\x0cfile:///etc/passwd",
        ],
    )
    def test_a_rejected_url_in_a_ping_list_is_neutralised(self, engine, value):
        html = _passthrough(engine, "theme_button", "ping", value)
        assert url_sweep.findings(html) == [], html
        for bad in ("javascript:", "blob:", "file:"):
            assert bad not in html, html

    def test_a_ping_list_of_allowed_urls_is_kept(self, engine):
        html = _passthrough(engine, "theme_button", "ping", "/p https://ok.example/ping")
        assert 'ping="/p https://ok.example/ping"' in html, html


class TestOrdinaryAttributesAreUntouched:
    """The URL check is for URL attributes: a label with a colon is still a label."""

    @pytest.mark.parametrize(
        "attr, value",
        [
            ("title", "Note: read this"),
            ("aria_label", "Open: menu"),
            ("placeholder", "e.g. javascript:void"),
            ("data_id", "ns:42"),
            ("dj_click", "save"),
            ("autocomplete", "blob:x"),
            ("value", "file:///not-a-url"),
        ],
    )
    def test_a_non_url_attribute_is_printed_as_written(self, attr, value):
        html = _passthrough(engines["django"], "theme_input", attr, value)
        assert '%s="%s"' % (attr.replace("_", "-"), value) in html, html


class TestMarkedSafeLazyValues:
    def test_plain_url_converts_a_lazy_safe_value_to_text(self):
        from djust.theming.templatetags.theme_components import _plain_url

        out = _plain_url(lazy(lambda: mark_safe("&#x6A;avascript:x"), str)())
        assert out == "&#x6A;avascript:x"
        assert not isinstance(out, SafeData) and not hasattr(out, "__html__")

    @pytest.mark.parametrize("value", [None, "", "/ok/"])
    def test_plain_url_leaves_text_and_none_alone(self, value):
        from djust.theming.templatetags.theme_components import _plain_url

        assert _plain_url(value) is value

    def test_plain_url_converts_anything_else_to_text_once(self):
        """The text that is judged is the text that is printed (#3765): an int is its digits."""
        from djust.theming.templatetags.theme_components import _plain_url

        out = _plain_url(3)
        assert out == "3" and type(out) is str


# --------------------------------------------------------------------------- #
# The pass-through set is pinned


def _functions(path: Path):
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.FunctionDef):
            yield node


def _calls(node: ast.AST, name: str) -> bool:
    return any(isinstance(n, ast.Name) and n.id == name for n in ast.walk(node)) or any(
        isinstance(n, ast.Attribute) and n.attr == name for n in ast.walk(node)
    )


#: Tags with ``**attrs`` that do NOT print arbitrary attributes: a template reads named keys
#: (``attrs.class`` / ``attrs.id`` / ``attrs.required`` ...), so there is no caller-named URL
#: attribute. The set is DERIVED from the code (a ``**attrs`` tag that does not call
#: ``_passthrough_attrs``) and compared with this one, so a new ``**attrs`` tag, or one that
#: starts to print arbitrary attributes, is a decision made here.
NAMED_ATTRS_ONLY = {
    *("theme_%s" % n for n in (
        "card", "badge", "alert", "modal", "dropdown", "tabs", "table", "pagination", "textarea",
        "checkbox", "radio", "breadcrumb", "avatar", "toast", "skeleton", "tooltip", "nav_item",
        "nav_group", "nav", "sidebar_nav",
    )),
    *("theme_%s_page" % n for n in (
        "login", "register", "password_reset", "password_confirm", "404", "500", "403",
        "maintenance", "empty_state",
    )),
}  # fmt: skip

#: Django FORM-WIDGET templates (``widget.attrs.items``): the attributes are the form author's
#: own, set in Python on the widget (``forms.TextInput(attrs={...})``), not a template-tag
#: argument a page passes. Pinned by file so a new widget template that prints attrs is seen.
WIDGET_ATTR_TEMPLATES = {
    "attrs.html", "checkbox.html", "file.html", "input.html", "input_option.html",
    "multiple_input.html", "select.html", "textarea.html",
}  # fmt: skip


class TestThePassthroughSetIsPinned:
    def test_the_tags_that_print_arbitrary_attributes_are_exactly_these(self):
        """A tag that calls ``_passthrough_attrs`` is a sink for every attribute name."""
        found = set()
        for path in sorted((THEMING / "templatetags").glob("theme_*.py")):
            for fn in _functions(path):
                if fn.name.startswith("theme_") and _calls(fn, "_passthrough_attrs"):
                    found.add(fn.name)
        assert found == set(PASSTHROUGH_TAGS), sorted(found ^ set(PASSTHROUGH_TAGS))

    def test_every_other_attrs_tag_reads_named_keys_only(self):
        """Derived from the code: a new ``**attrs`` tag is a decision, not an oversight."""
        other = set()
        for path in sorted((THEMING / "templatetags").glob("theme_*.py")):
            for fn in _functions(path):
                if (
                    fn.name.startswith("theme_")
                    and fn.args.kwarg is not None
                    and fn.args.kwarg.arg == "attrs"
                    and not _calls(fn, "_passthrough_attrs")
                ):
                    other.add(fn.name)
        assert other == NAMED_ATTRS_ONLY, sorted(other ^ NAMED_ATTRS_ONLY)

    def test_no_template_prints_attrs_whole_or_loops_over_it(self):
        """The only arbitrary-name printer is ``{{ extra_attrs }}`` (built by the check); the
        other templates read ``attrs.<name>``, and the form-widget templates print the widget's
        own attrs (:data:`WIDGET_ATTR_TEMPLATES`)."""
        offenders, widgets = [], set()
        for path in sorted((THEMING / "templates").rglob("*.html")):
            text = path.read_text(encoding="utf-8")
            for m in re.finditer(r"\{[{%][^}]*?\battrs\b[^}]*?[}%]\}", text):
                snippet = m.group(0)
                if re.search(r"widget\.attrs", snippet):
                    widgets.add(path.name)
                elif re.search(r"(?<![\w.])attrs\.(?!items\b)\w+", snippet):
                    continue  # a named read: attrs.class, attrs.required ...
                else:
                    offenders.append((path.name, snippet))
        assert offenders == [], offenders
        assert widgets == WIDGET_ATTR_TEMPLATES, sorted(widgets ^ WIDGET_ATTR_TEMPLATES)

    def test_extra_attrs_is_printed_only_by_templates_that_are_pinned(self):
        printers = sorted(
            {
                str(p.relative_to(THEMING / "templates/djust_theming")).replace("\\", "/")
                for p in (THEMING / "templates").rglob("*.html")
                if "{{ extra_attrs }}" in p.read_text(encoding="utf-8")
            }
        )
        names = {Path(p).name for p in printers}
        assert names == {"button.html", "input.html", "select.html", "progress.html"}, printers
        # the sweep's baseline excuses unjudged names on exactly these tags (#4130)
        assert set(PASSTHROUGH_TAGS) == url_sweep.PASSTHROUGH_TAGS

    def test_every_url_attribute_the_scanner_reads_is_judged_or_decided_not_to_be(self):
        """The scanner keeps its own list (it must not use production as its oracle); this is
        where the two are compared. Each name it reads as a URL is either judged by production
        or in ``UNJUDGED_PASSTHROUGH`` with a reason, so a name added to the scanner (or dropped
        from production) is a decision made here, not a silent gap."""
        from djust.theming.templatetags import theme_components as tc

        scanner = {a for a in url_sweep.URL_ATTRS if ":" not in a}
        judged = set(tc._URL_ATTRS) | set(tc._QUIET_URL_ATTRS)
        decided_not = DECIDED_NOT_A_URL
        assert scanner == (judged & scanner) | decided_not, sorted(
            scanner ^ ((judged & scanner) | decided_not)
        )
        assert not judged & decided_not
        # what production judges beyond the scanner's list: the framework's own directives
        assert judged - scanner == {"dj-navigate", "dj-patch", "dj-patch-reload"}, sorted(
            judged - scanner
        )

    def test_the_judged_and_unjudged_tables_of_this_file_are_production(self):
        from djust.theming.templatetags import theme_components as tc

        names = lambda attrs: {a.replace("_", "-") for a in attrs}  # noqa: E731
        assert names(PASSTHROUGH_ATTRS) == set(tc._QUIET_URL_ATTRS), sorted(
            names(PASSTHROUGH_ATTRS) ^ set(tc._QUIET_URL_ATTRS)
        )
        # the decision table names no judged attribute (``data_url`` is the author-defined sample)
        assert not {a.replace("_", "-") for a in UNJUDGED_PASSTHROUGH} & set(tc._QUIET_URL_ATTRS)

    @pytest.mark.parametrize("name", ["dj-navigate", "dj-patch", "dj-patch-reload", "ping", "data"])
    def test_a_judged_name_is_judged(self, name):
        from djust.theming.templatetags import theme_components as tc

        assert tc._is_url_attribute(name) is True

    @pytest.mark.parametrize(
        "name",
        [
            # the author-defined families: the author's own attribute, read by the author's script
            "data-url", "data-href", "data-link", "data-location", "data-image", "data-action",
            "data-imageurl", "data-srcset", "data-bg", "dj-action", "dj-redirect",
            "dj-navigatetarget", "dj-src", "data-dj-src",
            # HTML names decided not to be judged
            *UNJUDGED_PASSTHROUGH,
            # not URLs at all
            "dj-click", "data-id", "data-value", "aria-label", "title", "name",
        ],
    )  # fmt: skip
    def test_every_other_name_is_not_judged(self, name):
        from djust.theming.templatetags import theme_components as tc

        assert tc._is_url_attribute(name.replace("_", "-")) is False


@pytest.mark.parametrize("attr", sorted(set(UNJUDGED_PASSTHROUGH)))
class TestAnUnjudgedNameIsPrintedAsWritten:
    """The decision, executed: a value shaped like ``word:rest`` on a name decided not to be a
    URL attribute renders unchanged (a citation, an icon name, a namespaced id)."""

    @pytest.mark.parametrize("value", ["mdi:home", "Smith: 2020", "toggle:menu", "blob:x"])
    def test_it_renders_unchanged(self, engine, attr, value):
        html = _passthrough(engine, "theme_input", attr, value)
        assert '%s="%s"' % (attr.replace("_", "-"), value) in html, html


def test_data_is_judged_on_purpose():
    """``data`` is the ``<object>`` fetch attribute and #3749 names it, so ``data="x:y"`` (a scheme
    the allowlist does not know) renders ``#``, like ``href="x:y"``. The one pinned name on which
    that is a visible change: flip it here, in ``_QUIET_URL_ATTRS`` and in the changelog together."""
    html = _passthrough(engines["django"], "theme_input", "data", "x:y")
    assert 'data="#"' in html, html
    assert 'data="/object.pdf"' in _passthrough(
        engines["django"], "theme_input", "data", "/object.pdf"
    )
