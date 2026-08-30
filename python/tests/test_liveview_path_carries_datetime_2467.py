"""The LiveView path carries a datetime to Rust instead of flattening it (#2467).

What the two paths were
-----------------------
djust has two ways into the renderer, and #2448 / #2456 closed one of them:

* **raw** — ``render_template(tpl, {"p": dt})``. The object crosses the PyO3
  boundary intact and becomes ``Value::Encoded``, which carries ``str(o)``,
  ``DjangoJSONEncoder.default(o)``, CPython's ``tp_name`` and (since #2458)
  ``bool(o)``. ``djust/template/backend.py`` takes this path, so it is what a
  plain Django view rendering through ``DjustTemplateBackend`` gets.
* **LiveView** — the context runs through
  ``djust.serialization.normalize_django_value`` first, which converted a
  ``datetime`` to a **string in Python**. By the time Rust saw it the type was
  gone, and every downstream decision was made on text.

``TestWhichPathThisFixIsOn`` in ``test_json_script_datetime_value_2448.py`` and
``TestWhatThisDeliberatelyDoesNOTClose`` in
``test_datetime_encoder_spelling_2462.py`` both named that bound precisely and
pinned the flattening, so closing it reddens them rather than leaving them
stale. Both are inverted by this change rather than deleted.

Measured, not argued: **14 of 49** path-pairs diverged (7 values × 7 templates),
and the sharpest was not a spelling.

Not a spelling divergence — a PERMISSIVENESS one
------------------------------------------------
#2451 made seven filters refuse a value their Django body cannot iterate or
subscript, and its sweep renders djust through ``normalize_django_value``. With
a ``timedelta`` in the corpus (#2469) that sweep reported **twelve** cells in
its ``renders where Django refuses`` bucket: the flattened
``"P0DT00H00M00S"`` is a *string*, so ``{{ p|unordered_list }}`` emitted
thirteen ``<li>``s and ``{{ p|phone2numeric }}`` emitted ``7038004006007``
where Django raises ``TypeError: 'datetime.timedelta' object is not iterable``.

All twelve refused correctly on the raw path the whole time. That asymmetry —
djust more permissive than Django on the path most djust pages use, and stricter
on the one they do not — is what makes this a fix rather than a preference.

The shape, and why it is the `Decimal` one
-------------------------------------------
``Decimal`` (#2239) had the identical problem and the branch is copied verbatim:
carried through UNCONVERTED for the renderer, converted only at the
``state_roundtrip=True`` boundary. The consumer audit the issue asks for, run
per consumer:

* **template context (Rust renderer)** — takes it; that is the point.
* **wire encoders** (``websocket.py``, ``sse.py``, ``api/dispatch.py``) —
  ``json.dumps(…, cls=DjangoJSONEncoder)``, which handles a ``datetime``, and
  since #2462 djust's encoder spells it exactly as Django's does. The bytes on
  the wire do not change; ``TestTheWireBytesAreUnchanged`` runs it.
* **session / signed snapshot** — every ``request.session[...]`` write already
  passes ``state_roundtrip=True`` (``mixins/request.py:270``, ``:275``,
  ``:718``, ``:728``; ``mixins/components.py:149``). ``TestStateRoundtripBoundary``
  pins both directions.
* **the Rust state round trip** — the one that would have been the blocker, and
  it was already solved: ``Value::Encoded`` has a TAGGED msgpack encoding
  (``ENCODED_TAG``, ``crates/djust_core/src/lib.rs``) whose payload is
  ``[type_name, display, json, truthy]``, added by #2448/#2458 and pinned
  literally in ``test_encoded_truthiness_2458.py``. So the #1448 snapshot this
  change needs already exists; ``TestTheRustStateBackendRoundTrip`` asserts the
  LiveView path now actually reaches it.
"""

from __future__ import annotations

import datetime as dt
import json
import re

import pytest
from django.core.serializers.json import DjangoJSONEncoder as RealDjangoEncoder
from django.core.signing import JSONSerializer as DjangoSessionSerializer
from django.template import Context as DjangoContext
from django.template import Template as DjangoTemplate
from django.test import RequestFactory, override_settings

