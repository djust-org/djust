"""#2287 — a list whose ELEMENTS a view ``mark_safe``d must reach ``join`` and
``unordered_list`` live, the way Django's per-element ``conditional_escape``
delivers them.

Django's ``join`` and ``unordered_list`` are the two ``needs_autoescape=True``
built-ins whose body escapes PER ELEMENT rather than escaping the whole value::

    def join(value, arg, autoescape=True):
        if autoescape:
            data = conditional_escape(arg).join(conditional_escape(v) for v in value)

``conditional_escape`` is a no-op on ``SafeData``, so a view returning
``{"p": [mark_safe("<b>x</b>"), …]}`` gets live items even though the LIST is
not ``SafeData`` — ``mark_safe`` was never called on it.

What was actually missing
-------------------------
The issue says this "needs safety tracked *inside* the container" as though the
mechanism did not exist. By the time it was picked up it did: #2283 shipped
``InputSafety{container, items}`` (``crates/djust_templates/src/filters.rs``)
plus ``ITEM_SAFE_OUTPUT_FILTERS`` / ``ITEM_SAFETY_PRESERVING_FILTERS``
(``crates/djust_templates/src/renderer.rs``), and ``join`` / ``unordered_list``
already read ``input_safety.items``.

The gap was one seed. All three renderer sites opened
``let mut items_safe = false``, so the ONLY producer of item safety was a
``safeseq`` / ``escapeseq`` earlier in the same chain. Safety arriving from the
CONTEXT had no route in — even though ``_collect_safe_keys``
(``python/djust/mixins/rust_bridge.py``) already walks containers and had been
putting ``p.0`` / ``p.1`` into ``safe_keys`` the whole time. The channel
existed; nothing read it at this granularity. The fix is
``Context::items_are_safe`` plus the three seeds.

Measured through the real ``RustLiveView`` + ``mark_safe_keys`` channel
(``render_template`` has no context-safety channel and cannot see this surface
at all), Python 3.12.9 / Django 5.2, 22 cells × {join, unordered_list} × 11
value shapes::

    before  12 differ from Django
    after    6 differ from Django

The six that remain are all still djust escaping where Django does not:

* four MIXED cells — a list where only SOME items are ``mark_safe``d. Django
  answers per element; one bool cannot, so a partially-marked list is escaped
  whole. ``TestPartiallyMarkedListsAreEscapedWhole`` pins that.
* two NESTED cells — ``[mark_safe("a"), [mark_safe("b")]]``. Refused
  deliberately, and ``test_a_nested_container_is_refused_because_granting_it_
  would_out_permit_django`` shows the grant would be an UNDER-escape, which is
  why the narrowing is a security property and not a limitation. The nested
  ``unordered_list`` cell ALSO carried a separate, pre-existing indentation
  divergence, which is why the nested assertions are
  ``assert_no_more_permissive_than_django`` and not ``assert_agrees``. #2306
  fixed that indentation half (#2301); the refusal keeps these two cells off
  byte equality on its own, so what that case gained is an ``assert_agrees``
  on the same nesting with nothing marked safe.

Two further gaps this measurement surfaced are filed rather than fixed here
(#1079): ``first`` / ``last`` / ``random`` extract an ITEM and so need the
grant to become the RESULT's container safety, a different mechanism (#2299);
and ``mark_safe_keys`` accumulates without ever clearing, so a stale grant
survives into a later render (#2300, pre-existing and an UNDER-escape —
``test_a_stale_grant_cannot_reach_a_non_string_item`` pins the narrowing that
keeps this fix from widening it).

Direction
---------
This is an over-escaping fix, so the load-bearing assertion is not "does it
agree" but "did anything become more permissive than Django". The registry-wide
sweep in :class:`TestNothingIsMorePermissiveThanDjangoThroughTheContextChannel`
is that half, and it is the first sweep in the suite to run through the
context-safety channel — ``test_escape_chain_and_sequence_filters_2281_2283``'s
sweep uses ``render_template``, which cannot mark anything safe, so it is blind
to every cell this fix touches.
"""

from __future__ import annotations

import pytest

pytest.importorskip("django")

from django.template import Context as DjangoContext  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402
from django.template.defaultfilters import register  # noqa: E402
from django.utils.safestring import SafeData, mark_safe  # noqa: E402

from djust import _rust  # noqa: E402
from djust.mixins.rust_bridge import _collect_safe_keys  # noqa: E402
from djust.serialization import normalize_django_value  # noqa: E402

from test_safe_survives_is_safe_filter_2274 import capabilities  # noqa: E402

#: Hostile payloads, one per sink, so a fix that only handles ``<`` does not
#: pass the whole set.
HOSTILE = [
    "<img src=x onerror=alert(1)>",
    "</script><script>alert(1)</script>",
    '"><svg onload=alert(1)>',
]

#: The two filters #2287 names — the ``needs_autoescape`` built-ins whose body
#: is the per-element ``conditional_escape`` form.
PER_ELEMENT = {"join": '{{ p|join:", " }}', "unordered_list": "{{ p|unordered_list }}"}

#: The capabilities the HOSTILE payloads themselves carry.
#:
#: ``capabilities`` reports every live tag in the output, including markup the
#: FILTER generated: ``unordered_list`` always emits ``<li>``, so a bare
#: ``capabilities(out) == set()`` can never pass for it and would make every
#: assertion below vacuous in the other direction. The question this fix has to
#: answer is narrower and is the one the two-build differential asks —
#: *does djust emit a live fragment of the hostile INPUT that Django's output
#: does not* — so every security assertion is scoped to these.
PAYLOAD_CAPS = frozenset().union(*(capabilities(p) for p in HOSTILE))


def payload_capabilities(out: str) -> set[str]:
    """Live capabilities in *out* that could only have come from a payload."""
    return capabilities(out) & PAYLOAD_CAPS


def test_the_payload_capability_probe_is_not_vacuous() -> None:
    """The probe every security assertion in this file runs through.

    Pins both directions: a payload's own capabilities are IN scope, and the
    ``<li>`` ``unordered_list`` generates for itself is OUT of scope. If this
    were empty or universal the whole file would be worthless (#2259, #1859).
    """
    assert PAYLOAD_CAPS == {"tag:img", "evt:onerror", "tag:script", "tag:svg", "evt:onload"}
    assert payload_capabilities("<img src=x onerror=alert(1)>") == {"tag:img", "evt:onerror"}
    assert payload_capabilities("&lt;img src=x onerror=alert(1)&gt;") == set()
    assert payload_capabilities("\t<li>&lt;b&gt;x&lt;/b&gt;</li>") == set()
    assert payload_capabilities("\t<li><img src=x onerror=alert(1)></li>") == {
        "tag:img",
        "evt:onerror",
    }


