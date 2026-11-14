"""What a view sets on ``self`` survives; what reaches the client stays explicit (#4107).

The central test is a differential: the same events over a real
``WebsocketCommunicator`` (then a reconnect on a new consumer), over the SSE
endpoints, and over a GET plus stateless fallback POSTs end in the same state.

The rest pin the rules of ``djust._automatic_state``: which names and values
are saved, what never enters the store, what a stored envelope may put back,
when stored state is discarded (other code, other principal, a page load), the
size behaviour, the skipped write, and that nothing saved becomes visible to
the client.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from decimal import Decimal
from typing import Any, Dict, List

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth.models import AnonymousUser, User
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory

from djust import LiveView, _automatic_state, _per_turn, event_handler  # noqa: F401
from djust.decorators import state
from djust.tests import _per_turn_harness as harness

MODULE = __name__
SECRET = "s3cr3t-never-shown-4107"


def _probe(view: Any, *names: str) -> str:
    return json.dumps({name: getattr(view, name) for name in names}, sort_keys=True)


class _Base(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root><pre>{{ probe }}</pre></div>"
    shown: tuple = ()

    def get_context_data(self, **kwargs):
        return {"probe": _probe(self, *self.shown)}


class Counter(_Base):
    shown = ("count",)

    def mount(self, request, **kwargs):
        self.count = 0
        self.hidden_note = SECRET  # saved, never rendered

    @event_handler()
    def inc(self, **kwargs):
        self.count += 1

    @event_handler()
    def nothing(self, **kwargs):
        pass


class EditedList(_Base):
    shown = ("items",)

    def mount(self, request, **kwargs):
        self.items = ["a"]

    @event_handler()
    def add(self, value: str = "x", **kwargs):
        self.items.append(value)

    @event_handler()
    def drop_first(self, **kwargs):
        del self.items[0]


class Nested(_Base):
    shown = ("board",)

    def mount(self, request, **kwargs):
        self.board = {"todo": [{"id": 1, "tags": []}], "done": []}

    @event_handler()
    def tag(self, value: str = "t", **kwargs):
        self.board["todo"][0]["tags"].append(value)

    @event_handler()
    def finish(self, **kwargs):
        self.board["done"].append(self.board["todo"].pop())


class FormIsh(_Base):
    shown = ("name", "errors", "submitted")

    def mount(self, request, **kwargs):
        self.name = ""
        self.errors = {}
        self.submitted = False

    @event_handler()
    def set_name(self, value: str = "", **kwargs):
        self.name = value
        self.errors = {} if value else {"name": ["required"]}

    @event_handler()
    def submit(self, **kwargs):
        self.submitted = not self.errors and bool(self.name)


class Rows(_Base):
    """A queryset (never saved) and data derived from saved state."""

    shown = ("page", "names")

    def _load(self):
        self.rows = User.objects.filter(username__startswith="auto-4107-").order_by("username")
        self.names = [user.username for user in self.rows[self.page : self.page + 1]]

    def mount(self, request, **kwargs):
        self.page = 0
        self._load()

    def handle_restore(self, request):
        self._load()

    @event_handler()
    def next_page(self, **kwargs):
        self.page += 1
        self._load()


class Unserializable(_Base):
    shown = ("count",)

    def mount(self, request, **kwargs):
        self.count = 0
        self.price = Decimal("1.50")
        self.viewer = request.user
        self.mixed = [1, Decimal("2")]

    @event_handler()
    def inc(self, **kwargs):
        self.count += 1


class Declared(_Base):
    """``state()`` with no ``persist`` is kept; ``persist=False`` is not."""

    shown = ("kept", "scratch")
    kept = state(0)
    scratch = state(0, persist=False)

    @event_handler()
    def inc(self, **kwargs):
        self.kept += 1
        self.scratch += 1


class LegacyCounter(LiveView):
    exposure_policy = "legacy"
    template = "<div dj-root><pre>{{ count }}</pre></div>"

    def mount(self, request, **kwargs):
        self.count = 0

    @event_handler()
    def inc(self, **kwargs):
        self.count += 1


CASES = [
    (Counter, [("inc",), ("nothing",), ("inc",)], {"count": 2}),
    (
        EditedList,
        [("add", {"value": "b"}), ("drop_first",), ("add", {"value": "c"})],
        {"items": ["b", "c"]},
    ),
    (
        Nested,
        [("tag", {"value": "x"}), ("finish",)],
        {"board": {"todo": [], "done": [{"id": 1, "tags": ["x"]}]}},
    ),
    (
        FormIsh,
        [("set_name", {"value": ""}), ("set_name", {"value": "ann"}), ("submit",)],
        {"name": "ann", "errors": {}, "submitted": True},
    ),
    (Rows, [("next_page",), ("next_page",)], {"page": 2, "names": ["auto-4107-c"]}),
    (Unserializable, [("inc",), ("inc",)], {"count": 2}),
    (Declared, [("inc",), ("inc",)], {"kept": 2, "scratch": 2}),
]
IDS = [case[0].__name__ for case in CASES]


def _users() -> None:
    for name in ("a", "b", "c"):
        User.objects.get_or_create(username=f"auto-4107-{name}")


def _probe_of(markup: str) -> Dict[str, Any]:
    import html
    import re

    found = re.search(r"<pre[^>]*>(.*?)</pre>", markup, re.S)
    assert found, markup[:400]
    return json.loads(html.unescape(found.group(1)))


@pytest.fixture(autouse=True)
def _one_tab(monkeypatch):
    """Every page load in this module is the same tab unless a test says
    otherwise: the GET mints the id the harness's client sends back."""
    monkeypatch.setattr(_automatic_state, "new_tab_id", lambda: harness.TAB)


def _request(
    method: str,
    key: str | None,
    event: str = "",
    params: Any = None,
    path=harness.PATH,
    tab: str = harness.TAB,
):
    factory = RequestFactory()
    if method == "GET":
        request = factory.get(path)
    else:
        request = factory.post(
            path,
            data=json.dumps(params or {}),
            content_type="application/json",
            HTTP_X_DJUST_EVENT=event,
            HTTP_X_DJUST_TAB=tab,
        )
    request.user = AnonymousUser()
    request.tenant = None
    request.session = SessionStore(key)
    if key is None:
        request.session.create()
    return request


def _fallback(view_class: type, script: List[tuple], key: str | None = None):
    """GET, then one stateless POST per event, each on a new view and request."""
    first = _request("GET", key)
    assert view_class().get(first).status_code == 200
    key = first.session.session_key
    view, bodies = None, []
    for step in script:
        request = _request("POST", key, step[0], step[1] if len(step) > 1 else None)
        view = view_class()
        response = view.post(request)
        assert response.status_code == 200, response.content
        bodies.append(response.content.decode())
    return view, key, bodies


def _stored(key: str) -> Dict[str, Any]:
    session = SessionStore(key)
    return {name: value for name, value in session.items() if name.startswith("_djust_auto_")}


def _stored_values(key: str) -> Dict[str, Any]:
    stored = _stored(key)
    assert len(stored) == 1, list(stored)
    return next(iter(stored.values()))["values"]


# ---------------------------------------------------------------------------
# The differential
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("view_class,script,expected", CASES, ids=IDS)
def test_fallback_posts_end_in_the_expected_state(view_class, script, expected):
    _users()
    view, _key, _bodies = _fallback(view_class, script)
    if view_class is Declared:
        # persist=False: each request starts from the default, then one event.
        expected = {"kept": 2, "scratch": 1}
    assert json.loads(view.get_context_data()["probe"]) == expected


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("view_class,script,expected", CASES, ids=IDS)
async def test_websocket_then_reconnect_continues_from_the_saved_state(
    view_class, script, expected
):
    await sync_to_async(_users)()
    key = await harness.new_session_key_async()
    with harness.ws_settings(MODULE):
        comm, _mount = await harness.mount_view(view_class, key)
        try:
            for step in script:
                frame = await harness.event(comm, step[0], step[1] if len(step) > 1 else None)
                assert frame["type"] != "error", frame
        finally:
            await comm.disconnect()
        # A new consumer on the same session: mount(), then the saved state.
        again, mount = await harness.mount_view(view_class, key)
        await again.disconnect()
    if view_class is Declared:
        expected = {"kept": 2, "scratch": 0}  # persist=False is mount()'s value again
    assert _probe_of(mount["html"]) == expected


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("view_class,script,expected", CASES, ids=IDS)
async def test_sse_events_then_a_fallback_post_see_the_same_state(view_class, script, expected):
    """SSE runs the runtime's commit; a later stateless POST reads what it saved."""
    from django.contrib.auth import get_user
    from django.utils.functional import SimpleLazyObject

    from djust.sse import DjustSSEMessageView, DjustSSEStreamView, _sse_sessions

    await sync_to_async(_users)()
    url = f"/auto-{view_class.__name__}/"

    def request_for(method: str, target: str, body: Any, key: str | None = None) -> Any:
        factory = RequestFactory()
        if method == "GET":
            made = factory.get(target, data=body)
        else:
            made = factory.post(target, data=json.dumps(body), content_type="application/json")
        made.session = SessionStore(key)
        if key is None:
            made.session.create()
        made.session = SessionStore(made.session.session_key)
        made.user = SimpleLazyObject(lambda: get_user(made))
        made.tenant = None
        return made

    sid = str(uuid.uuid4())
    with harness.ws_settings(MODULE, ROOT_URLCONF=MODULE):
        first = await sync_to_async(request_for)(
            "GET",
            f"/djust/sse/{sid}/",
            {
                "view": f"{MODULE}.{view_class.__name__}",
                "_djust_url": url,
                "_djust_tab": harness.TAB,
            },
        )
        response = await DjustSSEStreamView().get(first, session_id=sid)
        assert response.status_code == 200
        key = first.session.session_key
        try:
            for step in script:
                body = {
                    "type": "event",
                    "event": step[0],
                    "params": step[1] if len(step) > 1 else {},
                }
                posted = await sync_to_async(request_for)(
                    "POST", f"/djust/sse/{sid}/message/", body, key
                )
                answer = await DjustSSEMessageView().post(posted, session_id=sid)
                assert answer.status_code in (200, 202, 204), answer.content
        finally:
            _sse_sessions.pop(sid, None)

        def read_back() -> Any:
            request = _request("POST", key, "noop_probe", path=url)
            view = view_class()
            view.post(request)  # unknown event: refused after the restore
            return json.loads(view.get_context_data()["probe"])

        seen = await sync_to_async(read_back)()
    if view_class is Declared:
        expected = {"kept": 2, "scratch": 0}
    assert seen == expected


from django.urls import path as _path  # noqa: E402

urlpatterns = [_path(f"auto-{case[0].__name__}/", case[0].as_view()) for case in CASES]


# ---------------------------------------------------------------------------
# What is saved, and what is not
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_context_processor_values_are_not_stored(settings):
    """#1786: a processor's output is context, never an attribute, never stored."""
    _view, key, _ = _fallback(Counter, [("inc",)])
    assert set(_stored_values(key)) == {"count", "hidden_note"}


@pytest.mark.django_db
def test_form_mixin_never_stores_a_sensitive_field():
    from django import forms

    from djust.forms import FormMixin

    class Login(forms.Form):
        username = forms.CharField()
        password = forms.CharField(widget=forms.PasswordInput)

    class LoginView(FormMixin, LiveView):
        exposure_policy = "explicit"
        form_class = Login
        template = "<div dj-root>{{ form_data.username }}</div>"

        @event_handler()
        def fill(self, **kwargs):
            self.form_data["username"] = "ann"
            self.form_data["password"] = "hunter2-4107"
            self.field_errors = {"username": ["hunter2-4107 is not a name"]}

    view, key, bodies = _fallback(LoginView, [("fill",), ("fill",)])
    stored = _stored_values(key)
    assert stored["form_data"] == {"username": "ann", "password": ""}
    assert "hunter2-4107" not in json.dumps(_stored(key))
    assert "form_choices" not in stored


