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

_KEY_RE = re.compile(r"""(?:\.get\(\s*|\[\s*)["'](\w+)["']""")
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
def _top_level_sources(path: str) -> dict[str, str]:
    """``{name: source}`` of a module's top-level functions and classes, LAST binding wins."""
    text = Path(path).read_text(encoding="utf-8")
    sources: dict[str, str] = {}
    for node in ast.parse(text).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            sources[node.name] = ast.get_source_segment(text, node) or ""
    return sources


def _source_of(obj: Any) -> str:
    """Source text of a top-level function or class, whatever the interpreter.

    ``inspect.getsource`` finds a CLASS by searching the module's AST on 3.12
    and by ``__firstlineno__`` on 3.13+; the two disagree when a module binds
    the name twice. Python itself resolves the name to the LAST top-level
    binding, so that is what this returns. ``""`` when there is none.
    """
    obj = inspect.unwrap(obj)
    module = sys.modules.get(getattr(obj, "__module__", ""))
    path = getattr(module, "__file__", None)
    if not path:
        return ""
    return _top_level_sources(path).get(obj.__name__, "")


def _keys_of(src: str) -> set[str]:
    return set(_KEY_RE.findall(src))


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
    src = _source_of(fn)
    keys = _keys_of(src)
    for cname in sorted(set(re.findall(r"return\s+(\w+)\(", src))):
        cls = fn.__globals__.get(cname)
        if inspect.isclass(cls):
            keys |= _keys_of(_source_of(cls))
    return [], sorted(keys)


def _rust_handler_keys() -> dict[str, list[str]]:
    from djust.components import rust_handlers as rh

    handlers = {}
    for name, handler in rh.INLINE_HANDLERS:
        handlers[name] = handler
    for name, _end, handler in rh.BLOCK_HANDLERS:
        handlers[name] = handler
    return {name: sorted(_keys_of(_source_of(type(h)))) for name, h in handlers.items()}


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

    form = Form(data={})
    form.is_valid()
    return form


def make_context() -> dict[str, Any]:
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
        "bound_form": _bound_form(),
    }


def render(engine: Any, src: str) -> tuple[str, str]:
    """``("ok", output)`` or ``("err", "<ExceptionType>: <message>")``."""
    reset_ids()
    try:
        return "ok", classify.norm_addr(engine.from_string(src).render(make_context()))
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


@dataclass
class Audit:
    rows: dict[str, classify.TagRow] = field(default_factory=dict)
    detail: dict[str, list[VariantRecord]] = field(default_factory=dict)
    #: tags of the two libraries WITHOUT a native handler: nothing to compare
    unmeasured: list[str] = field(default_factory=list)


def _selected(key: str, only: Optional[set[str]]) -> bool:
    return not only or key in only or key.split(":", 1)[1] in only


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
    audit = Audit(unmeasured=[k for k, i in infos.items() if not i.native])

    def lookup_in(label: str) -> Any:
        return lambda child: infos.get("%s:%s" % (label, child))

    for key, info in targets.items():
        if key in EXCLUDED or not _selected(key, only):
            continue
        results = []
        records = []
        for variant, src, invalid in variants.make_variants(info, lookup_in(info.lib), blockness):
            planting = (
                planted_difference(info.lib, info.name)
                if plant in (key, info.name)
                else contextlib.nullcontext()
            )
            with planting:
                py_status, py_out = render(python_engine, src)
            rs_status, rs_out = render(rust_engine, src)
            measured = classify.measure_variant(
                variant, invalid, py_status, py_out, rs_status, rs_out
            )
            results.append(measured)
            records.append(
                VariantRecord(variant, src, measured.cls, (py_status, py_out), (rs_status, rs_out))
            )
        audit.rows[key] = classify.classify_tag(results)
        audit.detail[key] = records
    return audit
