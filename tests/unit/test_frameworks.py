"""
Unit tests for CSS framework adapters.

Tests adapter rendering, CSS class assignment, adapter registry,
and correct dj-change event attribute usage.
"""

import pytest
from django import forms
from djust.frameworks import (
    BaseAdapter,
    Bootstrap5Adapter,
    TailwindAdapter,
    PlainAdapter,
    get_adapter,
    register_adapter,
)


# --- Test forms ---


class SimpleForm(forms.Form):
    name = forms.CharField(max_length=100, required=True, help_text="Enter your name")
    email = forms.EmailField(required=True)
    bio = forms.CharField(widget=forms.Textarea, required=False)
    role = forms.ChoiceField(choices=[("dev", "Developer"), ("mgr", "Manager")])
    agree = forms.BooleanField(required=False, label="I agree")
    priority = forms.ChoiceField(
        choices=[("low", "Low"), ("high", "High")],
        widget=forms.RadioSelect,
        required=False,
    )


# --- Adapter output tests ---


class TestBootstrap5Adapter:
    """Test Bootstrap 5 adapter rendering."""

    def setup_method(self):
        self.adapter = Bootstrap5Adapter()
        self.form = SimpleForm()

    def test_text_field_has_dj_change(self):
        html = self.adapter.render_field(self.form.fields["name"], "name", "", [])
        assert 'dj-change="validate_field"' in html
        assert "@change" not in html

    def test_text_field_has_input_type(self):
        html = self.adapter.render_field(self.form.fields["name"], "name", "", [])
        assert 'type="text"' in html

    def test_email_field_type(self):
        html = self.adapter.render_field(self.form.fields["email"], "email", "", [])
        assert 'type="email"' in html

    def test_textarea_rendering(self):
        html = self.adapter.render_field(self.form.fields["bio"], "bio", "hello", [])
        assert "<textarea" in html
        assert "hello" in html

    def test_select_rendering(self):
        html = self.adapter.render_field(self.form.fields["role"], "role", "dev", [])
        assert "<select" in html
        assert "Developer" in html
        assert "selected" in html

    def test_checkbox_rendering(self):
        html = self.adapter.render_field(self.form.fields["agree"], "agree", False, [])
        assert 'type="checkbox"' in html

    def test_radio_rendering(self):
        html = self.adapter.render_field(self.form.fields["priority"], "priority", "low", [])
        assert 'type="radio"' in html
        assert "Low" in html
        assert "High" in html

    def test_errors_rendered(self):
        html = self.adapter.render_field(
            self.form.fields["name"], "name", "", ["This field is required."]
        )
        assert "This field is required." in html

    def test_help_text_rendered(self):
        html = self.adapter.render_field(self.form.fields["name"], "name", "", [])
        assert "Enter your name" in html
        assert "form-text" in html

    def test_required_marker(self):
        html = self.adapter.render_field(self.form.fields["name"], "name", "", [])
        assert "text-danger" in html

    def test_label_rendered(self):
        html = self.adapter.render_field(self.form.fields["name"], "name", "", [])
        assert "<label" in html

    def test_label_hidden_when_disabled(self):
        html = self.adapter.render_field(
            self.form.fields["name"], "name", "", [], render_label=False
        )
        assert "<label" not in html


class TestTailwindAdapter:
    """Test Tailwind adapter rendering."""

    def setup_method(self):
        self.adapter = TailwindAdapter()
        self.form = SimpleForm()

    def test_dj_change_present(self):
        html = self.adapter.render_field(self.form.fields["name"], "name", "", [])
        assert 'dj-change="validate_field"' in html
        assert "@change" not in html

    def test_required_marker(self):
        html = self.adapter.render_field(self.form.fields["name"], "name", "", [])
        assert "text-red-600" in html

    def test_help_text_uses_p_tag(self):
        html = self.adapter.render_field(self.form.fields["name"], "name", "", [])
        assert "<p" in html
        assert "text-sm" in html

    def test_errors_not_wrapped_in_div(self):
        html = self.adapter.render_errors(["Error one"])
        assert "<p" in html
        # Tailwind uses <p> tags, not wrapping <div>
        assert html.startswith("<p")


