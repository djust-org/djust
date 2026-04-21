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
    assert (
        f'value="{new_value}"' in html
    ), f"rendered HTML must reflect the new |safe value; html={html!r}"
    if old_value:
        assert (
            f'value="{old_value}"' not in html
        ), f"rendered HTML must no longer contain the old value {old_value!r}"
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
            f"empty patches with _force_full_html is the #783 symptom; " f"got patches={patches!r}"
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
        assert (
            'name="incident_type"' in html
        ), f"step swap must surface the new branch's field; html={html!r}"
        assert (
            patches is not None and len(patches) > 0
        ), f"step swap must produce patches; patches={patches!r}"


class TestDerivedSafeBlobDiffExtends:
    """Same semantics through a ``{% extends %} + {% block %}`` chain.

    The inheritance resolution path in ``template.py`` / ``inheritance.rs``
    produces a flattened node list — regression-guard that partial render
    still detects the derived-key change after that flattening.
    """

    @pytest.fixture
    def template_dir(self, tmp_path):
        (tmp_path / "base_wizard.html").write_text(
            "<div dj-root><header>Wizard</header>" "{% block content %}default{% endblock %}</div>"
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
        assert (
            html.count('class="active"') == 3
        ), f"inline-if condition change must re-render the for body; html={html!r}"
        assert (
            patches is not None and len(patches) > 0
        ), f"expected non-empty patches; got {patches!r} — latent #783 sibling."

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
