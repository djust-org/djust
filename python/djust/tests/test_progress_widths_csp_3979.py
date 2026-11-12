"""Progress bars outside ``{% progress %}`` draw their width without an inline style (#3979).

A ``style-src`` without ``'unsafe-inline'`` refuses a ``style="width:N%"`` attribute,
so the bar showed empty under a strict CSP. ``{% theme_progress %}`` now uses the
width classes ``{% progress %}`` uses (#3969), and the admin's bulk-action
progress page a native ``<progress>`` element.
"""

import re
from pathlib import Path

import pytest

from djust.tests.theming_component_test_base import ComponentTestCase

THEMING_CSS = (
    Path(__file__).resolve().parents[1]
    / "theming"
    / "static"
    / "djust_theming"
    / "css"
    / "components.css"
)
BULK_TEMPLATE = (
    Path(__file__).resolve().parents[1]
    / "admin_ext"
    / "templates"
    / "djust_admin"
    / "bulk_progress.html"
)


class TestThemeProgressWidthClass(ComponentTestCase):
    def test_the_fill_width_is_a_class_not_a_style(self):
        html = self.render_component("progress", value=25, max=100)
        assert "style=" not in html
        assert "dj-progress-w-25" in html

    def test_the_width_rounds_to_a_whole_percent_and_caps_at_100(self):
        assert "dj-progress-w-33" in self.render_component("progress", value=1, max=3)
        assert "dj-progress-w-100" in self.render_component("progress", value=7, max=5)
        assert "dj-progress-w-0" in self.render_component("progress", value=0, max=5)

    def test_an_indeterminate_bar_has_no_fill(self):
        html = self.render_component("progress", label="Loading")
        assert "dj-progress-w-" not in html and "style=" not in html


@pytest.mark.parametrize("percent", [0, 1, 50, 99, 100])
def test_every_width_class_the_tag_emits_is_defined(percent):
    css = THEMING_CSS.read_text()
    assert re.search(r"\.dj-progress-w-%d \{ width: %d%%; \}" % (percent, percent), css)


def test_the_bulk_progress_page_has_no_inline_style():
    from django.template import engines

    source = BULK_TEMPLATE.read_text()
    assert "style=" not in source
    # The bar itself, rendered: a native <progress> carrying the value.
    bar = re.search(r"<progress.*?</progress>", source, re.S).group(0)
    html = engines["django"].from_string(bar).render({"percent": 40, "action_label": "Export"})
    assert 'value="40"' in html and 'max="100"' in html and 'aria-label="Export"' in html
