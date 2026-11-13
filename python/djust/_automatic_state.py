"""Automatic server-side state: what a view sets on ``self`` survives (#4107).

A page view's public, serializable instance attributes are saved in the
server-side session after each event and put back after ``mount()`` on the
next request or reconnect. Nothing here grants client exposure: the values are
written from, and restored to, server-side attributes only. Template context,
the client projection, signed snapshots and debug payloads keep reading
declarations (ADR-038 D1).

The rules, in one place (see ``docs/adr/notes/038-amendment-automatic-server-state.md``):

* **Names** (:func:`eligible_name`): public ASCII identifiers that are not
  framework-owned, not reserved, not on the serialization floor, and not a
  method, property or other descriptor of the class. A ``state()`` field takes
  part unless it states a ``persist`` of its own. The SAME predicate decides
  what is saved and what a stored envelope may put back.
* **Values**: only what :func:`djust._exposure.clone_json_state` accepts. Any
  other value is skipped, never an error, with a one-time notice that names
  the attribute and its type, never its value.
* **Size**: one :class:`~djust._exposure.StateLimits` budget; the largest
  attributes are left out until the rest fits.
* **Deploys** (:func:`class_digest`): an envelope written by different code of
  the view class is discarded.
* **Binding**: the session, user, tenant and route of
  :func:`djust._exposure_sessions.request_binding`, and the declared
  envelope's lifetime.

Session storage errors propagate, as they do for declared state. Everything
else (an unsupported session engine, no authentication middleware, a value
that does not fit) turns automatic persistence off for that save and is
reported once; it never fails a turn.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from inspect import getattr_static
from typing import Any, Callable, Dict, Optional, Tuple

from . import _per_turn
from ._class_snapshot import namespace
from ._exposure import (
    _NAME,
    _UNSAFE_KEYS,
    ExposureError,
    StateLimitError,
    StateLimits,
    _class_facts,
    clone_json_state,
    declared_schema_version,
    reserved_state_names,
)

logger = logging.getLogger("djust.automatic_state")

#: Envelope format. A reader discards any other.
_VERSION = 1
_KEY_PREFIX = "_djust_auto_"
_LIMITS = StateLimits()
_MISSING: Any = object()
_PRIMITIVES = (str, int, float, bool, type(None), list, dict)
#: Public names a transport sets on the view for each connection.
_TRANSPORT_ATTRS = frozenset({"client_timezone"})

#: Notices already given, so each (class, attribute, reason) is logged once
#: per process. Bounded: emptied when full, which repeats a notice, never
#: grows without limit.
_NOTICED: set = set()
_NOTICED_LIMIT = 4096


def _notice(view: Any, name: str, reason: str, *, warn: bool = False) -> None:
    """Say once that an attribute is not being kept. Never logs a value."""
    owner = type(view)
    key = (owner.__module__, owner.__qualname__, name, reason)
    if key in _NOTICED:
        return
    if len(_NOTICED) >= _NOTICED_LIMIT:
        _NOTICED.clear()
    _NOTICED.add(key)
    level = logging.DEBUG
    if warn:
        level = logging.WARNING
    else:
        try:
            from django.conf import settings

            if settings.configured and settings.DEBUG:
                level = logging.INFO
        except Exception:  # noqa: BLE001 — settings unavailable: keep the quiet level
            level = logging.DEBUG
    logger.log(
        level,
        "djust: %s.%s.%s is not kept between requests (%s). It comes from mount() "
        "on the next request or reconnect; name it with a leading underscore or "
        "declare state(persist=False) to say that is intended.",
        owner.__module__,
        owner.__qualname__,
        name,
        reason,
    )


# ---------------------------------------------------------------------------
# The class digest: "was this envelope written by the same code?"
# ---------------------------------------------------------------------------


def _constant(value: Any, out: list, depth: int = 0) -> None:
    """Canonical text for a code constant or a plain class attribute.

    Sets are sorted: their iteration order depends on the process's hash seed,
    and two processes of one build must agree.
    """
    code = getattr(value, "co_code", None)
    if code is not None:
        _code(value, out, depth + 1)
    elif isinstance(value, (frozenset, set)):
        parts: list = []
        for item in value:
            inner: list = []
            _constant(item, inner, depth + 1)
            parts.append("".join(inner))
        out.append("set(" + ",".join(sorted(parts)) + ")")
    elif isinstance(value, (tuple, list)) and depth < 8:
        out.append(type(value).__name__ + "(")
        for item in value:
            _constant(item, out, depth + 1)
            out.append(",")
        out.append(")")
    elif isinstance(value, dict) and depth < 8:
        out.append("dict(")
        for key in sorted(value, key=repr):
            _constant(key, out, depth + 1)
            out.append(":")
            _constant(value[key], out, depth + 1)
            out.append(",")
        out.append(")")
    elif (
        value is None
        or type(value) in (str, bytes, int, float, bool, complex)
        or (value is Ellipsis)
    ):
        out.append(type(value).__name__ + ":" + repr(value))
    else:
        kind = type(value)
        out.append("object:%s.%s" % (kind.__module__, kind.__qualname__))


def _code(code: Any, out: list, depth: int = 0) -> None:
    """What a function does, without where it is: no file name, no line numbers."""
    if depth > 16:
        return
    out.append("code(")
    out.append(code.co_code.hex())
    out.append(repr((code.co_names, code.co_varnames, code.co_freevars, code.co_cellvars)))
    out.append(repr((code.co_argcount, code.co_posonlyargcount, code.co_kwonlyargcount)))
    for const in code.co_consts:
        _constant(const, out, depth)
        out.append(",")
    out.append(")")


def _member(value: Any, out: list) -> None:
    from ._state import StateProperty

    if issubclass(type(value), StateProperty):
        exposure = value.exposure
        out.append("state(%r,%r,%r)" % (exposure.persist, exposure.client, value.render))
        _constant(value.default, out)
        return
    if isinstance(value, (staticmethod, classmethod)):
        value = value.__func__
    if isinstance(value, property):
        for accessor in (value.fget, value.fset, value.fdel):
            _member(accessor, out)
        return
    hashed = False
    for _ in range(8):
        code = getattr(value, "__code__", None)
        if code is None:
            break
        _code(code, out)
        hashed = True
        defaults = getattr(value, "__defaults__", None)
        if defaults:
            _constant(defaults, out)
        value = getattr(value, "__wrapped__", None)
        if value is None:
            break
    if not hashed:
        _constant(value, out)


def _is_application_class(cls: type) -> bool:
    from ._framework_code import is_djust_production_module

    module = getattr(cls, "__module__", "") or ""
    return not (
        cls is object
        or module == "builtins"
        or module == "django"
        or module.startswith("django.")
        or is_djust_production_module(module)
    )


def _compute_class_digest(view_class: type) -> str:
    from . import __version__

    out: list = [
        "djust=%s" % __version__,
        "owner=%s.%s" % (view_class.__module__, view_class.__qualname__),
        "schema=%d" % declared_schema_version(view_class),
    ]
    for cls in view_class.__mro__:
        if not _is_application_class(cls):
            # Framework code is covered by the djust version above.
            out.append("base=%s.%s" % (cls.__module__, cls.__qualname__))
            continue
        out.append("class=%s.%s{" % (cls.__module__, cls.__qualname__))
        for name, value in sorted(namespace(cls).items()):  # #3151: one atomic copy
            if name.startswith("__") and name.endswith("__") and not callable(value):
                continue  # __doc__, __module__, __firstlineno__, __annotations__, ...
            if name.startswith("_djust_"):
                continue  # caches the framework writes on the class after a render
            out.append(name + "=")
            _member(value, out)
            out.append(";")
        out.append("}")
    return hashlib.sha256("".join(out).encode("utf-8", "backslashreplace")).hexdigest()


def class_digest(view_class: type) -> str:
    """A digest of the view class's own code, equal across processes of one build.

    Covers every application class in the MRO: each function's bytecode,
    names and constants (not its file or line numbers, so an edit elsewhere in
    the module changes nothing), each plain class attribute, each ``state()``
    declaration, ``exposure_schema_version`` and the djust version. Compiled
    once per class and re-checked against the class dictionaries on every use
    (``_exposure._ClassFacts``).

    Not covered: code in other modules that the view calls. Bump
    ``exposure_schema_version`` to discard stored state after such a change.
    """
    facts = _class_facts(view_class)
    if facts is None:
        return _compute_class_digest(view_class)
    digest = facts.code_digest
    if digest is None:
        digest = facts.code_digest = _compute_class_digest(view_class)
    return digest


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------


def _assign_keys(view: Any, attribute: str) -> frozenset:
    value = getattr(view, attribute, None)
    if isinstance(value, dict) or isinstance(value, (list, tuple, set, frozenset)):
        return frozenset(key for key in value if isinstance(key, str))
    return frozenset()


def _unsaved_attrs(view_class: type) -> frozenset:
    """Names framework mixins rebuild in ``mount()`` and never save."""
    names: set = set()
    for owner in view_class.__mro__:
        declared = namespace(owner).get("_djust_unsaved_attrs")
        if isinstance(declared, frozenset):
            names.update(declared)
    return frozenset(names)


class _Names:
    """The per-save name rule for one view: built once, asked per attribute."""

    __slots__ = ("view_class", "framework", "reserved", "sensitive", "skipped")

    def __init__(self, view: Any) -> None:
        from .change_detection import CHANGE_DETECTION_IGNORED
        from .serialization import _resolve_sensitive_fields

        self.view_class = type(view)
        self.framework = getattr(view, "_framework_attrs", frozenset())
        self.reserved = (
            reserved_state_names() | CHANGE_DETECTION_IGNORED | _UNSAFE_KEYS | _TRANSPORT_ATTRS
        )
        self.sensitive = _resolve_sensitive_fields()
        # Reset every render by design, or declared never to change.
        # Also what a framework mixin rebuilds itself (``_djust_unsaved_attrs``).
        self.skipped = (
            _assign_keys(view, "temporary_assigns")
            | _assign_keys(view, "static_assigns")
            | _unsaved_attrs(type(view))
        )

    def eligible(self, name: Any) -> bool:
        """Whether ``name`` may be saved, and may be put back by a restore."""
        if (
            type(name) is not str
            or len(name) > 128
            or not _NAME.fullmatch(name)  # no leading underscore, no dunder, ASCII
            or name in self.reserved
            or name in self.framework
            or name in self.skipped
            or name in self.sensitive
        ):
            return False
        declared = getattr_static(self.view_class, name, _MISSING)
        if declared is _MISSING:
            return True
        for owner in self.view_class.__mro__:
            if name in namespace(owner):
                if not _is_application_class(owner):
                    return False  # a djust (or Django) class defines it: framework configuration
                break
        from ._state import StateProperty

        if type(declared) is StateProperty:
            # ``None`` is "not stated": kept automatically. A field that states
            # ``persist`` ("server", "client" or False) is not ours.
            return declared.exposure.persist is None
        kind = type(declared)
        if any(hasattr(kind, hook) for hook in ("__get__", "__set__", "__delete__")):
            return False  # a method, property, component declaration, ...
        return True  # a plain class-level default (``count = 0``)


def eligible_name(view: Any, name: Any) -> bool:
    """The one name rule, for save and restore alike."""
    return _Names(view).eligible(name)


def _candidates(view: Any) -> Dict[str, Any]:
    """Live values of the attributes the name rule admits (not yet validated)."""
    from ._state import StateProperty

    names = _Names(view)
    found: Dict[str, Any] = {}
    for name, value in list(vars(view).items()):
        if name.startswith("_"):
            continue
        if names.eligible(name):
            found[name] = value
    # A state() field stores its value under ``_state_<name>``.
    for owner in type(view).__mro__:
        for name, declared in namespace(owner).items():  # #3151
            if type(declared) is not StateProperty or name in found:
                continue
            if getattr_static(type(view), name, _MISSING) is not declared:
                continue  # shadowed by a subclass
            if not names.eligible(name):
                continue
            # Read the slot, never the descriptor: reading through it would
            # create the default in ``__dict__``, which change detection would
            # then report as a change made by the turn.
            value = vars(view).get(declared.attr_name or "", _MISSING)
            if value is _MISSING:
                if declared.default_factory is not None:
                    continue  # not created yet: mount() gives the same default
                value = declared.default
            found[name] = value
    return found


# ---------------------------------------------------------------------------
# Projection
# ---------------------------------------------------------------------------


def _encode(values: Dict[str, Any]) -> str:
    return json.dumps(values, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def project(view: Any) -> Tuple[Dict[str, Any], str]:
    """Detached, validated values of what is kept, and their canonical encoding.

    Never raises for a value: one the codec refuses is left out, and when the
    whole does not fit the limits the largest attributes are left out.
    """
    candidates = _candidates(view)
    hook = getattr(view, "_djust_automatic_state_filter", None)
    selected: Dict[str, Any] = {}
    for name, value in candidates.items():
        if type(value) not in _PRIMITIVES:
            _notice(view, name, "a %s cannot be saved" % type(value).__name__)
            continue
        selected[name] = value
    try:
        values = clone_json_state(selected, limits=_LIMITS)
    except ExposureError:
        values = _project_each(view, selected)
    if callable(hook):
        # Framework mixins remove what must not be stored (FormMixin: sensitive
        # form fields). Runs on the detached copy, never on the live values.
        values = hook(values)
    return values, _encode(values)


def _project_each(view: Any, selected: Dict[str, Any]) -> Dict[str, Any]:
    """The slow path: find the attributes that do not serialize or do not fit."""
    kept: Dict[str, Any] = {}
    sizes: Dict[str, int] = {}
    for name, value in selected.items():
        try:
            kept[name] = clone_json_state(value, limits=_LIMITS)
        except StateLimitError:
            _notice(view, name, "it is over the size limit for saved state", warn=True)
            continue
        except ExposureError:
            _notice(view, name, "it holds a value that cannot be saved")
            continue
        sizes[name] = len(json.dumps(kept[name], ensure_ascii=False))
    while kept:
        try:
            return dict(clone_json_state(kept, limits=_LIMITS))
        except StateLimitError:
            largest = max(sizes, key=lambda item: (sizes[item], item))
            _notice(view, largest, "the saved state would be over its size limit", warn=True)
            del kept[largest], sizes[largest]
        except ExposureError:  # pragma: no cover — each value already validated
            return {}
    return {}


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


class _Store:
    """The session, key and binding of one view's automatic envelope."""

    __slots__ = ("session", "key", "binding", "digest", "max_age")

    def __init__(self, session: Any, key: str, binding: Any, digest: str, max_age: int) -> None:
        self.session = session
        self.key = key
        self.binding = binding
        self.digest = digest
        self.max_age = max_age


