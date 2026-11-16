"""Interactive components under explicit exposure: identity, state, lookup (#4046).

One module, shared by the WebSocket, SSE and HTTP-fallback transports, so the
three cannot disagree about which component an event reaches or what state it
finds there.

* **Identity.** On an explicit-exposure view a declared interactive component's
  ``component_id`` is derived from its declaration: the view class, the
  attribute name and, for a collection member, its key and generation
  (:func:`derive_identity`). A request that builds the view again (the HTTP
  fallback, a second server) builds the same ids.
* **The wire id.** What the page carries is that id plus a tag
  (:func:`wire_id`): an HMAC, keyed from ``SECRET_KEY``, over the id and the
  page's binding (session, user, tenant, route) and tab. Knowing how ids are
  derived does not let a client address a component the server did not render
  to it, in this session, on this route, in this tab.
* **State.** A component's own state (open, selected, the observation cursor;
  a collection member's generation) is a small record saved with the view's
  declared ``persist="server"`` state, in the same envelope, under the same
  binding, expiry and size budget (:func:`component_records`,
  ``ExposureContract``). ``persist=False`` on the declaration opts out.
* **One restore** (:func:`restore_component_state`) and **one lookup**
  (:func:`resolve_component_target`) for every transport.

Legacy-exposure views keep random ids and no record: nothing here changes them.

**Tabs, pages and ids.** The record is per session and route; ids are minted
per tab. So an id's validity must depend only on what the tab that minted it
can see: a row's id is its key and that key's *generation*, and a generation
changes only when ``sync()`` removes that very key. A request never changes
the generation of a row it does not build; it carries that row's entry on in
the record (bounded by ``MAX_REMEMBERED``). An id whose tag verifies for this
binding and tab but whose row the server no longer builds is *stale*: the HTTP
fallback answers it with the current page (no handler runs), the socket with
an error frame (its own page was re-rendered when the row went).

=====================================  ===========================================
Situation                              Outcome
=====================================  ===========================================
Two tabs, one route, different rows    Each tab's ids stay valid; each request
(``?page=1`` / ``?page=2``)            builds its rows, carries the other's.
Two tabs on the same page              Same rows; the tags differ per tab; state
                                       is shared (per session and route).
A tab reloads (page GET)               The GET saves its own rows at generation
                                       0 and keeps the stored copy of the rest.
                                       The other tab's rows answer; a row it
                                       shows at a higher generation is stale:
                                       re-rendered.
Back / Forward to an older page        As an older tab: its rows answer unless
                                       their key was removed by ``sync()`` since
                                       (stale: re-rendered).
Tab 1 removes a row tab 2 still shows  Tab 2's event on it is stale: the fallback
                                       answers with the current page.
WebSocket in one tab, fallback in the  Each save writes the rows its view holds
other                                  (or removed) and keeps the stored copy of
                                       the others, so neither undoes the other.
                                       Two views holding one row: last writer
                                       wins for its open/selected.
Two processes                          Ids are derived and records live in the
                                       shared session store: as one process.
A row deleted from the data, then      Not a ``sync()`` removal: its generation
re-added with the same key             is unchanged and its old id answers again
                                       (djust cannot tell it from the same row).
A removed row's old id after a page    Answers again: the page load saves the
load, or after a tab that still held   rows it builds at generation 0, and a
the row saves                          holder saves its row back (last writer).
=====================================

What an old id can reach is bounded either way: only the menu's own actions
on whichever row has that key now, for the same session, user, tenant, route
and tab, with every check of the view still applied.  ===========================================
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
from inspect import getattr_static
from typing import Any

logger = logging.getLogger(__name__)

#: Hex digits of the tag on a wire id.
TAG_LENGTH = 32
_SALT = "djust.components.interactive.wire-id"

#: Returned by :func:`resolve_component_target` for an ADR-031 descriptor's
#: name that nothing has materialized: the event goes to the view's own alias,
#: which resolves the component from ``component_id`` itself.
PASS_THROUGH = object()

#: Returned by :func:`resolve_component_target` for a wire id this server
#: minted for this binding and tab (its tag verifies) whose component the view
#: no longer holds: a row removed since the page rendered. Not a forgery; the
#: HTTP fallback answers it with the current page.
STALE = object()

#: View classes already warned that their component records did not fit.
_WARNED_OVER_LIMIT: set[str] = set()
#: (view class, reason) pairs already warned that remembered rows were left out.
_WARNED_ROWS_LEFT_OUT: set[tuple[str, str]] = set()


def warn_rows_left_out(view: Any, reason: str) -> None:
    """Log once per view class and reason, without values, that the record
    leaves out rows other tabs (or removals) left in it: those rows start from
    ``mount()`` when a tab builds them again, and a removed row forgotten this
    way answers to its old id again."""
    # Read through getattr (#2488): a bare module read can raise while building this message.
    owner = "%s.%s" % (
        getattr(type(view), "__module__", ""),
        getattr(type(view), "__qualname__", type(view).__name__),
    )
    if (owner, reason) in _WARNED_ROWS_LEFT_OUT:
        return
    _WARNED_ROWS_LEFT_OUT.add((owner, reason))
    logger.warning(
        "djust: %s: interactive collection rows this page does not show were left out "
        "of the saved state (%s). They start from mount() when a page builds them again. "
        "Shorter keys or fewer rows per route keep them.",
        owner,
        reason,
    )


_RAW_ID = re.compile(r"cmp_[0-9a-f]{32}", re.ASCII)
_TAG_TEXT = re.compile(r"[0-9a-f]{%d}" % 32, re.ASCII)
_LIFETIME = re.compile(r"obs_[0-9a-f]{32}")
_MAX_SELECTED = 1024
_MAX_INT = 2**53 - 1

#: Record kinds, part of the view's schema digest: a changed kind (a fixed menu
#: that became client-owned, a fixed menu that became a collection) rejects the
#: saved envelope and the view mounts fresh.
FIXED_SERVER, FIXED_CLIENT, COLLECTION = "dropdown/server", "dropdown/client", "dropdown/collection"


# --------------------------------------------------------------------------- #
# Declarations
# --------------------------------------------------------------------------- #


def _declarations(view_class: type) -> dict[str, Any]:
    """The interactive declarations ``view_class`` holds, by attribute name
    (the effective class attribute only; no descriptor is evaluated)."""
    from ._component_subscriptions import DECLARATIONS_ATTR
    from .components._interactive import DropdownMenu, DropdownMenuCollection

    found: dict[str, Any] = {}
    registry = getattr_static(view_class, DECLARATIONS_ATTR, None)
    if not isinstance(registry, dict):
        return found
    for name, declaration in registry.items():
        if getattr_static(view_class, name, None) is not declaration:
            continue
        if isinstance(declaration, (DropdownMenu, DropdownMenuCollection)):
            found[name] = declaration
    return found


def _explicit(view: Any) -> bool:
    from ._exposure import uses_legacy_exposure

    return not uses_legacy_exposure(view)


def record_specs(view_class: type) -> dict[str, str]:
    """``{attribute: record kind}`` for every interactive declaration whose
    state is saved (``persist="server"``, the default). Read by the exposure
    contract: these names are saved with the view's declared server state."""
    from .components._interactive import DropdownMenuCollection

    specs: dict[str, str] = {}
    for name, declaration in _declarations(view_class).items():
        if declaration._persist != "server":
            continue
        if isinstance(declaration, DropdownMenuCollection):
            specs[name] = COLLECTION
        else:
            specs[name] = FIXED_CLIENT if declaration.visibility == "client" else FIXED_SERVER
    return specs


