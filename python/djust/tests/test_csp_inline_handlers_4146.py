"""The inline event handlers djust writes, and their strict-CSP form (#4146).

A ``Content-Security-Policy`` whose ``script-src`` lacks ``'unsafe-inline'`` refuses every
``on*=`` attribute, and no nonce can allow one. #4139's sweep
(``python/djust/tests/test_csp_constant_styles_4139.py``) found four sites djust writes. By
DEFAULT each stays the inline ``onclick`` it has always been, byte for byte, and needs no script.
Under ``LIVEVIEW_CONFIG["csp_strict"]`` (one switch for styles and handlers; ``csp_strict_styles``
is its earlier name, :class:`TestTheSwitch`) each is a marker a static module handles from a
listener on ``document``:

* ``NavBar``'s menu toggle, Tailwind (``classList.toggle('hidden')`` on ``<id>-mobile``) and plain
  (``classList.toggle('show')`` on ``<id>-menu``): ``data-navbar-toggle`` /
  ``data-navbar-toggle-class``, handled by ``djust_components/navbar-toggle.js``, which the navbar
  renders a ``<script>`` for (with a nonce) and a strict LiveView page loads with the client, so a
  navbar a later patch inserts works too (:class:`TestNavBar`, :class:`TestTheStrictPage`);
* the theme alert's and toast's close button (``this.parentElement.remove()``):
  ``data-theme-dismiss``, handled by ``djust_theming/js/components.js``, which
  ``{% theme_head %}`` loads (:class:`TestDismiss`), with ``djust_theming.W005`` for a strict
  project that turns it off (:class:`TestW005`).

A caller's own ``onclick=`` on ``{% theme_button %}`` and the ``Button`` component stays in both
modes (it is the caller's code); :class:`TestCallerOnclick` pins that it is one attribute however
it is written. For the theming tags, both engines. The listeners are
``tests/js/csp_inline_handlers_4146.test.js``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterator

import pytest
from django.template import engines
from django.test import RequestFactory, override_settings
from django.utils.safestring import mark_safe

from djust.config import config

ROOT = Path(__file__).resolve().parents[3]
PKG = ROOT / "python" / "djust"


def _handlers(markup: str) -> list[tuple[str, str]]:
    """The sweep's own reading: each ``on*`` attribute and ``javascript:`` URL in ``markup``."""
    from scripts.lib.template_library_parity import csp_sweep

    return [f for f in csp_sweep.findings(markup) if f[0] in ("handler", "js-url")]


@pytest.fixture(params=["default", "strict"])
def mode(request: pytest.FixtureRequest) -> Iterator[str]:
    before = config.get("csp_strict", False)
    config.set("csp_strict", request.param == "strict")
    try:
        yield request.param
    finally:
        config.set("csp_strict", before)


def _with_framework(name: str, render: Any) -> str:
    before = config.get("css_framework", "bootstrap5")
    config.set("css_framework", name)
    try:
        return str(render())
    finally:
        config.set("css_framework", before)


@pytest.fixture(scope="module")
def rust_backend() -> Any:
    from djust.components.rust_handlers import register_with_rust_engine
    from djust.template.backend import DjustTemplateBackend

    register_with_rust_engine()
    return DjustTemplateBackend({"NAME": "csp4146", "DIRS": [], "APP_DIRS": True, "OPTIONS": {}})


@pytest.fixture(params=["django", "djust"])
def engine(request: pytest.FixtureRequest, rust_backend: Any) -> Any:
    return engines["django"] if request.param == "django" else rust_backend


def _render(engine: Any, source: str, **context: Any) -> str:
    req = RequestFactory().get("/")
    return str(engine.from_string(source).render({"request": req, **context}, req))


# ---------------------------------------------------------------------------
# The switch
# ---------------------------------------------------------------------------