def django_render(source: str, value):
    return DjangoTemplate(source).render(DjangoContext({"p": value}))


def djust_render(source: str, value) -> str:
    """Render through the REAL context-safety channel.

    ``_rust.render_template`` has no ``safe_keys`` parameter, so a test written
    against it cannot express "the view marked these items safe" and would be
    green on both sides of this fix. This mirrors what
    ``rust_bridge._sync_state_to_rust`` does on every render: normalize, collect
    the dotted paths of every ``SafeString``, hand both to the view.
    """
    view = _rust.RustLiveView(source)
    normalized = normalize_django_value({"p": value})
    safe_keys: list[str] = []
    for key, sub in normalized.items():
        safe_keys.extend(_collect_safe_keys(sub, key))
    view.update_state(normalized)
    if safe_keys:
        view.mark_safe_keys(safe_keys)
    return view.render()


def _render_with_extras(source: str, ctx: dict) -> str:
    """``djust_render`` for templates needing more than the single ``p``.

    Same channel, same ``_collect_safe_keys`` walk — the loop-alias and
    inline-conditional sites need a companion variable and a flag, which the
    single-key helper cannot express.
    """
    view = _rust.RustLiveView(source)
    normalized = normalize_django_value(ctx)
    safe_keys: list[str] = []
    for key, sub in normalized.items():
        safe_keys.extend(_collect_safe_keys(sub, key))
    view.update_state(normalized)
    if safe_keys:
        view.mark_safe_keys(safe_keys)
    return view.render()


def assert_agrees(source: str, value) -> None:
    expected = django_render(source, value)
    got = djust_render(source, value)
    assert got == expected, f"{source} on {value!r}: django={expected!r} djust={got!r}"


def assert_no_more_permissive_than_django(source: str, value) -> None:
    """The direction that matters for an over-escaping fix.

    Django is the bar, not "nothing is live" — Django itself emits the payload
    for a list the view marked safe, and a test asserting djust emits nothing
    live would pass on output that escapes everything and prove nothing
    (#2259's rule).
    """
    expected = django_render(source, value)
    got = djust_render(source, value)
    extra = (capabilities(got) - capabilities(expected)) & PAYLOAD_CAPS
    assert extra == set(), (
        f"{source} on {value!r} grants {sorted(extra)} that Django does not: "
        f"django={expected!r} djust={got!r}"
    )


# ---------------------------------------------------------------------------
# The reported cells
# ---------------------------------------------------------------------------


class TestTheReportedDivergence:
    """The two cells in the issue body, verbatim."""

    @pytest.mark.parametrize("name", sorted(PER_ELEMENT))
    def test_a_list_of_mark_safe_items_comes_through_live(self, name: str) -> None:
        source = PER_ELEMENT[name]
        value = [mark_safe("<b>x</b>"), mark_safe("<i>y</i>")]
        expected = django_render(source, value)
        # Guard the premise rather than assume it: if a Django release stopped
        # honouring per-element mark_safe, byte-equality below would still pass
        # while asserting the opposite of what this test is named for.
        assert "<b>x</b>" in expected, "Django stopped honouring per-element mark_safe"
        assert djust_render(source, value) == expected

    @pytest.mark.parametrize("name", sorted(PER_ELEMENT))
    def test_a_tuple_is_the_same_shape(self, name: str) -> None:
        """The contract is "sequence", not "list" (#1108). A tuple's items get
        the same ``p.0`` / ``p.1`` paths from ``_collect_safe_keys``."""
        assert_agrees(PER_ELEMENT[name], (mark_safe("<b>x</b>"), mark_safe("<i>y</i>")))

    @pytest.mark.parametrize("name", sorted(PER_ELEMENT))
    @pytest.mark.parametrize("payload", HOSTILE)
    def test_an_unmarked_list_is_still_escaped(self, name: str, payload: str) -> None:
        """The other half. Nothing marked the items, so nothing may come
        through live — this is the cell the fix must NOT move."""
        assert_agrees(PER_ELEMENT[name], [payload])
        assert payload_capabilities(djust_render(PER_ELEMENT[name], [payload])) == set()


class TestTheChainCellsTheSeedRepairs:
    """Cells that diverged for the same missing seed and are fixed with it.

    Each one is a different consumer of the item grant: ``escapeseq``'s
    conditional escape, ``slice``'s item-identity preservation, and the
    container safety ``join`` mints for the filters that follow it.
    """

    ITEMS_SAFE = [mark_safe("<b>x</b>"), mark_safe("<i>y</i>")]

    @pytest.mark.parametrize(
        "source",
        [
            # `escapeseq` is `conditional_escape` per item in Django, so
            # already-safe items pass through untouched; djust escaped them.
            '{{ p|escapeseq|join:", " }}',
            # `slice` hands back the SAME item objects, so the grant survives it
            # — ITEM_SAFETY_PRESERVING_FILTERS, which had no context-sourced
            # grant to preserve before this fix.
            '{{ p|slice:":2"|join:", " }}',
            '{{ p|slice:":2"|unordered_list }}',
            # `join` mints CONTAINER safety by escaping every item it emits, so
            # everything downstream of it changes once the items are right.
            '{{ p|join:", "|safe }}',
            '{{ p|join:", "|upper }}',
            '{{ p|join:", "|escape }}',
            "{{ p|unordered_list|safe }}",
            "{{ p|unordered_list|upper }}",
            # Already correct before the fix; pinned so the seed cannot regress
            # the filter-sourced grant that #2283 shipped.
            '{{ p|safeseq|join:", " }}',
            "{{ p|safeseq|unordered_list }}",
            # `|safe` stringifies the list (#2296) and the grant must NOT
            # survive that collapse.
            '{{ p|safe|join:", " }}',
            "{{ p|safe|unordered_list }}",
        ],
    )
    def test_agrees_with_django(self, source: str) -> None:
        assert_agrees(source, self.ITEMS_SAFE)

    @pytest.mark.parametrize(
        "source",
        [
            '{{ p|escapeseq|join:", " }}',
            '{{ p|slice:":2"|join:", " }}',
            '{{ p|join:", "|safe }}',
            '{{ p|join:", "|escape }}',
            "{{ p|unordered_list|safe }}",
            '{{ p|safe|join:", " }}',
        ],
    )
    @pytest.mark.parametrize("payload", HOSTILE)
    def test_the_same_chain_on_unmarked_items_stays_inert(self, source: str, payload: str) -> None:
        assert_agrees(source, [payload])
        assert payload_capabilities(djust_render(source, [payload])) == set()


