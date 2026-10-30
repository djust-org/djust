"""The Django-facing half of the parity ratchet (#3663).

Renders the same template with Django's engine (the Python tag) and with
djust's Rust engine (``DjustTemplateBackend``; a native handler wins) for every
tag of ``djust_components`` and ``theme_tags`` that has a native handler, and
classifies the pair with ``classify``.

This module changes PROCESS-WIDE state — it replaces ``uuid.uuid4`` and
``datetime.date`` / ``datetime.datetime``, and configures Django's settings —
so it is only ever run in a fresh process. ``scripts/template-library-parity.py``
is that process; the tests start it as a subprocess, which is also what keeps
the patched clock and uuids from reaching any other test.

Determinism, in the order it is applied:

* ``install_determinism()`` runs BEFORE Django or djust is imported. uuids
  count up (and the counter is reset before every render, so two engines that
  ask for the same number of ids get the same ones); ``today()`` / ``now()``
  are a fixed instant. Both must precede the imports because a module that
  does ``from uuid import uuid4`` binds the function it finds at import time.
* Settings are explicit (``TIME_ZONE``, ``LANGUAGE_CODE``, a private
  ``ROOT_URLCONF``), never inherited from ``DJANGO_SETTINGS_MODULE``.
* Memory addresses a ``repr`` leaks into HTML are normalised.
* Every collection that reaches the output is sorted; nothing depends on
  ``PYTHONHASHSEED`` or on the interpreter version (see ``classify`` for why
  the structural scan avoids ``html.parser``, and ``_source_of`` for why
  parameter discovery avoids ``inspect.getsource`` on classes).
"""

from __future__ import annotations

import ast
import contextlib
import functools
import importlib
import inspect
import itertools
import logging
import os
import re
import sys
import types
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional

from . import classify, variants

#: The libraries this ratchet covers: ``{% load <label> %}`` -> module.
LIBRARIES = {
    "djust_components": "djust.components.templatetags.djust_components",
    "theme_tags": "djust.theming.templatetags.theme_tags",
}

#: Tags measured by nothing, and why. Empty on purpose: the clock and the uuids
#: are fixed, so no tag is inherently non-deterministic. A tag may only be added
#: here with a recorded reason, and ``test_template_library_parity_3663`` pins
#: that this table and the baseline never overlap.
EXCLUDED: dict[str, str] = {}

#: What a planted Python-side difference appends (``--plant``).
PLANT_MARKER = "<i data-parity-plant></i>"

FROZEN_DATE = (2024, 3, 15)
FROZEN_DATETIME = (2024, 3, 15, 12, 0, 0)

_ids = itertools.count()


def install_determinism() -> None:
    """Fix uuids and the clock for this whole process. Call before importing Django."""
    import datetime

    def fake_uuid4() -> uuid.UUID:
        return uuid.UUID(int=next(_ids) + 1)

    uuid.uuid4 = fake_uuid4  # type: ignore[assignment]

    real_date, real_datetime = datetime.date, datetime.datetime

    class FrozenDate(real_date):  # type: ignore[valid-type, misc]
        @classmethod
        def today(cls) -> Any:
            return cls(*FROZEN_DATE)

    class FrozenDateTime(real_datetime):  # type: ignore[valid-type, misc]
        @classmethod
        def now(cls, tz: Any = None) -> Any:
            base = cls(*FROZEN_DATETIME, tzinfo=datetime.timezone.utc)
            return base.astimezone(tz) if tz is not None else base.replace(tzinfo=None)

        @classmethod
        def utcnow(cls) -> Any:
            return cls(*FROZEN_DATETIME)

        @classmethod
        def today(cls) -> Any:
            return cls(*FROZEN_DATETIME)

    datetime.date = FrozenDate  # type: ignore[misc]
    datetime.datetime = FrozenDateTime  # type: ignore[misc]


def reset_ids() -> None:
    """Rewind every id source before a render, so the engine that renders second
    is not handed the ids the first one consumed.

    ``uuid4`` is ours. ``_tooltip_id_counter`` is the one process-global counter
    in the two libraries (``dj-tooltip-tip-N``); the Python tag and the Rust
    handler both draw from it, so without this they alternate and every tooltip
    "differs" in its ids. ``--self-compare`` is what finds the next one.
    """
    global _ids
    _ids = itertools.count()
    from djust.components.templatetags import djust_components

    djust_components._tooltip_id_counter = itertools.count(1)


