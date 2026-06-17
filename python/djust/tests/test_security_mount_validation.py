"""Security regression tests for #1819 — unvalidated mount/redirect URL.

``LiveViewConsumer.handle_mount`` (and the sticky-child ``live_redirect``
request rebuild) take the page URL straight from the attacker-controlled
WebSocket frame (``data.get("url", "/")``) and feed it into
``RequestFactory.get(...)``, ``resolve(...)``, query-string concatenation, and
log statements. Without validation a crafted ``url`` can be:

  * ``"../../admin/"`` — path traversal. Django prepends ``/`` but does NOT
    normalize ``..``: ``request.path`` becomes ``"/..../admin/"`` and
    ``request.path_info`` is the verbatim ``"../../admin/"``. A view that
    inspects ``request.path`` for an auth/routing decision sees the traversed
    path. (This is the load-bearing leak — see the EMPIRICAL FINDING test.)
  * ``"/page\\r\\nX-Injected: header"`` — CRLF / header / log injection.
  * ``"https://evil.com/page"`` / ``"//evil.com/page"`` — absolute /
    protocol-relative URL. Django silently drops the scheme+authority and
    treats it as a relative request; accepting that is surprising and is
    rejected explicitly.

The fix is a shared module-level helper ``_validate_mount_url`` applied at both
``RequestFactory`` sites in ``python/djust/websocket.py``. These tests pin both
the empirical Django behavior (so a future reader knows what Django already
sanitizes vs what leaks) and the helper's normalization.

Gate-off check (#1468): revert/disable the body of ``_validate_mount_url`` so
it returns its input unchanged and the malicious-URL tests in
``TestValidateMountUrlRejects`` + ``TestValidatedUrlIsSafeForRequestFactory``
fail — proving they exercise the change, not a tautology.
"""

from __future__ import annotations

import pytest
from django.test import RequestFactory

from djust.websocket import _validate_mount_url


# --------------------------------------------------------------------------- #
# EMPIRICAL FINDING — what Django's RequestFactory actually does with the      #
# malicious inputs (documents the BEHAVIOR DIFFERENCE the helper guards).      #
# --------------------------------------------------------------------------- #
class TestRequestFactoryEmpiricalBehavior:
    """Pin the raw (pre-fix) behavior so the threat model is verifiable.

    These tests assert on Django itself, not on djust code — they document
    *why* the validation is needed. They are intentionally independent of
    ``_validate_mount_url``.
    """

    def test_traversal_segments_survive_into_request_path(self):
        """``..`` is NOT normalized by RequestFactory — it leaks into path."""
        req = RequestFactory().get("../../admin/")
        # Django prepends "/" but leaves the traversal segments intact.
        assert req.path == "/..../admin/"
        # And path_info carries the verbatim traversal string.
        assert req.path_info == "../../admin/"
        # => a view inspecting request.path/path_info sees a traversed path.

    def test_crlf_is_stripped_by_django_path_parsing(self):
        """Django strips bare CR/LF from the path (defense-in-depth still warranted)."""
        req = RequestFactory().get("/page\r\nX-Injected: header")
        assert "\r" not in req.path and "\n" not in req.path
        assert "\r" not in req.get_full_path() and "\n" not in req.get_full_path()

    def test_absolute_url_authority_is_silently_dropped(self):
        """An absolute URL is accepted; only its path survives (host discarded)."""
        req = RequestFactory().get("https://evil.com/page")
        # scheme + host are silently dropped — Django treats it as "/page".
        assert req.path == "/page"

    def test_protocol_relative_authority_is_silently_dropped(self):
        """A protocol-relative URL likewise loses its authority."""
        req = RequestFactory().get("//evil.com/page")
        assert req.path == "/page"

    def test_legitimate_path_with_query_is_preserved(self):
        req = RequestFactory().get("/dashboard?q=1")
        assert req.path == "/dashboard"
        assert req.META.get("QUERY_STRING") == "q=1"


