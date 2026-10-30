"""Components under a strict nonce CSP, with ARIA semantics and stable ids.

A Content-Security-Policy without ``'unsafe-inline'`` refuses every inline
event-handler attribute (``onclick=`` and friends) and every inline
``<script>`` that does not carry the request's nonce. Each component here used
to depend on one of those, or rendered markup a socket render could not diff:

* ``theme_toggle`` rendered a new random id on every render, and its buttons
  did nothing unless a separate ``theme-toggle.js`` (which ``theme_head`` does
  not load) was on the page.
* ``rich_select`` / ``combobox`` opened, closed and filtered through inline
  handlers.
* ``modal`` rendered over the socket (the Rust engine) had no dialog role and
  put its size class on the full-screen backdrop.
* ``dropdown_menu`` items could only be plain buttons, and ``menu_item``
  children were a template error under the Rust engine.
* ``copy_button`` copied through an inline handler, ``critical_css`` loaded the
  deferred stylesheet through an inline ``onload=``, ``colocated_hook`` needed
  ``'unsafe-eval'``, and ``confirm_dialog`` had random ids and no body.

Every component is rendered through BOTH engines: the Django template tag
(first HTTP render) and the Rust tag handler (socket renders).
"""

from __future__ import annotations

import re

import pytest
from django.template import Context, Engine, Template
from django.test import RequestFactory, override_settings

from djust._rust import RustLiveView
from djust.components.rust_handlers import register_with_rust_engine

_ENGINE = Engine(
    libraries={
        "djust_components": "djust.components.templatetags.djust_components",
        "live_tags": "djust.templatetags.live_tags",
    }
)

# Any inline event-handler attribute: ` onclick=`, ` onkeydown=`, ` onload=` ...
_INLINE_HANDLER = re.compile(r"""[\s"'](on[a-z]+)\s*=""", re.IGNORECASE)
_SCRIPT_OPEN = re.compile(r"<script\b([^>]*)>", re.IGNORECASE)
_RANDOM_ID = re.compile(r'id="[^"]*-[0-9a-f]{6,}"')


def _django(src: str, ctx: dict | None = None) -> str:
    return _ENGINE.from_string("{% load djust_components %}" + src).render(Context(ctx or {}))


def _rust(src: str, ctx: dict | None = None) -> str:
    register_with_rust_engine()
    rv = RustLiveView("<div dj-root>" + src + "</div>")
    if ctx:
        rv.update_state(ctx)
    out = rv.render()
    return out[len("<div dj-root>") : -len("</div>")]


def _both(src: str, ctx: dict | None = None) -> dict[str, str]:
    return {"django": _django(src, ctx), "rust": _rust(src, ctx)}


def assert_strict_csp_safe(html: str, nonce: str = "") -> None:
    """No inline handler; every executable inline script carries ``nonce``."""
    handlers = _INLINE_HANDLER.findall(html)
    assert not handlers, f"inline event handlers {handlers} in: {html}"
    for attrs in _SCRIPT_OPEN.findall(html):
        type_match = re.search(r'type="([^"]*)"', attrs)
        script_type = type_match.group(1).lower() if type_match else ""
        if script_type in ("application/json", "application/ld+json"):
            continue  # data, never executed
        if "src=" in attrs and script_type in ("", "text/javascript", "module"):
            continue  # external file: governed by script-src, not the nonce
        assert nonce and f'nonce="{nonce}"' in attrs, (
            f"inline <script{attrs}> without the request nonce in: {html}"
        )


OPTIONS = [
    {"value": "a", "label": "Alpha"},
    {"value": "b", "label": "Beta"},
]


# ---------------------------------------------------------------------------
# theme_toggle: deterministic id, ARIA radios, djust.theming's mode attribute
# ---------------------------------------------------------------------------


