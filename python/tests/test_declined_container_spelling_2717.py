"""#2717 — a large `list` / `QuerySet` is no longer converted at binding time.

The #2695 review made the conversion decline to enumerate a sized sequence
past ``OPAQUE_ITEM_CAP``, then EXEMPTED a ``list`` and a Django ``QuerySet``
from that decline at any length, because declining those two produced a wrong
SPELLING: ``{{ rows }}`` over a 100 001-row queryset rendered 66 MB of djust's
own identity dicts where the same queryset one row shorter rendered
``[qs0, qs1, qs2]``, and ``{{ rows.0 }}`` answered ``''``.

The exemption cost, measured on an UNEVALUATED ``User.objects.all()`` over a
real 150 000-row table (issue #2717, reproduced on this branch):

===========================  ==========  ========
cell                         peak RSS    seconds
===========================  ==========  ========
``{{ v|length }}`` exempt      4 437 MB     13.4
``{{ v|length }}`` declined      567 MB      9.7
``{{ v.0 }}``      exempt      2 982 MB     13.3
``{{ v.0 }}``      declined       149 MB      0.7
===========================  ==========  ========

#2717 keeps the spelling and drops the cost, with two changes that are
deliberately narrow:

1. ``Encoded::declined_list_spelling`` answers the THREE sinks that spell a
   value whole — ``{{ v }}``, ``|pprint``, ``|json_script`` — from the live
   handle, through the same ``consume_live_items`` walk ``{% for %}`` already
   uses. Every other sink already read only what it needed.
2. ``_SidecarQuerySetProxy.__getitem__``, which the live walk needs to
   subscript a declined queryset and which the proxy never had.

The exemption is then gone, and nothing is exempt for its length.

**What this file does NOT re-pin.** That the rendered bytes are unchanged
either side of the cap is
``TestARealQuerySetIsSpelledTheSameOnBothSidesOfTheCap`` in
``test_sized_sequence_conversion_2695_2693.py`` — it was written for the
exemption and passes unchanged against the replacement, which is the strongest
statement available that the swap is invisible. This file pins the parts that
are new: the cost, the proxy's subscript, the floor on the shapes the decline
NEWLY claims, and the boundary against #2704.
"""

from __future__ import annotations

import array
import collections
import datetime
import functools
import json
import pathlib
import re

import pytest

CAP = 100_000
PAST = CAP + 1
SECRET = "pbkdf2_sha256$SUPERSECRETHASH"

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------
# Shapes.
#
# `None` padding puts a collection past the cap without building 100 000 real
# objects — the same device the #2695 file uses, and only element 0 is ever
# read.
# --------------------------------------------------------------------------
#: A FIXED `date_joined`, because `|pprint` and `|json_script` dump every
#: field and `auto_now_add`'s microseconds would make two builds of the same
#: fixture differ — a spelling comparison would then fail for a reason that
#: has nothing to do with the cap.
_JOINED = datetime.datetime(2020, 1, 2, 3, 4, 5, tzinfo=datetime.timezone.utc)


def _users(padding: int) -> list:
    from django.contrib.auth.models import User

    rows = []
    for i in range(3):
        user = User(
            username="alice",
            password=SECRET,
            email="a@b.c",
            is_superuser=True,
            is_staff=True,
            date_joined=_JOINED,
        )
        user.pk = i
        rows.append(user)
    return rows + [None] * padding


def _queryset(padding: int):
    """A real ``QuerySet`` with its result cache stuffed — the exact state an
    evaluated queryset is in, with no database."""
    from django.contrib.auth.models import User

    qs = User.objects.all()
    qs._result_cache = _users(padding)
    return qs


def _proxy(padding: int):
    from djust.serialization import _SidecarQuerySetProxy

    return _SidecarQuerySetProxy(_queryset(padding))


def _projection(padding: int):
    """A ``.values()`` projection proxy — rows with no per-field floor, which
    the proxy refuses wholesale (#1986 vector 5)."""
    from django.contrib.auth.models import User
    from djust.serialization import _SidecarQuerySetProxy

    qs = User.objects.values("password")
    qs._result_cache = [{"password": SECRET}] * (padding + 3)
    return _SidecarQuerySetProxy(qs)


