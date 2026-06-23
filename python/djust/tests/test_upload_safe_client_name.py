"""Regression tests for ``UploadEntry.safe_client_name`` (security finding #15).

``UploadEntry.client_name`` is the RAW, attacker-controlled original filename.
The framework's own usage docstring previously taught interpolating it directly
into a storage destination key:

    default_storage.save(f'avatars/{entry.client_name}', entry.file)

That is a path / object-key injection sink (CWE-22 path traversal /
CWE-73 external control of file name/path):

- on ``FileSystemStorage`` a value like ``../../../etc/x`` raises
  ``SuspiciousFileOperation`` (500 / DoS);
- on object stores (S3/GCS/Azure) ``../`` is a *valid* key, so the attacker
  controls the destination object key (overwrite / mis-place).

The fix ADDS a sanitised accessor ``safe_client_name`` (basename-only,
traversal-/control-byte-/dotfile-neutralised) and re-points the docstring at
it. ``client_name`` is intentionally left RAW for display use (auto-escaped).

These tests pin the sanitiser behaviour, the documented-path-use safety, and
the gate-off invariant (they would FAIL if ``safe_client_name`` returned
``client_name`` unchanged).
"""

from __future__ import annotations

import uuid

from djust.uploads import UploadEntry


def _entry(client_name: str) -> UploadEntry:
    return UploadEntry(
        ref=str(uuid.uuid4()),
        upload_name="avatar",
        client_name=client_name,
        client_type="image/png",
        client_size=100,
    )


class TestSafeClientNameStripsTraversal:
    """safe_client_name must collapse every path-traversal shape to a basename."""

    def test_posix_relative_traversal(self):
        assert _entry("../../../etc/x").safe_client_name == "x"

    def test_posix_absolute_path(self):
        assert _entry("/etc/passwd").safe_client_name == "passwd"

    def test_backslash_traversal_has_no_separators(self):
        # PurePosixPath/Path(...).name does NOT split on "\\"; the sanitiser
        # must map backslashes to "/" first so Windows-style traversal collapses.
        result = _entry("..\\..\\win").safe_client_name
        assert "\\" not in result
        assert "/" not in result
        assert not result.startswith(".")
        assert result == "win"

    def test_mixed_dotslash_does_not_become_dotdot(self):
        result = _entry("....//x").safe_client_name
        assert result not in ("..", ".")
        assert result == "x"

    def test_leading_dot_name_not_dotfile(self):
        # A leading-dot name must not survive as a dotfile / ".." / ".".
        assert _entry(".bashrc").safe_client_name == "bashrc"
        assert _entry("..").safe_client_name == "upload"
        assert _entry(".").safe_client_name == "upload"
        assert _entry("...").safe_client_name == "upload"

    def test_embedded_null_and_control_bytes_removed(self):
        result = _entry("inno\x00cent\x01.jpg").safe_client_name
        assert "\x00" not in result
        assert "\x01" not in result
        assert result == "innocent.jpg"

    def test_null_byte_truncation_classic(self):
        # "innocent.jpg\x00.exe" — NUL stripped, basename preserved, no path.
        result = _entry("innocent.jpg\x00.exe").safe_client_name
        assert "\x00" not in result
        assert "/" not in result and "\\" not in result


class TestSafeClientNameFallback:
    """Empty / None / all-dots inputs fall back to a safe default."""

    def test_empty_string(self):
        assert _entry("").safe_client_name == "upload"

    def test_none_value(self):
        # client_name is typed str, but be defensive: None must not crash.
        e = _entry("x")
        e.client_name = None  # type: ignore[assignment]
        assert e.safe_client_name == "upload"

    def test_whitespace_only(self):
        assert _entry("   ").safe_client_name == "upload"

    def test_all_dots(self):
        assert _entry("....").safe_client_name == "upload"


class TestSafeClientNamePreservesOrdinaryNames:
    """The sanitiser must not over-mangle legitimate filenames."""

    def test_ordinary_name_with_spaces_and_parens(self):
        assert _entry("my report (1).png").safe_client_name == "my report (1).png"

    def test_simple_name_unchanged(self):
        assert _entry("photo.jpg").safe_client_name == "photo.jpg"

    def test_unicode_name_preserved(self):
        assert _entry("résumé.pdf").safe_client_name == "résumé.pdf"


class TestClientNameStaysRaw:
    """The fix only ADDS safe_client_name; client_name itself is unchanged."""

    def test_client_name_remains_raw_traversal(self):
        e = _entry("../../../etc/x")
        # Display/Content-Disposition use keeps the original (auto-escaped at render).
        assert e.client_name == "../../../etc/x"
        assert e.safe_client_name == "x"

    def test_client_name_remains_raw_xss_payload(self):
        e = _entry("<script>alert(1)</script>.pdf")
        assert "<script>" in e.client_name


class TestDocumentedPathUseIsSafe:
    """Reproduce the finding: the documented save() pattern is safe with the accessor."""

    def test_safe_accessor_keeps_path_within_prefix(self):
        e = _entry("../../../etc/x")
        # The exact shape the (fixed) docstring teaches:
        safe_key = f"avatars/{e.safe_client_name}"
        assert safe_key == "avatars/x"
        assert ".." not in safe_key  # no traversal escape
        assert safe_key.startswith("avatars/")

    def test_raw_path_use_would_escape_prefix(self):
        # Contrast: the OLD documented pattern (raw client_name) escapes the prefix.
        e = _entry("../../../etc/x")
        unsafe_key = f"avatars/{e.client_name}"
        assert ".." in unsafe_key  # traversal present — this is the vuln

    def test_gate_off_traversal_is_actually_stripped(self):
        # GATE-OFF: this assertion FAILS if safe_client_name just returned
        # client_name unchanged (i.e. the sanitiser did nothing).
        e = _entry("../../../etc/x")
        assert e.safe_client_name != e.client_name
        assert ".." not in e.safe_client_name
        assert "/" not in e.safe_client_name


