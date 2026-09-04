"""URL targets are decoded and requoted independently of displayed text (#2582)."""

import pytest
from django.template import Context, Engine
from django.utils.safestring import mark_safe

from djust import _rust


@pytest.mark.parametrize("filter_expr", ["urlize", "urlizetrunc:20"])
@pytest.mark.parametrize("autoescape", ["on", "off"])
@pytest.mark.parametrize("safe", [False, True])
@pytest.mark.parametrize(
    "url",
    [
        "http://example.com?x=&amp;y=&lt;2&gt;",
        "http://example.com?x=&amp;y=",
        "http://example.com?x=&amp;y=&lt;2&gt;;.",
        "http://example.com/a%20b?q=a%26b&x=one+two",
        "http://example.com/?x=%2526&blank=&bare#some%20fragment",
        "http://éxample.com/café?name=café",
        "http://example.com/?x=&#x22;onclick=alert(1)",
        "http://[::1]/path?x=%2f",
        "www.example.com/?one&two=2",
        "http://example.com/%zz?x=%ff",
        "http://example.com/?x=a&#x09;b&y=&#13;z",
        "http://[2001:db8::1]/?x=%2f",
        "http://[v1.example]/?x=%2f",
        "http://[invalid]/?x=%2f",
        "http://[fe80::1%eth0]/?x=%2f",
    ],
)
def test_urlize_matches_django(url, safe, autoescape, filter_expr):
    source = "{% autoescape " + autoescape + " %}{{ url|" + filter_expr + " }}{% endautoescape %}"
    context = {"url": mark_safe(url) if safe else url}
    expected = Engine().from_string(source).render(Context(context))
    assert _rust.render_template_with_dirs(source, context, [], ["url"] if safe else []) == expected