def configure_django() -> None:
    """Settings for a process that does nothing but run this audit."""
    import django
    from django.conf import settings

    if settings.configured:
        raise RuntimeError("the parity harness must run in a fresh process")
    os.environ.pop("DJANGO_SETTINGS_MODULE", None)
    # The differential deliberately feeds tags bad input (None for a count, an
    # unreachable tile URL); their warnings are the measurement, not news.
    logging.disable(logging.ERROR)
    urls = types.ModuleType("template_library_parity_urls")
    urls.urlpatterns = []  # type: ignore[attr-defined]
    sys.modules[urls.__name__] = urls
    settings.configure(
        DEBUG=False,
        SECRET_KEY="template-library-parity-not-a-secret",
        USE_TZ=True,
        TIME_ZONE="UTC",
        LANGUAGE_CODE="en-us",
        INSTALLED_APPS=[
            "django.contrib.contenttypes",
            "django.contrib.auth",
            "djust",
            "djust.theming",
            "djust.components",
        ],
        DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "NAME": "django",
                "DIRS": [],
                "APP_DIRS": True,
                "OPTIONS": {},
            },
            {
                "BACKEND": "djust.template.backend.DjustTemplateBackend",
                "NAME": "djust",
                "DIRS": [],
                "APP_DIRS": True,
                "OPTIONS": {},
            },
        ],
        ALLOWED_HOSTS=["*"],
        ROOT_URLCONF=urls.__name__,
    )
    django.setup()


# --------------------------------------------------------------------------- #
# Inventory and parameter discovery


@functools.lru_cache(maxsize=None)
def _module_text(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


@functools.lru_cache(maxsize=None)
def _top_level_nodes(path: str) -> dict[str, ast.AST]:
    """``{name: node}`` of a module's top-level defs, LAST binding wins."""
    found: dict[str, ast.AST] = {}
    for node in ast.parse(_module_text(path)).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            found[node.name] = node
    return found


def _node_of(obj: Any) -> tuple[str, ast.AST] | None:
    obj = inspect.unwrap(obj)
    module = sys.modules.get(getattr(obj, "__module__", ""))
    path = getattr(module, "__file__", None)
    node = _top_level_nodes(path).get(obj.__name__) if path else None
    return (path, node) if path and node is not None else None


def _source_of(obj: Any) -> str:
    """Source text of a top-level function or class, whatever the interpreter.

    ``inspect.getsource`` finds a CLASS by searching the module's AST on 3.12
    and by ``__firstlineno__`` on 3.13+; the two disagree when a module binds
    the name twice. Python itself resolves the name to the LAST top-level
    binding, so that is what this returns. ``""`` when there is none.
    """
    found = _node_of(obj)
    return (ast.get_source_segment(_module_text(found[0]), found[1]) or "") if found else ""


_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _keys_in(node: ast.AST) -> set[str]:
    """String keys a function or class reads: ``x["k"]``, ``x.get("k")``, ``"k" in x``.

    Only identifiers: a key that is not one cannot be a template argument.
    """
    keys: set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Constant):
            if isinstance(n.slice.value, str):
                keys.add(n.slice.value)
        elif isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
            if n.func.attr in ("get", "pop", "setdefault") and n.args:
                first = n.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    keys.add(first.value)
        elif isinstance(n, ast.Compare) and isinstance(n.left, ast.Constant):
            if isinstance(n.left.value, str) and any(isinstance(o, ast.In) for o in n.ops):
                keys.add(n.left.value)
    return {k for k in keys if _IDENTIFIER.fullmatch(k)}


def _keys_of(src: str) -> set[str]:
    return _keys_in(ast.parse(src))


#: Only code in the libraries under test is followed: a helper in Django or in
#: the engine reads keys that are not tag arguments.
_FOLLOW_PREFIXES = ("djust.components", "djust.theming")
#: Parameters that carry the argument dict or the body rather than name an argument.
_CARRIERS = frozenset(
    {
        "self",
        "cls",
        "context",
        "kw",
        "kwargs",
        "args",
        "content",
        "body",
        "item",
        "request",
        "parser",
    }
)
_MAX_NODES = 80
_MAX_DEPTH = 5


