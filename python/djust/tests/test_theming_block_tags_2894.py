"""Block-tag forms of the container components (#2894, #3279).

``{% theme_card_block %}…{% end_theme_card_block %}`` and its siblings put a
body of template tags between an opening and a closing tag. Every case renders
through the real tag, in BOTH engines: Django's, and djust's Rust engine, which
is what renders a LiveView template (the tags reach it through the
``{% load %}`` bridge, ADR-030).
"""

from __future__ import annotations

import re

import pytest
from django.template import TemplateSyntaxError, engines
from django.test import RequestFactory
from django.utils.safestring import mark_safe

pytestmark = pytest.mark.theming

LOAD = "{% load theme_components %}"

# (inline tag, block tag, the inline call that takes the same body, the block args)
CONTAINERS = [
    ("theme_card", "title='T'", "body"),
    ("theme_alert", "variant='info' title='T'", "message"),
    ("theme_modal", "id='m' title='T' is_open=True", "slot_body"),
    ("theme_dropdown", "id='d' label='L' is_open=True", "slot_menu"),
    ("theme_tooltip", "'Help'", "slot_content"),
    ("theme_nav_group", "'G'", "slot_items"),
]


def django_render(source: str, request=None, **ctx) -> str:
    ctx = dict(ctx, **({"request": request} if request is not None else {}))
    return engines["django"].from_string(LOAD + source).render(ctx)


def rust_render(source: str, **ctx) -> str:
    from djust._rust import RustLiveView

    view = RustLiveView(LOAD + "<div dj-root>" + source + "</div>")
    view.update_state(ctx)
    return view.render()


def normalise(html: str) -> str:
    """Drop what legitimately differs between two renders of one template: the
    tooltip's per-render random id, the Rust engine's `{% if %}` VDOM marker
    comments, and whitespace."""
    html = re.sub(r"<!--.*?-->", "", html)
    html = re.sub(r"tooltip-[0-9a-f]{32}", "tooltip-ID", html)
    return re.sub(r"\s+", " ", html).strip()


def inner(html: str) -> str:
    return re.sub(r"^<div dj-root[^>]*>", "", html).removesuffix("</div>")


# ---------------------------------------------------------------------------
# Django engine
# ---------------------------------------------------------------------------


