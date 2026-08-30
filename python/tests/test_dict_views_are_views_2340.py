"""``d.items`` / ``d.keys`` / ``d.values`` are VIEWS, not lists (#2340).

#2334 made the three dict methods resolve, to a ``Value::List``. Everything a
template usually does with one was then exact — iteration, unpacking,
``|length``, ``|join``, truthiness, ``{% with %}`` — and two observable
properties of a real view were not:

    {{ p.items }}          django  'dict_items([(&#x27;a&#x27;, 1)])'
                           djust   '[(&#x27;a&#x27;, 1)]'

    {{ p.items|first }}    django  <<EXC TypeError>>   (not subscriptable)
                           djust   "('a', 1)"

#2340 tracked the residue. This closes it with a ``Value::DictView`` variant.

The issue's list of what raises is WRONG, in two directions
------------------------------------------------------------
The issue named ``|first``, ``|last``, ``|slice``, ``|json_script``,
``|random`` and ``|dictsort`` as the not-sequence-like set. Running Django
over ALL of its built-in filters against all three view kinds says otherwise:

* ``|slice`` does **not** raise. Django's ``slice`` catches the ``TypeError``
  and returns the value UNCHANGED — so ``{{ p.keys|slice:':1' }}`` renders the
  whole view repr, and ``{{ p.keys|slice:':1'|join:'' }}`` is every key. A
  model that made ``slice`` return nothing would be a new divergence.
* ``|dictsort`` returns ``''`` rather than raising.
* Five more raise that the issue did not name — ``divisibleby``,
  ``get_digit``, ``phone2numeric``, ``timesince``, ``timeuntil`` — because
  they raise for any non-numeric input, not because of the view.

So the model here is measured, not taken from the issue: ``TestEveryFilter``
below sweeps all of Django's built-ins against all three kinds and asserts
agreement, which is what settled each of those.

A third thing a curated list misses: **most filters see the view's `str()`.**
``|truncatewords``, ``|wordcount``, ``|linebreaks``, ``|stringformat``,
``|striptags``, ``|pprint``, ``|escape``, ``|safe``, ``|yesno`` and
``|make_list`` all operate on ``"dict_keys([…])"`` — so the repr is not a
cosmetic detail, it is the input to a third of the filter registry.

What the model deliberately does NOT do
---------------------------------------
Where Python RAISES, djust renders nothing rather than raising. That is the
shape #2325's differential already classifies and accepts, and it is never
more permissive: no output is not a leak.
"""

from __future__ import annotations

import pytest

pytest.importorskip("django")

from django.template import Context as DjangoContext  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402
from django.template.defaultfilters import register  # noqa: E402

from djust import _rust  # noqa: E402

XSS = "<img src=x onerror=alert(1)>"
KINDS = ("items", "keys", "values")


def django_render(src: str, ctx: dict) -> str:
    return DjangoTemplate(src).render(DjangoContext(ctx))


def djust_render(src: str, ctx: dict) -> str:
    return _rust.render_template(src, ctx)


def both(src: str, ctx: dict) -> tuple[str, str]:
    try:
        d = django_render(src, ctx)
    except Exception as exc:  # noqa: BLE001
        d = f"<<EXC {type(exc).__name__}>>"
    try:
        r = djust_render(src, ctx)
    except Exception as exc:  # noqa: BLE001
        r = f"<<EXC {type(exc).__name__}>>"
    return d, r


def assert_agrees(src: str, ctx: dict) -> None:
    d, r = both(src, ctx)
    assert r == d, f"{src} on {ctx!r}: django={d!r} djust={r!r}"


# ===========================================================================
# The issue's table, verbatim.
# ===========================================================================


