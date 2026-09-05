"""``{% regroup %}`` refuses a non-iterable source, as ``{% for %}`` does (#2463).

The divergence
--------------
``{% regroup %}`` over a value Python cannot iterate failed soft to an empty
grouping. ``{% for %}``, which asks the same question one tag over, refused::

    {% regroup p by k as g %}[{{ g|length }}]   p = 2   django  TypeError
                                                        djust   [0]
    {% for x in p %}[{{ x }}]{% endfor %}       p = 2   django  TypeError
                                                        djust   TypeError   ok

No filter is involved: a bare ``int`` in the context already diverged.

Django's ``RegroupNode.render`` has exactly one guard::

    obj_list = self.target.resolve(context, ignore_failures=True)
    if obj_list is None:
        context[self.var_name] = []
        return ""
    context[self.var_name] = [... for key, val in groupby(obj_list, ...)]

``groupby`` calls ``iter()`` unguarded, so every non-iterable that is not
``None`` raises. ``None`` — which ``ignore_failures=True`` also produces for an
operand that does not resolve — is the one value that answers "no groups", and
djust already got that one right.

Two implementations, one fixed: the #1646 shape
-----------------------------------------------
#2451 gave the crate a ``python_iter`` probe over ``filters::iter_values`` and
wired the type-named refusal into the ``{% for %}`` arm, which is why the
``for`` row above agrees. The ``regroup`` handler kept its own older
``except TypeError: return []``.

The fix DELETES that second answer rather than adding a third. The handler is
Python and holds the real Python object, so the sink Rust's ``python_iter``
*models* is directly available to it: ``list(decoded)``. Its message is
CPython's verbatim. :class:`TestNoSecondIterabilityCheckWasAdded` pins that no
new mechanism appeared, because a guard added at the CONSUMER instead of
deferring to the sink is how this class comes back.

The issue's cited location is wrong, and usefully so
-----------------------------------------------------
#2463 says to look at *"``crates/djust_templates/src/renderer.rs``, the
``{% regroup %}`` node"*. There is no such node. ``regroup`` is a **Python**
assign-tag handler (``djust.template_tags.regroup``) dispatched from Rust, and
its fail-soft lived in ``_decode_source``. Tracing symptom-up rather than from
the citation is what found it (CLAUDE.md, Bug-report triage).

Symptom-up also found a SECOND half the issue does not mention: a ``bool``
source stayed at ``[0]`` even after the handler stopped swallowing, because the
value channel handed the handler Python's ``True`` — which is not JSON, so
``json.loads`` raised, the handler took its "this must be an unresolved bare
name" branch, and answered no groups. Fixed at the encoder, which is the same
type-tag argument #2385 made for the ``String`` arm.
:class:`TestABoolSourceReachesTheHandlerAsAValue` covers it.

Every expectation here is LIVE Django, never a transcription.

Refs #2463, #2451, #2459, #2385, #1646, #1079.
"""

from __future__ import annotations

import decimal
import re
from pathlib import Path

import pytest

pytest.importorskip("django")

from django.template import Context as DjangoContext  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402

from djust import _rust  # noqa: E402
from djust.mixins.rust_bridge import _collect_safe_keys  # noqa: E402
from djust.template_tags import _registered_handlers as _LIVE_HANDLERS  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
RENDERER = REPO / "crates" / "djust_templates" / "src" / "renderer.rs"
FILTERS_RS = REPO / "crates" / "djust_templates" / "src" / "filters.rs"
REGROUP_PY = REPO / "python" / "djust" / "template_tags" / "regroup.py"
TAG_HANDLERS = REPO / "python" / "djust" / "template_tags"

#: ``[{{ g|length }}]`` — enough to tell "refused" from "no groups" from "one
#: group". The same shape ``test_regroup_string_source_2385_2394`` uses.
REGROUP = "{% regroup p by k as g %}[{{ g|length }}]"
FOR = "{% for x in p %}[{{ x }}]{% endfor %}"

#: Values Python cannot iterate. ``None`` is deliberately NOT here — it is the
#: one Django guards, and it has its own class below.
NON_ITERABLE = [
    pytest.param(2, id="int"),
    pytest.param(0, id="int-zero"),
    pytest.param(-7, id="int-negative"),
    pytest.param(True, id="bool-true"),
    pytest.param(False, id="bool-false"),
    pytest.param(1.5, id="float"),
    pytest.param(0.0, id="float-zero"),
    pytest.param(decimal.Decimal("1.5"), id="Decimal"),
    pytest.param(10**30, id="bigint"),
]

