"""A marked-safe value is not a URL and not an attribute value (#3898, #3894).

``mark_safe`` on a value (also a lazy one, or any ``str`` with ``__html__``) means "no HTML
escaping needed" in a TEXT slot. It never meant "navigable", and it never meant "write it into a
quoted attribute". So, in ``djust.components``:

* a URL is checked against the scheme allowlist and escaped whatever it is marked as (data, a
  constructor argument, a tag's own keyword, the native handlers), except the literal no-op hrefs
  ``javascript:void(0)`` / ``javascript:;``;
* an ITEM value (an entry of a list of items, an item dict) that lands in an attribute is escaped
  whatever it is marked as;
* a text slot keeps its markup; a scalar argument in a non-URL attribute is the developer's own.

What each class here proves, and what it does NOT:

* ``TestTheSweep`` / ``TestEveryClass``: the classes whose module names ``url_attr`` (all four
  claims, six payloads), and EVERY class the URL sweep can drive (200+; two claims, two payloads,
  item shapes only for attributes). The classes, parameters and per-class item keys are DERIVED
  from the code (``url_sweep.component_classes``, ``component_params``, ``harness.discover``), under
  all three CSS frameworks, with and without the companions that make conditional sections draw.
* ``TestTags``: the 18 tags that print a URL out of an item, on both engines.
* ``TestEveryTag``: EVERY tag of every library, every parameter, item keys read from each tag's own
  source. It does not claim they are clean: it pins the exact list that still leaks
  (``KNOWN_TAG_ITEM_LEAKS``, #3904), so the claim equals the coverage and the list can only shrink.
* ``TestCallerSet`` fails on any ``url_attr`` / ``srcset_attr`` call (the native handlers
  included) that leaves ``honor_safe`` at its default.
"""

from __future__ import annotations

import ast
import contextlib
import importlib
import inspect
import os
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.utils.functional import lazy
from django.utils.safestring import SafeString, mark_safe
from djust.components.base import LiveComponent
from scripts.lib.template_library_parity import harness, url_sweep

pytestmark = pytest.mark.components

ROOT = Path(__file__).resolve().parents[3]


class _SafeSub(SafeString):
    """``isinstance(x, SafeData)`` is true for a subclass too."""


class _HtmlStr(str):
    """Any ``str`` with an ``__html__`` method (what ``markupsafe.Markup`` is, without the
    dependency): Django's ``conditional_escape`` writes it as it is, though it is not ``SafeData``."""

    def __html__(self) -> str:
        return str(self)


def _lazy_safe(text: str) -> object:
    return lazy(lambda: mark_safe(text), str)()


CLAIMS = {
    "mark_safe": mark_safe,
    "safestring-subclass": _SafeSub,
    "lazy-mark_safe": _lazy_safe,
    "html-object": _HtmlStr,
}

#: ``(label, payload)``: three spellings of a script scheme, a data document, and two ways out of
#: an attribute. A scheme hole is found by the URL sweep's scanner; a breakout creates ``on...``.
PAYLOADS = (
    ("js", "javascript:alert(%s)" % url_sweep.MARK),
    ("js-mixed", "JaVaScRiPt:alert(%s)" % url_sweep.MARK),
    ("js-embedded-tab", "java\tscript:alert(%s)" % url_sweep.MARK),
    ("data-html", "data:text/html,<script>alert(%s)</script>" % url_sweep.MARK),
    ("breakout-dq", '/x" onmouseover="alert(%s)" y="' % url_sweep.MARK),
    ("breakout-sq", "/x' onmouseover='alert(%s)' y='" % url_sweep.MARK),
)


