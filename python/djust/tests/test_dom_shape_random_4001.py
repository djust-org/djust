"""Randomized pages and edits for the incremental DOM shape (#4001).

The client keeps each element's piece of the shape and combines the pieces
(``domShape`` in ``12-vdom-patch.js``), so after an edit it rehashes only what
the edit touched. The value must stay byte-identical to the server's
(:func:`djust._render_digest.dom_shape`), which hashes the whole text in one
pass. ``tests/js/fixtures/dom_shape_random_4001.json`` holds seeded random
pages, each with steps of DOM edits and the shape the server computes for the
page after each step; ``tests/js/dom_shape_incremental_4001.test.js`` applies
the same edits to the browser's DOM and checks the client's shape, warm and
cold, against it.

This side checks that the fixture is what the generator makes and that every
shape in it is the server's. Rewrite the fixture after a change to the
generator with ``DJUST_WRITE_DOM_SHAPE_RANDOM_FIXTURE=1``.
"""

from __future__ import annotations

import copy
import html
import json
import os
import random
from pathlib import Path
from typing import Any, Dict, List

from djust import _render_digest

FIXTURE = (
    Path(__file__).resolve().parents[3] / "tests" / "js" / "fixtures" / "dom_shape_random_4001.json"
)
SEED = 4001
PAGES = 40
STEPS = 6

TAGS = ("div", "span", "section", "b", "em", "code", "pre", "template")
CHARS = list("abcZ    \t\n&<>\"'") + ["é", "漢", "\U0001f600"]
ATTRS = ("id", "title", "data-key", "class", "aria-label", "dj-id", "data-djust-x", "dj-click")


def _text(rng: random.Random) -> str:
    text = "".join(rng.choice(CHARS) for _ in range(rng.randint(1, 8)))
    # A newline right after <pre> is dropped by the parser: no text starts with one.
    return text.lstrip("\n") or "x"


def _attrs(rng: random.Random) -> List[List[str]]:
    names = rng.sample(ATTRS, rng.randint(0, 3))
    attrs = [[name, _text(rng)] for name in names]
    roll = rng.random()
    # (No data-djust-original-text: the client writes it, never the server.)
    if roll < 0.08:
        attrs.append(["dj-update", "ignore"])
    elif roll < 0.12:
        attrs.append(["name", "csrfmiddlewaretoken"])
        attrs.append(["value", _text(rng)])
    return attrs


def _element(rng: random.Random, depth: int) -> Dict[str, Any]:
    tag = rng.choice(TAGS if depth < 4 else ("span", "b", "em"))
    children: List[Any] = []
    for _ in range(rng.randint(0, 4 if depth < 3 else 2)):
        roll = rng.random()
        if roll < 0.45 and depth < 5:
            children.append(_element(rng, depth + 1))
        elif roll < 0.85:
            if not (children and isinstance(children[-1], str)):
                children.append(_text(rng))
        elif roll < 0.92:
            children.append({"comment": rng.choice(['dj-if id="if-1-0"', "/dj-if"])})
        else:
            children.append({"comment": " note "})
    return {"tag": tag, "attrs": _attrs(rng), "children": children}


def render(node: Any) -> str:
    if isinstance(node, str):
        return html.escape(node, quote=False)
    if "comment" in node:
        return "<!--%s-->" % node["comment"]
    attrs = "".join(' %s="%s"' % (n, html.escape(v, quote=True)) for n, v in node["attrs"])
    inner = "".join(render(child) for child in node["children"])
    return "<%s%s>%s</%s>" % (node["tag"], attrs, inner, node["tag"])


def _elements(node: Dict[str, Any], path: List[int], out: List[Any]) -> None:
    """Every element under ``node`` with its path of element-child indices,
    outside template content (the browser keeps that in another tree)."""
    if node["tag"] == "template":
        return
    index = 0
    for child in node["children"]:
        if isinstance(child, dict) and "tag" in child:
            out.append((path + [index], child, node))
            _elements(child, path + [index], out)
            index += 1


def _node_index(parent: Dict[str, Any], child: Dict[str, Any]) -> int:
    return next(i for i, c in enumerate(parent["children"]) if c is child)


def _removable(parent: Dict[str, Any], child: Dict[str, Any]) -> bool:
    """Removing ``child`` must not leave two text nodes side by side: the DOM
    keeps them apart, a fresh render of the same model would merge them."""
    i = _node_index(parent, child)
    siblings = parent["children"]
    before = i > 0 and isinstance(siblings[i - 1], str)
    after = i + 1 < len(siblings) and isinstance(siblings[i + 1], str)
    return not (before and after)