def _store(view: Any, request: Any, *, create: bool) -> Optional[_Store]:
    """Where this view's automatic state lives, or None when it cannot be kept."""
    from ._exposure_sessions import request_binding, server_state_max_age, session_store_supported

    session = getattr(request, "session", None)
    if session is None or not session_store_supported(type(session)):
        # A cookie session is readable by the browser; an unknown store is not
        # audited. Either way nothing is written (system check ``djust.C030``).
        _notice(view, "*", "this request's session store cannot hold server state")
        return None
    if not session.session_key:
        if not create:
            return None
        session.create()
    view_class = type(view)
    try:
        binding = request_binding(request)
        max_age = server_state_max_age()
        digest = class_digest(view_class)
    except ExposureError:
        _notice(view, "*", "the request has no usable identity (authentication middleware)")
        return None
    owner = "%s.%s" % (view_class.__module__, view_class.__qualname__)
    identity = json.dumps([owner, binding.view], separators=(",", ":")).encode()
    key = _KEY_PREFIX + hashlib.sha256(identity).hexdigest()
    return _Store(session, key, binding, digest, max_age)


def _read(view: Any, store: _Store) -> Optional[Tuple[Dict[str, Any], int]]:
    """The stored values this view may take back, and when they were written.

    Anything that is not a current envelope of this very code, for this
    session, user, tenant and route, reads as nothing stored.
    """
    raw = store.session.get(store.key)
    if raw is None:
        return None
    try:
        envelope = clone_json_state(raw, limits=_LIMITS)
    except ExposureError:
        return None
    if type(envelope) is not dict or set(envelope) != {
        "version",
        "code",
        "binding",
        "created",
        "expires",
        "values",
    }:
        return None
    created, expires, values = envelope["created"], envelope["expires"], envelope["values"]
    now = int(time.time())
    if (
        type(envelope["version"]) is not int
        or envelope["version"] != _VERSION
        or type(envelope["code"]) is not str
        or envelope["code"] != store.digest
        or type(envelope["binding"]) is not str
        or envelope["binding"] != store.binding.digest
        or type(created) is not int
        or type(expires) is not int
        or created < 0
        or created > now
        or not 0 < expires - created <= 86400
        or now >= min(expires, created + store.max_age)
        or type(values) is not dict
    ):
        return None
    names = _Names(view)
    restorable = {name: value for name, value in values.items() if names.eligible(name)}
    if len(restorable) != len(values):
        logger.debug("djust: stored automatic state held names this view does not keep; dropped")
    return restorable, created


