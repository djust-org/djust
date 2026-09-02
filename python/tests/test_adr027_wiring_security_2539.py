"""ADR-027 movement 2 (#2539): the security requirements of the WIRING, each
with the test that proves it, run with the kill-switch **ON**.

The characterization net next door proves the flag-OFF byte identity and the
per-cell flag-ON delta. This file proves the five numbered requirements the
movement-1 Security Check posted on #2539, plus the ADR's own Security items —
and it runs them with the flag ON, because with it OFF nothing here is
reachable and every assertion would be vacuous.

One-to-one with the plan's section 3:

* 3.1 ``protect_sidecar``'s ``Err(_) => obj`` arm is the one open default —
  ``TestTheFloorFailsClosedInTheSink``. The Rust unit test proves the arm with
  a fake floor on a bare interpreter; this proves it end to end, through a
  REAL Django ``User``, on the paths a user's template takes.
* 3.2 defence-in-depth leading-underscore refusal — ``TestLeadingUnderscore``.
* 3.4 ``alters_data`` / ``do_not_call_in_templates`` — ``TestMutators``.
* 3.5 the tag-bridge sink stays closed (#2509) — ``TestTheTagBridge``.
* 3.6 the floor holds on every materialisation — ``TestTheFloorHolds``.
* 3.8 exceptions never fail open (#2506) — ``TestExceptionsNeverFailOpen``.

Section 6's two decided divergences are here too, because both are behaviour
CHANGES and a change needs a test that would go red without it:
``TestAltersDataContinues`` (6.1) and ``TestIgnoreFailuresSubstitutesNone``
(6.2).

Refs #2539, #2535 (ADR-027), #2506, #2507, #2509, #2528, #1468.
"""

from __future__ import annotations

import contextlib
from typing import Any, Callable

import pytest

pytest.importorskip("django")

from django.contrib.auth.models import User  # noqa: E402
from django.core.exceptions import PermissionDenied  # noqa: E402
from django.template import Context as DjangoContext  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402

from djust import _rust  # noqa: E402
from djust.components.base import LiveComponent  # noqa: E402
from djust.live_view import LiveView  # noqa: E402
from djust.testing import LiveViewTestClient  # noqa: E402


# ---------------------------------------------------------------------------
# The flag, and the three real entries
# ---------------------------------------------------------------------------
@contextlib.contextmanager
def resolve_lazy(enabled: bool = True):
    """Flip the kill-switch through the REAL wiring and ASSERT it landed."""
    from djust.config import config
    from djust.render_env import apply_render_env

    previous = config.get("template_resolve_lazy", False)
    config.update({"template_resolve_lazy": enabled})
    apply_render_env()
    assert _rust.resolve_lazy_enabled() is enabled, "the flag did not reach Rust"
    try:
        yield
    finally:
        config.update({"template_resolve_lazy": previous})
        apply_render_env()


def plain_render(source: str, context: dict) -> str:
    from djust.template_backend import DjustTemplateBackend

    backend = DjustTemplateBackend(
        params={"NAME": "djust", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}}
    )
    return backend.from_string(source).render(context=dict(context), request=None)


def raw_render(source: str, context: dict) -> str:
    return _rust.render_template(source, dict(context))


def liveview_render(source: str, context: dict) -> str:
    class _V(LiveView):
        def mount(self, request, **kwargs):
            pass

        def get_context_data(self, **kwargs):
            ctx = super().get_context_data(**kwargs)
            ctx.update(context)
            return ctx

    _V.template = f"<div dj-root>{source}</div>"
    client = LiveViewTestClient(_V)
    client.mount()
    html = client.render()
    import re

    match = re.search(r"<div dj-root[^>]*>(.*)</div>", html, re.S)
    assert match is not None, html
    return match.group(1)


ENTRIES = [
    pytest.param(raw_render, id="render_template"),
    pytest.param(plain_render, id="DjustTemplateBackend"),
    pytest.param(liveview_render, id="LiveView"),
]


def django_render(source: str, context: dict) -> str:
    return DjangoTemplate(source).render(DjangoContext(dict(context)))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
class Presenter:
    """#1986 vector 6: a NON-model intermediary holding a model. It has no
    proxy `__getattr__` of its own, so the floor can only be applied by the
    walk — which is exactly the arm 3.1 hardens."""

    def __init__(self, user: Any) -> None:
        self.user = user
        self._secret = "UNDERSCORE-LEAK"

    def get_user(self) -> Any:
        return self.user


