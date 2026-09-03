"""A falsy object WITH attributes reaches the carrier, not the `__dict__` arm (#2478).

The divergence
--------------
::

    class LenZeroWithAttrs:
        def __init__(self): self.a = 1
        def __len__(self):  return 0

    {% if p %}T{% else %}F{% endif %}   python False   django F   djust T
    {{ p|length }}                                    django 0   djust 1
    {% for x in p %}[{{ x }}]{% endfor %}             django ''  djust '[a]'
    {{ p }}                       django '<LenZeroWithAttrs object …>'   djust "{'a': 1}"

#2466 closed the class that lands on ``FromPyObject for Value``'s final
``Ok(Value::String(ob.str()?))`` — a ``set``, a ``frozenset``, ``complex(0)``,
a bare zero-``__len__`` class. This object never got there: it has a non-empty
``__dict__``, so the bulk-dump arm ABOVE the fallback claimed it and it became
a **non-empty** ``Value::Object``, whose truthiness is the mapping rule.

Why #2466 could not close it, and what changed
-----------------------------------------------
``opaque_value`` was placed AFTER the ``__dict__`` arm deliberately: routing an
attribute-carrying object through the ``Encoded`` carrier would have fixed
``{% if %}`` and broken ``{{ obj.a }}``, because an ``Encoded`` had no
attributes. That reasoning was correct, and it is still asserted — as the
CLOSING case — in ``test_falsy_conversion_2466.py``.

#2481 gave ``Encoded`` an attribute map. So #2478 is a REORDER plus one field:
``opaque_value`` moves ABOVE the ``__dict__`` arm and carries the object's
public ``__dict__`` on the carrier. Every cell is then answered by a spelling
the struct already has.

SIX independent facts, not four
--------------------------------
The issue names four cells. Measured against live Django over 45 cells, the
divergence is **six independent facts** — and the two the issue does not name
are what decide the fix's SHAPE:

=============  ==================  ====================================
fact           carried as          cells it drives
=============  ==================  ====================================
truthiness     ``truthy``          ``{% if %}``, ``not``, ``and``/``or``,
                                   ``{% with %}``, ``{% firstof %}``,
                                   ``|yesno``, ``|default``, ``in`` a list
length         ``sized_empty``     ``|length``
iteration      ``sized_empty``     ``{% for %}``, ``{% for k,v %}``,
               / ``iterable``      ``|join``, ``|safeseq``, ``|escapeseq``,
                                   ``|unordered_list``, ``.items``, ``.keys``
display        ``display``         ``{{ p }}``, ``|default_if_none``,
                                   ``|stringformat:"s"``, ``|linebreaks``,
                                   ``|lower``, ``|striptags``,
                                   ``{% cycle %}``, ``|make_list``, ``|slice``
repr           ``repr``            ``|pprint``, ``|stringformat:"r"``
attributes     ``attrs``           ``{{ p.a }}``, ``{{ d.p.a }}``
=============  ==================  ====================================

**The issue's own suggested remedy — a truthiness override on
``Value::Object`` — reaches the first row and nothing else.** Length, iteration
and display read the MAPPING, and the ``__dict__`` arm's whole claim is that
the object IS a mapping of its attributes. Patching one answer of a wrong
carrier value-by-value is the non-converging shape #2129 took five rounds over;
``TestTheIssuesOwnRemedyWouldNotHaveReached`` measures the split rather than
asserting it.

The gate is #2466's, unchanged
-------------------------------
``opaque_value`` still declines a falsy object with a NON-ZERO ``__len__`` and
one that is ITERABLE with no ``__len__`` — Django renders their items, and this
carrier cannot produce them without RUNNING the object. They keep their
``Value::Object``, which is what ``TestTheGateIsUNCHANGED`` measures: only the
objects the gate ADMITS moved, and every other shape answers byte-for-byte what
it answered on the build before this change.

Both serialization floors stay above this arm
----------------------------------------------
``__djust_serialize__`` (#1986) and the raw-``Model`` arm (#1986 vector 7) are
ordered BEFORE ``opaque_value``, so a Django model cannot reach it and cannot
have its denylisted fields collected into the attribute map. Asserted by source
ORDER rather than left to reading, because the ordering IS the enforcement.

Every expectation is LIVE Django and LIVE Python. ``PRE_FIX`` is a recorded
measurement of the build immediately before this change, not a transcription.

Refs #2478, #2481, #2466, #2458, #2448, #1986, #2129, #1646, #1079.
"""

from __future__ import annotations

import pathlib
import re

import pytest

pytest.importorskip("django")

from django.template import Context as DjangoContext  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402

from adr027_flag import resolve_lazy  # noqa: E402

from djust import _rust  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[2]
CORE_RS = REPO / "crates" / "djust_core" / "src" / "lib.rs"

REFUSED = "<<REFUSED>>"
#: `str(o)` for a plain object carries its memory ADDRESS, which differs per
#: instance and per process — so every comparison here normalises it. Without
#: this, half the display cells would be unassertable and the recorded table
#: could not exist at all.
ADDR = re.compile(r"0x[0-9a-f]+")


def norm(rendered: str) -> str:
    """The address out, and every refusal collapsed to one token.

    The address is the ONLY normalisation, and that is deliberate: `PRE_FIX`
    below was captured by importing THIS module under THIS name against the
    reverted build (`scratch/run_capture.py`), so the class's `__module__` —
    which `str(o)` also carries — matches by construction rather than by a
    second regex. A capture harness that normalises away more than it must is
    a harness that can no longer see a real change.

    The refusal COLLAPSE is deliberate: Django raises `TypeError` where djust
    raises `RuntimeError` (the PyO3 boundary re-raises), so the class carries
    no parity information and pinning it would make every refusal cell a
    permanent, meaningless mismatch. What matters is that both engines refuse.
    """
    rendered = ADDR.sub("0xADDR", rendered)
    return REFUSED if rendered.startswith(REFUSED) else rendered


def django_render(source: str, context: dict) -> str:
    try:
        return norm(DjangoTemplate(source).render(DjangoContext(dict(context))))
    except Exception:  # noqa: BLE001 — the refusal IS the answer
        return REFUSED


def djust_render(source: str, context: dict) -> str:
    try:
        return norm(_rust.render_template(source, dict(context)))
    except Exception:  # noqa: BLE001
        return REFUSED


def djust_render_with_dirs(source: str, context: dict) -> str:
    """The OTHER entry point `DjustTemplateBackend` binds."""
    try:
        return norm(_rust.render_template_with_dirs(source, dict(context), []))
    except Exception:  # noqa: BLE001
        return REFUSED


class LenZeroWithAttrs:
    """The issue's own class, plus a SECOND attribute: with one, `|length` is
    1 either way and the cell cannot tell "the map's length" from "the
    object's"."""

    def __init__(self) -> None:
        self.a = 1
        self.b = "x"

    def __len__(self) -> int:
        return 0


class BoolFalseWithAttrs:
    """Falsy via `__bool__`, NO `__len__` — so Django's `{% for %}` RAISES
    here where it renders the empty branch for the class above. Different
    carried bits answer the two (`iterable` vs `sized_empty`), which is why
    both are swept."""

    def __init__(self) -> None:
        self.a = 1

    def __bool__(self) -> bool:
        return False


class LenZeroWithAttrsAndIter:
    """Falsy, zero `__len__`, AND iterable — the third gate combination.
    `|safeseq` iterates to nothing here and RAISES for `BoolFalseWithAttrs`."""

    def __init__(self) -> None:
        self.a = 1

    def __len__(self) -> int:
        return 0

    def __iter__(self):  # noqa: ANN201
        return iter([])


class DunderStrWithAttrs:
    """`__str__` and `__repr__` defined INDEPENDENTLY, so a fix that copied one
    into the other renders the wrong one for `|pprint` — the #2472
    measured-not-copied property, now reachable through this arm."""

    def __init__(self) -> None:
        self.a = 1

    def __len__(self) -> int:
        return 0

    def __str__(self) -> str:
        return "STR"

    def __repr__(self) -> str:
        return "REPR"


class LenTwoBoolFalseWithAttrs:
    """DECLINED by #2466's gate: falsy with a NON-ZERO `__len__`.

    CLAIMED since #2477/#2489, which is why this shape moved lists. The
    decline's reason — "the carrier cannot produce the items without running
    the object" — was answered rather than argued away: `opaque_value` now
    enumerates a RE-iterable object (`iter(o) is not o`, which this is), so
    `{% for %}` renders `[10][20]` on both engines. Only a ONE-SHOT iterator
    is still declined, because reading one consumes the caller's object.
    """

    def __init__(self) -> None:
        self.a = 1

    def __bool__(self) -> bool:
        return False

    def __len__(self) -> int:
        return 2

    def __iter__(self):  # noqa: ANN201
        return iter([10, 20])


class LenZeroNoAttrs:
    """#2466's own case: falsy, no attributes. Already correct, and must stay."""

    def __len__(self) -> int:
        return 0


class TruthyWithAttrs:
    """The control on the other side of the gate — Python calls it TRUE, so
    `opaque_value` declines it and it keeps the `__dict__` arm."""

    def __init__(self) -> None:
        self.a = 1


class LenZeroPrivateOnly:
    """Falsy with only a `_`-prefixed attribute. The `__dict__` filter skips
    it, so this reached `opaque_value` BEFORE #2478 too — the control that
    proves the reorder did not change the empty-map path."""

    def __init__(self) -> None:
        self._a = 1

    def __len__(self) -> int:
        return 0


SHAPES = {
    c.__name__: c
    for c in (
        LenZeroWithAttrs,
        BoolFalseWithAttrs,
        LenZeroWithAttrsAndIter,
        DunderStrWithAttrs,
        LenTwoBoolFalseWithAttrs,
        LenZeroNoAttrs,
        TruthyWithAttrs,
        LenZeroPrivateOnly,
    )
}

#: The shapes the gate ADMITS — the ones #2478 moved, plus the one
#: #2477/#2489 added.
#:
#: `LenTwoBoolFalseWithAttrs` was in `UNCHANGED` until #2477/#2489. It is falsy
#: with a non-zero `__len__` and a re-iterable `__iter__`, and #2466 declined it
#: because the carrier could not produce its items; `Encoded::items` can, so it
#: is claimed and every one of its cells now agrees with Django except the two
#: on `STILL_DIVERGENT`. Its `PRE_FIX` rows stay in the table and are what
#: `TestPreFixIsNotTheCurrentBuild` reads to prove the move happened.
CLAIMED = [
    "LenZeroWithAttrs",
    "BoolFalseWithAttrs",
    "LenZeroWithAttrsAndIter",
    "DunderStrWithAttrs",
    "LenTwoBoolFalseWithAttrs",
]

#: The shapes no gate here admits. None may move.
UNCHANGED = ["LenZeroNoAttrs", "TruthyWithAttrs", "LenZeroPrivateOnly"]

