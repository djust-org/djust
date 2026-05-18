"""Tests for ``djust.V011`` — sticky-child opt-in misconfiguration check.

V011 is ADR-018 Decision 5's static enforcement: a class used as a sticky
child with ``enable_state_snapshot = True`` whose embedding parent does NOT
opt into ``enable_state_snapshot`` gets silently-incomplete persistence —
the child save is skipped (the both-opt-in gate
``sticky_child_should_persist`` fails). V011 surfaces that misconfiguration
at ``manage.py check`` time.

Mirrors the shape of the A075 scanner (``test_checks_a075.py``): walks
template directories, scans each ``{% live_render ... sticky=True %}`` tag,
resolves the child class via ``import_string``, and finds the embedding
parent LiveView via its ``template_name``.

The synthetic fixture classes live in ``djust.tests.checkviews_v011`` so the
V011 check's ``import_string`` resolution succeeds and so the parent-lookup
(by ``template_name``) has parent LiveView subclasses to match against.
"""

from __future__ import annotations

import pytest

# Import the fixture module at collection time so its parent LiveView
# subclasses (OptedInParent, NotOptedInParent, …) are registered in the
# LiveView subclass graph BEFORE ``check_sticky_child_optin`` builds its
# template_name -> parent map. (The check resolves the *child* via
# import_string, which imports the module too — but the parent map is
# built first, so the parents must already exist.)
import djust.tests.checkviews_v011  # noqa: F401

# Module-level child dotted paths (resolvable via import_string).
_OPTIN_CHILD = "djust.tests.checkviews_v011.OptedInChild"
_NO_OPTIN_CHILD = "djust.tests.checkviews_v011.NotOptedInChild"


def _live_render_template(child_path, sticky=True, verbatim=False):
    """Build a template body embedding ``child_path`` via {% live_render %}.

    String concatenation only (no ``%`` formatting) — the Django tag syntax
    ``{% load %}`` / ``{% live_render %}`` contains ``%`` characters that
    would otherwise be mis-parsed as printf conversion specifiers.
    """
    sticky_kwarg = " sticky=True" if sticky else ""
    tag = '<div>{% live_render "' + child_path + '"' + sticky_kwarg + " %}</div>"
    body = "{% load live_tags %}\n"
    if verbatim:
        body += "{% verbatim %}\n" + tag + "\n{% endverbatim %}\n"
    else:
        body += tag + "\n"
    return body


def _set_templates(settings, tpl_dir):
    """Point settings.TEMPLATES at a single tmp template DIRS dir."""
    settings.TEMPLATES = [
        {
            "DIRS": [str(tpl_dir)],
            "BACKEND": "django.template.backends.django.DjangoTemplateBackend",
        }
    ]