# ---------------------------------------------------------------------------
# Nothing saved becomes visible
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_saved_unexposed_attribute_reaches_no_frame_token_or_log(caplog):
    key = await harness.new_session_key_async()
    frames: List[Any] = []
    with caplog.at_level(logging.DEBUG, logger="djust"), harness.ws_settings(MODULE):
        comm, mount = await harness.mount_view(Counter, key)
        frames.append(mount)
        frames += await harness.event_frames(comm, "inc")
        await comm.disconnect()
        again, mount = await harness.mount_view(Counter, key)  # restored
        frames.append(mount)
        frames += await harness.event_frames(again, "inc")
        await again.disconnect()
    assert (await sync_to_async(_stored_values)(key))["hidden_note"] == SECRET
    wire = json.dumps(frames, default=str)
    assert SECRET not in wire and "hidden_note" not in wire
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert SECRET not in logged


@pytest.mark.django_db
def test_a_saved_unexposed_attribute_reaches_no_fallback_answer():
    first = _request("GET", None)
    page = Counter().get(first)
    key = first.session.session_key
    bodies = [page.content.decode()]
    for event in ("inc", "inc", "no_such_handler"):
        response = Counter().post(_request("POST", key, event))
        bodies.append(response.content.decode())
    assert _stored_values(key)["hidden_note"] == SECRET
    assert SECRET not in "".join(bodies) and "hidden_note" not in "".join(bodies)


# ---------------------------------------------------------------------------
# Restore validation: the store cannot name what the view would not save
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Binding, page loads, deploys
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_state_is_bound_to_the_user_and_the_route():
    _view, key, _ = _fallback(Counter, [("inc",), ("inc",)])
    same = Counter()
    same.post(_request("POST", key, "nothing"))
    assert same.count == 2
    elsewhere = Counter()
    elsewhere.post(_request("POST", key, "nothing", path="/another-route/"))
    assert elsewhere.count == 0
    # Another principal presenting the same session key reads nothing.
    user = User.objects.create(username="auto-4107-other")
    other = _request("POST", key, "nothing")
    other.user = user
    view = Counter()
    view.post(other)
    assert view.count == 0


# ---------------------------------------------------------------------------
# Size, cost, capture
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_refused_declared_save_leaves_the_automatic_state_unstaged():
    from djust._exposure_sessions import StateLimitExceeded, save_server_state

    class Mixed(_Base):
        shown = ("count",)
        blob = state("", persist="server")

        def mount(self, request, **kwargs):
            self.count = 0

    request = _request("POST", None, "x")
    view = Mixed()
    view.mount(request)
    view.count = 5
    view.blob = "x" * 70000
    with pytest.raises(StateLimitExceeded):
        save_server_state(view, request)
    assert not [k for k in request.session.keys() if k.startswith("_djust_auto_")]


def test_bookkeeping_is_framework_state_not_application_state():
    view = Counter()
    assert {
        "_djust_auto_basis",
        "_djust_auto_bytes",
        "_djust_auto_changed",
    } <= view._framework_attrs


# ---------------------------------------------------------------------------
# Framework attributes; the clear/stats helpers (#4087)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_saved_state_can_be_listed_measured_and_cleared_per_session():
    _view, key, _ = _fallback(Counter, [("inc",)])
    session = SessionStore(key)
    session["cart"] = ["kept"]
    session.save()
    session = SessionStore(key)
    stats = _automatic_state.saved_state_stats(session)
    assert stats["entries"] == len(_automatic_state.saved_state_keys(session)) >= 1
    assert stats["bytes"] > 0
    assert [s.session_key for s in _automatic_state.database_sessions_with_saved_state()] == [key]
    assert _automatic_state.clear_saved_state(session) == stats["entries"]
    after = SessionStore(key)
    assert not _automatic_state.saved_state_keys(after) and after["cart"] == ["kept"]
    assert not list(_automatic_state.database_sessions_with_saved_state())
    view = Counter()
    view.post(_request("POST", key, "nothing"))
    assert view.count == 0


# ---------------------------------------------------------------------------
# What is reused from change detection (the turn's changed keys)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Every plain Python value comes back with its type
# ---------------------------------------------------------------------------

import datetime as dt  # noqa: E402
import enum  # noqa: E402
import uuid as uuid_module  # noqa: E402
import zoneinfo  # noqa: E402

from djust.serialization import (  # noqa: E402
    StateValueError,
    decode_state_roundtrip,
    encode_state_value,
)


class Colour(enum.Enum):
    RED = "r"
    GREEN = "g"


class Level(enum.IntEnum):
    LOW = 1
    HIGH = 2


_SCALARS = [
    None,
    True,
    False,
    0,
    -1,
    2**53,
    -(2**70),
    1.5,
    float("inf"),
    float("-inf"),
    "",
    "text \u00e9 \U0001f600",
    Decimal("1.50"),
    Decimal("-0"),
    Decimal("Infinity"),
    Decimal("1E+30"),
    dt.date.min,
    dt.date.max,
    dt.date(2026, 2, 28),
    dt.datetime(2026, 11, 1, 1, 30, fold=1),
    dt.datetime(2026, 3, 8, 12, 0, 0, 123456, tzinfo=dt.timezone.utc),
    dt.datetime(2026, 11, 1, 1, 30, tzinfo=zoneinfo.ZoneInfo("America/New_York"), fold=1),
    dt.datetime(2026, 6, 1, 9, tzinfo=zoneinfo.ZoneInfo("Asia/Kolkata")),
    dt.datetime(2026, 6, 1, 9, tzinfo=dt.timezone(dt.timedelta(hours=-3, minutes=-30))),
    dt.datetime.min,
    dt.time(23, 59, 59, 999999),
    dt.time(8, 0, tzinfo=dt.timezone(dt.timedelta(hours=2))),
    dt.timedelta(days=-1, seconds=5, microseconds=7),
    dt.timedelta.max,
    uuid_module.UUID("12345678-1234-5678-1234-567812345678"),
    b"",
    b"\x00\xff bytes",
    Colour.RED,
    Level.HIGH,
]
_CONTAINERS = [
    lambda v: [v],
    lambda v: (v,),
    lambda v: {"key": v},
    lambda v: [(), [], {}, (v, [v, {"deep": (v,)}])],
    lambda v: {"__djust_value__": v},  # a user dict that looks like a tag
    lambda v: {"__djust_decimal__": "not a number", "other": v},
]
_HASHABLE_CONTAINERS = [
    lambda v: {v},
    lambda v: frozenset({v, "other"}),
    lambda v: {v: "by key", 2: v},
    lambda v: {(v, 1): [v]},
    lambda v: {(v, "a"), (v, "b")},  # a set of tuples
]
_ENUMS = frozenset({Colour, Level})


def _matrix() -> List[Any]:
    values: List[Any] = list(_SCALARS) + [(), [], {}, set(), frozenset(), Decimal("NaN")]
    for scalar in _SCALARS:
        values += [build(scalar) for build in _CONTAINERS]
        values += [build(scalar) for build in _HASHABLE_CONTAINERS]
    return values


def _same(a: Any, b: Any) -> bool:
    """Equal in type and value at every level (NaN equals NaN here)."""
    if type(a) is not type(b):
        return False
    if isinstance(a, (list, tuple)):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    if isinstance(a, dict):
        if len(a) != len(b):
            return False
        return all(any(_same(k, j) and _same(v, w) for j, w in b.items()) for k, v in a.items())
    if isinstance(a, (set, frozenset)):
        return len(a) == len(b) and all(any(_same(x, y) for y in b) for x in a)
    if isinstance(a, Decimal):
        return str(a) == str(b)
    if isinstance(a, float) and a != a:
        return b != b
    if isinstance(a, (dt.datetime, dt.time)):
        return (
            a.replace(tzinfo=None) == b.replace(tzinfo=None)
            and a.fold == b.fold
            and type(a.tzinfo) is type(b.tzinfo)
            and a.utcoffset() == b.utcoffset()
            and str(a.tzinfo) == str(b.tzinfo)
        )
    return bool(a == b)


def test_every_plain_value_round_trips_through_json_with_its_type():
    checked = 0
    for value in _matrix():
        wire = json.loads(json.dumps(encode_state_value(value, enums=_ENUMS)))
        back = decode_state_roundtrip(wire, enums=_ENUMS, strict=True)
        assert _same(value, back), (value, wire, back)
        checked += 1
    assert checked == len(_SCALARS) * (len(_CONTAINERS) + len(_HASHABLE_CONTAINERS) + 1) + 6


def test_the_comparison_used_by_the_round_trip_test_is_not_blind():
    assert not _same((), [])
    assert not _same({1}, frozenset({1}))
    assert not _same(Decimal("1.5"), Decimal("1.50"))
    assert not _same(1, True) and not _same(1, 1.0)
    assert not _same(
        dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
        dt.datetime(2026, 1, 1, tzinfo=zoneinfo.ZoneInfo("UTC")),
    )
    assert not _same(dt.datetime(2026, 11, 1, 1, 30), dt.datetime(2026, 11, 1, 1, 30, fold=1))


def test_a_set_encodes_to_the_same_bytes_whatever_its_iteration_order():
    forward = encode_state_value({("b", 2), ("a", 1), ("c", 3)})
    backward = encode_state_value(set(reversed(sorted({("b", 2), ("a", 1), ("c", 3)}))))
    assert json.dumps(forward) == json.dumps(backward)


@pytest.mark.parametrize(
    "value",
    [
        object(),
        lambda: 1,
        type("Sub", (str,), {})("subclass of str"),
        type("Rows", (list,), {})([1]),
        __import__("collections").OrderedDict(a=1),
        __import__("collections").namedtuple("P", "x")(1),
        open,
        [1, {"deep": object()}],
        dt.datetime(
            2026, 1, 1, tzinfo=type("Zone", (dt.tzinfo,), {"utcoffset": lambda s, d: None})()
        ),
    ],
    ids=lambda value: type(value).__name__,
)
def test_other_objects_are_refused_never_converted(value):
    with pytest.raises(StateValueError) as raised:
        encode_state_value(value, enums=_ENUMS)
    assert "subclass of str" not in str(raised.value)


def test_an_enum_is_saved_and_restored_only_through_classes_the_view_references():
    with pytest.raises(StateValueError):
        encode_state_value(Colour.RED)  # no classes given
    with pytest.raises(StateValueError):
        encode_state_value(Colour.RED, enums=frozenset({Level}))
    wire = encode_state_value(Colour.GREEN, enums=_ENUMS)
    with pytest.raises(StateValueError):
        decode_state_roundtrip(wire, enums=frozenset({Level}), strict=True)
    assert decode_state_roundtrip(wire) == wire  # not strict: left as it is, never imported
    assert Colour in _automatic_state.enum_classes(Typed)  # this module's global


@pytest.mark.parametrize(
    "tagged",
    [
        ["enum", ["os", "PathLike", "x"]],
        ["enum", ["builtins", "eval", "__call__"]],
        ["call", ["os", "system", "id"]],
        ["pickle", "gASV"],
        ["tuple", "not a list"],
        ["date", "2026-13-45"],
        ["bytes", "***"],
        ["datetime", ["2026-01-01T00:00:00", ["zone", "../../etc/passwd"], 0]],
        ["timedelta", [1, "2", 3]],
        "not a list",
    ],
)
def test_a_tag_restores_only_through_the_fixed_table(tagged):
    with pytest.raises(StateValueError):
        decode_state_roundtrip({"__djust_value__": tagged}, enums=_ENUMS, strict=True)


