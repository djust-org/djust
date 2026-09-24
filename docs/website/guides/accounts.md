---
title: "Accounts: sign-in, sign-up and verification pages"
slug: accounts
section: guides
order: 18
level: intermediate
description: "Themed, accessible account pages with a swappable backend: Django's contrib.auth, django-allauth, or your own."
---

# Accounts

djust ships account pages that work out of the box: sign in, sign up, email verification, password reset, and social sign-in (GitHub, Google and more). They're themed with your djust theme, work in light and dark mode, and work without JavaScript.

The pages are shared. The **backend** behind them is a setting:

- **`django`**: plain `django.contrib.auth`. It covers sign in, sign out, sign up and password reset, with no extra dependency.
- **`allauth`**: [django-allauth](https://docs.allauth.org/). It adds email verification by code, social providers, reset by code, rate limits and "remember me", with secure defaults.
- **Your own**: subclass `djust.auth.accounts.AccountBackend`.

Switching the backend never touches a template, and overriding a template never touches the backend. The design is recorded in [ADR-039](../../adr/039-pluggable-account-backends.md).

This guide covers the account *pages*. For requiring login on LiveViews and checking permissions, see [Authentication & Authorization](authentication.md).

## Quick start

With django-allauth (recommended):

```bash
pip install "djust[auth-allauth]"
```

```python
# settings.py
INSTALLED_APPS = [
    # ...
    "djust",
    "djust.theming",
    "djust.auth",               # before allauth, so djust's page skin wins
    "django.contrib.sites",
    "allauth",
    "allauth.account",
    "allauth.socialaccount",    # only if you want social sign-in
]
MIDDLEWARE = [
    # ...
    "allauth.account.middleware.AccountMiddleware",
]
SITE_ID = 1

DJUST_CONFIG = {
    "ACCOUNTS": {"BACKEND": "allauth"},
}
```

```python
# urls.py
from django.urls import include, path

urlpatterns = [
    path("accounts/", include("djust.auth.accounts.urls")),
    # ...
]
```

Run `python manage.py migrate` and open `/accounts/signup/`. New accounts get a verification code by email, and the address is confirmed when they type it in.

To use plain Django instead, set `"BACKEND": "django"` and leave out the allauth lines. `djust.auth` and `djust.theming` are still required: the layout uses `{% theme_head %}`.

## Choosing a backend

| | `django` | `allauth` |
|---|---|---|
| Sign in / sign out | yes | yes (email or username) |
| Sign up | yes | yes |
| Email verification | no | yes, by code (or link) |
| Password reset | by link | by code |
| Social sign-in | no | yes (GitHub, Google, GitLab, Microsoft, …) |
| Rate limits | no | yes |
| "Remember me" | no | yes |
| Extra dependency | none | `django-allauth` |

The backend is chosen once, when the URLconf loads. Changing `BACKEND` needs a restart, but hooks and options are read on every request.

## The `auth` context

Every account page renders from one object, `auth`, produced by a template tag (no context processor to install):

```django
{% load djust_auth %}
{% auth_context as auth %}
```

| Key | What it is |
|---|---|
| `auth.step` | The page: `login`, `signup`, `verify_email`, `password_reset`, … |
| `auth.form` | The page's Django form (the view's `form`) |
| `auth.providers` | Social providers: each has `id`, `label`, `login_url` |
| `auth.next` | Where to go afterwards, **already host-checked** (off-site values become `""`) |
| `auth.links` | URLs for `login`, `signup`, `logout`, `password_reset`, present only when the backend supports them |
| `auth.features` | What the backend supports, e.g. `{"login", "signup", "social"}` |
| `auth.errors` | The form's non-field errors |
| `auth.signup_open` | Whether sign-up is open right now |

The URL names are the same for every backend: `djust_auth:login`, `djust_auth:logout`, `djust_auth:signup`, `djust_auth:password_reset` and, where supported, `djust_auth:verify`. Link to them without knowing which backend is running:

```django
<a href="{% url 'djust_auth:login' %}?next={{ request.path|urlencode }}">Sign in</a>
```

## Components

Load them with `{% load djust_auth %}`. Each renders plain, escaped HTML that works without JavaScript. `djust_auth/auth.js` adds the enhancements.