def _basis(view: Any) -> Any:
    return view.__dict__.get("_djust_auto_basis")


def load(view: Any, request: Any) -> Optional[Dict[str, Any]]:
    """Validated stored values to put back after ``mount()``, or None.

    Also records what the store holds for this view (or, when it holds
    nothing, what ``mount()`` produced), so a later save that would write the
    same thing is skipped.
    """
    store = _store(view, request, create=False)
    found = _read(view, store) if store is not None else None
    first = _basis(view) is None
    if found is not None:
        values, created = found
        view.__dict__["_djust_auto_basis"] = (_encode(values), created)
        return values
    if first:
        # Nothing stored: an event that leaves mount()'s state as it is has
        # nothing to save, and so creates no session.
        view.__dict__["_djust_auto_basis"] = (project(view)[1], None)
    return None


def _touches_saved(changed: Any) -> bool:
    """Whether a turn's changed keys (``_compute_changed_keys``) include an
    attribute that is saved: a public name, or a ``state()`` slot. Private
    names and framework bookkeeping are never saved."""
    return any(
        type(key) is not str or not key.startswith("_") or key.startswith("_state_")
        for key in changed
    )


def _still_fresh(basis: Any, now: int) -> bool:
    """Nothing stored (state equals ``mount()``'s), or stored less than half a
    lifetime ago. Past that the unchanged state is rewritten, so an active
    page's state does not expire."""
    written = basis[1]
    if written is None:
        return True
    from ._exposure_sessions import server_state_max_age

    try:
        max_age = server_state_max_age()
    except ExposureError:
        return True
    return bool(now - written < max_age // 2)


def stage(view: Any, request: Any) -> Optional[Callable[[], None]]:
    """Put the view's current state in the session dict, unflushed.

    Returns an undo for the caller to run if the save it is part of is
    refused, or None when nothing needed writing. The caller flushes the
    session (``save_merged``): one write for declared and automatic state.

    Cost (``unchanged_save_skip``): when the turn's change detection already
    found no saved attribute changed (``_djust_auto_changed``, set by the
    runtime from the snapshots it takes anyway), nothing is walked at all.
    Otherwise the state is serialized once, and written only when its encoding
    differs from what the store holds.
    """
    changed = view.__dict__.get("_djust_auto_changed")
    if changed is not None:
        view.__dict__["_djust_auto_changed"] = None  # one turn's answer, used once
    basis = _basis(view)
    skip = _per_turn.on("unchanged_save_skip")
    now = int(time.time())
    if (
        skip
        and basis is not None
        and changed is not None
        and not _touches_saved(changed)
        and _still_fresh(basis, now)
    ):
        return None
    values, encoded = project(view)
    if basis is None and not values:
        return None  # nothing to keep, and nothing known to be stored
    if skip and basis is not None and basis[0] == encoded and _still_fresh(basis, now):
        return None
    store = _store(view, request, create=True)
    if store is None:
        return None
    envelope = {
        "version": _VERSION,
        "code": store.digest,
        "binding": store.binding.digest,
        "created": now,
        "expires": now + store.max_age,
        "values": values,
    }
    session = store.session
    previous = session.get(store.key, _MISSING)
    if session.session_key != store.binding.session:
        raise ExposureError("State binding does not match the current session")
    session[store.key] = envelope
    view.__dict__["_djust_auto_basis"] = (encoded, now)
    view.__dict__["_djust_auto_bytes"] = len(_encode(envelope))

    def undo() -> None:
        if previous is _MISSING:
            session.pop(store.key, None)
        else:
            session[store.key] = previous
        view.__dict__["_djust_auto_basis"] = basis

    return undo


def discard(view: Any, request: Any) -> bool:
    """A full page load is a fresh ``mount()``: drop this route's stored state.

    Returns whether the session dict changed (the caller flushes). Creates no
    session.
    """
    store = _store(view, request, create=False)
    if store is None or store.session.get(store.key, _MISSING) is _MISSING:
        return False
    del store.session[store.key]
    return True


# ---------------------------------------------------------------------------
# Inspecting and clearing saved view state (``djust stats`` / ``djust clear``, #4087)
# ---------------------------------------------------------------------------

#: Session keys that hold saved view state: the automatic envelope of a view,
#: the declared ``persist="server"`` envelope, and the child-slot index.
SAVED_STATE_PREFIXES = (_KEY_PREFIX, "_djust_explicit_")


def saved_state_keys(session: Any) -> list:
    """The keys of one session that hold saved view state."""
    return sorted(
        key
        for key in session.keys()
        if isinstance(key, str) and key.startswith(SAVED_STATE_PREFIXES)
    )


def saved_state_stats(session: Any) -> Dict[str, int]:
    """How many saved-state entries one session holds, and their encoded size."""
    keys = saved_state_keys(session)
    size = 0
    for key in keys:
        try:
            size += len(json.dumps(session[key], separators=(",", ":"), ensure_ascii=False))
        except (TypeError, ValueError):
            continue
    return {"entries": len(keys), "bytes": size}


def clear_saved_state(session: Any) -> int:
    """Remove every saved view state from one session and save it; the count removed.

    The session's other keys (the login, the application's own) are left alone.
    """
    keys = saved_state_keys(session)
    for key in keys:
        del session[key]
    if keys:
        session.save()
    return len(keys)


def database_sessions_with_saved_state() -> Any:
    """Every stored session that holds saved view state, for the ``db`` and
    ``cached_db`` engines (the only ones Django can enumerate).

    Yields a loaded ``SessionStore`` per session; pass it to
    :func:`saved_state_stats` or :func:`clear_saved_state`. Yields nothing for
    an engine that cannot list its sessions.
    """
    from importlib import import_module

    from django.conf import settings

    from ._exposure_sessions import CachedDBSession, DBSession

    store = getattr(import_module(settings.SESSION_ENGINE), "SessionStore", None)
    if store is None or not issubclass(store, (DBSession, CachedDBSession)):
        return
    model = store.get_model_class()
    for session_key in model.objects.values_list("session_key", flat=True).iterator():
        session = store(session_key)
        if saved_state_keys(session):
            yield session
