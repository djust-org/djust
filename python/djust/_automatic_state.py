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
import inspect
import json
import logging
import re
import time
import zlib
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
_VERSION = 8
_KEY_PREFIX = "_djust_auto_"
_MISSING: Any = object()
#: Public names a transport sets per connection, and this module's own settings.
_NOT_STATE = frozenset({"client_timezone", "automatic_state_max_kb", "automatic_state"})
#: The switch (#4128). Project: ``DJUST_AUTOMATIC_STATE`` (default on). View:
#: the class attribute ``automatic_state`` (None: the project's value).
SETTING = "DJUST_AUTOMATIC_STATE"
VIEW_ATTRIBUTE = "automatic_state"
#: The names Django's ``View.setup()`` sets on a dispatched view.
_VIEW_SETUP_NAMES = frozenset({"head"})
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


def report_unsaved_page(view: Any, session_written: bool = False) -> None:
    """A page load whose session could not be updated: say so once per view
    class and outcome. Nothing of the view was saved for it.

    djust serves the page. ``session_written``: the application itself also
    wrote to the session in this request, so Django's session middleware will
    try to save it, fail the same way and answer 400 (``SessionInterrupted``).
    """
    if not _first_time(view, "", "session-update-400" if session_written else "session-update"):
        return
    owner = type(view)
    outcome = (
        "This request also wrote to the session itself, so Django's session middleware "
        "will fail to save it too and answer 400 (the session was deleted while the "
        "request was in progress)."
        if session_written
        else "The page is served; mount() runs again on its next request."
    )
    logger.warning(
        "djust: the state of %s.%s was not saved for this page load: the session could "
        "not be updated (it was deleted meanwhile, or the session store refused the "
        "write). %s",
        owner.__module__,
        owner.__qualname__,
        outcome,
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
        if name in _VIEW_SETUP_NAMES and inspect.ismethod(value) and value.__self__ is view:
            # Not state: Django's ``View.setup()`` sets ``self.head = self.get``
            # on every dispatched view. Any OTHER callable (a class, a partial,
            # an object with ``__call__``, the view's own method kept under
            # another name) is a value the view holds: it goes on to the
            # encoder, which refuses it, so it is reported and the next request
            # mounts again.
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


def _digest(text: str) -> bytes:
    """What stands for an encoding where only equality is asked. A connected
    view keeps these, not second copies of its state (#3998)."""
    return hashlib.blake2b(text.encode("utf-8"), digest_size=16).digest()


def _digests(values: Dict[str, Any]) -> Dict[str, bytes]:
    """Per name, the digest of its encoded value."""
    return {name: _digest(_encode(value)) for name, value in values.items()}


def project_saving_enabled() -> bool:
    """The project's default: on, unless ``DJUST_AUTOMATIC_STATE`` is set to
    anything but ``True``. A value that is not a bool turns saving OFF (and
    system check ``djust.C035`` reports it): a project that set the switch
    wrongly must not have its state written while nobody looks."""
    try:
        from django.conf import settings

        value = getattr(settings, SETTING, True)
    except Exception:  # noqa: BLE001 — settings unavailable: nothing can be saved anyway
        return False
    return value is True


def saving_enabled(view: Any) -> bool:
    """Whether this view's state is saved between requests (#4128).

    The view's ``automatic_state`` class attribute decides when it is set
    (``True`` or ``False``), in either direction over the project's
    ``DJUST_AUTOMATIC_STATE``; ``None`` or absent takes the project's value.
    Any other value turns saving off. Never read from the instance: a handler
    or a restored attribute cannot switch saving on.

    Off means nothing of the view is written to or read from the session by
    this module: no envelope at the page load, none after an event, no
    component state, no changed/deleted record. Declared ``persist="server"``
    fields are another store and are not affected.
    """
    if (
        not isinstance(view, type)
        and VIEW_ATTRIBUTE in getattr(view, "__dict__", ())
        and _first_time(view, "*", "instance-switch")
    ):
        owner = type(view)
        logger.warning(
            "djust: %s.%s sets %s on the instance (in mount() or a handler). That is not "
            "read: the switch is a class attribute, `%s = False` in the class body.",
            owner.__module__,
            owner.__qualname__,
            VIEW_ATTRIBUTE,
            VIEW_ATTRIBUTE,
        )
    value = getattr_static(type(view), VIEW_ATTRIBUTE, None)
    if value is None:
        return project_saving_enabled()
    return value is True


def _forget(view: Any, request: Any) -> None:
    """Saving is off for this view: the state saved for this tab before that
    (with its changed/deleted record) is removed from the session, so it is
    neither read nor left at rest. One write, and only when there is one."""
    if uses_legacy_exposure(view) or view.__dict__.get("_djust_auto_forgot"):
        return
    view.__dict__["_djust_auto_forgot"] = True  # once per view object
    try:
        store = _store(view, request, create=False)
    except Exception:  # noqa: BLE001 — no usable session or identity: nothing to remove
        return
    if store is None or store.key not in store.session:
        return
    from ._session_merge import save_merged

    del store.session[store.key]
    save_merged(store.session, store.session.session_key)


def _not_saved(view: Any) -> bool:
    """The one gate every entry point of this module asks first."""
    return uses_legacy_exposure(view) or not saving_enabled(view)


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

    ``public`` and ``private`` are the encoded attribute values, ``components``
    the encoded state of each component the view holds, by attribute name;
    ``remount`` says something of the view could not be saved, so the next
    request must run ``mount()`` to have it; ``text`` is the canonical
    encoding of all four.
    """

    __slots__ = ("public", "private", "remount", "components", "text")

    def __init__(
        self,
        public: Dict[str, Any],
        private: Dict[str, Any],
        remount: bool,
        components: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.public = public
        self.private = private
        self.remount = remount
        self.components = components or {}
        self.text = _encode([public, private, remount, self.components])


# ---------------------------------------------------------------------------
# Component state (1.3's ``<key>_components``)
# ---------------------------------------------------------------------------


def _held_components(view: Any) -> Dict[str, Any]:
    """The components a view holds, by attribute name: public instance
    attributes, and the class-level descriptors this view has materialized.
    ADR-034 interactive components (a ``DropdownMenu``, a keyed collection) are
    left out: their identity is rebuilt per mount (#4046)."""
    from .components._interactive import DropdownMenu
    from .components.base import BoundComponent, Component, LiveComponent

    found: Dict[str, Any] = {}
    held = [(n, v) for n, v in list(vars(view).items()) if not n.startswith("_")]
    held += list((vars(view).get("_component_bindings") or {}).items())
    # A class-level component descriptor caches its per-view binding here.
    held += [
        (n[len("_component_") :], v)
        for n, v in list(vars(view).items())
        if n.startswith("_component_") and n != "_component_bindings"
    ]
    for name, value in held:
        if not isinstance(value, (BoundComponent, Component, LiveComponent)):
            continue
        if isinstance(value, DropdownMenu) or getattr(
            type(value), "_djust_component_collection", False
        ):
            continue
        if type(name) is str and _NAME.fullmatch(name):
            found[name] = value
    return found


def _component_values(component: Any) -> Tuple[Any, Dict[str, Any]]:
    """``(where the values live, the values)``: a component's ``state`` (a
    dict, or a typed state object), else its own public attributes, as 1.3's
    ``_extract_component_state`` read them."""
    state = getattr(component, "state", None)
    if isinstance(state, dict):
        # Its identity is the view's to assign, not saved state.
        return state, {key: value for key, value in state.items() if key != "component_id"}
    holder = state if state is not None and hasattr(state, "__dict__") else component
    values = {
        key: value
        for key, value in list(vars(holder).items())
        if not key.startswith("_")
        and key not in ("template", "template_name", "component_id", "state")
        and not callable(value)
    }
    return holder, values


def _project_components(view: Any, enums: Any) -> Dict[str, Any]:
    from .serialization import StateValueError, encode_state_value

    out: Dict[str, Any] = {}
    for name, component in _held_components(view).items():
        encoded: Dict[str, Any] = {}
        for key, value in _component_values(component)[1].items():
            if type(key) is not str:
                continue
            try:
                encoded[key] = encode_state_value(
                    value, max_nodes=_MAX_NODES, max_depth=_MAX_DEPTH, enums=enums
                )
            except StateValueError:
                continue  # 1.3 skipped a value it could not save, silently
        out[name] = encoded
    return out


def apply_components(view: Any, components: Dict[str, Any]) -> None:
    """Put saved component state back on the components the view holds now
    (after ``mount()`` rebuilt them, or as a class-level descriptor is
    materialized). A name that is no longer a component is skipped."""
    if not components:
        return
    for name, values in components.items():
        if type(name) is not str or not _NAME.fullmatch(name) or type(values) is not dict:
            continue
        try:
            component = getattr(view, name, None)
        except Exception:  # noqa: BLE001 — a descriptor that cannot bind yet
            continue
        if component is None or name not in _held_components(view):
            continue
        holder, current = _component_values(component)
        for key, value in values.items():
            if type(key) is not str or key.startswith("_") or key in _UNSAFE_KEYS:
                continue
            if isinstance(holder, dict):
                if key in current or type(holder) is dict:
                    holder[key] = value
            elif key in current or not hasattr(type(holder), key):
                try:
                    setattr(holder, key, value)
                except (AttributeError, TypeError):
                    continue


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
            # ... but a private callable is reported like a public one: it
            # makes every request mount again, which the author should know.
            if (not quiet or callable(value)) and not isinstance(value, LiveComponent):
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
    components = _project_components(view, enums)
    if vars(view).get("_async_tasks"):
        # Work mount() queued has not run (the GET never runs it): the next
        # request runs mount() again so the work exists (1.3's ``__mount_work``).
        remount = True
    hook = getattr(view, "_djust_automatic_state_filter", None)
    if callable(hook):
        # Framework mixins blank what must not be stored (FormMixin: secret
        # form controls). Runs on the encoded copy, never on the live values.
        public = hook(public)
    result = Projection(public, private, remount, components)
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

_ENVELOPE = frozenset(
    {
        "version",
        "binding",
        "created",
        "expires",
        "values",
        "private",
        "remount",
        "components",
        "changed",
        "deleted",
        "page",
        "bytes",
        "url",
    }
)


class _Store:
    """The session, key and binding of one view's envelope."""

    __slots__ = ("session", "key", "binding", "max_age", "page")

    def __init__(self, session: Any, key: str, binding: str, max_age: int, page: str) -> None:
        self.session = session
        self.key = key
        self.binding = binding
        self.max_age = max_age
        self.page = page  # the view class and route, shared by its tabs


#: A tab id: minted by the page GET, sent back by the page's client. It only
#: selects one of the states inside the caller's own session, so it needs no
#: signature: the envelope stays bound to the session, user, tenant and route.
_TAB = re.compile(r"[A-Za-z0-9_-]{8,64}\Z", re.ASCII)
#: Tab states kept per session, view and route; the oldest goes first. A page
#: load mints a new tab, so this is also how many reloads' states can linger
#: until they expire.
MAX_TABS = 8
TAB_HEADER = "X-Djust-Tab"
TAB_META = "djust-tab"


_HEAD_END = re.compile(r"</head\s*>", re.IGNORECASE)
_HTML_START = re.compile(r"""<html(?:\s(?:[^>"']|"[^"]*"|'[^']*')*)?>""", re.IGNORECASE)
_DOCTYPE = re.compile(r"\s*<!doctype[^>]*>", re.IGNORECASE)


def page_with_tab(html: str, tab: str) -> str:
    """The page with its tab id in a ``<meta name="djust-tab">``, where the
    client reads it (``document.head``)."""
    if not tab_id(tab):
        return html
    meta = '<meta name="%s" content="%s">' % (TAB_META, tab)
    found = _HEAD_END.search(html)
    if found is not None:
        at = found.start()
    else:
        # No <head>: right after <html>, else after the doctype, else in
        # front. The browser's parser puts a leading <meta> in the head it
        # creates. Never before a doctype, which would mean quirks mode.
        found = _HTML_START.search(html) or _DOCTYPE.search(html)
        at = found.end() if found is not None else 0
    return html[:at] + meta + html[at:]


def new_tab_id() -> str:
    """A fresh tab id for a page load."""
    import secrets

    return secrets.token_urlsafe(12)


def tab_id(value: Any) -> str:
    """A tab id a client sent, or ``""`` when it sent none or sent anything
    that is not one. ``""`` selects nothing: no state is read or saved."""
    return value if type(value) is str and _TAB.fullmatch(value) else ""


def _page_key(view: Any, binding_view: str) -> str:
    view_class = type(view)
    owner = "%s.%s" % (view_class.__module__, view_class.__qualname__)
    identity = json.dumps([owner, binding_view], separators=(",", ":")).encode()
    return hashlib.sha256(identity).hexdigest()


def _store(view: Any, request: Any, *, create: bool) -> Optional[_Store]:
    """Where this view's saved state lives, or None when it cannot be kept.

    One state per tab: the key is the view class, the route and the tab id the
    page load was given (``view._djust_tab``). Without a tab id nothing is read
    or saved, so a request can never be handed another tab's state."""
    tab = tab_id(vars(view).get("_djust_tab"))
    if not tab:
        return None
    from ._exposure_sessions import request_binding, server_state_max_age, session_store_supported

    session = getattr(request, "session", None)
    if session is None or not session_store_supported(type(session)):
        # A cookie session is readable by the browser; a store that is not one
        # of Django's server-side stores (or a subclass) is not known to keep
        # its data on the server. Nothing is written (system check ``djust.C034``).
        if _first_time(view, "*", "session"):
            logger.warning(
                "djust: view state is not saved between requests: this request's session "
                "store (%s) is not a server-side store djust knows. Use the db, cached_db, "
                "cache or file engine or a subclass of one, or set %s = False to say that "
                "nothing is to be saved.",
                "none" if session is None else getattr(type(session), "__module__", "<unknown>"),
                SETTING,
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
    page = _page_key(view, binding.view)
    key = _KEY_PREFIX + hashlib.sha256((page + "|" + tab).encode()).hexdigest()
    return _Store(session, key, binding.digest, max_age, page)


class Saved:
    """What the store holds for a view: decoded values, the names a handler
    changed since ``mount()``, and whether ``mount()`` must run before they are
    put back."""

    __slots__ = (
        "public",
        "private",
        "remount",
        "components",
        "changed",
        "deleted",
        "raw",
        "text",
        "created",
        "url",
    )

    def __init__(
        self,
        public: Dict[str, Any],
        private: Dict[str, Any],
        remount: bool,
        components: Dict[str, Any],
        changed: frozenset,
        deleted: frozenset,
        raw: Tuple[Dict[str, Any], Dict[str, Any]],
        text: str,
        created: int,
        url: Optional[str] = None,
    ) -> None:
        self.url = url  # the query the state was saved under, canonical
        self.public = public
        self.private = private
        self.remount = remount
        self.components = components
        self.changed = changed
        self.deleted = deleted  # names a handler removed from the view
        self.raw = raw  # the encoded public and private values, as stored
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
    components, changed = envelope["components"], envelope["changed"]
    deleted = envelope["deleted"]
    now = int(time.time())
    if (
        type(envelope["version"]) is not int
        or envelope["version"] != _VERSION
        or type(envelope["binding"]) is not str
        or envelope["binding"] != store.binding
        or type(created) is not int
        or type(expires) is not int
        or created < 0
        or not 0 < expires - created <= 86400
        or now >= min(expires, created + store.max_age)
        or type(public) is not dict
        or type(private) is not dict
        or type(components) is not dict
        or type(changed) is not list
        or type(deleted) is not list
        or type(envelope["bytes"]) is not int
        or type(envelope["remount"]) is not bool
    ):
        return None
    from ._exposure_sessions import written_in_the_future

    if written_in_the_future(created, now, "saved view state"):
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
            {
                name: decode_state_roundtrip(item, enums=enums, strict=True)
                for name, item in components.items()
                if type(item) is dict
            },
            frozenset(name for name in changed if type(name) is str),
            frozenset(name for name in deleted if names.eligible(name) or names.private(name)),
            (kept_public, kept_private),
            _encode([public, private, envelope["remount"], components]),
            created,
            envelope["url"] if type(envelope["url"]) is str else None,
        )
    except StateValueError:
        logger.warning("djust: stored view state could not be restored and was discarded")
        return None


class _Basis:
    """What a view's next save is compared with, and what a refused turn goes
    back to.

    Held by every connected view, so it holds one encoding and digests, never
    a second copy of the values (#3998):

    ``stored`` / ``created``: the digest of the encoding the store holds (or
    the view held when its mount sequence finished) and when it was written; a
    save equal to it is skipped. ``public`` / ``private``: per name, the digest
    of the encoded value at that point. ``changed``: the names a handler, or a
    later lifecycle step, has changed since ``mount()``. A save adds every name
    whose digest differs from this basis, and the record is stored with the
    state; a page GET starts it empty again. ``rollback``: the encoding of the
    view at that point (a ``Projection.text``, compressed when it is long),
    decoded only when a refused turn is rolled back.
    """

    __slots__ = (
        "stored",
        "created",
        "public",
        "private",
        "changed",
        "deleted",
        "rollback",
        "names",
    )

    def __init__(
        self,
        stored: bytes,
        created: Optional[int],
        public: Dict[str, bytes],
        private: Dict[str, bytes],
        changed: frozenset,
        deleted: frozenset,
        rollback: Any,
        names: Optional[frozenset] = None,
    ) -> None:
        self.stored = stored
        self.created = created
        self.public = public
        self.private = private
        self.changed = changed
        self.deleted = deleted
        self.rollback = rollback
        # Every application attribute the view had then, saveable or not
        # (None: read from the store, where that is not known).
        self.names = names

    def values(self) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
        """The encoded public, private and component values to roll back to."""
        text = self.rollback
        if type(text) is bytes:
            text = zlib.decompress(text).decode("utf-8")
        public, private, _remount, components = json.loads(text)
        return public, private, components


#: A rollback encoding longer than this is kept compressed: a list page's
#: state is rows that repeat their keys, and every open socket holds one.
_ROLLBACK_PLAIN = 2048


def _rollback(text: str) -> Any:
    if len(text) <= _ROLLBACK_PLAIN:
        return text
    return zlib.compress(text.encode("utf-8"), 1)


def _present(view: Any) -> frozenset:
    """The application attributes a view has now, public and private, whatever
    their values."""
    names = _Names(view)
    return frozenset(
        name for name in list(vars(view)) if names.application(name) or names.private(name)
    )


def _basis(view: Any) -> Optional[_Basis]:
    basis = view.__dict__.get("_djust_auto_basis")
    return basis if isinstance(basis, _Basis) else None


def saved(view: Any, request: Any) -> Optional[Saved]:
    """What the store holds for this view, before deciding whether to mount.

    Records it as the view's basis, so a save that would write the same thing
    is skipped.
    """
    if _not_saved(view):
        _forget(view, request)
        return None
    store = _store(view, request, create=False)
    found = _read(view, store) if store is not None else None
    # The query the state was saved under, for ``note_url`` to compare with.
    view.__dict__["_djust_auto_saved_url"] = found.url if found is not None else None
    if found is not None:
        view.__dict__["_djust_auto_basis"] = _Basis(
            _digest(found.text),
            found.created,
            _digests(found.raw[0]),
            _digests(found.raw[1]),
            found.changed,
            found.deleted,
            _rollback(found.text),
        )
    elif store is not None and any(
        type(other) is dict and other.get("page") == store.page
        for key, other in list(store.session.items())
        if type(key) is str and key.startswith(_KEY_PREFIX)
    ):
        # A well-formed tab id with no state of its own, while other tabs of
        # this page have one: this tab's state expired or was evicted.
        owner = type(view)
        route = str(getattr(request, "path", ""))[:200].replace("\n", " ").replace("\r", " ")
        # Once per view class and route per process, as the other reports are:
        # a client can send any number of well-formed ids.
        if _first_time(view, route, "evicted"):
            logger.warning(
                "djust: no saved state for this tab of %s.%s at %s (expired, or evicted "
                "past the limit of tab states kept per page); the view starts from mount().",
                owner.__module__,
                owner.__qualname__,
                route,
            )
    return found


def apply_private(view: Any, private: Dict[str, Any]) -> None:
    """Put saved ``_private`` attributes back (names already validated)."""
    for name, value in private.items():
        view.__dict__[name] = value
    recorded = set(vars(view).get("_user_private_keys") or ())
    view.__dict__["_user_private_keys"] = recorded | set(private)


def load(view: Any, request: Any) -> Optional[Dict[str, Any]]:
    """After ``mount()`` has run (the socket mount, the over-limit rollback):
    the stored public values to put back, or None. Private attributes and
    component state are put back here.

    Only what a handler changed goes back (``Saved.changed``): a value
    ``mount()`` computed and no handler touched stays as this ``mount()`` built
    it (a permission flag read from the user is fresh).
    """
    settled = _basis(view)
    first = settled is None
    if settled is not None:
        # The over-limit rollback (#4095): back to what this view held when
        # its mount sequence finished, or at its last save, whichever is later
        # (kept in memory). Not to the store: after a socket mount the store
        # may still hold the page GET's mount()-computed values, which this
        # connection's mount() has since replaced (a revoked permission flag).
        then_public, then_private, then_components = settled.values()
        restored = put_back(
            view,
            _decoded(view, then_public),
            _decoded(view, then_private),
            _decoded(view, then_components),
            over_mount=True,
        )
        if settled.names is not None:
            # What the refused handler ADDED goes too: left on the view, an
            # oversized new attribute would refuse every later save, and a
            # small one would be saved by the next event though its turn was
            # refused.
            for name in _present(view) - settled.names:
                vars(view).pop(name, None)
        return restored
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
    # does not, nothing is applied, and the page the browser holds already
    # shows the view's state, so the first event may answer with patches
    # (#1977).
    try:
        mine = project(view)
        same = mine.components == _encoded_components(found, mine) and all(
            found.raw[0].get(name, _MISSING) == mine.public.get(name, _MISSING)
            if name in found.raw[0]
            else found.raw[1].get(name, _MISSING) == mine.private.get(name, _MISSING)
            for name in found.changed
            if name in found.raw[0] or name in found.raw[1]
        )
    except (ExposureError, ValueError):
        same = False
    view.__dict__["_djust_auto_restore_same"] = same
    public = put_back(
        view,
        found.public,
        found.private,
        found.components,
        over_mount=True,
        changed=found.changed,
        deleted=found.deleted,
    )
    # The socket mount calls handle_params() after this restore (the URL wins
    # over a declared field, #3624); what was put back here goes on again after
    # it (``reapply``), so the hook's defaults do not reset what a handler set.
    view.__dict__["_djust_auto_reapply"] = (public, found.private, found.changed)
    return public


def _decoded(view: Any, values: Dict[str, Any]) -> Dict[str, Any]:
    from .serialization import decode_state_roundtrip

    enums = enum_classes(type(view))
    return {
        name: decode_state_roundtrip(item, enums=enums, strict=True)
        for name, item in values.items()
    }


def _encoded_components(found: Saved, mine: Projection) -> Dict[str, Any]:
    """The stored component state as it would be encoded now, for comparing."""
    from .serialization import encode_state_value

    try:
        return {
            name: {key: encode_state_value(value) for key, value in values.items()}
            for name, values in found.components.items()
        }
    except ValueError:
        return {"": None}  # not comparable: treat as different


def reapply(view: Any) -> Dict[str, Any]:
    """After ``handle_params()`` on a mount that restored: the public values
    to assign again (private ones are assigned here). Used once."""
    pending = view.__dict__.get("_djust_auto_reapply")
    view.__dict__["_djust_auto_reapply"] = None
    if not pending:
        return {}
    public, private, changed = pending
    return put_back(view, public, private, {}, over_mount=True, changed=changed)


def finish_mount(view: Any) -> None:
    """The end of a socket mount: what the restore put back goes on again over
    ``handle_params()``'s defaults, unless the URL changed since the state was
    saved (:func:`url_changed`), and the view is settled (:func:`settle`)."""
    from .security import safe_setattr

    declared = view.__dict__.get("_djust_auto_reapply_declared")
    view.__dict__["_djust_auto_reapply_declared"] = None
    if url_changed(view):
        view.__dict__["_djust_auto_reapply"] = None  # handle_params() has the last word
    else:
        again = reapply(view)
        if declared and url_unchanged(view):
            # Declared persist="server" fields follow the same rule: at the
            # URL they were saved under, the saved value wins over the hook's.
            again = {**again, **declared}
        for key, value in again.items():
            safe_setattr(view, key, value, allow_private=False, raise_on_blocked=True)
    settle(view)


# ---------------------------------------------------------------------------
# The URL: saved state wins while it is the same, handle_params() when it changed
# ---------------------------------------------------------------------------


def canonical_params(params: Any) -> str:
    """A query's parameters in one canonical form, so that two spellings of
    the same query compare equal: parsed values, sorted by key.
    No query and an empty query are the same (``"[]"``).

    A repeated key (``?a=1&a=2``) counts by ALL its values, in order: every
    transport hands ``handle_params()`` the list (#4125), so ``?a=1&a=2`` and
    ``?a=3&a=2`` are two URLs. A key given once compares as before (one pair).
    The sort is by key only and stable, so the order of a key's values counts."""
    pairs = []
    try:
        lists = params.lists() if hasattr(params, "lists") else None
        items = lists if lists is not None else dict(params or {}).items()
        for key, value in items:
            values = value if isinstance(value, (list, tuple)) else [value]
            for item in values:
                pairs.append((str(key), "" if item is None else str(item)))
    except Exception:  # noqa: BLE001 — not a mapping: no parameters
        pairs = []
    return json.dumps(sorted(pairs, key=lambda pair: pair[0]), separators=(",", ":"))


def note_url(view: Any, params: Any) -> None:
    """``handle_params()`` is about to read ``params``: remember them as the
    view's URL (saved with its state), and whether they differ from the URL the
    restored state was saved under.

    The rule (#4107): while the URL is the one the state was saved under, the
    saved state wins over what ``handle_params()`` assigns (a handler-set tab
    survives the hook's default). When the URL changed, ``handle_params()``
    wins for what it assigns (``?page=3`` shows page 3); everything it does not
    assign keeps its restored value.
    """
    if _not_saved(view):
        return
    now = canonical_params(params)
    saved_under = view.__dict__.get("_djust_auto_saved_url")
    view.__dict__["_djust_auto_url_changed"] = saved_under is not None and saved_under != now
    view.__dict__["_djust_auto_url"] = now


def note_patched_url(view: Any, params: Any) -> None:
    """A handler called ``live_patch(params=...)``: the page's URL is now that
    one, so the state this turn saves is saved under it."""
    if not _not_saved(view):
        view.__dict__["_djust_auto_url"] = canonical_params(params)


def url_unchanged(view: Any) -> bool:
    """Whether ``handle_params()`` just read the very URL the restored state
    was saved under. False when that URL is not known (nothing was restored)."""
    saved_under = view.__dict__.get("_djust_auto_saved_url")
    return saved_under is not None and saved_under == view.__dict__.get("_djust_auto_url")


def url_changed(view: Any) -> bool:
    """Whether the URL ``handle_params()`` just read differs from the one the
    restored state was saved under."""
    return bool(view.__dict__.get("_djust_auto_url_changed"))


def put_back(
    view: Any,
    public: Dict[str, Any],
    private: Dict[str, Any],
    components: Dict[str, Any],
    *,
    over_mount: bool,
    changed: Optional[frozenset] = None,
    deleted: frozenset = frozenset(),
) -> Dict[str, Any]:
    """The ONE place that decides which saved values go back on a view.

    Returns the public values for the caller to assign; assigns the private
    attributes and the component state itself.

    ``changed`` (the socket mount): only these names go back, the ones a
    handler changed since ``mount()``; everything else stays as this
    ``mount()`` built it. None (the HTTP fallback) puts every saved value back,
    as 1.3 did: there the view is rebuilt from its saved state.

    ``over_mount``: ``mount()`` has just run on this view, so it holds live
    objects: a saved value is never put over a live model instance, which was
    saved only as a dict of its fields.

    A rule that restores less (one tab's state) belongs here and in
    :func:`_store`'s key, nowhere else.
    """
    if changed is not None:
        public = {name: value for name, value in public.items() if name in changed}
        private = {name: value for name, value in private.items() if name in changed}
    if over_mount:
        from .live_view import _holds_model

        live = vars(view)
        public = {
            name: value
            for name, value in public.items()
            if not _holds_model(_live_value(view, name))
        }
        private = {
            name: value for name, value in private.items() if not _holds_model(live.get(name))
        }
    apply_private(view, private)
    apply_components(view, components)
    if over_mount:
        # What a handler removed (``del self.draft``) stays removed after the
        # mount() that just set it again. The names were validated on read.
        for name in deleted:
            vars(view).pop(name, None)
    return public


def _live_value(view: Any, name: str) -> Any:
    """The view's current value for a public name, without creating a
    ``state()`` default."""
    live = vars(view)
    if name in live:
        return live[name]
    return live.get("_state_" + name)


def settle(view: Any) -> None:
    """The mount sequence is over (``mount()`` or the rebuild, the restore,
    ``handle_params()``): what the view holds NOW is what the next handler
    starts from. A save records as changed every name that differs from it.

    Called by the socket mount and the fallback POST before the event. A page
    GET does not call it: its save starts the record empty.
    """
    if _not_saved(view):
        return
    basis = _basis(view)
    try:
        state = project(view)
    except ExposureError:
        return  # over its limit: reported; the first save refuses
    now_digest = _digest(state.text)
    view.__dict__["_djust_auto_basis"] = _Basis(
        # The store's own encoding, so that a turn which changes nothing is not
        # written; the per-name digests and the rollback are the view's.
        now_digest if basis is None or basis.created is None else basis.stored,
        None if basis is None else basis.created,
        _digests(state.public),
        _digests(state.private),
        frozenset() if basis is None else basis.changed,
        frozenset() if basis is None else basis.deleted,
        _rollback(state.text),
        _present(view),
    )
    view.__dict__["_djust_auto_settled"] = now_digest


def _still_fresh(basis: _Basis, now: int) -> bool:
    """Stored less than half a lifetime ago. Past that the unchanged state is
    rewritten, so an active page's state does not expire."""
    if basis.created is None:
        return False
    from ._exposure_sessions import server_state_max_age

    try:
        max_age = server_state_max_age()
    except ExposureError:
        return True
    return bool(now - basis.created < max_age // 2)


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
    view held when its mount sequence finished or last saved.
    """
    if _not_saved(view):
        return None
    hint = view.__dict__.get("_djust_auto_changed")
    if hint is not None:
        view.__dict__["_djust_auto_changed"] = None  # one turn's answer, used once
    basis = _basis(view)
    skip = _per_turn.on("unchanged_save_skip")
    now = int(time.time())
    if skip and basis is not None and hint is not None and not hint:
        if _still_fresh(basis, now):
            return None
    state = project(view)
    settled = view.__dict__.get("_djust_auto_settled")
    now_digest = _digest(state.text)
    unchanged = basis is not None and now_digest in (basis.stored, settled)
    if skip and basis is not None and unchanged and _still_fresh(basis, now):
        return None
    store = _store(view, request, create=create)
    if store is None:
        return None
    # The names a handler (or a later lifecycle step) changed since mount():
    # those already recorded, and every name whose value differs from the
    # basis. With no basis (the page GET's save, a mount with nothing stored)
    # the record starts empty.
    changed: frozenset = frozenset()
    now_public, now_private = _digests(state.public), _digests(state.private)
    if basis is not None:
        changed = basis.changed | frozenset(
            name
            for now_values, then_values in (
                (now_public, basis.public),
                (now_private, basis.private),
            )
            for name, value in now_values.items()
            if then_values.get(name, _MISSING) != value
        )
    # What a handler removed from the view: a name the basis held that the
    # view no longer has at all (not one that merely stopped being saveable).
    deleted: frozenset = frozenset()
    if basis is not None:
        live = vars(view)
        gone = {
            name
            for then_values in (basis.public, basis.private)
            for name in then_values
            if name not in live and "_state_" + name not in live
        }
        deleted = frozenset(
            name
            for name in basis.deleted | gone
            if name not in live and "_state_" + name not in live
        )
        changed = changed | gone
    envelope = {
        "version": _VERSION,
        "binding": store.binding,
        "created": now,
        "expires": now + store.max_age,
        "values": state.public,
        "private": state.private,
        "remount": state.remount,
        "components": state.components,
        "changed": sorted(changed),
        "deleted": sorted(deleted),
        "page": store.page,
        "bytes": len(state.text.encode("utf-8")),
        # The query string the view last read (``note_url``), canonical; None
        # when handle_params() never ran on it.
        "url": view.__dict__.get("_djust_auto_url"),
    }
    session = store.session
    previous = session.get(store.key, _MISSING)
    session[store.key] = envelope
    _evict_old_tabs(session, store, limits_for(view)[0], now)
    previous_basis = view.__dict__.get("_djust_auto_basis")
    previous_settled = settled
    view.__dict__["_djust_auto_basis"] = _Basis(
        now_digest,
        now,
        now_public,
        now_private,
        changed,
        deleted,
        _rollback(state.text),
        _present(view),
    )
    view.__dict__["_djust_auto_settled"] = None
    view.__dict__["_djust_auto_bytes"] = len(_encode(envelope))

    def undo() -> None:
        if previous is _MISSING:
            session.pop(store.key, None)
        else:
            session[store.key] = previous
        view.__dict__["_djust_auto_basis"] = previous_basis
        view.__dict__["_djust_auto_settled"] = previous_settled

    return undo


#: What every saved view state of one session may add up to: twice the default
#: per-view limit. Past it the oldest states go, whatever page they belong to.
SESSION_BUDGET_KB = 2 * DEFAULT_MAX_KB


def _evict_old_tabs(session: Any, store: _Store, maximum: int, now: int) -> None:
    """Bound what the session holds, at every write. The state just written
    is never removed.

    * Expired states, of any page, are dropped.
    * The tab states of this view and route: at most :data:`MAX_TABS`, and
      together at most this view's size limit (``automatic_state_max_kb``),
      so a page's reloads and tabs cannot multiply its limit. Oldest first.
    * All saved view states of the session together: at most
      :data:`SESSION_BUDGET_KB`. Oldest first, whatever page they belong to.
    """
    mine = session.get(store.key) or {}
    written = mine.get("bytes", 0) if type(mine) is dict else 0
    others = []
    for key in list(session.keys()):
        if type(key) is not str or not key.startswith(_KEY_PREFIX) or key == store.key:
            continue
        envelope = session.get(key)
        if type(envelope) is not dict:
            continue
        created, expires = envelope.get("created"), envelope.get("expires")
        if type(expires) is not int or expires <= now:
            del session[key]
            continue
        size = envelope.get("bytes")
        others.append(
            (
                created if type(created) is int else 0,
                key,
                size if type(size) is int and size >= 0 else 0,
                envelope.get("page") == store.page,
            )
        )
    others.sort(reverse=True)  # newest first: keep while the budgets allow
    page_count, page_bytes, total = 1, written, written
    for _created, key, size, same_page in others:
        over_page = same_page and (page_count + 1 > MAX_TABS or page_bytes + size > maximum)
        if over_page or total + size > SESSION_BUDGET_KB * 1024:
            del session[key]
            continue
        total += size
        if same_page:
            page_count += 1
            page_bytes += size


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


def clear_expired_saved_state(session: Any, now: Optional[int] = None) -> int:
    """Remove the saved view states of one session that have expired; the count removed.

    An entry whose ``expires`` is not a time in the future is expired (a state
    no reader would take back). Other keys are left alone (``djust clear``).
    """
    now = int(time.time()) if now is None else now
    expired = []
    for key in saved_state_keys(session):
        envelope = session[key]
        expires = envelope.get("expires") if type(envelope) is dict else None
        if type(expires) is not int or expires <= now:
            expired.append(key)
    for key in expired:
        del session[key]
    if expired:
        session.save()
    return len(expired)


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
