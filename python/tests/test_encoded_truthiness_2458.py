"""`bool(o)` decides an `Encoded`'s truthiness, not its display text (#2458).

The defect
----------
`bool(timedelta(0))` is ``False`` in Python and in Django, and was ``True`` in
djust::

    {% if p %}T{% else %}F{% endif %}     p = timedelta(0)

    python  False        django  F        djust  T

It predates #2448: a ``timedelta`` crossed the PyO3 boundary as
``Value::String("0:00:00")``, which is non-empty and therefore truthy under the
string rule.  #2448 gave the datetime family ``Value::Encoded`` and deliberately
kept the pre-fix answer (``!display.is_empty()`` — always true for this family),
because a truthiness change is not something a JSON-spelling fix should make
silently.

What #2448 changed is that the fix became *possible*: the type is no longer
erased at the conversion.  ``Encoded`` now carries a fourth field, ``truthy``,
set from Python's own ``bool(o)``.

Why the bit is carried rather than derived
------------------------------------------
Two derivations were available and both are wrong:

* off the ENCODER spelling (``json == "P0DT00H00M00S"``) — exact for the
  builtin, but it answers a truthiness question with a string comparison and
  cannot see a ``timedelta`` subclass that overrides ``__bool__``;
* off the DISPLAY spelling (``display == "0:00:00"``) — additionally wrong,
  because that is also the display text of the perfectly ordinary and
  Python-TRUTHY ``str`` ``"0:00:00"``.  The second derivation is the one the
  ``timesince`` argument rule would have had to use, and #2448's own
  ``test_every_display_arm_that_can_be_falsy_is_handled`` refused it on exactly
  that ground.

``bool(o)`` at the conversion is Python's answer for whatever the object is, so
it generalises to any future ``Encoded`` member and to any subclass.

The second half: the `timesince` ARGUMENT
-----------------------------------------
``{{ p|timesince:q }}`` with ``q = timedelta(0)`` measures from now in Django
and raised in djust.  The cause was a SECOND, text-shaped copy of the falsiness
rule — ``timesince_arg_is_falsy(&str, bool)`` — living in ``filters.rs``
alongside the value-typed ``arg_is_falsy`` (#2413) that ``ArgType::is_falsy``
already computes from the resolved ``Value``.  Two mechanisms for one question,
the #1646 shape at filter-argument scale.

The copy is gone and the filter reads the shared bit.  That closed three more
divergences the text rule had, which #2458's own issue did not predict — see
``TestTheConvergenceDividend``.

The state round trip
--------------------
``ENCODED_TAG``'s msgpack payload grew from three strings to
``[type_name, display, json, truthy]``.  Without that, one read of the default
``InMemoryStateBackend`` restores the value with the pre-#2458 truthiness and
``{% if p %}`` flips back — the same reopening the tag exists to prevent for the
JSON spelling (#2448), and the ``Decimal`` version of which shipped once and was
caught only by a gate-off (#2135).  The three-element payload is still READ, and
deserializes to the truthiness it was written with.
"""

from __future__ import annotations

import datetime
import os
import pathlib
import re
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "examples" / "demo_project"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "demo_project.settings")

import django  # noqa: E402

django.setup()

from django.template import Context as DjangoContext  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402

from djust import _rust  # noqa: E402

CORE_RS = pathlib.Path(__file__).resolve().parents[2] / "crates" / "djust_core" / "src" / "lib.rs"
FILTERS_RS = (
    pathlib.Path(__file__).resolve().parents[2]
    / "crates"
    / "djust_templates"
    / "src"
    / "filters.rs"
)

IF = "{% if p %}T{% else %}F{% endif %}"

UTC = datetime.timezone.utc
PLUS = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
MINUS = datetime.timezone(datetime.timedelta(hours=-8))


def djust_render(source: str, context: dict) -> str:
    return _rust.render_template(source, context)


def django_render(source: str, context: dict) -> str:
    return DjangoTemplate(source).render(DjangoContext(context))


