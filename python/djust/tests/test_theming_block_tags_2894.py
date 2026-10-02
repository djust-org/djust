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
        is an error, not a body that is silently dropped."""
        with pytest.raises(TemplateSyntaxError, match="end_theme_card"):
            django_render('{% theme_card title="T" %}x{% end_theme_card %}')

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
