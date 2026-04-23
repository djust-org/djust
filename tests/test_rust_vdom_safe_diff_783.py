"""Regression tests for #783 — Rust VDOM diff must detect attribute changes
inside ``|safe`` HTML blobs that are **derived** in ``get_context_data``.

This mirrors the WizardMixin pattern: an instance attribute
``wizard_step_data`` is mutated by an event handler, and ``get_context_data``
re-computes a ``field_html`` dict of pre-rendered widget HTML strings from it.

Before the full fix landed, this path produced ``patches=[]`` with
``diff_ms: 0`` — the text-region fast path short-circuited because the
partial render reassembled HTML byte-identical to the previous render.
The root cause was in ``crates/djust_templates/src/parser.rs``: nested
``Include`` / ``CustomTag`` / ``InlineIf`` nodes were dropped from the
enclosing wrapper's dep set, so when only a key referenced *inside* the
nested subtree changed, the wrapper reused its cached fragment.

These tests exercise the known failure surfaces:

- Reassignment of the source dict (``wizard_step_data = {...}``)
- In-place nested mutation (``wizard_step_data["x"]["y"] = z``)
- Explicit ``_force_full_html = True`` bypass
- ``{% extends %} + {% block %}`` inheritance wrapping the ``|safe`` node
- ``{% if %}`` / ``{% for %}`` wrapping a nested ``{% include %}`` that
  contains the ``|safe`` expression (exact NYC-Claims-style shape)
- Inline conditional ``{{ a if cond else b }}`` inside a ``{% for %}``
  (latent sibling of the nested-Include bug, same dep-propagation miss)
"""

import json

import pytest
from django.conf import settings
from django.template import engines
from django.test import RequestFactory

from djust.live_view import LiveView
from djust.utils import _get_template_dirs_cached
from djust.websocket import _compute_changed_keys, _snapshot_assigns


def _simulate_event_cycle(view, mutate):
    """Mimic the websocket.py handle_event path: snapshot → mutate →
    compute changed_keys → _sync_state_to_rust → render_with_diff.

    Returns (html, patches_json_or_None, version).
    """
    pre = _snapshot_assigns(view)
    mutate(view)
    post = _snapshot_assigns(view)
    view._changed_keys = _compute_changed_keys(pre, post)
    view._sync_done_this_cycle = False
    html, patches, version = view.render_with_diff()
    return html, patches, version


def _prime(view_factory):
    """Mount, initialize Rust view, run baseline render.

    ``view_factory`` is a zero-arg callable returning a LiveView instance.
    """
    view = view_factory()
    request = RequestFactory().get("/")
    view.mount(request)
    view._initialize_rust_view(request)
    view._sync_state_to_rust()
    view.render_with_diff()
    return view


def _patches_list(patches_json):
    if patches_json is None:
        return None
    if isinstance(patches_json, (bytes, bytearray)):
        return json.loads(patches_json.decode())
    return json.loads(patches_json)


def _assert_produces_patch(html, patches_json, *, new_value, old_value=""):
    """Common assertions across all variants."""
    patches = _patches_list(patches_json)
    assert f'value="{new_value}"' in html, (
        f"rendered HTML must reflect the new |safe value; html={html!r}"
    )
    if old_value:
        assert f'value="{old_value}"' not in html, (
            f"rendered HTML must no longer contain the old value {old_value!r}"
        )
    assert patches is not None and len(patches) > 0, (
        f"expected non-empty patches; patches={patches!r} — "
        "empty patches with diff_ms=0 is the #783 symptom."
    )


class _WizardLike(LiveView):
    """Base view that derives ``field_html`` from ``wizard_step_data``."""

    template = "<div dj-root>{{ field_html.first_name|safe }}</div>"

    def mount(self, request, **kwargs):
        self.wizard_step_data = {"claimant": {"first_name": ""}}

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        value = self.wizard_step_data.get("claimant", {}).get("first_name", "")
        ctx["field_html"] = {
            "first_name": f'<input name="first_name" value="{value}">',
        }
        return ctx


