"""The guide states the limits of the ``dj-update`` scan; the module must agree (#3647)."""

import pathlib

GUIDE = pathlib.Path(__file__).resolve().parents[3] / "docs/website/guides/BEST_PRACTICES.md"


class TestTheDocumentedNumbers:
    def test_the_guide_states_them(self):
        guide = " ".join(GUIDE.read_text().split())
        for phrase in (
            "more than 8 levels deep",
            "more than 64 included files",
            "more than 1,000,000 characters",
            "more than 1,000 elements",
            "more than 2 seconds",
            "more than 64 `{% with %}` aliases",
        ):
            assert phrase in guide, phrase

    def test_the_guide_names_what_the_scan_follows(self):
        guide = " ".join(GUIDE.read_text().split())
        for phrase in (
            "{% with items=rows %}",
            'context["feed"] = self.rows',
            "A nested `dj-update` container's body belongs to that container",
            "An `{% include %}` in a container is read too",
        ):
            assert phrase in guide, phrase
