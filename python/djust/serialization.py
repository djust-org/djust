"""
JSON serialization utilities for Django models and Python types.

Extracted from live_view.py for modularity.
"""

import importlib.util
import json
import logging
from datetime import datetime, date, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, FrozenSet, List, Optional, Union, cast
from uuid import UUID

from django.core.serializers.json import DjangoJSONEncoder as _DjangoJSONEncoder
from django.db import models
from django.utils.functional import Promise

logger = logging.getLogger(__name__)

# Try to use orjson for faster JSON operations (2-3x faster than stdlib)
HAS_ORJSON = importlib.util.find_spec("orjson") is not None

# Sensitive-field denylist (finding #19 / CWE-200 / CWE-359).
#
# When a Django Model instance is assigned to a *public* (non-``_``) LiveView
# attribute, every concrete field is auto-serialized and sent to the client.
# Without a denylist this leaks ``password`` hashes, privilege flags, and PII
# the moment a developer writes the very natural ``self.user = request.user``.
#
# ``_ALWAYS_EXCLUDED_FIELDS`` is the secure-by-default floor for the
# *auto-serialization* paths — the full model dump, the JIT empty-paths
# fallback, the state snapshot, and get_state — where a whole Model is
# serialized without the developer naming any field. On those paths these names
# are dropped regardless of settings AND regardless of a per-model
# ``djust_serializable_fields`` allowlist (#1868: the floor is UNCONDITIONAL —
# an allowlist may only NARROW the serialized set, never re-expose a floor
# field). The ONLY way to re-include a floor field is the deliberate, loudly
# named per-model ``djust_serialize_sensitive_fields`` opt-out — a developer
# must explicitly take ownership of shipping ``password``/privilege flags.
# It does NOT (and cannot) cover a template that *explicitly* references a
# field: ``{{ user.password }}`` flows through the compiled JIT serializer,
# which emits exactly the paths the template names — that is a
# developer-initiated disclosure which already renders into server-side HTML.
# The match is name-EXACT: it covers ``password`` but not a ``get_password()``
# accessor or a differently-named ``@property`` — use ``DJUST_SENSITIVE_FIELDS``
# / per-model ``djust_exclude_fields`` for those.
# ``DJUST_SENSITIVE_FIELDS`` (settings) is UNIONED with this floor for
# project-wide additions. Per-model ``djust_exclude_fields`` (denylist) and
# ``djust_serializable_fields`` (allowlist) give developers field-level control;
# a model-level ``to_dict()`` is the full opt-out (developer takes ownership of
# what ships to the client).
#
# The floor covers Django's auth model: the ``password`` hash plus the privilege
# flags (``is_superuser``/``is_staff``) — all explicitly called out in finding
# #19 as leaked to the browser via ``self.user = request.user``.
_ALWAYS_EXCLUDED_FIELDS = frozenset({"password", "is_superuser", "is_staff"})

# Sensitive / expensive model METHODS that eager serialization never emits
# (``_add_safe_model_methods``) — and that the template sidecar proxy
# (``_SidecarModelProxy``, #1986) must also refuse, so the serialization floor
# holds on the lazy getattr path too. ``get_session_auth_hash`` leaks a
# session-security value; the ``get_*_permissions`` family and the
# ``get_next_by_``/``get_previous_by_`` prefixes cause N+1 queries. Shared by
# both the eager and sidecar paths so the two can't drift (#1646).
_SENSITIVE_MODEL_METHODS = frozenset(
    {
        "get_all_permissions",
        "get_user_permissions",
        "get_group_permissions",
        "get_session_auth_hash",
        "get_deferred_fields",
    }
)
_SENSITIVE_MODEL_METHOD_PREFIXES = ("get_next_by_", "get_previous_by_")


def _sensitive_field_types() -> frozenset:
    """Configured field-CLASS names to exclude from serialization (#1987).

    Reads ``LIVEVIEW_CONFIG['sensitive_field_types']`` — a list of Django field
    class names (matched anywhere in a field's MRO). Empty by default.
    """
    try:
        from .config import get_config

        vals = get_config().get("sensitive_field_types", None)
    except Exception:  # pragma: no cover - config not loaded
        return frozenset()
    if not vals:
        return frozenset()
    try:
        return frozenset(vals)
    except TypeError:  # pragma: no cover - misconfigured
        return frozenset()


# Field classes the encrypted-NAME heuristic has already logged, so the DEBUG
# breadcrumb fires once per class rather than once per render (the eager loop
# re-checks every field on every re-render / WebSocket event).
_HEURISTIC_TYPE_DROP_WARNED: set = set()


def _warn_heuristic_type_drop(field_cls: type) -> None:
    """One-shot DEBUG breadcrumb when the #1987 encrypted-name HEURISTIC drops a
    field type (not the unconditional ``BinaryField`` rule, not explicit
    ``sensitive_field_types`` config). Makes a false-positive drop diagnosable
    instead of a silent vanish (#1987 review M1)."""
    if field_cls in _HEURISTIC_TYPE_DROP_WARNED:
        return
    _HEURISTIC_TYPE_DROP_WARNED.add(field_cls)
    logger.debug(
        "Field type %s dropped from client serialization by the #1987 "
        "encrypted-field name heuristic (its class name contains "
        "'encrypted'/'fernet'). If this field is NOT sensitive, add it to "
        "LIVEVIEW_CONFIG['sensitive_field_types'] intentionally, rename the "
        "class, or precompute a serializable value in get_context_data().",
        field_cls.__name__,
    )


# TYPE-floor verdict memo. The verdict is a pure function of the field's
# CLASS and the configured sensitive-type names — never of instance state —
# and the eager path calls the authority once per field per serialized model
# (a chat-sized render is thousands of calls per event, each walking the MRO
# with casefold scans). Keyed by (field class, configured frozenset) so a
# ``LIVEVIEW_CONFIG['sensitive_field_types']`` mutation lands on a NEW key and
# can never be served a stale verdict. Field classes are finite per process,
# so the unbounded dict is safe.
_FIELD_TYPE_EXCLUSION_MEMO: Dict[Any, bool] = {}


def _field_type_is_excluded(field: Any) -> bool:
    """TYPE-based serialization floor (#1987) — the single authority both the
    eager (:meth:`DjangoJSONEncoder._serialize_model_safely`) and sidecar
    (:class:`_SidecarModelProxy`) paths call, so they can't drift (#1646).

    A field is excluded when its TYPE should never reach the client regardless
    of the field's NAME:

    - ``BinaryField`` — raw bytes, never sensibly rendered to a template;
      always dropped.
    - Any class in ``LIVEVIEW_CONFIG['sensitive_field_types']`` (matched by
      class name anywhere in the field's MRO).
    - Best-effort encrypted-field detection: any MRO class whose name
      case-INsensitively contains ``encrypted`` or ``fernet`` (django-encrypted-fields
      / django-fernet-fields and similar) — no hard dependency; excluded
      fail-closed. Because this heuristic can drop a field a project did NOT
      intend as sensitive, a one-shot DEBUG breadcrumb is logged per field
      class the FIRST time the heuristic (not the unconditional ``BinaryField``
      rule, not explicit config) drops it, so a false-positive "field silently
      vanished from the client" is diagnosable (#1987 review M1).

    ``FileField``/``ImageField`` are explicitly NOT excluded — they serialize a
    URL, the intended client payload.
    """
    from django.db import models as _m

    configured = _sensitive_field_types()
    memo_key = (type(field), configured)
    hit = _FIELD_TYPE_EXCLUSION_MEMO.get(memo_key)
    if hit is not None:
        return hit

    # FileField (and its ImageField subclass) serialize a URL — never dropped.
    if isinstance(field, _m.FileField):
        result = False
    elif isinstance(field, _m.BinaryField):
        result = True
    else:
        mro_names = [k.__name__ for k in type(field).__mro__]
        if configured and any(n in configured for n in mro_names):
            result = True
        # Case-insensitive so a lowercased/oddly-cased variant can't slip the
        # floor (#1987 review M2). Configured names above stay case-EXACT (the
        # developer spells the class name they mean).
        elif any(("encrypted" in n.casefold() or "fernet" in n.casefold()) for n in mro_names):
            # The memo preserves the one-shot breadcrumb semantics: the warn
            # fires on the first (computed) drop per class and never again on
            # memo hits — same observable behavior as the un-memoized one-shot.
            _warn_heuristic_type_drop(type(field))
            result = True
        else:
            result = False

    _FIELD_TYPE_EXCLUSION_MEMO[memo_key] = result
    return result


def _field_type_excluded_for(model_class: Any, field_name: str) -> bool:
    """Sidecar-path helper for the #1987 TYPE floor: resolve *field_name* on
    *model_class* and apply :func:`_field_type_is_excluded`. Non-fields
    (properties, methods, reverse relations that raise) are not type-excluded
    here — the name/method floor governs those."""
    try:
        field = model_class._meta.get_field(field_name)
    except Exception:
        return False
    return _field_type_is_excluded(field)