#: The shapes #2717's decline newly claims, and how to build one at a length.
NEWLY_DECLINED = {
    "list[Model]": _users,
    "QuerySet": _queryset,
    "_SidecarQuerySetProxy": _proxy,
}

#: The three sinks that spell a value WHOLE. Every other sink reads only what
#: it needs from the live handle and was never what the exemption protected.
CONTAINER_SINKS = ["{{ v }}", "{{ v|pprint }}", '{{ v|json_script:"x" }}']


@functools.lru_cache(maxsize=None)
def _shape(name: str, padding: int):
    """One built collection per (shape, length), reused across the sinks.

    Safe to share: every shape here is RE-iterable and nothing below mutates
    one, so a second render sees the same object in the same state — and
    building a 100 001-element collection once per sink rather than once per
    (shape, length) is most of this file's runtime.
    """
    return dict(NEWLY_DECLINED, **{"values()-projection-proxy": _projection})[name](padding)


def _render(src: str, value) -> str:
    from djust import _rust

    return _rust.render_template(src, {"v": value})


# --------------------------------------------------------------------------


class TestTheContainerSinksSpellFromTheLiveHandle:
    """The half the exemption existed to protect, done at the sink instead.

    Asserted as "the same spelling either side of the cap" rather than as an
    expected string, for the reason the #2695 file gives: what a queryset
    renders as is djust's own answer (``[qs0, qs1, qs2]``, never Django's
    ``<QuerySet [...]>``), and the claim is that crossing 100 000 rows does
    not CHANGE it.
    """

    @pytest.mark.parametrize("shape", sorted(NEWLY_DECLINED))
    @pytest.mark.parametrize("src", CONTAINER_SINKS)
    def test_the_spelling_is_the_same_on_both_sides_of_the_cap(self, shape: str, src: str) -> None:
        under = _render(src, _shape(shape, 0))
        past = _render(src, _shape(shape, PAST - 3))
        # The `None` padding is the ONLY thing that may differ: it is what puts
        # the collection past the cap and it has no counterpart under it.
        # Deleting the padding elements from the long rendering must give the
        # short one back, BYTE for byte — a prefix check would not catch a
        # changed suffix, and `json_script`'s is `]</script>`.
        padding_free = re.sub(r"(,\s*(None|null))+", "", past)
        assert padding_free == under, (
            f"{shape} {src}: past the cap the container is spelled\n"
            f"  {padding_free[:300]!r}\nwhere under it, it is\n  {under[:300]!r}"
        )
        assert SECRET not in under and SECRET not in past

    @pytest.mark.parametrize("shape", sorted(NEWLY_DECLINED))
    def test_the_payload_is_the_padding_and_not_a_re_spelling(self, shape: str) -> None:
        """The 66 MB cell.

        The declined spelling before #2717 was ``str()`` of djust's own
        identity dicts — 66 MB for ``{{ v }}`` against ~0.6 MB here, and every
        row carrying a ``__model__`` key that the correct spelling shows zero
        of.
        """
        past = _render("{{ v }}", _shape(shape, PAST - 3))
        assert past.count("__model__") == 0, (
            f"{shape}: `{{{{ v }}}}` past the cap spelled "
            f"{past.count('__model__')} identity maps — the decline put djust's "
            f"own serialization dicts on screen: {past[:200]!r}"
        )
        assert len(past) < 5_000_000, f"{shape}: `{{{{ v }}}}` rendered {len(past)} bytes"

    @pytest.mark.parametrize("shape", sorted(NEWLY_DECLINED))
    def test_the_item_sinks_answer_the_same_on_both_sides(self, shape: str) -> None:
        """``{{ v.0 }}`` is the other half — ``''`` before #2717 for both
        queryset shapes, because ``_SidecarQuerySetProxy`` had no
        ``__getitem__`` for ``Context::walk_live`` to subscript."""
        for src, expected in (
            ("{{ v.0 }}", "alice"),
            ("{{ v.0.username }}", "alice"),
            ("{{ v|first }}", "alice"),
            ("{% if v %}T{% else %}F{% endif %}", "T"),
        ):
            assert _render(src, _shape(shape, 0)) == expected, f"{shape} {src} under"
            assert _render(src, _shape(shape, PAST - 3)) == expected, f"{shape} {src} past"

    def test_the_padded_shapes_really_are_carriers(self) -> None:
        """Non-vacuity for every case above (#1200/#1468).

        A `list` and a `QuerySet` past the cap answer `True` only because
        #2717 removed the exemption; between the #2695 review and this change
        both answered `False` and every cell above was measuring the
        `Value::List` path twice.

        `_SidecarQuerySetProxy` answers `False` and that is correct rather
        than a gap: the conversion claims it one arm EARLIER, at
        `__djust_serialize__`, which hands back a `list` — and it is THAT
        list, past the cap, that is declined. The proxy shape is in this file
        because it is the object the `{{ v }}` walk actually converts, so a
        regression there would not show on the other two.
        """
        from djust import _rust

        assert _rust.crosses_as_encoded(_users(PAST - 3)) is True
        assert _rust.crosses_as_encoded(_users(0)) is False
        assert _rust.crosses_as_encoded(_queryset(PAST - 3)) is True
        assert _rust.crosses_as_encoded(_queryset(0)) is False
        # The proxy's own list, which is what the decline claims for it.
        assert _rust.crosses_as_encoded(_proxy(PAST - 3).__djust_serialize__()) is True
        assert _rust.crosses_as_encoded(_proxy(0).__djust_serialize__()) is False