class Lazy:
    @property
    def lazy_user(self) -> Any:
        return make_user()


def make_user() -> User:
    return User(username="alice", password="pbkdf2$hash", is_staff=True)


class Mutator:
    """`alters_data` mid-path (§6.1). `delete` raises if it is ever CALLED, so
    the continuation cannot be mistaken for a call."""

    def delete(self) -> None:
        raise AssertionError("alters_data callable was CALLED")

    delete.alters_data = True  # type: ignore[attr-defined]

    def __repr__(self) -> str:
        return "<Mutator>"


class Guarded:
    title = "Q3 layoffs memo"

    @property
    def is_restricted(self) -> bool:
        raise PermissionDenied("acl backend down")


class Holder:
    def __init__(self, doc: Any) -> None:
        self.doc = doc


# ---------------------------------------------------------------------------
# 3.1 / 3.6 — the serialization floor
# ---------------------------------------------------------------------------
FLOOR_SHAPES = [
    ("{{ p.user.password }}", lambda: {"p": Presenter(make_user())}),
    ("{{ p.get_user.password }}", lambda: {"p": Presenter(make_user())}),
    ("{{ p.lazy_user.password }}", lambda: {"p": Lazy()}),
    ("{% for u in us %}[{{ u.password }}]{% endfor %}", lambda: {"us": [make_user()]}),
]


@pytest.mark.django_db
class TestTheFloorHolds2539:
    """3.6: a model reached MID-WALK through a handle is still wrapped by
    `_SidecarModelProxy`, so the denylist governs however it was reached."""

    @pytest.mark.parametrize("render", ENTRIES)
    @pytest.mark.parametrize(("source", "make_ctx"), FLOOR_SHAPES, ids=[s for s, _ in FLOOR_SHAPES])
    def test_the_password_never_renders(self, render, source: str, make_ctx) -> None:
        # The premise: Django itself renders the hash, so an empty cell here
        # is djust's floor and not the template failing to resolve.
        assert "pbkdf2" in django_render(source, make_ctx())
        with resolve_lazy(True):
            out = render(source, make_ctx())
        assert "pbkdf2" not in out, f"the serialization floor leaked: {out!r}"

    @pytest.mark.parametrize("render", ENTRIES)
    def test_the_same_walk_resolves_a_public_field(self, render) -> None:
        """Non-vacuity: an engine that resolved NOTHING through a handle would
        pass every assertion above. The public field on the same object, in
        the same shape, resolves."""
        with resolve_lazy(True):
            assert render("{{ p.user.username }}", {"p": Presenter(make_user())}) == "alice"
            assert render("{{ p.get_user.username }}", {"p": Presenter(make_user())}) == "alice"


@pytest.mark.django_db
class TestTheFloorFailsClosedInTheSink2539:
    """3.1 / requirement 1: `_protect_sidecar_value` RAISING must stop the
    walk, not hand the raw object to the next segment.

    The pre-ADR walk answers `Err(_) => obj` — fail-safe for a render, which
    is fail-OPEN for a floor. `protect_sidecar_strict` answers `Invalid`.
    """

    #: The two PLAIN entries only, and that is a harness limit rather than a
    #: coverage gap. On the LiveView path `_sync_state_to_rust` calls the same
    #: `_protect_sidecar_value` while BUILDING the sidecar, so a monkeypatched
    #: exploding floor takes the state sync down before a template is parsed —
    #: the render never reaches the walk, so the assertion would measure the
    #: sync's failure and not the sink's. The sink is the same code on all
    #: three entries (`Context::walk_live` is one function with one call
    #: site, pinned in the net), and the Rust unit test drives its floor arm
    #: directly with a fake `djust.serialization`.
    PLAIN_ENTRIES = [
        pytest.param(raw_render, id="render_template"),
        pytest.param(plain_render, id="DjustTemplateBackend"),
    ]

    @pytest.mark.parametrize("render", PLAIN_ENTRIES)
    def test_a_raising_floor_renders_nothing_rather_than_the_raw_model(
        self, render, monkeypatch
    ) -> None:
        import djust.serialization as serialization

        # The control FIRST, so a harness that never reaches the floor at all
        # cannot pass the fail-closed assertion by accident.
        with resolve_lazy(True):
            assert render("{{ p.user.username }}", {"p": Presenter(make_user())}) == "alice"

        calls: list[int] = []

        def exploding(value):
            calls.append(1)
            raise RuntimeError("floor enforcement broke")

        monkeypatch.setattr(serialization, "_protect_sidecar_value", exploding)
        with resolve_lazy(True):
            out = render("{{ p.user.password }}", {"p": Presenter(make_user())})
        assert calls, "the floor was never consulted — this test proves nothing"
        assert "pbkdf2" not in out, f"the raw model flowed on past a broken floor: {out!r}"
        assert out == "", f"expected string_if_invalid, got {out!r}"