from djust import LiveView, _rust
from djust.serialization import (
    DjangoJSONEncoder as DjustEncoder,
)
from djust.serialization import (
    django_json_datetime,
    normalize_django_value,
)

UTC = dt.timezone.utc

#: Every member of the family `Value::Encoded` carries, plus the shapes whose
#: spelling differs between `str()` and the encoder (#2448's own table) and the
#: one member with a falsy inhabitant.
FAMILY = {
    "datetime naive": dt.datetime(2020, 1, 1, 3, 4, 5),
    "datetime utc": dt.datetime(2026, 8, 22, 23, 30, tzinfo=UTC),
    "datetime us": dt.datetime(2020, 1, 1, 3, 4, 5, 123456),
    "date": dt.date(2020, 1, 1),
    "time": dt.time(9, 5, 30),
    "timedelta zero": dt.timedelta(0),
    "timedelta 90s": dt.timedelta(seconds=90),
}

#: One template per decision the flattening used to make on text.
TEMPLATES = {
    "bare": "{{ p }}",
    "if": "{% if p %}T{% else %}F{% endif %}",
    "json_script": '{{ p|json_script:"d" }}',
    "date-fmt": '{{ p|date:"Y-m-d H:i" }}',
    "time-fmt": '{{ p|time:"H:i" }}',
    "default": '{{ p|default:"D" }}',
    "yesno": "{{ p|yesno }}",
}

#: The seven #2451 filters whose Django body cannot iterate or subscript a
#: `timedelta`. Named here rather than imported so this file states its own
#: claim; `test_sequence_op_chokepoint_2451.py` is the owner of the class.
NON_ITERABLE_FILTERS = (
    "escapeseq",
    "safeseq",
    "unordered_list",
    "first",
    "last",
    "phone2numeric",
)


class _BaseView(LiveView):
    """The mount body is shared; the template and the value vary per subclass.

    A DYNAMIC subclass per call rather than mutating class attributes (#1109) —
    mutating `_BaseView` would leak the previous case's value into the next
    test, which on a 49-cell cross is exactly the shape that produces a green
    suite over a real divergence.
    """

    _value: object = None

    def mount(self, request, **kwargs):  # noqa: ANN001, ANN003, ANN201
        self.p = type(self)._value


def _liveview_render(template: str, value: object) -> str:
    """A real LiveView mount + render — the path `render_template` cannot see.

    Reproduction fidelity: a renderer-only harness runs the RAW path, which is
    exactly why #2456 could not close this issue. `view.render()` goes through
    `_sync_state_to_rust`, which is where `normalize_django_value` is called.
    """
    view_cls = type(
        "_V",
        (_BaseView,),
        {"template": '<div dj-id="0">' + template + "</div>", "_value": value},
    )
    view = view_cls()
    request = RequestFactory().get("/")
    view.mount(request)
    view.request = request
    return _strip(view.render())


def _strip(out: str) -> str:
    body = re.sub(r"^.*?<div[^>]*>", "", out, count=1, flags=re.S)
    return body.split("</div>")[0]


def _raw_render(template: str, value: object) -> str:
    return _rust.render_template(template, {"p": value})


class TestTheTwoPathsAgree:
    """The invariant this issue is about, swept rather than sampled.

    7 values x 7 templates. 14 of the 49 diverged before this change; the
    parametrisation is the whole cross so a future divergence in a cell nobody
    thought to name fails here.
    """

    @pytest.mark.parametrize("value_name", sorted(FAMILY))
    @pytest.mark.parametrize("tpl_name", sorted(TEMPLATES))
    def test_the_raw_and_liveview_paths_render_the_same(
        self, value_name: str, tpl_name: str
    ) -> None:
        value = FAMILY[value_name]
        source = TEMPLATES[tpl_name]
        assert _raw_render(source, value) == _liveview_render(source, value), (
            f"the two djust paths disagree for {value_name} through {tpl_name!r} — "
            "which is #2467 reopening"
        )

    def test_the_four_rows_the_issue_names(self) -> None:
        """The issue's own table, as a single readable assertion.

        `{% if p %}` over `timedelta(0)` is the row that made this a bug rather
        than a spelling preference: #2458 gave the raw path Python's own
        `bool(o)` and the LiveView path kept answering `T`, because the
        flattened `"P0DT00H00M00S"` is a non-empty string.
        """
        zero = dt.timedelta(0)
        assert _liveview_render("{% if p %}T{% else %}F{% endif %}", zero) == "F"
        assert _liveview_render("{{ p|yesno }}", zero) == "no"
        assert _liveview_render('{{ p|default:"D" }}', zero) == "D"
        assert _liveview_render("{{ p }}", dt.datetime(2020, 1, 1, 3, 4, 5)) == (
            "2020-01-01 03:04:05"
        )