class TestAllThreeSeedSitesAndTheLoopAlias:
    """The renderer seeds ``items_safe`` at THREE sites and they must agree.

    ``filter_output_is_safe`` was extracted in #2259 precisely because these
    three had drifted while a comment claimed they had not (#1646). A fix that
    seeds one of them and not the others reintroduces exactly that class, and a
    test that only renders ``{{ … }}`` cannot tell — so each site gets the
    template syntax that reaches it, named for the site.

    The templates are not interchangeable: the ``{% firstof %}`` /
    ``{% cycle %}`` emit path goes through ``get_value_safe``'s own pipe loop,
    and an inline conditional's filters are parsed onto ``Node::InlineIf`` and
    applied by its arm — neither reaches the ``Node::Variable`` arm.
    """

    ITEMS_SAFE = [mark_safe("<b>x</b>"), mark_safe("<i>y</i>")]

    @pytest.mark.parametrize(
        "source",
        [
            # Site 1 — `Node::Variable`.
            '{{ p|join:", " }}',
            # Site 2 — `Node::InlineIf`. The filters bind to the whole
            # conditional, and `p` must be reached through BOTH branches.
            "{{ p if flag else q|unordered_list }}",
            "{{ q if nope else p|unordered_list }}",
            # Site 3 — `get_value_safe`, via the `{% firstof %}` / `{% cycle %}`
            # emit path.
            '{% firstof p|join:", " %}',
            '{% cycle p|join:", " p|join:", " %}',
        ],
    )
    def test_every_seed_site_honours_context_item_safety(self, source: str) -> None:
        got = _render_with_extras(
            source, {"p": self.ITEMS_SAFE, "flag": True, "q": ["z"], "nope": False}
        )
        assert "<b>x</b>" in got and "<i>y</i>" in got, (
            f"{source} escaped items the context marked safe: {got!r}"
        )

    @pytest.mark.parametrize(
        "source",
        [
            '{{ p|join:", " }}',
            "{{ p if flag else q|unordered_list }}",
            "{{ q if nope else p|unordered_list }}",
            '{% firstof p|join:", " %}',
            '{% cycle p|join:", " p|join:", " %}',
        ],
    )
    def test_every_seed_site_still_escapes_unmarked_items(self, source: str) -> None:
        got = _render_with_extras(
            source, {"p": [HOSTILE[0]], "flag": True, "q": ["z"], "nope": False}
        )
        assert payload_capabilities(got) == set(), f"{source} leaked: {got!r}"

    def test_a_loop_variable_resolves_to_its_iterables_item_paths(self) -> None:
        """``{% for row in rows %}{{ row|join }}{% endfor %}``.

        ``_collect_safe_keys`` records the marked strings at ``rows.0.0`` /
        ``rows.0.1``, but inside the loop the variable is named ``row``.
        ``Context::is_safe`` already resolves that alias through
        ``loop_mappings`` and ``items_are_safe`` has to do the same, or the
        grant is invisible to every list rendered inside a ``{% for %}`` — which
        is the shape a real template uses.
        """
        got = _render_with_extras(
            '{% for row in rows %}{{ row|join:"," }}{% endfor %}',
            {"rows": [self.ITEMS_SAFE]},
        )
        assert got == "<b>x</b>,<i>y</i>", got

    def test_the_loop_alias_does_not_grant_safety_to_an_unmarked_row(self) -> None:
        got = _render_with_extras(
            '{% for row in rows %}{{ row|join:"," }}{% endfor %}',
            {"rows": [self.ITEMS_SAFE, [HOSTILE[0]]]},
        )
        assert "<b>x</b>" in got, "the marked row lost its grant"
        assert payload_capabilities(got) == set(), f"the unmarked row leaked: {got!r}"


# ---------------------------------------------------------------------------
# The narrowings — each is the escaping direction, three are security
# ---------------------------------------------------------------------------


class TestPartiallyMarkedListsAreEscapedWhole:
    """Django answers per element; ``InputSafety.items`` is one bool.

    Where they disagree the bool must resolve to "escape", so a list with any
    unmarked item is escaped entirely. That is a residual divergence from
    Django and it is the SAFE one — asserted here as a measured fact so a
    future widening of the grant to "any item marked" fails loudly.
    """

    @pytest.mark.parametrize("name", sorted(PER_ELEMENT))
    @pytest.mark.parametrize("marked_index", [0, 1])
    def test_a_mixed_list_is_not_more_permissive_than_django(
        self, name: str, marked_index: int
    ) -> None:
        items = ["<b>x</b>", "<i>y</i>"]
        items[marked_index] = mark_safe(items[marked_index])
        assert_no_more_permissive_than_django(PER_ELEMENT[name], items)

    @pytest.mark.parametrize("name", sorted(PER_ELEMENT))
    def test_a_mixed_list_with_a_hostile_unmarked_item_emits_nothing_live(self, name: str) -> None:
        """The realistic shape of the risk: a template renders a list of
        sanitized fragments and one attacker-controlled string slips in
        unmarked. The unmarked one must not ride the marked ones' grant."""
        value = [mark_safe("<b>ok</b>"), "<img src=x onerror=alert(1)>"]
        assert payload_capabilities(djust_render(PER_ELEMENT[name], value)) == set()


