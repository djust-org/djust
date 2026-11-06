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


def _item(value: object, keys: set[str] = KEYS) -> dict:
    """An item dict whose every key is ``value``, with a nested ``dropdown`` list of the same
    (a navbar's dropdown entries are items under the ``dropdown`` key)."""
    item = url_sweep.dict_of(value, keys)  # type: ignore[arg-type]
    item["dropdown"] = [url_sweep.dict_of(value, keys) for _ in range(2)]  # type: ignore[arg-type]
    return item


def _obj(value: object, keys: set[str] = KEYS) -> SimpleNamespace:
    """An object item (``NavItem``): every attribute is ``value``, except the badge, which the
    component compares with ``> 0``."""
    return SimpleNamespace(**{**dict.fromkeys(OBJ_KEYS | keys, value), "badge": 1})


def _shapes(value: object, keys: set[str] = KEYS) -> dict[str, object]:
    return {
        "scalar": value,
        "list_str": [value] * N_ITEMS,
        "dict": url_sweep.dict_of(value, keys),  # type: ignore[arg-type]
        # alternate items WITH a dropdown and plain ones: a navbar draws the two differently
        "list_dict": [
            _item(value, keys) if i % 2 == 0 else url_sweep.dict_of(value, keys)  # type: ignore[arg-type]
            for i in range(N_ITEMS)
        ],
        "list_obj": [_obj(value, keys) for _ in range(N_ITEMS)],
    }


#: Parameters that make a conditional section draw (a split button, a brand, an image with its
#: alt text): the second pass sets them so the attributes inside those sections are reached.
URL_COMPANIONS = {
    "src", "image", "logo", "brand_logo", "thumbnail", "avatar", "brand_href", "url", "href",
}  # fmt: skip
TEXT_COMPANIONS = {"brand", "brand_name", "alt", "label", "title", "name", "text"}


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
        if variants.kind_of(n) == "bool" or _bool_default(cls, n):
            out[n] = True
        elif n in URL_COMPANIONS:
            out[n] = "/ok.png"
        elif n in TEXT_COMPANIONS:
            out[n] = "x"
    return out


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
        names, required = url_sweep.component_params(cls)
        keys = class_keys(cls)
        # ``component_id`` is a LiveComponent's own constructor argument (it lands in ``id=``)
        if issubclass(cls, LiveComponent) and "component_id" not in names:
            names = [*names, "component_id"]
        # EVERY framework, for every class: the renderer is chosen by the constructor argument or
        # by ``djust.config``, so each is driven under both (a class that ignores them renders the
        # same three times)
        frameworks = url_sweep.FRAMEWORKS
        for claim in only_claims:
            for label, payload in payloads:
                for shape, value in _shapes(CLAIMS[claim](payload), keys).items():
                    for name in names:
                        for framework in frameworks:
                            kwargs = {
                                **required,
                                **(_companions(cls, names, name) if companions else {}),
                                name: value,
                            }
                            if "css_framework" in names and name != "css_framework":
                                kwargs["css_framework"] = framework
                            harness.reset_ids()
                            try:
                                with _framework(framework):
                                    obj = cls(**kwargs)  # type: ignore[call-arg]
                                    if force_python:
                                        if hasattr(obj, "_rust_instance"):
                                            obj._rust_instance = None
                                        if hasattr(obj, "template"):
                                            obj.template = None
                                    elif _hybrid_template_path(obj):
                                        hybrid.add((key, claim))
                                        continue
                                    out = str(obj.render())
                            except Exception:  # noqa: BLE001 - a call the component rejects says nothing
                                continue
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
