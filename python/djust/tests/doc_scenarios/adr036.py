"""ADR-036 P3 scenarios: strict-policy examples run under their stated policy."""

import json
import re

from django.test import RequestFactory

from djust.tests import _doc_examples as H

from . import scenario

# Per documented view: (event, payload the guide says the browser sends,
# expected state after it, or None for a rejection that must not run code).
PAYLOADS = {
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


@scenario("strict-policy")
def strict_policy(example):
    from djust.tests.test_exposure_runtime import make_request

    # Strip template-only expressions the examples render inside a loop.
    html = "<div dj-root>" + re.sub(r"\{\{ item\.id \}\}", "5", example.template) + "</div>"
    view_class = H.load(example, template=html)
    name = view_class.__name__

    # The documented template renders, and the page advertises every
    # documented handler as strict: the stated policy.
    initial = make_request()
    page = view_class().get(initial).content.decode()
    block = re.search(
        r'<script type="application/json" data-djust-parameter-contracts>(.*?)</script>', page, re.S
    )
    handlers = json.loads(block.group(1))["contracts"]["owners"][0]["handlers"]
    events = {event for event, _p, _s in PAYLOADS[name]}
    assert {handlers[e]["policy"] for e in events} == {"strict"}
    if example.path.endswith("core-concepts/events.md"):
        for event in events:
            got = [
                (p["name"], p["type"], p["kind"], p["required"])
                for p in handlers[event]["parameters"]
            ]
            assert got == JS_CONTRACTS[event], event
    # Every documented dj-value-* argument names a declared parameter.
    for event in events:
        declared = {p["name"] for p in handlers[event]["parameters"]}
        for binding in re.findall(rf'dj-click="{event}"([^>]*)>', example.template):
            for arg in re.findall(r"dj-value-([a-z-]+)", binding):
                assert arg.replace("-", "_") in declared, (event, arg)

    for event, payload, expected in PAYLOADS[name]:
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
