"""Regression: literal string filter args must not produce JSON-quoted output (#1081).

Issue #1081 reported that ``{{ value|date:"M d, Y" }}`` and
``{{ value|default:"fallback" }}`` rendered with literal double quotes
wrapping the result, e.g. ``"Apr 25, 2026"`` and ``"fallback"``. Root
cause would be the parser preserving surrounding quotes on literal
filter args (intentional, for dep-tracking — see #787) without the
renderer stripping them at render time.

The fix landed in v0.5.2rc1 via ``strip_filter_arg_quotes`` (called from
``render_node_with_loader`` at the two filter-application sites), and a
third inline strip in ``get_value`` for filter chains parsed inside
expressions. This test locks down every shape of literal filter arg the
issue reporter listed across both the issue body and follow-up comments,
plus the equivalent shapes for filter chains and HTML escaping.

If a future refactor regresses any of these, the test fails — preventing
the silent re-emergence of the JSON-quoting bug.
"""

from __future__ import annotations

import html

from djust._rust import RustLiveView, render_template


# ---------------------------------------------------------------------------
# |date with literal format string — issue body
# ---------------------------------------------------------------------------


def test_date_filter_literal_format_M_d_Y():
    """``|date:"M d, Y"`` produces ``Apr 25, 2026`` — no surrounding quotes."""
    out = render_template('{{ d|date:"M d, Y" }}', {"d": "2026-04-25"})
    assert html.unescape(out) == "Apr 25, 2026"
    assert "&quot;" not in out
    assert '"' not in html.unescape(out)


def test_date_filter_literal_format_F_j_Y():
    out = render_template('{{ d|date:"F j, Y" }}', {"d": "2026-04-25"})
    assert html.unescape(out) == "April 25, 2026"
    assert "&quot;" not in out


def test_date_filter_literal_format_single_quoted():
    out = render_template("{{ d|date:'M d, Y' }}", {"d": "2026-04-25"})
    assert html.unescape(out) == "Apr 25, 2026"
    assert "&quot;" not in out
    assert "&#x27;" not in out


def test_date_filter_via_dotted_path():
    """``{{ obj.field|date:"M d, Y" }}`` matches a Django-model field path."""
    out = render_template(
        '{{ claim.filed_date|date:"M d, Y" }}',
        {"claim": {"filed_date": "2026-04-25"}},
    )
    assert html.unescape(out) == "Apr 25, 2026"


# ---------------------------------------------------------------------------
# |default with literal string — issue comment 2 and 3
# ---------------------------------------------------------------------------


def test_default_filter_literal_simple_word():
    out = render_template('{{ x|default:"fallback" }}', {"x": ""})
    assert html.unescape(out) == "fallback"
    assert "&quot;" not in out


def test_default_filter_literal_multi_word():
    """Comment 3 — ``|default:"Claims Examiner"`` was reported as quote-wrapped."""
    out = render_template(
        '{{ user_role_display|default:"Claims Examiner" }}',
        {"user_role_display": ""},
    )
    assert html.unescape(out) == "Claims Examiner"
    assert "&quot;" not in out


def test_default_filter_literal_slash():
    out = render_template('{{ x|default:"N/A" }}', {"x": ""})
    assert html.unescape(out) == "N/A"
    assert "&quot;" not in out


def test_default_filter_literal_em_dash():
    out = render_template('{{ x|default:"—" }}', {"x": ""})
    assert html.unescape(out) == "—"
    assert "&quot;" not in out


def test_default_filter_literal_dash():
    out = render_template('{{ x|default:"-" }}', {"x": ""})
    assert html.unescape(out) == "-"
    assert "&quot;" not in out


def test_default_filter_literal_yes_no():
    out = render_template('{{ x|default:"No" }}', {"x": ""})
    assert html.unescape(out) == "No"


def test_default_filter_single_quoted():
    out = render_template("{{ x|default:'fallback' }}", {"x": ""})
    assert html.unescape(out) == "fallback"
    assert "&#x27;" not in out