class TestTheIssueTable:
    def test_the_container_str_is_the_views_own(self) -> None:
        assert_agrees("{{ p.items }}", {"p": {"a": 1}})
        assert djust_render("{{ p.items }}", {"p": {"a": 1}}) == (
            "dict_items([(&#x27;a&#x27;, 1)])"
        )

    def test_all_three_kinds_name_themselves(self) -> None:
        p = {"a": 1, "b": 2}
        for kind in KINDS:
            assert_agrees("{{ p.%s }}" % kind, {"p": p})
            assert djust_render("{{ p.%s }}" % kind, {"p": p}).startswith(f"dict_{kind}([")

    def test_a_view_is_not_subscriptable(self) -> None:
        """Both engines refuse since #2449.

        This asserted ``r == ""`` — djust rendering NOTHING where Python raises,
        which #2340 recorded as "never more permissive" and accepted. #2449
        closed the same ``TypeError`` for every scalar shape, and a view is the
        same refusal one variant over, so it refuses here too. The exception
        TYPES still differ (``TypeError`` vs the ``RuntimeError`` every djust
        render error becomes at the PyO3 boundary); the comparable property is
        the bit — does this template render or fail.
        """
        for src in ("{{ p.items|first }}", "{{ p.items|last }}"):
            d, r = both(src, {"p": {"a": 1}})
            assert d == "<<EXC TypeError>>", d
            assert r == "<<EXC RuntimeError>>", f"djust must refuse too, got {r!r}"

    def test_slice_returns_the_view_unchanged_rather_than_raising(self) -> None:
        """The issue said ``slice`` was in the not-sequence-like set.

        Django's ``slice`` catches the ``TypeError`` and returns the value, so
        the whole view repr comes through — and a later ``|join`` still sees
        every element. Measured, not assumed.
        """
        assert_agrees("{{ p.keys|slice:':2' }}", {"p": {"a": 1, "b": 2, "c": 3}})
        assert_agrees("{{ p.keys|slice:':2'|join:'' }}", {"p": {"a": 1, "b": 2, "c": 3}})
        assert djust_render("{{ p.keys|slice:':2'|join:'' }}", {"p": {"a": 1, "b": 2, "c": 3}}) == (
            "abc"
        )


# ===========================================================================
# What a view still IS.
# ===========================================================================


class TestAViewIsStillASequenceWherePythonSaysItIs:
    P = {"a": 1, "b": 2}

    def test_iteration_unpacking_length_join_truthiness(self) -> None:
        for src in (
            "{% for k, v in p.items %}[{{ k }}={{ v }}]{% endfor %}",
            "{% for x in p.keys %}[{{ x }}]{% endfor %}",
            "{% for x in p.values %}[{{ x }}]{% endfor %}",
            "{{ p.items|length }}",
            "{{ p.keys|length }}",
            "{{ p.keys|join:'-' }}",
            "{{ p.keys|unordered_list }}",
            "{% if p.keys %}Y{% else %}N{% endif %}",
            "{% if 'a' in p.keys %}Y{% else %}N{% endif %}",
            "{% if 1 in p.values %}Y{% else %}N{% endif %}",
            "{% with q=p.keys %}[{{ q }}]{% endwith %}",
            "{% for x in p.items reversed %}[{{ x }}]{% endfor %}",
            "{% for x in p.keys %}{% empty %}E{% endfor %}",
        ):
            assert_agrees(src, {"p": self.P})

    def test_an_empty_dicts_view_is_empty_and_falsy(self) -> None:
        for src in (
            "{{ p.keys }}",
            "{{ p.items }}",
            "{{ p.keys|length }}",
            "{% if p.keys %}Y{% else %}N{% endif %}",
            "{% for x in p.keys %}[{{ x }}]{% empty %}E{% endfor %}",
        ):
            assert_agrees(src, {"p": {}})

    def test_the_elements_keep_their_type(self) -> None:
        """#2339's key type reaches through the view."""
        for src in (
            "{% for k in p.keys %}{% if k == 0 %}INT{% else %}STR{% endif %}{% endfor %}",
            "{{ p.keys }}",
            "{{ p.items }}",
        ):
            assert_agrees(src, {"p": {0: 1}})


# ===========================================================================
# The exhaustive filter sweep — what settled the model.
# ===========================================================================


FILTER_ARGS = {
    "add": "1",
    "center": "9",
    "cut": "a",
    "date": "Y",
    "default": "X",
    "default_if_none": "X",
    "dictsort": "0",
    "dictsortreversed": "0",
    "divisibleby": "2",
    "floatformat": "2",
    "get_digit": "1",
    "join": "-",
    "json_script": "i",
    "ljust": "9",
    "rjust": "9",
    "slice": ":2",
    "stringformat": "s",
    "time": "H",
    "truncatechars": "9",
    "truncatechars_html": "9",
    "truncatewords": "2",
    "truncatewords_html": "2",
    "urlizetrunc": "9",
    "wordwrap": "9",
    "yesno": "y,n",
    "phone2numeric": None,
}


