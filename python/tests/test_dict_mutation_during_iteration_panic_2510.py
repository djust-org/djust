"""#2510 — a PyO3 panic ("dictionary changed size during iteration") on any
LiveView render when `TEMPLATES.OPTIONS.context_processors` has `request`
but not `auth` — a real, reported 500-on-every-page regression.

Root cause, confirmed by reading `crates/djust_core/src/lib.rs` (not
assumed): two arms of `impl FromPyObject for Value` hold a LIVE PyO3
iterator directly over a Python dict and, for each value, recursively call
`.extract::<Value>()` — which can run arbitrary Python code (any dunder
check on an unresolved `SimpleLazyObject`, e.g. `__bool__`, triggers Django's
lazy-object `_setup()`). If that code mutates the SAME dict being iterated
— which is exactly what `AuthenticationMiddleware.get_user`'s
`request._cached_user = auth.get_user(request)` does to `request.__dict__`
— the dict resizes mid-iteration and PyO3 panics.

  1. `public_dict_attrs` (the `__dict__` bulk-dump carrier, reached by any
     "truthy, non-iterable object with public attributes" — a real
     `HttpRequest` is exactly this shape).
  2. The genuine-`PyDict` arm a few lines above it (`Value::Object` for an
     actual Python dict whose VALUES include something reentrant).

Neither is Django- or `HttpRequest`-specific: any object whose attribute
access can mutate its own `__dict__` on first touch — which is precisely
what `functools.cached_property` does — hits the same class.

Minimal reproduction needs NO Django settings, no WSGI, no `django.test`
client — a real HTTP dispatch reproduces it, but `LiveViewTestClient` and
`django.test.Client` did not in manual investigation (the exact reason was
not resolved and does not matter: the bug is reachable and confirmed at the
Rust boundary directly, which is what these tests exercise).
"""

import asyncio
import functools
from pathlib import Path

import pytest

from djust import _rust

ROOT = Path(__file__).resolve().parents[2]


class _LazyLike:
    """Mimics `SimpleLazyObject`: first-touch resolution mutates the PARENT
    object's `__dict__`, exactly like `AuthenticationMiddleware.get_user`'s
    `request._cached_user = auth.get_user(request)`."""

    def __init__(self, parent):
        self._parent = parent
        self._resolved = False

    def __bool__(self):
        if not self._resolved:
            self._resolved = True
            self._parent.newly_added_key = "mutated mid-iteration"
        return True


class _RequestLike:
    """A real `HttpRequest`'s shape: several public attrs, one of them
    unresolved-lazy, sitting in `__dict__` before rendering ever starts —
    exactly as `AuthenticationMiddleware.process_request` leaves `request`
    when nothing has forced `request.user` yet."""

    def __init__(self):
        self.path = "/"
        self.method = "GET"
        self.user = _LazyLike(self)
        self.other_attr = "z"


class TestPublicDictAttrsArm:
    """`public_dict_attrs` — the `__dict__` bulk-dump carrier."""

    def test_a_lazy_attribute_that_mutates_its_parent_does_not_panic(self):
        """The template never references `.user` — `public_dict_attrs`
        dumps the WHOLE `__dict__` regardless of which attribute the
        template needs, so the hazard fires even for `{{ c.path }}`."""
        req = _RequestLike()
        html = _rust.render_template("{{ c.path }}", {"c": req})
        assert html == "/"

    def test_the_mutation_still_happens_but_does_not_corrupt_the_result(self):
        req = _RequestLike()
        _rust.render_template("{{ c.path }}", {"c": req})
        assert req.newly_added_key == "mutated mid-iteration"

    def test_cached_property_is_not_actually_the_same_hazard(self):
        """Checked, not assumed (#1468): `cached_property.__get__` writes
        `instance.__dict__[name]` only on ATTRIBUTE ACCESS
        (`obj.computed`). `public_dict_attrs` iterates `obj.__dict__`
        DIRECTLY and never calls `getattr(obj, name)` for a name that is
        not already a key — so an un-accessed `cached_property` is simply
        absent from the dict being walked, and this case cannot panic
        regardless of the fix. Kept as a documented negative, since the
        plan's own text originally claimed this was an equivalent trigger
        and it is not.
        """

        class WithCachedProperty:
            def __init__(self):
                self.plain_attr = "x"

            @functools.cached_property
            def computed(self):
                return "computed-value"

        obj = WithCachedProperty()
        assert "computed" not in obj.__dict__, "premise: not yet cached"
        html = _rust.render_template("{{ c.plain_attr }}", {"c": obj})
        assert html == "x"


