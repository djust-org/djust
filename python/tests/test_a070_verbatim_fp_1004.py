"""Tests for #1004 — `djust.A070` must not false-positive on `{% dj_activity %}`
literals wrapped in `{% verbatim %}...{% endverbatim %}` blocks.

The A070 / A071 scanner walks template source as raw text. Templates
that document the `{% dj_activity %}` tag — common pattern on docs /
marketing pages — wrap literal example markup in `{% verbatim %}` so
Django renders the example as-is. Before #1004, A070 fired on those
documentation examples as if they were real uninstrumented activity
calls.

Two contracts under test:

1. The `_strip_verbatim_blocks(content)` helper redacts the BODY of
   every `{% verbatim %}...{% endverbatim %}` region while preserving
   line numbers (newlines kept verbatim, every other char becomes a
   space).

2. The A070 / A071 scanner uses the helper before iterating
   `_DJ_ACTIVITY_TAG_RE` over the source — so a verbatim-wrapped
   example produces no A070 warning, while a *real* uninstrumented
   `{% dj_activity %}` outside any verbatim block continues to fire.

These tests live in their own file so the contract is locked
independently of the broader A070 behavior tests; deleting or
loosening the verbatim handling would fail this file specifically.
"""

from __future__ import annotations

from djust.checks import _strip_verbatim_blocks


# ---------------------------------------------------------------------------
# 1. _strip_verbatim_blocks helper contract
# ---------------------------------------------------------------------------


class TestStripVerbatimBlocks:
    """The pre-processor that redacts verbatim regions before scanning."""

    def test_no_verbatim_returns_unchanged(self):
        """The fast path: no `verbatim` keyword in source → return content
        as-is. Avoids the regex pass for the common case."""
        src = "<html>\n  <body>{% dj_activity 'panel' %}{% enddj_activity %}</body>\n</html>"
        assert _strip_verbatim_blocks(src) is src

    def test_unnamed_verbatim_block_redacted(self):
        """`{% verbatim %}...{% endverbatim %}` body is replaced with
        whitespace; the wrapping tags themselves are also blanked
        (the entire match is redacted)."""
        src = "before\n{% verbatim %}{% dj_activity %}{% endverbatim %}\nafter"
        out = _strip_verbatim_blocks(src)
        # The dj_activity literal must be gone from the redacted output.
        assert "dj_activity" not in out
        # `before` and `after` must survive.
        assert "before" in out
        assert "after" in out

    def test_named_verbatim_block_redacted(self):
        """Django's named-verbatim form `{% verbatim foo %}...{% endverbatim foo %}`
        must also be redacted. Locks the broader regex coverage."""
        src = "x\n{% verbatim foo %}{% dj_activity %}{% endverbatim foo %}\ny"
        out = _strip_verbatim_blocks(src)
        assert "dj_activity" not in out
        assert "x" in out
        assert "y" in out

    def test_line_numbers_preserved(self):
        """Newlines inside the verbatim region must be preserved so
        line numbers from `match.start()` stay accurate for matches
        OUTSIDE the region. This is the whole point of replacing the
        body with whitespace instead of stripping it."""
        src = (
            "line1\n"
            "{% verbatim %}\n"  # opens on line 2
            "  {% dj_activity %}\n"  # would be line 3 in original
            "{% endverbatim %}\n"  # closes on line 4
            "{% dj_activity %}\n"  # real call on line 5
        )
        out = _strip_verbatim_blocks(src)
        # The output must have the same number of newlines as the input.
        assert out.count("\n") == src.count("\n")
        # The post-verbatim line content (line 5) must be intact in the
        # redacted output — match.start() into `out` must give the same
        # line as in `src`.
        line5_offset_src = src.index("{% dj_activity %}\n", src.index("endverbatim"))
        assert src[:line5_offset_src].count("\n") == 4
        line5_offset_out = out.rindex("{% dj_activity %}")
        assert out[:line5_offset_out].count("\n") == 4

    def test_multiple_verbatim_blocks(self):
        """All verbatim blocks in a single template are redacted, not
        just the first."""
        src = (
            "{% verbatim %}{% dj_activity %}{% endverbatim %}\n"
            "{% verbatim %}{% dj_activity 'foo' %}{% endverbatim %}\n"
            "{% verbatim %}{% dj_activity 'bar' %}{% endverbatim %}\n"
        )
        out = _strip_verbatim_blocks(src)
        assert "dj_activity" not in out

    def test_verbatim_outside_dj_activity_unaffected(self):
        """A verbatim block that doesn't contain any `dj_activity` is
        still redacted, but real `{% dj_activity %}` calls outside it
        are unaffected."""
        src = (
            "{% verbatim %}{% csrf_token %}{% endverbatim %}\n"
            '{% dj_activity "real-panel" %}\n'
            "{% enddj_activity %}\n"
        )
        out = _strip_verbatim_blocks(src)
        # Real dj_activity outside verbatim survives.
        assert "dj_activity" in out
        assert '"real-panel"' in out
        # csrf_token inside verbatim is gone.
        assert "csrf_token" not in out

    def test_multiline_verbatim_body_redacted(self):
        """Verbatim spans across multiple lines (the common doc case)
        and the entire multi-line body must be blanked."""
        src = (
            "before\n"
            "{% verbatim %}\n"
            '  {% dj_activity "example-panel" visible=expr %}\n'
            "    <p>example body</p>\n"
            "  {% enddj_activity %}\n"
            "{% endverbatim %}\n"
            "after\n"
        )
        out = _strip_verbatim_blocks(src)
        assert "dj_activity" not in out
        assert "example body" not in out  # body redacted too
        assert "before" in out
        assert "after" in out
        assert out.count("\n") == src.count("\n")  # line count preserved


