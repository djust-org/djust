# ADR-038 amendment (draft): a view's state is saved server-side, as in 1.3

**Status**: Proposed
**Date**: 2026-11-13
**Related**: ADR-038 (explicit exposure), #4107, #4000 (survival table), #4095 (over-limit refusal), #4078 (secret form controls), #4046 (interactive components over the fallback), #4106 (two tabs)

## Decision

1.3's persistence is restored for every view on explicit exposure, with the
minimum changes 2.0 needs. Exposure to the client stays explicit (ADR-038 D1):
saving a value on the server does not render it, send it or let the client set
it. This amends D3 ("persistence defaults to none"), D1's "private attributes
are never persisted" and D-e (form input).

## What 1.3 did, and what this does

| | 1.3 (`v1.3.3`, legacy-exposure views) | This change |
|---|---|---|
| Page GET | `mount()`, then save, overwriting | Same |
| What is saved | The base context (public attributes), `_private` attributes created by `mount()`, component state | Public attributes, `_private` attributes created by `mount()`, `state()` fields. Component state: not restored (see below) |
| Fallback POST | Restore, no `mount()`; `mount()` only when nothing saved, or private attributes could not be saved, or mount work was owed | Same, and an unsaveable PUBLIC attribute, a component or queued mount work also makes the next request mount (one `remount` flag) |
| After restore | `handle_restore()` | `handle_params()` (2.0: the URL wins, #3624), then `handle_restore()` |
| Socket mount | Restore in place of `mount()` only for views that opted in (`enable_state_snapshot`); otherwise `mount()` fresh | `mount()`, then the saved state over it, for every view. Not "in place of `mount()`": that needed the per-process re-registration helpers (uploads, presence, listen channels) 2.0 removed |
| Socket turns | Saved (opted-in views) | Saved (every view), skipped when nothing changed |
| Keying | `liveview_<path>`, tenant-scoped; no user binding, no expiry of its own | 2.0's binding: session, user, tenant, route; `DJUST_SERVER_STATE_MAX_AGE` |
| Deploys | Old state applied to new code; a name that is now a method or property skipped | Same |
| Switch | `view_state_in_session` | Always on; the setting stays refused |

## Differences from 1.3, each with its reason

1. **Types round-trip.** 1.3 kept `Decimal` (tagged) and turned tuples into
   lists and `UUID`s into strings. The same tagged codec
   (`decode_state_roundtrip`) now also carries tuples, sets, dates, times,
   `UUID`, bytes, enum members, big integers and non-string dict keys, through
   a fixed table of constructors; nothing is imported by name or unpickled. A
   model instance is still saved as its field dict.
2. **No stringifying.** 1.3 turned an unknown value into `str(value)` with a
   warning, so an object came back as a string. It is now skipped, a WARNING
   names it once per view class and attribute, and the next request runs
   `mount()` again so it exists. Never an error: a view that holds
   `self.user = request.user` keeps running.
3. **Whole or refused.** 1.3 had no cap. A WARNING names the largest
   attributes from 100 KB; past 512 KB (under a Memcached item once Django
   encodes the session) the event's change is refused whole, as #4095 refuses a
   declared field. State is never saved in part.
4. **Secrets.** 1.3's floor (`password`, `is_staff`, `is_superuser`,
   `DJUST_SENSITIVE_FIELDS`) plus one secret-control predicate for form input
   (#4078's rule, extended to one-time-code and payment-card `autocomplete`
   values). No guessing from names.
5. **Binding.** Session, user, tenant and route, so state never restores for
   another principal.
6. **Cost.** The session is written only when the state changed, and a socket
   turn whose change detection found nothing changed serializes nothing.

## The stored session is not a client destination

ADR-038's exposure rule is about the client: under the explicit policy nothing
outside `get_context_data()` or a declared `state()` field is rendered, sent,
or settable from the browser. That holds unchanged for every client-facing
destination: page HTML, socket and SSE frames, fallback answers, patches,
tokens, the tab meta, error messages and logs.

The caller's own server-side session is a separate destination with its own
rule:

- A view's undeclared public attributes and the `_private` attributes `mount()`
  created MAY rest in that view's saved-state envelope (`_djust_auto_...`,
  under `values` and `private`), and nowhere else in the session. Being saved
  does not expose them: they are still never rendered, sent, or client-settable.
- What is on the floor is NEVER in the session, under any policy or transport:
  names on the sensitive-field list (`password`, ...), request and user
  objects, secret form controls.
- The envelope is bound to session, user, tenant and route and has a lifetime,
  so another session, user or tenant reads nothing.

The E5 matrix (`tests/playwright/test_exposure_matrix.py`) pins both: the
client-facing rows keep "never appears", and the stored-session row is
`tests/playwright/_exposure_session_contract.py`, which
`python/djust/tests/test_exposure_session_contract_4107.py` also runs, with its
gate-offs, without a browser.

## Decided after review (2026-11-13)

- **Socket mount restores handler changes only.** The envelope records the
  names a handler, or a later lifecycle step, changed since `mount()`
  (`changed`): each save adds every name whose value differs from the view's
  state at the end of its mount sequence. A socket mount runs `mount()` and puts
  back only those names; what `mount()` computes is fresh. The fallback keeps
  1.3's rule (no `mount()`, everything saved goes back), so the two paths
  differ for values `mount()` computes and no handler changes.
- **A saved model dict never goes over a live instance** that `mount()` just
  loaded (`put_back(over_mount=True)`).
- **The URL and the saved state.** `handle_params()` runs on every fallback
  request and socket mount (2.0 pins that the URL a request carries wins over a
  declared `persist="server"` field, #3624). The envelope records the query the
  view last read, canonical (parsed, sorted). On restore the saved state goes
  on first; `handle_params()` runs; and only while the query is the one the
  state was saved under is the undeclared saved state put back again over what
  the hook assigned. When the query changed, the hook has the last word for
  what it assigned. Chosen because it needs no knowledge of which names the
  hook assigns: skipping the second put-back is exactly "handle_params() wins
  for what it assigned, everything else keeps its restored value". Its cost: a
  name the hook defaults on every call is defaulted when any parameter changed.
  `live_patch(params=...)` moves the recorded URL, so the request the browser
  then makes there is not a change.
- **Component state** is saved with the view's state and put back onto the
  components the view holds (1.3's `<key>_components`). ADR-034 interactive
  components are left out: their identity is rebuilt per mount (#4046).
- **Per-tab state (#4106).** The page GET mints a tab id
  (`secrets.token_urlsafe(12)`), carried in `<meta name="djust-tab">`; the
  client sends it on the socket mount frame (`tab`), the SSE stream URL
  (`_djust_tab`) and each fallback POST (`X-Djust-Tab`). The store key is the
  view class, the route and the tab id, inside the session; the envelope stays
  bound to session, user, tenant and route. No id, or a malformed one, saves
  and restores nothing. At most 8 tab states per view and route are kept; the
  oldest is evicted at a write. A navigation that stays on the socket keeps
  the page's tab id with the new route's key. A duplicated browser tab shares
  its original's id until its next page load.

## Not done here

- **One envelope.** Declared `persist="server"` fields keep their own envelope
  (`_djust_explicit_...`), per route and therefore shared by tabs. Both are
  written in one session save.
- **Legacy-policy views** save nothing.

## Clearing and counting saved state (#4087)

`_automatic_state.saved_state_keys(session)`, `saved_state_stats(session)`,
`clear_saved_state(session)` and `database_sessions_with_saved_state()`.