class Typed(_Base):
    """The review's reproduction: these came back as ``mount()`` defaults."""

    shown = ("label",)

    def mount(self, request, **kwargs):
        self.label = "start"
        self.day = None
        self.pair = ()
        self.tags = set()
        self.price = Decimal("0")
        self.when = None
        self.colour = Colour.RED
        self.by_id = {}

    @event_handler()
    def fill(self, **kwargs):
        self.label = "filled"
        self.day = dt.date(2026, 11, 13)
        self.pair = (1, "two")
        self.tags = {"a", "b"}
        self.price = Decimal("19.90")
        self.when = dt.datetime(2026, 11, 13, 9, 30, tzinfo=zoneinfo.ZoneInfo("Europe/Paris"))
        self.colour = Colour.GREEN
        self.by_id = {7: ("x", Decimal("1.0"))}

    @event_handler()
    def nothing(self, **kwargs):
        pass


def _typed_state(view: Any) -> Dict[str, Any]:
    return {
        name: getattr(view, name)
        for name in ("label", "day", "pair", "tags", "price", "when", "colour", "by_id")
    }


_FILLED = {
    "label": "filled",
    "day": dt.date(2026, 11, 13),
    "pair": (1, "two"),
    "tags": {"a", "b"},
    "price": Decimal("19.90"),
    "when": dt.datetime(2026, 11, 13, 9, 30, tzinfo=zoneinfo.ZoneInfo("Europe/Paris")),
    "colour": Colour.GREEN,
    "by_id": {7: ("x", Decimal("1.0"))},
}


@pytest.mark.django_db
def test_typed_values_survive_the_fallback_with_their_types():
    view, _key, _ = _fallback(Typed, [("fill",), ("nothing",)])
    assert _same(_typed_state(view), _FILLED), _typed_state(view)


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_typed_values_survive_a_reconnect_and_a_later_fallback_post():
    key = await harness.new_session_key_async()
    with harness.ws_settings(MODULE):
        comm, _mount = await harness.mount_view(Typed, key)
        assert (await harness.event(comm, "fill"))["type"] != "error"
        await comm.disconnect()
        again, _mount = await harness.mount_view(Typed, key)
        assert (await harness.event(again, "nothing"))["type"] != "error"
        await again.disconnect()

    def read_back() -> Any:
        view = Typed()
        assert view.post(_request("POST", key, "nothing")).status_code == 200
        return _typed_state(view)

    assert _same(await sync_to_async(read_back)(), _FILLED)


# ---------------------------------------------------------------------------
# Whole or refused: the size limit
# ---------------------------------------------------------------------------


class Cart(_Base):
    """The review's reproduction: 300 items, then 800 more."""

    shown = ("count",)
    automatic_state_max_kb = 16

    def mount(self, request, **kwargs):
        self.count = 0
        self.catalog = "c"
        self.cart = []

    @event_handler()
    def add(self, n: int = 1, **kwargs):
        self.count += 1
        self.cart = self.cart + [{"sku": "item-%04d" % i, "qty": 1} for i in range(n)]


async def _event_or_error(comm: Any, name: str, params: Any = None) -> Dict[str, Any]:
    """Send one event; the frame that answers it, or the error frame a refused
    change gets (which carries no ``ref``)."""
    from djust.tests._ws_frames import has_type, receive_until

    await comm.send_json_to({"type": "event", "event": name, "params": params or {}, "ref": 4107})
    frames = await receive_until(
        comm, has_type("error", "patch", "html_update", "noop"), what=f"the answer to {name!r}"
    )
    return frames[-1]


@pytest.mark.django_db
def test_state_over_the_limit_is_refused_whole_never_saved_in_part(caplog):
    first = _request("GET", None)
    assert Cart().get(first).status_code == 200
    key = first.session.session_key
    assert Cart().post(_request("POST", key, "add", {"n": 300})).status_code == 200
    before = _stored_values(key)
    assert len(before["cart"]) == 300 and before["count"] == 1
    with caplog.at_level(logging.WARNING, logger="djust.automatic_state"):
        refused = Cart().post(_request("POST", key, "add", {"n": 800}))
    assert refused.status_code == 413
    body = json.loads(refused.content)
    assert body["code"] == "state_too_large"
    assert "'cart'" in body["error"] and "16384-byte" in body["error"]
    assert _stored_values(key) == before  # whole, as it was: never a subset
    assert any("Cart" in r.getMessage() and "cart" in r.getMessage() for r in caplog.records)
    # The page is still usable from its last saved state.
    view = Cart()
    assert view.post(_request("POST", key, "add", {"n": 1})).status_code == 200
    assert len(view.cart) == 301 and view.count == 2


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_over_a_socket_the_change_is_refused_and_the_view_goes_back():
    key = await harness.new_session_key_async()
    with harness.ws_settings(MODULE):
        comm, _mount = await harness.mount_view(Cart, key)
        try:
            assert (await harness.event(comm, "add", {"n": 300}))["type"] != "error"
            refused = await _event_or_error(comm, "add", {"n": 800})
            assert refused["type"] == "error" and refused["code"] == "state_too_large", refused
            assert "cart" in refused["error"]
            after = await harness.event(comm, "add", {"n": 1})
            assert after["type"] != "error", after
        finally:
            await comm.disconnect()
    stored = await sync_to_async(_stored_values)(key)
    assert len(stored["cart"]) == 301 and stored["count"] == 2


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_refused_first_change_goes_back_to_mounts_state_with_nothing_stored():
    """Nothing saved yet: the refused change still has a state to go back to."""
    key = await harness.new_session_key_async()
    with harness.ws_settings(MODULE):
        comm, _mount = await harness.mount_view(Cart, key)
        try:
            refused = await _event_or_error(comm, "add", {"n": 2000})
            assert refused["type"] == "error" and refused["code"] == "state_too_large", refused
            after = await harness.event(comm, "add", {"n": 2})
            assert after["type"] != "error", after
        finally:
            await comm.disconnect()
    stored = await sync_to_async(_stored_values)(key)
    assert len(stored["cart"]) == 2 and stored["count"] == 1


@pytest.mark.django_db
def test_large_state_is_warned_about_once_naming_the_largest_attributes(caplog, settings):
    settings.DJUST_AUTOMATIC_STATE_WARN_KB = 4
    _automatic_state._NOTICED.clear()
    with caplog.at_level(logging.WARNING, logger="djust.automatic_state"):
        view, key, _ = _fallback(Cart, [("add", {"n": 200}), ("add", {"n": 1})])
    assert len(view.cart) == 201
    lines = [r.getMessage() for r in caplog.records if "Largest" in r.getMessage()]
    assert len(lines) == 1 and "cart" in lines[0] and "Cart" in lines[0], lines
    assert "item-0001" not in "".join(lines)


def test_the_limits_come_from_the_view_then_the_settings_then_the_defaults(settings):
    assert _automatic_state.limits_for(Counter()) == (512 * 1024, 100 * 1024)
    settings.DJUST_AUTOMATIC_STATE_MAX_KB = 64
    assert _automatic_state.limits_for(Counter()) == (64 * 1024, 64 * 1024)
    assert _automatic_state.limits_for(Cart())[0] == 16 * 1024
    settings.DJUST_AUTOMATIC_STATE_MAX_KB = "lots"
    assert _automatic_state.limits_for(Counter())[0] == 512 * 1024


# ---------------------------------------------------------------------------
# What cannot be saved is reported, loudly under DEBUG
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Secrets are not stored
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_secret_form_controls_are_blanked_whatever_their_widget():
    from django import forms

    from djust.forms import FormMixin, is_secret_form_control

    class Pay(forms.Form):
        name = forms.CharField()
        ref = forms.CharField(widget=forms.HiddenInput)
        card = forms.CharField(widget=forms.TextInput(attrs={"autocomplete": "cc-number"}))
        code = forms.CharField(widget=forms.TextInput(attrs={"autocomplete": "cc-csc"}))
        sms = forms.CharField(widget=forms.TextInput(attrs={"autocomplete": "one-time-code"}))
        fresh = forms.CharField(widget=forms.TextInput(attrs={"autocomplete": "new-password"}))
        pw = forms.CharField(widget=forms.PasswordInput)

    class PayView(FormMixin, LiveView):
        exposure_policy = "explicit"
        form_class = Pay
        template = "<div dj-root>{{ form_data.name }}</div>"

        @event_handler()
        def fill(self, **kwargs):
            for name in self.form_data:
                self.form_data[name] = name + "-value-4107"
            self.field_errors = {"card": ["card-value-4107 is not a card"], "name": ["too short"]}

    _view, key, _ = _fallback(PayView, [("fill",), ("fill",)])
    stored = _stored_values(key)
    assert stored["form_data"] == {
        "name": "name-value-4107",
        "ref": "ref-value-4107",  # hidden, server-rendered, not named as a secret
        "card": "",
        "code": "",
        "sms": "",
        "fresh": "",
        "pw": "",
    }
    assert stored["field_errors"] == {"card": ["Invalid value."], "name": ["too short"]}
    for secret in ("card", "code", "sms", "fresh", "pw"):
        assert secret + "-value-4107" not in json.dumps(_stored(key))
    assert not is_secret_form_control("name", None)
    assert is_secret_form_control("password", None)  # the explicit list (the floor)


# ---------------------------------------------------------------------------
# The page, the session store, the class digest
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_cookie_session_holds_no_automatic_state(caplog):
    """A cookie session is readable by the browser: nothing may be written to
    it. The request is otherwise complete, so only the store check stops it."""
    from django.contrib.sessions.backends.signed_cookies import SessionStore as CookieSession

    _automatic_state._NOTICED.clear()
    request = _request("POST", None, "inc")
    key = request.session.session_key
    request.session = CookieSession(key)
    request.session._session_key = key
    assert request.session.session_key == key
    view = Counter()
    view.mount(request)
    view._djust_tab = harness.TAB
    view.count = 3
    with caplog.at_level(logging.WARNING, logger="djust.automatic_state"):
        assert _automatic_state.stage(view, request) is None
    assert not [name for name in request.session.keys() if name.startswith("_djust_")]
    assert any("session store cannot hold" in r.getMessage() for r in caplog.records)


@pytest.mark.django_db
def test_a_legacy_view_takes_no_part():
    """Deprecated and on its way out: nothing is saved for it on any transport,
    so a fallback POST cannot bring back a value older than its socket's."""
    view, key, _ = _fallback(LegacyCounter, [("inc",), ("inc",)])
    assert view.count == 1 and not _stored(key)


# ---------------------------------------------------------------------------
# The 1.3 model: a saved view is rebuilt, mount() does not run again
# ---------------------------------------------------------------------------

CALLS: List[str] = []


class Rebuilt(_Base):
    shown = ("count",)

    def mount(self, request, **kwargs):
        CALLS.append("mount")
        self.count = 0
        self._cursor = 10  # private, created by mount(): saved too
        self.loaded_by = "mount"

    def handle_restore(self, request):
        CALLS.append("restore")

    @event_handler()
    def inc(self, **kwargs):
        self.count += 1
        self._cursor += 1
        self.loaded_by = "handler"


def _saved_envelope(key: str) -> Dict[str, Any]:
    stored = _stored(key)
    assert len(stored) == 1, list(stored)
    return next(iter(stored.values()))


