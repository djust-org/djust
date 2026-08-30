"""``safeseq`` replaces every item with its ``str()``, because ``mark_safe`` does (#2324).

Django's filter is ``[mark_safe(obj) for obj in value]`` and ``mark_safe`` is::

    def mark_safe(s):
        if hasattr(s, "__html__"): return s
        if callable(s): return _safety_decorator(mark_safe, s)
        return SafeString(s)          # str.__new__(SafeString, s) -> str(s)

so it does not merely MARK an item — it changes the item's TYPE. A list of ints
comes out a list of strings, and a nested sublist comes out the string Python
made of it. djust kept the typed value, which showed up two ways:

* directly, because a list renders its elements through ``repr``:
  ``{{ p|safeseq }}`` on ``[1, 2]`` is ``['1', '2']`` in Django and was
  ``[1, 2]`` here;
* through a LATER filter, because the item's type is still readable:
  ``{{ p|safeseq|unordered_list }}`` on ``['<b>', ['c', ['d']]]`` nests a
  ``<ul>`` in djust where Django emits ``<li>['c', ['d']]</li>`` — it is
  reading the string ``mark_safe`` made of the sublist.

The spelling is ``str()`` and NOT the render form
--------------------------------------------------
``Value``'s ``Display`` is Django's ``numberformat.format()`` for ``Float`` and
``Decimal`` (#2214, #2258), which is the right answer for ``{{ f }}`` and the
wrong one for ``str(f)``::

    1e20              str() '1e+20'   Display '100000000000000000000'
    Decimal('1E-9')   str() '1E-9'    Display '0.000000001'

That is exactly the split the ``@stringfilter`` coercion at the top of
``apply_builtin_filter`` already carries for the 28 filters Django decorates —
``safe`` among them since #2303. So this fix adds NO second stringify: it names
the existing one ``Value::py_str()`` (sibling of ``py_repr()``, which is the
same split for a NESTED value) and has both the coercion and ``safeseq`` call
it. :class:`TestOneStringifyMechanism` pins that mechanically.

Not a permissiveness change
---------------------------
``safeseq`` grants its items safety by NAME (``ITEM_SAFE_OUTPUT_FILTERS`` in
``renderer.rs``), before and after, so ``join``/``unordered_list`` emitted them
unescaped either way. What changes is *what* they emit — and Django emits the
same bytes, which is what :class:`TestNotMorePermissiveThanDjango` checks
against Django's own output rather than against "nothing is live".
"""

from __future__ import annotations

import random
import re
from decimal import Decimal
from pathlib import Path

import pytest

pytest.importorskip("django")

from django.template import Context as DjangoContext  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402
from django.utils.safestring import mark_safe  # noqa: E402

from djust import _rust  # noqa: E402

CRATES = Path(__file__).resolve().parents[2] / "crates"
FILTERS_RS = CRATES / "djust_templates" / "src" / "filters.rs"
CORE_RS = CRATES / "djust_core" / "src" / "lib.rs"


def django_render(src: str, value) -> str:
    return DjangoTemplate(src).render(DjangoContext({"p": value}))


def djust_render(src: str, value) -> str:
    # UN-normalized on purpose: `normalize_django_value` collapses a tuple to a
    # list, so a normalized context could not construct the tuple cells below.
    # Same premise as test_sequence_shape_preservation_2317_2321.py.
    return _rust.render_template(src, {"p": value})


def assert_agrees(src: str, value) -> None:
    d, r = django_render(src, value), djust_render(src, value)
    assert r == d, f"{src} on {value!r}: django={d!r} djust={r!r}"


class TestTheRowsFromTheIssue:
    """The exact cells #2324 measured against Django 5.2, verbatim."""

    @pytest.mark.parametrize(
        "value, expected",
        [
            ([1, 2], "[&#x27;1&#x27;, &#x27;2&#x27;]"),
            ([None, True], "[&#x27;None&#x27;, &#x27;True&#x27;]"),
            (
                ["<b>", ["c", ["d"]]],
                "[&#x27;&lt;b&gt;&#x27;, &quot;[&#x27;c&#x27;, [&#x27;d&#x27;]]&quot;]",
            ),
        ],
    )
    def test_safeseq_renders_a_list_of_strings(self, value, expected) -> None:
        assert djust_render("{{ p|safeseq }}", value) == expected
        assert_agrees("{{ p|safeseq }}", value)

    @pytest.mark.parametrize("sub", [["c", ["d"]], ("c", ("d",))])
    def test_unordered_list_reads_the_string_safeseq_made_of_a_sublist(self, sub) -> None:
        # The chain #2317 found this through: once `unordered_list` learned to
        # treat a tuple as a sublist, the tuple cell joined the list cell that
        # was already diverging. Django never nests here, because by the time
        # `unordered_list` runs there is no sequence left to nest.
        value = ["<b>", sub]
        out = djust_render("{{ p|safeseq|unordered_list }}", value)
        assert "<ul>" not in out, f"still nesting: {out!r}"
        assert_agrees("{{ p|safeseq|unordered_list }}", value)


