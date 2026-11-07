"""
Serialization utilities for djust template rendering.

Prepares Django/Python values for native rendering or JSON serialization.
"""

import logging
from collections import namedtuple
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from decimal import Decimal
from django.utils.datastructures import MultiValueDict
from django.utils.functional import Promise
from typing import Any, Dict, List, Union, cast
from uuid import UUID

from django.db.models import Model
from django.db.models.fields.files import FieldFile

from djust.serialization import django_json_datetime

logger = logging.getLogger(__name__)

# JSON-compatible value the Rust engine accepts.
JSONValue = Union[str, int, float, bool, None, List[Any], Dict[str, Any]]


@lru_cache(maxsize=4096)
def _namedtuple_shape_is_buildable_cached(name: str, fields: tuple) -> bool:
    try:
        namedtuple(name, fields)
    except (ValueError, SyntaxError, TypeError):
        # ``SyntaxError``: fields equal only after NFKC normalisation (``("\ufb01", "fi")``) fail
        # when the generated ``__new__`` is compiled. It used to escape ``serialize_value`` and fail
        # the render. ``TypeError``: no SHAPE raises it, but ``isinstance(x, str)`` admits a ``str``
        # SUBCLASS and ``namedtuple()`` calls ``str()`` on every name, so a class whose ``__name__``
        # or ``_fields`` is a ``str`` subclass with a raising ``__str__`` raises here. (Only the
        # Rust side cannot see that: it extracts real ``String``s without calling ``__str__``.)
        return False
    return True


def _namedtuple_shape_is_buildable(name: Any, fields: Any) -> bool:
    """Would ``collections.namedtuple(name, fields)`` accept this shape? (#3849)

    The engine holds a namedtuple as ``(name, fields, items)`` and rebuilds the Python class from
    the first two with ``collections.namedtuple`` whenever it hands the context to a native tag.
    That call REFUSES some shapes a real class can have: a duplicate field renamed by
    ``namedtuple(..., rename=True)`` is ``('a', '_1', 'b')`` and a leading underscore is refused;
    a class whose name is not an identifier; a Python keyword. A namedtuple like that must stay a
    plain tuple, so it never reaches the engine as a ``Value::NamedTuple``. The real constructor
    is the test, not a copy of its rules, and the answer is cached per shape (the call execs a
    class body).
    """
    if not isinstance(name, str) or not isinstance(fields, (tuple, list)):
        return False
    if not all(isinstance(field, str) for field in fields):
        return False
    return _namedtuple_shape_is_buildable_cached(name, tuple(fields))


def _spells_itself(value: Any) -> bool:
    """Is ``value`` a ``dict`` / ``list`` subclass that renders itself as markup? (#3755)

    The Python half of the engine's ``dict_subclass_spells_itself`` /
    ``list_subclass_spells_itself``, narrowed to the subclasses that define ``__html__``
    (Django's ``ErrorList`` / ``ErrorDict``, whose ``str()`` is the ``<ul class="errorlist">``
    markup). The engine's rule is wider — any override of ``__str__`` / ``__repr__``, so an
    ``OrderedDict`` nested in a value is already a carrier — and the wider rule is NOT applied
    here: rebuilding a top-level ``OrderedDict`` / ``defaultdict`` / user mapping as a plain
    dict is what this function has always done, and it also recurses into the values (a
    ``FieldFile`` becomes its URL), which a raw pass-through would stop doing. Compared by
    identity against the builtin's own slots, so a subclass that inherits both ``__str__`` and
    ``__repr__`` is a plain container whatever it defines.
    """
    for base in (dict, list):
        if isinstance(value, base):
            cls = type(value)
            return (
                cls is not base
                and hasattr(cls, "__html__")
                and (cls.__str__ is not base.__str__ or cls.__repr__ is not base.__repr__)
            )
    return False


