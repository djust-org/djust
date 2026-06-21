"""Security regression tests for the dj-model mass-assignment guard (CWE-915).

Root cause
----------
``ModelBindingMixin`` is in the ``LiveView`` base MRO, so EVERY LiveView
exposes a default ``@event_handler update_model(self, field, value)`` that
``setattr``s a view attribute whose NAME is client-supplied. Before this fix
the only gates were: reject ``_``-prefixed names, reject the 14-entry
``FORBIDDEN_MODEL_FIELDS`` denylist, optionally require membership in
``allowed_model_fields`` (which defaulted to ``None`` = allow ALL public
attrs), and require ``hasattr``. So a client could set ANY public, existing
view attribute — ``is_admin``, ``account_id``, ``total_price``, … — not just
the fields actually bound with ``dj-model="…"`` in the template. That is an
IDOR / authz-flag / price-tampering surface on the standard djust state
pattern (CWE-915 mass assignment).

Fix (secure-by-default auto-allowlist from the TEMPLATE SOURCE)
--------------------------------------------------------------
The render pipeline derives the bindable-field set from the TEMPLATE SOURCE —
the Rust template engine walks the parsed AST's ``Node::Text`` literals and
collects every static ``dj-model="<field>"`` binding (covering
``{% extends %}``/``{% include %}``). It is recorded on
``self._dj_model_fields`` each render via
``ModelBindingMixin._record_dj_model_fields_from_rust`` (LiveView paths) /
``_record_dj_model_fields_from_source`` (embedded children). ``update_model``
is fail-closed: a field is bindable iff it is in ``self._dj_model_fields``
(auto-allowlist) OR in an explicit ``allowed_model_fields`` (union semantics).

This replaces the earlier approach of parsing the RENDERED HTML, which was
attacker-influenceable and re-opened the hole TWICE in review: rendered output
carries attacker data in text nodes, in unquoted-interpolated attributes
(``<div x={{ v }}>``), and in ``|safe`` content. The template SOURCE is the
immune source — attacker data only ever reaches output through ``{{ }}``
``Node::Variable`` substitution at render time, never a ``Node::Text`` literal.
A dynamic ``dj-model="{{ var }}"`` binding is NOT captured (fail-closed; opt in
via ``allowed_model_fields``).

These tests cover (per the reproduce-first + gate-off discipline):
  * the exploit is blocked AFTER a render exposes only the legit binding;
  * the legit dj-model field still updates;
  * ``allowed_model_fields`` explicit override still works (union);
  * FORBIDDEN + ``_``-prefix still blocked;
  * the auto-allowlist refreshes across re-renders;
  * the three rendered-output POISONING vectors (text-node, unquoted-
    interpolated attr, ``|safe`` element injection) leave the field NOT
    bindable when driven END-TO-END through ``render_with_diff`` — these are
    the acceptance criteria for finding #3;
  * a real-render integration path populates ``_dj_model_fields`` and gates
    ``update_model`` end-to-end (reproduction fidelity — exercises the actual
    ``render_with_diff`` chokepoint, not just the helper).
"""

from __future__ import annotations

import importlib.util
import shutil
import tempfile
from pathlib import Path

import pytest