@pytest.mark.django_db
def test_a_later_fallback_request_restores_and_does_not_run_mount_again():
    CALLS.clear()
    view, key, _ = _fallback(Rebuilt, [("inc",), ("inc",), ("inc",)])
    assert CALLS == ["mount", "restore", "restore", "restore"]  # the GET's mount only
    assert (view.count, view._cursor, view.loaded_by) == (3, 13, "handler")
    envelope = _saved_envelope(key)
    assert envelope["values"] == {"count": 3, "loaded_by": "handler"}
    # (beside the framework's own post-init slots, which 1.3 saved as well)
    assert envelope["private"]["_cursor"] == 13 and envelope["remount"] is False


@pytest.mark.django_db
def test_a_view_dispatched_through_as_view_is_still_rebuilt_without_mount(caplog):
    """Django's ``View.setup()`` puts ``self.head = self.get`` on every
    dispatched view: a callable is not state, so it neither makes the next
    request mount again nor draws a warning."""
    CALLS.clear()
    _automatic_state._NOTICED.clear()
    entry = Rebuilt.as_view()
    first = _request("GET", None)
    with caplog.at_level(logging.WARNING, logger="djust.automatic_state"):
        assert entry(first).status_code == 200
        key = first.session.session_key
        for _ in range(3):
            assert entry(_request("POST", key, "inc")).status_code == 200
    assert CALLS == ["mount", "restore", "restore", "restore"]
    envelope = _saved_envelope(key)
    assert envelope["remount"] is False and envelope["values"]["count"] == 3
    assert not caplog.records, [record.getMessage() for record in caplog.records]


@pytest.mark.django_db
def test_with_nothing_saved_the_fallback_post_runs_mount():
    CALLS.clear()
    request = _request("POST", None, "inc")
    view = Rebuilt()
    assert view.post(request).status_code == 200
    assert CALLS == ["mount"] and view.count == 1  # no restore hook: nothing was restored


@pytest.mark.django_db
def test_a_page_load_mounts_again_and_overwrites_the_saved_state():
    CALLS.clear()
    _view, key, _ = _fallback(Rebuilt, [("inc",), ("inc",)])
    assert _saved_envelope(key)["values"]["count"] == 2
    assert Rebuilt().get(_request("GET", key)).status_code == 200
    assert _saved_envelope(key)["values"] == {"count": 0, "loaded_by": "mount"}
    assert CALLS.count("mount") == 2


class Holder:
    pass


class HoldsObjects(_Base):
    """What a 1.3 application holds: the user, a queryset, a service object."""

    shown = ("count",)

    def mount(self, request, **kwargs):
        CALLS.append("mount")
        self.count = 0
        self.user = request.user
        self.rows = User.objects.all()
        self.service = Holder()
        self._client = Holder()

    @event_handler()
    def inc(self, **kwargs):
        self.count += 1
        assert self.user is not None and self.rows is not None  # re-created, never missing
        assert isinstance(self.service, Holder) and isinstance(self._client, Holder)


@pytest.mark.django_db
def test_an_unsaveable_attribute_is_skipped_warned_once_and_recreated_by_mount(caplog, settings):
    """Never a string, never missing, never an error (DEBUG included): the next
    request runs mount() again and the saved state goes back over it."""
    CALLS.clear()
    _automatic_state._NOTICED.clear()
    settings.DEBUG = True
    with caplog.at_level(logging.WARNING, logger="djust.automatic_state"):
        view, key, _ = _fallback(HoldsObjects, [("inc",), ("inc",), ("inc",)])
    assert view.count == 3  # the saved count over mount()'s 0
    assert CALLS == ["mount"] * 4  # the GET, and each POST: something was not saved
    envelope = _saved_envelope(key)
    assert envelope["values"] == {"count": 3} and envelope["remount"] is True
    assert "_client" not in envelope["private"]
    text = "\n".join(record.getMessage() for record in caplog.records)
    for name in ("user", "rows", "service"):
        assert text.count("HoldsObjects.%s " % name) == 1, (name, text)
    assert "a queryset is never saved" in text and "a Holder cannot be saved" in text
    assert "mount() runs again" in text
    assert "_client" not in text  # a private attribute is skipped quietly, as in 1.3
    assert "Holder object at" not in json.dumps(_stored(key))  # nothing stringified


@pytest.mark.django_db
def test_a_model_instance_is_saved_as_its_field_dict_as_in_1_3():
    class HoldsModel(_Base):
        shown = ("count",)

        def mount(self, request, **kwargs):
            self.count = 0
            self.member = User(username="ann-4107", email="ann@example.com", password="pw-hash")

        @event_handler()
        def inc(self, **kwargs):
            self.count += 1

    view, key, _ = _fallback(HoldsModel, [("inc",), ("inc",)])
    assert view.count == 2
    saved_member = _saved_envelope(key)["values"]["member"]
    assert saved_member["username"] == "ann-4107" and "password" not in saved_member
    assert isinstance(view.member, dict) and view.member["username"] == "ann-4107"


@pytest.mark.django_db
def test_work_mount_queued_makes_the_next_request_mount_again():
    class Queues(_Base):
        shown = ("count",)

        def mount(self, request, **kwargs):
            CALLS.append("mount")
            self.count = 0
            self.start_async(self._work, name="work")

        def _work(self):
            self.count = 100

        @event_handler()
        def inc(self, **kwargs):
            self.count += 1

    CALLS.clear()
    first = _request("GET", None)
    assert Queues().get(first).status_code == 200
    assert _saved_envelope(first.session.session_key)["remount"] is True
    view = Queues()
    assert view.post(_request("POST", first.session.session_key, "inc")).status_code == 200
    assert CALLS == ["mount", "mount"]


@pytest.mark.django_db
def test_a_sensitive_name_is_not_stored_and_is_reported(caplog, settings):
    settings.DJUST_SENSITIVE_FIELDS = ["api_key"]
    _automatic_state._NOTICED.clear()

    class Keys(_Base):
        shown = ("count",)

        def mount(self, request, **kwargs):
            self.count = 0
            self.password = "pw-4107"
            self.api_key = "key-4107"
            self.is_staff = True

        @event_handler()
        def inc(self, **kwargs):
            self.count += 1

    with caplog.at_level(logging.WARNING, logger="djust.automatic_state"):
        _view, key, _ = _fallback(Keys, [("inc",)])
    assert _saved_envelope(key)["values"] == {"count": 1}
    assert "pw-4107" not in json.dumps(_stored(key)) and "key-4107" not in json.dumps(_stored(key))
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert text.count("Keys.password ") == 1 and text.count("Keys.api_key ") == 1
    assert "pw-4107" not in text and "key-4107" not in text


class Names(_Base):
    shown = ("count",)
    limit = 5  # a plain class-level default
    temporary_assigns = {"flash_rows": []}

    @property
    def doubled(self):
        return self.count * 2

    def helper(self):
        return "method"

    def mount(self, request, **kwargs):
        self.count = 0
        self.limit = 7
        self._private = "p"
        self.flash_rows = ["x"]
        self.page_title = "Title"
        self.client_timezone = "Europe/Paris"
        self.tick_interval = 5

    @event_handler()
    def inc(self, **kwargs):
        self.count += 1


@pytest.mark.django_db
def test_only_application_attributes_are_stored():
    _view, key, _ = _fallback(Names, [("inc",)])
    envelope = _saved_envelope(key)
    assert envelope["values"] == {"count": 1, "limit": 7}
    assert envelope["private"]["_private"] == "p"
    assert not [name for name in envelope["private"] if name.startswith(("_djust_", "_state_"))]


def _tamper(key: str, section: str, **values: Any) -> None:
    session = SessionStore(key)
    (name,) = [n for n in session.keys() if n.startswith("_djust_auto_")]
    envelope = session[name]
    envelope[section].update(values)
    session[name] = envelope
    session.save()


@pytest.mark.django_db
def test_a_stored_name_the_view_would_not_save_is_not_put_back():
    """Also 1.3's deploy rule: state saved by older code goes onto the new code,
    except a name that is now a method or a property."""
    _view, key, _ = _fallback(Names, [("inc",)])
    _tamper(
        key,
        "values",
        _private="public section, private name",
        __class__="x",
        doubled=99,
        helper="was an attribute in the old code",
        inc="x",
        mount="x",
        request="x",
        template_name="evil.html",
        login_required=False,
        password="injected",
        flash_rows=["injected"],
        exposure_policy="legacy",
        brand_new="allowed: a handler may create attributes",
    )
    _tamper(
        key,
        "private",
        __dict__={},
        __class__="x",
        _framework_attrs=[],
        _user_private_keys=["x"],
        _djust_auto_basis="x",
        _djust_event_request="x",
        _state_count=41,
        _async_tasks={"x": 1},
        helper="x",
        public_name="private section, public name",
        _kept="allowed",
    )
    view = Names()
    assert view.post(_request("POST", key, "inc")).status_code == 200
    assert view.count == 2 and view._private == "p" and view._kept == "allowed"
    assert view.doubled == 4 and view.helper() == "method" and callable(view.inc)
    assert view.exposure_policy == "explicit" and view.template_name != "evil.html"
    assert not hasattr(view, "password") and not hasattr(view, "public_name")
    assert view.brand_new.startswith("allowed")
    assert type(view) is Names and isinstance(view._framework_attrs, frozenset)
    assert view._djust_auto_basis != "x" and not getattr(view, "_async_tasks", None)


@pytest.mark.django_db
def test_a_bad_envelope_reads_as_nothing_saved():
    _view, key, _ = _fallback(Counter, [("inc",)])
    session = SessionStore(key)
    (name,) = [n for n in session.keys() if n.startswith("_djust_auto_")]
    good = dict(session[name])
    for change in (
        {"version": 99},
        {"binding": "0" * 64},
        {"created": 2**40},
        {"values": [1, 2]},
        {"private": "x"},
        {"remount": "yes"},
        {"extra": 1},
        {"values": {"count": {"__djust_value__": ["enum", ["os", "PathLike", "x"]]}}},
    ):
        broken = SessionStore(key)
        broken[name] = {**good, **change}
        broken.save()
        view = Counter()
        assert view.post(_request("POST", key, "nothing")).status_code == 200
        assert view.count == 0, change
        fixed = SessionStore(key)
        fixed[name] = good
        fixed.save()


@pytest.mark.django_db
def test_the_session_is_written_only_when_the_state_changed(monkeypatch):
    from djust import _session_merge

    writes: List[int] = []
    real = _session_merge.save_merged
    monkeypatch.setattr(
        _session_merge, "save_merged", lambda *a, **k: (writes.append(1), real(*a, **k))[1]
    )
    _fallback(Counter, [("inc",), ("nothing",), ("nothing",), ("inc",)])
    assert len(writes) == 3  # the page load, and the two changes
    writes.clear()
    monkeypatch.setattr(_per_turn, "disabled", frozenset({"unchanged_save_skip"}))
    _fallback(Counter, [("inc",), ("nothing",), ("nothing",), ("inc",)])
    assert len(writes) == 5  # gate-off: every request writes


@pytest.mark.django_db
def test_an_unchanged_state_is_rewritten_once_half_its_lifetime_has_passed(monkeypatch):
    _view, key, _ = _fallback(Counter, [("inc",)])
    created = _saved_envelope(key)["created"]
    real = _automatic_state.time.time
    monkeypatch.setattr(_automatic_state.time, "time", lambda: real() + 1900)  # > 3600 / 2
    view = Counter()
    view.post(_request("POST", key, "nothing"))
    assert view.count == 1 and _saved_envelope(key)["created"] > created