class TestPlainAdapter:
    """Test Plain HTML adapter rendering."""

    def setup_method(self):
        self.adapter = PlainAdapter()
        self.form = SimpleForm()

    def test_dj_change_present(self):
        html = self.adapter.render_field(self.form.fields["name"], "name", "", [])
        assert 'dj-change="validate_field"' in html
        assert "@change" not in html

    def test_required_marker_plain_text(self):
        html = self.adapter.render_field(self.form.fields["name"], "name", "", [])
        assert " *" in html
        # Plain adapter uses " *" not a <span>
        assert "text-danger" not in html

    def test_help_text_uses_small_tag(self):
        html = self.adapter.render_field(self.form.fields["name"], "name", "", [])
        assert "<small>" in html

    def test_error_class(self):
        html = self.adapter.render_errors(["Something went wrong"])
        assert "error-message" in html

    def test_field_class_with_errors(self):
        cls = self.adapter.get_field_class(self.form.fields["name"], has_errors=True)
        assert cls == "error"

    def test_field_class_without_errors(self):
        cls = self.adapter.get_field_class(self.form.fields["name"], has_errors=False)
        assert cls == ""


# --- Registry tests ---


class TestAdapterRegistry:
    """Test get_adapter() and register_adapter()."""

    def test_get_bootstrap_adapter(self):
        adapter = get_adapter("bootstrap5")
        assert isinstance(adapter, Bootstrap5Adapter)

    def test_get_tailwind_adapter(self):
        adapter = get_adapter("tailwind")
        assert isinstance(adapter, TailwindAdapter)

    def test_get_plain_adapter(self):
        adapter = get_adapter("plain")
        assert isinstance(adapter, PlainAdapter)

    def test_unknown_framework_falls_back_to_plain(self):
        adapter = get_adapter("unknown_framework")
        assert isinstance(adapter, PlainAdapter)

    def test_register_custom_adapter(self):
        class CustomAdapter(BaseAdapter):
            required_marker = " (required)"

        register_adapter("custom", CustomAdapter())
        adapter = get_adapter("custom")
        assert isinstance(adapter, CustomAdapter)

        # Clean up
        from djust.frameworks import _adapters

        del _adapters["custom"]


# --- dj-change verification across all adapters ---


class TestDjChangeAttribute:
    """Verify dj-change is used (not @change) across ALL adapters and field types."""

    @pytest.fixture(params=["bootstrap5", "tailwind", "plain"])
    def adapter(self, request):
        return get_adapter(request.param)

    def test_text_field_uses_dj_change(self, adapter):
        field = forms.CharField()
        html = adapter.render_field(field, "test", "", [])
        assert "dj-change" in html
        assert "@change" not in html

    def test_email_field_uses_dj_change(self, adapter):
        field = forms.EmailField()
        html = adapter.render_field(field, "test", "", [])
        assert "dj-change" in html

    def test_textarea_uses_dj_change(self, adapter):
        field = forms.CharField(widget=forms.Textarea)
        html = adapter.render_field(field, "test", "", [])
        assert "dj-change" in html

    def test_select_uses_dj_change(self, adapter):
        field = forms.ChoiceField(choices=[("a", "A"), ("b", "B")])
        html = adapter.render_field(field, "test", "", [])
        assert "dj-change" in html

    def test_checkbox_uses_dj_change(self, adapter):
        field = forms.BooleanField(required=False)
        html = adapter.render_field(field, "test", False, [])
        assert "dj-change" in html

    def test_radio_uses_dj_change(self, adapter):
        field = forms.ChoiceField(choices=[("a", "A"), ("b", "B")], widget=forms.RadioSelect)
        html = adapter.render_field(field, "test", "", [])
        assert "dj-change" in html

    def test_auto_validate_false_omits_dj_change(self, adapter):
        field = forms.CharField()
        html = adapter.render_field(field, "test", "", [], auto_validate=False)
        assert "dj-change" not in html