#: Values Python CAN iterate, including the two empties. Every one of these
#: agreed before this fix and must still agree.
ITERABLE = [
    pytest.param("ab", id="str"),
    pytest.param("1", id="str-one-char"),
    pytest.param("", id="str-empty"),
    pytest.param([{"k": 1}, {"k": 1}, {"k": 2}], id="list"),
    pytest.param([], id="list-empty"),
    pytest.param(({"k": 1},), id="tuple"),
    pytest.param({"a": 1, "b": 2}, id="dict"),
    pytest.param({}, id="dict-empty"),
]


def _safe_keys(ctx: dict) -> list[str]:
    keys: list[str] = []
    for key, value in ctx.items():
        keys.extend(_collect_safe_keys(value, key))
    return keys


def dj(source: str, ctx: dict) -> str:
    """Django's answer, or ``<<REFUSED>>``.

    A fresh context dict per call: ``RegroupNode.render`` writes its result
    INTO the context it is given, so the two engines must not share one.
    """
    try:
        return DjangoTemplate(source).render(DjangoContext(dict(ctx)))
    except Exception:  # noqa: BLE001 — the refusal IS the answer
        return "<<REFUSED>>"


def du(source: str, ctx: dict) -> str:
    """djust's answer, or ``<<REFUSED>>``.

    Collapsed to a verdict rather than compared as text, for the reason #2454
    records: Django raises ``TypeError: 'int' object is not iterable`` and
    djust raises ``RuntimeError: Template error: …``, so comparing the two
    MESSAGES marks every agreeing refusal as a disagreement.
    """
    djust_ctx = dict(ctx)
    try:
        return _rust.render_template_with_dirs(source, djust_ctx, [], _safe_keys(djust_ctx) or None)
    except Exception:  # noqa: BLE001
        return "<<REFUSED>>"


def raised_message(source: str, ctx: dict) -> str:
    """The text djust raises, for the cases that assert on CPython's wording."""
    djust_ctx = dict(ctx)
    with pytest.raises(Exception) as caught:  # noqa: PT011 — the crate raises RuntimeError
        _rust.render_template_with_dirs(source, djust_ctx, [], _safe_keys(djust_ctx) or None)
    return str(caught.value)


def production(path: Path) -> str:
    """A crate module's source with every ``#[cfg(test)]`` block removed.

    The same reader :mod:`test_get_digit_returns_an_int_2459` uses, and for the
    same reason: these modules carry inline test modules whose fixtures would
    otherwise be counted as production emitters.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    keep, i = [], 0
    while i < len(lines):
        if lines[i].strip() == "#[cfg(test)]":
            while i < len(lines) and not lines[i].startswith("}"):
                i += 1
            i += 1
            continue
        keep.append(lines[i])
        i += 1
    return "\n".join(keep)


class TestRegroupRefusesWhatDjangoRefuses:
    """The cell the issue cites, and every non-iterable shape around it."""

    @pytest.mark.parametrize("value", NON_ITERABLE)
    def test_both_engines_refuse(self, value) -> None:
        assert dj(REGROUP, {"p": value}) == "<<REFUSED>>", value
        assert du(REGROUP, {"p": value}) == "<<REFUSED>>", value

    def test_the_cited_cell_verbatim(self) -> None:
        """``{% regroup p by k as g %}`` over ``p = 2``, with no filter."""
        assert dj(REGROUP, {"p": 2}) == "<<REFUSED>>"
        assert du(REGROUP, {"p": 2}) == "<<REFUSED>>"

    def test_the_message_is_CPythons_own_wording(self) -> None:
        """The refusal names the type, because ``list()`` raised it.

        Not re-derived here — this is what makes deleting the catch the whole
        fix rather than half of it: the handler holds the real object, so
        Python writes the message the ``{% for %}`` arm has to reconstruct.
        """
        assert "'int' object is not iterable" in raised_message(REGROUP, {"p": 2})
        assert "'float' object is not iterable" in raised_message(REGROUP, {"p": 1.5})

    @pytest.mark.parametrize(
        "operand",
        ["p", "p.a", "p|first", 'p|default:"x"'],
        ids=["bare", "dotted", "filtered", "defaulted"],
    )
    def test_every_operand_spelling_that_resolves_to_an_int(self, operand: str) -> None:
        """#2394's lesson: the defect is the VALUE, not one operand channel."""
        source = REGROUP.replace("p by", f"{operand} by")
        ctx = (
            {"p": {"a": 2}} if operand == "p.a" else {"p": [2]} if "|first" in operand else {"p": 2}
        )
        assert dj(source, ctx) == du(source, ctx) == "<<REFUSED>>", (source, ctx)