- **`{% auth_providers auth %}`**: social sign-in buttons with built-in icons for GitHub, Google, GitLab and Microsoft, and a plain button for anything else. The label reads "Continue with GitHub", or "Sign up with GitHub" on the sign-up page. `next` is carried through.
- **`{% auth_divider "or with email" %}`**: a labelled divider between the provider buttons and the form.
- **`{% auth_field form.password %}`**: a label, the input, help text and the error. It sets `autocomplete` (`username`, `current-password`, `new-password`, …) and `aria-invalid` / `aria-describedby`. Password fields get a Show/Hide button. It's hidden until JavaScript runs, so a page without JavaScript never shows a dead button.
- **`{% auth_code_input form.code %}`**: a one-time-code input with autofill, automatic capitals and paste support. Codes can contain letters (allauth's look like `HQPL-VMXW`), so it doesn't force a numeric keypad.
- **`{% auth_errors form %}`**: the form-level errors in a `role="alert"` box. With JavaScript, the first invalid field gets focus.
- **`{% auth_links auth %}`**: "Forgot password?", "New here? Create an account", "Back to sign in". A link only appears when the backend supports it, and "Create an account" disappears when sign-up is closed.

## Overriding templates

The kit's templates live in `djust.auth`:

- **`djust_auth/layouts/auth.html`**: the layout, with blocks `title`, `head`, `brand`, `messages`, `card`, `aside` and `footer`.
- **`djust_auth/pages/*.html`**: one page per step (`login`, `signup`, `verify_email`, `verify_sent`, `verify_done`, `password_reset`, `password_reset_sent`, `password_reset_confirm`, `password_reset_done`, `social_signup`, `social_error`, `logout`, `inactive`), used by the `django` backend and custom backends.
- **`allauth/layouts/*.html` and `allauth/elements/*.html`**: the allauth skin. allauth keeps its own pages, which carry real logic such as code flows and reauthentication. djust restyles them through allauth's supported override points, so every allauth page, including ones this guide never mentions, gets the kit's look.

Templates are found in this order: your `TEMPLATES["DIRS"]`, then `djust.auth`, then `allauth`. To add your logo, override one block:

```django
{# templates/djust_auth/layouts/auth.html #}
{% extends "djust_auth/layouts/auth.html" %}
{% block brand %}<a href="/"><img src="/static/logo.svg" alt="Acme" height="28"></a>{% endblock %}
```

Override a single page the same way, for example `templates/djust_auth/pages/login.html`. Classes are prefixed `dj-auth-`, and colours come from your theme's tokens, so a theme change restyles the pages.

## Writing a backend

A backend declares what it supports and returns its views under the stable names. This is a complete backend that signs people in with an emailed link (the email itself is left out):

```python
from django import forms
from django.shortcuts import render
from django.urls import path

from djust.auth.accounts import AccountBackend


class MagicLinkForm(forms.Form):
    email = forms.EmailField(label="Email")


def magic_login(request):
    form = MagicLinkForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        # send a signed, expiring link to form.cleaned_data["email"] here
        return render(request, "djust_auth/pages/verify_sent.html",
                      {"auth_step": "verify_sent", "email": form.cleaned_data["email"]})
    return render(request, "djust_auth/pages/login.html", {"form": form, "auth_step": "login"})


class MagicLinkBackend(AccountBackend):
    name = "magic-link"
    features = frozenset({"login", "logout"})

    def urlpatterns(self):
        from djust.auth.views import logout_view

        return [
            path("login/", magic_login, name="login"),
            path("logout/", logout_view, name="logout"),
        ]
```

```python
DJUST_CONFIG = {"ACCOUNTS": {"BACKEND": "myproject.auth.MagicLinkBackend"}}
```

Because `signup` isn't in `features`, `djust_auth:signup` doesn't exist and no page shows "Create an account". Other members you can override:

- `providers(request)`: return a list of `djust.auth.accounts.Provider`;
- `is_open_for_signup(request)`;
- `client_ip(request)`;
- `extra_urlpatterns()`: routes mounted outside the `djust_auth` namespace.

To change a built-in backend a little, subclass it instead: `djust.auth.accounts.backends.django.DjangoBackend` or `djust.auth.accounts.backends.allauth.AllauthBackend`.

## Hooks and signals

**`signup_validators`** runs before any account is created, with any backend. Raise `ValidationError` to refuse a signup; the message appears at the top of the form.

```python
from django.core.exceptions import ValidationError


def block_disposable(request, data):
    if data.get("email", "").lower().endswith("@mailinator.com"):
        raise ValidationError("Please use a permanent email address.")


def require_turnstile(request, data):
    if not verify_turnstile(request.POST.get("cf-turnstile-response", ""), request):
        raise ValidationError("Please complete the challenge.")


DJUST_CONFIG = {
    "ACCOUNTS": {
        "BACKEND": "allauth",
        "OPTIONS": {"signup_validators": [block_disposable, require_turnstile]},
    },
}
```

(`verify_turnstile` stands for your own captcha check.)

**`is_open_for_signup(request)`**: subclass the backend and return `False` to close sign-up, for example behind a setting. With the allauth backend this also closes social sign-up.

**Signals.** These are sent the same way by every backend:

- `djust.auth.signals.user_signed_up`, with `request` and `user`;
- `djust.auth.signals.email_verified`, with `request`, `user` and `email`.

```python
from django.dispatch import receiver

from djust.auth.signals import email_verified


@receiver(email_verified)
def welcome(sender, request, user, email, **kwargs):
    send_welcome_email(user, email)
```

## Security defaults

With `"BACKEND": "allauth"`, djust applies these allauth settings at startup. **Any of them you set yourself wins.**

| Setting | djust default | Why |
|---|---|---|
| `ACCOUNT_EMAIL_VERIFICATION` | `"mandatory"` | Nobody uses an address they don't own |
| `ACCOUNT_EMAIL_VERIFICATION_BY_CODE_ENABLED` | `True` | No link for mail scanners to follow; works on another device |
| `ACCOUNT_CONFIRM_EMAIL_ON_GET` | `False` | Link mode: a scanner's GET prefetch can't verify an account |
| `ACCOUNT_PASSWORD_RESET_BY_CODE_ENABLED` | `True` | Same reasons as verification |
| `ACCOUNT_LOGOUT_ON_GET` | `False` | A crafted link can't sign someone out |
| `ACCOUNT_SESSION_REMEMBER` | `None` | Shows "Remember me" and honours it |
| `ACCOUNT_LOGIN_METHODS` | `{"email", "username"}` | Sign in with either |
| `ACCOUNT_SIGNUP_FIELDS` | email, username, password | One password field; the Show button replaces "confirm password" |
| `ACCOUNT_ADAPTER` | djust's adapter | Sign-up gate from the backend; strict redirects (below) |
| `ACCOUNT_FORMS` | djust's sign-up form | Runs your `signup_validators` |

Two more behaviours:

- **Redirects stay on your site.** allauth normally treats every host your `ALLOWED_HOSTS` accepts as a safe `?next=` target. So `ALLOWED_HOSTS = ["*"]`, or a wildcard such as `.example.app` whose subdomains users control, turns `next` into an open redirect. djust's adapter (`djust.auth.accounts.backends.allauth.DjustAccountAdapter`) allows only the current host, plus any hosts you list in `OPTIONS["redirect_hosts"]`.
- **Rate limits see the real client.** allauth's `ALLAUTH_TRUSTED_PROXY_COUNT` is set from `DJUST_TRUSTED_PROXY_COUNT`, so allauth and djust agree on the visitor's IP behind a proxy. Check A102 warns if you run behind a proxy with neither set.

Options (`DJUST_CONFIG["ACCOUNTS"]["OPTIONS"]`):

| Option | Effect |
|---|---|
| `verification` | `"code"` (default) or `"link"` |
| `signup_validators` | List of `(request, data)` callables |
| `redirect_hosts` | Extra hosts `next` may point to |

## System checks

Account checks are silent unless `DJUST_CONFIG["ACCOUNTS"]` is set. Details and fixes are in [Error codes](error-codes.md).

| ID | Level | Meaning |
|---|---|---|
| A100 | Error | The backend can't be imported, or isn't an `AccountBackend` |
| A101 | Error | allauth backend, but allauth isn't installed or its apps or middleware are missing |
| A102 | Warning | Behind a proxy with no trusted proxy count: every visitor shares one rate limit |
| A103 | Warning | Email verification is off in production |
| A104 | Error | Account URLs are included twice |
| A105 | Info | A template override extends allauth's base instead of the kit layout |
| A106 | Error | `djust.auth` or `djust.theming` is missing, or `djust.auth` comes after `allauth` |

## Migrating

**From `djust.auth.urls`.** `include("djust.auth.urls")` keeps working as the `django` backend, and its pages now render instead of raising `TemplateDoesNotExist`. To get the full kit and the stable names, include `djust.auth.accounts.urls` instead.

**From `{% theme_login_page %}` and friends.** These tags now accept `form=`. Pass a real form and they render the kit's card with that form's field names:

```django
{% load theme_pages %}
{% theme_login_page form=form action="/accounts/login/" %}
```

Without `form=`, they render the old themed mock-up, whose `email` input doesn't match Django's login form.

**From `djust.auth.social.social_auth_providers`.** This context processor still works but is deprecated. Use `{% auth_providers auth %}`, whose `auth.providers` holds the same information.