# ---------------------------------------------------------------------------
# 3.2 — the leading-underscore refusal
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestLeadingUnderscoreIsRefused2539:
    """3.2 / requirement 2. Django refuses the SPELLING at
    `Variable.__init__`, so this is parity rather than a djust-ism — and the
    engine refuses it in three independent places, which is what defence in
    depth means. The unit-level proof that the SINK itself refuses (rather
    than only the parser above it) is Rust case 13, which calls the walk
    directly; this is the end-to-end half."""

    @pytest.mark.parametrize("render", ENTRIES)
    @pytest.mark.parametrize("source", ["{{ p._secret }}", "{{ h.doc._secret }}"])
    def test_both_engines_refuse_the_spelling_at_parse_time(self, render, source: str) -> None:
        """End to end, the OUTER guard is what a user meets: both engines
        raise before a lookup runs. Django's message is "Variables and
        attributes may not begin with underscores"; djust's (#2418) says the
        same thing. So no template a user can write reaches the sink with such
        a segment — which is precisely why the sink's own refusal is defence
        in depth and has to be proven by calling it DIRECTLY (Rust case 13).
        """
        ctx = {"p": Presenter(make_user()), "h": Holder(Presenter(make_user()))}
        with pytest.raises(Exception) as django_exc:
            django_render(source, ctx)
        assert "underscore" in str(django_exc.value)
        with resolve_lazy(True), pytest.raises(Exception) as djust_exc:
            render(source, ctx)
        assert "underscore" in str(djust_exc.value)

    @pytest.mark.parametrize("render", ENTRIES)
    def test_the_public_sibling_resolves(self, render) -> None:
        """Non-vacuity: the same objects answer a PUBLIC attribute, so the
        refusals above are the guard and not a dead walk."""
        with resolve_lazy(True):
            assert render("{{ p.user.username }}", {"p": Presenter(make_user())}) == "alice"

    def test_the_sink_refuses_it_even_when_the_parser_does_not(self) -> None:
        """The defence-in-depth half, reachable from Python: a `{% for %}`
        loop variable's PATH is assembled at runtime, so the guard's position
        (first statement of the segment walk, before any item access) is what
        keeps a programmatic path from reaching `getattr(o, "_state")`. The
        exhaustive per-position proof is the Rust direct-call case; this pins
        that the refusal is in the SOURCE at that position, so a reorder
        cannot silently reopen it."""
        import pathlib

        context_rs = (
            pathlib.Path(__file__).resolve().parents[2]
            / "crates"
            / "djust_core"
            / "src"
            / "context.rs"
        ).read_text(encoding="utf-8")
        body = context_rs.split("fn walk_one_segment", 1)[1].split("\n    }\n", 1)[0]
        assert "part.starts_with('_')" in body
        assert body.index("starts_with('_')") < body.index("get_item")


# ---------------------------------------------------------------------------
# 3.4 — mutators are never auto-called
# ---------------------------------------------------------------------------
MUTATORS = ["mount", "unmount", "update", "trigger_update", "clear_context_providers"]


def make_recording_card(calls: list) -> type:
    class _Card(LiveComponent):
        template = "<b>card</b>"

        def get_context_data(self) -> dict:
            return {}

        def mount(self, **kwargs) -> None:
            calls.append("mount")

        def unmount(self) -> None:
            calls.append("unmount")
            super().unmount()

        def update(self, **kwargs):
            calls.append("update")
            return self

        def trigger_update(self) -> None:
            calls.append("trigger_update")

        def clear_context_providers(self) -> None:
            calls.append("clear_context_providers")

    return _Card