class TestV011StickyChildOptin:
    """V011 fires when a sticky child opts in but its parent does not."""

    def test_v011_fires_on_child_optin_parent_not(self, tmp_path, settings):
        """Child has enable_state_snapshot=True, parent does not → one V011."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        # NotOptedInParent.template_name == "v011_parent_no_optin.html".
        (tpl_dir / "v011_parent_no_optin.html").write_text(_live_render_template(_OPTIN_CHILD))
        _set_templates(settings, tpl_dir)

        from djust.checks import check_sticky_child_optin

        errors = check_sticky_child_optin(None)
        v011 = [e for e in errors if e.id == "djust.V011"]
        assert len(v011) == 1, "expected exactly one V011 for the misconfiguration"
        assert "OptedInChild" in v011[0].msg
        assert "NotOptedInParent" in v011[0].msg

    def test_v011_silent_when_both_opt_in(self, tmp_path, settings):
        """Child + parent both enable_state_snapshot=True → zero V011."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        # OptedInParent.template_name == "v011_parent_optin.html".
        (tpl_dir / "v011_parent_optin.html").write_text(_live_render_template(_OPTIN_CHILD))
        _set_templates(settings, tpl_dir)

        from djust.checks import check_sticky_child_optin

        errors = check_sticky_child_optin(None)
        v011 = [e for e in errors if e.id == "djust.V011"]
        assert v011 == [], "both opted in — no misconfiguration"

    def test_v011_silent_when_neither_opts_in(self, tmp_path, settings):
        """Neither opts in → zero V011 (nothing to persist, nothing to flag)."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "v011_parent_no_optin.html").write_text(_live_render_template(_NO_OPTIN_CHILD))
        _set_templates(settings, tpl_dir)

        from djust.checks import check_sticky_child_optin

        errors = check_sticky_child_optin(None)
        v011 = [e for e in errors if e.id == "djust.V011"]
        assert v011 == [], "neither opts in — no Decision-5 misconfiguration"

    def test_v011_silent_when_only_parent_opts_in(self, tmp_path, settings):
        """Parent opts in, child does not → zero V011 (not the misconfig)."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "v011_parent_optin.html").write_text(_live_render_template(_NO_OPTIN_CHILD))
        _set_templates(settings, tpl_dir)

        from djust.checks import check_sticky_child_optin

        errors = check_sticky_child_optin(None)
        v011 = [e for e in errors if e.id == "djust.V011"]
        assert v011 == [], "child not opted in — nothing to persist"

    def test_v011_silent_on_non_sticky_embed(self, tmp_path, settings):
        """A ``{% live_render %}`` WITHOUT sticky=True → zero V011.

        Non-sticky embeds are explicitly unsupported for persistence
        (Decision 1) — they are a documented limitation, not a flagged
        misconfiguration.
        """
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "v011_parent_no_optin.html").write_text(
            _live_render_template(_OPTIN_CHILD, sticky=False)
        )
        _set_templates(settings, tpl_dir)

        from djust.checks import check_sticky_child_optin

        errors = check_sticky_child_optin(None)
        v011 = [e for e in errors if e.id == "djust.V011"]
        assert v011 == [], "non-sticky embed is not flagged"

    def test_v011_silent_inside_verbatim(self, tmp_path, settings):
        """The misconfigured tag inside a verbatim block → zero V011.

        Docs-example suppression — mirrors the A075 / #1004 fix.
        """
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "v011_parent_no_optin.html").write_text(
            _live_render_template(_OPTIN_CHILD, verbatim=True)
        )
        _set_templates(settings, tpl_dir)

        from djust.checks import check_sticky_child_optin

        errors = check_sticky_child_optin(None)
        v011 = [e for e in errors if e.id == "djust.V011"]
        assert v011 == [], "tag inside a verbatim block is a docs example, not flagged"

    def test_v011_suppressed_via_config(self, tmp_path, settings):
        """DJUST_CONFIG['suppress_checks']=['V011'] → zero V011 on a real misconfig."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "v011_parent_no_optin.html").write_text(_live_render_template(_OPTIN_CHILD))
        _set_templates(settings, tpl_dir)
        settings.DJUST_CONFIG = {"suppress_checks": ["V011"]}

        from djust.checks import check_sticky_child_optin

        errors = check_sticky_child_optin(None)
        v011 = [e for e in errors if e.id == "djust.V011"]
        assert v011 == [], "V011 is suppressed via DJUST_CONFIG"

    def test_v011_empirical_canary(self, tmp_path, settings):
        """#1459 empirical canary — the tooling-class validation.

        Construct a synthetic misconfigured view setup FROM SCRATCH (a
        template embedding an opted-in sticky child under a non-opted-in
        parent), run ``check_sticky_child_optin`` against it, and confirm
        it reports a ``djust.V011`` whose ``file_path`` points at the child
        class source. This is the "the check catches the bug class it
        claims to catch" proof — not just unit assertions.
        """
        import inspect as _inspect

        from djust.tests.checkviews_v011 import OptedInChild

        # --- Synthetic misconfigured setup, built fresh ---
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "v011_parent_no_optin.html").write_text(_live_render_template(_OPTIN_CHILD))
        _set_templates(settings, tpl_dir)

        from djust.checks import check_sticky_child_optin

        # --- Run the tool against the synthetic trigger ---
        errors = check_sticky_child_optin(None)
        v011 = [e for e in errors if e.id == "djust.V011"]

        # --- Confirm the tool reports the trigger ---
        assert len(v011) == 1, "the check must report the synthetic misconfiguration"
        warning = v011[0]
        assert warning.file_path == _inspect.getfile(OptedInChild), (
            "V011 file_path must point at the child class source"
        )
        assert warning.line_number is not None
        assert "enable_state_snapshot" in warning.hint
        assert "enable_state_snapshot" in warning.fix_hint

    def test_v011_multiple_parents(self, tmp_path, settings):
        """ONE template is the ``template_name`` of TWO parent classes — one
        opted-in, one not — so it embeds the same opted-in sticky child for
        both. Exercises V011's multi-parent loop (``parent_map`` value is a
        list of length 2) → exactly one V011, for the non-opted-in parent.
        """
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        # SecondNotOptedInParent.template_name == "v011_parent_multi.html"
        # MultiOptedInParent.template_name      == "v011_parent_multi.html"  (same!)
        # The single template thus maps to BOTH parents in parent_map.
        (tpl_dir / "v011_parent_multi.html").write_text(_live_render_template(_OPTIN_CHILD))
        _set_templates(settings, tpl_dir)

        from djust.checks import check_sticky_child_optin

        errors = check_sticky_child_optin(None)
        v011 = [e for e in errors if e.id == "djust.V011"]
        assert len(v011) == 1, "only the non-opted-in parent of the two should be flagged"
        assert "SecondNotOptedInParent" in v011[0].msg

    @pytest.mark.parametrize(
        "parent_opts_in,expect_v011",
        [
            (False, True),  # misconfig → V011 fires
            (True, False),  # both opt in → silent
        ],
    )
    def test_v011_gate_off_self_test(self, tmp_path, settings, parent_opts_in, expect_v011):
        """#1468 gate-off self-test — proves the positive assertion is not
        a tautology.

        With the misconfig in place (parent does NOT opt in) V011 fires;
        gating the misconfig off (parent DOES opt in) makes the fire-case
        assertion fail — i.e. the test exercises the real branch, not a
        constant-true condition.
        """
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        # Pick the parent template by opt-in state — both embed the
        # opted-in child; only the parent's enable_state_snapshot differs.
        relname = "v011_parent_optin.html" if parent_opts_in else "v011_parent_no_optin.html"
        (tpl_dir / relname).write_text(_live_render_template(_OPTIN_CHILD))
        _set_templates(settings, tpl_dir)

        from djust.checks import check_sticky_child_optin

        errors = check_sticky_child_optin(None)
        v011 = [e for e in errors if e.id == "djust.V011"]
        if expect_v011:
            assert len(v011) == 1, "misconfig must fire V011"
        else:
            assert v011 == [], "both opted in — V011 must be silent (gate-off case)"
