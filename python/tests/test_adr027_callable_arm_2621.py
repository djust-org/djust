"""ADR-027 #2621 — ``normalize_django_value``'s callable arm, gated on the flag.

The six cells movement 3 (#2539, PR #2620) left held were, by the time this
landed, five — rows **J**, **J2**, **Q**, **P** and **P0** on the LiveView
path — and all five were ONE cause. ``normalize_django_value`` replaced ANY
callable with ``None`` before Rust saw the context ("safety net: skip
callables"), so on the LiveView path:

* ``{{ callable }}`` and ``{{ var.callable }}`` rendered ``None`` where Django
  renders the CALL's result (J / J2);
* ``{{ k }}`` on a class rendered ``None`` where Django INSTANTIATES it (Q);
* and with no value to hold a handle, ``{{ class_var.class_property }}`` fell
  to the pre-ADR sidecar walk's unguarded string-key ``get_item``, which
  honours ``__class_getitem__`` and rendered a ``types.GenericAlias`` spelled
  with the STRING key (P / P0 — a segfault until #2624's depth ceiling).

The plain path has no ``normalize_django_value`` in front of it and answered
Django's bytes for all five under the flag, which is what made the cause
legible: the arm, not the sink.

The other two cells this issue inherited closed before it: **O** (a ``__str__``
returning ``SafeData``) via ``Encoded::display_safe``, PR #2665 — runtime-only
metadata, never restored from wire data, so no twelfth wire slot — and
**V** (a generator) via #2613's one-shot-iterator handle. The
characterization net (``test_adr027_characterization_net_2539.py``) carries
the per-cell bytes; this file carries what the net does not — the arm's TRUTH
TABLE, the guard rails a raw callable now meets at the sink, and the two
channels that must NOT widen.

Gate-off (#1468): every ON-column assertion here has an OFF-column sibling in
the same test asserting ``"None"``. Restoring the unconditional ``return
None`` makes the ON half fail while the OFF half keeps passing, so neither
half can be green for the wrong reason.
"""

from __future__ import annotations

import collections
import contextlib
import functools
import json
import re
from typing import Any, Callable

import pytest
from django.template import Context as DjangoContext
from django.template import Template as DjangoTemplate
from django.test import RequestFactory
from django.contrib.sessions.middleware import SessionMiddleware

from djust import LiveView, _rust
from djust.serialization import _crosses_as_encoded, normalize_django_value
from djust.testing import LiveViewTestClient

DJ_ROOT = re.compile(r"<div[^>]*dj-root[^>]*>(.*)</div>", re.S)
#: Addresses differ per run; the ONE thing a repr-carrying cell may not pin.
HEX = re.compile(r"0x[0-9a-f]+")


def scrub(text: str) -> str:
    return HEX.sub("0x…", text)


@contextlib.contextmanager
def resolve_lazy(enabled: bool):
    """Flip the ADR-027 flag through the REAL wiring and ASSERT it landed.

    Copied in shape from the characterization net's context manager for the
    reason that file gives: a fixture that sets the config and ASSUMES the
    push would make every flag-ON assertion vacuous if the wiring broke.
    """
    from djust.config import config
    from djust.render_env import apply_render_env

    previous = config.get("template_resolve_lazy", False)
    config.update({"template_resolve_lazy": enabled})
    apply_render_env()
    assert _rust.resolve_lazy_enabled() is enabled, (
        "the ADR-027 flag did not reach Rust — apply_render_env() is not wiring it"
    )
    try:
        yield
    finally:
        config.update({"template_resolve_lazy": previous})
        apply_render_env()


# ---------------------------------------------------------------------------
# The shapes. Each is a CALLABLE that reaches the arm, and each answers a
# different one of Django's `_resolve_lookup` call rules.
# ---------------------------------------------------------------------------
CALLS: list[str] = []


def plain_lambda() -> str:
    CALLS.append("plain_lambda")
    return "foo bar"


def mutator() -> str:
    CALLS.append("mutator")
    return "MUTATED"


mutator.alters_data = True  # type: ignore[attr-defined]