class _Handlers(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found: list[tuple[str, str]] = []

    def handle_starttag(self, tag, attrs):
        # the PAYLOAD's handler: a component's own inline ``onclick`` (the navbar toggler) is not
        # a hole this sweep made
        self.found += [(tag, n) for n, _v in attrs if n == "onmouseover"]

    handle_startendtag = handle_starttag


def holes_in(markup: str) -> list:
    """Script-capable URLs in an attribute, and event-handler attributes."""
    parser = _Handlers()
    parser.feed(markup)
    parser.close()
    return url_sweep.findings(markup) + parser.found


def url_classes() -> dict[str, type]:
    """Every driven class whose module calls ``url_attr`` (derived, not listed)."""
    out = {}
    for key, cls in url_sweep.component_classes().items():
        module = importlib.import_module(cls.__module__)
        # the NAME, not a call: a module that imports the helper and stops calling it, or calls
        # it under another name, is still swept
        if "url_attr" in inspect.getsource(module):
            out[key] = cls
    return out


#: ``MARKED_SAFE_SWEEP_ONLY=breadcrumb,carousel`` narrows the sweep to the classes whose key
#: contains one of the words and the tags of that name: the gate-off harness reverts ONE call site
#: and runs only the sweep that site belongs to. Unset (always, in CI) it is the whole sweep.
ONLY = {w for w in os.environ.get("MARKED_SAFE_SWEEP_ONLY", "").split(",") if w}

CLASSES = {
    key: cls for key, cls in url_classes().items() if not ONLY or any(word in key for word in ONLY)
}
#: EVERY class the URL sweep can drive (derived: every ``Component`` / ``LiveComponent`` in the
#: component packages), not only the ones whose module names ``url_attr``: an attribute breakout
#: needs no URL helper, so the claim "an item value never leaves an attribute" is made for all.
ALL_CLASSES = {
    key: cls
    for key, cls in url_sweep.component_classes().items()
    if not ONLY or any(word in key for word in ONLY)
}
KEYS = set(url_sweep.COMMON_KEYS)
#: Attribute-ish item keys no URL sweep names (a lightbox image's ``alt``, a card's ``caption``).
ATTR_KEYS = frozenset(
    {"alt", "caption", "description", "subtitle", "tooltip", "class", "color", "status", "target",
     "rel", "placeholder", "aria_label", "name", "email", "key", "slug", "category", "user",
     "action", "time", "badge", "content", "message", "type", "variant", "size", "role", "value",
     "label", "title", "id", "text", "icon", "content_id", "panel", "header", "body"}
)  # fmt: skip


def class_keys(cls: type) -> set[str]:
    """The item keys ``cls`` reads, DERIVED from its source (``.get("x")`` / ``["x"]``, followed
    through its helpers by the harness), plus the common URL keys and :data:`ATTR_KEYS`."""
    try:
        found, _params = harness.discover(cls)
    except Exception:  # noqa: BLE001 - a class the harness cannot read keeps the common keys
        found = set()
    return set(KEYS) | set(ATTR_KEYS) | {k for k in found if k.isidentifier()}


#: The item keys the tags read besides the URL-shaped ones (a card's category, a feed's user, a
#: node's parent ...): derived from the tags' own keys would need the harness; these are the ones
#: the data tags' sources name, and ``test_each_tag_is_reached_by_the_driver`` shows the driver
#: gets as far as an attribute.
TAG_KEYS = (
    KEYS
    | ATTR_KEYS
    | {"category", "user", "action", "target", "time", "description", "badge", "event", "active"}
    | {"nid"}
)


#: Item attributes a component reads off an OBJECT item (``NavItem``): the dict keys plus these.
OBJ_KEYS = KEYS | {"target", "badge", "badge_variant", "active", "external", "icon", "label"}
#: A list of FIVE items, not one: a link is drawn for every item but the last, and a breadcrumb
#: collapses its middle items into a dropdown past its ``max_items``.
N_ITEMS = 5


#: Item keys whose value is not text but a CONTAINER: a list-group item's ``badge`` is a mapping
#: (``badge.get("text")``), a navbar item's ``dropdown`` is a list of items. Filling them with the
#: claim string crashes the component (``'str' has no attribute 'get'``), and a crash is a cell
#: that proved nothing: the first version of this sweep skipped every cell of ``ui.ListGroup`` and
#: ``ui.NavBar`` that way and two leaks sat behind it. So the builder is typed, and
#: ``TestEveryClassIsReached`` fails any (class, parameter) pair that no cell got through.
CONTAINER_KEYS = ("badge", "dropdown")


def _flat(value: object, keys: set[str]) -> dict:
    """An item whose every text key is ``value`` and that has no container key."""
    return url_sweep.dict_of(value, set(keys) - set(CONTAINER_KEYS))  # type: ignore[arg-type]


def _item(value: object, keys: set[str] = KEYS, dropdown: bool = True) -> dict:
    """An item dict: every text key is ``value``, ``badge`` is a mapping of the same, and (when
    ``dropdown``) the navbar's ``dropdown`` holds two entries of the same kind. An item WITHOUT a
    ``dropdown`` key is what a navbar draws as a plain link."""
    item = _flat(value, keys)
    item["badge"] = _flat(value, keys)
    if dropdown:
        item["dropdown"] = [_flat(value, keys) for _ in range(2)]
    return item


def _obj(value: object, keys: set[str] = KEYS) -> SimpleNamespace:
    """An object item (``NavItem``): every attribute is ``value``, except the badge, which the
    component compares with ``> 0``."""
    return SimpleNamespace(**{**dict.fromkeys(OBJ_KEYS | keys, value), "badge": 1})


#: Item keys a component cannot take the claim for: it does ``int(item["level"])`` or
#: ``item["badge"].get(...)``. ``DROP`` leaves the key out, ``NUMBER`` gives it a number. The
#: search for them is :func:`_overrides_for`; what it finds is pinned by
#: ``TestEveryClassIsReached`` (a key the sweep cannot carry is stated, never skipped).
DROP = "<drop>"
NUMBER = 1


def _override(node: object, overrides: dict[str, object]) -> object:
    """Apply ``overrides`` to every item under ``node`` (items, objects, nested containers)."""
    if isinstance(node, dict):
        for k in list(node):
            if k in overrides:
                if overrides[k] is DROP:
                    del node[k]
                else:
                    node[k] = overrides[k]
            else:
                _override(node[k], overrides)
    elif isinstance(node, list):
        for entry in node:
            _override(entry, overrides)
    elif isinstance(node, SimpleNamespace):
        for k in list(vars(node)):
            if k in overrides:
                if overrides[k] is DROP:
                    delattr(node, k)
                else:
                    setattr(node, k, overrides[k])
    return node


SHAPES = ("scalar", "list_str", "dict", "list_dict", "list_dict_flat", "list_obj", "form_like")


def _build(
    shape: str, value: object, keys: set[str] = KEYS, overrides: dict[str, object] | None = None
) -> object:
    """One shape of ``value``: a scalar, a list of text, one item, a list of items (alternating
    with and without a ``dropdown``: a navbar draws the two differently), a list of items whose
    every key holds the claim itself (a component that prints ``badge`` as text), a list of
    object items. ``overrides`` are the keys the component cannot take the claim for."""
    if shape == "scalar":
        return value
    built: object = {
        "list_str": lambda: [value] * N_ITEMS,
        # one item: ``badge`` a mapping, no ``dropdown`` (a navbar draws a plain link)
        "dict": lambda: _item(value, keys, dropdown=False),
        "list_dict": lambda: [_item(value, keys, dropdown=i % 2 == 0) for i in range(N_ITEMS)],
        "list_dict_flat": lambda: [
            url_sweep.dict_of(value, set(keys) - {"dropdown"})  # type: ignore[arg-type]
            for _ in range(N_ITEMS)
        ],
        "list_obj": lambda: [_obj(value, keys) for _ in range(N_ITEMS)],
        # what ``FieldError`` / ``FormErrors`` read off a Django form: ``errors`` and
        # ``non_field_errors()``
        "form_like": lambda: SimpleNamespace(errors=[value], non_field_errors=lambda: [value]),
    }[shape]()
    return _override(built, overrides) if overrides else built


#: Parameters that make a conditional section draw (a split button, a brand, an image with its
#: alt text): the second pass sets them so the attributes inside those sections are reached.
URL_COMPANIONS = {
    "src", "image", "logo", "brand_logo", "thumbnail", "avatar", "brand_href", "url", "href",
}  # fmt: skip
TEXT_COMPANIONS = {"brand", "brand_name", "alt", "label", "title", "name", "text"}
#: A ``LiveComponent`` has no signature to read a bool default from: these draw nothing until set.
BOOL_COMPANIONS = {"show", "open", "visible", "is_open"}


def _bool_default(cls: type, name: str) -> bool:
    """A constructor parameter whose default is a bool (``split=False``): the harness's name
    heuristic does not know every flag by name."""
    try:
        param = inspect.signature(cls.__init__).parameters.get(name)  # type: ignore[misc]
    except (TypeError, ValueError):
        return False
    return param is not None and isinstance(param.default, bool)


def _companions(cls: type, names: list[str], skip: str) -> dict[str, object]:
    from scripts.lib.template_library_parity import variants

    out: dict[str, object] = {}
    for n in names:
        if n == skip:
            continue
        if variants.kind_of(n) == "bool" or n in BOOL_COMPANIONS or _bool_default(cls, n):
            out[n] = True
        elif n in URL_COMPANIONS:
            out[n] = "/ok.png"
        elif n in TEXT_COMPANIONS:
            out[n] = "x"
    return out


#: What a required parameter may need besides the URL sweep's filler (``value: str`` wants text,
#: ``buttons: List[Dict]`` wants entries): tried in order until the class draws with its defaults.
REQUIRED_ALTERNATIVES = (
    "x",
    ["x"],
    [{"label": "x", "text": "x", "title": "x", "url": "/ok"}],
    {"label": "x"},
    1,
)
_REQUIRED: dict[type, dict[str, object]] = {}


def _draws(cls: type, kwargs: dict) -> bool:
    try:
        with _framework("bootstrap5"):
            obj = cls(**kwargs)  # type: ignore[call-arg]
            for attr in ("_rust_instance", "template"):
                if hasattr(obj, attr):
                    setattr(obj, attr, None)
            obj.render()
        return True
    except Exception:  # noqa: BLE001 - the answer is "no"
        return False


def class_params(cls: type) -> tuple[list[str], dict[str, object]]:
    """``(parameters, required)`` of ``cls``: the URL sweep's, with the filler of each required
    parameter repaired when the class does not draw with it. ``StatCard(value=1)`` crashed on
    ``.replace`` in every cell, so no parameter of ``StatCard`` was ever swept."""
    names, required = url_sweep.component_params(cls)
    if cls not in _REQUIRED:
        fixed = dict(required)
        if fixed and not _draws(cls, fixed):
            for name in list(fixed):
                for alternative in (fixed[name], *REQUIRED_ALTERNATIVES):
                    if _draws(cls, {**fixed, name: alternative}):
                        fixed[name] = alternative
                        break
                else:
                    continue
                break
        _REQUIRED[cls] = fixed
    # ``component_id`` is a LiveComponent's own constructor argument (it lands in ``id=``)
    if issubclass(cls, LiveComponent) and "component_id" not in names:
        names = [*names, "component_id"]
    return names, _REQUIRED[cls]


@contextlib.contextmanager
def _framework(framework: str):
    """The CSS framework a class reads from ``djust.config`` (``ui/*`` and ``layout/*`` choose
    their renderer there, not from a constructor argument): patch ONLY ``css_framework``."""
    from djust import config as config_module

    real_get = config_module.config.get

    def get(key, default=None):
        return framework if key == "css_framework" else real_get(key, default)

    with patch.object(config_module.config, "get", get):
        yield


def _hybrid_template_path(obj: object) -> bool:
    """Whether ``Component.render`` will draw ``obj`` through its ``template`` (``{{ x }}``):
    no Rust instance took the value (the crate constructor refuses a lazy string) and the class
    has a template. That path does no URL check at all, for ANY value; it is not the
    ``url_attr`` / ``conditional_escape`` rule and is tracked separately (#3900)."""
    return (
        getattr(obj, "_rust_instance", None) is None and getattr(obj, "template", None) is not None
    )


#: Set aside, not a crash: the cell drew through the hybrid template path (see the sweep).
HYBRID = object()
#: A benign marked-safe value: what a cell is decided with, and what the reach test looks for.
REACH_MARK = "ZQREACH"
PROBE = mark_safe(REACH_MARK)

#: ``cell -> overrides`` (``None``: the component rejects the shape whatever the keys). A cell is
#: ``(class, parameter, shape, framework, companions, force_python)``.
_OVERRIDES: dict[tuple, dict[str, object] | None] = {}


def _search_overrides(attempt, keys: set[str]) -> dict[str, object] | None:
    """The item keys a component cannot take the claim for, found PER KEY, never per class: with
    only the common URL keys the shape must draw (else the component rejects the shape, and the
    cell is not one); then each further key is put back as the claim, else as a number, else left
    out. Whatever is left out is recorded in ``_OVERRIDES`` and pinned by the reach test."""

    def ok(overrides: dict[str, object]) -> bool:
        try:
            attempt(overrides)
        except Exception:  # noqa: BLE001 - the answer is "no"
            return False
        return True

    extras = sorted(set(keys) - set(KEYS))
    found: dict[str, object] = {k: DROP for k in extras}
    if not ok(found):
        return None
    for k in extras:
        for form in (None, NUMBER):
            trial = {name: value for name, value in found.items() if name != k}
            if form is not None:
                trial[k] = form
            if ok(trial):
                found = trial
                break
    return found


def drive(
    cls: type,
    key: str,
    names: list[str],
    required: dict[str, object],
    keys: set[str],
    name: str,
    shape: str,
    framework: str,
    companions: bool,
    value: object,
    force_python: bool,
) -> object:
    """Render ``cls`` with ``name`` set to ``shape`` of ``value``: the output, :data:`HYBRID`, or
    ``None`` (the component rejects this shape of this parameter, with every key it could take
    left out). A crash is not the end of a cell: the keys the component cannot take the claim
    for are searched for one by one (:func:`_search_overrides`)."""

    def attempt(claimed: object, overrides: dict[str, object]) -> object:
        kwargs = {
            **required,
            **(_companions(cls, names, name) if companions else {}),
            name: _build(shape, claimed, keys, overrides),
        }
        if "css_framework" in names and name != "css_framework":
            kwargs["css_framework"] = framework
        harness.reset_ids()
        with _framework(framework):
            obj = cls(**kwargs)  # type: ignore[call-arg]
            if force_python:
                for attr in ("_rust_instance", "template"):
                    if hasattr(obj, attr):
                        setattr(obj, attr, None)
            elif _hybrid_template_path(obj):
                return HYBRID
            return str(obj.render())

    cell = (key, name, shape, framework, companions, force_python)
    if cell not in _OVERRIDES:
        # decided ONCE per cell, with the benign probe, whatever claim and payload come first: a
        # payload a validator rejects must not be what leaves a key out of every later cell
        def with_probe(overrides: dict[str, object]) -> object:
            return attempt(PROBE, overrides)

        try:
            with_probe({})
        except Exception:  # noqa: BLE001 - searched for below, recorded, never silent
            _OVERRIDES[cell] = _search_overrides(with_probe, keys) if shape != "scalar" else None
        else:
            _OVERRIDES[cell] = {}
    overrides = _OVERRIDES[cell]
    if overrides is None:
        return None
    try:
        return attempt(value, overrides)
    except Exception:  # noqa: BLE001 - a value this payload makes the component reject says nothing
        return None


def sweep(
    only_claims: tuple[str, ...] = tuple(CLAIMS),
    force_python: bool = False,
    companions: bool = False,
    classes: dict[str, type] | None = None,
    payloads: tuple[tuple[str, str], ...] = PAYLOADS,
    wide: bool = False,
) -> tuple[list[tuple], int, set[tuple[str, str]]]:
    """``([(class, param, shape, claim, payload label, holes)], renders, hybrid-template cells)``.

    ``force_python`` renders through ``_render_custom`` (the f-string path) for every component,
    the way ``test_ui_simple_components_escaping`` does: the path this rule governs, whichever
    path the crate would have taken. Without it, the render is whatever ``Component.render`` picks,
    and a cell that picks the hybrid template path is set aside, not counted."""
    found: list[tuple] = []
    rendered = 0
    hybrid: set[tuple[str, str]] = set()
    for key, cls in (CLASSES if classes is None else classes).items():
        names, required = class_params(cls)
        keys = class_keys(cls)
        # EVERY framework, for every class: the renderer is chosen by the constructor argument or
        # by ``djust.config``, so each is driven under both (a class that ignores them renders the
        # same three times)
        frameworks = url_sweep.FRAMEWORKS
        for claim in only_claims:
            for label, payload in payloads:
                claimed = CLAIMS[claim](payload)
                for shape in SHAPES:
                    for name in names:
                        for framework in frameworks:
                            result = drive(
                                cls, key, names, required, keys, name, shape, framework,
                                companions, claimed, force_python,
                            )  # fmt: skip
                            if result is None:
                                continue
                            if result is HYBRID:
                                hybrid.add((key, claim))
                                continue
                            out = str(result)
                            rendered += 1
                            holes = holes_in(out)
                            if wide:
                                # the classes outside the URL family: a payload's handler in an
                                # attribute an ITEM value reached, or a script URL a browser FOLLOWS
                                # (``exec``). A scalar constructor argument is the developer's own,
                                # and an author-named ``data-url`` attribute is inert.
                                holes = [
                                    h
                                    for h in holes
                                    if (len(h) == 4 and h[2] == "exec")
                                    or (len(h) == 2 and shape != "scalar")
                                ]
                            if holes:
                                found.append((key, name, shape, claim, label, tuple(holes)))
    return found, rendered, hybrid


def _report(found: list[tuple]) -> str:
    by_site: dict[tuple[str, str], set] = {}
    for key, name, shape, claim, label, _holes in found:
        by_site.setdefault((key.replace("djust.components.", ""), name), set()).add(
            (shape, claim, label)
        )
    return "\n".join(
        f"{cls}.{param}: {len(v)} cells, e.g. {sorted(v)[0]}"
        for (cls, param), v in sorted(by_site.items())
    )


class TestTheSweep:
    @pytest.mark.parametrize(
        ("force_python", "companions"),
        [(False, False), (True, False), (True, True)],
        ids=["as-rendered", "python-path", "python-path+companions"],
    )
    def test_no_class_component_lets_a_marked_safe_value_out_of_its_attribute(
        self, force_python: bool, companions: bool
    ) -> None:
        found, rendered, _hybrid = sweep(force_python=force_python, companions=companions)
        assert rendered > (50 if ONLY else 10_000), f"the sweep barely ran ({rendered} renders)"
        assert ONLY or len(CLASSES) >= 20, sorted(CLASSES)
        assert not found, _report(found)

    def test_the_hybrid_template_path_is_set_aside_and_only_for_classes_with_a_template(
        self,
    ) -> None:
        """A value the crate's constructor refuses (a lazy string is one) leaves a ``Component``
        with a ``template`` to draw it with ``{{ x }}``: no URL check, for any value (#3900).
        Pinned so the cells set aside stay exactly that, and the day it is fixed this test says to
        delete the exception."""
        _found, _rendered, hybrid = sweep()
        if ONLY:
            pytest.skip("a narrowed sweep (the gate-off harness)")
        assert hybrid, "no cell reached the hybrid template path any more: delete this exception"
        for key, _claim in hybrid:
            assert getattr(CLASSES[key], "template", None) is not None, key
        assert "lazy-mark_safe" in {claim for _key, claim in hybrid}

    def test_the_sweep_can_fail(self) -> None:
        """A component that prints a marked-safe value raw IS found (not a tautology)."""
        from djust.components.base import Component

        class Leaky(Component):
            def __init__(self, items: list | None = None, href: str = "") -> None:
                super().__init__()
                self.href = href

            def _render_custom(self) -> str:  # pragma: no cover - never called
                return ""

            def render(self) -> str:  # type: ignore[override]
                return f'<a href="{self.href}">x</a>'

        out = Leaky(href=mark_safe('/x" onmouseover="alert(1)" y="')).render()
        assert holes_in(out) == [("a", "onmouseover")]
        assert holes_in(Leaky(href=mark_safe("javascript:alert(1)")).render())


def _call_sites() -> set[tuple[str, str, str]]:
    """``(file, function, first argument)`` of every ``url_attr`` / ``srcset_attr`` call that does
    not say ``honor_safe=False`` and is not the shared data helper, under ``python/djust``
    (tests excluded). The native handlers are in: they are handed text today, and that is
    the engine's doing, not a property of the handler."""
    sites: set[tuple[str, str, str]] = set()
    for path in sorted((ROOT / "python/djust").rglob("*.py")):
        rel = str(path.relative_to(ROOT / "python/djust"))
        if "/tests/" in "/" + rel or path.name.startswith("test_"):
            continue
        sites |= _honouring_calls(path.read_text(), rel)
    return sites


def _honouring_calls(source: str, rel: str = "x.py") -> set[tuple[str, str, str]]:
    """The ``url_attr`` / ``srcset_attr`` calls in ``source`` that are not ``honor_safe=False``."""
    sites: set[tuple[str, str, str]] = set()
    tree = ast.parse(source)
    parents = {c: n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, (ast.Name, ast.Attribute))):
            continue
        name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr
        if name not in ("url_attr", "srcset_attr"):
            continue
        honor = {k.arg: k.value for k in node.keywords}.get("honor_safe")
        if isinstance(honor, ast.Constant) and honor.value is False:
            continue
        fn: ast.AST = node
        while fn in parents and not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            fn = parents[fn]
        sites.add((rel, getattr(fn, "name", "?"), ast.unparse(node.args[0]) if node.args else ""))
    return sites


