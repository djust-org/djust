"""Constant inline styles and inline scripts a strict CSP refuses (#4139), and the sweep that pins it.

A ``Content-Security-Policy`` whose ``style-src`` lacks ``'unsafe-inline'`` refuses every
``style="..."`` attribute and every ``<style>`` without the request's nonce; a ``script-src``
without it refuses every inline ``<script>`` without the nonce. #3979 moved every component value
that comes from a caller (a width, a colour) behind ``LIVEVIEW_CONFIG["csp_strict_styles"]``. What
was left are CONSTANTS: a hidden trigger, a collapsed node, a pointer cursor. A constant needs no
switch and no client module: it is the ``hidden`` attribute or a class of the shipped stylesheets,
in every mode, with JavaScript off and at first paint. Where a framework variant (Bootstrap) has
no utility for the value and may load no djust stylesheet, the inline style stays by default and
a djust class takes its place under ``csp_strict_styles``, as #3979 did for the dropdown popover.

* ``TestTheSweep`` runs ``scripts/template-library-csp-sweep.py`` (every tag of djust's template
  libraries on both engines, every component class under each ``css_framework``, both modes, a
  request with a nonce) and asserts: in strict mode no inline style, ``<style>`` or executable
  inline ``<script>`` BLOCK without the nonce, and no ``javascript:`` URL; by default only the
  validated free-form values #3979 kept and the constants listed below; every tag was rendered;
  and a planted inline style is found (gate-off). Inline event-handler attributes are reported
  too, but converting them is #4146: the ones that exist today are named exceptions
  (:data:`HANDLERS_4146`), and a handler anywhere else fails.
* ``TestTheFindings``: what the sweep counts as a finding (handler, ``javascript:`` URL, the
  JavaScript MIME types).
* ``TestEverySite``: one test per converted site, in both modes, on each render path it has.
* ``TestTheStylesheets``: each class and ``[hidden]`` guard a site relies on is defined.
* ``TestTheScaffold``: a generated project's pages carry no inline style, and ``app.css`` defines
  every class they use.

The client side (the scripts that show what the server hid) is ``tests/js/csp_constant_styles_4139.test.js``
and ``tests/js/component_batch2_2985.test.js`` (the file tree).
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterator

import pytest
from django.template import Context, Engine

from djust.components import rust_handlers as rh
from djust.components.utils import STYLE_PROPERTIES, style_value
from djust.config import config
from djust.tests.native_call import native_render

ROOT = Path(__file__).resolve().parents[3]
PKG = ROOT / "python" / "djust"
SWEEP = ROOT / "scripts" / "template-library-csp-sweep.py"
COMPONENTS_CSS = PKG / "components" / "static" / "djust_components" / "components.css"
CLASSES_CSS = PKG / "components" / "static" / "djust_components" / "components-classes.css"
THEMING_CSS = PKG / "theming" / "static" / "djust_theming" / "css"

_ENGINE = Engine(
    dirs=[str(PKG / "components" / "templates"), str(PKG / "templates")],
    app_dirs=False,
    libraries={
        "djust_components": "djust.components.templatetags.djust_components",
        "djust_pwa": "djust.templatetags.djust_pwa",
        "static": "django.templatetags.static",
        "djust_assets": "djust.templatetags.djust_assets",
    },
)


@pytest.fixture(params=["default", "strict"])
def mode(request: pytest.FixtureRequest) -> Iterator[str]:
    before = config.get("csp_strict_styles", False)
    config.set("csp_strict_styles", request.param == "strict")
    try:
        yield request.param
    finally:
        config.set("csp_strict_styles", before)


@contextlib.contextmanager
def _framework(name: str) -> Iterator[None]:
    before = config.get("css_framework", "bootstrap5")
    config.set("css_framework", name)
    try:
        yield
    finally:
        config.set("css_framework", before)


def _tag(source: str, **context: Any) -> str:
    return _ENGINE.from_string("{% load djust_components %}" + source).render(Context(context))


def _no_style(markup: str) -> None:
    """Nothing a strict ``style-src`` refuses: no ``style`` attribute, no ``<style>``."""
    assert not re.search(r"\sstyle\s*=", markup), markup
    assert "<style" not in markup, markup


# ---------------------------------------------------------------------------
# The sweep (the issue's done-when)
# ---------------------------------------------------------------------------

#: Values #3979 kept inline by default, each a decision of that change, not a free-form value:
#: ``{% djust_skeleton shape="circle" %}``'s ``border-radius:50%`` and its ``<style>`` of keyframes
#: (``TestDjustSkeleton.test_default_mode_is_unchanged`` in ``test_csp_free_form_styles_3979.py``).
KEPT_DECLARATIONS = {("border-radius", "50%")}
#: Constants #4139 keeps inline by default, because the class that replaces them in strict mode is
#: defined only in a djust stylesheet a page may not load: a clickable data-table row's pointer.
KEPT_DECLARATIONS |= {("cursor", "pointer")}
KEPT_BLOCKS = {("style-element", "@keyframes djust-skeleton-shimmer")}

#: The shorthand / custom names the sites' ``css=`` templates print for a checked property
#: (``style_attrs(css="background:{}")``, ``scroll_area``'s custom property).
_PRINTED_AS = {"background": "background-color", "--dj-scroll-area-max-height": "max-height"}

#: The inline event handlers that exist today, each to be converted by #4146 (not here): what the
#: sweep reports for each, ``(key, "<element attribute>")``. A handler anywhere else fails.
HANDLERS_4146 = {
    ("theme_components:theme_alert", "<button onclick>"): "#4146: the alert's dismiss button",
    ("theme_components:theme_toast", "<button onclick>"): "#4146: the toast's close button",
    ("djust.components.ui.navbar_simple.NavBar", "<button onclick>"): (
        "#4146: NavBar's two menu toggles (Tailwind mobile menu, Bootstrap navbar-toggler)"
    ),
    ("theme_components:theme_button", "<button onclick>"): (
        "#4146: a caller-supplied onclick= (button.html and the six theme packs' button.html)"
    ),
    ("djust.components.components.button.Button", "<button onclick>"): (
        "#4146: a caller-supplied onclick= on the Button component"
    ),
}

#: The one tag the Rust engine cannot render at all, a gap older than this change: it has no
#: handler in ``djust/template_tags/pwa.py`` (the Django tag carries the nonce, #655).
NO_RUST_HANDLER = {"djust_pwa:djust_offline_styles"}


def _sweep_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k != "DJANGO_SETTINGS_MODULE"}
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(PKG.parent), env.get("PYTHONPATH")]))
    return env


def _run_sweep(out: Path, *args: str) -> dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, str(SWEEP), "--json", str(out), *args],
        cwd=ROOT,
        env=_sweep_env(),
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert proc.returncode == 0, proc.stderr[-4000:]
    data: dict[str, Any] = json.loads(out.read_text(encoding="utf-8"))
    return data


@pytest.fixture(scope="module")
def sweep(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    return _run_sweep(tmp_path_factory.mktemp("csp4139") / "sweep.json")


def _free_form_problems(value: str) -> list[str]:
    """Each declaration of a default-mode ``style`` that is not a checked free-form value."""
    problems = []
    for decl in filter(str.strip, value.split(";")):
        prop, _, text = (part.strip() for part in decl.partition(":"))
        checked = _PRINTED_AS.get(prop, prop)
        if (prop, text) in KEPT_DECLARATIONS:
            continue
        if checked not in STYLE_PROPERTIES or style_value(checked, text) != text:
            problems.append(decl)
    return problems


def _named_handler(key: str, finding: list[str]) -> bool:
    return finding[0] == "handler" and (key, finding[1]) in HANDLERS_4146


class TestTheSweep:
    def test_strict_mode_leaves_nothing_a_strict_csp_refuses(self, sweep):
        found = [
            (r["engine"], r["key"], r["variant"], f)
            for r in sweep["renders"]
            if r["mode"] == "strict"
            for f in r["findings"]
            if not _named_handler(r["key"], f)
        ]
        assert found == []
        assert sum(r["mode"] == "strict" for r in sweep["renders"]) > 1000

    def test_the_handler_exceptions_are_real(self, sweep):
        """Not vacuous: the sweep does report the handlers #4146 will convert."""
        seen = {
            (r["key"], f[1]) for r in sweep["renders"] for f in r["findings"] if f[0] == "handler"
        }
        assert {
            ("theme_components:theme_alert", "<button onclick>"),
            ("theme_components:theme_toast", "<button onclick>"),
            ("djust.components.ui.navbar_simple.NavBar", "<button onclick>"),
        } <= seen
        assert seen <= set(HANDLERS_4146)

    def test_by_default_the_only_inline_styles_are_checked_free_form_values(self, sweep):
        bad = set()
        for r in sweep["renders"]:
            if r["mode"] != "default":
                continue
            for kind, detail in r["findings"]:
                if _named_handler(r["key"], [kind, detail]):
                    continue
                if kind == "style-attr":
                    value = re.match(r"<\w+ style=(['\"])(.*)\1>$", detail, re.S).group(2)
                    bad |= {(r["key"], d) for d in _free_form_problems(value)}
                elif not any(k == kind and detail.startswith(t) for k, t in KEPT_BLOCKS):
                    bad.add((r["key"], kind, detail[:80]))
        assert bad == set()

    def test_and_by_default_those_are_there(self, sweep):
        """Not vacuous: the default render does carry #3979's free-form styles."""
        keys = {r["key"] for r in sweep["renders"] if r["mode"] == "default" and r["findings"]}
        assert {"djust_components:color_picker", "djust_components:dashboard_grid"} <= keys

    def test_every_tag_and_component_was_rendered_on_both_engines(self, sweep):
        rendered = {k: set(v) for k, v in sweep["rendered"].items()}
        inventory = set(sweep["inventory"])
        tags = {k for k in inventory if ":" in k} - set(sweep["skipped"])
        classes = {k for k in inventory if ":" not in k}
        assert len(tags) > 200 and len(classes) > 150
        for mode_name in ("default", "strict"):
            assert tags - rendered["%s|django" % mode_name] == set()
            assert tags - rendered["%s|djust" % mode_name] == NO_RUST_HANDLER
            frameworks = set().union(
                *(
                    rendered["%s|class:%s" % (mode_name, fw)]
                    for fw in ("bootstrap5", "tailwind", "plain")
                )
            )
            assert classes - frameworks == set()
        # Only closing tags and the documented exceptions are skipped.
        assert all(
            k.split(":")[1].startswith("end") or reason for k, reason in sweep["skipped"].items()
        )

    def test_a_planted_constant_style_is_found_in_both_modes(self, tmp_path):
        """Gate-off: the sweep reads the tags' real output (#1459)."""
        data = _run_sweep(
            tmp_path / "plant.json",
            "--only",
            "djust_components:badge",
            "--plant",
            "djust_components:badge",
        )
        for mode_name in ("default", "strict"):
            hits = [
                f
                for r in data["renders"]
                if r["mode"] == mode_name and r["engine"] == "django"
                for f in r["findings"]
                if f == ["style-attr", "<i style='display:none'>"]
            ]
            assert hits, mode_name