#: Every cell, one per consumer of the six facts above. Kept in the order
#: `scratch/sweep_2478.py` emits, so the recorded table below lines up.
CELLS = [
    "{% if p %}T{% else %}F{% endif %}",  # if
    "{% if not p %}T{% else %}F{% endif %}",  # if-not
    "{% if p and 1 %}T{% else %}F{% endif %}",  # if-and
    "{% if p or 0 %}T{% else %}F{% endif %}",  # if-or
    "{% with q=p %}{% if q %}T{% else %}F{% endif %}{% endwith %}",  # with-if
    "{% firstof p 'FB' %}",  # firstof
    "{{ p|yesno }}",  # yesno
    "{{ p|yesno:'Y,N,M' }}",  # yesno3
    "{{ p|default:'D' }}",  # default
    "{{ p|default_if_none:'D' }}",  # default_if_none
    "{{ p|length }}",  # length
    "{% for x in p %}[{{ x }}]{% empty %}E{% endfor %}",  # for
    "{% for k, v in p %}[{{ k }}={{ v }}]{% empty %}E{% endfor %}",  # for-unpack
    "{{ p }}",  # bare
    "{{ p.a }}",  # attr-a
    "{{ p.b }}",  # attr-b
    "{{ p.zzz }}",  # attr-missing
    "{{ p._a }}",  # attr-private
    "{{ d.p.a }}",  # nested-attr
    "{{ p.0 }}",  # attr-index
    "{{ p.items }}",  # items
    "{% for k, v in p.items %}[{{ k }}={{ v }}]{% empty %}E{% endfor %}",  # for-items
    "{{ p.keys }}",  # keys
    "{{ p|join:',' }}",  # join
    "{{ p|first }}",  # first
    "{{ p|last }}",  # last
    "{{ p|pprint }}",  # pprint
    "{{ p|stringformat:'r' }}",  # stringformat-r
    "{{ p|stringformat:'s' }}",  # stringformat-s
    "{{ p|safeseq }}",  # safeseq
    "{{ p|escapeseq }}",  # escapeseq
    "{{ p|unordered_list }}",  # unordered_list
    "{{ p|make_list }}",  # make_list
    "{{ p|dictsort:'a' }}",  # dictsort
    "{{ p|slice:':1' }}",  # slice
    "{{ p|add:1 }}",  # add
    "{{ p|json_script:'x' }}",  # json_script
    "{% if 'a' in p %}T{% else %}F{% endif %}",  # in
    "{% if p == None %}T{% else %}F{% endif %}",  # eq-none
    "{% for x in rows %}{% if x %}T{% else %}F{% endif %}{% endfor %}",  # in-list
    "{{ d.p }}",  # nested-bare
    "{% cycle p 'z' %}",  # cycle
    "{{ p|linebreaks }}",  # linebreaks
    "{{ p|striptags }}",  # striptags
    "{{ p|lower }}",  # lower
]