class TestTheSpellingIsPythonStrNotTheRenderForm:
    """``str()``, not ``numberformat.format()`` — the reason this needed its own PR.

    A naive ``item.to_string()`` fixes every container row above and breaks
    these: ``Display`` for a ``Float``/``Decimal`` is Django's RENDER form,
    which expands the exponent.
    """

    @pytest.mark.parametrize(
        "item, expected",
        [
            (1e20, "1e+20"),
            (1e-200, "1e-200"),
            (Decimal("1E-9"), "1E-9"),
            (Decimal("19.99"), "19.99"),
            (float("nan"), "nan"),
            (float("inf"), "inf"),
            (10**30, "1000000000000000000000000000000"),
            (2.0, "2.0"),
            (True, "True"),
            (None, "None"),
        ],
    )
    def test_join_emits_the_python_str_of_each_item(self, item, expected) -> None:
        # `join` is the shortest way to see ONE item's text with no repr
        # quoting around it.
        assert djust_render('{{ p|safeseq|join:"" }}', [item]) == expected
        assert_agrees('{{ p|safeseq|join:"" }}', [item])

    def test_the_bare_render_of_that_float_is_still_the_expanded_form(self) -> None:
        # The other half of the split, and the reason `Display` could not be
        # reused: `{{ f }}` must keep expanding. If this ever equals `1e+20`,
        # `py_str` has been wired into the render path by mistake.
        assert djust_render("{{ p }}", 1e20) == "100000000000000000000"
        assert_agrees("{{ p }}", 1e20)
        assert djust_render("{{ p }}", Decimal("1E-9")) == "0.000000001"
        assert_agrees("{{ p }}", Decimal("1E-9"))


class TestNotMorePermissiveThanDjango:
    """djust must emit no live fragment of the payload that Django does not."""

    PAYLOAD = "<img src=x onerror=alert(1)>"
    FRAGMENTS = ("<img", "onerror=")

    @pytest.mark.parametrize(
        "src",
        [
            "{{ p|safeseq }}",
            '{{ p|safeseq|join:", " }}',
            "{{ p|safeseq|unordered_list }}",
            "{{ p|safeseq|first }}",
            '{{ p|safeseq|slice:":2" }}',
            "{{ p|safeseq|escapeseq|join:'-' }}",
            "{{ p|safeseq|pprint }}",
            "{{ p|safeseq|escape }}",
            "{{ p|safeseq|striptags }}",
            "{{ p|safeseq|force_escape }}",
        ],
    )
    @pytest.mark.parametrize(
        "value",
        [
            [PAYLOAD],
            [PAYLOAD, "x"],
            [["a", PAYLOAD]],
            [(PAYLOAD,)],
            ({"k": PAYLOAD},),
            [mark_safe("<b>ok</b>"), PAYLOAD],
            [42, PAYLOAD],
        ],
    )
    def test_no_live_fragment_django_does_not_also_emit(self, src, value) -> None:
        d, r = django_render(src, value), djust_render(src, value)
        for frag in self.FRAGMENTS:
            if frag in r:
                assert frag in d, (
                    f"{src} on {value!r} emits {frag!r} live where Django does "
                    f"not: django={d!r} djust={r!r}"
                )


# ---------------------------------------------------------------------------
# The randomised half. A curated table samples one axis and goes blind on the
# next (v1.1.1-2 retro); Django is one call away, so ask it.
# ---------------------------------------------------------------------------

LEAVES = [
    "a",
    "<b>x</b>",
    "",
    "héllo",
    "a < b",
    42,
    -7,
    0,
    True,
    False,
    None,
    1.5,
    1e20,
    1e-200,
    1e300,
    float("nan"),
    float("inf"),
    Decimal("1E-9"),
    Decimal("19.99"),
    10**30,
    mark_safe("<i>m</i>"),
    "<img src=x onerror=alert(1)>",
    2.0,
]

CHAINS = [
    "{{ p|safeseq }}",
    "{{ p|safeseq|join:', ' }}",
    "{{ p|safeseq|unordered_list }}",
    "{{ p|safeseq|first }}",
    "{{ p|safeseq|last }}",
    '{{ p|safeseq|slice:":2" }}',
    "{{ p|safeseq|length }}",
    "{{ p|safeseq|pprint }}",
    "{{ p|safeseq|make_list }}",
    "{{ p|safeseq|escapeseq|join:'-' }}",
    "{{ p|safeseq|safeseq|join:'-' }}",
]


