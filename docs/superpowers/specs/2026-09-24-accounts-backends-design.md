# djust.auth accounts — pluggable account backends and a shared page kit

**Status:** approved design (brainstormed 2026-09-24 with the maintainer)
**Decision record:** [ADR-039](../../adr/039-pluggable-account-backends.md)
**First consumer:** djustlive.com (a separate adoption spec follows this release)

## Why

djust has three half-built pieces of account UI:

1. `djust.auth.views` (`SignupView`, `DjustLoginView`, `logout_view`) render
   `djust_auth/login.html` and `djust_auth/signup.html`. **Neither template
   exists**, so `include("djust.auth.urls")` raises `TemplateDoesNotExist`.
2. `djust.theming`'s `{% theme_login_page %}` family (`theming/templatetags/theme_pages.py`)
   is undocumented and not bound to a form: it hard-codes `email`/`password`
   inputs, which Django's `AuthenticationForm` (`username`) does not accept.
3. `djust.auth.social.social_auth_providers` injects provider metadata with
   Tailwind-classed inline SVGs.

Every djust app that needs sign-in (djustlive, djust.org, the starter) builds
its own pages, and they diverge in look, accessibility and security. The goal:
**one well-designed, documented, extensible account feature**, where the
backend (Django's `contrib.auth`, django-allauth, or your own) is a setting.

## Principles

- **Developer first.** One setting and one include give a working, themed
  sign-in. Every extension point is documented with an example.
- **Swap the backend without touching templates; override templates without
  touching the backend.**
- **Secure by default.** The safe settings are the defaults; unsafe ones warn.
- **Opt-in and backward compatible.** Nothing changes until
  `DJUST_CONFIG["ACCOUNTS"]` is set; existing names keep working.
- **Fix gaps upstream.** Where allauth or djust is missing something, the fix
  is filed upstream (issue or PR; allauth items are shown to the maintainer
  first), not patched around locally.

## 1. Architecture and the backend contract

### Configuration

```python
DJUST_CONFIG = {
    "ACCOUNTS": {
        "BACKEND": "allauth",   # "django" | "allauth" | "dotted.path.To.Backend"
        "OPTIONS": {"verification": "code", "remember_me": True},
    },
}
# urls.py — one include, whatever the backend
path("accounts/", include("djust.auth.accounts.urls")),
```

Resolved through djust's existing `BackendRegistry` convention
(`djust.utils.BackendRegistry`, as `PRESENCE_BACKEND` uses).

### `djust.auth.accounts.AccountBackend`

A documented base class with four responsibilities:

| Member | Meaning |
|---|---|
| `features: frozenset[str]` | What the backend supports: `login`, `logout`, `signup`, `verify_email`, `password_reset`, `social`, `remember_me` (room for `mfa` later). Pages and links render only supported features. |
| `urlpatterns() -> list` | Views for each supported flow under **stable names in the `djust_auth` namespace**: `login`, `logout`, `signup`, `verify`, `password_reset`, `password_reset_confirm`, `social_login`. Apps and templates never branch on the backend. |
| rendering | Every flow renders `djust_auth/pages/<step>.html` with one context object, **`auth`** (below). |
| hooks | `signup_validators`, `is_open_for_signup(request)`, `client_ip(request)`; signals `djust.auth.signals.user_signed_up` and `email_verified`, sent the same way by every backend. |

### The `auth` context contract

| Key | Type | Notes |
|---|---|---|
| `auth.step` | str | `login`, `signup`, `verify_email`, `verify_sent`, `password_reset`, … |
| `auth.form` | bound Django form or `None` | always a real form; field names are the backend's |
| `auth.providers` | list of `Provider(id, label, icon, login_url)` | empty unless `social` |
| `auth.next` | str | already host-checked |
| `auth.links` | dict | `login`, `signup`, `password_reset`, … present only when supported |
| `auth.features` | frozenset | from the backend |
| `auth.errors` | list[str] | non-field errors |

The contract is versioned in the guide. Additions are backward compatible.
Removals go through djust's deprecation policy.

### Built-in backends

- **`django`** (the default, for backward compatibility): `contrib.auth`.
  Login, logout, signup, and Django's password-reset views. The existing
  `djust.auth.urls` and views become this backend, gaining their missing templates.
- **`allauth`**: wraps django-allauth's views. It adds email verification (code or
  link), social providers, reset by code, rate limits and remember-me. It points
  allauth's template lookup at the kit and fills `auth` from allauth's forms and
  settings.

