"""When each per-turn shortcut must NOT reuse what it has (#3996, #3997, #3999).

One section per thing a turn now reuses or skips. Each states the rule that
invalidates it and tests it with a change made AFTER the value was captured
(CLAUDE.md, "Mutation-after-capture test discipline"). Where a test's point is
"the stale value is not served", a sibling serves it on purpose (the freshness
check forced to pass) and asserts the stale answer then appears, so the test is
known to reach the case.

* the per-class exposure contract and provider manifest (``_ClassFacts``),
* the request-scoped fingerprints, and what #1786 still keeps out,
* ``_is_json_serializable`` answering by type,
* the event snapshot's skipped hops,
* the ids ``_collect_sub_ids`` no longer collects,
* and the reuse that was left out: the post-event snapshot's fingerprint.
"""

from __future__ import annotations

import ast
import enum
import json
import logging
import pathlib
import uuid
from collections import OrderedDict, defaultdict, namedtuple
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Callable, Dict, List

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.core import signing
from django.test import RequestFactory
from django.utils.functional import SimpleLazyObject, lazy
from django.utils.safestring import mark_safe

from djust import LiveView, _exposure, _per_turn, event_handler
from djust._exposure import (
    ExposureContract,
    ExposureError,
    FieldExposure,
    ProviderContract,
    provider_contracts_for,
    provider_owners,
)
from djust.components.descriptors.base import LiveComponent, TypedState
from djust.decorators import state
from djust.mixins import context as context_module
from djust.mixins import rust_bridge
from djust.tests import _per_turn_harness as harness

MODULE = __name__
PACKAGE = pathlib.Path(__file__).resolve().parents[1]
ws = [pytest.mark.asyncio, pytest.mark.django_db(transaction=True)]


# =====================================================================================
# 1. The per-class contract
# =====================================================================================


def new_view_class(**body: Any) -> type:
    """A fresh explicit view class, so no test sees another's cache entry."""
    namespace = {"exposure_policy": "explicit", "template": "<div dj-root></div>", **body}
    namespace.setdefault("count", state(0, persist="server"))
    return type("Fresh", (LiveView,), {"__module__": MODULE, **namespace})


def declare(cls: type, name: str, descriptor: Any) -> None:
    """Assign a ``state()`` field to an existing class, as a class body would."""
    descriptor.__set_name__(cls, name)
    setattr(cls, name, descriptor)


def summary(cls: type) -> Any:
    """Everything a contract says, as comparable data."""
    contract = ExposureContract.from_view_class(cls)
    return (
        contract.schema,
        contract.version,
        {name: (policy.persist, policy.client) for name, policy in contract.fields.items()},
        tuple(provider.name for provider in contract.providers),
        tuple(sorted(provider_owners(cls))),
    )


def uncached(cls: type) -> Any:
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(_per_turn, "disabled", frozenset({"contract_cache"}))
        return summary(cls)


class _Mixin:
    """A base the mutations below reach through."""


def _add_field(cls: type) -> None:
    declare(cls, "added", state("", persist="client", client=True))


def _remove_field(cls: type) -> None:
    del cls.count


def _replace_with_property(cls: type) -> None:
    cls.count = property(lambda self: "not state")


def _regrant(cls: type) -> None:
    declare(cls, "count", state(0, persist="client", client=True))


def _rebind_exposure_in_place(cls: type) -> None:
    cls.__dict__["count"].exposure = FieldExposure(persist="client", client=True)


def _add_field_on_base(cls: type) -> None:
    declare(cls.__mro__[1], "from_base", state(0))


def _bump_schema_version(cls: type) -> None:
    cls.exposure_schema_version = 7


def _replace_providers(cls: type) -> None:
    cls._djust_context_providers = (
        ProviderContract("tests.added", rendered=frozenset({"added_by_provider"})),
    )


def _rebase(cls: type) -> None:
    class Extra:
        extra = state(0)

    Extra.extra.__set_name__(Extra, "extra")
    cls.__bases__ = (Extra,) + cls.__bases__