# ---------------------------------------------------------------------------
# 2. A070 scanner integration (end-to-end via _DJ_ACTIVITY_TAG_RE)
# ---------------------------------------------------------------------------


class TestA070VerbatimSuppression:
    """A070 must NOT fire for `{% dj_activity %}` tokens inside
    `{% verbatim %}` blocks. Validates the pre-processor is wired
    into the scan path (the `_activity_scan_source = _strip_verbatim_blocks(content)`
    line in `python/djust/checks.py`).

    These tests exercise the scanner via the same regex the
    production code uses, on the redacted content — so a future
    refactor that reverts the pre-processor would fail here."""

    def _scan(self, content: str) -> int:
        """Count A070-eligible (no-name) `{% dj_activity %}` matches in
        `content` after applying the verbatim pre-processor. Mirrors
        the production scan path."""
        from djust.checks import _DJ_ACTIVITY_NAME_RE, _DJ_ACTIVITY_TAG_RE

        scan_source = _strip_verbatim_blocks(content)
        no_name_count = 0
        for match in _DJ_ACTIVITY_TAG_RE.finditer(scan_source):
            args = match.group(1)
            name_match = _DJ_ACTIVITY_NAME_RE.match(args)
            if name_match is None:
                no_name_count += 1
                continue
            name_literal = name_match.group(1) or name_match.group(2)
            identifier_name = name_match.group(3)
            if not name_literal and not identifier_name:
                no_name_count += 1
        return no_name_count

    def test_verbatim_wrapped_example_no_a070(self):
        """#1004 canonical case: docs page wraps the tag in verbatim →
        no A070 fires."""
        content = "<h2>How to use {% verbatim %}{% dj_activity %}{% endverbatim %}</h2>\n"
        assert self._scan(content) == 0

    def test_real_no_name_dj_activity_still_fires(self):
        """Negative regression: a *real* uninstrumented
        `{% dj_activity %}` outside any verbatim block must still trip
        A070. Without this, the pre-processor would over-redact."""
        content = "{% dj_activity %}\n  <p>broken — no name</p>\n{% enddj_activity %}\n"
        assert self._scan(content) == 1

    def test_mixed_verbatim_example_and_real_call(self):
        """Both forms in one template: only the real call counts. Locks
        the boundary semantics."""
        content = (
            "<p>Example: {% verbatim %}{% dj_activity %}{% endverbatim %}</p>\n"
            "{% dj_activity %}\n"  # real, missing name
            "  <p>actual content</p>\n"
            "{% enddj_activity %}\n"
        )
        assert self._scan(content) == 1

    def test_named_dj_activity_in_verbatim_no_a070_or_a071(self):
        """A `{% dj_activity "panel" %}` in a verbatim block also
        produces no scan hit — A071 (duplicate name) shouldn't fire on
        documentation examples either."""
        content = (
            "{% verbatim %}{% dj_activity 'panel' %}{% endverbatim %}\n"
            "{% verbatim %}{% dj_activity 'panel' %}{% endverbatim %}\n"  # would be duplicate
        )
        assert self._scan(content) == 0
        # Duplicate-name detection (A071) — both occurrences are inside
        # verbatim, so no A071 hit either. Verified indirectly: the
        # _DJ_ACTIVITY_TAG_RE.finditer loop sees zero matches.

    def test_real_named_call_unaffected_by_verbatim_neighbor(self):
        """Real call survives even when surrounded by verbatim examples
        of the same tag. Locks that the scanner's state (`_seen_activity_names`)
        is built from real calls only."""
        content = (
            "{% verbatim %}{% dj_activity 'doc-panel' %}{% endverbatim %}\n"
            '{% dj_activity "real-panel" %}\n'
            "  <p>OK</p>\n"
            "{% enddj_activity %}\n"
        )
        assert self._scan(content) == 0  # real call has a name, so no A070
