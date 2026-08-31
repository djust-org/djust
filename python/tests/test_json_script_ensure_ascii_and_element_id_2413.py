"""`json_script` matches Django byte for byte: `ensure_ascii`, and the `id` (#2413).

Two defects in one filter, both byte-level, both a different mechanism from
the key ordering #2405 closed.

1. ``ensure_ascii``
-------------------
``django.utils.html.json_script`` calls ``json.dumps(value, cls=encoder or
DjangoJSONEncoder)`` and passes no ``ensure_ascii``, so it takes the default
of ``True`` — ``DjangoJSONEncoder`` overrides only ``default()``. Every
non-ASCII character therefore leaves Django as ``\\uXXXX``, and one above the
BMP as a surrogate PAIR. djust emitted raw UTF-8::

    {{ p|json_script:"d" }}     p = {"k": "héllo→"}

    django  <script id="d" …>{"k": "h\\u00e9llo\\u2192"}</script>
    djust   <script id="d" …>{"k": "héllo→"}</script>

It applies to KEYS as much as values and at every nesting depth, because
``value_to_json`` routes every quoted string through one escaper (#2241).

2. A falsy ``element_id`` still emitted one
--------------------------------------------
The issue reported this as "a MISSING ``element_id``". Running Django says the
premise is narrower than the defect: the source is ``if element_id:`` — a
TRUTHINESS test on the resolved Python object, not ``is not None`` — so
``None``, ``""``, ``0``, ``0.0``, ``False``, ``[]`` and ``{}`` all omit the
attribute WHOLE. djust wrote ``id="data"``, ``id=""``, ``id="0"``,
``id="False"``, ``id="[]"`` for those seven. ``str()`` cannot answer the
question (``str(0)`` is ``"0"``), which is why the fix threads the resolved
value's truthiness as an ``ArgType`` bit rather than re-reading the ``&str``.

The masking this removes
------------------------
Every argument-less ``json_script`` cell in
``scripts/filter-parity-differential.py`` diverged on the ``id`` attribute, so
any divergence in the JSON BODY sat underneath and could not be attributed —
which is why #2405's corpus shape had to pass an explicit ``id``. Fixing the
``id`` half un-masks that surface.

Every expectation here is LIVE Django, never a transcription: each case calls
``django.utils.html.json_script`` or renders through ``django.template``, and
compares bytes.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import pathlib
import random

import pytest
from django.template import Context as DjangoContext
from django.template import Template as DjangoTemplate
from django.utils.html import json_script as django_json_script

from djust import _rust


def both(source: str, ctx: dict) -> tuple[str, str]:
    return (
        DjangoTemplate(source).render(DjangoContext(dict(ctx))),
        _rust.render_template(source, dict(ctx)),
    )


def body_of(script_html: str) -> str:
    """The JSON body between the tag's `>` and its `</script>`."""
    start = script_html.index(">") + 1
    end = script_html.rindex("</script>")
    return script_html[start:end]


# ---------------------------------------------------------------------------
# 1. ensure_ascii
# ---------------------------------------------------------------------------

#: One row per branch of the escaper, and deliberately including the shapes a
#: BMP-only, value-only sample cannot see: a non-ASCII KEY, an astral
#: codepoint (surrogate pair) in both positions, and DEL — the one character
#: where `ensure_ascii=True` and `ensure_ascii=False` disagree.
ENSURE_ASCII_VALUES: list[object] = [
    {"k": "héllo→"},
    {"héy→": "v"},
    {"k": "\U0001f600"},
    {"\U0001f600": 1},
    {"a": [{"bé": "c\U0001f600"}]},
    {"k": "\x7f"},
    {"k": "\xa0"},
    {"k": "\u2028\u2029"},
    {"k": "<script>&"},
    ["é", "\U0001f600", "\U0010ffff"],
    "héllo→",
    {"\ud7ff\ue000\uffff": "\U00010000"},
]