class TestNothingIsConvertedUntilASinkAsksForIt:
    """The cost, as a COUNT rather than as a memory threshold.

    An RSS or wall-clock assertion is an outlier-sensitive gate and this repo
    has retired two of them; the property #2717 actually changed is *how many
    elements the render converts*, which is exact and load-independent. Each
    element counts its own conversion — `opaque_value` spells every carried
    object with `str(o)` — so the counter is the number of elements the
    conversion touched.

    Before #2717 every cell converted all 100 001; after it, only the cells
    that spell the container whole do.
    """

    class Counted:
        """Sized-sequence element that records each conversion.

        A class rather than a `Mock`: the conversion reaches `str(o)` through
        `opaque_value`, which a mock's auto-attributes would make unpredictable.
        """

        conversions = 0

        def __init__(self, i: int) -> None:
            self.i = i

        def __str__(self) -> str:
            type(self).conversions += 1
            return f"c{self.i}"

        def __repr__(self) -> str:
            # NOT counted, and defined at all only because a carrier records
            # `str(o)` AND `repr(o)`: counting both would double every number
            # here for no extra statement, and a class without `__repr__` puts
            # `<… object at 0x…>` inside the list spelling.
            return f"c{self.i}"

    def _fresh(self, n: int) -> list:
        # A dynamic subclass per call, so one test's count cannot leak into
        # the next (#1109).
        cls = type("Counted", (TestNothingIsConvertedUntilASinkAsksForIt.Counted,), {})
        cls.conversions = 0
        return [cls(i) for i in range(n)], cls

    @pytest.mark.parametrize(
        ("src", "expected"),
        [
            # Answered from `Encoded::len` / `truthy` — nothing is converted.
            ("{{ v|length }}", 0),
            ("{% if v %}T{% endif %}", 0),
            # One `__getitem__` on the live handle, one conversion.
            ("{{ v.0 }}", 1),
            ("{{ v|first }}", 1),
            ("{{ v|last }}", 1),
        ],
    )
    def test_a_cheap_cell_converts_almost_nothing_past_the_cap(
        self, src: str, expected: int
    ) -> None:
        rows, cls = self._fresh(PAST)
        _render(src, rows)
        assert cls.conversions == expected, (
            f"{src} converted {cls.conversions} of {PAST} elements; before #2717 it "
            f"converted all of them, which is the 4 GB the issue measured"
        )

    def test_a_container_sink_converts_every_element_exactly_once(self) -> None:
        """The other side of the trade, stated rather than hidden: spelling
        the container whole still costs one conversion per element — the same
        cost the exemption paid for EVERY cell. It is paid here because the
        cell emits every element, so there is nothing to save."""
        rows, cls = self._fresh(PAST)
        out = _render("{{ v }}", rows)
        assert cls.conversions == PAST
        assert out.startswith("[c0, c1, c2,")

    def test_under_the_cap_every_cell_converts_everything_as_it_always_did(
        self,
    ) -> None:
        """The bound is on the LENGTH, not on the cell: a short list is still
        enumerated at the conversion, so this fix cannot have moved the common
        case."""
        rows, cls = self._fresh(3)
        _render("{{ v|length }}", rows)
        assert cls.conversions == 3


