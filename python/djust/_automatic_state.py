"""Automatic server-side state: what a view sets on ``self`` survives (#4107).

A page view's public, saveable instance attributes are saved in the
server-side session after each event and put back after ``mount()`` on the
next request or reconnect. Nothing here grants client exposure: the values are
written from, and restored to, server-side attributes only. Template context,
the client projection, signed snapshots and debug payloads keep reading
declarations (ADR-038 D1).

The rules, in one place (``docs/adr/notes/038-amendment-automatic-server-state.md``):

* **Names** (:class:`_Names`): public ASCII identifiers that are not
  framework-owned, not reserved, not sensitive, and not a method, property or
  other descriptor of the class. A ``state()`` field takes part unless it
  states a ``persist`` of its own. The SAME predicate decides what is saved and
  what a stored envelope may put back.
* **Values**: every plain Python value, with its type
  (:func:`djust.serialization.encode_state_value`): the JSON types, ``tuple``,
  ``set``, ``frozenset``, ``Decimal``, dates and times, ``UUID``, ``bytes`` and
  enum members. Nothing is stringified and nothing is unpickled.
* **Whole or refused**: the state is never saved in part. Over its size limit
  the event's change is refused (:class:`StateLimitExceeded`, the #4095 path).
* **Reported**: an attribute that cannot be saved is skipped with a WARNING,
  once per view class and attribute, naming it and the fix; it is never turned
  into a string. Because something of the view is then missing from the store,
  the next request runs ``mount()`` again before the saved state is put back
  (1.3's ``__private_unsaved`` rule, for public and private attributes alike).
* **Private attributes** set by ``mount()`` are saved beside the public ones,
  as 1.3 saved them, so a later request can skip ``mount()``.
* **No versioning**: as in 1.3, saved state is applied to whatever code is
  running; a name that is now a method or property is skipped.
* **Binding**: the session, user, tenant and route, and the declared
  envelope's lifetime.

A view on the deprecated ``exposure_policy = "legacy"`` takes no part.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from inspect import getattr_static
from typing import Any, Callable, Dict, Optional, Tuple

from . import _per_turn
from ._class_snapshot import namespace
from ._exposure import (
    _NAME,
    _UNSAFE_KEYS,
    ExposureError,
    StateLimits,
    _class_facts,
    reserved_state_names,
    uses_legacy_exposure,
)

logger = logging.getLogger("djust.automatic_state")

#: Envelope format. A reader discards any other.
_VERSION = 3
_KEY_PREFIX = "_djust_auto_"
_MISSING: Any = object()
#: Public names a transport sets per connection, and this module's own settings.
_NOT_STATE = frozenset({"client_timezone", "automatic_state_max_kb"})
_PRIVATE = re.compile(r"_[A-Za-z][A-Za-z0-9_]*\Z", re.ASCII)
#: Framework slots that exist after ``mount()`` on some paths: never saved.
_PRIVATE_BOOKKEEPING = frozenset(
    {"_framework_attrs", "_user_private_keys", "_reactive_state", "_lazy_thunks"}
)

#: Hard cap on a view's automatic state, encoded, and the size at which a
#: WARNING names the largest attributes. 512 KB stays under a Memcached item
#: (1 MB) once Django has encoded the session; ``db``, ``file`` and Redis hold
#: far more. Settings: ``DJUST_AUTOMATIC_STATE_MAX_KB`` /
#: ``DJUST_AUTOMATIC_STATE_WARN_KB``; per view: ``automatic_state_max_kb``.
DEFAULT_MAX_KB = 512
DEFAULT_WARN_KB = 100
_MAX_NODES = 200000
_MAX_DEPTH = 32

#: Reports already made, so each (class, attribute, kind) is reported once per
#: process. Bounded: emptied when full, which repeats a report.
_NOTICED: set = set()
_NOTICED_LIMIT = 4096


def _debug() -> bool:
    try:
        from django.conf import settings

        return bool(settings.configured and settings.DEBUG)
    except Exception:  # noqa: BLE001 — settings unavailable: behave as production
        return False


def _first_time(view: Any, name: str, kind: str) -> bool:
    owner = type(view)
    key = (owner.__module__, owner.__qualname__, name, kind)
    if key in _NOTICED:
        return False
    if len(_NOTICED) >= _NOTICED_LIMIT:
        _NOTICED.clear()
    _NOTICED.add(key)
    return True


def _report(view: Any, name: str, kind: str, reason: str, fix: str) -> None:
    """Tell the developer once that ``name`` is not kept. Never logs a value,
    never raises: an application that holds ``self.user = request.user`` keeps
    running."""
    if not _first_time(view, name, kind):
        return
    owner = type(view)
    logger.warning(
        "djust: %s.%s.%s is not saved between requests: %s. %s",
        owner.__module__,
        owner.__qualname__,
        name,
        reason,
        fix,
    )


_FIX = (
    "mount() runs again on the next request to re-create it; to avoid that, keep "
    "only an id and load the value in handle_restore() or get_context_data()."
)
_FIX_ROWS = (
    "mount() runs again on the next request to query it; to avoid that, keep the "
    "filter values and query in handle_restore() or get_context_data()."
)


def _unsaveable(view: Any, name: str, value: Any, reason: str) -> None:
    from django.contrib.auth.base_user import AbstractBaseUser
    from django.contrib.auth.models import AnonymousUser
    from django.contrib.sessions.backends.base import SessionBase
    from django.db.models import Manager
    from django.db.models.query import QuerySet
    from django.forms import BaseForm
    from django.http import HttpRequest
    from django.utils.functional import LazyObject, Promise

    if isinstance(value, (QuerySet, Manager)):
        _report(view, name, "rows", "a queryset is never saved", _FIX_ROWS)
    elif isinstance(
        value,
        (HttpRequest, SessionBase, LazyObject, Promise, BaseForm, AnonymousUser, AbstractBaseUser),
    ):
        # Belongs to one request (the request, its user, a lazy value, a form).
        _report(
            view,
            name,
            "request",
            "a %s belongs to one request and is never saved" % type(value).__name__,
            "mount() runs again on the next request and reads it from that request.",
        )
    else:
        _report(view, name, "type", reason, _FIX)


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------


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


def _assign_keys(view: Any, attribute: str) -> frozenset:
    value = getattr(view, attribute, None)
    if isinstance(value, (dict, list, tuple, set, frozenset)):
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
    """The name rule for one view: built once per save, asked per attribute."""

    __slots__ = ("view_class", "framework", "reserved", "floor", "skipped")

    def __init__(self, view: Any) -> None:
        from .change_detection import CHANGE_DETECTION_IGNORED
        from .serialization import _resolve_sensitive_fields

        self.view_class = type(view)
        self.framework = getattr(view, "_framework_attrs", frozenset())
        self.reserved = (
            reserved_state_names() | CHANGE_DETECTION_IGNORED | _UNSAFE_KEYS | _NOT_STATE
        )
        self.floor = _resolve_sensitive_fields()
        # Reset every render by design, declared never to change, or rebuilt
        # by a framework mixin (``_djust_unsaved_attrs``).
        self.skipped = (
            _assign_keys(view, "temporary_assigns")
            | _assign_keys(view, "static_assigns")
            | _unsaved_attrs(self.view_class)
        )

    def sensitive(self, name: str) -> bool:
        """The serialization floor: ``password``, ``is_staff``, ``is_superuser``
        and ``settings.DJUST_SENSITIVE_FIELDS`` (the explicit list, as in 1.3)."""
        return name in self.floor

    def private(self, name: Any) -> bool:
        """Whether ``name`` is an application ``_private`` attribute that may be
        saved and put back: a single leading underscore, not framework-owned,
        not a ``state()`` slot, and not a class-level method or descriptor."""
        if (
            type(name) is not str
            or len(name) > 128
            or not _PRIVATE.fullmatch(name)
            or name.startswith(("_djust_", "_state_"))
            or name in self.reserved
            or name in self.framework
            or name in _PRIVATE_BOOKKEEPING
        ):
            return False
        declared = getattr_static(self.view_class, name, _MISSING)
        if declared is _MISSING:
            return True
        kind = type(declared)
        return not any(hasattr(kind, hook) for hook in ("__get__", "__set__", "__delete__"))

    def application(self, name: Any) -> bool:
        """Whether ``name`` is an application attribute at all (sensitivity aside)."""
        if (
            type(name) is not str
            or len(name) > 128
            or not _NAME.fullmatch(name)  # no leading underscore, no dunder, ASCII
            or name in self.reserved
            or name in self.framework
            or name in self.skipped
        ):
            return False
        declared = getattr_static(self.view_class, name, _MISSING)
        if declared is _MISSING:
            return True
        for owner in self.view_class.__mro__:
            if name in namespace(owner):
                if not _is_application_class(owner):
                    return False  # a djust (or Django) class defines it: configuration
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

    def eligible(self, name: Any) -> bool:
        """Whether ``name`` may be saved, and may be put back by a restore."""
        return self.application(name) and not self.sensitive(name)


def eligible_name(view: Any, name: Any) -> bool:
    """The one name rule, for save and restore alike."""
    return _Names(view).eligible(name)


def _candidates(view: Any) -> Dict[str, Any]:
    """Live values of the attributes the name rule admits (not yet encoded)."""
    from ._state import StateProperty

    names = _Names(view)
    found: Dict[str, Any] = {}

    def take(name: str, value: Any) -> None:
        if not names.application(name):
            return
        if names.sensitive(name):
            _report(
                view,
                name,
                "sensitive",
                "its name is on the sensitive-field list, and such values are not stored",
                "Read it again in mount() or handle_restore().",
            )
            return
        found[name] = value

    for name, value in list(vars(view).items()):
        if not name.startswith("_"):
            take(name, value)
    # A state() field stores its value under ``_state_<name>``.
    for owner in type(view).__mro__:
        for name, declared in namespace(owner).items():  # #3151
            if type(declared) is not StateProperty or name in found:
                continue
            if getattr_static(type(view), name, _MISSING) is not declared:
                continue  # shadowed by a subclass
            # Read the slot, never the descriptor: reading through it would
            # create the default in ``__dict__``, which change detection would
            # then report as a change made by the turn.
            value = vars(view).get(declared.attr_name or "", _MISSING)
            if value is _MISSING:
                if declared.default_factory is not None:
                    continue  # not created yet: mount() gives the same default
                value = declared.default
            take(name, value)
    return found


def enum_classes(view_class: type) -> frozenset:
    """The enum classes a view's saved state may hold.

    Those the view already references: an attribute of an application class in
    its MRO, or a name in the module that defines one. An enum member is saved
    only if its class is here, and restored only by finding its class here;
    nothing is imported by name.
    """
    facts = _class_facts(view_class)
    if facts is not None and facts.enums is not None:
        return facts.enums
    import enum
    import sys

    found: set = set()
    for cls in view_class.__mro__:
        if not _is_application_class(cls):
            continue
        module = sys.modules.get(cls.__module__)
        scopes = [namespace(cls), namespace(module) if module is not None else {}]
        for scope in scopes:
            for value in scope.values():
                if isinstance(value, type) and issubclass(value, enum.Enum):
                    found.add(value)
    result = frozenset(found)
    if facts is not None:
        facts.enums = result
    return result


# ---------------------------------------------------------------------------
# Projection: whole, or refused
# ---------------------------------------------------------------------------


def _encode(values: Any) -> str:
    return json.dumps(values, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def limits_for(view: Any) -> Tuple[int, int]:
    """``(hard cap, warning threshold)`` in bytes for this view's saved state."""

    def kb(value: Any, default: int) -> int:
        return value if type(value) is int and 0 < value <= 16 * 1024 else default

    maximum, warn = DEFAULT_MAX_KB, DEFAULT_WARN_KB
    try:
        from django.conf import settings

        maximum = kb(getattr(settings, "DJUST_AUTOMATIC_STATE_MAX_KB", None), maximum)
        warn = kb(getattr(settings, "DJUST_AUTOMATIC_STATE_WARN_KB", None), warn)
    except Exception:  # noqa: BLE001 — settings unavailable: the defaults
        logger.debug("djust: settings unavailable; default saved-state limits")
    maximum = kb(getattr_static(type(view), "automatic_state_max_kb", None), maximum)
    return maximum * 1024, min(warn, maximum) * 1024