#: djust's answer for every `(shape, cell)` on the build immediately BEFORE
#: this change — captured by `scratch/run_capture.py`, which REVERTS the
#: reorder, rebuilds the crate (asserting the `.so` mtime advanced), imports
#: THIS module under THIS name so `str(o)`'s module prefix matches, records
#: the corpus, then restores and rebuilds. A measurement, not a
#: transcription — and not a seed from the current build, which is what
#: `TestThePreFixTableIsNOTVacuous` proves.
#:
#: Several of these still diverge from Django and are SUPPOSED to; that is
#: why the unchanged shapes are compared against this rather than against
#: Django.
PRE_FIX: dict[tuple[str, str], str] = {
    ("BoolFalseWithAttrs", "{% cycle p 'z' %}"): "{&#x27;a&#x27;: 1}",
    ("BoolFalseWithAttrs", "{% firstof p 'FB' %}"): "{&#x27;a&#x27;: 1}",
    (
        "BoolFalseWithAttrs",
        "{% for k, v in p %}[{{ k }}={{ v }}]{% empty %}E{% endfor %}",
    ): "<<REFUSED>>",
    (
        "BoolFalseWithAttrs",
        "{% for k, v in p.items %}[{{ k }}={{ v }}]{% empty %}E{% endfor %}",
    ): "[a=1]",
    ("BoolFalseWithAttrs", "{% for x in p %}[{{ x }}]{% empty %}E{% endfor %}"): "[a]",
    ("BoolFalseWithAttrs", "{% for x in rows %}{% if x %}T{% else %}F{% endif %}{% endfor %}"): "T",
    ("BoolFalseWithAttrs", "{% if 'a' in p %}T{% else %}F{% endif %}"): "T",
    ("BoolFalseWithAttrs", "{% if not p %}T{% else %}F{% endif %}"): "F",
    ("BoolFalseWithAttrs", "{% if p %}T{% else %}F{% endif %}"): "T",
    ("BoolFalseWithAttrs", "{% if p == None %}T{% else %}F{% endif %}"): "F",
    ("BoolFalseWithAttrs", "{% if p and 1 %}T{% else %}F{% endif %}"): "T",
    ("BoolFalseWithAttrs", "{% if p or 0 %}T{% else %}F{% endif %}"): "T",
    ("BoolFalseWithAttrs", "{% with q=p %}{% if q %}T{% else %}F{% endif %}{% endwith %}"): "T",
    ("BoolFalseWithAttrs", "{{ d.p }}"): "{&#x27;a&#x27;: 1}",
    ("BoolFalseWithAttrs", "{{ d.p.a }}"): "1",
    ("BoolFalseWithAttrs", "{{ p }}"): "{&#x27;a&#x27;: 1}",
    ("BoolFalseWithAttrs", "{{ p.0 }}"): "",
    ("BoolFalseWithAttrs", "{{ p._a }}"): "<<REFUSED>>",
    ("BoolFalseWithAttrs", "{{ p.a }}"): "1",
    ("BoolFalseWithAttrs", "{{ p.b }}"): "",
    ("BoolFalseWithAttrs", "{{ p.items }}"): "dict_items([(&#x27;a&#x27;, 1)])",
    ("BoolFalseWithAttrs", "{{ p.keys }}"): "dict_keys([&#x27;a&#x27;])",
    ("BoolFalseWithAttrs", "{{ p.zzz }}"): "",
    ("BoolFalseWithAttrs", "{{ p|add:1 }}"): "",
    ("BoolFalseWithAttrs", "{{ p|default:'D' }}"): "{&#x27;a&#x27;: 1}",
    ("BoolFalseWithAttrs", "{{ p|default_if_none:'D' }}"): "{&#x27;a&#x27;: 1}",
    ("BoolFalseWithAttrs", "{{ p|dictsort:'a' }}"): "",
    ("BoolFalseWithAttrs", "{{ p|escapeseq }}"): "[&#x27;a&#x27;]",
    ("BoolFalseWithAttrs", "{{ p|first }}"): "<<REFUSED>>",
    ("BoolFalseWithAttrs", "{{ p|join:',' }}"): "a",
    (
        "BoolFalseWithAttrs",
        "{{ p|json_script:'x' }}",
    ): '<script id="x" type="application/json">{"a": 1}</script>',
    ("BoolFalseWithAttrs", "{{ p|last }}"): "<<REFUSED>>",
    ("BoolFalseWithAttrs", "{{ p|length }}"): "1",
    ("BoolFalseWithAttrs", "{{ p|linebreaks }}"): "<p>{&#x27;a&#x27;: 1}</p>",
    ("BoolFalseWithAttrs", "{{ p|lower }}"): "{&#x27;a&#x27;: 1}",
    (
        "BoolFalseWithAttrs",
        "{{ p|make_list }}",
    ): "[&#x27;{&#x27;, &quot;&#x27;&quot;, &#x27;a&#x27;, &quot;&#x27;&quot;, &#x27;:&#x27;, &#x27; &#x27;, &#x27;1&#x27;, &#x27;}&#x27;]",
    ("BoolFalseWithAttrs", "{{ p|pprint }}"): "{&#x27;a&#x27;: 1}",
    ("BoolFalseWithAttrs", "{{ p|safeseq }}"): "[&#x27;a&#x27;]",
    ("BoolFalseWithAttrs", "{{ p|slice:':1' }}"): "{&#x27;a&#x27;: 1}",
    ("BoolFalseWithAttrs", "{{ p|stringformat:'r' }}"): "{&#x27;a&#x27;: 1}",
    ("BoolFalseWithAttrs", "{{ p|stringformat:'s' }}"): "{&#x27;a&#x27;: 1}",
    ("BoolFalseWithAttrs", "{{ p|striptags }}"): "{&#x27;a&#x27;: 1}",
    ("BoolFalseWithAttrs", "{{ p|unordered_list }}"): "\t<li>a</li>",
    ("BoolFalseWithAttrs", "{{ p|yesno }}"): "yes",
    ("BoolFalseWithAttrs", "{{ p|yesno:'Y,N,M' }}"): "Y",
    ("DunderStrWithAttrs", "{% cycle p 'z' %}"): "{&#x27;a&#x27;: 1}",
    ("DunderStrWithAttrs", "{% firstof p 'FB' %}"): "{&#x27;a&#x27;: 1}",
    (
        "DunderStrWithAttrs",
        "{% for k, v in p %}[{{ k }}={{ v }}]{% empty %}E{% endfor %}",
    ): "<<REFUSED>>",
    (
        "DunderStrWithAttrs",
        "{% for k, v in p.items %}[{{ k }}={{ v }}]{% empty %}E{% endfor %}",
    ): "[a=1]",
    ("DunderStrWithAttrs", "{% for x in p %}[{{ x }}]{% empty %}E{% endfor %}"): "[a]",
    ("DunderStrWithAttrs", "{% for x in rows %}{% if x %}T{% else %}F{% endif %}{% endfor %}"): "T",
    ("DunderStrWithAttrs", "{% if 'a' in p %}T{% else %}F{% endif %}"): "T",
    ("DunderStrWithAttrs", "{% if not p %}T{% else %}F{% endif %}"): "F",
    ("DunderStrWithAttrs", "{% if p %}T{% else %}F{% endif %}"): "T",
    ("DunderStrWithAttrs", "{% if p == None %}T{% else %}F{% endif %}"): "F",
    ("DunderStrWithAttrs", "{% if p and 1 %}T{% else %}F{% endif %}"): "T",
    ("DunderStrWithAttrs", "{% if p or 0 %}T{% else %}F{% endif %}"): "T",
    ("DunderStrWithAttrs", "{% with q=p %}{% if q %}T{% else %}F{% endif %}{% endwith %}"): "T",
    ("DunderStrWithAttrs", "{{ d.p }}"): "{&#x27;a&#x27;: 1}",
    ("DunderStrWithAttrs", "{{ d.p.a }}"): "1",
    ("DunderStrWithAttrs", "{{ p }}"): "{&#x27;a&#x27;: 1}",
    ("DunderStrWithAttrs", "{{ p.0 }}"): "",
    ("DunderStrWithAttrs", "{{ p._a }}"): "<<REFUSED>>",
    ("DunderStrWithAttrs", "{{ p.a }}"): "1",
    ("DunderStrWithAttrs", "{{ p.b }}"): "",
    ("DunderStrWithAttrs", "{{ p.items }}"): "dict_items([(&#x27;a&#x27;, 1)])",
    ("DunderStrWithAttrs", "{{ p.keys }}"): "dict_keys([&#x27;a&#x27;])",
    ("DunderStrWithAttrs", "{{ p.zzz }}"): "",
    ("DunderStrWithAttrs", "{{ p|add:1 }}"): "",
    ("DunderStrWithAttrs", "{{ p|default:'D' }}"): "{&#x27;a&#x27;: 1}",
    ("DunderStrWithAttrs", "{{ p|default_if_none:'D' }}"): "{&#x27;a&#x27;: 1}",
    ("DunderStrWithAttrs", "{{ p|dictsort:'a' }}"): "",
    ("DunderStrWithAttrs", "{{ p|escapeseq }}"): "[&#x27;a&#x27;]",
    ("DunderStrWithAttrs", "{{ p|first }}"): "<<REFUSED>>",
    ("DunderStrWithAttrs", "{{ p|join:',' }}"): "a",
    (
        "DunderStrWithAttrs",
        "{{ p|json_script:'x' }}",
    ): '<script id="x" type="application/json">{"a": 1}</script>',
    ("DunderStrWithAttrs", "{{ p|last }}"): "<<REFUSED>>",
    ("DunderStrWithAttrs", "{{ p|length }}"): "1",
    ("DunderStrWithAttrs", "{{ p|linebreaks }}"): "<p>{&#x27;a&#x27;: 1}</p>",
    ("DunderStrWithAttrs", "{{ p|lower }}"): "{&#x27;a&#x27;: 1}",
    (
        "DunderStrWithAttrs",
        "{{ p|make_list }}",
    ): "[&#x27;{&#x27;, &quot;&#x27;&quot;, &#x27;a&#x27;, &quot;&#x27;&quot;, &#x27;:&#x27;, &#x27; &#x27;, &#x27;1&#x27;, &#x27;}&#x27;]",
    ("DunderStrWithAttrs", "{{ p|pprint }}"): "{&#x27;a&#x27;: 1}",
    ("DunderStrWithAttrs", "{{ p|safeseq }}"): "[&#x27;a&#x27;]",
    ("DunderStrWithAttrs", "{{ p|slice:':1' }}"): "{&#x27;a&#x27;: 1}",
    ("DunderStrWithAttrs", "{{ p|stringformat:'r' }}"): "{&#x27;a&#x27;: 1}",
    ("DunderStrWithAttrs", "{{ p|stringformat:'s' }}"): "{&#x27;a&#x27;: 1}",
    ("DunderStrWithAttrs", "{{ p|striptags }}"): "{&#x27;a&#x27;: 1}",
    ("DunderStrWithAttrs", "{{ p|unordered_list }}"): "\t<li>a</li>",
    ("DunderStrWithAttrs", "{{ p|yesno }}"): "yes",
    ("DunderStrWithAttrs", "{{ p|yesno:'Y,N,M' }}"): "Y",
    ("LenTwoBoolFalseWithAttrs", "{% cycle p 'z' %}"): "{&#x27;a&#x27;: 1}",
    ("LenTwoBoolFalseWithAttrs", "{% firstof p 'FB' %}"): "{&#x27;a&#x27;: 1}",
    (
        "LenTwoBoolFalseWithAttrs",
        "{% for k, v in p %}[{{ k }}={{ v }}]{% empty %}E{% endfor %}",
    ): "<<REFUSED>>",
    (
        "LenTwoBoolFalseWithAttrs",
        "{% for k, v in p.items %}[{{ k }}={{ v }}]{% empty %}E{% endfor %}",
    ): "[a=1]",
    ("LenTwoBoolFalseWithAttrs", "{% for x in p %}[{{ x }}]{% empty %}E{% endfor %}"): "[a]",
    (
        "LenTwoBoolFalseWithAttrs",
        "{% for x in rows %}{% if x %}T{% else %}F{% endif %}{% endfor %}",
    ): "T",
    ("LenTwoBoolFalseWithAttrs", "{% if 'a' in p %}T{% else %}F{% endif %}"): "T",
    ("LenTwoBoolFalseWithAttrs", "{% if not p %}T{% else %}F{% endif %}"): "F",
    ("LenTwoBoolFalseWithAttrs", "{% if p %}T{% else %}F{% endif %}"): "T",
    ("LenTwoBoolFalseWithAttrs", "{% if p == None %}T{% else %}F{% endif %}"): "F",
    ("LenTwoBoolFalseWithAttrs", "{% if p and 1 %}T{% else %}F{% endif %}"): "T",
    ("LenTwoBoolFalseWithAttrs", "{% if p or 0 %}T{% else %}F{% endif %}"): "T",
    (
        "LenTwoBoolFalseWithAttrs",
        "{% with q=p %}{% if q %}T{% else %}F{% endif %}{% endwith %}",
    ): "T",
    ("LenTwoBoolFalseWithAttrs", "{{ d.p }}"): "{&#x27;a&#x27;: 1}",
    ("LenTwoBoolFalseWithAttrs", "{{ d.p.a }}"): "1",
    ("LenTwoBoolFalseWithAttrs", "{{ p }}"): "{&#x27;a&#x27;: 1}",
    ("LenTwoBoolFalseWithAttrs", "{{ p.0 }}"): "",
    ("LenTwoBoolFalseWithAttrs", "{{ p._a }}"): "<<REFUSED>>",
    ("LenTwoBoolFalseWithAttrs", "{{ p.a }}"): "1",
    ("LenTwoBoolFalseWithAttrs", "{{ p.b }}"): "",
    ("LenTwoBoolFalseWithAttrs", "{{ p.items }}"): "dict_items([(&#x27;a&#x27;, 1)])",
    ("LenTwoBoolFalseWithAttrs", "{{ p.keys }}"): "dict_keys([&#x27;a&#x27;])",
    ("LenTwoBoolFalseWithAttrs", "{{ p.zzz }}"): "",
    ("LenTwoBoolFalseWithAttrs", "{{ p|add:1 }}"): "",
    ("LenTwoBoolFalseWithAttrs", "{{ p|default:'D' }}"): "{&#x27;a&#x27;: 1}",
    ("LenTwoBoolFalseWithAttrs", "{{ p|default_if_none:'D' }}"): "{&#x27;a&#x27;: 1}",
    ("LenTwoBoolFalseWithAttrs", "{{ p|dictsort:'a' }}"): "",
    ("LenTwoBoolFalseWithAttrs", "{{ p|escapeseq }}"): "[&#x27;a&#x27;]",
    ("LenTwoBoolFalseWithAttrs", "{{ p|first }}"): "<<REFUSED>>",
    ("LenTwoBoolFalseWithAttrs", "{{ p|join:',' }}"): "a",
    (
        "LenTwoBoolFalseWithAttrs",
        "{{ p|json_script:'x' }}",
    ): '<script id="x" type="application/json">{"a": 1}</script>',
    ("LenTwoBoolFalseWithAttrs", "{{ p|last }}"): "<<REFUSED>>",
    ("LenTwoBoolFalseWithAttrs", "{{ p|length }}"): "1",
    ("LenTwoBoolFalseWithAttrs", "{{ p|linebreaks }}"): "<p>{&#x27;a&#x27;: 1}</p>",
    ("LenTwoBoolFalseWithAttrs", "{{ p|lower }}"): "{&#x27;a&#x27;: 1}",
    (
        "LenTwoBoolFalseWithAttrs",
        "{{ p|make_list }}",
    ): "[&#x27;{&#x27;, &quot;&#x27;&quot;, &#x27;a&#x27;, &quot;&#x27;&quot;, &#x27;:&#x27;, &#x27; &#x27;, &#x27;1&#x27;, &#x27;}&#x27;]",
    ("LenTwoBoolFalseWithAttrs", "{{ p|pprint }}"): "{&#x27;a&#x27;: 1}",
    ("LenTwoBoolFalseWithAttrs", "{{ p|safeseq }}"): "[&#x27;a&#x27;]",
    ("LenTwoBoolFalseWithAttrs", "{{ p|slice:':1' }}"): "{&#x27;a&#x27;: 1}",
    ("LenTwoBoolFalseWithAttrs", "{{ p|stringformat:'r' }}"): "{&#x27;a&#x27;: 1}",
    ("LenTwoBoolFalseWithAttrs", "{{ p|stringformat:'s' }}"): "{&#x27;a&#x27;: 1}",
    ("LenTwoBoolFalseWithAttrs", "{{ p|striptags }}"): "{&#x27;a&#x27;: 1}",
    ("LenTwoBoolFalseWithAttrs", "{{ p|unordered_list }}"): "\t<li>a</li>",
    ("LenTwoBoolFalseWithAttrs", "{{ p|yesno }}"): "yes",
    ("LenTwoBoolFalseWithAttrs", "{{ p|yesno:'Y,N,M' }}"): "Y",
    (
        "LenZeroNoAttrs",
        "{% cycle p 'z' %}",
    ): "&lt;test_falsy_with_attributes_2478.LenZeroNoAttrs object at 0xADDR&gt;",
    ("LenZeroNoAttrs", "{% firstof p 'FB' %}"): "FB",
    ("LenZeroNoAttrs", "{% for k, v in p %}[{{ k }}={{ v }}]{% empty %}E{% endfor %}"): "E",
    ("LenZeroNoAttrs", "{% for k, v in p.items %}[{{ k }}={{ v }}]{% empty %}E{% endfor %}"): "E",
    ("LenZeroNoAttrs", "{% for x in p %}[{{ x }}]{% empty %}E{% endfor %}"): "E",
    ("LenZeroNoAttrs", "{% for x in rows %}{% if x %}T{% else %}F{% endif %}{% endfor %}"): "F",
    ("LenZeroNoAttrs", "{% if 'a' in p %}T{% else %}F{% endif %}"): "F",
    ("LenZeroNoAttrs", "{% if not p %}T{% else %}F{% endif %}"): "T",
    ("LenZeroNoAttrs", "{% if p %}T{% else %}F{% endif %}"): "F",
    ("LenZeroNoAttrs", "{% if p == None %}T{% else %}F{% endif %}"): "F",
    ("LenZeroNoAttrs", "{% if p and 1 %}T{% else %}F{% endif %}"): "F",
    ("LenZeroNoAttrs", "{% if p or 0 %}T{% else %}F{% endif %}"): "F",
    ("LenZeroNoAttrs", "{% with q=p %}{% if q %}T{% else %}F{% endif %}{% endwith %}"): "F",
    (
        "LenZeroNoAttrs",
        "{{ d.p }}",
    ): "&lt;test_falsy_with_attributes_2478.LenZeroNoAttrs object at 0xADDR&gt;",
    ("LenZeroNoAttrs", "{{ d.p.a }}"): "",
    (
        "LenZeroNoAttrs",
        "{{ p }}",
    ): "&lt;test_falsy_with_attributes_2478.LenZeroNoAttrs object at 0xADDR&gt;",
    ("LenZeroNoAttrs", "{{ p.0 }}"): "",
    ("LenZeroNoAttrs", "{{ p._a }}"): "<<REFUSED>>",
    ("LenZeroNoAttrs", "{{ p.a }}"): "",
    ("LenZeroNoAttrs", "{{ p.b }}"): "",
    ("LenZeroNoAttrs", "{{ p.items }}"): "",
    ("LenZeroNoAttrs", "{{ p.keys }}"): "",
    ("LenZeroNoAttrs", "{{ p.zzz }}"): "",
    ("LenZeroNoAttrs", "{{ p|add:1 }}"): "",
    ("LenZeroNoAttrs", "{{ p|default:'D' }}"): "D",
    (
        "LenZeroNoAttrs",
        "{{ p|default_if_none:'D' }}",
    ): "&lt;test_falsy_with_attributes_2478.LenZeroNoAttrs object at 0xADDR&gt;",
    ("LenZeroNoAttrs", "{{ p|dictsort:'a' }}"): "",
    ("LenZeroNoAttrs", "{{ p|escapeseq }}"): "<<REFUSED>>",
    ("LenZeroNoAttrs", "{{ p|first }}"): "<<REFUSED>>",
    (
        "LenZeroNoAttrs",
        "{{ p|join:',' }}",
    ): "&lt;test_falsy_with_attributes_2478.LenZeroNoAttrs object at 0xADDR&gt;",
    (
        "LenZeroNoAttrs",
        "{{ p|json_script:'x' }}",
    ): '<script id="x" type="application/json">"\\u003Ctest_falsy_with_attributes_2478.LenZeroNoAttrs object at 0xADDR\\u003E"</script>',
    ("LenZeroNoAttrs", "{{ p|last }}"): "<<REFUSED>>",
    ("LenZeroNoAttrs", "{{ p|length }}"): "0",
    (
        "LenZeroNoAttrs",
        "{{ p|linebreaks }}",
    ): "<p>&lt;test_falsy_with_attributes_2478.LenZeroNoAttrs object at 0xADDR&gt;</p>",
    (
        "LenZeroNoAttrs",
        "{{ p|lower }}",
    ): "&lt;test_falsy_with_attributes_2478.lenzeronoattrs object at 0xADDR&gt;",
    (
        "LenZeroNoAttrs",
        "{{ p|make_list }}",
    ): "[&#x27;&lt;&#x27;, &#x27;t&#x27;, &#x27;e&#x27;, &#x27;s&#x27;, &#x27;t&#x27;, &#x27;_&#x27;, &#x27;f&#x27;, &#x27;a&#x27;, &#x27;l&#x27;, &#x27;s&#x27;, &#x27;y&#x27;, &#x27;_&#x27;, &#x27;w&#x27;, &#x27;i&#x27;, &#x27;t&#x27;, &#x27;h&#x27;, &#x27;_&#x27;, &#x27;a&#x27;, &#x27;t&#x27;, &#x27;t&#x27;, &#x27;r&#x27;, &#x27;i&#x27;, &#x27;b&#x27;, &#x27;u&#x27;, &#x27;t&#x27;, &#x27;e&#x27;, &#x27;s&#x27;, &#x27;_&#x27;, &#x27;2&#x27;, &#x27;4&#x27;, &#x27;7&#x27;, &#x27;8&#x27;, &#x27;.&#x27;, &#x27;L&#x27;, &#x27;e&#x27;, &#x27;n&#x27;, &#x27;Z&#x27;, &#x27;e&#x27;, &#x27;r&#x27;, &#x27;o&#x27;, &#x27;N&#x27;, &#x27;o&#x27;, &#x27;A&#x27;, &#x27;t&#x27;, &#x27;t&#x27;, &#x27;r&#x27;, &#x27;s&#x27;, &#x27; &#x27;, &#x27;o&#x27;, &#x27;b&#x27;, &#x27;j&#x27;, &#x27;e&#x27;, &#x27;c&#x27;, &#x27;t&#x27;, &#x27; &#x27;, &#x27;a&#x27;, &#x27;t&#x27;, &#x27; &#x27;, &#x27;0&#x27;, &#x27;x&#x27;, &#x27;1&#x27;, &#x27;0&#x27;, &#x27;9&#x27;, &#x27;2&#x27;, &#x27;8&#x27;, &#x27;a&#x27;, &#x27;e&#x27;, &#x27;a&#x27;, &#x27;0&#x27;, &#x27;&gt;&#x27;]",
    (
        "LenZeroNoAttrs",
        "{{ p|pprint }}",
    ): "&lt;test_falsy_with_attributes_2478.LenZeroNoAttrs object at 0xADDR&gt;",
    ("LenZeroNoAttrs", "{{ p|safeseq }}"): "<<REFUSED>>",
    (
        "LenZeroNoAttrs",
        "{{ p|slice:':1' }}",
    ): "&lt;test_falsy_with_attributes_2478.LenZeroNoAttrs object at 0xADDR&gt;",
    (
        "LenZeroNoAttrs",
        "{{ p|stringformat:'r' }}",
    ): "&lt;test_falsy_with_attributes_2478.LenZeroNoAttrs object at 0xADDR&gt;",
    (
        "LenZeroNoAttrs",
        "{{ p|stringformat:'s' }}",
    ): "&lt;test_falsy_with_attributes_2478.LenZeroNoAttrs object at 0xADDR&gt;",
    ("LenZeroNoAttrs", "{{ p|striptags }}"): "",
    ("LenZeroNoAttrs", "{{ p|unordered_list }}"): "<<REFUSED>>",
    ("LenZeroNoAttrs", "{{ p|yesno }}"): "no",
    ("LenZeroNoAttrs", "{{ p|yesno:'Y,N,M' }}"): "N",
    (
        "LenZeroPrivateOnly",
        "{% cycle p 'z' %}",
    ): "&lt;test_falsy_with_attributes_2478.LenZeroPrivateOnly object at 0xADDR&gt;",
    ("LenZeroPrivateOnly", "{% firstof p 'FB' %}"): "FB",
    ("LenZeroPrivateOnly", "{% for k, v in p %}[{{ k }}={{ v }}]{% empty %}E{% endfor %}"): "E",
    (
        "LenZeroPrivateOnly",
        "{% for k, v in p.items %}[{{ k }}={{ v }}]{% empty %}E{% endfor %}",
    ): "E",
    ("LenZeroPrivateOnly", "{% for x in p %}[{{ x }}]{% empty %}E{% endfor %}"): "E",
    ("LenZeroPrivateOnly", "{% for x in rows %}{% if x %}T{% else %}F{% endif %}{% endfor %}"): "F",
    ("LenZeroPrivateOnly", "{% if 'a' in p %}T{% else %}F{% endif %}"): "F",
    ("LenZeroPrivateOnly", "{% if not p %}T{% else %}F{% endif %}"): "T",
    ("LenZeroPrivateOnly", "{% if p %}T{% else %}F{% endif %}"): "F",
    ("LenZeroPrivateOnly", "{% if p == None %}T{% else %}F{% endif %}"): "F",
    ("LenZeroPrivateOnly", "{% if p and 1 %}T{% else %}F{% endif %}"): "F",
    ("LenZeroPrivateOnly", "{% if p or 0 %}T{% else %}F{% endif %}"): "F",
    ("LenZeroPrivateOnly", "{% with q=p %}{% if q %}T{% else %}F{% endif %}{% endwith %}"): "F",
    (
        "LenZeroPrivateOnly",
        "{{ d.p }}",
    ): "&lt;test_falsy_with_attributes_2478.LenZeroPrivateOnly object at 0xADDR&gt;",
    ("LenZeroPrivateOnly", "{{ d.p.a }}"): "",
    (
        "LenZeroPrivateOnly",
        "{{ p }}",
    ): "&lt;test_falsy_with_attributes_2478.LenZeroPrivateOnly object at 0xADDR&gt;",
    ("LenZeroPrivateOnly", "{{ p.0 }}"): "",
    ("LenZeroPrivateOnly", "{{ p._a }}"): "<<REFUSED>>",
    ("LenZeroPrivateOnly", "{{ p.a }}"): "",
    ("LenZeroPrivateOnly", "{{ p.b }}"): "",
    ("LenZeroPrivateOnly", "{{ p.items }}"): "",
    ("LenZeroPrivateOnly", "{{ p.keys }}"): "",
    ("LenZeroPrivateOnly", "{{ p.zzz }}"): "",
    ("LenZeroPrivateOnly", "{{ p|add:1 }}"): "",
    ("LenZeroPrivateOnly", "{{ p|default:'D' }}"): "D",
    (
        "LenZeroPrivateOnly",
        "{{ p|default_if_none:'D' }}",
    ): "&lt;test_falsy_with_attributes_2478.LenZeroPrivateOnly object at 0xADDR&gt;",
    ("LenZeroPrivateOnly", "{{ p|dictsort:'a' }}"): "",
    ("LenZeroPrivateOnly", "{{ p|escapeseq }}"): "<<REFUSED>>",
    ("LenZeroPrivateOnly", "{{ p|first }}"): "<<REFUSED>>",
    (
        "LenZeroPrivateOnly",
        "{{ p|join:',' }}",
    ): "&lt;test_falsy_with_attributes_2478.LenZeroPrivateOnly object at 0xADDR&gt;",
    (
        "LenZeroPrivateOnly",
        "{{ p|json_script:'x' }}",
    ): '<script id="x" type="application/json">"\\u003Ctest_falsy_with_attributes_2478.LenZeroPrivateOnly object at 0xADDR\\u003E"</script>',
    ("LenZeroPrivateOnly", "{{ p|last }}"): "<<REFUSED>>",
    ("LenZeroPrivateOnly", "{{ p|length }}"): "0",
    (
        "LenZeroPrivateOnly",
        "{{ p|linebreaks }}",
    ): "<p>&lt;test_falsy_with_attributes_2478.LenZeroPrivateOnly object at 0xADDR&gt;</p>",
    (
        "LenZeroPrivateOnly",
        "{{ p|lower }}",
    ): "&lt;test_falsy_with_attributes_2478.lenzeroprivateonly object at 0xADDR&gt;",
    (
        "LenZeroPrivateOnly",
        "{{ p|make_list }}",
    ): "[&#x27;&lt;&#x27;, &#x27;t&#x27;, &#x27;e&#x27;, &#x27;s&#x27;, &#x27;t&#x27;, &#x27;_&#x27;, &#x27;f&#x27;, &#x27;a&#x27;, &#x27;l&#x27;, &#x27;s&#x27;, &#x27;y&#x27;, &#x27;_&#x27;, &#x27;w&#x27;, &#x27;i&#x27;, &#x27;t&#x27;, &#x27;h&#x27;, &#x27;_&#x27;, &#x27;a&#x27;, &#x27;t&#x27;, &#x27;t&#x27;, &#x27;r&#x27;, &#x27;i&#x27;, &#x27;b&#x27;, &#x27;u&#x27;, &#x27;t&#x27;, &#x27;e&#x27;, &#x27;s&#x27;, &#x27;_&#x27;, &#x27;2&#x27;, &#x27;4&#x27;, &#x27;7&#x27;, &#x27;8&#x27;, &#x27;.&#x27;, &#x27;L&#x27;, &#x27;e&#x27;, &#x27;n&#x27;, &#x27;Z&#x27;, &#x27;e&#x27;, &#x27;r&#x27;, &#x27;o&#x27;, &#x27;P&#x27;, &#x27;r&#x27;, &#x27;i&#x27;, &#x27;v&#x27;, &#x27;a&#x27;, &#x27;t&#x27;, &#x27;e&#x27;, &#x27;O&#x27;, &#x27;n&#x27;, &#x27;l&#x27;, &#x27;y&#x27;, &#x27; &#x27;, &#x27;o&#x27;, &#x27;b&#x27;, &#x27;j&#x27;, &#x27;e&#x27;, &#x27;c&#x27;, &#x27;t&#x27;, &#x27; &#x27;, &#x27;a&#x27;, &#x27;t&#x27;, &#x27; &#x27;, &#x27;0&#x27;, &#x27;x&#x27;, &#x27;1&#x27;, &#x27;0&#x27;, &#x27;9&#x27;, &#x27;2&#x27;, &#x27;8&#x27;, &#x27;a&#x27;, &#x27;e&#x27;, &#x27;a&#x27;, &#x27;0&#x27;, &#x27;&gt;&#x27;]",
    (
        "LenZeroPrivateOnly",
        "{{ p|pprint }}",
    ): "&lt;test_falsy_with_attributes_2478.LenZeroPrivateOnly object at 0xADDR&gt;",
    ("LenZeroPrivateOnly", "{{ p|safeseq }}"): "<<REFUSED>>",
    (
        "LenZeroPrivateOnly",
        "{{ p|slice:':1' }}",
    ): "&lt;test_falsy_with_attributes_2478.LenZeroPrivateOnly object at 0xADDR&gt;",
    (
        "LenZeroPrivateOnly",
        "{{ p|stringformat:'r' }}",
    ): "&lt;test_falsy_with_attributes_2478.LenZeroPrivateOnly object at 0xADDR&gt;",
    (
        "LenZeroPrivateOnly",
        "{{ p|stringformat:'s' }}",
    ): "&lt;test_falsy_with_attributes_2478.LenZeroPrivateOnly object at 0xADDR&gt;",
    ("LenZeroPrivateOnly", "{{ p|striptags }}"): "",
    ("LenZeroPrivateOnly", "{{ p|unordered_list }}"): "<<REFUSED>>",
    ("LenZeroPrivateOnly", "{{ p|yesno }}"): "no",
    ("LenZeroPrivateOnly", "{{ p|yesno:'Y,N,M' }}"): "N",
    ("LenZeroWithAttrs", "{% cycle p 'z' %}"): "{&#x27;a&#x27;: 1, &#x27;b&#x27;: &#x27;x&#x27;}",
    (
        "LenZeroWithAttrs",
        "{% firstof p 'FB' %}",
    ): "{&#x27;a&#x27;: 1, &#x27;b&#x27;: &#x27;x&#x27;}",
    (
        "LenZeroWithAttrs",
        "{% for k, v in p %}[{{ k }}={{ v }}]{% empty %}E{% endfor %}",
    ): "<<REFUSED>>",
    (
        "LenZeroWithAttrs",
        "{% for k, v in p.items %}[{{ k }}={{ v }}]{% empty %}E{% endfor %}",
    ): "[a=1][b=x]",
    ("LenZeroWithAttrs", "{% for x in p %}[{{ x }}]{% empty %}E{% endfor %}"): "[a][b]",
    ("LenZeroWithAttrs", "{% for x in rows %}{% if x %}T{% else %}F{% endif %}{% endfor %}"): "T",
    ("LenZeroWithAttrs", "{% if 'a' in p %}T{% else %}F{% endif %}"): "T",
    ("LenZeroWithAttrs", "{% if not p %}T{% else %}F{% endif %}"): "F",
    ("LenZeroWithAttrs", "{% if p %}T{% else %}F{% endif %}"): "T",
    ("LenZeroWithAttrs", "{% if p == None %}T{% else %}F{% endif %}"): "F",
    ("LenZeroWithAttrs", "{% if p and 1 %}T{% else %}F{% endif %}"): "T",
    ("LenZeroWithAttrs", "{% if p or 0 %}T{% else %}F{% endif %}"): "T",
    ("LenZeroWithAttrs", "{% with q=p %}{% if q %}T{% else %}F{% endif %}{% endwith %}"): "T",
    ("LenZeroWithAttrs", "{{ d.p }}"): "{&#x27;a&#x27;: 1, &#x27;b&#x27;: &#x27;x&#x27;}",
    ("LenZeroWithAttrs", "{{ d.p.a }}"): "1",
    ("LenZeroWithAttrs", "{{ p }}"): "{&#x27;a&#x27;: 1, &#x27;b&#x27;: &#x27;x&#x27;}",
    ("LenZeroWithAttrs", "{{ p.0 }}"): "",
    ("LenZeroWithAttrs", "{{ p._a }}"): "<<REFUSED>>",
    ("LenZeroWithAttrs", "{{ p.a }}"): "1",
    ("LenZeroWithAttrs", "{{ p.b }}"): "x",
    (
        "LenZeroWithAttrs",
        "{{ p.items }}",
    ): "dict_items([(&#x27;a&#x27;, 1), (&#x27;b&#x27;, &#x27;x&#x27;)])",
    ("LenZeroWithAttrs", "{{ p.keys }}"): "dict_keys([&#x27;a&#x27;, &#x27;b&#x27;])",
    ("LenZeroWithAttrs", "{{ p.zzz }}"): "",
    ("LenZeroWithAttrs", "{{ p|add:1 }}"): "",
    ("LenZeroWithAttrs", "{{ p|default:'D' }}"): "{&#x27;a&#x27;: 1, &#x27;b&#x27;: &#x27;x&#x27;}",
    (
        "LenZeroWithAttrs",
        "{{ p|default_if_none:'D' }}",
    ): "{&#x27;a&#x27;: 1, &#x27;b&#x27;: &#x27;x&#x27;}",
    ("LenZeroWithAttrs", "{{ p|dictsort:'a' }}"): "",
    ("LenZeroWithAttrs", "{{ p|escapeseq }}"): "[&#x27;a&#x27;, &#x27;b&#x27;]",
    ("LenZeroWithAttrs", "{{ p|first }}"): "<<REFUSED>>",
    ("LenZeroWithAttrs", "{{ p|join:',' }}"): "a,b",
    (
        "LenZeroWithAttrs",
        "{{ p|json_script:'x' }}",
    ): '<script id="x" type="application/json">{"a": 1, "b": "x"}</script>',
    ("LenZeroWithAttrs", "{{ p|last }}"): "<<REFUSED>>",
    ("LenZeroWithAttrs", "{{ p|length }}"): "2",
    (
        "LenZeroWithAttrs",
        "{{ p|linebreaks }}",
    ): "<p>{&#x27;a&#x27;: 1, &#x27;b&#x27;: &#x27;x&#x27;}</p>",
    ("LenZeroWithAttrs", "{{ p|lower }}"): "{&#x27;a&#x27;: 1, &#x27;b&#x27;: &#x27;x&#x27;}",
    (
        "LenZeroWithAttrs",
        "{{ p|make_list }}",
    ): "[&#x27;{&#x27;, &quot;&#x27;&quot;, &#x27;a&#x27;, &quot;&#x27;&quot;, &#x27;:&#x27;, &#x27; &#x27;, &#x27;1&#x27;, &#x27;,&#x27;, &#x27; &#x27;, &quot;&#x27;&quot;, &#x27;b&#x27;, &quot;&#x27;&quot;, &#x27;:&#x27;, &#x27; &#x27;, &quot;&#x27;&quot;, &#x27;x&#x27;, &quot;&#x27;&quot;, &#x27;}&#x27;]",
    ("LenZeroWithAttrs", "{{ p|pprint }}"): "{&#x27;a&#x27;: 1, &#x27;b&#x27;: &#x27;x&#x27;}",
    ("LenZeroWithAttrs", "{{ p|safeseq }}"): "[&#x27;a&#x27;, &#x27;b&#x27;]",
    ("LenZeroWithAttrs", "{{ p|slice:':1' }}"): "{&#x27;a&#x27;: 1, &#x27;b&#x27;: &#x27;x&#x27;}",
    (
        "LenZeroWithAttrs",
        "{{ p|stringformat:'r' }}",
    ): "{&#x27;a&#x27;: 1, &#x27;b&#x27;: &#x27;x&#x27;}",
    (
        "LenZeroWithAttrs",
        "{{ p|stringformat:'s' }}",
    ): "{&#x27;a&#x27;: 1, &#x27;b&#x27;: &#x27;x&#x27;}",
    ("LenZeroWithAttrs", "{{ p|striptags }}"): "{&#x27;a&#x27;: 1, &#x27;b&#x27;: &#x27;x&#x27;}",
    ("LenZeroWithAttrs", "{{ p|unordered_list }}"): "\t<li>a</li>\n\t<li>b</li>",
    ("LenZeroWithAttrs", "{{ p|yesno }}"): "yes",
    ("LenZeroWithAttrs", "{{ p|yesno:'Y,N,M' }}"): "Y",
    ("LenZeroWithAttrsAndIter", "{% cycle p 'z' %}"): "{&#x27;a&#x27;: 1}",
    ("LenZeroWithAttrsAndIter", "{% firstof p 'FB' %}"): "{&#x27;a&#x27;: 1}",
    (
        "LenZeroWithAttrsAndIter",
        "{% for k, v in p %}[{{ k }}={{ v }}]{% empty %}E{% endfor %}",
    ): "<<REFUSED>>",
    (
        "LenZeroWithAttrsAndIter",
        "{% for k, v in p.items %}[{{ k }}={{ v }}]{% empty %}E{% endfor %}",
    ): "[a=1]",
    ("LenZeroWithAttrsAndIter", "{% for x in p %}[{{ x }}]{% empty %}E{% endfor %}"): "[a]",
    (
        "LenZeroWithAttrsAndIter",
        "{% for x in rows %}{% if x %}T{% else %}F{% endif %}{% endfor %}",
    ): "T",
    ("LenZeroWithAttrsAndIter", "{% if 'a' in p %}T{% else %}F{% endif %}"): "T",
    ("LenZeroWithAttrsAndIter", "{% if not p %}T{% else %}F{% endif %}"): "F",
    ("LenZeroWithAttrsAndIter", "{% if p %}T{% else %}F{% endif %}"): "T",
    ("LenZeroWithAttrsAndIter", "{% if p == None %}T{% else %}F{% endif %}"): "F",
    ("LenZeroWithAttrsAndIter", "{% if p and 1 %}T{% else %}F{% endif %}"): "T",
    ("LenZeroWithAttrsAndIter", "{% if p or 0 %}T{% else %}F{% endif %}"): "T",
    (
        "LenZeroWithAttrsAndIter",
        "{% with q=p %}{% if q %}T{% else %}F{% endif %}{% endwith %}",
    ): "T",
    ("LenZeroWithAttrsAndIter", "{{ d.p }}"): "{&#x27;a&#x27;: 1}",
    ("LenZeroWithAttrsAndIter", "{{ d.p.a }}"): "1",
    ("LenZeroWithAttrsAndIter", "{{ p }}"): "{&#x27;a&#x27;: 1}",
    ("LenZeroWithAttrsAndIter", "{{ p.0 }}"): "",
    ("LenZeroWithAttrsAndIter", "{{ p._a }}"): "<<REFUSED>>",
    ("LenZeroWithAttrsAndIter", "{{ p.a }}"): "1",
    ("LenZeroWithAttrsAndIter", "{{ p.b }}"): "",
    ("LenZeroWithAttrsAndIter", "{{ p.items }}"): "dict_items([(&#x27;a&#x27;, 1)])",
    ("LenZeroWithAttrsAndIter", "{{ p.keys }}"): "dict_keys([&#x27;a&#x27;])",
    ("LenZeroWithAttrsAndIter", "{{ p.zzz }}"): "",
    ("LenZeroWithAttrsAndIter", "{{ p|add:1 }}"): "",
    ("LenZeroWithAttrsAndIter", "{{ p|default:'D' }}"): "{&#x27;a&#x27;: 1}",
    ("LenZeroWithAttrsAndIter", "{{ p|default_if_none:'D' }}"): "{&#x27;a&#x27;: 1}",
    ("LenZeroWithAttrsAndIter", "{{ p|dictsort:'a' }}"): "",
    ("LenZeroWithAttrsAndIter", "{{ p|escapeseq }}"): "[&#x27;a&#x27;]",
    ("LenZeroWithAttrsAndIter", "{{ p|first }}"): "<<REFUSED>>",
    ("LenZeroWithAttrsAndIter", "{{ p|join:',' }}"): "a",
    (
        "LenZeroWithAttrsAndIter",
        "{{ p|json_script:'x' }}",
    ): '<script id="x" type="application/json">{"a": 1}</script>',
    ("LenZeroWithAttrsAndIter", "{{ p|last }}"): "<<REFUSED>>",
    ("LenZeroWithAttrsAndIter", "{{ p|length }}"): "1",
    ("LenZeroWithAttrsAndIter", "{{ p|linebreaks }}"): "<p>{&#x27;a&#x27;: 1}</p>",
    ("LenZeroWithAttrsAndIter", "{{ p|lower }}"): "{&#x27;a&#x27;: 1}",
    (
        "LenZeroWithAttrsAndIter",
        "{{ p|make_list }}",
    ): "[&#x27;{&#x27;, &quot;&#x27;&quot;, &#x27;a&#x27;, &quot;&#x27;&quot;, &#x27;:&#x27;, &#x27; &#x27;, &#x27;1&#x27;, &#x27;}&#x27;]",
    ("LenZeroWithAttrsAndIter", "{{ p|pprint }}"): "{&#x27;a&#x27;: 1}",
    ("LenZeroWithAttrsAndIter", "{{ p|safeseq }}"): "[&#x27;a&#x27;]",
    ("LenZeroWithAttrsAndIter", "{{ p|slice:':1' }}"): "{&#x27;a&#x27;: 1}",
    ("LenZeroWithAttrsAndIter", "{{ p|stringformat:'r' }}"): "{&#x27;a&#x27;: 1}",
    ("LenZeroWithAttrsAndIter", "{{ p|stringformat:'s' }}"): "{&#x27;a&#x27;: 1}",
    ("LenZeroWithAttrsAndIter", "{{ p|striptags }}"): "{&#x27;a&#x27;: 1}",
    ("LenZeroWithAttrsAndIter", "{{ p|unordered_list }}"): "\t<li>a</li>",
    ("LenZeroWithAttrsAndIter", "{{ p|yesno }}"): "yes",
    ("LenZeroWithAttrsAndIter", "{{ p|yesno:'Y,N,M' }}"): "Y",
    ("TruthyWithAttrs", "{% cycle p 'z' %}"): "{&#x27;a&#x27;: 1}",
    ("TruthyWithAttrs", "{% firstof p 'FB' %}"): "{&#x27;a&#x27;: 1}",
    (
        "TruthyWithAttrs",
        "{% for k, v in p %}[{{ k }}={{ v }}]{% empty %}E{% endfor %}",
    ): "<<REFUSED>>",
    (
        "TruthyWithAttrs",
        "{% for k, v in p.items %}[{{ k }}={{ v }}]{% empty %}E{% endfor %}",
    ): "[a=1]",
    ("TruthyWithAttrs", "{% for x in p %}[{{ x }}]{% empty %}E{% endfor %}"): "[a]",
    ("TruthyWithAttrs", "{% for x in rows %}{% if x %}T{% else %}F{% endif %}{% endfor %}"): "T",
    ("TruthyWithAttrs", "{% if 'a' in p %}T{% else %}F{% endif %}"): "T",
    ("TruthyWithAttrs", "{% if not p %}T{% else %}F{% endif %}"): "F",
    ("TruthyWithAttrs", "{% if p %}T{% else %}F{% endif %}"): "T",
    ("TruthyWithAttrs", "{% if p == None %}T{% else %}F{% endif %}"): "F",
    ("TruthyWithAttrs", "{% if p and 1 %}T{% else %}F{% endif %}"): "T",
    ("TruthyWithAttrs", "{% if p or 0 %}T{% else %}F{% endif %}"): "T",
    ("TruthyWithAttrs", "{% with q=p %}{% if q %}T{% else %}F{% endif %}{% endwith %}"): "T",
    ("TruthyWithAttrs", "{{ d.p }}"): "{&#x27;a&#x27;: 1}",
    ("TruthyWithAttrs", "{{ d.p.a }}"): "1",
    ("TruthyWithAttrs", "{{ p }}"): "{&#x27;a&#x27;: 1}",
    ("TruthyWithAttrs", "{{ p.0 }}"): "",
    ("TruthyWithAttrs", "{{ p._a }}"): "<<REFUSED>>",
    ("TruthyWithAttrs", "{{ p.a }}"): "1",
    ("TruthyWithAttrs", "{{ p.b }}"): "",
    ("TruthyWithAttrs", "{{ p.items }}"): "dict_items([(&#x27;a&#x27;, 1)])",
    ("TruthyWithAttrs", "{{ p.keys }}"): "dict_keys([&#x27;a&#x27;])",
    ("TruthyWithAttrs", "{{ p.zzz }}"): "",
    ("TruthyWithAttrs", "{{ p|add:1 }}"): "",
    ("TruthyWithAttrs", "{{ p|default:'D' }}"): "{&#x27;a&#x27;: 1}",
    ("TruthyWithAttrs", "{{ p|default_if_none:'D' }}"): "{&#x27;a&#x27;: 1}",
    ("TruthyWithAttrs", "{{ p|dictsort:'a' }}"): "",
    ("TruthyWithAttrs", "{{ p|escapeseq }}"): "[&#x27;a&#x27;]",
    ("TruthyWithAttrs", "{{ p|first }}"): "<<REFUSED>>",
    ("TruthyWithAttrs", "{{ p|join:',' }}"): "a",
    (
        "TruthyWithAttrs",
        "{{ p|json_script:'x' }}",
    ): '<script id="x" type="application/json">{"a": 1}</script>',
    ("TruthyWithAttrs", "{{ p|last }}"): "<<REFUSED>>",
    ("TruthyWithAttrs", "{{ p|length }}"): "1",
    ("TruthyWithAttrs", "{{ p|linebreaks }}"): "<p>{&#x27;a&#x27;: 1}</p>",
    ("TruthyWithAttrs", "{{ p|lower }}"): "{&#x27;a&#x27;: 1}",
    (
        "TruthyWithAttrs",
        "{{ p|make_list }}",
    ): "[&#x27;{&#x27;, &quot;&#x27;&quot;, &#x27;a&#x27;, &quot;&#x27;&quot;, &#x27;:&#x27;, &#x27; &#x27;, &#x27;1&#x27;, &#x27;}&#x27;]",
    ("TruthyWithAttrs", "{{ p|pprint }}"): "{&#x27;a&#x27;: 1}",
    ("TruthyWithAttrs", "{{ p|safeseq }}"): "[&#x27;a&#x27;]",
    ("TruthyWithAttrs", "{{ p|slice:':1' }}"): "{&#x27;a&#x27;: 1}",
    ("TruthyWithAttrs", "{{ p|stringformat:'r' }}"): "{&#x27;a&#x27;: 1}",
    ("TruthyWithAttrs", "{{ p|stringformat:'s' }}"): "{&#x27;a&#x27;: 1}",
    ("TruthyWithAttrs", "{{ p|striptags }}"): "{&#x27;a&#x27;: 1}",
    ("TruthyWithAttrs", "{{ p|unordered_list }}"): "\t<li>a</li>",
    ("TruthyWithAttrs", "{{ p|yesno }}"): "yes",
    ("TruthyWithAttrs", "{{ p|yesno:'Y,N,M' }}"): "Y",
}