# Identity keys that are ALWAYS allowed even under a per-model allowlist —
# the client relies on these for {% if %} comparisons and __str__ display.
_IDENTITY_KEYS = frozenset({"pk", "id", "__str__", "__model__"})


def model_identity(obj: Any) -> Dict[str, Any]:
    """The identity map standing in for a Django model — the ONLY producer.

    Six sites used to build this by hand, and they had drifted (#2322): only
    two stamped ``"__model__"``, and the two that did disagreed about key
    ORDER (``id, pk`` here, ``pk, id`` in ``jit.py``), which survives to the
    wire. Which producer fires depends on prefetch depth, on whether the
    template referenced a field, and on whether serialization raised — none of
    it visible from the consuming side, so a consumer keying on
    ``"__model__"`` was right in development and wrong in production the
    moment a relation crossed ``max_depth``.

    The cure is one producer rather than six correct copies (#1646): with a
    single place to answer "what does a model's identity map contain?", the
    marker is universal by construction and the next key added to
    :data:`_IDENTITY_KEYS` cannot land at five sites out of six.

    ``id`` and ``pk`` stay NATIVE types (``int``/``UUID``): the client compares
    them in ``{% if item.id == selected_id %}``, and stringifying either would
    break every such comparison silently.

    The key ORDER is part of the contract, not incidental — a dict's order
    survives ``json.dumps`` and msgpack, so two producers ordering differently
    is a wire-shape difference. It is the order the main path
    (``_serialize_model_safely``) has always emitted.
    """
    return {
        "id": obj.pk,
        "pk": obj.pk,
        "__str__": str(obj),
        "__model__": obj.__class__.__name__,
    }


def _resolve_sensitive_fields() -> FrozenSet[str]:
    """Return the set of field names to always drop during model serialization.

    Unions the built-in ``_ALWAYS_EXCLUDED_FIELDS`` floor with the optional
    ``settings.DJUST_SENSITIVE_FIELDS`` (any iterable of field names). Resolved
    defensively: a missing setting, an unconfigured Django, or a non-iterable
    value all degrade gracefully to just the built-in floor — serialization
    must never raise because of this lookup.
    """
    try:
        from django.conf import settings

        configured = getattr(settings, "DJUST_SENSITIVE_FIELDS", None)
    except Exception:
        configured = None

    if not configured:
        return _ALWAYS_EXCLUDED_FIELDS

    try:
        return _ALWAYS_EXCLUDED_FIELDS | frozenset(configured)
    except TypeError:
        logger.warning(
            "DJUST_SENSITIVE_FIELDS is not iterable (got %s); "
            "falling back to the built-in denylist only.",
            type(configured).__name__,
        )
        return _ALWAYS_EXCLUDED_FIELDS


def fast_json_loads(s: Union[str, bytes]) -> Any:
    """Parse JSON string using orjson if available, stdlib json otherwise."""
    if HAS_ORJSON:
        import orjson

        return orjson.loads(s)
    return json.loads(s)


#: Django's own encoder, instantiated once. ``default()`` is a pure function of
#: its argument, so one shared instance is safe.
_DJANGO_JSON_ENCODER = _DjangoJSONEncoder()


def django_json_datetime(value: Union[datetime, date, time, timedelta]) -> str:
    """The string Django's own ``DjangoJSONEncoder.default`` writes for *value*.

    **Called, not re-implemented** — the reason #2448's Rust side gives
    (``crates/djust_core/src/lib.rs::django_json_encoded`` calls the encoder
    too, and that is what kept its spelling exact). The rules are short enough
    to look transcribable and are not::

        datetime:  r = o.isoformat()
                   if o.microsecond:  r = r[:23] + r[26:]   # µs -> ms
                   if r.endswith("+00:00"):  r = r[:-6] + "Z"
        date:      o.isoformat()
        time:      raise if aware; r = o.isoformat()
                   if o.microsecond:  r = r[:12]            # a DIFFERENT slice
        timedelta: duration_iso_string(o)

    ``time``'s truncation is ``r[:12]``, not the ``datetime`` slice pair —
    #2462's own issue body quoted the datetime rule for both, which is the kind
    of transcription error calling the encoder cannot make.

    **The one refusal djust does not adopt.** ``default()`` raises
    ``ValueError: JSON can't represent timezone-aware times.`` for an aware
    ``datetime.time``. That is the more-permissive direction djust deliberately
    keeps for every unserialisable value (#2429, and the same choice
    ``django_json_encoded`` makes by failing closed), so an aware ``time`` takes
    its previous ``isoformat()`` spelling rather than 500-ing a render that
    used to work. It is the ONLY branch here that is not Django's answer, and it
    is a branch rather than a bare ``except`` so it cannot silently swallow a
    different failure.
    """
    if isinstance(value, time) and value.utcoffset() is not None:
        return value.isoformat()
    return cast(str, _DJANGO_JSON_ENCODER.default(value))


