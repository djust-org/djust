"""Integration tests for #968 — Rust renderer honors the ``"__str__"``
key on serialized model dicts.

The Python-side serializer (`djust.serialization._serialize_model_safely`)
sets ``"__str__": str(obj)`` on every dict it produces so Rust-engine
templates can match Django's default ``{{ obj }}`` semantics. Before
the #968 fix, the Rust `Value::Object` Display impl emitted the
literal ``"[Object]"`` for any dict, silently breaking FK display:
``{{ claim.claimant }}`` (where `claimant` serializes to a nested
dict) rendered as ``[Object]`` instead of the claimant's string.

These tests exercise the Rust renderer via the PyO3 `render_template`
entry point so the full Python→Rust→Display path is covered. Native
Rust unit tests for `Value::Object` Display live in
`crates/djust_core/src/lib.rs` alongside the impl.
"""

from __future__ import annotations

from djust._rust import render_template


class TestModelDictStrKey:
    """``Value::Object`` with ``"__str__"`` key → renders the string."""

    def test_model_dict_renders_str_value(self):
        """Canonical case: ``{{ claim }}`` on a serialized-model dict."""
        out = render_template(
            "{{ claim }}",
            {"claim": {"id": 1, "__str__": "Claim 2026PD000075", "claim_number": "2026PD000075"}},
        )
        assert out == "Claim 2026PD000075"

    def test_model_dict_str_value_is_html_escaped(self):
        """Auto-escape applies to ``__str__`` output — `<Claim: ...>` → `&lt;Claim: ...&gt;`."""
        out = render_template(
            "{{ claim }}",
            {"claim": {"__str__": "<Claim: 2026PD000075>"}},
        )
        # Full HTML escape: angle brackets encoded.
        assert out == "&lt;Claim: 2026PD000075&gt;"

    def test_model_dict_dotted_access_unchanged(self):
        """Dotted access to scalar fields still works; doesn't hit ``__str__``."""
        out = render_template(
            "{{ claim.claim_number }}",
            {"claim": {"__str__": "parent-str", "claim_number": "ABC"}},
        )
        assert out == "ABC"

    def test_nested_fk_dict_renders_str(self):
        """Nested FK (the #968 canonical bug): ``{{ claim.claimant }}`` →
        claimant's ``__str__`` instead of ``[Object]``."""
        out = render_template(
            "{{ claim.claimant }}",
            {
                "claim": {
                    "__str__": "parent",
                    "claimant": {"__str__": "John Doe", "first_name": "John"},
                }
            },
        )
        assert out == "John Doe"


class TestPlainDictFallback:
    """Dicts WITHOUT a string ``"__str__"`` key keep rendering ``[Object]``
    — non-model data (context dict passed directly, etc.) was never meant
    to hit ``__str__`` semantics."""

    def test_plain_dict_no_str_key(self):
        out = render_template("{{ x }}", {"x": {"a": 1, "b": 2}})
        assert out == "[Object]"

    def test_empty_dict(self):
        out = render_template("{{ x }}", {"x": {}})
        assert out == "[Object]"

    def test_str_key_is_none(self):
        """``"__str__": None`` → treat as absent; fall back to ``[Object]``."""
        out = render_template("{{ x }}", {"x": {"__str__": None, "id": 1}})
        assert out == "[Object]"

    def test_str_key_is_integer(self):
        """``"__str__": 42`` → not a String variant; fall back. Guards
        against Display emitting coerced type names."""
        out = render_template("{{ x }}", {"x": {"__str__": 42}})
        assert out == "[Object]"


class TestStrKeyEdgeCases:
    """Edge cases around the ``"__str__"`` key resolution."""

    def test_empty_str_value_renders_empty(self):
        """Django semantic: if ``str(obj) == ''`` the template renders
        empty. Rust engine must match — not fall back to ``[Object]``."""
        out = render_template("{{ x }}", {"x": {"__str__": ""}})
        assert out == ""

    def test_str_key_among_many_fields(self):
        """``__str__`` resolution works regardless of dict ordering or
        number of other keys."""
        big = {f"field_{i}": i for i in range(20)}
        big["__str__"] = "canonical"
        out = render_template("{{ x }}", {"x": big})
        assert out == "canonical"


class TestBackwardsCompatibility:
    """Paths that worked before the fix must still work."""

    def test_plain_python_object_str_unchanged(self):
        """Plain Python objects with ``__str__`` continued to work
        through `FromPyObject` — string-extract path. #968 was about
        the dict case specifically; the object case was already
        correct."""

        class Obj:
            def __str__(self):
                return "My Custom Str"

        out = render_template("{{ x }}", {"x": Obj()})
        assert out == "My Custom Str"

    def test_list_still_renders_list_placeholder(self):
        """``[List]`` fallback for lists unchanged — only `Value::Object`
        was touched."""
        out = render_template("{{ x }}", {"x": [1, 2, 3]})
        assert out == "[List]"

    def test_scalar_types_unchanged(self):
        """Sanity: integer/bool/string/null Display unchanged.

        Note: Rust's built-in ``{}`` format for bool emits lowercase
        ``true`` / ``false`` — pre-existing djust behavior, out of
        scope for #968. This test locks the current (pre-fix) behavior
        so #968's change to the `Object` arm doesn't accidentally
        regress any other variant.
        """
        assert render_template("{{ x }}", {"x": 42}) == "42"
        assert render_template("{{ x }}", {"x": True}) == "true"
        assert render_template("{{ x }}", {"x": "hello"}) == "hello"
        assert render_template("{{ x }}", {"x": None}) == ""