class _LazyLikeDictValue:
    """`_LazyLike`'s dict-shaped twin: the parent here is a `dict`, which
    has no attribute-assignment protocol, so the mutation must be a
    `__setitem__` call, not an attribute set.

    Writing this as a SEPARATE class rather than branching inside
    `_LazyLike` is deliberate: a version of this test that reused
    `_LazyLike` unchanged (`self._parent.newly_added_key = ...` against a
    `dict` parent) raised `AttributeError` inside `__bool__`, which the
    Rust side's `if let Ok(b) = ob.extract::<bool>()` silently swallows —
    so the mutation never actually happened and the test passed for the
    wrong reason (#1468). Caught by checking why an expected-panic case
    came back green instead of trusting it.
    """

    def __init__(self, parent):
        self._parent = parent
        self._resolved = False

    def __bool__(self):
        if not self._resolved:
            self._resolved = True
            self._parent["newly_added_key"] = "mutated mid-iteration"
        return True


class TestTheGenuinePyDictArm:
    """The `Value::Object` map-building arm for an actual Python `dict`,
    a few lines above `public_dict_attrs` in the same match chain — a
    structurally identical hazard on a different carrier."""

    def test_a_dict_value_that_mutates_the_dict_during_conversion_does_not_panic(self):
        d: dict = {}
        d["lazy"] = _LazyLikeDictValue(d)
        d["other"] = "y"
        html = _rust.render_template("{{ c.other }}", {"c": d})
        assert html == "y"

    def test_the_mutation_actually_happened(self):
        """Without this, the test above could pass because the mutation
        silently failed (exactly the bug this class's docstring
        describes), not because the fix handled it."""
        d: dict = {}
        d["lazy"] = _LazyLikeDictValue(d)
        d["other"] = "y"
        _rust.render_template("{{ c.other }}", {"c": d})
        assert d.get("newly_added_key") == "mutated mid-iteration"


class TestReentrancyDoesNotOverreach:
    """The fix's scope is "don't panic on THIS dict resizing mid-iteration",
    not "refuse all reentrancy" — an attribute mutating some OTHER,
    unrelated dict must keep working exactly as it does today."""

    def test_mutating_an_unrelated_dict_is_unaffected(self):
        unrelated: dict = {}

        class MutatesElsewhere:
            def __init__(self):
                self.a = "a-value"
                self.b = _Toucher(unrelated)

        class _Toucher:
            def __init__(self, target):
                self._target = target

            def __bool__(self):
                self._target["touched"] = True
                return True

        obj = MutatesElsewhere()
        html = _rust.render_template("{{ c.a }}", {"c": obj})
        assert html == "a-value"
        assert unrelated == {"touched": True}


class _IndexTrigger:
    """A second flavor of reentrant mutation: `__index__` (invoked by
    `.extract::<i64>()`), rather than `__bool__`. The `fast_json_dumps` /
    `serialize_models_fast` / actor-dispatch converters below check `i64`
    before `bool`, so a `__bool__`-only trigger like `_LazyLike` would never
    reach them — this is the shape that actually exercises those paths.
    """

    def __init__(self, owner_dict):
        self._owner_dict = owner_dict
        self._done = False

    def __index__(self):
        if not self._done:
            self._done = True
            self._owner_dict["late"] = True
        return 42


class TestStage11ReviewFoundThreeMoreSites:
    """Stage-11 review of the #2510 fix caught a false completeness claim in
    the first commit's own message: "grepped every remaining `.iter()` in
    the conversion boundary" was scoped to `djust_core/src/lib.rs` only.
    The identical bug shape — a live PyO3 dict/list iterator plus a
    recursive conversion that can run arbitrary Python — existed
    unfixed in THREE more converters in `crates/djust_live/src/`, one of
    which (`fast_json_dumps`) is used in shipped example code today with no
    opt-in gate. All confirmed by the reviewer with a working reproducer,
    independently re-confirmed here, and fixed in the same PR rather than
    deferred — the fix shape is identical and mechanical.
    """

    def test_fast_json_dumps_does_not_panic(self):
        d = {}
        d["x"] = _IndexTrigger(d)
        d["other"] = "y"
        result = _rust.fast_json_dumps(d)
        assert '"other":"y"' in result
        assert '"x":42' in result
        assert d.get("late") is True

    def test_serialize_models_fast_does_not_panic(self):
        d = {}
        d["x"] = _IndexTrigger(d)
        d["other"] = "y"
        result = _rust.serialize_models_fast([d])
        assert '"other":"y"' in result
        assert '"x":42' in result

    def test_the_actor_dispatch_path_does_not_panic(self):
        """`use_actors` defaults to `False` (opt-in), so this path is not
        the default request flow — still a real, reachable bug once
        opted in, and the fix is the same shape."""
        import asyncio

        async def _run():
            handle = await _rust.create_session_actor("test-2510-actor")
            params: dict = {}
            params["x"] = _IndexTrigger(params)
            params["other"] = "y"
            # A bogus view module is enough to exercise the params
            # conversion; whether mount() itself succeeds or raises for an
            # unrelated reason is not what this test is about.
            try:
                await handle.mount("bogus.module.NoSuchView", params)
            except Exception:
                pass
            assert params.get("late") is True

        asyncio.run(_run())