class TestTheGrantIsRefusedForShapesItCannotDescribe:
    @pytest.mark.parametrize("name", sorted(PER_ELEMENT))
    def test_a_dict_whose_values_are_safe_grants_nothing_to_its_keys(self, name: str) -> None:
        """``_collect_safe_keys`` records a dict's paths by NAME (``p.<k>``)
        while the filters iterate its KEYS, so an index-keyed check can never
        confuse the two. Asserted with a hostile KEY, which is the cell that
        would be live if the check keyed by position."""
        value = {"<img src=x onerror=alert(1)>": mark_safe("<b>v</b>")}
        assert_agrees(PER_ELEMENT[name], value)
        assert payload_capabilities(djust_render(PER_ELEMENT[name], value)) == set()

    @pytest.mark.parametrize("name", sorted(PER_ELEMENT))
    def test_a_marked_safe_string_grants_nothing_to_its_characters(self, name: str) -> None:
        """A string IS a sequence to these filters (#2283 — they iterate its
        characters), and ``mark_safe`` on the string marks the CONTAINER. The
        item grant must not follow, or ``{{ p|join }}`` on a safe string would
        take a path Django does not."""
        assert_agrees(PER_ELEMENT[name], mark_safe("<b>x</b>"))

    @pytest.mark.parametrize("name", sorted(PER_ELEMENT))
    def test_an_empty_list_is_unobservable_either_way(self, name: str) -> None:
        assert_agrees(PER_ELEMENT[name], [])

    def test_a_nested_container_is_refused_because_granting_it_would_out_permit_django(
        self,
    ) -> None:
        """The narrowing that is a security property, shown rather than
        asserted by comment.

        ``join`` stringifies a sublist and Django escapes that ``repr``, so a
        recursive "all leaves are safe" grant would emit raw ``<`` where Django
        emits ``&lt;``. Django's own output is the bar: it contains no live
        markup for the sublist, so neither may djust's.

        Byte equality is still NOT asserted for the marked value: the refusal
        itself is an over-escape, so djust and Django legitimately differ here.
        When this case was written there was a SECOND, unrelated reason — a
        nested sublist was indented one level deeper than Django's (#2301,
        which reproduced with nothing marked safe at all). That one is fixed
        (#2306), so the two reasons can now be told apart, which is what the
        last assertion below does: it pins the SAME nesting, with the same
        markup, unmarked, to Django byte-for-byte. Anything still differing
        above is therefore the refusal and nothing else.

        ``test_nested_unmarked_list_matches_django`` pins the plain ``a``/``b``
        shape for the same reason; this one is deliberately the markup-carrying
        cell, so the two are not the same assertion twice.
        """
        value = [mark_safe("<b>a</b>"), [mark_safe("<i>b</i>")]]
        expected = django_render('{{ p|join:", " }}', value)
        assert "<i>b</i>" not in expected, (
            "Django now emits the sublist live — the nested narrowing needs "
            "revisiting, not just this assertion"
        )
        assert_no_more_permissive_than_django('{{ p|join:", " }}', value)
        assert_no_more_permissive_than_django("{{ p|unordered_list }}", value)
        # #2301 (the indentation half) is fixed, so the SAME nesting with
        # nothing marked safe is now byte-identical to Django. What still
        # differs above is only the refusal — an over-escape, deliberate — and
        # asserting the unmarked shape here is what separates the two reasons.
        assert_agrees("{{ p|unordered_list }}", ["<b>a</b>", ["<i>b</i>"]])

    def test_nested_unmarked_list_matches_django(self) -> None:
        """The nested list wrapper stays at the parent indent (#2301)."""
        assert_agrees("{{ p|unordered_list }}", ["a", ["b"]])

    @pytest.mark.parametrize("name", sorted(PER_ELEMENT))
    def test_a_stale_grant_cannot_reach_a_non_string_item(self, name: str) -> None:
        """``mark_safe_keys`` only ever EXTENDS its set — there is no clear — so
        a path marked in one render stays marked in the next.

        That staleness is pre-existing at the CONTAINER granularity (a scalar
        ``mark_safe``d once emits every later value raw — #2300) and this fix
        rides the same set rather than making a second one. The element-is-a-``String``
        narrowing is what keeps a stale ``p.0`` from granting safety to a shape
        that was never a ``SafeString``.
        """
        source = PER_ELEMENT[name]
        view = _rust.RustLiveView(source)
        view.update_state(normalize_django_value({"p": [mark_safe("<b>x</b>")]}))
        view.mark_safe_keys(["p.0"])
        assert "<b>x</b>" in view.render()

        # Same index, a shape `_collect_safe_keys` would never have marked.
        view.update_state(normalize_django_value({"p": [{"k": "<script>x</script>"}]}))
        assert payload_capabilities(view.render()) == set(), view.render()


# ---------------------------------------------------------------------------
# The load-bearing half: the permissiveness sweep, through the CONTEXT channel
# ---------------------------------------------------------------------------


class TestNothingIsMorePermissiveThanDjangoThroughTheContextChannel:
    """The registry-wide sweep this fix's surface actually needs.

    ``test_escape_chain_and_sequence_filters_2281_2283``'s sweep — the one that
    found #2281 and #2291 — renders with ``_rust.render_template``, which has no
    ``safe_keys`` parameter. It therefore cannot construct a single cell in
    which the CONTEXT marked anything safe, and is blind to every cell this fix
    touches. This is the same sweep against the same bar, through the channel
    that can express the input.
    """

    #: Filters whose bare name is not a valid call.
    ARGS = {
        "add": ":'1'",
        "center": ":'20'",
        "cut": ":'b'",
        "date": ":'Y'",
        "default": ":'D'",
        "default_if_none": ":'D'",
        "dictsort": ":'k'",
        "dictsortreversed": ":'k'",
        "divisibleby": ":'2'",
        "floatformat": ":'2'",
        "get_digit": ":'1'",
        "join": ":', '",
        "ljust": ":'20'",
        "pluralize": ":'s'",
        "rjust": ":'20'",
        "slice": ":':3'",
        "stringformat": ":'s'",
        "time": ":'H'",
        "truncatechars": ":'5'",
        "truncatechars_html": ":'5'",
        "truncatewords": ":'2'",
        "truncatewords_html": ":'2'",
        "urlizetrunc": ":'15'",
        "wordwrap": ":'5'",
        "yesno": ":'y,n,m'",
    }

    #: Nondeterministic — a disagreement says nothing about escaping.
    NONDET = {"random"}

    def _sweep(self, values) -> tuple[list, int]:
        leaks, compared = [], 0
        for name in sorted(register.filters):
            if name in self.NONDET:
                continue
            spec = name + self.ARGS.get(name, "")
            for source in (
                "{{ p|%s }}" % spec,
                '{{ p|%s|join:", " }}' % spec,
                '{{ p|join:", "|%s }}' % spec,
                "{{ p|%s|unordered_list }}" % spec,
                "{{ p|unordered_list|%s }}" % spec,
            ):
                for value in values:
                    try:
                        expected = django_render(source, value)
                    except Exception:  # noqa: BLE001 — Django raises: no bar
                        continue
                    try:
                        got = djust_render(source, value)
                    except Exception:  # noqa: BLE001 — a raise grants nothing
                        continue
                    compared += 1
                    extra = (capabilities(got) - capabilities(expected)) & PAYLOAD_CAPS
                    if extra:
                        leaks.append((source, sorted(extra), got))
        return leaks, compared

    def test_marked_items_grant_no_capability_django_withholds(self) -> None:
        """Every item ``mark_safe``d — the input shape the fix newly honours,
        and therefore the one where a widening would leak."""
        values = [[mark_safe(p) for p in HOSTILE], [mark_safe(HOSTILE[0])]]
        leaks, compared = self._sweep(values)
        assert compared > 300, f"the sweep compared only {compared} cells"
        assert leaks == [], f"{len(leaks)} cells more permissive than Django: {leaks[:3]}"

    def test_unmarked_and_partially_marked_items_stay_inert(self) -> None:
        """Nothing (or only some things) marked. Django escapes the unmarked
        ones, so djust emitting them live is a leak — this is the half that
        would catch a grant keyed on "any item marked" or on a stale path."""
        values = [
            list(HOSTILE),
            [mark_safe("<b>ok</b>"), HOSTILE[0]],
            [HOSTILE[0], mark_safe("<b>ok</b>")],
            {HOSTILE[0]: mark_safe("<b>ok</b>")},
        ]
        leaks, compared = self._sweep(values)
        assert compared > 300, f"the sweep compared only {compared} cells"
        assert leaks == [], f"{len(leaks)} cells more permissive than Django: {leaks[:3]}"