### Extending and replacing

- **Extend:** subclass a backend and override a hook, for example
  `class SiteBackend(AllauthBackend): signup_validators = [turnstile, block_disposable]`.
- **Replace:** implement `AccountBackend`. The guide works through a magic-link
  backend (`features={"login", "logout"}`, one view) that renders through the kit.

## 2. The page kit and components

### Pages: `djust_auth/pages/`

- `login`, `signup`, `logout` (the confirm page)
- `verify_email` (code or link), `verify_sent`, `verify_done`
- `password_reset`, `password_reset_sent`, `password_reset_confirm`, `password_reset_done`
- `social_signup` (complete missing details), `social_error`, `inactive`

Each page is short and composes components.

### Layout: `djust_auth/layouts/auth.html`

- Blocks `brand`, `aside` and `footer`, around a centred card.
- `{% theme_head %}` and the theme mode toggle, so every theme pack and
  light/dark work out of the box.
- Projects override the layout, one page, or one block.

### Components: `{% load djust_auth %}`

| Tag | Purpose |
|---|---|
| `{% auth_providers auth %}` | Social buttons with built-in icons (GitHub, Google, GitLab, Microsoft; generic fallback). The label follows the step ("Continue with" / "Sign up with"). |
| `{% auth_divider "or with email" %}` | Divider |
| `{% auth_field form.field %}` | Label, input, help, error. Sets `autocomplete`, `aria-describedby` and `aria-invalid`; password fields get a show/hide button. |
| `{% auth_code_input form.code %}` | One-time code: `inputmode=numeric`, `autocomplete=one-time-code`, paste-friendly |
| `{% auth_errors form %}` | Summary of form-level errors, `role=alert`; focuses the first invalid field |
| `{% auth_links auth %}` | "Forgot password?", "New here?" and similar, only for supported features |

- **No-JS first.** Everything works without JavaScript. One static script,
  `djust_auth/auth.js` (about 30 lines), adds the password toggle, focus on the
  first error, and pasting a whole code at once.
- **Plain HTTP forms, not LiveViews.** Sign-in must set the session cookie on a
  normal request, and CSRF and rate limiting stay simple (recorded in ADR-039).
- **Theming.** Components use the theme's `css_prefix`. The `{% theme_*_page %}`
  tags become thin wrappers over the kit (same signatures, now bound to real
  form fields).
- **Accessibility.** Visible labels (never placeholder-only), fieldsets,
  focus rings, contrast checked against the theme packs.

## 3. Security defaults, system checks and compatibility

### `allauth` backend defaults

djust applies these only when the project hasn't set the allauth setting
itself; an explicit project value always wins.

| Behaviour | allauth setting | djust default |
|---|---|---|
| Verification required | `ACCOUNT_EMAIL_VERIFICATION` | `"mandatory"` |
| Verify by code | `ACCOUNT_EMAIL_VERIFICATION_BY_CODE_ENABLED` | `True` (`OPTIONS["verification"]="link"` → `False`) |
| No verify on GET (mail-scanner prefetch) | `ACCOUNT_CONFIRM_EMAIL_ON_GET` | `False` |
| Reset by code | `ACCOUNT_PASSWORD_RESET_BY_CODE_ENABLED` | `True` |
| Logout needs POST | `ACCOUNT_LOGOUT_ON_GET` | `False` |
| Remember me offered | `ACCOUNT_SESSION_REMEMBER` | `None` (shows the checkbox; honoured) |
| Rate limits | `ACCOUNT_RATE_LIMITS` | allauth's defaults, kept on |
| Client IP behind proxies | `ALLAUTH_TRUSTED_PROXY_COUNT` | **derived from `DJUST_TRUSTED_PROXY_COUNT`** (`djust/_client_ip.py`), so allauth's rate limits and djust agree on the client |
| Safe `next` | allauth's own host check | kept |