class DjangoJSONEncoder(json.JSONEncoder):
    """
    Custom JSON encoder that handles common Django and Python types.

    Automatically converts:
    - datetime/date/time → ISO format strings
    - UUID → string
    - Decimal → string (exact digits, matching Django's own encoder; #2239)
    - Component/LiveComponent → rendered HTML string
    - Django models → dict with id and __str__
    - QuerySets → list
    """

    # Class variable to track recursion depth
    _depth = 0

    # Cache @property names per model class to avoid repeated MRO walks
    _property_cache: Dict[type, List[str]] = {}

    @staticmethod
    def _get_max_depth() -> int:
        """Get max depth from config (lazy load to avoid circular import)"""
        from .config import config

        return int(config.get("serialization_max_depth", 3))

    def default(self, obj: Any) -> Any:
        # Track recursion depth to prevent infinite loops
        DjangoJSONEncoder._depth += 1
        try:
            return self._default_impl(obj)
        finally:
            DjangoJSONEncoder._depth -= 1

    def _default_impl(self, obj: Any) -> Any:
        # AsyncResult — emit dict so templates can read .loading/.ok/.failed/.result/.error.
        # Closes #1274. Must come before Component check (AsyncResult is a frozen
        # dataclass; doesn't subclass Component but Component check is duck-typed
        # against attrs that AsyncResult doesn't have, so order doesn't strictly
        # matter — kept early for clarity).
        from .async_result import AsyncResult

        if isinstance(obj, AsyncResult):
            return obj.to_dict()

        # Handle Component and LiveComponent instances (render to HTML)
        # Import from both old and new locations for compatibility
        from .components.base import Component, LiveComponent
        from .components.base import (
            Component as BaseComponent,
            LiveComponent as BaseLiveComponent,
        )

        if isinstance(obj, (Component, LiveComponent, BaseComponent, BaseLiveComponent)):
            return str(obj)  # Calls __str__() which calls render()

        # Handle set/frozenset → sorted list (#626)
        if isinstance(obj, (set, frozenset)):
            try:
                return sorted(obj)
            except TypeError:
                # Elements aren't comparable (mixed types) — return unsorted
                return list(obj)

        # datetime / date / time / timedelta -> Django's own spelling (#2462).
        #
        # This used to be a bare ``obj.isoformat()``, which is NOT what
        # ``DjangoJSONEncoder.default`` writes: it truncates microseconds to
        # milliseconds and rewrites a trailing ``+00:00`` to ``Z``. This encoder
        # feeds JSON that LEAVES the process -- the WebSocket frame
        # (``websocket.py``), the SSE stream (``sse.py``), the HTTP-API body
        # (``api/dispatch.py``) -- so the divergence was visible to every client
        # that parsed a timestamp out of a djust payload and compared it to one
        # Django wrote.
        #
        # ``timedelta`` joins the branch: Django's encoder has handled it since
        # forever (``duration_iso_string``) and this one raised ``TypeError``,
        # which is also why ``normalize_django_value`` documented it as an
        # "enhancement beyond DjangoJSONEncoder" -- a claim that was true of THIS
        # encoder and false of Django's.
        if isinstance(obj, (datetime, date, time, timedelta)):
            return django_json_datetime(obj)

        # Handle UUID
        if isinstance(obj, UUID):
            return str(obj)

        # Decimal -> str, exactly what Django's own ``DjangoJSONEncoder`` does
        # (#2239). This encoder feeds JSON that LEAVES the process — the
        # WebSocket frame (``websocket.py``), the SSE stream (``sse.py``), the
        # HTTP-API body (``api/dispatch.py``) — and a JSON *number* cannot
        # carry a Decimal's digits: ``Decimal('12345678901234567890.123456789')``
        # becomes ``1.2345678901234567e+19`` as a float. The Rust wire already
        # emits the digit string (``serialize_context``, #2214), so this is also
        # what makes the two wires agree.
        #
        # The one boundary that must NOT take the string is a round-trip whose
        # output is restored back ONTO the view — see
        # :class:`StateRoundtripJSONEncoder` and
        # :func:`decimal_for_state_roundtrip` below.
        if isinstance(obj, Decimal):
            return str(obj)

        # Handle Django FieldFile/ImageFieldFile (must check before Model)
        from django.db.models.fields.files import FieldFile

        if isinstance(obj, FieldFile):
            # Return URL if file exists, otherwise None
            if obj:
                try:
                    return obj.url
                except ValueError:
                    # No file associated with this field
                    return None
            return None

        # Handle Django model instances (must be before duck-typing check
        # since models with 'url' and 'name' properties would match file-like heuristic)
        if isinstance(obj, models.Model):
            return self._serialize_model_safely(obj)

        # Duck-typing fallback for file-like objects (e.g., custom file fields, mocks)
        # Must have 'url' and 'name' attributes (signature of file fields)
        if hasattr(obj, "url") and hasattr(obj, "name") and not isinstance(obj, type):
            # Exclude dicts, lists, and strings which might have these attrs
            if not isinstance(obj, (dict, list, tuple, str)):
                if obj:
                    try:
                        return obj.url
                    except (ValueError, AttributeError):
                        return None
                return None

        # Handle QuerySets
        if hasattr(obj, "model") and hasattr(obj, "__iter__"):
            # This is likely a QuerySet
            return list(obj)

        # Safety net: skip callable objects (e.g., dict.items method references
        # that leaked through JIT codegen). These should never be in serialized
        # context but can appear when template variable extraction picks up
        # dict method names like .items/.keys/.values.
        if callable(obj):
            logger.debug(
                "Skipping callable %s during JSON serialization",
                type(obj).__name__,
            )
            return None

        return super().default(obj)

    def _serialize_model_safely(self, obj: models.Model) -> Any:
        """Cache-aware model serialization that prevents N+1 queries.

        Only accesses related objects if they were prefetched via
        select_related() or prefetch_related(). Otherwise, only includes
        the FK ID without triggering a database query.

        Sensitive-field filtering (finding #19): fields named in the built-in
        denylist, ``settings.DJUST_SENSITIVE_FIELDS``, or a per-model
        ``djust_exclude_fields`` are dropped. A per-model
        ``djust_serializable_fields`` allowlist, when present, restricts output
        to exactly those fields (plus identity keys). A model-level
        ``to_dict()`` overrides everything (developer opt-out).
        """
        # Model-level to_dict() override — developer takes full ownership of the
        # client-bound payload (intentional opt-out from the denylist).
        to_dict = getattr(type(obj), "to_dict", None)
        if callable(to_dict):
            try:
                return obj.to_dict()
            except Exception:
                logger.debug(
                    "Model %s.to_dict() raised; falling back to safe serialization",
                    type(obj).__name__,
                )

        # The identity keys first, then the model's fields on top. One producer
        # for that map (#2322) — see `model_identity`.
        result = model_identity(obj)

        # Resolve the effective denylist / allowlist / sensitive-opt-out once.
        denied = self._get_denied_fields(obj)
        allowed = self._get_allowlist_fields(obj)
        optout = self._get_sensitive_optout_fields(obj)

        for field in obj._meta.get_fields():
            if not hasattr(field, "name"):
                continue

            field_name = field.name

            # Sensitive-field filter (finding #19 / #1868). Identity keys always
            # pass; the floor is unconditional unless deliberately opted out.
            if not self._field_is_serializable(field_name, denied, allowed, optout):
                continue

            # TYPE-based floor (#1987): drop BinaryField / configured / encrypted
            # field types regardless of name — the SAME authority the sidecar
            # proxy uses, so the two paths can't drift (#1646).
            if _field_type_is_excluded(field):
                continue

            # Skip all reverse relations (ManyToOneRel, OneToOneRel, ManyToManyRel)
            # and many-to-many fields (forward or backward)
            # concrete=False means it's a reverse relation, not a forward FK/O2O
            if field.is_relation:
                is_concrete = getattr(field, "concrete", True)
                is_m2m = getattr(field, "many_to_many", False)
                if not is_concrete or is_m2m:
                    continue

            # Handle ForeignKey/OneToOne (forward relations only now)
            if field.is_relation and hasattr(field, "related_model"):
                if self._is_relation_prefetched(obj, field_name):
                    # Relation is cached, safe to access without N+1
                    try:
                        related = getattr(obj, field_name, None)
                    except Exception:
                        logger.debug(
                            "Failed to access relation '%s' on %s", field_name, type(obj).__name__
                        )
                        related = None

                    if related and DjangoJSONEncoder._depth < self._get_max_depth():
                        result[field_name] = self._serialize_model_safely(related)
                    elif related:
                        # Past the depth limit: the identity map and no fields.
                        # It carries `__model__` like every other producer since
                        # #2322 — before that, a consumer keying on the marker
                        # saw this related model as "not a model" purely because
                        # of how deep the prefetch happened to be.
                        result[field_name] = model_identity(related)
                    else:
                        result[field_name] = None
                else:
                    # Include FK ID without fetching the related object (no N+1!)
                    fk_id = getattr(obj, f"{field_name}_id", None)
                    if fk_id is not None:
                        result[f"{field_name}_id"] = fk_id
            else:
                # Regular field - safe to access
                try:
                    result[field_name] = getattr(obj, field_name, None)
                except (AttributeError, ValueError):
                    logger.debug(
                        "Skipping inaccessible field '%s' on %s", field_name, type(obj).__name__
                    )

        # Only include explicitly defined get_* methods (skip auto-generated ones)
        self._add_safe_model_methods(obj, result)

        # Include @property values defined on user model classes
        self._add_property_values(obj, result)

        return result

    @staticmethod
    def _get_denied_fields(obj: models.Model) -> FrozenSet[str]:
        """Effective set of field names to drop for *obj* (finding #19).

        Union of the global denylist (built-in floor + DJUST_SENSITIVE_FIELDS)
        and the per-model ``djust_exclude_fields`` iterable, if defined.
        """
        denied = _resolve_sensitive_fields()
        per_model = getattr(type(obj), "djust_exclude_fields", None)
        if per_model:
            try:
                denied = denied | frozenset(per_model)
            except TypeError:
                logger.warning(
                    "%s.djust_exclude_fields is not iterable; ignoring it.",
                    type(obj).__name__,
                )
        return denied

    @staticmethod
    def _get_allowlist_fields(obj: models.Model) -> Optional[FrozenSet[str]]:
        """Per-model ``djust_serializable_fields`` allowlist, or None.

        When present, ONLY these field names (plus identity keys) are
        serialized. Returns ``None`` when no allowlist is defined (the common
        case — denylist semantics apply instead).
        """
        allowlist = getattr(type(obj), "djust_serializable_fields", None)
        if not allowlist:
            return None
        try:
            return frozenset(allowlist)
        except TypeError:
            logger.warning(
                "%s.djust_serializable_fields is not iterable; ignoring it.",
                type(obj).__name__,
            )
            return None

    @staticmethod
    def _get_sensitive_optout_fields(obj: models.Model) -> FrozenSet[str]:
        """Per-model ``djust_serialize_sensitive_fields`` opt-out set (#1868).

        The ONLY mechanism that re-enables a hardcore-floor field
        (``password``/``is_superuser``/``is_staff`` and any
        ``DJUST_SENSITIVE_FIELDS`` / ``djust_exclude_fields`` addition). It is a
        deliberate, loudly-named declaration the developer must opt into — the
        per-model ``djust_serializable_fields`` allowlist alone can NOT re-expose
        a floor field. Returns an empty ``frozenset`` when unset (default deny).
        """
        optout = getattr(type(obj), "djust_serialize_sensitive_fields", None)
        if not optout:
            return frozenset()
        try:
            return frozenset(optout)
        except TypeError:
            logger.warning(
                "%s.djust_serialize_sensitive_fields is not iterable; ignoring it.",
                type(obj).__name__,
            )
            return frozenset()

    @staticmethod
    def _field_is_serializable(
        field_name: str,
        denied: FrozenSet[str],
        allowed: Optional[FrozenSet[str]],
        optout: FrozenSet[str] = frozenset(),
    ) -> bool:
        """Return True if *field_name* may be serialized (finding #19 / #1868).

        Precedence (the denylist floor is UNCONDITIONAL — #1868):
        1. Identity keys (pk/id/__str__/__model__) always pass.
        2. If *field_name* is in *denied* (the ``_ALWAYS_EXCLUDED_FIELDS`` floor
           unioned with ``DJUST_SENSITIVE_FIELDS`` / ``djust_exclude_fields``),
           it is dropped REGARDLESS of any allowlist — UNLESS the developer
           deliberately re-includes it via ``djust_serialize_sensitive_fields``
           (*optout*). A ``djust_serializable_fields`` allowlist alone can NOT
           re-expose a denied field; it may only NARROW the non-denied set.
        3. If a per-model allowlist (*allowed*) is set, only those fields pass
           (for the remaining, non-denied fields).
        4. Otherwise, the field passes.
        """
        if field_name in _IDENTITY_KEYS:
            return True
        # The floor wins first: a denied field is dropped even when an allowlist
        # names it. Only the explicit, deliberate opt-out lifts the floor.
        if field_name in denied and field_name not in optout:
            return False
        if allowed is not None:
            # Allowlist narrows the (now floor-cleared) set. A field opted back
            # in via *optout* but absent from the allowlist still passes —
            # opting a sensitive field in is itself an explicit "ship this".
            return field_name in allowed or field_name in optout
        return True

    def _is_relation_prefetched(self, obj: models.Model, field_name: str) -> bool:
        """Check if a relation was loaded via select_related/prefetch_related.

        This prevents N+1 queries by only accessing relations that are
        already cached in memory.
        """
        # Check Django's fields_cache (populated by select_related)
        state = getattr(obj, "_state", None)
        if state:
            fields_cache = getattr(state, "fields_cache", {})
            if field_name in fields_cache:
                return True

        # Check prefetch cache (populated by prefetch_related)
        prefetch_cache = getattr(obj, "_prefetched_objects_cache", {})
        if field_name in prefetch_cache:
            return True

        return False

    def _add_safe_model_methods(self, obj: models.Model, result: Dict[str, Any]) -> None:
        """Add only explicitly defined model methods, skip auto-generated ones.

        Django auto-generates methods like get_next_by_created_at(),
        get_previous_by_updated_at() which execute expensive cursor queries.
        We only want explicitly defined methods like get_full_name().
        """
        # Skip Django's auto-generated N+1 methods + known-sensitive ones.
        # Shared with the template sidecar proxy so the two paths can't drift
        # (#1646 / #1986).
        SKIP_PREFIXES = _SENSITIVE_MODEL_METHOD_PREFIXES
        SKIP_METHODS = _SENSITIVE_MODEL_METHODS

        model_class = obj.__class__

        # Sensitive-field filter (finding #19 / #1868): a get_*/property added
        # below must also respect the unconditional floor + allowlist + opt-out
        # (e.g. a get_password() getter).
        denied = self._get_denied_fields(obj)
        allowed = self._get_allowlist_fields(obj)
        optout = self._get_sensitive_optout_fields(obj)

        for attr_name in dir(obj):
            if attr_name.startswith("_") or attr_name in result:
                continue
            if not attr_name.startswith("get_"):
                continue
            if any(attr_name.startswith(p) for p in SKIP_PREFIXES):
                continue
            if attr_name in SKIP_METHODS:
                continue
            if not self._field_is_serializable(attr_name, denied, allowed, optout):
                continue

            # Only include methods explicitly defined on the model class
            if not self._is_method_explicit(model_class, attr_name):
                continue

            try:
                attr = getattr(obj, attr_name)
                if callable(attr):
                    # ADR-024 Decision 2 (shared auto-call guard semantics):
                    # never call alters_data methods; leave
                    # do_not_call_in_templates callables un-called.
                    if getattr(attr, "alters_data", False) or getattr(
                        attr, "do_not_call_in_templates", False
                    ):
                        continue
                    value = attr()
                    if isinstance(value, (str, int, float, bool, type(None))):
                        result[attr_name] = value
            except Exception:
                # Skip methods that fail - they may require arguments,
                # access missing related objects, or have other runtime errors.
                logger.debug(
                    "Skipping method '%s' on %s during serialization", attr_name, type(obj).__name__
                )

    def _is_method_explicit(self, model_class: type, method_name: str) -> bool:
        """Check if method is explicitly defined, not auto-generated by Django.

        Auto-generated methods like get_next_by_* are not in the class __dict__
        of any user-defined model class, only in Django's base Model class.
        """
        for cls in model_class.__mro__:
            if cls is models.Model:
                break
            if method_name in cls.__dict__:
                return True
        return False

    def _add_property_values(self, obj: models.Model, result: Dict[str, Any]) -> None:
        """Add @property values defined on user model classes (not Django base)."""
        model_class = obj.__class__

        if model_class not in DjangoJSONEncoder._property_cache:
            prop_names = []
            for cls in model_class.__mro__:
                if cls is models.Model:
                    break
                for attr_name, attr_value in cls.__dict__.items():
                    if isinstance(attr_value, property):
                        prop_names.append(attr_name)
            DjangoJSONEncoder._property_cache[model_class] = prop_names

        cache = getattr(obj, "_djust_prop_cache", None)
        if cache is None:
            cache = {}
            obj._djust_prop_cache = cache

        # Sensitive-field filter (finding #19 / #1868): a @property named password
        # (or any floor/non-allowlisted name) must not be serialized — the floor
        # is unconditional unless deliberately opted out.
        denied = self._get_denied_fields(obj)
        allowed = self._get_allowlist_fields(obj)
        optout = self._get_sensitive_optout_fields(obj)

        for attr_name in DjangoJSONEncoder._property_cache[model_class]:
            if not self._field_is_serializable(attr_name, denied, allowed, optout):
                continue
            if attr_name not in result:
                if attr_name in cache:
                    result[attr_name] = cache[attr_name]
                    continue
                try:
                    val = getattr(obj, attr_name)
                    if isinstance(val, (str, int, float, bool, type(None))):
                        cache[attr_name] = val
                        result[attr_name] = val
                except Exception:
                    logger.debug(
                        "Skipping property '%s' on %s during serialization",
                        attr_name,
                        type(obj).__name__,
                    )


