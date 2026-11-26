"""The operands a native component handler is handed (#3882, #3883; the component plan's S1 / S4).

A native handler (``djust.components.rust_handlers``: the kept twins, the theme-tag handlers,
``{% call %}`` / ``{% slot %}``) writes what it is handed into attributes and text nodes and escapes
it there. Three properties, each through every handler the registry holds:

1. **The operand contract.** Every operand is plain: an exact ``str`` (no ``SafeString``, no lazy
   string), ``int``, ``float``, ``bool``, ``None``, a ``list`` / ``dict`` of operands, or a stand-in
   (``TupleValue``, ``UUID``, ``PurePath``). ``plain_operand`` (``djust.components._operands``) is the
   one conversion; ``_parse_args`` and ``_passed_or_context`` are its two readers. A recorder wraps
   both and checks every value they return, deep, while each handler renders hostile operands.
   ``ENTRIES`` names the ways a value reaches a handler; today only the Rust engine. A Django-tag
   entry or a Python-class entry is one more item there, and the recorder applies unchanged.
2. **No second reading.** A resolved value is the operand: it is not looked up in the context again
   (#3882: a value naming another variable printed THAT variable) and not re-read as source text
   (#3883: ``"false"`` became ``False``, ``"007"`` became ``7``, ``'"x"'`` lost its quotes). The
   oracle needs no Django: ``{% tag p=v %}`` with ``v = S`` must render exactly as ``{% tag p="S" %}``.
3. **The readers are pinned by name**: the functions that read the context or decode text, as a
   set, so a new reader cannot appear unnoticed.
"""

from __future__ import annotations

import ast
import datetime
import decimal
import enum
import pathlib
import re
import uuid
from collections import namedtuple
from pathlib import Path
from typing import Any, Callable

import pytest
from django.utils.functional import lazy
from django.utils.safestring import SafeString, mark_safe

from djust.components import _operands, rust_handlers
from djust.components.function_component import _COMPONENT_REGISTRY

ROOT = Path(__file__).resolve().parents[2]

#: What a hostile or exotic operand looks like. Marked-safe text breaks out of an attribute if it
#: reaches one unconverted (``conditional_escape`` leaves ``SafeData`` alone).
BREAKOUT = '" data-breakout="1'
PLANTED = "PLANTEDVALUE"
P = namedtuple("P", "x y")


class Level(enum.IntEnum):
    HIGH = 3


class _SafeSub(SafeString):
    pass


PAYLOADS: dict[str, Callable[[], Any]] = {
    "marked-safe": lambda: mark_safe(BREAKOUT),
    "safestring-subclass": lambda: _SafeSub(BREAKOUT),
    "lazy": lambda: lazy(lambda: BREAKOUT, str)(),
    "lazy-marked-safe": lambda: lazy(lambda: mark_safe(BREAKOUT), str)(),
    "tuple-of-marked-safe": lambda: (mark_safe(BREAKOUT), 1),
    "namedtuple-of-marked-safe": lambda: P(mark_safe(BREAKOUT), 2),
    "list-of-marked-safe": lambda: [mark_safe(BREAKOUT), {"label": mark_safe(BREAKOUT)}],
    "dict-of-marked-safe": lambda: {"label": mark_safe(BREAKOUT), "value": 1},
    "decimal": lambda: decimal.Decimal("1.50"),
    "int-enum": lambda: Level.HIGH,
    "uuid": lambda: uuid.UUID(int=7),
    "path": lambda: pathlib.PurePosixPath("/a/b"),
    "date": lambda: datetime.date(2024, 3, 15),
    "str-false": lambda: "false",
    "str-name": lambda: "victim",
}


# --------------------------------------------------------------------------- #
# The population: every native handler the registry holds