def serialize_value(
    value: Any,
    *,
    for_render: bool = False,
) -> Any:
    """
    Prepare a value for JSON or, with ``for_render=True``, native rendering.

    Handles:
    - datetime/date/time -> ``DjangoJSONEncoder.default``'s spelling (#2462)
    - UUID -> string
    - Decimal -> float
    - FieldFile/ImageFieldFile -> URL string or None
    - dict -> recursively serialized dict
    - list/tuple -> recursively serialized list
    - Other types -> passed through (will fail at JSON encoding if not serializable)

    Args:
        value: Any Python value to serialize

    Returns:
        JSON-serializable value
    """
    # The native render API accepts Python objects directly. JSON conversion
    # would erase the types needed by filters, comparisons, and date formatting.
    # Keep the existing JSON-oriented helper contract for other callers.
    if for_render:
        if isinstance(value, Promise):
            return str(value)
        if isinstance(value, (datetime, date, time, timedelta, Decimal, UUID)):
            return value
        # A model crosses whole (#3902): the engine carries it as a floored live handle (#3888), so
        # it keeps its managers and its relations. It has to be decided HERE, before the duck-typed
        # file check below: that check asks ``hasattr(value, "url") and hasattr(value, "name")``,
        # which a ``Link(name, url)`` row satisfies (``{{ link }}`` rendered its URL instead of its
        # ``str()``) and which RUNS a ``url`` property — one that raises anything but
        # ``AttributeError`` failed the whole render. A model is never a file.
        if isinstance(value, Model):
            return value

    if value is None:
        return None

    # datetime / date / time -> the encoder's spelling, not ``isoformat()``
    # (#2462). The THIRD sink of the same conversion: found by grepping for
    # ``isoformat()`` rather than by listing the callers already known, which
    # is the rule that keeps this kind of fix from landing on two of three
    # paths (#1646).
    if isinstance(value, (datetime, date, time)):
        return django_json_datetime(value)

    # Handle UUID
    if isinstance(value, UUID):
        return str(value)

    # Handle Decimal
    if isinstance(value, Decimal):
        return float(value)

    # Handle Django FieldFile/ImageFieldFile
    # Use isinstance check first, then duck-typing for file-like objects with 'url'
    if isinstance(value, FieldFile):
        if value:
            try:
                return cast(str, value.url)
            except ValueError:
                return None
        return None

    # Duck-typing fallback for file-like objects (e.g., custom file fields, mocks)
    # Must have 'url' attribute and 'name' attribute (signature of file fields)
    # but not be a type (class) itself
    if hasattr(value, "url") and hasattr(value, "name") and not isinstance(value, type):
        # Check it's not a plain dict or list that happens to have these attrs
        if not isinstance(value, (dict, list, tuple, str)):
            if value:
                try:
                    return cast(str, value.url)
                except (ValueError, AttributeError):
                    return None
            return None

    # Django Form / BoundField.
    #
    # With ``for_render=True`` the object itself crosses to the engine. The
    # native render API takes Python objects (see the top of this function), and
    # a ``Form`` is one whose behaviour IS its type: iterating it yields
    # ``BoundField``s, ``form|length`` is 0 (no ``__len__``), ``{{ form }}`` is its
    # ``as_div`` HTML, and ``f.errors`` / ``f.field.required`` read live
    # attributes. ``render_form_value``'s ``{name: SafeString}`` snapshot keeps
    # only the widget HTML, so ``{% for f in form %}`` iterated the KEYS,
    # ``{{ f.errors }}`` and ``{% field_error field=f %}`` rendered empty (#3755).
    # The engine already knows how to carry an object that way (a handle-bearing
    # ``Value::Encoded``, ADR-027: the ``{% for %}`` / ``{% with %}`` binding takes
    # the live object with it).
    #
    # The JSON-state paths (``normalize_django_value``, the LiveView bridge) still
    # need a plain value and keep ``render_form_value``.
    if for_render:
        from django.forms import BaseForm
        from django.forms.boundfield import BoundField

        if isinstance(value, (BaseForm, BoundField)):
            return value

    # Django Form / BoundField — render to SafeString HTML so that
    # {{ form.field_name }} produces widget HTML. Must come before dict check.
    from djust.serialization import render_form_value

    form_result = render_form_value(value)
    if form_result is not None:
        return cast(str, form_result)

    # A `QueryDict` / `MultiValueDict` stays RAW here, and only here (#2556).
    # On this path the raw-Python sidecar is derived in Rust from this very
    # dict (`entry_sidecar` -> `build_render_sidecar`), so a rebuilt plain
    # dict would leave `{% querystring my_qd a=2 %}` with no `.urlencode()`
    # and no multi-values. The converters read the raw object the way Django
    # resolves it — LAST value per key (`djust_core::multi_value_dict_pairs`),
    # so `{{ qd.a }}` on `?a=1&a=2` is `2`, not the `['1', '2']` storage the
    # plain-dict arm used to see (PR #2596 Stage 11). The LiveView path
    # differs: `normalize_django_value` feeds JSON state, so it rebuilds the
    # dict and `rust_bridge` carries the raw object in its own sidecar.
    if isinstance(value, MultiValueDict):
        return cast(JSONValue, value)

    # A ``dict`` / ``list`` SUBCLASS that renders itself as markup (Django's ``ErrorDict`` /
    # ``ErrorList``: ``str(o)`` is the ``<ul class="errorlist">`` markup) crosses as itself
    # with ``for_render=True``: rebuilding it as a plain container loses the spelling Django
    # renders for ``{{ errors }}`` (#3755). See ``_spells_itself`` for why this is narrower
    # than the engine's own rule.
    if for_render and _spells_itself(value):
        return value

    # Handle dict - recursively serialize
    if isinstance(value, dict):
        return {k: serialize_value(v, for_render=for_render) for k, v in value.items()}

    # Handle list/tuple - recursively serialize
    if isinstance(value, (list, tuple)):
        items = [serialize_value(item, for_render=for_render) for item in value]
        if not (for_render and isinstance(value, tuple)):
            return items
        fields = getattr(value, "_fields", None)
        if fields is not None and _namedtuple_shape_is_buildable(type(value).__name__, fields):
            # A namedtuple keeps its type (#3849): the engine holds it as ``Value::NamedTuple``
            # (name + fields + items) and prints ``P(x=1, y=2)`` as Django does, where a plain
            # ``tuple(items)`` printed ``(1, 2)``. Rebuilt POSITIONALLY: ``type(value)(items)``
            # would wrap the list in the first field.
            #
            # This runs the class's own ``__new__`` at render time, on values that are already
            # serialized (a lazy string is a ``str`` by now), so ANY exception it raises means
            # "this value cannot be rebuilt here": it degrades to the plain tuple it was before
            # #3849, never to a failed render. (``_protect_sidecar_tree`` in
            # ``python/djust/serialization.py`` rebuilds the same way and catches only
            # ``TypeError``; not changed here.)
            try:
                return type(value)(*items)
            except Exception as exc:  # noqa: BLE001 - see above: the user's __new__ is arbitrary
                # Through ``log_failure`` (ADR-038): the user's ``__new__`` is arbitrary code, so
                # its exception text may carry undeclared state and is logged only where
                # diagnostics are allowed (``test_log_exposure_pin`` pins this). Imported here,
                # not at module level: the template backend must not load the exposure modules
                # at import time (``test_lazy_package_init_2559`` pins the allowlist).
                from djust._exposure_diagnostics import log_failure

                log_failure(
                    logger,
                    exc,
                    "namedtuple %s could not be rebuilt for rendering, using a plain tuple",
                    type(value).__name__,
                    level="debug",
                    traceback=True,
                )
        return tuple(items)

    # Pass through other types (str, int, float, bool, etc.)
    return cast(JSONValue, value)