def _callees(node: ast.AST, owner: Any) -> list[Any]:
    """The djust-library functions and classes ``node`` calls by name.

    A call resolves through the node's own function-local ``from X import Y``
    first (that is how every native handler reaches ``render_modal`` and its
    siblings), then through the owner module's globals.
    """
    local: dict[str, tuple[str, str]] = {}
    for n in ast.walk(node):
        if isinstance(n, ast.ImportFrom) and n.module and not n.level:
            for alias in n.names:
                local[alias.asname or alias.name] = (n.module, alias.name)
    module_globals = vars(sys.modules.get(getattr(owner, "__module__", ""), None) or owner)
    found: list[Any] = []
    for n in ast.walk(node):
        if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)):
            continue
        name = n.func.id
        if name in local:
            module = local[name][0]
            if not module.startswith(_FOLLOW_PREFIXES):
                continue
            target = getattr(importlib.import_module(module), local[name][1], None)
        else:
            target = module_globals.get(name)
        if (inspect.isfunction(target) or inspect.isclass(target)) and str(
            getattr(target, "__module__", "")
        ).startswith(_FOLLOW_PREFIXES):
            found.append(target)
    return found


def discover(root: Any) -> tuple[set[str], set[str]]:
    """``(keys read, helper parameters)`` for ``root``, following calls transitively.

    A native handler or a compile function rarely reads its arguments itself:
    it hands them to ``render_modal`` / ``render_menu_item`` / ..., which read
    them. Stopping at the entry point finds one key of eleven and leaves the
    rest of the tag never exercised, which is how a handler could drop a
    parameter and the baseline stay green. Parameters of a ``render_*`` helper
    count as arguments; other helpers contribute only the keys they read.
    """
    keys: set[str] = set()
    params: set[str] = set()
    seen: set[tuple[str, str]] = set()
    queue: list[tuple[Any, int]] = [(root, 0)]
    while queue and len(seen) < _MAX_NODES:
        obj, depth = queue.pop(0)
        found = _node_of(obj)
        ident = (str(getattr(obj, "__module__", "")), str(getattr(obj, "__qualname__", "")))
        if found is None or ident in seen:
            continue
        seen.add(ident)
        node = found[1]
        keys |= _keys_in(node)
        if obj is not root and inspect.isfunction(obj) and obj.__name__.startswith("render_"):
            params |= {p for p in inspect.signature(obj).parameters if p not in _CARRIERS}
        if depth < _MAX_DEPTH:
            queue.extend((c, depth + 1) for c in _callees(node, obj))
    return keys, params


def _native_kinds(name: str) -> list[str]:
    from djust import _rust

    kinds = []
    if _rust.has_tag_handler(name):
        kinds.append("tag")
    if _rust.has_block_tag_handler(name):
        kinds.append("block")
    if _rust.has_assign_tag_handler(name):
        kinds.append("assign")
    if _rust.has_raw_block_tag_handler(name):
        kinds.append("raw")
    return kinds


def _python_params(fn: Any, kind: str) -> tuple[list[tuple[str, str, str]], list[str]]:
    """``(signature, keys read)``: signature for simple/inclusion tags, keys for raw ones."""
    if kind in variants.SIMPLE_KINDS:
        wrapped = getattr(fn, "__wrapped__", None)
        sig: list[tuple[str, str, str]] = []
        if wrapped:
            for p in inspect.signature(wrapped).parameters.values():
                required = p.default is inspect.Parameter.empty and p.kind in (
                    p.POSITIONAL_OR_KEYWORD,
                    p.KEYWORD_ONLY,
                )
                sig.append((p.name, "req" if required else "opt", p.kind.name))
        return sig, []
    keys, params = discover(fn)
    return [], sorted(keys | params)


def _rust_handler_keys() -> dict[str, list[str]]:
    from djust.components import rust_handlers as rh

    handlers = {}
    for name, handler in rh.INLINE_HANDLERS:
        handlers[name] = handler
    for name, _end, handler in rh.BLOCK_HANDLERS:
        handlers[name] = handler
    out = {}
    for name, h in handlers.items():
        keys, params = discover(type(h))
        out[name] = sorted(keys | params)
    return out