#: The one cell the RECORDED table cannot hold, and the reason is the cell's
#: own shape rather than anything about the fix.
#:
#: `|make_list` splits `str(o)` into individual characters — so an object whose
#: `str()` carries a memory address arrives as a list of the address's DIGITS,
#: which no whole-string normalisation can reach (`norm` sees `'0'`, `'x'`,
#: `'1'`, … as separate escaped entries, not as one `0x…` token). The address
#: differs per process, so a table captured in one process and compared in
#: another can never match it.
#:
#: It stays in `CELLS`: its Django parity is asserted IN-PROCESS by
#: `TestTheDivergenceIsClosed`, where both engines see the same instance. Only
#: the cross-process comparison against `PRE_FIX` is exempt, and naming the one
#: cell — rather than filtering by a predicate like "contains an address" — is
#: what stops a second cell being absorbed silently.
PRE_FIX_UNASSERTABLE = {"{{ p|make_list }}"}

#: The two cells this fix does NOT close, named rather than filtered by a
#: predicate — a predicate would silently absorb a third.
STILL_DIVERGENT = {
    # Django REFUSES `json_script` over any non-JSON-serializable object
    # (`Object of type X is not JSON serializable`) and djust emits the value's
    # `json` spelling. That is #2429's recorded refusal direction, unchanged in
    # KIND here — though the payload is now `str(o)` rather than a dict of the
    # object's attributes, which is strictly less of the object on the page.
    "{{ p|json_script:'x' }}",
    # `dictsort` over an empty iterable is `[]` in Django and `''` here, before
    # and after. Pre-existing and unrelated to the carrier.
    "{{ p|dictsort:'a' }}",
}


