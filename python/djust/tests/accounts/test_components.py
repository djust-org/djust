from django import forms
from django.template import Context, Template
from django.test import RequestFactory, override_settings

from djust.auth.accounts import Provider
from djust.auth.accounts.context import build_auth


class F(forms.Form):
    login = forms.CharField(label="Email or username")
    password = forms.CharField(widget=forms.PasswordInput)
    code = forms.CharField(label="Code", max_length=6)


def render(src, **ctx):
    return Template("{% load djust_auth %}" + src).render(Context(ctx))


def auth_obj(providers, step="login"):
    return type("A", (), {"providers": providers, "step": step, "next": ""})()


def test_field_has_label_autocomplete_and_aria():
    f = F(data={"login": "", "password": ""})
    f.is_valid()
    html = render("{% auth_field f.login %}", f=f)
    assert '<label class="dj-auth-label" for="id_login"' in html
    assert 'aria-invalid="true"' in html and 'aria-describedby="id_login-error"' in html
    assert 'autocomplete="username"' in html


def test_password_field_gets_a_hidden_toggle_and_current_password_autocomplete():
    html = render("{% auth_field f.password %}", f=F())
    assert "data-dj-auth-toggle" in html and 'autocomplete="current-password"' in html
    assert "hidden" in html  # no-JS: no dead button


def test_code_input_is_an_otp_field():
    html = render("{% auth_code_input f.code %}", f=F())
    assert 'inputmode="numeric"' in html and 'autocomplete="one-time-code"' in html


def test_error_summary_is_an_alert():
    f = F(data={})
    f.is_valid()
    f.add_error(None, "That email or password didn't match.")
    html = render("{% auth_errors f %}", f=f)
    assert 'role="alert"' in html and "didn&#x27;t match" in html


def test_no_error_summary_without_errors():
    assert "role" not in render("{% auth_errors f %}", f=F())


def test_providers_render_known_and_unknown():
    html = render(
        "{% auth_providers auth %}",
        auth=auth_obj([Provider("github", "GitHub", "/g/"), Provider("acme", "Acme SSO", "/a/")]),
    )
    assert "Continue with GitHub" in html and "<svg" in html
    assert "Continue with Acme SSO" in html  # generic button, no crash


def test_providers_say_sign_up_on_the_signup_step():
    html = render(
        "{% auth_providers auth %}", auth=auth_obj([Provider("github", "GitHub", "/g/")], "signup")
    )
    assert "Sign up with GitHub" in html


def test_providers_carry_next():
    a = auth_obj([Provider("github", "GitHub", "/g/?process=login")])
    a.next = "/dashboard/"
    assert "/g/?process=login&amp;next=/dashboard/" in render("{% auth_providers auth %}", auth=a)


@override_settings(
    DJUST_CONFIG={"ACCOUNTS": {"BACKEND": "djust.tests.accounts.test_contract_base.Dummy"}}
)
def test_next_off_site_is_dropped():
    req = RequestFactory().get("/accounts/login/", {"next": "https://evil.example/"})
    assert build_auth(req).next == ""
    req = RequestFactory().get("/accounts/login/", {"next": "/dashboard/"})
    assert build_auth(req).next == "/dashboard/"
    req = RequestFactory().get("/accounts/login/", {"next": "//evil.example/x"})
    assert build_auth(req).next == ""


def test_escaping_in_labels():
    html = render("{% auth_providers auth %}", auth=auth_obj([Provider("x", "<b>X</b>", "/x/")]))
    assert "<b>X</b>" not in html


def test_divider_and_links():
    assert "or with email" in render('{% auth_divider "or with email" %}')
    a = type(
        "A",
        (),
        {"links": {"password_reset": "/r/", "signup": "/s/"}, "signup_open": True, "step": "login"},
    )()
    html = render("{% auth_links auth %}", auth=a)
    assert 'href="/r/"' in html and 'href="/s/"' in html


def test_links_hide_signup_when_closed():
    a = type("A", (), {"links": {"signup": "/s/"}, "signup_open": False, "step": "login"})()
    assert "/s/" not in render("{% auth_links auth %}", auth=a)
