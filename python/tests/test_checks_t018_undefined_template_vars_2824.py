"""Tests for djust system check T018 (#2824).

An undefined ``{{ variable }}`` in a template previously rendered as empty
with NO error, NO warning, and NO system check — a typo'd or never-set
context name was invisible to both the test suite and
``manage.py djust_check``. T018 (WARNING) compares each LiveView's template
variable references against its statically-determinable context and warns
on names that resolve nowhere.

T018 delegates its extraction to the SAME helpers ``manage.py djust_typecheck``
already uses (shipped since v0.5.1 / #849), via the shared
``_check_view_source()`` extraction point added in this PR — a structural
cure for the #1646 parallel-path-drift class (one extraction body, two
entry points: the standalone command and this system check).

Empirical canary (#1459): ``test_fires_only_on_genuinely_undefined_var``
is the load-bearing test — it reproduces the issue's exact scenario (a
genuinely undefined var) alongside every false-positive class the issue's
own caveat enumerates (framework names, loop vars, dotted attribute tails,
filter pipes) in ONE template, and asserts the check fires on exactly one
name. A gate-off sibling (``test_gate_off_...``) proves each guard is
load-bearing, not decorative (#1468).
"""

import gc

import pytest


def _liveview_available():
    """Return True if LiveView can be imported (Rust extension built)."""
    try:
        from djust.live_view import LiveView  # noqa: F401

        return True
    except ImportError:
        return False


def _force_gc():
    """Force garbage collection to clean up dynamically created subclasses."""
    gc.collect()


def _t018(errors):
    return [e for e in errors if e.id == "djust.T018"]


@pytest.mark.skipif(not _liveview_available(), reason="Rust extension not available")
class TestT018EmpiricalCanary:
    """The load-bearing canary: fires on the genuine bug, silent on every
    false-positive class the issue's caveat enumerates -- all in one
    template, mirroring the issue's own reproduction exactly (inline
    ``template = "..."``, not ``template_name``)."""

    def test_fires_only_on_genuinely_undefined_var(self):
        from djust.live_view import LiveView
        from djust.checks.templates import check_undefined_template_vars

        class T018CanaryView(LiveView):
            # (a) genuinely undefined -- MUST fire.
            # (b) false-positive classes -- MUST NOT fire:
            #     csrf_token (framework), forloop (loop-injected),
            #     row.options (dotted tail resolves on root 'row'),
            #     value|default:"x" (filter-piped, resolves on 'value'),
            #     x declared by {% for x in items %} in this template.
            template = (
                "<div>"
                "{{ defined }}"
                "{{ never_set_anywhere }}"
                "{{ csrf_token }}"
                "{% for x in items %}{{ x }}{{ forloop.counter }}{% endfor %}"
                "{{ row.options }}"
                '{{ value|default:"x" }}'
                "</div>"
            )

            def mount(self, request, **kwargs):
                self.defined = "yes"
                self.items = [1, 2, 3]
                self.row = {"options": "x"}
                self.value = "v"

        try:
            errors = check_undefined_template_vars(None)
            t018 = [e for e in _t018(errors) if "T018CanaryView" in e.msg]
            names = set()
            import re as _re

            for e in t018:
                m = _re.search(r"undefined variable '([^']+)'", e.msg)
                if m:
                    names.add(m.group(1))
            assert names == {"never_set_anywhere"}, (
                "T018 must fire ONLY on the genuinely undefined name, "
                "not on any false-positive class. Got: %r" % names
            )
        finally:
            del T018CanaryView
            _force_gc()

    def test_gate_off_check_finds_nothing_when_neutered(self):
        """Non-tautology proof: neutering the extraction makes the canary
        find nothing, so the pass above is not vacuous (#1468)."""
        import djust.management.commands.djust_typecheck as _tc
        from djust.live_view import LiveView
        from djust.checks.templates import check_undefined_template_vars

        class T018GateOffView(LiveView):
            template = "<div>{{ never_set_anywhere }}</div>"

            def mount(self, request, **kwargs):
                pass

        orig = _tc._check_view_source
        _tc._check_view_source = lambda *a, **k: None
        try:
            errors = check_undefined_template_vars(None)
            hits = [e for e in _t018(errors) if "T018GateOffView" in e.msg]
            assert hits == [], "gate-off must suppress ALL findings"
        finally:
            _tc._check_view_source = orig
            del T018GateOffView
            _force_gc()


