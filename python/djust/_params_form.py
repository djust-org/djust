"""Declared URL parameters: a ``params_form`` bound before ``handle_params()`` (#3689).

``handle_params(params, uri)`` used to receive the query string as raw strings,
so every view that reads its URL wrote its own parsing, defaults and idea of what
an invalid value does (the example in its own docstring answered 500 on
``?page=abc``). A view that sets ``params_form`` to a Django form gets this
instead:

* the form is bound to the URL's parameters and cleaned;
* valid input calls ``handle_params(cleaned_data, uri)``, with the form's
  ``initial`` values filling the fields the URL left empty;
* invalid input calls ``handle_invalid_params(form, uri)``, whose default hands
  ``handle_params`` the initial values (``params_form_fallback = "fields"`` keeps
  the fields that validated and replaces only the ones that did not, #3874);
* either way ``view.params_errors`` holds the messages, for the template.

Every path that calls ``handle_params`` goes through :func:`run_handle_params`
(async: the socket and SSE mount, ``url_change``) or
:func:`run_handle_params_sync` (the HTTP GET and the HTTP fallback POST), so no
transport reads the URL differently from another (#1646). A view with no
``params_form`` gets ``handle_params(params, uri)`` with the original object,
exactly as before.
"""

from __future__ import annotations

import copy
import inspect
import logging
from collections.abc import Mapping
from typing import Any, Callable, Dict, List, Optional, Tuple

from asgiref.sync import sync_to_async
from django import forms
from django.core.exceptions import ImproperlyConfigured, SynchronousOnlyOperation, ValidationError
from django.http import QueryDict

from . import _form_data_bounds
from ._removed_settings import reject_removed_view_attributes
from ._view_hooks import call_hook, call_hook_sync
from .sync_turn import guarded_step, run_guarded_step

logger = logging.getLogger(__name__)

#: The key Django uses for errors that belong to no field (``NON_FIELD_ERRORS``).
NON_FIELD_ERRORS_KEY = "__all__"

#: The bound form and its cleaned values; the values are ``None`` when the form is invalid.
BoundParams = Tuple[forms.BaseForm, Optional[Dict[str, Any]]]


def form_class_problem(declared: Any) -> Optional[str]:
    """Why ``declared`` cannot be a ``params_form``, or ``None`` when it can.

    The one definition of "is a form class": the request path raises on it
    (:func:`_declared_form_class`) and the V023 system check reports it, so the
    two cannot disagree (#3706). ``None`` (undeclared) is not a problem.
    """
    if declared is None:
        return None
    if isinstance(declared, type) and issubclass(declared, forms.BaseForm):
        return None
    return "must be a Django form class, not %r" % type(declared).__name__


#: ``params_form_fallback``: what the default ``handle_invalid_params`` hands ``handle_params``.
#: ``"initial"`` (the default) is the initial value of every field; ``"fields"`` keeps the
#: fields that validated and uses the initial value only for the ones that did not (#3874).
FALLBACK_INITIAL = "initial"
FALLBACK_FIELDS = "fields"
FALLBACKS = (FALLBACK_INITIAL, FALLBACK_FIELDS)


def fallback_problem(declared: Any) -> Optional[str]:
    """Why ``declared`` cannot be a ``params_form_fallback``, or ``None`` when it can.

    The one definition, as :func:`form_class_problem` is for ``params_form``: the
    request path raises on it (:func:`declared_fallback`) and the V023 system
    check reports it, so the two cannot disagree.
    """
    if isinstance(declared, str) and declared in FALLBACKS:
        return None
    return "must be one of %s, not %r" % (", ".join(repr(name) for name in FALLBACKS), declared)


def declared_fallback(view: Any) -> str:
    """The view's ``params_form_fallback``, read from its class (like ``params_form``).

    Read from the class so an instance attribute that happens to share the name
    cannot replace the declaration. Anything but a known mode raises
    ``ImproperlyConfigured``: a typo (``"field"``) must not silently keep the
    conservative default the author meant to leave.
    """
    declared = getattr(type(view), "params_form_fallback", FALLBACK_INITIAL)
    problem = fallback_problem(declared)
    if problem is not None:
        raise ImproperlyConfigured("%s.params_form_fallback %s." % (type(view).__name__, problem))
    return declared