def _targets() -> list[tuple[str, str, str]]:
    """``(library, tag, source)`` per parameter of every native handler, each parameter set to
    ``probe``, the others to a typical value, a child tag inside its parent. Derived from the
    registries (``NATIVE_INLINE_HANDLERS``, ``NATIVE_BLOCK_HANDLERS``, the theme-tag names) and the
    parity harness's parameter inventory, not typed in."""
    from djust.theming import rust_handlers as theme_handlers
    from scripts.lib.template_library_parity import harness, variants

    names = {name for name, _h in rust_handlers.NATIVE_INLINE_HANDLERS}
    names |= {name for name, _e, _h in rust_handlers.NATIVE_BLOCK_HANDLERS}
    theme = set(theme_handlers._THEME_TAG_NAMES)
    out = []
    infos = harness.inventory()
    found = set()
    for _key, info in sorted(infos.items()):
        wanted = names if info.lib == "djust_components" else theme
        if info.name not in wanted:
            continue
        found.add(info.name)
        params = variants.passed_names(info)
        required = [s[0] for s in info.sig if s[1] == "req" and s[0] != "context"]
        switches = [n for n in params if variants.kind_of(n) == "bool"]
        fixed = list(dict.fromkeys(switches + [r for r in required if r in params]))
        parent = variants.PARENT.get(info.name)
        for name in params:
            call = ["%s=%s" % (m, variants.token(m, "typical")) for m in fixed if m != name]
            call.append("%s=probe" % name)
            src = variants.wrap(info.lib, info.name, " ".join(call), info.is_block, parent, "")
            out.append((info.name, name, src))
    assert found == names | theme, (names | theme) - found
    return out


TARGETS = _targets()
TAGS = sorted({tag for tag, _p, _s in TARGETS})


@pytest.fixture(scope="module")
def rust_engine():
    from djust.template.backend import DjustTemplateBackend
    from djust.theming import rust_handlers as theme_handlers

    rust_handlers.register_with_rust_engine()
    theme_handlers.register_with_rust_engine()
    return DjustTemplateBackend(
        {"NAME": "operands-3883", "DIRS": [], "APP_DIRS": True, "OPTIONS": {}}
    )


def _render(engine: Any, src: str, context: dict[str, Any]) -> str:
    try:
        return str(engine.from_string(src).render(dict(context)))
    except Exception as exc:  # noqa: BLE001 - a refusal is an observation, not a pass
        return "<error %s: %s>" % (type(exc).__name__, exc)


#: The ways a value reaches a native handler. Each takes ``(engine, source, context)``. A
#: Django-tag entry and a Python-class entry are added here when they exist (component plan S3).
ENTRIES: dict[str, Callable[[Any, str, dict[str, Any]], str]] = {"rust": _render}


# --------------------------------------------------------------------------- #
# 1. The operand contract


def violations(value: Any, path: str = "$") -> list[str]:
    """Where ``value`` breaks the operand contract (the module docstring), as paths."""
    if isinstance(value, _operands.TupleValue):
        return [v for i, item in enumerate(value) for v in violations(item, "%s[%d]" % (path, i))]
    if type(value) in (str, int, float, bool, type(None)):
        return []
    if isinstance(value, _operands.STANDIN_TYPES):
        return []
    if type(value) is list:
        return [v for i, item in enumerate(value) for v in violations(item, "%s[%d]" % (path, i))]
    if type(value) is dict:
        found = []
        for key, item in value.items():
            if type(key) not in (str, int):
                found.append("%s key %r is a %s" % (path, key, type(key).__name__))
            found += violations(item, "%s[%r]" % (path, key))
        return found
    return ["%s is a %s" % (path, type(value).__name__)]


@pytest.fixture
def recorded(monkeypatch):
    """Every value the two readers hand a handler, while the test renders."""
    seen: list[tuple[str, Any]] = []
    parse, passed = rust_handlers._parse_args, rust_handlers._passed_or_context

    def parse_spy(args, context):
        result = parse(args, context)
        seen.append(("_parse_args", result))
        return result

    def passed_spy(*args, **kwargs):
        result = passed(*args, **kwargs)
        seen.append(("_passed_or_context", result))
        return result

    monkeypatch.setattr(rust_handlers, "_parse_args", parse_spy)
    monkeypatch.setattr(rust_handlers, "_passed_or_context", passed_spy)
    return seen