# ---------------------------------------------------------------------------
# The state-round-trip boundary (#2239; closed lossless in #2252)
#
# Three destinations take a serialized djust value, and they do NOT want the
# same representation of a ``Decimal``:
#
# 1. **The template context.** ``normalize_django_value``'s output is written
#    straight into it (``mixins/context.py``, ``mixins/jit.py``,
#    ``template/rendering.py``), so the Rust renderer sees it. A ``Decimal``
#    is carried there exactly, as ``Value::Decimal`` (#2214) — measured to
#    render identically to Django for every idiom in
#    ``python/tests/test_decimal_precision_2214.py``.
# 2. **The client wire.** ``DjangoJSONEncoder`` and the Rust
#    ``serialize_context`` both emit the exact digit string. A JSON number
#    cannot carry the digits, so ``str`` is the only lossless answer, and it
#    is what Django's own encoder returns.
# 3. **A round trip back ONTO the view** — the Django session
#    (``mixins/request.py``, ``runtime.py``, ``mixins/sticky.py``,
#    ``mixins/components.py``) and the snapshot captures in ``live_view.py``,
#    restored by ``runtime.py`` (the signed back-navigation snapshot) and
#    ``time_travel.py`` (the recorded event buffer). This one is neither of
#    the above: whatever is stored is ``safe_setattr``-ed back onto the view on
#    restore and lands in the template context on the very next render.
#
# Destination 3 can take NEITHER of the other two answers, which is what made
# it the residue of #2239:
#
# * **Not the raw ``Decimal``.** Django's session serializer is
#   ``json.dumps(obj, separators=(",", ":"))`` with NO encoder — see
#   ``django/core/signing.py``'s ``JSONSerializer`` — and the signed snapshot
#   is a bare ``json.dumps`` too (``runtime.py``'s ``state_snapshot_signed``
#   emit). Both raise ``TypeError``.
# * **Not the exact string.** A string restored into view state is a string in
#   the template, where ``{{ p|floatformat }}`` stops rounding and
#   ``{% if p > 10 %}`` compares lexically — the #2214 regression, one hop
#   later.
#
# #2239 therefore shipped ``float`` as "today's behaviour exactly, today's loss
# exactly". What #2252 changed is that the cost of that was measured, and it is
# NOT the "past ~15 significant digits" the issue framed it as. ``float`` is
# wrong for ordinary money too, in two ways needing no precision loss at all:
#
# * **The type changes.** ``Decimal('19.99')`` comes back a ``float``, so an
#   ordinary handler line — ``self.price + Decimal('1')`` — raises
#   ``TypeError: unsupported operand type(s)`` after a reconnect and not
#   before one.
# * **Trailing zeros are gone.** ``Decimal('19.90')`` comes back ``19.9`` and
#   renders ``19.9`` where Django renders ``19.90``. Measured across
#   {19.90, 0.00, 100.00, 2.50, 19.99} x {``{{ p }}``, ``|floatformat``,
#   ``|floatformat:2``, ``|stringformat:'s'``}: **10 of 20** cases disagree
#   with Django through the float round trip, against 2 of 20 through the
#   tagged one — and those 2 are the separate ``floatformat`` gap (#2253),
#   not this boundary.
#
# So destination 3 gets a TAGGED round trip: the shape
# ``encode_private_model_refs`` uses for models (#1994), under the same tag
# name the Rust binary encoding already uses for the same job (``DECIMAL_TAG``
# in ``crates/djust_core/src/lib.rs``, #2214).
#
# ``Decimal('19.99')`` is written as ``{"__djust_decimal__": "19.99"}``, which
# every JSON serializer on these paths accepts, and
# :func:`decode_state_roundtrip` turns back into a real ``Decimal`` at every
# restore site. Backward compatible in the useful direction: an untagged
# ``float`` from a session written by an older release passes straight through.
#
# COLLISION HAZARD, deliberately the same one the Rust side documents: a user
# dict that is exactly ``{"__djust_decimal__": <digit string>}`` and nothing
# else is misread as a ``Decimal`` on restore. The guard is the same three
# rules ``visit_map`` applies — exactly one key, that key, a ``str`` payload —
# plus a fourth the Rust side does not need, because Python's ``Decimal()``
# raises where Rust just stores the string: a payload ``Decimal`` refuses is
# left as a dict rather than raised on. The name is chosen to make the
# collision a thing you have to try to do.
#
# ONE function decides each direction, so the encoder adapter and the
# normalizer adapter can never drift apart (#1646), and every restore site
# decodes by the same rules.
# ---------------------------------------------------------------------------