# --------------------------------------------------------------------------- #
# Identity
# --------------------------------------------------------------------------- #


def derive_identity(view: Any, component: Any, name: str, key: str | None, born: int) -> None:
    """Give ``component`` its id from its declaration, on an explicit view.

    ``cmp_`` and 32 hex digits, as before, from the view class, the attribute
    name, and for a member its key and lifetime. A legacy view keeps the
    random id the component was constructed with.
    """
    if not _explicit(view):
        return
    owner = type(view)
    payload = json.dumps(
        ["%s.%s" % (owner.__module__, owner.__qualname__), name, key, born],
        separators=(",", ":"),
    ).encode()
    component.component_id = "cmp_" + hashlib.sha256(payload).hexdigest()[:32]
    component._derived_id = True


def _scope(view: Any) -> str:
    """The page binding a wire id is bound to: session, user, tenant and route
    (``request_binding``), and the tab. ``unbound`` when the view has no
    request with a session (a unit test's render): such a tag verifies only on
    another unbound view, which no transport builds."""
    from ._exposure_sessions import request_binding

    request = vars(view).get("request")
    try:
        binding = request_binding(request).digest if request is not None else "unbound"
    except Exception:  # noqa: BLE001 — no session or identity: nothing to bind to
        binding = "unbound"
    tab = vars(view).get("_djust_tab")
    return "%s|%s" % (binding, tab if type(tab) is str else "")


def _secrets() -> list[Any]:
    from django.conf import settings

    return [settings.SECRET_KEY, *getattr(settings, "SECRET_KEY_FALLBACKS", ())]