# --- #2145: no data-field_name, and the `name` that replaced it ---


class TestNoDataFieldNameAttribute:
    """The adapters must not emit ``data-field_name`` (#2145).

    Until #2145 all three widget renderers set ``data-field_name="<name>"``
    next to ``dj-change="validate_field"``, commented *"so event handler knows
    which field changed"*. Nothing read it, and the misleading appearance that
    it did is what produced #2137. These tests are the mechanical guard: they
    go red the moment any renderer re-adds it.

    Three renderer sites emit the binding, so all three are covered
    individually (#1104) — ``_render_input`` (text/email/textarea/select),
    ``_render_checkbox``, and ``_render_radio``.
    """

    @pytest.fixture(params=["bootstrap5", "tailwind", "plain"])
    def adapter(self, request):
        return get_adapter(request.param)

    # --- site 1: _render_input (text, email, textarea, select branches) ---

    def test_text_input_has_no_data_field_name(self, adapter):
        field = forms.CharField()
        html = adapter.render_field(field, "email", "", [])
        assert "data-field_name" not in html

    def test_email_input_has_no_data_field_name(self, adapter):
        field = forms.EmailField()
        html = adapter.render_field(field, "email", "", [])
        assert "data-field_name" not in html

    def test_textarea_has_no_data_field_name(self, adapter):
        field = forms.CharField(widget=forms.Textarea)
        html = adapter.render_field(field, "bio", "", [])
        assert "data-field_name" not in html

    def test_select_has_no_data_field_name(self, adapter):
        field = forms.ChoiceField(choices=[("a", "A"), ("b", "B")])
        html = adapter.render_field(field, "role", "", [])
        assert "data-field_name" not in html

    # --- site 2: _render_checkbox ---

    def test_checkbox_has_no_data_field_name(self, adapter):
        field = forms.BooleanField(required=False)
        html = adapter.render_field(field, "agree", False, [])
        assert "data-field_name" not in html

    # --- site 3: _render_radio ---

    def test_radio_has_no_data_field_name(self, adapter):
        field = forms.ChoiceField(choices=[("a", "A"), ("b", "B")], widget=forms.RadioSelect)
        html = adapter.render_field(field, "priority", "", [])
        assert "data-field_name" not in html

    def test_radio_had_it_outside_the_auto_validate_guard(self, adapter):
        """The radio site emitted it even with ``auto_validate=False``.

        ``frameworks.py``'s radio branch had the assignment one indent level
        OUT of the ``if auto_validate:`` block that the other two sites kept
        it inside, so a radio rendered with ``auto_validate=False`` carried
        ``data-field_name`` on an element with **no djust directive at all** —
        the sharpest demonstration that nothing consumed it.
        """
        field = forms.ChoiceField(choices=[("a", "A"), ("b", "B")], widget=forms.RadioSelect)
        html = adapter.render_field(field, "priority", "", [], auto_validate=False)
        assert "dj-change" not in html
        assert "data-field_name" not in html

    # --- the attribute that actually carries the name ---

    @pytest.mark.parametrize(
        "field,field_name",
        [
            (forms.CharField(), "name"),
            (forms.EmailField(), "email"),
            (forms.CharField(widget=forms.Textarea), "bio"),
            (forms.ChoiceField(choices=[("a", "A")]), "role"),
            (forms.BooleanField(required=False), "agree"),
            (forms.ChoiceField(choices=[("a", "A")], widget=forms.RadioSelect), "priority"),
        ],
        ids=["text", "email", "textarea", "select", "checkbox", "radio"],
    )
    def test_every_widget_carries_name(self, adapter, field, field_name):
        """``name`` is why deleting ``data-field_name`` was safe.

        The client's ``getFieldName`` (``09-event-binding.js:504``) resolves
        the field name as ``data-field`` → ``name`` → ``id``. The adapters
        never emitted ``data-field``, so the live path already ran on the
        ``name`` branch; the reconnect path (``_processFormRecovery``,
        ``:1773``) reads ``field.name || field.id`` and never looked at
        ``data-*`` either. If a renderer ever stops setting ``name``, the
        field name stops reaching the handler — hence this test.
        """
        html = adapter.render_field(field, field_name, "", [])
        assert f'name="{field_name}"' in html

    def test_no_directive_that_would_collect_data_attributes(self, adapter):
        """Pins the reachability chain that made removal safe.

        ``data-*`` is collected by exactly two client functions:
        ``extractTypedParams`` (``08-event-parsing.js:248``), which runs only
        for ``dj-click`` / ``dj-poll`` / ``dj-mounted`` / ``dj-click-away`` /
        ``dj-shortcut`` / ``dj-window-*`` / ``dj-document-*``; and
        ``_processAutoRecover`` (``09-event-binding.js:1684``), which reads the
        ``dj-auto-recover`` container's own ``data-*``. The adapters emit none
        of those, which is why ``data-field_name`` could never be read.

        If a renderer ever grows one of these directives, the analysis above
        stops holding and this test goes red so it gets redone. Note the
        caller-controlled escape hatch it does NOT cover: ``dom_event=`` and
        ``widget.attrs`` can put an arbitrary directive on the element.
        """
        collecting_directives = [
            "dj-click",
            "dj-poll",
            "dj-mounted",
            "dj-click-away",
            "dj-shortcut",
            "dj-window-",
            "dj-document-",
            "dj-auto-recover",
        ]
        fields = [
            (forms.CharField(), "name"),
            (forms.CharField(widget=forms.Textarea), "bio"),
            (forms.ChoiceField(choices=[("a", "A")]), "role"),
            (forms.BooleanField(required=False), "agree"),
            (forms.ChoiceField(choices=[("a", "A")], widget=forms.RadioSelect), "priority"),
        ]
        for field, field_name in fields:
            html = adapter.render_field(field, field_name, "", [])
            for directive in collecting_directives:
                assert directive not in html, (
                    f"{field_name} now renders {directive!r} — an element carrying it "
                    "DOES have its data-* collected, so the #2145 reachability "
                    "analysis must be redone before trusting it."
                )


