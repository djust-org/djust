"""Tests for pre-minified client.js distribution (v0.6.0 P1).

Verifies that the script-injection in ``_inject_client_script``:
- Uses ``client.min.js`` in production (``DEBUG=False``).
- Uses ``client.js`` in development (``DEBUG=True``) for debuggability.
- Honors an explicit ``DJUST_CLIENT_JS_MINIFIED`` override in settings.
- Ships the ``.min.js``, ``.gz``, and (when brotli available) ``.br``
  sibling files in the static tree after a build.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
from django.test import override_settings

from djust.mixins.post_processing import PostProcessingMixin


# ---------------------------------------------------------------------------
# Static-file presence (contract of `make build-js`)
# ---------------------------------------------------------------------------


STATIC_DIR = Path(__file__).resolve().parents[2] / "python" / "djust" / "static" / "djust"


def test_minified_client_js_exists_after_build():
    """``make build-js`` emits ``client.min.js`` alongside the raw file."""
    raw = STATIC_DIR / "client.js"
    minified = STATIC_DIR / "client.min.js"
    assert raw.exists(), f"build artifact missing: {raw}"
    if not minified.exists():
        pytest.skip(
            "client.min.js missing — terser not installed in this environment; "
            "install via 'npm install' then re-run 'make build-js' to produce "
            "the minified sibling."
        )
    raw_size = raw.stat().st_size
    min_size = minified.stat().st_size
    assert min_size < raw_size, (
        f"client.min.js ({min_size} bytes) must be smaller than "
        f"client.js ({raw_size} bytes) — otherwise minification is a no-op"
    )
    # Sanity: expect at least 40% reduction from terser; real-world sees ~65%.
    assert min_size < raw_size * 0.6, (
        f"client.min.js ({min_size}) is only {min_size / raw_size:.0%} the size "
        f"of client.js ({raw_size}); terser may have failed silently"
    )


def test_gzip_sibling_exists_after_build():
    """``make build-js`` emits a ``.gz`` pre-compressed sibling for whitenoise.

    The ``.gz``/``.br``/``.map`` siblings are gitignored, build-time-only
    artifacts (#2054) — not committed, because their bytes depend on the
    local machine's gzip/brotli library version even when the source
    ``client.min.js`` is byte-identical. A bare checkout (or a CI job that
    doesn't run ``scripts/build-client.sh``) legitimately won't have them on
    disk, so this test skips rather than fails in that case; it still
    validates the compression ratio whenever the artifact IS present (e.g.
    after a local ``make build-js``).
    """
    gz = STATIC_DIR / "client.min.js.gz"
    if not (STATIC_DIR / "client.min.js").exists():
        pytest.skip("client.min.js missing — skipping gzip sibling check")
    if not gz.exists():
        pytest.skip(
            f"{gz} missing — gitignored build-time artifact (#2054); "
            "run 'make build-js' to generate it locally"
        )
    minified_size = (STATIC_DIR / "client.min.js").stat().st_size
    gz_size = gz.stat().st_size
    # Gzip of already-minified JS should be ~30-40% of the minified size.
    assert gz_size < minified_size * 0.5, (
        f"client.min.js.gz ({gz_size}) should be < 50% of client.min.js "
        f"({minified_size}); gzip may have failed"
    )


# ---------------------------------------------------------------------------
# Script-selection logic in _inject_client_script
# ---------------------------------------------------------------------------


class _FakeView(PostProcessingMixin):
    """Minimal mixin host — only the bits _inject_client_script uses."""

    def get_debug_info(self) -> dict:
        return {}


def _extract_client_src(html_with_injected_script: str) -> str:
    """Pull the src attribute of the first djust client <script> tag."""
    import re

    m = re.search(
        r'<script src="([^"]*djust/client[^"]*)"',
        html_with_injected_script,
    )
    if not m:
        raise AssertionError(
            f"no djust client <script> tag found in injected HTML: {html_with_injected_script!r}"
        )
    return m.group(1)


@override_settings(DEBUG=False)
def test_production_uses_minified_client_js():
    """With DEBUG=False, the injected script tag points at client.min.js."""
    view = _FakeView()
    injected = view._inject_client_script("<html><body></body></html>")
    src = _extract_client_src(injected)
    assert src.endswith("client.min.js"), f"expected client.min.js in production; got {src!r}"


@override_settings(DEBUG=True)
def test_debug_uses_unminified_client_js():
    """With DEBUG=True, the injected script tag points at the readable client.js."""
    view = _FakeView()
    injected = view._inject_client_script("<html><body></body></html>")
    src = _extract_client_src(injected)
    assert src.endswith("client.js") and not src.endswith(".min.js"), (
        f"expected client.js in debug; got {src!r}"
    )


@override_settings(DEBUG=True, DJUST_CLIENT_JS_MINIFIED=True)
def test_explicit_override_forces_minified_in_debug():
    """DJUST_CLIENT_JS_MINIFIED=True takes precedence over DEBUG heuristic."""
    view = _FakeView()
    injected = view._inject_client_script("<html><body></body></html>")
    src = _extract_client_src(injected)
    assert src.endswith("client.min.js"), f"override should force minified; got {src!r}"


@override_settings(DEBUG=False, DJUST_CLIENT_JS_MINIFIED=False)
def test_explicit_override_forces_raw_in_production():
    """DJUST_CLIENT_JS_MINIFIED=False forces the raw file even with DEBUG=False."""
    view = _FakeView()
    injected = view._inject_client_script("<html><body></body></html>")
    src = _extract_client_src(injected)
    assert src.endswith("client.js") and not src.endswith(".min.js"), (
        f"override should force raw; got {src!r}"
    )


# ---------------------------------------------------------------------------
# #2662 — a shipped .js file must never reference a file the package does not
# ship. `client.min.js` carried `//# sourceMappingURL=client.min.js.map` while
# the .map itself is gitignored (.gitignore: `*.min.js.map`) and so absent
# from the wheel. Django's `HashedFilesMixin` post-processes every *.js for
# `sourceMappingURL` and raises `MissingFileError` for an unresolvable target,
# so `collectstatic` under any ManifestStaticFilesStorage failed outright.
# ---------------------------------------------------------------------------

_SOURCEMAP_RE = re.compile(r"^\s*//[#@]\s*sourceMappingURL\s*=\s*(\S+)\s*$", re.MULTILINE)


def _is_shipped(path: Path) -> bool:
    """A static file is *shipped* when it exists AND is tracked by git.

    The wheel is built from the tracked tree; a locally-built artifact that
    `.gitignore` excludes (the .map siblings) is on disk for the developer
    but is not in the package a user installs.
    """
    if not path.exists():
        return False
    repo_root = STATIC_DIR.parents[3]
    if not (repo_root / ".git").exists():
        return True  # installed package: existence is the only signal
    try:
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", str(path)],
            cwd=repo_root,
            capture_output=True,
            check=False,
        )
    except OSError:
        return True
    return tracked.returncode == 0


@pytest.mark.parametrize(
    "js", sorted(STATIC_DIR.rglob("*.js")), ids=lambda p: str(p.relative_to(STATIC_DIR))
)
def test_shipped_js_does_not_reference_an_unshipped_sourcemap(js: Path):
    """Every `sourceMappingURL` in the static tree must resolve to a SHIPPED
    file, or the comment must be absent (#2662)."""
    if "node_modules" in js.parts:
        pytest.skip("not a djust static asset")
    text = js.read_text(encoding="utf-8", errors="replace")
    for m in _SOURCEMAP_RE.finditer(text):
        target = js.parent / m.group(1)
        assert _is_shipped(target), (
            f"{js.relative_to(STATIC_DIR)} references sourcemap {m.group(1)!r} "
            f"which is not shipped (missing or gitignored) — collectstatic under "
            f"ManifestStaticFilesStorage raises MissingFileError (#2662). Either "
            f"ship the map or build without the sourceMappingURL comment."
        )