def inventory() -> dict[str, variants.TagInfo]:
    """Every tag of both libraries, keyed ``"<label>:<name>"``, in a fixed order."""
    from django.template.library import import_library
    from djust import template_libraries as tl

    rust_keys = _rust_handler_keys()
    infos: dict[str, variants.TagInfo] = {}
    for label, dotted in LIBRARIES.items():
        library = import_library(dotted)
        for name in sorted(library.tags):
            fn = library.tags[name]
            kind = tl._classify(fn)
            sig, keys = _python_params(fn, kind)
            native = _native_kinds(name)
            infos[f"{label}:{name}"] = variants.TagInfo(
                lib=label,
                name=name,
                py_kind=kind,
                sig=sig,
                py_keys=keys,
                rust_keys=rust_keys.get(name),
                native=native,
                is_block=("block" in native) or (kind == "raw" and tl._consumes_body(fn)),
            )
    return infos


# --------------------------------------------------------------------------- #
# The differential

EVIL = "<script>alert(1)</script>\"'&<img src=x onerror=alert(2)>"


def _bound_form() -> Any:
    from django import forms

    class Form(forms.Form):
        name = forms.CharField(required=True)
        age = forms.IntegerField(required=True)

        def clean(self) -> None:
            # A form-level error: ``{% form_errors %}`` renders only these.
            raise forms.ValidationError("Form-level <b>error</b>")

    form = Form(data={})
    form.is_valid()
    return form


def make_context(with_form: bool = True) -> dict[str, Any]:
    from django.utils.safestring import mark_safe

    item = {
        "id": "i1", "key": "k1", "name": "Item", "label": "Label", "title": "Title",
        "text": "Text", "description": "Desc", "url": "/u/", "href": "/h/", "src": "/s.png",
        "icon": "star", "color": "#336699", "status": "ok", "type": "file", "content": "Content",
        "time": "2024-01-02", "value": 3, "count": 3, "total": 10, "x": 1, "y": 2, "percent": 50,
        "progress": 50, "amount": 5, "price": 9, "quantity": 2, "size": 3, "max": 10, "min": 0,
        "role": "user", "children": [], "active": True, "disabled": False, "open": False,
    }  # fmt: skip
    evil_item = {
        k: (EVIL if isinstance(v, str) and k not in ("url", "href", "src") else v)
        for k, v in item.items()
    }
    for k in ("url", "href", "src"):
        evil_item[k] = "javascript:alert(1)"
    return {
        "evil": EVIL,
        "evil_url": "javascript:alert(1)",
        "safe_html": mark_safe("<b>safe</b>"),
        "none": None,
        "empty": "",
        "n": 3,
        "lst_dict": [dict(item, id="i1"), dict(item, id="i2", name="Item2", label="Label2")],
        "lst_str": ["alpha", "beta", "gamma"],
        "lst_evil": [dict(evil_item, id="e1"), dict(evil_item, id="e2")],
        "lst_evil_str": [EVIL, EVIL],
        "cp_examples": [{"label": "A", "status": "success"}, {"label": "B", "status": "error"}],
        "cp_values": {},
        # The Rust engine serialises every key of the context on every render and
        # a bound form is by far the most expensive; a template that never names
        # it renders the same without it.
        **({"bound_form": _bound_form()} if with_form else {}),
    }


def render(engine: Any, src: str) -> tuple[str, str]:
    """``("ok", output)`` or ``("err", "<ExceptionType>: <message>")``."""
    reset_ids()
    try:
        return "ok", classify.norm_addr(
            engine.from_string(src).render(make_context("bound_form" in src))
        )
    except Exception as exc:  # noqa: BLE001 - the exception IS the measurement
        return "err", "%s: %s" % (type(exc).__name__, str(exc)[:300])


@contextlib.contextmanager
def planted_difference(label: str, name: str) -> Iterator[None]:
    """Make the PYTHON tag ``name`` render one extra element (gate-off, ``--plant``).

    Wraps the library's compile function so the node it builds appends
    ``PLANT_MARKER``. Only the Django engine ever calls it for a tag with a
    native handler, so the Rust side is untouched — exactly the shape of a real
    regression in the Python tag. Restored on exit.
    """
    from django.template.library import import_library

    library = import_library(LIBRARIES[label])
    original = library.tags[name]

    def planted(parser: Any, token: Any) -> Any:
        node = original(parser, token)
        inner = node.render
        node.render = lambda context: inner(context) + PLANT_MARKER
        return node

    library.tags[name] = planted
    try:
        yield
    finally:
        library.tags[name] = original