def _edit(rng: random.Random, root: Dict[str, Any]) -> Dict[str, Any] | None:
    found: List[Any] = []
    _elements(root, [], found)
    kind = rng.choice(("attr", "attr", "text", "insert", "remove", "move", "detach_attr"))
    if kind == "insert" or not found:
        targets = [(p, el) for p, el, _ in found if el["tag"] != "template"] + [([], root)]
        path, parent = rng.choice(targets)
        index = rng.randint(0, len(parent["children"]))
        new = _element(rng, 3)
        parent["children"].insert(index, new)
        return {"op": "insert", "path": path, "index": index, "html": render(new)}
    path, el, parent = rng.choice(found)
    if kind == "attr":
        name = rng.choice(ATTRS + ("dj-update",))
        value = None if rng.random() < 0.3 else ("ignore" if name == "dj-update" else _text(rng))
        el["attrs"] = [a for a in el["attrs"] if a[0] != name]
        if value is not None:
            el["attrs"].append([name, value])
        return {"op": "attr", "path": path, "name": name, "value": value}
    if kind == "text":
        texts = [i for i, c in enumerate(el["children"]) if isinstance(c, str)]
        if not texts or el["tag"] == "template":
            return None
        i = rng.choice(texts)
        el["children"][i] = rng.choice([_text(rng), " ", "  \t "])
        return {"op": "text", "path": path, "index": i, "value": el["children"][i]}
    if not _removable(parent, el):
        return None
    if kind == "remove":
        parent["children"].remove(el)
        return {"op": "remove", "path": path}
    if kind == "detach_attr":
        # Taken out, changed while detached, put back where it was.
        name, value = rng.choice(ATTRS), _text(rng)
        el["attrs"] = [a for a in el["attrs"] if a[0] != name] + [[name, value]]
        return {"op": "detach_attr", "path": path, "name": name, "value": value}
    # move: out of its parent (its subtree goes with it), into another place.
    parent["children"].remove(el)
    rest: List[Any] = []
    _elements(root, [], rest)
    targets = [(p, e) for p, e, _ in rest if e["tag"] != "template"] + [([], root)]
    to_path, to = rng.choice(targets)
    index = rng.randint(0, len(to["children"]))
    to["children"].insert(index, el)
    return {"op": "move", "path": path, "to": to_path, "index": index}


def _root_html(root: Dict[str, Any]) -> str:
    return '<div dj-root="">%s</div>' % "".join(render(c) for c in root["children"])


def generate() -> Dict[str, Any]:
    rng = random.Random(SEED)
    pages = []
    for n in range(PAGES):
        root = {"tag": "div", "attrs": [], "children": []}
        for _ in range(rng.randint(1, 5)):
            child = _element(rng, 1) if rng.random() < 0.8 else _text(rng)
            if not (
                isinstance(child, str)
                and root["children"]
                and isinstance(root["children"][-1], str)
            ):
                root["children"].append(child)
        page_html = _root_html(root)
        steps = []
        for _ in range(STEPS):
            ops = []
            for _ in range(rng.randint(1, 3)):
                op = _edit(rng, root)
                if op is not None:
                    ops.append(op)
            steps.append({"ops": ops, "shape": _render_digest.dom_shape(_root_html(root))})
        pages.append(
            {
                "name": "page-%02d" % n,
                "html": page_html,
                "shape": _render_digest.dom_shape(page_html),
                "steps": steps,
                "final_html": _root_html(copy.deepcopy(root)),
            }
        )
    return {
        "_about": (
            "Generated by python/djust/tests/test_dom_shape_random_4001.py (seed %d). "
            "Each page's html, then steps of DOM edits with the server's dom_shape of "
            "the page after each step. tests/js/dom_shape_incremental_4001.test.js "
            "applies the edits to the DOM and checks the client's domShape against it." % SEED
        ),
        "pages": pages,
    }


def test_the_fixture_is_what_the_generator_makes():
    generated = generate()
    if os.environ.get("DJUST_WRITE_DOM_SHAPE_RANDOM_FIXTURE") == "1":
        FIXTURE.write_text(
            json.dumps(generated, ensure_ascii=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
    assert json.loads(FIXTURE.read_text(encoding="utf-8")) == generated


def test_every_shape_in_the_fixture_is_the_servers():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert len(fixture["pages"]) == PAGES
    ops = set()
    for page in fixture["pages"]:
        assert _render_digest.dom_shape(page["html"]) == page["shape"]
        assert page["shape"] is not None
        assert _render_digest.dom_shape(page["final_html"]) == page["steps"][-1]["shape"]
        for step in page["steps"]:
            assert step["shape"] is not None
            ops.update(op["op"] for op in step["ops"])
    assert ops == {"attr", "text", "insert", "remove", "move", "detach_attr"}
