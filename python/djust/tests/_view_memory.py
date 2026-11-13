"""What one connected view holds between turns, by owner (#3998).

Each open WebSocket keeps a consumer, a view and a Rust renderer alive, so a
process's memory grows with open sockets times what one of them holds. This
module measures that without reading the process's resident size, which
depends on the allocator, the machine and what else ran in the process:

* The Python side is a ``sys.getsizeof`` walk over the consumer and the view.
  Every object is counted once, for the first owner that reaches it, in the
  fixed order of :data:`OWNERS`. A fingerprint tree's leaves ARE the state's
  own strings and numbers, so walking the state first charges them to the
  state and leaves the fingerprint trees with what they add: their tuples.
* The Rust side is ``RustLiveView.retained_bytes()``: capacities and
  ``size_of``, the same on every machine.

Both are lower bounds on what the allocators hold (block rounding, free lists)
and neither is a clock or a resident-set reading, so a test can assert on the
numbers and :func:`fit` can state how they grow.

The Rust numbers are the same on every machine and interpreter. The Python
numbers do not depend on the machine but do depend on the interpreter: a
tuple is 8 bytes larger from CPython 3.14, and a free-threaded build adds 16
bytes to most objects. Those are fixed amounts per object, by type, so
:func:`reference_sizeof` takes them off again and reports every object as
CPython 3.12 sizes it. The published numbers are stated for 3.12, and the
tests that compare against them measure with ``reference_sizeof``, which makes
them the same test on every interpreter.

``measure`` mounts a view over a real ``WebsocketCommunicator``, sends the
events it is given and reports the bytes per owner. ``test_view_memory.py``
is where the numbers in ``docs/website/guides/scaling.md`` come from, and it
fails when a published number drifts from what is measured.

Checked once against the allocators (not a test: it depends on the platform):
the Python total was within 1% of ``tracemalloc`` for a 1,000-row view, plus
about 17 KB per socket of event-loop and channel objects this walk does not
reach; the Rust total was 14-16% under what ``malloc`` reported in use.
"""

from __future__ import annotations

import gc
import sys
import types
import weakref
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

#: Attributes of the view that are the previous render's bookkeeping.
FINGERPRINT_ATTRS = ("_prev_context_fingerprints", "_prev_request_scoped")
REF_ATTRS = ("_prev_context_refs", "_prev_context_immutables")

#: Owners, in the order the walk visits them (an object reached by two owners
#: is charged to the first).
OWNERS = (
    "py.state",
    "py.dirty_baseline",
    "py.context_fingerprints",
    "py.context_refs",
    "py.recovery_html",
    "py.view_other",
    "py.consumer_other",
    "rust.template_source",
    "rust.state",
    "rust.vdom",
    "rust.last_html",
    "rust.node_html_cache",
    "rust.text_node_index",
    "rust.fragment_text_map",
    "rust.loop_render_cache",
    "rust.other",
)

_SHARED_TYPES: Tuple[type, ...] = (
    type,
    types.ModuleType,
    types.FunctionType,
    types.BuiltinFunctionType,
    types.MethodType,
    types.CodeType,
    types.MethodDescriptorType,
    types.WrapperDescriptorType,
    types.GetSetDescriptorType,
    types.MemberDescriptorType,
    weakref.ReferenceType,
    property,
    classmethod,
    staticmethod,
)


#: ``sys.getsizeof`` of the empty value of each builtin type on CPython 3.12
#: (and 3.13, which sizes them the same). A later interpreter differs from
#: these by a fixed amount per object of the type, whatever its length:
#: measured on 3.13, 3.14, 3.14t, 3.15 and 3.15t.
REFERENCE_PYTHON = (3, 12)
_REFERENCE_EMPTY = {
    tuple: 40,
    list: 56,
    dict: 64,
    set: 216,
    frozenset: 216,
    str: 41,
    bytes: 33,
    int: 28,
    bool: 28,
    float: 24,
    type(None): 16,
}
_EMPTY_VALUES: Tuple[Any, ...] = ((), [], {}, set(), frozenset(), "", b"", 0, False, 0.0, None)
#: What this interpreter adds to one object of each type, over 3.12.
_EXTRA: Dict[type, int] = {
    type(empty): sys.getsizeof(empty) - _REFERENCE_EMPTY[type(empty)] for empty in _EMPTY_VALUES
}
#: The same for an instance of any other class (its object header).
_EXTRA_OBJECT = sys.getsizeof(object()) - 16