@pytest.mark.django_db
class TestMutatorsAreNeverAutoCalled2539:
    """3.4 / #2507, with the flag ON — which is when it first becomes
    load-bearing on this path: a handle is what lets a lookup REACH
    `unmount` at all."""

    @pytest.mark.parametrize("render", ENTRIES)
    @pytest.mark.parametrize("method", MUTATORS)
    def test_an_overridden_mutator_is_not_run(self, render, method: str) -> None:
        calls: list = []
        card = make_recording_card(calls)()
        with resolve_lazy(True):
            out = render("[{{ c.%s }}]" % method, {"c": card})
        assert [c for c in calls if c != "mount"] == [], f"{method} was CALLED during the render"
        assert out == "[]", f"{method} rendered {out!r}"

    @pytest.mark.parametrize("render", ENTRIES)
    def test_a_plain_method_on_the_same_object_IS_called(self, render) -> None:
        """Non-vacuity, and the sharpest form of it: the refusal is the
        `alters_data` marker, not the walk failing to reach the object."""

        class _Probe:
            def __init__(self) -> None:
                self.plain_called = False

            def plain(self) -> str:
                self.plain_called = True
                return "PLAIN"

            def guarded(self) -> str:  # pragma: no cover - must never run
                raise AssertionError("alters_data method was CALLED")

            guarded.alters_data = True  # type: ignore[attr-defined]

        probe = _Probe()
        with resolve_lazy(True):
            assert render("{{ o.plain }}", {"o": probe}) == "PLAIN"
            assert probe.plain_called is True
            assert render("{{ o.guarded }}", {"o": _Probe()}) == ""


# ---------------------------------------------------------------------------
# One expression invokes a user callable ONCE (#2507 family)
# ---------------------------------------------------------------------------
class Counting:
    """Django's `test_callables.Doodad`, as a side-effect SENTINEL.

    `__call__` counts, so "how many times did the engine invoke this" is a
    number the test can read rather than a property of the rendered bytes —
    the shape `test_template_auto_call_1985.py` uses for the same question.
    """

    def __init__(self, value: int = 42) -> None:
        self.num_calls = 0
        self.value = value

    def __call__(self) -> dict:
        self.num_calls += 1
        return {"the_value": self.value}


class CountingAttr:
    """The MID-PATH sentinel: a plain object whose ATTRIBUTE is the counting
    callable, so `{{ obj.attr }}` auto-calls it exactly once — the shape the
    lead named."""

    def __init__(self) -> None:
        self.attr = Counting()