class TestTheFindings:
    """What the sweep counts (``csp_sweep.findings``), on synthetic markup."""

    @staticmethod
    def _findings(markup: str) -> list[tuple[str, str]]:
        from scripts.lib.template_library_parity import csp_sweep

        return csp_sweep.findings(markup)

    @pytest.mark.parametrize("attr", ["onclick", "onload", "onerror", "ONMOUSEOVER"])
    def test_an_event_handler_attribute(self, attr):
        assert self._findings('<b %s="x()">t</b>' % attr) == [("handler", "<b %s>" % attr.lower())]

    @pytest.mark.parametrize(
        "markup,where",
        [
            ('<a href="javascript:x()">', "<a href>"),
            ('<a href="java&#x09;script:x()">', "<a href>"),
            ('<a href=" JAVASCRIPT:x()">', "<a href>"),
            ('<iframe src="javascript:x()">', "<iframe src>"),
            ('<form action="javascript:x()">', "<form action>"),
            ('<button formaction="javascript:x()">', "<button formaction>"),
            ('<svg><a xlink:href="javascript:x()"></a></svg>', "<a xlink:href>"),
        ],
    )
    def test_a_javascript_url(self, markup, where):
        assert ("js-url", where) in self._findings(markup)

    def test_a_safe_url_is_not_one(self):
        assert (
            self._findings('<a href="/x?javascript:1">a</a><img src="data:image/png;base64,AA">')
            == []
        )

    @pytest.mark.parametrize(
        "script_type",
        [
            "",
            "module",
            "text/javascript",
            "text/javascript; charset=utf-8",
            "TEXT/JavaScript",
            "application/x-javascript",
            "application/ecmascript",
            "text/javascript1.5",
            "text/jscript",
        ],
    )
    def test_an_executable_inline_script(self, script_type):
        attr = ' type="%s"' % script_type if script_type else ""
        assert self._findings("<script%s>x()</script>" % attr) == [("script-inline", "x()")]

    @pytest.mark.parametrize(
        "script_type", ["application/json", "application/ld+json", "djust/hook", "text/template"]
    )
    def test_a_data_block_is_not_executable(self, script_type):
        assert self._findings('<script type="%s">x</script>' % script_type) == []

    def test_a_nonce_or_a_src_is_not_a_finding(self):
        from scripts.lib.template_library_parity import csp_sweep

        nonce = csp_sweep.NONCE
        assert (
            self._findings(
                '<script nonce="%s">x()</script><style nonce="%s">a{}</style>' % (nonce, nonce)
            )
            == []
        )
        assert self._findings('<script type="module" src="/m.js"></script>') == []


