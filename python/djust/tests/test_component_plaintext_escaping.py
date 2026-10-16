"""Plain text component arguments remain text across theme overrides."""

import re
from pathlib import Path

import pytest
from django.template import Context, Template, loader
from django.test import RequestFactory
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from djust.theming.template_resolver import resolve_component_template


THEMES_DIR = Path(__file__).parents[1] / "theming/templates/djust_theming/themes"
THEMES = ["default", *sorted(path.name for path in THEMES_DIR.iterdir() if path.is_dir())]
PAYLOAD = "<img src=x onerror=alert(1)>"
ESCAPED = "&lt;img src=x onerror=alert(1)&gt;"


def themed_request(theme="default"):
    request = RequestFactory().get("/")
    request.COOKIES = {"djust_theme": theme}
    request._djust_theme_manager = None
    return request


def render(source, theme="default", **values):
    return Template("{% load theme_components djust_pwa %}" + source).render(
        Context({"request": themed_request(theme), **values})
    )


def render_component(component, theme="default", **values):
    """Render a component template directly, as an include or render_to_string does."""
    template = resolve_component_template(themed_request(theme), component)
    return template.render({"css_prefix": "", "attrs": {}, **values})


@pytest.mark.theming
@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("argument", ["title", "footer"])
def test_card_arguments_escape_plain_strings(theme, argument):
    plain = render("{% theme_card " + argument + "=value %}", theme, value=PAYLOAD)
    assert PAYLOAD not in plain
    assert ESCAPED in plain


@pytest.mark.theming
@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("slot", ["slot_header", "slot_body", "slot_footer"])
def test_card_template_slots_escape_plain_strings_and_keep_safe_html(theme, slot):
    plain = render_component("card", theme, **{slot: PAYLOAD})
    safe = render_component("card", theme, **{slot: mark_safe(PAYLOAD)})
    assert PAYLOAD not in plain
    assert ESCAPED in plain
    assert PAYLOAD in safe


@pytest.mark.theming
@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("field", ["content", "label"])
def test_tab_dict_fields_escape_plain_strings_and_keep_safe_html(theme, field):
    tab = {"label": "Tab", "content": "Content"}
    tab[field] = PAYLOAD
    plain = render('{% theme_tabs id="test" tabs=tabs %}', theme, tabs=[tab])
    tab[field] = mark_safe(PAYLOAD)
    safe = render('{% theme_tabs id="test" tabs=tabs %}', theme, tabs=[tab])
    assert PAYLOAD not in plain
    assert ESCAPED in plain
    assert PAYLOAD in safe


@pytest.mark.theming
def test_format_html_card_body_keeps_markup_and_escapes_its_argument():
    html = render_component("card", slot_body=format_html("<em>{}</em>", PAYLOAD))
    assert "<em>" + ESCAPED + "</em>" in html


# Tags that lift ``slot_*`` keywords into the template context on this line.
SLOT_TAGS = [
    ("avatar", "", "slot_image"),
    ("breadcrumb", "items=items", "slot_separator"),
    ("checkbox", '"name"', "slot_label"),
    ("nav", "", "slot_brand"),
    ("nav_group", '"Group"', "slot_label"),
    ("nav_item", '"Item" "/item"', "slot_icon"),
    ("progress", "", "slot_label"),
    ("radio", '"name"', "slot_label"),
    ("select", '"name"', "slot_label"),
    ("sidebar_nav", "", "slot_header"),
    ("textarea", '"name"', "slot_label"),
    ("toast", '"Message"', "slot_message"),
    ("tooltip", '"Tooltip"', "slot_content"),
]

# Templates whose tag does not pass ``slot_*`` through on this line; the slots
# are reached by rendering the template directly (include / render_to_string).
TEMPLATE_SLOTS = [
    ("alert", {"message": "Message"}, "slot_message"),
    ("badge", {"text": "Badge"}, "slot_content"),
    ("button", {"text": "Button"}, "slot_content"),
    ("dropdown", {"id": "id", "label": "Label"}, "slot_trigger"),
    ("input", {"name": "name"}, "slot_label"),
    ("modal", {"id": "id"}, "slot_body"),
    ("pagination", {}, "slot_prev"),
    ("table", {}, "slot_caption"),
]


