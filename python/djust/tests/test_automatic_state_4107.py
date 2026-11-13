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
import uuid
from decimal import Decimal
from typing import Any, Dict, List

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth.models import AnonymousUser, User
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory

from djust import LiveView, _automatic_state, _per_turn, event_handler
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


def _request(method: str, key: str | None, event: str = "", params: Any = None, path=harness.PATH):
    factory = RequestFactory()
    if method == "GET":
        request = factory.get(path)
    else:
        request = factory.post(
            path,
            data=json.dumps(params or {}),
            content_type="application/json",
            HTTP_X_DJUST_EVENT=event,
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
            {"view": f"{MODULE}.{view_class.__name__}", "_djust_url": url},
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


@pytest.mark.django_db
def test_a_legacy_view_keeps_its_state_over_the_fallback():
    view, key, _ = _fallback(LegacyCounter, [("inc",), ("inc",), ("inc",)])
    assert view.count == 3
    assert _stored_values(key) == {"count": 3}


# ---------------------------------------------------------------------------
# What is saved, and what is not
# ---------------------------------------------------------------------------


class Names(_Base):
    shown = ("count",)
    limit = 5  # a plain class-level default
    temporary_assigns = {"flash_rows": []}

    @property
    def doubled(self):
        return self.count * 2

    def mount(self, request, **kwargs):
        self.count = 0
        self.limit = 7
        self._private = "p"
        self.password = "hunter2"
        self.is_staff = True
        self.flash_rows = ["x"]
        self.page_title = "Title"

    @event_handler()
    def inc(self, **kwargs):
        self.count += 1


@pytest.mark.django_db
def test_only_public_serializable_application_attributes_are_stored():
    _view, key, _ = _fallback(Names, [("inc",)])
    assert _stored_values(key) == {"count": 1, "limit": 7}


@pytest.mark.django_db
def test_unserializable_values_are_skipped_named_once_and_never_fail(caplog):
    _automatic_state._NOTICED.clear()
    with caplog.at_level(logging.DEBUG, logger="djust.automatic_state"):
        view, key, _ = _fallback(Unserializable, [("inc",), ("inc",)])
    assert view.count == 2
    assert _stored_values(key) == {"count": 2}
    text = [record.getMessage() for record in caplog.records]
    for name in ("price", "viewer", "mixed"):
        assert sum(f"Unserializable.{name} " in line for line in text) == 1, (name, text)
    assert "1.50" not in "".join(text)


@pytest.mark.django_db
def test_request_user_lazy_queryset_and_model_never_enter_the_store():
    """#1786's shapes: nothing request-scoped is stored, by type, even nested."""
    from django.utils.functional import SimpleLazyObject, lazystr

    class Scoped(_Base):
        shown = ("count",)

        def mount(self, request, **kwargs):
            self.count = 0
            self.req = request
            self.viewer = request.user
            self.lazy_user = SimpleLazyObject(lambda: request.user)
            self.label = lazystr("lazy")
            self.rows = User.objects.all()
            self.row = User(username="unsaved")
            self.holder = {"user": request.user}
            self.listed = [User(username="in-a-list")]
            self.session_ref = request.session

        @event_handler()
        def inc(self, **kwargs):
            self.count += 1

    _view, key, _ = _fallback(Scoped, [("inc",)])
    assert _stored_values(key) == {"count": 1}
    assert "AnonymousUser" not in json.dumps(_stored(key))


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


def _tamper(key: str, **values: Any) -> None:
    session = SessionStore(key)
    (name,) = [n for n in session.keys() if n.startswith("_djust_auto_")]
    envelope = session[name]
    envelope["values"].update(values)
    session[name] = envelope
    session.save()


@pytest.mark.django_db
def test_a_stored_name_the_view_would_not_save_is_not_put_back():
    view, key, _ = _fallback(Names, [("inc",)])
    _tamper(
        key,
        _private="injected",
        __class__="x",
        doubled=99,  # a property
        inc="not a method any more",
        mount="x",
        request="x",
        template_name="evil.html",
        login_required=False,
        password="injected",
        flash_rows=["injected"],
        brand_new="allowed: a handler may create attributes",
        _framework_attrs=[],
        exposure_policy="legacy",
    )
    request = _request("POST", key, "inc")
    restored = Names()
    assert restored.post(request).status_code == 200
    assert restored.count == 2
    assert restored._private == "p" and restored.password == "hunter2"
    assert restored.doubled == 4 and callable(restored.inc) and callable(restored.mount)
    assert restored.exposure_policy == "explicit"
    assert restored.template_name != "evil.html"
    assert restored.flash_rows != ["injected"]
    assert restored.brand_new.startswith("allowed")


@pytest.mark.django_db
def test_a_field_with_its_own_persist_is_not_restored_from_the_automatic_store():
    class Strict(_Base):
        shown = ("count",)
        token = state("server-default", persist="client", client=True)
        scratch = state(0, persist=False)

        def mount(self, request, **kwargs):
            self.count = 0

        @event_handler()
        def inc(self, **kwargs):
            self.count += 1

    _view, key, _ = _fallback(Strict, [("inc",)])
    assert _stored_values(key) == {"count": 1}
    _tamper(key, token="forged", scratch=41)
    view = Strict()
    view.post(_request("POST", key, "inc"))
    assert view.token == "server-default" and view.scratch == 0 and view.count == 2


@pytest.mark.django_db
def test_stored_values_are_revalidated_and_a_bad_envelope_is_ignored():
    _view, key, _ = _fallback(Counter, [("inc",)])
    session = SessionStore(key)
    (name,) = [n for n in session.keys() if n.startswith("_djust_auto_")]
    for change in (
        {"version": 99},
        {"code": "0" * 64},
        {"binding": "0" * 64},
        {"created": 2**40},
        {"values": [1, 2]},
        {"extra": 1},
    ):
        envelope = dict(SessionStore(key)[name])
        envelope.update(change)
        broken = SessionStore(key)
        broken[name] = envelope
        broken.save()
        view = Counter()
        assert view.post(_request("POST", key, "nothing")).status_code == 200
        assert view.count == 0, change
        _fallback_again = SessionStore(key)
        _fallback_again[name] = session[name]
        _fallback_again.save()


# ---------------------------------------------------------------------------
# Binding, page loads, deploys
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_state_is_bound_to_the_user_and_the_route():
    _view, key, _ = _fallback(Counter, [("inc",), ("inc",)])
    user = User.objects.create(username="auto-4107-other")
    other = _request("POST", key, "nothing")
    other.user = user
    view = Counter()
    view.post(other)
    assert view.count == 0  # another principal on the same session key
    elsewhere = _request("POST", key, "nothing", path="/another-route/")
    view = Counter()
    view.post(elsewhere)
    assert view.count == 0
    same = Counter()
    same.post(_request("POST", key, "nothing"))
    assert same.count == 2


@pytest.mark.django_db
def test_a_page_load_starts_fresh_and_drops_the_stored_state():
    _view, key, _ = _fallback(Counter, [("inc",)])
    assert _stored(key)
    assert Counter().get(_request("GET", key)).status_code == 200
    assert not _stored(key)


@pytest.mark.django_db
def test_a_view_that_changes_nothing_stores_nothing():
    _view, key, _ = _fallback(Counter, [("nothing",), ("nothing",)])
    assert not _stored(key)


def test_the_class_digest_follows_the_code_not_the_file_position():
    def build(body: str, pad: str = "") -> type:
        namespace: Dict[str, Any] = {"LiveView": LiveView, "event_handler": event_handler}
        source = (
            pad
            + "class Shop(LiveView):\n"
            + "    exposure_policy = 'explicit'\n"
            + "    TAGS = frozenset({'a', 'b', 'c', 'd'})\n"
            + "    def mount(self, request, **kwargs):\n"
            + "        self.items = []\n"
            + "    @event_handler()\n"
            + "    def add(self, **kwargs):\n"
            + f"        {body}\n"
        )
        exec(compile(source, "shop.py", "exec"), namespace)  # noqa: S102 — fixed test source
        namespace["Shop"].__module__ = "shop"
        return namespace["Shop"]

    same = _automatic_state.class_digest(build("self.items.append(1)"))
    assert (
        _automatic_state.class_digest(build("self.items.append(1)", pad="\n\n\n# moved\n")) == same
    )
    assert _automatic_state.class_digest(build("self.items.append(2)")) != same
    assert _automatic_state.class_digest(build("self.items.append({'price': 1})")) != same


@pytest.mark.django_db
def test_the_class_digest_is_the_same_before_and_after_a_render():
    """The framework caches per-class facts ON the class after a render
    (``_djust_*``). A process that has rendered and one that has not must agree,
    or state saved by one is dropped by the other."""

    class Fresh(Declared):
        pass

    before = _automatic_state._compute_class_digest(Fresh)
    assert Fresh().get(_request("GET", None)).status_code == 200
    # What the socket path leaves on the class (seen across two real processes).
    Fresh._djust_rendered_state_cache = ("kept", "scratch")
    Fresh._djust_descriptor_fields_cache = {"kept": ("state", "_state_kept")}
    assert _automatic_state._compute_class_digest(Fresh) == before
    Fresh.page_size = 20  # an application class attribute does change it
    assert _automatic_state._compute_class_digest(Fresh) != before


def test_the_class_digest_is_equal_across_processes_with_other_hash_seeds():
    import os
    import subprocess
    import sys

    program = (
        "import django, os\n"
        "os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'demo_project.settings')\n"
        "django.setup()\n"
        "from djust import _automatic_state\n"
        "from djust.tests.test_automatic_state_4107 import Names, Rows\n"
        "print(_automatic_state.class_digest(Names), _automatic_state.class_digest(Rows))\n"
    )
    seen = set()
    for seed in ("1", "2", "3"):
        env = dict(os.environ, PYTHONHASHSEED=seed, PYTHONPATH=os.pathsep.join(sys.path))
        out = subprocess.run(  # noqa: S603 — the test's own interpreter and source
            [sys.executable, "-c", program], env=env, capture_output=True, text=True, check=True
        )
        seen.add(out.stdout.strip().splitlines()[-1])
    assert len(seen) == 1, seen


@pytest.mark.django_db
def test_state_written_by_other_code_of_the_class_is_dropped(monkeypatch):
    _view, key, _ = _fallback(Counter, [("inc",), ("inc",)])

    @event_handler()
    def inc(self, **kwargs):
        self.count += 10  # the deploy changed the handler

    monkeypatch.setattr(Counter, "inc", inc)
    view = Counter()
    view.post(_request("POST", key, "inc"))
    assert view.count == 10  # mount()'s 0, not the old code's 2
    monkeypatch.undo()
    # The old version, still live during a rolling deploy, drops the new one's.
    view = Counter()
    view.post(_request("POST", key, "inc"))
    assert view.count == 1


@pytest.mark.django_db
def test_bumping_the_schema_version_drops_stored_state(monkeypatch):
    _view, key, _ = _fallback(Counter, [("inc",)])
    monkeypatch.setattr(Counter, "exposure_schema_version", 2, raising=False)
    view = Counter()
    view.post(_request("POST", key, "nothing"))
    assert view.count == 0


# ---------------------------------------------------------------------------
# Size, cost, capture
# ---------------------------------------------------------------------------


class Big(_Base):
    shown = ("count",)

    def mount(self, request, **kwargs):
        self.count = 0
        self.small = "s"
        self.rows = []

    @event_handler()
    def grow(self, **kwargs):
        self.count += 1
        self.rows = ["x" * 1000] * 80  # ~80 KB: over the 64 KB budget


@pytest.mark.django_db
def test_a_value_over_the_limit_is_left_out_and_the_event_succeeds(caplog):
    _automatic_state._NOTICED.clear()
    with caplog.at_level(logging.WARNING, logger="djust.automatic_state"):
        view, key, _ = _fallback(Big, [("grow",), ("grow",)])
    assert view.count == 2
    assert _stored_values(key) == {"count": 2, "small": "s"}
    assert sum("Big.rows " in record.getMessage() for record in caplog.records) == 1


@pytest.mark.django_db
def test_the_session_is_not_written_when_the_state_did_not_change(monkeypatch):
    from djust import _session_merge

    writes: List[int] = []
    real = _session_merge.save_merged
    monkeypatch.setattr(
        _session_merge, "save_merged", lambda *a, **k: (writes.append(1), real(*a, **k))[1]
    )
    _fallback(Counter, [("inc",), ("nothing",), ("nothing",), ("inc",)])
    assert len(writes) == 2


@pytest.mark.django_db
def test_without_the_shortcut_every_turn_writes(monkeypatch):
    """Gate-off for ``unchanged_save_skip``: the write it saves comes back."""
    from djust import _session_merge

    writes: List[int] = []
    real = _session_merge.save_merged
    monkeypatch.setattr(
        _session_merge, "save_merged", lambda *a, **k: (writes.append(1), real(*a, **k))[1]
    )
    monkeypatch.setattr(_per_turn, "disabled", frozenset({"unchanged_save_skip"}))
    _fallback(Counter, [("inc",), ("nothing",), ("nothing",), ("inc",)])
    assert len(writes) == 4


@pytest.mark.django_db
def test_an_unchanged_state_is_rewritten_once_half_its_lifetime_has_passed(monkeypatch):
    _view, key, _ = _fallback(Counter, [("inc",)])
    created = next(iter(_stored(key).values()))["created"]
    real = _automatic_state.time.time
    monkeypatch.setattr(_automatic_state.time, "time", lambda: real() + 1900)  # > 3600 / 2
    view = Counter()
    view.post(_request("POST", key, "nothing"))
    assert view.count == 1
    assert next(iter(_stored(key).values()))["created"] > created


@pytest.mark.django_db
def test_what_is_stored_does_not_share_references_with_the_view():
    """Mutation after capture: the stored copy and the basis stay as captured."""
    request = _request("POST", None, "add")
    view = EditedList()
    view.mount(request)
    undo = _automatic_state.stage(view, request)
    assert undo is not None
    basis = view._djust_auto_basis
    view.items.append("after-capture")
    (envelope,) = [v for k, v in request.session.items() if k.startswith("_djust_auto_")]
    assert envelope["values"] == {"items": ["a"]}
    assert view._djust_auto_basis == basis
    # And the changed state is seen as changed, not as "already stored".
    assert _automatic_state.stage(view, request) is not None


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


@pytest.mark.django_db
def test_a_cookie_session_holds_no_automatic_state():
    from django.contrib.sessions.backends.signed_cookies import SessionStore as CookieSession

    request = _request("POST", None, "inc")
    request.session = CookieSession()
    view = Counter()
    view.mount(request)
    view.count = 3
    assert _automatic_state.stage(view, request) is None
    assert not list(request.session.keys())


# ---------------------------------------------------------------------------
# Framework attributes; the clear/stats helpers (#4087)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_per_connection_and_framework_class_attributes_are_not_stored():
    class Tz(_Base):
        shown = ("count",)

        def mount(self, request, **kwargs):
            self.count = 0
            self.client_timezone = "Europe/Paris"  # set per connection by the transport
            self.tick_interval = 5  # djust configuration, defined on a djust class
            self.login_required = False

        @event_handler()
        def inc(self, **kwargs):
            self.count += 1

    _view, key, _ = _fallback(Tz, [("inc",)])
    assert _stored_values(key) == {"count": 1}


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


@pytest.mark.django_db
def test_an_unchanged_turn_serializes_nothing_and_a_changed_one_does(monkeypatch):
    request = _request("POST", None, "x")
    view = EditedList()
    view.mount(request)
    assert _automatic_state.load(view, request) is None  # the basis: mount()'s state
    calls: List[int] = []
    real = _automatic_state.project
    monkeypatch.setattr(_automatic_state, "project", lambda v: (calls.append(1), real(v))[1])
    view._djust_auto_changed = {"_private", "_changed_keys"}  # nothing saved changed
    assert _automatic_state.stage(view, request) is None
    assert calls == [] and view._djust_auto_changed is None
    view.items.append("b")
    view._djust_auto_changed = {"items"}
    assert _automatic_state.stage(view, request) is not None
    assert calls == [1]


@pytest.mark.django_db
def test_the_changed_keys_are_one_turns_answer_and_are_used_once():
    """Stale value: an "unchanged" answer left over from an earlier turn must
    not hide a later change."""
    request = _request("POST", None, "x")
    view = EditedList()
    view.mount(request)
    _automatic_state.load(view, request)
    view._djust_auto_changed = set()
    assert _automatic_state.stage(view, request) is None  # consumed here
    view.items.append("later")  # a later turn with no answer of its own
    assert _automatic_state.stage(view, request) is not None


@pytest.mark.parametrize(
    "keys,touches",
    [
        (set(), False),
        ({"_private", "_changed_keys", "_streams"}, False),
        ({"count"}, True),
        ({"_state_count"}, True),  # a state() field's slot
        ({"_private", "brand_new"}, True),  # an attribute the handler created
    ],
)
def test_which_changed_keys_mean_saved_state_changed(keys, touches):
    assert _automatic_state._touches_saved(keys) is touches


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
    """Reading through the descriptor would add ``_state_<name>`` to the
    instance, which change detection would report as the turn's change."""
    view = Declared()
    before = set(vars(view))
    values, _encoded = _automatic_state.project(view)
    assert values == {"kept": 0}
    assert set(vars(view)) == before