class TestTheNoneArmIsIntact:
    """Django's ``if obj_list is None`` arm — the one value that stays ``[]``."""

    def test_an_explicit_None_source_builds_no_groups(self) -> None:
        assert dj(REGROUP, {"p": None}) == du(REGROUP, {"p": None}) == "[0]"

    def test_an_UNRESOLVED_operand_builds_no_groups(self) -> None:
        """``ignore_failures=True`` turns a missing variable into ``None``.

        This is the arm the fix could most easily have broken: the value
        channel hands an unresolved operand through as its raw TOKEN, so the
        handler's fallback lookup is what produces the ``None``.
        """
        source = "{% regroup nope by k as g %}[{{ g|length }}]"
        assert dj(source, {}) == du(source, {}) == "[0]"

    def test_a_missing_DOTTED_operand_builds_no_groups(self) -> None:
        source = "{% regroup p.missing by k as g %}[{{ g|length }}]"
        assert dj(source, {"p": {"a": 1}}) == du(source, {"p": {"a": 1}}) == "[0]"


class TestEveryIterableSourceIsUnchanged:
    """Non-regression: the shapes that worked before still agree.

    ``{}`` and ``[]`` and ``""`` are here on purpose. The deleted catch fired
    for non-iterables, but a fix that deleted one line too many would take the
    empty containers with it — they iterate to nothing, they do not refuse.
    """

    @pytest.mark.parametrize("value", ITERABLE)
    def test_the_two_engines_agree(self, value) -> None:
        assert du(REGROUP, {"p": value}) == dj(REGROUP, {"p": value}), value

    def test_a_real_grouping_still_groups(self) -> None:
        rows = [{"k": "a"}, {"k": "a"}, {"k": "b"}]
        source = (
            "{% regroup p by k as g %}"
            "{% for x in g %}({{ x.grouper }}:{{ x.list|length }}){% endfor %}"
        )
        assert du(source, {"p": rows}) == dj(source, {"p": rows}) == "(a:2)(b:1)"


class TestTheForArmIsTheReferenceImplementation:
    """The #1646 pin: two implementations of "can this be iterated?" agree.

    Asserted as a VERDICT identity across the whole value axis rather than as a
    list of values, for the reason #2459's identity pin exists: a hand-written
    list of affected shapes is one short by construction. If either arm ever
    moves — the crate's ``{% for %}`` refusal or the handler's ``list()`` —
    this goes red without anyone having had to think of the value.
    """

    @pytest.mark.parametrize("value", NON_ITERABLE + ITERABLE + [pytest.param(None, id="None")])
    def test_regroup_and_for_answer_the_same_verdict(self, value) -> None:
        def verdict(engine, source):
            return "REFUSED" if engine(source, {"p": value}) == "<<REFUSED>>" else "RENDERED"

        assert verdict(du, REGROUP) == verdict(du, FOR), value

    @pytest.mark.parametrize("value", NON_ITERABLE + ITERABLE + [pytest.param(None, id="None")])
    def test_and_that_verdict_is_djangos(self, value) -> None:
        def verdict(engine, source):
            return "REFUSED" if engine(source, {"p": value}) == "<<REFUSED>>" else "RENDERED"

        assert verdict(du, REGROUP) == verdict(dj, REGROUP), value


class TestABoolSourceReachesTheHandlerAsAValue:
    """The half the issue does not mention: the value channel's type tag.

    ``value_channel_arg_string`` JSON-encodes a ``String`` so a resolved string
    is distinguishable from an unresolved bare name (#2385). A ``Bool`` went
    through ``Display`` — Python's ``True`` — which is not JSON, so the
    handler's ``json.loads`` raised and it took the bare-name branch. Deleting
    the fail-soft alone left ``bool`` at ``[0]``.
    """

    def test_the_handler_receives_the_original_bool(self) -> None:
        handler = _LIVE_HANDLERS["regroup"]
        original = handler.render
        seen = []

        def spy(args, context, autoescape=True):
            seen.append((args[0], context["p"]))
            return original(args, context, autoescape)

        handler.render = spy
        try:
            for value in (True, False):
                with pytest.raises(TypeError):
                    _rust.render_template_with_dirs(REGROUP, {"p": value}, [], None)
            assert seen == [("p", True), ("p", False)]
        finally:
            handler.render = original

    def test_the_encoder_has_a_Bool_arm(self) -> None:
        source = production(RENDERER)
        start = source.index("fn value_channel_arg_string(")
        body = source[start : source.index("\n}\n", start)]
        assert "Value::Bool(b) =>" in body, body
        # …and the String arm it was modelled on is still there. Two arms, and
        # #2385's pin asserts Decimal/BigInt stay OUT of both.
        assert "Value::String(s) | Value::SafeString(s) => {" in body, body
        assert "serde_json::to_string(s)" in body, body