#: Every axis the datetime family actually has, crossed — because a curated
#: table samples one axis and blinds you on the next (v1.1.1-2 canon), and
#: because #2448's own report tabulated `time` as agreeing on exactly that
#: mistake.
#:
#: * type: datetime / date / time / timedelta
#: * microsecond: zero and non-zero
#: * tzinfo: naive, UTC, positive offset, negative offset
#: * timedelta: zero, positive, negative, sub-second, multi-day
#:
#: `bool()` is the expected answer for every row and is computed rather than
#: written down, so a row cannot be mis-transcribed.
FAMILY: dict[str, datetime.date | datetime.time | datetime.timedelta] = {
    "datetime naive": datetime.datetime(2020, 1, 1, 3, 4, 5),
    "datetime naive us": datetime.datetime(2020, 1, 1, 3, 4, 5, 123456),
    "datetime naive us tiny": datetime.datetime(2020, 1, 1, 3, 4, 5, 7),
    "datetime midnight": datetime.datetime(2020, 1, 1, 0, 0, 0),
    "datetime utc": datetime.datetime(2020, 1, 1, 3, 4, 5, tzinfo=UTC),
    "datetime utc us": datetime.datetime(2020, 1, 1, 3, 4, 5, 123456, tzinfo=UTC),
    "datetime plus": datetime.datetime(2020, 1, 1, 3, 4, 5, tzinfo=PLUS),
    "datetime minus": datetime.datetime(2020, 1, 1, 3, 4, 5, tzinfo=MINUS),
    "date": datetime.date(2020, 1, 1),
    "date epoch": datetime.date(1970, 1, 1),
    "date min": datetime.date.min,
    "time": datetime.time(3, 4, 5),
    "time us": datetime.time(3, 4, 5, 123456),
    "time midnight": datetime.time(0, 0, 0),
    "time midnight us": datetime.time(0, 0, 0, 1),
    "time aware utc": datetime.time(3, 4, 5, tzinfo=UTC),
    "time midnight aware": datetime.time(0, 0, 0, tzinfo=UTC),
    "timedelta zero": datetime.timedelta(0),
    "timedelta positive": datetime.timedelta(seconds=90),
    "timedelta negative": datetime.timedelta(seconds=-90),
    "timedelta us only": datetime.timedelta(microseconds=1),
    "timedelta us only neg": datetime.timedelta(microseconds=-1),
    "timedelta days": datetime.timedelta(days=3, hours=4),
    "timedelta days neg": datetime.timedelta(days=-3),
    "timedelta max": datetime.timedelta.max,
}

#: The one falsy member, named — so a fix that answered "falsy" for the whole
#: variant would fail the non-vacuity half rather than pass everything.
FALSY = {"timedelta zero"}


class TestPythonsOwnAnswerForTheWholeFamily:
    """The crossed axes, run against live Django rather than a table."""

    @pytest.mark.parametrize("name", sorted(FAMILY))
    def test_the_if_branch_matches_django(self, name: str) -> None:
        value = FAMILY[name]
        expected = "T" if bool(value) else "F"
        assert django_render(IF, {"p": value}) == expected, "Django changed"
        assert djust_render(IF, {"p": value}) == expected

    @pytest.mark.parametrize("name", sorted(FAMILY))
    def test_the_family_membership_of_FALSY_is_computed_not_transcribed(self, name: str) -> None:
        """Non-vacuity for the parametrisation above: exactly one row of the
        crossed sweep is falsy, and it is the one named.

        A midnight `time` has been truthy since 3.5 and a `date` has no falsy
        value at all, so a variant-wide "falsy" answer would show up here.
        """
        assert (not bool(FAMILY[name])) == (name in FALSY), name


