"""The guide states the limits of the ``dj-update`` scan; the module must agree (#3647)."""

import pathlib

GUIDE = pathlib.Path(__file__).resolve().parents[3] / "docs/website/guides/BEST_PRACTICES.md"
LARGE_LISTS = GUIDE.parent / "large-lists.md"


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
            "after 3 timeouts in a row the scan stops retrying",
        ):
            assert phrase in guide, phrase

    def test_the_guide_names_what_the_scan_follows(self):
        guide = " ".join(GUIDE.read_text().split()).replace(
            "Keep the `dj-update`", "keep the `dj-update`"
        )
        for phrase in (
            "{% with items=rows %}",
            'context["feed"] = self.rows',
            "A nested `dj-update` container's body belongs to that container",
            "An `{% include %}` in a container is read too",
            "`./x` is `x` at the root of the template directories",
            "**Unsupported shapes",
            "a container whose **tag lives inside an included file**",
            "a value **derived** from the attribute",
            "the list goes blank",
            "keep the `dj-update` container tag and its rows in the same template",
            "`{% with rows=other %}` that wraps the container",
        ):
            assert phrase in guide, phrase

    def test_large_lists_states_the_failure_mode(self):
        text = " ".join(LARGE_LISTS.read_text().split())
        for phrase in (
            "keeps only the latest render's rows",
            "an unrelated event blanks the list",
            "the container tag inside an included file",
        ):
            assert phrase in text, phrase