MUTATIONS: Dict[str, Callable[[type], None]] = {
    "a field is added": _add_field,
    "a field is removed": _remove_field,
    "a field is replaced by a property": _replace_with_property,
    "a field is redeclared with another grant": _regrant,
    "a descriptor's exposure is reassigned": _rebind_exposure_in_place,
    "a base class gains a field": _add_field_on_base,
    "the schema version is bumped": _bump_schema_version,
    "the provider tuple is replaced": _replace_providers,
    "the bases are reassigned": _rebase,
}


def _mutable_class() -> type:
    base = type("MixinCopy", (_Mixin,), {"__module__": MODULE})
    return type(
        "Fresh",
        (base, LiveView),
        {
            "__module__": MODULE,
            "exposure_policy": "explicit",
            "template": "<div dj-root></div>",
            "count": state(0, persist="server"),
        },
    )


class TestContractCache:
    def test_an_unchanged_class_is_compiled_once(self):
        cls = new_view_class()
        first = ExposureContract.from_view_class(cls)
        assert ExposureContract.from_view_class(cls) is first
        assert provider_contracts_for(cls) is provider_contracts_for(cls)
        assert summary(cls) == uncached(cls)

    @pytest.mark.parametrize("label", sorted(MUTATIONS))
    def test_a_class_changed_after_creation_is_compiled_again(self, label):
        cls = _mutable_class()
        before = summary(cls)
        MUTATIONS[label](cls)
        after = summary(cls)
        assert after == uncached(cls), f"stale contract after: {label}"
        assert after != before, f"{label} changes nothing the contract says"

    @pytest.mark.parametrize("label", sorted(MUTATIONS))
    def test_without_the_freshness_check_each_change_would_be_missed(self, label, monkeypatch):
        """The sibling: with ``fresh()`` forced true the old contract is served,
        so the test above is exercising the check and not a recompile."""
        cls = _mutable_class()
        before = summary(cls)
        monkeypatch.setattr(_exposure._ClassFacts, "fresh", lambda self: True)
        MUTATIONS[label](cls)
        assert summary(cls) == before

    def test_a_removed_field_still_fails_an_older_contract(self):
        """``project_view`` re-reads the declarations so a compiled contract
        cannot read a field that was removed or replaced. The cache must not
        turn that into "compare the cached contract with itself"."""
        cls = new_view_class()
        held = ExposureContract.from_view_class(cls)
        view = cls()
        assert held.project_view(view, "server") == {"count": 0}
        cls.count = property(lambda self: "SECRET")
        with pytest.raises(ExposureError, match="no longer match"):
            held.project_view(view, "server")

    def test_classes_built_at_run_time_do_not_share_an_entry(self):
        first = new_view_class(count=state(0, persist="server"))
        second = new_view_class(count=state(0, persist="client", client=True))
        assert first.__qualname__ == second.__qualname__ and first.__module__ == second.__module__
        assert ExposureContract.from_view_class(first).fields["count"].persist == "server"
        assert ExposureContract.from_view_class(second).fields["count"].persist == "client"

    def test_a_subclass_has_its_own_contract_and_follows_its_base(self):
        base = new_view_class(count=state(0))
        child = type("Child", (base,), {"__module__": MODULE, "extra": state(1, persist="server")})
        assert set(ExposureContract.from_view_class(base).fields) == {"count"}
        assert set(ExposureContract.from_view_class(child).fields) == {"count", "extra"}
        declare(base, "later", state(2))
        assert set(ExposureContract.from_view_class(child).fields) == {"count", "extra", "later"}
        assert set(ExposureContract.from_view_class(base).fields) == {"count", "later"}

    def test_an_instance_attribute_cannot_reach_the_contract(self):
        cls = new_view_class()
        view = cls()
        before = summary(cls)
        view.__dict__["count"] = "instance value"
        view.__dict__["exposure_schema_version"] = 99
        view.__dict__["_djust_context_providers"] = ()
        view.added = state(0, persist="client", client=True)
        assert summary(type(view)) == before == uncached(cls)

    def test_a_failed_compile_is_not_kept(self):
        base = new_view_class(count=state(0, persist="server"))
        child = type("Child", (base,), {"__module__": MODULE})
        for _ in range(2):
            with pytest.raises(ExposureError, match="Inherited exposure grants"):
                ExposureContract.from_view_class(child)
        declare(child, "count", state(0, persist="server"))
        assert ExposureContract.from_view_class(child).fields["count"].persist == "server"

    def test_a_version_override_is_kept_apart(self):
        cls = new_view_class()
        default = ExposureContract.from_view_class(cls)
        other = ExposureContract.from_view_class(cls, version=5)
        assert (default.version, other.version) == (1, 5) and default.schema != other.schema
        assert ExposureContract.from_view_class(cls, version=5) is other
        with pytest.raises(ExposureError):
            ExposureContract.from_view_class(cls, version=-1)

    def test_the_table_is_bounded(self, monkeypatch):
        monkeypatch.setattr(_exposure, "_FACTS_LIMIT", 4)
        classes = [new_view_class() for _ in range(10)]
        for cls in classes:
            assert summary(cls) == uncached(cls)
        assert len(_exposure._FACTS) <= 4
        assert summary(classes[0]) == uncached(classes[0])

    def test_the_uncached_compilers_have_one_caller_each(self):
        """Caching sits inside the two functions every caller already uses, so
        no call site can bypass it by naming the compiler directly."""
        callers: Dict[str, List[str]] = {"_compile": [], "_compile_provider_contracts": []}
        for path in PACKAGE.rglob("*.py"):
            if "tests" in path.parts:
                continue
            source = path.read_text()
            if "_compile" not in source:
                continue
            for function in ast.walk(ast.parse(source)):
                if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for node in ast.walk(function):
                    if not isinstance(node, ast.Call):
                        continue
                    called = ast.unparse(node.func)
                    if called == "_compile_provider_contracts":
                        callers[called].append(f"{path.name}:{function.name}")
                    elif called.endswith("._compile") and (
                        path.name == "_exposure.py" or "ExposureContract" in called
                    ):
                        callers["_compile"].append(f"{path.name}:{function.name}")
        assert sorted(set(callers["_compile"])) == ["_exposure.py:from_view_class"]
        assert sorted(set(callers["_compile_provider_contracts"])) == [
            "_exposure.py:provider_contracts_for"
        ]