class TestEnsureAscii:
    @pytest.mark.parametrize("value", ENSURE_ASCII_VALUES, ids=repr)
    def test_the_rendered_bytes_are_djangos(self, value: object) -> None:
        dj, du = both('{{ p|json_script:"d" }}', {"p": value})
        assert dj == du

    @pytest.mark.parametrize("value", ENSURE_ASCII_VALUES, ids=repr)
    def test_the_body_is_pure_ascii_as_json_dumps_makes_it(self, value: object) -> None:
        """The property `ensure_ascii` names, asserted directly.

        Byte equality with Django already implies it, but this is the reading
        that survives a future change to how the tag is assembled: whatever
        else moves, the BODY may not contain a non-ASCII byte.
        """
        _, du = both('{{ p|json_script:"d" }}', {"p": value})
        body = body_of(du)
        assert body.isascii(), body

    @pytest.mark.parametrize("value", ENSURE_ASCII_VALUES, ids=repr)
    def test_the_body_still_parses_back_to_the_value(self, value: object) -> None:
        """Escaping is not corruption: `json.loads` recovers the input."""
        _, du = both('{{ p|json_script:"d" }}', {"p": value})
        # `<`, `>` and `&` arrive as the `\\u003C` forms Django's
        # `_json_script_escapes` writes, which `json.loads` decodes.
        assert json.loads(body_of(du)) == value

    def test_an_astral_codepoint_is_a_surrogate_PAIR(self) -> None:
        """The half a BMP-only sample cannot see.

        `\\U0001f600` has no 4-hex spelling, so an implementation that
        formatted the codepoint directly would emit six hex digits and a
        parser would read the last two as literal text.
        """
        _, du = both('{{ p|json_script:"d" }}', {"p": "\U0001f600"})
        assert "\\ud83d\\ude00" in du, du
        assert "\\u1f600" not in du, du

    def test_DEL_is_escaped_because_this_is_the_ensure_ascii_TRUE_path(self) -> None:
        """`json.dumps("\\x7f")` is `'"\\\\u007f"'`; with `ensure_ascii=False`
        it is the raw byte. DEL is the sharpest single witness for which of
        the two flags this path takes, and the Rust helper's doc used to cite
        the wrong one."""
        assert json.dumps("\x7f") == '"\\u007f"'
        assert json.dumps("\x7f", ensure_ascii=False) == '"\x7f"'
        dj, du = both('{{ p|json_script:"d" }}', {"p": "\x7f"})
        assert dj == du
        assert "\\u007f" in du and "\x7f" not in du

    def test_the_hex_case_is_djangos_in_BOTH_spellings(self) -> None:
        """Django composes two steps that disagree on case, and so must this.

        `json.dumps`'s `ensure_ascii` writes LOWERCASE (`\\u00e9`);
        `_json_script_escapes` is a dict of literal strings and writes
        UPPERCASE for its three (`\\u003C`). A single-case implementation
        matches one and breaks the other.
        """
        dj, du = both('{{ p|json_script:"d" }}', {"p": "é<&>"})
        assert dj == du
        assert "\\u00e9" in du, du
        assert "\\u003C" in du and "\\u003E" in du and "\\u0026" in du, du

    def test_the_encoder_django_uses_does_not_override_the_flag(self) -> None:
        """The premise the whole fix rests on, falsification-tested (#1516).

        If `DjangoJSONEncoder` set `ensure_ascii=False`, every row above would
        be wrong in the other direction.
        """
        assert json_script_output("é") == '<script type="application/json">"\\u00e9"</script>'


def json_script_output(value: object, element_id: object = None) -> str:
    return str(django_json_script(value, element_id))


# ---------------------------------------------------------------------------
# 2. The `id` attribute
# ---------------------------------------------------------------------------

#: Django's `if element_id:` is a truthiness test on the RESOLVED object, so
#: every falsy Python object omits the attribute. Each entry is what the
#: context variable `e` is bound to.
FALSY_IDS: list[object] = [None, "", 0, 0.0, False, [], {}, ()]
TRUTHY_IDS: list[object] = ["x", " ", "0", "id-é", 5, True, ["a"]]


