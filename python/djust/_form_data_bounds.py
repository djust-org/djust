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

So the bound lives where the data is KEPT, not where a frame is read. Three settings, in
``LIVEVIEW_CONFIG``; the first two default to Django's own numbers:

* ``max_form_fields`` (1000): how many keys a form's data may hold beyond the fields it declares;
* ``max_form_data_size`` (2,621,440): how many characters its keys and values may add up to;
* ``max_form_members`` (100,000): how many members it may hold, a member being a key, a value, or a
  container inside one (#3844).

The third exists because characters do not bound TIME. Every event hands the whole of the stored data to
the template engine (a Python pass per member in ``state_pass._convert``, then the extension's
``update_state``), at about 0.7 microseconds a member: ``[""] * 15000`` costs 15,000 characters, so 174 of
them (2.61 million empty members, under the size limit) made each later event take 1.2-1.8 s, and
``[[]] * 7500`` made it 4 s (#3844). A member costs at least one character, so the count is always at most
the size, and 100,000 of them is ~70 ms of that pass. It is judged by the same walk as the size (the same
chunking, early stops, nesting bound and replaced-entry arithmetic), with every node counting one.

The size is exact: the characters of the keys and values of the data an event would leave (a string,
``bytes`` or ``bytearray`` is its length, and an empty one is 1, so that no member is free: a list of
twenty million empty strings stored under one key costs twenty million, not one; each list, tuple, set or mapping inside a value is one more
character plus its members; any other object is 8), with no character for the data itself, and a size
equal to the limit is within it. A ``memoryview`` or a dict view is such an "other object": JSON cannot
carry one, so only the project's own code can store one, and it is charged 8, not its length.

What an event costs is what it adds plus one pass over what is stored, and that pass is a few C calls
per 4,096 members (a type census, a ``sum(map(len, ...))``), not a Python step per member: with the form
filled to the cap an event cost 174-279 ms to weigh and now costs about a tenth of that (#3841). A
running per-key total was weighed and rejected: the data is a plain ``dict`` that any handler may edit
in place, so a total kept beside it is only right if every edit is seen, and checking that costs a pass
over the data anyway. Weighing the data itself has no state to go stale, so the verdict is the full
recount by construction.

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
from typing import Any, Collection, Iterator, List, Mapping, Optional, Sequence, Tuple

from .config import (
    DEFAULT_MAX_FORM_DATA_SIZE,
    DEFAULT_MAX_FORM_FIELDS,
    DEFAULT_MAX_FORM_MEMBERS,
    config,
)

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


def member_limit() -> int:
    """``max_form_members``, ``0`` meaning no limit (#3844).

    A separate function, not a third element of :func:`limits`: that pair is what callers (and tests)
    already read and replace. Validated by the config exactly as the other two are."""
    return config.get("max_form_members", DEFAULT_MAX_FORM_MEMBERS)


_END = object()

#: Containers of at least this many members are weighed a chunk at a time (see :func:`_bulk`); smaller
#: ones are walked member by member, where the setup of a chunk would cost more than it saves.
_VECTOR_MIN = 64
#: How many members :func:`_bulk` pulls and classifies at once. The budget is checked between chunks, so
#: a value is still weighed in O(budget), not O(value).
_CHUNK = 4096
#: The exact types that weigh a constant (8) or their length. A subclass (``SafeString``, an ``IntEnum``)
#: is not in the set, so it is walked member by member, with ``isinstance``, as it always was.
_SIZED = frozenset({str, bytes})
_LEAVES = frozenset({str, bytes, int, float, bool, type(None)})


def _leaf_weight(chunk: Sequence[Any]) -> Optional[int]:
    """The weight of *chunk* when every member is an exact leaf type, else ``None``.

    ``set(map(type, chunk))`` and ``sum(map(len, chunk))`` run in C, so a form whose data is a long
    list of strings, or a mapping of a million short ones, costs a handful of C passes per event
    instead of a Python-level step per member (#3841: 174-279 ms at the cap, ~15 ms now)."""
    kinds = set(map(type, chunk))
    if not kinds <= _LEAVES:
        return None
    if kinds <= _SIZED:
        return _sized_weight(chunk)
    if kinds.isdisjoint(_SIZED):
        return 8 * len(chunk)
    # Strings among other leaves (a form of text, checkboxes and numbers): the sized members are picked
    # out and measured in C, the rest are 8 each.
    sized = list(itertools.compress(chunk, map(_SIZED.__contains__, map(type, chunk))))
    return _sized_weight(sized) + 8 * (len(chunk) - len(sized))


def _sized_weight(chunk: Sequence[Any]) -> int:
    """Their lengths, an empty one counting 1: every member costs at least one character, so a list of
    twenty million empty strings is not free (#3841)."""
    lengths = list(map(len, chunk))
    return sum(lengths) + lengths.count(0)


def _take(chunk: Sequence[Any], rest: List[Any]) -> int:
    """The weight of the leaves of *chunk*; every other member is appended to *rest* to be walked."""
    weight = _leaf_weight(chunk)
    if weight is not None:
        return weight
    total = 0
    for item in chunk:
        kind = type(item)
        if kind in _LEAVES:
            total += (len(item) or 1) if kind in _SIZED else 8
        else:
            rest.append(item)
    return total


def _count_take(chunk: Sequence[Any], rest: List[Any]) -> int:
    """:func:`_take` when counting members (#3844): every exact leaf is one, whatever its length;
    every other member is appended to *rest* to be walked (it is counted, and its own members are
    taken up, when the walk reaches it)."""
    if set(map(type, chunk)) <= _LEAVES:
        return len(chunk)
    leaves = 0
    for item in chunk:
        if type(item) in _LEAVES:
            leaves += 1
        else:
            rest.append(item)
    return leaves


def _scan(
    members: Iterator[Any], rest: List[Any], total: int, budget: int, count: bool = False
) -> int:
    """Add the weight of *members* to *total*, a chunk at a time, stopping once *total* is past
    *budget*. Whatever is not an exact leaf is appended to *rest* for the walk to take up. With
    *count*, a member weighs one, not its length (the member limit counts, the size limit weighs)."""
    take = _count_take if count else _take
    while total <= budget:
        chunk = list(itertools.islice(members, _CHUNK))
        if not chunk:
            break
        total += take(chunk, rest)
        total = _charge_rest(total, rest, budget)
    return total


def _charge_rest(total: int, rest: List[Any], budget: int) -> int:
    """*total*, plus what *rest* is already known to cost when that alone passes *budget*.

    Every member of *rest* costs at least one character when the walk reaches it, so a flat list of
    twenty million empty lists is over the budget once *rest* is, and is not pulled whole to find out
    (#3841). Below the budget nothing is added: they are charged when the walk takes them up."""
    return total + len(rest) if total + len(rest) > budget else total


def _bulk(
    members: Iterator[Any], stack: List[Iterator[Any]], total: int, budget: int, count: bool = False
) -> int:
    """Weigh a big container's *members* (:func:`_scan`). The members that are containers are pushed on
    *stack* as one iterator, so nesting is followed exactly as it is for a small container."""
    rest: List[Any] = []
    total = _scan(members, rest, total, budget, count)
    if rest:
        stack.append(iter(rest))
    return total


def _bulk_mapping(
    mapping: Mapping[Any, Any],
    stack: List[Iterator[Any]],
    total: int,
    budget: int,
    count: bool = False,
) -> int:
    """:func:`_bulk` for a mapping's keys and values (not the +1 for the mapping itself)."""
    rest: List[Any] = []
    if type(mapping) is dict:
        # Keys and values are summed separately, so no (key, value) tuple is built per entry.
        total = _scan(iter(mapping.keys()), rest, total, budget, count)
        total = _scan(iter(mapping.values()), rest, total, budget, count)
    else:
        take = _count_take if count else _take
        pairs_iter = iter(mapping.items())
        while total <= budget:
            pairs = list(itertools.islice(pairs_iter, _CHUNK))
            if not pairs:
                break
            keys, values = zip(*pairs)
            total += take(keys, rest) + take(values, rest)
            total = _charge_rest(total, rest, budget)
    if rest:
        stack.append(iter(rest))
    return total


def _walk(stack: List[Iterator[Any]], budget: int, total: int = 0, count: bool = False) -> int:
    """Walk the iterators on *stack*, adding up the characters, until they run out or *total* passes
    *budget* (the caller only needs to know whether it does).

    A string, ``bytes`` or ``bytearray`` is its length (an empty one is 1); a list, tuple, set or mapping is one character
    plus its members (and a mapping's keys); anything else is a small constant. There is NO depth at
    which a container stops being looked into: a flat charge at some depth was a hole a client walks
    through by wrapping its payload in that many lists. The walk is iterative (a stack of iterators,
    one member pulled at a time), every node costs at least one character, so a cycle or a very deep
    value runs out of budget instead of recursion; past ``_MAX_WEIGH_NESTING`` levels it is refused.

    With *count* every node, whatever it is, is one (the member limit, #3844): the walk, the nesting
    bound and the early stops are the same, so the two limits cannot disagree about the shape of a value.
    """
    while stack:
        item = next(stack[-1], _END)
        if item is _END:
            stack.pop()
            continue
        if isinstance(item, (str, bytes, bytearray)):
            total += 1 if count else (len(item) or 1)
        elif isinstance(item, Mapping):
            total += 1
            if len(item) >= _VECTOR_MIN:
                # The level a small container would have opened is past the bound.
                if len(stack) >= _MAX_WEIGH_NESTING:
                    return max(total, budget + 1)
                total = _bulk_mapping(item, stack, total, budget, count)
            else:
                stack.append(itertools.chain.from_iterable(item.items()))
        elif isinstance(item, (list, tuple, set, frozenset)):
            total += 1
            if len(item) >= _VECTOR_MIN:
                if len(stack) >= _MAX_WEIGH_NESTING:
                    return max(total, budget + 1)
                total = _bulk(iter(item), stack, total, budget, count)
            else:
                stack.append(iter(item))
        else:
            total += 1 if count else 8
        if total > budget or len(stack) > _MAX_WEIGH_NESTING:
            return max(total, budget + 1)
    return total


def _weigh(value: Any, budget: int) -> int:
    """The characters *value* adds up to (a container is one character plus its members), stopping as
    soon as they exceed *budget*, so the work is O(budget) however the value is shaped. See :func:`_walk`
    for what a character is."""
    return _walk([iter((value,))], budget)


def _weigh_members(mapping: Mapping[Any, Any], budget: int, count: bool = False) -> int:
    """What a mapping's keys and values add up to: :func:`_weigh` without the one character for the
    mapping itself, because the data a form holds is not a container the client sent (#3841: every
    call used to add one, so an event could count two more than the exact sum).

    With *count*, the number of members instead: every key, every value and every container inside
    them is one (#3844)."""
    # The bottom entry stands for the one-member iterator :func:`_weigh` starts from, so a value is
    # refused at the same nesting whether it is weighed alone or as a member of the data it is in.
    stack: List[Iterator[Any]] = [iter(())]
    total = 0
    if len(mapping) >= _VECTOR_MIN:
        total = _bulk_mapping(mapping, stack, 0, budget, count)
        if total > budget:
            return max(total, budget + 1)
    else:
        stack.append(itertools.chain.from_iterable(mapping.items()))
    return _walk(stack, budget, total, count)


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

    The size is exactly the characters the merged mapping's keys and values add up to, and a size equal
    to the limit is within it (:func:`_weigh_members`). It is judged WITHOUT building the merged
    mapping: what *incoming* adds, plus what *current* holds, less what *incoming* replaces (#3841).
    """
    max_fields, max_size = limits()
    max_members = member_limit()
    if not max_fields and not max_size and not max_members:
        return None
    current = current or {}
    if max_fields:
        if len(incoming) > max_fields + len(declared):
            return _too_many(max_fields)
        # The keys the client added: the merged keys that are not declared fields, counted without
        # building the merged mapping or visiting a key of *current*: the declared names are few, so
        # the declared keys *current* holds are found by asking for those.
        names = frozenset(declared)
        extra = len(current) - sum(1 for name in names if name in current)
        extra += sum(1 for key in incoming if key not in current and key not in names)
        if extra > max_fields:
            return _too_many(max_fields)
    # Members before characters: a member costs at least a character, so the count reaches its limit
    # first for the shapes that make an event slow (millions of tiny members), and it is judged in
    # O(max_members), not O(max_size).
    if max_members and _over(current, incoming, max_members, True):
        return _too_many_members(max_members)
    if max_size and _over(current, incoming, max_size, False):
        return _too_large(max_size)
    return None


def _over(
    current: Mapping[str, Any], incoming: Mapping[str, Any], limit: int, count: bool
) -> bool:
    """Would ``{**current, **incoming}`` weigh more than *limit* (characters, or members when *count*)?

    Judged WITHOUT building the merged mapping: what *incoming* adds, plus what *current* holds, less
    what *incoming* replaces (#3841)."""
    size = _weigh_members(incoming, limit, count)
    if size <= limit and current:
        budget = limit - size
        replaced = _replaced_weight(current, incoming, limit, count)
        if replaced is None:
            # What *incoming* replaces is itself past the limit (data a project stored, or a
            # limit lowered after it was): count what survives, without the replaced entries.
            kept = {key: value for key, value in current.items() if key not in incoming}
            held = _weigh_members(kept, budget, count)
            size = limit + 1 if held > budget else size + held
        else:
            held = _weigh_members(current, budget + replaced, count)
            size = limit + 1 if held > budget + replaced else size + held - replaced
    return size > limit


def _replaced_weight(
    current: Mapping[str, Any], incoming: Mapping[str, Any], limit: int, count: bool = False
) -> Optional[int]:
    """What the entries of *current* that *incoming* sends again add up to (they are not in the merged
    mapping), or ``None`` when that is more than *limit*. Each entry is weighed as a one-entry mapping,
    so it meets the same nesting bound it meets inside *current*."""
    replaced = 0
    for key in incoming:
        if key in current:
            replaced += _weigh_members({key: current[key]}, limit - replaced, count)
            if replaced > limit:
                return None
    return replaced


def _too_many(limit: int) -> str:
    return (
        "This form received more fields than the server accepts (%d). It was not submitted." % limit
    )


def _too_many_members(limit: int) -> str:
    return (
        "This form contains more values than the server accepts (%d). It was not submitted." % limit
    )


def _too_large(limit: int) -> str:
    return (
        "This form's data is larger than the server accepts (%d characters). It was not submitted."
        % limit
    )


def warn(view: Any, message: str) -> None:
    """Say, in the server log, that *view* refused a form's data (the message carries no client data)."""
    logger.warning("%s refused form data: %s", type(view).__name__, message)