def marked() -> str:
    CALLS.append("marked")
    return "CALLED"


marked.do_not_call_in_templates = True  # type: ignore[attr-defined]


def loud() -> str:
    CALLS.append("loud")
    raise RuntimeError("boom from the callable body")


def needs_args(a: Any, b: Any) -> str:
    CALLS.append("needs_args")
    return f"{a}{b}"


class Cls:
    def __str__(self) -> str:
        return "<Cls>"


class CallableBytes(bytes):
    """A callable the CONVERSION does not model as an ``Encoded``.

    PyO3's sequence extraction claims a ``bytes`` subclass before
    ``opaque_gate`` is consulted, so ``crosses_as_encoded`` is False and there
    is no handle for the sink to walk — the second half of the arm's gate.
    """

    def __call__(self) -> str:
        CALLS.append("CallableBytes")
        return "CB-called"


class CallableDeque(collections.deque):
    """The same, via the other sequence spelling."""

    def __call__(self) -> str:
        CALLS.append("CallableDeque")
        return "CD-called"


# ---------------------------------------------------------------------------
# The three columns, as the net spells them.
# ---------------------------------------------------------------------------
def django_render(source: str, context: dict) -> str:
    return DjangoTemplate(source).render(DjangoContext(dict(context)))


def plain_render(source: str, context: dict) -> str:
    from djust.template_backend import DjustTemplateBackend

    backend = DjustTemplateBackend(
        params={"NAME": "djust", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}}
    )
    return backend.from_string(source).render(context=dict(context), request=None)


def liveview_render(source: str, context: dict) -> str:
    """The REAL LiveView entry — the path the arm sits on. Needs `django_db`."""

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
    match = DJ_ROOT.search(html)
    assert match is not None, html
    return match.group(1)


def observe(render: Callable[[str, dict], str], source: str, context: dict) -> str:
    """Rendered bytes with addresses scrubbed, or a named raise."""
    try:
        return scrub(render(source, context))
    except Exception as exc:  # noqa: BLE001 — the raise IS the observation
        return f"RAISES {type(exc).__name__}"