# ---------------------------------------------------------------------------
# Every site
# ---------------------------------------------------------------------------

_TRIGGERS = {
    "reorder": "reorderable",
    "edit": "editable_columns",
    "visibility": "column_visibility",
    "drag": "row_drag",
    "copy": "copyable",
    "import": "importable",
    "expression": "column_expressions",
}
_COLS = [{"key": "a", "label": "A", "sortable": True}]
_ROWS = [{"id": 1, "a": "x", "u": "/r/1"}]


def _native_table(**flags: Any) -> str:
    args = ["rows=r", "columns=c"] + ["%s=%s" % (k, k) for k in flags]
    return native_render(rh.DataTableHandler(), args, {"r": _ROWS, "c": _COLS, **flags})


class TestEverySite:
    @pytest.mark.parametrize("trigger", sorted(_TRIGGERS))
    def test_data_table_hidden_trigger_native(self, trigger, mode):
        flag = _TRIGGERS[trigger]
        value = {"editable_columns": ["a"], "column_expressions": {"b": "a"}}.get(flag, True)
        html = _native_table(**{flag: value})
        assert re.search(r'<button class="data-table-%s-trigger" hidden ' % trigger, html), html
        _no_style(html)

    def test_data_table_import_file_input_native(self, mode):
        html = _native_table(importable=True)
        assert '<input type="file" class="data-table-import-file" hidden' in html, html
        _no_style(html)

    @staticmethod
    def _assert_row_pointer(html: str, mode: str) -> None:
        """Default: the inline pointer it always carried (the class's rule is only in
        components.css, which a page may not load). Strict: the class alone."""
        row = re.search(r'<tr class="data-table-row-clickable"[^>]*>', html)
        assert row, html
        assert 'role="button" tabindex="0"' in row.group(0), html
        if mode == "strict":
            assert "style" not in row.group(0), html
            assert "cursor" not in html, html
        else:
            assert row.group(0).count('style="cursor:pointer"') == 1, html

    @pytest.mark.parametrize("nav", ["row_click_event", "row_url"])
    def test_data_table_clickable_row_native(self, nav, mode):
        html = _native_table(**{nav: "open" if nav == "row_click_event" else "u"})
        self._assert_row_pointer(html, mode)

    @pytest.mark.parametrize("nav", ['row_click_event="open"', 'row_url="u"'])
    def test_data_table_clickable_row_django(self, nav, mode):
        html = _tag("{% data_table rows=r columns=c " + nav + " %}", r=_ROWS, c=_COLS)
        self._assert_row_pointer(html, mode)

    def test_data_grid_hidden_trigger(self, mode):
        html = _tag("{% data_grid columns=c rows=r %}", c=[{"key": "a"}], r=[{"id": 1}])
        assert '<button class="data-grid-edit-trigger" hidden ' in html, html
        _no_style(html)

    def test_connection_status_tag(self, mode):
        html = _tag("{% connection_status %}")
        assert 'role="status" aria-live="polite" hidden>' in html, html
        _no_style(html)

    def test_connection_status_class(self, mode):
        from djust.components.components.connection_status import ConnectionStatus

        html = str(ConnectionStatus().render())
        assert 'role="status" aria-live="polite" hidden>' in html, html
        _no_style(html)

    def test_scroll_to_top_tag(self, mode):
        html = _tag("{% scroll_to_top %}")
        assert re.search(r'<button class="dj-scroll-to-top"[^>]* hidden>', html), html
        _no_style(html)

    def test_scroll_to_top_class(self, mode):
        from djust.components.components.scroll_to_top import ScrollToTop

        html = str(ScrollToTop().render())
        assert re.search(r'<button class="dj-scroll-to-top"[^>]* hidden>', html), html
        _no_style(html)

    _FILES = [{"name": "src", "type": "folder", "expanded": False, "children": [{"name": "a.py"}]}]

    def test_file_tree_collapsed_folder_tag(self, mode):
        html = _tag("{% file_tree nodes=n %}", n=self._FILES)
        assert '<div class="dj-file-tree__children" hidden>' in html, html
        assert "display" not in html, html

    def test_file_tree_collapsed_folder_class(self, mode):
        from djust.components.components.file_tree import FileTree

        html = str(FileTree(nodes=self._FILES).render())
        assert '<div class="dj-file-tree__children" hidden>' in html, html
        assert "display" not in html, html

    def test_tabs_plain_inactive_pane(self, mode):
        from djust.components.layout.tabs import TabsComponent

        tabs = [
            {"id": "a", "label": "A", "content": "x"},
            {"id": "b", "label": "B", "content": "y"},
        ]
        with _framework("plain"):
            html = str(TabsComponent(tabs=tabs, active="a").render())
        assert '<div class="tab-pane" id="tab-a">' in html, html
        assert '<div class="tab-pane" id="tab-b" hidden>' in html, html
        _no_style(html)

    @pytest.mark.parametrize("framework", ["bootstrap5", "plain"])
    def test_modal_open(self, framework, mode):
        from djust.components.ui.modal import ModalComponent

        with _framework(framework):
            html = str(ModalComponent(title="T", body="B", show=True).render())
        assert re.search(r'<div class="modal[^"]* d-block"', html), html
        _no_style(html)

    def test_badge_close_button_bootstrap(self, mode):
        from djust.components.ui.badge import BadgeComponent

        with _framework("bootstrap5"):
            html = str(BadgeComponent(text="x", dismissible=True).render())
        if mode == "strict":
            assert 'class="btn-close btn-close-white dj-badge-close"' in html, html
            _no_style(html)
        else:  # unchanged: a Bootstrap page may load no djust stylesheet
            assert 'style="font-size: 0.65em; padding: 0.1em 0.25em;"' in html, html
            assert "dj-badge-close" not in html, html

    def _navbar(self) -> str:
        from djust.components.layout.navbar import NavbarComponent, NavItem

        items = [NavItem("Docs", "/docs", external=True), NavItem("Home", "/")]
        with _framework("bootstrap5"):
            return str(NavbarComponent(brand_name="A", items=items).render())

    def test_navbar_collapse_is_d_flex(self, mode):
        html = self._navbar()
        assert '<div class="navbar-collapse d-flex">' in html, html
        assert "display: flex" not in html, html

    def test_navbar_external_link_bootstrap(self, mode):
        html = self._navbar()
        if mode == "strict":
            assert 'class="nav-link  dj-navbar-link--external"' in html, html
            _no_style(html)
        else:
            assert 'style="color: #667eea !important; font-weight: 600;"' in html, html
            assert "dj-navbar-link--external" not in html, html

    def test_table_simple_sortable_header_bootstrap(self, mode):
        from djust.components.ui.table_simple import Table

        with _framework("bootstrap5"):
            html = str(
                Table(columns=[{"key": "a", "label": "A", "sortable": True}], data=[]).render()
            )
        if mode == "strict":
            assert '<th scope="col" class="sortable dj-sortable-th">A</th>' in html, html
            _no_style(html)
        else:
            assert '<th scope="col" class="sortable" style="cursor: pointer;">A</th>' in html, html

    def test_data_table_component_sortable_header_bootstrap(self, mode):
        from djust.components.data.table import TableComponent

        with _framework("bootstrap5"):
            html = str(TableComponent(columns=_COLS, rows=_ROWS).render())
        if mode == "strict":
            assert '<th class="dj-sortable-th" aria-sort=' in html, html
            assert "cursor" not in html, html
        else:
            assert '<th style="cursor: pointer" aria-sort=' in html, html

    @staticmethod
    def _ttyd(**extra: Any) -> str:
        from djust.components.utils import strict_styles

        return _ENGINE.get_template("djust_components/ttyd_terminal.html").render(
            Context(
                {
                    "ttyd_url": "ws://h/x",
                    "rows": 3,
                    "cols": 8,
                    "theme_json": "{}",
                    "strict_styles": strict_styles(),
                    **extra,
                }
            )
        )

    def test_ttyd_hook_module_is_loaded_by_its_own_static_url(self, mode):
        html = self._ttyd()
        scripts = re.findall(r"<script\b([^>]*)>(.*?)</script>", html, re.S)
        assert scripts, html
        for attrs, body in scripts:
            assert re.search(r'\bsrc="', attrs), attrs  # no inline body: nothing for script-src
            assert body.strip() == "", body
        assert '<script type="module" src="/static/djust_components/ttyd/ttyd_terminal.js">' in html
        hook = PKG / "components" / "static" / "djust_components" / "ttyd" / "ttyd_terminal.js"
        # It registers itself: no second module, so no relative import a manifest would not rewrite.
        assert "window.djust.hooks.TtydTerminal = TtydTerminalHook;" in hook.read_text()
        assert not (hook.parent / "register.js").exists()

    def test_ttyd_hook_module_carries_the_request_nonce(self):
        html = self._ttyd(csp_nonce="n0nce")
        assert re.search(r'<script type="module" src="[^"]+ttyd_terminal\.js" nonce="n0nce">', html)

    def test_ttyd_view_hands_the_template_the_request_nonce(self, rf):
        from djust.components.ttyd.terminal import TtydTerminalView

        request = rf.get("/")
        request.csp_nonce = "n0nce"
        view = TtydTerminalView()
        view.request = request
        assert view.get_context_data()["csp_nonce"] == "n0nce"
        view.request = rf.get("/")
        assert view.get_context_data()["csp_nonce"] == ""

    def test_ttyd_hook_url_is_the_hashed_one_under_manifest_storage(self, tmp_path, settings):
        """Django's manifest storage serves only the hashed name; the template must ask for it."""
        hashed = "djust_components/ttyd/ttyd_terminal.0123456789ab.js"
        (tmp_path / "staticfiles.json").write_text(
            json.dumps(
                {
                    "version": "1.1",
                    "paths": {
                        "djust_components/ttyd/ttyd_terminal.js": hashed,
                        "djust_components/vendor/xterm/xterm.mjs": "djust_components/vendor/xterm/xterm.ffff.mjs",
                        "djust_components/vendor/xterm/xterm.css": "djust_components/vendor/xterm/xterm.ffff.css",
                    },
                }
            )
        )
        from django.contrib.staticfiles.storage import staticfiles_storage

        settings.DEBUG = False
        settings.STATIC_ROOT = str(tmp_path)
        settings.STORAGES = {
            **settings.STORAGES,
            "staticfiles": {
                "BACKEND": "django.contrib.staticfiles.storage.ManifestStaticFilesStorage"
            },
        }
        from django.utils.functional import empty

        staticfiles_storage._wrapped = empty  # the lazy storage reads the overridden settings
        try:
            html = self._ttyd()
        finally:
            staticfiles_storage._wrapped = empty
        assert '<script type="module" src="/static/%s">' % hashed in html, html

    def test_theme_modal_and_dropdown_closed(self, mode):
        from django.template.loader import render_to_string

        modal = render_to_string(
            "djust_theming/components/modal.html", {"id": "m", "is_open": False, "css_prefix": ""}
        )
        dropdown = render_to_string(
            "djust_theming/components/dropdown.html",
            {"id": "d", "is_open": False, "css_prefix": ""},
        )
        assert re.search(r'data-theme-modal="m"\s+hidden>', modal), modal
        assert re.search(r'role="menu"\s+hidden>', dropdown), dropdown
        _no_style(modal + dropdown)

    @pytest.mark.parametrize("prefix", ["", "dj-"])
    def test_theme_modal_and_dropdown_closed_stay_hidden_under_the_components_css(self, prefix):
        """With css_prefix="dj-" the theme backdrop and menu carry ``dj-modal-backdrop`` /
        ``dj-dropdown-menu``, which components.css gives a ``display``; its guard must hide them
        when ``hidden`` (a page with that stylesheet and not the theming one)."""
        from django.template.loader import render_to_string

        modal = render_to_string(
            "djust_theming/components/modal.html",
            {"id": "m", "is_open": False, "css_prefix": prefix},
        )
        dropdown = render_to_string(
            "djust_theming/components/dropdown.html",
            {"id": "d", "is_open": False, "css_prefix": prefix},
        )
        backdrop = re.search(r'<div class="([^"]*modal-backdrop)"[^>]*\shidden>', modal).group(1)
        menu = re.search(r'<div class="([^"]*dropdown-menu)\b[^"]*"[^>]*\shidden>', dropdown).group(
            1
        )
        css = COMPONENTS_CSS.read_text()
        bare = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
        for cls in (backdrop, menu):
            sets_display = any(
                ("." + cls) in [x.strip() for x in m.group(1).split(",")]
                and re.search(r"(?<![-\w])display:\s*(?!none)", m.group(2))
                for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", bare)
            )
            if sets_display:  # components.css gives it a display: its [hidden] guard must exist
                assert re.search(
                    r"display:\s*none\s*!important", _rule(css, ".%s:where([hidden])" % cls)
                ), cls
        if prefix:
            assert backdrop == "dj-modal-backdrop" and menu == "dj-dropdown-menu"

    def test_accessibility_report_score(self):
        from djust.theming.accessibility import AccessibilityValidator

        validator = AccessibilityValidator()
        design = next(iter(validator.design_systems))
        preset = next(iter(validator.color_presets))
        reports = {"t": validator.validate_theme_accessibility(design, preset)}
        html = validator.generate_accessibility_report_html(reports)
        assert not re.search(r"\sstyle\s*=", html), html  # its own <style> holds the rule
        assert ".theme-header .score { float: right; }" in html

    @pytest.mark.parametrize("nonce", ["", "n0nce"])
    def test_theme_context_switcher(self, rf, nonce):
        from djust.theming.context_processors import theme_context

        request = rf.get("/")
        if nonce:
            request.csp_nonce = nonce
        html = str(theme_context(request)["theme_switcher"])
        assert not re.search(r"\son[a-z]+\s*=", html), html  # no inline handler
        assert html.count('data-theme-mode="') == 3 and "data-theme-preset-select" in html
        styles = re.findall(r"<style\b[^>]*>", html)
        assert styles == ['<style nonce="%s">' % nonce if nonce else "<style>"], styles

    @pytest.mark.parametrize("engine", ["django", "rust"])
    def test_pwa_head_carries_the_request_nonce(self, rf, engine):
        request = rf.get("/")
        request.csp_nonce = "n0nce"
        if engine == "django":
            html = _ENGINE.from_string("{% load djust_pwa %}{% djust_pwa_head %}").render(
                Context({"request": request})
            )
        else:
            from djust.template_tags.pwa import PwaHeadHandler

            html = PwaHeadHandler().render([], {"request": request})
        assert re.findall(r"<(style|script)\b([^>]*)>", html) == [
            ("style", ' nonce="n0nce"'),
            ("script", ' nonce="n0nce"'),
        ], html

    def test_pwa_head_without_a_nonce_is_unchanged(self):
        html = _ENGINE.from_string("{% load djust_pwa %}{% djust_pwa_head %}").render(Context({}))
        assert "<style>" in html and "<script>" in html and "nonce=" not in html

    @pytest.mark.parametrize("template", ["install_prompt", "update_notification"])
    def test_pwa_snippets(self, template):
        html = _ENGINE.get_template("djust/pwa/%s.html" % template).render(
            Context({"class_name": "c", "text": "t", "message": "m"})
        )
        assert re.search(r"<(button|div) class=\"[^\"]+\" hidden>", html), html
        _no_style(html)