class TestTheBitIsPythonsAndNotADerivation:
    """A subclass with an overridden `__bool__` is the case no derivation from
    either spelling can get right."""

    def test_a_subclass_that_overrides_bool_is_answered_by_the_subclass(self) -> None:
        class AlwaysFalse(datetime.timedelta):
            def __bool__(self) -> bool:
                return False

        class AlwaysTrue(datetime.timedelta):
            def __bool__(self) -> bool:
                return True

        # A NON-zero delta whose `__bool__` says False: every derivation from
        # the encoder spelling (`P0DT00H00M00S`) or the display spelling
        # (`0:00:00`) answers True here, because neither spelling is the zero
        # one.
        falsy = AlwaysFalse(seconds=90)
        assert bool(falsy) is False
        assert django_render(IF, {"p": falsy}) == "F"
        assert djust_render(IF, {"p": falsy}) == "F"

        # And the inverse: a ZERO delta whose `__bool__` says True. Both
        # spellings are the zero ones, so every derivation answers False.
        truthy = AlwaysTrue(0)
        assert bool(truthy) is True
        assert django_render(IF, {"p": truthy}) == "T"
        assert djust_render(IF, {"p": truthy}) == "T"

    def test_the_display_text_is_shared_with_a_truthy_string(self) -> None:
        """Why a display-text derivation is not merely inelegant but wrong.

        `str(timedelta(0))` is `"0:00:00"`, and so is the ordinary `str`
        `"0:00:00"` — which Python calls truthy. One text, two answers.
        """
        assert str(datetime.timedelta(0)) == "0:00:00"
        assert bool("0:00:00") is True
        assert django_render(IF, {"p": "0:00:00"}) == "T"
        assert djust_render(IF, {"p": "0:00:00"}) == "T"
        assert djust_render(IF, {"p": datetime.timedelta(0)}) == "F"