def _tag(component_id: str, scope: str, secret: Any = None) -> str:
    from django.utils.crypto import salted_hmac

    digest = salted_hmac(_SALT, component_id + "|" + scope, secret=secret, algorithm="sha256")
    return digest.hexdigest()[:TAG_LENGTH]


def _tagged(component: Any) -> bool:
    return bool(getattr(component, "_derived_id", False))


def wire_id(component: Any) -> str:
    """The id a page carries for ``component``: its derived id plus the tag
    for this view's binding and tab. A legacy view's component: its id."""
    if not _tagged(component):
        return str(component.component_id)
    return "%s.%s" % (
        component.component_id,
        _tag(component.component_id, _scope(component._parent)),
    )


# --------------------------------------------------------------------------- #
# Lookup
# --------------------------------------------------------------------------- #


def bind_declared_components(view: Any) -> None:
    """Bind every fixed interactive declaration, so the registry holds what
    the page rendered before anything renders (the HTTP fallback resolves an
    event before its render). Collections bind members only through ``sync()``."""
    from .components._interactive import DropdownMenu

    for name, declaration in _declarations(type(view)).items():
        if isinstance(declaration, DropdownMenu):
            getattr(view, name)


def resolve_component_target(view: Any, component_id: str) -> Any:
    """The component an event's ``component_id`` names, for every transport.

    Returns the component, :data:`PASS_THROUGH` for an ADR-031 descriptor that
    nothing has materialized (its view-level alias resolves it), :data:`STALE`
    for a genuine wire id whose row is gone, or ``None``: refused. Every
    refusal looks the same to the client.

    An interactive component of an explicit view is reachable only by its wire
    id: its derived id and a tag that verifies, in constant time over bytes,
    for this request's binding and tab (under ``SECRET_KEY`` or one of
    ``SECRET_KEY_FALLBACKS``). The id and the tag must be lowercase ASCII hex
    of their exact lengths before anything is computed: any other text (other
    scripts, full-width digits, a NUL, a second dot) is refused like an
    unknown id. Its bare derived id and a tag minted for another session,
    user, tenant, route or tab are refused. Other components are looked up as
    before.
    """
    if type(component_id) is not str or not component_id:
        return None
    bind_declared_components(view)
    registry = getattr(view, "_components", None)
    if not isinstance(registry, dict):
        registry = {}
    raw, dot, tag = component_id.partition(".")
    if dot:
        if not _RAW_ID.fullmatch(raw) or not _TAG_TEXT.fullmatch(tag):
            return None
        scope = _scope(view)
        given = tag.encode("ascii")
        if not any(
            hmac.compare_digest(given, _tag(raw, scope, secret).encode("ascii"))
            for secret in _secrets()
        ):
            return None
        component = registry.get(raw)
        if component is None or not _tagged(component):
            return STALE
        return component
    component = registry.get(component_id)
    if component is not None:
        # A derived id without its tag is refused: it is computable.
        return None if _tagged(component) else component
    if component_id in getattr(type(view), "_component_descriptors", {}):
        return PASS_THROUGH
    return None


# --------------------------------------------------------------------------- #
# State
# --------------------------------------------------------------------------- #


def component_record(view: Any, name: str) -> Any:
    """The saved record of declaration ``name`` on ``view`` (binds it)."""
    return getattr(view, name)._record()


def merge_stored_records(view: Any, values: dict[str, Any], stored: Any) -> None:
    """Before a save: keep the stored copy of every collection row this view
    neither holds nor removed (#4046).

    Another tab (or connection) on the route may have changed such a row since
    this view read the record; this view's copy is older, so writing it would
    undo that change. ``values`` is the projection about to be saved, changed
    in place; the remembered rows stay bounded by ``MAX_REMEMBERED``.
    """
    from .components._interactive import MAX_REMEMBERED

    if not isinstance(stored, dict):
        return
    for name, kind in record_specs(type(view)).items():
        mine, theirs = values.get(name), stored.get(name)
        if kind != COLLECTION or not isinstance(mine, dict) or not isinstance(theirs, dict):
            continue
        collection = getattr(view, name)
        own = set(collection._order) | collection._removed
        members = dict(mine["members"])
        for key, entry in theirs["members"].items():
            if key not in own:
                members.pop(key, None)
                members[key] = entry
        others = [key for key in members if key not in own]
        if len(others) > MAX_REMEMBERED:
            warn_rows_left_out(view, "more than %d remembered rows" % MAX_REMEMBERED)
        for key in others[: max(0, len(others) - MAX_REMEMBERED)]:
            del members[key]
        values[name] = {**mine, "members": members}