### System checks (IDs assigned in the plan, `djust.A0xx` family)

| # | Condition | Level |
|---|---|---|
| 1 | `BACKEND` not importable, or not an `AccountBackend` | error |
| 2 | `allauth` chosen but allauth, its app or its middleware is missing | error |
| 3 | Behind a proxy (forwarded headers seen / `USE_X_FORWARDED_HOST`) with no trusted proxy count: every visitor would share one rate-limit bucket | warning |
| 4 | Email verification `"none"` with `DEBUG=False` | warning |
| 5 | Accounts URLs included twice, or `allauth.urls` also included directly | error |
| 6 | A project template still extends allauth's base instead of the kit layout | info |

### Backward compatibility

- `djust.auth.urls` (`djust_auth:login/signup/logout`), `DjustLoginView`,
  `SignupView` and `logout_view` keep working as the `django` backend, now with
  real templates.
- `social_auth_providers` stays and feeds `auth.providers`. Its old dict format
  (Tailwind-classed SVG) is deprecated with a `DeprecationWarning`.
- The `{% theme_*_page %}` signatures are unchanged.
- The whole feature is opt-in through `ACCOUNTS.BACKEND`.

### Proven by the first consumer

djustlive must be able to express, through hooks alone:

- Cloudflare Turnstile and a disposable-domain block (`signup_validators`);
- a signup gate from a setting (`is_open_for_signup`);
- setting its own `is_verified` flag and sending a welcome email (`email_verified` signal).

A missing hook is added here, not worked around in djustlive.

## 4. Documentation, testing and release

### Documentation

It must pass djust-docs' `make docs-verify`: every documented name exists.

- New guide `docs/website/guides/accounts.md`:
  - a 5-minute quick start;
  - choosing a backend (comparison table);
  - the `auth` contract;
  - every component with examples;
  - overriding the layout, a page or a block;
  - a custom-backend walkthrough (magic link);
  - hooks and signals;
  - the security-defaults table;
  - system checks with fixes;
  - migrating from today's `djust.auth` views and `{% theme_*_page %}`.
- `authentication.md` links to it (authorization and accounts are separate topics).
- ADR-039.
- `changelog.d/` fragments, one per `feat`/`fix`.

### Testing (TDD)

- **Contract suite.** One parametrized suite every backend must pass, run
  against `django`, `allauth` and a minimal custom test backend. The contract is
  what's tested, not each implementation separately.
- **Pages.** Every page renders under the default theme packs, in light and
  dark. Form errors and ARIA attributes are checked.
- **Security.** Verification by POST only, logout by POST only, rate limits
  keyed on the derived client IP, host-checked `next`.
- **System checks.** Each check fires when it should and stays silent when it
  shouldn't.
- **Backward compatibility.** The old `djust_auth:` names resolve and render;
  `theme_*_page` still renders; `social_auth_providers` warns but works.
- **Demo project.** A working accounts demo, checked in a browser.

### Release

- djust's normal pipeline: its own review and CI.
- Targets **djust 1.3** (a new feature under semver). A 1.2.x point release is
  possible, since the feature is fully opt-in, if the maintainer prefers.

## Out of scope

- MFA and passkeys (`allauth.mfa`): the contract reserves an `mfa` feature.
- djustlive's adoption: data migration, brand and CLI/OIDC regressions. That's
  the next spec, written once this ships.

## Risks

- **allauth coupling.** allauth's views and settings shift between majors.
  Mitigations: pin a supported range, run the contract suite in CI against the
  lowest and highest supported allauth versions, and file changes upstream.
- **Template override precedence.** Pointing allauth's lookup at the kit must
  not break projects that already override `account/*.html`; check 6 plus
  documented precedence.