class TestTheStateRoundTripKeepsTheAnswer:
    """`SerializableViewState.state` round-trips through msgpack on EVERY read
    of the default `InMemoryStateBackend`, so an untagged `truthy` would flip
    the answer back after one cache hit — the `Decimal` version of exactly this
    shipped once and was caught only by a gate-off (#2135)."""

    @staticmethod
    def _round_trip(value: object) -> str:
        from djust._rust import RustLiveView

        view = RustLiveView(IF)
        view.set_state("p", value)
        return RustLiveView.deserialize_msgpack(view.serialize_msgpack()).render()

    @pytest.mark.parametrize("name", sorted(FAMILY))
    def test_a_round_trip_preserves_the_truthiness(self, name: str) -> None:
        value = FAMILY[name]
        assert self._round_trip(value) == ("T" if bool(value) else "F"), name

    def test_the_payload_is_what_carries_it(self) -> None:
        """Non-vacuity for the tag: the blob names it AND the fourth element is
        the bit.

        Without reading the payload the round trip above would also pass on an
        implementation that kept the value alive some other way, and the fourth
        element could be dropped with the suite green — which is the #2135
        shape exactly.
        """
        import msgpack

        from djust._rust import RustLiveView

        view = RustLiveView(IF)
        view.set_state("p", datetime.timedelta(0))
        blob = view.serialize_msgpack()
        assert b"__djust_encoded__" in blob
        payload = msgpack.unpackb(blob, raw=False, strict_map_key=False)[1]["p"][
            "__djust_encoded__"
        ]
        # NINE elements: #2466 appended `sized_empty` and `iterable`,
        # #2471/#2472 appended `repr(o)` and the comparison key, and #2481
        # appended the attribute map — each for the reason this bit was
        # appended, that a state entry dropping it restores a value answering
        # with the pre-fix rule after one cache hit.
        #
        # The fourth is still this issue's bit and its POSITION is what
        # matters: every optional is TRAILING, the safe slot in a positional
        # msgpack payload (#1541). The whole tail is spelled out so a silent
        # reshuffle cannot pass, and element 3 is asserted separately so the
        # claim survives a future append re-flowing the literal.
        assert payload == [
            "datetime.timedelta",
            "0:00:00",
            "P0DT00H00M00S",
            False,
            # #2477/#2489 WIDENED this slot from #2466's `sized_empty` boolean
            # to `len(o)` itself — a bit cannot say `Some(3)`, and the objects
            # the carrier claims now have counts. `None` for a `timedelta`:
            # `len(timedelta(0))` raises.
            None,
            False,
            "datetime.timedelta(0)",
            [1, 0, 0],
            # #2481's attribute map, appended last for the reason every
            # widening before it was: a positional payload only stays readable
            # if nothing moves (#1541). `{{ p.days }}` resolves off this.
            {"days": 0, "seconds": 0, "microseconds": 0},
            # #2477/#2489's enumerated items, appended after it. `None` for a
            # `timedelta`, which is not iterable — and `None` is a DIFFERENT
            # statement from an empty list, which is why the codec keeps them
            # apart.
            None,
        ]
        assert payload[3] is False, "the truthiness bit moved off element 3"

    def test_a_three_element_payload_still_reads(self) -> None:
        """A #2448-era process's state outlives it: a Redis backend hands back
        a three-element payload on the first request after a rolling deploy, so
        this is a live input rather than a hypothetical.

        It restores to the truthiness that entry was WRITTEN with
        (`!display.is_empty()`), which is the honest answer rather than a
        guess — the value's `bool()` was never recorded.

        Constructed by TRUNCATING a real four-element blob, so the test cannot
        drift from the shape the serializer actually writes.
        """
        import msgpack

        from djust._rust import RustLiveView

        view = RustLiveView(IF)
        view.set_state("p", datetime.timedelta(0))
        decoded = msgpack.unpackb(view.serialize_msgpack(), raw=False, strict_map_key=False)
        payload = decoded[1]["p"]["__djust_encoded__"]
        assert len(payload) == 10, payload  # nine until #2477/#2489 grew it
        # A LEGACY payload is not a truncation of the current one (#2477/#2489):
        # slot 4 widened from #2466's `sized_empty` boolean to `len(o)` itself,
        # so every width below 10 carries a `Bool` there while a 10-element one
        # carries an int or `None`. Put the boolean back before truncating, or
        # the test measures that mismatch rather than the fallback it is named
        # for.
        payload[4] = False
        decoded[1]["p"]["__djust_encoded__"] = payload[:3]
        legacy = msgpack.packb(decoded, use_bin_type=True)

        assert RustLiveView.deserialize_msgpack(legacy).render() == "T"
        # And the CURRENT payload for the same value says `F`, which is what
        # makes the arm above a compatibility read rather than a bug.
        assert self._round_trip(datetime.timedelta(0)) == "F"

    def test_a_FOUR_element_payload_still_reads(self) -> None:
        """The #2458-era shape, which #2466 widened to six and #2471/#2472 to
        eight.

        Same argument as the three-element arm one method up, two releases
        later: a Redis backend hands back a four-element payload on the first
        request after a rolling deploy. It restores `truthy` from the payload
        (so `timedelta(0)` stays `F`), `sized_empty` / `iterable` as `false` —
        correct for every value that shape could have held, since all four were
        datetimes and none has a `__len__` — and `repr` / `cmp_key` as the
        pre-#2472 answers, which is the honest restore rather than a guess.
        """
        import msgpack

        from djust._rust import RustLiveView

        view = RustLiveView(IF)
        view.set_state("p", datetime.timedelta(0))
        decoded = msgpack.unpackb(view.serialize_msgpack(), raw=False, strict_map_key=False)
        payload = decoded[1]["p"]["__djust_encoded__"]
        assert len(payload) == 10, payload
        # A LEGACY payload is not a truncation of the current one (#2477/#2489):
        # slot 4 widened from #2466's `sized_empty` boolean to `len(o)` itself,
        # so every width below 10 carries a `Bool` there while a 10-element one
        # carries an int or `None`. Put the boolean back before truncating, or
        # the test measures that mismatch rather than the fallback it is named
        # for.
        payload[4] = False
        decoded[1]["p"]["__djust_encoded__"] = payload[:4]
        legacy = msgpack.packb(decoded, use_bin_type=True)
        assert RustLiveView.deserialize_msgpack(legacy).render() == "F"

    def test_a_payload_of_the_wrong_shape_is_a_plain_dict(self) -> None:
        """The discrimination that keeps a user dict under this key from
        forging an `Encoded`: two elements is neither shape, so it falls
        through to `Value::Object` and renders as a dict."""
        import msgpack

        from djust._rust import RustLiveView

        view = RustLiveView("{{ p }}")
        view.set_state("p", datetime.timedelta(0))
        decoded = msgpack.unpackb(view.serialize_msgpack(), raw=False, strict_map_key=False)
        decoded[1]["p"]["__djust_encoded__"] = ["datetime.timedelta", "0:00:00"]
        forged = msgpack.packb(decoded, use_bin_type=True)
        out = RustLiveView.deserialize_msgpack(forged).render()
        assert "__djust_encoded__" in out, out