#: The tag key. Must equal ``DECIMAL_TAG`` in ``crates/djust_core/src/lib.rs``
#: — the Rust ``visit_map`` decodes this exact shape, so the two halves of the
#: framework agree on what a tagged ``Decimal`` looks like. Pinned by
#: ``test_the_python_tag_is_the_rust_tag``.
STATE_DECIMAL_TAG = "__djust_decimal__"


def decimal_for_state_roundtrip(value: Decimal) -> Dict[str, str]:
    """Represent *value* for a boundary that restores its output onto the view.

    Returns the tagged form ``{"__djust_decimal__": "<exact digits>"}``, which
    every JSON serializer on those paths accepts and which
    :func:`decode_state_roundtrip` restores to a real ``Decimal`` — type and
    digits both. See the block comment above for why neither the raw
    ``Decimal`` (the serializers refuse it) nor the bare string (it stops being
    a number in the template) can be used here.

    Client-bound boundaries take the bare string instead
    (:class:`DjangoJSONEncoder`), and template-bound ones keep the ``Decimal``
    (:func:`normalize_django_value`).
    """
    return {STATE_DECIMAL_TAG: str(value)}


def decode_state_roundtrip(obj: Any) -> Any:
    """Inverse of :func:`decimal_for_state_roundtrip` (#2252).

    Recursively replaces every ``{"__djust_decimal__": "<digits>"}`` map with
    the ``Decimal`` it stands for. Call this on ANY state read back from the
    Django session or a recorded/signed snapshot BEFORE it is applied to a
    view: an undecoded tag is strictly *worse* than the ``float`` it replaced,
    because it reaches the template as a dict rather than as a wrong number.

    Untagged values pass through unchanged, which is what makes state written
    by an older release keep working.

    Fails soft on a payload ``Decimal`` refuses — ``{"__djust_decimal__": "hi"}``
    stays a dict — because a user dict that collides with the tag must not
    crash a reconnect.
    """
    if isinstance(obj, dict):
        # The same three rules the Rust ``visit_map`` applies (#2214): exactly
        # one key, that key, a string payload. Anything else is a real dict.
        if len(obj) == 1:
            payload = obj.get(STATE_DECIMAL_TAG)
            if isinstance(payload, str):
                try:
                    return Decimal(payload)
                except InvalidOperation:
                    # A colliding user dict whose payload is not a number.
                    # Leave it alone rather than raise — see the docstring.
                    return dict(obj)
        return {k: decode_state_roundtrip(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [decode_state_roundtrip(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(decode_state_roundtrip(v) for v in obj)
    return obj


def decimal_tags_to_strings(obj: Any) -> Any:
    """Render every tagged ``Decimal`` as its bare digit string, recursively.

    For a CLIENT-BOUND *view* of state that was captured for destination 3 —
    the time-travel debug panel, which DISPLAYS ``state_before`` /
    ``state_after`` rather than restoring from them. Destination 2's rule is
    the exact digit string, so this converts between the two instead of leaking
    a tag shape into a UI (``{__djust_decimal__: "19.99"}`` where ``19.99``
    belongs).

    Never call this on state that will be restored: the restore path needs
    :func:`decode_state_roundtrip`, and a bare string there is the #2214
    regression.
    """
    if isinstance(obj, dict):
        if len(obj) == 1:
            payload = obj.get(STATE_DECIMAL_TAG)
            if isinstance(payload, str):
                return payload
        return {k: decimal_tags_to_strings(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [decimal_tags_to_strings(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(decimal_tags_to_strings(v) for v in obj)
    return obj


class StateRoundtripJSONEncoder(DjangoJSONEncoder):
    """:class:`DjangoJSONEncoder` for a JSON round trip back onto the view.

    Identical in every respect except ``Decimal``, which
    :func:`decimal_for_state_roundtrip` writes in the tagged form so
    :func:`decode_state_roundtrip` can restore the real ``Decimal`` on the way
    back. Use this — never the plain encoder — when the ``json.dumps`` output
    is read back and assigned to view state (``_capture_snapshot_state``,
    ``_capture_components_snapshot``), and pair it with a decode at the restore
    site.
    """

    def _default_impl(self, obj: Any) -> Any:
        if isinstance(obj, Decimal):
            return decimal_for_state_roundtrip(obj)
        return super()._default_impl(obj)


# ---------------------------------------------------------------------------
# Direct Python-to-Python value normalizer (replaces json.loads(json.dumps()))
# ---------------------------------------------------------------------------

# Singleton encoder instance reused for model serialization (GIL-safe: only
# calls _serialize_model_safely which mutates _property_cache and
# obj._djust_prop_cache -- dict writes are atomic under CPython's GIL but
# this is not truly thread-safe under free-threaded builds).
_encoder = DjangoJSONEncoder()


def _protect_sidecar_value(value: Any) -> Any:
    """Wrap a value reached during the template getattr sidecar walk so the
    serialization floor keeps holding transitively (#1986 review).

    - A Django ``Model`` → ``_SidecarModelProxy`` (floor-enforcing getattr +
      a denylist-filtered ``__djust_serialize__`` for terminal conversion).
    - A ``Manager`` / ``QuerySet`` → ``_SidecarQuerySetProxy``, so the models
      it yields (``.first``/``.get``/``.all``, iteration) are themselves
      protected — otherwise ``{{ x.groups.first.user_set.first.password }}``
      or ``{% for u in qs %}{{ u.password }}{% endfor %}`` would read a raw,
      unwrapped model and leak the floor field.
    - Anything else → returned unchanged (no floor to enforce).
    """
    if isinstance(value, models.Model):
        return _SidecarModelProxy(value)
    from django.db.models import Manager, QuerySet

    if isinstance(value, (Manager, QuerySet)):
        return _SidecarQuerySetProxy(value)
    return value


class _SidecarModelProxy:
    """Denylist-consulting wrapper for a Django ``Model`` placed in the
    template getattr sidecar (ADR-024 / #1986 review).

    The Rust template engine's sidecar walk resolves ``{{ obj.attr }}`` by raw
    ``getattr`` on live model instances — the fallback that lets reverse
    relations, managers, and methods the eager dict can't hold still render.
    Without this wrapper that walk **bypasses the serialization floor**
    (``_ALWAYS_EXCLUDED_FIELDS`` / ``DJUST_SENSITIVE_FIELDS`` / per-model
    ``djust_exclude_fields``, SECURE_DEFAULTS Pattern 1), leaking
    ``password`` / ``is_superuser`` / ``get_session_auth_hash`` to the client
    (both for explicitly-assigned models and request-scoped ``user``).

    The proxy refuses exactly what the eager path (``DjangoJSONEncoder``)
    refuses — the same field floor/allowlist via ``_field_is_serializable``
    and the same sensitive-method set — so ONE authority governs both the
    eager and the sidecar channel (#1646). Refused names raise
    ``AttributeError``; the Rust walk turns that into an empty render
    (Django's ``string_if_invalid`` default).

    Three defenses, all needed (each closed a distinct #1986-review bypass):

    1. **Field/method floor** on ``__getattr__`` — direct
       ``{{ member.password }}`` / ``{{ member.get_session_auth_hash }}``.
    2. **Transitive protection** of every returned value via
       ``_protect_sidecar_value`` — a model or manager/queryset reached deeper
       in the walk (``{{ x.groups.first.user_set.first.password }}``,
       ``{% for u in qs %}``) is itself wrapped, so it can't leak.
    3. **``__djust_serialize__``** returns a denylist-filtered dict
       (``normalize_django_value``, the SAME serializer the eager path uses),
       which the Rust ``FromPyObject`` calls instead of bulk-dumping the raw
       model's ``__dict__`` (that dump — ``lib.rs`` — filtered only
       ``_``-prefixed keys, so it leaked ``password`` for any model converted
       to a value, e.g. queryset items in a ``{% for %}``).

    ``_``-prefixed names are refused outright (Django-parity: template
    resolution never touches them) — this also closes the ``{{ obj._meta }}``
    worker segfault + ``{{ obj._meta.db_table }}`` schema disclosure. Safe
    fields, ``get_full_name``, managers, and relations delegate transparently,
    so Django-parity auto-call still works.

    Type-based field exclusion (e.g. always-drop ``BinaryField``) is a
    separate follow-up (#1987) that hardens both serialization paths.
    """

    __slots__ = ("_obj", "_denied", "_allowed", "_optout")

    def __init__(self, obj: "models.Model") -> None:
        object.__setattr__(self, "_obj", obj)
        object.__setattr__(self, "_denied", DjangoJSONEncoder._get_denied_fields(obj))
        object.__setattr__(self, "_allowed", DjangoJSONEncoder._get_allowlist_fields(obj))
        object.__setattr__(self, "_optout", DjangoJSONEncoder._get_sensitive_optout_fields(obj))

    def __getattr__(self, name: str) -> Any:
        # ``__getattr__`` fires only for names not in ``__slots__`` — i.e.
        # every attribute the template can reference on the wrapped model.
        # Django-parity: refuse ``_``-prefixed names outright. Templates never
        # resolve them, and allowing them lets ``{{ obj._meta }}`` segfault the
        # worker (Options extraction) + ``{{ obj._meta.db_table }}`` disclose
        # the schema (#1986 review). Identity keys (pk/id) are not ``_``-names.
        if name.startswith("_"):
            raise AttributeError(name)
        # Sensitive / expensive methods the eager path never emits.
        if name in _SENSITIVE_MODEL_METHODS or name.startswith(_SENSITIVE_MODEL_METHOD_PREFIXES):
            raise AttributeError(name)
        # The serialization floor / allowlist — the SAME authority the eager
        # dict uses. A floor field (or, under a per-model allowlist, any
        # non-allowlisted name) is refused.
        denied = object.__getattribute__(self, "_denied")
        allowed = object.__getattribute__(self, "_allowed")
        optout = object.__getattribute__(self, "_optout")
        if not DjangoJSONEncoder._field_is_serializable(name, denied, allowed, optout):
            raise AttributeError(name)
        obj = object.__getattribute__(self, "_obj")
        # TYPE-based floor (#1987): refuse BinaryField / configured / encrypted
        # field types even when the NAME passes the denylist — same authority
        # the eager path calls.
        if _field_type_excluded_for(type(obj), name):
            raise AttributeError(name)
        return _protect_sidecar_value(getattr(obj, name))

    def __djust_serialize__(self) -> Any:
        """Denylist-filtered dict for terminal Value conversion (called by the
        Rust ``FromPyObject``). Routes through the eager serializer so a model
        that becomes a template value (queryset item, terminal model) is
        floor-filtered instead of ``__dict__``-dumped."""
        return normalize_django_value(object.__getattribute__(self, "_obj"))

    def __str__(self) -> str:
        # ``{{ member }}`` normally renders via the eager dict's ``__str__``
        # key; this covers the case where a proxied related model is the
        # terminal value of a walk.
        return str(object.__getattribute__(self, "_obj"))


class _SidecarQuerySetProxy:
    """Floor-preserving wrapper for a Django ``Manager`` / ``QuerySet`` reached
    in the template getattr sidecar walk (#1986 review).

    The models a manager/queryset produces — via an auto-called method
    (``.first``/``.get``/``.latest``…), via a chained queryset (``.all``/
    ``.filter``…), or via iteration in ``{% for %}`` — must be wrapped in
    ``_SidecarModelProxy`` too, or the next segment reads a raw model and
    leaks a floor field. Attribute access returns a protected attr (or a
    call-wrapping proxy whose *result* is protected); iteration yields
    protected items. Django's template-callable guards (``alters_data`` /
    ``do_not_call_in_templates``) and the ORM-warning ``__self__`` are copied
    onto the call wrapper so the Rust auto-call path treats it exactly like
    the underlying bound method.

    **``.values()`` / ``.values_list()`` projections are refused** (#1986
    re-review, vector 5). Those querysets yield raw ``dict`` / ``tuple`` ROWS
    with no model identity, so neither this proxy nor ``_SidecarModelProxy``
    can apply the per-field floor to them — and ``.first()`` / ``.get()`` /
    iteration / positional index would each hand back an *unfiltered* row
    (leaking e.g. ``password`` via ``{% for x in qs.values %}{{ x.password }}``
    or ``{{ qs.values.first.password }}``). Rather than floor-filter every row
    escape hatch, a projection is refused wholesale in the sidecar: iteration
    is empty, attribute access raises. This is fail-closed with **zero
    regression** — a ``.values`` projection never rendered in the sidecar
    auto-call walk before ADR-024 (auto-call is new), the un-called bound
    method just stringified. Precompute projected rows in
    ``get_context_data()``, where the eager serialization floor applies.
    """

    __slots__ = ("_qs", "_is_projection")

    def __init__(self, qs: Any) -> None:
        object.__setattr__(self, "_qs", qs)
        # A ``.values()`` / ``.values_list()`` queryset sets ``_fields`` (to a
        # possibly-empty tuple); a normal queryset / manager leaves it ``None``.
        # This is the ONLY queryset op that sets ``_fields``, so it cleanly
        # detects the projection class we must refuse.
        object.__setattr__(self, "_is_projection", getattr(qs, "_fields", None) is not None)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        # Refuse ALL access on a values/values_list projection — .first/.get/
        # .aggregate/etc. each return an unfiltered raw row/scalar (vector 5).
        if object.__getattribute__(self, "_is_projection"):
            raise AttributeError(name)
        qs = object.__getattribute__(self, "_qs")
        attr = getattr(qs, name)
        if callable(attr) and not isinstance(attr, type):

            def _wrapped(*args: Any, **kwargs: Any) -> Any:
                return _protect_sidecar_value(attr(*args, **kwargs))

            # Preserve the template-callable guards + ORM ``__self__`` so the
            # Rust walk auto-calls (or refuses) the wrapper exactly as it would
            # the bound method (e.g. ``qs.delete`` keeps ``alters_data=True``).
            for _marker in ("alters_data", "do_not_call_in_templates", "__self__"):
                if hasattr(attr, _marker):
                    try:
                        setattr(_wrapped, _marker, getattr(attr, _marker))
                    except (AttributeError, TypeError):  # pragma: no cover - defensive
                        pass
            return _wrapped
        return _protect_sidecar_value(attr)

    def __iter__(self) -> Any:
        if object.__getattribute__(self, "_is_projection"):
            return iter(())  # refuse: projection rows have no per-field floor
        return (_protect_sidecar_value(item) for item in object.__getattribute__(self, "_qs"))

    def __len__(self) -> int:
        if object.__getattribute__(self, "_is_projection"):
            return 0
        return len(object.__getattribute__(self, "_qs"))

    def __djust_serialize__(self) -> Any:
        """Denylist-filtered LIST for terminal Value conversion (called by the
        Rust ``FromPyObject``). A ``{% for x in member.groups.all %}`` loop
        resolves the queryset to this value; each item is floor-filtered
        (``{{ x.name }}`` works, ``{{ x.password }}`` is empty) instead of
        ``__dict__``-dumped. A ``.values()``/``.values_list()`` projection is
        refused (empty) — its rows carry no per-field floor (vector 5)."""
        if object.__getattribute__(self, "_is_projection"):
            return []
        return [normalize_django_value(item) for item in object.__getattribute__(self, "_qs")]

    def __str__(self) -> str:
        return str(object.__getattribute__(self, "_qs"))


def render_form_value(value: Any) -> Any:
    """Render a Django Form or BoundField to SafeString HTML.

    BoundField.__str__() delegates to as_widget() → widget.render(),
    which returns already-safe HTML.  BaseForm is converted to a dict
    of {field_name: SafeString} so templates can use dot notation
    (e.g. ``{{ form.first_name }}``).

    Returns the rendered value, or *None* if *value* is not a Form or
    BoundField (caller should continue with its own logic).
    """
    from django.forms import BaseForm
    from django.forms.boundfield import BoundField
    from django.utils.safestring import mark_safe

    if isinstance(value, BoundField):
        return mark_safe(str(value))

    if isinstance(value, BaseForm):
        return {name: mark_safe(str(value[name])) for name in value.fields}

    return None


def normalize_django_value(value: Any, _depth: int = 0, *, state_roundtrip: bool = False) -> Any:
    """Convert Django/Python types to values djust's serializers can carry.

    This is the **pre-pass** for :class:`DjangoJSONEncoder`, not a replacement
    for it: for every type both handle, encoding the output is identical to
    encoding the input —

    .. code-block:: python

        json.dumps(normalize_django_value(v), cls=DjangoJSONEncoder)
        == json.dumps(v, cls=DjangoJSONEncoder)

    — while avoiding the serialise-then-parse round trip through JSON text,
    which is a meaningful speedup in hot paths (context serialization, state
    sync). ``TestParityWithJSONRoundtrip`` pins exactly that composition.

    The output is NOT ``json.dumps``-able on its own, because ``Decimal`` and
    the ``datetime`` family are carried through unconverted (see those branches
    below, #2239 / #2467). Every caller either hands it to the Rust renderer / a
    djust encoder — both of which take all of them — or passes
    ``state_roundtrip=True``.

    **Enhancements beyond DjangoJSONEncoder**: the following type would raise
    ``TypeError`` under ``json.dumps(value, cls=DjangoJSONEncoder)`` but is
    handled here as a convenience:

    - Promise    -- str() (Django lazy translation strings)

    ``timedelta`` used to be listed here too. It was never an enhancement over
    *Django's* encoder, which has always spelled it with ``duration_iso_string``
    -- only over djust's own, which raised. #2462 gave djust's encoder the same
    branch, so the two agree and the identity above covers it.

    **Non-serializable values (issue #292)**: If a value cannot be serialized,
    this function logs a warning and falls back to str(value). Configure
    ``strict_serialization=True`` in LIVEVIEW_CONFIG to raise TypeError instead.
    Always emits warning logs before fallback, even when not in strict mode.

    Supported types:
    - None, bool, int, float, str  -- pass through
    - Decimal                      -- carried through EXACTLY (see the branch, #2239);
                                      float() under ``state_roundtrip=True``
    - UUID                         -- str()
    - datetime, date, time,
      timedelta                    -- carried through UNCONVERTED (#2467), so Rust
                                      builds ``Value::Encoded`` (#2448) and the
                                      LiveView path answers what the raw path
                                      answers. Under ``state_roundtrip=True`` it
                                      takes ``DjangoJSONEncoder.default``'s
                                      spelling, which is NOT ``.isoformat()``:
                                      microseconds truncate to milliseconds and a
                                      trailing ``+00:00`` becomes ``Z`` (#2462)
    - Promise (lazy strings)       -- str()
    - dict                         -- recurse values
    - list / tuple                 -- recurse elements (always returns list)
    - set / frozenset              -- carried through UNCONVERTED (#2477), so Rust
                                      builds ``Value::Encoded`` with the ITEMS
                                      enumerated and the LiveView path answers what
                                      the raw path answers. Sorted list under
                                      ``state_roundtrip=True``
    - Django Model                 -- serialized via DjangoJSONEncoder._serialize_model_safely, then recursed
    - QuerySet                     -- list of normalized models
    - FieldFile / file-like        -- .url or None
    - Component / LiveComponent    -- str() (renders HTML)
    - callable                     -- None (safety net, matches encoder)
    - anything that crosses as a
      ``Value::Encoded`` (a dict
      view, a ``complex``, a
      zero-``__len__`` or
      ``__bool__``-False class)     -- carried through UNCONVERTED (#2477/#2489),
                                      decided by ``_rust.crosses_as_encoded`` so the
                                      gate has one statement rather than two
    - anything else                -- str() fallback

    Args:
        value: The value to normalize.
        _depth: Internal recursion depth counter (do not set manually).
        state_roundtrip: Set by the callers whose output is written to the
            Django session or a signed snapshot and later restored back ONTO
            the view. Converts ``Decimal`` via
            :func:`decimal_for_state_roundtrip` so the result is
            ``json.dumps``-able by Django's encoder-less session serializer AND
            still behaves like a number once restored. See the block comment on
            :func:`decimal_for_state_roundtrip` for why those boundaries take
            neither the ``Decimal`` nor the exact string.
    """
    # Fast path: JSON-native primitives need no conversion
    if value is None or isinstance(value, (bool, int, float)):
        return value

    if isinstance(value, str):
        return value

    # Containers -- recurse
    if isinstance(value, dict):
        return {
            k: normalize_django_value(v, _depth, state_roundtrip=state_roundtrip)
            for k, v in value.items()
        }

    if isinstance(value, (list, tuple)):
        return [
            normalize_django_value(item, _depth, state_roundtrip=state_roundtrip) for item in value
        ]

    # set / frozenset -> the value ITSELF (#2477), except at the state-roundtrip
    # boundary, where it takes the sorted list #626 gave it.
    #
    # The ``Decimal`` and ``datetime`` branches below, verbatim, and for the
    # same reason. A sorted list is not a set to the renderer: a list is
    # SUBSCRIPTABLE, so ``{{ tags|first }}`` rendered an element where Django
    # raises ``TypeError: 'set' object is not subscriptable``, ``{{ tags }}``
    # rendered ``['a']`` where Django writes ``{'a'}``, and ``|pprint`` and
    # ``|slice`` followed it. Rust carries a set exactly, as a
    # ``Value::Encoded`` holding ``str(o)``, ``bool(o)``, ``len(o)``,
    # ``repr(o)`` and its ITEMS (#2466/#2477/#2489), so the LiveView path now
    # answers what the raw ``render_template`` path answers.
    #
    # The list was not merely a spelling: it also DECIDED the four cells above.
    # Carrying the object moves the decision to the one place that can make it
    # from the object itself.
    #
    # ``state_roundtrip=True`` is the one boundary that cannot take the live
    # object -- Django's session serializer passes no encoder and ``json.dumps``
    # refuses a set -- and every ``request.session[...]`` write already sets it.
    if isinstance(value, (set, frozenset)):
        if not state_roundtrip:
            return value
        try:
            items = sorted(value)
        except TypeError:
            # Elements aren't comparable (mixed types) — return unsorted
            items = list(value)
        return [
            normalize_django_value(item, _depth, state_roundtrip=state_roundtrip) for item in items
        ]

    # Django lazy translation strings (Promise) -- must be before str check
    # since Promise is not a str subclass
    if isinstance(value, Promise):
        return str(value)

    # Decimal -> the Decimal itself (#2239).
    #
    # This function's output goes into the TEMPLATE CONTEXT
    # (``mixins/context.py``, ``mixins/jit.py``, ``template/rendering.py``,
    # ``mixins/rust_bridge.py``), so the Rust renderer sees it. Rust carries a
    # ``Decimal`` exactly, as ``Value::Decimal`` (#2214) — through
    # ``update_state``, through the msgpack state-backend round trip, and out
    # to the wire as the exact digit string. Verified against real Django in
    # ``python/tests/test_decimal_precision_2214.py``.
    #
    # Neither of the two conversions works here, which is why the fix is this
    # branch and not a different one:
    #
    # - ``float`` (what this used to do) loses every digit past ~15 significant
    #   ones: ``Decimal('12345678901234567890.123456789')`` becomes
    #   ``1.2345678901234567e+19``. ``DecimalField`` is Django's money type.
    # - ``str`` — the right answer at the wire, and what ``DjangoJSONEncoder``
    #   above now returns — is wrong HERE, because a string is a string to the
    #   renderer: ``{{ p|floatformat }}`` stops rounding and ``{% if p > 10 %}``
    #   compares lexically. Both measured.
    #
    # The exposure this closes is not a rare fallback. ``mixins/jit.py`` calls
    # this function at seven sites (197, 202, 210, 293, 302, 307, 347) and its
    # fallbacks are ordinary: JIT unavailable, template-variable extraction
    # returning None, no paths extracted for the variable, or the Rust
    # serializer not capturing every expected path — it cannot read
    # ``@property`` attributes, so a model with a ``@property`` in the template
    # sends the WHOLE object down this path.
    #
    # ``state_roundtrip=True`` is the one boundary that cannot take it: see
    # ``decimal_for_state_roundtrip``.
    if isinstance(value, Decimal):
        return decimal_for_state_roundtrip(value) if state_roundtrip else value

    # UUID -> str
    if isinstance(value, UUID):
        return str(value)

    # datetime / date / time / timedelta -> the value ITSELF (#2467), except at
    # the state-roundtrip boundary, where it takes the encoder's own spelling
    # (#2462).
    #
    # The ``Decimal`` branch above, verbatim, and for the same reason. This
    # function's output goes into the TEMPLATE CONTEXT, so the Rust renderer
    # sees it -- and Rust carries the whole datetime family exactly, as
    # ``Value::Encoded`` (#2448), which holds ``str(o)``, Django's encoder
    # spelling, CPython's ``tp_name`` and (since #2458) ``bool(o)``. Flattening
    # to a string here meant that variant was never constructed on the LiveView
    # path, so every downstream decision was made on TEXT and djust's two paths
    # answered differently for the same value:
    #
    # - ``{% if p %}`` over ``timedelta(0)`` was ``T`` here and ``F`` on the raw
    #   path, because a non-empty string is truthy;
    # - ``{{ p }}`` rendered the ISO string rather than ``str(o)``;
    # - and the sharpest one, which is a PERMISSIVENESS gap rather than a
    #   spelling: the seven #2451 filters that refuse a non-iterable were handed
    #   the string ``"P0DT00H00M00S"`` and iterated its thirteen CHARACTERS,
    #   so ``{{ p|unordered_list }}`` emitted thirteen ``<li>``s where Django
    #   raises ``TypeError: 'datetime.timedelta' object is not iterable``.
    #
    # The documented identity above survives this, exactly as it survives for
    # ``Decimal``: ``json.dumps`` with either encoder handles a datetime, so
    # every wire consumer emits byte-identical bytes with and without the
    # pre-pass. ``state_roundtrip=True`` is the one boundary that cannot take
    # the live object -- Django's session serializer passes no encoder -- and
    # every ``request.session[...]`` write already sets it.
    #
    # ``django_json_datetime`` is still the converter there, so the ordering
    # subtlety (``datetime`` before ``date``, since ``datetime`` IS a ``date``)
    # stays inside ``DjangoJSONEncoder.default``, which is where Django keeps it.
    if isinstance(value, (datetime, date, time, timedelta)):
        return django_json_datetime(value) if state_roundtrip else value

    # Django Form / BoundField — must come before FieldFile check because
    # Form/BoundField objects don't have `.url` but duck-typing could match.
    form_result = render_form_value(value)
    if form_result is not None:
        return form_result

    # Django FieldFile / ImageFieldFile (must check before Model)
    from django.db.models.fields.files import FieldFile

    if isinstance(value, FieldFile):
        if value:
            try:
                return value.url
            except ValueError:
                return None
        return None

    # Django Model -> serialize via encoder, then normalize nested values
    if isinstance(value, models.Model):
        max_depth = DjangoJSONEncoder._get_max_depth()
        if _depth >= max_depth:
            # At max depth, the identity map and no fields (#2322).
            return model_identity(value)
        # Increment DjangoJSONEncoder._depth so _serialize_model_safely
        # respects the depth limit for prefetched relations.
        DjangoJSONEncoder._depth += 1
        try:
            model_dict = _encoder._serialize_model_safely(value)
        finally:
            DjangoJSONEncoder._depth -= 1
        return normalize_django_value(model_dict, _depth + 1, state_roundtrip=state_roundtrip)

    # Duck-typing fallback for file-like objects (must be after Model check)
    if hasattr(value, "url") and hasattr(value, "name") and not isinstance(value, type):
        if not isinstance(value, (dict, list, tuple, str)):
            if value:
                try:
                    return value.url
                except (ValueError, AttributeError):
                    return None
            return None

    # QuerySet -> list of normalized models
    if hasattr(value, "model") and hasattr(value, "__iter__"):
        return [
            normalize_django_value(item, _depth, state_roundtrip=state_roundtrip) for item in value
        ]

    # AsyncResult -> serializable dict (closes #1274). Must come before Component
    # check since AsyncResult is its own frozen dataclass. Recurse via
    # normalize_django_value so the inner ``result`` payload (which may be a
    # Django Model, dict, list, etc.) is normalized too.
    from .async_result import AsyncResult

    if isinstance(value, AsyncResult):
        return normalize_django_value(value.to_dict(), _depth + 1, state_roundtrip=state_roundtrip)

    # Components -> rendered HTML string
    try:
        from .components.base import Component, LiveComponent

        if isinstance(value, (Component, LiveComponent)):
            return str(value)
    except ImportError:
        pass  # components module is optional; skip check if not installed

    # Safety net: skip callables (matches encoder behavior)
    if callable(value):
        logger.debug(
            "Skipping callable %s during normalization",
            type(value).__name__,
        )
        return None

    # Final fallback - warn before str() conversion
    from .config import config

    value_type = type(value).__name__
    value_module = type(value).__module__
    msg = (
        f"LiveView state contains non-serializable value: {value_type} "
        f"(from {value_module}). This will be converted to a string, "
        f"which may cause AttributeError on deserialization. "
        f"Consider using self._<attr> for private state, or re-initialize "
        f"in mount()/event handlers. See: https://djust.org/docs/guides/services.md"
    )

    # Always warn, even if not in strict mode
    logger.warning(msg)

    # In strict mode, raise instead of falling back
    if config.get("strict_serialization", False):
        raise TypeError(msg)

    # An object the CONVERSION models -- carried through, not stringified
    # (#2477/#2489).
    #
    # Placed AFTER the warning and the strict-mode raise, deliberately, and the
    # ordering is the whole of the DX decision here. #292's warning is about
    # LiveView STATE, and its text stays true of the boundary it names: the
    # state paths pass `state_roundtrip=True`, which never reaches this line,
    # and a value that survives to the RENDERER as a `Value::Encoded` still
    # comes back off a msgpack round trip as its display string. So the signal
    # a project gets about putting a service object in public state is
    # unchanged, in volume and in wording, and only the value the RENDERER sees
    # moves.
    #
    # What moves: `impl FromPyObject for Value` carries a `dict_keys`, a
    # `complex`, a zero-`__len__` class and a falsy `__iter__` class EXACTLY,
    # as a `Value::Encoded` holding `str(o)`, `bool(o)`, `len(o)`, `repr(o)`,
    # its attributes and its items. Stringifying here meant the LiveView path
    # handed the renderer `str(o)` while `render_template` handed it the
    # object, so `{% if p %}` was `T` here and `F` there for an empty
    # `dict_keys`, and `{{ p|length }}` counted the thirteen characters of
    # `"dict_keys([])"`.
    #
    # `_rust.crosses_as_encoded` RUNS the conversion and asks what came out,
    # rather than re-stating its gate: a Python copy would be a second
    # statement of one question and would drift on the first widening (#1646).
    # It answers FALSE for the `__dict__` bulk-dump arm and for every EARLIER
    # arm — a `bytes` and a `deque` are claimed by PyO3's sequence extraction
    # and cross as a `Value::List`, so they keep the `str()` below and
    # `{{ p }}` still renders `b'ab'` rather than `[97, 98]`.
    #
    # `state_roundtrip=True` is the one boundary that cannot take the live
    # object, for the reason the `Decimal` / `datetime` / `set` branches above
    # record: its output is written to the Django session by an encoder-less
    # serializer.
    if not state_roundtrip:
        try:
            from . import _rust

            if _rust.crosses_as_encoded(value):
                return value
        except (ImportError, AttributeError):
            # No compiled extension (a pure-Python install, or a build that
            # predates the export): fall through to the historical `str()`.
            # Failing SOFT here matters because this is the fallback branch —
            # raising would turn "we could not serialize it" into a 500.
            pass

    return str(value)


# ---------------------------------------------------------------------------
# Private-state model-reference round-trip (#1994)
#
# A Django model cached on a PRIVATE (``_``-prefixed) LiveView attr is persisted
# to the session so it survives the HTTP-POST-fallback / reconnect restore path
# (which does NOT re-run ``mount()``). The session is JSON-serialized, so a live
# model can't be stored as-is; the old path ran ``normalize_django_value`` over
# private state, which turned the model into the LOSSY *client* dict
# (``{"pk", "__str__", <fields>}``) — so on restore ``self._workspace`` came back
# a plain ``dict`` and ``self._workspace.memberships`` raised ``AttributeError``.
#
# Instead, encode each model as a re-hydratable REFERENCE and re-fetch it from
# the DB on restore, so a private model attr round-trips AS A MODEL. Private
# state is server-side view cache, not client-bound payload — the serialization
# floor does not apply here (nothing is sent to the client).
# ---------------------------------------------------------------------------

_PRIVATE_MODEL_REF_KEY = "__djust_model_ref__"


def encode_private_model_refs(obj: Any) -> Any:
    """Recursively replace Django ``Model`` instances with a re-hydratable ref
    ``{"__djust_model_ref__": "<app_label>.<model_name>", "pk": <pk>}`` (#1994).

    Recurses into ``dict`` values and ``list``/``tuple`` items so a model nested
    inside a private container is encoded too. Non-model values pass through
    unchanged (they are handled by the existing ``normalize_django_value`` /
    JSON session serialization).
    """
    if isinstance(obj, models.Model):
        return {
            _PRIVATE_MODEL_REF_KEY: f"{obj._meta.app_label}.{obj._meta.model_name}",
            "pk": obj.pk,
        }
    if isinstance(obj, dict):
        return {k: encode_private_model_refs(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [encode_private_model_refs(v) for v in obj]
    return obj


def decode_private_model_refs(obj: Any) -> Any:
    """Inverse of :func:`encode_private_model_refs` — re-hydrate model-ref dicts
    back to model instances via a fresh DB fetch by pk (#1994).

    A ref whose row no longer exists (deleted between save and restore) or whose
    model is gone re-hydrates to ``None`` with a warning rather than raising —
    a stale cached model must not hard-crash a reconnect, and handler code that
    reads it typically already guards (``self._x.attr if self._x else ...``).
    """
    if isinstance(obj, dict):
        if _PRIVATE_MODEL_REF_KEY in obj and "pk" in obj:
            return _rehydrate_private_model_ref(obj[_PRIVATE_MODEL_REF_KEY], obj["pk"])
        return {k: decode_private_model_refs(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [decode_private_model_refs(v) for v in obj]
    return obj


def _rehydrate_private_model_ref(label: Any, pk: Any) -> Any:
    from django.apps import apps

    try:
        model_cls = apps.get_model(label)
        return model_cls.objects.get(pk=pk)
    except Exception:
        logger.warning(
            "djust: could not re-hydrate private model %s(pk=%r) on state restore; setting to None",
            label,
            pk,
        )
        return None