# =====================================================================================
# 2. Request-scoped values: in change detection, out of everything else (#1786)
# =====================================================================================

TOKEN = {"value": "TOKEN-SENTINEL-0", "box": {"n": 0, "rows": [1]}}
SEEN: List[Any] = []


class Unserializable:
    def __str__(self) -> str:
        return "UNSERIALIZABLE-SENTINEL"


def request_processor(request):
    """The request, the user, a lazy value, an object, and values that change."""
    return {
        "request": request,
        "user": request.user,
        "lazy_user": SimpleLazyObject(lambda: request.user),
        "lazy_text": SimpleLazyObject(lambda: "lazy-text"),
        "thing": Unserializable(),
        "nonce": TOKEN["value"],
        "box": TOKEN["box"],
        "constant": "CONSTANT",
        "safe_constant": mark_safe("<u>S</u>"),
    }


class Scoped(LiveView):
    exposure_policy = "explicit"
    template = (
        "<div dj-root><p>{{ nonce }}</p><b>{{ box.n }}{% for r in box.rows %}-{{ r }}{% endfor %}</b>"
        "<i>{{ constant }}{{ safe_constant }}</i><s>{{ lazy_text }}{{ thing }}{{ user }}</s>"
        "<span>{{ count }}</span><em>{{ other }}</em></div>"
    )
    count = state(0, persist="server")
    other = state(0, persist="client", client=True)

    def mount(self, request, **kwargs):
        SEEN.append(self)

    def _get_context_processors(self):
        return [MODULE + ".request_processor"]

    @event_handler()
    def inc(self, **kwargs):
        self.count += 1

    @event_handler()
    def touch_other(self, **kwargs):
        self.other += 1

    @event_handler()
    def forced(self, **kwargs):
        self.count += 1
        self._force_full_html = True


