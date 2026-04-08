"""
Regression tests for BUG-03: Django BoundField / Form rendering in templates.

When a Django Form is passed in template context, `{{ form.field_name }}`
should render the widget HTML (e.g. `<input type="text" ...>`), not an empty
string, `[Object]`, or a Python repr.

Root cause: the Rust renderer extracted Form.__dict__ into a Value::Object.
Accessing `form.field_name` via dot-notation returned None (the attribute is
not in __dict__), so the variable resolved to Value::Null → empty string.

Fix: normalize_django_value() now pre-renders BoundField → mark_safe(str(bf))
and BaseForm → {field_name: mark_safe(str(form[field_name]))}.  The SafeString
type survives the normalization pass and is detected by _collect_safe_keys(),
which marks the fields as safe in the Rust context so auto-escaping is skipped.
"""

import django
from django.conf import settings

if not settings.configured:
    settings.configure(
        DATABASES={
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": ":memory:",
            }
        },
        INSTALLED_APPS=[
            "django.contrib.contenttypes",
            "django.contrib.auth",
        ],
        SECRET_KEY="test-secret-key-for-boundfield-tests",
        USE_TZ=True,
    )
    django.setup()


from django import forms


# ---------------------------------------------------------------------------
# Test forms
# ---------------------------------------------------------------------------


class SimpleForm(forms.Form):
    first_name = forms.CharField(max_length=100, label="First Name")
    email = forms.EmailField(label="Email")
    age = forms.IntegerField(required=False, label="Age")


class ChoiceForm(forms.Form):
    STATUS_CHOICES = [("open", "Open"), ("closed", "Closed")]
    status = forms.ChoiceField(choices=STATUS_CHOICES)


# ---------------------------------------------------------------------------
# normalize_django_value: BoundField and BaseForm
# ---------------------------------------------------------------------------


class TestNormalizeDjangoValueForms:
    """normalize_django_value converts Form/BoundField to SafeString dicts."""

    def test_boundfield_returns_safestring(self):
        """BoundField → mark_safe(str(bf)) so widget HTML is preserved."""
        from django.utils.safestring import SafeString

        from djust.serialization import normalize_django_value

        form = SimpleForm()
        bf = form["first_name"]  # BoundField

        result = normalize_django_value(bf)

        assert isinstance(result, SafeString), "BoundField must serialize to SafeString"
        assert "<input" in result, f"Expected <input> widget HTML, got: {result!r}"
        assert 'name="first_name"' in result

    def test_baseform_returns_dict_of_safestrings(self):
        """BaseForm → {field_name: SafeString} for each field."""
        from django.utils.safestring import SafeString

        from djust.serialization import normalize_django_value

        form = SimpleForm()
        result = normalize_django_value(form)

        assert isinstance(result, dict), "Form must serialize to dict"
        assert set(result.keys()) == {"first_name", "email", "age"}
        for name, value in result.items():
            assert isinstance(value, SafeString), f"form[{name!r}] must be SafeString"
            assert (
                "<input" in value or "<select" in value or "<textarea" in value
            ), f"Expected widget HTML for {name!r}, got: {value!r}"

    def test_form_field_html_contains_name_attribute(self):
        """Each rendered field must include the correct name= attribute."""
        from djust.serialization import normalize_django_value

        form = SimpleForm()
        result = normalize_django_value(form)

        assert 'name="first_name"' in result["first_name"]
        assert 'name="email"' in result["email"]

    def test_form_with_initial_data_renders_value(self):
        """BoundField with initial data should include the value in widget HTML."""
        from djust.serialization import normalize_django_value

        form = SimpleForm(initial={"first_name": "Rosa"})
        result = normalize_django_value(form)

        assert "Rosa" in result["first_name"], "Initial value should appear in rendered widget HTML"

    def test_bound_form_with_submitted_data(self):
        """Bound form (with POST data) renders submitted values."""
        from djust.serialization import normalize_django_value

        form = SimpleForm(data={"first_name": "Rosa", "email": "rosa@test.com", "age": ""})
        result = normalize_django_value(form)

        assert "Rosa" in result["first_name"]
        assert "rosa@test.com" in result["email"]

    def test_choice_field_renders_select(self):
        """ChoiceField widget should render as <select> HTML."""
        from djust.serialization import normalize_django_value

        form = ChoiceForm()
        result = normalize_django_value(form)

        assert (
            "<select" in result["status"]
        ), f"ChoiceField must render as <select>, got: {result['status']!r}"
        assert "Open" in result["status"]
        assert "Closed" in result["status"]

    def test_not_empty_string_regression(self):
        """Regression: form fields must NOT serialize to empty string."""
        from djust.serialization import normalize_django_value

        form = SimpleForm()
        result = normalize_django_value(form)

        for name, value in result.items():
            assert (
                value.strip() != ""
            ), f"form[{name!r}] rendered to empty string — BUG-03 regression"

    def test_not_object_literal_regression(self):
        """Regression: form fields must NOT serialize to '[Object]'."""
        from djust.serialization import normalize_django_value

        form = SimpleForm()
        result = normalize_django_value(form)

        for name, value in result.items():
            assert (
                "[Object]" not in value
            ), f"form[{name!r}] rendered to '[Object]' — BUG-03 regression"