def _ctx(name: str) -> dict:
    obj = SHAPES[name]()
    return {"p": obj, "d": {"p": obj}, "rows": [obj]}


class TestTheDivergenceIsClosed:
    """Every cell, for every shape the gate admits, against live Django —
    through BOTH entry points `python/djust/template/backend.py` binds."""

    @pytest.mark.parametrize("shape", CLAIMED)
    @pytest.mark.parametrize("source", CELLS)
    def test_the_cell_agrees_with_django(self, shape: str, source: str) -> None:
        if source in STILL_DIVERGENT:
            pytest.skip("recorded divergence — see TestTheTwoItDoesNotClose")
        ctx = _ctx(shape)
        expected = django_render(source, ctx)
        assert djust_render(source, ctx) == expected
        assert djust_render_with_dirs(source, ctx) == expected

    def test_the_issues_own_four_headline_cells(self) -> None:
        """Spelled out verbatim, so the issue's table has a named assertion."""
        value = LenZeroWithAttrs()
        assert bool(value) is False
        for source, expected in [
            ("{% if p %}T{% else %}F{% endif %}", "F"),
            ("{{ p|length }}", "0"),
            ("{% for x in p %}[{{ x }}]{% endfor %}", ""),
        ]:
            assert django_render(source, {"p": value}) == expected, source
            assert djust_render(source, {"p": value}) == expected, source
        bare = django_render("{{ p }}", {"p": value})
        assert "LenZeroWithAttrs object at" in bare
        assert djust_render("{{ p }}", {"p": value}) == bare