class TestAFalsyElementIdEmitsNoIdAttribute:
    def test_no_argument_at_all(self) -> None:
        dj, du = both("{{ p|json_script }}", {"p": {"k": 1}})
        assert dj == du
        assert "id=" not in du, du
        assert du.startswith('<script type="application/json">'), du

    def test_the_invented_default_is_gone(self) -> None:
        """Named separately from the parity row because it is the DEFECT.

        djust wrote `id="data"` — an attribute Django never writes — so two
        argument-less `{{ …|json_script }}` calls on one page collided on the
        same DOM id.
        """
        _, du = both("{% for x in p %}{{ x|json_script }}{% endfor %}", {"p": [1, 2]})
        assert "data" not in du, du
        assert du.count("id=") == 0, du

    @pytest.mark.parametrize("e", FALSY_IDS, ids=repr)
    def test_every_falsy_resolved_argument(self, e: object) -> None:
        dj, du = both("{{ p|json_script:e }}", {"p": {"k": 1}, "e": e})
        assert "id=" not in dj, (e, dj)
        assert dj == du

    @pytest.mark.parametrize("e", TRUTHY_IDS, ids=repr)
    def test_every_truthy_resolved_argument_keeps_its_id(self, e: object) -> None:
        dj, du = both("{{ p|json_script:e }}", {"p": {"k": 1}, "e": e})
        assert "id=" in dj, (e, dj)
        assert dj == du

    @pytest.mark.parametrize(
        "literal",
        ['""', "''", "0", "0.0", "-0", "None", "False"],
    )
    def test_a_falsy_LITERAL_argument_too(self, literal: str) -> None:
        """The other channel. `FilterExpression.resolve` produces a Python
        object for a constant as much as for a variable — a quoted constant is
        a `str`, an unquoted number is a number, and `None`/`True`/`False`
        come from the context builtins — so the same truthiness test applies.

        `str(0)` is `"0"`, so a spelling fallback cannot answer this; the
        engine threads the resolved value's truthiness instead.
        """
        source = "{{ p|json_script:%s }}" % literal
        dj, du = both(source, {"p": {"k": 1}})
        assert "id=" not in dj, (literal, dj)
        assert dj == du

    @pytest.mark.parametrize("literal", ['"d"', "5", "True", '" "'])
    def test_a_truthy_LITERAL_argument_keeps_its_id(self, literal: str) -> None:
        source = "{{ p|json_script:%s }}" % literal
        dj, du = both(source, {"p": {"k": 1}})
        assert "id=" in dj, (literal, dj)
        assert dj == du

    def test_the_id_is_omitted_WHOLE_not_emitted_empty(self) -> None:
        """Django has two templates, not one with an empty default — and
        `id=""` is not the same DOM as no `id`.
        """
        _, du = both('{{ p|json_script:"" }}', {"p": {"k": 1}})
        assert 'id=""' not in du, du
        assert du == '<script type="application/json">{"k": 1}</script>', du

    def test_the_escaping_of_a_TRUTHY_id_is_unchanged(self) -> None:
        """The #2389 grant this fix must not disturb: a QUOTED-literal id goes
        in raw (a constant argument is `mark_safe`d), a RESOLVED one escapes.
        """
        dj, du = both('{{ p|json_script:"<b>" }}', {"p": 1})
        assert dj == du
        assert 'id="<b>"' in du, du
        dj, du = both("{{ p|json_script:e }}", {"p": 1, "e": "<b>"})
        assert dj == du
        assert 'id="&lt;b&gt;"' in du, du


# ---------------------------------------------------------------------------
# The randomized differential
# ---------------------------------------------------------------------------

#: One entry per branch of the escaper, so an assembled value can reach any of
#: them at any depth and in either position. A curated table samples one axis
#: and blinds you on the next; this is the axis-free companion.
ALPHABET = [
    "a",
    "Z",
    "0",
    " ",
    "~",
    '"',
    "\\",
    "/",
    "<",
    ">",
    "&",
    "'",
    "\n",
    "\r",
    "\t",
    "\b",
    "\f",
    "\x00",
    "\x0b",
    "\x1f",
    "\x7f",
    "\x80",
    "\xa0",
    "é",
    "→",
    "\u2028",
    "\u2029",
    "\ud7ff",
    "\ue000",
    "\uffff",
    "\U00010000",
    "\U0001f600",
    "\U0010ffff",
    "中",
    "\u0301",
]


def _rand_str(rng: random.Random, n: int | None = None) -> str:
    n = rng.randint(0, 5) if n is None else n
    return "".join(rng.choice(ALPHABET) for _ in range(n))