# ---------------------------------------------------------------------------
# The stylesheets
# ---------------------------------------------------------------------------


def _rule(css: str, selector: str) -> str:
    """The declarations of every rule whose selector list contains ``selector``, joined."""
    found = [
        m.group(2)
        for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", re.sub(r"/\*.*?\*/", "", css, flags=re.S))
        if selector in [s.strip() for s in m.group(1).split(",")]
    ]
    assert found, "no rule for %s" % selector
    return ";".join(found)


class TestTheStylesheets:
    @pytest.mark.parametrize(
        "selector",
        [
            ".dj-scroll-to-top:where([hidden])",
            ".dj-connection-status:where([hidden])",
            ".dj-file-tree__children:where([hidden])",
            ".data-table-import-file:where([hidden])",
            ".data-grid-edit-trigger:where([hidden])",
            ".tab-pane:where([hidden])",
            *(".data-table-%s-trigger:where([hidden])" % t for t in sorted(_TRIGGERS)),
        ],
    )
    def test_the_hidden_guard_beats_a_components_display(self, selector):
        assert re.search(
            r"display:\s*none\s*!important", _rule(COMPONENTS_CSS.read_text(), selector)
        )

    def test_a_clickable_row_has_the_pointer(self):
        assert "cursor: pointer" in _rule(COMPONENTS_CSS.read_text(), ".data-table-row-clickable")

    @pytest.mark.parametrize("sheet", [COMPONENTS_CSS, CLASSES_CSS], ids=["components", "classes"])
    @pytest.mark.parametrize(
        "selector,declarations",
        [
            (
                ".dj-badge-close",
                ["font-size: 0.65em !important", "padding: 0.1em 0.25em !important"],
            ),
            (
                ".dj-navbar-link--external",
                ["color: #667eea !important", "font-weight: 600 !important"],
            ),
            (".dj-sortable-th", ["cursor: pointer"]),
        ],
    )
    def test_the_strict_mode_classes_match_the_inline_styles(self, sheet, selector, declarations):
        rule = _rule(sheet.read_text(), selector)
        for decl in declarations:
            assert decl in rule, (selector, rule)

    def test_the_theme_modal_backdrop_is_hidden_by_both_its_stylesheets(self):
        guard = r"display:\s*none\s*!important"
        assert re.search(
            guard, _rule((THEMING_CSS / "scaffold.css").read_text(), ".modal-backdrop[hidden]")
        )
        assert re.search(
            guard,
            _rule(
                (THEMING_CSS / "components.css").read_text(), '[hidden]:not([hidden="until-found"])'
            ),
        )

    def test_the_scroll_button_fades_only_while_hidden(self):
        css = COMPONENTS_CSS.read_text()
        assert '[style*="display:none"]' not in css
        assert "opacity: 0" in _rule(css, ".dj-scroll-to-top[hidden]")