def _random_shape(rng: random.Random, depth: int = 0):
    """A nesting whose sublists are randomly lists, tuples or dicts."""
    out = []
    for _ in range(rng.randint(0, 4)):
        roll = rng.random()
        if depth < 3 and roll < 0.30:
            sub = _random_shape(rng, depth + 1)
            out.append(tuple(sub) if rng.random() < 0.5 else sub)
        elif roll < 0.36:
            out.append({"k": rng.choice(LEAVES)})
        else:
            out.append(rng.choice(LEAVES))
    return out


class TestRandomisedSweep:
    def test_every_chain_agrees_over_a_thousand_shapes(self) -> None:
        rng = random.Random(2324)  # fixed: the suite must be deterministic
        values = [_random_shape(rng) for _ in range(700)]
        values += [tuple(_random_shape(rng)) for _ in range(300)]
        # Every leaf on its own, in both containers, so no item type is
        # reachable only by chance.
        for leaf in LEAVES:
            values.append([leaf])
            values.append((leaf,))

        def has_container_item(v) -> bool:
            return any(isinstance(x, (list, tuple, dict)) for x in v)

        nested = sum(1 for v in values if has_container_item(v))
        assert nested > 300, (
            f"only {nested}/{len(values)} shapes carry a container ITEM — the "
            "generator stopped producing the case this file is about"
        )

        bad = []
        for v in values:
            for src in CHAINS:
                d, r = django_render(src, v), djust_render(src, v)
                if d != r:
                    bad.append((src, v, d, r))
        assert not bad, f"{len(bad)} disagreeing cells, first 5: {bad[:5]}"


class TestOneStringifyMechanism:
    """One ``str()`` spelling, not two (#1646).

    #2303 solved the same problem for the SCALAR case by listing ``safe`` in
    ``STRING_FILTERS`` — the ``@stringfilter`` coercion — rather than growing a
    second stringify inside the ``"safe"`` arm. This fix follows it: the
    coercion and ``safeseq`` both call ``Value::py_str``, which is the only
    place the ``Float``/``Decimal`` split is written down.
    """

    def test_py_str_is_defined_exactly_once_and_lives_in_djust_core(self) -> None:
        defs = [
            p
            for p in CRATES.rglob("*.rs")
            if re.search(r"\bfn py_str\b", p.read_text(encoding="utf-8"))
        ]
        assert [p.name for p in defs] == ["lib.rs"], (
            f"`py_str` is defined in {[str(p) for p in defs]} — it must have "
            "exactly one definition, beside `py_repr` in djust_core"
        )
        src = CORE_RS.read_text(encoding="utf-8")
        assert "pub fn py_repr(" in src and "pub fn py_str(" in src

    def test_the_stringfilter_coercion_does_not_respell_the_split(self) -> None:
        """The pre-fix coercion spelled ``str()`` of a Float/Decimal inline.

        A second copy of that rule — here or in ``safeseq`` — is the #1646
        shape this fix exists to avoid, so the coercion prologue must reach the
        spelling only through ``py_str``. Scoped to the prologue rather than the
        whole file: ``floatformat``'s expansion and ``json_script``'s
        ``json_float_body`` legitimately call ``python_float_repr`` for
        different jobs.
        """
        src = FILTERS_RS.read_text(encoding="utf-8")
        head, _, rest = src.partition("    let coerced: Value;")
        assert rest, "the `@stringfilter` coercion prologue moved — update this pin"
        prologue, _, _ = rest.partition("let result: Result<Value> = match filter_name")
        assert "python_float_repr" not in prologue, (
            "the coercion spells CPython's float repr directly again — it and "
            "`safeseq` must both go through `Value::py_str`"
        )
        assert prologue.count("py_str()") == 1, (
            "expected exactly one `py_str` call in the coercion prologue, found "
            f"{prologue.count('py_str()')}"
        )
        code = "\n".join(
            line
            for line in src.split("#[cfg(test)]")[0].splitlines()
            if not line.lstrip().startswith("//")
        )
        assert code.count(".py_str()") == 2, (
            "expected exactly two `py_str` call sites in filters.rs (the "
            f"`@stringfilter` coercion and `safeseq`), found {code.count('.py_str()')}"
        )

    def test_safeseq_maps_its_items_through_py_str(self) -> None:
        src = FILTERS_RS.read_text(encoding="utf-8")
        # `python_iter` since #2451 — the same sink, wrapped so its `None` can
        # name the exception Python raises there.
        arm = src.split('"safeseq" => match python_iter(value)', 1)
        assert len(arm) == 2, "the `safeseq` arm moved — update this pin"
        body = arm[1].split('"escapeseq"', 1)[0]
        assert "py_str()" in body, (
            "the `safeseq` arm no longer stringifies its items through "
            "`py_str` — #2324 would be back"
        )