def text(frame: Dict[str, Any]) -> str:
    return json.dumps(frame, sort_keys=True)


@pytest.fixture
def scoped(monkeypatch):
    TOKEN["value"], TOKEN["box"] = "TOKEN-SENTINEL-0", {"n": 0, "rows": [1]}
    SEEN.clear()
    return harness.counting(monkeypatch)


class TestRequestScopedChanges:
    pytestmark = ws

    async def test_a_changed_value_renders_and_an_unchanged_one_is_not_sent(self, scoped):
        key = await harness.new_session_key_async()
        with harness.ws_settings(MODULE):
            comm, mount = await harness.mount_view(Scoped, key)
            try:
                assert "TOKEN-SENTINEL-0" in mount["html"] and "CONSTANT" in mount["html"]
                await harness.event(comm, "inc")
                scoped.reset()
                # Nothing a processor supplies changed: only the view's key goes.
                # (``lazy_text`` is a lazy object over a string: it is not
                # fingerprinted, because its fingerprint would keep the object,
                # so it is sent on every turn and never reported, as before.)
                quiet = await harness.event(comm, "inc")
                assert scoped.sent == [["count", "lazy_text"]]
                assert scoped.changed == [["count"]]
                assert "TOKEN-SENTINEL" not in text(quiet)
                # A scalar a processor supplies changed: it is sent AND rendered.
                TOKEN["value"] = "TOKEN-SENTINEL-1"
                changed = await harness.event(comm, "inc")
                assert sorted(scoped.sent[-1]) == ["count", "lazy_text", "nonce"]
                assert sorted(scoped.changed[-1]) == ["count", "nonce"]
                assert "TOKEN-SENTINEL-1" in text(changed)
                # The same dict object, changed in place after it was
                # fingerprinted, deep inside: the fingerprint shares nothing
                # with it, so the change is seen.
                TOKEN["box"]["rows"].append(2)
                nested = await harness.event(comm, "touch_other")
                assert "box" in scoped.sent[-1] and "box" in scoped.changed[-1]
                assert "-2" in text(nested)
                # And back to quiet.
                scoped.reset()
                await harness.event(comm, "inc")
                assert scoped.sent == [["count", "lazy_text"]]
            finally:
                await comm.disconnect()

    async def test_before_the_change_a_changed_value_was_stored_but_not_rendered(
        self, scoped, monkeypatch
    ):
        """The behaviour this replaces, kept beside it: with the shortcut off
        every processor value is sent on every turn, none is ever a changed
        key, and the page keeps the old text."""
        key = await harness.new_session_key_async()
        with harness.shortcuts(monkeypatch, "request_scoped_changes"), harness.ws_settings(MODULE):
            comm, _mount = await harness.mount_view(Scoped, key)
            try:
                await harness.event(comm, "inc")
                TOKEN["value"] = "TOKEN-SENTINEL-1"
                stale = await harness.event(comm, "inc")
                assert {"nonce", "constant", "box"} <= set(scoped.sent[-1])
                assert scoped.changed[-1] == ["count"]
                assert "TOKEN-SENTINEL-1" not in text(stale)
            finally:
                await comm.disconnect()

    async def test_a_forced_full_render_carries_the_changed_value(self, scoped):
        key = await harness.new_session_key_async()
        with harness.ws_settings(MODULE):
            comm, _mount = await harness.mount_view(Scoped, key)
            try:
                await harness.event(comm, "inc")
                TOKEN["value"] = "TOKEN-SENTINEL-1"
                frame = await harness.event(comm, "forced")
                assert "TOKEN-SENTINEL-1" in text(frame)
            finally:
                await comm.disconnect()

    async def test_request_scoped_values_stay_out_of_everything_stored(self, scoped, caplog):
        """#1786, unchanged: the request, the user, lazy values and objects are
        not fingerprinted, kept, persisted, signed or logged. Only change
        DETECTION sees a processor's plain data, as a fingerprint."""
        # INFO and above. (DEBUG logs the rendered HTML, which holds whatever
        # the template prints; that is not this change's and not asserted.)
        caplog.set_level(logging.INFO)
        key = await harness.new_session_key_async()
        with harness.ws_settings(MODULE):
            comm, _mount = await harness.mount_view(Scoped, key)
            try:
                frames = [await harness.event(comm, "inc")]
                TOKEN["value"] = "TOKEN-SENTINEL-1"
                frames.append(await harness.event(comm, "touch_other"))
                view = SEEN[-1]
                refs, fingerprints = view._prev_request_scoped
                # Kept apart from, and tied to, the ordinary bookkeeping.
                assert refs is view._prev_context_refs
                scoped_keys = set(view._context_processor_keys) | {"request"}
                assert {"user", "lazy_user", "lazy_text", "thing", "nonce", "box"} <= scoped_keys
                assert not scoped_keys & set(view._prev_context_refs)
                assert not scoped_keys & set(view._prev_context_immutables)
                assert not scoped_keys & set(view._prev_context_fingerprints)
                # Only plain data is fingerprinted; nothing lazy, no object.
                assert set(fingerprints) == {"nonce", "box", "constant", "safe_constant"}
                assert _leaves_are_values(fingerprints)
                # The snapshot that decides whether a turn renders ignores it.
                from djust.websocket import _snapshot_assigns

                assert "_prev_request_scoped" not in _snapshot_assigns(view)
                # Persisted server state: the declared field and nothing else.
                stored = json.dumps(
                    dict(await sync_to_async(SessionStore(key).load)()), default=str
                )
                for needle in (
                    "TOKEN-SENTINEL",
                    "CONSTANT",
                    "UNSERIALIZABLE",
                    "lazy-text",
                    "nonce",
                ):
                    assert needle not in stored
                # The signed client snapshot: the declared client field only.
                from djust._exposure_snapshots import _SALT

                token = frames[-1]["state_snapshot_signed"]
                payload = json.loads(signing.TimestampSigner(salt=_SALT).unsign(token))
                assert payload["state"]["values"] == {"other": 1}
                # The debug projection.
                assert set(_exposure.explicit_debug_projection(view)) == {"count", "other"}
            finally:
                await comm.disconnect()
        logged = "\n".join(record.getMessage() for record in caplog.records)
        assert "non-serializable value" not in logged
        assert "fingerprint truncated" not in logged
        for needle in ("TOKEN-SENTINEL", "UNSERIALIZABLE-SENTINEL", "lazy-text"):
            assert needle not in logged

    async def test_a_value_that_stops_being_data_is_forgotten(self, scoped, monkeypatch):
        """Data, then an object, then the same data again: the fingerprint is
        dropped while the value rides the sidecar, so the return is sent."""
        key = await harness.new_session_key_async()
        with harness.ws_settings(MODULE):
            comm, _mount = await harness.mount_view(Scoped, key)
            try:
                await harness.event(comm, "inc")
                real = TOKEN["value"]
                TOKEN["value"] = Unserializable()
                await harness.event(comm, "inc")
                assert "nonce" not in SEEN[-1]._prev_request_scoped[1]
                TOKEN["value"] = real
                scoped.reset()
                await harness.event(comm, "inc")
                assert "nonce" in scoped.sent[-1]
            finally:
                await comm.disconnect()

    async def test_discarding_the_previous_render_discards_the_fingerprints(self, scoped):
        """They are believed only beside the ``_prev_context_refs`` they were
        stored with, so a site that resets that (the static render, a restore)
        resets these without knowing about them."""
        key = await harness.new_session_key_async()
        with harness.ws_settings(MODULE):
            comm, _mount = await harness.mount_view(Scoped, key)
            try:
                await harness.event(comm, "inc")
                view = SEEN[-1]
                assert rust_bridge._trusted_request_scoped_fingerprints(
                    view, view._prev_context_refs
                )
                view._prev_context_refs = dict(view._prev_context_refs)  # a new dict
                assert (
                    rust_bridge._trusted_request_scoped_fingerprints(view, view._prev_context_refs)
                    is None
                )
                scoped.reset()
                await harness.event(comm, "inc")
                assert {"nonce", "constant", "box", "safe_constant"} <= set(scoped.sent[-1])
            finally:
                await comm.disconnect()