class TestTheSwitch:
    @pytest.mark.parametrize(
        "values,strict",
        [
            ({}, False),
            ({"csp_strict": True}, True),
            ({"csp_strict_styles": True}, True),
            ({"csp_strict": True, "csp_strict_styles": True}, True),
        ],
    )
    def test_one_helper_answers_for_both_names(self, values, strict):
        from djust.components.utils import csp_strict, strict_styles

        before = {k: config.get(k, False) for k in ("csp_strict", "csp_strict_styles")}
        try:
            for key in before:
                config.set(key, values.get(key, False))
            assert csp_strict() is strict
            assert strict_styles is csp_strict
        finally:
            for key, value in before.items():
                config.set(key, value)

    @pytest.mark.parametrize(
        "live,error",
        [
            ({"csp_strict": True, "csp_strict_styles": False}, True),
            ({"csp_strict": False, "csp_strict_styles": True}, True),
            ({"csp_strict": True, "csp_strict_styles": True}, False),
            ({"csp_strict": True}, False),
            ({"csp_strict_styles": True}, False),
            ({}, False),
        ],
    )
    def test_c039_reports_the_two_names_disagreeing(self, settings, live, error):
        from djust.checks.configuration import _check_csp_strict_alias

        settings.LIVEVIEW_CONFIG = live
        errors: list = []
        _check_csp_strict_alias(errors)
        assert [e.id for e in errors] == (["djust.C039"] if error else [])

    @pytest.mark.parametrize(
        "live,djust_cfg,error",
        [
            ({"csp_strict": False}, {"csp_strict_styles": True}, True),
            ({"csp_strict_styles": True}, {"csp_strict": False}, True),
            ({}, {"csp_strict": True, "csp_strict_styles": False}, True),
            ({"csp_strict": True}, {"csp_strict_styles": True}, False),
            # LIVEVIEW_CONFIG wins a key, as the loader reads it: no disagreement left.
            ({"csp_strict": True, "csp_strict_styles": True}, {"csp_strict_styles": False}, False),
        ],
    )
    def test_c039_reads_both_sources_as_the_loader_does(self, settings, live, djust_cfg, error):
        from djust.checks.configuration import _check_csp_strict_alias

        settings.LIVEVIEW_CONFIG = live
        settings.DJUST_CONFIG = djust_cfg
        errors: list = []
        _check_csp_strict_alias(errors)
        assert [e.id for e in errors] == (["djust.C039"] if error else [])
        if error:
            assert "DJUST_CONFIG" in errors[0].msg


# ---------------------------------------------------------------------------
# NavBar
# ---------------------------------------------------------------------------

#: What main writes for the two toggles (the inline handler of ``navbar_simple.py`` before
#: #4146), for ``id="nav1"`` and the light variant: default mode must write exactly this.
_DEFAULT_TOGGLES = {
    "tailwind": (
        '                <button type="button" class="text-gray-700 hover:bg-gray-100 '
        'hover:text-gray-900 p-2 rounded-md" onclick="document.getElementById('
        "'nav1-mobile').classList.toggle('hidden')\">"
    ),
    "plain": (
        '        <button class="navbar-toggler" onclick="document.getElementById('
        "'nav1-menu').classList.toggle('show')\">☰</button>"
    ),
}
_STRICT_TOGGLES = {
    "tailwind": (
        '                <button type="button" class="text-gray-700 hover:bg-gray-100 '
        'hover:text-gray-900 p-2 rounded-md" data-navbar-toggle="nav1-mobile" '
        'data-navbar-toggle-class="hidden">'
    ),
    "plain": (
        '        <button class="navbar-toggler" data-navbar-toggle="nav1-menu" '
        'data-navbar-toggle-class="show">☰</button>'
    ),
}