class TestTheConvergenceDividend:
    """Three divergences the SECOND half of this fix closed, which the issue
    did not predict (#1646's convergence dividend).

    Deleting `timesince_arg_is_falsy` — a text-shaped copy of a rule the
    codebase already answered value-typed — did not just make `timedelta(0)`
    work. It replaced every answer the text rule got wrong.
    """

    NOON = datetime.datetime(2020, 1, 1, 12, 0, 0)

    def _raises_djust(self, source: str, ctx: dict) -> bool:
        try:
            djust_render(source, ctx)
        except Exception:
            return True
        return False

    def _raises_django(self, source: str, ctx: dict) -> bool:
        try:
            django_render(source, ctx)
        except Exception:
            return True
        return False

    def test_a_zero_timedelta_ARGUMENT_measures_from_now(self) -> None:
        """The row #2458 asked for. Django's `if not now:` falls through to the
        wall clock; djust raised, because the text rule could not accept
        `"0:00:00"`."""
        src = "{{ p|timesince:q }}"
        ctx = {"p": self.NOON, "q": datetime.timedelta(0)}
        assert not self._raises_django(src, ctx), "Django changed"
        assert not self._raises_djust(src, ctx)
        assert djust_render(src, ctx) == djust_render("{{ p|timesince }}", {"p": self.NOON})

    @pytest.mark.parametrize("text", ["0", "None", "False", "0.0", "[]", "{}"])
    def test_a_resolved_STRING_that_spells_a_falsy_object_is_truthy(self, text: str) -> None:
        """The text rule read these as the objects they spell. Python does not:
        every non-empty `str` is truthy and has no `.year`, so Django raises."""
        src = "{{ p|timesince:q }}"
        ctx = {"p": self.NOON, "q": text}
        assert self._raises_django(src, ctx), f"Django changed for {text!r}"
        assert self._raises_djust(src, ctx), text

    def test_an_empty_sequence_under_legacy_display_is_falsy(self) -> None:
        """`legacy_display` renders EVERY sequence as the literal `[List]`, so
        the text rule could not tell `[]` from `['a']` and raised for both."""
        _rust.set_django_value_repr(False)
        try:
            src = "{{ p|timesince:q }}"
            assert not self._raises_django(src, {"p": self.NOON, "q": []})
            assert not self._raises_djust(src, {"p": self.NOON, "q": []})
            # Non-vacuity: the full list is truthy and is not a date.
            assert self._raises_django(src, {"p": self.NOON, "q": ["a"]})
            assert self._raises_djust(src, {"p": self.NOON, "q": ["a"]})
        finally:
            _rust.set_django_value_repr(True)