class TestDjangoEngine:
    def test_a_card_body_can_hold_other_component_tags(self):
        html = django_render(
            '{% theme_card_block title="Welcome" %}'
            '<p>hello</p>{% theme_button "Save" variant="primary" dj_click="save" %}'
            "{% end_theme_card_block %}"
        )
        assert 'class="card-body"' in html
        assert "<p>hello</p>" in html
        assert 'dj-click="save"' in html
        assert "Welcome" in html
        # The button sits INSIDE the card body, not after the card.
        assert html.index("card-body") < html.index("<button") < html.rindex("</div>")

    def test_the_body_is_autoescaped_like_any_template_content(self):
        html = django_render(
            "{% theme_card_block %}{{ evil }}{% end_theme_card_block %}",
            evil="<script>alert(1)</script>",
        )
        assert "<script>" not in html
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html

    def test_a_safe_variable_in_the_body_stays_safe(self):
        html = django_render(
            "{% theme_card_block %}{{ ok }}{% end_theme_card_block %}", ok=mark_safe("<b>x</b>")
        )
        assert "<b>x</b>" in html

    def test_body_sees_the_callers_context_and_control_flow(self):
        html = django_render(
            "{% theme_card_block %}{% for r in rows %}"
            "{% if r.on %}<i>{{ r.n }}</i>{% endif %}{% endfor %}{% end_theme_card_block %}",
            rows=[{"on": True, "n": 1}, {"on": False, "n": 2}, {"on": True, "n": 3}],
        )
        assert "<i>1</i>" in html and "<i>3</i>" in html and "<i>2</i>" not in html

    def test_arguments_resolve_in_the_callers_context(self):
        html = django_render(
            "{% theme_card_block title=t footer=f %}x{% end_theme_card_block %}", t="TT", f="FF"
        )
        assert "TT" in html and "FF" in html

    def test_blocks_nest(self):
        html = django_render(
            "{% theme_card_block title='outer' %}"
            "{% theme_card_block title='inner' %}deep{% end_theme_card_block %}"
            "{% end_theme_card_block %}"
        )
        assert html.count('class="card ') == 2
        assert html.index("outer") < html.index("inner") < html.index("deep")

    def test_the_inline_form_is_unchanged(self):
        html = django_render('{% theme_card title="T" body="plain" %}')
        assert "plain" in html and 'class="card-body"' in html

    def test_the_inline_form_still_takes_no_closing_tag(self):
        """The spelling #2894 once documented (`{% end_theme_card %}`) is not
        half-supported: the inline tag stays an inline tag and the closing tag
        is an error that points at the block form, not a body that is silently
        dropped and not a tag that closes."""
        with pytest.raises(TemplateSyntaxError, match="theme_card_block"):
            django_render('{% theme_card title="T" %}x{% end_theme_card %}')
        with pytest.raises(TemplateSyntaxError, match="not a closing tag"):
            django_render("{% theme_alert 'x' %}y{% end_theme_alert %}")

    def test_the_misspelt_closing_tag_is_an_error_in_the_rust_engine_too(self):
        with pytest.raises(Exception, match="theme_card_block"):
            rust_render('{% theme_card title="T" %}x{% end_theme_card %}')

    def test_alert_block_rejects_positional_arguments(self):
        """`theme_alert "x"` binds the MESSAGE; the block fills that, so a
        positional argument would bind `title` instead. Refused, not guessed."""
        with pytest.raises(TemplateSyntaxError, match="pass them as keywords"):
            django_render("{% theme_alert_block 'Heads up' %}x{% end_theme_alert_block %}")

    def test_positional_arguments_before_the_body_match_the_inline_form(self):
        html = django_render("{% theme_card_block 'Title' 'Foot' %}x{% end_theme_card_block %}")
        assert "Title" in html and "Foot" in html

    def test_as_variable_is_refused(self):
        with pytest.raises(TemplateSyntaxError, match="as <variable>"):
            django_render("{% theme_card_block title='x' as out %}y{% end_theme_card_block %}")

    def test_an_unclosed_block_is_a_syntax_error(self):
        with pytest.raises(TemplateSyntaxError, match="end_theme_card_block"):
            django_render("{% theme_card_block %}x")

    @pytest.mark.parametrize(
        "args",
        ["body='x'", "slot_body='x'"],
    )
    def test_the_body_cannot_also_be_passed_as_an_argument(self, args):
        with pytest.raises(TemplateSyntaxError, match="theme_card_block"):
            django_render(f"{{% theme_card_block {args} %}}x{{% end_theme_card_block %}}")

    def test_the_alert_block_does_not_ask_for_a_message(self):
        html = django_render(
            "{% theme_alert_block variant='success' %}saved {{ n }}{% end_theme_alert_block %}", n=3
        )
        assert "alert-success" in html and "saved 3" in html
        with pytest.raises(TemplateSyntaxError, match="theme_alert_block"):
            django_render("{% theme_alert_block message='x' %}y{% end_theme_alert_block %}")

    def test_alert_keeps_its_role_logic(self):
        assert 'role="alert"' in django_render(
            "{% theme_alert_block variant='destructive' %}x{% end_theme_alert_block %}"
        )
        assert 'role="status"' in django_render(
            "{% theme_alert_block variant='info' %}x{% end_theme_alert_block %}"
        )
        with pytest.raises(ValueError, match="role must be one of"):
            django_render("{% theme_alert_block role='bogus' %}x{% end_theme_alert_block %}")

    def test_a_missing_required_argument_is_still_an_error(self):
        with pytest.raises(TemplateSyntaxError, match="theme_modal_block"):
            django_render("{% theme_modal_block %}x{% end_theme_modal_block %}")

    def test_the_body_is_not_padded_with_the_tags_own_line_breaks(self):
        html = django_render(
            "{% theme_tooltip_block 'Help' %}\n  <b>x</b>\n{% end_theme_tooltip_block %}"
        )
        assert "><b>x</b><span" in html

    def test_a_nav_group_block_holds_nav_items(self):
        request = RequestFactory().get("/inbox/")
        html = django_render(
            "{% theme_nav_group_block 'Mail' %}"
            "{% for l, u in links %}{% theme_nav_item l u %}{% endfor %}"
            "{% end_theme_nav_group_block %}",
            request=request,
            links=[("Home", "/"), ("Inbox", "/inbox/")],
        )
        assert html.index("Mail") < html.index("Home") < html.index("Inbox")
        assert html.count("nav-link-label") == 2

    @pytest.mark.parametrize(
        "theme", ["material", "playful", "neo_brutalist", "fluent", "ios", "default"]
    )
    def test_every_theme_override_renders_the_block_body(self, theme):
        """Block forms resolve the same per-theme `card.html` the inline form does."""
        request = RequestFactory().get("/")
        request.COOKIES = {"djust_theme": theme}
        request._djust_theme_manager = None
        block = django_render(
            "{% theme_card_block title='T' %}<i>B</i>{% end_theme_card_block %}", request=request
        )
        request._djust_theme_manager = None
        inline = django_render(
            "{% theme_card title='T' body=b %}", request=request, b=mark_safe("<i>B</i>")
        )
        assert "<i>B</i>" in block
        assert normalise(block) == normalise(inline)


