"""#3318 -- ``theme_nav_item`` marks the links the client keeps in sync.

``theme_nav_item`` computes ``active`` from ``request.path`` at render time. A nav
outside ``dj-root`` is never re-rendered by ``dj-navigate``, so ``components.js``
recomputes the active state on navigation for links stamped ``data-dj-nav``
(``tests/js/theming_nav_active_3318.test.js``).

The server rendering stays the source of truth for first paint and no-JS, so the
rule has to be the SAME on both sides. ``PARITY_CASES`` is mirrored in that JS
test: change one table and change the other.
"""

import re

from djust.tests.theming_component_test_base import ComponentTestCase

# (nav item url, request.path, location.pathname, active?). Django reports
# request.path percent-DECODED and the browser reports location.pathname
# percent-ENCODED, so the two differ for non-ASCII paths. A url matches its own
# path and what is under it on a SEGMENT boundary, with or without a trailing
# slash; "/" matches only itself.
PARITY_CASES = [
    ("/", "/", "/", True),
    ("/", "/inbox/", "/inbox/", False),
    ("/inbox/", "/inbox/", "/inbox/", True),
    ("/inbox/", "/inbox/archive/", "/inbox/archive/", True),
    ("/inbox/", "/other/", "/other/", False),
    ("/inbox/", "/", "/", False),
    ("/inbox/archive/", "/inbox/", "/inbox/", False),
    ("/docs", "/docs", "/docs", True),
    ("/docs", "/docs/", "/docs/", True),
    ("/docs", "/docs/intro/", "/docs/intro/", True),
    ("/docs", "/docs-old/", "/docs-old/", False),
    ("/docs/", "/docs-old/", "/docs-old/", False),
    ("/docs/", "/docs", "/docs", True),
    ("/doc", "/docs/", "/docs/", False),
    ("/caf%C3%A9/", "/caf\u00e9/", "/caf%C3%A9/", True),
    ("/caf\u00e9/", "/caf\u00e9/", "/caf%C3%A9/", True),
    ("/caf%C3%A9/", "/caf\u00e9/menu/", "/caf%C3%A9/menu/", True),
    ("/caf%C3%A9/", "/cafe/", "/cafe/", False),
]

_MARKER = re.compile(r'data-dj-nav="([^"]*)"')


class TestNavItemMarker(ComponentTestCase):
    def test_auto_detected_link_carries_the_marker_with_its_url(self):
        html = self.render_component("nav_item", label="Inbox", url="/inbox/", request_path="/")
        assert _MARKER.search(html).group(1) == "/inbox/"

    def test_marker_is_stamped_whether_or_not_the_link_is_active_now(self):
        for path in ("/inbox/", "/elsewhere/"):
            html = self.render_component(
                "nav_item", label="Inbox", url="/inbox/", request_path=path
            )
            assert 'data-dj-nav="/inbox/"' in html

    def test_marker_is_stamped_without_a_request(self):
        html = self.render_component("nav_item", label="Inbox", url="/inbox/")
        assert 'data-dj-nav="/inbox/"' in html

    def test_explicit_active_is_the_apps_call_and_carries_no_marker(self):
        for active in (True, False):
            html = self.render_component("nav_item", label="Inbox", url="/inbox/", active=active)
            assert "data-dj-nav" not in html

    def test_urls_that_are_not_plain_paths_carry_no_marker(self):
        for url in ("https://example.com/x/", "//example.com/x/", "/x/?page=2", "/x/#top", "#top"):
            html = self.render_component("nav_item", label="X", url=url, request_path="/")
            assert "data-dj-nav" not in html, url

    def test_marker_url_is_attribute_escaped(self):
        html = self.render_component("nav_item", label="X", url='/a"b/', request_path="/")
        assert 'data-dj-nav="/a&quot;b/"' in html

    def test_server_rendering_follows_the_parity_table(self):
        for url, path, _location, expected in PARITY_CASES:
            html = self.render_component("nav_item", label="X", url=url, request_path=path)
            assert ("active" in self._classes(html)) is expected, (url, path)
            assert ('aria-current="page"' in html) is expected, (url, path)

    def test_no_url_does_not_raise_and_is_never_active(self):
        for kwargs in ({}, {"request_path": "/"}):
            html = self.render_component("nav_item", label="X", url=None, **kwargs)
            assert "data-dj-nav" not in html
            assert 'aria-current="page"' not in html

    def test_empty_url_is_not_active_on_every_path(self):
        html = self.render_component("nav_item", label="X", url="", request_path="/anything/")
        assert 'aria-current="page"' not in html

    @staticmethod
    def _classes(html):
        return re.search(r'class="([^"]*)"', html).group(1).split()