class TestCallerSet:
    """ONE rule, pinned: a marked-safe value never bypasses the URL scheme allowlist, whether the
    URL is data (an item, a row, a constructor argument) or a template tag's own keyword. So no
    call site may leave ``honor_safe`` at its default or pass ``True``."""

    def test_no_url_call_site_honours_a_marked_safe_value(self) -> None:
        sites = _call_sites()
        assert not sites, (
            "a url_attr / srcset_attr call that honours a marked-safe value (use data_url_attr "
            f"or honor_safe=False): {sorted(sites)}"
        )

    def test_the_scan_sees_calls(self) -> None:
        """Not vacuous: the helper's own callers are there to be found."""
        src = "".join(
            p.read_text()
            for p in (ROOT / "python/djust/components").rglob("*.py")
            if "/tests/" not in str(p)
        )
        assert src.count("data_url_attr(") > 60

    @pytest.mark.parametrize(
        "source",
        [
            "def f(x):\n    return url_attr(x)\n",
            "def f(x):\n    return url_attr(x, image=True)\n",
            "def f(x):\n    return url_attr(x, honor_safe=True)\n",
            "def f(x):\n    return srcset_attr(x)\n",
            "def f(x, h):\n    return u.url_attr(x, honor_safe=h)\n",
        ],
    )
    def test_the_scan_can_fail(self, source: str) -> None:
        assert _honouring_calls(source), source

    @pytest.mark.parametrize(
        "source",
        [
            "def f(x):\n    return url_attr(x, honor_safe=False)\n",
            "def f(x):\n    return data_url_attr(x)\n",
            "def f(x):\n    return srcset_attr(x, honor_safe=False)\n",
        ],
    )
    def test_the_scan_passes_the_helper_and_an_explicit_false(self, source: str) -> None:
        assert not _honouring_calls(source), source