class TestNavBar:
    @staticmethod
    def _navbar(framework: str, strict: bool, **kwargs: Any) -> str:
        from djust.components.ui.navbar_simple import NavBar

        before = config.get("csp_strict", False)
        config.set("csp_strict", strict)
        try:
            items = [{"label": "Home", "url": "/"}]
            return _with_framework(
                framework, lambda: NavBar(items=items, id="nav1", **kwargs).render()
            )
        finally:
            config.set("csp_strict", before)

    @pytest.mark.parametrize("framework", ["tailwind", "plain"])
    def test_default_mode_writes_the_inline_handler_and_nothing_else(self, framework):
        html = self._navbar(framework, strict=False)
        assert _DEFAULT_TOGGLES[framework] + "\n" in html
        assert _handlers(html) == [("handler", "<button onclick>")]
        assert "data-navbar-toggle" not in html and "<script" not in html

    @pytest.mark.parametrize("framework", ["tailwind", "plain"])
    def test_strict_mode_differs_only_by_the_marker_and_its_script(self, framework):
        default = self._navbar(framework, strict=False)
        strict = self._navbar(framework, strict=True)
        assert _STRICT_TOGGLES[framework] + "\n" in strict
        assert _handlers(strict) == []
        script = re.search(r"\n<script src=[^\n]*></script>(?=\n</nav>$)", strict)
        assert script, strict
        # The root carries the marker the listener requires, on the opening <nav> only.
        assert strict.count(" data-djust-navbar") == 1
        assert strict[: strict.index(">") + 1].endswith(" data-djust-navbar>")
        back = (
            strict.replace(script.group(0), "")
            .replace(_STRICT_TOGGLES[framework], _DEFAULT_TOGGLES[framework])
            .replace(" data-djust-navbar>", ">", 1)
        )
        assert back == default

    @pytest.mark.parametrize(
        "framework,menu,cls",
        [("tailwind", "nav1-mobile", "hidden"), ("plain", "nav1-menu", "show")],
    )
    def test_the_marker_names_the_element_and_class_the_handler_toggled(self, framework, menu, cls):
        html = self._navbar(framework, strict=True)
        target = re.search(r'<div class="([^"]*)" id="%s">' % menu, html)
        assert target, html
        assert (cls in target.group(1).split()) == (cls == "hidden")

    @pytest.mark.parametrize("framework", ["tailwind", "plain"])
    def test_strict_mode_loads_its_module_inside_the_nav_with_a_nonce(self, framework):
        from django.templatetags.static import static

        url = static("djust_components/navbar-toggle.js")
        html = self._navbar(framework, strict=True)
        assert html.count("<script") == 1
        assert html.endswith('\n<script src="%s" defer></script>\n</nav>' % url)
        html = self._navbar(framework, strict=True, nonce='n"1')
        assert html.endswith('\n<script src="%s" defer nonce="n&quot;1"></script>\n</nav>' % url)
        assert (PKG / "components" / "static" / "djust_components" / "navbar-toggle.js").is_file()

    @pytest.mark.parametrize("strict", [False, True])
    def test_the_bootstrap_renderer_needs_neither(self, strict):
        html = self._navbar("bootstrap5", strict=strict)
        assert _handlers(html) == []
        assert "data-navbar-toggle" not in html and "<script" not in html

    def test_an_uncollected_module_omits_the_tag_and_does_not_raise(self, monkeypatch, caplog):
        """With a manifest storage that has no entry for the file ``static()`` raises
        ``ValueError``; the render must not, and a strict LiveView page loads the file anyway."""
        import django.templatetags.static as static_module

        from djust.components.ui import navbar_simple

        def missing(path: str) -> str:
            raise ValueError("Missing staticfiles manifest entry for '%s'" % path)

        monkeypatch.setattr(static_module, "static", missing)
        monkeypatch.setattr(navbar_simple, "_STATIC_WARNED", [])
        html = self._navbar("plain", strict=True)
        assert "<script" not in html and "data-navbar-toggle=" in html
        assert html.endswith("</div>\n</nav>")
        self._navbar("tailwind", strict=True)
        warned = [r for r in caplog.records if "navbar-toggle.js" in r.getMessage()]
        assert len(warned) == 1

    def test_the_module_follows_static_url(self):
        with override_settings(STATIC_URL="/assets/"):
            html = self._navbar("plain", strict=True)
        assert '<script src="/assets/djust_components/navbar-toggle.js" defer></script>' in html