class TestThemeToggle:
    @pytest.mark.parametrize("engine", ["django", "rust"])
    def test_renders_identically_twice(self, engine):
        render = _django if engine == "django" else _rust
        src = '{% theme_toggle current="dark" %}'
        assert render(src) == render(src)

    @pytest.mark.parametrize("engine", ["django", "rust"])
    def test_no_random_id_and_optional_id_kwarg(self, engine):
        render = _django if engine == "django" else _rust
        assert not _RANDOM_ID.search(render("{% theme_toggle %}"))
        assert 'id="site-theme"' in render('{% theme_toggle id="site-theme" %}')

    @pytest.mark.parametrize("engine", ["django", "rust"])
    def test_buttons_are_radios_checked_by_current(self, engine):
        render = _django if engine == "django" else _rust
        out = render('{% theme_toggle current="dark" %}')
        assert 'role="radiogroup"' in out
        assert out.count('role="radio"') == 3
        assert re.search(r'data-theme-mode="dark"[^>]*aria-checked="true"', out)
        assert re.search(r'data-theme-mode="light"[^>]*aria-checked="false"', out)
        assert re.search(r'data-theme-mode="system"[^>]*aria-checked="false"', out)

    @pytest.mark.parametrize("engine", ["django", "rust"])
    def test_buttons_use_the_theming_mode_attribute(self, engine):
        """djust.theming's theme.js handles any ``[data-theme-mode]`` click,
        so the toggle works with ``{% theme_head %}`` alone. The old
        ``data-theme="dark"`` on a button matched ``[data-theme="dark"]``
        theme selectors, styling the button as if the page were dark."""
        render = _django if engine == "django" else _rust
        out = render("{% theme_toggle %}")
        for mode in ("light", "dark", "system"):
            assert f'data-theme-mode="{mode}"' in out
        assert not re.search(r'\sdata-theme="', out)

    @pytest.mark.parametrize("engine", ["django", "rust"])
    def test_event_reaches_the_server_with_the_mode(self, engine):
        render = _django if engine == "django" else _rust
        out = render('{% theme_toggle event="set_theme" %}')
        assert out.count('dj-click="set_theme"') == 3
        assert 'data-value="dark"' in out

    def test_engines_agree(self):
        out = _both('{% theme_toggle current="light" event="set_theme" %}')
        assert out["django"] == out["rust"]


# ---------------------------------------------------------------------------
# rich_select / combobox: no inline handlers
# ---------------------------------------------------------------------------


class TestRichSelect:
    @pytest.mark.parametrize("searchable", [False, True])
    def test_both_engines_strict_csp_safe(self, searchable):
        src = '{% rich_select name="pick" options=opts value="a" searchable=s %}'
        for out in _both(src, {"opts": OPTIONS, "s": searchable}).values():
            assert_strict_csp_safe(out)
            assert "rich-select-trigger" in out

    def test_component_class_strict_csp_safe(self):
        from djust.components.components.rich_select import RichSelect

        out = str(RichSelect(name="pick", options=OPTIONS, value="a"))
        assert_strict_csp_safe(out)

    @pytest.mark.parametrize("engine", ["django", "rust"])
    def test_deterministic_id(self, engine):
        render = _django if engine == "django" else _rust
        src = '{% rich_select name="pick" options=opts %}'
        first = render(src, {"opts": OPTIONS})
        assert first == render(src, {"opts": OPTIONS})
        assert not _RANDOM_ID.search(first)

    @pytest.mark.parametrize("engine", ["django", "rust"])
    def test_listbox_wiring(self, engine):
        render = _django if engine == "django" else _rust
        out = render('{% rich_select name="pick" options=opts value="b" %}', {"opts": OPTIONS})
        listbox = re.search(r'<div class="rich-select-dropdown" role="listbox" id="([^"]+)"', out)
        assert listbox, out
        assert f'aria-controls="{listbox.group(1)}"' in out
        assert re.search(r'data-value="b"[^>]*aria-selected="true"', out)