class TestTheSinkHasExactlyTheCallersItClaims:
    """Grep the SINK, and pin the caller SET rather than a floor (#1125).

    Both directions, with a canary that proves each can go red — a pin nobody
    has watched fail is a pin whose failure mode is unknown (#2129/#2135).
    """

    @staticmethod
    def _production(source: str) -> str:
        """Source with `//` comments and the test module removed. A pin that
        counts an occurrence in a COMMENT is the #2237 false alarm, and one
        that counts a `#[cfg(test)]` occurrence measures the test rather than
        the engine."""
        head = source.split("#[cfg(test)]", 1)[0]
        return "\n".join(line for line in head.splitlines() if not line.lstrip().startswith("//"))

    @staticmethod
    def _count(source: str, needle: str) -> int:
        return len(re.findall(re.escape(needle), source))

    #: `truthy` is written at exactly one place — the conversion — and read at
    #: exactly one — `is_truthy`'s `Encoded` arm. The struct field and the two
    #: deserializer arms are the rest.
    def test_the_bit_is_set_at_the_conversion_and_read_by_is_truthy(self) -> None:
        src = self._production(CORE_RS.read_text(encoding="utf-8"))
        assert "let truthy = ob.is_truthy().ok()?;" in src, (
            "the conversion stopped asking Python — a derivation from either "
            "spelling is what #2458 rejected"
        )
        assert "Value::Encoded(e) => e.truthy," in src, (
            "`is_truthy` stopped reading the carried bit"
        )
        assert "!e.display.is_empty()" not in src, "the pre-#2458 display-emptiness rule is back"

    def test_every_payload_width_is_read_and_only_the_narrowest_derives(self) -> None:
        """The compatibility read is deliberate and bounded: exactly ONE arm
        may fall back to `!display.is_empty()`, and it is the three-element
        one.

        SIX widths since #2477/#2489 widened slot 4 and appended the items —
        ten (current), nine (#2481-era), eight (#2471/#2472-era), six
        (#2466-era), four (#2458-era) and three (#2448-era) — so exactly FIVE
        arms carry the bit verbatim from the payload and exactly ONE derives
        it. The count is an equality rather than a floor so a DELETED
        compatibility arm reddens it as loudly as an added derivation:
        dropping the four-element read would silently turn every state entry
        written by a 1.1.x process into a plain dict after a rolling deploy.
        """
        src = self._production(CORE_RS.read_text(encoding="utf-8"))
        assert self._count(src, "truthy: !display.is_empty(),") == 1
        assert self._count(src, "truthy: *truthy,") == 5

    def test_the_counter_goes_red_in_BOTH_directions(self) -> None:
        """The canary. Each mutation asserts it APPLIED before its count is
        read, so a no-op edit cannot report a passing number."""
        src = self._production(CORE_RS.read_text(encoding="utf-8"))
        baseline = self._count(src, "truthy: !display.is_empty(),")
        assert baseline == 1, baseline

        added = src.replace(
            "truthy: !display.is_empty(),",
            "truthy: !display.is_empty(),\n// dup\ntruthy: !display.is_empty(),",
            1,
        )
        assert added != src, "the ADD mutation did not apply"
        assert self._count(added, "truthy: !display.is_empty(),") == baseline + 1

        removed = src.replace("truthy: !display.is_empty(),", "truthy: true,", 1)
        assert removed != src, "the REMOVE mutation did not apply"
        assert self._count(removed, "truthy: !display.is_empty(),") == baseline - 1

    def test_the_text_shaped_argument_predicate_is_gone(self) -> None:
        """The second half's structural claim: one definition of the falsiness
        rule, and it is the value-typed one."""
        src = FILTERS_RS.read_text(encoding="utf-8")
        assert "fn timesince_arg_is_falsy(" not in src
        assert src.count("pub(crate) fn arg_is_falsy(") == 1


class TestWhatThisDeliberatelyDoesNOTClose:
    """Pinned as still-divergent so a stale exemption goes red (#1859)."""

    def test_a_set_was_still_truthy_here_and_is_now_CLOSED(self) -> None:
        """The same family one level up — filed as #2466 and closed there.

        A `set` had no `Value` variant, so the conversion landed it on its
        final `Value::String(str(o))` and it arrived as the non-empty
        `"set()"` while `bool(set())` is `False`. That was a defect at the
        CONVERSION rather than in the truthiness rule — `Encoded` never
        entered it — and it was out of a datetime fix's scope (#1079).

        This method was written to fail the day it was closed, and it did.
        Flipped rather than deleted: the localisation ("the rule is right, the
        value never reaches it") is what makes the two issues separable, and
        that stays worth checking in the other direction. Full coverage lives
        in `python/tests/test_falsy_conversion_2466.py`.
        """
        assert bool(set()) is False
        assert django_render(IF, {"p": set()}) == "F"
        assert djust_render(IF, {"p": set()}) == "F"

    def test_a_date_shaped_STRING_argument_still_reads_as_a_date(self) -> None:
        """The residue of the wire format, not of truthiness.

        A Python `datetime` crosses into Rust as a string and has no other
        spelling, so a resolved `str` that is date-SHAPED is read as the
        datetime it spells. The truthiness gate passes it (a non-empty `str`
        IS truthy, which is now right); the PARSE below it has no type to
        consult. Unchanged by this fix.
        """
        src = "{{ p|timesince:q }}"
        ctx = {"p": TestTheConvergenceDividend.NOON, "q": "2020-01-01 15:30:00"}
        with pytest.raises(Exception):
            django_render(src, ctx)
        assert djust_render(src, ctx)  # renders a duration rather than raising

    def test_the_bare_display_of_a_datetime_is_unchanged(self) -> None:
        """`Encoded::display` is `str(o)` and `{{ p }}` still renders it —
        djust's bare-render spelling already diverges from Django's localized
        one, and moving it would be an unrelated behaviour change riding a
        truthiness fix (the same reasoning #2448 gave)."""
        value = datetime.datetime(2020, 1, 1, 3, 4, 5)
        assert djust_render("{{ p }}", {"p": value}) == "2020-01-01 03:04:05"
