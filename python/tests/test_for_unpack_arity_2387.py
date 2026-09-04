"""`{% for a, b in x %}` refuses an arity mismatch, as Django does (#2387).

The defect
----------
Django's ``ForNode.render`` checks the loop-variable count against the item's
length BEFORE unpacking, and raises::

    try:               len_item = len(item)
    except TypeError:  len_item = 1
    if num_loopvars != len_item:
        raise ValueError("Need %d values to unpack in for loop; got %d. " % …)
    unpacked_vars = dict(zip(self.loopvars, item))

djust padded the extra names with ``Value::Missing`` and rendered, so
``{% for a, b in p %}[{{ a }}={{ b }}]{% endfor %}`` over ``"abc"`` rendered
``'[a=][b=][c=]'`` where Django refuses the template. **More permissive than
Django, and silent** — the region rendered, with the variables empty.

The second half, which the issue does not name
----------------------------------------------
`zip` ITERATES the item; it does not index it. So an item whose length DOES
match unpacks by Python's iteration:

* ``{% for a, b in p %}`` over ``["ab"]`` binds ``a="a"``, ``b="b"``;
* over ``[{"x": 1, "y": 2}]`` binds ``a="x"``, ``b="y"`` (a dict iterates its
  keys).

djust bound the whole item to the first name and ``Missing`` to the rest, so
the dict case rendered the dict's own repr into ``{{ a }}``. The arity check
alone would have left both wrong, because both PASS it.

Exception parity
----------------
Both engines raise ValueError with Django's message, including its trailing
space. The backend preserves the exception class across the Rust boundary.

Provenance
----------
``TestTheUnpackArityDivergenceIsNamedNotFixed`` in
``test_for_unpack_comma_spelling_2377.py`` pinned this divergence and said it
"goes red the day this is closed, and names itself as the thing to move".
This is where it moved; its three parametrized cells are the first three rows
of :class:`TestBothEnginesRefuseAnArityMismatch`.

Every expectation here is LIVE Django, never a transcription.
"""

from __future__ import annotations

import pytest
from django.template import Context as DjangoContext
from django.template import Template as DjangoTemplate

from djust import _rust

UNPACK = "{% for a, b in p %}[{{ a }}={{ b }}]{% endfor %}"


def djust(tpl: str, ctx: dict) -> str:
    return _rust.render_template_with_dirs(tpl, dict(ctx), [], None)


def django(tpl: str, ctx: dict) -> str:
    return DjangoTemplate(tpl).render(DjangoContext(dict(ctx)))


def assert_agrees(tpl: str, ctx: dict) -> str:
    d = django(tpl, ctx)
    r = djust(tpl, ctx)
    assert r == d, f"{tpl!r} over {ctx!r}\n  django={d!r}\n  djust ={r!r}"
    return d


class TestBothEnginesRefuseAnArityMismatch:
    """Django's exact message, on every shape that reaches the check.

    The rows are parametrized over what `len(item)` answers, because that is
    what the check reads: a sequence's element count, a string's CODE POINT
    count, a dict's key count, and 1 for everything `len()` raises on.
    """

    @pytest.mark.parametrize(
        ("tpl", "ctx", "need", "got"),
        [
            # The three cells the #2377 pin carried, moved here verbatim.
            ("{% for a,b in p %}[{{ a }}={{ b }}]{% endfor %}", {"p": "abc"}, 2, 1),
            (UNPACK, {"p": "abc"}, 2, 1),
            ("{% for a,b,c in p %}[{{ a }}]{% endfor %}", {"p": [("x", "y")]}, 3, 2),
            # `len()` raises for these, so Django counts 1.
            (UNPACK, {"p": [1, 2]}, 2, 1),
            (UNPACK, {"p": [None]}, 2, 1),
            (UNPACK, {"p": [1.5]}, 2, 1),
            (UNPACK, {"p": [True]}, 2, 1),
            # A string item that is too LONG — the other side of the check.
            (UNPACK, {"p": ["abc"]}, 2, 3),
            # A sequence item that is too short.
            (UNPACK, {"p": [[1, 2], [3]]}, 2, 1),
            # A dict item with the wrong number of keys.
            (UNPACK, {"p": [{"x": 1}]}, 2, 1),
            # The spaced-comma spelling of #2377, over a nested sequence.
            (
                "{% for a ,b in p %}[{{ a }}={{ b }}]{% endfor %}",
                {"p": ["a", ["b", "c"]]},
                2,
                1,
            ),
        ],
    )
    def test_the_message_is_djangos_verbatim(
        self, tpl: str, ctx: dict, need: int, got: int
    ) -> None:
        expected = f"Need {need} values to unpack in for loop; got {got}. "

        with pytest.raises(ValueError) as django_exc:
            django(tpl, ctx)
        assert str(django_exc.value) == expected, (
            "live Django's message moved — this test transcribes nothing, so "
            "the expectation itself is what needs updating"
        )

        # djust refuses with the same class and message as Django.
        with pytest.raises(ValueError) as djust_exc:
            djust(tpl, ctx)
        assert str(djust_exc.value).endswith(expected), djust_exc.value

    def test_it_refuses_on_the_first_bad_item_even_after_good_ones(self) -> None:
        """The check is per-ITEM, inside the loop — a template that renders
        three iterations and then meets a short one renders NOTHING, because
        the exception discards the whole output."""
        ctx = {"p": [[1, 2], [3, 4], [5]]}
        with pytest.raises(ValueError, match="got 1"):
            django(UNPACK, ctx)
        with pytest.raises(ValueError, match="got 1"):
            djust(UNPACK, ctx)