# ---------------------------------------------------------------------------
# The #2290 interaction — a custom filter now sees context-sourced item safety
# ---------------------------------------------------------------------------


class TestACustomFilterSeesContextSourcedItemSafety:
    """#2290 (PR #2302) wraps items as ``SafeString`` before a custom filter,
    gated on ``input_safety.items``; this issue is what lets ``items`` be
    seeded from the CONTEXT.

    So a project ``@register.filter`` can now receive ``SafeString`` items that
    came from ``mark_safe_keys`` rather than from ``safeseq`` — a path NEITHER
    change exercised alone, because #2290 landed while the only producer of an
    item grant was a ``safeseq``/``escapeseq`` earlier in the same chain.
    """

    @staticmethod
    def _register_probe():
        """A live filter on Django's engine and djust's registry at once.

        Reports the container SHAPE and per-item ``SafeData`` so a divergence
        says WHICH half moved — a probe returning only the rendered text cannot
        distinguish "the items were not marked" from "the filter escaped them".
        """
        from django import template as dt
        from django.template import Engine

        lib = dt.Library()

        @lib.filter(name="_ctx_item_probe")
        def _probe(value):
            if isinstance(value, (list, tuple)):
                inner = ",".join(str(isinstance(v, SafeData)) for v in value)
                return f"{type(value).__name__}[{inner}]"
            return f"{type(value).__name__}:{isinstance(value, SafeData)}"

        builtins = Engine.get_default().template_builtins
        if lib not in builtins:
            builtins.append(lib)
        for name, fn in lib.filters.items():
            _rust.register_custom_filter(name, fn, getattr(fn, "is_safe", False))

    def test_marked_items_arrive_as_SafeData(self) -> None:
        """The cell the two changes create together."""
        self._register_probe()
        source = "{{ p|_ctx_item_probe }}"
        value = [mark_safe("<b>x</b>"), mark_safe("<i>y</i>")]
        expected = django_render(source, value)
        assert expected == "list[True,True]", (
            f"Django stopped marking the items — the premise moved: {expected!r}"
        )
        assert djust_render(source, value) == expected

    def test_unmarked_items_arrive_plain(self) -> None:
        """The other direction, or the assertion above passes on a build that
        marks everything unconditionally."""
        self._register_probe()
        assert_agrees("{{ p|_ctx_item_probe }}", ["<b>x</b>", "<i>y</i>"])

    def test_a_partially_marked_list_arrives_wholly_plain(self) -> None:
        """The narrowing, seen from the custom-filter side: djust withholds the
        grant Django gives element 0. Over-escaping, and the residual this
        issue's one-bool model implies."""
        self._register_probe()
        source = "{{ p|_ctx_item_probe }}"
        value = [mark_safe("<b>x</b>"), "<i>y</i>"]
        assert django_render(source, value) == "list[True,False]"
        assert djust_render(source, value) == "list[False,False]"

    def test_a_raw_tuple_reaches_a_custom_filter_marked(self) -> None:
        """The pin this class shipped with, flipped to parity by #2305.

        It was written as a residual — ``tuple[False,False]`` — with a failure
        message saying that if the answer ever became ``tuple[True,True]`` then
        #2305 was fixed and the pin should assert parity instead. It did, and
        this is that assertion.

        The history is the point. #2290 deleted a ``PyTuple`` arm from
        ``mark_input_safety`` as unreachable, and was RIGHT on the evidence it
        had: the only producer of an item grant was ``safeseq``, a list
        comprehension, so a tuple had already become a list before the grant
        existed. #2287 added ``Context::items_are_safe``, a second producer
        that accepts ``Value::Tuple``, and the claim expired the moment the two
        changes met.
        """
        self._register_probe()
        source = "{{ p|_ctx_item_probe }}"
        raw = ("<b>x</b>", "<i>y</i>")

        expected = django_render(source, tuple(mark_safe(v) for v in raw))
        assert expected == "tuple[True,True]", (
            f"Django stopped marking the tuple's items — premise moved: {expected!r}"
        )
        assert _rust.render_template_with_dirs(source, {"p": raw}, [], ["p.0", "p.1"]) == expected

    def test_the_second_entry_point_the_issue_did_not_name(self) -> None:
        """``RustLiveView`` + ``mark_safe_keys`` reaches the arm too.

        #2305 names one caller — a 4-argument ``render_template_with_dirs``.
        There is a second, equally public in ``_rust.pyi``: build a view,
        ``update_state`` an un-normalized tuple, ``mark_safe_keys`` its item
        paths. It is the same channel ``rust_bridge._sync_state_to_rust`` uses,
        minus the ``normalize_django_value`` call that is the only reason the
        FRAMEWORK paths never reach here.
        """
        self._register_probe()
        source = "{{ p|_ctx_item_probe }}"
        raw = ("<b>x</b>", "<i>y</i>")

        view = _rust.RustLiveView(source)
        view.update_state({"p": raw})
        view.mark_safe_keys(["p.0", "p.1"])
        assert view.render() == django_render(source, tuple(mark_safe(v) for v in raw))

    def test_a_tuple_keeps_its_type_across_the_boundary(self) -> None:
        """The wrap must rebuild a ``tuple``, not a ``list``.

        Django marks the ELEMENTS and leaves the sequence object alone, so a
        filter branching on ``isinstance(value, tuple)`` keeps its answer. A
        ``PyTuple`` arm that appended into a ``PyList`` would fix the safety
        half and silently break the shape half — which the probe reports,
        because it prints ``type(value).__name__`` alongside the flags.
        """
        self._register_probe()
        got = _rust.render_template_with_dirs(
            "{{ p|_ctx_item_probe }}", {"p": ("<b>x</b>", "<i>y</i>")}, [], ["p.0", "p.1"]
        )
        assert got.startswith("tuple["), got

    def test_an_unmarked_tuple_still_arrives_plain(self) -> None:
        """The direction that keeps the assertion above from passing on a build
        that marks every sequence unconditionally."""
        self._register_probe()
        source = "{{ p|_ctx_item_probe }}"
        raw = ("<b>x</b>", "<i>y</i>")
        assert django_render(source, raw) == "tuple[False,False]"
        assert _rust.render_template_with_dirs(source, {"p": raw}, [], []) == "tuple[False,False]"

    def test_a_partially_marked_tuple_arrives_wholly_plain(self) -> None:
        """The same one-bool narrowing the list arm has, on the tuple arm.

        ``Context::items_are_safe`` requires EVERY index, so a tuple with only
        ``p.0`` marked gets no grant at all — djust withholds what Django gives
        element 0. Over-escaping, and it is what stops the restored arm from
        widening anything the list arm does not already.
        """
        self._register_probe()
        source = "{{ p|_ctx_item_probe }}"
        raw = ("<b>x</b>", "<i>y</i>")
        assert django_render(source, (mark_safe(raw[0]), raw[1])) == "tuple[True,False]"
        assert (
            _rust.render_template_with_dirs(source, {"p": raw}, [], ["p.0"]) == "tuple[False,False]"
        )

    def test_safeseq_hands_the_filter_marked_strings_even_for_a_non_string_item(
        self,
    ) -> None:
        """``mark_item``'s ``str``-only policy, and what is left REACHABLE.

        ``mark_safe`` STRINGIFIES a non-``str``, which would change the TYPE a
        filter receives — the reason ``mark_input_safety`` wraps ``str`` only.

        This test used to read ``list[True,False]`` and assert that djust hands
        the ``int`` through unmarked where Django hands over
        ``SafeString('2')``. That was a real divergence wearing a policy's
        clothes, and #2324 closed it at the source: ``safeseq`` now replaces
        every item with the item's ``str()``, exactly as
        ``[mark_safe(o) for o in value]`` does, so by the time
        ``mark_input_safety`` sees the list there is no non-``str`` element
        left. Django's own answer for this cell is ``list[True,True]``, which
        ``test_marked_items_arrive_as_SafeData``'s sibling assertion below
        measures rather than assumes.

        What that leaves: no *known* producer of an item grant can present a
        non-``str`` element — ``safeseq``/``escapeseq`` return all-``String``
        lists, and ``Context::items_are_safe`` refuses a sequence holding an
        ``int`` or a nested container outright (asserted below, because
        "unreachable" is a claim and not an excuse). Whether ``mark_item``'s
        non-``str`` branch therefore has any remaining producer at all — and if
        not, whether it should be deleted rather than tested around — is
        tracked in #2337; keeping the pass-through is the ESCAPING direction,
        so it is the safe side to be wrong on.
        """
        self._register_probe()
        source = "{{ p|safeseq|_ctx_item_probe }}"
        expected = django_render(source, ("a", 2))
        assert expected == "list[True,True]", (
            f"Django stopped marking the stringified item — premise moved: {expected!r}"
        )
        got = _rust.render_template_with_dirs(source, {"p": ("a", 2)}, [])
        assert got == expected, got

        # The two refusals that keep `Context::items_are_safe` off the non-`str`
        # branch: marking every index does NOT grant a sequence holding one.
        for value in (("a", 2), ("a", ["b"])):
            unreached = _rust.render_template_with_dirs(
                "{{ p|_ctx_item_probe }}", {"p": value}, [], ["p.0", "p.1"]
            )
            assert unreached == "tuple[False,False]", (
                f"a tuple holding a non-String element was granted item safety, so "
                f"`mark_item`'s non-`str` branch IS reachable through the context "
                f"grant and this test's reasoning is stale: {value!r} -> {unreached!r}"
            )

    def test_but_the_tuple_still_renders_live_through_join(self) -> None:
        """The same raw tuple through the BUILT-IN path is already correct, so
        the residual above is specific to the PyO3 hand-off and not to
        ``items_are_safe`` refusing tuples."""
        raw = ("<b>x</b>", "<i>y</i>")
        marked = tuple(mark_safe(v) for v in raw)
        for source in ('{{ p|join:", " }}', "{{ p|unordered_list }}"):
            got = _rust.render_template_with_dirs(source, {"p": raw}, [], ["p.0", "p.1"])
            assert got == django_render(source, marked), source