class TestTheTwelvePermissivenessCells:
    """#2451's `renders where Django refuses` bucket, closed on both paths.

    The twelve cells `test_sequence_op_chokepoint_2451.py` pinned as #2467 —
    that pin is deleted by this change, and this is where the claim moves.
    """

    @pytest.mark.parametrize("name", NON_ITERABLE_FILTERS)
    @pytest.mark.parametrize("value", [dt.timedelta(0), dt.timedelta(seconds=90)])
    def test_django_refuses_a_timedelta(self, name: str, value: dt.timedelta) -> None:
        """The premise, run rather than assumed — the reference IS Django."""
        with pytest.raises((TypeError, AttributeError)):
            DjangoTemplate("{{ p|%s }}" % name).render(DjangoContext({"p": value}))

    @pytest.mark.parametrize("name", NON_ITERABLE_FILTERS)
    @pytest.mark.parametrize("value", [dt.timedelta(0), dt.timedelta(seconds=90)])
    def test_the_liveview_path_refuses_it_too_now(self, name: str, value: dt.timedelta) -> None:
        """Before this change djust iterated the thirteen CHARACTERS of the
        flattened `"P0DT00H00M00S"` and put them on the page — thirteen `<li>`s
        for `unordered_list`, `7038004006007` for `phone2numeric`."""
        with pytest.raises(Exception, match="not iterable|not subscriptable|raises"):
            _rust.render_template("{{ p|%s }}" % name, normalize_django_value({"p": value}))

    @pytest.mark.parametrize("name", NON_ITERABLE_FILTERS)
    def test_the_raw_path_always_refused_it(self, name: str) -> None:
        """The other half of the asymmetry, kept as the control: the raw path
        was correct throughout, which is what made this the LiveView path's
        bug rather than a hole in #2451's chokepoint."""
        with pytest.raises(Exception, match="not iterable|not subscriptable|raises"):
            _rust.render_template("{{ p|%s }}" % name, {"p": dt.timedelta(0)})


class TestTheNormalizerCarriesTheObject:
    """The branch itself, and the boundary that still converts."""

    @pytest.mark.parametrize("name", sorted(FAMILY))
    def test_the_value_is_carried_through_unconverted(self, name: str) -> None:
        value = FAMILY[name]
        carried = normalize_django_value({"p": value})["p"]
        assert carried is value, f"{name} was flattened to {carried!r}"

    @pytest.mark.parametrize("name", sorted(FAMILY))
    def test_state_roundtrip_still_converts(self, name: str) -> None:
        value = FAMILY[name]
        stored = normalize_django_value({"p": value}, state_roundtrip=True)["p"]
        assert isinstance(stored, str)
        assert stored == django_json_datetime(value)

    def test_nested_values_take_the_branch_too(self) -> None:
        """The recursion, which a top-level-only test would not exercise: a
        `datetime` in a dict, a list and a model-shaped map all reach the same
        branch."""
        value = dt.datetime(2020, 1, 1, 3, 4, 5)
        out = normalize_django_value({"a": [value], "b": {"c": value}})
        assert out["a"][0] is value
        assert out["b"]["c"] is value
        stored = normalize_django_value({"a": [value], "b": {"c": value}}, state_roundtrip=True)
        assert stored["a"][0] == django_json_datetime(value)
        assert stored["b"]["c"] == django_json_datetime(value)

    def test_the_documented_identity_still_holds(self) -> None:
        """`normalize_django_value`'s docstring contract, which is the reason
        this change is safe for every `json.dumps`-shaped consumer::

            json.dumps(normalize_django_value(v), cls=Enc) == json.dumps(v, cls=Enc)

        It survives carrying the object through, because the encoder handles a
        datetime — the same reason it survives for `Decimal` (#2239).
        """
        for value in FAMILY.values():
            assert json.dumps(normalize_django_value(value), cls=RealDjangoEncoder) == json.dumps(
                value, cls=RealDjangoEncoder
            )