def test_default_filter_with_truthy_value_passes_through():
    """Literal arg shouldn't appear at all when value is truthy."""
    out = render_template('{{ x|default:"fallback" }}', {"x": "real"})
    assert html.unescape(out) == "real"
    assert "fallback" not in out


def test_default_filter_none_uses_fallback():
    out = render_template('{{ x|default:"fallback" }}', {"x": None})
    assert html.unescape(out) == "fallback"


# ---------------------------------------------------------------------------
# Filter chains — both filters with literal args
# ---------------------------------------------------------------------------


def test_chain_date_then_default():
    out = render_template(
        '{{ d|date:"M d, Y"|default:"N/A" }}',
        {"d": "2026-04-25"},
    )
    assert html.unescape(out) == "Apr 25, 2026"
    assert "&quot;" not in out


def test_chain_default_then_upper():
    out = render_template(
        '{{ x|default:"fallback"|upper }}',
        {"x": ""},
    )
    assert html.unescape(out) == "FALLBACK"
    assert "&quot;" not in out


# ---------------------------------------------------------------------------
# HTML attribute context — html_escape_attr also escapes ``"``,
# so a literal quote left in the value would surface as ``&quot;``
# inside an attribute. Lock down both contexts.
# ---------------------------------------------------------------------------


def test_default_in_html_attribute_no_quote_wrap():
    out = render_template(
        "<div class=\"{{ cls|default:'box' }}\">x</div>",
        {"cls": ""},
    )
    # Attribute context: class="box", not class="&quot;box&quot;"
    assert 'class="box"' in out
    assert "&quot;" not in out


def test_date_in_html_attribute_no_quote_wrap():
    out = render_template(
        "<time datetime=\"{{ d|date:'Y-m-d' }}\">x</time>",
        {"d": "2026-04-25"},
    )
    assert 'datetime="2026-04-25"' in out
    assert "&quot;" not in out


# ---------------------------------------------------------------------------
# `serialize_context` output shape — added after #1081 was reopened with a
# claim that this Rust function JSON-encodes date values (i.e. produces
# strings *containing* literal `"` characters). Source code at
# `crates/djust_live/src/lib.rs:1776-1781` calls `value.call_method0("isoformat")`
# and passes the resulting `String` straight through `into_pyobject` — no
# `serde_json::to_string`, no quote-wrapping. Lock that contract here so a
# future "let's wrap dates in JSON for the wire" refactor can't silently
# regress to producing literal-quote-wrapped output.
# ---------------------------------------------------------------------------


def test_serialize_context_date_is_bare_iso_string():
    """``serialize_context`` returns the bare ISO string, not JSON-quoted."""
    from datetime import date

    from djust._rust import serialize_context

    out = serialize_context({"d": date(2026, 4, 25)})
    # Bare 10-character ISO string. NOT '"2026-04-25"' (12 chars with embedded quotes).
    assert out["d"] == "2026-04-25"
    assert len(out["d"]) == 10
    assert '"' not in out["d"]


def test_serialize_context_datetime_is_bare_iso_string():
    from datetime import datetime

    from djust._rust import serialize_context

    out = serialize_context({"dt": datetime(2026, 4, 25, 14, 30, 0)})
    assert out["dt"].startswith("2026-04-25T14:30:00")
    assert '"' not in out["dt"]


def test_serialize_context_nested_date_in_list_of_dicts():
    """Mirrors the queryset-of-models reproduction path from the #1081 reopen."""
    from datetime import date

    from djust._rust import serialize_context

    out = serialize_context(
        {
            "tasks": [
                {"id": 1, "due_date": date(2026, 5, 3)},
                {"id": 2, "due_date": date(2026, 5, 10)},
            ]
        }
    )
    assert out["tasks"][0]["due_date"] == "2026-05-03"
    assert out["tasks"][1]["due_date"] == "2026-05-10"
    for task in out["tasks"]:
        assert '"' not in task["due_date"]


# ---------------------------------------------------------------------------
# Full ``LiveView.render()`` with Django-Model + DateField, exercising the
# JIT-serializer path (`_jit_serialize_model` / `_jit_serialize_queryset`).
# This is the path the issue reporter named as the source of the JSON-quoting.
# ---------------------------------------------------------------------------