# --- XSS escaping in adapters ---


class TestAdapterXSSEscaping:
    """Test that adapters escape user-supplied values."""

    @pytest.fixture(params=["bootstrap5", "tailwind", "plain"])
    def adapter(self, request):
        return get_adapter(request.param)

    def test_value_escaped_in_input(self, adapter):
        field = forms.CharField()
        html = adapter.render_field(field, "test", "<script>alert(1)</script>", [])
        assert "<script>" not in html

    def test_value_escaped_in_textarea(self, adapter):
        field = forms.CharField(widget=forms.Textarea)
        html = adapter.render_field(field, "test", "<img src=x onerror=alert(1)>", [])
        # The < > are escaped so the tag won't render as HTML
        assert "&lt;img" in html
        assert "<img src=" not in html

    def test_errors_escaped(self, adapter):
        field = forms.CharField()
        html = adapter.render_field(field, "test", "", ["<b>bold</b>"])
        assert "<b>" not in html
        assert "&lt;b&gt;" in html

    def test_label_escaped(self, adapter):
        field = forms.CharField(label="<script>bad</script>")
        html = adapter.render_field(field, "test", "", [])
        assert "<script>bad</script>" not in html

    def test_help_text_escaped(self, adapter):
        field = forms.CharField(help_text="<img src=x onerror=alert(1)>")
        html = adapter.render_field(field, "test", "", [])
        # The < > are escaped so the tag won't render as HTML
        assert "&lt;img" in html
        assert "<img src=" not in html

    def test_select_choices_escaped(self, adapter):
        field = forms.ChoiceField(choices=[("<script>", "<b>Bad</b>")])
        html = adapter.render_field(field, "test", "", [])
        assert "<script>" not in html
        assert "<b>" not in html
