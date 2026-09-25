"""ADR-036 P3: the published strict-policy examples execute as documented.

The Python blocks of the events guide's "Typed event parameters" section and
of the AI events reference are extracted with the doc-snippet checker's own
extractor, executed as a module, rendered with their paired HTML block through
a real GET, and driven through the real HTTP fallback with the payloads the
guide says the browser sends. Invalid events must never reach the handler.
"""

import importlib.util
import json
import pathlib
import re
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]
GUIDE = ROOT / "docs/website/core-concepts/events.md"
AI_GUIDE = ROOT / "docs/ai/events.md"
SECTION = "## Typed event parameters (strict policy)"


def _snippet_checker():
    spec = importlib.util.spec_from_file_location(
        "_doc_snippets", ROOT / "scripts/check-doc-snippets.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _examples(path, start_heading=None, end_heading="\n## "):
    """(python, html) pairs: each Python block with the next ```html block."""
    text = path.read_text()
    first_line = 0
    last_line = len(text.splitlines()) + 1
    if start_heading:
        start = text.index(start_heading)
        first_line = text[:start].count("\n") + 1
        end = text.find(end_heading, start + len(start_heading))
        if end != -1:
            last_line = text[:end].count("\n") + 1
    lines = text.splitlines()
    pairs = []
    for fence, code, _marker in _snippet_checker().extract_python_blocks(path):
        if not first_line <= fence <= last_line:
            continue
        # The paired template: the first ```html block after this one.
        i = fence + code.count("\n") + 1
        while i < len(lines) and lines[i].strip() != "```html":
            i += 1
        body = []
        for line in lines[i + 1 :]:
            if line.strip() == "```":
                break
            body.append(line)
        pairs.append((code, "\n".join(body)))
    return pairs


def _load(code, html, name):
    """Execute a documented block as an importable module; bind its template."""
    from djust import LiveView

    module = types.ModuleType(name)
    sys.modules[name] = module
    exec(compile(code, f"<{name}>", "exec"), module.__dict__)
    views = [
        v
        for v in vars(module).values()
        if isinstance(v, type) and issubclass(v, LiveView) and v.__module__ == name
    ]
    assert len(views) == 1, views
    view = views[0]
    # Strip template-only expressions the examples render inside a loop.
    view.template = "<div dj-root>" + re.sub(r"\{\{ item\.id \}\}", "5", html) + "</div>"
    return view


# Per documented view: (event, payload the guide says the browser sends,
# expected state after it, or None for a rejection that must not run code).
SCENARIOS = {
    "ItemSelectionView": [
        ("select_item", {"item_id": "42", "active": "true"}, {"selected_id": 42, "active": True}),
        ("select_item", {"item_id": "4x"}, None),
        ("select_item", {"item_id": "42", "other": "x"}, None),
        ("select_item", {"active": "true"}, None),
        ("select_item", {"item_id": "87"}, {"selected_id": 87, "active": False}),
    ],
    "NoteView": [
        ("search", {"value": "abc"}, {"query": "abc"}),
        ("search", {"value": "abc", "field": "q"}, None),
        (
            "save",
            {"title": "Draft", "notes": "Hello"},
            {"saved": {"title": "Draft", "notes": "Hello"}},
        ),
        ("save", {"notes": "Hello"}, None),
    ],
    "ItemView": [
        ("select_item", {"item_id": "5", "active": "true"}, {"selected_id": 5}),
        ("select_item", {"item_id": "five"}, None),
        ("search", {"value": "q"}, {"query": "q"}),
    ],
}

# The public contracts tests/js/adr036_documented_examples.test.js mounts the
# guide's HTML under; asserted here against the documented signatures.
JS_CONTRACTS = {
    "select_item": [
        ("item_id", "int", "positional_or_keyword", True),
        ("active", "bool", "positional_or_keyword", False),
    ],
    "search": [("value", "str", "positional_or_keyword", True)],
    "save": [
        ("title", "str", "positional_or_keyword", True),
        ("fields", "str", "var_keyword", False),
    ],
}

EXAMPLES = [
    *[(code, html, "guide") for code, html in _examples(GUIDE, SECTION)],
    *[(code, html, "ai") for code, html in _examples(AI_GUIDE)[:1]],
]


def test_the_documented_examples_are_found():
    names = [re.search(r"class (\w+)\(LiveView\)", code).group(1) for code, _h, _s in EXAMPLES]
    assert names == ["ItemSelectionView", "NoteView", "ItemView"]


@pytest.mark.django_db
@pytest.mark.parametrize("index", range(3))
def test_documented_example_runs_under_its_stated_policy(index):
    from django.test import RequestFactory
    from djust.tests.test_exposure_runtime import make_request

    code, html, source = EXAMPLES[index]
    view_class = _load(code, html, f"djust_doc_example_{source}_{index}")
    name = view_class.__name__

    # The documented template renders, and the page advertises every
    # documented handler as strict: the stated policy.
    initial = make_request()
    page = view_class().get(initial).content.decode()
    block = re.search(
        r'<script type="application/json" data-djust-parameter-contracts>(.*?)</script>', page, re.S
    )
    handlers = json.loads(block.group(1))["contracts"]["owners"][0]["handlers"]
    events = {event for event, _p, _s in SCENARIOS[name]}
    assert {handlers[e]["policy"] for e in events} == {"strict"}
    if source == "guide":
        for event in events:
            got = [
                (p["name"], p["type"], p["kind"], p["required"])
                for p in handlers[event]["parameters"]
            ]
            assert got == JS_CONTRACTS[event], event
    # Every documented dj-value-* argument names a declared parameter.
    for event in events:
        declared = {p["name"] for p in handlers[event]["parameters"]}
        for binding in re.findall(rf'dj-click="{event}"([^>]*)>', html):
            for arg in re.findall(r"dj-value-([a-z-]+)", binding):
                assert arg.replace("-", "_") in declared, (event, arg)

    for event, payload, expected in SCENARIOS[name]:
        request = RequestFactory().post(
            initial.path,
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_DJUST_EVENT=event,
        )
        request.user, request.session, request.tenant = initial.user, initial.session, None
        view = view_class()
        response = view.post(request)
        if expected is None:
            # 400 is the validation response: dispatch refused before the call.
            assert response.status_code == 400, (event, payload, response.content)
            assert b"4x" not in response.content and b"five" not in response.content
        else:
            assert response.status_code == 200, (event, payload, response.content)
            for attribute, value in expected.items():
                assert getattr(view, attribute) == value, (event, attribute)
