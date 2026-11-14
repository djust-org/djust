"""The last helper methods and the progress handler the safe-markup guard could not prove (#3845, #3847).

Each of these was safe and audited by hand, for a reason the guard cannot check:

* ``OrgChartNode._render_node`` and ``FeedbackWidgetNode._render_thumbs`` / ``_render_stars`` /
  ``_render_emoji`` wrote an ``e_event`` parameter the caller had escaped. A method can be called by
  a subclass with anything, so the guard judges a method's parameters raw (#3845). They now take the
  event as it is and escape it where it is written: safe for any caller, and the guard proves it.
* ``FileTreeNode._render_tree_node`` (and the ``FileTree`` component's ``_render_node``) wrote
  ``depth * 1.25`` of a parameter that is an int at every call site. ``int(depth)`` says so.
* ``ProgressHandler`` read ``["value"]`` of the Python tag's context dict, whose other keys are raw
  (#3847). ``int(...)`` of it says which part is clean.

The markup is unchanged for every value a caller could pass before: these tests pin that, and what
each method now does with a hostile value handed to it directly. That the guard proves the four
functions is pinned by the audit (``TestTheRealTree`` in ``test_safe_markup_guard_3820.py``: a site
with no audit entry fails it, and the four entries are deleted).

Refs #3845, #3847.
"""

from __future__ import annotations

import pytest
from django.template import Context, Template

from djust.components.components.file_tree import FileTree
from djust.components.rust_handlers import ProgressHandler
from djust.components.templatetags._advanced import OrgChartNode
from djust.components.templatetags._dev_tools import FileTreeNode
from djust.components.templatetags.djust_components import FeedbackWidgetNode

from scripts.lib import safe_markup_audit as audit

pytestmark = pytest.mark.components

HOSTILE = '"><x onmouseover=1>'
ESCAPED = "&quot;&gt;&lt;x onmouseover=1&gt;"
ORG = [{"id": "a", "name": "Ann"}, {"id": "b", "name": "Bo", "parent": "a"}]
TREE = [{"name": "src", "type": "folder", "children": [{"name": "a.py", "type": "file"}]}]


def tag(source: str, **context) -> str:
    return Template("{% load djust_components %}" + source).render(Context(context))


class TestOrgChart:
    def test_the_method_escapes_an_event_handed_to_it_raw(self):
        out = OrgChartNode({})._render_node("a", {"a": ORG[0], "b": ORG[1]}, {"a": ["b"]}, HOSTILE)
        assert "<x" not in out
        assert out.count(f'dj-click="{ESCAPED}"') == 2  # the root and its child

    def test_the_tag_writes_the_event_once_escaped(self):
        out = tag("{% org_chart nodes=nodes event=ev %}", nodes=ORG, ev="pick&go")
        assert out.count('dj-click="pick&amp;go"') == 2
        assert "&amp;amp;" not in out

    def test_a_hostile_event_through_the_tag(self):
        out = tag("{% org_chart nodes=nodes event=ev %}", nodes=ORG, ev=HOSTILE)
        assert "<x" not in out and out.count(f'dj-click="{ESCAPED}"') == 2

    def test_no_event_no_click_attribute(self):
        assert "dj-click" not in tag("{% org_chart nodes=nodes %}", nodes=ORG)
        assert "dj-click" not in OrgChartNode({})._render_node("a", {"a": ORG[0]}, {}, "")


class TestFeedbackWidget:
    @pytest.mark.parametrize("helper,clicks", [("thumbs", 2), ("stars", 5), ("emoji", 5)])
    def test_each_method_escapes_an_event_handed_to_it_raw(self, helper, clicks):
        out = getattr(FeedbackWidgetNode({}), f"_render_{helper}")(HOSTILE, None)
        assert "<x" not in out
        assert out.count(f'dj-click="{ESCAPED}"') == clicks

    @pytest.mark.parametrize("mode,clicks", [("thumbs", 2), ("stars", 5), ("emoji", 5)])
    def test_the_tag_writes_the_event_once_escaped(self, mode, clicks):
        out = tag("{% feedback event=ev mode=mode %}", ev="rate&go", mode=mode)
        assert out.count('dj-click="rate&amp;go"') == clicks
        assert "&amp;amp;" not in out

    @pytest.mark.parametrize("mode", ["thumbs", "stars", "emoji"])
    def test_a_hostile_event_through_the_tag(self, mode):
        out = tag("{% feedback event=ev mode=mode %}", ev=HOSTILE, mode=mode)
        assert "<x" not in out and f'dj-click="{ESCAPED}"' in out