@pytest.mark.django_db
class TestOneExpressionInvokesACallableOnce2539:
    """A template expression must invoke a user callable EXACTLY ONCE, on
    every entry, under BOTH flag states.

    This is the #2507 family — side effects during a render — rather than a
    correctness nicety, and it is a permanent pin rather than a fixed path:
    movements 3 and 4 flip the default and delete the pre-ADR walk, and this
    assertion has to survive both.

    # The regression it exists for

    ADR-027's routing point answers a dotted lookup from a live handle. Its
    first version returned `Ok(None)` for Django's `VariableDoesNotExist`,
    which is indistinguishable at the call site from "no handle here" — so
    `resolve_without_builtins` FELL THROUGH and ran the pre-ADR sidecar walk
    over the SAME object. Both walks auto-call, so `{{ d.value }}` on a
    callable object left `num_calls == 2` where Django leaves 1.

    The shapes below are chosen to drive that fall-through specifically:
    `{{ d.value }}` and `{{ d.attr.absent }}` both END in a lookup FAILURE
    after an auto-call, which is the only path that reached the second walk.
    A test using a shape that RESOLVES would pass either way and pin nothing.
    """

    #: `(source, make_ctx, read_count)` — each ending in a resolution FAILURE
    #: after at least one auto-call, which is the fall-through shape.
    SHAPES = [
        pytest.param(
            "{{ d.value }}",
            lambda: Counting(),
            lambda d: d.num_calls,
            id="root-autocall-then-missing-segment",
        ),
        pytest.param(
            "{{ d.attr.absent }}",
            lambda: CountingAttr(),
            lambda d: d.attr.num_calls,
            id="mid-path-autocall-then-missing-segment",
        ),
    ]

    @pytest.mark.parametrize("render", ENTRIES)
    @pytest.mark.parametrize("enabled", [False, True], ids=["lazy-off", "lazy-on"])
    @pytest.mark.parametrize(("source", "make_obj", "read_count"), SHAPES)
    def test_exactly_one_invocation(
        self, render, enabled: bool, source: str, make_obj, read_count
    ) -> None:
        # The premise, from Django itself: ONE invocation, and an empty cell.
        reference = make_obj()
        assert django_render(source, {"d": reference}) == ""
        assert read_count(reference) == 1, "premise: Django auto-calls exactly once"

        obj = make_obj()
        with resolve_lazy(enabled):
            out = render(source, {"d": obj})
        count = read_count(obj)
        # The SECURITY half, and it holds in BOTH flag states — this is the
        # assertion that must survive movements 3 and 4. Django invokes once,
        # so anything above one is a side effect the template did not ask for
        # (#2507). Fewer than one is the pre-ADR under-resolution the movement
        # FIXES, not a hazard: with the flag off the plain entries answer
        # `{{ d.value }}` from the eager `__dict__` dump and never reach a
        # walk at all, which is exactly net row K3.
        assert count <= 1, (
            f"the engine invoked the callable {count} times for one {source!r} — Django "
            f"invokes it once. A resolution that produced nothing is an ANSWER: if the "
            f"ADR-027 sink returns Invalid and `resolve_without_builtins` then falls "
            f"through to the pre-ADR sidecar walk, both walk the SAME object, both "
            f"auto-call, and the side effect happens twice."
        )
        if enabled:
            # The PARITY half. With the sink routed, the count is Django's
            # exactly — which is what makes the bound above non-vacuous: it is
            # measured against a path that really does invoke.
            assert count == 1, (
                f"with the ADR-027 flag ON the engine invoked the callable {count} times "
                f"for one {source!r}; Django invokes it once"
            )
            assert out == "", out

    @pytest.mark.parametrize("render", ENTRIES)
    @pytest.mark.parametrize("enabled", [False, True], ids=["lazy-off", "lazy-on"])
    def test_a_resolving_expression_also_invokes_once(self, render, enabled: bool) -> None:
        """Non-vacuity: the count is not one because the engine never reached
        the object. The SUCCEEDING spelling of the same lookup renders the
        called value and still counts one."""
        obj = Counting()
        with resolve_lazy(enabled):
            assert render("{{ d.the_value }}", {"d": obj}) == "42"
        assert obj.num_calls == 1


# ---------------------------------------------------------------------------
# 3.5 — the tag bridge stays closed (#2509)
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestTheTagBridgeStaysClosed2539:
    """3.5: a handle can never reach a custom-tag handler, because
    `IntoPyObject for Value` maps an `Encoded` to its DISPLAY string. The
    structural pin is in the net; this is the behavioural half.

    **A named behaviour change**: a value that crossed to a handler as a
    `dict` (the `__dict__` bulk dump) now crosses as the display string. That
    is the #2509 chokepoint NARROWING — the safe direction — and it is
    declared rather than discovered.
    """

    def test_a_handler_receives_a_string_and_never_the_object(self) -> None:
        seen: list = []

        class _Probe:
            def render(self, args, _context):
                seen.append(args[0])
                return "ok"

        _rust.register_tag_handler("a2539_probe", _Probe())
        try:
            with resolve_lazy(True):
                out = _rust.render_template("{% a2539_probe o %}", {"o": Presenter(make_user())})
        finally:
            _rust.unregister_tag_handler("a2539_probe")
        assert out == "ok"
        assert len(seen) == 1
        crossed = seen[0]
        assert isinstance(crossed, str), (
            f"a handle-bearing value crossed to a tag handler as {type(crossed).__name__} — "
            f"`IntoPyObject for Value` must keep mapping an Encoded to its display string (#2509)"
        )
        assert "Presenter object at" in crossed

    def test_the_flag_is_what_narrowed_it(self) -> None:
        """The declared behaviour change, made explicit: with the flag OFF the
        same value crosses as the `dict` of its `__dict__`; with it ON it
        crosses as the display string. The exposure NARROWS — a handler that
        used to receive a live-ish mapping of the object's attributes now
        receives a string — and that is stated here rather than discovered."""
        seen: list = []

        class _Probe:
            def render(self, args, _context):
                seen.append(args[0])
                return "ok"

        _rust.register_tag_handler("a2539_probe_off", _Probe())
        try:
            with resolve_lazy(False):
                _rust.render_template("{% a2539_probe_off o %}", {"o": Presenter(make_user())})
        finally:
            _rust.unregister_tag_handler("a2539_probe_off")
        crossed_off = seen[0]
        assert "alice" in str(crossed_off), (
            "the flag-OFF shape stopped carrying the object's ATTRIBUTES — the declared "
            f"behaviour change above is no longer the change it describes: {crossed_off!r}"
        )
        # And the flag-ON shape carries none of them. THIS is the narrowing:
        # a handler that used to be handed the presenter's whole `__dict__`
        # (with the user's fields inside it) is now handed `str(o)`.
        with resolve_lazy(True):
            _rust.register_tag_handler("a2539_probe_on", _Probe())
            try:
                _rust.render_template("{% a2539_probe_on o %}", {"o": Presenter(make_user())})
            finally:
                _rust.unregister_tag_handler("a2539_probe_on")
        crossed_on = seen[1]
        assert "alice" not in str(crossed_on), crossed_on
        assert crossed_on != crossed_off


