"""A safety grant on a SUB-PATH follows the name across a binding (#2375).

The defect
----------
#2361/#2363 made a bind carry the grant at the **name** granularity:
``Context::bind`` revokes ``name`` and ``name.…`` and then grants what the
resolved value carries as a whole. So ``{% with q=p %}{{ q }}{% endwith %}`` is
right. But ``rust_bridge._collect_safe_keys`` writes a dict's marks at
``p.<key>``, and nothing ever wrote ``q.a`` — so ``{{ q.a }}`` asked
``Context::is_safe("q.a")``, missed, and the marked value came out escaped.

The single-variable ``{% for %}`` did NOT have the bug, and the reason is the
whole of the fix: ``Context::set_loop_mapping`` registered ``x -> (rows, i)``
and ``is_safe`` REWROTE the dotted path through it. That mechanism is an
ALIAS — ``bind`` is a copy — and it was expressible for exactly one shape.
Widening it to a plain ``name -> <dotted prefix>`` retires the copy-vs-alias
split (#1646) rather than adding a second copy.

Direction
---------
The bug is OVER-escaping: a lost capability, never a leak. Everything below
that asserts an ESCAPED output is therefore the load-bearing half — the fix
must not grant one path segment more than Django does, and the ways it could
are enumerated in ``TestTheAliasIsRefusedWhereTheCorrespondenceIsFalse`` and
``TestABindReplacesTheAliasToo``.

The rule #2378 established is **"a bind REPLACES the grant"**, not "a bind also
carries one". That has to hold for the alias or it means nothing: with ``p``
marked, ``{% with q=p %}{% with q=hostile %}{{ q.a }}{% endwith %}{% endwith %}``
must NOT resolve ``q.a`` through the stale ``p.a``. ``Context::bind`` therefore
removes the alias — via ``revoke_safe_subtree``, and deliberately ABOVE its
``safe_keys.is_empty()`` early return.

Every expectation here is LIVE Django, never a transcription.
"""

import itertools

import pytest
from django.template import Context as DjangoContext
from django.template import Template as DjangoTemplate
from django.utils.safestring import mark_safe

from djust import _rust
from djust.mixins.rust_bridge import _collect_safe_keys

MARKED = mark_safe("<b>ok</b>")
HOSTILE = "<img src=x onerror=alert(1)>"
ESCAPED_HOSTILE = "&lt;img src=x onerror=alert(1)&gt;"


def _safe_keys(ctx: dict) -> list[str]:
    keys: list[str] = []
    for key, value in ctx.items():
        keys.extend(_collect_safe_keys(value, key))
    return keys


def both(tpl: str, ctx: dict) -> tuple[str, str]:
    django_out = DjangoTemplate(tpl).render(DjangoContext(dict(ctx)))
    safe_keys = _safe_keys(ctx)
    djust_out = _rust.render_template_with_dirs(tpl, ctx, [], safe_keys or None)
    return django_out, djust_out


def assert_agrees(tpl: str, ctx: dict) -> str:
    django_out, djust_out = both(tpl, ctx)
    assert djust_out == django_out, (
        f"{tpl!r} over {ctx!r}\n  django {django_out!r}\n  djust  {djust_out!r}"
    )
    return djust_out