class TestDerivedSafeBlobDiff:
    """Validate that derived ``|safe`` HTML changes produce VDOM patches.

    These cases all previously returned ``patches=[]`` with ``diff_ms: 0``
    before the #779 + a2b9483f fixes.
    """

    def test_reassignment_produces_patches(self):
        """Handler replaces ``wizard_step_data`` with a new dict object."""
        view = _prime(_WizardLike)

        def mutate(v):
            v.wizard_step_data = {"claimant": {"first_name": "Amanda"}}

        html, patches_json, version = _simulate_event_cycle(view, mutate)
        assert version == 2
        _assert_produces_patch(html, patches_json, new_value="Amanda")

    def test_in_place_nested_mutation_produces_patches(self):
        """Handler mutates ``wizard_step_data["claimant"]["first_name"]`` in
        place — the outer dict keeps its identity, so snapshot-based change
        tracking sees no change. The id()-fallback path in ``rust_bridge.py``
        must still detect that derived ``field_html`` changed."""
        view = _prime(_WizardLike)

        def mutate(v):
            v.wizard_step_data["claimant"]["first_name"] = "Amanda"

        html, patches_json, version = _simulate_event_cycle(view, mutate)
        assert version == 2
        _assert_produces_patch(html, patches_json, new_value="Amanda")

    def test_force_full_html_produces_patches(self):
        """Handler sets ``_force_full_html = True`` — exercises the
        commit a2b9483f codepath through ``set_changed_keys`` on every key."""
        view = _prime(_WizardLike)

        def mutate(v):
            v.wizard_step_data = {"claimant": {"first_name": "Amanda"}}
            v._force_full_html = True

        html, patches_json, version = _simulate_event_cycle(view, mutate)
        assert version == 2
        patches = _patches_list(patches_json)
        assert 'value="Amanda"' in html, f"new value missing from html; {html!r}"
        # The specific bug symptom is []-with-diff_ms=0. Non-empty patches
        # OR a None/absent patches field are both acceptable — the websocket
        # layer may emit a full-html update instead of patches when
        # _force_full_html is set.
        assert not (patches is not None and len(patches) == 0), (
            f"empty patches with _force_full_html is the #783 symptom; got patches={patches!r}"
        )

    def test_step_index_plus_data_change(self):
        """Simulates the ``demo_autofill`` → ``next_step`` sequence:
        both ``wizard_step_data`` and ``wizard_step_index`` change in the
        same event, swapping which branch of an ``{% if %}`` renders."""

        class WizardWithSteps(_WizardLike):
            template = (
                "<div dj-root>"
                "{% if wizard_step_index == 0 %}"
                '<section class="step-0">{{ field_html.first_name|safe }}</section>'
                "{% else %}"
                '<section class="step-1">{{ field_html.incident_type|safe }}</section>'
                "{% endif %}"
                "</div>"
            )

            def mount(self, request, **kwargs):
                super().mount(request, **kwargs)
                self.wizard_step_index = 0

            def get_context_data(self, **kwargs):
                ctx = super().get_context_data(**kwargs)
                ctx["field_html"]["incident_type"] = '<select name="incident_type"></select>'
                return ctx

        view = _prime(WizardWithSteps)

        def mutate(v):
            v.wizard_step_data = {"claimant": {"first_name": "Amanda"}}
            v.wizard_step_index = 1

        html, patches_json, version = _simulate_event_cycle(view, mutate)
        patches = _patches_list(patches_json)
        assert version == 2
        assert 'name="incident_type"' in html, (
            f"step swap must surface the new branch's field; html={html!r}"
        )
        assert patches is not None and len(patches) > 0, (
            f"step swap must produce patches; patches={patches!r}"
        )