@pytest.mark.parametrize("inline, block_args, body_param", CONTAINERS)
def test_a_block_form_is_its_inline_form_with_the_body_filled(inline, block_args, body_param):
    """Single source of truth: the block form emits exactly the inline form's
    markup, so there is no second copy of a component's template to drift."""
    block = f"{inline}_block"
    via_block = django_render(f"{{% {block} {block_args} %}}<i>BODY</i>{{% end_{block} %}}")
    if body_param == "message":
        inline_args = block_args + " message=b"
    else:
        inline_args = block_args + (f" {body_param}=b")
    via_inline = django_render(f"{{% {inline} {inline_args} %}}", b=mark_safe("<i>BODY</i>"))
    assert "<i>BODY</i>" in via_block
    assert normalise(via_block) == normalise(via_inline)


# ---------------------------------------------------------------------------
# Rust engine (what renders a LiveView template)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("inline, block_args, body_param", CONTAINERS)
def test_every_block_form_renders_in_the_rust_engine_like_django(inline, block_args, body_param):
    block = f"{inline}_block"
    src = f"{{% {block} {block_args} %}}<p>{{{{ who }}}}</p>{{% if on %}}<b>on</b>{{% endif %}}{{% end_{block} %}}"
    rust = rust_render(src, who="<w>", on=True)
    django = django_render(src, who="<w>", on=True)
    assert "&lt;w&gt;" in rust and "<b>on</b>" in rust
    assert normalise(inner(rust)) == normalise(django)


def test_rust_body_holds_nested_component_tags_inside_a_loop():
    src = (
        "{% for r in rows %}{% theme_card_block title=r.t %}"
        "{% if r.show %}<p>{{ r.body }}</p>{% endif %}"
        '{% theme_button "Go" dj_click="go" dj_value_id=r.id %}'
        "{% end_theme_card_block %}{% endfor %}"
    )
    rows = [
        {"t": "A", "show": True, "body": "<x>", "id": 1},
        {"t": "B", "show": False, "body": "n", "id": 2},
    ]
    rust = rust_render(src, rows=rows)
    assert normalise(inner(rust)) == normalise(django_render(src, rows=rows))
    assert rust.count('class="card ') == 2
    assert "<p>&lt;x&gt;</p>" in rust
    assert 'dj-click="go" dj-value-id="1"' in rust and 'dj-click="go" dj-value-id="2"' in rust
    # Row B's `{% if %}` was false: its body holds the button only.
    assert rust.count("<p>") == 1


def test_rust_blocks_nest_and_mix_with_inline_forms():
    src = (
        "{% theme_card_block title='outer' %}"
        "{% theme_alert_block variant='info' %}a{% end_theme_alert_block %}"
        "{% theme_card_block title='inner' %}deep{% end_theme_card_block %}"
        "{% theme_alert 'inline' variant='success' %}"
        "{% end_theme_card_block %}"
    )
    rust = rust_render(src)
    assert normalise(inner(rust)) == normalise(django_render(src))
    assert rust.count('class="card ') == 2 and "alert-success" in rust and "alert-info" in rust


def test_rust_autoescape_off_is_honoured_like_django():
    src = "{% autoescape off %}{% theme_card_block %}{{ v }}{% end_theme_card_block %}{% endautoescape %}"
    rust = rust_render(src, v="<u>x</u>")
    django = django_render(src, v="<u>x</u>")
    assert normalise(inner(rust)) == normalise(django)
    assert "<u>x</u>" in rust


def test_rust_rejects_a_clashing_body_argument_with_a_syntax_error():
    with pytest.raises(Exception, match="theme_card_block"):
        rust_render("{% theme_card_block body='x' %}y{% end_theme_card_block %}")