def _leaves_are_values(fingerprints: Dict[str, Any]) -> bool:
    """A fingerprint tree holds tags, types and exact scalar values: no object."""
    allowed = (str, int, float, bool, type(None), type)

    def walk(node: Any) -> bool:
        if isinstance(node, tuple):
            return all(walk(child) for child in node)
        return isinstance(node, allowed)

    return all(walk(fingerprint) for fingerprint in fingerprints.values())


class Tabs(LiveComponent):
    class State(TypedState):
        active: str = "overview"

    template = "<p>{{ active }}</p>"

    @event_handler()
    def select(self, value: str = "", **kwargs: Any) -> None:
        self.state.active = value


def nonce_processor(request):
    return {"nonce": TOKEN["value"]}


class ScopedComponentPage(LiveView):
    exposure_policy = "legacy"
    template = "<div dj-root><h1>{{ nonce }}</h1>{{ nav }}</div>"
    nav = Tabs()

    def mount(self, request, **kwargs):
        pass

    def _get_context_processors(self):
        return [MODULE + ".nonce_processor"]


class TestScopedComponentRender:
    pytestmark = ws

    async def _run(self, change: bool) -> Dict[str, Any]:
        TOKEN["value"] = "TOKEN-SENTINEL-0"
        key = await harness.new_session_key_async()
        with harness.ws_settings(MODULE):
            comm, _mount = await harness.mount_view(ScopedComponentPage, key)
            try:
                await harness.event(comm, "select", {"component_id": "nav", "value": "a"})
                if change:
                    TOKEN["value"] = "TOKEN-SENTINEL-1"
                return await harness.event(comm, "select", {"component_id": "nav", "value": "b"})
            finally:
                await comm.disconnect()

    async def test_a_changed_processor_value_is_not_left_stale_by_the_scoped_patch(self):
        """The scoped component render sends a patch without a Rust sync, and
        without running the processors. It now runs them and takes the page
        render when one of their values changed."""
        frame = await self._run(change=True)
        assert "TOKEN-SENTINEL-1" in text(frame)

    async def test_an_unchanged_one_keeps_the_scoped_patch(self):
        frame = await self._run(change=False)
        assert "TOKEN-SENTINEL" not in text(frame) and frame["type"] == "patch"

    async def test_without_the_check_the_value_stays_stale(self, monkeypatch):
        with harness.shortcuts(monkeypatch, "request_scoped_changes"):
            frame = await self._run(change=True)
        assert "TOKEN-SENTINEL-1" not in text(frame)