class TestTheStrictPage:
    """A strict LiveView page loads ``navbar-toggle.js`` with the client, so a navbar that first
    arrives in a patch (whose own ``<script>`` never runs) is covered; a default page does not."""

    @staticmethod
    def _page(rf: Any, nonce: str = "") -> str:
        from django.contrib.sessions.backends.signed_cookies import SessionStore

        from djust import LiveView

        class V(LiveView):
            exposure_policy = "explicit"

        V.template = "<html><head><title>t</title></head><body><div dj-root>x</div></body></html>"
        request = rf.get("/")
        request.session = SessionStore()
        if nonce:
            request.csp_nonce = nonce
        response = V.as_view()(request)
        return response.content.decode()

    @pytest.mark.django_db
    def test_the_page_loads_the_listener_only_in_strict_mode(self, mode, rf):
        from django.templatetags.static import static

        html = self._page(rf, nonce="pg4146")
        tag = re.findall(r"<script [^>]*navbar-toggle\.js[^>]*>", html)
        if mode == "default":
            assert tag == []
            return
        assert len(tag) == 1, html
        assert 'src="%s"' % static("djust_components/navbar-toggle.js") in tag[0]
        assert 'nonce="pg4146"' in tag[0] and " defer" in tag[0]
        # After the client, which it does not need but follows like the other modules.
        assert html.index(tag[0]) > html.index("djust/client")


# ---------------------------------------------------------------------------
# The theme alert's and toast's close button
# ---------------------------------------------------------------------------


_DISMISS = re.compile(r"<button\b[^>]*\bdata-theme-dismiss\b[^>]*>")
_ALERT = '{% load theme_components %}{% theme_alert "Hi" dismissible=True %}'
_TOAST = '{% load theme_components %}{% theme_toast "Saved" %}'
#: What main writes for each close button: default mode must write exactly this.
_DEFAULT_BUTTONS = {
    _ALERT: '<button class="alert-dismiss" onclick="this.parentElement.remove()" aria-label="Dismiss">',
    _TOAST: '<button class="toast-close" onclick="this.parentElement.remove()" aria-label="Dismiss">',
}


class TestDismiss:
    @pytest.mark.parametrize("source", [_ALERT, _TOAST])
    def test_default_mode_writes_the_inline_handler(self, engine, source):
        before = config.get("csp_strict", False)
        config.set("csp_strict", False)
        try:
            html = _render(engine, source)
        finally:
            config.set("csp_strict", before)
        assert _DEFAULT_BUTTONS[source] in html, html
        assert "data-theme-dismiss" not in html
        assert _handlers(html) == [("handler", "<button onclick>")]

    @pytest.mark.parametrize("source", [_ALERT, _TOAST])
    def test_strict_mode_differs_only_by_the_marker(self, engine, source):
        before = config.get("csp_strict", False)
        try:
            config.set("csp_strict", False)
            default = _render(engine, source)
            config.set("csp_strict", True)
            strict = _render(engine, source)
        finally:
            config.set("csp_strict", before)
        assert _handlers(strict) == []
        assert strict.count(" data-theme-dismiss-root") == 1
        assert (
            strict.replace(
                " data-theme-dismiss ", ' onclick="this.parentElement.remove()" '
            ).replace('" data-theme-dismiss-root\n', '"\n', 1)
            == default
        )

    @pytest.mark.parametrize("source,root_class", [(_ALERT, "alert"), (_TOAST, "toast")])
    def test_the_strict_marker_is_on_a_child_of_the_root(self, engine, source, root_class):
        before = config.get("csp_strict", False)
        config.set("csp_strict", True)
        try:
            html = _render(engine, source)
        finally:
            config.set("csp_strict", before)
        button = _DISMISS.search(html)
        assert button, html
        assert 'aria-label="Dismiss"' in button.group(0)
        # ``this.parentElement.remove()`` removed the root: the button is its direct child, and
        # that root carries the marker the listener requires.
        assert re.match(
            r'\s*<div class="%s\b[^"]*" data-theme-dismiss-root\n' % root_class, html
        ), html
        depth = 0
        for tag in re.finditer(r"<(/?)(div|button)\b", html[: button.start()]):
            depth += -1 if tag.group(1) else 1
        assert depth == 1, html

    def test_an_alert_that_is_not_dismissible_has_no_button(self, mode, engine):
        html = _render(engine, '{% load theme_components %}{% theme_alert "Hi" %}')
        assert "data-theme-dismiss" not in html and _handlers(html) == []

    @pytest.mark.parametrize("nonce", ["", "hd4146"])
    def test_theme_head_scripts_carry_the_nonce(self, engine, nonce):
        """The strict close button needs ``components.js``: under a nonce-only policy its tag must
        carry the nonce. Without one the tags are what they were."""
        req = RequestFactory().get("/")
        if nonce:
            req.csp_nonce = nonce
        html = str(
            engine.from_string("{% load theme_tags %}{% theme_head %}").render(
                {"request": req}, req
            )
        )
        tags = re.findall(
            r'<script src="[^"]*djust_theming/js/(?:theme|components)\.js[^"]*"[^>]*>', html
        )
        assert len(tags) == 2, html
        for tag in tags:
            assert tag.endswith(' defer nonce="%s">' % nonce if nonce else '" defer>'), tag

    def test_components_js_handles_the_marker_and_theme_head_loads_it(self):
        js = (PKG / "theming" / "static" / "djust_theming" / "js" / "components.js").read_text()
        assert "closest('[data-theme-dismiss]')" in js
        assert "document.addEventListener('click', handleDismissClick)" in js
        head = (PKG / "theming" / "templates" / "djust_theming" / "theme_head.html").read_text()
        assert "djust_theming/js/components.js" in head


