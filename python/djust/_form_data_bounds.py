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

* ``max_form_fields`` (1000): how many keys a form's data may hold beyond the fields it declares;
* ``max_form_data_size`` (2,621,440): how many characters its keys and values may add up to.

``0`` removes a limit, as ``max_message_size = 0`` does. An event that would take the data past a limit
is refused WHOLE (nothing of it is stored: a half-merged submit is a form nobody posted) and the view
reports why in the form's errors; the data stays as it was, so the next event is judged on the same
footing, and the message is cleared by the next event that is accepted.

Every path that lets a client name a key of a bound form's data asks :func:`refusal` first: ``FormMixin``
(``validate_field``, ``submit_form``), ``WizardMixin`` (``update_step_field``, ``validate_field``), the
admin form view, and a ``params_form`` bound to a ``url_change``'s parameters.
"""

from __future__ import annotations

import itertools
import logging
from typing import Any, Collection, Mapping, Optional, Tuple

from .config import DEFAULT_MAX_FORM_DATA_SIZE, DEFAULT_MAX_FORM_FIELDS, config

logger = logging.getLogger("djust.forms")

#: How many containers deep :func:`_weigh` follows a value before it refuses it. Each level costs the
#: budget at least one character, so this only bounds the weigher's own stack, not what a client may send.
_MAX_WEIGH_NESTING = 1000


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


_END = object()


def _weigh(value: Any, budget: int) -> int:
    """The characters *value* adds up to, stopping as soon as they exceed *budget* (the caller only
    needs to know whether they do), so the work is O(budget) however the value is shaped.

    A string, ``bytes`` or ``bytearray`` is its length; a list, tuple, set or mapping is one character
    plus its members (and a mapping's keys); anything else is a small constant. There is NO depth at
    which a container stops being looked into: a flat charge at some depth was a hole a client walks
    through by wrapping its payload in that many lists. The walk is iterative (a stack of iterators,
    one member pulled at a time), every node costs at least one character, so a cycle or a very deep
    value runs out of budget instead of recursion; past ``_MAX_WEIGH_NESTING`` levels it is refused.
    """
    total = 0
    stack = [iter((value,))]
    while stack:
        item = next(stack[-1], _END)
        if item is _END:
            stack.pop()
            continue
        if isinstance(item, (str, bytes, bytearray)):
            total += len(item)
        elif isinstance(item, Mapping):
            total += 1
            stack.append(itertools.chain.from_iterable(item.items()))
        elif isinstance(item, (list, tuple, set, frozenset)):
            total += 1
            stack.append(iter(item))
        else:
            total += 8
        if total > budget or len(stack) > _MAX_WEIGH_NESTING:
            return max(total, budget + 1)
    return total


def refusal(
    current: Optional[Mapping[str, Any]],
    incoming: Mapping[str, Any],
    declared: Collection[str] = (),
) -> Optional[str]:
    """Why *incoming* may not be merged into *current*, or ``None`` when the result stays in bounds.

    *current* is the data the form already holds; *incoming* is what the client just sent. The result
    of ``{**current, **incoming}`` is what is judged (a key sent again replaces its value, it does not
    add a field), and *incoming* is looked at first, so a flood of keys is refused on its length
    without being copied. The message is for the person using the form and names the limit, never the
    keys.

    *declared* is the names of the fields the form itself declares. They are not the client's to add
    (a mounted form already holds one key for each), so they do not count toward ``max_form_fields``:
    a form that declares 1,200 fields can be submitted, and what the limit bounds is the keys a client
    adds beyond them. Their values still count toward ``max_form_data_size``.
    """
    max_fields, max_size = limits()
    if not max_fields and not max_size:
        return None
    current = current or {}
    if max_fields:
        if len(incoming) > max_fields + len(declared):
            return _too_many(max_fields)
        # The keys the client added: the merged keys that are not declared fields, counted without
        # building the merged mapping.
        extra = sum(1 for key in current if key not in declared)
        extra += sum(1 for key in incoming if key not in current and key not in declared)
        if extra > max_fields:
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
