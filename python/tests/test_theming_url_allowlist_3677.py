"""``djust_theming`` sanitises every URL with the SAME allowlist ``djust_components`` uses (#3677).

``djust_theming`` used to guard URLs with its own DENYLIST (``javascript:``, ``vbscript:``,
``data:``, and since #3665 ``livescript:`` / ``mhtml:``), so every scheme the components
allowlist rejects (``blob:``, ``file:``, ``view-source:``, ``jar:``, ``intent:``,
``ms-word:``, ``feed:``, ``ws:``, ``git:``, custom application schemes ...) passed through
19 sinks in both engines. A denylist ages badly: each new dangerous scheme is a hole until
someone adds it. Theming now asks ``djust.components.utils.url_allowed`` (the decision
``safe_url`` / ``url_attr`` make) and keeps no list of its own.

What this file proves:

* every theming URL sink, in BOTH engines (Django's and ``DjustTemplateBackend``), turns a
  non-allowlisted scheme into ``#``, in every written form a browser still reads as that
  scheme (case, leading space or C0 control, embedded tab or newline);
* the values the allowlist keeps are still printed, escaped once;
* the entity / percent-encoded spellings are printed as literal text (a relative URL), never
  as a scheme, judged on the FINAL attributes by the sweep's scanner, not by the sanitiser;
* ``SINKS`` is the complete set of theming ``(tag, parameter)`` pairs through which a value
  reaches a URL attribute: ``test_url_scheme_sweep_3665.py`` derives that set from a real
  sweep and fails when it differs, so a new sink must be added here, with its check;
* theming keeps no list that DECIDES whether a URL is allowed; the one list it has says which
  schemes raise in a developer-supplied literal (what has always raised still raises, and
  every other rejected scheme renders ``#`` instead of a 500);
* a ``mark_safe`` URL is never printed raw by a theming sink: it is judged and escaped like
  any other string, as ``safe_url`` does.

Refs #3677, #3665, #1825, #1646, #1468.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from django.template import engines
from django.utils.safestring import mark_safe

from scripts.lib.template_library_parity import url_sweep

pytestmark = pytest.mark.theming

ROOT = Path(__file__).resolve().parents[2]

#: Written forms of schemes the allowlist rejects. Every one is read by a browser as that
#: scheme, so every one must become ``#``. The first group is the issue's list; the second
#: is the same schemes written the ways a browser still normalises to them; the third is
#: what the denylist already refused (the regression half).
NEUTRALISED = {
    # the issue's list
    "livescript": "livescript:alert(1)",
    "mhtml": "mhtml:file://C:/x.mht!y",
    "blob": "blob:https://example.com/0b6d",
    "file": "file:///etc/passwd",
    "jar": "jar:https://example.com/a.jar!/x",
    "view-source": "view-source:https://example.com/",
    "intent": "intent://example.com/x#Intent;scheme=https;end",
    "ms-word": "ms-word:ofe|u|https://example.com/x.docx",
    "custom": "x-app://open/42",
    "feed": "feed:https://example.com/x.xml",
    "ws": "ws://example.com/socket",
    "git": "git://example.com/x.git",
    # not on the issue's list; the allowlist rejects them too (see the docs)
    "sms": "sms:+15551234567",
    "geo": "geo:40.7,-74.0",
    "app-deep-link": "myapp://record/42",
    # written the ways a browser still reads as the scheme
    "livescript-mixed": "LiveScript:alert(1)",
    "blob-upper": "BLOB:https://example.com/x",
    "blob-lead-space": "  blob:https://example.com/x",
    "blob-lead-newline": "\n\tblob:https://example.com/x",
    "blob-lead-control": "\x01blob:https://example.com/x",
    "blob-embedded-tab": "bl\tob:https://example.com/x",
    "blob-embedded-newline": "bl\nob:https://example.com/x",
    "file-embedded-cr": "fi\rle:///etc/passwd",
    # the script schemes the denylist already refused
    "javascript": "javascript:alert(1)",
    "javascript-mixed": "JaVaScRiPt:alert(1)",
    "javascript-tab": "java\tscript:alert(1)",
    "javascript-lead-control": "\x01javascript:alert(1)",
    "javascript-lead-space": "  javascript:alert(1)",
    "vbscript": "vbscript:msgbox(1)",
    "data-html": "data:text/html,<script>alert(1)</script>",
}

#: Spellings a browser reads as a RELATIVE URL (a character reference or a percent escape
#: is not decoded inside a scheme), so the page prints them as text. They must never become
#: a scheme in the final attribute, whatever the sanitiser thought of them.
INERT = {
    "entity-tab": "java&#x09;script:alert(1)",
    "entity-letter": "&#106;avascript:alert(1)",
    "entity-colon": "javascript&colon;alert(1)",
    "percent-letter": "%6Aavascript:alert(1)",
    "percent-colon": "javascript%3Aalert(1)",
    "blob-entity": "&#x62;lob:https://example.com/x",
}

#: Values a developer marked safe. ``safe_url`` escapes these (it never trusts the marker), so
#: theming must not print them raw either: ``mark_safe("&#x6A;avascript:x")`` has no scheme to
#: the allowlist and is ``javascript:`` to a browser if a template prints it unescaped.
MARKED_SAFE_INERT = {
    "entity-letter": "&#x6A;avascript:x",
    "entity-decimal": "&#106;avascript:x",
    "entity-colon": "javascript&colon;alert(1)",
    "entity-tab": "java&#x09;script:x",
    "entity-blob": "&#x62;lob:https://example.com/x",
}
MARKED_SAFE_REFUSED = {
    "javascript": "javascript:alert(1)",
    "blob": "blob:https://example.com/x",
    "livescript": "livescript:x",
}

#: Values the allowlist keeps; each is printed, HTML-escaped once.
PRESERVED = {
    "relative": "/items/42/",
    "relative-path": "items/42",
    "dots": "../up/x",
    "anchor": "#section",
    "query": "?page=2",
    "https": "https://example.com/a?b=1&c=2",
    "http": "http://example.com/a",
    "mailto": "mailto:a@example.com?subject=hi",
    "tel": "tel:+15551234567",
    "ftp": "ftp://example.com/x",
    "scheme-relative": "//cdn.example.com/x",
    "colon-in-path": "/a:b/c",
}

#: (tag, parameter) -> (template source, context built from the URL under test).
#: The key is the sink the sweep reports (``<library>:<tag>``, parameter), so
#: ``test_url_scheme_sweep_3665.py`` can compare this table with the sweep's own findings.
SINKS: dict[tuple[str, str], tuple[str, object]] = {
    ("theme_components:theme_nav_item", "url"): (
        "{% theme_nav_item 'L' u %}",
        lambda u: {"u": u},
    ),
    ("theme_components:theme_nav", "items"): (
        "{% theme_nav items=items %}",
        lambda u: {"items": [{"label": "L", "url": u}, {"label": "Current"}]},
    ),
    ("theme_components:theme_nav_group", "items"): (
        "{% theme_nav_group 'G' items=items %}",
        lambda u: {"items": [{"label": "L", "url": u}, {"label": "Current"}]},
    ),
    ("theme_components:theme_sidebar_nav", "sections"): (
        "{% theme_sidebar_nav sections=sections %}",
        lambda u: {"sections": [{"title": "S", "items": [{"label": "L", "url": u}]}]},
    ),
    ("theme_components:theme_breadcrumb", "items"): (
        "{% theme_breadcrumb items=items %}",
        lambda u: {"items": [{"label": "L", "url": u}, {"label": "Here"}]},
    ),
    ("theme_components:theme_avatar", "src"): (
        "{% theme_avatar src=u alt='me' %}",
        lambda u: {"u": u},
    ),
    ("theme_components:theme_pagination", "url_pattern"): (
        "{% theme_pagination current_page=2 total_pages=3 url_pattern=u %}",
        lambda u: {"u": u},
    ),
    ("theme_pages:theme_login_page", "action"): (
        "{% theme_login_page action=u %}",
        lambda u: {"u": u},
    ),
    ("theme_pages:theme_login_page", "forgot_password_url"): (
        "{% theme_login_page forgot_password_url=u %}",
        lambda u: {"u": u},
    ),
    ("theme_pages:theme_login_page", "register_url"): (
        "{% theme_login_page register_url=u %}",
        lambda u: {"u": u},
    ),
    ("theme_pages:theme_register_page", "action"): (
        "{% theme_register_page action=u %}",
        lambda u: {"u": u},
    ),
    ("theme_pages:theme_register_page", "login_url"): (
        "{% theme_register_page login_url=u %}",
        lambda u: {"u": u},
    ),
    ("theme_pages:theme_register_page", "terms_url"): (
        "{% theme_register_page terms_url=u %}",
        lambda u: {"u": u},
    ),
    ("theme_pages:theme_password_reset_page", "action"): (
        "{% theme_password_reset_page action=u %}",
        lambda u: {"u": u},
    ),
    ("theme_pages:theme_password_reset_page", "login_url"): (
        "{% theme_password_reset_page login_url=u %}",
        lambda u: {"u": u},
    ),
    ("theme_pages:theme_password_confirm_page", "action"): (
        "{% theme_password_confirm_page action=u %}",
        lambda u: {"u": u},
    ),
    ("theme_pages:theme_404_page", "home_url"): (
        "{% theme_404_page home_url=u %}",
        lambda u: {"u": u},
    ),
    ("theme_pages:theme_500_page", "home_url"): (
        "{% theme_500_page home_url=u %}",
        lambda u: {"u": u},
    ),
    ("theme_pages:theme_500_page", "retry_url"): (
        "{% theme_500_page retry_url=u %}",
        lambda u: {"u": u},
    ),
    ("theme_pages:theme_403_page", "back_url"): (
        "{% theme_403_page back_url=u %}",
        lambda u: {"u": u},
    ),
    ("theme_pages:theme_empty_state_page", "cta_url"): (
        "{% theme_empty_state_page cta_url=u cta_text='Add' %}",
        lambda u: {"u": u},
    ),
}

_LIBRARY_OF = {"theme_components": "theme_components", "theme_pages": "theme_pages"}


def _backend(name: str):
    if name == "django":
        return engines["django"]
    from djust.template import DjustTemplateBackend

    return DjustTemplateBackend({"NAME": "djust", "DIRS": [], "APP_DIRS": True, "OPTIONS": {}})


@pytest.fixture(scope="module", params=["django", "djust"])
def engine(request):
    return _backend(request.param)


def render(engine, sink: tuple[str, str], url: str) -> str:
    library = _LIBRARY_OF[sink[0].split(":", 1)[0]]
    source, context = SINKS[sink]
    return engine.from_string("{% load " + library + " %}" + source).render(context(url))


SINK_IDS = ["%s.%s" % (tag.split(":", 1)[1], param) for tag, param in SINKS]


@pytest.mark.parametrize("sink", list(SINKS), ids=SINK_IDS)
class TestEverySink:
    @pytest.mark.parametrize("url", list(NEUTRALISED.values()), ids=list(NEUTRALISED))
    def test_a_scheme_the_allowlist_rejects_becomes_a_hash(self, engine, sink, url):
        html = render(engine, sink, url)
        assert url_sweep.findings(html) == [], html
        # the value did not reach any URL attribute, in any spelling
        assert not url_sweep.carries(html, url), html
        assert 'href="#"' in html or 'src="#"' in html or 'action="#"' in html, html

    @pytest.mark.parametrize("url", list(INERT.values()), ids=list(INERT))
    def test_an_encoded_scheme_is_never_a_scheme_in_the_output(self, engine, sink, url):
        html = render(engine, sink, url)
        assert url_sweep.findings(html) == [], html
        # an entity written into the value is escaped, not left for the browser to decode
        assert "java&#x09;script" not in html and "&#106;avascript" not in html, html

    @pytest.mark.parametrize("url", list(MARKED_SAFE_INERT.values()), ids=list(MARKED_SAFE_INERT))
    def test_a_marked_safe_url_is_escaped_not_printed_raw(self, engine, sink, url):
        html = render(engine, sink, mark_safe(url))
        assert url_sweep.findings(html) == [], html
        # printed raw, the reference would be decoded by the browser into a scheme
        assert url not in html, html
        assert url.replace("&", "&amp;") in html, html

    @pytest.mark.parametrize(
        "url", list(MARKED_SAFE_REFUSED.values()), ids=list(MARKED_SAFE_REFUSED)
    )
    def test_a_marked_safe_url_with_a_rejected_scheme_becomes_a_hash(self, engine, sink, url):
        html = render(engine, sink, mark_safe(url))
        assert url_sweep.findings(html) == [], html
        assert not url_sweep.carries(html, url), html

    @pytest.mark.parametrize("url", list(PRESERVED.values()), ids=list(PRESERVED))
    def test_an_allowed_url_is_still_printed_escaped_once(self, engine, sink, url):
        html = render(engine, sink, url)
        shown = url.replace("&", "&amp;")
        assert url_sweep.findings(html) == [], html
        assert (
            'href="%s"' % shown in html
            or 'src="%s"' % shown in html
            or ('action="%s"' % shown in html)
        ), html
        assert "&amp;amp;" not in html


class TestOneRuleNotTwo:
    """Theming keeps no scheme list: the components decision is the only one."""

    def test_the_theming_url_helpers_ask_the_shared_allowlist(self):
        from djust.components import utils
        from djust.theming.templatetags import theme_components

        assert theme_components.url_allowed is utils.url_allowed

    @pytest.mark.parametrize("value", [*NEUTRALISED.values(), *PRESERVED.values(), "", None])
    @pytest.mark.parametrize("image", [False, True])
    def test_neutralise_url_agrees_with_the_shared_function(self, value, image):
        from djust.components.utils import url_allowed
        from djust.theming.templatetags.theme_components import _neutralise_url

        kept = _neutralise_url("u", value, image=image)
        if value is None or url_allowed(value, image=image):
            assert kept is value
        else:
            assert kept == "#"

    @pytest.mark.parametrize("value", [*MARKED_SAFE_INERT.values(), "/ok/", "javascript:x"])
    def test_a_marked_safe_value_leaves_the_helpers_as_plain_text(self, value):
        """Both helpers return text a template escapes, never a ``SafeData``."""
        from django.utils.safestring import SafeData

        from djust.theming.templatetags.theme_components import _check_url, _neutralise_url

        for helper in (_neutralise_url, _check_url):
            try:
                out = helper("u", mark_safe(value))
            except ValueError:
                assert value.startswith("javascript:")
                continue
            assert not isinstance(out, SafeData), (helper.__name__, out)

    def test_an_object_with_html_is_plain_text_too(self):
        from django.utils.safestring import SafeData

        from djust.theming.templatetags.theme_components import _neutralise_url

        class Marked:
            def __html__(self):
                return "&#x6A;avascript:x"

            def __str__(self):
                return "&#x6A;avascript:x"

        out = _neutralise_url("u", Marked())
        assert out == "&#x6A;avascript:x" and not isinstance(out, SafeData)

    @pytest.mark.parametrize("value", ["", "   ", "\t\n"])
    def test_an_empty_url_is_allowed(self, value):
        """Pins the intended result: nothing to navigate to is not a rejected scheme.

        ``_neutralise_url`` compares itself with ``url_allowed``, so it cannot catch
        ``url_allowed("")`` flipping to ``False``; this does.
        """
        from djust.components.utils import url_allowed
        from djust.theming.templatetags.theme_components import _neutralise_url

        assert url_allowed(value) is True
        assert url_allowed(value, image=True) is True
        assert _neutralise_url("u", value) == value

    @pytest.mark.parametrize(
        "value",
        ["javascript://[x%0aalert(1)", "http://[::1", "https://[bad]/x", "//[x"],
    )
    def test_a_url_the_parser_cannot_read_is_rejected(self, value):
        """``urlsplit`` raises ``ValueError`` on these ("Invalid IPv6 URL"); the allowlist
        fails closed, which is the only guard for such a value (a browser rejects the URL
        too, so this is defence in depth, but a silent flip to "allowed" must fail here)."""
        from urllib.parse import urlsplit

        from djust.components.utils import safe_url, url_allowed

        with pytest.raises(ValueError):
            urlsplit(value)
        assert url_allowed(value) is False
        assert safe_url(value) == "#"

    @pytest.mark.parametrize("value", [*NEUTRALISED.values(), *PRESERVED.values()])
    def test_url_allowed_is_the_decision_safe_url_makes(self, value):
        from djust.components.utils import safe_url, url_allowed

        refused = safe_url(value) == "#" and value.strip() != "#"
        assert url_allowed(value) is (not refused)

    def test_theming_source_has_no_scheme_list_of_its_own(self):
        """The shape of the old denylist (a tuple of ``"scheme:"`` strings) is gone.

        A collection of bare scheme tokens in the theming modules is a second mechanism
        waiting to drift. The AST is read, not the text, so a docstring that names a
        scheme does not count. (``_UNSAFE_URL_SCHEMES`` and ``_has_unsafe_url_scheme`` were
        the old ones.)
        """
        token = re.compile(r"[a-z][a-z0-9+.-]*:")
        found = []
        for path in sorted((ROOT / "python/djust/theming").rglob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, (ast.Tuple, ast.List, ast.Set)) and len(node.elts) >= 2:
                    values = [e.value for e in node.elts if isinstance(e, ast.Constant)]
                    if len(values) == len(node.elts) and all(
                        isinstance(v, str) and token.fullmatch(v.lower()) for v in values
                    ):
                        found.append("%s:%d %r" % (path.name, node.lineno, values))
        assert found == [], found
        from djust.theming.templatetags import theme_components

        assert not hasattr(theme_components, "_UNSAFE_URL_SCHEMES")
        assert not hasattr(theme_components, "_has_unsafe_url_scheme")


class TestLiteralAndPassthroughSinks:
    """``theme_button href=`` and the ``**attrs`` ``href``/``src``/``action``/``formaction``.

    These sinks used to RAISE for a script scheme and RENDER every other scheme. The
    allowlist must not turn a link that rendered into a 500 (``href=record.url`` is the
    documented use, and ``record.url`` is data): what raised still raises, and every other
    scheme the allowlist rejects renders ``#``.
    """

    #: schemes the old denylist did not name: they rendered before, so they must not raise
    RENDERED_BEFORE = [
        "sms:+15551234567",
        "webcal://example.com/cal.ics",
        "myapp://record/42",
        "blob:https://example.com/x",
        "file:///etc/passwd",
        "intent://example.com/x#Intent;scheme=https;end",
    ]
    #: schemes (and spellings) that raised before and still do
    RAISED_BEFORE = [
        "javascript:alert(1)",
        "JaVaScRiPt:alert(1)",
        "java\tscript:alert(1)",
        "\x01javascript:alert(1)",
        "  vbscript:msgbox(1)",
        "data:text/html,<script>alert(1)</script>",
        "livescript:alert(1)",
        "mhtml:file://C:/x.mht!y",
    ]

    @staticmethod
    def _button(url):
        return (
            engines["django"]
            .from_string("{% load theme_components %}{% theme_button 'Go' href=u %}")
            .render({"u": url})
        )

    @staticmethod
    def _input(attr, url):
        return (
            engines["django"]
            .from_string("{% load theme_components %}{% theme_input name='q' " + attr + "=u %}")
            .render({"u": url})
        )

    @pytest.mark.parametrize("url", RENDERED_BEFORE)
    def test_button_href_renders_a_hash_instead_of_raising(self, url, caplog):
        with caplog.at_level("WARNING", logger="djust.theming.templatetags.theme_components"):
            html = self._button(url)
        assert 'href="#"' in html and url not in html, html
        assert url_sweep.findings(html) == []
        # the warning names the parameter, not the (possibly sensitive) URL
        warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert any("href=" in m for m in warnings), warnings
        assert not any(url in m for m in warnings), warnings

    @pytest.mark.parametrize("url", RAISED_BEFORE)
    def test_button_href_still_raises_for_a_script_scheme(self, url):
        with pytest.raises(ValueError, match="refuses"):
            self._button(url)

    @pytest.mark.parametrize("attr", ["href", "src", "action", "formaction"])
    @pytest.mark.parametrize("url", RENDERED_BEFORE)
    def test_passthrough_renders_a_hash_instead_of_raising(self, attr, url):
        html = self._input(attr, url)
        assert '%s="#"' % attr in html and url not in html, html

    @pytest.mark.parametrize("attr", ["href", "src", "action", "formaction"])
    @pytest.mark.parametrize("url", RAISED_BEFORE)
    def test_passthrough_still_raises_for_a_script_scheme(self, attr, url):
        with pytest.raises(ValueError, match="refuses"):
            self._input(attr, url)

    @pytest.mark.parametrize("url", ["tel:+15551234567", "mailto:a@example.com", "/rel/", "#x"])
    def test_the_allowed_schemes_render_as_written(self, url):
        assert 'href="%s"' % url in self._button(url)
        assert 'href="%s"' % url in self._input("href", url)

    @pytest.mark.parametrize("url", list(MARKED_SAFE_INERT.values()))
    def test_a_marked_safe_literal_is_escaped(self, url):
        for html in (self._button(mark_safe(url)), self._input("href", mark_safe(url))):
            assert url_sweep.findings(html) == [], html
            assert url not in html, html


# --------------------------------------------------------------------------- #
# The sink SET is pinned, so a new sink is a visible decision
#
# The sweep finds a sink only when the value reaches an attribute under the sweep's
# TYPICAL arguments (it did not reach ``theme_pagination``'s pattern, ``theme_sidebar_nav``
# or ``theme_empty_state_page``'s link), so the set is also derived from the code: every
# URL-bearing attribute of every theming template that is not a fixed string, and the tag
# functions that call the checks.

TEMPLATES = ROOT / "python/djust/theming/templates/djust_theming"
#: ``name="value"`` / ``name='value'`` inside a template (double quotes may hold single ones).
_TEMPLATE_ATTR = re.compile(r"""(?<![\w:.-])([A-Za-z_][\w:.-]*)\s*=\s*(?:"([^"]*)"|'([^']*)')""")
_WHOLE_VARIABLE = re.compile(r"\{\{\s*([\w.]+)\s*\}\}")