class TestTheSidecarQuerySetProxySubscripts:
    """``_SidecarQuerySetProxy.__getitem__`` (#2717) — the third half of the
    sequence protocol the proxy forwards, and the one it was missing."""

    def test_an_index_returns_a_protected_model(self) -> None:
        from djust.serialization import _SidecarModelProxy

        assert isinstance(_proxy(0)[0], _SidecarModelProxy)

    def test_a_slice_protects_every_element(self) -> None:
        """`_protect_sidecar_value` has no list arm, so a slice's elements are
        protected here one at a time rather than by trusting it to recurse —
        a bare `qs[0:2]` is a list of RAW models."""
        from djust.serialization import _SidecarModelProxy

        sliced = _proxy(0)[0:2]
        assert len(sliced) == 2
        assert all(isinstance(item, _SidecarModelProxy) for item in sliced)

    def test_the_floor_holds_through_the_subscript(self) -> None:
        with pytest.raises(AttributeError):
            _proxy(0)[0].password
        assert _proxy(0)[0].username == "alice"

    def test_a_projection_behaves_as_the_zero_length_sequence_it_reports(
        self,
    ) -> None:
        """A `.values()` projection carries no per-field floor, so the proxy
        refuses it wholesale — `__len__` already reports 0 and `__iter__`
        already yields nothing, and the subscript says the same thing."""
        proj = _projection(0)
        assert len(proj) == 0
        with pytest.raises(IndexError):
            proj[0]
        assert proj[0:2] == []

    def test_the_walk_needs_it(self) -> None:
        """The cell, not just the method: without `__getitem__` the live walk
        falls through step 1 (item), step 2 (`getattr('0')`) and step 3
        (`current[0]`) and answers `VariableDoesNotExist`, which renders
        empty. That is what `{{ rows.0 }}` did for a declined queryset."""
        assert _render("{{ v.0 }}", _queryset(PAST - 3)) == "alice"
        assert _render("{{ v.0.username }}", _queryset(PAST - 3)) == "alice"


