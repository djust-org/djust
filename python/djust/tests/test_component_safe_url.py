"""Regression tests for javascript:-URL XSS in built-in component tags (#2, CWE-79).

Component tags rendered a developer/user-supplied URL into an href/action
attribute with `conditional_escape` (HTML-entity escaping) but NO scheme
validation — so `javascript:alert(1)` landed verbatim and executed on click.
The fix routes every navigation-context URL sink through `safe_url`, which
neutralizes dangerous schemes to `#` while preserving legitimate URLs.
"""

import pytest

from djust.components.templatetags._registry import safe_url


# --- safe_url helper ---


@pytest.mark.parametrize(
    "value",
    [
        "javascript:alert(1)",
        "JaVaScRiPt:alert(1)",
        "  javascript:alert(1)",
        "java\tscript:alert(1)",
        "java\nscript:alert(1)",
        "java\x00script:alert(1)",
        "vbscript:msgbox(1)",
        "data:text/html,<script>alert(1)</script>",
    ],
)
def test_safe_url_neutralizes_dangerous_schemes(value):
    assert safe_url(value) == "#", f"dangerous scheme not neutralized: {value!r}"


@pytest.mark.parametrize(
    "value",
    [
        "https://example.com/path?q=1",
        "http://example.com",
        "/relative/path",
        "#fragment",
        "?query=1",
        "mailto:user@example.com",
        "tel:+15551234",
    ],
)
def test_safe_url_preserves_legitimate_urls(value):
    out = str(safe_url(value))
    assert out != "#", f"legitimate URL wrongly blocked: {value!r}"
    # the meaningful part survives (HTML-escaping may encode & etc.)
    assert value.split(":")[0].split("?")[0].strip("/#") in out or out.startswith(
        ("/", "#", "?")
    ), out


def test_safe_url_empty_is_empty():
    assert safe_url("") == ""


# --- the component sinks (end-to-end) ---


def test_breadcrumb_blocks_javascript_url():
    import djust.components.templatetags.djust_components as dc

    html = str(dc.breadcrumb(items=[{"label": "Home", "url": "javascript:alert(document.cookie)"}]))
    assert "javascript:alert" not in html, "breadcrumb emitted a javascript: href"
    assert 'href="#"' in html


def test_breadcrumb_preserves_real_url():
    import djust.components.templatetags.djust_components as dc

    html = str(dc.breadcrumb(items=[{"label": "Home", "url": "/dashboard/"}]))
    assert "/dashboard/" in html


# --- structural pin: NO href/action navigation sink may use bare
#     conditional_escape (covers all 11 sinks + prevents regression on new ones)
#     — the #1646 parallel-path-drift guard for the whole class.


def test_no_inline_navigation_sink_uses_bare_conditional_escape():
    import re
    from pathlib import Path

    import djust.components.templatetags.djust_components as dc

    base = Path(dc.__file__).parent
    files = ["djust_components.py", "_advanced.py", "_forms.py"]
    # An inline f-string href=/action=/formaction= whose value is
    # conditional_escape(...) is exactly the vulnerable shape.
    bad = re.compile(r'(?:href|action|formaction)="\{conditional_escape\(')
    offenders = []
    for f in files:
        for i, line in enumerate((base / f).read_text().splitlines(), 1):
            if bad.search(line):
                offenders.append(f"{f}:{i}: {line.strip()}")
    assert not offenders, (
        "inline navigation sink(s) still use bare conditional_escape:\n" + "\n".join(offenders)
    )


def test_safe_url_sink_count_pinned():
    """All 11 navigation URL sinks route through safe_url. Pinning the count
    catches a revert of a *precomputed-var* sink (e_url = conditional_escape(...))
    that the inline-regex test above can't see (#1646 whole-class guard).

    NOTE: this pins the literal helper name ``safe_url(``; renaming the helper
    will (loudly) fail this assertion — update the pin if you rename it. The
    companion ``test_no_inline_navigation_sink_uses_bare_conditional_escape`` is
    the behavior-level guard.
    """
    from pathlib import Path

    import djust.components.templatetags.djust_components as dc

    base = Path(dc.__file__).parent
    counts = {
        f: (base / f).read_text().count("safe_url(")
        for f in ["djust_components.py", "_advanced.py", "_forms.py"]
    }
    total = sum(counts.values())
    # 7 (djust_components: breadcrumb, citation, cookie-consent, 4×dj_nav)
    # + 3 (_advanced: error-page, 2×breadcrumb) + 1 (_forms: form action) = 11
    assert total >= 11, f"safe_url sink count dropped to {total} (expected >= 11): {counts}"