#: (template, attribute, variable) for every URL-bearing attribute of a COMPONENT or PAGE
#: template whose value is exactly ``{{ variable }}``: the values a tag's caller supplies.
#: URL-bearing is the sweep scanner's ``is_url_attr`` (``href``, ``src``, ``action``,
#: ``formaction``, ``poster``, ``srcset``, ``ping``, ``data`` ... and ``data-*`` / ``dj-*``
#: whose name says url / navigate / ...), so ``dj-navigate`` is in the set.
TEMPLATE_SINKS = {
    ("components/avatar.html", "src", "src"),
    ("components/breadcrumb.html", "dj-navigate", "item.url"),
    ("components/breadcrumb.html", "href", "item.url"),
    ("components/button.html", "href", "href"),
    ("components/nav.html", "href", "item.url"),
    ("components/nav_group.html", "dj-navigate", "item.url"),
    ("components/nav_group.html", "href", "item.url"),
    ("components/nav_item.html", "dj-navigate", "url"),
    ("components/nav_item.html", "href", "url"),
    ("components/pagination.html", "href", "first_url"),
    ("components/pagination.html", "href", "last_url"),
    ("components/pagination.html", "href", "next_url"),
    ("components/pagination.html", "href", "page.url"),
    ("components/pagination.html", "href", "prev_url"),
    ("components/sidebar_nav.html", "href", "item.url"),
    ("pages/403.html", "href", "back_url"),
    ("pages/404.html", "href", "home_url"),
    ("pages/500.html", "href", "home_url"),
    ("pages/500.html", "href", "retry_url"),
    ("pages/empty_state.html", "href", "cta_url"),
    ("pages/login.html", "action", "action"),
    ("pages/login.html", "href", "forgot_password_url"),
    ("pages/login.html", "href", "register_url"),
    ("pages/password_confirm.html", "action", "action"),
    ("pages/password_reset.html", "action", "action"),
    ("pages/password_reset.html", "href", "login_url"),
    ("pages/register.html", "action", "action"),
    ("pages/register.html", "href", "login_url"),
    ("pages/register.html", "href", "terms_url"),
    *(
        ("themes/%s/components/button.html" % theme, "href", "href")
        for theme in ("fluent", "ios", "material", "neo_brutalist", "playful")
    ),
}

