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
  ``handle_params`` the initial values;
* either way ``view.params_errors`` holds the messages, for the template.

Every path that calls ``handle_params`` goes through :func:`run_handle_params`
(async: the socket and SSE mount, ``url_change``) or
:func:`run_handle_params_sync` (the HTTP GET and the HTTP fallback POST), so no
transport reads the URL differently from another (#1646). A view with no
``params_form`` gets ``handle_params(params, uri)`` with the original object,
exactly as before.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Dict, List, Optional, Tuple

from asgiref.sync import sync_to_async
from django import forms
from django.core.exceptions import ImproperlyConfigured
from django.http import QueryDict

from ._view_hooks import call_hook, call_hook_sync
from .sync_turn import guarded_step, run_guarded_step

#: The key Django uses for errors that belong to no field (``NON_FIELD_ERRORS``).
NON_FIELD_ERRORS_KEY = "__all__"

#: The bound form and its cleaned values; the values are ``None`` when the form is invalid.
BoundParams = Tuple[forms.BaseForm, Optional[Dict[str, Any]]]


def _declared_form_class(view: Any) -> Optional[type]:
    """The view's ``params_form``, read from its class; ``None`` when undeclared.

    Read from the class, not the instance, so a view attribute that happens to
    share the name cannot replace the declaration.
    """
    declared = getattr(type(view), "params_form", None)
    if declared is None:
        return None
    if not (isinstance(declared, type) and issubclass(declared, forms.BaseForm)):
        raise ImproperlyConfigured(
            "%s.params_form must be a Django form class, not %r."
            % (type(view).__name__, type(declared).__name__)
        )
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
        scalars = [
            item if isinstance(item, str) else str(item)
            for item in values
            if isinstance(item, (str, int, float))
        ]
        if scalars:
            data.setlist(key, scalars)
    return data


def initial_values(form: forms.BaseForm) -> Dict[str, Any]:
    """The initial value of every declared field, callables evaluated."""
    return {name: form.get_initial_for_field(field, name) for name, field in form.fields.items()}


def _cleaned_values(form: forms.BaseForm) -> Dict[str, Any]:
    values: Dict[str, Any] = dict(form.cleaned_data)
    for name, field in form.fields.items():
        if values.get(name) in field.empty_values:
            initial = form.get_initial_for_field(field, name)
            if initial is not None:
                values[name] = initial
    return values


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
    form = form_class(data=_bindable(params))
    if form.is_valid():
        view.params_errors = {}
        return form, _cleaned_values(form)
    view.params_errors = _error_messages(form)
    return form, None


async def run_handle_params(view: Any, params: Any, uri: str) -> None:
    """Call the view's params hook from async code (the socket, SSE, ``url_change``)."""
    form_class = _declared_form_class(view)
    if form_class is None:
        if hasattr(view, "handle_params"):
            await call_hook(view.handle_params, params, uri)
        return
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
    form_class = _declared_form_class(view)
    if form_class is None:
        if hasattr(view, "handle_params"):
            call_hook_sync(view.handle_params, params, uri)
        return
    form, values = run_guarded_step(view, "handle_params", bind_params, view, form_class, params)
    if values is None:
        call_hook_sync(view.handle_invalid_params, form, uri)
    else:
        call_hook_sync(view.handle_params, values, uri)
