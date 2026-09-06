"""#2689 — two ``{% include %}``s of one fragment must not render identical
``<!--dj-if id=...-->`` marker ids (link N+2 of #2530/#2686).

``if-<hash>-<N>`` takes ``<hash>`` from the template SOURCE and ``<N>`` from a
per-parse ordinal, so ANY mechanism that renders one parsed template more than
once into a single output buffer emits duplicate ids. Three instances of that
class are now known:

===========================================  ============================
axis                                         cure
===========================================  ============================
``{% for %}`` iterations                     ``Context::dj_if_loop_path`` (#1832)
two ``template_name`` component instances    ``Context::dj_if_id_namespace`` (#2686)
two ``{% include %}``s of one fragment       this issue
===========================================  ============================

The client resolves ``RemoveSubtree`` / ``InsertSubtree`` / ``MoveSubtree`` by
FIRST matching id (``static/djust/src/12-vdom-patch.js``), so a toggle inside
the second include lands on the first.

Every assertion here is POSITIONAL. Under the bug the two ids are EQUAL, and a
dict keyed on the id silently collapses them to one entry — the exact tautology
that stayed green under the live bug in #2686 (#2135, "green while destroying
what it guards").
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from django.test import override_settings

from djust import LiveView
from djust.decorators import event_handler
from djust.testing import LiveViewTestClient
from djust.utils import clear_template_dirs_cache

pytestmark = [pytest.mark.django_db]

MOD = __name__

_MARKER = re.compile(r'<!--dj-if id="(if-[0-9A-Za-z_-]+)"-->(.*?)<!--/dj-if-->', re.S)


class TwoIncludes2689(LiveView):
    template_name = "inc_2689/page.html"

    def mount(self, request: Any, **kwargs: Any) -> None:
        self.a = False
        self.b = False

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        return {"a": self.a, "b": self.b}

    @event_handler()
    def toggle_b(self, **kwargs: Any) -> None:
        self.b = not self.b


class NestedIncludes2689(LiveView):
    """An include whose own body includes the fragment twice, plus a sibling.

    Three renders of one parsed fragment, reached down two different include
    chains — so a per-site ordinal that resets inside a nested include would
    still collide.
    """

    template_name = "inc_2689/outer.html"

    def mount(self, request: Any, **kwargs: Any) -> None:
        self.a = False
        self.b = False
        self.c = False

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        return {"a": self.a, "b": self.b, "c": self.c}


class OnlyIncludes2689(LiveView):
    """``{% include … only %}`` builds a FRESH ``Context`` mid-render.

    That fresh context is the one place the include-site path can be dropped
    on the floor — it already had to be taught to carry the marker flag
    (#2519), the loop path (#1832) and the component namespace (#2686).
    """

    template_name = "inc_2689/only.html"

    def mount(self, request: Any, **kwargs: Any) -> None:
        self.a = False
        self.b = False

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        return {"a": self.a, "b": self.b}


class ExtendsIncludes2689(LiveView):
    """A base and its child each include the fragment.

    Page-level ``{% extends %}`` resolves through ONE parse of the merged
    source, so the two include sites share a counter and are already distinct
    without the source hash in ``site_id`` — gate-off proved that, contradicting
    the guess this test was first written on. The case that does need the hash
    is :class:`IncludedExtender2689`; this one pins that composing an
    ``{% extends %}`` page does not lose the suffix altogether.
    """

    template_name = "inc_2689/child.html"

    def mount(self, request: Any, **kwargs: Any) -> None:
        self.a = False
        self.b = False

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        return {"a": self.a, "b": self.b}


class IncludedExtender2689(LiveView):
    """``{% include %}`` of a template that itself ``{% extends %}``.

    The page-level ``{% extends %}`` resolves through one parse of the merged
    source, so its include ordinals share a counter. This path does NOT: the
    renderer's include arm calls ``build_inheritance_chain_from`` on nodes the
    loader parsed SEPARATELY, so the base's include #0 and the extender's
    include #0 both start at 0. Only the including template's source hash in
    ``site_id`` keeps them apart.
    """

    template_name = "inc_2689/host.html"

    def mount(self, request: Any, **kwargs: Any) -> None:
        self.a = False
        self.b = False

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        return {"a": self.a, "b": self.b}


class IncludeInLoop2689(LiveView):
    """``{% include %}`` inside ``{% for %}`` — the two suffix axes composed."""

    template_name = "inc_2689/loop.html"

    def mount(self, request: Any, **kwargs: Any) -> None:
        self.rows = [False, False, False]

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        return {"rows": self.rows}


@pytest.fixture
def templates(tmp_path: Any) -> Any:
    d = tmp_path / "templates" / "inc_2689"
    d.mkdir(parents=True)
    (d / "frag.html").write_text("<p>{% if flag %}<b>on</b>{% endif %}</p>")
    (d / "page.html").write_text(
        f'<div dj-root dj-view="{MOD}.TwoIncludes2689" dj-id="0">'
        '<div id="a">{% include "inc_2689/frag.html" with flag=a %}</div>'
        '<div id="b">{% include "inc_2689/frag.html" with flag=b %}</div>'
        "</div>"
    )
    (d / "pair.html").write_text(
        '<span>{% include "inc_2689/frag.html" with flag=a %}'
        '{% include "inc_2689/frag.html" with flag=b %}</span>'
    )
    (d / "outer.html").write_text(
        f'<div dj-root dj-view="{MOD}.NestedIncludes2689" dj-id="0">'
        '{% include "inc_2689/pair.html" %}'
        '<div id="c">{% include "inc_2689/frag.html" with flag=c %}</div>'
        "</div>"
    )
    (d / "only_pair.html").write_text(
        '<span>{% include "inc_2689/frag.html" with flag=a only %}'
        '{% include "inc_2689/frag.html" with flag=b only %}</span>'
    )
    (d / "only.html").write_text(
        f'<div dj-root dj-view="{MOD}.OnlyIncludes2689" dj-id="0">'
        '{% include "inc_2689/only_pair.html" with a=a b=b only %}'
        '<div id="c">{% include "inc_2689/frag.html" with flag=a only %}</div>'
        "</div>"
    )
    (d / "base.html").write_text(
        f'<div dj-root dj-view="{MOD}.ExtendsIncludes2689" dj-id="0">'
        '<div id="base">{% include "inc_2689/frag.html" with flag=a %}</div>'
        "{% block extra %}{% endblock %}"
        "</div>"
    )
    (d / "child.html").write_text(
        '{% extends "inc_2689/base.html" %}'
        '{% block extra %}<div id="child">{% include "inc_2689/frag.html" with flag=b %}</div>'
        "{% endblock %}"
    )
    (d / "inner_base.html").write_text(
        '<u>{% include "inc_2689/frag.html" with flag=a %}{% block slot %}{% endblock %}</u>'
    )
    (d / "inner_child.html").write_text(
        '{% extends "inc_2689/inner_base.html" %}'
        '{% block slot %}{% include "inc_2689/frag.html" with flag=b %}{% endblock %}'
    )
    (d / "host.html").write_text(
        f'<div dj-root dj-view="{MOD}.IncludedExtender2689" dj-id="0">'
        '{% include "inc_2689/inner_child.html" %}'
        "</div>"
    )
    (d / "loop.html").write_text(
        f'<div dj-root dj-view="{MOD}.IncludeInLoop2689" dj-id="0">'
        '{% for row in rows %}{% include "inc_2689/frag.html" with flag=row %}{% endfor %}'
        "</div>"
    )
    with override_settings(
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "DIRS": [str(tmp_path / "templates")],
                "APP_DIRS": False,
                "OPTIONS": {},
            }
        ],
        LIVEVIEW_ALLOWED_MODULES=[MOD],
    ):
        # `get_template_dirs()` is lru-cached (#1801).
        clear_template_dirs_cache()
        try:
            yield
        finally:
            clear_template_dirs_cache()


def _ids(html: str) -> list[str]:
    return [mid for mid, _ in _MARKER.findall(html)]


class TestTwoIncludesGetDistinctMarkerIds:
    def test_the_two_include_sites_do_not_share_a_marker_id(self, templates: Any) -> None:
        client = LiveViewTestClient(TwoIncludes2689)
        client.mount()
        html = client.render()
        ids = _ids(html)
        assert len(ids) == 2, f"expected one marker per include site, got {ids} in {html}"
        assert len(set(ids)) == 2, (
            f"both {{% include %}}s of one fragment rendered the SAME dj-if id {ids[0]!r} — "
            "the client resolves subtree patches by first match, so a toggle in the "
            "second include would land on the first (#2689)."
        )

    def test_the_toggled_body_belongs_to_the_second_include(self, templates: Any) -> None:
        """Positional, never keyed on the id — see the module docstring."""
        client = LiveViewTestClient(TwoIncludes2689)
        client.mount()
        before = _MARKER.findall(client.render())
        client.send_event("toggle_b")
        after = _MARKER.findall(client.render())

        assert len(before) == len(after) == 2, (before, after)
        assert [mid for mid, _ in before] == [mid for mid, _ in after], (before, after)
        assert before[0][1] == after[0][1] == "", (
            f"the FIRST include's subtree changed on a toggle of the SECOND: {before} -> {after}"
        )
        assert before[1][1] == "" and after[1][1] == "<b>on</b>", (before, after)
        assert after[1][0] != after[0][0], (
            "the two include sites still share a marker id, so the client would "
            f"resolve the second's patch onto the first (#2689): {after}"
        )

    def test_ids_are_stable_across_renders(self, templates: Any) -> None:
        """The client keys DOM subtrees on these ids, so they must not churn."""
        client = LiveViewTestClient(TwoIncludes2689)
        client.mount()
        first = _ids(client.render())
        client.send_event("toggle_b")
        second = _ids(client.render())
        assert first == second, (first, second)


class TestNestedAndComposedAxes:
    def test_three_renders_down_two_include_chains_stay_distinct(self, templates: Any) -> None:
        client = LiveViewTestClient(NestedIncludes2689)
        client.mount()
        html = client.render()
        ids = _ids(html)
        assert len(ids) == 3, f"expected three markers, got {ids} in {html}"
        assert len(set(ids)) == 3, (
            f"a nested include collided with a sibling include: {ids} (#2689)"
        )

    def test_only_includes_carry_the_site_path_into_their_fresh_context(
        self, templates: Any
    ) -> None:
        """Three ``only`` includes down two chains — the fresh-`Context` arm."""
        client = LiveViewTestClient(OnlyIncludes2689)
        client.mount()
        html = client.render()
        ids = _ids(html)
        assert len(ids) == 3, f"expected three markers, got {ids} in {html}"
        assert len(set(ids)) == 3, (
            f"`{{% include … only %}}` dropped the include-site path: {ids} (#2689)"
        )

    def test_base_and_child_include_sites_stay_distinct(self, templates: Any) -> None:
        """Two SEPARATELY PARSED templates composed by ``{% extends %}``.

        A bare per-parse ordinal gives base-site-0 and child-site-0 the same
        suffix, so the ordinal must carry the including template's identity.
        """
        client = LiveViewTestClient(ExtendsIncludes2689)
        client.mount()
        html = client.render()
        ids = _ids(html)
        assert len(ids) == 2, f"expected one marker per include site, got {ids} in {html}"
        assert len(set(ids)) == 2, (
            f"the base's include and the child's include collided: {ids} (#2689)"
        )

    def test_an_included_extender_does_not_collide_with_its_own_base(self, templates: Any) -> None:
        """The one case that needs the source hash inside ``site_id``.

        Both templates are parsed by the LOADER, independently, and their nodes
        are merged at render time — so both include ordinals start at 0 at the
        same depth of the path. Gating the hash off (``site_id`` = a bare
        ordinal) makes exactly this case go red and nothing else.
        """
        client = LiveViewTestClient(IncludedExtender2689)
        client.mount()
        html = client.render()
        ids = _ids(html)
        assert len(ids) == 2, f"expected one marker per include site, got {ids} in {html}"
        assert len(set(ids)) == 2, (
            f"an included extender's include collided with its base's: {ids} (#2689)"
        )

    def test_include_inside_a_for_loop_stays_distinct(self, templates: Any) -> None:
        """The include suffix must compose with ``dj_if_loop_path`` (#1832)."""
        client = LiveViewTestClient(IncludeInLoop2689)
        client.mount()
        html = client.render()
        ids = _ids(html)
        assert len(ids) == 3, f"expected one marker per iteration, got {ids} in {html}"
        assert len(set(ids)) == 3, f"loop iterations of an included fragment collided: {ids}"