#: Where a tag's caller supplies the value. Everywhere else (the layout, ``theme_head``, the
#: catalogue and gallery pages) a URL is computed by the framework or its own view.
CALLER_SUPPLIED_DIRECTORIES = ("components/", "pages/", "themes/", "forms/", "form_templates/")
#: Variables that may appear in a URL attribute outside those directories: set by the
#: framework's own views / context processor, never by a tag argument.
VIEW_COMPUTED_VARIABLES = {
    "asset_version",
    "client_script",
    "client_script_url",
    "components_gallery_url",
    "docs_component_url",
    "docs_guide_url",
    "docs_hooks_url",
    "left_preset",
    "right_preset",
}
#: Template tags that may appear there: they print a reversed or static URL.
SERVER_TEMPLATE_TAGS = {"static", "url", "theme_asset_version"}

#: The helpers that apply the allowlist; a tag function that mentions one is guarded.
_GUARDS = {"_neutralise_url", "_neutralise_item_urls", "_check_url", "_map_item_attribute"}


def _url_attributes() -> set[tuple[str, str, str]]:
    """``(template, attribute, value)`` of every URL-bearing attribute holding a ``{{`` or ``{%``.

    Quoted values only (an unquoted URL attribute is not a form these templates use); the
    attribute name decides, never the value, so a composite ``href="{{ a }}{{ b }}"`` or a
    ``{% if %}`` inside the value is found like any other.
    """
    found = set()
    for path in sorted(TEMPLATES.rglob("*.html")):
        rel = str(path.relative_to(TEMPLATES))
        for match in _TEMPLATE_ATTR.finditer(path.read_text(encoding="utf-8")):
            name = match.group(1).lower()
            value = match.group(2) if match.group(2) is not None else match.group(3)
            if url_sweep.is_url_attr(name) and ("{{" in value or "{%" in value):
                found.add((rel, name, value))
    return found