class TestCombobox:
    @pytest.mark.parametrize("multiple", [False, True])
    def test_both_engines_strict_csp_safe(self, multiple):
        src = '{% combobox name="city" options=opts multiple=m %}'
        for out in _both(src, {"opts": OPTIONS, "m": multiple}).values():
            assert_strict_csp_safe(out)
            assert "combobox-dropdown" in out

    def test_component_class_strict_csp_safe(self):
        from djust.components.components.combobox import Combobox

        assert_strict_csp_safe(str(Combobox(name="city", options=OPTIONS)))

    @pytest.mark.parametrize("engine", ["django", "rust"])
    def test_combobox_aria(self, engine):
        render = _django if engine == "django" else _rust
        out = render('{% combobox name="city" options=opts value="a" %}', {"opts": OPTIONS})
        assert 'role="combobox"' in out
        assert 'aria-controls="city-listbox"' in out
        assert 'aria-expanded="false"' in out
        assert 'id="city-listbox" role="listbox"' in out
        assert out.count('role="option"') == 2
        assert re.search(
            r'aria-selected="true"[^>]*data-value="a"|data-value="a"[^>]*aria-selected="true"', out
        )


# ---------------------------------------------------------------------------
# modal over the socket: same markup as the Django tag
# ---------------------------------------------------------------------------


class TestModalSocketRender:
    SRC = '{% modal open=True size="sm" title="Edit" id="edit" %}<p>Body</p>{% endmodal %}'

    def test_rust_render_is_a_dialog(self):
        out = _rust(self.SRC)
        assert 'role="dialog"' in out
        assert 'aria-modal="true"' in out
        assert 'aria-labelledby="edit-title"' in out
        assert 'id="edit-title"' in out
        assert 'aria-label="Close"' in out

    def test_size_class_is_on_the_dialog_not_the_backdrop(self):
        out = _rust(self.SRC)
        backdrop = re.search(r'<div class="([^"]*)"', out).group(1)
        assert "sm" not in backdrop, backdrop
        assert re.search(r'class="dj-modal dj-modal--sm" role="dialog"', out), out

    def test_engines_agree(self):
        out = _both(self.SRC)
        assert out["django"] == out["rust"]

    def test_closed_renders_nothing(self):
        assert _rust("{% modal open=False %}x{% endmodal %}") == ""


# ---------------------------------------------------------------------------
# dropdown_menu: item kinds, children under the Rust engine, custom content
# ---------------------------------------------------------------------------