@pytest.mark.django_db
def test_what_is_stored_does_not_share_references_with_the_view():
    """Mutation after capture: the stored copy stays as captured, and the
    changed state is then seen as changed."""
    request = _request("POST", None, "add")
    view = EditedList()
    view.mount(request)
    view._djust_tab = harness.TAB
    assert _automatic_state.stage(view, request) is not None
    view.items.append("after-capture")
    assert _saved_values_in(request.session) == {"items": ["a"]}
    assert _automatic_state.stage(view, request) is not None
    assert _saved_values_in(request.session) == {"items": ["a", "after-capture"]}


def _saved_values_in(session: Any) -> Any:
    (envelope,) = [v for k, v in session.items() if k.startswith("_djust_auto_")]
    return envelope["values"]


@pytest.mark.django_db
def test_the_turns_changed_keys_are_used_once_and_never_hide_a_later_change(monkeypatch):
    request = _request("POST", None, "x")
    view = EditedList()
    view.mount(request)
    view._djust_tab = harness.TAB
    assert _automatic_state.stage(view, request) is not None  # the basis
    calls: List[int] = []
    real = _automatic_state.project
    monkeypatch.setattr(_automatic_state, "project", lambda v: (calls.append(1), real(v))[1])
    view._djust_auto_changed = set()  # this turn changed nothing
    assert _automatic_state.stage(view, request) is None
    assert calls == [] and view._djust_auto_changed is None
    view.items.append("later")  # a later turn with no answer of its own
    assert _automatic_state.stage(view, request) is not None and calls == [1]
    view._djust_auto_changed = {"_cursor"}  # a private attribute changed: saved state
    assert _automatic_state.stage(view, request) is None and calls == [1, 1]


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_over_a_socket_only_changing_turns_serialize_and_write(monkeypatch):
    from djust import _session_merge

    projected: List[int] = []
    writes: List[int] = []
    real_project = _automatic_state.project
    real_save = _session_merge.save_merged
    key = await harness.new_session_key_async()
    with harness.ws_settings(MODULE):
        comm, _mount = await harness.mount_view(Counter, key)
        monkeypatch.setattr(
            _automatic_state, "project", lambda v: (projected.append(1), real_project(v))[1]
        )
        monkeypatch.setattr(
            _session_merge, "save_merged", lambda *a, **k: (writes.append(1), real_save(*a, **k))[1]
        )
        try:
            for name in ("nothing", "inc", "nothing", "nothing", "inc"):
                await harness.event(comm, name)
        finally:
            await comm.disconnect()
    assert len(projected) == 2 and len(writes) == 2


def test_a_state_field_is_read_without_creating_its_default():
    view = Declared()
    before = set(vars(view))
    assert _automatic_state.project(view).public == {"kept": 0}
    assert set(vars(view)) == before


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_socket_mount_runs_mount_then_puts_the_saved_state_back():
    """The socket keeps running mount() (its registrations live in the
    process), then restores public and private state over it."""
    CALLS.clear()
    key = await harness.new_session_key_async()
    with harness.ws_settings(MODULE):
        comm, _mount = await harness.mount_view(Rebuilt, key)
        await harness.event(comm, "inc")
        await harness.event(comm, "inc")
        await comm.disconnect()
        again, mount = await harness.mount_view(Rebuilt, key)
        frame = await harness.event(again, "inc")
        await again.disconnect()
    assert CALLS == ["mount", "mount", "restore"]
    assert _probe_of(mount["html"]) == {"count": 2}
    assert frame["type"] != "error"
    assert (await sync_to_async(_saved_envelope)(key))["private"]["_cursor"] == 13


def test_the_store_is_written_and_read_in_one_place_each():
    """Every transport saves and restores through ``_exposure_sessions`` (and
    the fallback's ``put_back``); nothing else calls the store's functions."""
    import pathlib
    import re as regex

    root = pathlib.Path(_automatic_state.__file__).parent
    callers: Dict[str, set] = {}
    pattern = (
        r"_automatic_state\.(stage|load|saved|apply_private|put_back|reapply|settle"
        r"|finish_mount|apply_components|url_changed)\("
    )
    for path in root.rglob("*.py"):
        if "tests" in path.parts or path.name == "_automatic_state.py":
            continue
        for name in regex.findall(pattern, path.read_text()):
            callers.setdefault(name, set()).add(path.name)
    assert callers == {
        "stage": {"_exposure_sessions.py"},
        "load": {"_exposure_sessions.py"},
        "saved": {"_exposure_sessions.py"},
        "finish_mount": {"_exposure_sessions.py"},
        "put_back": {"request.py"},
        "url_changed": {"request.py"},
        "settle": {"request.py"},
    }


# ---------------------------------------------------------------------------
# A held model, handle_params(), components, two tabs
# ---------------------------------------------------------------------------


class HoldsLiveModel(_Base):
    """``mount()`` loads a model; a handler calls a method on it."""

    shown = ("count", "who")

    def mount(self, request, **kwargs):
        self.count = 0
        self.who = ""
        self.obj = User.objects.get(username="auto-4107-a")
        self._owner = User.objects.get(username="auto-4107-b")

    @event_handler()
    def greet(self, **kwargs):
        self.count += 1
        self.who = self.obj.get_username() + "/" + self._owner.get_username()


async def _page_then_socket(view_class: type) -> Any:
    """The real order: the page GET (which saves), then the socket mount."""
    first = await sync_to_async(_request)("GET", None)
    status = await sync_to_async(lambda: view_class().get(first).status_code)()
    assert status == 200
    key = first.session.session_key
    comm, mount = await harness.mount_view(view_class, key)
    return key, comm, mount


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_over_a_socket_a_model_mount_loaded_stays_a_model():
    """The GET saved the model as a dict of its fields; the socket's mount()
    loaded the instance again, and the saved dict must not go over it: not
    when nothing changed, and not after a reconnect that restores other state."""
    await sync_to_async(_users)()
    with harness.ws_settings(MODULE):
        key, comm, _mount = await _page_then_socket(HoldsLiveModel)
        try:
            frame = await _event_or_error(comm, "greet")
            assert frame["type"] != "error", frame
        finally:
            await comm.disconnect()
        saved_now = await sync_to_async(_saved_envelope)(key)
        assert isinstance(saved_now["values"]["obj"], dict)  # 1.3's saved form
        again, mount = await harness.mount_view(HoldsLiveModel, key)
        try:
            assert _probe_of(mount["html"]) == {"count": 1, "who": "auto-4107-a/auto-4107-b"}
            frame = await _event_or_error(again, "greet")
            assert frame["type"] != "error", frame  # self.obj is still a User
        finally:
            await again.disconnect()
    assert (await sync_to_async(_saved_envelope)(key))["values"]["count"] == 2


class SwapsModel(HoldsLiveModel):
    """A handler replaces the held model, so the attribute is one a handler
    changed and its saved form (a dict) is a candidate to put back."""

    @event_handler()
    def swap(self, **kwargs):
        self.obj = User.objects.get(username="auto-4107-c")
        self._owner = User.objects.get(username="auto-4107-c")
        self.count += 1


class RemountsWithModel(HoldsLiveModel):
    """Holds something unsaveable too, so every fallback request runs mount()
    and the saved state goes back over it."""

    def mount(self, request, **kwargs):
        super().mount(request, **kwargs)
        self.service = Holder()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_saved_model_dict_never_goes_over_the_instance_mount_loaded():
    await sync_to_async(_users)()
    with harness.ws_settings(MODULE):
        key, comm, _mount = await _page_then_socket(SwapsModel)
        assert (await _event_or_error(comm, "swap"))["type"] != "error"
        await comm.disconnect()
        stored = await sync_to_async(_saved_envelope)(key)
        assert "obj" in stored["changed"] and isinstance(stored["values"]["obj"], dict)
        again, _mount = await harness.mount_view(SwapsModel, key)
        try:
            frame = await _event_or_error(again, "greet")
            assert frame["type"] != "error", frame  # obj and _owner are model instances
        finally:
            await again.disconnect()


@pytest.mark.django_db
def test_a_fallback_request_that_mounts_keeps_the_model_mount_loaded():
    _users()
    view, _key, _ = _fallback(RemountsWithModel, [("greet",), ("greet",), ("greet",)])
    assert isinstance(view.obj, User) and isinstance(view._owner, User)
    assert (view.count, view.who) == (3, "auto-4107-a/auto-4107-b")


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_socket_mount_after_the_page_get_applies_nothing():
    CALLS.clear()
    with harness.ws_settings(MODULE):
        _key, comm, _mount = await _page_then_socket(Rebuilt)
        frame = await harness.event(comm, "inc")
        await comm.disconnect()
    # mount() on the GET and on the socket; the saved state equals what the
    # socket's mount() built, so nothing was restored and the first event
    # answers with a patch, not the whole page.
    assert CALLS == ["mount", "mount", "restore"]
    assert frame["type"] == "patch", frame


class Tabs(_Base):
    """A handler-set value ``handle_params()`` would default."""

    shown = ("tab", "count")

    def mount(self, request, **kwargs):
        self.tab = "a"
        self.count = 0

    def handle_params(self, params, uri):
        self.tab = params.get("tab", "a")

    @event_handler()
    def pick(self, value: str = "b", **kwargs):
        self.tab = value

    @event_handler()
    def inc(self, **kwargs):
        self.count += 1


class TabsRemounts(Tabs):
    """The same, on a view that mounts every request (it holds an object)."""

    def mount(self, request, **kwargs):
        super().mount(request, **kwargs)
        self.service = Holder()


@pytest.mark.django_db
@pytest.mark.parametrize("view_class", [Tabs, TabsRemounts])
def test_handle_params_does_not_reset_what_a_handler_set_over_the_fallback(view_class):
    view, _key, _ = _fallback(view_class, [("pick", {"value": "b"}), ("inc",), ("inc",)])
    assert (view.tab, view.count) == ("b", 2)


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("view_class", [Tabs, TabsRemounts])
async def test_handle_params_does_not_reset_what_a_handler_set_on_a_reconnect(view_class):
    key = await harness.new_session_key_async()
    with harness.ws_settings(MODULE):
        comm, _mount = await harness.mount_view(view_class, key)
        await harness.event(comm, "pick", {"value": "b"})
        await harness.event(comm, "inc")
        await comm.disconnect()
        again, mount = await harness.mount_view(view_class, key)
        await again.disconnect()
    assert _probe_of(mount["html"]) == {"tab": "b", "count": 1}


from djust.components.descriptors.base import LiveComponent as _DescriptorComponent  # noqa: E402
from djust.components.descriptors.base import TypedState as _TypedState  # noqa: E402


class Bump(_DescriptorComponent):
    template = "<span>{{ n }}</span>"

    class State(_TypedState):
        n: int = 0


class HasComponent(_Base):
    shown = ("count",)
    bumper = Bump()

    def mount(self, request, **kwargs):
        self.count = 0

    def get_context_data(self, **kwargs):
        return {"probe": json.dumps({"count": self.count, "n": self.bumper.state.n})}

    @event_handler()
    def bump(self, **kwargs):
        self.count += 1
        self.bumper.state.n += 1