class TestTheGrantReachesASubPath:
    """The two cells the issue reports, plus the third binding spelling."""

    def test_with_binding(self):
        assert (
            assert_agrees("{% with q=p %}[{{ q.a }}]{% endwith %}", {"p": {"a": MARKED}})
            == "[<b>ok</b>]"
        )

    def test_for_tuple_unpacking(self):
        assert (
            assert_agrees(
                "{% for a, b in p %}[{{ b.z }}]{% endfor %}",
                {"p": [("x", {"z": MARKED})]},
            )
            == "[<b>ok</b>]"
        )

    def test_a_deeper_source_path(self):
        assert (
            assert_agrees(
                "{% with q=p.sub %}[{{ q.a }}]{% endwith %}",
                {"p": {"sub": {"a": MARKED}}},
            )
            == "[<b>ok</b>]"
        )

    def test_a_deeper_target_path(self):
        assert (
            assert_agrees(
                "{% with q=p %}[{{ q.a.b }}]{% endwith %}",
                {"p": {"a": {"b": MARKED}}},
            )
            == "[<b>ok</b>]"
        )

    def test_the_alias_chain_collapses_through_a_loop_variable(self):
        # `q -> row` would resolve against a `safe_keys` set that never spells
        # `row` at all. `set_alias` expands the path through the EXISTING
        # aliases at registration time, so this registers `q -> rows.0`.
        assert (
            assert_agrees(
                "{% for row in rows %}{% with q=row %}[{{ q.a }}]{% endwith %}{% endfor %}",
                {"rows": [{"a": MARKED}]},
            )
            == "[<b>ok</b>]"
        )

    def test_the_item_granularity_resolves_through_the_alias_too(self):
        # `Context::items_are_safe` reads the same alias — `join` must emit the
        # marked ELEMENTS live, which is #2287 seen through a binding.
        assert (
            assert_agrees(
                "{% with q=p %}[{{ q.a|join:'-' }}]{% endwith %}",
                {"p": {"a": [MARKED, MARKED]}},
            )
            == "[<b>ok</b>-<b>ok</b>]"
        )

    def test_include_with_is_the_third_spelling(self, tmp_path):
        # Decided EXPLICITLY rather than by omission (#1646): `{% include … with %}`
        # is the same operation under a third name.
        (tmp_path / "child.html").write_text("[{{ q.a }}]", encoding="utf-8")
        tpl = '{% include "child.html" with q=p %}'
        ctx = {"p": {"a": MARKED}}
        from django.template import Engine
        from django.template import Template as DT

        engine = Engine(dirs=[str(tmp_path)], libraries={})
        django_out = DT(tpl, engine=engine).render(DjangoContext(dict(ctx)))
        djust_out = _rust.render_template_with_dirs(
            tpl, ctx, [str(tmp_path)], _safe_keys(ctx) or None
        )
        assert djust_out == django_out == "[<b>ok</b>]", (django_out, djust_out)


class TestTheGrantDoesNotLEAKSIDEWAYS:
    """The half that must stay escaped — an over-grant here is a live XSS."""

    def test_an_unmarked_sibling_key_is_still_escaped(self):
        assert (
            assert_agrees(
                "{% with q=p %}[{{ q.z }}]{% endwith %}",
                {"p": {"a": MARKED, "z": HOSTILE}},
            )
            == f"[{ESCAPED_HOSTILE}]"
        )

    def test_an_unmarked_sibling_component_of_an_unpack_is_still_escaped(self):
        assert (
            assert_agrees(
                "{% for a, b in p %}[{{ b.z }}]{% endfor %}",
                {"p": [("x", {"z": HOSTILE, "a": MARKED})]},
            )
            == f"[{ESCAPED_HOSTILE}]"
        )

    def test_a_second_row_does_not_inherit_the_first_rows_mark(self):
        assert (
            assert_agrees(
                "{% for a, b in p %}[{{ b.z }}]{% endfor %}",
                {"p": [("x", {"z": MARKED}), ("y", {"z": HOSTILE})]},
            )
            == f"[<b>ok</b>][{ESCAPED_HOSTILE}]"
        )

    def test_an_unrelated_name_sharing_a_prefix_is_untouched(self):
        assert (
            assert_agrees(
                "{% with q=p %}[{{ pz.a }}]{% endwith %}",
                {"p": {"a": MARKED}, "pz": {"a": HOSTILE}},
            )
            == f"[{ESCAPED_HOSTILE}]"
        )