# --- the TAGS that read a URL out of an item: both engines ------------------------------------

#: The tags with a ``data_url_attr`` site (a URL read out of a dict item or a list). Each name must
#: exist in the tag inventory (a rename fails ``test_every_data_tag_exists``).
DATA_TAGS = (
    "activity_feed", "avatar_group", "carousel", "chat_bubble", "data_card_grid", "dropdown_menu",
    "image_cropper", "image_upload_preview", "lightbox", "live_indicator", "mentions_input",
    "menu_item", "org_chart", "presence_avatars", "rich_select", "sortable_grid",
    "avatar", "responsive_image",
)  # fmt: skip
#: The tags under test (all of them unless ``MARKED_SAFE_SWEEP_ONLY`` narrows the sweep).
TAGS = tuple(t for t in DATA_TAGS if not ONLY or t in ONLY)

TAG_PAYLOADS = tuple(p for p in PAYLOADS if p[0] in {"js", "js-mixed", "data-html", "breakout-dq"})
HTTPS = dict(url_sweep.SAFE)["https"]


def _inventory() -> dict:
    return {i.name: i for i in url_sweep.inventory().values() if i.lib == "djust_components"}


DATA_SHAPES = ("list_str", "list_dict", "dict")


def _tag_calls(info, shapes: tuple[str, ...] = DATA_SHAPES) -> list[tuple[str, str, str]]:
    """``(parameter, shape, template)`` for the tag, one parameter at a time."""
    from scripts.lib.template_library_parity import variants

    out = []
    names = variants.passed_names(info)
    for name in names:
        extra = " open=True" if info.name == "dropdown_menu" else ""
        base = " ".join(url_sweep.typical_args(info, name))
        # an ``event`` makes a card / node clickable (more attributes carry the item's id/title)
        if "event" in names and name != "event":
            base += ' event="ev"'
        # an org chart draws from its ``root`` id: the item ids are the payload, so is the root
        if info.name == "org_chart" and name == "nodes":
            base += " root=p_scalar"
        # DATA shapes (a list of items, an item) are swept for every hole. A SCALAR is the
        # developer's own tag argument (written in the template): its marked-safe value is
        # honoured by every tag's text and attributes (the tag-argument policy), so a scalar is
        # swept for a script-capable URL only (see ``test_no_tag_..._scalar``).
        for shape in shapes:
            args = f"{base} {name}=p_{shape}{extra}".strip()
            src = url_sweep.wrap(info, args, None, "")
            if info.name == "dropdown_menu":
                # an EMPTY body: a body replaces the items (``render_dropdown_menu``)
                src = (
                    "{% load djust_components %}{% dropdown_menu "
                    + f"{name}=p_{shape}"
                    + (" open=True" if name != "open" else "")
                    + " %}{% enddropdown_menu %}"
                )
            elif info.name == "menu_item":
                # not ``disabled`` (a disabled item draws no link) and not ``divider``
                src = (
                    "{% load djust_components %}{% menu_item "
                    + ('label="x" ' if name != "label" else "")
                    + f"{name}=p_{shape}"
                    + " %}"
                )
            out.append((name, shape, src))
    return out