@pytest.mark.django_db
def test_a_components_state_survives_fallback_posts():
    view, key, _ = _fallback(HasComponent, [("bump",), ("bump",), ("bump",)])
    assert (view.count, view.bumper.state.n) == (3, 3)
    assert _saved_envelope(key)["components"] == {"bumper": {"n": 3}}


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_components_state_survives_a_reconnect():
    key = await harness.new_session_key_async()
    with harness.ws_settings(MODULE):
        comm, _mount = await harness.mount_view(HasComponent, key)
        await harness.event(comm, "bump")
        await harness.event(comm, "bump")
        await comm.disconnect()
        again, mount = await harness.mount_view(HasComponent, key)
        frame = await harness.event(again, "bump")
        await again.disconnect()
    assert _probe_of(mount["html"]) == {"count": 2, "n": 2}  # the view AND its component
    assert frame["type"] != "error"
    stored = await sync_to_async(_saved_envelope)(key)
    assert stored["components"] == {"bumper": {"n": 3}} and stored["values"]["count"] == 3


@pytest.mark.django_db
def test_stored_component_state_only_goes_onto_components():
    _view, key, _ = _fallback(HasComponent, [("bump",)])
    _tamper(
        key,
        "components",
        count={"real": "a plain attribute, not a component"},
        bump={"__name__": "x"},
        __class__={"x": 1},
        bumper={"n": 5, "_private": "x", "__class__": "x", "__proto__": 1},
    )
    view = HasComponent()
    assert view.post(_request("POST", key, "bump")).status_code == 200
    assert view.count == 2 and callable(view.bump)
    assert view.bumper.state.n == 6
    assert not hasattr(view.bumper.state, "_private")
    assert type(view.bumper.state).__name__ == "State"


# ---------------------------------------------------------------------------
# A socket mount puts back only what a handler changed
# ---------------------------------------------------------------------------

STAFF = {"is": True}


class Gated(_Base):
    """``mount()`` computes a permission flag and a default; handlers change a
    counter, the default, and create an attribute ``mount()`` never sets."""

    shown = ("can_edit", "count", "label", "extra")

    def mount(self, request, **kwargs):
        self.can_edit = STAFF["is"]
        self.count = 0
        self.label = "from-mount"
        self._seen = STAFF["is"]

    def get_context_data(self, **kwargs):
        extra = getattr(self, "extra", None)
        return {"probe": json.dumps([self.can_edit, self.count, self.label, extra, self._seen])}

    @event_handler()
    def inc(self, **kwargs):
        self.count += 1

    @event_handler()
    def rename(self, **kwargs):
        self.label = "from-handler"
        self.extra = "made by a handler"


def _gated(markup: str) -> List[Any]:
    import html
    import re

    return json.loads(html.unescape(re.search(r"<pre[^>]*>(.*?)</pre>", markup, re.S).group(1)))


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_reconnect_keeps_what_mount_computes_fresh_and_restores_handler_changes():
    """The right is revoked between two connections: ``can_edit`` (and the
    private ``_seen``) are what THIS mount() computed, while the counter, the
    label a handler changed and the attribute a handler created come back."""
    STAFF["is"] = True
    try:
        with harness.ws_settings(MODULE):
            key, comm, _mount = await _page_then_socket(Gated)
            await harness.event(comm, "inc")
            await harness.event(comm, "inc")
            await harness.event(comm, "rename")
            await comm.disconnect()
            stored = await sync_to_async(_saved_envelope)(key)
            assert stored["changed"] == ["count", "extra", "label"]
            assert stored["values"]["can_edit"] is True  # saved, but not a handler's change
            STAFF["is"] = False  # revoked
            again, mount = await harness.mount_view(Gated, key)
            frame = await harness.event(again, "inc")
            await again.disconnect()
        assert _gated(mount["html"]) == [False, 2, "from-handler", "made by a handler", False]
        assert frame["type"] != "error"
        stored = await sync_to_async(_saved_envelope)(key)
        # The record is kept, and the store now holds the fresh mount() values.
        assert stored["changed"] == ["count", "extra", "label"]
        assert stored["values"]["can_edit"] is False and stored["values"]["count"] == 3
    finally:
        STAFF["is"] = True


@pytest.mark.django_db
def test_over_the_fallback_a_mount_computed_value_continues_as_saved_as_in_1_3():
    """The fallback rebuilds the view from its saved state and does not run
    mount(), so what mount() computed at the page load continues until the
    next page load. Authorization belongs in the gates, not in mount()."""
    STAFF["is"] = True
    try:
        first = _request("GET", None)
        assert Gated().get(first).status_code == 200
        key = first.session.session_key
        Gated().post(_request("POST", key, "inc"))
        STAFF["is"] = False
        view = Gated()
        view.post(_request("POST", key, "inc"))
        assert (view.can_edit, view.count) == (True, 2)
        assert _saved_envelope(key)["changed"] == ["count"]
        # A page load runs mount() again and starts the record empty.
        assert Gated().get(_request("GET", key)).status_code == 200
        fresh = _saved_envelope(key)
        assert fresh["values"]["can_edit"] is False and fresh["changed"] == []
    finally:
        STAFF["is"] = True


@pytest.mark.django_db
def test_changes_made_over_the_fallback_are_recorded_for_a_later_socket_mount():
    _view, key, _ = _fallback(Gated, [("inc",), ("rename",), ("inc",)])
    assert _saved_envelope(key)["changed"] == ["count", "extra", "label"]


@pytest.mark.django_db
def test_a_stored_changed_record_cannot_name_what_the_view_would_not_restore():
    _view, key, _ = _fallback(Names, [("inc",)])
    session = SessionStore(key)
    (name,) = [n for n in session.keys() if n.startswith("_djust_auto_")]
    envelope = session[name]
    envelope["changed"] = ["count", "__class__", "inc", "template_name", "_framework_attrs", 7]
    envelope["values"].update(template_name="evil.html", inc="x")
    session[name] = envelope
    session.save()
    request = _request("POST", key, "inc")
    view = Names()
    view.mount(request)
    view._djust_tab = harness.TAB
    view._snapshot_user_private_attrs()
    restored = _automatic_state.load(view, request)
    assert restored == {"count": 1}
    assert callable(view.inc) and type(view) is Names


# ---------------------------------------------------------------------------
# One saved state per tab (#4106)
# ---------------------------------------------------------------------------

TAB_A, TAB_B = "tab-aaaaaaaa", "tab-bbbbbbbb"


@pytest.mark.django_db
def test_the_page_carries_a_new_tab_id_on_every_load(monkeypatch):
    monkeypatch.undo()  # the real id minting
    first = _request("GET", None)
    pages = [
        Counter().get(_request("GET", first.session.session_key)).content.decode() for _ in "ab"
    ]
    ids = [
        re.search(r'<meta name="djust-tab" content="([A-Za-z0-9_-]{8,64})">', page)
        for page in pages
    ]
    assert all(ids) and ids[0].group(1) != ids[1].group(1)


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_two_tabs_on_one_route_keep_independent_state_over_the_socket():
    key = await harness.new_session_key_async()
    with harness.ws_settings(MODULE):
        tab_a, _mount = await harness.mount_view(Counter, key, tab=TAB_A)
        await harness.event(tab_a, "inc")
        await harness.event(tab_a, "inc")
        tab_b, mount_b = await harness.mount_view(Counter, key, tab=TAB_B)
        await harness.event(tab_b, "inc")
        await tab_a.disconnect()
        await tab_b.disconnect()
        again_a, mount_a = await harness.mount_view(Counter, key, tab=TAB_A)
        again_b, mount_b2 = await harness.mount_view(Counter, key, tab=TAB_B)
        # A duplicated browser tab carries the same id, so it shares the state.
        copy_a, mount_copy = await harness.mount_view(Counter, key, tab=TAB_A)
        for socket in (again_a, again_b, copy_a):
            await socket.disconnect()
    assert _probe_of(mount_b["html"]) == {"count": 0}  # not tab A's 2
    assert _probe_of(mount_a["html"]) == {"count": 2}
    assert _probe_of(mount_b2["html"]) == {"count": 1}
    assert _probe_of(mount_copy["html"]) == {"count": 2}
    assert len(await sync_to_async(_stored)(key)) == 2


@pytest.mark.django_db
def test_two_tabs_on_one_route_keep_independent_state_over_the_fallback():
    key = _request("GET", None).session.session_key
    for tab, events in ((TAB_A, 3), (TAB_B, 1)):
        for _ in range(events):
            assert Counter().post(_request("POST", key, "inc", tab=tab)).status_code == 200
    counts = {}
    for tab in (TAB_A, TAB_B):
        view = Counter()
        view.post(_request("POST", key, "nothing", tab=tab))
        counts[tab] = view.count
    assert counts == {TAB_A: 3, TAB_B: 1}


@pytest.mark.django_db
@pytest.mark.parametrize(
    "forged",
    [
        "",
        "short",
        "x" * 65,
        "has space in it",
        "../../etc/passwd",
        "tab-aaaaaaa\u00e9",
        "tab-cccccccc",
    ],
)
def test_a_missing_malformed_or_unknown_tab_id_restores_nothing(forged):
    key = _request("GET", None).session.session_key
    for _ in range(2):
        Counter().post(_request("POST", key, "inc", tab=TAB_A))
    before = _stored(key)
    view = Counter()
    response = view.post(_request("POST", key, "inc", tab=forged))
    assert response.status_code == 200  # never an error page
    assert view.count == 1  # mounted fresh: not tab A's state
    after = _stored(key)
    assert {k: v["values"] for k, v in after.items() if k in before} == {
        k: v["values"] for k, v in before.items()
    }
    if not _automatic_state.tab_id(forged):
        assert after.keys() == before.keys()  # and nothing is saved without an id


def test_what_counts_as_a_tab_id(monkeypatch):
    monkeypatch.undo()  # the real id minting
    assert _automatic_state.tab_id("tab-aaaaaaaa") == "tab-aaaaaaaa"
    assert len(_automatic_state.new_tab_id()) == 16
    assert _automatic_state.tab_id(_automatic_state.new_tab_id())
    for bad in (
        None,
        7,
        b"tab-aaaaaaaa",
        ["tab-aaaaaaaa"],
        "",
        "a" * 7,
        "a" * 65,
        "tab aaaaaaa",
        "tab-aaaa\n",
    ):
        assert _automatic_state.tab_id(bad) == ""


@pytest.mark.django_db
def test_the_oldest_tab_states_of_a_route_are_evicted_past_the_cap(monkeypatch):
    clock = {"now": int(_automatic_state.time.time()) - 100}
    monkeypatch.setattr(_automatic_state.time, "time", lambda: clock["now"])
    key = _request("GET", None).session.session_key
    tabs = ["tab-%08d" % n for n in range(_automatic_state.MAX_TABS + 3)]
    for tab in tabs:
        clock["now"] += 1
        assert Counter().post(_request("POST", key, "inc", tab=tab)).status_code == 200
    # Another route's tab states are not counted with this route's.
    Counter().post(_request("POST", key, "inc", tab=TAB_A, path="/another-route/"))
    stored = _stored(key)
    assert len(stored) == _automatic_state.MAX_TABS + 1
    first_kept = clock["now"] - (_automatic_state.MAX_TABS - 1)
    assert sorted(v["created"] for v in stored.values())[0] == first_kept  # the three oldest went
    newest = Counter()
    newest.post(_request("POST", key, "nothing", tab=tabs[-1]))
    assert newest.count == 1
    oldest = Counter()
    oldest.post(_request("POST", key, "nothing", tab=tabs[0]))
    assert oldest.count == 0  # evicted: mounts fresh