#: A SECOND arg for the filters whose behaviour turns on it.
#:
#: One arg per filter is a curated sample wearing a sweep's clothes, and it hid
#: ``dictsort`` entirely: the chosen ``'0'`` is a case where DJANGO ALSO FAILS
#: (a quoted ``'0'`` is a key lookup, and a tuple has no key ``'0'``), so the
#: cell agreed about a filter that diverges for every arg that resolves.
SECOND_ARGS = {
    "dictsort": "k",
    "dictsortreversed": "k",
    "slice": "1:",
    "join": "",
    "default": "",
    "truncatewords": "1",
    "get_digit": "2",
    "floatformat": "-3",
    "stringformat": "r",
    "yesno": "y,n,m",
    "add": "abc",
    "center": "3",
    "cut": "d",
    "divisibleby": "1",
    "ljust": "3",
    "rjust": "3",
}


class TestEveryFilter:
    """A curated list samples one axis and blinds you on the next.

    Every Django built-in against every view kind AND every dict size AND a
    second arg where the arg matters — rather than the six names the issue
    happened to think of, which is what showed ``slice`` does not raise and
    that five more filters do.
    """

    #: EVERY size, not one. `pluralize` answers `""` for a 1-entry view and
    #: `"s"` otherwise, so a sweep pinned to a 2-entry dict agrees by luck at
    #: the one length where the two implementations differ — which is exactly
    #: what happened: the divergence was found by auditing the `List | Tuple`
    #: or-patterns, not here. A size-dependent axis needs its sizes swept.
    DICTS = [{}, {"a": 1}, {"a": 1, "b": 2}, {"a": 1, "b": 2, "c": 3}]
    P = {"a": 1, "b": 2}

    @staticmethod
    def _equivalent_list(kind: str, d: dict) -> list:
        """What ``list(view)`` is — the twin every exempt cell is judged against."""
        if kind == "keys":
            return list(d.keys())
        if kind == "values":
            return list(d.values())
        return list(d.items())

    def _sweep(self) -> tuple[int, dict[str, set[str]], list]:
        """Returns (cells, exempt names per kind, unexplained disagreements).

        The exemption is a MECHANICAL PREDICATE, not a name list: a cell is
        exempt only when the SAME filter over a PLAIN LIST of the same
        elements diverges too — which proves the divergence belongs to the
        filter and not to the view. Eight of Django's built-ins raise or
        return ``''`` for any non-scalar (``add``, ``date``, ``divisibleby``,
        ``get_digit``, ``phone2numeric``, ``time``, ``timesince``,
        ``timeuntil``) and djust has answered them its own way for every list
        and every dict since long before this change.
        """
        bad: list = []
        exempt: dict[str, set[str]] = {k: set() for k in KINDS}
        cells = 0
        for p_dict in self.DICTS:
            for kind in KINDS:
                twin = self._equivalent_list(kind, p_dict)
                for name in sorted(register.filters):
                    args = [FILTER_ARGS.get(name)]
                    if name in SECOND_ARGS:
                        args.append(SECOND_ARGS[name])
                    for arg in args:
                        suffix = f":'{arg}'" if arg else ""
                        src = "{{ p.%s|%s%s }}" % (kind, name, suffix)
                        cells += 1
                        d, r = both(src, {"p": p_dict})
                        # BOTH refuse. The exception TYPES differ and always
                        # will — Django's is CPython's, djust's is the
                        # `RuntimeError` every render error becomes at the PyO3
                        # boundary — so the comparable property is the bit.
                        # `first` / `last` / `random` over a non-empty view
                        # joined this branch in #2449.
                        if d.startswith("<<EXC") and r.startswith("<<EXC"):
                            continue
                        # Where DJANGO raises, djust renders nothing instead.
                        # That is the accepted shape (#2325), never more
                        # permissive.
                        if d.startswith("<<EXC") and r == "":
                            continue
                        if d == r:
                            continue
                        twin_d, twin_r = both("{{ q|%s%s }}" % (name, suffix), {"q": twin})
                        if twin_d != twin_r:
                            exempt[kind].add(name)
                            continue
                        bad.append((src, d, r))
        return cells, exempt, bad

    def test_every_builtin_filter_over_every_view_kind(self) -> None:
        cells, exempt, bad = self._sweep()
        assert cells >= len(self.DICTS) * 3 * 50, cells
        assert not bad, f"{len(bad)}/{cells} unexplained:\n" + "\n".join(
            f"  {s}: django={a!r} djust={b!r}" for s, a, b in bad[:30]
        )

    def test_the_exemption_fires_and_is_a_property_of_the_filter(self) -> None:
        """Two guards on the predicate above.

        It must actually FIRE — a dead classifier carries an allowance
        nothing needs (#1859) — and the set it produces must be IDENTICAL
        across the three kinds. A divergence that appeared for ``keys`` and
        not for ``values`` would be a property of the VIEW, which is exactly
        what this change could have broken, and the equality is what stops
        the exemption from absorbing one.
        """
        _, exempt, _ = self._sweep()
        assert exempt["keys"], "the exemption never fired — it is a dead allowance"
        assert exempt["keys"] == exempt["items"] == exempt["values"], exempt

    def test_pluralize_counts_a_views_entries(self) -> None:
        """The one the fixed-size sweep could not see.

        Django's ``pluralize`` does ``len(value)`` in a try, and a view answers
        it — so a 1-entry view is singular. djust reached this through the
        ``_`` arm, which returns the suffix unconditionally: right for two
        entries by luck, wrong for one.
        """
        for n, d in ((0, {}), (1, {"a": 1}), (2, {"a": 1, "b": 2})):
            for kind in KINDS:
                src = "[{{ p.KIND|pluralize }}]".replace("KIND", kind)
                assert_agrees(src, {"p": d})
        assert djust_render("[{{ p.keys|pluralize }}]", {"p": {"a": 1}}) == "[]"
        assert djust_render("[{{ p.keys|pluralize }}]", {"p": {"a": 1, "b": 2}}) == "[s]"

    def test_dictsort_sorts_a_view(self) -> None:
        """Django's ``dictsort`` is ``sorted(value, key=…)``, and ``sorted()``
        takes any iterable — so ``{{ d.values|dictsort:"k" }}`` is a real,
        working idiom that returns a LIST.

        djust answered ``''`` for every kind and every arg. The or-pattern
        audit first classified this arm as correct on the ONE arg the sweep
        carried, ``'0'`` — a case where Django also fails, so it said "agree"
        about a filter that diverged everywhere else.
        """
        rows = {"a": {"k": 2}, "b": {"k": 1}}
        for src in (
            '{{ p.values|dictsort:"k" }}',
            '{{ p.values|dictsortreversed:"k" }}',
            "{{ p.keys|dictsort:0 }}",
            "{{ p.items|dictsort:0 }}",
            "{{ p.items|dictsortreversed:0 }}",
        ):
            assert_agrees(src, {"p": rows})
            assert_agrees(src, {"p": {"b": 2, "a": 1}})

    def test_a_view_reaches_a_tag_operand_through_BOTH_branches(self) -> None:
        """Two halves, and each was a separate defect on a separate channel.

        ``{% regroup %}`` hands its source through ``value_to_arg_string``,
        whose match has a ``_`` fallback — so the compiler could not ask about
        a view, and the view fell to ``to_string()``, handing the Python
        handler the TEXT ``dict_items([…])`` instead of the rows. That is the
        #2042 ``[List]``-collapse class one placeholder over, and #2340 fixed
        it; the PIPE-branch cell below is what proves that arm is live rather
        than decoration.

        The BARE dotted path was a second, unrelated miss —
        ``resolve_tag_operand``'s non-pipe branch used ``Context::get``, and
        ``d.values`` resolves only in ``Context::resolve`` — measured identical
        on the pre-#2340 build, filed as #2368 and closed there. This test
        asserted ``djust == "0"`` for it until then; it now asserts agreement,
        which is what the pin said should happen the day it was fixed.
        """
        rows = {"a": {"k": 2}, "b": {"k": 1}}
        SRC = "{% regroup p.values|slice:':2' by k as g %}{{ g|length }}"
        assert_agrees(SRC, {"p": rows})
        assert djust_render(SRC, {"p": rows}) == "2", (
            "the view must reach the handler as its ROWS — a `to_string()` "
            "collapse hands it the text `dict_values([…])` and regroup finds "
            "nothing to group"
        )

        bare = "{% regroup p.values by k as g %}{{ g|length }}"
        assert_agrees(bare, {"p": rows})
        assert djust_render(bare, {"p": rows}) == "2", (
            "the BARE dotted path must reach the handler too (#2368) — the "
            "non-pipe branch resolves through `Context::resolve`, which is "
            "where the dict views live"
        )

    def test_the_differential_corpus_can_reach_the_raising_half(self) -> None:
        """The dict-view axis (#2334) built only sequence-like cells.

        `{{ d.items|first }}`, `|last`, `|json_script` and `|slice` are where
        a view differs from a list at all, and the corpus constructed none of
        them — so the whole of #2340 was invisible to the two-build
        differential. A corpus gap is silent by construction, which is why it
        is pinned rather than remembered.
        """
        from pathlib import Path

        src = (
            Path(__file__).resolve().parents[2] / "scripts" / "filter-parity-differential.py"
        ).read_text()
        for shape in (
            "{{ p.items|first }}",
            "{{ p.keys|last }}",
            "{{ p.keys|json_script:'i' }}",
            "{{ p.keys|slice:':1' }}",
            "{{ p.keys|safe }}",
            "{{ p.values }}",
        ):
            assert shape in src, (
                f"the differential builds no {shape!r} cell — the raising and "
                "str()-consuming halves of the view model are unmeasured"
            )

    def test_the_sweep_actually_covers_the_raising_filters(self) -> None:
        """Premise assertion: if Django stopped raising for these, the sweep
        above would silently become a weaker test.
        """
        for name in ("first", "last", "random", "json_script"):
            d, _ = both("{{ p.keys|%s }}" % name, {"p": {"a": 1}})
            assert d == "<<EXC TypeError>>", (
                f"{name} no longer raises in Django — the sweep's raise branch "
                f"is now unexercised for it. got {d!r}"
            )