# --------------------------------------------------------------------------- #
# The helper rejects every malicious shape → "/"                               #
# --------------------------------------------------------------------------- #
class TestValidateMountUrlRejects:
    @pytest.mark.parametrize(
        "malicious",
        [
            "../../admin/",  # relative traversal
            "/../admin/",  # absolute-prefixed traversal
            "/foo/../../etc/passwd",  # interior traversal
            "/page\r\nX-Injected: header",  # CRLF injection
            "/page\nSet-Cookie: x=1",  # bare LF injection
            "https://evil.com/page",  # absolute URL
            "http://evil.com/page",  # absolute URL (http)
            "//evil.com/page",  # protocol-relative URL
            "javascript:alert(1)",  # scheme, does not start with "/"
            "relative/path",  # relative, no leading slash
            "",  # empty
            # Percent-encoded traversal (#1819 review): RequestFactory decodes
            # the path AFTER validation, so a raw-segment check missed these.
            "/%2e%2e/admin/",  # encoded ".." segment
            "/foo/%2e%2e/admin",  # encoded interior traversal
            "/foo%2f..%2fadmin",  # encoded separators around literal ".."
            "/..%2f..%2fadmin",  # encoded separators, leading ".."
            "/.%2e/admin",  # mixed literal-dot + encoded-dot ".."
            "/%2e./admin",  # mixed encoded-dot + literal-dot ".."
            "/..%5cadmin",  # encoded backslash separator
            "/foo/..%00/admin",  # encoded null byte
        ],
    )
    def test_malicious_url_normalized_to_root(self, malicious):
        assert _validate_mount_url(malicious) == "/"

    def test_none_normalized_to_root(self):
        assert _validate_mount_url(None) == "/"

    def test_non_string_normalized_to_root(self):
        # Defensive: a non-string from a malformed frame must not raise.
        assert _validate_mount_url(123) == "/"  # type: ignore[arg-type]
        assert _validate_mount_url(["/x"]) == "/"  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# The helper preserves legitimate site-relative URLs unchanged                 #
# --------------------------------------------------------------------------- #
class TestValidateMountUrlPreserves:
    @pytest.mark.parametrize(
        "legit",
        [
            "/",
            "/dashboard",
            "/dashboard?q=1",
            "/a/b/c?x=1&y=2",
            "/a/b/c?x=1#frag",
            "/items/42/edit",
        ],
    )
    def test_legitimate_url_preserved(self, legit):
        assert _validate_mount_url(legit) == legit


# --------------------------------------------------------------------------- #
# End-to-end: feeding the VALIDATED url to RequestFactory yields a safe path    #
# (this is the property the two call sites rely on).                           #
# --------------------------------------------------------------------------- #
class TestValidatedUrlIsSafeForRequestFactory:
    def test_traversal_url_after_validation_builds_root_request(self):
        """The exact site-1/site-2 flow: validate -> RequestFactory.get()."""
        safe = _validate_mount_url("../../admin/")
        req = RequestFactory().get(safe)
        assert req.path == "/"
        assert req.path_info == "/"

    def test_crlf_url_after_validation_builds_root_request(self):
        safe = _validate_mount_url("/page\r\nX-Injected: header")
        req = RequestFactory().get(safe)
        assert req.path == "/"
        assert "\r" not in req.get_full_path() and "\n" not in req.get_full_path()

    def test_absolute_url_after_validation_builds_root_request(self):
        safe = _validate_mount_url("https://evil.com/page")
        req = RequestFactory().get(safe)
        assert req.path == "/"

    def test_legitimate_url_after_validation_preserved(self):
        safe = _validate_mount_url("/dashboard?q=1")
        req = RequestFactory().get(safe)
        assert req.path == "/dashboard"
        assert req.META.get("QUERY_STRING") == "q=1"

    @pytest.mark.parametrize(
        "encoded_traversal",
        [
            "/%2e%2e/admin/",
            "/foo/%2e%2e/admin",
            "/foo%2f..%2fadmin",
            "/..%2f..%2fadmin",
            "/.%2e/admin",
            "/..%5cadmin",
        ],
    )
    def test_encoded_traversal_after_validation_builds_root_request(self, encoded_traversal):
        # The #1819 review bypass: RequestFactory percent-DECODES the path, so
        # an un-decoded ".." segment check let "/%2e%2e/admin/" through and it
        # landed in request.path as "/../admin/". After the fix, validation
        # decodes first → returns "/" → the built request can never carry a
        # traversed path.
        safe = _validate_mount_url(encoded_traversal)
        req = RequestFactory().get(safe)
        assert req.path == "/"
        assert ".." not in req.path
        assert ".." not in req.path_info


# --------------------------------------------------------------------------- #
# Both RequestFactory call sites call the helper (parallel-path pin, #1646)    #
# --------------------------------------------------------------------------- #
class TestBothCallSitesValidate:
    def test_both_request_factory_sites_use_validate_mount_url(self):
        """#1646: the fix is one shared helper applied at BOTH mount sites.

        Pins that no RequestFactory.get(data.get("url"...)) site reintroduces
        the raw-URL path. Counts the validated assignments in source.
        """
        import inspect

        import djust.websocket as ws_mod

        src = inspect.getsource(ws_mod)
        # Every `page_url = data.get("url"...)` assignment must go through the
        # validator. The raw form must not reappear.
        assert 'page_url = data.get("url"' not in src, (
            "A RequestFactory mount site still reads the raw client url without "
            "_validate_mount_url — parallel-path drift (#1819/#1646)."
        )
        # And the validated form is present at both sites.
        assert src.count('_validate_mount_url(data.get("url"') == 2, (
            "Expected exactly 2 validated mount-url sites (handle_mount + "
            "live_redirect request rebuild); found a different count."
        )