class TestRebindingTheALIAS_TARGET_RetiresItToo:
    """The half that was a LIVE XSS in this change's first version.

    An alias asserts an IDENTITY — ``q`` *is* the value at ``p`` — so rebinding
    either END of it makes the claim false. The first version removed the alias
    ON the bound name and not the aliases POINTING AT it, and:

    ``{% with q=p %}{% with p=r|safe %}{{ q }}{% endwith %}{% endwith %}``

    bound ``q`` to the ORIGINAL (hostile) ``p``, then re-bound ``p`` to
    something safe. ``set_safety("p", True)`` marks the NAME ``p``; the
    surviving ``q -> p`` alias answered ``is_safe("q")`` from it, and ``q``'s
    hostile value reached the page with an **unescaped tag opener**.

    Found by probing the mechanism adversarially, not by reading it — measured
    against live Django, which escapes. These cases are why
    ``revoke_safe_subtree`` scans the alias TARGETS as well as removing the
    alias on the key, and why both sit ABOVE its ``safe_keys.is_empty()``
    early return: in this very case ``safe_keys`` is empty at that moment and
    ``set_safety`` fills it one line later.
    """

    def test_rebinding_the_target_to_a_safe_value_does_not_free_the_alias(self):
        out = assert_agrees(
            "{% with q=p %}{% with p=r|safe %}[{{ q }}]{% endwith %}{% endwith %}",
            {"p": HOSTILE, "r": "<i>safe</i>"},
        )
        assert out == f"[{ESCAPED_HOSTILE}]", out
        assert "<img" not in out, out

    def test_the_same_through_a_sub_path(self):
        out = assert_agrees(
            "{% with q=p %}{% with p=r %}[{{ q.a }}]{% endwith %}{% endwith %}",
            {"p": {"a": HOSTILE}, "r": {"a": MARKED}},
        )
        assert "<img" not in out, out

    def test_rebinding_a_loop_variables_ITERABLE_retires_its_alias(self):
        # The loop's alias target is `rows.<i>`, so the sweep has to match a
        # target BENEATH the rebound name, not only one equal to it.
        out = assert_agrees(
            "{% for x in rows %}{% with rows=r|safe %}[{{ x }}]{% endwith %}{% endfor %}",
            {"rows": [HOSTILE], "r": "<i>s</i>"},
        )
        assert "<img" not in out, out

    @pytest.mark.parametrize(
        ("tpl", "ctx"),
        [
            (
                '{% with q=p %}{% with p="<i>lit</i>" %}[{{ q }}]{% endwith %}{% endwith %}',
                {"p": HOSTILE},
            ),
            (
                '{% with q=p %}{% with p="<i>lit</i>" %}[{{ q.z }}]{% endwith %}{% endwith %}',
                {"p": {"z": HOSTILE}},
            ),
            ('{% with a="<i>lit</i>" b=a %}[{{ b }}]{% endwith %}', {"a": HOSTILE}),
        ],
        ids=["target-whole", "target-subpath", "multi-assign"],
    )
    def test_a_quoted_LITERAL_is_a_second_way_into_the_same_hazard(self, tpl, ctx):
        """#2376 opened a new spelling of the trigger; the guard already covers it.

        The two leaks this class exists for were triggered by
        ``set_safety("<name>", True)`` marking a NAME that a surviving alias
        then read. When they were found, the only way to reach that was a
        ``|safe``-filtered rebind. #2376 landed afterwards and made a quoted
        literal ``SafeData``, so ``{% with p="<i>lit</i>" %}`` now marks the
        name too — the same trigger through a spelling that did not exist when
        the guard was written.

        Probed on the merged build and pinned here rather than left as a
        one-off check: a merge combining two escaping changes is exactly where
        a new path into an old hazard appears, and the next such merge should
        find this test already standing.
        """
        out = assert_agrees(tpl, ctx)
        assert "<img" not in out, out