# ---------------------------------------------------------------------------
# djust_theming.W005
# ---------------------------------------------------------------------------


class TestW005:
    @staticmethod
    def _check(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, files: dict[str, str], strict: bool = True
    ) -> list:
        import djust.checks as djust_checks
        from djust.theming.checks import check_dismiss_needs_components_js

        for name, body in files.items():
            (tmp_path / name).write_text(body, encoding="utf-8")
        monkeypatch.setattr(djust_checks, "_get_project_app_dirs", lambda: [])
        templates = [
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "DIRS": [str(tmp_path)],
                "APP_DIRS": False,
            }
        ]
        before = config.get("csp_strict", False)
        config.set("csp_strict", strict)
        try:
            with override_settings(TEMPLATES=templates):
                return check_dismiss_needs_components_js(None)
        finally:
            config.set("csp_strict", before)

    BASE_NO_JS = "{% load theme_tags %}<head>{% theme_head include_js=False %}</head>"
    EXTENDS = '{% extends "base.html" %}'

    @pytest.mark.parametrize(
        "button",
        [
            '{% theme_alert "x" dismissible=True %}',
            "{% theme_alert msg dismissible=can_close %}",
            '{% theme_toast "Saved" %}',
        ],
    )
    def test_reports_a_dismiss_button_on_a_page_that_extends_the_head(
        self, tmp_path, monkeypatch, button
    ):
        files = {"base.html": self.BASE_NO_JS, "page.html": self.EXTENDS + button}
        found = self._check(tmp_path, monkeypatch, files)
        assert [m.id for m in found] == ["djust_theming.W005"]
        assert "components.js" in found[0].hint
        assert "base.html renders" in found[0].msg and "page.html, on the same page" in found[0].msg
        # Default mode: the button is the inline handler, which needs no script.
        assert self._check(tmp_path, monkeypatch, files, strict=False) == []

    @pytest.mark.parametrize(
        "files",
        [
            # The page includes the head.
            {
                "head.html": BASE_NO_JS,
                "page.html": '{% include "head.html" %}{% theme_toast "x" %}',
            },
            # A partial with the button, included by a page that extends the head.
            {
                "base.html": BASE_NO_JS,
                "toasts.html": '{% theme_toast "x" %}',
                "page.html": EXTENDS + '{% include "toasts.html" %}',
            },
            # Through two levels of extends.
            {
                "base.html": BASE_NO_JS,
                "mid.html": EXTENDS,
                "page.html": '{% extends "mid.html" %}{% theme_toast "x" %}',
            },
        ],
    )
    def test_reports_it_through_include_and_extends(self, tmp_path, monkeypatch, files):
        assert [m.id for m in self._check(tmp_path, monkeypatch, files)] == ["djust_theming.W005"]

    def test_both_in_one_template_says_so(self, tmp_path, monkeypatch):
        files = {"page.html": self.BASE_NO_JS + '{% theme_toast "x" %}'}
        (found,) = self._check(tmp_path, monkeypatch, files)
        assert "page.html renders {% theme_head include_js=False %} and a dismissible" in found.msg
        assert "same page" not in found.msg

    @pytest.mark.parametrize(
        "files",
        [
            # theme_head loads it (the default).
            {"base.html": "{% theme_head %}", "page.html": EXTENDS + '{% theme_toast "x" %}'},
            # No dismiss button.
            {
                "base.html": BASE_NO_JS,
                "page.html": EXTENDS + '{% theme_alert "x" dismissible=False %}',
            },
            {"base.html": BASE_NO_JS, "page.html": EXTENDS + '{% theme_alert "x" %}'},
            # The page loads the script itself.
            {
                "base.html": BASE_NO_JS
                + "<script src=\"{% static 'djust_theming/js/components.js' %}\" defer></script>",
                "page.html": EXTENDS + '{% theme_toast "x" %}',
            },
            # Only in a comment.
            {
                "base.html": "{# {% theme_head include_js=False %} #}",
                "page.html": EXTENDS + "{% theme_toast 'x' %}",
            },
            # Two templates that never share a page.
            {"admin_base.html": BASE_NO_JS, "page.html": '{% theme_toast "x" %}'},
            # Two pages extending one base: the head is on one page, the button on the other.
            {
                "base.html": "<head>{% block head %}{% endblock %}</head>",
                "a.html": EXTENDS + "{% block head %}" + BASE_NO_JS + "{% endblock %}",
                "b.html": EXTENDS + '{% theme_toast "x" %}',
            },
        ],
    )
    def test_is_quiet_otherwise(self, tmp_path, monkeypatch, files):
        assert self._check(tmp_path, monkeypatch, files) == []