def _drive(engine: Any, entry: str, tag: str) -> None:
    for name, _param, src in TARGETS:
        if name != tag:
            continue
        for make in PAYLOADS.values():
            ENTRIES[entry](engine, src, {"probe": make(), "victim": PLANTED})


@pytest.mark.parametrize("entry", sorted(ENTRIES))
@pytest.mark.parametrize("tag", TAGS)
def test_every_operand_a_native_handler_reads_is_plain(rust_engine, recorded, entry, tag):
    _drive(rust_engine, entry, tag)
    assert recorded, "%s rendered without reading an operand through the two readers" % tag
    bad = [(reader, v) for reader, value in recorded for v in violations(value)]
    assert bad == [], bad


def test_the_context_fallback_is_converted_too(rust_engine, recorded):
    """``data_table`` reads ``rows`` from the context when it is not passed. A marked-safe row id
    reached the row's attributes raw on ``main`` (the component plan's inferred gap)."""
    src = '{% data_table columns=cols row_click_event="go" %}'
    ctx = {
        "rows": [{"id": mark_safe(BREAKOUT), "name": lazy(lambda: "n", str)()}],
        "cols": [{"key": "name", "label": "N"}],
    }
    html = _render(rust_engine, src, ctx)
    assert 'data-breakout="1"' not in html, html
    assert "&quot; data-breakout=&quot;1" in html, html
    assert any(reader == "_passed_or_context" for reader, _v in recorded)
    assert [v for _r, value in recorded for v in violations(value)] == []


def test_call_and_slot_operands_are_plain(rust_engine, recorded):
    from djust.components.function_component import component

    @component
    def operand_probe_3883(assigns):
        return "<i>%s|%s</i>" % (type(assigns["label"]).__name__, assigns["label"])

    try:
        src = (
            '{% call "operand_probe_3883" label=probe %}'
            "{% slot head label=probe %}h{% endslot %}{% endcall %}"
        )
        for make in PAYLOADS.values():
            _render(rust_engine, src, {"probe": make(), "victim": PLANTED})
    finally:
        _COMPONENT_REGISTRY.pop("operand_probe_3883", None)
    assert len(recorded) >= len(PAYLOADS)
    assert [v for _r, value in recorded for v in violations(value)] == []


def test_gate_off_an_unconverted_operand_is_caught(rust_engine, recorded, monkeypatch):
    """With the conversion bypassed, the recorder must report the marked-safe operand: the
    contract test is not vacuous."""
    monkeypatch.setattr(rust_handlers, "plain_operand", lambda value: value)
    _render(rust_engine, "{% badge label=probe %}", {"probe": mark_safe(BREAKOUT)})
    _render(
        rust_engine,
        "{% data_table columns=cols %}",
        {"rows": [{"id": mark_safe(BREAKOUT)}], "cols": [{"key": "id", "label": "I"}]},
    )
    found = [v for _r, value in recorded for v in violations(value)]
    assert any("SafeString" in v for v in found), found


@pytest.mark.parametrize(
    "value, expected",
    [
        (mark_safe("<b>"), "<b>"),
        (_SafeSub("x"), "x"),
        (lazy(lambda: "l", str)(), "l"),
        (lazy(lambda: mark_safe("m"), str)(), "m"),
        (decimal.Decimal("1.5"), 1.5),
        (Level.HIGH, 3),
        (True, True),
        (None, None),
    ],
)
def test_plain_operand_gives_exact_types(value, expected):
    got = _operands.plain_operand(value)
    assert got == expected and type(got) is type(expected)


def test_plain_operand_converts_inside_containers_and_tuples():
    got = _operands.plain_operand(
        {"rows": [mark_safe("a"), (mark_safe("b"), P(mark_safe("c"), 1))], mark_safe("k"): 1}
    )
    assert violations(got) == []
    assert str(got["rows"][1]) == "('b', P(x='c', y=1))"
    deep: Any = mark_safe("x")
    for _ in range(3000):
        deep = [deep]
    got = _operands.plain_operand(deep)  # iterative: no RecursionError
    for _ in range(3000):
        got = got[0]
    assert type(got) is str