class TestABindReplacesTheAliasToo:
    """#2378's rule, one path segment down — the UNDER-escape direction.

    A stale alias is strictly worse than a stale grant: it re-points a whole
    subtree at a path that no longer describes the value.
    """

    def test_a_rebinding_with_revokes_the_alias(self):
        assert (
            assert_agrees(
                "{% with q=p %}{% with q=r %}[{{ q.a }}]{% endwith %}{% endwith %}",
                {"p": {"a": MARKED}, "r": {"a": HOSTILE}},
            )
            == f"[{ESCAPED_HOSTILE}]"
        )

    def test_a_loop_rebinding_an_aliased_name_revokes_it(self):
        # The loop's revoke is HOISTED out of the iteration; the alias removal
        # rides it, which is why it must sit above the `safe_keys.is_empty()`
        # early return rather than under it.
        assert (
            assert_agrees(
                "{% with q=p %}{% for q in rows %}[{{ q.a }}]{% endfor %}{% endwith %}",
                {"p": {"a": MARKED}, "rows": [{"a": HOSTILE}]},
            )
            == f"[{ESCAPED_HOSTILE}]"
        )

    def test_a_filtered_rebind_revokes_it_and_grants_nothing(self):
        assert (
            assert_agrees(
                "{% with q=p %}{% with q=r|dictsort:'a' %}[{{ q.0.a }}]{% endwith %}{% endwith %}",
                {"p": {"a": MARKED}, "r": [{"a": HOSTILE}]},
            )
            == f"[{ESCAPED_HOSTILE}]"
        )

    def test_the_alias_does_not_outlive_its_block(self):
        # `{% with %}` renders its body against a CLONE, so the alias dies with
        # it. Asserted rather than assumed: an alias that leaked out would
        # re-point a name the outer scope owns.
        assert (
            assert_agrees(
                "{% with q=p %}{% endwith %}[{{ q.a }}]",
                {"p": {"a": MARKED}, "q": {"a": HOSTILE}},
            )
            == f"[{ESCAPED_HOSTILE}]"
        )

    def test_a_self_binding_registers_nothing(self):
        # `set_alias` refuses `p -> p`, and refusing it changes nothing: the
        # `bind` that precedes it has already revoked `p` and every `p.…`, so
        # a self-alias would resolve to a path that was just cleared.
        #
        # The resulting cell is an OVER-escape and it is PRE-EXISTING — it is
        # #2378's revoke seen one segment down, identical before and after this
        # change. Pinned rather than left silent so it cannot be mistaken for
        # something this fix was supposed to cover.
        django_out, djust_out = both("{% with p=p %}[{{ p.a }}]{% endwith %}", {"p": {"a": MARKED}})
        assert django_out == "[<b>ok</b>]"
        assert djust_out == "[&lt;b&gt;ok&lt;/b&gt;]"