@pytest.mark.django_db
def test_a_tab_id_cannot_reach_another_users_or_sessions_state():
    key = _request("GET", None).session.session_key
    for _ in range(2):
        Counter().post(_request("POST", key, "inc", tab=TAB_A))
    other_session = _request("GET", None).session.session_key
    view = Counter()
    view.post(_request("POST", other_session, "nothing", tab=TAB_A))
    assert view.count == 0
    request = _request("POST", key, "nothing", tab=TAB_A)
    request.user = User.objects.create(username="auto-4107-tab")
    view = Counter()
    view.post(request)
    assert view.count == 0


# ---------------------------------------------------------------------------
# Re-review fixes: rollback, held callables, the size of tab states, deletions
# ---------------------------------------------------------------------------


class GatedCart(_Base):
    """A permission flag ``mount()`` computes, and a change that can be refused."""

    automatic_state_max_kb = 16

    def mount(self, request, **kwargs):
        self.can_edit = STAFF["is"]
        self.count = 0
        self.cart = []

    def get_context_data(self, **kwargs):
        return {"probe": json.dumps([self.can_edit, self.count, len(self.cart)])}

    @event_handler()
    def add(self, n: int = 1, **kwargs):
        self.count += 1
        self.cart = self.cart + [{"sku": "item-%04d" % i} for i in range(n)]


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_refused_change_rolls_back_to_this_mounts_state_not_the_page_gets():
    """GET while the right is held; it is revoked; the socket mounts (false);
    the first event is refused as too large. The rollback must go back to what
    THIS mount settled on, not to the page GET's saved ``can_edit=True``."""
    STAFF["is"] = True
    try:
        with harness.ws_settings(MODULE):
            first = await sync_to_async(_request)("GET", None)
            assert (await sync_to_async(lambda: GatedCart().get(first).status_code)()) == 200
            key = first.session.session_key
            assert (await sync_to_async(_saved_envelope)(key))["values"]["can_edit"] is True
            STAFF["is"] = False
            comm, mount = await harness.mount_view(GatedCart, key)
            try:
                assert _gated(mount["html"]) == [False, 0, 0]
                refused = await _event_or_error(comm, "add", {"n": 2000})
                assert refused["code"] == "state_too_large", refused
                frames = await harness.event_frames(comm, "add", {"n": 1})
            finally:
                await comm.disconnect()
        assert "[true," not in json.dumps(frames) and "[True," not in json.dumps(frames)
        stored = (await sync_to_async(_saved_envelope)(key))["values"]
        assert (stored["can_edit"], stored["count"], len(stored["cart"])) == (False, 1, 1)
    finally:
        STAFF["is"] = True


class Thing:
    def __call__(self):
        return "called"


class HoldsCallables(_Base):
    shown = ("count",)

    def mount(self, request, **kwargs):
        import functools

        CALLS.append("mount")
        self.count = 0
        self.kind = Decimal  # a class
        self.model = User  # a model class
        self.maker = functools.partial(int, "7")
        self.thing = Thing()

    @event_handler()
    def inc(self, **kwargs):
        self.count += 1
        assert self.kind("1.5") == Decimal("1.5") and self.model is User
        assert self.maker() == 7 and self.thing() == "called"


@pytest.mark.django_db
def test_a_held_callable_is_reported_and_recreated_by_mount_never_dropped(caplog):
    """Was a 500: the attribute was skipped silently, mount() did not run on
    the next POST, and the handler found no ``self.kind``."""
    CALLS.clear()
    _automatic_state._NOTICED.clear()
    entry = HoldsCallables.as_view()
    first = _request("GET", None)
    with caplog.at_level(logging.WARNING, logger="djust.automatic_state"):
        assert entry(first).status_code == 200
        key = first.session.session_key
        for _ in range(2):
            assert entry(_request("POST", key, "inc")).status_code == 200
    assert CALLS == ["mount", "mount", "mount"]
    envelope = _saved_envelope(key)
    assert envelope["values"] == {"count": 2} and envelope["remount"] is True
    text = "\n".join(record.getMessage() for record in caplog.records)
    for name in ("kind", "model", "maker", "thing"):
        assert text.count("HoldsCallables.%s " % name) == 1, (name, text)
    assert "HoldsCallables.head " not in text  # the view's own bound method: not state


class Heavy(_Base):
    shown = ("count",)
    automatic_state_max_kb = 64

    def mount(self, request, **kwargs):
        self.count = 0
        self.blob = ""

    @event_handler()
    def fill(self, kb: int = 1, **kwargs):
        self.count += 1
        self.blob = "x" * (kb * 1024)


def _auto_bytes(key: str) -> int:
    return sum(len(json.dumps(value)) for value in _stored(key).values())


@pytest.mark.django_db
def test_tab_states_of_one_page_stay_within_the_views_limit_together(monkeypatch):
    """Thirteen reloads of a view holding 30 KB: the session holds what fits in
    the view's 64 KB limit (two of them), not thirteen copies."""
    clock = {"now": int(_automatic_state.time.time()) - 1000}
    monkeypatch.setattr(_automatic_state.time, "time", lambda: clock["now"])
    key = _request("GET", None).session.session_key
    tabs = ["tab-%08d" % n for n in range(13)]
    for tab in tabs:
        clock["now"] += 1
        assert Heavy().post(_request("POST", key, "fill", {"kb": 30}, tab=tab)).status_code == 200
    stored = _stored(key)
    assert len(stored) == 2 and _auto_bytes(key) < 64 * 1024 + 2048
    newest = Heavy()
    newest.post(_request("POST", key, "nothing_here", tab=tabs[-1]))
    assert newest.count == 1  # the one just written is always kept


@pytest.mark.django_db
def test_all_saved_states_of_a_session_stay_within_the_session_budget(monkeypatch):
    clock = {"now": int(_automatic_state.time.time()) - 1000}
    monkeypatch.setattr(_automatic_state.time, "time", lambda: clock["now"])
    monkeypatch.setattr(_automatic_state, "SESSION_BUDGET_KB", 128)
    key = _request("GET", None).session.session_key
    for n in range(40):  # forty routes, one tab each, 30 KB each
        clock["now"] += 1
        request = _request("POST", key, "fill", {"kb": 30}, path="/route-%d/" % n)
        assert Heavy().post(request).status_code == 200
    assert _auto_bytes(key) < 128 * 1024 + 4096
    assert len(_stored(key)) == 4
    latest = Heavy()
    latest.post(_request("POST", key, "nothing_here", path="/route-39/"))
    assert latest.count == 1


@pytest.mark.django_db
def test_expired_states_are_dropped_at_the_next_write(monkeypatch):
    key = _request("GET", None).session.session_key
    for tab in (TAB_A, TAB_B):
        Counter().post(_request("POST", key, "inc", tab=tab))
    assert len(_stored(key)) == 2
    real = _automatic_state.time.time
    monkeypatch.setattr(_automatic_state.time, "time", lambda: real() + 3700)  # past the lifetime
    Counter().post(_request("POST", key, "inc", tab="tab-cccccccc"))
    assert len(_stored(key)) == 1


@pytest.mark.django_db
def test_an_evicted_tab_is_said_once_per_view_and_route_without_values(caplog):
    _automatic_state._NOTICED.clear()
    key = _request("GET", None).session.session_key
    Counter().post(_request("POST", key, "inc", tab=TAB_A))
    with caplog.at_level(logging.WARNING, logger="djust.automatic_state"):
        for n in range(12):  # a client can send any number of well-formed ids
            view = Counter()
            request = _request("POST", key, "nothing", tab="tab-random%03d" % n)
            assert view.post(request).status_code == 200
            assert view.count == 0
    lines = [
        r.getMessage() for r in caplog.records if "no saved state for this tab" in r.getMessage()
    ]
    assert len(lines) == 1, lines
    assert "Counter" in lines[0] and harness.PATH in lines[0] and SECRET not in lines[0]
    caplog.clear()
    _automatic_state._NOTICED.clear()
    with caplog.at_level(logging.WARNING, logger="djust.automatic_state"):
        Counter().post(_request("POST", _request("GET", None).session.session_key, "nothing"))
    assert not [r for r in caplog.records if "no saved state for this tab" in r.getMessage()]


class Draft(_Base):
    shown = ("count",)

    def mount(self, request, **kwargs):
        self.count = 0
        self.draft = "from mount"
        self._scratch = "from mount"

    def get_context_data(self, **kwargs):
        return {
            "probe": json.dumps(
                [self.count, getattr(self, "draft", None), getattr(self, "_scratch", None)]
            )
        }

    @event_handler()
    def discard(self, **kwargs):
        self.count += 1
        del self.draft
        del self._scratch

    @event_handler()
    def rewrite(self, **kwargs):
        self.draft = "again"

    @event_handler()
    def noop(self, **kwargs):
        pass


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_an_attribute_a_handler_deleted_stays_deleted_after_a_reconnect():
    key = await harness.new_session_key_async()
    with harness.ws_settings(MODULE):
        comm, _mount = await harness.mount_view(Draft, key)
        await harness.event(comm, "discard")
        await comm.disconnect()
        stored = await sync_to_async(_saved_envelope)(key)
        assert stored["deleted"] == ["_scratch", "draft"]
        again, mount = await harness.mount_view(Draft, key)
        await harness.event(again, "rewrite")
        await again.disconnect()
        third, mount3 = await harness.mount_view(Draft, key)
        await third.disconnect()
    assert _gated(mount["html"]) == [1, None, None]  # mount() set them again; still gone
    assert _gated(mount3["html"]) == [1, "again", None]  # set again by a handler: back
    assert (await sync_to_async(_saved_envelope)(key))["deleted"] == ["_scratch"]


@pytest.mark.django_db
def test_an_attribute_a_handler_deleted_stays_deleted_over_the_fallback():
    view, key, _ = _fallback(Draft, [("discard",), ("noop",)])
    assert not hasattr(view, "draft") and view.count == 1


@pytest.mark.django_db
def test_a_stored_deleted_record_cannot_remove_what_the_view_would_not_restore():
    _view, key, _ = _fallback(Names, [("inc",)])
    session = SessionStore(key)
    (name,) = [n for n in session.keys() if n.startswith("_djust_auto_")]
    envelope = session[name]
    envelope["deleted"] = ["limit", "_framework_attrs", "__dict__", "template_name", "inc", 7]
    session[name] = envelope
    session.save()
    request = _request("POST", key, "inc")
    view = Names()
    view.mount(request)
    view._djust_tab = harness.TAB
    view._snapshot_user_private_attrs()
    _automatic_state.load(view, request)
    assert "limit" not in vars(view)  # an application attribute: removed, as recorded
    assert isinstance(view._framework_attrs, frozenset) and callable(view.inc)
    assert view.template_name == Names().template_name


class BumpEvents(_DescriptorComponent):
    template = '<button dj-click="bump">{{ n }}</button>'

    class State(_TypedState):
        n: int = 0

    @event_handler()
    def bump(self, **kwargs):
        self.state.n += 1


class HasEventComponent(_Base):
    shown = ("count",)
    bumper = BumpEvents()

    def mount(self, request, **kwargs):
        self.count = 0

    def get_context_data(self, **kwargs):
        return {
            "bumper": self.bumper,
            "probe": json.dumps({"count": self.count, "n": self.bumper.state.n}),
        }


@pytest.mark.django_db
def test_an_event_addressed_to_a_component_reaches_it_over_the_fallback():
    first = _request("GET", None)
    assert HasEventComponent().get(first).status_code == 200
    key = first.session.session_key
    for expected in (1, 2, 3):
        view = HasEventComponent()
        response = view.post(_request("POST", key, "bump", {"component_id": "bumper"}))
        assert response.status_code == 200, response.content
        assert view.bumper.state.n == expected
    assert _saved_envelope(key)["components"] == {"bumper": {"n": 3}}