def test_liveview_render_with_django_model_datefield_no_quote_wrap(db):
    """Real LiveView render of a Django Model with DateField + ``|date`` filter."""
    from datetime import date

    from django.contrib.auth import get_user_model

    from djust import LiveView

    user_model = get_user_model()
    user = user_model(
        id=1, username="amanda", first_name="Amanda", last_name="Smith", email="a@b.c"
    )
    user.date_joined = date(2026, 5, 3)

    class V(LiveView):
        template = (
            "<div>\n"
            'A: {{ u.date_joined|date:"M d, Y" }}\n'
            'B: {{ u.first_name|default:"FALLBACK" }}\n'
            'C: {{ u.email|default:"—" }}\n'
            "</div>\n"
        )

        def get_context_data(self, **kwargs):
            return {"u": user}

    out = V().render()
    body = out[out.find("<div") : out.find("</div>") + 6]
    assert "A: May 03, 2026" in body
    assert "B: Amanda" in body
    assert "C: a@b.c" in body
    assert "&quot;" not in body
    # Visible-DOM body should contain none of the JSON-encoded forms reported
    assert '"May 03, 2026"' not in body
    assert '"Amanda"' not in body


def test_liveview_render_with_list_of_models_jit_path_no_quote_wrap(db):
    """JIT-serialized list-of-Model-instances path with ``|date`` and ``|default``."""
    from datetime import date

    from django.contrib.auth import get_user_model

    from djust import LiveView

    user_model = get_user_model()
    u1 = user_model(id=1, username="a", first_name="Amanda", email="a@b.c")
    u1.date_joined = date(2026, 5, 3)
    u2 = user_model(id=2, username="b", first_name="Brian", email="")
    u2.date_joined = date(2026, 6, 15)

    class V(LiveView):
        template = (
            "<ul>\n"
            "{% for u in users %}"
            '<li>{{ u.date_joined|date:"M d, Y" }} :: '
            '{{ u.email|default:"NONE" }} :: '
            '{{ u.first_name|default:"—" }}</li>'
            "{% endfor %}\n"
            "</ul>\n"
        )

        def get_context_data(self, **kwargs):
            return {"users": [u1, u2]}

    out = V().render()
    body = out[out.find("<ul") : out.find("</ul>") + 5]
    assert "May 03, 2026 :: a@b.c :: Amanda" in body
    assert "Jun 15, 2026 :: NONE :: Brian" in body
    assert "&quot;" not in body
    assert '"May 03, 2026"' not in body
    assert '"NONE"' not in body
    assert '"Amanda"' not in body


# ---------------------------------------------------------------------------
# ``render_with_diff`` — the WebSocket-update path that produces VDOM patches.
# The issue reporter described filter outputs being "inserted as JSON string
# values into the VDOM" — this test locks down that the VDOM-patch path
# produces unquoted output identical to the initial-render path.
# ---------------------------------------------------------------------------


def test_render_with_diff_full_no_quote_wrap(db):
    """First-call ``render_with_diff`` (full render) produces unquoted HTML."""
    from datetime import date

    from django.contrib.auth import get_user_model

    from djust import LiveView

    user_model = get_user_model()
    user = user_model(id=1, username="a", first_name="Amanda", last_name="S", email="a@b.c")
    user.date_joined = date(2026, 5, 3)

    class V(LiveView):
        template = (
            "<div>\n"
            'A: {{ u.date_joined|date:"M d, Y" }}\n'
            'B: {{ u.first_name|default:"FALLBACK" }}\n'
            "</div>\n"
        )

        def get_context_data(self, **kwargs):
            return {"u": user}

    v = V()
    html, patches, version = v.render_with_diff()
    assert "A: May 03, 2026" in html
    assert "B: Amanda" in html
    assert "&quot;" not in html
    assert '"May 03, 2026"' not in html
    assert version == 1


