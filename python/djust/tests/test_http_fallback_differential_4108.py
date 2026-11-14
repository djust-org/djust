"""The page-POST fallback and the WebSocket end in the same DOM (#4108).

The same event sequence is driven over a real ``WebsocketCommunicator`` and
over the page GET plus fallback POSTs (``http_fallback_matrix``). The server's
final render is compared here; the recorded answers are also written to
``tests/js/fixtures/http_fallback_replay_4108.json``, which
``tests/js/http_fallback_replay_4108.test.js`` replays with the built client
and compares, node by node, with the same expected DOM.

The fixture is generated, never edited. Regenerate it with::

    DJUST_WRITE_HTTP_FALLBACK_FIXTURE=1 .venv/bin/python -m pytest \\
        python/djust/tests/test_http_fallback_differential_4108.py

and ``test_committed_fixture_is_current`` fails when the committed copy is not
what the server answers now.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest
from asgiref.sync import sync_to_async
from django.test import override_settings

from djust.tests import http_fallback_matrix as matrix

pytestmark = [pytest.mark.django_db(transaction=True)]

FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "tests"
    / "js"
    / "fixtures"
    / "http_fallback_replay_4108.json"
)
WRITE_ENV = "DJUST_WRITE_HTTP_FALLBACK_FIXTURE"

SETTINGS = {
    "ROOT_URLCONF": matrix.__name__,
    "LIVEVIEW_ALLOWED_MODULES": ["djust"],
    "ALLOWED_HOSTS": ["*"],
    "DEBUG": False,
}

#: What each fallback answer is, per scenario: patches where the pre-handler
#: render is the page the client shows, the whole page otherwise.
EXPECTED_KINDS = {
    "counter": ["patches", "patches", "patches"],
    # Rows added and removed, an ``{% if %}`` block appearing and disappearing.
    "list": ["patches", "patches", "patches", "patches", "patches"],
    # The form's input and errors are the tab's saved state (#4107, #4109).
    "form": ["patches", "patches", "patches"],
    "paged": ["patches", "patches"],
    # ``temporary_assigns``: a render consumes the rows, so there is no pre-render.
    "feed": ["html", "html"],
    # A different value on every render: the digest never matches.
    "unstable": ["html", "html"],
    # A plain attribute the handler set is the tab's saved state (#4107).
    "volatile": ["patches", "patches", "patches"],
    "stamped": ["html", "html"],
    "leaving": ["patches", "patches", "patches"],
    "hooked": ["patches", "patches", "patches"],
}

#: Diff renders per fallback request: the pre-handler baseline and the render
#: after the handler, or that one alone when no baseline is rendered.
EXPECTED_RENDERS = {
    name: [1 if name == "feed" else 2] * len(kinds) for name, kinds in EXPECTED_KINDS.items()
}

#: Scenarios whose final DOM over the fallback differs from the socket's, with
#: the reason. Empty since the tab's state is saved (#4107): the form and the
#: plain attribute that used to be listed here are kept (#4109).
KNOWN_DIFFERENT: dict[str, str] = {}

#: The server renders only the new rows; the client keeps the earlier ones
#: (``dj-update="append"``). Equality holds in the browser, not between two
#: server renders, so only the jsdom replay compares these.
CLIENT_ACCUMULATED = {"feed"}

#: Per-render values, replaced before comparing two renders.
_MASKS = (
    (re.compile(r'(<p class="serial">render )\d+'), r"\1N"),
    (re.compile(r'(<p class="stamp">)\d+'), r"\1N"),
)


def _masked(html: str | None) -> str | None:
    html = matrix.normalize(html)
    if html is None:
        return None
    for pattern, replacement in _MASKS:
        html = pattern.sub(replacement, html)
    return html


_CSRF_META = re.compile(r'(<meta name="djust-csrf-token" content=")[^"]*(">)')


_TAB_META = re.compile(r'(<meta name="djust-tab" content=")[^"]*(">)')


def _stable_document(document: str) -> str:
    """The GET document with the per-response CSRF token and the per-page-load
    tab id (#4106) replaced."""
    document = _TAB_META.sub(r"\1tab-fixture-0001\2", document)
    return _CSRF_META.sub(r"\1CSRF\2", document)


def _stable_digests(record: dict) -> dict:
    """Digests are bound to a random session: rename them by order of appearance."""
    names: dict[str, str] = {}

    def name(value):
        if not value:
            return value
        # "<digest>.<shape>": the shape is a checksum of the render's tree and
        # is the same in every run, so it stays.
        digest, _, shape = value.partition(".")
        return names.setdefault(digest, "digest-%d" % (len(names) + 1)) + "." + shape

    document = record["get"]
    if record["get_digest"]:
        document = document.replace(record["get_digest"], name(record["get_digest"]))
    answers = []
    for sent, answer in zip(record["sent_digests"], record["answers"]):
        name(sent)
        answer = dict(answer)
        if answer.get("render_digest"):
            answer["render_digest"] = name(answer["render_digest"])
        answers.append(answer)
    return {
        "get": document,
        "sent_digests": [name(sent) for sent in record["sent_digests"]],
        "answers": answers,
    }


async def _scenario(name: str, monkeypatch) -> dict:
    url, events = matrix.SCENARIOS[name]
    view_path = "%s.%s" % (matrix.__name__, matrix.VIEWS[name].__name__)
    log = matrix.RenderLog(monkeypatch)
    matrix.reset_serial()
    fallback = await sync_to_async(matrix.run_fallback)(url, events, log)
    # The same events from a client that sends no token: whole pages. The
    # replay answers with these when the built client withholds its token.
    matrix.reset_serial()
    whole = await sync_to_async(lambda: matrix.run_fallback(url, events, log, echo=False))()
    socket = await matrix.run_websocket(url, view_path, events)
    stable = _stable_digests(fallback)
    equal_to_socket = name not in KNOWN_DIFFERENT
    return {
        "url": url,
        "events": fallback["events"],
        "get": _stable_document(stable["get"]),
        "sent_digests": stable["sent_digests"],
        "answers": stable["answers"],
        "answers_whole": [
            {k: v for k, v in answer.items() if k != "render_digest"} for answer in whole["answers"]
        ],
        "kinds": fallback["kinds"],
        "renders": fallback["renders"],
        "fallback_final": _masked(fallback["final"]),
        "socket_final": _masked(socket["final"]),
        # What the browser must show after the last answer.
        "expected_dom": _masked(socket["final"] if equal_to_socket else fallback["final"]),
        "equal_to_socket": equal_to_socket,
    }


@pytest.fixture
async def recorded(monkeypatch) -> dict:
    with override_settings(**SETTINGS):
        return {name: await _scenario(name, monkeypatch) for name in matrix.SCENARIOS}


@pytest.mark.asyncio
async def test_fallback_and_socket_end_in_the_same_dom(recorded):
    for name, record in recorded.items():
        if name in CLIENT_ACCUMULATED:
            continue
        if name in KNOWN_DIFFERENT:
            # Pinned so that a change which closes the gap is noticed here.
            assert record["fallback_final"] != record["socket_final"], (name, KNOWN_DIFFERENT[name])
        else:
            assert record["fallback_final"] == record["socket_final"], name


@pytest.mark.asyncio
async def test_each_answer_is_the_expected_kind(recorded):
    assert {name: record["kinds"] for name, record in recorded.items()} == EXPECTED_KINDS
    assert {name: record["renders"] for name, record in recorded.items()} == EXPECTED_RENDERS


@pytest.mark.asyncio
async def test_a_dropped_state_field_turns_the_differential_red(monkeypatch):
    """Gate-off: with the declared state not restored, the transports disagree."""
    from djust import _exposure_sessions

    monkeypatch.setattr(_exposure_sessions, "load_server_state", lambda view, request: None)
    # The fallback reads its saved state through ``load_saved_view`` (#4107).
    monkeypatch.setattr(
        _exposure_sessions,
        "load_saved_view",
        lambda view, request: _exposure_sessions.SavedView({}, {}, {}, True),
    )
    with override_settings(**SETTINGS):
        record = await _scenario("counter", monkeypatch)
    assert record["fallback_final"] != record["socket_final"]


@pytest.mark.asyncio
async def test_committed_fixture_is_current(recorded):
    """The jsdom replay runs on what the server answers now."""
    document = {
        "_generated_by": "python/djust/tests/test_http_fallback_differential_4108.py",
        # Elements whose text differs on every render: the replay sets them to
        # the same placeholder ``_masked`` leaves in ``expected_dom``.
        "masks": [
            {"selector": "p.serial", "text": "render N"},
            {"selector": "p.stamp", "text": "N"},
        ],
        "scenarios": recorded,
    }
    text = json.dumps(document, indent=1, sort_keys=True) + "\n"
    if os.environ.get(WRITE_ENV):
        FIXTURE.write_text(text, encoding="utf-8")
    assert FIXTURE.exists(), "generate the fixture: see this module's docstring"
    assert json.loads(FIXTURE.read_text(encoding="utf-8")) == json.loads(text), (
        "tests/js/fixtures/http_fallback_replay_4108.json is stale: regenerate it "
        "(see this module's docstring) and run tests/js/http_fallback_replay_4108.test.js"
    )
