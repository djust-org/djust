"""#3289 — ``theme_progress`` must carry ``dj-upload-progress`` / ``dj-hook`` /
``data-*`` attributes so a themed bar can be wired to an upload slot.

Before, the tag read only ``class`` and ``id`` from its extra keyword
arguments, so ``{% theme_progress dj_upload_progress="avatar" %}`` rendered a
bar the client could never find.
"""

from djust.tests.theming_component_test_base import ComponentTestCase


class TestThemeProgressAttrPassthrough(ComponentTestCase):
    def test_upload_progress_attribute_reaches_the_wrapper(self):
        html = self.render_component("progress", value=0, dj_upload_progress="avatar")
        self.assert_contains(html, 'dj-upload-progress="avatar"')
        # On the outer wrapper, so the client sees the bar as a descendant.
        assert html.lstrip().index("dj-upload-progress") < html.index('role="progressbar"')

    def test_hook_and_data_attributes_pass_through(self):
        html = self.render_component(
            "progress", value=10, dj_hook="Meter", data_slot="docs", aria_busy=True
        )
        self.assert_contains(html, 'dj-hook="Meter"')
        self.assert_contains(html, 'data-slot="docs"')
        self.assert_contains(html, " aria-busy")

    def test_values_are_escaped(self):
        html = self.render_component(
            "progress", value=0, dj_upload_progress='a"><script>x</script>'
        )
        self.assert_not_contains(html, "<script>")

    def test_class_and_id_are_not_duplicated(self):
        html = self.render_component("progress", value=5, id="p1", **{"class": "wide"})
        assert html.count('id="p1"') == 1
        assert html.count("wide") == 1

    def test_plain_call_adds_no_attributes(self):
        html = self.render_component("progress", value=50)
        self.assert_not_contains(html, "dj-upload-progress")
        self.assert_contains(html, 'aria-valuenow="50"')