def reference_sizeof(obj: Any) -> int:
    """``sys.getsizeof(obj)`` as CPython 3.12 would report it."""
    return sys.getsizeof(obj) - _EXTRA.get(type(obj), _EXTRA_OBJECT)


def _is_process_wide(obj: Any) -> bool:
    """Is *obj* something every socket shares (a class, a module, a function)?"""
    if isinstance(obj, _SHARED_TYPES):
        return True
    module = type(obj).__module__ or ""
    # Settings, the channel layer, loggers, locks and the event loop are one
    # per process; a walk that entered them would charge the process to a view.
    return module.startswith(
        (
            "logging",
            "threading",
            "_thread",
            "asyncio",
            "concurrent",
            "channels.layers",
            "channels_redis",
            "django.conf",
            "django.apps",
            "django.db.backends",
            "django.template.backends",
            "django.template.engine",
            "django.urls",
        )
    )


def deep_bytes(
    root: Any,
    seen: Set[int],
    stop: Sequence[Any] = (),
    sizeof: Callable[[Any], int] = sys.getsizeof,
) -> int:
    """``sys.getsizeof`` of *root* and everything it alone reaches.

    *seen* holds the ``id()`` of every object already charged; it is updated.
    Objects in *stop* are not entered (the view while walking the consumer).
    *sizeof* is :func:`reference_sizeof` for a number that is compared with a
    published one.
    """
    stop_ids = {id(obj) for obj in stop}
    total = 0
    stack = [root]
    while stack:
        obj = stack.pop()
        oid = id(obj)
        if oid in seen or oid in stop_ids or _is_process_wide(obj):
            continue
        seen.add(oid)
        try:
            total += sizeof(obj)
        except TypeError:
            continue
        if isinstance(obj, (str, bytes, bytearray, int, float, bool, type(None))):
            continue
        if isinstance(obj, dict):
            stack.extend(obj.keys())
            stack.extend(obj.values())
            continue
        if isinstance(obj, (list, tuple, set, frozenset)):
            stack.extend(obj)
            continue
        attrs = getattr(obj, "__dict__", None)
        if isinstance(attrs, dict):
            stack.append(attrs)
        for klass in type(obj).__mro__:
            for name in getattr(klass, "__slots__", ()) or ():
                if isinstance(name, str) and name not in ("__dict__", "__weakref__"):
                    try:
                        stack.append(getattr(obj, name))
                    except AttributeError:
                        pass
    return total


def state_names(view: Any) -> List[str]:
    """The names on *view* that hold application state."""
    from djust.change_detection import CHANGE_DETECTION_IGNORED

    framework = getattr(view, "_framework_attrs", frozenset())
    return [
        name
        for name in vars(view)
        if name not in framework and name not in CHANGE_DETECTION_IGNORED
    ]


def report(
    consumer: Any, view: Any, sizeof: Callable[[Any], int] = sys.getsizeof
) -> Dict[str, int]:
    """Bytes held per owner for one connected view.

    With ``sizeof=reference_sizeof`` the Python side is what CPython 3.12
    would hold, on any interpreter.
    """
    gc.collect()
    seen: Set[int] = set()
    out: Dict[str, int] = dict.fromkeys(OWNERS, 0)
    attrs = vars(view)
    rust_view = attrs.get("_rust_view")
    seen.add(id(rust_view))

    # A component reaches its page, and a view its consumer: neither is
    # entered while an owner's own data is walked.
    both = (view, consumer)
    for name in state_names(view):
        out["py.state"] += deep_bytes(attrs[name], seen, stop=both, sizeof=sizeof)
    out["py.dirty_baseline"] = deep_bytes(
        attrs.get("_dirty_baseline"), seen, stop=both, sizeof=sizeof
    )
    for name in FINGERPRINT_ATTRS:
        out["py.context_fingerprints"] += deep_bytes(
            attrs.get(name), seen, stop=both, sizeof=sizeof
        )
    for name in REF_ATTRS:
        out["py.context_refs"] += deep_bytes(attrs.get(name), seen, stop=both, sizeof=sizeof)
    consumer_attrs = vars(consumer)
    out["py.recovery_html"] = deep_bytes(consumer_attrs.get("_recovery_html"), seen, sizeof=sizeof)
    out["py.view_other"] = deep_bytes(view, seen, stop=(consumer,), sizeof=sizeof)
    out["py.consumer_other"] = deep_bytes(consumer, seen, stop=(view,), sizeof=sizeof)

    if rust_view is not None:
        real = getattr(rust_view, "_real", rust_view)
        rust = real.retained_bytes()
        for owner in OWNERS:
            if owner.startswith("rust."):
                out[owner] = rust[owner[len("rust.") :]]
        out["rust.vdom_nodes"] = rust["vdom_nodes"]
        out["rust.vdom_node_structs"] = rust["vdom_node_structs"]
        out["rust.vdom_attrs"] = rust["vdom_attrs"]
        out["rust.vdom_text"] = rust["vdom_text"]
        out["rust.vdom_tags"] = rust["vdom_tags"]
        out["rust.vdom_ids"] = rust["vdom_ids"]
    out["py.total"] = sum(v for k, v in out.items() if k in OWNERS and k.startswith("py."))
    out["rust.total"] = sum(v for k, v in out.items() if k in OWNERS and k.startswith("rust."))
    out["total"] = out["py.total"] + out["rust.total"]
    return out