# ---------------------------------------------------------------------------
# Forward pin for #2300
# ---------------------------------------------------------------------------


def test_a_context_item_grant_is_per_render() -> None:
    """The property #2287 and #2300 only deliver together.

    #2287 makes a context-sourced item grant *possible*; #2300 is what makes it
    *per-render*. Until #2300 lands, render 2's unmarked list inherits render
    1's ``p.0``/``p.1`` and comes through live — the same staleness the
    container granularity already has, one level down.

    Was a strict xfail so that closing #2300 would produce a RED test naming
    itself — the landmark shape #2284 used, and why that one got closed rather
    than forgotten. It fired: #2300's fix made it XPASS(strict), the marker is
    gone, and the assertions below stand as an ordinary regression test.

    Note which half of #2300 this needed. Making the bridge call
    ``mark_safe_keys`` unconditionally was not enough — this test drives the
    Rust API directly and never makes that second call, so the grant survived.
    What closes it is ``update_state`` REVOKING the grant for any key whose
    value it replaces, which holds no matter who is driving the API. That this
    test still failed against the caller-discipline half is what surfaced the
    difference.
    """
    view = _rust.RustLiveView('{{ p|join:", " }}')

    view.update_state(normalize_django_value({"p": [mark_safe("<b>x</b>")]}))
    view.mark_safe_keys(["p.0"])
    assert view.render() == "<b>x</b>", "render 1 should honour the marked item"

    # Render 2: nothing is marked, so `_collect_safe_keys` returns [] and the
    # bridge (today) skips the call entirely — there is no way to clear `p.0`.
    view.update_state(normalize_django_value({"p": ["<img src=x onerror=alert(1)>"]}))
    assert payload_capabilities(view.render()) == set(), view.render()


# ---------------------------------------------------------------------------
# #2299 — the THIRD consumer of the item grant: the extractors
# ---------------------------------------------------------------------------

#: The two filters that pull ONE item out of a sequence and hand it back as the
#: whole value, and that are byte-comparable. Django's bodies are ``value[0]``
#: and ``value[-1]`` — each returns the ELEMENT OBJECT, so a ``SafeString``
#: element survives to ``render_value_in_context``'s ``conditional_escape``.
#: ``random`` is the third and gets its own class below, because djust's RNG
#: picks a different element than Django's.
EXTRACTORS = {"first": "{{ p|first }}", "last": "{{ p|last }}"}