@pytest.mark.theming
@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("tag,args,slot", SLOT_TAGS)
def test_each_slot_tag_escapes_plain_strings_and_keeps_safe_html(theme, tag, args, slot):
    source = "{% theme_" + tag + " " + args + " " + slot + "=value %}"
    plain = render(source, theme, value=PAYLOAD, items=[("One", "/one"), ("Two", "/two")])
    safe = render(source, theme, value=mark_safe(PAYLOAD), items=[("One", "/one"), ("Two", "/two")])
    assert PAYLOAD not in plain
    assert ESCAPED in plain
    assert PAYLOAD in safe


@pytest.mark.theming
@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("component,values,slot", TEMPLATE_SLOTS)
def test_component_template_slots_escape_plain_strings_and_keep_safe_html(
    theme, component, values, slot
):
    plain = render_component(component, theme, **values, **{slot: PAYLOAD})
    safe = render_component(component, theme, **values, **{slot: mark_safe(PAYLOAD)})
    assert PAYLOAD not in plain
    assert ESCAPED in plain
    assert PAYLOAD in safe


@pytest.mark.theming
def test_all_caller_supplied_slot_templates_use_autoescape():
    templates = Path(__file__).parents[1] / "theming/templates/djust_theming"
    for path in templates.rglob("*.html"):
        if "/gallery/" in str(path) or "/form_templates/" in str(path):
            continue
        source = path.read_text()
        assert not re.search(r"\{\{\s*(?:slot_\w+|tab\.content)\|safe\s*\}\}", source), path


PAGE_DIR = Path(__file__).parents[1] / "theming/templates/djust_theming/pages"
PAGE_SLOTS = [
    (path.name, slot)
    for path in sorted(PAGE_DIR.glob("*.html"))
    for slot in sorted(set(re.findall(r"\{\{\s*(slot_\w+)\s*\}\}", path.read_text())))
]


@pytest.mark.theming
@pytest.mark.parametrize("page,slot", PAGE_SLOTS)
def test_page_slots_escape_plain_strings_and_keep_safe_html(page, slot):
    template = loader.get_template("djust_theming/pages/" + page)
    plain = template.render({slot: PAYLOAD})
    safe = template.render({slot: mark_safe(PAYLOAD)})
    assert PAYLOAD not in plain and ESCAPED in plain
    assert PAYLOAD in safe


@pytest.mark.parametrize("argument", ["sw_url", "scope"])
def test_sw_register_json_cannot_end_script(argument):
    html = render(
        "{% djust_sw_register " + argument + "=value %}",
        value="</script><img src=x onerror=alert(1)>",
    )
    assert html.lower().count("</script>") == 1
    assert "<img" not in html
    assert "\\u003C/script\\u003E" in html


def test_pwa_nonce_is_an_escaped_attribute():
    request = RequestFactory().get("/")
    request.csp_nonce = '"><img src=x onerror=alert(1)>'
    html = Template("{% load djust_pwa %}{% djust_sw_register %}").render(
        Context({"request": request})
    )
    assert 'nonce="&quot;&gt;&lt;img' in html
    assert "<img" not in html


@pytest.mark.theming
def test_form_help_text_renders_unescaped_like_django():
    """help_text is developer configuration; Django's own field templates render
    it unescaped, and the themed field template keeps that parity."""
    from types import SimpleNamespace

    markup = '<a href="/terms/">terms</a>'
    template = loader.get_template("djust_theming/form_templates/django/forms/field.html")
    field = SimpleNamespace(help_text=markup, auto_id="field", errors=[], label="Label")
    plain = template.render({"field": field, "prefix": ""})
    field.help_text = mark_safe(markup)
    safe = template.render({"field": field, "prefix": ""})
    assert markup in plain
    assert markup in safe
