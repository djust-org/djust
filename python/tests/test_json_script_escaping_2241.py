"""`json_script` must emit parseable JSON for every string it carries (#2241).

The bug: `value_to_json`'s object-KEY path kept its own partial escape chain
(`\\` and `"` only) after the `String` and `Decimal` arms had converged on
`json_string_body`. A dict key holding a newline emitted a raw control
character, so the `<script type="application/json">` body did not parse:

    {"a\\nb": "v"}  ->  json.loads: Invalid control character at char 3

Related and hit in EVERY arm, key and value alike: the control characters
without a short form (`0x00`–`0x1F` other than `\\n`/`\\r`/`\\t`) were raw too,
so `{"k": "a\\x00b"}` did not parse either.

**The assertion is the round trip**, never a substring of the escaped output.
`json.loads(body) == original` cannot pass for the wrong reason: it fails if an
escape is missing (a parse error) and it fails if an escape is wrong (a value
mismatch), which a `'\\\\n' in body` check does neither of.

Two escape mechanisms live in `json_string_body` and this file keeps both
independently reachable (#2129/#2135):

* the generic `\\u00XX` arm, covering the whole control range — pinned by the
  round-trip tests, which go red the moment any control character escapes raw;
* the five short forms (`\\n \\r \\t \\b \\f`), which the generic arm would
  otherwise SHADOW — dropping `'\\n' => "\\\\n"` still yields valid, round-tripping
  `\\u000a`. They are pinned instead by the `json.dumps` differential, because
  their reason to exist is byte-parity with Python's encoder, not validity.

What this file deliberately does NOT assert: `<`, `>`, `&`, U+2028 and U+2029.
`json_script` composes two functions — `json_string_body` per string, then
`json_escape_for_script` over the assembled document — and those five belong to
the second. The differential excludes them for that reason; the round-trip
tests include them, since the composed output must parse either way.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

pytest.importorskip("django")

from djust import _rust  # noqa: E402

#: Characters `json_escape_for_script` rewrites AFTER `json_string_body` has
#: run. Excluded from the byte-parity differential only — `json.dumps` leaves
#: them alone, so a difference there is the composition working as designed.
SCRIPT_STAGE_CHARS = ("<", ">", "&", "\u2028", "\u2029")


def script_body(value: object) -> str:
    """The JSON text `{{ v|json_script:"x" }}` puts inside the script element."""
    out = _rust.render_template('{{ v|json_script:"x" }}', {"v": value})
    return out[out.index(">") + 1 : out.rindex("</script>")]


def assert_round_trips(value: object) -> str:
    """`json.loads` of the rendered body must reproduce `value` exactly."""
    body = script_body(value)
    try:
        parsed = json.loads(body)
    except ValueError as exc:  # pragma: no cover - the failure path is the point
        raise AssertionError(f"{value!r} rendered unparseable JSON: {body!r} ({exc})") from exc
    assert parsed == value, f"{value!r} round-tripped as {parsed!r} via {body!r}"
    return body


# ---------------------------------------------------------------------------
# The reported case, verbatim.
# ---------------------------------------------------------------------------


def test_a_newline_in_a_dict_key_parses() -> None:
    """The issue's measured reproduction: `{"a\\nb": "v"}` raised on `json.loads`.

    Before the fix the body was a literal `{"a<LF>b": "v"}` — the key path
    escaped `\\` and `"` and nothing else.
    """
    body = assert_round_trips({"a\nb": "v"})
    assert "\n" not in body, f"a raw newline survived into the script body: {body!r}"


def test_a_nul_in_a_value_parses() -> None:
    """The second half of the issue: no arm escaped `0x00`–`0x1F` without a short form."""
    body = assert_round_trips({"k": "a\x00b"})
    assert "\x00" not in body, f"a raw NUL survived into the script body: {body!r}"


# ---------------------------------------------------------------------------
# Every variant of the surface, not a sample of it (v1.0.0rc4 finding #1).
# The control range is 32 characters; enumerate all of them rather than
# picking the three with short forms.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("code", range(0x00, 0x20))
def test_every_control_character_parses_in_a_key(code: int) -> None:
    assert_round_trips({f"a{chr(code)}b": "v"})


@pytest.mark.parametrize("code", range(0x00, 0x20))
def test_every_control_character_parses_in_a_value(code: int) -> None:
    assert_round_trips({"k": f"a{chr(code)}b"})


#: The hostile characters the issue names, each exercised in both positions.
#: `\x7f` is here to pin that it is left RAW — JSON permits DEL unescaped, and
#: `json.dumps(ensure_ascii=False)` emits it raw, so escaping it would be a
#: gratuitous divergence rather than a fix.
HOSTILE = {
    "newline": "a\nb",
    "carriage-return": "a\rb",
    "tab": "a\tb",
    "backspace": "a\bb",
    "form-feed": "a\fb",
    "backslash": "a\\b",
    "quote": 'a"b',
    "nul": "a\x00b",
    "unit-separator": "a\x1fb",
    "delete": "a\x7fb",
    "line-separator": "a\u2028b",
    "paragraph-separator": "a\u2029b",
    "script-close": "a</script>b",
    "json-injection": '","admin":true,"x":"',
    "all-at-once": '\\"\n\r\t\x00\x1f\x7f\u2028</script>',
}


@pytest.mark.parametrize("payload", HOSTILE.values(), ids=list(HOSTILE))
def test_hostile_payload_parses_as_a_key(payload: str) -> None:
    assert_round_trips({payload: "v"})


@pytest.mark.parametrize("payload", HOSTILE.values(), ids=list(HOSTILE))
def test_hostile_payload_parses_as_a_value(payload: str) -> None:
    assert_round_trips({"k": payload})


@pytest.mark.parametrize("payload", HOSTILE.values(), ids=list(HOSTILE))
def test_hostile_payload_parses_as_a_bare_string(payload: str) -> None:
    """The `String` arm reached directly, with no object around it."""
    assert_round_trips(payload)


@pytest.mark.parametrize("payload", HOSTILE.values(), ids=list(HOSTILE))
def test_hostile_payload_parses_when_nested(payload: str) -> None:
    """Keys and values at depth, inside both container arms.

    `value_to_json` recurses, so a top-level-only test would leave the nested
    key path — the one an ORM-shaped payload actually reaches — unexercised.
    """
    assert_round_trips({"outer": {payload: [payload, {payload: payload}]}})


def test_a_json_injection_key_cannot_add_structure() -> None:
    """The key half of the injection the `Decimal` arm was fixed for (#2214)."""
    payload = '","admin":true,"x":"'
    body = assert_round_trips({payload: "v"})
    assert '"admin":true' not in body, f"JSON structure injected via a key: {body!r}"


def test_a_tagged_decimal_escapes_the_whole_control_range() -> None:
    """The `Decimal` arm — a `Value::Decimal` can hold an arbitrary string (#2214).

    #2214's own regression case covers `\\ " \\n \\r \\t`; it stayed green with a
    raw `0x00` because the escape chain had no generic arm. Driven through
    msgpack because that is how a Decimal acquires a payload the decimal parser
    never saw.
    """
    from djust._rust import RustLiveView

    payload = 'a\\b"c\nd\re\tf\x00g\x1fh'
    view = RustLiveView('{{ p|json_script:"d" }}')
    view.set_state("p", {"__djust_decimal__": payload})
    restored = RustLiveView.deserialize_msgpack(view.serialize_msgpack())

    out = restored.render()
    body = out[out.index(">") + 1 : out.rindex("</script>")]
    assert json.loads(body) == payload, body


# ---------------------------------------------------------------------------
# Byte-parity differential against `json.dumps`.
#
# A curated table samples the axis you thought of; Python's encoder is
# importable, so there is no reason to guess (v1.1.1-2 retro). This is also the
# only thing that pins the five short forms, which the generic `\uXXXX` arm
# would otherwise shadow.
# ---------------------------------------------------------------------------

#: Every control character, the two structural escapes, an ASCII spread, DEL,
#: and two multi-byte characters — minus the five `json_escape_for_script`
#: claims, which are not `json_string_body`'s to emit.
DIFFERENTIAL_ALPHABET = [
    c
    for c in ([chr(code) for code in range(0x00, 0x22)] + list('"\\/aZ9 -\x7f\u00e9\u4e16'))
    if c not in SCRIPT_STAGE_CHARS
]


def test_the_escaped_body_is_byte_identical_to_json_dumps() -> None:
    """4,000 random payloads x 3 shapes, against the reference encoder.

    Only single-key objects: `value_to_json` sorts keys and `json.dumps` keeps
    insertion order, a separate, deliberate divergence (documented in
    `value_to_json`) that a multi-key comparison would trip over.

    Gate-off note: this is the test that distinguishes `\\n` from `\\u000a`.
    Dropping any short-form arm leaves every round-trip test green and turns
    this one red.
    """
    rng = random.Random(2241)

    def payload() -> str:
        return "".join(rng.choice(DIFFERENTIAL_ALPHABET) for _ in range(rng.randint(0, 6)))

    for _ in range(4000):
        key, value = payload(), payload()
        for shape in (value, {key: value}, [value, {key: [value]}]):
            got = script_body(shape)
            want = json.dumps(shape, ensure_ascii=False)
            assert got == want, f"{shape!r}: rust {got!r} != json.dumps {want!r}"


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ("a\nb", r'"a\nb"'),
        ("a\rb", r'"a\rb"'),
        ("a\tb", r'"a\tb"'),
        ("a\bb", r'"a\bb"'),
        ("a\fb", r'"a\fb"'),
        ("a\x00b", r'"a\u0000b"'),
        ("a\x1fb", r'"a\u001fb"'),
        ("a\x7fb", '"a\x7fb"'),
    ],
)
def test_the_short_forms_are_the_ones_json_dumps_uses(payload: str, expected: str) -> None:
    """The five short forms, named — and the two neighbours that must NOT get one.

    Spelled out rather than left to the sweep so a failure names the arm.
    """
    assert script_body(payload) == expected
    assert script_body(payload) == json.dumps(payload, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Structural pin: one helper, mechanically.
#
# The partial chain this issue fixes survived a convergence that had ALREADY
# named it. A comment naming a gap does not close it; a test that goes red when
# a third chain reappears does (#1646/#1859).
# ---------------------------------------------------------------------------

FILTERS_RS = (
    Path(__file__).resolve().parents[2] / "crates" / "djust_templates" / "src" / "filters.rs"
)


def _value_to_json_source() -> str:
    source = FILTERS_RS.read_text(encoding="utf-8")
    start = source.index("fn value_to_json(")
    end = source.index("\nfn ", start)
    return source[start:end]


def test_value_to_json_escapes_every_string_through_the_one_helper() -> None:
    """Three quoted-string sites — `Decimal`, `String`, the object key — one helper."""
    body = _value_to_json_source()
    assert body.count("json_string_body(") == 3, (
        "value_to_json should escape exactly its three quoted-string sites "
        f"through json_string_body; found {body.count('json_string_body(')}"
    )


def test_value_to_json_has_no_escape_chain_of_its_own() -> None:
    """A local `.replace(` inside `value_to_json` is how the key path drifted."""
    body = _value_to_json_source()
    assert ".replace(" not in body, (
        "value_to_json grew an inline escape chain again — route it through "
        "json_string_body instead (#2241)"
    )
