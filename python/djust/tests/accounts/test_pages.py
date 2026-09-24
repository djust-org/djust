import pathlib
import re

import pytest
from django.contrib.auth.forms import AuthenticationForm
from django.template.loader import render_to_string
from django.test import RequestFactory, override_settings

PAGES = [
    "login",
    "signup",
    "logout",
    "verify_email",
    "verify_sent",
    "verify_done",
    "password_reset",
    "password_reset_sent",
    "password_reset_confirm",
    "password_reset_done",
    "social_signup",
    "social_error",
    "inactive",
]
AUTH_DIR = pathlib.Path(__file__).parents[2] / "auth"
DUMMY = {"ACCOUNTS": {"BACKEND": "djust.tests.accounts.test_contract_base.Dummy"}}


@pytest.mark.django_db
@override_settings(DJUST_CONFIG=DUMMY)
@pytest.mark.parametrize("page", PAGES)
def test_every_page_renders_in_the_layout(page):
    req = RequestFactory().get("/accounts/")
    req.user = type("U", (), {"is_authenticated": False})()
    html = render_to_string(
        f"djust_auth/pages/{page}.html",
        {"form": AuthenticationForm(req), "auth_step": page, "email": "a@b.c"},
        request=req,
    )
    assert 'class="dj-auth-card"' in html
    assert "djust_auth/auth.css" in html and "djust_auth/auth.js" in html
    assert re.search(r"<title>[^<]+</title>", html)


def test_theme_tokens_are_wrapped_in_hsl():
    css = (AUTH_DIR / "static/djust_auth/auth.css").read_text()
    bad = re.findall(
        r"(?<!hsl\()(?<!, )var\(--(?:background|foreground|card|border|primary|primary-foreground|muted|"
        r"muted-foreground|destructive|ring|input)\)",
        css,
    )
    assert bad == []


def test_auth_js_is_small_and_quiet():
    js = (AUTH_DIR / "static/djust_auth/auth.js").read_text()
    assert len(js.splitlines()) <= 60
    assert "console.log" not in js


def test_code_paste_keeps_letters():
    # A pasted allauth code ("HQPL-VMXW") must survive: strip whitespace only.
    js = (AUTH_DIR / "static/djust_auth/auth.js").read_text()
    assert "\\D" not in js and "\\s" in js


def test_kit_uses_the_theme_font():
    css = (AUTH_DIR / "static/djust_auth/auth.css").read_text()
    assert "var(--font-sans" in css


def test_kit_styles_the_theme_toggle_in_its_header():
    # djust's {% theme_mode_toggle %} ships an unstyled <button>; the kit's own
    # header must not show a browser-default box.
    css = (AUTH_DIR / "static/djust_auth/auth.css").read_text()
    assert re.search(r"\.dj-auth-mode \.theme-mode-toggle\s*\{[^}]*border-radius", css)
