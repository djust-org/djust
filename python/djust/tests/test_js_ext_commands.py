"""ADR-025 Feature B: JS.ext.* dynamic custom-command factory.

Wire-format pins included per the #1448 rule: the ops array is a
Python<->JS contract; Task 2's client dispatch parses exactly these strings.
"""

import pytest

from djust.js import _BUILTIN_OPS, JS, JSChain, _JSFactory


class TestExtWireFormat:
    """Pin the exact serialized JSON (#1448 wire-contract class)."""

    def test_ext_op_serializes_with_prefix(self):
        chain = JS.ext.scroll_to(to="#top", smooth=True)
        assert chain.to_json() == '[["ext.scroll_to",{"to":"#top","smooth":true}]]'

    def test_ext_op_no_target_no_params(self):
        assert JS.ext.confetti().to_json() == '[["ext.confetti",{}]]'

    def test_ext_op_inner_target(self):
        assert JS.ext.highlight(inner=".row").to_json() == '[["ext.highlight",{"inner":".row"}]]'

    def test_ext_op_closest_target(self):
        assert (
            JS.ext.collapse(closest=".card").to_json() == '[["ext.collapse",{"closest":".card"}]]'
        )

    def test_ext_op_json_value_types(self):
        chain = JS.ext.chart_update(
            points=[1, 2, 3], config={"animate": True}, count=7, label="Sales"
        )
        assert chain.to_json() == (
            '[["ext.chart_update",{"points":[1,2,3],'
            '"config":{"animate":true},"count":7,"label":"Sales"}]]'
        )


class TestExtChaining:
    def test_ext_then_builtin(self):
        chain = JS.ext.scroll_to(to="#top").add_class("flash", to="#header")
        assert chain.ops == [
            ["ext.scroll_to", {"to": "#top"}],
            ["add_class", {"to": "#header", "names": "flash"}],
        ]

    def test_builtin_then_ext(self):
        chain = JS.hide("#modal").ext.scroll_to(to="#top")
        assert chain.ops == [
            ["hide", {"to": "#modal"}],
            ["ext.scroll_to", {"to": "#top"}],
        ]

    def test_chains_are_immutable(self):
        base = JS.ext.confetti()
        extended = base.add_class("x")
        assert len(base.ops) == 1
        assert len(extended.ops) == 2


class TestExtGuards:
    def test_builtin_name_under_ext_raises(self):
        with pytest.raises(AttributeError, match="use JS.show"):
            JS.ext.show

    def test_all_builtin_names_blocked_under_ext(self):
        for name in _BUILTIN_OPS:
            with pytest.raises(AttributeError):
                getattr(JS.ext, name)

    def test_private_name_raises(self):
        with pytest.raises(AttributeError):
            JS.ext._sneaky

    def test_non_identifier_raises(self):
        with pytest.raises(AttributeError):
            getattr(JS.ext, "has-dash")

    def test_builtin_typo_still_raises_on_factory(self):
        """Regression pin: ADR-025 must NOT loosen the strict built-in surface."""
        with pytest.raises(AttributeError):
            JS.shwo  # noqa: B018

    def test_target_exclusivity_enforced(self):
        with pytest.raises(ValueError, match="at most one"):
            JS.ext.fancy(to="#a", inner=".b")

    def test_builtin_ops_set_matches_factory_methods(self):
        """_BUILTIN_OPS must stay in sync with _JSFactory's public methods."""
        factory_methods = {
            n for n, v in vars(_JSFactory).items() if not n.startswith("_") and callable(v)
        }
        assert factory_methods == set(_BUILTIN_OPS)

    def test_camel_case_name_raises(self):
        """snake_case is enforced — a camelCase name would create ops the
        client can never satisfy (review finding: collision asymmetry)."""
        with pytest.raises(AttributeError, match="snake_case"):
            JS.ext.scrollTo  # noqa: B018

    def test_camelcase_builtin_alias_raises(self):
        """The client blocks registering camelCase aliases (addClass etc.);
        the Python builder must not construct those unsatisfiable ops."""
        with pytest.raises(AttributeError):
            JS.ext.addClass  # noqa: B018


class TestExtTemplateInterpolation:
    def test_str_matches_to_json(self):
        chain = JS.ext.scroll_to(to="#top")
        assert str(chain) == chain.to_json()

    def test_chain_ext_returns_jschain(self):
        assert isinstance(JS.ext.anything_at_all(), JSChain)