class TestTheWireBytesAreUnchanged:
    """The consumer the issue names second, and the one a wire-shape pin would
    be about (#1448)."""

    @pytest.mark.parametrize("name", sorted(FAMILY))
    def test_djusts_encoder_writes_what_django_writes(self, name: str) -> None:
        value = FAMILY[name]
        assert json.dumps({"p": value}, cls=DjustEncoder) == json.dumps(
            {"p": value}, cls=RealDjangoEncoder
        )

    @pytest.mark.parametrize("name", sorted(FAMILY))
    def test_the_frame_is_byte_identical_before_and_after_the_pre_pass(self, name: str) -> None:
        """What `websocket.py` / `sse.py` / `api/dispatch.py` actually emit.

        They run `json.dumps(..., cls=DjangoJSONEncoder)` over the normalized
        context, so the question is whether normalizing first changes the
        bytes. It does not — which is why this change needs no new wire pin.
        """
        value = FAMILY[name]
        assert json.dumps(normalize_django_value({"p": value}), cls=DjustEncoder) == json.dumps(
            {"p": value}, cls=DjustEncoder
        )


class TestStateRoundtripBoundary:
    """Django's session serializer passes no encoder — the consumer that makes
    this an audit rather than a branch change. Mirrors
    `test_decimal_converters_2239.py::TestStateRoundtripBoundary`."""

    def test_django_session_serializer_refuses_the_bare_datetime(self) -> None:
        """The premise, run. This is why the flag exists — without it a session
        write of a view holding a `DateTimeField` would raise."""
        with pytest.raises(TypeError):
            DjangoSessionSerializer().dumps(normalize_django_value({"p": dt.datetime(2020, 1, 1)}))

    @pytest.mark.parametrize("name", sorted(FAMILY))
    def test_django_session_serializer_accepts_the_roundtrip_form(self, name: str) -> None:
        payload = normalize_django_value({"p": FAMILY[name]}, state_roundtrip=True)
        assert DjangoSessionSerializer().dumps(payload)

    def test_the_restored_value_still_formats(self) -> None:
        """Whatever is stored is restored back onto the view and lands in the
        template context on the next render, so the stored string has to be one
        the date filters can still read."""
        value = dt.datetime(2020, 1, 1, 3, 4, 5)
        stored = json.loads(
            DjangoSessionSerializer()
            .dumps(normalize_django_value({"p": value}, state_roundtrip=True))
            .decode("latin-1")
        )
        assert _rust.render_template('{{ p|date:"Y-m-d H:i" }}', stored) == (
            _rust.render_template('{{ p|date:"Y-m-d H:i" }}', {"p": value})
        )


class TestTheRustStateBackendRoundTrip:
    """`Value::Encoded` already had a tagged msgpack encoding (#2448/#2458).
    What changes is that the LiveView path now reaches it."""

    @pytest.mark.parametrize("name", sorted(FAMILY))
    def test_a_datetime_survives_the_msgpack_round_trip(self, name: str) -> None:
        from djust._rust import RustLiveView

        value = FAMILY[name]
        source = "{{ p }}|{% if p %}T{% else %}F{% endif %}"
        view = RustLiveView(source)
        view.update_state(normalize_django_value({"p": value}))
        before = view.render()
        restored = RustLiveView.deserialize_msgpack(view.serialize_msgpack())
        assert restored.render() == before

    def test_the_truthiness_bit_survives_it(self) -> None:
        """The half #2458 added the fourth payload element for: without it a
        cache hit restores `timedelta(0)` with the pre-#2458 truthiness and
        `{% if p %}` flips back."""
        from djust._rust import RustLiveView

        view = RustLiveView("{% if p %}T{% else %}F{% endif %}")
        view.update_state(normalize_django_value({"p": dt.timedelta(0)}))
        assert view.render() == "F"
        assert RustLiveView.deserialize_msgpack(view.serialize_msgpack()).render() == "F"