class TestDerivedSafeBlobDiffExtends:
    """Same semantics through a ``{% extends %} + {% block %}`` chain.

    The inheritance resolution path in ``template.py`` / ``inheritance.rs``
    produces a flattened node list — regression-guard that partial render
    still detects the derived-key change after that flattening.
    """

    @pytest.fixture
    def template_dir(self, tmp_path):
        (tmp_path / "base_wizard.html").write_text(
            "<div dj-root><header>Wizard</header>{% block content %}default{% endblock %}</div>"
        )
        (tmp_path / "child_wizard.html").write_text(
            '{% extends "base_wizard.html" %}\n'
            "{% block content %}<section>"
            "{{ field_html.first_name|safe }}"
            "</section>{% endblock %}"
        )

        original_dirs = settings.TEMPLATES[0]["DIRS"]
        settings.TEMPLATES[0]["DIRS"] = [str(tmp_path)]
        engines._engines = {}
        engines.__dict__.pop("templates", None)
        _get_template_dirs_cached.cache_clear()
        try:
            yield str(tmp_path)
        finally:
            settings.TEMPLATES[0]["DIRS"] = original_dirs
            engines._engines = {}
            engines.__dict__.pop("templates", None)
            _get_template_dirs_cached.cache_clear()

    def test_extends_and_block_wrapping_produces_patches(self, template_dir):
        class ChildWizard(_WizardLike):
            template = None  # clear parent's inline template
            template_name = "child_wizard.html"

        view = _prime(ChildWizard)

        def mutate(v):
            v.wizard_step_data = {"claimant": {"first_name": "Amanda"}}

        html, patches_json, version = _simulate_event_cycle(view, mutate)
        assert version == 2
        _assert_produces_patch(html, patches_json, new_value="Amanda")


class TestSafeBlobDiffNestedInclude:
    """#783 exact reproduction: ``{% extends %}`` + ``{% if %}`` wrapping a
    ``{% include %}`` that contains the ``|safe`` expression.

    Before the fix, the enclosing ``If`` node's dep set was `{current_step_name}`
    because ``extract_from_nodes`` silently skipped the nested ``Include``.
    When only ``field_html`` changed, ``needs_render`` returned False, the
    cached ``If`` fragment was reused, and ``text_region_fast_path`` found
    identical old/new HTML → zero patches, Amanda never reached the client.
    """

    @pytest.fixture
    def template_dir(self, tmp_path):
        (tmp_path / "base.html").write_text(
            "<html><body>{% block content %}default{% endblock %}</body></html>"
        )
        (tmp_path / "step_claimant.html").write_text(
            '<div class="step">{{ field_html.first_name|safe }}</div>'
        )
        (tmp_path / "nested_wizard.html").write_text(
            '{% extends "base.html" %}\n'
            "{% block content %}\n<div dj-root>\n"
            '{% if current_step_name == "claimant" %}\n'
            '{% include "step_claimant.html" %}\n'
            "{% endif %}\n</div>\n{% endblock %}"
        )

        original_dirs = settings.TEMPLATES[0]["DIRS"]
        settings.TEMPLATES[0]["DIRS"] = [str(tmp_path)]
        engines._engines = {}
        engines.__dict__.pop("templates", None)
        _get_template_dirs_cached.cache_clear()
        try:
            yield str(tmp_path)
        finally:
            settings.TEMPLATES[0]["DIRS"] = original_dirs
            engines._engines = {}
            engines.__dict__.pop("templates", None)
            _get_template_dirs_cached.cache_clear()

    def test_inline_if_in_for_loop_produces_patches(self):
        """Sibling of the nested-include bug: an inline conditional
        ``{{ a if cond else b }}`` inside a ``{% for %}`` wrapper lost its
        dep on ``cond``. Changing ``cond`` alone produced ``patches=[]``.

        Fixed by the same parser.rs commit as the nested-include case:
        ``extract_from_nodes`` now has an arm for ``Node::InlineIf`` that
        extracts from all three expressions (``true_expr``, ``condition``,
        ``false_expr``).
        """

        class InlineIfView(LiveView):
            template = (
                "<div dj-root>"
                "{% for s in steps %}"
                '<span class="{{ "active" if step_active else "idle" }}">x</span>'
                "{% endfor %}"
                "</div>"
            )

            def mount(self, request, **kwargs):
                self.steps = [1, 2, 3]
                self.step_active = False

        view = _prime(InlineIfView)

        def mutate(v):
            v.step_active = True

        html, patches_json, version = _simulate_event_cycle(view, mutate)
        patches = _patches_list(patches_json)
        assert version == 2
        assert html.count('class="active"') == 3, (
            f"inline-if condition change must re-render the for body; html={html!r}"
        )
        assert patches is not None and len(patches) > 0, (
            f"expected non-empty patches; got {patches!r} — latent #783 sibling."
        )

    def test_if_include_wrapping_produces_patches(self, template_dir):
        class NestedWizard(_WizardLike):
            template = None
            template_name = "nested_wizard.html"

            def get_context_data(self, **kwargs):
                ctx = super().get_context_data(**kwargs)
                ctx["current_step_name"] = "claimant"
                return ctx

        view = _prime(NestedWizard)

        def mutate(v):
            v.wizard_step_data = {"claimant": {"first_name": "Amanda"}}

        html, patches_json, version = _simulate_event_cycle(view, mutate)
        assert version == 2
        _assert_produces_patch(html, patches_json, new_value="Amanda")


