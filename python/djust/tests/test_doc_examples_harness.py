"""The documentation-example harness itself (ADR-037 D2)."""

import importlib.util
import textwrap

import pytest

from djust.tests import _doc_examples as H


def _doc(tmp_path, body):
    path = tmp_path / "doc.md"
    path.write_text(textwrap.dedent(body).lstrip())
    return path


def test_blocks_reads_every_fence_with_its_comment_run(tmp_path):
    path = _doc(
        tmp_path,
        """
        # Title

        <!-- djust-example: first scenario=one -->
        ```python
        x = 1
        ```

        ```html
        <div dj-root></div>
        ```
        """,
    )
    found = H.blocks(path)
    assert [(b.line, b.language) for b in found] == [(4, "python"), (8, "html")]
    assert found[0].comments == ("<!-- djust-example: first scenario=one -->",)
    assert found[0].code == "x = 1"


def test_marker_stacks_above_the_doc_snippet_skip_line(tmp_path):
    path = _doc(
        tmp_path,
        """
        <!-- djust-example: skip -- settings fragment -->
        <!-- doc-snippet-check: skip -->
        ```python
        LIVEVIEW_CONFIG = {}
        ```
        """,
    )
    (block,) = H.blocks(path)
    assert H.marker(block) == (None, None, "settings fragment")
    # The doc-snippet checker still sees its own marker on the line above the fence.
    spec = importlib.util.spec_from_file_location(
        "_snippets", H.ROOT / "scripts/check-doc-snippets.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.extract_python_blocks(path) == []


@pytest.mark.parametrize(
    "comment",
    [
        "<!-- djust-example: skip -- -->",
        "<!-- djust-example: skip -->",
        "<!-- djust-example: Bad_Id scenario=x -->",
        "<!-- djust-example: ok -->",
    ],
)
def test_malformed_markers_are_errors(tmp_path, comment):
    path = _doc(tmp_path, comment + "\n```python\nx = 1\n```\n")
    (block,) = H.blocks(path)
    with pytest.raises(H.DocExampleError):
        H.marker(block)


def test_two_markers_on_one_block_are_an_error(tmp_path):
    path = _doc(
        tmp_path,
        "<!-- djust-example: a scenario=s -->\n"
        "<!-- djust-example: b scenario=s -->\n```python\nx = 1\n```\n",
    )
    (block,) = H.blocks(path)
    with pytest.raises(H.DocExampleError):
        H.marker(block)


def test_bounds_ignore_heading_lookalikes_inside_fences(tmp_path):
    path = _doc(
        tmp_path,
        """
        ## Start

        ```python
        # WRONG
        ## Stop
        ```

        ## Stop

        tail
        """,
    )
    assert H.bounds(H.Section(str(path), "## Start", "## Stop")) == (1, 7)


def test_bounds_report_a_missing_heading(tmp_path):
    path = _doc(tmp_path, "## Present\n")
    with pytest.raises(H.DocExampleError, match="Absent"):
        H.bounds(H.Section(str(path), "## Absent"))


def test_examples_pair_the_first_html_block_after_and_keep_the_section_code(tmp_path):
    path = _doc(
        tmp_path,
        """
        ## S

        <!-- djust-example: view scenario=s -->
        ```python
        class V: pass
        ```

        <!-- djust-example: skip -- route -->
        ```python
        path("x/", V)
        ```

        ```html
        <div dj-root>T</div>
        ```

        ## Next
        """,
    )
    view, route = H.examples(H.Section(str(path), "## S", "## Next"))
    assert (view.id, view.scenario, view.template) == ("view", "s", "<div dj-root>T</div>")
    assert route.skip_reason == "route" and route.id.endswith(":9")
    assert view.section_code == ("class V: pass", 'path("x/", V)')


def test_unmarked_blocks_are_returned_as_unmarked(tmp_path):
    path = _doc(tmp_path, "```python\nx = 1\n```\n")
    (example,) = H.examples(H.Section(str(path)))
    assert not example.marked and example.scenario is None


def test_marker_lines_find_markers_by_text(tmp_path):
    path = _doc(tmp_path, "<!-- djust-example: a scenario=s -->\n```python\nx = 1\n```\n")
    assert H.marker_lines(H.Section(str(path))) == [1]


def test_two_examples_with_one_class_name_load_as_distinct_modules():
    code = "from djust import LiveView\n\nclass ProjectView(LiveView):\n    pass\n"
    first = H.Example("a-view", "s", None, "x.md", 1, code, "<div dj-root></div>", (code,))
    second = H.Example("b-view", "s", None, "y.md", 1, code, "<div dj-root>2</div>", (code,))
    one, two = H.load(first), H.load(second)
    assert one is not two and one.__module__ != two.__module__
    assert (one.template, two.template) == ("<div dj-root></div>", "<div dj-root>2</div>")