@dataclass
class VariantRecord:
    variant: str
    src: str
    cls: str
    python: tuple[str, str]
    rust: tuple[str, str]
    #: what was compared when it is not the whole output (a child tag's own fragment)
    compared: Optional[tuple[str, str]] = None


@dataclass
class Audit:
    rows: dict[str, classify.TagRow] = field(default_factory=dict)
    detail: dict[str, list[VariantRecord]] = field(default_factory=dict)
    #: tags of the two libraries WITHOUT a native handler: nothing to compare
    unmeasured: list[str] = field(default_factory=list)
    #: what this process saw: the ``djust`` it imported, the date, the first uuid
    probe: dict[str, str] = field(default_factory=dict)


def _selected(key: str, only: Optional[set[str]]) -> bool:
    return not only or key in only or key.split(":", 1)[1] in only


def _probe() -> dict[str, str]:
    import datetime

    from djust import __file__ as djust_file  # one import style for djust in this module

    return {
        "djust": str(djust_file),
        "today": datetime.date.today().isoformat(),
        "uuid": str(uuid.uuid4()),
    }


def run_audit(
    only: Optional[set[str]] = None,
    plant: Optional[str] = None,
    self_compare: bool = False,
) -> Audit:
    """Measure every native tag (or ``only``). Must run in a fresh, configured process.

    ``self_compare`` renders Django against Django: every tag MUST come out
    ``IDENTICAL``, which proves the harness itself adds no difference (ids,
    clock, ordering). ``plant`` is ``"<label>:<tag>"`` — see ``planted_difference``.
    """
    from django.template import engines

    python_engine = engines["django"]
    rust_engine = python_engine if self_compare else engines["djust"]
    infos = inventory()
    targets = {k: i for k, i in infos.items() if i.native}
    blockness = {i.name: "block" in i.native for i in targets.values()}
    audit = Audit(unmeasured=[k for k, i in infos.items() if not i.native], probe=_probe())

    def lookup_in(label: str) -> Any:
        return lambda child: infos.get("%s:%s" % (label, child))

    for key, info in targets.items():
        if key in EXCLUDED or not _selected(key, only):
            continue
        results = []
        records = []
        bare_outputs: dict[str, tuple[str, str]] = {}
        for variant in variants.make_variants(info, lookup_in(info.lib), blockness):
            planting = (
                planted_difference(info.lib, info.name)
                if plant in (key, info.name)
                else contextlib.nullcontext()
            )
            with planting:
                py_status, py_out = render(python_engine, variant.src)
            rs_status, rs_out = render(rust_engine, variant.src)
            py_cmp, rs_cmp, fragment = py_out, rs_out, None
            if variant.bare is not None:
                if variant.bare not in bare_outputs:
                    bare_outputs[variant.bare] = (
                        render(python_engine, variant.bare)[1],
                        render(rust_engine, variant.bare)[1],
                    )
                bare_py, bare_rs = bare_outputs[variant.bare]
                if py_status == "ok":
                    py_cmp = classify.own_fragment(bare_py, py_out)
                if rs_status == "ok":
                    rs_cmp = classify.own_fragment(bare_rs, rs_out)
                fragment = (py_cmp, rs_cmp)
            present = bool(py_status == "ok" and py_cmp.strip()) or bool(
                rs_status == "ok" and rs_cmp.strip()
            )
            measured = classify.measure_variant(
                variant.name, variant.invalid_usage, py_status, py_cmp, rs_status, rs_cmp, present
            )
            results.append(measured)
            records.append(
                VariantRecord(
                    variant.name,
                    variant.src,
                    measured.cls,
                    (py_status, py_out),
                    (rs_status, rs_out),
                    fragment,
                )
            )
        passed = variants.passed_count(info)
        audit.rows[key] = classify.classify_tag(results, passed, max(passed, len(info.readable())))
        audit.detail[key] = records
    return audit
