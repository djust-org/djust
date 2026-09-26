"""Class-namespace walks survive concurrent first-use class caches (#3151).

djust caches per-class facts on the class itself the first time they are
needed (``_djust_descriptor_fields_cache``, ``_djust_template_hash_slot``,
``_djust_component_opaque``, ``_djust_warned_*``). On free-threaded CPython
(3.14t, GIL off) that ``setattr(cls, ...)`` can land while another thread is
iterating the same class's ``__dict__`` -- the first simultaneous page loads
after a deploy 500'd with ``RuntimeError: dictionary changed size during
iteration`` (snake-arena, ``PooledHTTP(threads=3)``).

Measured on 3.14t with the GIL off, only ``mappingproxy.copy()`` and
``list(mappingproxy.items())`` snapshot a class namespace atomically;
iterating it, ``dict()``/``list()``/``tuple()``/``set.update()`` over it, and
``dir()`` of a class or an instance all raise under a concurrent class write.
So every request-path walk now iterates a ``copy()`` (``_class_snapshot``).

Two kinds of test:

* **Deterministic** (any interpreter): a class attribute whose inspection
  itself writes to the class being walked -- the write lands mid-iteration on
  every run, GIL or not.
* **Hammer** (meaningful with the GIL off; run in the 3.14t CI job): reader
  threads walk fresh leaf classes over a shared base while a writer thread
  keeps adding first-use-style attributes to that base, the way view mixins
  and parent views are shared by every view that inherits them.
"""

from __future__ import annotations

import ast
import itertools
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

import pytest

from djust import LiveView
from djust.decorators import debounce, event_handler
from djust.live_view import _descriptor_fields

_COUNTER = itertools.count()


# --------------------------------------------------------------------------
# Deterministic: the class is written DURING its own walk.
# --------------------------------------------------------------------------


class _WritesOnInspection(type):
    """A metaclass whose missing-attribute lookups write to a target class.

    ``_descriptor_fields`` asks ``getattr(type(value), "_djust_state_field")``
    of every class attribute; ``ContextMixin.get_context_data`` asks
    ``getattr(value, "_djust_framework_derived")``. Either question, asked of an
    instance of this metaclass (or its class), adds an attribute to the class
    being walked -- exactly what a concurrent first-use cache does.
    """

    target: Any = None

    def __getattr__(cls, name: str) -> Any:
        target = _WritesOnInspection.target
        if target is not None and name.startswith("_djust"):
            setattr(target, f"_djust_probe_{next(_COUNTER)}", True)
        raise AttributeError(name)


class _Poke(metaclass=_WritesOnInspection):
    def __getattr__(self, name: str) -> Any:
        return getattr(type(self), name)


@pytest.fixture
def poke_target():
    def arm(cls: type) -> None:
        _WritesOnInspection.target = cls

    yield arm
    _WritesOnInspection.target = None


def _view_with_poke(**extra: Any) -> type:
    attrs = {"template": "<div dj-root>{{ a }}</div>", "a": 1, "poke": _Poke(), "z": 2}
    attrs.update(extra)
    return type(f"PokeView{next(_COUNTER)}", (LiveView,), attrs)


def test_descriptor_fields_survives_a_class_write_mid_walk(poke_target):
    cls = _view_with_poke()
    poke_target(cls)
    fields = _descriptor_fields(cls)  # RuntimeError before #3151
    assert isinstance(fields, dict)
    assert "_djust_descriptor_fields_cache" in cls.__dict__


def test_legacy_context_walk_survives_a_class_write_mid_walk(poke_target):
    cls = _view_with_poke()
    view = cls()
    view.mount(None)
    poke_target(cls)
    context = view.get_context_data()  # RuntimeError before #3151
    assert context["a"] == 1 and context["z"] == 2


# --------------------------------------------------------------------------
# Hammer: concurrent writer on a shared base (GIL off in the 3.14t job).
# --------------------------------------------------------------------------

_HAMMER_SECONDS = 0.6
_READERS = 4


