"""
JSON serialization utilities for Django models and Python types.

Extracted from live_view.py for modularity.
"""

import importlib.util
import json
import logging
from datetime import datetime, date, time, timedelta
from decimal import Decimal
from typing import Any, Dict, FrozenSet, List, Optional, Union
from uuid import UUID

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

# Identity keys that are ALWAYS allowed even under a per-model allowlist —
# the client relies on these for {% if %} comparisons and __str__ display.
_IDENTITY_KEYS = frozenset({"pk", "id", "__str__", "__model__"})


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


class DjangoJSONEncoder(json.JSONEncoder):
    """
    Custom JSON encoder that handles common Django and Python types.

    Automatically converts:
    - datetime/date/time → ISO format strings
    - UUID → string
    - Decimal → float
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

        # Handle datetime types
        if isinstance(obj, (datetime, date, time)):
            return obj.isoformat()

        # Handle UUID
        if isinstance(obj, UUID):
            return str(obj)

        # Handle Decimal
        if isinstance(obj, Decimal):
            return float(obj)

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

        result = {
            "id": obj.pk,  # Native type (int/UUID) for {% if %} comparisons
            "pk": obj.pk,
            "__str__": str(obj),
            "__model__": obj.__class__.__name__,
        }

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
                        result[field_name] = {
                            "id": related.pk,
                            "pk": related.pk,
                            "__str__": str(related),
                        }
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


def normalize_django_value(value: Any, _depth: int = 0) -> Any:
    """Convert Django/Python types to JSON-safe Python primitives **directly**.

    For types supported by both, this produces output identical to
    ``json.loads(json.dumps(value, cls=DjangoJSONEncoder))`` but avoids the
    serialise-then-parse roundtrip through JSON text, giving a meaningful
    speedup when called in hot paths (context serialization, state sync).

    **Enhancements beyond DjangoJSONEncoder**: the following types would raise
    ``TypeError`` under ``json.dumps(value, cls=DjangoJSONEncoder)`` but are
    handled here as a convenience:

    - timedelta  -- ISO-8601 duration string (via ``django.utils.duration``)
    - Promise    -- str() (Django lazy translation strings)

    **Non-serializable values (issue #292)**: If a value cannot be serialized,
    this function logs a warning and falls back to str(value). Configure
    ``strict_serialization=True`` in LIVEVIEW_CONFIG to raise TypeError instead.
    Always emits warning logs before fallback, even when not in strict mode.

    Supported types:
    - None, bool, int, float, str  -- pass through
    - Decimal                      -- float()
    - UUID                         -- str()
    - datetime, date, time         -- .isoformat()
    - timedelta                    -- ISO-8601 duration string (via Django util)
    - Promise (lazy strings)       -- str()
    - dict                         -- recurse values
    - list / tuple                 -- recurse elements (always returns list)
    - Django Model                 -- serialized via DjangoJSONEncoder._serialize_model_safely, then recursed
    - QuerySet                     -- list of normalized models
    - FieldFile / file-like        -- .url or None
    - Component / LiveComponent    -- str() (renders HTML)
    - callable                     -- None (safety net, matches encoder)
    - anything else                -- str() fallback

    Args:
        value: The value to normalize.
        _depth: Internal recursion depth counter (do not set manually).
    """
    # Fast path: JSON-native primitives need no conversion
    if value is None or isinstance(value, (bool, int, float)):
        return value

    if isinstance(value, str):
        return value

    # Containers -- recurse
    if isinstance(value, dict):
        return {k: normalize_django_value(v, _depth) for k, v in value.items()}

    if isinstance(value, (list, tuple)):
        return [normalize_django_value(item, _depth) for item in value]

    # set/frozenset → sorted list (#626)
    if isinstance(value, (set, frozenset)):
        try:
            items = sorted(value)
        except TypeError:
            items = list(value)
        return [normalize_django_value(item, _depth) for item in items]

    # Django lazy translation strings (Promise) -- must be before str check
    # since Promise is not a str subclass
    if isinstance(value, Promise):
        return str(value)

    # Decimal -> float (matches DjangoJSONEncoder.default)
    if isinstance(value, Decimal):
        return float(value)

    # UUID -> str
    if isinstance(value, UUID):
        return str(value)

    # datetime/date/time -> isoformat
    # Note: check datetime before date because datetime is a subclass of date
    if isinstance(value, datetime):
        return value.isoformat()

    if isinstance(value, date):
        return value.isoformat()

    if isinstance(value, time):
        return value.isoformat()

    # timedelta -> ISO-8601 duration string (Django compat)
    if isinstance(value, timedelta):
        from django.utils.duration import duration_iso_string

        return duration_iso_string(value)

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
            # At max depth, return a minimal representation
            return {
                "id": value.pk,
                "pk": value.pk,
                "__str__": str(value),
            }
        # Increment DjangoJSONEncoder._depth so _serialize_model_safely
        # respects the depth limit for prefetched relations.
        DjangoJSONEncoder._depth += 1
        try:
            model_dict = _encoder._serialize_model_safely(value)
        finally:
            DjangoJSONEncoder._depth -= 1
        return normalize_django_value(model_dict, _depth + 1)

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
        return [normalize_django_value(item, _depth) for item in value]

    # AsyncResult -> serializable dict (closes #1274). Must come before Component
    # check since AsyncResult is its own frozen dataclass. Recurse via
    # normalize_django_value so the inner ``result`` payload (which may be a
    # Django Model, dict, list, etc.) is normalized too.
    from .async_result import AsyncResult

    if isinstance(value, AsyncResult):
        return normalize_django_value(value.to_dict(), _depth + 1)

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

    return str(value)
