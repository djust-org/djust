"""Tests for #1625 — code_block emits a MutationObserver bootstrap.

The per-instance inline `<script>` works on initial HTTP page load but
fails for WS-patch-inserted code blocks (browsers don't execute scripts
inserted via innerHTML). Fix: install a MutationObserver ONCE per page
that watches for new `<pre><code class="language-*">` elements and
calls `hljs.highlightElement` on them.

These are source-text gate tests that pin the implementation shape
(the canonical pattern used by other client-side fixes in this repo —
see `tests/js/mount-deferred-reinit.test.js` for the prior art).
"""

from djust.components.templatetags.djust_components import code_block


class TestCodeBlockObserverBootstrap1625:
    """#1625: code_block installs a MutationObserver for WS-patch-inserted code."""

    def test_output_contains_mutation_observer(self):
        """The emitted inline script must construct a MutationObserver."""
        html = code_block(code="print(1)", language="python")
        assert "MutationObserver" in html, (
            "Expected 'MutationObserver' in code_block output; got: %s" % html
        )

    def test_observer_install_is_idempotent(self):
        """Install gated by `__djcHljsObserverInstalled` so only one observer per page."""
        html = code_block(code="print(1)", language="python")
        assert "__djcHljsObserverInstalled" in html, (
            "Expected '__djcHljsObserverInstalled' flag in code_block output; got: %s" % html
        )

    def test_observer_uses_subtree_childlist(self):
        """The observer must watch childList + subtree to catch nested insertions
        (WS patches frequently insert grandchildren of dj-view containers)."""
        html = code_block(code="print(1)", language="python")
        # Both options must be requested for the observer to catch new code
        # elements anywhere in the DOM, not just direct children of body.
        assert "childList" in html
        assert "subtree" in html

    def test_observer_selector_matches_language_classes(self):
        """The observer's selector must match elements that already use the
        same `language-*` class the code_block template emits."""
        html = code_block(code="print(1)", language="python")
        # Either selector form is acceptable; the prefix gate must be present.
        assert "language-" in html and (
            "[class^=language-" in html or '[class^="language-"' in html
        ), "Expected language-prefix selector in observer; got: %s" % html

    def test_highlight_false_path_unchanged(self):
        """When highlight=False, no inline script (and therefore no observer) is emitted."""
        html = code_block(code="print(1)", language="python", highlight=False)
        assert "MutationObserver" not in html
        assert "__djcHljsObserverInstalled" not in html

    def test_existing_lazy_loader_path_preserved(self):
        """Regression backstop: __djcHljsLoading + the lazy CDN URL remain.

        The CDN URL assertion uses a path-specific substring rather than just
        the host name — CodeQL flags bare-host substring checks as the
        ``js/incomplete-url-substring-sanitization`` anti-pattern (false-positive
        for test assertions, but a real concern for runtime sanitization), so
        the longer slice keeps the assertion unambiguous AND silences the
        rule.
        """
        html = code_block(code="print(1)", language="python")
        assert "__djcHljsLoading" in html
        assert "highlightjs/cdn-release" in html