class TestTheArmsTruthTable2621:
    """``normalize_django_value`` on a callable, across the three inputs that
    decide the arm. Unit level, no template — the arm's contract on its own.

    Both gate conditions are independently reachable, which is what keeps this
    a truth table rather than one condition plus a decoration (the shadowing
    trap): a lambda flips on the FLAG, and — on this ``main`` — a
    ``CallableBytes`` flips on ``crosses_as_encoded`` with the flag ON. That
    second one is reachable BY VALUE today and structurally always (a missing
    compiled extension); see the note on
    ``test_a_real_shape_agrees_with_whatever_the_gate_answers``.
    """

    #: Callables the conversion DOES model as an `Encoded` — the ones the sink
    #: can walk. Every callable a template is realistically handed lands here;
    #: the exceptions are the sequence-subclass spellings below.
    CROSSING = [
        pytest.param(lambda: "x", id="lambda"),
        pytest.param(Cls, id="class"),
        pytest.param(len, id="builtin"),
        pytest.param("abc".upper, id="bound-method"),
        pytest.param(functools.partial(len, "ab"), id="partial"),
        pytest.param(mutator, id="alters_data"),
        pytest.param(marked, id="do_not_call_in_templates"),
    ]
    #: Callables it does NOT — an earlier conversion arm claims them.
    NOT_CROSSING = [
        pytest.param(CallableBytes(b"ab"), id="CallableBytes"),
        pytest.param(CallableDeque([1, 2]), id="CallableDeque"),
    ]

    @pytest.mark.parametrize("value", CROSSING)
    def test_a_crossing_callable_is_carried_with_the_flag_on(self, value: Any) -> None:
        with resolve_lazy(True):
            assert normalize_django_value(value) is value, (
                "the arm still dropped a callable the sink can resolve"
            )

    @pytest.mark.parametrize("value", CROSSING)
    def test_the_same_callable_is_dropped_with_the_flag_off(self, value: Any) -> None:
        """The gate-off sibling, in the suite (#1468): OFF is byte-identical
        to the pre-#2621 behaviour, which is what makes the ON assertion above
        a claim about the FLAG rather than about callables in general."""
        with resolve_lazy(False):
            assert normalize_django_value(value) is None

    def test_the_no_handle_half_of_the_gate_drops_rather_than_carries(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The second half of the gate, pinned on the CONDITION.

        With nothing for the sink to walk, the arm must keep today's answer
        rather than hand the renderer an object it does not model. Stated
        against the condition (not a shape) because WHICH shapes decline is
        the conversion's business and moves: #2704 moves every non-``list``
        sequence onto the carrier, so the ``bytes``/``deque`` spellings below
        stop declining the moment it lands. A fixture-shaped pin would go red
        on that merge and read as a regression in the wrong file.
        """
        monkeypatch.setattr(
            "djust.serialization._crosses_as_encoded", lambda _value: False, raising=True
        )
        with resolve_lazy(True):
            assert normalize_django_value(lambda: "x") is None

    @pytest.mark.parametrize("value", NOT_CROSSING)
    def test_a_real_shape_agrees_with_whatever_the_gate_answers(self, value: Any) -> None:
        """The arm must never disagree with the conversion about a shape.

        A DIFFERENTIAL against ``_crosses_as_encoded``, not a recorded answer,
        because WHICH shapes decline is the conversion's business and moves:
        on ``main`` today a callable ``bytes``/``deque`` subclass declines
        (PyO3's sequence extraction claims it before ``opaque_gate``), and
        #2704 moves every non-``list`` sequence onto the carrier, at which
        point these same two start crossing and the arm must start carrying
        them. Both readings are correct; disagreeing with the conversion is
        the only thing that is not.

        Note on reachability, so the ``crosses_as_encoded`` term is not read
        as decoration: it answers False by two routes, and only ONE is
        by-value. The other — no compiled extension — is structural and
        always reachable, and is pinned by
        ``TestTheQuestionHasOneStatement2621``. That is why the term stays
        even on a ``main`` where no callable shape declines by value.
        """
        crosses = _crosses_as_encoded(value)
        with resolve_lazy(True):
            carried = normalize_django_value(value)
        if crosses:
            assert carried is value, "a modelled callable was dropped anyway"
        else:
            assert carried is None, "an unmodelled callable was handed to the renderer"

    @pytest.mark.parametrize("value", CROSSING)
    def test_the_state_channel_drops_it_on_both_settings(self, value: Any) -> None:
        """``state_roundtrip=True`` is the session / signed-snapshot boundary:
        an encoder-less serializer writes it, so it CANNOT hold a live object.
        Same reason the ``Decimal`` / ``datetime`` / ``set`` branches split on
        this flag."""
        for enabled in (True, False):
            with resolve_lazy(enabled):
                assert normalize_django_value(value, state_roundtrip=True) is None

    def test_the_arm_never_invokes_what_it_carries(self) -> None:
        """Normalization only stops DROPPING the callable — deciding whether
        to call it is the sink's job (`walk_live`'s root `maybe_call`). If
        normalization called it, `alters_data` would already have been
        violated one layer too early."""
        CALLS.clear()
        with resolve_lazy(True):
            for fn in (plain_lambda, mutator, marked, loud, needs_args):
                normalize_django_value(fn)
        assert CALLS == [], f"normalization CALLED something: {CALLS}"


@pytest.mark.django_db
class TestTheSinkAnswersDjangoOnTheLiveViewPath2621:
    """The end-to-end half: with the callable reaching the sink, Django's own
    call rules answer — the ones `maybe_call` already implements.

    The net pins rows J / J2 / Q / P / P0. These are the neighbouring rules
    the net does not sample, which is where a curated table blinds you: the
    five cells all exercise the *plain call* arm, and `alters_data`,
    `do_not_call_in_templates`, a raising body and an arity mismatch are four
    separate branches of `_resolve_lookup`'s callable block.
    """

    #: id -> (source, context factory). Every entry is asserted against the
    #: REAL Django engine in the same test, so nothing here is a recorded
    #: expectation that can drift away from Django.
    SHAPES: dict[str, Callable[[], dict]] = {
        "plain": lambda: {"f": plain_lambda},
        "alters_data": lambda: {"f": mutator},
        "do_not_call_in_templates": lambda: {"f": marked},
        "raises": lambda: {"f": loud},
        "needs_args": lambda: {"f": needs_args},
        "builtin": lambda: {"f": len},
        "bound_method": lambda: {"f": "abc".upper},
        "partial": lambda: {"f": functools.partial(lambda: "P")},
        "class": lambda: {"f": Cls},
        "nested_in_a_dict": lambda: {"f": {"inner": plain_lambda}},
    }

    def _source(self, key: str) -> str:
        return "{{ f.inner }}" if key == "nested_in_a_dict" else "{{ f }}"

    @pytest.mark.parametrize("key", sorted(SHAPES))
    def test_the_liveview_path_answers_django_with_the_flag_on(self, key: str) -> None:
        source, make_ctx = self._source(key), self.SHAPES[key]
        expected = observe(django_render, source, make_ctx())
        actual = observe(liveview_render, source, make_ctx())
        assert actual == expected, (
            f"{key}: the LiveView path answers {actual!r}, Django answers {expected!r}"
        )

    @pytest.mark.parametrize("key", sorted(SHAPES))
    def test_the_flag_off_column_is_still_none(self, key: str) -> None:
        """The gate-off sibling. Every one of these rendered ``None`` before
        #2621, and still does with the flag OFF — so the ON assertions above
        measure the change rather than a property callables always had."""
        source, make_ctx = self._source(key), self.SHAPES[key]
        with resolve_lazy(False):
            assert observe(liveview_render, source, make_ctx()) == "None"

    @pytest.mark.parametrize("key", sorted(SHAPES))
    def test_the_two_djust_paths_agree_with_the_flag_on(self, key: str) -> None:
        """#1646: the arm was a LiveView-only divergence, so the closing claim
        is that the divergence is GONE, not merely that one path improved."""
        source, make_ctx = self._source(key), self.SHAPES[key]
        with resolve_lazy(True):
            plain = observe(plain_render, source, make_ctx())
            liveview = observe(liveview_render, source, make_ctx())
        assert plain == liveview, f"{key}: plain={plain!r} liveview={liveview!r}"

    def test_a_mutator_is_refused_rather_than_run(self) -> None:
        """#2506/#2507 never fail OPEN. Presence AND silence: the render
        reaches the name (the neighbouring cell proves the lookup ran) and the
        mutator segment is empty, with no call recorded."""
        CALLS.clear()
        with resolve_lazy(True):
            out = liveview_render("[{{ ok }}][{{ f }}]", {"ok": plain_lambda, "f": mutator})
        assert out == "[foo bar][]", out
        assert "mutator" not in CALLS, f"the alters_data callable RAN: {CALLS}"

    def test_a_raising_body_propagates_rather_than_rendering_empty(self) -> None:
        """A swallowed exception here would be the fail-open shape #2506
        exists to refuse — and would be INVISIBLE, since the cell would render
        the empty string a legitimately-empty callable also renders."""
        with resolve_lazy(True):
            with pytest.raises(RuntimeError, match="boom from the callable body"):
                liveview_render("{{ f }}", {"f": loud})

    def test_a_no_handle_callable_renders_what_the_gate_decided(self) -> None:
        """The no-handle half, end to end — asserted against the gate.

        Two premises were WRONG when this test was first written, and both are
        recorded here rather than quietly fixed, because either would have
        made a plausible-looking green assertion:

        1. *"the two djust paths agree here"* — they do not, and not because
           of #2621. PyO3's sequence extraction claims a ``bytes`` subclass
           and gives the plain path a ``Value::List``, so ``{{ f }}`` renders
           ``[97, 98]`` there while the LiveView path renders ``None``. That
           divergence pre-dates this change and belongs to the conversion
           (#2704's subject), not to the arm.
        2. *"the LiveView answer is ``None``"* — true on this ``main`` and
           only because the shape declines ``crosses_as_encoded``. #2704 moves
           every non-``list`` sequence onto the carrier, at which point this
           shape crosses, the arm carries it, and the sink CALLS it — the
           Django answer. Hard-coding ``None`` would go red on that merge and
           read as a regression in the wrong file.

        What holds either way: the render agrees with what the gate decided,
        and the escape hatch is unmoved.
        """

        def ctx() -> dict:
            return {"f": CallableBytes(b"ab")}

        django_bytes = observe(django_render, "{{ f }}", ctx())
        assert django_bytes == "CB-called", (
            "the Django side of the differential stopped being what it was measured to be"
        )
        crosses = _crosses_as_encoded(CallableBytes(b"ab"))
        with resolve_lazy(False):
            assert observe(liveview_render, "{{ f }}", ctx()) == "None", (
                "the ADR-027 escape hatch moved — it drops every callable, unconditionally"
            )
        with resolve_lazy(True):
            rendered = observe(liveview_render, "{{ f }}", ctx())
        if crosses:
            assert rendered == django_bytes, (
                f"the arm carried it to the sink but the render is {rendered!r}, "
                f"not Django's {django_bytes!r}"
            )
        else:
            assert rendered == "None", (
                f"the arm handed a handle-less callable to the renderer: {rendered!r}"
            )


@pytest.mark.django_db
class TestNoLiveObjectReachesAChannelThatPersists2621:
    """The two boundaries the widening must not cross.

    A handle-bearing ``Encoded`` crosses back to Python as its ``display``
    string, which for a lambda is ``<function <lambda> at 0x…>`` — an address.
    Neither the session snapshot nor the client payload may carry one.
    """

    def test_the_session_snapshot_holds_no_function_address(self) -> None:
        """The REAL GET path: `mixins/request.py` writes
        `request.session[view_key]` through
        `normalize_django_value(..., state_roundtrip=True)`."""

        class _CallableStateView(LiveView):
            template = "<div dj-root>{{ cb }}</div>"

            def mount(self, request, **kwargs):
                self.cb = plain_lambda

            def get_context_data(self, **kwargs):
                return {"cb": self.cb}

        factory = RequestFactory()
        request = factory.get("/callable-2621/")
        SessionMiddleware(lambda _r: None).process_request(request)
        request.session.save()
        with resolve_lazy(True):
            _CallableStateView().get(request)

        saved = request.session["liveview_/callable-2621/"]
        assert saved["cb"] is None, f"a live callable reached the session: {saved['cb']!r}"
        # The strong form: the whole snapshot is JSON-encodable with no
        # address anywhere in it, which is what the encoder-less session
        # serializer actually requires.
        blob = json.dumps(saved)
        assert "0x" not in blob and "<function" not in blob, blob

    def test_the_client_state_payload_holds_no_function_address(self) -> None:
        """The other outbound channel, measured rather than assumed.

        `get_state()` is what the client is given. A carried callable is a
        `Value::Encoded` whose display for a lambda is
        `<function … at 0x…>` — a server address — so "no address reaches the
        client" is a claim that needs a measurement, not an inference from the
        session pin above (a different channel, built by different code).

        Honest about its own category: this is a BOUNDARY pin, not a change
        pin. It stays green with the change gated off, because `get_state()`
        reads the view's public attributes and the attribute walk drops a
        callable before this arm ever sees it (the sibling below). The value
        is that the claim in this class's docstring is measured on both
        settings rather than reasoned from the session channel — and that a
        future change routing context values into client state has to come
        here and face it.
        """

        class _StateView(LiveView):
            template = "<div dj-root>{{ cb }}{{ plain }}</div>"

            def mount(self, request, **kwargs):
                self.cb = plain_lambda
                self.plain = "visible"

        for enabled in (True, False):
            with resolve_lazy(enabled):
                client = LiveViewTestClient(_StateView)
                client.mount()
                client.render()
                assert client.view_instance is not None
                state = client.view_instance.get_state()
            blob = json.dumps(state)
            assert "0x" not in blob and "<function" not in blob, (
                f"a function address reached the client payload with the flag {enabled}: {blob}"
            )
            assert state == {"plain": "visible"}, state

    def test_a_public_callable_ATTRIBUTE_is_still_dropped_before_the_arm(self) -> None:
        """The boundary #2621 does NOT move, stated so nobody reads the gate
        as wider than it is.

        A callable assigned as a public LiveView **attribute** never reaches
        ``normalize_django_value`` at all: the public-attribute walk drops it
        first (``mixins/context.py``'s ``if callable(value): continue``), and
        that exclusion is deliberate and load-bearing — without it every
        ``@event_handler`` on the view would become a context variable. So
        ``{{ cb }}`` for ``self.cb = fn`` renders EMPTY on both flag settings,
        where Django's ``Context({"cb": fn})`` renders the call's result.

        #2621 is about the context DICT — what ``get_context_data`` returns,
        which is what the characterization net's rows exercise. A future
        change that widens the attribute walk has to come here and say so.
        """

        class _AttrView(LiveView):
            template = "<div dj-root>[{{ cb }}][{{ plain }}]</div>"

            def mount(self, request, **kwargs):
                self.cb = plain_lambda
                self.plain = "visible"

        for enabled in (True, False):
            with resolve_lazy(enabled):
                client = LiveViewTestClient(_AttrView)
                client.mount()
                html = client.render()
            match = DJ_ROOT.search(html)
            assert match is not None, html
            assert match.group(1) == "[][visible]", (
                f"the public-attribute walk changed with the flag {enabled}: {match.group(1)!r}"
            )

    def test_the_display_string_is_what_a_handle_hands_back_not_the_object(self) -> None:
        """A carried callable is a `Value::Encoded` with a transient handle,
        and `IntoPyObject` maps an `Encoded` to its DISPLAY string — so a
        custom-tag handler receives text, never the live function (#2509)."""
        with resolve_lazy(True):
            carried = normalize_django_value(plain_lambda)
        assert carried is plain_lambda
        assert _rust.crosses_as_encoded(carried) is True


class TestTheQuestionHasOneStatement2621:
    """#1646, structurally: ``normalize_django_value`` asks
    "does this cross as an ``Encoded``?" from TWO arms now, and both must
    route through the one helper — including its fail-SOFT answer when the
    compiled extension is absent."""

    def _serialization_source(self) -> str:
        from djust import serialization

        assert serialization.__file__ is not None
        with open(serialization.__file__, encoding="utf-8") as handle:
            return handle.read()

    # The "one statement" half of this claim — the Rust export named exactly
    # once, and the helper's two arms — is pinned NEXT DOOR, in
    # `test_opaque_collections_2477_2489.py::TestTheGateHasOneStatement`, the
    # class that already owned this gate's structural pins and that #2621
    # extended. Restating it here would be two pins for one invariant, and the
    # canon on shadowing says delete the redundant one rather than test around
    # it. What is left below is what only #2621 introduced.

    def test_a_missing_extension_answers_false_rather_than_raising(self, monkeypatch) -> None:
        """The fallback branch is the one least likely to be exercised, so it
        must never turn "we could not serialize it" into a 500. Both callers
        want the same soft answer, which is why it lives in the helper."""
        import djust

        monkeypatch.delattr(djust._rust, "crosses_as_encoded", raising=True)
        assert _crosses_as_encoded(lambda: 1) is False
        with resolve_lazy(True):
            # With the question unanswerable, the callable arm falls back to
            # the historical drop rather than propagating the AttributeError.
            assert normalize_django_value(lambda: 1) is None

    def test_the_flag_is_read_through_the_config_helper(self) -> None:
        """`config.py` is the only file allowed to spell the settings key
        (`test_the_config_reader_is_the_only_one`), so the arm must call the
        reader rather than reach for `LIVEVIEW_CONFIG` itself."""
        source = self._serialization_source()
        assert "template_resolve_lazy_enabled" in source
        assert '"template_resolve_lazy"' not in source
        assert "'template_resolve_lazy'" not in source