# =====================================================================================
# 3. ``_is_json_serializable`` answers by type
# =====================================================================================


class Colour(enum.IntEnum):
    RED = 1


class Label(str):
    pass


@dataclass
class Point:
    x: int = 1


Pair = namedtuple("Pair", "a b")

CORPUS: List[Any] = [
    None,
    True,
    0,
    1.5,
    float("nan"),
    "text",
    b"bytes",
    bytearray(b"x"),
    Colour.RED,
    Label("x"),
    mark_safe("<b>"),
    [],
    [1, "a", None],
    [1, object()],
    (1, 2),
    (1, {3}),
    Pair(1, 2),
    {},
    {"a": 1},
    {"a": [1, {"b": None}]},
    {1: "int key"},
    {"a": object()},
    OrderedDict(a=1),
    defaultdict(list, a=[1]),
    {1, 2},
    frozenset({1}),
    object(),
    Point(),
    Decimal("1.5"),
    date(2026, 1, 1),
    uuid.UUID(int=1),
    range(3),
    iter([1]),
    (x for x in [1]),
    lambda: 1,
    int,
    Exception("x"),
    SimpleLazyObject(lambda: "lazy"),
    SimpleLazyObject(lambda: [1, 2]),
    SimpleLazyObject(lambda: object()),
    SimpleLazyObject(lambda: AnonymousUser()),
    lazy(lambda: "promise", str)(),
    AnonymousUser(),
    RequestFactory().get("/"),
    memoryview(b"x"),
    complex(1, 2),
    type("Dynamic", (), {})(),
    [[[[object()]]]],
    {"deep": {"er": {"est": (1, 2, [3, {"x": Decimal("1")}])}}},
]