class TestTheAttributeStillResolves:
    """The regression #2466's decline was about, and the reason this needed
    #2481 first.

    Asserted on its own rather than only inside the sweep, because it is the
    ONE cell the fix had to KEEP — every other cell it moves toward Django.
    """

    @pytest.mark.parametrize("shape", CLAIMED)
    def test_a_public_attribute_resolves_on_every_path(self, shape: str) -> None:
        from djust._rust import RustLiveView

        ctx = _ctx(shape)
        assert django_render("{{ p.a }}", ctx) == "1"
        assert djust_render("{{ p.a }}", ctx) == "1"
        assert djust_render_with_dirs("{{ p.a }}", ctx) == "1"
        assert djust_render("{{ d.p.a }}", ctx) == "1"
        view = RustLiveView("{{ p.a }}")
        view.set_state("p", SHAPES[shape]())
        assert view.render() == "1"

    def test_a_second_attribute_and_a_string_one_resolve_too(self) -> None:
        """A one-attribute fixture cannot tell "the map has THE attribute" from
        "the map has AN attribute"."""
        ctx = _ctx("LenZeroWithAttrs")
        assert djust_render("{{ p.b }}", ctx) == django_render("{{ p.b }}", ctx) == "x"

    def test_the_attribute_survives_a_state_round_trip(self) -> None:
        """`SerializableViewState.state` round-trips through msgpack on every
        read of the default backend, so an attribute that answered once and
        went empty afterwards would be worse than not answering at all.

        On the ESCAPE-HATCH axis since #2539 movement 3: with the eager
        conversion the attribute map IS the serialized value, so it survives.
        The default's answer is the #2570 contract, asserted by name below.
        """
        from djust._rust import RustLiveView

        with resolve_lazy(False):
            view = RustLiveView("{{ p.a }}|{% if p %}T{% else %}F{% endif %}|{{ p|length }}")
            view.set_state("p", LenZeroWithAttrs())
            clone = RustLiveView.deserialize_msgpack(view.serialize_msgpack())
            assert clone.render() == "1|F|0"

    def test_the_round_trip_under_the_default_is_the_2570_contract(self) -> None:
        """The same clone under the shipped default, with its bytes NAMED.

        Under ADR-027 the value carries a live handle instead of an eager
        attribute map, and the handle is transient — `Deserialize` restores
        `live: None` and `attrs` empty. So a `RustLiveView` clone rendered
        WITHOUT a re-sync answers empty for a handle-only lookup, while the
        facts recorded on the value (truthiness, `len`) still answer. That is
        #2570's documented contract, not a regression: every framework path
        runs a FULL sync before the first render after a restore
        (`_force_full_html` empties `prev_refs`), which re-converts the value
        and re-attaches the handle.

        Asserted by name so a later change that silently moves these bytes —
        an eager snapshot beside the handle, say — fails here rather than
        quietly redefining the contract (#1125).
        """
        from djust._rust import RustLiveView

        view = RustLiveView("{{ p.a }}|{% if p %}T{% else %}F{% endif %}|{{ p|length }}")
        view.set_state("p", LenZeroWithAttrs())
        assert view.render() == "1|F|0", "the handle answers BEFORE the round trip"
        clone = RustLiveView.deserialize_msgpack(view.serialize_msgpack())
        assert clone.render() == "|F|0", (
            "the #2570 contract moved: a restored clone rendered without a sync must answer "
            "empty for the handle-only lookup and keep the recorded facts (`F`, `0`)"
        )

    def test_a_private_attribute_is_still_unreachable(self) -> None:
        """The `_`-prefix filter moved WITH the collection, so it still
        applies. Django refuses a `_`-leading path segment at parse time
        (`Variables and attributes may not begin with underscores`); djust
        refuses too — both refuse, which is the parity that matters."""
        ctx = _ctx("LenZeroPrivateOnly")
        assert django_render("{{ p._a }}", ctx) == REFUSED
        assert djust_render("{{ p._a }}", ctx) == REFUSED