class TestTheExtractorsConsumeTheItemGrant:
    """#2299. ``join`` / ``unordered_list`` consume the grant PER ELEMENT
    inside their own body; ``first`` / ``last`` / ``random`` consume it by
    becoming the RESULT's container safety. #2287 seeded the grant and repaired
    the first mechanism only, so these three kept escaping.
    """

    ITEMS_SAFE = [mark_safe("<b>x</b>"), mark_safe("<i>y</i>")]

    @pytest.mark.parametrize("name", sorted(EXTRACTORS))
    def test_the_reported_divergence(self, name: str) -> None:
        """The issue body's two cells, verbatim."""
        source = EXTRACTORS[name]
        expected = django_render(source, self.ITEMS_SAFE)
        assert "<" in expected and "&lt;" not in expected, (
            f"Django stopped returning the element object — premise moved: {expected!r}"
        )
        assert djust_render(source, self.ITEMS_SAFE) == expected

    @pytest.mark.parametrize("name", sorted(EXTRACTORS))
    def test_a_tuple_is_the_same_shape(self, name: str) -> None:
        """The contract is "sequence", not "list" (#1108)."""
        assert_agrees(EXTRACTORS[name], tuple(self.ITEMS_SAFE))

    @pytest.mark.parametrize("name", sorted(EXTRACTORS))
    @pytest.mark.parametrize("payload", HOSTILE)
    def test_an_unmarked_list_is_still_escaped(self, name: str, payload: str) -> None:
        """The cell the fix must NOT move: nothing marked the items, so nothing
        may come through live."""
        value = [payload, payload]
        assert_agrees(EXTRACTORS[name], value)
        assert payload_capabilities(djust_render(EXTRACTORS[name], value)) == set()

    @pytest.mark.parametrize(
        "source",
        [
            # The FILTER-sourced grant. `safeseq` marks every element, so the
            # extracted one is safe in Django whatever the input was — these
            # diverged before this fix even with nothing marked in the context.
            "{{ p|safeseq|first }}",
            "{{ p|safeseq|last }}",
            # `escapeseq` marks the ESCAPED elements, so djust was escaping a
            # second time and emitting `&amp;lt;b&amp;gt;`.
            "{{ p|escapeseq|first }}",
            "{{ p|escapeseq|last }}",
            # `slice` hands back the same objects, so the grant survives it —
            # ITEM_SAFETY_PRESERVING_FILTERS, one hop before the extractor.
            "{{ p|slice:':2'|first }}",
            "{{ p|slice:':2'|last }}",
            # The CONTEXT-sourced grant feeding the extractor, then a filter
            # DOWNSTREAM of it: `first` mints container safety here, and `upper`
            # is `is_safe=True`, so Django keeps it and so must djust.
            "{{ p|first|upper }}",
            "{{ p|last|upper }}",
            # `escape` is eager (#2281): it must see the value as already safe
            # and not escape a second time.
            "{{ p|first|escape }}",
            "{{ p|last|escape }}",
        ],
    )
    def test_the_chain_cells(self, source: str) -> None:
        assert_agrees(source, self.ITEMS_SAFE)

    @pytest.mark.parametrize(
        "source",
        [
            "{{ p|safeseq|first }}",
            "{{ p|escapeseq|last }}",
            "{{ p|slice:':2'|first }}",
            "{{ p|first|escape }}",
            "{{ p|last|upper }}",
        ],
    )
    @pytest.mark.parametrize("payload", HOSTILE)
    def test_the_same_chains_on_unmarked_items(self, source: str, payload: str) -> None:
        """``safeseq`` on hostile input is Django emitting it live too — the
        bar is Django's own output, not "nothing is live" (#2259)."""
        assert_agrees(source, [payload, payload])

    def test_the_grant_does_not_survive_the_extraction(self) -> None:
        """``first`` is neither a producer nor a preserver of ITEM safety, so
        the grant is re-taken after it. Otherwise ``{{ p|first|make_list }}``
        would carry item safety onto characters nothing ever marked."""
        assert_agrees("{{ p|first|make_list|join:'' }}", self.ITEMS_SAFE)
        assert_no_more_permissive_than_django(
            "{{ p|first|make_list|join:'' }}", [mark_safe("<img src=x onerror=alert(1)>")]
        )


class TestTheNarrowingsTheExtractorsInherit:
    """The grant's PRODUCERS do all the narrowing, so the extractor arm needs
    none of its own — and these are the cells that prove it rather than assert
    it.

    Each is a shape ``Context::items_are_safe`` REFUSES, so djust escapes where
    Django does not. Over-escaping, deliberately, and unchanged by #2299.
    """

    @pytest.mark.parametrize("name", sorted(EXTRACTORS))
    def test_a_partially_marked_list_is_escaped_whole(self, name: str) -> None:
        """One bool cannot express Django's per-element answer, so a mixed list
        gets no grant at all."""
        source = EXTRACTORS[name]
        value = [mark_safe("<b>x</b>"), "<i>y</i>"]
        assert "&lt;" in djust_render(source, value)
        assert_no_more_permissive_than_django(source, value)

    @pytest.mark.parametrize("name", sorted(EXTRACTORS))
    def test_a_nested_container_is_refused(self, name: str) -> None:
        """The ``String``-element narrowing, and the one that is a SECURITY
        property rather than a limitation: granting it would emit a sublist's
        raw ``<`` where Django escapes the repr."""
        value = [mark_safe("<b>x</b>"), [mark_safe("<i>y</i>")]]
        assert_no_more_permissive_than_django(EXTRACTORS[name], value)

    @pytest.mark.parametrize("name", sorted(EXTRACTORS))
    def test_a_dict_grants_nothing_to_its_keys(self, name: str) -> None:
        """``iter_values`` yields a dict's KEYS while ``_collect_safe_keys``
        records its VALUES by name, so a by-index check can never confuse the
        two — a hostile KEY must never ride a grant its value earned.

        Django's ``first`` is ``value[0]`` and its ``last`` is ``value[-1]``,
        both a ``KeyError`` on a dict with neither key, so it 500s. djust
        failed soft there until #2451 and now refuses too, which is why this
        reads as a raise on BOTH sides — but the assertion that matters is
        still the absolute one, because a future grant regression would show as
        OUTPUT rather than as a refusal: nothing live, whatever the grant.
        """
        value = {"<img src=x onerror=alert(1)>": mark_safe("z")}
        with pytest.raises(Exception):
            django_render(EXTRACTORS[name], value)
        try:
            out = djust_render(EXTRACTORS[name], value)
        except Exception as exc:  # noqa: BLE001 — a refusal is the strongest form
            assert "KeyError" in str(exc) or "not subscriptable" in str(exc), exc
            return
        assert payload_capabilities(out) == set(), out

    @pytest.mark.parametrize("name", sorted(EXTRACTORS))
    def test_an_empty_sequence_grants_nothing(self, name: str) -> None:
        assert_agrees(EXTRACTORS[name], [])

    @pytest.mark.parametrize("name", sorted(EXTRACTORS))
    def test_a_safe_container_does_not_grant_the_extraction(self, name: str) -> None:
        """``container`` is deliberately NOT a term in the extractor arm.

        Django's ``mark_safe(list)`` is ``SafeString(str(list))`` and indexing a
        ``SafeString`` yields a plain ``str``, so ``{{ p|safe|first }}`` is
        escaped in Django. ``last`` still gets Django's ``is_safe=True`` arm,
        which ``IS_SAFE_FILTERS`` already models — a ``container`` term here
        would be a SECOND, redundant route to ``last``'s answer and a plainly
        permissive one for ``first``.
        """
        source = EXTRACTORS[name].replace("p|", "p|safe|")
        assert_no_more_permissive_than_django(source, ["<img src=x onerror=alert(1)>"])
        assert_no_more_permissive_than_django(source, [mark_safe("<img src=x onerror=alert(1)>")])