def test_render_with_diff_tab_navigation_no_quote_wrap(db):
    """Multi-step tab-navigation flow that the #1081 reopen named as the bug trigger.

    Sequence (mirrors ``dj-patch`` URL-change navigation):
      1. Initial mount, ``tab=summary`` — full render.
      2. Navigate to ``tab=documents`` — partial render produces SetText patches.
      3. Navigate BACK to ``tab=summary`` — partial render again.

    Reporter claims dates render with literal ``"`` characters on step 3.
    Lock the invariant that every step produces unquoted output AND that
    every SetText patch's ``text`` field is a bare unquoted string.
    """
    from datetime import date

    from django.contrib.auth import get_user_model

    from djust import LiveView

    user_model = get_user_model()
    user = user_model(id=1, username="a", first_name="Amanda", last_name="S", email="a@b.c")
    user.date_joined = date(2026, 5, 3)

    class TabbedView(LiveView):
        template = (
            "<div>\n"
            "Tab: {{ tab }}\n"
            'Date: {{ u.date_joined|date:"M d, Y" }}\n'
            'Email: {{ u.email|default:"N/A" }}\n'
            "</div>\n"
        )

        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.tab = "summary"

        def get_context_data(self, **kwargs):
            return {"tab": self.tab, "u": user}

    import json as _json

    v = TabbedView()

    # Step 1: initial mount
    html1, _patches1, ver1 = v.render_with_diff()
    assert "Date: May 03, 2026" in html1
    assert "&quot;" not in html1
    assert ver1 == 1

    # Step 2: nav to ``tab=documents``
    v.tab = "documents"
    html2, patches2, ver2 = v.render_with_diff()
    assert "Date: May 03, 2026" in html2
    assert "&quot;" not in html2
    assert ver2 == 2
    if patches2:
        for p in _json.loads(patches2):
            if p.get("type") == "SetText":
                assert '"May 03, 2026"' not in p["text"]
                assert "&quot;" not in p["text"]

    # Step 3: nav BACK to ``tab=summary`` (the path the reporter named)
    v.tab = "summary"
    html3, patches3, ver3 = v.render_with_diff()
    assert "Date: May 03, 2026" in html3
    assert "&quot;" not in html3
    assert ver3 == 3
    if patches3:
        for p in _json.loads(patches3):
            if p.get("type") == "SetText":
                # Bare string in the patch text — no JSON-encoded quotes.
                assert '"May 03, 2026"' not in p["text"]
                assert "&quot;" not in p["text"]


def test_render_with_diff_partial_no_quote_wrap(db):
    """Second-call ``render_with_diff`` after state mutation (partial path) — same invariant."""
    from datetime import date

    from django.contrib.auth import get_user_model

    from djust import LiveView

    user_model = get_user_model()
    user = user_model(id=1, username="a", first_name="Amanda", last_name="S", email="a@b.c")
    user.date_joined = date(2026, 5, 3)

    class V(LiveView):
        template = (
            '<div>\nA: {{ u.date_joined|date:"M d, Y" }}\nB: {{ counter|default:"0" }}\n</div>\n'
        )

        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.counter = 0

        def get_context_data(self, **kwargs):
            return {"u": user, "counter": self.counter}

    v = V()
    v.render_with_diff()  # version 1 — full render, populates fragment cache
    v.counter = 7
    html, _patches, version = v.render_with_diff()  # version 2 — partial path
    assert "A: May 03, 2026" in html
    assert "&quot;" not in html
    assert '"May 03, 2026"' not in html
    assert version == 2


# ---------------------------------------------------------------------------
# The exact ``render_full_template`` data flow named in the third reopen of
# #1081: ``date`` Python object → ``normalize_django_value`` → ISO string →
# ``RustLiveView.update_state(json_ctx)`` → ``temp_rust.render()``. The
# reporter claimed this path produces literal-quote-wrapped output because
# the Rust ``|date`` filter "doesn't know it's looking at a date" once the
# value has been normalized to a string. Lock the contract that the Rust
# ``|date`` filter operating on an ISO-string value produces unquoted
# output, exactly mirroring the behavior of the same filter on a native
# ``datetime.date`` object.
# ---------------------------------------------------------------------------