class _MutOnStr:
    """A THIRD flavor of reentrant mutation trigger: `__str__`, invoked by
    `normalize_value`'s fallback `value.str()?` arm — which checks strict
    `is_instance_of` for primitives first (not the `.extract::<T>()`
    coercion the other converters use), so neither `_LazyLike`'s `__bool__`
    nor `_IndexTrigger`'s `__index__` reaches this specific path.
    """

    def __init__(self, owner_dict):
        self._owner_dict = owner_dict
        self._fired = False

    def __str__(self):
        if not self._fired:
            self._fired = True
            self._owner_dict["sneaky"] = True
        return "stringified"


class TestReReviewFoundAFourthSite:
    """Re-review of the Stage-11 fix pass found a FOURTH site — in the same
    file (`model_serializer.rs`) the fix pass had already touched.
    `serialize_models_fast` was fixed; its sibling export
    `serialize_models_to_list` routes through entirely separate, unpatched
    helpers (`normalize_dict`/`normalize_value`) that nobody had grepped for
    yet, because the prior sweep's file list happened to stop at the
    functions the earlier repros exercised rather than every function in
    the file. Same shape, same fix, third round of "found more" on this
    single PR — recorded here as the finding it is, not smoothed over.
    """

    def test_serialize_models_to_list_does_not_panic(self):
        d = {}
        d["z"] = _MutOnStr(d)
        d["k2"] = "v2"
        result = _rust.serialize_models_to_list([d])
        assert result == [{"z": "stringified", "k2": "v2"}]
        assert d.get("sneaky") is True


class TestASecondReReviewFoundTheOriginalBugStillLiveAtTheTopLevel:
    """A THIRD review round found the most significant instance yet: the
    literal ORIGINAL #2510 trigger — a top-level context dict mutating
    itself — was still exploitable, because fixing every NESTED arm inside
    `impl FromPyObject for Value` (`public_dict_attrs`, the nested-`PyDict`
    arm) does nothing for the OUTERMOST container. `render_template`,
    `render_template_with_dirs`, and `RustLiveView.update_state` all took
    their context as `HashMap<String, Value>` (directly, or via a local
    `.extract()` call) — PyO3's OWN blanket `HashMap<K, V>: FromPyObject`
    impl does the identical live-iterator-plus-recursive-extraction as
    every hand-written loop this bug class was found in, and it is
    dependency code: no `impl FromPyObject for Value` fix can ever reach
    it, no `guard_panic` wrapper can catch it (the panic happens in PyO3's
    own FFI argument extraction, before any function body runs).

    Fixed by changing each entry point to accept `&Bound<PyAny>`/cast to
    `&Bound<PyDict>` and doing the conversion by hand via
    `snapshot_context_to_value_hashmap`, mirroring every other fix in this
    file. `update_state`'s Rust-only sibling caller (`update_state_rust`,
    used by other Rust crates with no Python object at all, so no
    reentrancy risk) required splitting the shared logic into
    `apply_state_update` so its signature could stay unchanged.
    """

    def test_render_template_top_level_mutation_does_not_panic(self):
        d: dict = {}
        d["trigger"] = _IndexTrigger(d)
        d["other"] = "y"
        assert _rust.render_template("{{ other }}", d) == "y"
        assert d.get("late") is True

    def test_render_template_with_dirs_top_level_mutation_does_not_panic(self):
        d: dict = {}
        d["trigger"] = _IndexTrigger(d)
        d["other"] = "y"
        assert _rust.render_template_with_dirs("{{ other }}", d, []) == "y"

    def test_update_state_top_level_mutation_does_not_panic(self):
        d: dict = {}
        d["trigger"] = _IndexTrigger(d)
        d["other"] = "y"
        view = _rust.RustLiveView("<p>{{ other }}</p>", [])
        view.update_state(d)
        assert view.render() == "<p>y</p>"
        assert d.get("late") is True


