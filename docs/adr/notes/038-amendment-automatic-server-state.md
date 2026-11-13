# ADR-038 amendment (draft): view state is saved server-side automatically

**Status**: Proposed
**Date**: 2026-11-13
**Deciders**: Project maintainers
**Related**: ADR-038 (explicit exposure), #4107 (the decision), #4000 (survival table), #4095 (over-limit refusal), #1786 (request-scoped values in stored state), #4046 (interactive components over the fallback), #4106 (two tabs on one route)

---

## Decision

Exposure to the client and keeping state on the server are separate things.
ADR-038 keeps governing what may be rendered to, or set from, the browser.
Saving a view's state in the server-side session exposes nothing to the client,
so it needs no declaration.

**The rule an app author holds: what you set on `self` survives.** Public,
serializable attributes of a page view are saved in the server-side session
after each event and put back after `mount()` on the next request or reconnect.
A leading underscore, or `state(persist=False)`, means "do not keep this".

This amends ADR-038 D3 ("persistence defaults to none") and D-e (form input).
D1's projections are unchanged: template context, client export and signed
snapshots still read declarations only.

## The rules, each with its reason

1. **What is saved.** An instance attribute is saved when its name is public
   (no leading underscore), is not framework-owned (not set by `LiveView()`
   itself, not in `CHANGE_DETECTION_IGNORED`, not in `reserved_state_names()`,
   not a `static_assigns` / `temporary_assigns` key, not a name a djust or
   Django class in the MRO defines, not the per-connection `client_timezone`),
   is not a descriptor other than a `state()` field on the class, and its value is accepted by the
   existing explicit codec (`clone_json_state`: `None`, `bool`, `int`, `float`,
   `str`, `list`, `dict`, exact types). A `state()` field with no `persist=` is
   saved the same way; `persist=False` opts it out. `persist="server"` and
   `persist="client"` keep their strict meaning.
   *Reason:* one codec, one name list, and the same attributes change detection
   already tracks, so "it re-renders when it changes" and "it survives" name
   the same set.
   A value the codec refuses is **skipped**, never an error: the event
   succeeds and the attribute comes from `mount()` next time. The first skip of
   each (class, attribute, type) is logged once at DEBUG on
   `djust.automatic_state` and, when `DEBUG=True`, once at INFO; the value is
   never logged. *Reason:* a 500 for a `Decimal` on `self` would make the
   default worse than not having it; one line per attribute is a notice, not a
   storm.

2. **Precedence.** `mount()`, then saved state over it, then `handle_params()`,
   then `handle_restore()`. Every restore site applies the values returned by
   one function (`load_server_state`), so declared and automatic state cannot
   take different paths. A full page load (GET) is a fresh `mount()` and
   discards the saved automatic state of that route, as it already overwrites
   declared state.

3. **Deploys.** The envelope records a digest of the view class's code: for
   every application class in the MRO, the bytecode, constants and names of its
   functions and its plain class attributes (not the `_djust_*` caches the
   framework writes on a class after a render: two processes must agree whether
   or not they have rendered), plus `exposure_schema_version`, the declared
   fields and the djust version. An envelope whose digest differs
   is discarded and the view mounts fresh. *Reason:* undeclared attributes have
   no schema, so "same code" is the only check that covers them; bytecode needs
   no source files and ignores line numbers, so editing another class in the
   file keeps state. During a rolling deploy a session served by both versions
   mounts fresh each time it changes version (neither version reads the
   other's state) and the last writer's envelope stays. Code the view calls in
   other modules is not covered: bump `exposure_schema_version` to force a
   reset.

4. **What never enters the store.** Anything the codec refuses by type: the
   request, the user, lazy objects, querysets, model instances, forms,
   components. They are skipped, not replaced by an identifier: re-query in
   `mount()` or `handle_restore()`. Attribute names on the serialization floor
   (`password`, `is_superuser`, `is_staff`, `DJUST_SENSITIVE_FIELDS`) are
   skipped. `FormMixin` blanks sensitive fields in `form_data` before it is
   stored and replaces an error message that contains their value (the #4078
   predicate, `is_sensitive_form_field`); this amends D-e: other form input and
   errors are saved. Context-processor
   output never becomes an instance attribute, so #1786's shape cannot recur;
   its regression tests are extended to the store.

5. **Size.** The automatic envelope has its own `StateLimits` budget (64 KB,
   10,000 values, depth 16). Automatic state is best effort: when it does not
   fit, the largest attributes are left out until it does, with the same
   one-time notice naming them (and a WARNING the first time per class). The
   event is not refused. *Reason:* refusing the turn (#4095) is right for a
   field the author declared `persist="server"`, and wrong for a view that
   never asked: a view holding 200 KB of rows worked over a WebSocket in 1.x.
   To keep a large value out, name it `_rows` or declare
   `rows = state(persist=False)`; to make its loss an error, declare it
   `persist="server"`.

6. **Cost.** On the socket event path the runtime takes its post-event
   change-detection snapshot before the commit and hands the changed keys to
   the save; the render decision reuses that same snapshot. A turn that changed
   no saved attribute therefore **walks nothing extra and writes nothing**. A
   turn that did change one serializes the saved state once (the projection the
   explicit save already made for declared fields) and writes the session only
   when the encoding differs from what the store holds. Both are the per-turn
   switch `unchanged_save_skip`, covered by the frame-comparison oracle and a
   count-based gate-off. Other turns (background results, `url_change`, the
   fallback POST) have no snapshot to reuse and serialize once per turn. A
   view whose state is still what `mount()` produced stores nothing. The
   envelope is rewritten once half its lifetime has passed, so an active page
   does not expire.

7. **Legacy views** (`exposure_policy = "legacy"`) use the same module over the
   stateless HTTP fallback, which is where 1.x kept their state. Over a socket a
   legacy view stays in memory only, as in 1.x; it takes no part in the
   explicit turn commit.

8. **What stays explicit.** Rendering and client input. Restored values are put
   back as the same server-side attributes; the template context, the client
   projection, signed snapshot tokens, debug payloads and error messages are
   built from declarations and `get_context_data()` exactly as before. Tests
   assert that a saved-but-unexposed attribute appears in no frame, patch,
   token, error or log line.

9. **`djust.V029`** is narrowed to what is still lost: a handler that changes a
   `_private` attribute, or a `state(persist=False)` field, that a later event
   reads.

10. **Components and embedded children** are out of scope. A `LiveComponent`
    and an embedded child view keep today's rules (child views: declared
    `persist="server"` only). #4046's design restores interactive components
    from the page's saved state; it reads through the same `load_server_state`.

11. **Two tabs on one route** share one envelope, last write wins (#4106,
    undecided). Unchanged; more attributes are now affected.

## Restore validation

The store is written only from server-side attributes. On restore every name
is checked again with the save's own predicate (public ASCII identifier, not
reserved, not framework-owned, not sensitive, not a method/property/other
descriptor on the class); a name that fails is dropped. Values are re-validated
by the codec under the limits. The envelope is bound to the session, user,
tenant and route (`StateBinding`), and has the declared envelope's lifetime
(`DJUST_SERVER_STATE_MAX_AGE`).