@pytest.mark.skipif(not _liveview_available(), reason="Rust extension not available")
class TestT018TemplateNameFilePath:
    """The ``template_name`` (file-based) path, and a literal
    ``get_context_data()`` dict return contributing a context key."""

    def test_fires_on_undefined_var_in_file_template(self, tmp_path, settings):
        from djust.live_view import LiveView
        from djust.checks.templates import check_undefined_template_vars

        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "t018_file.html").write_text(
            "<div>{{ from_mount }}{{ from_ctx }}{{ never_set_file }}</div>"
        )
        settings.TEMPLATES = [
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "DIRS": [str(tpl_dir)],
                "APP_DIRS": False,
            }
        ]

        class T018FileView(LiveView):
            template_name = "t018_file.html"

            def mount(self, request, **kwargs):
                self.from_mount = "yes"

            def get_context_data(self, **kwargs):
                return {"from_ctx": "yes"}

        try:
            errors = check_undefined_template_vars(None)
            hits = [e for e in _t018(errors) if "T018FileView" in e.msg]
            assert len(hits) == 1
            assert "never_set_file" in hits[0].msg
            assert not any("from_mount" in h.msg for h in hits)
            assert not any("from_ctx" in h.msg for h in hits)
            assert hits[0].file_path == str(tpl_dir / "t018_file.html")
            assert hits[0].line_number == 1
        finally:
            del T018FileView
            _force_gc()


@pytest.mark.skipif(not _liveview_available(), reason="Rust extension not available")
class TestT018ExtendsSkipped:
    """Known v1 limitation: templates using {% extends %} are skipped
    entirely (block-override context is inheritance-scoped, and this
    check has no way to see the parent template's context)."""

    def test_extends_template_is_skipped(self):
        from djust.live_view import LiveView
        from djust.checks.templates import check_undefined_template_vars

        class T018ExtendsView(LiveView):
            template = (
                '{% extends "base.html" %}'
                "{% block content %}{{ never_set_in_extends }}{% endblock %}"
            )

            def mount(self, request, **kwargs):
                pass

        try:
            errors = check_undefined_template_vars(None)
            hits = [e for e in _t018(errors) if "T018ExtendsView" in e.msg]
            assert hits == [], (
                "{%% extends %%} templates must be SKIPPED entirely (known v1 "
                "limitation), not flagged. Got: %r" % hits
            )
        finally:
            del T018ExtendsView
            _force_gc()


@pytest.mark.skipif(not _liveview_available(), reason="Rust extension not available")
class TestT018DynamicGetContextData:
    """Issue caveat #7: a get_context_data() this check can't statically
    follow (super() + subscript mutation) must never cause a false
    positive -- the WHOLE view is skipped silently."""

    def test_dynamic_context_data_view_is_skipped_not_false_positived(self):
        from djust.live_view import LiveView
        from djust.checks.templates import check_undefined_template_vars

        class T018DynamicCtxView(LiveView):
            # 'extra' is added via subscript-assignment on a super()-derived
            # dict -- not a literal {"extra": ...} return, not a
            # self.extra = ... assignment. Unanalyzable statically.
            template = "<div>{{ defined }}{{ extra }}</div>"

            def mount(self, request, **kwargs):
                self.defined = "yes"

            def get_context_data(self, **kwargs):
                context = super().get_context_data(**kwargs)
                context["extra"] = "computed dynamically"
                return context

        try:
            errors = check_undefined_template_vars(None)
            hits = [e for e in _t018(errors) if "T018DynamicCtxView" in e.msg]
            assert hits == [], (
                "a view with an unanalyzable get_context_data() must be "
                "skipped SILENTLY, never false-positived. Got: %r" % hits
            )
        finally:
            del T018DynamicCtxView
            _force_gc()

    def test_gate_off_guard_removed_reproduces_false_positive(self):
        """Non-tautology proof: with the dynamic-context guard neutered,
        the false positive on 'extra' reappears (#1468)."""
        from djust.live_view import LiveView
        from djust.checks import templates as _t

        class T018DynamicCtxGateOffView(LiveView):
            template = "<div>{{ defined }}{{ extra }}</div>"

            def mount(self, request, **kwargs):
                self.defined = "yes"

            def get_context_data(self, **kwargs):
                context = super().get_context_data(**kwargs)
                context["extra"] = "computed dynamically"
                return context

        orig_guard = _t._get_context_data_is_too_dynamic
        _t._get_context_data_is_too_dynamic = lambda cls: False
        try:
            errors = _t.check_undefined_template_vars(None)
            hits = [e for e in _t018(errors) if "T018DynamicCtxGateOffView" in e.msg]
            assert any("extra" in e.msg for e in hits), (
                "gate-off must reproduce the false positive on 'extra' -- "
                "proves the guard is load-bearing, not decorative"
            )
        finally:
            _t._get_context_data_is_too_dynamic = orig_guard
            del T018DynamicCtxGateOffView
            _force_gc()

    def test_bare_super_passthrough_is_not_dynamic(self):
        """`return super().get_context_data(**kwargs)` alone (no mutation)
        is a SAFE pattern -- must not skip the view or false-positive."""
        from djust.live_view import LiveView
        from djust.checks.templates import check_undefined_template_vars

        class T018BareSuperView(LiveView):
            template = "<div>{{ defined }}{{ never_set_bare_super }}</div>"

            def mount(self, request, **kwargs):
                self.defined = "yes"

            def get_context_data(self, **kwargs):
                return super().get_context_data(**kwargs)

        try:
            errors = check_undefined_template_vars(None)
            hits = [e for e in _t018(errors) if "T018BareSuperView" in e.msg]
            assert len(hits) == 1
            assert "never_set_bare_super" in hits[0].msg
        finally:
            del T018BareSuperView
            _force_gc()


