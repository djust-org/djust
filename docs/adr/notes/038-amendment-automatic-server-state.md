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

## Not done here

- **One envelope.** Declared `persist="server"` fields keep their own envelope
  (`_djust_explicit_...`) beside the view's state (`_djust_auto_...`). Both are
  written in one session save and restored through one function. Merging them
  means moving the schema digest, `migrate_state` and the child-slot adapters.
- **Component state** (`<key>_components` in 1.3). A view that holds a
  component runs `mount()` again on the next request, which rebuilds it with
  its initial state. Interactive components over the fallback are unchanged
  (#4046).
- **Legacy-policy views** save nothing.
- **Per-tab state** (#4106): two tabs on one route share one envelope, last
  write wins, and a page load in one tab overwrites it, as in 1.3.

## Clearing and counting saved state (#4087)

`_automatic_state.saved_state_keys(session)`, `saved_state_stats(session)`,
`clear_saved_state(session)` and `database_sessions_with_saved_state()`.