# ---------------------------------------------------------------------------
# _collect_safe_keys: SafeStrings from form normalization are detected
# ---------------------------------------------------------------------------


class TestCollectSafeKeysFromForms:
    """After normalization, _collect_safe_keys detects form field SafeStrings."""

    def test_form_fields_produce_safe_keys(self):
        """Normalized form fields produce safe_keys like 'form.first_name'."""
        from djust.mixins.rust_bridge import _collect_safe_keys
        from djust.serialization import normalize_django_value

        form = SimpleForm()
        normalized = normalize_django_value(form)  # → {name: SafeString}

        safe_keys = _collect_safe_keys(normalized, "form")

        assert "form.first_name" in safe_keys, f"Safe keys: {safe_keys}"
        assert "form.email" in safe_keys
        assert "form.age" in safe_keys

    def test_no_safe_keys_without_normalization(self):
        """Raw Form (not yet normalized) does not produce safe_keys (no SafeStrings yet)."""
        from djust.mixins.rust_bridge import _collect_safe_keys

        form = SimpleForm()
        # Raw form — _collect_safe_keys won't find any SafeStrings
        safe_keys = _collect_safe_keys(form, "form")
        assert safe_keys == [], "Raw Form has no SafeStrings yet — normalization must happen first"


# ---------------------------------------------------------------------------
# Full template rendering integration
# ---------------------------------------------------------------------------


class TestFormRenderingInTemplate:
    """End-to-end: {{ form.field_name }} renders widget HTML in djust templates."""

    def test_form_field_renders_in_template(self):
        """{{ form.first_name }} should produce <input> HTML, not empty or [Object]."""
        from djust.template_backend import DjustTemplateBackend

        backend = DjustTemplateBackend(
            params={
                "NAME": "djust",
                "DIRS": [],
                "APP_DIRS": False,
                "OPTIONS": {},
            }
        )

        template = backend.from_string("{{ form.first_name }}")
        form = SimpleForm()
        html = template.render(context={"form": form}, request=None)

        assert html.strip() != "", "form.first_name rendered to empty string"
        assert "[Object]" not in html, "form.first_name rendered to [Object]"
        assert "<input" in html, f"Expected <input> widget, got: {html!r}"
        assert 'name="first_name"' in html

    def test_multiple_form_fields_render(self):
        """Multiple form fields in a template all render correctly."""
        from djust.template_backend import DjustTemplateBackend

        backend = DjustTemplateBackend(
            params={
                "NAME": "djust",
                "DIRS": [],
                "APP_DIRS": False,
                "OPTIONS": {},
            }
        )

        template = backend.from_string(
            "<div>{{ form.first_name }}</div>" "<div>{{ form.email }}</div>"
        )
        form = SimpleForm()
        html = template.render(context={"form": form}, request=None)

        assert 'name="first_name"' in html
        assert 'name="email"' in html
        assert "[Object]" not in html

    def test_form_html_not_escaped(self):
        """Widget HTML must not be escaped — < and > must appear literally."""
        from djust.template_backend import DjustTemplateBackend

        backend = DjustTemplateBackend(
            params={
                "NAME": "djust",
                "DIRS": [],
                "APP_DIRS": False,
                "OPTIONS": {},
            }
        )

        template = backend.from_string("{{ form.first_name }}")
        form = SimpleForm()
        html = template.render(context={"form": form}, request=None)

        assert "&lt;input" not in html, "Widget HTML was incorrectly escaped"
        assert "<input" in html