class TestFileTreeDepth:
    """``depth`` is an int at every call site. A method handed text refused it before too (``str *
    float`` is a ``TypeError``); ``int(depth)`` says what it is, so the guard can read it, and the
    refusal is now the ``ValueError`` of a number that is not one."""

    def test_the_tag_indents_by_depth(self):
        out = tag("{% file_tree nodes=nodes %}", nodes=TREE)
        assert 'style="padding-left:0.0rem"' in out and 'style="padding-left:1.25rem"' in out

    def test_the_component_indents_by_depth(self):
        out = FileTree(nodes=TREE).render()
        assert 'style="padding-left:0.0rem"' in out and 'style="padding-left:1.25rem"' in out

    @pytest.mark.parametrize("hostile", ['1" onmouseover="1', "1rem;background:url(//e)", "x"])
    def test_the_tag_method_refuses_a_depth_that_is_not_a_number(self, hostile):
        with pytest.raises(ValueError):
            FileTreeNode({})._render_tree_node(TREE[0]["children"][0], hostile, "pick", True, "")

    @pytest.mark.parametrize("hostile", ['1" onmouseover="1', "1rem;background:url(//e)", "x"])
    def test_the_component_method_refuses_a_depth_that_is_not_a_number(self, hostile):
        with pytest.raises(ValueError):
            FileTree(nodes=TREE)._render_node(TREE[0]["children"][0], hostile)

    def test_a_numeric_depth_is_read_as_the_number(self):
        node = TREE[0]["children"][0]
        assert "padding-left:2.5rem" in FileTreeNode({})._render_tree_node(node, 2, "p", True, "")
        assert "padding-left:2.5rem" in FileTree(nodes=TREE)._render_node(node, 2)


class TestProgressHandler:
    @pytest.mark.parametrize(
        "given,drawn", [("40", "40"), ("250", "100"), ("-3", "0"), ("7.9", "7")]
    )
    def test_the_value_is_the_python_tags_clamp(self, given, drawn):
        out = ProgressHandler().render([f"value={given}"], {})
        assert f'aria-valuenow="{drawn}"' in out
        assert f"dj-progress-w-{drawn}" in out
        assert f">{drawn}%<" in out

    @pytest.mark.parametrize("refused", ["abc", HOSTILE])
    def test_a_value_that_is_not_a_number_is_refused(self, refused):
        with pytest.raises(ValueError):
            ProgressHandler().render(["value=v"], {"v": refused})


class TestTheAuditEntriesAreGone:
    def test_the_four_functions_have_no_audit_entry(self):
        gone = {
            (audit.ADV, "OrgChartNode.render"),
            (audit.DC, "FeedbackWidgetNode.render"),
            (audit.DT, "FileTreeNode.render"),
            (audit.RH, "ProgressHandler.render"),
        }
        assert not gone & set(audit.AUDITED)
        # what #3845 and #3847 leave audited, each for a reason its entry gives
        assert (audit.RH, "_root_layout_html") in audit.AUDITED
        assert (audit.RH, "DataTableHandler.render") in audit.AUDITED


class TestTheGateOffListApplies:
    def test_every_mutation_text_still_applies(self):
        import json

        spec = json.loads(
            (audit.ROOT / "scripts/safe-markup-batch-3845-3847-gateoff.json").read_text()
        )
        assert len(spec["mutations"]) == 7
        for mutation in spec["mutations"]:
            text = (audit.ROOT / mutation["file"]).read_text()
            assert text.count(mutation["old"]) == 1, mutation["label"]