def test_a_liveview_re_render_patches_the_block_body():
    """The block form works through the LiveView render path, VDOM diff
    included: a state change inside the body reaches the client as a patch."""
    from djust import LiveView

    class CardView(LiveView):
        template = (
            LOAD + "<div dj-root>{% theme_card_block title='T' %}"
            "<p>{{ count }}</p>{% theme_button 'Go' dj_click='go' %}"
            "{% end_theme_card_block %}</div>"
        )

        def mount(self, request, **kwargs):
            self.count = 1

    view = CardView()
    view.mount(None)
    html, _patches, _version = view.render_with_diff(None)
    assert re.search(r"<p[^>]*>1</p>", html) and 'dj-click="go"' in html
    view.count = 2
    html, patches, _version = view.render_with_diff(None)
    assert re.search(r"<p[^>]*>2</p>", html)
    assert patches, "a changed card body must diff to patches"


# ---------------------------------------------------------------------------
# A LiveView page under a base template: `{% extends %}` + `template_name`
#
# The tests above hand the engine a template STRING, which never goes through
# inheritance flattening. The inheritance resolver re-serialised a block tag
# with `{% end{name} %}` rather than its registered end tag, so every block
# form broke in a page that `{% extends %}` a base (the layout of nearly every
# LiveView page) while passing every string-based case.
# ---------------------------------------------------------------------------

BASE = "<html><body><div dj-root>{% block content %}{% endblock %}</div></body></html>"


class _TemplateDir:
    def __init__(self, files: dict):
        import tempfile
        from pathlib import Path

        self._tmpdir = Path(tempfile.mkdtemp())
        for name, src in files.items():
            (self._tmpdir / name).write_text(src)
        self._override = None

    def __enter__(self):
        from django.conf import settings
        from django.test import override_settings

        from djust.utils import clear_template_dirs_cache

        templates = [dict(t) for t in settings.TEMPLATES]
        templates[0] = dict(templates[0])
        templates[0]["DIRS"] = [str(self._tmpdir), *templates[0].get("DIRS", [])]
        self._override = override_settings(TEMPLATES=templates)
        self._override.enable()
        clear_template_dirs_cache()
        return self

    def __exit__(self, *exc):
        import shutil

        from djust.utils import clear_template_dirs_cache

        self._override.disable()
        clear_template_dirs_cache()
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        return False


def _extending_view(name: str):
    from djust import LiveView

    def mount(self, request, **kwargs):
        self.count = 1

    return type(
        f"Extends2894_{name}",
        (LiveView,),
        {"template_name": f"child_{name}.html", "__module__": __name__, "mount": mount},
    )


@pytest.mark.parametrize("inline, block_args, body_param", CONTAINERS)
def test_every_block_form_renders_and_patches_in_a_liveview_that_extends(
    inline, block_args, body_param
):
    block = f"{inline}_block"
    content = f"{{% {block} {block_args} %}}<p>{{{{ count }}}}</p>{{% end_{block} %}}"
    child = (
        "{% extends 'base_2894.html' %}{% load theme_components %}{% block content %}"
        + content
        + "{% endblock %}"
    )
    with _TemplateDir({"base_2894.html": BASE, f"child_{inline}.html": child}):
        view = _extending_view(inline)()
        view.mount(None)
        html, _patches, _version = view.render_with_diff(None)
        assert re.search(r"<p[^>]*>1</p>", html), html
        view.count = 2
        html, patches, _version = view.render_with_diff(None)
    assert re.search(r"<p[^>]*>2</p>", html)
    assert patches, "the changed body must diff to patches"


def test_nested_block_forms_render_in_a_liveview_that_extends():
    child = (
        "{% extends 'base_2894.html' %}{% load theme_components %}{% block content %}"
        "{% theme_card_block title='outer' %}"
        "{% theme_alert_block variant='info' %}{{ count }}{% end_theme_alert_block %}"
        "{% theme_modal_block id='m' title='M' %}in modal{% end_theme_modal_block %}"
        "{% end_theme_card_block %}{% endblock %}"
    )
    with _TemplateDir({"base_2894.html": BASE, "child_nested.html": child}):
        view = _extending_view("nested")()
        view.mount(None)
        html, _patches, _version = view.render_with_diff(None)
    assert html.count('class="card ') == 1 and "alert-info" in html and "in modal" in html
