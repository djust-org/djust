"""
Regression tests for #890 — djust.A010 / A011 recognize proxy-trusted deployments.

When a deployer explicitly sets BOTH ``SECURE_PROXY_SSL_HEADER`` AND
``DJUST_TRUSTED_PROXIES`` (new djust setting, non-empty list/tuple), they're
asserting that a trusted L7 load balancer (AWS ALB, Cloudflare, Fly.io, etc.)
terminates requests for them. In that topology ``ALLOWED_HOSTS=['*']`` is the
only practical path (task private IPs rotate on every redeploy / autoscale).

These tests make sure:

* Existing behavior is preserved — A010 / A011 fire without the escape hatch.
* The escape hatch needs BOTH settings — partial opt-in still fires.
* The escape hatch suppresses both A010 and A011.
"""

from django.test import override_settings

from djust.checks import check_configuration


def _ids(errors):
    return {getattr(e, "id", "") for e in errors}


class TestA010ProxyTrustedEscapeHatch:
    """Verify the DJUST_TRUSTED_PROXIES + SECURE_PROXY_SSL_HEADER escape hatch."""

    @override_settings(DEBUG=False, ALLOWED_HOSTS=["*"])
    def test_a010_fires_without_trusted_proxies(self):
        """Baseline: A010 still fires when neither escape-hatch setting is set."""
        errors = check_configuration(None)
        assert "djust.A010" in _ids(errors), (
            "A010 must keep firing when ALLOWED_HOSTS=['*'] and the deployer "
            "has NOT opted into proxy-trusted mode."
        )

    @override_settings(
        DEBUG=False,
        ALLOWED_HOSTS=["*"],
        SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
        DJUST_TRUSTED_PROXIES=["aws-alb"],
    )
    def test_a010_suppressed_when_trusted_proxies_set(self):
        """A010 is suppressed when both escape-hatch settings are set."""
        errors = check_configuration(None)
        ids = _ids(errors)
        assert "djust.A010" not in ids, (
            "A010 must NOT fire when SECURE_PROXY_SSL_HEADER + "
            "DJUST_TRUSTED_PROXIES are both set — the deployer has explicitly "
            "opted into proxy-trusted mode."
        )

    @override_settings(
        DEBUG=False,
        ALLOWED_HOSTS=["*"],
        SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
        # DJUST_TRUSTED_PROXIES intentionally unset
    )
    def test_a010_fires_when_only_proxy_ssl_header_set(self):
        """Partial opt-in (only SECURE_PROXY_SSL_HEADER) must still fire."""
        errors = check_configuration(None)
        assert "djust.A010" in _ids(errors), (
            "A010 must still fire when only SECURE_PROXY_SSL_HEADER is set — "
            "both settings are required to opt into proxy-trusted mode."
        )

    @override_settings(
        DEBUG=False,
        ALLOWED_HOSTS=["*"],
        DJUST_TRUSTED_PROXIES=["aws-alb"],
        # SECURE_PROXY_SSL_HEADER intentionally unset
    )
    def test_a010_fires_when_only_trusted_proxies_set(self):
        """Partial opt-in (only DJUST_TRUSTED_PROXIES) must still fire."""
        errors = check_configuration(None)
        assert "djust.A010" in _ids(errors), (
            "A010 must still fire when only DJUST_TRUSTED_PROXIES is set — "
            "both settings are required to opt into proxy-trusted mode."
        )

    @override_settings(
        DEBUG=False,
        ALLOWED_HOSTS=["*"],
        SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
        DJUST_TRUSTED_PROXIES=[],  # empty — shouldn't count as opt-in
    )
    def test_a010_fires_when_trusted_proxies_is_empty(self):
        """Empty DJUST_TRUSTED_PROXIES list does NOT count as opting in."""
        errors = check_configuration(None)
        assert "djust.A010" in _ids(errors), "Empty DJUST_TRUSTED_PROXIES must not suppress A010."


class TestA011ProxyTrustedEscapeHatch:
    """A011 (wildcard mixed with explicit hosts) gets the same escape hatch."""

    @override_settings(DEBUG=False, ALLOWED_HOSTS=["myapp.example.com", "*"])
    def test_a011_fires_without_trusted_proxies(self):
        errors = check_configuration(None)
        assert "djust.A011" in _ids(errors)

    @override_settings(
        DEBUG=False,
        ALLOWED_HOSTS=["myapp.example.com", "*"],
        SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
        DJUST_TRUSTED_PROXIES=["aws-alb"],
    )
    def test_a011_suppressed_when_trusted_proxies_set(self):
        errors = check_configuration(None)
        assert "djust.A011" not in _ids(errors), (
            "A011 must NOT fire under proxy-trusted mode — same reasoning as "
            "A010: the deployer explicitly asserts a trusted proxy terminates "
            "requests."
        )