def construction_problem(form_class: type) -> Optional[str]:
    """Why the framework cannot build ``form_class(data=...)``, or ``None`` when it can.

    :func:`bind_params` calls ``form_class(data=...)`` and nothing else, so a form
    whose ``__init__`` needs another argument (a user, a queryset, a tenant)
    fails with ``TypeError`` on the first request. A signature that cannot be
    read is not reported: "could not tell" is not "broken".
    """
    try:
        signature = inspect.signature(form_class)
    except (TypeError, ValueError):
        return None
    required = [
        name
        for name, parameter in signature.parameters.items()
        if name != "data"
        and parameter.default is parameter.empty
        and parameter.kind
        in (
            parameter.POSITIONAL_ONLY,
            parameter.POSITIONAL_OR_KEYWORD,
            parameter.KEYWORD_ONLY,
        )
    ]
    if required:
        return "cannot be built as form_class(data=...): its __init__ requires %s" % (
            ", ".join(repr(name) for name in required)
        )
    try:
        signature.bind(data={})
    except TypeError:
        return "cannot be built as form_class(data=...): its __init__ does not accept data="
    return None


def _declared_form_class(view: Any) -> Optional[type]:
    """The view's ``params_form``, read from its class; ``None`` when undeclared.

    Read from the class, not the instance, so a view attribute that happens to
    share the name cannot replace the declaration.
    """
    declared = getattr(type(view), "params_form", None)
    problem = form_class_problem(declared)
    if problem is not None:
        raise ImproperlyConfigured("%s.params_form %s." % (type(view).__name__, problem))
    return declared


def _bindable(params: Any) -> QueryDict:
    """The URL's parameters as the ``QueryDict`` a form binds to.

    A scalar becomes one value and a list several, so a repeated key reaches a
    ``MultipleChoiceField``. A frame whose ``params`` is not a mapping, and any
    value that is not a scalar or a list of scalars (nested data a client could
    send), binds as absent: the form reports it as missing rather than the
    server failing on it.
    """
    data = QueryDict(mutable=True)
    if not isinstance(params, Mapping):
        return data
    for key, value in params.items():
        if not isinstance(key, str):
            continue
        values = value if isinstance(value, (list, tuple)) else [value]
        scalars = [text for text in map(_scalar_text, values) if text is not None]
        if scalars:
            data.setlist(key, scalars)
    return data


def _scalar_text(item: Any) -> Optional[str]:
    """``item`` as the string a query string would carry, or ``None`` if it is not a scalar."""
    if isinstance(item, str):
        return item
    if isinstance(item, (int, float)):
        try:
            return str(item)
        except ValueError:  # an int over Python's digit limit
            return None
    return None


def initial_values(form: forms.BaseForm) -> Dict[str, Any]:
    """The initial value of every declared field, callables evaluated, each a copy (#3926)."""
    return {
        name: _copied(form.get_initial_for_field(field, name))
        for name, field in form.fields.items()
    }


def _url_supplied(form: forms.BaseForm, name: str, field: forms.Field) -> bool:
    """Whether the URL carried a value for ``field`` (an empty string is no value).

    Django's own ``value_omitted_from_data`` knows which keys a widget reads, so a
    widget with several (``SplitDateTimeField``'s ``when_0`` / ``when_1``, a
    ``SelectDateWidget``'s year, month and day) is judged by all of them. A
    checkbox says it is never omitted, because an unchecked HTML checkbox sends
    nothing, and a multi-select widget (``SelectMultiple``,
    ``CheckboxSelectMultiple``) says the same because a selection of none sends
    nothing; a URL has no such convention, so for both the key itself decides
    (#3922). An empty list binds as absent (:func:`_bindable`), so a URL cannot
    ask for "none selected" over a non-empty ``initial``.
    """
    key = form.add_prefix(name)
    widget = field.widget
    if isinstance(widget, forms.CheckboxInput) or getattr(widget, "allow_multiple_selected", False):
        omitted = key not in form.data
    else:
        omitted = field.widget.value_omitted_from_data(form.data, form.files, key)
    if omitted:
        return False
    return key not in form.data or any(text != "" for text in form.data.getlist(key))


def _fill_unsupplied(
    form: forms.BaseForm,
    values: Dict[str, Any],
    names: List[str],
    initial_of: Callable[[str], Any],
) -> None:
    """Set each of ``names`` the URL left out or empty to its ``initial``, in ``values``.

    "Left out" is read from what the URL supplied, not from what the field cleaned
    to: a ``BooleanField`` cleans an absent key to ``False``, which Django does not
    count as empty, so judging by the cleaned value alone would leave
    ``initial=True`` unapplied exactly where a URL has no way to say "unchecked".
    ``initial_of(name)`` is asked only for a field that needs it, so a callable
    ``initial`` is not evaluated for a field the URL supplied.
    """
    for name in names:
        field = form.fields[name]
        if _url_supplied(form, name, field) and values.get(name) not in field.empty_values:
            continue
        initial = initial_of(name)
        if initial is not None:
            values[name] = initial