# ---------------------------------------------------------------------------
# A caller's own onclick= (kept; one attribute however it is written)
# ---------------------------------------------------------------------------

_PACKS = ["", "fluent/", "material/", "ios/", "playful/", "neo_brutalist/"]
#: A caller's handler with both quotes, markup and an entity-looking text: decoded by the browser,
#: the attribute must be exactly this.
_HANDLER = "go(\"a\") && x<1 || f('b') /* &amp; </button><script>y()</script> */"


def _onclick_values(html: str) -> list[str]:
    """Each ``onclick`` attribute's value as a browser reads it (``attributes`` entity-decodes)."""
    from scripts.lib.template_library_parity import url_sweep

    return [a.value for a in url_sweep.attributes(html) if a.name == "onclick"]


def _attribute_names(html: str, element: str) -> list[str]:
    from scripts.lib.template_library_parity import url_sweep

    return [a.name for a in url_sweep.attributes(html) if a.element == element]


class TestCallerOnclick:
    @pytest.mark.parametrize("pack", _PACKS)
    @pytest.mark.parametrize("marked_safe", [False, True], ids=["plain", "marked_safe"])
    def test_theme_button_writes_one_attribute_with_the_callers_text(
        self, engine, monkeypatch, pack, marked_safe
    ):
        from djust.theming.templatetags import theme_components

        name = "djust_theming/%scomponents/button.html" % ("themes/" + pack if pack else "")
        monkeypatch.setattr(
            theme_components, "resolve_component_template", lambda _r, _c: engine.get_template(name)
        )
        value = mark_safe(_HANDLER) if marked_safe else _HANDLER
        html = _render(
            engine, '{% load theme_components %}{% theme_button "Go" onclick=v %}', v=value
        )
        assert _onclick_values(html) == [_HANDLER], html
        assert _attribute_names(html, "button").count("onclick") == 1
        assert "<script>" not in html

    def test_a_template_literal_stays_one_attribute(self, engine):
        """A string literal in a template is marked safe: ``onclick='go("a")'`` closed the
        attribute at its first ``"`` on both engines before #4146."""
        html = _render(
            engine, """{% load theme_components %}{% theme_button "Go" onclick='go("a")' %}"""
        )
        assert _onclick_values(html) == ['go("a")'], html
        assert set(_attribute_names(html, "button")) == {"class", "onclick"}

    def test_the_button_component_writes_one_attribute(self):
        from djust.components.components.button import Button

        for value in (_HANDLER, mark_safe(_HANDLER)):
            html = str(Button(label="Go", onclick=value).render())
            assert _onclick_values(html) == [_HANDLER], html
            assert "<script>" not in html