def _contexts(value: object) -> dict:
    return {
        "p_scalar": value,
        "p_list_str": [value, value],
        "p_dict": url_sweep.dict_of(value, TAG_KEYS),  # type: ignore[arg-type]
        "p_list_dict": [url_sweep.dict_of(value, TAG_KEYS)] * 2,  # type: ignore[arg-type]
    }


def _both(src: str, ctx: dict) -> tuple[str | None, str | None]:
    from djust.tests.test_component_dj_attr_parity_3669 import _django
    from djust.tests.test_components_csp_aria import _rust

    out = []
    for engine in (_django, _rust):
        harness.reset_ids()
        try:
            out.append(str(engine(src.replace("{% load djust_components %}", ""), ctx)))
        except Exception:  # noqa: BLE001 - a call a tag rejects says nothing
            out.append(None)
    return out[0], out[1]


class TestTags:
    def test_every_data_tag_exists(self) -> None:
        inv = _inventory()
        assert [t for t in DATA_TAGS if t not in inv] == []

    def test_each_tag_is_reached_by_the_driver(self) -> None:
        """The instrument sees a URL arrive at an attribute, for every tag (a sweep over tags it
        never reached would pass for nothing)."""
        inv = _inventory()
        reached: set[str] = set()
        for tag in TAGS:
            for _param, _shape, src in _tag_calls(inv[tag], (*DATA_SHAPES, "scalar")):
                py, rs = _both(src, _contexts(HTTPS))
                if any(out and url_sweep.carries(out, HTTPS) for out in (py, rs)):
                    reached.add(tag)
                    break
        assert reached == set(TAGS), sorted(set(TAGS) - reached)

    @pytest.mark.parametrize("tag", TAGS)
    def test_no_tag_lets_a_marked_safe_item_value_out_of_its_attribute(self, tag: str) -> None:
        info = _inventory()[tag]
        calls = _tag_calls(info)
        holes: dict[tuple[str, str], set] = {}
        rendered = 0
        for claim, make in CLAIMS.items():
            for label, payload in TAG_PAYLOADS:
                ctx = _contexts(make(payload))
                for param, shape, src in calls:
                    for engine, out in zip(("python", "rust"), _both(src, ctx)):
                        if out is None:
                            continue
                        rendered += 1
                        found = holes_in(out)
                        if found:
                            holes.setdefault((engine, param), set()).add((shape, claim, label))
        assert rendered > 100, f"{tag}: the sweep barely ran ({rendered})"
        assert not holes, "\n".join(f"{tag} {k}: {sorted(v)[:2]}" for k, v in sorted(holes.items()))

    @pytest.mark.parametrize("tag", TAGS)
    def test_no_tag_lets_a_marked_safe_scalar_url_through_as_a_script_url(self, tag: str) -> None:
        """The scalar URL arguments of these tags (``src=``, ``href=``) are checked too: the tags
        whose item URLs are data share the helper, and a script scheme is never honoured."""
        info = _inventory()[tag]
        calls = _tag_calls(info, ("scalar",))
        holes = set()
        for claim, make in CLAIMS.items():
            for label, payload in TAG_PAYLOADS:
                if label.startswith("breakout"):
                    continue
                ctx = _contexts(make(payload))
                for param, _shape, src in calls:
                    for engine, out in zip(("python", "rust"), _both(src, ctx)):
                        if out and url_sweep.findings(out):
                            holes.add((engine, param, claim, label))
        assert not holes, f"{tag}: {sorted(holes)[:4]}"


