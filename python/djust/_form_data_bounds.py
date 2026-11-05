"""How much a client may put into a form's submitted data over a WebSocket (#3834).

Django bounds an HTTP POST before a view sees it: ``DATA_UPLOAD_MAX_NUMBER_FIELDS`` (1000) fields and
``DATA_UPLOAD_MAX_MEMORY_SIZE`` (2.5 MB) of body. A WebSocket event is not a POST. Its parameters are a
JSON object the client wrote, and the bound a frame has is ``max_message_size`` (64 KB by default; ``0``
removes it; a project that sends large payloads raises it). That bounds ONE frame, and a form's data is
not one frame: ``FormMixin`` keeps what the client sent in ``form_data`` for the life of the view, adds
one client-named key per ``validate_field`` event and merges every key of a ``submit_form`` event, and
binds ALL of it to the form on every event. At the default rate limit (100 events per second) a client
reaches Django's 1000-field limit in ten seconds and a hundred thousand keys in about seventeen minutes,
and every event after that copies and filters all of it.

So the bound lives where the data is KEPT, not where a frame is read. Two settings, in
``LIVEVIEW_CONFIG``, defaulting to Django's own numbers:

* ``max_form_fields`` (1000): how many keys a form's data may hold;
* ``max_form_data_size`` (2,621,440): how many characters its keys and values may add up to.

``0`` removes a limit, as ``max_message_size = 0`` does. An event that would take the data past a limit
is refused WHOLE (nothing of it is stored: a half-merged submit is a form nobody posted) and the view
reports why in the form's errors; the data stays as it was, so the next event is judged on the same
footing.

Every path that lets a client name a key of a bound form's data asks :func:`refusal` first: ``FormMixin``
(``validate_field``, ``submit_form``), ``WizardMixin`` (``update_step_field``, ``validate_field``), the
admin form view, and a ``params_form`` bound to a ``url_change``'s parameters.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping, Optional, Tuple

from .config import DEFAULT_MAX_FORM_DATA_SIZE, DEFAULT_MAX_FORM_FIELDS, config

logger = logging.getLogger("djust.forms")

#: How deep :func:`_weigh` follows a nested value. A frame is JSON, so a value can be a list of lists;
#: past this a value is charged as if it were a string of that depth. The frame limit bounds the rest.
_MAX_WEIGH_DEPTH = 8


def limits() -> Tuple[int, int]:
    """``(max_form_fields, max_form_data_size)``, ``0`` meaning no limit.

    Read on every call so a ``LIVEVIEW_CONFIG`` change (a test, a reload) takes effect at once. The
    config validates both when it loads: a value that is not a non-negative integer is the default,
    never "unlimited", so a typo cannot remove a bound.
    """
    return (
        config.get("max_form_fields", DEFAULT_MAX_FORM_FIELDS),
        config.get("max_form_data_size", DEFAULT_MAX_FORM_DATA_SIZE),
    )


def _weigh(value: Any, budget: int, depth: int = 0) -> int:
    """The characters *value* adds up to, stopping once they exceed *budget* (the caller only needs to
    know whether they do). A string is its length; a number or ``None`` is the width of its text; a
    list or mapping is its members."""
    if isinstance(value, str):
        return len(value)
    if value is None or isinstance(value, (bool, int, float)):
        return 8
    if depth >= _MAX_WEIGH_DEPTH:
        return 8
    total = 0
    if isinstance(value, Mapping):
        for key, member in value.items():
            total += _weigh(key, budget - total, depth + 1) + _weigh(
                member, budget - total, depth + 1
            )
            if total > budget:
                return total
        return total
    if isinstance(value, (list, tuple, set, frozenset)):
        for member in value:
            total += _weigh(member, budget - total, depth + 1)
            if total > budget:
                return total
        return total
    return 8


def refusal(current: Optional[Mapping[str, Any]], incoming: Mapping[str, Any]) -> Optional[str]:
    """Why *incoming* may not be merged into *current*, or ``None`` when the result stays in bounds.

    *current* is the data the form already holds; *incoming* is what the client just sent. The result
    of ``{**current, **incoming}`` is what is judged (a key sent again replaces its value, it does not
    add a field), and *incoming* is looked at first, so a flood of keys is refused on its length
    without being copied. The message is for the person using the form and names the limit, never the
    keys.
    """
    max_fields, max_size = limits()
    if not max_fields and not max_size:
        return None
    current = current or {}
    if max_fields:
        if len(incoming) > max_fields:
            return _too_many(max_fields)
        # How many keys would be new: the merged count without building the merged mapping.
        added = sum(1 for key in incoming if key not in current)
        if len(current) + added > max_fields:
            return _too_many(max_fields)
    if max_size:
        size = _weigh(incoming, max_size)
        if size <= max_size:
            # What survives of *current*: everything *incoming* does not send again.
            kept = {key: value for key, value in current.items() if key not in incoming}
            size += _weigh(kept, max_size - size)
        if size > max_size:
            return _too_large(max_size)
    return None


def _too_many(limit: int) -> str:
    return (
        "This form received more fields than the server accepts (%d). It was not submitted." % limit
    )


def _too_large(limit: int) -> str:
    return (
        "This form's data is larger than the server accepts (%d characters). It was not submitted."
        % limit
    )


def warn(view: Any, message: str) -> None:
    """Say, in the server log, that *view* refused a form's data (the message carries no client data)."""
    logger.warning("%s refused form data: %s", type(view).__name__, message)
