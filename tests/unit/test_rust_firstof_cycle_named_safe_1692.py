"""Regression: {% firstof %}/{% cycle %} must honor the NAME-BASED
``safe_output_filters`` whitelist (#1692, completing the #1660→#1672 lineage).

#1672 (PR #1691) added ``get_value_safe`` to thread RUNTIME ``mark_safe()``-ness
through the ``{% firstof %}`` / ``{% cycle %}`` emit path. But it did not consult
the name-based ``safe_output_filters`` list (``safe``, ``urlize``, ...) that the
``Node::Variable`` render arm uses. So ``{% firstof x|safe %}`` and
``{% cycle x|urlize %}`` were still over-escaped.

The fix marks the value safe when the applied filter NAME is in the established
whitelist — mirroring the Variable arm exactly. It is FAIL-SAFE: only ever adds
safeness for whitelisted names; a plain filter (e.g. ``upper``) stays escaped.
"""

from __future__ import annotations

from djust._rust import render_template


def test_firstof_safe_filter_not_double_escaped():
    # {% firstof x|safe %} — `safe` is a name-based safe_output_filter.
    out = render_template("{% firstof x|safe %}", {"x": "<b>hi</b>"})
    assert out == "<b>hi</b>"


def test_cycle_urlize_filter_not_double_escaped():
    # urlize produces its own <a href=...> HTML; must not be re-escaped.
    out = render_template("{% cycle x|urlize %}", {"x": "Visit https://example.com"})
    assert '<a href="https://example.com"' in out
    assert "&lt;a" not in out


def test_firstof_nonsafe_filter_still_escaped():
    # `upper` is NOT a safe_output_filter — HTML in its output stays escaped.
    out = render_template("{% firstof x|upper %}", {"x": "<b>hi</b>"})
    assert out == "&lt;B&gt;HI&lt;/B&gt;"


def test_firstof_safe_then_plain_filter_re_taints():
    # LAST-filter semantics: `upper` is last and not safe → re-tainted, escaped.
    out = render_template("{% firstof x|safe|upper %}", {"x": "<b>hi</b>"})
    assert out == "&lt;B&gt;HI&lt;/B&gt;"