def _hammer(make_base: Callable[[], type], read: Callable[[type], Any]) -> list[BaseException]:
    """Readers call ``read(leaf)`` on fresh leaves of a shared base while a
    writer adds (and trims) attributes on that base. Returns reader errors."""
    base = make_base()
    stop = threading.Event()
    errors: list[BaseException] = []
    start = threading.Barrier(_READERS + 1)

    def writer() -> None:
        start.wait()
        i = 0
        while not stop.is_set():
            setattr(base, f"_djust_first_use_{i}", i)
            i += 1
            if i % 500 == 0:  # keep the namespace bounded; deletes resize too
                for j in range(i - 500, i):
                    delattr(base, f"_djust_first_use_{j}")

    def reader() -> None:
        # One leaf per reader, returned to its first-use state before every
        # call. A fresh leaf per call would work too, but every leaf is a
        # subclass of ``base``, and each ``setattr(base, ...)`` must then
        # invalidate an ever-growing subclass list -- the writer slows to a
        # crawl and the race window closes, hiding the bug.
        leaf = type(f"Leaf{next(_COUNTER)}", (base,), {"template": "<div dj-root></div>"})
        start.wait()
        while not stop.is_set():
            for name in [n for n in leaf.__dict__.copy() if n.startswith("_djust")]:
                delattr(leaf, name)
            try:
                read(leaf)
            except BaseException as exc:  # noqa: BLE001 — any failure fails the test
                # The race is "RuntimeError: dictionary changed size during
                # iteration"; anything else is a broken reader, which must not
                # pass silently either.
                errors.append(exc)
                return

    old = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)  # on a GIL build, switch as often as possible
    threads = [threading.Thread(target=writer)] + [
        threading.Thread(target=reader) for _ in range(_READERS)
    ]
    try:
        for t in threads:
            t.start()
        time.sleep(_HAMMER_SECONDS)
    finally:
        stop.set()
        for t in threads:
            t.join(timeout=30)
        sys.setswitchinterval(old)
    stuck = [t.name for t in threads if t.is_alive()]
    assert not stuck, f"hammer threads did not stop: {stuck}"
    return errors


def _base_view() -> type:
    from djust._state import state

    attrs: dict[str, Any] = {f"field_{i}": state(i) for i in range(40)}
    attrs.update({f"plain_{i}": i for i in range(40)})

    @event_handler()
    @debounce(wait=0.3)
    def search(self: Any, **kwargs: Any) -> None:
        pass

    attrs["search"] = search

    def boom(self: Any) -> Any:
        raise AttributeError("boom")

    attrs["boom"] = property(boom)
    return type(f"SharedBase{next(_COUNTER)}", (LiveView,), attrs)


def _descriptor_walk(leaf: type) -> None:
    fields = _descriptor_fields(leaf)
    assert "field_0" in fields


def _context_walk(leaf: type) -> None:
    view = leaf()
    view.mount(None)
    view.get_context_data()


def _exposure_contract(leaf: type) -> None:
    from djust._exposure import ExposureContract

    ExposureContract.from_view_class(leaf)


def _handler_plan(leaf: type) -> None:
    from djust._parameter_metadata import _event_methods

    methods = _event_methods(leaf())
    assert "search" in methods


def _mount_frame_handler_config(leaf: type) -> None:
    from djust.runtime import ViewRuntime

    runtime = ViewRuntime.__new__(ViewRuntime)
    config = runtime._extract_handler_config(leaf())
    # A race used to be swallowed by the extractor's ``except Exception`` and
    # silently ship a mount frame WITHOUT the client rate-limit config.
    if config is None or "search" not in config:
        raise RuntimeError("handler_config dropped by a concurrent class write")


def _raising_property_propagates(leaf: type) -> None:
    from djust import _rust

    # Django's ``bit in dir(current)`` re-raise probe (#2506), in Rust, now
    # answers by membership. Building ``dir()`` raced a class write, answered
    # False, and the property's error rendered as "" instead of propagating.
    try:
        rendered = _rust.render_template("[{{ o.boom }}]", {"o": leaf()})
    except AttributeError:
        return
    raise RuntimeError(f"a raising @property rendered {rendered!r} instead of raising")


