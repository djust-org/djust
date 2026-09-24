# djust.auth accounts: pluggable backends and a shared page kit (implementation plan)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `djust.auth.accounts`: one setting chooses an account backend (`django`, `allauth` or custom). All backends render through one themed, accessible page kit and are documented in a new guide and ADR-039.

**Architecture:**
- `AccountBackend` (a base class) declares `features`, supplies views under stable `djust_auth:` URL names, and exposes hooks and signals.
- The kit is a layout, pages and `{% load djust_auth %}` components on theme tokens.
- The `django` backend renders the kit's pages.
- The `allauth` backend keeps allauth's views and pages. It skins them through allauth's own extension API (`allauth/layouts/*.html` and `allauth/elements/*.html`) with the same layout and components, and applies secure defaults.

**Tech Stack:** Django 5.x, djust 1.2 (Python side only), django-allauth 65.x (an optional extra), pytest and pytest-django (`demo_project.settings`).

**Spec:** `docs/superpowers/specs/2026-09-24-accounts-backends-design.md` · **ADR:** `docs/adr/039-pluggable-account-backends.md`

## Global Constraints

- **Worktree:** `/Users/tip/Dropbox/online_projects/ai/djust_project/djust-accounts`, branch `feat/accounts-backends`. Never touch the main checkout `../djust` (another session's branch is checked out there).
- **Python environment** (Task 1 builds it): `.venv-wt/bin/python` with `PYTHONPATH=$WT/python:$WT/examples/demo_project`. Every pytest command is `PYTHONPATH=$PWD/python:$PWD/examples/demo_project .venv-wt/bin/python -m pytest …`. Verify first that `import djust` resolves under the worktree; the editable-install trap otherwise silently tests `../djust`.
- **uv:** always run with `UV_INDEX_URL=https://pypi.org/simple`, then check `git diff uv.lock` has no `127.0.0.1:8418` URLs.
- **Opt-in:** nothing changes for projects that don't set `DJUST_CONFIG["ACCOUNTS"]` or include `djust.auth.accounts.urls`. Existing `djust_auth:login/signup/logout` keep working.
- **Stable URL names** (`djust_auth` namespace): `login`, `logout`, `signup`, `verify`, `password_reset`, `password_reset_confirm`, `social_login`.
- **Features vocabulary:** `login`, `logout`, `signup`, `verify_email`, `password_reset`, `social`, `remember_me`.
- **Auth pages are plain HTTP forms, never LiveViews.**
- **No-JS first:** every page works without JavaScript. `djust_auth/auth.js` only enhances.
- **Theme tokens are bare HSL components:** always write `hsl(var(--token))` (the djustlive lesson).
- **Security logging** uses `%s` style; no `print`, no bare `except: pass`, no `mark_safe` on interpolated values (djust CLAUDE.md).
- **Commits:** conventional commits ending with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`. Each `feat`/`fix` adds a `changelog.d/<slug>.<section>.md` fragment; never edit `CHANGELOG.md` directly.
- **Plan-time spec amendments** (record them in the spec in Task 11):
  1. **allauth rendering.** The `allauth` backend skins allauth's own pages through allauth's supported override API (`allauth/layouts/base.html`, `entrance.html` and the `allauth/elements/*.html` field, form, button, provider and alert elements) with the kit's layout and components. It does not re-implement allauth pages as `djust_auth/pages/*`. The spec's "every flow renders `djust_auth/pages/<step>.html`" holds for the `django` and custom backends. allauth pages share the same layout, components, CSS and `auth` context. Reason: allauth pages carry logic (code flows, reauthentication, conditional fields) that copies would drift from.
  2. **`auth` comes from a tag.** The `auth` object is produced by `{% auth_context as auth %}` (no context processor to install). `auth.form` is the view's `form`.
  3. **`BackendRegistry` gains `warn_on_default=True`** (default unchanged), because its production "in-memory fallback" warning is wrong for accounts.
  4. **One extra check.** `djust.auth` must be in `INSTALLED_APPS`, before `allauth`, so the kit's `allauth/*` overrides win. That's check `djust.A106` (error), beyond the spec's six.

## Review Focus

1. **A project that already overrides `account/*.html` or `allauth/elements/*.html` in `DIRS`.** Its templates must still win over djust's (DIRS precede app dirs). Test in Task 8.
2. **`next` pointing off-site** on login and signup, under both backends: it must be ignored. Test in Task 9 (contract suite).
3. **A backend without `signup`.** `auth.links.signup` must be absent, `djust_auth:signup` must not resolve, and the login page must not render "Create an account". Test in Task 9 (the magic-link test backend).
4. **Unknown provider IDs** (a SocialApp for a provider with no built-in icon) must render a generic button, not crash or render empty. Test in Task 4.
5. **`DJUST_TRUSTED_PROXY_COUNT` set to junk** (a string or negative number) while the allauth backend derives `ALLAUTH_TRUSTED_PROXY_COUNT`: it must fail safe to 0 with the existing warning, never raise. Test in Task 7.

---

## File map

| File | Responsibility |
|---|---|
| `python/djust/utils.py` | `BackendRegistry(warn_on_default=…)` |
| `python/djust/auth/accounts/__init__.py` | Public API: `AccountBackend`, `Provider`, `FEATURES`, `get_account_backend`, `reset_account_backend` |
| `python/djust/auth/accounts/base.py` | `AccountBackend` base class and `Provider` |
| `python/djust/auth/accounts/registry.py` | Resolve `DJUST_CONFIG["ACCOUNTS"]` through `BackendRegistry`; reset on `setting_changed` |
| `python/djust/auth/accounts/providers.py` | Provider metadata and icons (moved from `social.py`, without Tailwind) |
| `python/djust/auth/accounts/context.py` | Builds the `auth` object |
| `python/djust/auth/accounts/urls.py` | The single include |
| `python/djust/auth/accounts/backends/django.py` | `DjangoBackend` |
| `python/djust/auth/accounts/backends/allauth.py` | `AllauthBackend`, secure defaults, adapter, signup form, signal bridge |
| `python/djust/auth/signals.py` | `user_signed_up`, `email_verified` |
| `python/djust/auth/templatetags/djust_auth.py` | Component tags plus `auth_context` |
| `python/djust/auth/templates/djust_auth/layouts/auth.html` | Shared layout |
| `python/djust/auth/templates/djust_auth/components/*.html` | Component templates |
| `python/djust/auth/templates/djust_auth/pages/*.html` | Kit pages (`django` and custom backends) |
| `python/djust/auth/templates/djust_auth/{login,signup}.html` | Legacy names (fixes the missing templates) |
| `python/djust/auth/templates/allauth/layouts/{base,entrance}.html`, `allauth/elements/*.html` | allauth skin |
| `python/djust/auth/static/djust_auth/{auth.css,auth.js}` | Kit styles and enhancement |
| `python/djust/auth/apps.py` | `ready()` applies allauth defaults when that backend is configured |
| `python/djust/checks/accounts.py` | Checks `djust.A100`–`A106` |
| `python/djust/theming/templatetags/theme_pages.py` | `theme_login_page` / `register` / `password_*` become wrappers over the kit |
| `python/djust/auth/social.py` | Deprecation shim over `accounts.providers` |
| `python/djust/tests/accounts/` | Tests |
| `docs/website/guides/accounts.md`, `docs/website/guides/authentication.md`, `docs/website/guides/error-codes.md`, `docs/adr/039-…`, `changelog.d/*` | Docs |
| `examples/demo_project/demo_project/{settings,urls}.py` | allauth apps (conditional), `djust.auth`, the accounts demo |

---

### Task 1: Environment, dependency and `BackendRegistry(warn_on_default)`

**Files:**
- Modify: `pyproject.toml` (optional-dependencies `auth-allauth`; dev deps)
- Modify: `uv.lock`
- Modify: `python/djust/utils.py:112-250`
- Test: `python/djust/tests/accounts/__init__.py` (empty) and `python/djust/tests/accounts/test_registry_flag.py`

**Interfaces:** Produces `BackendRegistry(..., warn_on_default: bool = True)`.

- [ ] **Step 1: Build the worktree environment.**

```bash
cd /Users/tip/Dropbox/online_projects/ai/djust_project/djust-accounts
cp ../djust/python/djust/_rust.cpython-312-darwin.so python/djust/ && codesign -s - -f python/djust/_rust.cpython-312-darwin.so
UV_INDEX_URL=https://pypi.org/simple uv venv --python 3.12 .venv-wt
UV_INDEX_URL=https://pypi.org/simple uv export --frozen --all-extras --no-hashes --no-emit-project > /tmp/claude-501/djust-req.txt
UV_INDEX_URL=https://pypi.org/simple uv pip install --python .venv-wt/bin/python -r /tmp/claude-501/djust-req.txt "django-allauth>=65,<66"
PYTHONPATH=$PWD/python:$PWD/examples/demo_project .venv-wt/bin/python -c "import djust, djust._rust, allauth; print(djust.__file__)"
```

Expected: the printed path is under `djust-accounts/python/djust/`. `.venv-wt` must be gitignored: if `git status` shows it, add `/.venv-wt/` to `.git/info/exclude` (not `.gitignore`).

- [ ] **Step 2: Write the failing test.**

```python
# python/djust/tests/accounts/test_registry_flag.py
import logging

from django.test import override_settings

from djust.utils import BackendRegistry


@override_settings(DEBUG=False)
def test_warn_on_default_false_is_silent(caplog):
    reg = BackendRegistry("ACCOUNTS", "django", lambda t, c: object(), name="accounts", warn_on_default=False)
    with caplog.at_level(logging.WARNING):
        reg.get()
    assert "Falling back to in-memory" not in caplog.text


@override_settings(DEBUG=False)
def test_warn_on_default_true_keeps_todays_warning(caplog):
    reg = BackendRegistry("PRESENCE_BACKEND", "memory", lambda t, c: object(), name="presence")
    with caplog.at_level(logging.WARNING):
        reg.get()
    assert "Falling back to in-memory" in caplog.text
```

- [ ] **Step 3: Run it.** `PYTHONPATH=$PWD/python:$PWD/examples/demo_project .venv-wt/bin/python -m pytest python/djust/tests/accounts/test_registry_flag.py -q`. Expected: FAIL, `unexpected keyword argument 'warn_on_default'`.

- [ ] **Step 4: Implement.** In `BackendRegistry.__init__`, add the parameter `warn_on_default: bool = True`, store `self._warn_on_default = warn_on_default`, and change the warning guard to `if self._warn_on_default and not _debug and backend_type == self._default_type:`. Document it in the class docstring's Args.

- [ ] **Step 5: Add the dependency.**
  - In `pyproject.toml`'s `[project.optional-dependencies]`, add `auth-allauth = ["django-allauth>=65.0,<66"]`, and add `"django-allauth>=65.0,<66"` to `dev`.
  - Run `UV_INDEX_URL=https://pypi.org/simple uv lock`, then `grep -c 127.0.0.1 uv.lock`. Expected: `0`.

- [ ] **Step 6: Run the tests.** Both pass: `… -m pytest python/djust/tests/accounts/test_registry_flag.py python/tests -q -k "registry or backend"`.

- [ ] **Step 7: Commit.**

```bash
git add pyproject.toml uv.lock python/djust/utils.py python/djust/tests/accounts/__init__.py python/djust/tests/accounts/test_registry_flag.py
printf -- "- \`BackendRegistry\` accepts \`warn_on_default=False\` for backends whose default isn't an in-memory fallback.\n" > changelog.d/accounts-registry-flag.changed.md && git add changelog.d/accounts-registry-flag.changed.md
git commit -m "feat(auth): allauth optional extra; BackendRegistry can skip the in-memory fallback warning"
```

---

### Task 2: The contract (`AccountBackend`, `Provider`, registry, signals)

**Files:**
- Create: `python/djust/auth/accounts/__init__.py`, `base.py`, `registry.py`, `providers.py`
- Create: `python/djust/auth/signals.py`
- Test: `python/djust/tests/accounts/test_contract_base.py`

**Interfaces:**
- Consumes: `BackendRegistry(warn_on_default=False)` (Task 1).
- Produces:
  - `FEATURES: frozenset[str]`.
  - `@dataclass(frozen=True) Provider(id: str, label: str, login_url: str, icon: str = "")`.
  - `class AccountBackend`, with:
    - `features: frozenset[str]`
    - `name: str`
    - `options: dict`
    - `signup_validators: list[Callable[[HttpRequest, dict], None]]`
    - `__init__(self, options: dict | None = None)`
    - `urlpatterns(self) -> list`, which must be overridden
    - `providers(self, request) -> list[Provider]`, default `[]`
    - `is_open_for_signup(self, request) -> bool`, default `True`
    - `client_ip(self, request) -> str | None`, default from `djust._client_ip`
    - `run_signup_validators(self, request, cleaned_data) -> None`, which raises `ValidationError`
    - `supports(self, feature: str) -> bool`
  - `get_account_backend() -> AccountBackend`, `reset_account_backend() -> None`.
  - Aliases `{"django": "djust.auth.accounts.backends.django.DjangoBackend", "allauth": "djust.auth.accounts.backends.allauth.AllauthBackend"}`.
  - Signals `djust.auth.signals.user_signed_up(sender, request, user)` and `email_verified(sender, request, user, email)`.
  - `providers.icon_for(provider_id) -> str` (inline SVG, `currentColor`, no classes) and `providers.label_for(provider_id, default) -> str`.

- [ ] **Step 1: Write the failing tests.**

```python
# python/djust/tests/accounts/test_contract_base.py
import pytest
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.test import RequestFactory, override_settings

from djust.auth.accounts import FEATURES, AccountBackend, Provider, get_account_backend, reset_account_backend
from djust.auth.accounts.providers import icon_for, label_for


class Dummy(AccountBackend):
    name = "dummy"
    features = frozenset({"login", "logout"})

    def urlpatterns(self):
        return []


@pytest.fixture(autouse=True)
def _reset():
    reset_account_backend()
    yield
    reset_account_backend()


def test_features_vocabulary():
    assert FEATURES == {"login", "logout", "signup", "verify_email", "password_reset", "social", "remember_me"}


def test_default_backend_is_django():
    assert get_account_backend().name == "django"


@override_settings(DJUST_CONFIG={"ACCOUNTS": {"BACKEND": "djust.tests.accounts.test_contract_base.Dummy", "OPTIONS": {"x": 1}}})
def test_dotted_path_backend_with_options():
    b = get_account_backend()
    assert isinstance(b, Dummy) and b.options == {"x": 1}
    assert b.supports("login") and not b.supports("signup")


@override_settings(DJUST_CONFIG={"ACCOUNTS": {"BACKEND": "nope.Missing"}})
def test_unimportable_backend_is_a_clear_error():
    with pytest.raises(ImproperlyConfigured, match="nope.Missing"):
        get_account_backend()


@override_settings(DJUST_CONFIG={"ACCOUNTS": {"BACKEND": "djust.auth.accounts.Provider"}})
def test_non_backend_class_is_rejected():
    with pytest.raises(ImproperlyConfigured, match="AccountBackend"):
        get_account_backend()


def test_settings_change_resets_the_cached_backend():
    first = get_account_backend()
    with override_settings(DJUST_CONFIG={"ACCOUNTS": {"BACKEND": "djust.tests.accounts.test_contract_base.Dummy"}}):
        assert isinstance(get_account_backend(), Dummy)
    assert get_account_backend().name == first.name


def test_unknown_features_are_rejected_at_init():
    class Bad(Dummy):
        features = frozenset({"login", "teleport"})

    with pytest.raises(ImproperlyConfigured, match="teleport"):
        Bad()


def test_signup_validators_run_in_order_and_raise():
    seen = []

    def ok(request, data):
        seen.append("ok")

    def no(request, data):
        raise ValidationError("blocked")

    b = Dummy()
    b.signup_validators = [ok, no]
    with pytest.raises(ValidationError, match="blocked"):
        b.run_signup_validators(RequestFactory().post("/"), {})
    assert seen == ["ok"]


def test_known_and_unknown_provider_metadata():
    assert "<svg" in icon_for("github") and 'class="' not in icon_for("github")
    assert label_for("github", "x") == "GitHub"
    assert icon_for("unknown-idp") == ""
    assert label_for("unknown-idp", "Acme SSO") == "Acme SSO"


def test_provider_is_plain_data():
    p = Provider("github", "GitHub", "/accounts/github/login/")
    assert p.icon == ""
```

- [ ] **Step 2: Run them.** `… -m pytest python/djust/tests/accounts/test_contract_base.py -q`. Expected: collection error, `No module named 'djust.auth.accounts'`.

- [ ] **Step 3: Implement.**

```python
# python/djust/auth/accounts/base.py
"""The account-backend contract (ADR-039)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest

FEATURES = frozenset({"login", "logout", "signup", "verify_email", "password_reset", "social", "remember_me"})


@dataclass(frozen=True)
class Provider:
    """A sign-in provider shown on login and signup pages."""

    id: str
    label: str
    login_url: str
    icon: str = ""


class AccountBackend:
    """Base class for account backends.

    Subclass to extend a built-in backend (override a hook) or implement one
    from scratch (declare ``features`` and return views from ``urlpatterns``).
    """

    name: str = "base"
    features: frozenset[str] = frozenset()
    signup_validators: list[Callable[[HttpRequest, dict], None]] = []

    def __init__(self, options: dict | None = None) -> None:
        unknown = set(self.features) - FEATURES
        if unknown:
            raise ImproperlyConfigured(
                f"{type(self).__name__}.features has unknown feature(s) {sorted(unknown)}; "
                f"known features are {sorted(FEATURES)}."
            )
        self.options = dict(options or {})
        self.signup_validators = list(self.signup_validators) + list(self.options.get("signup_validators", []))

    def supports(self, feature: str) -> bool:
        return feature in self.features

    def urlpatterns(self) -> list:
        raise NotImplementedError(f"{type(self).__name__} must implement urlpatterns()")

    def providers(self, request: HttpRequest) -> list[Provider]:
        return []

    def is_open_for_signup(self, request: HttpRequest) -> bool:
        return True

    def client_ip(self, request: HttpRequest) -> str | None:
        from djust._client_ip import resolve_client_ip

        return resolve_client_ip(request.META.get("HTTP_X_FORWARDED_FOR"), request.META.get("REMOTE_ADDR"))

    def run_signup_validators(self, request: HttpRequest, cleaned_data: dict) -> None:
        for validator in self.signup_validators:
            validator(request, cleaned_data)
```

```python
# python/djust/auth/accounts/registry.py
"""Resolve DJUST_CONFIG["ACCOUNTS"] to a backend instance (BackendRegistry convention)."""

from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured
from django.core.signals import setting_changed
from django.dispatch import receiver
from django.utils.module_loading import import_string

from djust.utils import BackendRegistry

from .base import AccountBackend

ALIASES = {
    "django": "djust.auth.accounts.backends.django.DjangoBackend",
    "allauth": "djust.auth.accounts.backends.allauth.AllauthBackend",
}


def _factory(value, config: dict) -> AccountBackend:
    spec = value if isinstance(value, dict) else {"BACKEND": value}
    path = ALIASES.get(spec.get("BACKEND", "django"), spec.get("BACKEND", "django"))
    try:
        cls = import_string(path)
    except ImportError as exc:
        raise ImproperlyConfigured(f'DJUST_CONFIG["ACCOUNTS"]["BACKEND"] = {path!r} could not be imported: {exc}') from exc
    if not (isinstance(cls, type) and issubclass(cls, AccountBackend)):
        raise ImproperlyConfigured(f"{path!r} is not an AccountBackend subclass.")
    return cls(spec.get("OPTIONS"))


_registry = BackendRegistry("ACCOUNTS", "django", _factory, name="accounts", warn_on_default=False)


def get_account_backend() -> AccountBackend:
    return _registry.get()


def reset_account_backend() -> None:
    _registry.reset()


@receiver(setting_changed)
def _reset_on_settings_change(setting, **kwargs):
    if setting == "DJUST_CONFIG":
        _registry.reset()
```

`providers.py` moves `_PROVIDER_META` out of `djust/auth/social.py`, and adds Microsoft. It removes `class="h-5 w-5"` from every SVG, sets `width="18" height="18" aria-hidden="true"`, and keeps Google's brand fills. Define:

```python
def icon_for(provider_id: str) -> str:
    meta = _PROVIDER_META.get(provider_id)
    return meta[1] if meta else ""


def label_for(provider_id: str, default: str) -> str:
    meta = _PROVIDER_META.get(provider_id)
    return meta[0] if meta else default
```

The Microsoft icon is the four-square logo: `<svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true"><path fill="#F25022" d="M1 1h10v10H1z"/><path fill="#7FBA00" d="M13 1h10v10H13z"/><path fill="#00A4EF" d="M1 13h10v10H1z"/><path fill="#FFB900" d="M13 13h10v10H13z"/></svg>`.

```python
# python/djust/auth/signals.py
"""Backend-neutral account signals (ADR-039). Every backend sends these."""

from django.dispatch import Signal

user_signed_up = Signal()  # sender=backend class, request, user
email_verified = Signal()  # sender=backend class, request, user, email
```

```python
# python/djust/auth/accounts/__init__.py
"""djust accounts: pluggable account backends and a shared page kit (ADR-039)."""

from .base import FEATURES, AccountBackend, Provider
from .registry import get_account_backend, reset_account_backend

__all__ = ["FEATURES", "AccountBackend", "Provider", "get_account_backend", "reset_account_backend"]
```

`djust.auth.accounts.backends.django` doesn't exist yet, so `test_default_backend_is_django` stays red until Task 5. Mark it `@pytest.mark.xfail(strict=True, reason="DjangoBackend lands in Task 5")` now, and remove the mark in Task 5.

- [ ] **Step 4: Run the tests.** Expected: all pass, and the xfail is reported as xfail.

- [ ] **Step 5: Commit.** `feat(auth): AccountBackend contract, provider metadata and account signals`, with changelog fragment `changelog.d/accounts-contract.added.md`: "`djust.auth.accounts`: pluggable account backends (`DJUST_CONFIG["ACCOUNTS"]`), ADR-039."

---

### Task 3: The `auth` context and the component tags

**Files:**
- Create: `python/djust/auth/accounts/context.py`, `python/djust/auth/templatetags/__init__.py`, `python/djust/auth/templatetags/djust_auth.py`
- Create: `python/djust/auth/templates/djust_auth/components/{providers,divider,field,code_input,errors,links}.html`
- Test: `python/djust/tests/accounts/test_components.py`

**Interfaces:**
- Consumes: `get_account_backend()`, `Provider`.
- Produces:
  - `build_auth(request, form=None, step="") -> AuthContext`, where `AuthContext` is a dataclass with `step, form, providers, next, links, features, errors, signup_open`.
  - `links` holds `login`, `signup`, `password_reset` and `logout`, each present only when the feature is supported and its URL name resolves.
  - `next` is host-checked with `url_has_allowed_host_and_scheme`, otherwise `""`.
  - Tags:
    - `{% auth_context as auth %}` (step from the context's `auth_step` variable)
    - `{% auth_providers auth %}`
    - `{% auth_divider label %}`
    - `{% auth_field bound_field %}`
    - `{% auth_code_input bound_field %}`
    - `{% auth_errors form %}`
    - `{% auth_links auth %}`
  - CSS classes are prefixed `dj-auth-`.

- [ ] **Step 1: Write the failing tests.**

```python
# python/djust/tests/accounts/test_components.py
import pytest
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


def test_field_has_label_autocomplete_and_aria():
    f = F(data={"login": "", "password": ""})
    f.is_valid()
    html = render("{% auth_field f.login %}", f=f)
    assert '<label for="id_login"' in html
    assert 'aria-invalid="true"' in html and 'aria-describedby="id_login-error"' in html
    assert 'autocomplete="username"' in html


def test_password_field_gets_a_toggle_and_current_password_autocomplete():
    html = render("{% auth_field f.password %}", f=F())
    assert 'data-dj-auth-toggle' in html and 'autocomplete="current-password"' in html


def test_code_input_is_an_otp_field():
    html = render("{% auth_code_input f.code %}", f=F())
    assert 'inputmode="numeric"' in html and 'autocomplete="one-time-code"' in html


def test_error_summary_is_an_alert():
    f = F(data={})
    f.is_valid()
    f.add_error(None, "That email or password didn't match.")
    html = render("{% auth_errors f %}", f=f)
    assert 'role="alert"' in html and "didn&#x27;t match" in html


def test_providers_render_known_and_unknown():
    auth = type("A", (), {"providers": [Provider("github", "GitHub", "/g/"), Provider("acme", "Acme SSO", "/a/")], "step": "login"})()
    html = render("{% auth_providers auth %}", auth=auth)
    assert "Continue with GitHub" in html and "<svg" in html
    assert "Continue with Acme SSO" in html  # generic button, no crash


def test_providers_say_sign_up_on_the_signup_step():
    auth = type("A", (), {"providers": [Provider("github", "GitHub", "/g/")], "step": "signup"})()
    assert "Sign up with GitHub" in render("{% auth_providers auth %}", auth=auth)


def test_next_off_site_is_dropped():
    req = RequestFactory().get("/accounts/login/", {"next": "https://evil.example/"})
    assert build_auth(req).next == ""
    req = RequestFactory().get("/accounts/login/", {"next": "/dashboard/"})
    assert build_auth(req).next == "/dashboard/"


def test_escaping_in_labels_and_errors():
    auth = type("A", (), {"providers": [Provider("x", "<b>X</b>", "/x/")], "step": "login"})()
    assert "<b>X</b>" not in render("{% auth_providers auth %}", auth=auth)
```

- [ ] **Step 2: Run them.** Expected: FAIL, `'djust_auth' is not a registered tag library`. Add `"djust.auth"` to the demo `INSTALLED_APPS` right after `"djust"` in `examples/demo_project/demo_project/settings.py` if it isn't there (the tag library lives in the app).

- [ ] **Step 3: Implement.**
  - `build_auth(request, form=None, step="")`:
    - reads the backend;
    - builds `providers = backend.providers(request)` when `backend.supports("social")`;
    - builds `links` by trying `reverse("djust_auth:<name>")` for each supported feature and swallowing `NoReverseMatch`;
    - takes `next` from `request.POST` or `request.GET` and keeps it only if `url_has_allowed_host_and_scheme(next, {request.get_host()}, require_https=request.is_secure())`;
    - takes `errors` from `form.non_field_errors()`;
    - sets `signup_open = backend.supports("signup") and backend.is_open_for_signup(request)`.
  - `auth_context` is `@register.simple_tag(takes_context=True)`, returning `build_auth(context.get("request"), context.get("form"), context.get("auth_step", ""))`.
  - The other tags are `inclusion_tag`s rendering the component templates. `auth_field` decides:
    - the autocomplete map: `login|username → username`, `email → email`, `password → current-password`, and `password1|new_password1|password2|new_password2 → new-password`;
    - `is_password` when the widget's `input_type == "password"`.
  - Component templates use `dj-auth-` classes and plain Django escaping. The field template renders `{{ field }}` with the widget attrs updated: copy `field.field.widget.attrs` and add `aria-invalid`, `aria-describedby`, `autocomplete`, `required`. Never use `mark_safe` except for the icon SVG from `providers.icon_for` (static constants).

`field.html`:
```django
<div class="dj-auth-field{% if field.errors %} dj-auth-field--error{% endif %}">
  <label class="dj-auth-label" for="{{ field.id_for_label }}">{{ field.label }}</label>
  <div class="dj-auth-control">{{ widget }}{% if is_password %}<button type="button" class="dj-auth-toggle" data-dj-auth-toggle aria-controls="{{ field.id_for_label }}" aria-label="Show password" hidden>Show</button>{% endif %}</div>
  {% if field.help_text %}<p class="dj-auth-help" id="{{ field.id_for_label }}-help">{{ field.help_text }}</p>{% endif %}
  {% if field.errors %}<p class="dj-auth-error" id="{{ field.id_for_label }}-error">{{ field.errors|join:" " }}</p>{% endif %}
</div>
```
(The toggle is `hidden` until `auth.js` unhides it, because a no-JS page must not show a dead button.)

`providers.html`:
```django
{% if auth.providers %}<div class="dj-auth-providers">{% for p in auth.providers %}<a class="dj-auth-provider" href="{{ p.login_url }}{% if auth.next %}{% if '?' in p.login_url %}&{% else %}?{% endif %}next={{ auth.next|urlencode }}{% endif %}">{{ p.icon_html }}<span>{% if auth.step == "signup" %}Sign up with{% else %}Continue with{% endif %} {{ p.label }}</span></a>{% endfor %}</div>{% endif %}
```
The tag passes each provider as a dict with `icon_html = mark_safe(icon_for(p.id) or p.icon)`. `icon_for` returns only the module's static SVG constants.

- [ ] **Step 4: Run the tests.** Expected: all pass.

- [ ] **Step 5: Commit.** `feat(auth): the auth context and {% load djust_auth %} components`.

---

### Task 4: Layout, CSS, `auth.js` and the kit pages

**Files:**
- Create: `python/djust/auth/templates/djust_auth/layouts/auth.html`
- Create: `python/djust/auth/templates/djust_auth/pages/{login,signup,logout,verify_email,verify_sent,verify_done,password_reset,password_reset_sent,password_reset_confirm,password_reset_done,social_signup,social_error,inactive}.html`
- Create: `python/djust/auth/static/djust_auth/auth.css`, `python/djust/auth/static/djust_auth/auth.js`
- Test: `python/djust/tests/accounts/test_pages.py`

**Interfaces:**
- Consumes: the tags from Task 3.
- Produces:
  - Every page does `{% extends "djust_auth/layouts/auth.html" %}`, sets `{% block title %}`, and fills `{% block card %}`.
  - Layout blocks: `title`, `head`, `brand`, `card`, `aside`, `footer`.
  - Pages expect context: `form`, `auth_step`, and optionally `email`, `providers_only`.

- [ ] **Step 1: Write the failing tests.**

```python
# python/djust/tests/accounts/test_pages.py
import re

import pytest
from django.contrib.auth.forms import AuthenticationForm
from django.template.loader import render_to_string
from django.test import RequestFactory

PAGES = ["login", "signup", "logout", "verify_email", "verify_sent", "verify_done", "password_reset",
         "password_reset_sent", "password_reset_confirm", "password_reset_done", "social_signup", "social_error", "inactive"]


@pytest.mark.django_db
@pytest.mark.parametrize("page", PAGES)
def test_every_page_renders_in_the_layout(page):
    req = RequestFactory().get("/accounts/")
    req.user = type("U", (), {"is_authenticated": False})()
    html = render_to_string(f"djust_auth/pages/{page}.html", {"form": AuthenticationForm(req), "auth_step": page, "email": "a@b.c"}, request=req)
    assert 'class="dj-auth-card"' in html
    assert "djust_auth/auth.css" in html and "djust_auth/auth.js" in html
    assert re.search(r"<title>[^<]+</title>", html)


def test_theme_tokens_are_wrapped_in_hsl():
    import pathlib

    css = (pathlib.Path(__file__).parents[2] / "auth/static/djust_auth/auth.css").read_text()
    bad = re.findall(r"(?<!hsl\()(?<!, )var\(--(?:background|foreground|card|border|primary|primary-foreground|muted|muted-foreground|destructive|ring)\)", css)
    assert bad == []


def test_auth_js_is_small_and_guarded():
    import pathlib

    js = (pathlib.Path(__file__).parents[2] / "auth/static/djust_auth/auth.js").read_text()
    assert len(js.splitlines()) <= 60
    assert "console.log" not in js or "djustDebug" in js
```

- [ ] **Step 2: Run them.** Expected: FAIL, `TemplateDoesNotExist: djust_auth/pages/login.html`.

- [ ] **Step 3: Implement.**

Layout:
```django
{% load static theme_tags djust_auth %}<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{% block title %}Sign in{% endblock %}</title>
  {% theme_head %}
  <link rel="stylesheet" href="{% static 'djust_auth/auth.css' %}">
  <script src="{% static 'djust_auth/auth.js' %}" defer></script>
  {% block head %}{% endblock %}
</head>
<body class="dj-auth-body">
  <header class="dj-auth-top">{% block brand %}<a class="dj-auth-brand" href="/">{{ request.get_host }}</a>{% endblock %}<span class="dj-auth-mode">{% theme_mode_toggle %}</span></header>
  <main class="dj-auth-main">
    <div class="dj-auth-card">{% block card %}{% endblock %}</div>
    {% block aside %}{% endblock %}
  </main>
  <footer class="dj-auth-footer">{% block footer %}{% endblock %}</footer>
</body>
</html>
```

`login.html`:
```django
{% extends "djust_auth/layouts/auth.html" %}{% load djust_auth %}
{% block title %}Sign in{% endblock %}
{% block card %}{% auth_context as auth %}
<h1 class="dj-auth-title">Sign in</h1>
{% auth_providers auth %}{% if auth.providers %}{% auth_divider "or with email" %}{% endif %}
{% auth_errors form %}
<form method="post" class="dj-auth-form" novalidate>{% csrf_token %}
  {% for field in form.visible_fields %}{% auth_field field %}{% endfor %}{% for h in form.hidden_fields %}{{ h }}{% endfor %}
  {% if auth.next %}<input type="hidden" name="next" value="{{ auth.next }}">{% endif %}
  <button class="dj-auth-submit" type="submit">Sign in</button>
</form>
{% auth_links auth %}
{% endblock %}
```

The other pages follow the same shape:
- `signup` has the title "Create your account", button "Create account", `auth_step` "signup", and the "Already have an account?" link through `auth_links`.
- `verify_email` uses `{% auth_code_input form.code %}` when `form.code` exists, otherwise a link-confirm POST button.
- The `*_sent` and `*_done` pages are text plus one action.
- `social_signup` renders `form` (the missing email).
- `social_error` has the heading "Sign-in didn't complete" and a "Try again" link to `auth.links.login`.
- `logout` is a POST form with a "Sign out" button.
- `inactive` reads "This account is inactive".

`novalidate` stays off on signup, reset and verify, so the browser's HTML5 checks give instant feedback. It's used only on login, where the credential error summary is clearer than a browser tooltip.

`auth.css` defines the `dj-auth-*` layout on theme tokens, wrapped as `hsl(var(--x))`:
- a centred 420px card with `hsl(var(--card))` and a 1px `hsl(var(--border))` border;
- inputs with a 44px minimum height and `:focus-visible` rings in `hsl(var(--ring))`;
- a full-width `.dj-auth-submit` in `hsl(var(--primary))` / `hsl(var(--primary-foreground))`;
- provider buttons: bordered, full width, icon then label;
- a divider with lines either side;
- `.dj-auth-error` in `hsl(var(--destructive))`;
- the reveal of the toggle `[hidden]` handled by JS;
- `@media (max-width: 480px)`, where the card goes full-bleed.

`auth.js`:
```javascript
// djust auth kit: progressive enhancement only; pages work without it.
(function () {
  function init() {
    document.querySelectorAll("[data-dj-auth-toggle]").forEach(function (btn) {
      var input = document.getElementById(btn.getAttribute("aria-controls"));
      if (!input) return;
      btn.hidden = false;
      btn.addEventListener("click", function () {
        var show = input.type === "password";
        input.type = show ? "text" : "password";
        btn.textContent = show ? "Hide" : "Show";
        btn.setAttribute("aria-label", show ? "Hide password" : "Show password");
      });
    });
    var firstError = document.querySelector('.dj-auth-form [aria-invalid="true"]');
    if (firstError) firstError.focus();
    document.querySelectorAll('input[autocomplete="one-time-code"]').forEach(function (el) {
      el.addEventListener("paste", function (e) {
        var text = (e.clipboardData || window.clipboardData).getData("text").replace(/\D/g, "");
        if (text) { e.preventDefault(); el.value = text.slice(0, el.maxLength > 0 ? el.maxLength : text.length); }
      });
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
```

- [ ] **Step 4: Run the tests.** Expected: all pass.

- [ ] **Step 5: Commit.** `feat(auth): the account page kit (layout, pages, styles, progressive-enhancement script)`.

---

### Task 5: The `django` backend, the single include, and the legacy templates

**Files:**
- Create: `python/djust/auth/accounts/backends/__init__.py`, `python/djust/auth/accounts/backends/django.py`, `python/djust/auth/accounts/urls.py`
- Create: `python/djust/auth/templates/djust_auth/login.html`, `python/djust/auth/templates/djust_auth/signup.html`
- Modify: `python/djust/auth/views.py` (`SignupView` runs the backend's signup validators and sends `user_signed_up`), `python/djust/tests/accounts/test_contract_base.py` (remove the xfail)
- Test: `python/djust/tests/accounts/test_django_backend.py`, `python/djust/tests/accounts/urls_accounts.py`

**Interfaces:**
- Consumes: `AccountBackend`, `build_auth`, the pages.
- Produces:
  - `DjangoBackend`: `name="django"`, `features={"login","logout","signup","password_reset"}`.
  - `urlpatterns()` returns patterns named `login`, `logout`, `signup`, `password_reset`, `password_reset_done`, `password_reset_confirm` and `password_reset_complete`, using Django's auth views with kit templates.
  - `djust.auth.accounts.urls.urlpatterns = [path("", include((backend.urlpatterns(), "djust_auth")))] + backend.extra_urlpatterns()`. `extra_urlpatterns()` defaults to `[]`; allauth uses it for its non-namespaced routes.

- [ ] **Step 1: Write the failing tests.**

```python
# python/djust/tests/accounts/urls_accounts.py
from django.urls import include, path

urlpatterns = [path("accounts/", include("djust.auth.accounts.urls"))]
```

```python
# python/djust/tests/accounts/test_django_backend.py
import pytest
from django.contrib.auth import get_user_model
from django.test import Client, override_settings
from django.urls import reverse

from djust.auth.accounts import reset_account_backend
from djust.auth.signals import user_signed_up

pytestmark = [pytest.mark.django_db, pytest.mark.urls("djust.tests.accounts.urls_accounts")]


@pytest.fixture(autouse=True)
def _reset():
    reset_account_backend()


def test_stable_names_resolve():
    for name in ("login", "logout", "signup", "password_reset"):
        assert reverse(f"djust_auth:{name}")


def test_login_page_is_the_kit():
    html = Client().get(reverse("djust_auth:login")).content.decode()
    assert "dj-auth-card" in html and 'name="username"' in html and "Create" in html


def test_signup_signs_in_and_sends_the_signal():
    got = []
    user_signed_up.connect(lambda **kw: got.append(kw["user"].username), weak=False, dispatch_uid="t1")
    r = Client().post(reverse("djust_auth:signup"), {"username": "ann", "email": "a@x.io", "password1": "Very-long-pw-9", "password2": "Very-long-pw-9"})
    user_signed_up.disconnect(dispatch_uid="t1")
    assert r.status_code == 302 and got == ["ann"]


def test_signup_validators_can_block():
    from django.core.exceptions import ValidationError

    def no_example(request, data):
        if data.get("email", "").endswith("@example.com"):
            raise ValidationError("Use a real email address.")

    with override_settings(DJUST_CONFIG={"ACCOUNTS": {"BACKEND": "django", "OPTIONS": {"signup_validators": [no_example]}}}):
        r = Client().post(reverse("djust_auth:signup"), {"username": "b", "email": "b@example.com", "password1": "Very-long-pw-9", "password2": "Very-long-pw-9"})
    assert r.status_code == 200 and "Use a real email address." in r.content.decode()
    assert not get_user_model().objects.filter(username="b").exists()


def test_logout_needs_post():
    assert Client().get(reverse("djust_auth:logout")).status_code == 405


def test_off_site_next_is_ignored_after_login():
    get_user_model().objects.create_user("c", "c@x.io", "Very-long-pw-9")
    r = Client().post(reverse("djust_auth:login") + "?next=https://evil.example/", {"username": "c", "password": "Very-long-pw-9"})
    assert r.status_code == 302 and "evil.example" not in r["Location"]


@pytest.mark.urls("djust.auth.urls")
def test_legacy_djust_auth_urls_now_render():
    # Before ADR-039 these raised TemplateDoesNotExist (djust_auth/login.html).
    assert Client().get("/login/").status_code == 200
    assert Client().get("/signup/").status_code == 200
```

- [ ] **Step 2: Run them.** Expected: FAIL, `No module named 'djust.auth.accounts.urls'`.

- [ ] **Step 3: Implement.**
  - `DjangoBackend.urlpatterns()`:
    - `login`: `LoginView.as_view(template_name="djust_auth/pages/login.html", redirect_authenticated_user=True, extra_context={"auth_step": "login"})`
    - `logout`: `djust.auth.views.logout_view`
    - `signup`: `SignupView.as_view(template_name="djust_auth/pages/signup.html", extra_context={"auth_step": "signup"})`
    - `password_reset`: `PasswordResetView(template_name="djust_auth/pages/password_reset.html", success_url=reverse_lazy("djust_auth:password_reset_done"))`
    - `password_reset_done`: `…_sent.html`
    - `password_reset_confirm` (`reset/<uidb64>/<token>/`): `…confirm.html`, with `success_url=reverse_lazy("djust_auth:password_reset_complete")`
    - `password_reset_complete`: `…_done.html`
  - `SignupView.form_valid`:
    - first call `get_account_backend().run_signup_validators(self.request, form.cleaned_data)`;
    - on `ValidationError`, `form.add_error(None, e)` and `return self.form_invalid(form)`;
    - after `login()`, send `user_signed_up.send(sender=type(get_account_backend()), request=self.request, user=user)`.
  - The legacy `djust_auth/login.html` and `signup.html` are one-liners: `{% extends "djust_auth/pages/login.html" %}` and the signup equivalent.
  - `DjustLoginView` gets `extra_context = {"auth_step": "login"}`.

- [ ] **Step 4: Run the tests.** Also remove the Task 2 xfail and run `python/djust/tests/accounts -q`. Expected: all pass.

- [ ] **Step 5: Commit.** `feat(auth): the django account backend and the single accounts include; fix djust.auth's missing templates`. Add a changelog fragment `changelog.d/accounts-missing-templates.fixed.md`: "`djust.auth.urls` login/signup rendered `TemplateDoesNotExist` (`djust_auth/*.html` never shipped)."

---

### Task 6: The `allauth` backend: views, skin and hooks

**Files:**
- Create: `python/djust/auth/accounts/backends/allauth.py`
- Create: `python/djust/auth/templates/allauth/layouts/base.html`, `python/djust/auth/templates/allauth/layouts/entrance.html`
- Create: `python/djust/auth/templates/allauth/elements/{h1,p,form,fields,field,button,button_group,alert,hr,provider_list,provider,panel}.html`
- Modify: `examples/demo_project/demo_project/settings.py` (conditional allauth apps and middleware, and `django.contrib.sites` + `SITE_ID = 1` if allauth is importable; `djust.auth` placed before `allauth`)
- Test: `python/djust/tests/accounts/test_allauth_backend.py`, `python/djust/tests/accounts/urls_accounts_allauth.py`

**Interfaces:**
- Consumes: the contract, the kit components and layout, `build_auth`.
- Produces:
  - `AllauthBackend`: `name="allauth"`, `features={"login","logout","signup","verify_email","password_reset","social","remember_me"}`.
  - `urlpatterns()` returns name aliases: `path("login/", allauth LoginView, name="login")` and likewise for `logout`, `signup`, `verify` (→ `account_email_verification_sent`), `password_reset`, and `social_login` (`<provider>/login/`). They sit on the same paths as allauth's own routes; first match wins and they're the same view.
  - `extra_urlpatterns()` returns `[path("", include("allauth.urls"))]`.
  - `providers(request)` comes from `allauth.socialaccount.adapter.get_adapter().list_apps(request)`, with `label_for`, `icon_for` and `provider.get_login_url(request)`.
  - `DjustAccountAdapter(DefaultAccountAdapter)`: `is_open_for_signup` delegates to the backend.
  - `DjustSignupForm(allauth SignupForm)`: `clean()` runs the backend's signup validators.
  - Signal bridge: allauth's `email_confirmed` → `djust.auth.signals.email_verified`, and allauth's `user_signed_up` → djust's `user_signed_up`.

- [ ] **Step 1: Write the failing tests.**

```python
# python/djust/tests/accounts/urls_accounts_allauth.py
from django.urls import include, path

urlpatterns = [path("accounts/", include("djust.auth.accounts.urls"))]
```

```python
# python/djust/tests/accounts/test_allauth_backend.py
import pytest

pytest.importorskip("allauth")

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import Client, override_settings
from django.urls import reverse

from djust.auth.accounts import reset_account_backend
from djust.auth.signals import email_verified

ALLAUTH = {"ACCOUNTS": {"BACKEND": "allauth"}}
pytestmark = [
    pytest.mark.django_db,
    pytest.mark.urls("djust.tests.accounts.urls_accounts_allauth"),
]


@pytest.fixture(autouse=True)
def _allauth(settings):
    settings.DJUST_CONFIG = ALLAUTH
    reset_account_backend()
    from djust.auth.accounts.backends.allauth import apply_allauth_defaults

    apply_allauth_defaults(settings)


def test_stable_names_and_allauth_names_both_resolve():
    assert reverse("djust_auth:login") == reverse("account_login")
    assert reverse("djust_auth:signup") == reverse("account_signup")


def test_login_is_skinned_by_the_kit():
    html = Client().get(reverse("account_login")).content.decode()
    assert "dj-auth-card" in html and "djust_auth/auth.css" in html
    assert 'name="login"' in html and 'name="remember"' in html  # remember-me offered


def test_signup_then_code_verification_sends_email_verified():
    got = []
    email_verified.connect(lambda **kw: got.append(kw["email"]), weak=False, dispatch_uid="t2")
    c = Client()
    r = c.post(reverse("account_signup"), {"email": "dee@x.io", "username": "dee", "password1": "Very-long-pw-9"})
    assert r.status_code == 302
    code = next(line.strip() for line in mail.outbox[-1].body.splitlines() if line.strip().isalnum() and len(line.strip()) == 6)
    r = c.post(r["Location"], {"code": code})
    email_verified.disconnect(dispatch_uid="t2")
    assert got == ["dee@x.io"]


def test_signup_validators_block_through_allauth():
    from django.core.exceptions import ValidationError

    def nope(request, data):
        raise ValidationError("Signups are paused.")

    with override_settings(DJUST_CONFIG={"ACCOUNTS": {"BACKEND": "allauth", "OPTIONS": {"signup_validators": [nope]}}}):
        reset_account_backend()
        r = Client().post(reverse("account_signup"), {"email": "e@x.io", "username": "e", "password1": "Very-long-pw-9"})
    assert "Signups are paused." in r.content.decode()
    assert not get_user_model().objects.filter(username="e").exists()


def test_verification_link_get_does_not_verify(settings):
    settings.ACCOUNT_EMAIL_VERIFICATION_BY_CODE_ENABLED = False
    from allauth.account.models import EmailAddress, EmailConfirmationHMAC

    u = get_user_model().objects.create_user("f", "f@x.io", "Very-long-pw-9")
    addr = EmailAddress.objects.create(user=u, email="f@x.io", primary=True, verified=False)
    key = EmailConfirmationHMAC(addr).key
    Client().get(reverse("account_confirm_email", args=[key]))
    addr.refresh_from_db()
    assert addr.verified is False  # a mail-scanner prefetch can't verify


def test_logout_get_does_not_log_out():
    u = get_user_model().objects.create_user("g", "g@x.io", "Very-long-pw-9")
    c = Client()
    c.force_login(u)
    c.get(reverse("account_logout"))
    assert "_auth_user_id" in c.session
```

- [ ] **Step 2: Run them.** Expected: FAIL, because `djust.auth.accounts.backends.allauth` doesn't exist.

- [ ] **Step 3: Implement.**

```python
# python/djust/auth/accounts/backends/allauth.py  (key parts)
"""django-allauth account backend: allauth's views, skinned by the djust kit (ADR-039)."""

from django.urls import include, path

from djust._client_ip import _trusted_proxy_count

from ..base import AccountBackend, Provider

SECURE_DEFAULTS = {
    "ACCOUNT_EMAIL_VERIFICATION": "mandatory",
    "ACCOUNT_EMAIL_VERIFICATION_BY_CODE_ENABLED": True,
    "ACCOUNT_CONFIRM_EMAIL_ON_GET": False,
    "ACCOUNT_PASSWORD_RESET_BY_CODE_ENABLED": True,
    "ACCOUNT_LOGOUT_ON_GET": False,
    "ACCOUNT_SESSION_REMEMBER": None,
    "ACCOUNT_SIGNUP_FIELDS": ["email*", "username*", "password1*"],
    "ACCOUNT_ADAPTER": "djust.auth.accounts.backends.allauth.DjustAccountAdapter",
    "ACCOUNT_FORMS": {"signup": "djust.auth.accounts.backends.allauth.DjustSignupForm"},
}


def apply_allauth_defaults(settings) -> list[str]:
    """Set each secure default the project hasn't set itself. Returns the names applied."""
    applied = []
    for name, value in SECURE_DEFAULTS.items():
        if not hasattr(settings, name):
            setattr(settings, name, value)
            applied.append(name)
    if not hasattr(settings, "ALLAUTH_TRUSTED_PROXY_COUNT"):
        settings.ALLAUTH_TRUSTED_PROXY_COUNT = _trusted_proxy_count()  # fail-safe coercion lives there
        applied.append("ALLAUTH_TRUSTED_PROXY_COUNT")
    return applied
```

- `AllauthBackend.options["verification"] == "link"` sets `ACCOUNT_EMAIL_VERIFICATION_BY_CODE_ENABLED=False`, applied in `apply_allauth_defaults` when the project hasn't set it.
- `DjustAccountAdapter.is_open_for_signup(request)` returns `get_account_backend().is_open_for_signup(request)`.
- `DjustSignupForm.clean()` calls `super().clean()`, then `get_account_backend().run_signup_validators(self.request, self.cleaned_data)` (allauth passes `request` to the form's `__init__` in 65.x; keep it as `self.request`). Its `ValidationError`s become non-field errors.
- The signal bridge is connected in `apps.py` `ready()` when allauth is importable: receivers on `allauth.account.signals.email_confirmed(request, email_address)` → `email_verified.send(sender=AllauthBackend, request=request, user=email_address.user, email=email_address.email)`, and on `allauth.account.signals.user_signed_up(request, user)` → `user_signed_up.send(...)`.

Skin templates:
- `allauth/layouts/base.html`: `{% extends "djust_auth/layouts/auth.html" %}`. It maps allauth's `head_title` block into `title`, and renders `{% block content %}` inside `card`. Messages render as `.dj-auth-flash` above the card.
- `allauth/layouts/entrance.html`: `{% extends "allauth/layouts/base.html" %}` with `{% block content %}{% endblock %}`.
- Elements are rewritten with `dj-auth-` classes:
  - `h1` → `.dj-auth-title`
  - `p` → `.dj-auth-text`
  - `form` → `<form class="dj-auth-form">` with the `body` and `actions` slots
  - `fields` iterates `attrs.form` with `{% auth_field field %}`, and `{% auth_code_input %}` when `field.name == "code"`
  - `field` renders one allauth field through the same markup as `components/field.html`, keeping allauth's `attrs.*` (id, name, type, value, required, autocomplete, errors)
  - `button` → `.dj-auth-submit`, or `.dj-auth-secondary` when `attrs.tags` contains `"secondary"` or `"link"`
  - `provider_list`, `provider` → the `dj-auth-provider` markup with `icon_for(attrs.provider.id)` through a tiny filter `{{ attrs.provider.id|auth_provider_icon }}`, added to `djust_auth.py`
  - `alert` → `.dj-auth-alert role=alert`
  - `hr` → the divider
  - `button_group` and `panel` keep their structure with kit classes

Demo settings: when allauth is importable, append `"django.contrib.sites"`, `"allauth"`, `"allauth.account"` and `"allauth.socialaccount"` after `"djust.auth"`. Add `"allauth.account.middleware.AccountMiddleware"` at the end of `MIDDLEWARE`, set `SITE_ID = 1`, and set `EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"` under tests (pytest-django already swaps to locmem).

- [ ] **Step 4: Run the tests.** Run `python/djust/tests/accounts -q`, then `python/tests -q -x` to be sure adding allauth apps breaks no existing test. Expected: all pass. The code-extraction line in the verification test may need allauth's actual code format: read `mail.outbox[-1].body` in a debug run and adjust the parser. The code must still come from the email.

- [ ] **Step 5: Commit.** `feat(auth): the allauth account backend with secure defaults, skinned by the djust kit`, with changelog fragment `changelog.d/accounts-allauth.added.md`.

---

### Task 7: Apply allauth defaults at startup (proxy-IP alignment included)

**Files:**
- Modify: `python/djust/auth/apps.py`
- Test: `python/djust/tests/accounts/test_allauth_defaults.py`

**Interfaces:** Consumes `apply_allauth_defaults(settings) -> list[str]`, `_trusted_proxy_count()`.

- [ ] **Step 1: Write the failing tests.**

```python
# python/djust/tests/accounts/test_allauth_defaults.py
import pytest

pytest.importorskip("allauth")

from djust.auth.accounts.backends.allauth import apply_allauth_defaults


class S:  # a bare settings stand-in
    pass


def test_defaults_fill_only_what_is_unset():
    s = S()
    s.ACCOUNT_EMAIL_VERIFICATION = "optional"
    applied = apply_allauth_defaults(s)
    assert s.ACCOUNT_EMAIL_VERIFICATION == "optional"  # project wins
    assert s.ACCOUNT_CONFIRM_EMAIL_ON_GET is False and s.ACCOUNT_LOGOUT_ON_GET is False
    assert "ACCOUNT_EMAIL_VERIFICATION" not in applied


def test_proxy_count_follows_djust(settings):
    settings.DJUST_TRUSTED_PROXY_COUNT = 2
    s = S()
    apply_allauth_defaults(s)
    assert s.ALLAUTH_TRUSTED_PROXY_COUNT == 2


def test_junk_proxy_count_fails_safe_to_zero(settings, caplog):
    settings.DJUST_TRUSTED_PROXY_COUNT = "lots"
    s = S()
    apply_allauth_defaults(s)
    assert s.ALLAUTH_TRUSTED_PROXY_COUNT == 0


def test_ready_applies_defaults_only_for_the_allauth_backend(settings):
    from django.apps import apps

    settings.DJUST_CONFIG = {"ACCOUNTS": {"BACKEND": "django"}}
    if hasattr(settings, "ACCOUNT_LOGOUT_ON_GET"):
        del settings.ACCOUNT_LOGOUT_ON_GET
    apps.get_app_config("djust_auth").ready()
    assert not hasattr(settings, "ACCOUNT_LOGOUT_ON_GET")
```

- [ ] **Step 2: Run them.** Expected: the last test fails, because `ready()` doesn't consult the config yet.

- [ ] **Step 3: Implement.** In `DjustAuthConfig.ready()`:
  1. Read `get_djust_config().get("ACCOUNTS")`.
  2. If its `BACKEND` (or the whole value, when it's a string) resolves to `"allauth"` or a subclass path of `AllauthBackend`, and allauth is importable, call `apply_allauth_defaults(django.conf.settings)`.
  3. Log at DEBUG level which names were applied, using `%s` formatting.
  4. Connect the signal bridge.

- [ ] **Step 4: Run the tests.** Expected: all pass.

- [ ] **Step 5: Commit.** `feat(auth): apply allauth secure defaults at startup and align its client-IP proxy count with djust`.

---

### Task 8: System checks `djust.A100`–`A106`

**Files:**
- Create: `python/djust/checks/accounts.py`
- Modify: `python/djust/checks/__init__.py` (import it for registration), `docs/website/guides/error-codes.md` (one entry per ID, following the file's existing format)
- Test: `python/djust/tests/accounts/test_checks.py`

**Interfaces:** Produces the check IDs:

| ID | Level | Condition |
|---|---|---|
| A100 | error | backend import fails, or the class isn't an `AccountBackend` |
| A101 | error | `allauth` backend, but allauth or its apps or middleware are missing |
| A102 | warning | `allauth` backend with `DJUST_TRUSTED_PROXY_COUNT == 0` while `USE_X_FORWARDED_HOST` or `SECURE_PROXY_SSL_HEADER` is set |
| A103 | warning | `ACCOUNT_EMAIL_VERIFICATION == "none"` with `DEBUG=False` |
| A104 | error | `allauth.urls` included more than once, or `djust.auth.accounts.urls` more than once |
| A105 | info | a `DIRS` template under `account/` or `allauth/layouts/` still `{% extends "account/base_entrance.html" %}` or `"allauth/layouts/base.html"` without going through the kit |
| A106 | error | `ACCOUNTS` configured but `djust.auth` isn't installed, or is listed after `allauth` |

All of them are silent when `ACCOUNTS` isn't configured.

- [ ] **Step 1: Write the failing tests.**

```python
# python/djust/tests/accounts/test_checks.py
import pytest
from django.test import override_settings

from djust.checks.accounts import check_accounts


def ids(**kw):
    return sorted(m.id for m in check_accounts(None))


def test_silent_when_accounts_is_not_configured(settings):
    settings.DJUST_CONFIG = {}
    assert ids() == []


def test_a100_bad_backend(settings):
    settings.DJUST_CONFIG = {"ACCOUNTS": {"BACKEND": "nope.Missing"}}
    assert "djust.A100" in ids()


def test_a101_allauth_missing_app(settings):
    settings.DJUST_CONFIG = {"ACCOUNTS": {"BACKEND": "allauth"}}
    settings.INSTALLED_APPS = [a for a in settings.INSTALLED_APPS if not a.startswith("allauth")]
    assert "djust.A101" in ids()


def test_a102_proxy_without_trusted_count(settings):
    pytest.importorskip("allauth")
    settings.DJUST_CONFIG = {"ACCOUNTS": {"BACKEND": "allauth"}}
    settings.USE_X_FORWARDED_HOST = True
    settings.DJUST_TRUSTED_PROXY_COUNT = 0
    assert "djust.A102" in ids()
    settings.DJUST_TRUSTED_PROXY_COUNT = 1
    assert "djust.A102" not in ids()


def test_a103_no_verification_in_production(settings):
    pytest.importorskip("allauth")
    settings.DJUST_CONFIG = {"ACCOUNTS": {"BACKEND": "allauth"}}
    settings.DEBUG = False
    settings.ACCOUNT_EMAIL_VERIFICATION = "none"
    assert "djust.A103" in ids()


@override_settings(ROOT_URLCONF="djust.tests.accounts.urls_double_include")
def test_a104_double_include(settings):
    pytest.importorskip("allauth")
    settings.DJUST_CONFIG = {"ACCOUNTS": {"BACKEND": "allauth"}}
    assert "djust.A104" in ids()


def test_a106_djust_auth_missing_or_after_allauth(settings):
    settings.DJUST_CONFIG = {"ACCOUNTS": {"BACKEND": "django"}}
    settings.INSTALLED_APPS = [a for a in settings.INSTALLED_APPS if a != "djust.auth"]
    assert "djust.A106" in ids()


def test_project_dirs_override_still_wins(tmp_path, settings):
    # Review focus 1: DIRS precede app dirs, so a project's element override beats djust's skin.
    (tmp_path / "allauth" / "elements").mkdir(parents=True)
    (tmp_path / "allauth" / "elements" / "h1.html").write_text("<h1 class=project>{% load allauth %}{% slot %}{% endslot %}</h1>")
    settings.TEMPLATES = [{**settings.TEMPLATES[0], "DIRS": [str(tmp_path)]}]
    from django.template.loader import get_template

    assert "class=project" in get_template("allauth/elements/h1.html").template.source
```

Also add `python/djust/tests/accounts/urls_double_include.py`, which contains `path("accounts/", include("djust.auth.accounts.urls"))` and `path("a2/", include("allauth.urls"))`.

- [ ] **Step 2: Run them.** Expected: FAIL, `No module named 'djust.checks.accounts'`.

- [ ] **Step 3: Implement.**
  - `@register("djust") def check_accounts(app_configs, **kwargs)` returns `Error`/`Warning`/`Info` with the IDs above. Each has a one-line `hint` naming the exact fix, for example A102: `Set DJUST_TRUSTED_PROXY_COUNT to the number of reverse proxies in front of Django (e.g. 1 behind ingress-nginx).`
  - URL walking (A104) uses `get_resolver().url_patterns`, recursing into `URLResolver`s and counting `urlconf_module.__name__` for `allauth.urls` and `djust.auth.accounts.urls`.
  - Every branch is wrapped so a check never raises: catch `Exception`, then `logger.exception` and skip that check.

- [ ] **Step 4: Run the tests.** Expected: all pass. Then `… -m pytest python/djust/tests -q -k check` confirms no existing check test regressed.

- [ ] **Step 5: Commit.** `feat(checks): djust.A100-A106 account backend misconfiguration checks`, plus `error-codes.md` entries.

---

### Task 9: The backend contract suite (all backends, including a custom one)

**Files:**
- Create: `python/djust/tests/accounts/magic_backend.py` (the test-only custom backend)
- Create: `python/djust/tests/accounts/test_contract_suite.py`

**Interfaces:** `MagicLinkBackend(AccountBackend)`, with `features={"login","logout"}`. It has one `login` view (POST email → "check your inbox" page rendering `djust_auth/pages/verify_sent.html`) and `logout`.

- [ ] **Step 1: Write the failing tests.**

```python
# python/djust/tests/accounts/test_contract_suite.py
import pytest
from django.test import Client, override_settings
from django.urls import NoReverseMatch, reverse

from djust.auth.accounts import get_account_backend, reset_account_backend

BACKENDS = ["django", "djust.tests.accounts.magic_backend.MagicLinkBackend"]
try:
    import allauth  # noqa: F401

    BACKENDS.append("allauth")
except ImportError:
    pass

pytestmark = [pytest.mark.django_db, pytest.mark.urls("djust.tests.accounts.urls_accounts")]


@pytest.fixture(params=BACKENDS)
def backend(request, settings):
    settings.DJUST_CONFIG = {"ACCOUNTS": {"BACKEND": request.param}}
    reset_account_backend()
    if request.param == "allauth":
        from djust.auth.accounts.backends.allauth import apply_allauth_defaults

        apply_allauth_defaults(settings)
    import importlib

    import djust.auth.accounts.urls as u
    import djust.tests.accounts.urls_accounts as root

    importlib.reload(u)
    importlib.reload(root)
    from django.urls import clear_url_caches

    clear_url_caches()
    return get_account_backend()


def test_login_resolves_and_renders_the_kit(backend):
    html = Client().get(reverse("djust_auth:login")).content.decode()
    assert "dj-auth-card" in html


def test_unsupported_features_have_no_url_and_no_link(backend):
    if backend.supports("signup"):
        pytest.skip("backend supports signup")
    with pytest.raises(NoReverseMatch):
        reverse("djust_auth:signup")
    assert "Create" not in Client().get(reverse("djust_auth:login")).content.decode()


def test_off_site_next_never_rendered_or_followed(backend):
    html = Client().get(reverse("djust_auth:login") + "?next=https://evil.example/").content.decode()
    assert "evil.example" not in html


def test_logout_requires_post(backend):
    assert Client().get(reverse("djust_auth:logout")).status_code in (200, 405)  # allauth shows a confirm page on GET
```

- [ ] **Step 2: Run them.** Expected: FAIL, because `magic_backend` doesn't exist.

- [ ] **Step 3: Implement `MagicLinkBackend`** (about 30 lines):
  - a `login` view whose form is `email = forms.EmailField()`;
  - it renders `djust_auth/pages/login.html` on GET with `auth_step="login"`, and `verify_sent.html` on a valid POST;
  - the `logout` view is `djust.auth.views.logout_view`.

- [ ] **Step 4: Run the tests.** Expected: all pass for every backend in `BACKENDS`.

- [ ] **Step 5: Commit.** `test(auth): the account backend contract suite, run against django, allauth and a custom backend`.

---

### Task 10: Compatibility: `{% theme_*_page %}` wrappers and the `social_auth_providers` deprecation

**Files:**
- Modify: `python/djust/theming/templatetags/theme_pages.py:69-222` (`theme_login_page`, `theme_register_page`, `theme_password_reset_page`, `theme_password_confirm_page`)
- Modify: `python/djust/auth/social.py`
- Create: `python/djust/auth/templates/djust_auth/components/card_only.html`: the card body of a kit page (title, providers, errors, form, links) without the layout, so `{% theme_*_page %}` drops into the caller's own layout as before. Structure: the `{% block card %}` contents of `pages/login.html`, parameterised by `title`, `action`, `submit_label` and `slot_social`.
- Test: `python/djust/tests/accounts/test_compat.py`

- [ ] **Step 1: Write the failing tests.**

```python
# python/djust/tests/accounts/test_compat.py
import warnings

from django.template import Context, Template
from django.test import RequestFactory


def test_theme_login_page_now_posts_django_field_names():
    req = RequestFactory().get("/")
    html = Template('{% load theme_pages %}{% theme_login_page action="/auth/login/" %}').render(Context({"request": req}))
    assert 'name="username"' in html and 'name="password"' in html
    assert 'name="email"' not in html  # the old, non-working field name


def test_social_auth_providers_still_works_and_warns():
    from djust.auth.social import social_auth_providers

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        out = social_auth_providers(RequestFactory().get("/"))
    assert "oauth_providers" in out
    assert any(issubclass(x.category, DeprecationWarning) for x in w)
```

- [ ] **Step 2: Run them.** Expected: FAIL, because the login page posts `name="email"` and nothing warns.

- [ ] **Step 3: Implement.**
  - Each `theme_*_page` builds the matching Django form: `AuthenticationForm`, `UserCreationForm`, `PasswordResetForm`, or `SetPasswordForm(user=None)`.
  - It renders `djust_auth/pages/<page>.html`'s card through `render_to_string("djust_auth/components/card_only.html", …)`, a new component holding just the card body, so it drops into the caller's layout as before.
  - Signatures (`action`, `title`, slots, `attrs`) are preserved: `action` sets the form action, `title` the heading, and `slot_social` renders after the providers.
  - `social_auth_providers` emits `warnings.warn("djust.auth.social.social_auth_providers is deprecated; use {% auth_providers auth %} (ADR-039).", DeprecationWarning, stacklevel=2)` and returns the same dict shape, using `providers.icon_for` (SVGs no longer carry Tailwind classes).

- [ ] **Step 4: Run the tests.** Also run the existing theming tests (`… -m pytest python/djust/tests -q -k "theme_pages or theming"`). Expected: all pass. If an existing test asserted the old `name="email"` field, update it and record a ruling: that field never worked with Django's `AuthenticationForm`.

- [ ] **Step 5: Commit.** `fix(theming): theme_*_page tags render real Django auth forms via the account kit; deprecate social_auth_providers' old format`, with changelog fragments `.fixed.md` and `.deprecated.md`.

---

### Task 11: Documentation, ADR status, demo, and the spec amendment

**Files:**
- Create: `docs/website/guides/accounts.md`
- Modify: `docs/website/guides/authentication.md` (a "Sign-in pages" section linking to accounts.md)
- Modify: `docs/adr/039-pluggable-account-backends.md` (Status: Accepted, with the landing PR)
- Modify: `docs/superpowers/specs/2026-09-24-accounts-backends-design.md` (the plan-time amendments section)
- Modify: `examples/demo_project/demo_project/urls.py` (`path("accounts/", include("djust.auth.accounts.urls"))`)
- Test: `python/djust/tests/accounts/test_docs.py`

- [ ] **Step 1: Write the failing test** (the docs name only things that exist):

```python
# python/djust/tests/accounts/test_docs.py
import importlib
import pathlib
import re

GUIDE = pathlib.Path(__file__).parents[4] / "docs/website/guides/accounts.md"


def test_guide_exists_with_the_required_sections():
    text = GUIDE.read_text()
    for heading in ("## Quick start", "## Choosing a backend", "## The `auth` context", "## Components",
                    "## Overriding templates", "## Writing a backend", "## Hooks and signals",
                    "## Security defaults", "## System checks", "## Migrating"):
        assert heading in text, heading


def test_every_dotted_name_in_the_guide_imports():
    text = GUIDE.read_text()
    for dotted in set(re.findall(r"`(djust\.[a-z_.]+\.[A-Za-z_]+)`", text)):
        module, _, attr = dotted.rpartition(".")
        try:
            assert hasattr(importlib.import_module(module), attr), dotted
        except ModuleNotFoundError:
            importlib.import_module(dotted)  # a module itself


def test_every_tag_in_the_guide_is_registered():
    from djust.auth.templatetags.djust_auth import register

    text = GUIDE.read_text()
    for tag in set(re.findall(r"\{% (auth_[a-z_]+)", text)):
        assert tag in register.tags, tag


def test_every_check_id_is_documented():
    codes = pathlib.Path(__file__).parents[4] / "docs/website/guides/error-codes.md"
    for n in range(100, 107):
        assert f"A{n}" in codes.read_text()
```

- [ ] **Step 2: Run it.** Expected: FAIL, because `accounts.md` doesn't exist.

- [ ] **Step 3: Write the guide.** Use the spec's §1–§4 as the source, with runnable examples:
  - **Quick start:** `pip install "djust[auth-allauth]"`, the `INSTALLED_APPS` order (`djust.auth` before `allauth`), the `DJUST_CONFIG`, the include, and `migrate`.
  - **Choosing a backend:** a feature comparison table.
  - **The `auth` context:** the key table from the spec.
  - **Components:** each tag, with a snippet and its output.
  - **Overriding templates:** the layout blocks, one page, and allauth elements (precedence: `DIRS` → `djust.auth` → `allauth`).
  - **Writing a backend:** the complete `MagicLinkBackend` from Task 9, cleaned up.
  - **Hooks and signals:** `signup_validators` (Turnstile and disposable-domain examples), `is_open_for_signup`, `user_signed_up`, `email_verified`.
  - **Security defaults:** the table from the spec.
  - **System checks:** A100–A106, each with its fix.
  - **Migrating:** from `djust.auth.urls`, from `{% theme_*_page %}`, and from `social_auth_providers`.

  Add the spec amendments section (copy the four amendments from this plan's Global Constraints). Set ADR-039's status to Accepted.

- [ ] **Step 4: Run the full accounts suite and the docs tests.** Expected: all pass. Then run the whole suite in the background with `make test-python-parallel`, using the worktree PYTHONPATH and `.venv-wt` as its `PYTHON`. Don't edit files while it runs.

- [ ] **Step 5: Commit.** `docs(auth): accounts guide, error codes, ADR-039 accepted; demo project mounts accounts`, with a `changelog.d/accounts-guide.documentation.md` fragment.

---

### Task 12: Browser check (demo project)

**Files:** none unless a defect is found. A defect gets a failing test first, then the fix.

- [ ] **Step 1:** Run the demo on its own port (not 8002 if it's taken) with the worktree PYTHONPATH, `DJUST_CONFIG["ACCOUNTS"]={"BACKEND":"allauth"}` and the console email backend.
- [ ] **Step 2:** In Chrome, through `http://localhost:<port>` (the djust-browser MCP is localhost-only), check:
  - `/accounts/login/`, `/accounts/signup/` and the code-verification page after a real signup (read the code from the console log);
  - `/accounts/password/reset/`;
  - dark and light modes;
  - 1280 px and 500 px widths (500 is headless Chrome's minimum; state that);
  - the password toggle shows and hides;
  - pasting a 6-digit code fills the field;
  - focus moves to the first error.
- [ ] **Step 3:** Repeat login and signup with `BACKEND: "django"`.
- [ ] **Step 4:** Save screenshots to the scratchpad for the PR.