# ---------------------------------------------------------------------------
# 3.8 — exceptions never fail open (#2506)
# ---------------------------------------------------------------------------
GUARD_SOURCE = "{% if not d.doc.is_restricted %}{{ d.doc.title }}{% else %}(withheld){% endif %}"


@pytest.mark.django_db
class TestExceptionsNeverFailOpen2539:
    """3.8 / #2506, with the flag ON. An authorization check spelled as an
    exception must not render as an authorised value."""

    @pytest.mark.parametrize("render", ENTRIES)
    def test_the_gated_content_never_renders(self, render) -> None:
        assert django_render(GUARD_SOURCE, {"d": Holder(Guarded())}) == "(withheld)"
        with resolve_lazy(True), pytest.raises(Exception) as exc:
            render(GUARD_SOURCE, {"d": Holder(Guarded())})
        cause = exc.value.__cause__ if type(exc.value) is Exception else exc.value
        assert isinstance(cause, PermissionDenied)
        assert "Q3 layoffs memo" not in str(exc.value)

    @pytest.mark.parametrize("render", ENTRIES)
    def test_a_non_raising_gate_renders_both_ways(self, render) -> None:
        class _Open:
            title = "public memo"
            is_restricted = False

        class _Shut(_Open):
            is_restricted = True

        with resolve_lazy(True):
            for doc, expected in ((_Open(), "public memo"), (_Shut(), "(withheld)")):
                assert django_render(GUARD_SOURCE, {"d": Holder(doc)}) == expected
                assert render(GUARD_SOURCE, {"d": Holder(doc)}) == expected

    @pytest.mark.parametrize("render", ENTRIES)
    def test_a_silent_failure_renders_empty_and_a_loud_one_propagates(self, render) -> None:
        class _Silent(Exception):
            silent_variable_failure = True

        class _Loud(Exception):
            silent_variable_failure = False

        class _Raiser:
            @property
            def quiet(self):
                raise _Silent("quiet")

            @property
            def loud(self):
                raise _Loud("loud")

        with resolve_lazy(True):
            assert render("{{ r.quiet }}", {"r": _Raiser()}) == ""
            with pytest.raises(Exception):
                render("{{ r.loud }}", {"r": _Raiser()})

    @pytest.mark.parametrize("render", ENTRIES)
    def test_a_silent_failure_does_not_keep_walking(self, render) -> None:
        """Django's silent arm is its OUTERMOST handler, which RETURNS — it
        does not assign `string_if_invalid` and walk the next bit the way the
        `alters_data` arm does. `{{ r.quiet.isupper }}` is therefore empty on
        both engines, where `{{ m.delete.isupper }}` is `False`."""

        class _Silent(Exception):
            silent_variable_failure = True

        class _Raiser:
            @property
            def quiet(self):
                raise _Silent("quiet")

        source = "{{ r.quiet.isupper }}"
        assert django_render(source, {"r": _Raiser()}) == ""
        with resolve_lazy(True):
            assert render(source, {"r": _Raiser()}) == ""


