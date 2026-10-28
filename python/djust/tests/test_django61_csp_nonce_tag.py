"""The Django 6.1 built-in CSP nonce tag behaves alike on both engines."""

import django
import pytest
from django.forms import Media
from django.template import Context, Engine

from djust._rust import has_tag_handler, render_template

requires_django61 = pytest.mark.skipif(
    django.VERSION < (6, 1), reason="CSP nonce tag requires Django 6.1"
)


def test_registration_follows_installed_django_builtin():
    from django.template.defaulttags import register

    assert has_tag_handler("csp_nonce_attr") == ("csp_nonce_attr" in register.tags)


@requires_django61
@pytest.mark.parametrize("nonce", [None, "abc123", '<bad"value>'])
def test_nonce_attribute_matches_django_and_escapes(nonce):
    source = "<script {% csp_nonce_attr %}></script>"
    context = {"csp_nonce": nonce}
    expected = Engine().from_string(source).render(Context(context))
    assert render_template(source, context) == expected


@requires_django61
def test_nonce_attribute_as_variable_is_safe():
    source = "{% csp_nonce_attr as result %}{{ result }}"
    context = {"csp_nonce": 'quoted"value'}
    expected = Engine().from_string(source).render(Context(context))
    assert render_template(source, context) == expected


@requires_django61
@pytest.mark.parametrize("nonce", [None, "abc123"])
def test_nonce_is_applied_to_django_form_media(nonce):
    source = "{% csp_nonce_attr media %}"
    context = {"csp_nonce": nonce, "media": Media(js=["/path/to/js"])}
    expected = Engine().from_string(source).render(Context(context))
    assert render_template(source, context) == expected


@requires_django61
def test_lazy_nonce_is_resolved_and_reused():
    from django.utils.csp import LazyNonce

    nonce = LazyNonce()
    assert not nonce
    html = render_template("{% csp_nonce_attr %}", {"csp_nonce": nonce})
    assert nonce
    assert html == f'nonce="{nonce}"'


@requires_django61
def test_conflicting_media_nonce_fails():
    from django.forms.widgets import Script

    media = Media(js=[Script("/path/to/js", nonce="static")])
    with pytest.raises(ValueError, match="conflicting attributes: nonce"):
        render_template("{% csp_nonce_attr media %}", {"media": media, "csp_nonce": "abc"})


@requires_django61
def test_liveview_render_uses_the_escaped_nonce():
    from djust._rust import RustLiveView

    view = RustLiveView("<div><script {% csp_nonce_attr %}></script></div>")
    view.update_state({"csp_nonce": 'bad"value'})
    html, _, _ = view.render_with_diff()
    assert 'nonce="bad&amp;quot;value"' not in html
    assert 'nonce="bad&quot;value"' in html