def test_normalize_then_rust_update_state_no_quote_wrap():
    """``normalize_django_value`` → ``RustLiveView.update_state`` → render.

    Mirrors ``render_full_template`` (`mixins/template.py:525`) line-by-line.
    """
    from datetime import date

    from djust._rust import RustLiveView

    from djust.serialization import normalize_django_value

    template = (
        '<div>\nA: {{ claim.filed_date|date:"M d, Y" }}\nB: {{ claim.notes|default:"—" }}\n</div>\n'
    )

    ctx = {"claim": {"filed_date": date(2026, 5, 3), "notes": ""}}
    json_ctx = normalize_django_value(ctx)

    # After normalize, filed_date is a str — the exact precondition the
    # reporter described as the bug trigger.
    assert isinstance(json_ctx["claim"]["filed_date"], str)
    assert json_ctx["claim"]["filed_date"] == "2026-05-03"

    rv = RustLiveView(template)
    rv.update_state(json_ctx)
    out = rv.render()

    # Rust ``|date`` filter on the ISO string produces unquoted output.
    assert "A: May 03, 2026" in out
    assert "B: —" in out
    assert "&quot;" not in out
    assert '"May 03, 2026"' not in out
    assert '"—"' not in out


# ---------------------------------------------------------------------------
# Embedded-quote input — the actual root cause surfaced by the third reopen
# of #1081. When upstream code (a custom ``BaseLiveView`` override, a
# ``JSONField`` storing a JSON-encoded string, etc.) passes a value with
# literal ``"`` characters into ``update_state``, the framework correctly:
#  (1) attempts to parse it as a date via the ``|date`` filter,
#  (2) fails (the embedded-quote string isn't a valid date),
#  (3) returns the value unchanged from the filter,
#  (4) HTML-escapes the literal ``"`` characters to ``&quot;``.
# This is correct, defensive behavior. Lock the contract so a future "let's
# silently strip embedded quotes from filter inputs" refactor can't sneak
# through (that would be a real correctness regression — the ``"`` chars
# may be load-bearing for non-date data).
# ---------------------------------------------------------------------------


def test_date_filter_on_embedded_quote_string_html_escapes_quotes():
    """Date string with embedded literal quotes — ``|date`` parse fails, HTML escape preserves chars.

    The string ``'"2026-04-25"'`` (10 chars + 2 surrounding ``"``) is what
    ``json.dumps(date.isoformat())`` produces, and it's the symptom the
    #1081 reporter actually had after their custom upstream serialization
    JSON-encoded the date before reaching ``update_state``.
    """
    template = "{% for c in claims %}{{ c.filed_date|date:'M d, Y' }}|{% endfor %}"

    rv = RustLiveView(template)
    rv.update_state({"claims": [{"filed_date": '"2026-04-25"'}]})
    out = rv.render()

    # Filter parse fails on embedded-quote string; value passes through;
    # HTML escape converts the ``"`` chars to ``&quot;``.
    assert out == "&quot;2026-04-25&quot;|"
    # Sanity: the framework is *not* silently swallowing the quote chars —
    # they're correctly preserved through HTML escaping.
    assert "&quot;" in out
    # Sanity: the date filter did NOT format the value (because parse failed).
    assert "Apr 25" not in out


def test_date_filter_on_clean_iso_string_unquoted_output():
    """Companion to the test above — the matching clean-input case.

    Same template, same pipeline, but the input is a clean ISO string
    ``'2026-04-25'``. Filter parses successfully and produces
    ``'Apr 25, 2026'`` with NO embedded quotes. This is the contract the
    #1081 reporter expected; the test above is the contract for what
    happens when upstream code violates that input shape.
    """
    template = "{% for c in claims %}{{ c.filed_date|date:'M d, Y' }}|{% endfor %}"

    rv = RustLiveView(template)
    rv.update_state({"claims": [{"filed_date": "2026-04-25"}]})
    out = rv.render()

    assert out == "Apr 25, 2026|"
    assert "&quot;" not in out
    assert '"' not in out