class TestTheTwoHandWrittenClassBSitesTheThirdRoundFound:
    """The same review round found two more hand-written-loop instances,
    both unrelated to the `HashMap<String, Value>` parameter-type class
    above: `registry.rs`'s assign-tag-handler result coercion (a PUBLIC
    extension point — a handler author's own return value), and
    `serialize_context`/`serialize_python_value`.
    """

    def test_an_assign_handler_whose_return_value_mutates_itself_does_not_panic(self):
        class Handler:
            def render(self, args, context):
                out: dict = {}
                out["trigger"] = _IndexTrigger(out)
                out["other"] = "y"
                return out

        _rust.register_assign_tag_handler("test2510assign", Handler())
        try:
            html = _rust.render_template("{% test2510assign %}{{ other }}", {})
            assert html == "y"
        finally:
            _rust.unregister_assign_tag_handler("test2510assign")

    def test_serialize_context_does_not_panic(self):
        d: dict = {}
        d["trigger"] = _IndexTrigger(d)
        d["other"] = "y"
        result = _rust.serialize_context(d)
        assert result == {"trigger": 42, "other": "y"}
        assert d.get("late") is True


class _RemoveTrigger:
    """`_IndexTrigger`'s inverse: its `__index__` DELETES a sibling key from
    the owner dict rather than adding one. Pins the second half of the
    snapshot contract, which needs a key that is present when the snapshot
    is taken and gone by the time the loop reaches it — so the doomed key
    must be inserted AFTER the trigger (dict order is insertion order).
    """

    def __init__(self, owner_dict, doomed_key):
        self._owner_dict = owner_dict
        self._doomed_key = doomed_key
        self._done = False

    def __index__(self):
        if not self._done:
            self._done = True
            del self._owner_dict[self._doomed_key]
        return 42


class TestTheSnapshotContractIsPinned:
    """Stage-11 review of PR #2514 approved the fix but found the contract
    it introduces UNPINNED (finding 1): with the snapshot, a key ADDED to
    the context mid-conversion is silently absent from the render, and a
    key REMOVED mid-conversion still renders from the snapshot. Neither was
    asserted anywhere, so a future change (re-reading the live dict after
    each value, say) could flip either half silently. Both halves are
    pinned here on both Python entry points that route through
    `snapshot_context_to_value_hashmap` — `render_template` and
    `RustLiveView.update_state` — and stated in that helper's doc comment.

    Gate-off (#1468), three ways: reverting the helper to a live
    `context.iter()` fails all four (PanicException); making it skip keys no
    longer in the dict fails exactly the two `removed` cases; making it
    re-scan the dict for keys added since the snapshot fails exactly the two
    `added` cases.
    """

    def test_render_template_a_key_added_mid_conversion_is_absent_from_the_render(self):
        d: dict = {}
        d["trigger"] = _IndexTrigger(d)
        d["other"] = "y"
        assert _rust.render_template("[{{ late }}][{{ other }}]", d) == "[][y]"
        # The mutation DID happen — the render just never saw it.
        assert d.get("late") is True

    def test_render_template_a_key_removed_mid_conversion_still_renders_from_the_snapshot(
        self,
    ):
        d: dict = {}
        d["trigger"] = _RemoveTrigger(d, "doomed")
        d["doomed"] = "still-here"
        assert _rust.render_template("[{{ doomed }}]", d) == "[still-here]"
        assert "doomed" not in d

    def test_update_state_a_key_added_mid_conversion_is_absent_from_the_render(self):
        d: dict = {}
        d["trigger"] = _IndexTrigger(d)
        d["other"] = "y"
        view = _rust.RustLiveView("<p>[{{ late }}][{{ other }}]</p>", [])
        view.update_state(d)
        assert view.render() == "<p>[][y]</p>"
        assert d.get("late") is True

    def test_update_state_a_key_removed_mid_conversion_still_renders_from_the_snapshot(
        self,
    ):
        d: dict = {}
        d["trigger"] = _RemoveTrigger(d, "doomed")
        d["doomed"] = "still-here"
        view = _rust.RustLiveView("<p>[{{ doomed }}]</p>", [])
        view.update_state(d)
        assert view.render() == "<p>[still-here]</p>"
        assert "doomed" not in d