def _rand_value(rng: random.Random, depth: int = 0) -> object:
    kinds = ["str", "int", "float", "bool", "none"]
    if depth < 3:
        kinds += ["list", "dict", "dict", "list"]
    kind = rng.choice(kinds)
    if kind == "str":
        return _rand_str(rng)
    if kind == "int":
        return rng.randint(-1000, 1000)
    if kind == "float":
        return rng.choice([0.0, -0.0, 1.5, 1e20, -3.25])
    if kind == "bool":
        return rng.choice([True, False])
    if kind == "none":
        return None
    if kind == "list":
        return [_rand_value(rng, depth + 1) for _ in range(rng.randint(0, 3))]
    return {
        _rand_str(rng, rng.randint(1, 4)): _rand_value(rng, depth + 1)
        for _ in range(rng.randint(0, 3))
    }


class TestRandomizedDifferentialAgainstDjango:
    """Django is a subprocess away, so "what does the reference actually do"
    is worth preferring to reasoning — and a randomized sweep sees the axes a
    curated table was not written to notice.

    Fixed seed so a failure is reproducible; the four templates cover both
    argument channels and both halves of the fix at once.
    """

    #: Kept modest so the suite stays fast; the PR ran 3,000 × 4 = 12,000 of
    #: these against both builds (9,227 divergent pre-fix, 0 post-fix).
    CASES = 250
    SEED = 2413

    @pytest.mark.parametrize(
        "template",
        [
            '{{ p|json_script:"d" }}',
            "{{ p|json_script }}",
            "{{ p|json_script:e }}",
            '{{ p|json_script:"" }}',
        ],
    )
    def test_every_assembled_value_renders_the_same_bytes(self, template: str) -> None:
        rng = random.Random(self.SEED)
        for _ in range(self.CASES):
            value = _rand_value(rng)
            e = rng.choice(["x", "", None, 0, False, [], "id-é"])
            ctx = {"p": value, "e": e}
            dj, du = both(template, ctx)
            assert dj == du, (template, value, e)

    def test_the_sweep_can_SEE_a_non_ascii_case(self) -> None:
        """The sweep is only evidence if its inputs reach the branch under
        test. Asserted rather than assumed: an `ensure_ascii` mutation is
        invisible to any all-ASCII corpus, so a sweep of ASCII values would
        report the same clean zero as a correct implementation.
        """
        rng = random.Random(self.SEED)
        non_ascii = 0
        for _ in range(self.CASES):
            value = _rand_value(rng)
            if not json.dumps(value, ensure_ascii=False).isascii():
                non_ascii += 1
        assert non_ascii > self.CASES // 10, (
            f"only {non_ascii}/{self.CASES} sampled values carry a non-ASCII "
            "character — the sweep cannot see the ensure_ascii branch"
        )


# ---------------------------------------------------------------------------
# What the un-masking revealed, and this does NOT close
# ---------------------------------------------------------------------------