def _tags_that_call_a_guard() -> set[str]:
    tags = set()
    for path in sorted((ROOT / "python/djust/theming/templatetags").glob("theme_*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.FunctionDef) or not node.name.startswith("theme_"):
                continue
            names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
            if names & _GUARDS:
                tags.add(node.name)
    return tags


class TestTheSinkSetIsPinned:
    def test_every_caller_supplied_url_attribute_is_a_known_whole_variable(self):
        """A new ``dj-navigate`` / ``ping`` / ``poster``, or a composite value such as
        ``href="{{ a }}{{ b }}"`` or ``href="{{ base }}?x={{ y }}"``, is not in the pin."""
        found = set()
        for rel, name, value in _url_attributes():
            if not rel.startswith(CALLER_SUPPLIED_DIRECTORIES):
                continue
            whole = _WHOLE_VARIABLE.fullmatch(value)
            found.add((rel, name, whole.group(1) if whole else "<composite> " + value))
        assert found == TEMPLATE_SINKS, sorted(found ^ TEMPLATE_SINKS)

    def test_a_url_attribute_elsewhere_prints_only_what_the_framework_computes(self):
        """The layout, ``theme_head``, catalogue and gallery: static / reversed / view URLs."""
        unexplained = []
        for rel, name, value in sorted(_url_attributes()):
            if rel.startswith(CALLER_SUPPLIED_DIRECTORIES):
                continue
            variables = set(re.findall(r"\{\{\s*([\w.]+)", value))
            tags = set(re.findall(r"\{%\s*(\w+)", value))
            if variables - VIEW_COMPUTED_VARIABLES or tags - SERVER_TEMPLATE_TAGS:
                unexplained.append((rel, name, value))
        assert unexplained == [], unexplained

    def test_the_scan_sees_what_it_claims_to(self):
        """Presence before absence: the scan finds ``dj-navigate`` and the composite values."""
        found = _url_attributes()
        assert ("components/nav_item.html", "dj-navigate", "{{ url }}") in found
        assert any(v.count("{") > 2 and "?v=" in v for _r, _n, v in found), "no composite value"
        assert any(r.startswith("catalogue/") for r, _n, _v in found)
        assert len(found) > len(TEMPLATE_SINKS)

    def test_the_tags_that_guard_a_url_are_exactly_the_tags_this_file_drives(self):
        driven = {tag.split(":", 1)[1] for tag, _param in SINKS}
        # ``theme_button`` takes a developer literal (TestLiteralAndPassthroughSinks)
        assert _tags_that_call_a_guard() == driven | {"theme_button"}

    def test_every_template_variable_is_a_parameter_of_a_driven_tag(self):
        """The templates' variables are the tags' parameters (or the item key of a list one)."""
        parameters = {param for _tag, param in SINKS}
        # ``items``/``sections`` carry ``item.url``; ``pagination`` derives its urls from the pattern
        derived = {"item.url", "page.url", "first_url", "last_url", "next_url", "prev_url"}
        template_variables = {variable for _t, _a, variable in TEMPLATE_SINKS}
        unexplained = template_variables - parameters - derived - {"href"}
        assert unexplained == set(), sorted(unexplained)