class TestTheNoOpHrefException:
    """The ONE exception, on purpose: the literal no-op hrefs of a dropdown toggle. Exact text
    (after ``strip`` / case-folding, nothing appended), in an ``href`` only."""

    @pytest.mark.parametrize("claim", ["plain", *sorted(CLAIMS)])
    @pytest.mark.parametrize(
        ("given", "kept"),
        [
            ("javascript:void(0)", "javascript:void(0)"),
            ("javascript:;", "javascript:;"),
            ("  JavaScript:Void(0)  ", "javascript:void(0)"),
            ("JAVASCRIPT:;", "javascript:;"),
            # whitespace around the literal is stripped (the rule says so); nothing inside it
            ("javascript:void(0) ", "javascript:void(0)"),
            ("\tjavascript:;\n", "javascript:;"),
        ],
    )
    def test_the_literal_is_kept(self, claim: str, given: str, kept: str) -> None:
        from djust.components.utils import data_url_attr

        value = given if claim == "plain" else CLAIMS[claim](given)
        assert data_url_attr(value) == kept

    @pytest.mark.parametrize("claim", ["plain", *sorted(CLAIMS)])
    @pytest.mark.parametrize(
        "given",
        [
            "javascript:void(0);alert(1)",
            # the spellings a widened ``_NOOP_HREFS`` would let through: each is refused
            "javascript:void(0);",
            "JAVASCRIPT:VOID(0);",
            "javascript:void (0)",
            "javascript:void(0)\x00",
            "javascript:void(0) x",
            "javascript:;;",
            "javascript:; ;",
            "javascript:void(0),",
            "javascript:void(0)#",
            "javascript:undefined",
            "javascript:0",
            "javascript:void 0",
            "javascript:void(0) 1x",
            "javascript:void(0)//x",
            "javascript:void( 0 )",
            "javascript:void(1)",
            "javascript:;alert(1)",
            "javascript:",
            "java\tscript:void(0)",
            "\x00javascript:void(0)",
            "vbscript:void(0)",
            "data:;",
            "javascript:alert(1)",
        ],
    )
    def test_anything_else_is_neutralised_marked_or_not(self, claim: str, given: str) -> None:
        from djust.components.utils import data_url_attr

        value = given if claim == "plain" else CLAIMS[claim](given)
        assert data_url_attr(value) == "#"

    @pytest.mark.parametrize("claim", ["plain", *sorted(CLAIMS)])
    @pytest.mark.parametrize("given", ["javascript:void(0)", "javascript:;"])
    def test_an_image_source_never_gets_the_exception(self, claim: str, given: str) -> None:
        from djust.components.utils import data_url_attr

        value = given if claim == "plain" else CLAIMS[claim](given)
        assert data_url_attr(value, image=True) == "#"

    def test_the_class_components_keep_a_toggle_href(self) -> None:
        from djust.components.components.nav_menu import NavMenu

        out = str(NavMenu(items=[{"label": "a", "href": "javascript:void(0)"}]).render())
        assert 'href="javascript:void(0)"' in out, out


class TestTextSlotsKeepTheirMarkup:
    """The other half of the rule: what a TEXT slot did with a marked-safe value is unchanged.

    The template TAGS keep the markup of a ``mark_safe`` / ``SafeString`` / lazy marked-safe value
    in a text slot (``conditional_escape``), and escape ``Markup`` (``str()`` drops its
    ``__html__``): both as before. The class-based components have always escaped what they were
    given, a marked-safe value included, which is why they pass ``honor_safe=False``."""

    @pytest.mark.parametrize("claim", sorted(CLAIMS))
    def test_a_class_component_escapes_a_text_slot_as_it_always_did(self, claim: str) -> None:
        from djust.components.components.breadcrumb import Breadcrumb

        out = str(
            Breadcrumb(
                items=[{"label": CLAIMS[claim]("<b>home</b>"), "url": "/"}, {"label": "x"}]
            ).render()
        )
        assert "&lt;b&gt;home&lt;/b&gt;" in out and "<b>" not in out, out
        out = str(
            Breadcrumb(items=[{"label": "a", "url": CLAIMS[claim]("javascript:alert(1)")}]).render()
        )
        assert "javascript:" not in out, out

    @pytest.mark.parametrize("claim", ["mark_safe", "safestring-subclass", "lazy-mark_safe"])
    @pytest.mark.parametrize(
        ("src", "key"),
        [
            ("{% data_card_grid items=p_list_dict %}", "title"),
            ("{% org_chart nodes=p_list_dict root=p_scalar %}", "name"),
            ("{% activity_feed events=p_list_dict %}", "user"),
        ],
        ids=["data_card_grid", "org_chart", "activity_feed"],
    )
    def test_a_tag_text_slot(self, claim: str, src: str, key: str) -> None:
        from djust.tests.test_component_dj_attr_parity_3669 import _django

        marked = CLAIMS[claim]("<b>shown</b>")
        item = {"id": "n1", "title": marked, "name": marked, "user": marked, "action": "did"}
        out = _django(src, {"p_list_dict": [item], "p_scalar": "n1"})
        assert "<b>shown</b>" in out, out


class TestModelTable:
    """``model_table`` keeps a marked-safe value in its CELLS (text) and never in the attributes it
    also writes: the row id (``dj-value-row``), the column key and the filter values (#3898)."""

    @staticmethod
    def _queryset(**fields: object):
        from django.contrib.auth.models import User

        class QS(list):
            model = User

        return QS([User(**fields)])

    @staticmethod
    def _render(qs, **kw: object) -> str:
        from djust.components.templatetags._forms import model_table

        return str(model_table(queryset=qs, include=["username"], **kw))

    @pytest.mark.parametrize("claim", sorted(CLAIMS))
    @pytest.mark.parametrize(
        "payload", ['x" onmouseover="alert(1)" y="', "x' onmouseover='alert(1)"]
    )
    def test_a_marked_safe_row_id_stays_inside_dj_value_row(self, claim: str, payload: str) -> None:
        qs = self._queryset(id=CLAIMS[claim](payload), username="u")
        out = self._render(qs, selectable=True)
        assert "dj-value-row" in out
        assert holes_in(out) == [], out

    @pytest.mark.parametrize("claim", sorted(CLAIMS))
    def test_a_marked_safe_cell_keeps_its_markup(self, claim: str) -> None:
        qs = self._queryset(id=1, username=CLAIMS[claim]("<i>mark</i>"))
        out = self._render(qs)
        # SafeString family and lazy keep the markup (``conditional_escape(str(v))``); an
        # ``__html__`` str is the one the cell code escapes, as before
        assert ("<i>mark</i>" in out) == (claim != "html-object"), out

    @pytest.mark.parametrize("claim", sorted(CLAIMS))
    def test_a_marked_safe_filter_value_stays_inside_value(self, claim: str) -> None:
        qs = self._queryset(id=1, username="u")
        out = self._render(qs, filters={"username": CLAIMS[claim]('x" onmouseover="alert(1)" y="')})
        assert "data-table__filter-input" in out
        assert holes_in(out) == [], out


