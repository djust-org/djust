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
        for src in ("{{ p.items|first }}", "{{ p.items|last }}"):
            d, r = both(src, {"p": {"a": 1}})
            assert d == "<<EXC TypeError>>", d
            assert r == "", f"djust must render NOTHING where Python raises, got {r!r}"

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


class TestEveryFilter:
    """A curated list samples one axis and blinds you on the next.

    Every Django built-in against every view kind, rather than the six names
    the issue happened to think of — which is what showed ``slice`` does not
    raise and that five more filters do.
    """

    P = {"a": 1, "b": 2}

    @classmethod
    def _equivalent_list(cls, kind: str) -> list:
        """What ``list(view)`` is — the twin every exempt cell is judged against."""
        if kind == "keys":
            return list(cls.P.keys())
        if kind == "values":
            return list(cls.P.values())
        return list(cls.P.items())

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
        for kind in KINDS:
            twin = self._equivalent_list(kind)
            for name in sorted(register.filters):
                arg = FILTER_ARGS.get(name)
                suffix = f":'{arg}'" if arg else ""
                src = "{{ p.%s|%s%s }}" % (kind, name, suffix)
                cells += 1
                d, r = both(src, {"p": self.P})
                # Where DJANGO raises, djust renders nothing instead. That is
                # the accepted shape (#2325) and is never more permissive.
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
        assert cells >= 3 * 50, cells
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