def _node_limit(maximum: int) -> int:
    """How many values one attribute may hold: a bound on the walk, scaled
    with the byte limit so that a raised ``automatic_state_max_kb`` is the one
    limit an author meets."""
    return max(_MAX_NODES, maximum // 2)


def _refuse(view: Any, name: str, limit: str, maximum: int) -> None:
    from ._exposure_sessions import StateLimitExceeded

    bounds = StateLimits(
        max_bytes=maximum, max_nodes=min(_node_limit(maximum), 1000000), max_depth=_MAX_DEPTH
    )
    refusal = StateLimitExceeded(name, limit, bounds)
    owner = type(view)
    logger.log(
        logging.ERROR if _debug() else logging.WARNING,
        "djust: %s.%s: %s Keep a large value out of the saved state: load it in "
        "handle_restore() or get_context_data(), or raise automatic_state_max_kb.",
        owner.__module__,
        owner.__qualname__,
        refusal,
    )
    raise refusal


def _model_as_dict(value: Any) -> Any:
    """A model instance as 1.3 saved it: its serialized fields, as a dict."""
    from django.db.models import Model

    from .serialization import StateValueError, normalize_django_value

    if not isinstance(value, Model):
        raise StateValueError("a %s cannot be saved" % type(value).__name__)
    return normalize_django_value(value, state_roundtrip=True)


class Projection:
    """A view's saveable state, encoded.

    ``public`` and ``private`` are the encoded attribute values; ``remount``
    says something of the view could not be saved, so the next request must
    run ``mount()`` to have it; ``text`` is the canonical encoding of all three.
    """

    __slots__ = ("public", "private", "remount", "text")

    def __init__(self, public: Dict[str, Any], private: Dict[str, Any], remount: bool) -> None:
        self.public = public
        self.private = private
        self.remount = remount
        self.text = _encode([public, private, remount])


def _private_candidates(view: Any, names: _Names) -> Dict[str, Any]:
    """The ``_private`` attributes ``mount()`` created (``_user_private_keys``,
    recorded right after ``mount()`` as in 1.3, or restored from the store)."""
    recorded = vars(view).get("_user_private_keys") or ()
    return {
        name: vars(view)[name] for name in recorded if name in vars(view) and names.private(name)
    }


def project(view: Any) -> Projection:
    """The view's saveable state: whole, or refused.

    Raises :class:`~djust._exposure_sessions.StateLimitExceeded` when the state
    is over its limit, naming the largest attribute. An attribute that cannot
    be saved at all is reported (:func:`_report`), left out, and marks the
    projection ``remount``.
    """
    from .components.base import LiveComponent
    from .serialization import StateValueError, encode_state_value

    maximum, warn = limits_for(view)
    names = _Names(view)
    enums = enum_classes(type(view))
    remount = False

    def encode(name: str, value: Any, quiet: bool) -> Any:
        nonlocal remount
        try:
            return encode_state_value(
                value,
                max_nodes=_node_limit(maximum),
                max_depth=_MAX_DEPTH,
                enums=enums,
                convert=_model_as_dict,
            )
        except StateValueError as error:
            if error.kind == "structure":
                _refuse(view, name, "structure", maximum)
            remount = True
            # A component is rebuilt by mount(); a private attribute was
            # skipped silently in 1.3 too.
            if not quiet and not isinstance(value, LiveComponent):
                _unsaveable(view, name, value, error.reason)
            return _MISSING

    public: Dict[str, Any] = {}
    for name, value in _candidates(view).items():
        encoded = encode(name, value, False)
        if encoded is not _MISSING:
            public[name] = encoded
    private: Dict[str, Any] = {}
    for name, value in _private_candidates(view, names).items():
        encoded = encode(name, value, True)
        if encoded is not _MISSING:
            private[name] = encoded
    if vars(view).get("_async_tasks"):
        # Work mount() queued has not run (the GET never runs it): the next
        # request runs mount() again so the work exists (1.3's ``__mount_work``).
        remount = True
    hook = getattr(view, "_djust_automatic_state_filter", None)
    if callable(hook):
        # Framework mixins blank what must not be stored (FormMixin: secret
        # form controls). Runs on the encoded copy, never on the live values.
        public = hook(public)
    result = Projection(public, private, remount)
    size = len(result.text.encode("utf-8"))
    if size > warn or size > maximum:
        both = [*public.items(), *private.items()]
        sizes = sorted(((len(_encode({name: item})), name) for name, item in both), reverse=True)
        if size > maximum:
            _refuse(view, sizes[0][1], "bytes", maximum)
        if _first_time(view, "*", "large"):
            owner = type(view)
            logger.warning(
                "djust: %s.%s keeps %d KB of state between requests (limit %d KB). Largest: %s. "
                "A value that can be loaded again belongs in handle_restore() or "
                "get_context_data().",
                owner.__module__,
                owner.__qualname__,
                size // 1024,
                maximum // 1024,
                ", ".join("%s %d KB" % (name, bytes_ // 1024) for bytes_, name in sizes[:3]),
            )
    return result


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

_ENVELOPE = frozenset({"version", "binding", "created", "expires", "values", "private", "remount"})


class _Store:
    """The session, key and binding of one view's envelope."""

    __slots__ = ("session", "key", "binding", "max_age")

    def __init__(self, session: Any, key: str, binding: str, max_age: int) -> None:
        self.session = session
        self.key = key
        self.binding = binding
        self.max_age = max_age


def _store(view: Any, request: Any, *, create: bool) -> Optional[_Store]:
    """Where this view's saved state lives, or None when it cannot be kept."""
    from ._exposure_sessions import request_binding, server_state_max_age, session_store_supported

    session = getattr(request, "session", None)
    if session is None or not session_store_supported(type(session)):
        # A cookie session is readable by the browser; an unknown store is not
        # audited. Nothing is written (system check ``djust.C030``).
        if _first_time(view, "*", "session"):
            logger.warning(
                "djust: view state is not saved between requests: this request's session "
                "store cannot hold server state (use the db, cached_db, cache or file engine)."
            )
        return None
    if not session.session_key:
        if not create:
            return None
        session.create()
    try:
        binding = request_binding(request)
        max_age = server_state_max_age()
    except ExposureError:
        if _first_time(view, "*", "identity"):
            logger.warning(
                "djust: view state is not saved between requests: the request has no "
                "usable identity (AuthenticationMiddleware)."
            )
        return None
    if session.session_key != binding.session:
        return None
    view_class = type(view)
    owner = "%s.%s" % (view_class.__module__, view_class.__qualname__)
    identity = json.dumps([owner, binding.view], separators=(",", ":")).encode()
    key = _KEY_PREFIX + hashlib.sha256(identity).hexdigest()
    return _Store(session, key, binding.digest, max_age)


class Saved:
    """What the store holds for a view: decoded values, and whether ``mount()``
    must run before they are put back."""

    __slots__ = ("public", "private", "remount", "text", "created")

    def __init__(
        self,
        public: Dict[str, Any],
        private: Dict[str, Any],
        remount: bool,
        text: str,
        created: int,
    ) -> None:
        self.public = public
        self.private = private
        self.remount = remount
        self.text = text
        self.created = created


def _read(view: Any, store: _Store) -> Optional[Saved]:
    """The stored state this view may take back.

    Anything that is not a current envelope for this session, user, tenant and
    route reads as nothing stored. Names are checked again with the save's own
    rule, and one that is now a method or property of the class is skipped (as
    1.3's ``restore_public_state`` skipped it).
    """
    from .serialization import StateValueError, decode_state_roundtrip

    envelope = store.session.get(store.key)
    if type(envelope) is not dict or set(envelope) != _ENVELOPE:
        return None
    created, expires = envelope["created"], envelope["expires"]
    public, private = envelope["values"], envelope["private"]
    now = int(time.time())
    if (
        type(envelope["version"]) is not int
        or envelope["version"] != _VERSION
        or type(envelope["binding"]) is not str
        or envelope["binding"] != store.binding
        or type(created) is not int
        or type(expires) is not int
        or created < 0
        or created > now
        or not 0 < expires - created <= 86400
        or now >= min(expires, created + store.max_age)
        or type(public) is not dict
        or type(private) is not dict
        or type(envelope["remount"]) is not bool
    ):
        return None
    names = _Names(view)
    enums = enum_classes(type(view))
    kept_public = {name: item for name, item in public.items() if names.eligible(name)}
    kept_private = {name: item for name, item in private.items() if names.private(name)}
    try:
        return Saved(
            {
                name: decode_state_roundtrip(item, enums=enums, strict=True)
                for name, item in kept_public.items()
            },
            {
                name: decode_state_roundtrip(item, enums=enums, strict=True)
                for name, item in kept_private.items()
            },
            envelope["remount"],
            _encode([public, private, envelope["remount"]]),
            created,
        )
    except StateValueError:
        logger.warning("djust: stored view state could not be restored and was discarded")
        return None


def saved(view: Any, request: Any) -> Optional[Saved]:
    """What the store holds for this view, before deciding whether to mount.

    Records it as the view's basis, so a save that would write the same thing
    is skipped.
    """
    if uses_legacy_exposure(view):
        return None
    store = _store(view, request, create=False)
    found = _read(view, store) if store is not None else None
    if found is not None:
        view.__dict__["_djust_auto_basis"] = (found.text, found.created)
    return found


def apply_private(view: Any, private: Dict[str, Any]) -> None:
    """Put saved ``_private`` attributes back (names already validated)."""
    for name, value in private.items():
        view.__dict__[name] = value
    recorded = set(vars(view).get("_user_private_keys") or ())
    view.__dict__["_user_private_keys"] = recorded | set(private)


def load(view: Any, request: Any) -> Optional[Dict[str, Any]]:
    """The stored public values to put back, or None; private attributes are
    put back here. For a caller that has already run ``mount()``: the socket
    mount and the over-limit rollback (#4095)."""
    first = _basis(view) is None
    found = saved(view, request)
    if found is None:
        if first:
            # A mount with nothing stored (a socket opened without the page
            # GET that normally saves): save what mount() built, so the first
            # refused change has a saved state to go back to (#4095).
            from ._exposure_sessions import StateLimitExceeded
            from ._session_merge import save_merged

            try:
                # Never creates a session here: a socket cannot hand the
                # browser a new session cookie.
                if stage(view, request, create=False) is not None:
                    save_merged(request.session, request.session.session_key)
            except StateLimitExceeded:
                return None  # reported; refused again at the first save
        return None
    # Whether putting it back changes the view mount() just built. When it
    # does not (the page GET saved exactly this), the page the browser holds
    # already shows it and the first event may answer with patches (#1977).
    try:
        # The values only: ``remount`` says what the save could not hold, not
        # what the view shows.
        same = json.loads(project(view).text)[:2] == json.loads(found.text)[:2]
    except (ExposureError, ValueError):
        same = False
    view.__dict__["_djust_auto_restore_same"] = same
    apply_private(view, found.private)
    return found.public


def _basis(view: Any) -> Any:
    return view.__dict__.get("_djust_auto_basis")


def _still_fresh(basis: Any, now: int) -> bool:
    """Stored less than half a lifetime ago. Past that the unchanged state is
    rewritten, so an active page's state does not expire."""
    from ._exposure_sessions import server_state_max_age

    try:
        max_age = server_state_max_age()
    except ExposureError:
        return True
    return bool(now - basis[1] < max_age // 2)


def stage(view: Any, request: Any, *, create: bool = True) -> Optional[Callable[[], None]]:
    """Put the view's current state in the session dict, unflushed.

    Returns an undo for the caller to run if the save it is part of is
    refused, or None when nothing needed writing. The caller flushes the
    session (``save_merged``): one write for declared and automatic state.
    Raises ``StateLimitExceeded`` before touching the session when the state
    is over its limit: nothing is ever saved in part.

    Cost (``unchanged_save_skip``): when the turn's change detection found
    nothing changed (``_djust_auto_changed``, set by the runtime from the
    snapshots it takes anyway), nothing is walked. Otherwise the state is
    encoded once, and written only when the encoding differs from what the
    store holds.
    """
    if uses_legacy_exposure(view):
        return None
    changed = view.__dict__.get("_djust_auto_changed")
    if changed is not None:
        view.__dict__["_djust_auto_changed"] = None  # one turn's answer, used once
    basis = _basis(view)
    skip = _per_turn.on("unchanged_save_skip")
    now = int(time.time())
    if skip and basis is not None and changed is not None and not changed:
        if _still_fresh(basis, now):
            return None
    state = project(view)
    if skip and basis is not None and basis[0] == state.text and _still_fresh(basis, now):
        return None
    store = _store(view, request, create=create)
    if store is None:
        return None
    envelope = {
        "version": _VERSION,
        "binding": store.binding,
        "created": now,
        "expires": now + store.max_age,
        "values": state.public,
        "private": state.private,
        "remount": state.remount,
    }
    session = store.session
    previous = session.get(store.key, _MISSING)
    session[store.key] = envelope
    view.__dict__["_djust_auto_basis"] = (state.text, now)
    view.__dict__["_djust_auto_bytes"] = len(_encode(envelope))

    def undo() -> None:
        if previous is _MISSING:
            session.pop(store.key, None)
        else:
            session[store.key] = previous
        view.__dict__["_djust_auto_basis"] = basis

    return undo


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