# ---------------------------------------------------------------------------
# Partial-render correctness harness (#783 P0 follow-up)
# ---------------------------------------------------------------------------
#
# Oracle for silent dep-drop regressions. For each template shape + mutation
# pair, renders twice:
#
# 1. Via the normal partial-render path (cache populated from the baseline
#    render, then a mutation triggers a fragment-level diff).
# 2. Control: same mutation applied to a fresh view with the Rust fragment
#    cache cleared (``_rust_view.clear_fragment_cache()``), forcing a full
#    collecting render.
#
# Byte-equality of the two HTML outputs is the correctness oracle: any
# dep-miss that causes partial render to reuse a stale cached fragment will
# diverge from the control, regardless of Node type or wrapper depth.
#
# Added in the #783 follow-up because the earlier symptom (``patches=[]``
# with ``diff_ms: 0``) is indistinguishable from "nothing changed" without
# this comparison.


def _assert_partial_matches_full(view_factory, mutate, *, expected_change_substring=""):
    """Render a mutation twice — once via partial render, once via a
    cache-cleared full render — and assert the two HTMLs are byte-identical.

    Parameters:
        view_factory: zero-arg callable returning a fresh LiveView instance.
        mutate: callable(view) that applies the state change to mutate.
        expected_change_substring: if non-empty, also asserts the substring
            appears in the partial-render HTML (so the test detects the
            degenerate "no change happened" case where both outputs agree
            trivially).
    """
    # Partial-render path
    partial_view = _prime(view_factory)
    partial_html, _partial_patches, _partial_version = _simulate_event_cycle(partial_view, mutate)

    # Control: same mutation, full render via cleared cache
    control_view = _prime(view_factory)
    pre = _snapshot_assigns(control_view)
    mutate(control_view)
    post = _snapshot_assigns(control_view)
    control_view._changed_keys = _compute_changed_keys(pre, post)
    control_view._sync_done_this_cycle = False
    # Force full re-render by clearing the Rust fragment cache. `last_vdom`
    # is kept intact so diff baseline is preserved.
    control_view._rust_view.clear_fragment_cache()
    control_html, _control_patches, _control_version = control_view.render_with_diff()

    assert partial_html == control_html, (
        "partial render diverged from control full render — dep-miss regression suspected.\n"
        f"partial: {partial_html!r}\n"
        f"control: {control_html!r}"
    )
    if expected_change_substring:
        assert expected_change_substring in partial_html, (
            f"expected change substring {expected_change_substring!r} missing from "
            f"partial HTML; {partial_html!r}"
        )