class TestEachMechanismIsIndependentlyREACHABLE:
    """One test per mechanism that goes red when ONLY that mechanism is removed.

    Three of the nine mutations in this change's gate-off SURVIVED on the first
    pass, and none of the three was a no-op — each was a second mechanism
    covering for the first (#2129/#2135). The cases below are what separates
    them, and each was constructed by asking "what input does this mechanism
    and only this mechanism answer?" rather than by adding coverage generally.
    """

    def test_a_FILTERED_rebind_of_an_aliased_name_leaves_no_stale_alias(self):
        """Separates `aliases.remove(key)` from the overwrite that hides it.

        An UNFILTERED rebind registers a new alias, which overwrites the stale
        one — so every unfiltered case passes whether or not `bind` removes it.
        A FILTERED rebind registers nothing (`bare_dotted_path` refuses it), so
        the removal is the only thing standing between `q` and `p`'s mark.
        """
        out = assert_agrees(
            "{% with q=p %}{% with q=r|upper %}[{{ q }}]{% endwith %}{% endwith %}",
            {"p": MARKED, "r": HOSTILE},
        )
        assert out == "[&lt;IMG SRC=X ONERROR=ALERT(1)&gt;]", out
        assert "<IMG" not in out, out

    def test_a_FILTERED_loop_over_an_aliased_name_leaves_no_stale_alias(self):
        # The same separation on the loop's hoisted revoke: a filtered operand
        # registers no loop mapping, so nothing overwrites the stale alias.
        out = assert_agrees(
            "{% with q=p %}{% for q in rows|slice:':1' %}[{{ q }}]{% endfor %}{% endwith %}",
            {"p": MARKED, "rows": [HOSTILE]},
        )
        assert "<img" not in out, out

    def test_a_loop_over_an_ALIASED_iterable_resolves_its_item_marks(self):
        """Separates the loop's OWN alias expansion from the binding's.

        `{% with q=rows %}{% for x in q %}` registers `x -> q.<i>` unless
        `set_loop_mapping` expands `q` first — and `safe_keys` never spells
        `q` at all, so without the expansion the mark is lost. Over-escaping,
        so this asserts the CAPABILITY rather than a leak.
        """
        assert (
            assert_agrees(
                "{% with q=rows %}{% for x in q %}[{{ x.a }}]{% endfor %}{% endwith %}",
                {"rows": [{"a": MARKED}]},
            )
            == "[<b>ok</b>]"
        )

    def test_a_nested_loop_resolves_through_the_outer_loops_alias(self):
        assert (
            assert_agrees(
                "{% for row in rows %}{% for cell in row %}[{{ cell }}]{% endfor %}{% endfor %}",
                {"rows": [[MARKED]]},
            )
            == "[<b>ok</b>]"
        )

    def test_a_safe_key_spelled_like_a_FILTERED_expression_cannot_be_reached(self):
        """Separates `bare_dotted_path` from "the target never matches anyway".

        A filtered expression's TEXT is normally unreachable as a `safe_keys`
        entry, which is why refusing to alias it looks like a no-op. It is not:
        `_collect_safe_keys` builds paths out of dict KEYS, and a dict may hold
        a key spelled `a|upper`. Without the guard, `{% with q=p.a|upper %}`
        would register `q -> p.a|upper`, which IS a real safe_key here — and
        `{{ q }}`, the upper-cased HOSTILE value, would go to the page raw.
        """
        out = assert_agrees(
            "{% with q=p.a|upper %}[{{ q }}]{% endwith %}",
            {"p": {"a": HOSTILE, "a|upper": MARKED}},
        )
        assert out == "[&lt;IMG SRC=X ONERROR=ALERT(1)&gt;]", out
        assert "<IMG" not in out, out


class TestTheAliasIsRefusedWhereTheCorrespondenceIsFalse:
    """The #2334 gate: register only where `name` genuinely IS `<path>`.

    Each of these is an over-escape — a lost capability — and each is the
    direction to fail in, because the alternative is resolving a mark that
    belongs to a DIFFERENT element.
    """

    @pytest.mark.parametrize(
        "expr",
        ["p|dictsort:'a'", "p|slice:':2'", "p|first", "'literal'", "5"],
    )
    def test_a_non_path_expression_gets_no_alias(self, expr):
        # The claim is that NO grant is registered, so the marked value comes
        # out escaped. Django disagrees for the filter spellings — that is the
        # over-escaping direction the #2334 gate deliberately fails in — so
        # this asserts djust's answer directly rather than agreement.
        tpl = "{% with q=" + expr + " %}[{{ q.0.a }}]{% endwith %}"
        _django_out, djust_out = both(tpl, {"p": [{"a": MARKED}]})
        assert "<b>" not in djust_out, djust_out

    def test_a_filtered_binding_does_not_carry_the_sub_path_grant(self):
        # `dictsort` REORDERS, so `q.0` is not `p.0`; resolving `p.0.a`'s mark
        # through it would grant a mark belonging to a different element. The
        # escape here is deliberate, and Django disagrees — the over-escaping
        # direction, filed rather than closed.
        django_out, djust_out = both(
            "{% with q=p|dictsort:'k' %}[{{ q.0.a }}]{% endwith %}",
            {"p": [{"k": 1, "a": MARKED}]},
        )
        assert django_out == "[<b>ok</b>]"
        assert djust_out == "[&lt;b&gt;ok&lt;/b&gt;]"

    def test_a_dict_view_unpack_does_not_carry_the_sub_path_grant(self):
        # `_collect_safe_keys` spells a dict's paths BY KEY NAME (`p.<k>`) and
        # a positional alias would assert `p.<INDEX>` — the #2334 collision,
        # which is a LIVE XSS when keys are user data. Refused, so the marked
        # value is escaped. Django disagrees; over-escaping again.
        django_out, djust_out = both(
            "{% for k, v in p.items %}[{{ v.a }}]{% endfor %}",
            {"p": {"k1": {"a": MARKED}}},
        )
        assert django_out == "[<b>ok</b>]"
        assert djust_out == "[&lt;b&gt;ok&lt;/b&gt;]"

    def test_the_collision_the_positional_alias_would_have_caused(self):
        # The falsifying case for the rule above, run rather than reasoned
        # about (#1516). A dict whose SECOND key is attacker-controlled and
        # whose key `"1"` is marked: a positional alias would resolve the
        # second key through `p.1` and emit it RAW.
        ctx = {"p": {"1": {"a": MARKED}, HOSTILE: {"a": HOSTILE}}}
        django_out, out = both("{% for k, v in p.items %}[{{ v.a }}]{% endfor %}", ctx)
        # Django emits the marked FIRST value live and escapes the second.
        assert django_out == f"[<b>ok</b>][{ESCAPED_HOSTILE}]", django_out
        # djust escapes BOTH — the refused alias costs the first cell's
        # capability and buys the second cell's safety. The load-bearing claim
        # is the second: the payload never reaches the page raw.
        assert HOSTILE not in out, out
        assert out == f"[&lt;b&gt;ok&lt;/b&gt;][{ESCAPED_HOSTILE}]", out