# ===========================================================================
# Not more permissive than Django.
# ===========================================================================


class TestNotMorePermissive:
    def test_a_hostile_key_is_escaped_through_the_view_repr(self) -> None:
        p = {XSS: 1}
        for kind in KINDS:
            for src in ("{{ p.%s }}", "{{ p.%s|pprint }}", "{{ p.%s|truncatewords:9 }}"):
                s = src % kind
                d, r = both(s, {"p": p})
                assert r == d, f"{s}: django={d!r} djust={r!r}"
                assert "<img" not in r, f"LIVE PAYLOAD from {s}: {r!r}"

    def test_looping_a_view_never_resolves_a_siblings_safety_mark(self) -> None:
        """The loop safe-key mapping asserts the loop variable IS
        ``<iterable>.<index>``; for a view that path does not exist, because a
        view is not subscriptable. So the mapping is gated off — the same
        ``!normalised`` condition a dict and a string already take.

        Measured rather than assumed, both directions: a hostile key must stay
        escaped, AND the gate must not have started emitting something Django
        does not. It is defence in depth — ``_collect_safe_keys`` writes
        ``p.<key>`` while the mapping would look up ``p.keys.<index>``, so no
        collision is reachable through a view today — and the probe exists so
        that a future collector that DID write such a path fails here rather
        than in a page.

        That the two spellings never meet is also why a *legitimately* marked
        value LOSES its mark through ``.values`` / ``.items``. Measured on both
        builds — flipping the gate changes no output, because
        ``p.values.<index>`` is not in ``safe_keys`` either way — so it is
        upstream of this gate, and is filed as #2361 rather than widened into
        here (#1079).
        """
        from django.utils.safestring import mark_safe

        from djust.mixins.rust_bridge import _collect_safe_keys

        for d in (
            {"1": mark_safe("<b>ok</b>"), XSS: "v"},
            {"0": mark_safe("<b>ok</b>"), XSS: "v"},
            {XSS: mark_safe("<b>ok</b>"), "1": "v"},
        ):
            safe_keys = _collect_safe_keys(d, "p")
            for kind in KINDS:
                # `.replace`, not `%`: the template's own `%}` is a format spec.
                src = "{% for x in p.KIND %}[{{ x }}]{% endfor %}".replace("KIND", kind)
                out = _rust.render_template_with_dirs(src, {"p": d}, [], safe_keys)
                assert "<img" not in out, f"LIVE PAYLOAD from {src} on {d!r}: {out!r}"

    def test_marking_the_view_safe_matches_django_exactly(self) -> None:
        """``|safe`` on a view emits its repr LIVE in Django too, so djust
        must emit the same thing — no more, and no less.
        """
        assert_agrees("{{ p.keys|safe }}", {"p": {XSS: 1}})

    def test_no_filter_emits_a_live_payload_djangos_does_not(self) -> None:
        p = {XSS: 1}
        leaks = []
        for kind in KINDS:
            for name in sorted(register.filters):
                arg = FILTER_ARGS.get(name)
                expr = f"p.{kind}|{name}" + (f":'{arg}'" if arg else "")
                src = "{{ %s }}" % expr
                d, r = both(src, {"p": p})
                if "<img" in r and "<img" not in d:
                    leaks.append((src, d, r))
        assert not leaks, leaks[:5]