class TestPartialRenderCorrectness:
    """Parametrized partial-render vs full-render correctness oracle.

    Each case defines a template wrapper shape + mutation pair. The harness
    asserts byte-equality between partial and control full renders, catching
    silent dep-drops the way #783 / #774 went undetected. If a new parser or
    extractor regression silently reuses a stale cached fragment, the
    corresponding shape will fail.
    """

    def test_no_wrapper(self):
        class NoWrapperView(LiveView):
            template = "<div dj-root>{{ x }}</div>"

            def mount(self, request, **kwargs):
                self.x = "a"

        def mutate(v):
            v.x = "b"

        _assert_partial_matches_full(NoWrapperView, mutate, expected_change_substring=">b<")

    def test_if_wrapper(self):
        class IfView(LiveView):
            template = "<div dj-root>{% if show %}{{ x }}{% endif %}</div>"

            def mount(self, request, **kwargs):
                self.show = True
                self.x = "a"

        def mutate(v):
            v.x = "b"

        _assert_partial_matches_full(IfView, mutate, expected_change_substring="b")

    def test_for_loop_over_dicts(self):
        class ForView(LiveView):
            template = (
                "<div dj-root>{% for i in items %}<span>{{ i.name }}</span>{% endfor %}</div>"
            )

            def mount(self, request, **kwargs):
                self.items = [{"name": "alpha"}, {"name": "beta"}]

        def mutate(v):
            v.items = [{"name": "gamma"}, {"name": "delta"}]

        _assert_partial_matches_full(ForView, mutate, expected_change_substring="gamma")

    def test_with_wrapper(self):
        class WithView(LiveView):
            template = "<div dj-root>{% with y=x %}<span>{{ y }}</span>{% endwith %}</div>"

            def mount(self, request, **kwargs):
                self.x = "a"

        def mutate(v):
            v.x = "b"

        _assert_partial_matches_full(WithView, mutate, expected_change_substring="b")

    def test_extends_if_include_safe_blob(self, tmp_path):
        """Exact #783 shape: extends → block → if → include → |safe blob."""
        (tmp_path / "corr_base.html").write_text(
            "<html><body>{% block content %}default{% endblock %}</body></html>"
        )
        (tmp_path / "corr_step.html").write_text(
            '<div class="step">{{ field_html.first_name|safe }}</div>'
        )
        (tmp_path / "corr_wizard.html").write_text(
            '{% extends "corr_base.html" %}\n'
            "{% block content %}\n<div dj-root>\n"
            '{% if current_step_name == "claimant" %}\n'
            '{% include "corr_step.html" %}\n'
            "{% endif %}\n</div>\n{% endblock %}"
        )

        original_dirs = settings.TEMPLATES[0]["DIRS"]
        settings.TEMPLATES[0]["DIRS"] = [str(tmp_path)]
        engines._engines = {}
        engines.__dict__.pop("templates", None)
        _get_template_dirs_cached.cache_clear()
        try:

            class CorrectnessWizard(_WizardLike):
                template = None
                template_name = "corr_wizard.html"

                def get_context_data(self, **kwargs):
                    ctx = super().get_context_data(**kwargs)
                    ctx["current_step_name"] = "claimant"
                    return ctx

            def mutate(v):
                v.wizard_step_data = {"claimant": {"first_name": "Amanda"}}

            _assert_partial_matches_full(
                CorrectnessWizard, mutate, expected_change_substring='value="Amanda"'
            )
        finally:
            settings.TEMPLATES[0]["DIRS"] = original_dirs
            engines._engines = {}
            engines.__dict__.pop("templates", None)
            _get_template_dirs_cached.cache_clear()

    def test_inline_if_in_for(self):
        """InlineIf condition inside a {% for %} wrapper — sibling of the
        nested-include bug (#783). Changing ``active`` alone must re-render
        every span in the loop."""

        class InlineIfInForView(LiveView):
            template = (
                "<div dj-root>"
                "{% for s in steps %}"
                '<span class="{{ "on" if active else "off" }}">x</span>'
                "{% endfor %}"
                "</div>"
            )

            def mount(self, request, **kwargs):
                self.steps = [1, 2, 3]
                self.active = False

        def mutate(v):
            v.active = True

        _assert_partial_matches_full(
            InlineIfInForView, mutate, expected_change_substring='class="on"'
        )

    # --- Additional wrapper shapes per code-review feedback on #785 -----

    def test_spaceless_wrapper(self):
        """``{% spaceless %}`` strips whitespace between tags. Partial-render
        must still pick up changes to variables referenced inside the block —
        regression guard for dep propagation through Spaceless nodes."""

        class SpacelessView(LiveView):
            template = (
                "<div dj-root>{% spaceless %}"
                "<span>{{ x }}</span>"
                "<span>{{ y }}</span>"
                "{% endspaceless %}</div>"
            )

            def mount(self, request, **kwargs):
                self.x = "alpha"
                self.y = "beta"

        def mutate(v):
            v.x = "zeta"

        _assert_partial_matches_full(SpacelessView, mutate, expected_change_substring="zeta")

    def test_nested_with_chain(self):
        """Nested ``{% with %}`` blocks — the inner block must still see
        outer-scope rebindings when the outer's source variable changes.
        Regression guard for dep propagation across With node chains."""

        class NestedWithView(LiveView):
            template = (
                "<div dj-root>"
                "{% with outer=x %}"
                "{% with inner=outer %}"
                "<span>{{ inner }}</span>"
                "{% endwith %}"
                "{% endwith %}"
                "</div>"
            )

            def mount(self, request, **kwargs):
                self.x = "root-a"

        def mutate(v):
            v.x = "root-b"

        _assert_partial_matches_full(NestedWithView, mutate, expected_change_substring="root-b")

    def test_standalone_block_without_extends(self):
        """``{% block %}`` used without ``{% extends %}`` is still valid as
        a content wrapper. Changing a variable inside a standalone block
        must trigger a partial re-render of that block."""

        class StandaloneBlockView(LiveView):
            template = (
                "<div dj-root>{% block header %}<h1>{{ title }}</h1>{% endblock %}<p>body</p></div>"
            )

            def mount(self, request, **kwargs):
                self.title = "old"

        def mutate(v):
            v.title = "new"

        _assert_partial_matches_full(StandaloneBlockView, mutate, expected_change_substring="new")

    def test_verbatim_island_does_not_break_sibling_deps(self):
        """``{% verbatim %}`` prints its body literally. Crucially, a
        variable reference INSIDE verbatim is NOT a real dep — but a sibling
        variable outside the verbatim block must still re-render correctly.
        Regression guard against mistakenly treating verbatim bodies as
        real variable references."""

        class VerbatimView(LiveView):
            template = (
                "<div dj-root>"
                "<pre>{% verbatim %}{{ not_a_var }}{% endverbatim %}</pre>"
                "<span>{{ real_var }}</span>"
                "</div>"
            )

            def mount(self, request, **kwargs):
                self.real_var = "before"

        def mutate(v):
            v.real_var = "after"

        _assert_partial_matches_full(VerbatimView, mutate, expected_change_substring="after")

    def test_filter_chain_on_attribute(self):
        """Chained filters on a dotted attribute: changes to the root
        attribute value must invalidate cached fragments referencing
        ``obj.field|filter|another``."""

        class FilterChainView(LiveView):
            template = "<div dj-root><span>{{ user.name|lower|truncatechars:10 }}</span></div>"

            def mount(self, request, **kwargs):
                self.user = {"name": "Alice"}

        def mutate(v):
            v.user = {"name": "BOB-The-Very-Very-Long"}

        _assert_partial_matches_full(FilterChainView, mutate, expected_change_substring="bob")