class TestWhatThisCosts:
    """The trade, stated rather than discovered later."""

    def test_the_bare_render_spelling_moves(self) -> None:
        """`{{ p }}` on the LiveView path renders `str(o)` now, where it
        rendered the encoder's ISO string.

        Both are already non-Django — Django LOCALIZES a bare datetime
        (`Jan. 1, 2020, 3:04 a.m.`) — so this moves one non-Django spelling to
        the other one djust already uses on its raw path, and buys agreement
        between djust's own two paths. #2462 made the mirror-image trade in the
        other direction and said so; this reverses that particular line.
        """
        value = dt.datetime(2020, 1, 1, 3, 4, 5, tzinfo=UTC)
        assert _liveview_render("{{ p }}", value) == "2020-01-01 03:04:05+00:00"
        assert _liveview_render("{{ p }}", value) != "2020-01-01T03:04:05Z"
        # ...and it is the RAW path's spelling, which is the point.
        assert _liveview_render("{{ p }}", value) == _raw_render("{{ p }}", value)

    @override_settings(USE_TZ=True, TIME_ZONE="America/New_York")
    def test_the_date_filters_are_unaffected(self) -> None:
        """The half that would have been a regression rather than a cost: an
        aware datetime still converts to the active timezone, and a bare `time`
        is still not shifted (#2216)."""
        assert (
            _liveview_render(
                '{{ p|date:"Y-m-d H:i" }}', dt.datetime(2026, 8, 22, 23, 30, tzinfo=UTC)
            )
            == "2026-08-22 19:30"
        )
        assert _liveview_render('{{ p|time:"H:i" }}', dt.time(9, 5, 30)) == "09:05"

    def test_a_timedelta_through_the_time_filter_IMPROVES(self) -> None:
        """It rendered nothing on the LiveView path, because the flattened
        `"P0DT00H01M30S"` matched no parse branch."""
        assert _liveview_render('{{ p|time:"H:i" }}', dt.timedelta(seconds=90)) == "00:01"


class TestTheGateOffWouldFail:
    """Non-vacuity (#1468), stated as the mutation and its MEASURED result.

    Restoring the pre-fix branch — `return django_json_datetime(value)`
    unconditionally, with every `__pycache__` cleared and pytest ERRORS counted
    apart from failures (#2129/#2135) — reddens **71** cases:

        this file                          39 failed,  92 passed
        the inverted pins elsewhere        16 failed, 556 passed
        the nine CI pins re-judged         16 failed, 326 passed
        the consumers this must NOT move    0 failed, 113 passed

    The last line is the load-bearing one and it is stated as a NEGATIVE
    deliberately: `TestTheWireBytesAreUnchanged`, #2458's literal msgpack
    payload pin and #2216's time-only renders pass in BOTH states, which is
    what says this change moves no wire shape and no date-filter answer. A
    gate-off that only reports what went red cannot say that.

    One correction worth keeping, because it is the discipline rather than the
    number: the first run predicted 0 for that group and got 1. A surprising
    gate-off result is a question, not a pass — and the answer was that the
    CLASSIFICATION was wrong, not the test.
    `TestStateRoundtripBoundary::test_django_session_serializer_refuses_the_bare_datetime`
    asserts the PREMISE that makes `state_roundtrip=True` necessary, and that
    premise only becomes observable once the pre-pass stops converting, so it
    belongs with the reddening group.
    """

    def test_the_flattened_spelling_is_still_what_the_boundary_stores(self) -> None:
        """The one place the old spelling survives, so a reader can see that
        the pre-fix string was not deleted, only relocated."""
        assert normalize_django_value(dt.timedelta(0), state_roundtrip=True) == "P0DT00H00M00S"
