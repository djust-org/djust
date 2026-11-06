"""A marked-safe value read out of a component's DATA is not a URL and not an attribute (#3898, #3894).

``url_attr`` honours a ``SafeData`` value by default: a marked-safe string skips the scheme
allowlist AND the escaping. That is right for a developer's own tag argument and wrong for a value
a view assembled into an item dict, a list of images or a constructor argument of a class-based
component, which "have always escaped what they were given, a marked-safe value included"
(``url_attr``'s own docstring). ``mark_safe`` on a value keeps its markup in a TEXT slot; it never
meant "use it as a URL" or "write it into an attribute".

This is the sweep that makes the rule hold for every component, not the ones somebody listed:

* it DERIVES the classes (every class-based component whose module calls ``url_attr``) and the
  parameters (the constructor's, or the keys a ``LiveComponent`` reads), with the helpers the URL
  sweep uses;
* it drives each parameter with each way a value claims to be safe (``mark_safe``, a ``SafeString``
  subclass, a lazy marked-safe string, ``markupsafe.Markup``) in three shapes (a scalar, a dict, a
  list of dicts), under all three CSS frameworks, with script schemes and attribute-breakout
  payloads;
* a hole is a script-capable URL in an attribute (``url_sweep.findings``) or an ``on...`` attribute
  the payload created.

``TestCallerSet`` pins which ``url_attr`` call sites still honour a marked-safe value: only the
tags whose value is the developer's own argument. A new site fails until it is decided.
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
KEYS = set(url_sweep.COMMON_KEYS)
#: The item keys the tags read besides the URL-shaped ones (a card's category, a feed's user, a
#: node's parent ...): derived from the tags' own keys would need the harness; these are the ones
#: the data tags' sources name, and ``test_each_tag_is_reached_by_the_driver`` shows the driver
#: gets as far as an attribute.
TAG_KEYS = KEYS | {
    "category", "user", "action", "target", "time", "description", "badge", "event", "active",
    "nid",
}  # fmt: skip


#: Item attributes a component reads off an OBJECT item (``NavItem``): the dict keys plus these.
OBJ_KEYS = KEYS | {"target", "badge", "badge_variant", "active", "external", "icon", "label"}
#: A list of FIVE items, not one: a link is drawn for every item but the last, and a breadcrumb
#: collapses its middle items into a dropdown past its ``max_items``.
N_ITEMS = 5


def _item(value: object) -> dict:
    """An item dict whose every key is ``value``, with a nested ``dropdown`` list of the same
    (a navbar's dropdown entries are items under the ``dropdown`` key)."""
    item = url_sweep.dict_of(value, KEYS)  # type: ignore[arg-type]
    item["dropdown"] = [url_sweep.dict_of(value, KEYS) for _ in range(2)]  # type: ignore[arg-type]
    return item


def _obj(value: object) -> SimpleNamespace:
    """An object item (``NavItem``): every attribute is ``value``, except the badge, which the
    component compares with ``> 0``."""
    return SimpleNamespace(**{**dict.fromkeys(OBJ_KEYS, value), "badge": 1})


def _shapes(value: object) -> dict[str, object]:
    return {
        "scalar": value,
        "list_str": [value] * N_ITEMS,
        "dict": url_sweep.dict_of(value, KEYS),  # type: ignore[arg-type]
        "list_dict": [_item(value) for _ in range(N_ITEMS)],
        "list_obj": [_obj(value) for _ in range(N_ITEMS)],
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
) -> tuple[list[tuple], int, set[tuple[str, str]]]:
    """``([(class, param, shape, claim, payload label, holes)], renders, hybrid-template cells)``.

    ``force_python`` renders through ``_render_custom`` (the f-string path) for every component,
    the way ``test_ui_simple_components_escaping`` does: the path this rule governs, whichever
    path the crate would have taken. Without it, the render is whatever ``Component.render`` picks,
    and a cell that picks the hybrid template path is set aside, not counted."""
    found: list[tuple] = []
    rendered = 0
    hybrid: set[tuple[str, str]] = set()
    for key, cls in CLASSES.items():
        names, required = url_sweep.component_params(cls)
        # ``component_id`` is a LiveComponent's own constructor argument (it lands in ``id=``)
        if issubclass(cls, LiveComponent) and "component_id" not in names:
            names = [*names, "component_id"]
        # EVERY framework, for every class: the renderer is chosen by the constructor argument or
        # by ``djust.config``, so each is driven under both (a class that ignores them renders the
        # same three times)
        frameworks = url_sweep.FRAMEWORKS
        for claim in only_claims:
            for label, payload in PAYLOADS:
                for shape, value in _shapes(CLAIMS[claim](payload)).items():
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
    (tests and the native handlers excluded: those are handed text)."""
    sites = set()
    for path in sorted((ROOT / "python/djust").rglob("*.py")):
        rel = str(path.relative_to(ROOT / "python/djust"))
        if (
            "/tests/" in "/" + rel
            or path.name.startswith("test_")
            or path.name == "rust_handlers.py"
        ):
            continue
        tree = ast.parse(path.read_text())
        parents = {c: n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call) and isinstance(node.func, (ast.Name, ast.Attribute))
            ):
                continue
            name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr
            if name not in ("url_attr", "srcset_attr"):
                continue
            keywords = {k.arg: k.value for k in node.keywords}
            honor = keywords.get("honor_safe")
            if isinstance(honor, ast.Constant) and honor.value is False:
                continue
            fn = node
            while fn in parents and not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                fn = parents[fn]
            sites.add(
                (rel, getattr(fn, "name", "?"), ast.unparse(node.args[0]) if node.args else "")
            )
    return sites


#: The ONLY places a marked-safe value is still honoured: the value is the developer's own tag
#: ARGUMENT (a keyword of the template tag), not data read out of an item or a constructor of a
#: class-based component. Everything else goes through ``data_url_attr`` / ``honor_safe=False``.
HONOURED = {
    ("components/templatetags/djust_components.py", "avatar", "src"),
    ("components/templatetags/djust_components.py", "responsive_image", "src"),
    ("components/templatetags/djust_components.py", "responsive_image", "srcset"),
    ("components/templatetags/djust_components.py", "responsive_image", "placeholder"),
}


class TestCallerSet:
    def test_only_a_tag_argument_still_honours_a_marked_safe_value(self) -> None:
        sites = _call_sites()
        assert sites == HONOURED, (
            f"new (decide it: data goes through data_url_attr): {sorted(sites - HONOURED)}\n"
            f"gone (delete it from HONOURED): {sorted(HONOURED - sites)}"
        )

    def test_no_class_based_component_honours_one(self) -> None:
        for rel, fn, arg in _call_sites():
            assert rel.startswith("components/templatetags/"), (rel, fn, arg)


# --- the TAGS that read a URL out of an item: both engines ------------------------------------

#: The tags with a ``data_url_attr`` site (a URL read out of a dict item or a list). Each name must
#: exist in the tag inventory (a rename fails ``test_every_data_tag_exists``).
DATA_TAGS = (
    "activity_feed", "avatar_group", "carousel", "chat_bubble", "data_card_grid", "dropdown_menu",
    "image_cropper", "image_upload_preview", "lightbox", "live_indicator", "mentions_input",
    "menu_item", "org_chart", "presence_avatars", "rich_select", "sortable_grid",
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