def project_without_records(contract: Any, view: Any) -> dict[str, Any] | None:
    """The view's server projection with every component record left out
    (``None``), or ``None`` when even that is over the limits.

    For a save whose component records do not fit the envelope's budget: the
    view's declared fields are saved, and the components start from
    ``mount()`` on the next request (a member added back after a removal then
    takes a new identity). Logged without values.
    """
    from ._exposure import StateLimitError

    values = {
        name: getattr(view, name)
        for name, policy in contract.fields.items()
        if policy.permits("server")
    }
    values.update({name: None for name in contract.components})
    try:
        projected: dict[str, Any] = contract.project(values, "server")
    except StateLimitError:
        return None
    if contract.owner in _WARNED_OVER_LIMIT:
        return projected
    _WARNED_OVER_LIMIT.add(contract.owner)
    logger.warning(
        "djust: %s: interactive component state was not saved; with it the view's saved "
        "state is over its limit. The components start from mount() on the next request. "
        "Paginate the rows, or declare the component with persist=False.",
        contract.owner,
    )
    return projected


def validate_records(specs: dict[str, str], values: dict[str, Any]) -> None:
    """Check every component record's exact shape before anything is applied.

    Pure: reads ``values`` only. Raises ``ExposureError`` (the whole envelope
    is rejected and the view mounts fresh) on any deviation.
    """
    from ._exposure import ExposureError

    for name, kind in specs.items():
        record = values.get(name)
        if record is None:
            continue  # not saved (over the limit): the component starts from mount()
        if kind == COLLECTION:
            ok = _valid_collection(record)
        else:
            ok = _valid_member(record, kind == FIXED_CLIENT)
        if not ok:
            raise ExposureError("Saved interactive component state does not match its declaration")


def _int(value: Any, upper: int = _MAX_INT) -> bool:
    return type(value) is int and 0 <= value <= upper


def _valid_member(record: Any, client: bool) -> bool:
    if type(record) is not list or len(record) != 4:
        return False
    opened, selected, lifetime, sequence = record
    if type(opened) is not bool or type(selected) is not str or len(selected) > _MAX_SELECTED:
        return False
    if client:
        return (
            opened is False
            and type(lifetime) is str
            and bool(_LIFETIME.fullmatch(lifetime))
            and _int(sequence)
        )
    return lifetime == "" and sequence == 0


def _valid_collection(record: Any) -> bool:
    from .components._interactive import MAX_KEY_LENGTH

    if type(record) is not dict or set(record) != {"members"}:
        return False
    members = record["members"]
    if type(members) is not dict:
        return False
    for key, entry in members.items():
        if type(key) is not str or not 0 < len(key) <= MAX_KEY_LENGTH:
            return False
        if type(entry) is not list or len(entry) != 6:
            return False
        generation, client = entry[0], entry[1]
        if not _int(generation, 2**31 - 1) or type(client) is not bool:
            return False
        if not _valid_member(entry[2:], client):
            return False
    return True


def split_component_records(view: Any, values: Any) -> tuple[Any, dict[str, Any]]:
    """``(the other values, the component records)`` of restored declared state."""
    if not isinstance(values, dict):
        return values, {}
    specs = record_specs(type(view))
    if not specs:
        return values, {}
    plain = {k: v for k, v in values.items() if k not in specs}
    return plain, {k: v for k, v in values.items() if k in specs}


def declares_interactive(view_class: type) -> bool:
    """Whether ``view_class`` declares an interactive component: its members
    come from ``mount()``'s ``sync()``, so every request runs ``mount()``."""
    return bool(_declarations(view_class))


def apply_declared_state(view: Any, values: Any) -> None:
    """Assign restored declared ``persist="server"`` values: the fields, then
    the component records through :func:`restore_component_state`. Shared by
    the HTTP fallback, the socket restore (WebSocket and SSE) and an embedded
    child, so they apply one envelope the same way."""
    from .security import safe_setattr

    if not values:
        return
    plain, records = split_component_records(view, values)
    for key, value in plain.items():
        safe_setattr(view, key, value, allow_private=False, raise_on_blocked=True)
    restore_component_state(view, records)


def restore_component_state(view: Any, records: dict[str, Any]) -> None:
    """Put saved component records back on ``view``: the one restore (#4046).

    Called on every transport after ``mount()`` built the components and with
    records the contract already validated whole. A fixed component takes its
    open/selected/observation state; a collection's live members take their
    saved generation and state, and the saved keys no member has are
    remembered (adopted by a later ``sync()``, carried on by the next save).
    Nothing emits; no callback runs; a record never creates a member.
    """
    if not records:
        return
    for name in record_specs(type(view)):
        record = records.get(name)
        if record is None:
            continue
        getattr(view, name)._restore_record(record)