class TestNoSecondIterabilityCheckWasAdded:
    """The fix deletes an answer; it must not add one (#1646).

    Both assertions are EQUALITIES against a named set, not floors. A floor
    ("at least one site refuses") cannot see an arm being REMOVED, which is
    the direction this whole class fails in — ``regroup``'s arm was not added
    wrong, it was left behind.
    """

    def test_the_handler_swallows_no_iteration_TypeError(self) -> None:
        source = REGROUP_PY.read_text(encoding="utf-8")
        code = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))
        assert "except TypeError" not in code, (
            "the regroup handler must defer to Python's own list(); catching "
            "TypeError here is the second answer #2463 deleted"
        )

    def test_no_tag_handler_swallows_an_iteration_TypeError(self) -> None:
        """The whole handler package, not just ``regroup``.

        The set is asserted EMPTY rather than "regroup is clean" so a NEW
        handler that grows the same fail-soft is caught the day it lands.
        """
        offenders = sorted(
            path.name
            for path in TAG_HANDLERS.glob("*.py")
            if "except TypeError" in path.read_text(encoding="utf-8")
        )
        assert offenders == [], offenders

    def test_the_crate_has_exactly_two_not_iterable_emitters(self) -> None:
        """``ValueOpError::detail`` (filters) and the ``{% for %}`` arm.

        An equality on the SET of file:line sites. A third copy of the message
        means a third answer to the same question; zero means the refusal was
        deleted. Both are failures and both turn this red.
        """
        found = set()
        for path in (RENDERER, FILTERS_RS):
            for number, line in enumerate(production(path).splitlines(), start=1):
                stripped = line.strip()
                if stripped.startswith("//"):
                    continue
                if "object is not iterable" in stripped:
                    found.add((path.name, stripped))
        # The canary: the extraction is not silently reading nothing.
        assert len(found) == 2, sorted(found)
        assert {name for name, _ in found} == {"renderer.rs", "filters.rs"}, sorted(found)
        by_file = {name: text for name, text in found}
        assert by_file["renderer.rs"] == "\"'{}' object is not iterable\","
        assert by_file["filters.rs"] == (
            "ValueOpError::NotIterable => format!(\"'{ty}' object is not iterable\"),"
        )

    def test_grouping_delegates_to_djangos_regroup_node(self) -> None:
        source = REGROUP_PY.read_text(encoding="utf-8")
        assert "DjangoBuiltinTagHandler" in source
        assert "from django.template.defaulttags import regroup" in source
        assert "json.loads" not in source


class TestWhatThisDeliberatelyDoesNOTClose:
    """Asserted in the DIVERGING direction, so closing it reddens this.

    A bare ``object()`` still renders where Django refuses. The REASON moved at
    #2477/#2489 and the divergence did not, which is the whole point of keeping
    these two tests rather than editing the number.

    It used to arrive at the value channel as ``Value::String(str(o))`` —
    ``Value``'s conversion had no variant for it — so the handler correctly
    iterated a STRING and produced one group. ``opaque_value`` carries it now,
    so ``{% for %}`` refuses exactly as Django does; the value reaching the
    regroup HANDLER is no longer a string and it yields ZERO groups instead of
    one. Django refuses either way, so the cell is still divergent — but the
    two tags no longer agree with each other, and that is the signature of a
    defect in the tag rather than below it. Re-diagnosed rather than
    re-numbered; still not this issue's business.
    """

    def test_a_bare_object_refuses_as_django_does(self) -> None:
        value = object()
        assert dj(REGROUP, {"p": value}) == "<<REFUSED>>"
        # The typed bridge now preserves the original non-iterable object.
        assert du(REGROUP, {"p": value}) == "<<REFUSED>>"

    def test_and_the_reason_is_no_longer_that_it_arrives_as_a_string(self) -> None:
        """The evidence, not the assertion — and it is the OTHER way round now.

        ``{% for %}`` iterated the object's repr, character by character, and
        both tags then agreed with each other while disagreeing with Django:
        the signature of a defect BELOW them. ``{% for %}`` refuses now, with
        CPython's own message, so the two tags disagree with each other and the
        remaining regroup divergence is the tag's.
        """
        value = object()
        assert dj(FOR, {"p": value}) == "<<REFUSED>>"
        assert du(FOR, {"p": value}) == "<<REFUSED>>"
        assert not du(FOR, {"p": value}).startswith("[&lt;][o][b][j]"), (
            "the repr is being iterated again — the carrier stopped claiming a "
            "bare object, which is #2477/#2489 regressing"
        )

    def test_a_Decimal_refuses_but_names_the_wrong_type(self) -> None:
        """Same conversion boundary, benign direction.

        A ``Decimal`` crosses the value channel as a JSON NUMBER, so the
        handler refuses it as a ``float``. Both engines refuse — the verdict
        agrees, only the type name does not — and #2385's pin explains why the
        channel deliberately does not send it as a string.
        """
        message = raised_message(REGROUP, {"p": decimal.Decimal("1.5")})
        assert re.search(r"'(float|decimal\.Decimal)' object is not iterable", message), message
        assert dj(REGROUP, {"p": decimal.Decimal("1.5")}) == "<<REFUSED>>"
