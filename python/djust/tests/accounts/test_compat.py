import warnings

import pytest
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.template import Context, Template
from django.test import RequestFactory, override_settings

DUMMY = {"ACCOUNTS": {"BACKEND": "djust.tests.accounts.test_contract_base.Dummy"}}


def render(src, **ctx):
    return Template("{% load theme_pages %}" + src).render(Context(ctx))


@override_settings(DJUST_CONFIG=DUMMY)
def test_theme_login_page_with_a_form_renders_real_field_names():
    req = RequestFactory().get("/")
    html = render(
        '{% theme_login_page form=form action="/auth/login/" %}',
        request=req,
        form=AuthenticationForm(req),
    )
    assert 'name="username"' in html and 'name="password"' in html
    assert 'name="email"' not in html  # the old, non-working field name
    assert 'action="/auth/login/"' in html and "dj-auth-field" in html


@override_settings(DJUST_CONFIG=DUMMY)
def test_theme_register_page_with_a_form_renders_real_field_names():
    req = RequestFactory().get("/")
    html = render("{% theme_register_page form=form %}", request=req, form=UserCreationForm())
    assert 'name="password1"' in html and 'name="password2"' in html


def test_theme_login_page_without_a_form_is_unchanged():
    html = render("{% theme_login_page %}", request=RequestFactory().get("/"))
    assert 'type="email"' in html  # the themed mock-up is untouched


@pytest.mark.django_db
def test_social_auth_providers_still_works_and_warns():
    from djust.auth.social import social_auth_providers

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        out = social_auth_providers(RequestFactory().get("/"))
    assert "oauth_providers" in out
    assert any(issubclass(x.category, DeprecationWarning) for x in caught)