def test_plain_operand_refuses_an_object_by_name():
    with pytest.raises(_operands.OperandRefused, match="object"):
        _operands.plain_operand(object())


def test_plain_operands_leaves_the_markup_operands_alone():
    body = mark_safe("<b>body</b>")
    got = _operands.plain_operands({"content": body, "label": mark_safe("x")}, markup=("content",))
    assert got["content"] is body and type(got["label"]) is str


# --------------------------------------------------------------------------- #
# 2. No second reading: a resolved value renders exactly as the same literal


#: Strings that read like a literal or a name. Each must reach the handler as itself.
STRINGS = [
    "false", "False", "True", "None", "null", "007", "1e3", "1_000", "-2", "inf",
    "victim", "victim_list", "obj.k", "[1, 2]", "[victim", "x y", "", " 5 ",
]  # fmt: skip


@pytest.mark.parametrize("tag", TAGS)
def test_a_resolved_string_renders_as_the_same_literal(rust_engine, tag):
    ctx = {"victim": PLANTED, "victim_list": [PLANTED], "obj": {"k": PLANTED}}
    failures = []
    for name, param, src in TARGETS:
        if name != tag:
            continue
        for text in STRINGS:
            resolved = _render(rust_engine, src, {**ctx, "probe": text})
            literal = _render(
                rust_engine, src.replace("%s=probe" % param, '%s="%s"' % (param, text)), ctx
            )
            if resolved != literal or PLANTED in resolved:
                failures.append((param, text))
    assert failures == [], failures


def test_a_quoted_string_keeps_its_quotes(rust_engine):
    """``'"x"'`` (a string with quotes in it) is not a quoted literal (#3883)."""
    for value in ('"x"', "'x'"):
        html = _render(rust_engine, "{% badge label=v %}", {"v": value})
        assert re.search(r"(&quot;|&#x27;)x(&quot;|&#x27;)", html), html


def test_a_theme_tag_operand_is_not_looked_up_again(rust_engine):
    src = "{% theme_mode_toggle button_class=v %}"
    html = _render(rust_engine, src, {"v": "victim", "victim": PLANTED})
    assert PLANTED not in html and "victim" in html, html


def test_a_component_name_and_prop_are_not_looked_up_again(rust_engine):
    from djust.components.function_component import component

    @component
    def name_probe_3882(assigns):
        return "<i>%s</i>" % assigns["label"]

    try:
        ctx = {
            "which": "name_probe_3882",
            "name_probe_3882": PLANTED,
            "v": "victim",
            "victim": PLANTED,
        }
        html = _render(rust_engine, "{% call which label=v %}{% endcall %}", ctx)
    finally:
        _COMPONENT_REGISTRY.pop("name_probe_3882", None)
    assert html == "<i>victim</i>", html


# --------------------------------------------------------------------------- #
# 3. The readers, pinned by name