# Direct import to avoid pulling in Django/channels via djust.__init__ for the
# unit-level tests (mirrors python/tests/test_model_binding.py). The allowlist
# is now derived from the TEMPLATE SOURCE via the Rust template engine
# (``dj_model_fields_from_template``), which imports standalone without Django.
_spec = importlib.util.spec_from_file_location(
    "model_binding_allowlist",
    Path(__file__).resolve().parent.parent / "mixins" / "model_binding.py",
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
ModelBindingMixin = _mod.ModelBindingMixin
FORBIDDEN_MODEL_FIELDS = _mod.FORBIDDEN_MODEL_FIELDS


def _make_view(**attrs):
    """Fresh ModelBindingMixin subclass per call (dynamic-subclass discipline
    #1109 — never mutate a shared class)."""
    cls = type("AllowlistView", (ModelBindingMixin,), dict(attrs))
    return cls()


def _record(view, template_source):
    """Populate ``view._dj_model_fields`` from a template SOURCE string via the
    real Rust template walker (the production collection path). Replaces the old
    helper that fed RENDERED HTML — the allowlist is now derived from
    developer-authored template text, immune to rendered-output poisoning."""
    view._record_dj_model_fields_from_source(template_source)


class TestMassAssignmentRepro:
    """The CWE-915 exploit: a view with one dj-model field (``search``) plus
    sensitive public attrs (``is_admin``, ``account_id``) that are NEVER bound
    via dj-model. After a render whose TEMPLATE exposes only
    ``dj-model="search"``, the client may update ``search`` but NOT the
    sensitive attrs."""

    def _rendered_view(self):
        v = _make_view(search="", is_admin=False, account_id=1)
        # The template binds ONLY dj-model="search".
        _record(
            v,
            '<div dj-root><input dj-model="search"><span>{{ search }}</span></div>',
        )
        return v

    def test_legit_dj_model_field_updates(self):
        v = self._rendered_view()
        v.update_model(field="search", value="hello")
        assert v.search == "hello"

    def test_is_admin_blocked(self):
        """The exploit: flipping a bool the template never bound. This test
        FAILS before the fix (is_admin gets set to True) and passes after."""
        v = self._rendered_view()
        v.update_model(field="is_admin", value="true")
        assert v.is_admin is False, (
            "is_admin was set via update_model even though it is not bound via "
            "dj-model — CWE-915 mass-assignment regression"
        )

    def test_account_id_blocked(self):
        """IDOR shape: setting an arbitrary id the template never bound."""
        v = self._rendered_view()
        v.update_model(field="account_id", value=999)
        assert v.account_id == 1, (
            "account_id was set via update_model even though it is not bound "
            "via dj-model — CWE-915 mass-assignment regression"
        )

    def test_skip_render_not_set_when_blocked(self):
        v = self._rendered_view()
        v.update_model(field="is_admin", value="true")
        assert not getattr(v, "_skip_render", False)


class TestFailClosedBeforeRender:
    """Before any render populates the auto-allowlist (and with no explicit
    ``allowed_model_fields``), NOTHING is bindable — fail-closed by default."""

    def test_nothing_bindable_before_render(self):
        v = _make_view(search="")
        assert v._dj_model_fields == frozenset()
        v.update_model(field="search", value="x")
        assert v.search == "", "field was bindable before any render exposed it"


class TestExplicitAllowedModelFields:
    """``allowed_model_fields`` is a UNION with the auto-allowlist: it permits
    fields that are never bound as dj-model in the template (the documented
    escape hatch for programmatically-written / dynamically-bound values)."""

    def test_explicit_allow_without_render(self):
        v = _make_view(name="", email="", role="user", allowed_model_fields={"name", "email"})
        # No render performed → auto-allowlist empty; explicit allow still works.
        v.update_model(field="name", value="John")
        assert v.name == "John"

    def test_explicit_allow_blocks_unlisted(self):
        v = _make_view(name="", role="user", allowed_model_fields={"name"})
        v.update_model(field="role", value="admin")
        assert v.role == "user"

    def test_union_auto_and_explicit(self):
        """A field in the auto-allowlist (template-bound) AND a different field
        in the explicit allowlist are BOTH bindable; an attr in neither is
        not."""
        v = _make_view(search="", flag=False, secret="", allowed_model_fields={"flag"})
        _record(v, '<input dj-model="search">')
        v.update_model(field="search", value="rendered-ok")
        v.update_model(field="flag", value="true")
        v.update_model(field="secret", value="nope")
        assert v.search == "rendered-ok"
        assert v.flag is True
        assert v.secret == ""


class TestForbiddenAndPrivateStillBlocked:
    """Defense-in-depth: even if a forbidden/private name somehow appeared in
    the auto-allowlist, the earlier gates still reject it."""

    def test_private_prefix_blocked_even_if_in_allowlist(self):
        v = _make_view(_secret="orig")
        # Force the name into the auto-allowlist; the _-prefix gate must win.
        v._dj_model_fields = frozenset({"_secret"})
        v.update_model(field="_secret", value="hacked")
        assert v._secret == "orig"

    def test_forbidden_field_blocked_even_if_in_allowlist(self):
        v = _make_view(template_name="ok.html")
        v._dj_model_fields = frozenset({"template_name"})
        v.update_model(field="template_name", value="evil.html")
        assert v.template_name == "ok.html"


class TestAutoAllowlistAcrossReRenders:
    """The auto-allowlist refreshes on every render: a field bound in render N
    is bindable; if render N+1's template no longer binds it, it becomes
    unbindable."""

    def test_field_becomes_bindable_after_render_exposes_it(self):
        v = _make_view(a="", b="")
        _record(v, '<input dj-model="a">')
        v.update_model(field="b", value="x")
        assert v.b == "", "b bindable before any render bound it"
        # Second render now binds b too.
        _record(v, '<input dj-model="a"><input dj-model="b">')
        v.update_model(field="b", value="y")
        assert v.b == "y"

    def test_field_becomes_unbindable_when_render_drops_it(self):
        v = _make_view(a="", b="")
        _record(v, '<input dj-model="a"><input dj-model="b">')
        v.update_model(field="b", value="first")
        assert v.b == "first"
        # Re-render no longer binds b (e.g. removed from the template).
        _record(v, '<input dj-model="a">')
        v.update_model(field="b", value="second")
        assert v.b == "first", "b stayed bindable after render dropped its binding"


class TestRecordDjModelFieldsFromSource:
    """Unit coverage of the template-source scanner itself (both quote styles,
    empty, nested-in-tags, dynamic-binding-not-captured, fail-closed)."""

    def test_double_and_single_quotes(self):
        v = _make_view()
        _record(v, "<input dj-model=\"x\"><input dj-model='y'>")
        assert v._dj_model_fields == frozenset({"x", "y"})

    def test_empty_source_resets_to_empty(self):
        v = _make_view()
        v._dj_model_fields = frozenset({"stale"})
        _record(v, "")
        assert v._dj_model_fields == frozenset()

    def test_no_dj_model_yields_empty(self):
        v = _make_view()
        _record(v, "<div><input type='text' name='q'></div>")
        assert v._dj_model_fields == frozenset()

    def test_binding_nested_in_if_and_for_is_collected(self):
        v = _make_view()
        _record(
            v,
            "{% if flag %}<input dj-model='a'>{% endif %}"
            "{% for it in items %}<input dj-model='b'>{% endfor %}",
        )
        assert v._dj_model_fields == frozenset({"a", "b"})

    def test_dynamic_binding_not_captured(self):
        # dj-model="{{ field }}" straddles Text + Variable — the literal value
        # is empty between the quotes, so nothing is captured (fail-closed).
        v = _make_view()
        _record(v, '<input dj-model="{{ field }}">')
        assert v._dj_model_fields == frozenset()

    def test_data_dj_model_boundary_not_overmatched(self):
        # A different attribute that merely ends in "dj-model" must NOT widen
        # the allowlist (the old serialized-HTML regex over-matched).
        v = _make_view()
        _record(v, '<input dj-model="search" data-dj-model="is_admin">')
        assert v._dj_model_fields == frozenset({"search"})

    def test_malformed_source_fails_closed(self):
        v = _make_view(search="")
        # Garbage should not raise and should not over-allow.
        _record(v, "<<<not really html dj-model=oops")
        v.update_model(field="search", value="x")
        # search wasn't a real quoted attribute here → blocked (fail-closed).
        assert v.search == ""


# ---------------------------------------------------------------------------
# Real-render integration: exercise the ACTUAL render_with_diff chokepoint so
# the test pins the production wiring, not just the helper (reproduction
# fidelity — CLAUDE.md). Requires Django settings + the Rust extension.
# ---------------------------------------------------------------------------


class _SingleTemplateHarness:
    """Write one template to a tmp dir wired into Django + Rust search paths."""

    def __init__(self, name: str, src: str):
        self._tmpdir = Path(tempfile.mkdtemp())
        (self._tmpdir / name).write_text(src)
        self._override = None

    def __enter__(self):
        from django.conf import settings
        from django.test import override_settings
        from djust.utils import clear_template_dirs_cache

        templates = [dict(t) for t in settings.TEMPLATES]
        templates[0] = dict(templates[0])
        templates[0]["DIRS"] = [str(self._tmpdir), *templates[0].get("DIRS", [])]
        self._override = override_settings(TEMPLATES=templates)
        self._override.enable()
        clear_template_dirs_cache()
        return self

    def __exit__(self, *exc):
        from djust.utils import clear_template_dirs_cache

        if self._override is not None:
            self._override.disable()
        clear_template_dirs_cache()
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        return False


_TEMPLATE = '<div dj-root><input type="text" dj-model="search"><span>{{ search }}</span></div>'


def _make_live_view_class(template_name="allowlist_view.html", extra=None):
    from djust import LiveView

    body = {
        "template_name": template_name,
        "search": "",
        "is_admin": False,
        "account_id": 1,
        "comment": "",
        "mount": lambda self, request, **kwargs: None,
        "get_context_data": lambda self, **kwargs: {
            "search": self.search,
            "is_admin": self.is_admin,
            "account_id": self.account_id,
            "comment": self.comment,
        },
    }
    if extra:
        body.update(extra)
    return type("AllowlistLiveView", (LiveView,), body)


@pytest.mark.django_db
class TestRealRenderPipelinePopulatesAllowlist:
    def test_render_with_diff_populates_then_gates_update_model(self):
        with _SingleTemplateHarness("allowlist_view.html", _TEMPLATE):
            cls = _make_live_view_class()
            v = cls()
            v.mount(None)
            # Drive the dominant render chokepoint (mount + every WS event +
            # HTTP-GET baseline all funnel through render_with_diff).
            html, _patches, _version = v.render_with_diff(None)

            # The auto-allowlist was populated from the TEMPLATE SOURCE.
            assert 'dj-model="search"' in html
            assert "search" in v._dj_model_fields
            assert "is_admin" not in v._dj_model_fields
            assert "account_id" not in v._dj_model_fields

            # Legit field updates; exploit fields are blocked end-to-end.
            v.update_model(field="search", value="hello")
            v.update_model(field="is_admin", value="true")
            v.update_model(field="account_id", value=999)
            assert v.search == "hello"
            assert v.is_admin is False
            assert v.account_id == 1

    def test_dj_model_fields_is_framework_state_not_persisted(self):
        """``_dj_model_fields`` is a framework slot (assigned before the
        _framework_attrs snapshot), so it must NOT be captured as user-private
        state — it is derived from the template each render, never persisted."""
        with _SingleTemplateHarness("allowlist_view.html", _TEMPLATE):
            cls = _make_live_view_class()
            v = cls()
            v.mount(None)
            v._snapshot_user_private_attrs()
            assert "_dj_model_fields" not in v._user_private_keys
            assert "_dj_model_fields" not in v._get_private_state()


@pytest.mark.django_db
class TestPoisoningVectorsClosedEndToEnd:
    """The three rendered-output poisoning vectors are the acceptance criteria
    for finding #3. Each drives attacker data through ``render_with_diff`` (the
    real render path) and asserts the poisoned field is NOT in the allowlist and
    ``update_model`` blocks it. These pass ONLY because the allowlist is derived
    from the TEMPLATE SOURCE; a rendered-HTML-derived allowlist is poisoned by
    (b) and (c) (see the gate-off probe in the PR description)."""

    def _run(self, template_src, comment_value):
        with _SingleTemplateHarness("poison_view.html", template_src):
            cls = _make_live_view_class("poison_view.html")
            v = cls()
            v.mount(None)
            # Attacker-controlled context value flowing through {{ comment }} /
            # {{ comment|safe }} at render time.
            v.comment = comment_value
            html, _patches, _version = v.render_with_diff(None)
            assert "is_admin" not in v._dj_model_fields, (
                "is_admin entered the allowlist via rendered-output poisoning — CWE-915 re-opened"
            )
            v.update_model(field="is_admin", value="true")
            assert v.is_admin is False, "CWE-915 re-opened: is_admin set via poisoned binding"
            # The legit static binding is unaffected.
            assert "search" in v._dj_model_fields
            return html

    def test_text_node_poison_blocked(self):
        # (a) Attacker text in a text node looks like dj-model="is_admin".
        self._run(
            '<div dj-root><input dj-model="search"><p>{{ comment }}</p></div>',
            'nice post! dj-model="is_admin"',
        )

    def test_unquoted_interpolated_attr_poison_blocked(self):
        # (b) Unquoted-interpolated attribute: <div data-x={{ comment }}> with
        # comment='x dj-model=is_admin' — the prior rendered-HTML parser is
        # poisoned by this; the template-source parser is immune.
        self._run(
            '<div dj-root><input dj-model="search"><div data-x={{ comment }}>x</div></div>',
            "x dj-model=is_admin",
        )

    def test_safe_injected_element_poison_blocked(self):
        # (c) |safe attacker content containing a real <input dj-model=is_admin>
        # element — the prior rendered-HTML parser honors it; template-source
        # collection never sees it (it's a Node::Variable, not Node::Text).
        self._run(
            '<div dj-root><input dj-model="search"><div>{{ comment|safe }}</div></div>',
            "<input dj-model=is_admin>",
        )