class TestTheFourSitesNoTestReached:
    """PR #2514 review, finding 3 (#1104 — N fixed sites need N tests): four
    of the snapshotted sites had no test reaching them. Each test below
    names its site, mutates the dict THAT site iterates (not a parent or a
    child of it), and was gate-off-verified by reverting only that site to
    a live `.iter()` — which fails exactly this test and no other.
    """

    def test_serialize_python_value_nested_dict_arm(self):
        """`crates/djust_live/src/lib.rs` `serialize_python_value`, the
        `PyDict` arm — reached through `serialize_context` with a NESTED
        dict value. The existing top-level test only reaches
        `serialize_context_py`'s own loop, one level up."""
        nested: dict = {}
        nested["trigger"] = _IndexTrigger(nested)
        nested["other"] = "y"
        result = _rust.serialize_context({"outer": nested})
        assert result == {"outer": {"trigger": 42, "other": "y"}}
        assert nested.get("late") is True

    def test_python_to_value_nested_dict_arm(self):
        """`crates/djust_live/src/lib.rs` `python_to_value`, the `PyDict` arm
        — reached through `SessionActorHandle.mount` params with a NESTED
        dict value. The existing actor test mutates the top-level params
        dict, which is `python_dict_to_hashmap`'s loop, not this one."""

        async def _run():
            nested: dict = {}
            nested["trigger"] = _IndexTrigger(nested)
            nested["other"] = "y"
            handle = await _rust.create_session_actor("test-2510-nested-params")
            try:
                await handle.mount("test.View", {"nested": nested}, None)
            finally:
                await handle.shutdown()
            assert nested.get("late") is True

        asyncio.run(_run())

    def test_view_actor_sync_state_from_python(self):
        """`crates/djust_live/src/actors/view.rs`
        `ViewActor::sync_state_from_python` — the loop over the Python
        view's own `get_context_data()` result, run after every
        `SessionActorHandle.event`."""

        class View:
            def __init__(self):
                self.context = None

            def poke(self, **kwargs):
                pass

            def get_context_data(self):
                d: dict = {}
                d["trigger"] = _IndexTrigger(d)
                d["other"] = "y"
                self.context = d
                return d

        async def _run():
            handle = await _rust.create_session_actor("test-2510-view-sync")
            view = View()
            try:
                await handle.mount("test.View", {}, view)
                result = await handle.event("poke", {})
            finally:
                await handle.shutdown()
            assert "version" in result
            assert view.context.get("late") is True

        asyncio.run(_run())

    def test_component_actor_sync_state_from_python(self):
        """`crates/djust_live/src/actors/component.rs`
        `ComponentActor::sync_state_from_python` — the loop over the Python
        component's own `get_context_data()` result, run after every
        `SessionActorHandle.component_event`. The re-render after the sync
        is asserted too, so the synced state is proven to be the snapshot's."""

        class Component:
            def __init__(self):
                self.context = None

            def poke(self, **kwargs):
                pass

            def get_context_data(self):
                d: dict = {}
                d["trigger"] = _IndexTrigger(d)
                d["other"] = "y"
                self.context = d
                return d

        async def _run():
            handle = await _rust.create_session_actor("test-2510-component-sync")
            component = Component()
            try:
                view = await handle.mount("test.View", {}, None)
                view_id = view["view_id"]
                await handle.create_component(
                    view_id, "comp", "<div>[{{ other }}][{{ trigger }}]</div>", {}, component
                )
                html = await handle.component_event(view_id, "comp", "poke", {})
            finally:
                await handle.shutdown()
            assert html == "<div>[y][42]</div>"
            assert component.context.get("late") is True

        asyncio.run(_run())


class TestTheSnapshotRunsInsideGuardPanic:
    """PR #2514 review, finding 4: `render_template` and
    `render_template_with_dirs` called `snapshot_context_to_value_hashmap`
    BEFORE entering their `guard_panic` closure, so a panic anywhere in the
    conversion — which runs arbitrary Python through every value's dunders
    — would have escaped as a `pyo3_runtime.PanicException` (a
    `BaseException` that `except Exception` does not catch) instead of the
    `RuntimeError` every other engine panic becomes. No known input panics
    inside the snapshot today (that is what the snapshot is for), so the
    change is structural and this pin is a source-level one: the call must
    sit lexically inside the closure. Same idiom as
    `test_bool_before_int_converters_2212.py`.
    """

    LIB_RS = ROOT / "crates" / "djust_live" / "src" / "lib.rs"

    @pytest.mark.parametrize("entry", ["render_template", "render_template_with_dirs"])
    def test_the_snapshot_call_is_inside_the_guard_panic_closure(self, entry):
        src = self.LIB_RS.read_text()
        start = src.index(f"\nfn {entry}(")
        # The first column-0 closing brace after the signature ends the fn.
        end = src.index("\n}\n", start)
        body = src[start:end]
        guard = body.index(f'guard_panic("{entry}"')
        snapshot = body.index("snapshot_context_to_value_hashmap(")
        assert snapshot > guard, (
            f"{entry} converts its context BEFORE entering guard_panic; a panic in the "
            f"conversion would escape as PanicException (PR #2514 review, finding 4)"
        )
