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
  ``unordered_list`` cell ALSO carries a separate, pre-existing indentation
  divergence (#2301), which is why the nested assertions are
  ``assert_no_more_permissive_than_django`` and not ``assert_agrees``.

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
from django.utils.safestring import mark_safe  # noqa: E402

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
            '{% cycle p|join:", " %}',
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
            '{% cycle p|join:", " %}',
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

        Byte equality is deliberately NOT asserted for the ``unordered_list``
        half: nested sublists carry a separate, pre-existing indentation
        divergence (#2301) that reproduces with nothing marked safe at all.
        """
        value = [mark_safe("<b>a</b>"), [mark_safe("<i>b</i>")]]
        expected = django_render('{{ p|join:", " }}', value)
        assert "<i>b</i>" not in expected, (
            "Django now emits the sublist live — the nested narrowing needs "
            "revisiting, not just this assertion"
        )
        assert_no_more_permissive_than_django('{{ p|join:", " }}', value)
        assert_no_more_permissive_than_django("{{ p|unordered_list }}", value)

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
