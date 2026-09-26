# ADR-039: Account flows go through a swappable backend; the pages are shared

**Status**: Accepted — shipped (plan-time amendments are in the spec)
**Shipped in**: v1.3.0rc1 (#3067); follow-ups in v1.3.0rc2 (#3076: the allauth skin keeps the layout's head block, the phone flash gutter, A102 accepts an adapter that overrides `get_client_ip`)
**Date**: 2026-09-24
**Deciders**: Project maintainers
**Spec**: [docs/superpowers/specs/2026-09-24-accounts-backends-design.md](../superpowers/specs/2026-09-24-accounts-backends-design.md)
**Related**:
- `djust.auth.views` / `djust.auth.urls`: today's login and signup views, whose templates (`djust_auth/*.html`) do not exist
- `djust.theming.templatetags.theme_pages`: undocumented `{% theme_*_page %}` tags, not bound to a form
- `djust.auth.social.social_auth_providers`: provider metadata with Tailwind-classed icons
- `djust.utils.BackendRegistry`: the backend convention this follows (`PRESENCE_BACKEND`)
- `djust/_client_ip.py` (`DJUST_TRUSTED_PROXY_COUNT`): the client-IP source the `allauth` backend feeds to allauth

---

## Summary (plain language first)

Every djust app that lets people sign in builds its own login, signup,
verification and reset pages. djust's own pieces don't add up: its views point
at templates that don't exist, and its themed page tags aren't wired to real
forms. Apps diverge in look, accessibility and security, and djustlive rebuilt
it all by hand.

We decided to split an account system into two parts. **Flows** (logging in,
signing up, verifying an email, resetting a password, social login) belong to a
**backend** chosen by one setting, Django-style. **Pages** belong to one shared,
themed, accessible kit that every backend renders through a small, documented
context object. You can change the backend without touching a template, and
override a template without touching the backend.

djust ships two backends: `django` (plain `contrib.auth`, the backward-compatible
default) and `allauth` (verification, social, reset by code, rate limits),
plus a base class for writing your own.

## Context

- **Demand.** djustlive.com, djust.org and the starter all need sign-in.
  djustlive's hand-built pages grew rate limits, Turnstile, a disposable-domain
  block and POST-only verification (after a 2026-07 signup-spam flood where
  mail scanners auto-verified accounts via GET). None of that is reusable today.
- **Existing partial pieces** (see Related) prove there's intent but no design,
  and one is broken outright.
- **allauth** is the de facto Django account library. It already solves the hard
  parts (verification, social, codes, rate limits), but its templates are
  unstyled and its security-relevant defaults need care. The key example is
  client-IP detection behind a proxy: without it, every visitor shares one
  rate-limit bucket.

## Decision

1. **Setting:** `DJUST_CONFIG["ACCOUNTS"] = {"BACKEND": <alias or dotted path>, "OPTIONS": {...}}`,
   resolved through `BackendRegistry`. Opt-in: nothing changes until it's set.
2. **Contract:** `djust.auth.accounts.AccountBackend`
   - declares `features`;
   - provides views under **stable `djust_auth:` URL names**;
   - renders `djust_auth/pages/<step>.html` with an `auth` context object
     (`step`, `form`, `providers`, `next`, `links`, `features`, `errors`);
   - exposes hooks (`signup_validators`, `is_open_for_signup`, `client_ip`) and
     backend-neutral signals (`user_signed_up`, `email_verified`).
3. **Shared page kit:** a layout, pages and `{% load djust_auth %}` components,
   themed through `css_prefix` and `{% theme_head %}`. They work without
   JavaScript, with one small progressive-enhancement script.
4. **Auth pages are plain HTTP forms, not LiveViews.** Sign-in must set the
   session cookie on a normal request. CSRF, rate limiting and redirects stay in
   Django's well-trodden path, and nothing here benefits from a WebSocket.
5. **Secure defaults in the `allauth` backend**, applied only where the project
   hasn't set allauth's setting itself:
   - mandatory verification, by code and by POST only;
   - reset by code;
   - logout by POST;
   - rate limits kept on, with `ALLAUTH_TRUSTED_PROXY_COUNT` derived from
     `DJUST_TRUSTED_PROXY_COUNT`.
6. **System checks** catch misconfigurations: backend import, allauth
   installation, proxy client IP, verification off in production, duplicate URL
   includes, stale allauth template overrides.
7. **Backward compatible.** Today's `djust.auth` views and names become the
   `django` backend (fixing the missing templates); `{% theme_*_page %}` wraps the
   kit; `social_auth_providers`' old format is deprecated, not removed.

## Options considered

- **A. Each backend owns flows *and* templates.** Simplest to write a backend,
  but every backend re-implements the UI and themes diverge, the problem we're
  solving. Rejected.
- **B. Thin adapter over allauth only** (djust always uses allauth underneath and
  the "backend" answers a few questions). Least code, but not replaceable, and it
  forces allauth on projects that only need `contrib.auth`. Rejected.
- **C. LiveView auth pages** (live validation while typing). Login still needs
  an HTTP round trip for the cookie. Rate limits and captcha would move into
  WebSocket handlers, re-proving security code for a small UX gain. Rejected;
  inline HTML5 validation and server errors cover it.
- **D. Flows in a backend, shared UI** (chosen). One contract, one kit, two
  built-in backends, documented extension.

## Consequences

- **Positive.** One setting and one include give a themed, accessible sign-in.
  Security defaults come from djust, not each app. djustlive's hardening becomes
  hooks any app can reuse. The broken templates are fixed.
- **Negative.** A new public contract (`auth` context, URL names, hooks) to keep
  stable. The `allauth` backend tracks allauth's releases (mitigated by CI
  against the lowest and highest supported versions).
- **Neutral.** MFA and passkeys are out of scope; the contract reserves an `mfa` feature.