class TestTheGateIsUNCHANGED:
    """Only the objects `opaque_value`'s gate ADMITS moved.

    Every cell of every other shape is compared against `PRE_FIX` — djust's own
    answer on the build immediately BEFORE this change — rather than against
    Django's, because several of these still diverge and are supposed to.
    Recomputing "what Django says" here would make the test a second copy of
    the divergence table; recording djust's own answer is what catches the fix
    reaching past its gate.

    On the ESCAPE-HATCH axis since #2539 movement 3. `PRE_FIX` records the
    EAGER conversion's answers — the `__dict__` dump arm, `public_dict_attrs`,
    the by-name sidecar — and the flip makes those dormant under the shipped
    default, so an ambient comparison would be reading a table captured against
    a mechanism that no longer runs. Pushed OFF, the table still measures what
    it was built to measure, and it keeps measuring it for as long as the hatch
    exists (movement 4 deletes the arms and the flag together, and this table
    goes with them).
    """

    @pytest.mark.parametrize("shape", UNCHANGED)
    @pytest.mark.parametrize("source", CELLS)
    def test_an_unclaimed_shape_answers_the_pre_fix_way(self, shape: str, source: str) -> None:
        if source in PRE_FIX_UNASSERTABLE:
            pytest.skip("address-shredding cell — see PRE_FIX_UNASSERTABLE")
        with resolve_lazy(False):
            assert djust_render(source, _ctx(shape)) == PRE_FIX[(shape, source)]

    def test_the_len_two_shape_that_used_to_iterate_its_ATTRIBUTES_now_agrees(
        self,
    ) -> None:
        """This test recorded #2466's decline; #2477/#2489 closed it.

        A falsy object with a NON-ZERO `__len__` arrived as a `Value::Object`,
        so `{% for %}` over it iterated the ATTRIBUTES — `[a]` where Django
        renders `[10][20]`. Kept, with its assertion INVERTED, rather than
        deleted: the pin's value is that it names the exact cell the decline
        cost, and a regression would put `[a]` back.
        """
        ctx = _ctx("LenTwoBoolFalseWithAttrs")
        assert django_render("{% for x in p %}[{{ x }}]{% endfor %}", ctx) == "[10][20]"
        assert djust_render("{% for x in p %}[{{ x }}]{% endfor %}", ctx) == "[10][20]"
        assert djust_render("{{ p|length }}", ctx) == django_render("{{ p|length }}", ctx) == "2"

    def test_the_2466_family_is_untouched(self) -> None:
        """#2466's own objects took this arm before #2478 and must be
        byte-identical: no `__dict__` at all and an empty one both mean no
        attributes."""
        for value in (set(), frozenset(), complex(0), {}.keys(), {}.values()):
            assert djust_render("{% if p %}T{% else %}F{% endif %}", {"p": value}) == "F"
            assert djust_render("{{ p|yesno }}", {"p": value}) == "no"


class TestTheIssuesOwnRemedyWouldNotHaveReached:
    """The issue proposes *"a truthiness override on `Value::Object`"*. Measured
    against the corpus, it reaches the truthiness row and nothing else — and
    the cells it misses are the ones that make the `__dict__` carrier WRONG
    rather than merely mis-answered.

    A proposed remedy has the same epistemic status as a cited code location
    (CLAUDE.md, v1.1.1-2 rule 1). This measures the split so choosing a
    different fix is a finding rather than a preference.
    """

    #: The cells a truthiness override could reach — exactly those that consult
    #: `is_truthy` and never the mapping.
    TRUTHINESS_ONLY = frozenset(
        {
            "{% if p %}T{% else %}F{% endif %}",
            "{% if not p %}T{% else %}F{% endif %}",
            "{% if p and 1 %}T{% else %}F{% endif %}",
            "{% if p or 0 %}T{% else %}F{% endif %}",
            "{% with q=p %}{% if q %}T{% else %}F{% endif %}{% endwith %}",
            "{% firstof p 'FB' %}",
            "{{ p|yesno }}",
            "{{ p|yesno:'Y,N,M' }}",
            "{{ p|default:'D' }}",
            "{% for x in rows %}{% if x %}T{% else %}F{% endif %}{% endfor %}",
        }
    )

    def test_the_remedy_would_have_left_most_of_the_divergence(self) -> None:
        ctx = _ctx("LenZeroWithAttrs")
        reachable, unreachable = [], []
        for source in CELLS:
            if source in PRE_FIX_UNASSERTABLE:
                continue
            if PRE_FIX[("LenZeroWithAttrs", source)] == django_render(source, ctx):
                continue  # agreed before the fix — not this issue's business
            (reachable if source in self.TRUTHINESS_ONLY else unreachable).append(source)

        assert reachable, "the truthiness cells stopped diverging pre-fix"
        assert unreachable, (
            "every diverging cell is now reachable by a truthiness override — "
            "the argument for moving the carrier has gone, so re-derive it"
        )
        # Named, so the count is a claim rather than a shrug. Each reads the
        # MAPPING, which is the thing the `__dict__` arm asserts and the
        # override would leave in place.
        for named in (
            "{{ p|length }}",
            "{% for x in p %}[{{ x }}]{% empty %}E{% endfor %}",
            "{{ p }}",
            "{{ p|pprint }}",
            "{{ p.items }}",
        ):
            assert named in unreachable, named

    def test_one_change_answers_both_lists(self) -> None:
        """And the point: ONE carrier move closes the reachable AND the
        unreachable cells, because each reads a spelling `Encoded` already
        carries."""
        ctx = _ctx("LenZeroWithAttrs")
        for source in CELLS:
            if source in STILL_DIVERGENT:
                continue
            assert djust_render(source, ctx) == django_render(source, ctx), source