#: ``(module, function)`` that read a native handler's operands from the context or decode them
#: from text. A new one is a new way for a value to be read twice: route it through
#: ``_parse_args`` / ``_passed_or_context`` instead, or add it here with the reason.
READERS = {
    "python/djust/components/rust_handlers.py": {
        "context": {
            "_typed_args",  # the typed channel, by its reserved key
            "_passed_or_context",  # the context fallback, through plain_operand
        },
        "decode": {
            "_decoded_container",  # the JSON the engine encodes a list or dict as
            # ``FilterBarHandler.render`` / its ``take``: the controls its children encoded into
            # the body, from operands ``_parse_args`` already read (``_FilterControlHandler``)
            "render",
            "take",
            # ``NavMenuHandler``: the hex-encoded ``[id, href]`` marker each ``{% nav_item %}``
            # wrote into the menu's body, never an operand
            "_nav_key_matches",
            # ``AccordionHandler`` (#4331): the hex-encoded ``[id, title, open, event, length]``
            # marker each ``{% accordion_item %}`` wrote into the accordion's body, never an operand
            "_accordion_item_payload",
            # ``AccordionItemHandler.render_kwargs`` (#4331): decodes its own ``json.dumps`` of
            # operands ``_parse_args`` / ``_python_operand`` already read (a round trip, so the
            # standalone item and the accordion's redraw read the same payload)
            "render_kwargs",
            # ``render_data_table`` (#3869): the DJANGO engine's ``{% data_table %}`` arguments,
            # already resolved once by Django's ``simple_tag``, made the shape the Rust engine
            # hands ``DataTableHandler``. Its ``json.loads`` decodes its own ``json.dumps`` of the
            # normalized value (a round trip, never text an engine or a user wrote), and nothing
            # is looked up in a context, so no operand is read twice.
            "_python_operand",
        },
    },
    # The ``TagHandler`` family (``djust.template_tags``): ``_resolve_arg`` is their reader, and it
    # does not look an operand up in the context (#3882: ``{% djust_markdown body %}`` with
    # ``body = "user.email"`` printed the variable it spelled).
    "python/djust/template_tags/__init__.py": {"context": set(), "decode": set()},
    "python/djust/template_tags/markdown.py": {"context": set(), "decode": set()},
    "python/djust/template_tags/flash.py": {"context": set(), "decode": set()},
    "python/djust/template_tags/pwa.py": {"context": set(), "decode": set()},
    "python/djust/template_tags/live_render.py": {
        "context": {"_split_args"},  # the typed channel, by its reserved key
        "decode": set(),
    },
    "python/djust/components/suspense.py": {
        # ``await="a,b"`` names the async results the boundary waits on: the names are the
        # template's words, looked up on purpose (an ``AsyncResult`` gates the body; no value
        # of it reaches the page)
        "context": {"_render_state"},
        "decode": set(),
    },
    "python/djust/theming/rust_handlers.py": {
        "context": {"render"},  # ``request``, by its fixed name
        "decode": set(),
    },
    "python/djust/components/function_component.py": {
        "context": set(),
        "decode": {
            # the slot sentinels ``{% slot %}`` writes into a ``{% call %}`` body
            "_extract_slots",
            "_consume",
            # ``{% render_slot %}``: a structure a Python caller encoded (the engine hands it the
            # template's literal token, ``RESOLVE_ARG_POSITIONS``)
            "render",
        },
    },
}
#: The callers of ``plain_operand`` (the conversion) outside its own module. The two readers of
#: the Rust engine's operands, and ``_python_operand`` (#3869), which hands a Django-resolved
#: scalar of ``{% data_table %}`` the same conversion so both engines draw it alike (see READERS).
CONVERTERS = {"_parse_args", "_passed_or_context", "_python_operand"}


def _functions_reading(path: Path) -> dict[str, set[str]]:
    """``{"context": {...}, "decode": {...}, "plain_operand": {...}}``: the functions of ``path``
    that read a parameter named ``context`` (``.get`` or subscript), call ``json.loads``, or call
    ``plain_operand``."""
    tree = ast.parse(path.read_text())
    found: dict[str, set[str]] = {"context": set(), "decode": set(), "plain_operand": set()}
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(func):
            target = None
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                target = node.func.value
                if node.func.attr == "loads":
                    found["decode"].add(func.name)
                elif node.func.attr == "get" and isinstance(target, ast.Name):
                    if target.id == "context":
                        found["context"].add(func.name)
            elif isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
                if node.value.id == "context":
                    found["context"].add(func.name)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == "plain_operand":
                    found["plain_operand"].add(func.name)
    return found


@pytest.mark.parametrize("module", sorted(READERS))
def test_the_functions_that_read_an_operand_are_exactly_the_known_ones(module):
    found = _functions_reading(ROOT / module)
    assert found["context"] == READERS[module]["context"], found
    assert found["decode"] == READERS[module]["decode"], found


def test_the_conversion_is_called_by_exactly_the_two_readers():
    found = _functions_reading(ROOT / "python/djust/components/rust_handlers.py")
    assert found["plain_operand"] == CONVERTERS