def serialize_context(context: Dict[str, Any], *, for_render: bool = False) -> Dict[str, Any]:
    """
    Prepare context values for JSON or the native renderer.

    ``for_render=True`` retains Python scalar and tuple types and evaluates
    lazy translation strings. Form and file-field rendering applies in both modes.

    This function recursively processes the context dictionary, converting
    Django/Python types that are not natively JSON-serializable into their
    string or primitive representations.

    Supported type conversions:
    - datetime.datetime -> ISO format string (e.g., "2024-06-15T14:30:45")
    - datetime.date -> ISO format string (e.g., "2024-06-15")
    - datetime.time -> ISO format string (e.g., "14:30:45")
    - Decimal -> float
    - UUID -> string
    - FieldFile/ImageFieldFile -> URL string if file exists, else None
    - Nested dicts and lists are processed recursively

    Args:
        context: The template context dictionary

    Returns:
        A new dictionary with all values serialized to JSON-compatible types

    Example:
        >>> from datetime import datetime
        >>> from decimal import Decimal
        >>> context = {
        ...     'created_at': datetime(2024, 6, 15, 14, 30),
        ...     'price': Decimal('99.99'),
        ... }
        >>> serialized = serialize_context(context)
        >>> serialized['created_at']
        '2024-06-15T14:30:00'
        >>> serialized['price']
        99.99
    """
    return {key: serialize_value(value, for_render=for_render) for key, value in context.items()}