class TestTheDivergenceTheUnMaskingRevealed:
    """Named rather than silent, so the next reader does not re-find it (#2425).

    Removing the invented `id` made the BODY of every argument-less
    `json_script` cell attributable for the first time. Of the 41 plain
    `json_script <value>` cells in `scripts/filter-parity-differential.py`, 41
    diverged before and 1 diverges after: `d-typed-key`, whose non-`str` keys
    `json.dumps` treats differently in two ways at once — a SPELLING half and a
    REFUSAL half.

    The class was written asserting "these STILL diverge", so that fixing #2425
    would turn it red and force a rewrite as a parity assertion. It did, for the
    spelling half, and the first method below is now that parity assertion. The
    refusal half is still open (#2429) and is still asserted as divergent — so
    `d-typed-key` stays in the scope set at the bottom, for the tuple key rather
    than for the `True` / `None` ones.

    The full re-derivation over every key type — including the two the #2425
    table missed, `float('inf')` and `float('nan')` — lives in
    `test_json_script_typed_keys_2425.py`; this keeps only the two cells the
    un-masking itself revealed.
    """

    def test_a_bool_or_None_KEY_is_now_spelled_the_JSON_way(self) -> None:
        """CLOSED by #2425 — was `{"True": "b", "None": "c"}`.

        `json.dumps` writes `true` / `null` for a `bool` / `None` KEY. djust
        routed the key through `to_display_string()` (Python's `str()`) and
        wrote `True` / `None`; it now routes it through `json_key_body`.
        """
        dj, du = both('{{ p|json_script:"d" }}', {"p": {True: "b", None: "c"}})
        assert '{"true": "b", "null": "c"}' in du, du
        assert dj == du
        # The two that agreed BEFORE the fix, so the parity is pinned per-type
        # rather than as "non-string keys" — these are the coincidence rows
        # (`str(0)` is already `"0"`), and a fix that moved them would be a
        # regression the top assertion cannot see.
        for agreeing in ({0: "a"}, {1.5: "d"}):
            before, after = both('{{ p|json_script:"d" }}', {"p": agreeing})
            assert before == after, agreeing

    @pytest.mark.parametrize(
        "key",
        [(1, "t"), frozenset({1}), b"k"],
        ids=["tuple", "frozenset", "bytes"],
    )
    def test_an_unsupported_KEY_type_is_EMITTED_where_json_dumps_refuses(self, key: object) -> None:
        """The more-permissive direction: `json.dumps` raises `TypeError`
        (there is no `skipkeys=True` in `json_script`) and djust renders a
        document.
        """
        with pytest.raises(TypeError, match="keys must be str, int, float, bool or None"):
            DjangoTemplate('{{ p|json_script:"d" }}').render(DjangoContext({"p": {key: "v"}}))
        du = _rust.render_template('{{ p|json_script:"d" }}', {"p": {key: "v"}})
        assert du.startswith("<script"), du

    def test_the_only_json_script_cells_left_are_the_refusal_half(self) -> None:
        """The scope claim, run rather than asserted from the sweep log.

        Every value shape the differential carries, through the argument-less
        spelling this fix un-masked. Every cell that still diverges must be a
        member of ONE known class — Django's encoder REFUSES the value and
        djust renders it (#2429) — so a NEW body divergence cannot hide here.
        The direction is asserted per cell below rather than left to the
        names, because an exact-set pin over names alone would let a cell
        change class without changing membership.

        `d-typed-key` is
        `{0: "a", True: "b", None: "c", 1.5: "d", (1, "t"): "e"}`, one key of
        each kind. #2425 closed the `True` / `None` spellings; the `(1, "t")`
        key is the still-open refusal half, where Django RAISES and djust
        renders — so the cell stays divergent and this claim stays true across
        that fix.

        `set-empty` / `set-plain` arrived with #2477 and are the same class one
        type over: `json.dumps` refuses a `set` outright
        (`Object of type set is not JSON serializable`), and djust writes the
        value's `str()`, which is what the `Value::String` path it replaced
        already wrote — #2466 records that this axis does not move. Closing
        #2429 is what empties the set.

        This is a claim about the CORPUS, not about the key-type axis: the
        differential carries no non-finite-float key, so it was silent about the
        `float('inf')` / `float('nan')` spellings #2425's fix also had to close.
        The axis itself is swept in `test_json_script_typed_keys_2425.py`.
        """
        module = _load_differential()
        diverging = {}
        for name, value in module.INPUTS.items():
            try:
                dj = DjangoTemplate("{{ p|json_script }}").render(
                    DjangoContext({"p": copy.deepcopy(value)})
                )
            except Exception:  # noqa: BLE001 — a raise is a comparable outcome
                dj = "<<EXC>>"
            try:
                du = _rust.render_template("{{ p|json_script }}", {"p": copy.deepcopy(value)})
            except Exception:  # noqa: BLE001
                du = "<<EXC>>"
            if dj != du:
                diverging[name] = (dj, du)
        assert set(diverging) == {"d-typed-key", "set-empty", "set-plain"}, sorted(diverging)
        # ...and every one is the SAME class: Django's encoder refuses, djust
        # renders. A cell that flipped to a body divergence would keep its
        # membership and fail here instead of passing quietly.
        for name, (dj, du) in sorted(diverging.items()):
            assert dj == "<<EXC>>", f"{name}: Django no longer refuses ({dj!r})"
            assert du.startswith("<script"), f"{name}: djust no longer renders ({du!r})"


def _load_differential():
    """The differential's corpus, imported for its `INPUTS` only.

    Read from the real file rather than re-listed, so a corpus entry added
    later is swept by the scope claim above without anyone remembering to
    mirror it here.
    """
    path = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "filter-parity-differential.py"
    spec = importlib.util.spec_from_file_location("djust_differential_2413", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