class TestAMatchingArityUnpacksByIteration:
    """`dict(zip(self.loopvars, item))` iterates the item.

    These PASS the arity check, so they are not fixed by it — they are the
    half the issue does not name.
    """

    def test_a_string_item_unpacks_to_its_characters(self) -> None:
        assert assert_agrees(UNPACK, {"p": ["ab"]}) == "[a=b]"

    def test_a_dict_item_unpacks_to_its_keys(self) -> None:
        assert assert_agrees(UNPACK, {"p": [{"x": 1, "y": 2}]}) == "[x=y]"

    def test_three_names_over_a_three_character_string(self) -> None:
        assert (
            assert_agrees("{% for a, b, c in p %}[{{a}}{{b}}{{c}}]{% endfor %}", {"p": ["xyz"]})
            == "[xyz]"
        )

    def test_a_non_ascii_string_item_counts_code_points(self) -> None:
        """`len("中é")` is 2 in Python and 5 in bytes — the #2279 rule, now
        load-bearing for whether the render is REFUSED rather than only for
        what `|length` prints."""
        assert assert_agrees(UNPACK, {"p": ["中é"]}) == "[中=é]"
        with pytest.raises(ValueError, match="got 3"):
            django(UNPACK, {"p": ["中éx"]})
        with pytest.raises(ValueError, match="got 3"):
            djust(UNPACK, {"p": ["中éx"]})


class TestTheAnswersThatMustNotMove:
    """Everything that agreed before must still agree."""

    @pytest.mark.parametrize(
        ("tpl", "ctx"),
        [
            (UNPACK, {"p": [("x", "y")]}),
            (UNPACK, {"p": [["x", "y"]]}),
            (UNPACK, {"p": [[1, 2], [3, 4]]}),
            (UNPACK, {"p": []}),
            (UNPACK, {"p": None}),
            ("{% for a,b in p %}[{{ a }}={{ b }}]{% endfor %}", {"p": [("x", "y")]}),
            ("{% for a, b in p reversed %}[{{a}}{{b}}]{% endfor %}", {"p": [[1, 2], [3, 4]]}),
            ("{% for a, b in p %}x{% empty %}E{% endfor %}", {"p": []}),
            # Single variable: the whole other branch, untouched.
            ("{% for a in p %}[{{ a }}]{% endfor %}", {"p": ["abc", 1, None]}),
            # The `.items` idiom — the most common two-name loop there is.
            ("{% for k, v in p.items %}[{{ k }}={{ v }}]{% endfor %}", {"p": {"a": 1, "b": 2}}),
            ("{% for k, v in p.items %}[{{ k }}={{ v }}]{% endfor %}", {"p": {"a": [1, 2]}}),
        ],
    )
    def test_unaffected_shapes_still_agree(self, tpl: str, ctx: dict) -> None:
        assert_agrees(tpl, ctx)


class TestTheLengthRuleIsStatedOnce:
    """`python_len` is shared; the FALLBACK is not, and that is the point.

    Django writes `except TypeError: return 0` in `defaultfilters.length` and
    `except TypeError: len_item = 1` in `ForNode.render`. Collapsing the two
    into one "length with a fallback" would make one of them wrong, so the
    helper returns `None` and each call site picks. These two tests are the
    two fallbacks, measured through the two surfaces.
    """

    @pytest.mark.parametrize(
        "value", [None, 5, True, 1.5, "abc", [1, 2, 3], (1, 2), {"a": 1}, {}, ""]
    )
    def test_the_length_filter_is_unchanged_by_the_refactor(self, value: object) -> None:
        assert_agrees("{{ p|length }}", {"p": value})

    def test_the_length_filters_fallback_is_zero(self) -> None:
        """A value `len()` raises on prints 0, not 1."""
        assert assert_agrees("{{ p|length }}", {"p": 5}) == "0"

    def test_the_for_unpacks_fallback_is_one(self) -> None:
        """The SAME value counts as 1 in the arity check, not 0 — which is
        why the message reads `got 1`."""
        with pytest.raises(ValueError, match=r"got 1\. "):
            djust(UNPACK, {"p": [5]})
        with pytest.raises(ValueError, match=r"got 1\. "):
            django(UNPACK, {"p": [5]})

    def test_a_non_ascii_length_still_counts_code_points(self) -> None:
        assert assert_agrees("{{ p|length }}", {"p": "中<b"}) == "3"

    def test_a_dict_view_still_has_its_entry_count(self) -> None:
        assert assert_agrees("{{ p.items|length }}", {"p": {"a": 1, "b": 2}}) == "2"


class TestNoSafetyGrantSurvivesTheNewUnpackArm:
    """The non-sequence arm grants nothing, deliberately.

    `_collect_safe_keys` spells a dict BY KEY NAME, so the positional
    `<expr>.<index>.<i>` lookup the sequence arm uses would be the #2334
    collision here — a live XSS whenever keys are attacker-controlled. A key
    spelled `"1"` whose value is marked would otherwise resolve for the loop's
    SECOND key, which is a different string entirely.
    """

    def test_an_unpacked_dict_key_is_escaped(self) -> None:
        from djust.mixins.rust_bridge import _collect_safe_keys
        from django.utils.safestring import mark_safe

        ctx = {"p": [{"0": mark_safe("<b>x</b>"), "1": "<script>bad()</script>"}]}
        safe_keys: list[str] = []
        for key, value in ctx.items():
            safe_keys.extend(_collect_safe_keys(value, key))
        out = _rust.render_template_with_dirs(UNPACK, dict(ctx), [], safe_keys or None)
        # Both names are bound to KEY STRINGS ("0" and "1"), not to the values,
        # so nothing hostile appears at all — and nothing is emitted raw.
        assert out == "[0=1]", out
        assert "<script>" not in out and "<b>" not in out, out