def _copied(value: Any) -> Any:
    """A deep copy of ``value``, or ``value`` itself when it cannot be copied.

    Every ``initial`` handed to ``handle_params()`` is a COPY (#3926): Django's
    ``Field.__deepcopy__`` does not copy ``initial``, so the object a bound form
    returns is the one stored on the form class, and a ``handle_params()`` that
    mutated a mutable one (a list, a dict) changed the default for every later
    request. An ``initial`` that cannot be deep-copied is handed over as it is.
    """
    try:
        return copy.deepcopy(value)
    except Exception:  # noqa: BLE001 — an arbitrary initial object; the check already copied it once
        return value


def _cleaned_values(form: forms.BaseForm) -> Dict[str, Any]:
    """The cleaned data, with each field the URL left out or empty set to its ``initial``."""
    values: Dict[str, Any] = dict(form.cleaned_data)
    _fill_unsupplied(
        form,
        values,
        list(form.fields),
        lambda name: _copied(form.get_initial_for_field(form.fields[name], name)),
    )
    return values


def _rechecked(view: Any, form: forms.BaseForm, values: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The declared fields ``form.clean()`` leaves of ``values``, or ``None`` when it does not accept them.

    Runs the form's own ``clean()`` on a copy whose ``cleaned_data`` is a deep copy
    of ``values`` and whose errors start empty, so the form the caller holds (its
    ``errors``, its ``cleaned_data``) is not touched. What it leaves is read the way
    ``BaseForm._clean_form`` reads it on the valid path (the returned dict, else
    ``self.cleaned_data``; an empty returned dict is what it leaves, so no field
    survives), so a ``clean()`` that repairs a value hands back the same repair
    here as there. The caller adopts that output for the fields it KEPT only. Accepted
    means ``clean()`` neither raised ``ValidationError`` nor reported an error
    with ``add_error()``; a plain ``ValidationError`` is an ordinary rejection and
    logs nothing.

    **``clean()`` must be idempotent.** It runs on values it already cleaned once
    (a kept value is its own first-pass output, a replaced field's initial value
    passes through it too), so a ``clean()`` that multiplies a price by 100 hands
    ``handle_params`` 50000 where the valid path hands 500.

    ``None`` (nothing is kept) also for anything else that goes wrong: a
    ``clean()`` that compares with the ``None`` an initial value can be, that uses
    the ORM on the event loop (an ``async def handle_invalid_params`` that calls
    ``super()``), that raises ``PermissionDenied`` or ``Http404`` only on this
    second call, or values that cannot be deep-copied. The answer is the plain
    initial values and a logged warning, not a 500. (A kept ``ModelChoiceField``
    value comes back as a deep copy: equal by primary key, not the same object.)
    """
    try:
        probe = copy.copy(form)
        probe.cleaned_data = copy.deepcopy(values)
        probe._errors = type(form.errors)()
        returned = probe.clean()
        if returned is not None:
            probe.cleaned_data = returned
        if probe._errors:
            return None
        return {
            name: probe.cleaned_data[name] for name in form.fields if name in probe.cleaned_data
        }
    except ValidationError:
        return None
    except Exception as exc:  # noqa: BLE001 — application code runs inside clean()
        from ._exposure_diagnostics import log_failure_for

        if isinstance(exc, SynchronousOnlyOperation):
            message = (
                "params_form clean() used the ORM on the event loop, so the fields it did not "
                "see are not kept: call super().handle_invalid_params() from a sync "
                "handle_invalid_params, or keep ORM work out of clean(); using the initial "
                "values: %s"
            )
        else:
            message = (
                "params_form clean() failed on the merged fields; using the initial values: %s"
            )
        log_failure_for(logger, (view,), exc, message, exc, level="warning")
        return None


def fallback_values(form: forms.BaseForm, mode: str, view: Any = None) -> Dict[str, Any]:
    """What the default ``handle_invalid_params`` hands ``handle_params`` for an invalid ``form``.

    ``"initial"``: the initial value of every declared field.

    ``"fields"`` (#3874): the declared fields only, each field that validated
    with the value the valid path would hand over and each field that did not
    with its ``initial``. A field is "valid" when it has no entry in
    ``form.errors`` *and* is in ``form.cleaned_data``: Django removes a field
    from ``cleaned_data`` when its own ``clean_<field>()`` fails, but a field
    that merely did not fail is not proof it was checked, so a field a ``clean()``
    deleted counts as not valid. A failed field's own entry is always its initial
    value; ``"initial"`` is the mode that never hands over a raw request value.

    **The order is the valid path's** (#3891): the ``initial`` fill comes AFTER
    ``clean()`` (:func:`_cleaned_values`), so a kept field the URL left out or
    empty takes its ``initial`` over whatever ``clean()`` wrote into it, and both
    paths hand ``handle_params`` the same value for it. The check below still
    runs on the filled values, so what is handed over is a combination ``clean()``
    accepts; the fill is then applied again to ``clean()``'s output. A ``clean()``
    that copies raw request data into a KEPT field the URL SUPPLIED hands it over
    through that field, on both paths (``clean()``'s own trust boundary). One that
    copies it into a kept field the URL left out does not, if that field has an
    ``initial``: a field with no ``initial`` (``None``) is not filled, on either
    path; a ``clean()`` that also writes the field into ``self.data`` makes it
    count as supplied. (A multi-select widget the URL left out is not
    supplied, as a checkbox is not: #3922.)

    The result is always a combination the form's ``clean()`` accepts, or the
    plain initial values. A kept field is now paired with the initial value of a
    replaced one, a pair ``clean()`` never saw (``lo=9`` kept, ``hi=2`` replaced
    by ``3``), and a kept field the URL left out is paired with its own
    ``initial`` (``end=2019-01-01`` next to the default ``start=2020-01-01``),
    so the form's ``clean()`` runs once more on exactly the combination handed
    over (:func:`_rechecked`); if it objects, nothing is kept, and what it
    repairs it repairs here as on the valid path. The values it is given are the
    first pass's cleaned values of the kept fields (a ``clean()`` that wrote one
    on the first pass is re-run on its output) with the ``initial`` fill applied
    to the unsupplied ones, and the replaced fields' initial values. The check
    runs on a deep copy, and every ``initial`` handed over is a copy, so a
    ``handle_params()`` that mutates a mutable one does not change the form
    class's default (#3926, as on the valid path and in ``"initial"`` mode).
    **Only ``clean()`` runs
    again, not the fields' own ``clean_<field>()``:** a ``clean_end()`` that
    checks ``end >= start`` was skipped when ``start`` failed and is not run on
    the merged values, so ``?start=zz&end=1`` hands over ``start`` at its initial
    value 10 next to ``end=1``. A replaced field's own validity is not asked
    either (a required field with no initial value is replaced by ``None``).

    Three cases keep nothing. A form-level error (``NON_FIELD_ERRORS``) says the
    *combination* is wrong, so the siblings that validated one by one would hand
    ``handle_params`` the very combination the form refused; a form that was
    never validated (the data exceeded its bounds, :func:`bind_params`) has no
    ``cleaned_data`` at all; and a combination ``clean()`` rejects (above). All
    fall back to the initial values.

    A callable ``initial`` is evaluated at most once, and only for the fields
    that need it: never for a field the URL supplied and that was kept.
    """
    if mode != FALLBACK_FIELDS:
        return initial_values(form)
    evaluated: Dict[str, Any] = {}

    def initial_of(name: str) -> Any:
        if name not in evaluated:
            evaluated[name] = form.get_initial_for_field(form.fields[name], name)
        return evaluated[name]

    def all_initial() -> Dict[str, Any]:
        return {name: _copied(initial_of(name)) for name in form.fields}

    cleaned = getattr(form, "cleaned_data", None)
    if cleaned is None or form.non_field_errors():
        return all_initial()
    kept = [name for name in form.fields if name not in form.errors and name in cleaned]
    if not kept:
        return all_initial()
    # The re-check sees the combination that would be handed over: the kept fields' cleaned values
    # with each field the URL left out or empty filled with its initial value, next to the initial
    # value of every replaced field. That keeps "the result is a combination clean() accepts, or the
    # plain initials" true for a default start next to a supplied end (#3891 kept the guarantee).
    values: Dict[str, Any] = {name: cleaned[name] for name in kept}
    _fill_unsupplied(form, values, kept, initial_of)
    merged = {name: values[name] if name in values else initial_of(name) for name in form.fields}
    repaired = _rechecked(view, form, merged)
    if repaired is None:
        return all_initial()
    # clean()'s output is adopted for the fields the fallback KEPT only (a kept field the URL left
    # out counts: it is in `kept`). A replaced field is its initial value whatever clean() wrote:
    # a clean() that re-reads self.data would otherwise write the failed raw value into its own entry.
    # A kept field clean() removed is not valid, as one it was never given: its initial value.
    adopted: Dict[str, Any] = {name: repaired[name] for name in kept if name in repaired}
    # Then the initial fill AGAIN, on that OUTPUT, as ``_cleaned_values`` does after clean() on the
    # valid path: a field the URL left out or empty takes its initial value over whatever clean()
    # wrote into it, so a clean() that copies a failed field's raw value into an unsupplied field
    # that has an initial cannot hand it over through that field (#3891).
    _fill_unsupplied(form, adopted, list(adopted), lambda name: _copied(initial_of(name)))
    return {
        name: adopted[name] if name in adopted else _copied(initial_of(name))
        for name in form.fields
    }


def _error_messages(form: forms.BaseForm) -> Dict[str, List[str]]:
    return {name: [str(message) for message in errors] for name, errors in form.errors.items()}


def bind_params(view: Any, form_class: type, params: Any) -> BoundParams:
    """Bind and clean ``params`` against ``form_class``, the view's ``params_form``.

    Returns ``(form, values)``: the cleaned data, or ``None`` when the form is
    invalid. The messages are published as ``view.params_errors`` (``{}`` when
    valid, so an error from an earlier URL never outlives it).

    Validation runs application code (a validator, a ``ModelChoiceField``'s
    query), so the async runner calls this on a worker thread, inside the
    ``sync_turn_wrapper`` step of ``handle_params``.
    """
    # The parameters are a JSON object the client wrote (a ``url_change`` frame), so they are held to
    # the same field / size limits as any other data a client binds to a form (#3834): past them
    # the form is bound to nothing and the URL is invalid, with the reason in ``params_errors``.
    refused = (
        _form_data_bounds.refusal(None, params, getattr(form_class, "base_fields", None) or ())
        if isinstance(params, Mapping)
        else None
    )
    if refused is not None:
        _form_data_bounds.warn(view, refused)
        form = form_class(data=QueryDict(mutable=True))
        view.params_errors = {NON_FIELD_ERRORS_KEY: [refused]}
        return form, None
    form = form_class(data=_bindable(params))
    if form.is_valid():
        view.params_errors = {}
        return form, _cleaned_values(form)
    view.params_errors = _error_messages(form)
    return form, None


def _refuse_removed_configuration(view: Any) -> None:
    """Refuse a view that still turns on an attribute 2.0 removed (``use_actors``),
    or an explicit view with a plain component in its class body (#4045).

    The class-creation check cannot see a value ``__init__`` or ``mount()`` set
    on the instance, one assigned to the class afterwards, or a class whose
    mixin's ``__init_subclass__`` skipped ``super()``. Every transport calls the
    params hook right after ``mount()``, so this is where they are refused.
    """
    from ._exposure_providers import class_level_plain_component_error
    from .live_view import LiveView

    if isinstance(view, LiveView):
        reject_removed_view_attributes(view)
        # #4045: the same holds for a plain component in an explicit view's
        # class body (shared by every session), refused at class creation too.
        shared = class_level_plain_component_error(view)
        if shared is not None:
            raise shared


async def run_handle_params(view: Any, params: Any, uri: str) -> None:
    """Call the view's params hook from async code (the socket, SSE, ``url_change``)."""
    _refuse_removed_configuration(view)
    form_class = _declared_form_class(view)
    if form_class is None:
        if hasattr(view, "handle_params"):
            await call_hook(view.handle_params, params, uri)
        return
    declared_fallback(view)  # a mode nothing implements fails the request, not only an invalid one
    # A guarded step: a wrapper that swallowed a failure here would otherwise
    # leave nothing bound, and the view would act on a URL no form accepted.
    stepped = guarded_step(view, "handle_params", bind_params)
    form, values = await sync_to_async(stepped)(view, form_class, params)
    if values is None:
        await call_hook(view.handle_invalid_params, form, uri)
    else:
        await call_hook(view.handle_params, values, uri)


def run_handle_params_sync(view: Any, params: Any, uri: str) -> None:
    """Call the view's params hook from sync code (the HTTP GET and fallback POST)."""
    _refuse_removed_configuration(view)
    form_class = _declared_form_class(view)
    if form_class is None:
        if hasattr(view, "handle_params"):
            call_hook_sync(view.handle_params, params, uri)
        return
    declared_fallback(view)  # a mode nothing implements fails the request, not only an invalid one
    form, values = run_guarded_step(view, "handle_params", bind_params, view, form_class, params)
    if values is None:
        call_hook_sync(view.handle_invalid_params, form, uri)
    else:
        call_hook_sync(view.handle_params, values, uri)