# ---------------------------------------------------------------------------
# Verification residuals
# ---------------------------------------------------------------------------


class KeepsOwnMethod(_Base):
    """The view's own bound method under another name, and a private callable."""

    shown = ("count",)

    def mount(self, request, **kwargs):
        CALLS.append("mount")
        self.count = 0
        self.cb = self.refresh
        self._pfn = len

    def refresh(self):
        return 2

    @event_handler()
    def add(self, **kwargs):
        self.count += self.cb() + self._pfn("x")


@pytest.mark.django_db
def test_a_views_own_method_under_another_name_is_unsaveable_not_dropped(caplog):
    """Was a 500 on every fallback POST: ``self.cb = self.refresh`` was skipped
    silently like Django's ``self.head``, so mount() did not run again and the
    handler found no ``self.cb``."""
    CALLS.clear()
    _automatic_state._NOTICED.clear()
    entry = KeepsOwnMethod.as_view()
    first = _request("GET", None)
    with caplog.at_level(logging.WARNING, logger="djust.automatic_state"):
        assert entry(first).status_code == 200
        key = first.session.session_key
        assert _saved_envelope(key)["remount"] is True
        for _ in range(2):
            assert entry(_request("POST", key, "add")).status_code == 200
    assert CALLS == ["mount", "mount", "mount"]
    assert _saved_envelope(key)["values"] == {"count": 6}
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert text.count("KeepsOwnMethod.cb ") == 1
    assert text.count("KeepsOwnMethod._pfn ") == 1  # a private callable is said too
    assert "KeepsOwnMethod.head " not in text  # the name Django's setup() sets


class AddsNames(_Base):
    automatic_state_max_kb = 16

    def mount(self, request, **kwargs):
        self.count = 0

    def get_context_data(self, **kwargs):
        present = sorted(n for n in ("blob", "extra", "_note") if n in vars(self))
        return {"probe": json.dumps([self.count, present])}

    @event_handler()
    def too_big(self, **kwargs):
        self.count += 1
        self.extra = "x"
        self._note = "n"
        self.blob = ["item-%05d" % n for n in range(4000)]

    @event_handler()
    def inc(self, **kwargs):
        self.count += 1


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_refused_change_also_takes_back_the_names_the_handler_added():
    """Left on the view, the oversized ``blob`` refused every later event of the
    connection, and the small ``extra`` was saved by the next event though the
    turn that set it was refused."""
    key = await harness.new_session_key_async()
    with harness.ws_settings(MODULE):
        comm, _mount = await harness.mount_view(AddsNames, key)
        try:
            refused = await _event_or_error(comm, "too_big")
            assert refused["code"] == "state_too_large", refused
            after = await _event_or_error(comm, "inc")
            assert after["type"] != "error", after  # not refused for blob again
            later = await _event_or_error(comm, "inc")
            assert later["type"] != "error", later
        finally:
            await comm.disconnect()
    stored = await sync_to_async(_saved_envelope)(key)
    assert stored["values"] == {"count": 2}  # neither blob nor extra; the refused +1 undone
    assert "_note" not in stored["private"]


# ---------------------------------------------------------------------------
# The URL: saved state wins while it is the same, handle_params() when it changed
# ---------------------------------------------------------------------------


class Paged(_Base):
    shown = ("page", "tab", "count")

    def mount(self, request, **kwargs):
        self.page = 1
        self.tab = "a"
        self.count = 0

    def handle_params(self, params, uri):
        self.page = int(params.get("page", 1))
        self.tab = params.get("tab", "a")

    @event_handler()
    def inc(self, **kwargs):
        self.count += 1

    @event_handler()
    def pick(self, value: str = "b", **kwargs):
        self.tab = value

    @event_handler()
    def go(self, page: int = 1, **kwargs):
        self.page = page
        self.live_patch(params={"page": page})

    @event_handler()
    def nothing(self, **kwargs):
        pass


def _paged(view: Any) -> tuple:
    return (view.page, view.tab, view.count)


def _paged_fallback(steps: List[tuple]) -> Any:
    """GET the first step's URL, then one POST per step: ``(query, event, params)``."""
    first = _request("GET", None, path=harness.PATH + steps[0][0])
    assert Paged().get(first).status_code == 200
    key = first.session.session_key
    view = None
    for query, event, *rest in steps:
        view = Paged()
        request = _request("POST", key, event, rest[0] if rest else None, path=harness.PATH + query)
        assert view.post(request).status_code == 200
    return view


@pytest.mark.django_db
def test_fallback_a_changed_url_wins_and_unrelated_handler_state_is_kept():
    view = _paged_fallback([("?page=1", "inc"), ("?page=1", "inc"), ("?page=3", "nothing")])
    assert _paged(view) == (3, "a", 2)


@pytest.mark.django_db
def test_fallback_the_same_url_keeps_what_a_handler_set_over_the_hooks_default():
    view = _paged_fallback([("?page=2", "pick", {"value": "b"}), ("?page=2", "inc")])
    assert _paged(view) == (2, "b", 1)


@pytest.mark.django_db
def test_fallback_a_changed_url_lets_handle_params_reset_what_it_assigns():
    """Documented cost of the rule: ``tab`` is assigned by handle_params() on
    every call, so when the URL changed its default wins over the handler's."""
    view = _paged_fallback([("?page=2", "pick", {"value": "b"}), ("?page=5", "nothing")])
    assert _paged(view) == (5, "a", 0)


@pytest.mark.django_db
def test_fallback_live_patch_moves_the_saved_url_so_the_next_request_is_not_a_change():
    """``go`` patches the URL to ?page=7. The state it saves is saved under
    ?page=7, so the request the browser then makes there keeps the handler-set
    tab; without that the first request after a live_patch would reset it."""
    view = _paged_fallback(
        [
            ("?page=1", "pick", {"value": "b"}),
            ("?page=1", "go", {"page": 7}),
            ("?page=7", "inc"),
            ("?page=7", "inc"),
        ]
    )
    assert _paged(view) == (7, "b", 2)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "saved,later",
    [
        ("?page=2&tab=a", "?tab=a&page=2"),  # another order
        ("?tab=a%20b&page=2", "?page=2&tab=a+b"),  # another encoding of the same value
        ("", "?"),  # no query and an empty one
    ],
)
def test_fallback_another_spelling_of_the_same_query_is_not_a_change(saved, later):
    first = _request("GET", None, path=harness.PATH + saved)
    assert Paged().get(first).status_code == 200
    key = first.session.session_key
    picked = Paged()
    picked.post(_request("POST", key, "pick", {"value": "z"}, path=harness.PATH + saved))
    view = Paged()
    view.post(_request("POST", key, "inc", path=harness.PATH + later))
    assert (view.tab, view.count) == ("z", 1)  # the handler's tab survived: same URL


def test_canonical_params_compare_parsed_values():
    from django.http import QueryDict

    same = [
        _automatic_state.canonical_params(QueryDict("page=2&tab=a%20b&x=1&x=2")),
        _automatic_state.canonical_params(QueryDict("x=1&tab=a+b&x=2&page=2")),
        _automatic_state.canonical_params({"tab": "a b", "x": ["1", "2"], "page": 2}),
    ]
    assert len(set(same)) == 1
    assert _automatic_state.canonical_params(QueryDict("x=2&x=1")) != same[0]
    empty = {_automatic_state.canonical_params(p) for p in (None, {}, QueryDict(""), "nonsense")}
    assert empty == {"[]"}
    assert _automatic_state.canonical_params({"page": ""}) != "[]"  # ?page= is a parameter


async def _mount_at(view_class: type, key: str, params: Dict[str, Any]) -> Any:
    """A socket mount as the client makes it: the query string travels as ``params``."""
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    comm = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    comm.scope["session"] = SessionStore(key)
    comm.scope["user"] = AnonymousUser()
    assert (await comm.connect())[0]
    await comm.receive_json_from()
    await comm.send_json_to(
        {
            "type": "mount",
            "view": f"{view_class.__module__}.{view_class.__name__}",
            "url": harness.PATH,
            "params": params,
            "tab": harness.TAB,
        }
    )
    frame = await comm.receive_json_from()
    assert frame["type"] == "mount", frame
    return comm, frame


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_socket_a_reconnect_at_the_same_url_keeps_the_handlers_tab_and_another_url_wins():
    key = await harness.new_session_key_async()
    with harness.ws_settings(MODULE):
        comm, _mount = await _mount_at(Paged, key, {"page": "2"})
        await harness.event(comm, "pick", {"value": "b"})
        await harness.event(comm, "inc")
        await comm.disconnect()
        same, mount_same = await _mount_at(Paged, key, {"page": "2"})
        await same.disconnect()
        other, mount_other = await _mount_at(Paged, key, {"page": "9"})
        frame = await harness.event(other, "inc")
        await other.disconnect()
        again, mount_again = await _mount_at(Paged, key, {"page": "9"})
        await again.disconnect()
    assert _probe_of(mount_same["html"]) == {"page": 2, "tab": "b", "count": 1}
    # Another URL: handle_params() wins for what it assigns; the count is kept.
    assert _probe_of(mount_other["html"]) == {"page": 9, "tab": "a", "count": 1}
    assert frame["type"] != "error"
    # The event saved the state under ?page=9: the same URL again keeps it.
    assert _probe_of(mount_again["html"]) == {"page": 9, "tab": "a", "count": 2}


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_socket_live_patch_moves_the_saved_url():
    key = await harness.new_session_key_async()
    with harness.ws_settings(MODULE):
        comm, _mount = await _mount_at(Paged, key, {"page": "1"})
        await harness.event(comm, "pick", {"value": "b"})
        await harness.event_frames(comm, "go", {"page": 7})
        await comm.disconnect()
        stored = await sync_to_async(_saved_envelope)(key)
        again, mount = await _mount_at(Paged, key, {"page": "7"})
        await again.disconnect()
    assert stored["url"] == _automatic_state.canonical_params({"page": 7})
    assert _probe_of(mount["html"]) == {"page": 7, "tab": "b", "count": 0}


# ---------------------------------------------------------------------------
# What a connected view holds for all this (#3998)
# ---------------------------------------------------------------------------


def test_the_basis_holds_one_encoding_and_digests_never_a_copy_of_the_values():
    """Every open socket keeps a basis: one (compressed) encoding to roll a
    refused turn back to, and a digest per name."""
    view = Cart()
    view.mount(None)
    view.cart = [{"sku": "item-%04d" % i, "qty": 1} for i in range(400)]
    _automatic_state.settle(view)
    basis = view.__dict__["_djust_auto_basis"]
    state = _automatic_state.project(view)
    assert type(basis.rollback) is bytes and len(basis.rollback) < len(state.text) // 4
    assert set(basis.public) == {"count", "catalog", "cart"}
    assert all(type(d) is bytes and len(d) == 16 for d in basis.public.values())
    assert type(basis.stored) is bytes and view.__dict__["_djust_auto_settled"] == basis.stored
    # Nothing on the basis is, or contains, a row.
    held = [getattr(basis, slot) for slot in type(basis).__slots__]
    assert not any(type(item) in (list, dict) and item and "sku" in repr(item) for item in held)
    # What it decodes to is the view's state at that point.
    assert basis.values() == (state.public, state.private, state.components)

    small = Cart()
    small.mount(None)
    _automatic_state.settle(small)
    assert type(small.__dict__["_djust_auto_basis"].rollback) is str
    assert small.__dict__["_djust_auto_basis"].values()[0] == {
        "count": 0,
        "catalog": "c",
        "cart": [],
    }