class TestTheFloorHoldsOnEveryShapeTheDeclineNewlyClaims:
    """The serialization floor, re-probed on the NEW crossing set (#2717).

    ``TestTheSerializationFloorHoldsOnTheNewHandle`` in the #2695 file sweeps a
    ``deque`` — the shape that was already carried. #2717 moves three more
    shapes onto ``Encoded::live`` and adds a new subscript to the proxy, so
    "unchanged in kind" would be a guess. This is the measurement.

    Every sink that can carry a field to the page, on both sides of the cap,
    against a model whose ``password`` / ``is_superuser`` / ``is_staff`` are
    all set — the whole of ``_ALWAYS_EXCLUDED_FIELDS``.
    """

    FLOOR_SINKS = [
        "{{ v.0.password }}",
        "{{ v.0.is_superuser }}",
        "{{ v.0.is_staff }}",
        "{{ v.0.get_session_auth_hash }}",
        "{{ v.0.password|default:'D' }}",
        "{{ v }}",
        "{{ v|pprint }}",
        '{{ v|json_script:"x" }}',
        "{{ v|first }}",
        "{{ v|last }}",
        '{{ v|slice:":2" }}',
        "{{ v|safeseq|join:',' }}",
        "{{ v|unordered_list }}",
        "{% for r in v %}{{ r.password }}|{{ r.is_superuser }}{% endfor %}",
    ]

    @pytest.mark.parametrize("shape", sorted(list(NEWLY_DECLINED) + ["values()-projection-proxy"]))
    @pytest.mark.parametrize("side", ["under", "past"])
    @pytest.mark.parametrize("src", FLOOR_SINKS)
    def test_no_floor_field_reaches_the_page(self, shape: str, side: str, src: str) -> None:
        out = _render(src, _shape(shape, 0 if side == "under" else PAST - 3))
        assert SECRET not in out, f"{shape} {side} the cap: {src} leaked the hash"
        # The privilege flags are `True` on the model and must render as the
        # floor's empty string, never as the flag. Matched on the WHOLE cell,
        # not on a substring: the `{% for %}` row also names `is_superuser`
        # and its correct output is the separators, not `''`.
        if src in ("{{ v.0.is_superuser }}", "{{ v.0.is_staff }}"):
            assert out == "", f"{shape} {side} the cap: {src} rendered {out!r}"
        if src.startswith("{% for"):
            # `sep|flag` per row, both halves refused: no hash, no `True`.
            assert set(out) <= {"|"}, (
                f"{shape} {side} the cap: the loop emitted {out[:120]!r} — every "
                f"row must contribute only its separator"
            )

    def test_the_sweep_is_not_vacuous(self) -> None:
        """Each shape's crossing bit, so a future change that stops declining
        one of them cannot leave this class quietly measuring the old path
        (#1859: a pin that cannot go red is decorative).

        The two direct shapes cross as carriers past the cap; the two proxies
        are claimed one arm earlier by `__djust_serialize__`, and it is the
        `list` they hand back that crosses — asserted through that hook so the
        `False` here is a statement about the ROUTE rather than an unexplained
        exception.
        """
        from djust import _rust

        assert _rust.crosses_as_encoded(_users(PAST - 3)) is True
        assert _rust.crosses_as_encoded(_queryset(PAST - 3)) is True
        assert _rust.crosses_as_encoded(_proxy(PAST - 3)) is False
        assert _rust.crosses_as_encoded(_proxy(PAST - 3).__djust_serialize__()) is True
        # The projection's hook is empty by refusal, which is why its rows
        # above are all blank — stated, so a reader does not mistake the blank
        # cells for the floor working.
        assert _projection(PAST - 3).__djust_serialize__() == []

    def test_the_model_really_carries_the_fields_being_denied(self) -> None:
        """Non-vacuity of the payload itself: if the fixture stopped setting
        `password`, every row above would pass for the wrong reason."""
        rows = _users(0)
        assert rows[0].password == SECRET
        assert rows[0].is_superuser is True
        assert rows[0].is_staff is True


class TestAContainerThatSpellsItselfIsNotRespelledAsAList:
    """The #2704 boundary, from the other side.

    ``declined_list_spelling`` must claim the shapes whose spelling IS their
    items' list repr and NOTHING else. A ``range``, a ``deque``, an ``array``
    and a ``bytes`` each spell their own container, and re-spelling one as a
    list is #2704's defect with the sign flipped.
    """

    @pytest.mark.parametrize(
        ("label", "build"),
        [
            ("range", lambda: range(10**9)),
            ("deque", lambda: collections.deque(range(PAST))),
            # `array`'s repr carries quotes (`array('i', [...])`), which the
            # renderer escapes — the reason `expected` is derived rather than
            # written out.
            ("array", lambda: array.array("i", [1] * PAST)),
        ],
    )
    def test_a_self_spelling_container_keeps_str_o(self, label: str, build) -> None:
        from django.utils.html import escape

        obj = build()
        out = _render("{{ v }}", obj)
        expected = escape(str(obj))
        # Compared in three pieces rather than as one `==`. These renderings
        # are up to 700 KB, and pytest's failure diff over two strings that
        # size does not come back — a whole-string `assert` turns a one-line
        # regression into an apparent hang.
        assert len(out) == len(expected), (
            f"{label} past the cap rendered {len(out)} bytes against "
            f"{len(expected)} for `str(o)`: {out[:120]!r}"
        )
        assert out[:200] == expected[:200], (
            f"{label} past the cap starts {out[:200]!r}; it must be its own "
            f"`str(o)`, not a list repr (#2704)"
        )
        assert out[-80:] == expected[-80:], f"{label} ends {out[-80:]!r}"

    def test_the_range_spelling_is_djangos_verbatim(self) -> None:
        """One literal, so the derived comparison above cannot pass by
        agreeing with a `str(o)` that is itself wrong."""
        assert _render("{{ v }}", range(10**9)) == "range(0, 1000000000)"

    def test_the_shapes_above_really_are_carriers(self) -> None:
        """Non-vacuity: under the cap these are `Value::List`s and the
        assertions would be about a different mechanism."""
        from djust import _rust

        assert _rust.crosses_as_encoded(range(10**9)) is True
        assert _rust.crosses_as_encoded(collections.deque(range(PAST))) is True


