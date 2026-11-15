"""The inline event handlers djust wrote, as markers a static module handles (#4146).

A ``Content-Security-Policy`` whose ``script-src`` lacks ``'unsafe-inline'`` refuses every
``on*=`` attribute, and no nonce can allow one. #4139's sweep
(``python/djust/tests/test_csp_constant_styles_4139.py``) found four sites that djust wrote:

* ``NavBar``'s menu toggle, Tailwind (``classList.toggle('hidden')`` on ``<id>-mobile``) and plain
  (``classList.toggle('show')`` on ``<id>-menu``): now ``data-navbar-toggle`` /
  ``data-navbar-toggle-class``, handled by ``djust_components/navbar-toggle.js``, which the
  component loads itself (:class:`TestNavBar`);
* the theme alert's and toast's close button (``this.parentElement.remove()``): now
  ``data-theme-dismiss``, handled by ``djust_theming/js/components.js``, which ``{% theme_head %}``
  loads (:class:`TestDismiss`), with ``djust_theming.W005`` for a project that turns it off
  (:class:`TestW005`).

A caller's own ``onclick=`` on ``{% theme_button %}`` and the ``Button`` component stays (it is the
caller's code); :class:`TestCallerOnclick` pins that it is one attribute however it is written.
Both modes of ``csp_strict_styles`` and, for the theming tags, both engines. The listeners are
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
    before = config.get("csp_strict_styles", False)
    config.set("csp_strict_styles", request.param == "strict")
    try:
        yield request.param
    finally:
        config.set("csp_strict_styles", before)


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
# NavBar
# ---------------------------------------------------------------------------


class TestNavBar:
    @staticmethod
    def _navbar(framework: str, nav_id: str = "nav1") -> str:
        from djust.components.ui.navbar_simple import NavBar

        items = [{"label": "Home", "url": "/"}]
        return _with_framework(framework, lambda: NavBar(items=items, id=nav_id).render())

    @pytest.mark.parametrize(
        "framework,menu,cls",
        [("tailwind", "nav1-mobile", "hidden"), ("plain", "nav1-menu", "show")],
    )
    def test_the_toggle_is_a_marker_for_the_same_element_and_class(
        self, mode, framework, menu, cls
    ):
        html = self._navbar(framework)
        assert _handlers(html) == []
        button = re.search(r"<button[^>]*data-navbar-toggle=[^>]*>", html).group(0)
        assert 'data-navbar-toggle="%s"' % menu in button
        assert 'data-navbar-toggle-class="%s"' % cls in button
        # The element the old handler toggled: same id, and it starts in the same state.
        target = re.search(r'<div class="([^"]*)" id="%s">' % menu, html)
        assert target, html
        assert (cls in target.group(1).split()) == (cls == "hidden")

    @pytest.mark.parametrize("framework", ["tailwind", "plain"])
    def test_it_loads_its_module_once_inside_the_nav(self, mode, framework):
        from django.templatetags.static import static

        html = self._navbar(framework)
        tag = '<script src="%s" defer></script>' % static("djust_components/navbar-toggle.js")
        assert html.count("<script") == 1
        # Inside the <nav>, so the component keeps one root element.
        assert html.rstrip().endswith(tag + "\n</nav>")
        assert (PKG / "components" / "static" / "djust_components" / "navbar-toggle.js").is_file()

    def test_the_bootstrap_renderer_needs_neither(self, mode):
        html = self._navbar("bootstrap5")
        assert _handlers(html) == []
        assert "data-navbar-toggle" not in html and "<script" not in html
        assert 'data-bs-toggle="collapse"' in html

    def test_the_module_follows_static_url(self, mode):
        with override_settings(STATIC_URL="/assets/"):
            html = self._navbar("plain")
        assert '<script src="/assets/djust_components/navbar-toggle.js" defer></script>' in html


# ---------------------------------------------------------------------------
# The theme alert's and toast's close button
# ---------------------------------------------------------------------------


_DISMISS = re.compile(r"<button\b[^>]*\bdata-theme-dismiss\b[^>]*>")


class TestDismiss:
    @pytest.mark.parametrize(
        "source,root_class",
        [
            ('{% load theme_components %}{% theme_alert "Hi" dismissible=True %}', "alert"),
            ('{% load theme_components %}{% theme_toast "Saved" %}', "toast"),
        ],
    )
    def test_the_close_button_is_a_marker_on_a_child_of_the_root(
        self, mode, engine, source, root_class
    ):
        html = _render(engine, source)
        assert _handlers(html) == []
        button = _DISMISS.search(html)
        assert button, html
        assert 'aria-label="Dismiss"' in button.group(0)
        # ``this.parentElement.remove()`` removed the root: the button is its direct child.
        root = re.match(r'\s*<div class="%s\b' % root_class, html)
        assert root, html
        depth = 0
        for tag in re.finditer(r"<(/?)(div|button)\b", html[: button.start()]):
            depth += -1 if tag.group(1) else 1
        assert depth == 1, html

    def test_an_alert_that_is_not_dismissible_has_no_button(self, mode, engine):
        html = _render(engine, '{% load theme_components %}{% theme_alert "Hi" %}')
        assert "data-theme-dismiss" not in html and _handlers(html) == []

    def test_components_js_handles_the_marker_and_theme_head_loads_it(self, engine):
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
    def _check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, files: dict[str, str]) -> list:
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
        with override_settings(TEMPLATES=templates):
            return check_dismiss_needs_components_js(None)

    BASE_NO_JS = "{% load theme_tags %}<head>{% theme_head include_js=False %}</head>"

    @pytest.mark.parametrize(
        "page",
        [
            '{% theme_alert "x" dismissible=True %}',
            "{% theme_alert msg dismissible=can_close %}",
            '{% theme_toast "Saved" %}',
        ],
    )
    def test_reports_a_dismiss_button_on_a_page_without_components_js(
        self, tmp_path, monkeypatch, page
    ):
        found = self._check(
            tmp_path, monkeypatch, {"base.html": self.BASE_NO_JS, "page.html": page}
        )
        assert [m.id for m in found] == ["djust_theming.W005"]
        assert "components.js" in found[0].hint

    @pytest.mark.parametrize(
        "files",
        [
            # theme_head loads it (the default).
            {"base.html": "{% theme_head %}", "page.html": '{% theme_toast "x" %}'},
            # No dismiss button.
            {"base.html": BASE_NO_JS, "page.html": '{% theme_alert "x" dismissible=False %}'},
            {"base.html": BASE_NO_JS, "page.html": '{% theme_alert "x" %}'},
            # The project loads the script itself.
            {
                "base.html": BASE_NO_JS
                + "<script src=\"{% static 'djust_theming/js/components.js' %}\" defer></script>",
                "page.html": '{% theme_toast "x" %}',
            },
            # Only in a comment.
            {
                "base.html": "{# {% theme_head include_js=False %} #}",
                "page.html": "{% theme_toast 'x' %}",
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