# ---------------------------------------------------------------------------
# 6.1 — `alters_data` mid-path CONTINUES
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestAltersDataContinues2539:
    """6.1: Django assigns `string_if_invalid` INSIDE the loop and walks the
    next bit, so `{{ m.delete.isupper }}` is `"".isupper()` — `False`."""

    @pytest.mark.parametrize("render", ENTRIES)
    def test_the_walk_continues_from_the_empty_string(self, render) -> None:
        source = "{{ m.delete.isupper }}"
        assert django_render(source, {"m": Mutator()}) == "False"
        with resolve_lazy(True):
            assert render(source, {"m": Mutator()}) == "False"

    @pytest.mark.parametrize("render", ENTRIES)
    def test_the_flag_off_answer_is_the_old_one(self, render) -> None:
        """The gate-off sibling, in-suite: with the flag OFF the pre-ADR walk
        collapses both of Django's invalids into `Missing` and renders empty.
        So this behaviour change is the flag's, and switching it off restores
        the old bytes — which is movement 2's whole contract."""
        with resolve_lazy(False):
            assert render("{{ m.delete.isupper }}", {"m": Mutator()}) == ""

    @pytest.mark.parametrize("render", ENTRIES)
    def test_the_terminal_is_still_empty(self, render) -> None:
        with resolve_lazy(True):
            assert render("{{ m.delete }}", {"m": Mutator()}) == ""


# ---------------------------------------------------------------------------
# 6.2 — `ignore_failures` substitutes None
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestIgnoreFailuresSubstitutesNone2539:
    """6.2 / #2528 (net row G). Django's `FilterExpression.resolve` turns a
    `VariableDoesNotExist` into **None** when `ignore_failures=True`, which
    `{% if %}` / `{% for %}` / `{% cycle %}` / `{% firstof %}` / `{% regroup %}`
    all pass. djust substituted `Value::Missing`, so the filter chain ran over
    a value `default_if_none` correctly refused to fire for."""

    SOURCE = "{% if x|default_if_none:y %}yes{% else %}no{% endif %}"

    @pytest.mark.parametrize("render", ENTRIES)
    def test_a_missing_operand_reaches_the_filter_as_none(self, render) -> None:
        assert django_render(self.SOURCE, {"y": 1}) == "yes"
        with resolve_lazy(True):
            assert render(self.SOURCE, {"y": 1}) == "yes"

    @pytest.mark.parametrize("render", ENTRIES)
    def test_the_flag_off_answer_is_the_old_one(self, render) -> None:
        with resolve_lazy(False):
            assert render(self.SOURCE, {"y": 1}) == "no"

    @pytest.mark.parametrize("render", ENTRIES)
    def test_a_present_operand_is_untouched(self, render) -> None:
        """Non-vacuity in the other direction: the substitution fires on a
        RESOLUTION FAILURE only. A present falsy operand still answers itself,
        and a present non-None one is not replaced."""
        with resolve_lazy(True):
            assert render(self.SOURCE, {"x": 0, "y": 1}) == "no"
            assert render(self.SOURCE, {"x": None, "y": 1}) == "yes"
            assert render("{{ x|default_if_none:'D' }}", {"x": "v"}) == "v"

    @pytest.mark.parametrize("render", ENTRIES)
    def test_an_unfiltered_missing_operand_is_unchanged(self, render) -> None:
        """The substitution is scoped to the FILTERED operand, which is the
        whole of the observable difference: with no filter in the chain,
        `Missing` and `None` are both falsy to every consumer, and swapping
        them where the value is EMITTED would change bytes for no Django
        reason."""
        with resolve_lazy(True):
            assert render("{% if x %}yes{% else %}no{% endif %}", {}) == "no"
            assert render("{% firstof x 'fallback' %}", {}) == "fallback"
            assert render("{% for i in x %}[{{ i }}]{% endfor %}", {}) == ""