class TestDropdownMenu:
    ITEMS = [
        {"heading": "Theme"},
        {"label": "Light", "event": "set_mode", "value": "light", "type": "radio", "checked": True},
        {"label": "Dark", "event": "set_mode", "value": "dark", "type": "radio", "checked": False},
        {"divider": True},
        {"label": "Technical details", "event": "toggle_details", "checked": True},
        {"label": "Docs", "href": "/docs/", "icon": "?"},
        {"label": "Bad", "href": "javascript:alert(1)"},
        {"label": "Off", "event": "x", "disabled": True},
    ]

    def _render(self, engine: str) -> str:
        render = _django if engine == "django" else _rust
        return render(
            "{% dropdown_menu open=True items=items %}{% enddropdown_menu %}", {"items": self.ITEMS}
        )

    @pytest.mark.parametrize("engine", ["django", "rust"])
    def test_radio_and_checkbox_items(self, engine):
        out = self._render(engine)
        assert re.search(
            r'role="menuitemradio" aria-checked="true"[^>]*dj-click="set_mode" data-value="light"',
            out,
        ), out
        assert re.search(r'role="menuitemradio" aria-checked="false"[^>]*data-value="dark"', out)
        assert re.search(
            r'role="menuitemcheckbox" aria-checked="true"[^>]*dj-click="toggle_details"', out
        )

    @pytest.mark.parametrize("engine", ["django", "rust"])
    def test_link_items_and_unsafe_scheme(self, engine):
        out = self._render(engine)
        assert re.search(r'<a class="dj-dropdown-menu__item" role="menuitem" href="/docs/"', out), (
            out
        )
        assert '<a class="dj-dropdown-menu__item" role="menuitem" href="#">' in out
        assert "javascript:" not in out

    @pytest.mark.parametrize("engine", ["django", "rust"])
    def test_heading_separator_icon_disabled(self, engine):
        out = self._render(engine)
        assert 'class="dj-dropdown-menu__heading" role="presentation">Theme<' in out
        assert 'role="separator"' in out
        assert '<span class="dj-dropdown-menu__icon" aria-hidden="true">?</span>' in out
        assert re.search(r'aria-disabled="true" disabled', out)

    def test_engines_agree_on_items(self):
        assert self._render("django") == self._render("rust")

    CHILDREN = (
        '{% dropdown_menu open=True label="More" %}'
        '{% menu_item label="Edit" event="edit" icon="E" %}'
        "{% menu_divider %}"
        '{% menu_item label="Pin" event="pin" checked=True %}'
        '{% menu_item label="Help" href="/help/" %}'
        '<div class="extra">Runtime: 3s</div>'
        "{% enddropdown_menu %}"
    )

    def test_menu_item_children_render_under_rust(self):
        out = _rust(self.CHILDREN)
        assert 'role="menuitem" dj-click="edit"' in out
        assert 'role="menuitemcheckbox" aria-checked="true"' in out
        assert 'href="/help/"' in out
        assert 'role="separator"' in out

    def test_custom_content_kept_by_both_engines(self):
        for out in _both(self.CHILDREN).values():
            assert '<div class="extra">Runtime: 3s</div>' in out

    def test_engines_agree_on_children(self):
        out = _both(self.CHILDREN)
        assert out["django"] == out["rust"]

    @pytest.mark.parametrize("engine", ["django", "rust"])
    def test_open_state_attribute_is_the_same(self, engine):
        render = _django if engine == "django" else _rust
        out = render(
            "{% dropdown_menu open=True items=items %}{% enddropdown_menu %}",
            {"items": [{"label": "A"}]},
        )
        assert '<div class="dj-dropdown-menu dj-dropdown-menu--open" data-open>' in out


# ---------------------------------------------------------------------------
# copy_button, confirm_dialog, critical_css, colocated_hook
# ---------------------------------------------------------------------------


class TestCopyButton:
    def test_both_engines_strict_csp_safe_and_use_dj_copy(self):
        src = '{% copy_button text="pip install djust" copied_label="Done" %}'
        for out in _both(src).values():
            assert_strict_csp_safe(out)
            assert 'dj-copy="pip install djust"' in out
            assert 'dj-copy-feedback="Done"' in out
            assert 'type="button"' in out


class TestConfirmDialog:
    SRC = '{% confirm_dialog open=True title="Delete?" message="Gone for good." id="del" %}'

    @pytest.mark.parametrize("engine", ["django", "rust"])
    def test_ids_are_deterministic(self, engine):
        render = _django if engine == "django" else _rust
        out = render(self.SRC)
        assert out == render(self.SRC)
        assert 'aria-labelledby="del-title"' in out
        assert 'aria-describedby="del-msg"' in out

    @pytest.mark.parametrize("engine", ["django", "rust"])
    def test_default_ids_have_no_random_suffix(self, engine):
        render = _django if engine == "django" else _rust
        out = render("{% confirm_dialog open=True %}")
        assert not _RANDOM_ID.search(out), out

    BLOCK = (
        '{% confirm_dialog_block open=True title="Send?" message="To the approvers." '
        'confirm_event="send" id="send" %}'
        '<textarea name="note" dj-model="note"></textarea>'
        '<p class="pending">Sending</p>'
        "{% endconfirm_dialog_block %}"
    )

    def test_block_form_has_a_body_slot(self):
        for out in _both(self.BLOCK).values():
            assert 'role="alertdialog"' in out
            assert '<textarea name="note" dj-model="note"></textarea>' in out
            assert "To the approvers." in out
            assert 'dj-click="send"' in out
            assert_strict_csp_safe(out)

    def test_block_form_engines_agree(self):
        out = _both(self.BLOCK)
        assert out["django"] == out["rust"]

    def test_block_form_closed_renders_nothing(self):
        src = "{% confirm_dialog_block open=False %}x{% endconfirm_dialog_block %}"
        assert _django(src) == "" and _rust(src) == ""