def _attribute_names(leaf: type) -> None:
    from djust._class_snapshot import attribute_names

    attribute_names(leaf)
    attribute_names(leaf())


@pytest.mark.parametrize(
    "read",
    [
        _descriptor_walk,
        _context_walk,
        _exposure_contract,
        _handler_plan,
        _mount_frame_handler_config,
        _raising_property_propagates,
        _attribute_names,
    ],
    ids=lambda f: f.__name__.lstrip("_"),
)
def test_request_path_walks_survive_concurrent_class_writes(read):
    errors = _hammer(_base_view, read)
    assert not errors, f"{len(errors)} reader(s) raced a class write: {errors[0]!r}"


# --------------------------------------------------------------------------
# The snapshot helpers mirror what they replace.
# --------------------------------------------------------------------------


class _Slotted:
    __slots__ = ("x",)


class _Plain:
    cls_attr = 1

    def __init__(self) -> None:
        self.inst_attr = 2


class _CustomDir:
    def __dir__(self) -> list[str]:
        return ["only_this"]


@pytest.mark.parametrize(
    "obj",
    [_Plain, _Plain(), _Slotted, _Slotted(), LiveView, _CustomDir(), 3, "text", int],
    ids=[
        "class",
        "instance",
        "slots-class",
        "slots-instance",
        "liveview",
        "custom-dir",
        "int",
        "str",
        "int-type",
    ],
)
def test_attribute_names_equals_dir(obj):
    from djust._class_snapshot import attribute_names

    assert attribute_names(obj) == dir(obj)


def test_namespace_is_an_independent_copy():
    from djust._class_snapshot import namespace

    snap = namespace(_Plain)
    assert snap["cls_attr"] == 1 and type(snap) is dict
    snap["added"] = True
    assert "added" not in vars(_Plain)


# --------------------------------------------------------------------------
# Mechanical gate: no request-path code iterates a live namespace or calls dir().
# --------------------------------------------------------------------------

_PKG = Path(__file__).resolve().parents[1]
_GATE_SKIP_DIRS = {"tests", "checks", "management"}  # startup / tooling only
_GATE_SKIP_FILES = {
    "_class_snapshot.py",  # the helpers themselves
    "testing.py",  # test utilities
    "hot_view_replacement.py",  # dev-only file-watcher path
}
# (file, function, receiver): reviewed, not a shared class/module namespace.
_GATE_ALLOWED = {
    # Per-view instance dicts: owned by one session, mutated on its own turn.
    ("observability/views.py", "_lenient_assigns", "view"),
    ("runtime.py", "handler", "view"),
    ("runtime.py", "_dispatch_event_render", "view"),
    # Function attributes copied once in ``as_view`` (Django's own pattern).
    ("live_view.py", "as_view", "cls.dispatch"),
    # ``dir(JS)`` builds the static surface manifest, not a request.
    ("schema.py", "get_surface_manifest", "JS"),
    # Component gallery (development tool) import lookup over a module.
    ("theming/gallery/component_registry.py", "get_python_component_import", "module"),
}
_ITERATING_CALLS = {"dict", "list", "tuple", "set", "frozenset", "sorted", "update"}


def _namespace_receiver(node: ast.AST) -> str | None:
    """``vars(X)`` / ``X.__dict__`` (optionally ``.items()/.keys()/.values()``) -> X."""
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in ("items", "keys", "values")
        and not node.args
    ):
        node = node.func.value
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "vars"
        and len(node.args) == 1
    ):
        return ast.unparse(node.args[0])
    if isinstance(node, ast.Attribute) and node.attr == "__dict__":
        return ast.unparse(node.value)
    return None


def _live_namespace_walks() -> list[tuple[str, str, str, int]]:
    found = []
    for path in sorted(_PKG.rglob("*.py")):
        rel = path.relative_to(_PKG)
        if set(rel.parts[:-1]) & _GATE_SKIP_DIRS or rel.name in _GATE_SKIP_FILES:
            continue
        found += _namespace_walks_in(path.read_text(encoding="utf-8"), rel.as_posix())
    return found