# ---------------------------------------------------------------------------
# The switch itself
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestTheSwitch2539:
    def test_the_flag_defaults_off_and_round_trips(self) -> None:
        from djust.config import config, template_resolve_lazy_enabled

        assert config._defaults["template_resolve_lazy"] is False
        with resolve_lazy(True):
            assert template_resolve_lazy_enabled() is True
            assert _rust.resolve_lazy_enabled() is True
        with resolve_lazy(False):
            assert template_resolve_lazy_enabled() is False
            assert _rust.resolve_lazy_enabled() is False

    def test_a_thread_that_never_pushed_reads_off(self) -> None:
        """The Rust default, which is what an embedder and any not-yet-rendered
        worker thread get. A `sync_to_async` pool thread that has never called
        `apply_render_env` must not inherit another thread's flag."""
        import threading

        seen: list = []

        def worker():
            seen.append(_rust.resolve_lazy_enabled())

        with resolve_lazy(True):
            thread = threading.Thread(target=worker)
            thread.start()
            thread.join()
        assert seen == [False], "the flag is not thread-local"

    @pytest.mark.parametrize("render", ENTRIES)
    def test_the_switch_is_what_gates_the_behaviour(self, render) -> None:
        """The in-suite gate-off: the SAME template and the SAME context
        answer Django's bytes with the flag on and today's with it off. If
        this ever passes in both states, the flag has stopped gating."""
        # `{% with %}` over a bound name that SHADOWS a top-level one — the
        # net's row M6, wrong on every entry today and fixed on every entry by
        # the handle riding in the bound value.
        source = "{% with x=p.0 %}{{ x.cls_attr }}{% endwith %}"

        class _Cls:
            cls_attr = "class-level"

        class _Outer:
            cls_attr = "OUTER"

        def ctx():
            return {"p": [_Cls(), _Cls()], "x": _Outer()}

        assert django_render(source, ctx()) == "class-level"
        with resolve_lazy(True):
            on = render(source, ctx())
        with resolve_lazy(False):
            off = render(source, ctx())
        assert on != off, "the ADR-027 flag changed nothing — it is not wired"
        assert on == "class-level", on
        assert off == "OUTER", off


# ---------------------------------------------------------------------------
# The conversion differential, under BOTH flag states (risk 12)
# ---------------------------------------------------------------------------
SHAPES: list[Callable[[], Any]] = [
    lambda: None,
    lambda: True,
    lambda: 7,
    lambda: 1.5,
    lambda: "ab",
    lambda: b"ab",
    lambda: [1, 2],
    lambda: (1, 2),
    lambda: {"a": 1},
    lambda: set(),
    lambda: {"a"},
    lambda: frozenset(),
    lambda: complex(0),
    lambda: {}.keys(),
    lambda: {}.values(),
    lambda: {}.items(),
    lambda: Presenter(make_user()),
    lambda: Mutator(),
    lambda: make_user(),
    lambda: range(3),
]


@pytest.mark.django_db
class TestTheTwoConversionsAgreeUnderBothFlags2539:
    """Risk 12: `crosses_as_encoded` is a cheap PROBE and
    `extract::<Value>()` is the real conversion. They decide the same question
    for the LiveView path (`normalize_django_value`) and the plain path, so a
    disagreement is a silent divergence between the two entries — and the flag
    moves the answer for a whole class of object, which is exactly when a
    probe drifts from the thing it approximates."""

    @pytest.mark.parametrize("enabled", [False, True], ids=["lazy-off", "lazy-on"])
    def test_the_probe_and_the_conversion_answer_the_same_bit(self, enabled: bool) -> None:
        with resolve_lazy(enabled):
            disagreements = []
            for make in SHAPES:
                obj = make()
                probe = _rust.crosses_as_encoded(obj)
                actual = _rust.crosses_as_encoded_by_conversion(obj)
                if probe != actual:
                    disagreements.append((type(obj).__name__, probe, actual))
            assert disagreements == [], f"probe/conversion drift: {disagreements}"

    def test_the_flag_moves_at_least_one_shape(self) -> None:
        """Non-vacuity: the sweep above would pass trivially if the flag
        changed nothing about which objects cross as `Encoded`."""
        with resolve_lazy(False):
            off = [_rust.crosses_as_encoded(make()) for make in SHAPES]
        with resolve_lazy(True):
            on = [_rust.crosses_as_encoded(make()) for make in SHAPES]
        assert off != on, "the flag did not move any conversion — the sweep proves nothing"

    def test_no_container_or_model_ever_carries_a_handle(self) -> None:
        """ADR §Decision (b), enforced by the existing arm ORDER rather than
        by a new rule: a `list`, a `dict`, a tuple, a `Model` and anything
        with `__djust_serialize__` are claimed ABOVE `opaque_value`, so the
        flag cannot give any of them a handle. That is what keeps the #2532
        zero-crossing invariant true by construction."""
        with resolve_lazy(True):
            for make in (lambda: [1, 2], lambda: (1, 2), lambda: {"a": 1}, make_user):
                obj = make()
                assert _rust.crosses_as_encoded(obj) is False, type(obj).__name__