class TestCriticalCss:
    def _theme_head(self, nonce: str = "") -> str:
        request = RequestFactory().get("/")
        request.COOKIES = {}
        if nonce:
            request.csp_nonce = nonce
        with override_settings(
            LIVEVIEW_CONFIG={"theme": {"critical_css": True}},
            ROOT_URLCONF="tests.test_critical_css",
        ):
            return Template("{% load theme_tags %}{% theme_head %}").render(
                Context({"request": request})
            )

    def test_deferred_stylesheet_has_no_inline_onload(self):
        out = self._theme_head(nonce="n0nce")
        assert "data-djust-theme-deferred" in out
        assert_strict_csp_safe(out, nonce="n0nce")

    def test_activation_script_present_without_nonce(self):
        out = self._theme_head()
        assert "onload=" not in out
        assert "data-djust-theme-deferred" in out
        assert re.search(r"<script>[^<]*data-djust-theme-deferred", out), out


class TestColocatedHook:
    SRC = '{% load live_tags %}{% colocated_hook "Chart" %}hook.mounted=function(){};{% endcolocated_hook %}'

    def test_carries_the_request_nonce(self):
        request = RequestFactory().get("/")
        request.csp_nonce = "abc123"
        out = _ENGINE.from_string(self.SRC).render(Context({"request": request}))
        assert '<script type="djust/hook" data-hook="Chart" nonce="abc123">' in out

    def test_no_nonce_attribute_without_a_nonce(self):
        out = _ENGINE.from_string(self.SRC).render(Context({}))
        assert '<script type="djust/hook" data-hook="Chart">' in out


# ---------------------------------------------------------------------------
# CSP regression: every component touched here, both engines
# ---------------------------------------------------------------------------

_CSP_CASES = {
    "theme_toggle": ('{% theme_toggle current="dark" event="set_theme" %}', {}),
    "rich_select": (
        '{% rich_select name="p" options=opts value="a" searchable=True %}',
        {"opts": OPTIONS},
    ),
    "combobox": ('{% combobox name="c" options=opts %}', {"opts": OPTIONS}),
    "combobox_multiple": (
        '{% combobox name="c" options=opts multiple=True selected=sel %}',
        {"opts": OPTIONS, "sel": ["a"]},
    ),
    "modal": ('{% modal open=True title="T" %}<p>x</p>{% endmodal %}', {}),
    "dropdown_menu_items": (
        "{% dropdown_menu open=True items=items %}{% enddropdown_menu %}",
        {"items": TestDropdownMenu.ITEMS},
    ),
    "dropdown_menu_children": (TestDropdownMenu.CHILDREN, {}),
    "copy_button": ('{% copy_button text="x" %}', {}),
    "confirm_dialog": ('{% confirm_dialog open=True message="m" %}', {}),
    "confirm_dialog_block": (TestConfirmDialog.BLOCK, {}),
}


@pytest.mark.parametrize("engine", ["django", "rust"])
@pytest.mark.parametrize("case", sorted(_CSP_CASES))
def test_component_renders_no_inline_handler_or_unnonced_script(engine, case):
    src, ctx = _CSP_CASES[case]
    render = _django if engine == "django" else _rust
    out = render(src, dict(ctx))
    assert out.strip(), f"{case} rendered nothing under {engine}"
    assert_strict_csp_safe(out)


def test_colocated_hook_and_theme_head_under_a_nonce():
    request = RequestFactory().get("/")
    request.csp_nonce = "n0nce"
    hook = _ENGINE.from_string(TestColocatedHook.SRC).render(Context({"request": request}))
    assert 'nonce="n0nce"' in hook
    assert not _INLINE_HANDLER.findall(hook)
    assert_strict_csp_safe(TestCriticalCss()._theme_head(nonce="n0nce"), nonce="n0nce")


# ---------------------------------------------------------------------------
# The remaining components with inline handlers or inline scripts
# ---------------------------------------------------------------------------