class TestS008CheckCanary:
    """Empirical canary for the S008 path-injection system check (#1459).

    A synthetic snippet using ``default_storage.save(f'x/{entry.client_name}')``
    must be flagged; one using ``safe_client_name`` must NOT be.
    """

    def _run_check_on(self, source: str):
        import ast

        from djust.checks.security import _scan_client_name_path_sink

        tree = ast.parse(source)
        return _scan_client_name_path_sink(tree, source.splitlines(), "synthetic.py")

    def test_flags_raw_client_name_in_storage_save(self):
        src = (
            "def handle(self):\n"
            "    for entry in self.consume_uploaded_entries('avatar'):\n"
            "        default_storage.save(f'avatars/{entry.client_name}', entry.file)\n"
        )
        findings = self._run_check_on(src)
        assert len(findings) == 1
        assert findings[0].id == "djust.S008"

    def test_does_not_flag_safe_client_name(self):
        src = (
            "def handle(self):\n"
            "    for entry in self.consume_uploaded_entries('avatar'):\n"
            "        default_storage.save(f'avatars/{entry.safe_client_name}', entry.file)\n"
        )
        findings = self._run_check_on(src)
        assert findings == []

    def test_flags_os_path_join_sink(self):
        src = (
            "import os\n"
            "def handle(self, entry):\n"
            "    path = os.path.join('avatars', entry.client_name)\n"
        )
        findings = self._run_check_on(src)
        assert len(findings) == 1
        assert findings[0].id == "djust.S008"

    def test_does_not_flag_display_use(self):
        # Rendering / non-path use of client_name must NOT be flagged.
        src = (
            "def handle(self, entry):\n"
            "    label = f'Uploaded: {entry.client_name}'\n"
            "    return label\n"
        )
        findings = self._run_check_on(src)
        assert findings == []


class TestSafeClientNameUnicodeLookalikes:
    """Hardening (review nit #1): Unicode compatibility lookalikes must not
    survive as latent traversal that a downstream NFKC pass re-expands.

    Fullwidth solidus (U+FF0F), division slash (U+2215), fullwidth full stop
    (U+FF0E) all NFKC-normalise to ASCII "/" / "." — the sanitiser normalises
    FIRST so the basename/dot logic then strips them.
    """

    def test_fullwidth_solidus_traversal_neutralised(self):
        # "／etc／passwd" with U+FF0F → NFKC "/etc/passwd" → basename "passwd".
        result = _entry("／etc／passwd").safe_client_name
        assert "/" not in result
        assert "／" not in result
        assert result == "passwd"

    def test_fullwidth_dot_and_solidus_traversal_neutralised(self):
        # "．．／．．／etc" all-fullwidth → NFKC "../../etc" → basename "etc"
        # (no separator, no "..", stays a safe basename within any prefix).
        result = _entry("．．／．．／etc").safe_client_name
        assert "/" not in result
        assert ".." not in result
        assert result == "etc"

    def test_fullwidth_backslash_neutralised(self):
        # U+FF3C fullwidth reverse solidus → NFKC "\\" → mapped to "/" → basename.
        result = _entry("..＼..＼win.exe").safe_client_name
        assert "\\" not in result
        assert "＼" not in result
        assert result == "win.exe"

    def test_all_fullwidth_dotdot_collapses_to_fallback(self):
        # "．．／．．" → NFKC "../.." → basename ".." → lstrip → "" → "upload".
        assert _entry("．．／．．").safe_client_name == "upload"

    def test_bidi_override_removed(self):
        # U+202E RIGHT-TO-LEFT OVERRIDE (Trojan-source filename spoofing) is a
        # format char (category Cf) and must be stripped.
        result = _entry("evil‮gpj.png").safe_client_name
        assert "‮" not in result

    def test_zero_width_space_removed(self):
        result = _entry("inv​isible.png").safe_client_name
        assert "​" not in result
        assert result == "invisible.png"


class TestSafeClientNameTrailingDotsAndSpaces:
    """Hardening (review nit #2): strip trailing dots/spaces (Windows strips
    these at the FS layer, so "evil.png." would collide with "evil.png")."""

    def test_trailing_dot_stripped(self):
        assert _entry("evil.png.").safe_client_name == "evil.png"

    def test_trailing_dots_and_spaces_stripped(self):
        assert _entry("foo. . .").safe_client_name == "foo"

    def test_trailing_spaces_stripped(self):
        assert _entry("report.pdf   ").safe_client_name == "report.pdf"

    def test_gate_off_unicode_lookalike_actually_neutralised(self):
        # GATE-OFF: fails if NFKC normalisation were removed (the raw fullwidth
        # solidus would survive and a downstream normaliser would re-expand it).
        e = _entry("／etc／passwd")
        assert e.safe_client_name != e.client_name
        assert "／" not in e.safe_client_name
        assert "/" not in e.safe_client_name