# --- EVERY tag of every library: what is covered and what is not, derived ----------------------

#: ``library/tag/parameter`` of the template tags where an ITEM value (a list of items, an item)
#: that is marked safe still leaves an attribute, DERIVED by :func:`wide_tag_leaks` (every tag of
#: every library, every parameter, item keys read from each tag's own source). This is the list of
#: what #3898 does NOT cover for tags, kept exact: a new leak fails, and a fixed tag fails until
#: it is removed here. Tracked in the follow-up issue linked from the PR.
KNOWN_TAG_ITEM_LEAKS: frozenset[str] = frozenset(
    {
        f"djust_components/{tag}"
        for tag in (
            "audit_log/entries",
            "autocomplete/suggestions",
            "collab_selection/users",
            "combobox/options",
            "combobox/selected",
            "cursors/users",
            "dashboard_grid/panels",
            "data_grid/columns",
            "data_grid/rows",
            "dependent_select/options",
            "dj_select/options",
            "export_dialog/columns",
            "export_dialog/formats",
            "fab/actions",
            "file_tree/nodes",
            "gantt_chart/tasks",
            "kanban_board/columns",
            "line_chart/series",
            "masonry_grid/items",
            "meter/segments",
            "model_selector/options",
            "multi_select/options",
            "notification_center/notifications",
            "reactions/options",
            "scroll_spy/sections",
            "sortable_list/items",
            "tag_input/suggestions",
            "tag_input/tags",
            "toast_container/toasts",
            "tour/steps",
            "tree_view/nodes",
            "wizard/steps",
        )  # fmt: skip
    }
    | {"theme_components/theme_radio/options", "theme_components/theme_select/options"}
)


def wide_tag_leaks() -> set[str]:
    """``library/tag/parameter`` for every (tag, parameter) where a marked-safe ITEM value (a
    list of items, a list of text, an item) creates the payload's ``onmouseover`` attribute, in
    Django's engine. The scalar arguments of a tag are the developer's own and are not counted."""
    from django.template import engines

    from scripts.lib.template_library_parity import variants

    python_engine = engines["django"]
    value = mark_safe('x" onmouseover="alert(zq7)" y="')
    leaks: set[str] = set()
    for _key, info in sorted(url_sweep.inventory().items()):
        if info.name.startswith("end"):
            continue
        names = variants.passed_names(info)
        if not names:
            continue
        keys = url_sweep.keys_read(info, url_sweep._fn_of(info)) | set(TAG_KEYS) | set(ATTR_KEYS)
        parent = variants.PARENT.get(info.name)
        ctx = {
            "p_list_str": [value, value],
            "p_dict": url_sweep.dict_of(value, keys),  # type: ignore[arg-type]
            "p_list_dict": [url_sweep.dict_of(value, keys)] * 2,  # type: ignore[arg-type]
        }
        for name in names:
            for shape in DATA_SHAPES:
                args = url_sweep.typical_args(info, name) + [f"{name}=p_{shape}"]
                src = url_sweep.wrap(info, " ".join(args), parent, "open=True" if parent else "")
                html = url_sweep._render(python_engine, src, ctx)
                if html and any(len(h) == 2 for h in holes_in(html)):
                    leaks.add(f"{info.lib}/{info.name}/{name}")
    return leaks


class TestEveryClass:
    """EVERY class component (all of them the URL sweep can drive, 200+), not only the ones whose
    module names ``url_attr``: an item value that is marked safe never creates the payload's
    handler in an attribute, and no script URL a browser follows is printed. A scalar constructor
    argument is the developer's own (see ``wide`` in :func:`sweep`)."""

    @pytest.mark.parametrize("companions", [False, True], ids=["bare", "companions"])
    def test_no_class_lets_a_marked_safe_item_value_out_of_an_attribute(
        self, companions: bool
    ) -> None:
        payloads = tuple(p for p in PAYLOADS if p[0] in {"js", "breakout-dq"})
        found, rendered, _ = sweep(
            only_claims=("mark_safe", "html-object"),
            force_python=True,
            companions=companions,
            classes=ALL_CLASSES,
            payloads=payloads,
            wide=True,
        )
        assert rendered > (50 if ONLY else 40_000), f"the sweep barely ran ({rendered})"
        assert ONLY or len(ALL_CLASSES) > 150, len(ALL_CLASSES)
        assert not found, _report(found)

    def test_the_classes_are_derived_not_listed(self) -> None:
        """Every driven class is swept (the set is the URL sweep's own), and the URL family is a
        subset of it."""
        assert set(CLASSES) <= set(ALL_CLASSES)
        assert ONLY or len(ALL_CLASSES) > len(CLASSES) * 4


#: ``Class.parameter`` of the pairs NO cell can get through, and why. Each one takes numbers, a
#: dotted module path or a queryset and raises on any text, so no markup can arrive in them; the
#: list is exact (a pair that starts to draw fails until it is removed here).
UNREACHED = {
    "components.bar_chart.BarChart.data": "numbers",
    "components.calendar_heatmap.CalendarHeatmap.year": "a year",
    "components.markdown.Markdown.extensions": "module names (ModuleNotFoundError)",
    "components.markdown_editor.MarkdownEditor.mode": "two allowed words (ValueError)",
    "components.meter.Meter.segments": "numbers",
    "data.pagination.PaginationComponent.max_visible_pages": "a number",
    "data.pagination.PaginationComponent.total_items": "a number",
    "data.pagination.PaginationComponent.total_pages": "a number",
    "forms.foreign_key.ForeignKeySelect.queryset": "a queryset",
    "forms.foreign_key.ManyToManySelect.queryset": "a queryset",
    "ui.pagination_simple.Pagination.current_page": "a number",
    "ui.pagination_simple.Pagination.total_pages": "a number",
    "ui.progress.ProgressComponent.max_value": "a number",
    "ui.progress.ProgressComponent.min_value": "a number",
    "ui.progress.ProgressComponent.value": "a number",
    "ui.progress_simple.Progress.value": "a number",
    "ui.offcanvas_simple.Offcanvas.placement": "four allowed words (ValueError)",
}