# ---------------------------------------------------------------------------
# The scaffold
# ---------------------------------------------------------------------------


_SCAFFOLD_FLAGS = {
    "bare": {"bare": True},
    "full": {"with_auth": True, "with_presence": True, "with_streaming": True},
    "db": {"with_db": True},
    "schema": {"from_schema": "SCHEMA"},
}


class TestTheScaffold:
    @pytest.mark.parametrize("flags", sorted(_SCAFFOLD_FLAGS))
    def test_generated_pages_carry_no_inline_style_and_app_css_defines_their_classes(
        self, tmp_path, flags
    ):
        from djust.scaffolding.generator import generate_project

        kwargs = dict(_SCAFFOLD_FLAGS[flags])
        if "from_schema" in kwargs:
            schema = tmp_path / "schema.json"
            schema.write_text(
                json.dumps(
                    {
                        "models": [
                            {
                                "name": "Post",
                                "fields": [
                                    {"name": "title", "type": "string"},
                                    {"name": "body", "type": "text"},
                                ],
                            }
                        ]
                    }
                )
            )
            kwargs["from_schema"] = str(schema)
        project = generate_project("site4139", target_dir=str(tmp_path), auto_setup=False, **kwargs)
        pages = sorted((project / "site4139" / "templates").rglob("*.html"))
        assert pages
        css = (project / "site4139" / "static" / "site4139" / "app.css").read_text()
        defined = set(re.findall(r"\.([a-zA-Z][\w-]*)", css))
        for page in pages:
            text = page.read_text()
            text = re.sub(r"\{#.*?#\}", "", text)  # template comments are never rendered
            assert not re.search(r"\sstyle\s*=", text), page
            assert "<style" not in text, page
            used = {
                c
                for attr in re.findall(r'class="([^"]*)"', re.sub(r"\{%.*?%\}", " ", text))
                for c in attr.split()
                if c.startswith("app-")
            }
            assert used <= defined, (page, used - defined)
        base = (project / "site4139" / "templates" / "site4139" / "base.html").read_text()
        assert "{% static 'site4139/app.css' %}" in base