# ===========================================================================
# The #2360 interaction — which did not exist when this was written.
# ===========================================================================


class TestTheContextBuiltinsInteraction:
    """``True`` / ``False`` / ``None`` became context BUILTINS in #2360, in the
    same resolution path a typed dict key (#2339) and a view (#2340) live in.

    A key spelled like a builtin is where those three could collide, and the
    collision is silent in both directions: a needle that matches a key it
    should not opens a gate, and one that misses a key it should match closes
    a region. Every cell measured against live Django rather than reasoned
    about, because the ordering rules are Django's and not obvious.
    """

    def test_a_builtin_needle_compares_by_TYPE_against_a_key(self) -> None:
        """The cell #2339 changed, reached through #2360's builtin.

        ``True in {"True": 1}`` is False in Python — the builtin resolves to a
        BOOL and the key is a STRING. Before the typed key this answered Y,
        because the needle was stringified; the two fixes have to agree here or
        a `{% if True in d %}` gate opens on a dict that merely has a key
        spelled ``"True"``.
        """
        src = "{% if NEEDLE in q %}Y{% else %}N{% endif %}"
        cases = [
            ("True", {"True": 1}, "N"),
            ("True", {True: 1}, "Y"),
            # Numeric conflation reaches the builtin too: `True == 1`.
            ("True", {1: 1}, "Y"),
            ("None", {"None": 1}, "N"),
            ("None", {None: 1}, "Y"),
            ("False", {False: 1}, "Y"),
            ("False", {0: 1}, "Y"),
            ("False", {"False": 1}, "N"),
        ]
        for needle, q, want in cases:
            s = src.replace("NEEDLE", needle)
            assert_agrees(s, {"q": q})
            assert djust_render(s, {"q": q}) == want, (needle, q)

    def test_a_dict_KEY_wins_over_the_builtin_on_a_dotted_path(self) -> None:
        """Django's ``Variable._resolve_lookup`` resolves a dotted segment by
        mapping-item access; the builtin applies to a BARE name only.

        So ``{{ d.True }}`` is the value under the key ``"True"`` — a string
        lookup, which is also why it MISSES a dict keyed by the bool.
        """
        assert_agrees("[{{ d.True }}]", {"d": {"True": 7}})
        assert djust_render("[{{ d.True }}]", {"d": {"True": 7}}) == "[7]"
        assert_agrees("[{{ d.None }}]", {"d": {"None": 7}})
        # A dotted segment is a STRING, so the bool key is not reached.
        assert_agrees("[{{ d.True }}]", {"d": {True: 7}})
        assert djust_render("[{{ d.True }}]", {"d": {True: 7}}) == "[]"

    def test_a_context_variable_named_like_a_builtin_wins(self) -> None:
        assert_agrees("[{{ True }}]", {"True": 9})
        assert djust_render("[{{ True }}]", {"True": 9}) == "[9]"
        # …and with nothing shadowing it, the builtin renders.
        assert_agrees("[{{ True }}]", {})
        assert djust_render("[{{ True }}]", {}) == "[True]"

    def test_the_builtins_carry_through_a_VIEW(self) -> None:
        """All three mechanisms at once: a builtin needle, a typed key, and a
        dict view as the haystack.
        """
        for d in ({"True": 1, "None": 2}, {True: 1, None: 2}):
            for src in (
                "{% for k in d.keys %}[{{ k }}]{% endfor %}",
                "[{{ d.keys }}]",
                "[{{ d.items }}]",
                "{% if True in d.keys %}Y{% else %}N{% endif %}",
                "{% if None in d.keys %}Y{% else %}N{% endif %}",
            ):
                assert_agrees(src, {"d": d})
        # The discriminating pair: the SAME template answers differently for a
        # string-keyed and a typed-keyed dict, which is what proves the view
        # carries the key's type rather than its text.
        q = "{% if True in d.keys %}Y{% else %}N{% endif %}"
        assert djust_render(q, {"d": {"True": 1}}) == "N"
        assert djust_render(q, {"d": {True: 1}}) == "Y"
