"""Tests for djust system checks (djust/checks.py)."""

from djust.checks import _DOC_DJUST_EVENT_RE


class TestT004Regex:
    """T004 -- document.addEventListener for djust: events."""

    def test_matches_document_djust_push_event(self):
        content = """document.addEventListener('djust:push_event', (e) => {"""
        assert _DOC_DJUST_EVENT_RE.search(content) is not None

    def test_matches_double_quoted(self):
        content = """document.addEventListener("djust:push_event", (e) => {"""
        assert _DOC_DJUST_EVENT_RE.search(content) is not None

    def test_matches_djust_stream(self):
        content = """document.addEventListener('djust:stream', (e) => {"""
        assert _DOC_DJUST_EVENT_RE.search(content) is not None

    def test_matches_djust_connected(self):
        content = """document.addEventListener('djust:connected', () => {"""
        assert _DOC_DJUST_EVENT_RE.search(content) is not None

    def test_matches_with_space_after_dot(self):
        content = """document .addEventListener('djust:error', (e) => {"""
        assert _DOC_DJUST_EVENT_RE.search(content) is not None

    def test_no_match_window_listener(self):
        """window.addEventListener is correct -- should NOT match."""
        content = """window.addEventListener('djust:push_event', (e) => {"""
        assert _DOC_DJUST_EVENT_RE.search(content) is None

    def test_no_match_non_djust_event(self):
        """Non-djust events are fine on document."""
        content = """document.addEventListener('click', (e) => {"""
        assert _DOC_DJUST_EVENT_RE.search(content) is None

    def test_no_match_djust_without_colon(self):
        """'djust' without colon prefix is not a djust event."""
        content = """document.addEventListener('djust_init', (e) => {"""
        assert _DOC_DJUST_EVENT_RE.search(content) is None


class TestT004CheckIntegration:
    """Integration test for T004 using the actual check function."""

    def test_t004_detects_document_listener(self, tmp_path, settings):
        """T004 should flag document.addEventListener for djust: events."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "bad.html").write_text(
            "<script>document.addEventListener('djust:push_event', (e) => {});</script>"
        )
        settings.TEMPLATES = [
            {
                "DIRS": [str(tpl_dir)],
                "BACKEND": "django.template.backends.django.DjangoTemplateBackend",
            }
        ]

        from djust.checks import check_templates

        errors = check_templates(None)
        t004_errors = [e for e in errors if e.id == "djust.T004"]
        assert len(t004_errors) == 1
        assert "document.addEventListener" in t004_errors[0].msg

    def test_t004_passes_window_listener(self, tmp_path, settings):
        """T004 should NOT flag window.addEventListener for djust: events."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "good.html").write_text(
            "<script>window.addEventListener('djust:push_event', (e) => {});</script>"
        )
        settings.TEMPLATES = [
            {
                "DIRS": [str(tpl_dir)],
                "BACKEND": "django.template.backends.django.DjangoTemplateBackend",
            }
        ]

        from djust.checks import check_templates

        errors = check_templates(None)
        t004_errors = [e for e in errors if e.id == "djust.T004"]
        assert len(t004_errors) == 0