class TestRandomIsCoveredByCapabilityNotByBytes:
    """``random`` is the third extractor and the only one that is not
    byte-comparable: djust's RNG picks a different element than Django's, a
    pre-existing and unrelated divergence. The two-build differential is blind
    to it for the same reason — ``NONDET`` collapses every ``random`` cell to a
    marker before the compare — so this class is the coverage it has.

    The assertions are therefore capability-level: what MAY come through live,
    and what may not, on every draw.
    """

    def test_the_rng_really_does_move(self) -> None:
        """First, because the loops below prove nothing without it: if djust's
        ``random`` always returned element 0, twenty draws would be one draw."""
        seen = {djust_render("{{ p|random }}", list("abcdefgh")) for _ in range(200)}
        assert len(seen) > 1, f"random never moved — the loops below prove nothing: {seen}"

    def test_a_marked_item_comes_through_live(self) -> None:
        """Both elements identical, so the pick does not matter and the cell is
        byte-comparable after all — the one shape that makes ``random``
        assertable against Django."""
        value = [mark_safe("<b>x</b>"), mark_safe("<b>x</b>")]
        assert djust_render("{{ p|random }}", value) == django_render("{{ p|random }}", value)

    @pytest.mark.parametrize("payload", HOSTILE)
    def test_an_unmarked_item_never_does(self, payload: str) -> None:
        for _ in range(20):
            out = djust_render("{{ p|random }}", [payload, payload, payload])
            assert payload_capabilities(out) == set(), out

    def test_a_partially_marked_list_never_does(self) -> None:
        """The grant is all-or-nothing, so the hostile UNMARKED element cannot
        ride the marked one's grant on any draw."""
        value = [mark_safe("<b>ok</b>"), "<img src=x onerror=alert(1)>"]
        for _ in range(20):
            out = djust_render("{{ p|random }}", value)
            assert payload_capabilities(out) == set(), out

    def test_a_safeseq_grant_reaches_it(self) -> None:
        """The filter-sourced half, made deterministic the same way."""
        value = ["<b>x</b>", "<b>x</b>"]
        source = "{{ p|safeseq|random }}"
        assert djust_render(source, value) == django_render(source, value) == "<b>x</b>"


class TestEverySeedSiteReachesTheExtractorArm:
    """#1104 — the renderer seeds ``items_safe`` at THREE sites and each decides
    for itself whether to consume the ``produced_safe`` a filter reports.

    ``TestAllThreeSeedSitesAndTheLoopAlias`` pins the SEED for the per-element
    consumers; this pins that the EXTRACTOR arm's grant is honoured at each of
    them, which is the half a ``{{ … }}``-only test cannot see. Two sites are
    Django-comparable and asserted as parity; the inline conditional and
    ``{% cycle %}`` with a filtered argument are djust extensions Django parses
    as a ``TemplateSyntaxError``, so their bar is an unmarked control rather
    than Django's bytes.
    """

    ITEMS_SAFE = [mark_safe("<b>x</b>"), mark_safe("<i>y</i>")]
    EXTRAS = {"q": ["<z>"], "flag": False, "nope": False}

    @pytest.mark.parametrize(
        "source",
        [
            # Site 1 — `Node::Variable`.
            "{{ p|first }}",
            "{{ p|last }}",
            # Site 3 — `get_value_safe`, via the `{% firstof %}` emit path.
            "{% firstof p|first %}",
            "{% firstof p|last %}",
            # The loop alias `is_safe` resolves — `row`'s items live at
            # `rows.<i>.<j>`, a different prefix from the bare key.
            "{% for row in rows %}{{ row|first }}{% endfor %}",
        ],
    )
    def test_the_django_comparable_sites(self, source: str) -> None:
        ctx = {"p": self.ITEMS_SAFE, "rows": [self.ITEMS_SAFE], **self.EXTRAS}
        expected = DjangoTemplate(source).render(DjangoContext(ctx))
        assert _render_with_extras(source, ctx) == expected

    @pytest.mark.parametrize(
        "source",
        [
            # Site 2 — `Node::InlineIf`. The filters bind to the whole
            # conditional and `p` must be reached through BOTH branches.
            "{{ q if nope else p|first }}",
            "{{ q if nope else p|last }}",
            # Two operands: one operand is the `{% cycle name %}` REFERENCE
            # form on Django, which #2556 mirrors.
            "{% cycle p|first p|first %}",
        ],
    )
    def test_the_djust_only_sites(self, source: str) -> None:
        """Django cannot be the bar — it raises on this syntax — so the control
        is the SAME template on an unmarked list, which must stay escaped."""
        marked = _render_with_extras(source, {"p": self.ITEMS_SAFE, "rows": [], **self.EXTRAS})
        assert "<b>x</b>" in marked or "<i>y</i>" in marked, (
            f"{source} escaped items the context marked safe: {marked!r}"
        )
        plain = _render_with_extras(
            source, {"p": ["<b>x</b>", "<i>y</i>"], "rows": [], **self.EXTRAS}
        )
        assert "&lt;" in plain and "<b>" not in plain, (
            f"{source} emitted an UNMARKED item live: {plain!r}"
        )