class TestTheContainerSpellingCallSitesAreTheSetNamed:
    """A SET, not a floor (#1125).

    Four call sites across three sinks: `{{ v }}` reaches the substitution
    twice, because `renderer::localize_if_number` short-circuits `Display` for
    a non-temporal carrier and returns `encoded.display` directly — a fourth
    site found by grepping the SINK rather than by listing the callers I
    expected. A fifth sink that spells a value whole has to be added to
    `container_spelling`'s callers, or it renders djust's own serialization
    dicts for a declined queryset.
    """

    EXPECTED = {
        "crates/djust_core/src/lib.rs": 1,  # Display for Value
        "crates/djust_templates/src/renderer.rs": 1,  # localize_if_number
        "crates/djust_templates/src/filters.rs": 2,  # pprint, json_script
    }

    def test_the_call_sites_are_exactly_these(self) -> None:
        call = re.compile(r"\.container_spelling\(\)")
        found = {}
        for path in sorted((REPO_ROOT / "crates").rglob("*.rs")):
            text = path.read_text(encoding="utf-8")
            n = len(call.findall(text))
            if n:
                found[str(path.relative_to(REPO_ROOT))] = n
        assert found == self.EXPECTED, (
            f"the container-spelling call sites moved: {found} != {self.EXPECTED}. "
            "A NEW site means a sink learned to spell a declined carrier — add it "
            "to EXPECTED. A MISSING site means a sink stopped, and a large "
            "queryset now renders djust's own serialization dicts there."
        )

    def test_the_one_rule_has_one_statement(self) -> None:
        """`spelling_is_the_items_list_repr` is asked from exactly one place —
        `declined_list_spelling` — so "which shapes spell as a list" cannot
        drift between the conversion and the sink (#1646)."""
        text = (REPO_ROOT / "crates/djust_core/src/lib.rs").read_text(encoding="utf-8")
        calls = re.findall(r"spelling_is_the_items_list_repr\(", text)
        # One definition + one call.
        assert len(calls) == 2, f"expected fn + 1 call site, found {len(calls)}"


class TestTheEagerHatchIsUnchanged:
    """`template_resolve_lazy: False` has no live handle to spell from, so the
    decline never applies there and neither does any of this (#2695's own
    rule, re-asserted because #2717 widened what the decline claims)."""

    def test_the_hatch_still_enumerates_a_large_list(self) -> None:
        import subprocess
        import sys
        import textwrap

        child = textwrap.dedent(
            """
            import json, django
            from django.conf import settings
            settings.configure(
                SECRET_KEY="x", DEBUG=False, USE_TZ=False,
                TEMPLATES=[{"BACKEND": "djust.template_backend.DjustTemplateBackend",
                            "NAME": "e", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}}],
                LIVEVIEW_CONFIG={"template_resolve_lazy": False},
            )
            django.setup()
            from djust import _rust
            from djust.render_env import apply_render_env

            # The engine's thread-local mirror of `LIVEVIEW_CONFIG`, pushed by
            # the same helper every real render path uses. Without it the Rust
            # side keeps its DEFAULT (lazy ON) and this child would measure the
            # shipped mode while claiming to measure the hatch.
            apply_render_env()
            v = list(range(100_001))
            # The RENDER first: `template_resolve_lazy` reaches Rust through a
            # setter the backend calls, so `crosses_as_encoded` asked before
            # any render reads the thread-local's DEFAULT (lazy on) and would
            # answer about the wrong engine mode.
            head = _rust.render_template("{{ v }}", {"v": v})[:12]
            length = _rust.render_template("{{ v|length }}", {"v": v})
            print(json.dumps({
                "encoded": _rust.crosses_as_encoded(v),
                "head": head,
                "length": length,
            }))
            """
        )
        out = subprocess.run(
            [sys.executable, "-c", child], capture_output=True, text=True, timeout=300
        )
        assert out.returncode == 0, out.stderr[-2000:]
        answer = json.loads(out.stdout.strip().splitlines()[-1])
        assert answer["encoded"] is False, "the hatch must never decline a sized sequence"
        assert answer["head"] == "[0, 1, 2, 3,"
        assert answer["length"] == "100001"