@pytest.mark.skipif(not _liveview_available(), reason="Rust extension not available")
class TestT018AbstractAndSuppression:
    def test_abstract_base_class_is_skipped(self):
        from djust.live_view import LiveView
        from djust.checks.templates import check_undefined_template_vars

        class T018AbstractView(LiveView):
            abstract = True
            template = "<div>{{ never_set_abstract }}</div>"

            def mount(self, request, **kwargs):
                pass

        try:
            errors = check_undefined_template_vars(None)
            hits = [e for e in _t018(errors) if "T018AbstractView" in e.msg]
            assert hits == []
        finally:
            del T018AbstractView
            _force_gc()

    def test_suppressed_via_djust_config_short_id(self, settings):
        from djust.live_view import LiveView
        from djust.checks.templates import check_undefined_template_vars

        settings.DJUST_CONFIG = {"suppress_checks": ["T018"]}

        class T018SuppressedView(LiveView):
            template = "<div>{{ never_set_suppressed }}</div>"

            def mount(self, request, **kwargs):
                pass

        try:
            errors = check_undefined_template_vars(None)
            hits = [e for e in _t018(errors) if "T018SuppressedView" in e.msg]
            assert hits == []
        finally:
            del T018SuppressedView
            _force_gc()

    def test_suppressed_via_djust_config_qualified_id(self, settings):
        from djust.live_view import LiveView
        from djust.checks.templates import check_undefined_template_vars

        settings.DJUST_CONFIG = {"suppress_checks": ["djust.T018"]}

        class T018SuppressedQualifiedView(LiveView):
            template = "<div>{{ never_set_suppressed_q }}</div>"

            def mount(self, request, **kwargs):
                pass

        try:
            errors = check_undefined_template_vars(None)
            hits = [e for e in _t018(errors) if "T018SuppressedQualifiedView" in e.msg]
            assert hits == []
        finally:
            del T018SuppressedQualifiedView
            _force_gc()

    def test_inline_noqa_pragma_silences_specific_name(self):
        """Reuses djust_typecheck's own `{# djust_typecheck: noqa name #}`
        pragma (via the shared `_check_view_source`) rather than inventing
        a second noqa convention for the same underlying check."""
        from djust.live_view import LiveView
        from djust.checks.templates import check_undefined_template_vars

        class T018NoqaView(LiveView):
            template = (
                "<div>{{ never_set_noqa }}{{ still_undefined }}"
                "{# djust_typecheck: noqa never_set_noqa #}</div>"
            )

            def mount(self, request, **kwargs):
                pass

        try:
            errors = check_undefined_template_vars(None)
            hits = [e for e in _t018(errors) if "T018NoqaView" in e.msg]
            assert not any("never_set_noqa" in h.msg for h in hits)
            assert any("still_undefined" in h.msg for h in hits)
        finally:
            del T018NoqaView
            _force_gc()