KANBAN_COLUMNS = [
    {"id": "todo", "title": "To do", "cards": [{"id": "c1", "title": "One"}]},
    {"id": "done", "title": "Done", "cards": []},
]

_CSP_CASES_REST = {
    "popover": ('{% popover trigger="More" title="T" %}<p>x</p>{% endpopover %}', {}),
    "collapsible": ('{% collapsible trigger="Details" %}<p>x</p>{% endcollapsible %}', {}),
    "popconfirm": (
        '{% popconfirm message="Sure?" %}<button>Delete</button>{% endpopconfirm %}',
        {},
    ),
    "context_menu": (
        '{% context_menu label="Right-click" %}{% context_menu_item label="Edit" event="edit" %}'
        "{% endcontext_menu %}",
        {},
    ),
    "code_block": ('{% code_block code="x = 1" language="python" %}', {}),
    "file_dropzone": ('{% file_dropzone name="files" multiple=True %}', {}),
    "kanban_board": ("{% kanban_board columns=cols %}", {"cols": KANBAN_COLUMNS}),
    "rich_text_editor": ('{% rich_text_editor name="body" %}', {}),
    "multi_select": ('{% multi_select name="tags" options=opts %}', {"opts": OPTIONS}),
}


@pytest.mark.parametrize("engine", ["django", "rust"])
@pytest.mark.parametrize("case", sorted(_CSP_CASES_REST))
def test_remaining_component_renders_no_inline_handler_or_unnonced_script(engine, case):
    src, ctx = _CSP_CASES_REST[case]
    render = _django if engine == "django" else _rust
    out = render(src, dict(ctx))
    assert out.strip(), f"{case} rendered nothing under {engine}"
    assert_strict_csp_safe(out)


@pytest.mark.parametrize("engine", ["django", "rust"])
@pytest.mark.parametrize("case", ["popover", "collapsible", "popconfirm", "context_menu"])
def test_remaining_component_engines_agree_and_are_stable(engine, case):
    src, ctx = _CSP_CASES_REST[case]
    out = _both(src, dict(ctx))
    assert out["django"] == out["rust"], case
    render = _django if engine == "django" else _rust
    assert render(src, dict(ctx)) == render(src, dict(ctx))
    assert not _RANDOM_ID.search(out[engine]), out[engine]


class TestRemainingComponentMarkup:
    def test_popover_trigger_state(self):
        for out in _both('{% popover trigger="More" id="p1" %}x{% endpopover %}').values():
            assert 'id="p1"' in out
            assert re.search(r'class="popover-trigger[^"]*"[^>]*aria-expanded="false"', out)
            assert 'aria-controls="p1-content"' in out
            assert 'id="p1-content"' in out

    def test_collapsible_reflects_open(self):
        for out in _both('{% collapsible trigger="D" open=True %}x{% endcollapsible %}').values():
            assert 'class="collapsible collapsible-open"' in out
            assert 'aria-expanded="true"' in out

    def test_kanban_move_event_on_the_board(self):
        for out in _both(
            '{% kanban_board columns=cols move_event="moved" %}', {"cols": KANBAN_COLUMNS}
        ).values():
            assert 'data-move-event="moved"' in out
            assert 'draggable="true"' in out

    def test_code_block_highlighter_is_an_external_deferred_script(self):
        out = _django('{% code_block code="x = 1" language="python" %}')
        assert re.search(
            r'<script src="[^"]*djust_components/code-block-highlight\.js"[^>]* defer>', out
        ), out

    def test_split_pane_has_no_inline_script(self):
        src = "{% split_pane %}<p>a</p>{% pane %}<p>b</p>{% endsplit_pane %}"
        out = _django(src)
        assert_strict_csp_safe(out)
        assert "sp-handle" in out
        assert out == _django(src)

    def test_tutorial_bubble_has_no_inline_handler(self):
        from djust.templatetags.djust_tutorials import tutorial_bubble

        out = str(tutorial_bubble())
        assert_strict_csp_safe(out)
        assert "dj-tutorial-bubble__skip" in out