class TestTheTwoItDoesNotClose:
    """The exemption list is exact in BOTH directions."""

    @pytest.mark.parametrize("shape", CLAIMED)
    def test_the_two_it_does_not_close_are_the_only_two(self, shape: str) -> None:
        """The exemption list is exact in BOTH directions: a third divergence
        appearing fails here, and one of these two being closed fails here too
        — so the list cannot go stale as cover (#1859)."""
        ctx = _ctx(shape)
        diverging = {
            source for source in CELLS if djust_render(source, ctx) != django_render(source, ctx)
        }
        assert diverging <= STILL_DIVERGENT, (
            f"{shape}: a cell diverges that is not on the exemption list: "
            f"{sorted(diverging - STILL_DIVERGENT)}"
        )
        assert "{{ p|json_script:'x' }}" in diverging, (
            f"{shape}: `json_script` stopped diverging — delete it from the "
            "exemption list rather than leaving it as cover"
        )

    def test_json_script_emits_LESS_of_the_object_than_it_did(self) -> None:
        """The one cell whose divergence changes SHAPE rather than closing.

        Django refuses; djust emitted the object's attributes as a JSON object
        and now emits its `str()`. Both diverge, but the fix moves a leak of
        the attribute VALUES off the page — recorded because "unchanged in
        kind" is easy to write and would be wrong.
        """
        ctx = _ctx("LenZeroWithAttrs")
        assert django_render("{{ p|json_script:'x' }}", ctx) == REFUSED
        out = djust_render("{{ p|json_script:'x' }}", ctx)
        assert "LenZeroWithAttrs object at" in out
        assert '"a"' not in out and '"b"' not in out, out


class TestThePreFixTableIsNOTVacuous:
    """`PRE_FIX` is the reference `TestTheGateIsUNCHANGED` compares against, so
    a table that quietly recorded the CURRENT build would make that whole class
    a tautology. It was captured by running the corpus against the build
    immediately before this change; what proves it is that for the CLAIMED
    shapes the engine no longer produces those answers.
    """

    def test_the_claimed_shapes_moved_off_the_recorded_answers(self) -> None:
        moved = {
            shape: [
                source
                for source in CELLS
                if source not in PRE_FIX_UNASSERTABLE
                and djust_render(source, _ctx(shape)) != PRE_FIX[(shape, source)]
            ]
            for shape in CLAIMED
        }
        for shape, sources in moved.items():
            assert len(sources) >= 30, (
                f"{shape} moved only {len(sources)} cells off the recorded "
                "answers — either the fix regressed or PRE_FIX is recording "
                "the CURRENT build"
            )
        # 131 for the four shapes #2478 claimed, plus 32 for the fifth that
        # #2477/#2489 added — `LenTwoBoolFalseWithAttrs`, which moved off the
        # `Value::Object` of its attributes and onto the carrier.
        assert sum(len(v) for v in moved.values()) == 163, (
            "the total cell movement changed; re-measure with "
            "`scratch/sweep_2478.py` before editing this number"
        )

    def test_the_unclaimed_shapes_moved_nothing(self) -> None:
        """The other half of the same measurement, and the one that makes
        "only the gate's admissions moved" a fact rather than a hope."""
        with resolve_lazy(False):
            for shape in UNCHANGED:
                moved = [
                    source
                    for source in CELLS
                    if source not in PRE_FIX_UNASSERTABLE
                    and djust_render(source, _ctx(shape)) != PRE_FIX[(shape, source)]
                ]
                assert moved == [], (shape, moved)

    def test_every_shape_has_a_full_row(self) -> None:
        """A missing key makes the parametrized comparison raise `KeyError`
        rather than compare — a failure mode worth naming, since it looks like
        a test error rather than a parity finding."""
        for shape in SHAPES:
            for source in CELLS:
                assert (shape, source) in PRE_FIX, (shape, source)


class TestTheSerializationFloorsStayAbove:
    """`opaque_value` now dumps a `__dict__`, so its ORDER relative to the two
    denylist arms is a security boundary rather than a style choice: a Django
    model reaching it would have its floor fields (`password`, …) collected
    into the attribute map by the same bulk dump #1986 routed models around.
    """

    @staticmethod
    def _conversion_block() -> str:
        src = CORE_RS.read_text(encoding="utf-8")
        assert src.count("impl<'py> FromPyObject<'_, 'py> for Value {") == 1
        return src.split("impl<'py> FromPyObject<'_, 'py> for Value {", 1)[1]

    def test_both_floors_are_ordered_before_the_carrier_arm(self) -> None:
        block = self._conversion_block()
        serialize_at = block.index('ob.getattr("__djust_serialize__")')
        model_at = block.index('models_mod.getattr("Model")')
        falsy_at = block.index("if let Some(encoded) = opaque_value(")
        dict_at = block.index("if let Some(map) = public_dict_attrs(")
        assert serialize_at < falsy_at, (
            "the `__djust_serialize__` denylist arm moved BELOW `opaque_value` — "
            "a proxied model's floor fields would be dumped into the attribute map"
        )
        assert model_at < falsy_at, (
            "the raw-`Model` denylist arm moved BELOW `opaque_value` — #1986 vector 7"
        )
        assert falsy_at < dict_at, (
            "`opaque_value` moved back below the `__dict__` bulk dump — that is "
            "the pre-#2478 order and reopens the issue"
        )

    def test_the_order_check_can_go_red(self) -> None:
        """The canary. A source-order assertion that has never been watched
        fail is a pin with an unknown failure mode (#2129/#2135)."""
        block = self._conversion_block()
        swapped = block.replace("if let Some(encoded) = opaque_value(", "@@FALSY@@", 1).replace(
            "if let Some(map) = public_dict_attrs(", "if let Some(encoded) = opaque_value(", 1
        )
        swapped = swapped.replace("@@FALSY@@", "if let Some(map) = public_dict_attrs(", 1)
        assert swapped != block, "the ORDER mutation did not apply"
        assert swapped.index("if let Some(map) = public_dict_attrs(") < swapped.index(
            "if let Some(encoded) = opaque_value("
        ), "the mutation did not actually reverse the order"

    def test_the_underscore_filter_is_observable_on_the_dict_arm(self) -> None:
        """Where the `_`-prefix filter can actually be SEEN, and why it is
        asserted here rather than through `opaque_value`'s arm.

        On the `__dict__` arm the map becomes a `Value::Object`, and `{{ p }}`
        prints it — so a private attribute leaking into the map puts its VALUE
        on the page. That is reachable, so it is tested.

        On `opaque_value`'s arm the map is `Encoded::attrs`, whose ONLY reader
        is `context::lookup_segment`, and djust refuses a `_`-leading path
        segment before the lookup happens (as Django does, at parse time). So
        the filter there is defence-in-depth for a FUTURE reader of `attrs`
        rather than a currently-observable rule — a gate-off that removes it
        survives, and that is a semantic no-op for the reachable inputs, not a
        missing test. Recorded rather than papered over (CLAUDE.md, v1.1.1-2
        rule 3: a surviving mutation is a question).
        """

        class TruthyWithSecret:
            def __init__(self) -> None:
                self.a = 1
                self._secret = "hunter2"

        class FalsyWithSecret:
            def __init__(self) -> None:
                self.a = 1
                self._secret = "hunter2"

            def __len__(self) -> int:
                return 0

        truthy, falsy = TruthyWithSecret(), FalsyWithSecret()
        assert bool(truthy) is True and bool(falsy) is False

        # The `__dict__` arm: the map IS the rendered value.
        out = djust_render("{{ p }}", {"p": truthy})
        assert "hunter2" not in out, out
        assert "a" in out, out
        # ...and Django shows neither, so djust is not merely quieter.
        assert "hunter2" not in django_render("{{ p }}", {"p": truthy})

        # `opaque_value`'s arm: the display is `str(o)`, which carries no
        # attribute at all, and the path segment is refused before the map is
        # consulted.
        assert "hunter2" not in djust_render("{{ p }}", {"p": falsy})
        assert djust_render("{{ p._secret }}", {"p": falsy}) == REFUSED
        assert django_render("{{ p._secret }}", {"p": falsy}) == REFUSED

    def test_the_collection_is_stated_once(self) -> None:
        """One `public_dict_attrs`, two callers — the `__dict__` arm and
        `opaque_value`. Two copies of the `_`-prefix filter is the #1646 shape,
        one arm growing a rule the other does not, and THIS arm's copy is the
        one that would leak.

        #2477/#2489 added a THIRD reader of the same question and did NOT add a
        third copy of the rule: `has_public_dict_attrs` asks whether the map
        would be empty, over the KEYS, because building it to answer that would
        convert every attribute value for the arm below to convert again. The
        `_`-prefix rule moved into `is_public_attr_name`, which both call — so
        the count below is of the RULE, not of the map builder, and it is still
        one.
        """
        src = CORE_RS.read_text(encoding="utf-8")
        assert src.count("fn public_dict_attrs(") == 1
        assert src.count("fn has_public_dict_attrs(") == 1
        # The map BUILDER's callers: the `__dict__` arm and `opaque_value`.
        # `has_public_dict_attrs` is matched out by name so the two questions
        # stay countable apart.
        calls = re.findall(r"(?<!fn )(?<!has_)public_dict_attrs\(", src)
        assert len(calls) == 2, f"the caller set moved: {len(calls)}"
        # The RULE, stated once and read by both.
        assert src.count("fn is_public_attr_name(") == 1
        assert src.count("name.starts_with('_')") == 1, (
            "a second copy of the `_`-prefix filter appeared — state it once"
        )
        assert src.count("if k.starts_with('_') {") == 0, (
            "the map builder grew its own copy of the rule back"
        )