class Recorded:
    """The consumers a block created, in order."""

    def __init__(self) -> None:
        self.consumers: List[Any] = []


def recording_consumer(recorded: Recorded) -> type:
    """A ``LiveViewConsumer`` subclass that lists its instances in *recorded*."""
    from djust.websocket import LiveViewConsumer

    class Recording(LiveViewConsumer):  # type: ignore[misc]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            recorded.consumers.append(self)

    return Recording


async def mount_recorded(view_class: type, session_key: str, recorded: Recorded) -> Tuple[Any, Any]:
    """Mount *view_class* over a real socket; ``(communicator, mount frame)``."""
    from channels.testing import WebsocketCommunicator
    from django.contrib.auth.models import AnonymousUser
    from django.contrib.sessions.backends.db import SessionStore

    from . import _per_turn_harness as harness

    comm = WebsocketCommunicator(recording_consumer(recorded).as_asgi(), "/ws/")
    comm.scope["session"] = SessionStore(session_key)
    comm.scope["user"] = AnonymousUser()
    connected, _ = await comm.connect()
    assert connected
    await comm.receive_json_from()  # connect ack
    await comm.send_json_to(
        {
            "type": "mount",
            "view": f"{view_class.__module__}.{view_class.__name__}",
            "url": harness.PATH,
        }
    )
    frame = await comm.receive_json_from()
    assert frame["type"] == "mount", frame
    return comm, frame


async def measure(
    view_class: type,
    events: Iterable[Tuple[str, Optional[Dict[str, Any]]]] = (),
    module: Optional[str] = None,
    sizeof: Callable[[Any], int] = sys.getsizeof,
) -> Dict[str, int]:
    """Mount *view_class*, send *events*, and report what the socket then holds.

    The page's size is reported as ``page_bytes``: the HTML of the mount frame.
    """
    from . import _per_turn_harness as harness

    recorded = Recorded()
    key = await harness.new_session_key_async()
    with harness.ws_settings(module or view_class.__module__):
        comm, mount_frame = await mount_recorded(view_class, key, recorded)
        try:
            for name, params in events:
                await harness.event(comm, name, params)
            consumer = recorded.consumers[-1]
            view = consumer.view_instance
            assert view is not None
            out = report(consumer, view, sizeof=sizeof)
            out["page_bytes"] = len((mount_frame.get("html") or "").encode())
        finally:
            await comm.disconnect()
    return out


def fit(xs: Sequence[float], ys: Sequence[float]) -> Tuple[float, float, float]:
    """Least-squares line through the points: ``(intercept, slope, worst residual)``.

    The residual is relative to the measured value, so ``0.02`` reads "the line
    is within 2% of every point".
    """
    n = len(xs)
    assert n >= 2 and n == len(ys)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    sxx = sum((x - mean_x) ** 2 for x in xs)
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / sxx
    intercept = mean_y - slope * mean_x
    worst = max(abs(intercept + slope * x - y) / y for x, y in zip(xs, ys) if y)
    return intercept, slope, worst