#: ``Class.parameter`` -> the item keys a cell could not take the claim for, found per key by
#: :func:`_search_overrides`. A key left out is a key the sweep does NOT test: it is stated here,
#: exactly (a number is a number key: ``int(item["level"])``; ``badge`` is left out of the shape
#: that gives it the claim itself, because the list group reads it as a mapping, which the typed
#: shape ``list_dict`` gives it and sweeps).
CARRIED_DIFFERENTLY = {
    "components.dashboard_grid.DashboardGrid.panels": {
        "col=number", "height=number", "row=number", "width=number",
    },
    "components.org_chart.OrgChart.nodes": {"parent=number"},
    "components.table_of_contents.TableOfContents.items": {"level=number"},
    "ui.list_group_simple.ListGroup.items": {"badge=left out"},
}  # fmt: skip


def _numeric_parameter(cls: type, name: str) -> bool:
    """A constructor parameter whose default is a bool / int / float (derived from the signature):
    it selects, it does not print."""
    try:
        param = inspect.signature(cls.__init__).parameters.get(name)  # type: ignore[misc]
    except (TypeError, ValueError):
        return False
    return param is not None and isinstance(param.default, (bool, int, float))


def reach() -> dict[tuple[str, str], dict[str, int]]:
    """``{(class, parameter): {"rendered": n, "echoed": n}}`` over EVERY derived pair: the
    parameter is given a marked-safe probe (in every shape, under every framework, with and
    without the companions) and the output is searched for the probe. ``rendered`` counts the
    cells that drew; ``echoed`` those whose output carries the probe."""
    out: dict[tuple[str, str], dict[str, int]] = {}
    probe = PROBE
    for key, cls in ALL_CLASSES.items():
        names, required = class_params(cls)
        keys = class_keys(cls)
        for name in names:
            out[(key, name)] = {"rendered": 0, "echoed": 0}
        for companions in (False, True):
            for framework in url_sweep.FRAMEWORKS:
                for shape in SHAPES:
                    for name in names:
                        result = drive(
                            cls, key, names, required, keys, name, shape, framework,
                            companions, probe, True,
                        )  # fmt: skip
                        if result is None:
                            continue
                        out[(key, name)]["rendered"] += 1
                        if REACH_MARK in str(result):
                            out[(key, name)]["echoed"] += 1
    return out


def carried_differently() -> dict[str, set[str]]:
    """``"Class.parameter" -> {"key=left out" / "key=number"}`` for every item key some cell of
    the probe could not take the claim for (what :func:`_search_overrides` found), so a key the
    sweep does not reach is stated, not silently skipped."""
    found: dict[str, set[str]] = {}
    for (key, name, _shape, _fw, _companions, force_python), overrides in _OVERRIDES.items():
        if not force_python or not overrides:
            continue
        for k, form in overrides.items():
            how = "left out" if form is DROP else "number"
            found.setdefault(f"{key.replace('djust.components.', '')}.{name}", set()).add(
                f"{k}={how}"
            )
    return found


class TestEveryClassIsReached:
    """The class sweep skips a cell its component rejects. That is only honest if the skip is
    accounted for: this test fails a (class, parameter) pair that NO cell drew (so nothing was
    swept there), and a class none of whose parameters ever printed a probe. The first version
    of the sweep skipped every item cell of ``ui.ListGroup`` and ``ui.NavBar`` (a ``badge`` that
    must be a mapping, a ``dropdown`` that must be a list) and every parameter of ``StatCard`` (a
    required ``value`` that must be text) without a word, and a leak sat behind each."""

    @pytest.fixture(scope="class")
    def outcome(self) -> dict[tuple[str, str], dict[str, int]]:
        return reach()

    def test_every_pair_is_drawn_by_some_cell_or_listed_with_its_reason(self, outcome) -> None:
        short = lambda key: key.replace("djust.components.", "")  # noqa: E731
        unreached = {pair for pair, n in outcome.items() if not n["rendered"]}
        unexplained = {
            pair for pair in unreached if not _numeric_parameter(ALL_CLASSES[pair[0]], pair[1])
        }
        listed = {f"{short(k)}.{n}" for k, n in unexplained if not ONLY}
        if ONLY:
            assert not [p for p in listed if p not in UNREACHED], sorted(listed)
            return
        assert listed == set(UNREACHED), (
            f"pairs no cell drew (fix the probe's input, or list them with a reason): "
            f"{sorted(listed - set(UNREACHED))}\n"
            f"listed but now drawn (delete from UNREACHED): {sorted(set(UNREACHED) - listed)}"
        )

    def test_the_pairs_are_partitioned_and_most_of_them_print_the_probe(self, outcome) -> None:
        echoed = {p for p, n in outcome.items() if n["echoed"]}
        silent = {p for p, n in outcome.items() if n["rendered"] and not n["echoed"]}
        unreached = {p for p, n in outcome.items() if not n["rendered"]}
        derived = {(key, name) for key, cls in ALL_CLASSES.items() for name in class_params(cls)[0]}
        assert echoed | silent | unreached == derived
        assert not (echoed & silent or echoed & unreached or silent & unreached)
        assert len(echoed) + len(silent) + len(unreached) == len(derived)
        assert ONLY or len(echoed) > 700, len(echoed)

    def test_the_keys_a_cell_could_not_carry_are_exactly_the_listed_ones(self, outcome) -> None:
        """A crash is searched per KEY, and what the search leaves out or turns into a number
        is stated here, so the sweep cannot lose a key without a test saying so."""
        assert carried_differently() == CARRIED_DIFFERENTLY

    def test_every_class_prints_a_probe_through_some_parameter(self, outcome) -> None:
        silent_classes = sorted(
            {
                key
                for key in ALL_CLASSES
                if not any(n["echoed"] for (k, _name), n in outcome.items() if k == key)
            }
        )
        assert not silent_classes, silent_classes


class TestEveryTag:
    def test_the_item_leaks_of_the_tags_are_exactly_the_listed_ones(self) -> None:
        leaks = wide_tag_leaks()
        assert leaks == set(KNOWN_TAG_ITEM_LEAKS), (
            f"new leaks (escape the item value with attr_text): {sorted(leaks - KNOWN_TAG_ITEM_LEAKS)}\n"
            f"fixed (delete from KNOWN_TAG_ITEM_LEAKS): {sorted(set(KNOWN_TAG_ITEM_LEAKS) - leaks)}"
        )

    def test_the_tags_this_change_edits_are_not_among_them(self) -> None:
        edited = {f"djust_components/{t}" for t in DATA_TAGS}
        assert not [k for k in KNOWN_TAG_ITEM_LEAKS if "/".join(k.split("/")[:2]) in edited]