## Per-tab state (#4106): design, not implemented in this change

Decided by the owner after this change was started: saved state belongs to a
page load, not to every tab on the route. This change keeps today's keying
(session, user, tenant, route) and leaves the store ready for it: the storage
key is derived in one place (`_automatic_state._store`, and
`ServerStateSession._storage_key` for declared fields), and a page-load token
is one more element of that identity.

**Token.** The page GET mints a random 128-bit token (`secrets.token_urlsafe`)
and writes it on the root element (`dj-page="..."`). It is not a capability: it
only selects one of the entries inside the caller's own session, and the
envelope stays bound to the session, user, tenant and route, so a token copied
to another principal addresses nothing. It needs no signature for that reason.
The server accepts `[A-Za-z0-9_-]{16,64}` and treats anything else as no token.

**Carriers.** The client reads the attribute once and sends it back on: the
socket mount frame (`page`, next to `url`), the SSE connect query string, and
each fallback POST (`X-Djust-Page`, next to `X-Djust-Event`). The server puts
it on the request (`request._djust_page`) where `request_binding` reads it, as
it reads `_djust_slot_target` today, so every save and load takes it from one
place. A request without a token (an older cached page, a non-browser client)
uses the route-wide entry, which is today's behaviour.

**Lifetime and bound.** Entries expire after `DJUST_SERVER_STATE_MAX_AGE`
(one hour), as now. At most **8** page-load entries per view route and **32**
per session are kept; a write beyond that drops the oldest by `created`. The
page GET writes nothing (as in this change), so only tabs that changed state
hold an entry, and a client cannot grow the session past
32 x 64 KB by opening pages.

**Reload.** A hard reload is a new page load: a new token and `mount()`'s
state. Chosen over keeping state across a refresh through `sessionStorage`
because the GET cannot see a per-tab token: the server would render `mount()`'s
state and the page would then jump to the restored state when the socket
mounts (and, over the fallback, only on the first event). State that should
outlive a reload already has a home that the first paint can see: the URL
(`handle_params`) and the database. Back/forward within a tab restores from the
browser's own page cache or reloads, the same rule. If refresh-keeps-state is
wanted later it is additive: the client keeps the token in `sessionStorage` per
path and sends the old one on the mount frame.

**Duplicated tab.** A duplicated tab is a new GET, so it gets its own token;
with no `sessionStorage` in the design there is nothing to copy.

**What changes in the tests.** The two "Two sockets of one session on the same
route" rows of the survival table become "independent"; a reconnect keeps
sending the tab's token and restores its own entry.

## Clearing and counting saved state (#4087)

`_automatic_state.saved_state_keys(session)`, `saved_state_stats(session)`,
`clear_saved_state(session)` and `database_sessions_with_saved_state()` list,
measure and remove the saved view state (automatic and declared) of one
session, or of every session on the `db` / `cached_db` engines. `djust clear`
and `djust stats` can be built on them; the commands are not changed here.