class TestTheLoopMappingItReplacedStillWorks:
    """Non-regression: the alias absorbed `loop_mappings`, so its cases must
    answer exactly what they answered before."""

    def test_a_single_variable_loop_resolves_its_item_mark(self):
        assert (
            assert_agrees("{% for x in rows %}[{{ x.a }}]{% endfor %}", {"rows": [{"a": MARKED}]})
            == "[<b>ok</b>]"
        )

    def test_a_filtered_loop_operand_still_grants_nothing(self):
        django_out, djust_out = both(
            "{% for x in rows|slice:':2' %}[{{ x.a }}]{% endfor %}",
            {"rows": [{"a": MARKED}]},
        )
        assert djust_out == "[&lt;b&gt;ok&lt;/b&gt;]"
        assert django_out == "[<b>ok</b>]"

    def test_a_nested_loop_reusing_the_name_uses_the_inner_binding(self):
        assert (
            assert_agrees(
                "{% for x in outer %}{% for x in inner %}[{{ x.a }}]{% endfor %}{% endfor %}",
                {"outer": [1], "inner": [{"a": HOSTILE}]},
            )
            == f"[{ESCAPED_HOSTILE}]"
        )


class TestNoBindingSHAPEEmitsAPayloadRaw:
    """A generated sweep over the binding shapes, not a curated table.

    A curated table found the two live XSSes above — and it found them one at a
    time, each after the previous fix, which is the signal that the table was
    sampling an axis rather than covering it. This asks the same question over
    a generated product of wrapper shapes, emit spellings and value shapes:
    **can djust ever emit an unescaped tag opener where Django does not?**

    Non-vacuous by construction — ``test_the_sweep_can_see_a_leak`` shows the
    detector fires on a cell that genuinely is more permissive — and
    non-vacuous empirically: gating either half of the alias revocation off
    makes this report leaks.
    """

    NAMES_SHAPES = {  # noqa: RUF012 — read-only class data
        "dict-m": {"k": MARKED, "z": HOSTILE},
        "dict-h": {"k": HOSTILE, "z": HOSTILE},
        "bare-m": MARKED,
        "bare-h": HOSTILE,
        "list-m": [MARKED],
        "list-h": [HOSTILE],
    }

    WRAPPERS = [  # noqa: RUF012
        "{% with q=p %}@BODY@{% endwith %}",
        "{% with q=p r=q %}@BODY@{% endwith %}",
        "{% with q=p %}{% with p=r %}@BODY@{% endwith %}{% endwith %}",
        "{% with q=p %}{% with p=r|safe %}@BODY@{% endwith %}{% endwith %}",
        "{% with q=p %}{% with q=r %}@BODY@{% endwith %}{% endwith %}",
        "{% with q=p %}{% with q=r|safe %}@BODY@{% endwith %}{% endwith %}",
        "{% with a=p q=a %}@BODY@{% endwith %}",
        "{% with q=p.k %}@BODY@{% endwith %}",
        "{% for q in p %}@BODY@{% endfor %}",
        "{% for q in p %}{% with p=r|safe %}@BODY@{% endwith %}{% endfor %}",
        "{% for k, q in p.items %}@BODY@{% endfor %}",
        "{% for k, q in p %}@BODY@{% endfor %}",
        "{% with q=p %}{% for p in r %}@BODY@{% endfor %}{% endwith %}",
        "{% with q=p %}{% for q in r %}@BODY@{% endfor %}{% endwith %}",
        "{% for q in p %}{% with r=q %}@BODY@{% endwith %}{% endfor %}",
    ]

    BODIES = [  # noqa: RUF012
        "[{{ q }}]",
        "[{{ q.k }}]",
        "[{{ q.z }}]",
        "[{{ q.0 }}]",
        "[{{ r }}]",
        "[{{ r.k }}]",
    ]

    def _cells(self):
        for wrapper, body, ps, rs in itertools.product(
            self.WRAPPERS, self.BODIES, self.NAMES_SHAPES, self.NAMES_SHAPES
        ):
            ctx = {
                "p": self.NAMES_SHAPES[ps],
                "r": self.NAMES_SHAPES[rs],
                "a": self.NAMES_SHAPES["dict-h"],
            }
            yield wrapper.replace("@BODY@", body), ctx

    def test_no_cell_emits_the_payload_where_django_escapes_it(self):
        leaks = []
        cells = 0
        for tpl, ctx in self._cells():
            cells += 1
            try:
                django_out, djust_out = both(tpl, ctx)
            except Exception:  # noqa: BLE001, S112 — a raise is not a leak
                continue
            if "<img" in djust_out and "<img" not in django_out:
                leaks.append((tpl, ctx, django_out, djust_out))
        # The count is MECHANICAL — the product's own size, never a prose
        # number.
        assert cells == len(self.WRAPPERS) * len(self.BODIES) * len(self.NAMES_SHAPES) ** 2
        assert leaks == [], leaks[:5]

    def test_the_sweep_is_not_measuring_an_empty_space(self):
        """A sweep that reports zero because it built nothing that could
        discriminate is the same green as one that found nothing
        (#2129/#2135). Two independent things are asserted:

        1. the DETECTOR fires on a pair that is genuinely more permissive —
           run against constructed strings, because no real cell may be one;
        2. the CELLS actually carry the payload to the page in at least one
           of them, so ``"<img" in djust_out`` is a reachable state rather
           than a condition nothing can satisfy.
        """
        # (1) the predicate itself
        django_out, djust_out = "[&lt;img …&gt;]", "[<img src=x onerror=alert(1)>]"
        assert "<img" in djust_out and "<img" not in django_out

        # (2) the space reaches the state the predicate reads. `{{ q }}` over a
        # marked value renders live markup in BOTH engines — that is parity,
        # not a leak, and it is what proves the sweep's outputs are not all
        # inert text.
        live = 0
        empty_pairs = 0
        for tpl, ctx in self._cells():
            try:
                dj, du = both(tpl, ctx)
            except Exception:  # noqa: BLE001, S112
                continue
            if dj != du:
                empty_pairs += 1
            if "<b>ok</b>" in du:
                live += 1
        assert live > 0, "no cell emits live markup — the sweep cannot see an over-grant"
        assert empty_pairs > 0, (
            "every cell agrees with Django, so the sweep would report clean over any change at all"
        )
