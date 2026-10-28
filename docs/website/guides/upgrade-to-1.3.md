---
title: "Upgrading to djust 1.3"
slug: upgrade-to-1.3
section: guides
order: 18.3
level: reference
description: "Step-by-step migration from djust 1.2.x to 1.3.0, with diagnostics and fixes for behavior changes."
---

# Upgrading to djust 1.3

This guide starts with a 1.2.x app and covers the behavior changes in
`CHANGELOG.md`'s `1.3.0rc1`–`1.3.0rc9` sections. The explicit state and strict
event-parameter policies remain opt-in. If you are on 1.2.5, several security
fixes listed in the [release notes](../releases/1.3.md#security) are already
present; do not undo their authorization behavior while migrating.

## 1. Install, collect static files, and run checks

```bash
pip install "djust==1.3.0"
python manage.py collectstatic --noinput
python manage.py check
python manage.py djust_audit --ast
```

**What changed:** New template-binding checks `djust.T019`–`T022` compare
rendered event bindings with handler ownership and parameters (rc3,
[ADR-037](../../adr/037-event-contract-checks-and-executable-documentation.md)).
`djust.I001`–`I003` report AI/UI discovery information; `djust_audit --ast`
adds X101–X105 UI findings (rc8,
[AI discovery](ai-discovery.md)). **Who:** Any project whose check or audit
pipeline treats warnings or INFO as failures. **How to tell:**
`manage.py check --fail-level WARNING` fails on T019–T022;
`--fail-level INFO` can fail on I001–I003; `djust_audit --ast --strict`
can fail on X1xx. I002 also appears during `runserver` or `migrate` when a
LiveView project lacks `djust.theming`. **Do:** Fix the binding named by the
check and review UI findings. For intentional exceptions, use the reported
check ID or a scoped audit option:

```python
# settings.py: use only the IDs whose messages you have reviewed.
SILENCED_SYSTEM_CHECKS = ["djust.I001"]
DJUST_CONFIG = {"suppress_checks": ["I002"]}
```

```bash
python manage.py djust_audit --ast --strict --ast-no-ui
```

A same-line `{# djust: noqa X101 #}` or `/* djust: noqa X105 */` can suppress
a specific audit finding. `--ast-no-ui-cache` prevents cache writes but does
not suppress X1xx findings. `DJUST_AI_HINTS=0` silences I001 only. See
[AI discovery](ai-discovery.md) and the [event guide](../core-concepts/events.md).

## 2. Fix view declarations and retired configuration

**What changed:** A `PresenceMixin` or `LiveCursorMixin` after `LiveView` in
the bases now fails at class definition (rc4, #3109). **Who:** Views with
that base order. **How to tell:** Import raises `TypeError` naming the class.
**Do:** Put the mixin before `LiveView`:

```python
class RoomView(PresenceMixin, LiveView):
    pass
```

**What changed:** Six unused `LIVEVIEW_CONFIG` keys were removed (rc4,
#2984). **Who:** Projects setting `jit_cache_backend`, `jit_cache_dir`,
`jit_redis_url`, `debug_components`, `component_wrapper_class` or
`component_loading_class`. **How to tell:** `djust.C018` reports each key;
the key is ignored. **Do:** Delete it from `LIVEVIEW_CONFIG` or
`DJUST_CONFIG`. `djust.V007` was retired in rc3 ([ADR-037](../../adr/037-event-contract-checks-and-executable-documentation.md));
remove obsolete V007 suppressions. A wrong `event_parameter_policy` is now
reported as `djust.C022`, not the earlier candidate's ID.

**What changed:** Configuration reloads on Django's `setting_changed` signal
(rc5, #3217). **Who:** Tests and code using `override_settings` or
`config.set()`. **How to tell:** An override now takes effect without a
restart; values set with `config.set()` survive the reload. **Do:** Remove
manual reset workarounds and assert the intended setting inside the override.

## 3. Review Redis and static asset deployment

**What changed:** redis-py 8 can time out an idle `channels_redis` core-layer
read; 1.3 adds `djust.C023` (rc5, #3199). **Who:** Projects using that
combination without an explicit `socket_timeout` above 5 seconds. **How to
tell:** `manage.py check` reports C023; idle WebSockets may disconnect.
**Do:** Set the timeout on each Redis host, as in the
[deployment guide](deployment.md):

```python
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {"hosts": [{"address": REDIS_URL, "socket_timeout": 20}]},
    }
}
```

**What changed:** Admin CSS and highlight.js now carry Subresource Integrity,
and `crossorigin="anonymous"` when `STATIC_URL` is on another origin (rc4,
[ADR-040](../../adr/040-vendored-assets-and-sboms.md)). `djust.B008` moves to
`check --deploy` and `collectstatic` when `djust` is listed before
`staticfiles` (rc4, #3144). **Who:** Apps with an absolute, cross-origin
`STATIC_URL`, or with an SBOM file (`*.cdx.json`, `*.spdx.json`,
`*.bom.json`) in a static directory. **How to tell:** The browser blocks those
assets on a CORS error, or B008 reports that `collectstatic` would publish an
SBOM.
**Do:** Serve static files with an `Access-Control-Allow-Origin` response
header for the app's origin and follow [Vendored assets](vendored-assets.md)
for the manifest/SBOM check. Re-run `collectstatic` after the upgrade.
With `"djust"` before `"django.contrib.staticfiles"` in `INSTALLED_APPS`,
B008 runs under `check --deploy` and djust's `collectstatic`; otherwise it
remains in ordinary `check`.

## 4. Recheck event behavior and test clients

**What changed:** HTTP-fallback events queue one at a time; a render with no
patches returns `{"patches": []}` with its new version; and `dj-paste` inside
a component or embedded child routes to that owner (rc3). **Who:** Apps
with custom HTTP event clients, rapid sequences of form events, or a parent
handler receiving a child's paste. **How to tell:** A custom client sees an
empty `patches` array, event ordering changes, or the parent paste handler
stops firing. **Do:** Treat an empty list as a successful response and move
the paste handler to the component or child:

```python
# The component that renders dj-paste owns the event.
@event_handler()
def paste(self, text: str = "", **kwargs):
    self.pasted_text = text
```

**What changed:** `LiveViewTestClient.send_event()` enforces handler and
object authorization (rc3, #3094; already in 1.2.3). **Who:** Tests that
formerly called protected handlers as an unauthorized user. **How to tell:**
`success=False`, `code="permission_denied"`. **Do:** Mount with a user who
has the permission, or assert the denial; do not remove the decorator to
make the test pass.

**What changed:** Live authorization follows the corresponding HTTP access
controls, including supported URL wrappers, Django's
`LoginRequiredMiddleware`, and object checks after `handle_params()` (rc8;
also backported to 1.2.4). **Who:** Views relying on checks in `dispatch()`,
`get()`, `post()` or wrapped `as_view()` routes, and custom admin pages with
scoped querysets. **How to tell:** A refused mount returns `login_required`
or `permission_denied`, a refused lazy view shows `view_refused`, or an admin
object outside `get_queryset()` returns 404. **Do:** Keep the HTTP rule,
declare the intended access with a supported Django/djust declaration, and
test the live path as each user role. Declare custom admin filter and sort
fields in `ModelAdmin.list_filter`, `list_display` or `ordering`. A 1.2.5 app
already has these restrictions.

**What changed:** `@rate_limit` on an `@event_handler` now also applies when
the handler is reached through the page-POST fallback (an HTTP `POST` with
`X-Djust-Event`), with one budget per handler function across transports
(rc8; also in 1.2.4). Before, the fallback skipped the decorator. **Who:** Apps
whose fallback-path callers or tests send more requests than the handler's
`burst`. **How to tell:** A `429` with `{"code": "rate_limited"}` in the JSON
error, where your own limit or message used to answer. **Do:** Size `rate`
and `burst` for every transport, or remove the decorator if an app-level
limit is the one you want.

## 5. Update presence and server-push integration

**What changed:** A session with a templated `presence_key` joins its group
after `mount()` and first render (rc5, #3202). **Who:** Code that expects a
peer's broadcast during that short mount window. **How to tell:** The first
broadcast is absent; older mounts may have logged `Presence key format error`.
**Do:** Read the initial online count from the first render or send a later
broadcast after the recipient has mounted. Keep a key stable during a mount;
a changed key after mount does not move the existing group subscription.

**What changed:** A `push_scope` view's presence notifications now reach only
the sender's presence key (rc3, #3095). **Who:** New scoped views overriding
`_on_presence_change` to observe other rooms. **How to tell:** That override
no longer runs for another key. **Do:** Opt into view-wide broadcasts if
required:

```python
class RoomView(PresenceMixin, LiveView):
    presence_broadcast_scoped = False
```

**What changed:** Several views can share one WebSocket. Raw `group_send`
messages of type `server_push`, `presence_event` or `db_notify` without a
`group` are dropped when that socket has more than one mounted view (rc7,
#3252). **Who:** Code sending those messages directly. **How to tell:** A
multi-view page misses the push while a single-view page receives it.
**Do:** Use `push_to_view`, `PresenceManager` or the `db_notify` listener,
which add the group; see [Multiple views](multiple-views.md).

**What changed:** A server push or DB notification with `_skip_render` no
longer emits a no-ref `noop` (rc4, #3034). **Who:** Custom clients or tests
that counted that frame. **How to tell:** The frame is absent; queued side
effects still arrive. **Do:** Wait for the side effect or the ref-bearing
acknowledgement of a user event instead of a no-ref `noop`.

**What changed:** A refused or failed lazy view is released on its own;
`view_refused` replaces page-wide navigation for that child (rc8, #3252;
failed-mount cleanup was also backported in 1.2.4). **Who:** Apps with
lazy/batched views or custom frame handlers. **How to tell:** The container
has `data-djust-refused`, a `djust:view-refused` event fires, or an event to
an unavailable view returns `code: "view_unavailable"`. **Do:** Listen for
`djust:view-refused` if you need an application prompt and deploy the matching
client with the server ([Multiple views](multiple-views.md)).

## 6. Review UI and rendering changes

**What changed:** The `djust` preset's labels and links use new contrast
colours (rc4–rc8, #2996, #3165, #2885); a user-authored preset can now get
`djust_theming.W001` for status text on tinted surfaces and input borders
(rc7, #3281). **Who:** Apps that customize preset tokens or rely on exact
colours. **How to tell:** Visual comparison or W001. **Do:** Review the
reported pair and update your theme tokens; do not suppress a contrast
failure without checking the actual rendered surface.

**What changed:** `theme_context` HTML values are lazy objects rather than
`str` instances (rc8, #3028). **Who:** Python code passing `theme_head` or
the other generated HTML values to `json.dumps`, `re`, `startswith`,
`"".join` or `os.fspath`. **How to tell:** `TypeError`; template output
remains the same. **Do:** Convert on use:

```python
html = str(context["theme_head"])
```

**What changed:** `theme_context`'s `theme_presets` is no longer stored in
every LiveView's state (rc14, #3631). It is a `djust.request_scoped.RequestScopedList`,
a `list` subclass, so `isinstance(..., list)`, `json.dumps`, `|json_script`,
`len`, indexing and `{% for %}` behave as before. **Who:** Python code that
tests `type(context["theme_presets"]) is list`, or hands the value to a library
that dispatches on the exact type (`yaml.safe_dump` raises `RepresenterError`).
**How to tell:** the `type(...) is list` check is false; `yaml.safe_dump` fails.
**Do:** convert on use: `list(context["theme_presets"])`.

**What changed:** MarkdownEditor enables its Visual-mode bubble menu by
default (rc6, #3108). **Who:** Pages using that editor. **How to tell:** A
selection shows the menu. **Do:** Pass `bubble_menu=False` to retain the old
UI. `djust.T012` now catches more templates using client directives, while
T002 no longer reports templates without `dj-view` (rc6, #3225); inspect
the check ID and template path when reviewing new diagnostics.

**What changed:** An application's unlayered `a { color: ... }` rule can now
colour a plain `<a class="btn">` (rc6, #3230). **Who:** Apps with that
selector. **How to tell:** Compare the rendered link button after upgrade.
**Do:** Set the intended colour with an explicit `.btn` rule.

**What changed:** `{% csrf_token %}` now renders on the initial page outside
`dj-root`, for example in a logout form in a shared `base.html` nav, even
without the `csrf` context processor (rc7, #3294). Before, the page shell
rendered it empty and such a form got a 403. **Who:** Tests that count or
locate `csrfmiddlewaretoken` inputs across the whole page. **How to tell:** A
test that expected *n* token inputs finds one more. **Do:** Scope the
assertion to the form under test rather than the whole page.

## 7. Check opt-in policies and specialist integrations

**What changed:** Strict typed event parameters and explicit state exposure
are available, while `legacy` remains the default (rc1–rc3,
[ADR-036](../../adr/036-typed-event-parameter-contracts.md),
[ADR-038](../../adr/038-explicit-context-and-state-exposure.md)).
**Who:** Apps opting into either policy. **How to tell:** Strict handlers
reject undeclared or invalid parameters; `djust.V019` reports an explicitly
strict `dj-auto-recover` target; an explicit view that assigns a component on
`self` fails first render with `ExposureConfigurationError`. **Do:** Declare
parameters and state, leave recovery handlers on legacy, and declare
components on the view class. Follow [Explicit exposure](../state/explicit-exposure.md)
and [Events](../core-concepts/events.md) before switching a view:

```python
class OrdersView(LiveView):
    exposure_policy = "explicit"
    # Declare context and persisted/client state as described in the guide.
```

**What changed:** In a legacy view with `enable_state_snapshot`, a
state-changing skip-render `noop` now refreshes the signed snapshot before
queued push or navigation effects (rc6, #3246). **Who:** Hooks that assume
the push arrives before the acknowledgement. **How to tell:** Observe an
ack before the push in that case. **Do:** Treat the ack as the state boundary
and run post-navigation work after it; keep the current client and server
assets together.

**What changed:** A background task whose session/runtime has been collected
now logs value-free failures and redacts SQL parameters (rc4, #3116).
**Who:** Long-lived tasks relying on detailed diagnostics after disconnect.
**How to tell:** A static log line or redacted query parameters. **Do:**
Capture safe diagnostic context while the view is live, or use an
application-owned logger outside the session. Do not retain the session only
to keep detailed framework diagnostics.

**What changed:** `@push_handler` reserves a method for server push; a browser
event cannot call it (rc8, #3002). **Who:** Apps adopting the new marker or
accidentally combining it with event-only decorators. **How to tell:**
`djust.V021`, `djust.T019` or `TypeError` for conflicting decorators.
**Do:** Use `LiveViewTestClient.send_push()` for a push test and keep browser
events on a separate `@event_handler` method.

**What changed:** The themed `djust new` scaffold enables components and
theming and requests `djust[components]`; `--bare` remains core-only (rc9,
#3448). **Who:** Automation extending newly generated projects; existing
projects are unaffected. **How to tell:** A new project uses `{% app_shell %}`
or missing extras raise `ImproperlyConfigured` naming `djust[components]`.
**Do:** Install the named extra for themed projects or pass `--bare` for a
core-only starter.

## Finish the upgrade

Exercise representative pages over WebSocket and your configured fallback,
especially forms, lazy views, presence and authorization. Update client assets
with the server: a client older than the rc8 `view_refused` frame leaves a
refused lazy-view container empty instead of showing the sign-in link
([Multiple views](multiple-views.md)). Check the [1.3 release notes](../releases/1.3.md)
for security fixes and the distinction between the 1.2.5 backports and new
1.3 protections.