def _namespace_walks_in(source: str, rel: str) -> list[tuple[str, str, str, int]]:
    """Syntactic only: an alias (``ns = vars(C)`` then ``for k in ns``) is not
    followed, so the gate backs up review rather than replacing it (#3181)."""
    found = []
    tree = ast.parse(source)
    owner: dict[ast.AST, str] = {}
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for child in ast.walk(fn):
                owner[child] = fn.name  # innermost wins: walk order is outer-first
    for node in ast.walk(tree):
        receiver = None
        line = getattr(node, "lineno", 0)
        walks_mro = False  # dir()/getmembers() read every class dict, even for self
        if isinstance(node, ast.Call):
            func = node.func
            if (isinstance(func, ast.Name) and func.id in ("dir", "getmembers")) or (
                isinstance(func, ast.Attribute)
                and func.attr == "getmembers"
                and isinstance(func.value, ast.Name)
                and func.value.id == "inspect"
            ):
                receiver = ast.unparse(node.args[0]) if node.args else "<scope>"
                walks_mro = True
        if isinstance(node, ast.Dict):  # {**vars(X), ...} unpacks by iterating
            for key, value in zip(node.keys, node.values):
                if key is None and receiver is None:
                    receiver = _namespace_receiver(value)
        if isinstance(node, ast.Call):  # f(**vars(X)) / f(*vars(X)) iterate too (#3181)
            for keyword in node.keywords:
                if keyword.arg is None and receiver is None:
                    receiver = _namespace_receiver(keyword.value)
            for positional in node.args:
                if isinstance(positional, ast.Starred) and receiver is None:
                    receiver = _namespace_receiver(positional.value)
        if isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            receiver = _namespace_receiver(node.iter)
            line = node.iter.lineno
        elif isinstance(node, ast.Call) and node.args:
            func = node.func
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            arg = node.args[0]
            # ``list(ns.items())`` is atomic; ``list(ns)`` is not.
            if name in _ITERATING_CALLS and not (name == "list" and isinstance(arg, ast.Call)):
                receiver = receiver or _namespace_receiver(arg)
        # ``self.__dict__`` is the instance's own dict; ``dir(self)`` is not.
        if receiver is None or (receiver == "self" and not walks_mro):
            continue
        key = (rel, owner.get(node, "<module>"), receiver)
        if key not in _GATE_ALLOWED:
            found.append((*key, line))
    return found


def test_no_request_path_code_iterates_a_live_namespace():
    """Iterate ``namespace(X)`` / ``attribute_names(X)`` from ``_class_snapshot``
    instead: on a free-threaded build a first-use class cache written by another
    thread makes the live walk raise (#3151)."""
    offenders = _live_namespace_walks()
    assert not offenders, "\n".join(f"{f}:{line} {fn}() walks {r}" for f, fn, r, line in offenders)


@pytest.mark.parametrize(
    "source, receiver",
    [
        ("def f(C):\n    return g(**vars(C))\n", "C"),
        ("def f(C):\n    return g(**C.__dict__)\n", "C"),
        ("def f(C):\n    return g(1, **type(C).__dict__)\n", "type(C)"),
        ("def f(C):\n    return g(*vars(C))\n", "C"),
        ("def f(C):\n    return g(*C.__dict__.keys())\n", "C"),
        # The shapes the gate already caught before #3181.
        ("def f(C):\n    for k in vars(C):\n        pass\n", "C"),
        ("def f(C):\n    return {**C.__dict__}\n", "C"),
    ],
)
def test_gate_flags_unpacking_a_live_namespace(source, receiver):
    """#3181: call-argument unpacking iterates the namespace like a ``for`` does."""
    assert [(fn, r) for _, fn, r, _ in _namespace_walks_in(source, "x.py")] == [("f", receiver)]


@pytest.mark.parametrize(
    "source",
    [
        # presence.py's shape: an instance's own dict, owned by one session.
        "def f(self):\n    return self.fmt.format(**self.__dict__)\n",
        "def f(C):\n    return g(**namespace(C))\n",
        "def f(C):\n    return g(**kwargs)\n",
    ],
)
def test_gate_leaves_safe_unpacking_alone(source):
    assert _namespace_walks_in(source, "x.py") == []