_REAL_DUMPS = json.dumps


def _reference(value: Any) -> bool:
    """``_is_json_serializable`` as it was: the structural checks, then the probe."""
    if isinstance(value, (str, int, float, bool, type(None))):
        return True
    if isinstance(value, (list, tuple)):
        return all(_reference(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(k, str) and _reference(v) for k, v in value.items())
    try:
        _REAL_DUMPS(value)
        return True
    except (TypeError, ValueError, OverflowError):
        return False


class TestSerializableByType:
    @pytest.mark.parametrize("index", range(len(CORPUS)))
    def test_the_answer_is_the_probes_answer(self, index):
        value = CORPUS[index]
        assert context_module._is_json_serializable(value) is _reference(value)

    def test_the_corpus_reaches_the_shortcut_with_both_kinds(self, monkeypatch):
        """Not vacuous: the corpus has values the shortcut answers, and for
        every one of them the probe says False (it can never say True there)."""
        probed: List[Any] = []
        real = json.dumps

        def dumps(value: Any, *args: Any, **kwargs: Any) -> Any:
            probed.append(value)
            return real(value, *args, **kwargs)

        monkeypatch.setattr(_per_turn, "disabled", frozenset({"serializable_by_type"}))
        monkeypatch.setattr(context_module.json, "dumps", dumps)
        answers = [context_module._is_json_serializable(value) for value in CORPUS]
        assert len(probed) >= 20 and answers.count(True) >= 10
        for value in probed:
            with pytest.raises((TypeError, ValueError, OverflowError)):
                real(value)

    def test_a_wrong_answer_would_be_caught(self, monkeypatch):
        """The sibling: a shortcut that answered True fails the comparison."""
        monkeypatch.setattr(_per_turn, "on", lambda name: False)
        monkeypatch.setattr(
            context_module.json, "dumps", lambda value, *a, **k: "anything is serialisable"
        )
        wrong = [
            value
            for value in CORPUS
            if context_module._is_json_serializable(value) is not _reference(value)
        ]
        assert len(wrong) >= 20


# =====================================================================================
# 4. The ids under a changed attribute, and the reuse that was left out
# =====================================================================================


class LateWriter(LiveView):
    """``get_context_data`` writes into a declared list AFTER the post-event
    snapshot was taken. The Rust sync must fingerprint the list as it is when
    it is synced; the snapshot's fingerprint of the same object is older."""

    exposure_policy = "explicit"
    template = "<div dj-root><h1>{{ title }}</h1><p>{{ rows.0.label }}</p></div>"
    title = state("a")
    rows = state(default_factory=list)

    def mount(self, request, **kwargs):
        self.rows = [{"label": "a"}]

    def _get_context_processors(self):
        return []

    def get_context_data(self, **kwargs):
        self.rows[0]["label"] = self.title.upper()
        return super().get_context_data(**kwargs)

    @event_handler()
    def retitle(self, value: str = "b", **kwargs):
        self.title = value


class TestTheSnapshotFingerprintIsNotReused:
    pytestmark = ws

    async def test_a_list_written_by_get_context_data_after_the_snapshot_renders(self):
        """Why #3999's "reuse the post-event snapshot's fingerprint in the sync"
        was left out. The handler changes only ``title``; the list is then
        changed by ``get_context_data``, between the snapshot and the sync. A
        sync that took the snapshot's fingerprint for the list would find it
        equal to the previous render's and never send the new label."""
        key = await harness.new_session_key_async()
        with harness.ws_settings(MODULE):
            comm, mount = await harness.mount_view(LateWriter, key)
            try:
                assert "<p" in mount["html"] and ">A<" in mount["html"]
                frame = await harness.event(comm, "retitle", {"value": "b"})
                patched = {p.get("text") for p in frame["patches"]}
                assert {"b", "B"} <= patched, frame
                frame = await harness.event(comm, "retitle", {"value": "c"})
                assert {"c", "C"} <= {p.get("text") for p in frame["patches"]}, frame
            finally:
                await comm.disconnect()


class Holder:
    def __init__(self, label: str) -> None:
        self.label = label


class LateInsert(LiveView):
    """``get_context_data`` puts a live object into the list after the snapshot:
    whether the list is plain data is decided when it is synced, not before."""

    exposure_policy = "legacy"
    template = "<div dj-root><p>{{ current.label }}</p><b>{{ n }}</b></div>"

    def mount(self, request, **kwargs):
        self.items = [{"plain": 1}]
        self.n = 0
        self._holder = Holder("first")

    def _get_context_processors(self):
        return []

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.n >= 2 and self._holder not in self.items:
            self.items.append(self._holder)
        context["current"] = self._holder
        return context

    @event_handler()
    def grow(self, **kwargs):
        self.n += 1
        self.items.append({"plain": self.n})
        self._holder.label = f"label {self.n}"


class TestPlainSubIds:
    pytestmark = ws

    async def _run(self, monkeypatch, *off: str) -> List[Dict[str, Any]]:
        key = await harness.new_session_key_async()
        with harness.shortcuts(monkeypatch, *off), harness.ws_settings(MODULE):
            comm, _mount = await harness.mount_view(LateInsert, key)
            try:
                return [await harness.event(comm, "grow") for _ in range(4)]
            finally:
                await comm.disconnect()

    async def test_plainness_is_decided_on_the_object_being_synced(self, monkeypatch):
        """Turns 1-2 the list is plain and the object's new label is not found
        (as before: an attribute write on an object is not seen). From turn 2
        the list holds the object, so its id is collected again and the label
        renders. On and off agree on every turn."""
        now = await self._run(monkeypatch)
        off = await self._run(monkeypatch, "plain_sub_ids")
        strip = lambda frames: [{k: v for k, v in f.items() if k != "ref"} for f in frames]  # noqa: E731
        assert strip(now) == strip(off)
        assert "label 4" in text(now[3]), "the object under the list was not found"

    def test_only_a_converted_value_skips_the_walk(self):
        """The rule, at its source: the skip is taken only inside the branch
        that saw ``fingerprint_and_convert`` convert the very value, and the
        attribute's own id is added first."""
        source = (PACKAGE / "mixins" / "rust_bridge.py").read_text()
        start = source.index('plain_on = _per_turn.on("plain_sub_ids")')
        block = source[start : source.index("context = {}", start)]
        skip = block.index("continue", block.index("if ctx_key in converted_by_fp:"))
        assert block.index("_fp_of(ctx_key, val, convert=True)") < skip
        assert block.index("changed_sub_ids.add(id(val))") < skip
        assert block.index("full_context[ctx_key] is val") < skip
        assert block.rindex("_collect_sub_ids(val, changed_sub_ids)") > skip
