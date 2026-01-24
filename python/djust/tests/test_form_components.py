"""
Tests for ForeignKeySelect and ManyToManySelect components.

These components provide reactive select widgets for Django model relationships
with support for autocomplete and large querysets.
"""

# Configure Django settings BEFORE any djust imports
import django
from django.conf import settings

if not settings.configured:
    settings.configure(
        DEBUG=True,
        SECRET_KEY="test-secret-key",
        INSTALLED_APPS=[
            "django.contrib.contenttypes",
            "django.contrib.auth",
        ],
        DATABASES={
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": ":memory:",
            }
        },
    )
    django.setup()

from unittest.mock import MagicMock, patch

from djust.components.forms import ForeignKeySelect, ManyToManySelect


class TestForeignKeySelect:
    """Tests for ForeignKeySelect component."""

    def test_mount_sets_defaults(self):
        """Component should have sensible defaults after mount."""
        component = ForeignKeySelect()
        component.mount(name="author")

        assert component.name == "author"
        assert component.value is None
        assert component.required is False
        assert component.disabled is False
        assert component.searchable is False
        assert component.empty_label == "---------"
        assert component.max_options == 100

    def test_mount_with_options(self):
        """Component should accept all configuration options."""
        on_change = MagicMock()
        component = ForeignKeySelect()
        component.mount(
            name="author",
            label="Author",
            value=5,
            required=True,
            disabled=True,
            searchable=True,
            search_fields=["name", "email"],
            min_search_length=3,
            max_options=50,
            on_change=on_change,
        )

        assert component.name == "author"
        assert component.label == "Author"
        assert component.value == 5
        assert component.required is True
        assert component.disabled is True
        assert component.searchable is True
        assert component.search_fields == ["name", "email"]
        assert component.min_search_length == 3
        assert component.max_options == 50
        assert component.on_change is on_change

    def test_get_options_empty_without_queryset(self):
        """Should return empty list when no queryset provided."""
        component = ForeignKeySelect()
        component.mount(name="test")

        options = component.get_options()
        assert options == []

    def test_get_options_from_queryset(self):
        """Should build options from queryset."""
        # Mock a queryset with model objects
        mock_obj1 = MagicMock()
        mock_obj1.pk = 1
        mock_obj1.name = "Alice"
        mock_obj1.__str__ = lambda self: "Alice"

        mock_obj2 = MagicMock()
        mock_obj2.pk = 2
        mock_obj2.name = "Bob"
        mock_obj2.__str__ = lambda self: "Bob"

        mock_qs = MagicMock()
        mock_qs.__getitem__ = lambda self, key: [mock_obj1, mock_obj2]

        component = ForeignKeySelect()
        component.mount(
            name="author",
            queryset=mock_qs,
            label_field="name",
            value_field="pk",
        )

        options = component.get_options()

        assert len(options) == 2
        assert options[0] == {"value": 1, "label": "Alice"}
        assert options[1] == {"value": 2, "label": "Bob"}

    def test_select_sets_value(self):
        """Selecting should set the value."""
        component = ForeignKeySelect()
        component.mount(name="author")

        component.select(5)

        assert component.value == 5

    def test_select_converts_string_to_int(self):
        """Select should convert string IDs to integers."""
        component = ForeignKeySelect()
        component.mount(name="author")

        component.select("42")

        assert component.value == 42

    def test_select_calls_on_change(self):
        """Select should trigger on_change callback."""
        on_change = MagicMock()
        component = ForeignKeySelect()
        component.mount(name="author", on_change=on_change)

        component.select(10)

        on_change.assert_called_once_with(10)

    def test_clear_resets_value(self):
        """Clear should reset value and search query."""
        on_change = MagicMock()
        component = ForeignKeySelect()
        component.mount(name="author", value=5, on_change=on_change)
        component.search_query = "test"

        component.clear()

        assert component.value is None
        assert component.search_query == ""
        on_change.assert_called_once_with(None)

    def test_search_sets_query(self):
        """Search should update the search query."""
        component = ForeignKeySelect()
        component.mount(name="author", searchable=True)

        component.search("alice")

        assert component.search_query == "alice"

    def test_render_bootstrap(self):
        """Should render Bootstrap 5 HTML."""
        with patch("djust.config.config") as mock_config:
            mock_config.get.return_value = "bootstrap5"

            component = ForeignKeySelect()
            component.mount(
                name="author",
                label="Author",
                help_text="Select an author",
            )

            html = component.render()

            assert 'class="form-select"' in html
            assert 'class="form-label"' in html
            assert "Author" in html
            assert "Select an author" in html
            assert 'name="author"' in html

    def test_render_tailwind(self):
        """Should render Tailwind CSS HTML."""
        with patch("djust.config.config") as mock_config:
            mock_config.get.return_value = "tailwind"

            component = ForeignKeySelect()
            component.mount(name="author", label="Author")

            html = component.render()

            assert "rounded-md" in html
            assert "border-gray-300" in html
            assert "Author" in html

    def test_render_with_validation_state(self):
        """Should render validation state classes."""
        with patch("djust.config.config") as mock_config:
            mock_config.get.return_value = "bootstrap5"

            component = ForeignKeySelect()
            component.mount(
                name="author",
                validation_state="invalid",
                validation_message="This field is required",
            )

            html = component.render()

            assert "is-invalid" in html
            assert "invalid-feedback" in html
            assert "This field is required" in html

    def test_render_searchable(self):
        """Should render search input when searchable."""
        with patch("djust.config.config") as mock_config:
            mock_config.get.return_value = "bootstrap5"

            component = ForeignKeySelect()
            component.mount(name="author", searchable=True)

            html = component.render()

            assert 'placeholder="Search..."' in html
            assert 'dj-input="search(value)"' in html


class TestManyToManySelect:
    """Tests for ManyToManySelect component."""

    def test_mount_sets_defaults(self):
        """Component should have sensible defaults after mount."""
        component = ManyToManySelect()
        component.mount(name="tags")

        assert component.name == "tags"
        assert component.values == []
        assert component.required is False
        assert component.disabled is False
        assert component.render_as == "select"

    def test_mount_with_initial_values(self):
        """Component should accept initial selected values."""
        component = ManyToManySelect()
        component.mount(name="tags", values=[1, 2, 3])

        assert component.values == [1, 2, 3]

    def test_toggle_adds_value(self):
        """Toggle should add value if not selected."""
        component = ManyToManySelect()
        component.mount(name="tags", values=[1])

        component.toggle(2)

        assert 2 in component.values
        assert 1 in component.values

    def test_toggle_removes_value(self):
        """Toggle should remove value if already selected."""
        component = ManyToManySelect()
        component.mount(name="tags", values=[1, 2, 3])

        component.toggle(2)

        assert 2 not in component.values
        assert 1 in component.values
        assert 3 in component.values

    def test_toggle_calls_on_change(self):
        """Toggle should trigger on_change callback."""
        on_change = MagicMock()
        component = ManyToManySelect()
        component.mount(name="tags", on_change=on_change)

        component.toggle(5)

        on_change.assert_called_once_with([5])

    def test_clear_removes_all(self):
        """Clear should remove all selections."""
        on_change = MagicMock()
        component = ManyToManySelect()
        component.mount(name="tags", values=[1, 2, 3], on_change=on_change)

        component.clear()

        assert component.values == []
        on_change.assert_called_once_with([])

    def test_select_all(self):
        """Select all should select all available options."""
        mock_obj1 = MagicMock()
        mock_obj1.pk = 1
        mock_obj1.name = "Tag1"

        mock_obj2 = MagicMock()
        mock_obj2.pk = 2
        mock_obj2.name = "Tag2"

        mock_qs = MagicMock()
        mock_qs.__getitem__ = lambda self, key: [mock_obj1, mock_obj2]

        on_change = MagicMock()
        component = ManyToManySelect()
        component.mount(
            name="tags",
            queryset=mock_qs,
            label_field="name",
            on_change=on_change,
        )

        component.select_all()

        assert 1 in component.values
        assert 2 in component.values
        on_change.assert_called_once()

    def test_render_as_checkboxes_bootstrap(self):
        """Should render as checkboxes in Bootstrap."""
        with patch("djust.config.config") as mock_config:
            mock_config.get.return_value = "bootstrap5"

            mock_obj = MagicMock()
            mock_obj.pk = 1
            mock_obj.name = "Tag1"
            mock_qs = MagicMock()
            mock_qs.__getitem__ = lambda self, key: [mock_obj]

            component = ManyToManySelect()
            component.mount(
                name="tags",
                queryset=mock_qs,
                label_field="name",
                render_as="checkboxes",
                label="Tags",
            )

            html = component.render()

            assert 'class="form-check"' in html
            assert 'class="form-check-input"' in html
            assert 'type="checkbox"' in html
            assert "Tags" in html
            assert "Tag1" in html

    def test_render_as_select_bootstrap(self):
        """Should render as multi-select in Bootstrap."""
        with patch("djust.config.config") as mock_config:
            mock_config.get.return_value = "bootstrap5"

            mock_obj = MagicMock()
            mock_obj.pk = 1
            mock_obj.name = "Tag1"
            mock_qs = MagicMock()
            mock_qs.__getitem__ = lambda self, key: [mock_obj]

            component = ManyToManySelect()
            component.mount(
                name="tags",
                queryset=mock_qs,
                label_field="name",
                render_as="select",
            )

            html = component.render()

            assert 'class="form-select"' in html
            assert "multiple" in html
            assert "Tag1" in html

    def test_render_checkboxes_tailwind(self):
        """Should render as checkboxes in Tailwind."""
        with patch("djust.config.config") as mock_config:
            mock_config.get.return_value = "tailwind"

            mock_obj = MagicMock()
            mock_obj.pk = 1
            mock_obj.name = "Tag1"
            mock_qs = MagicMock()
            mock_qs.__getitem__ = lambda self, key: [mock_obj]

            component = ManyToManySelect()
            component.mount(
                name="tags",
                queryset=mock_qs,
                label_field="name",
                render_as="checkboxes",
            )

            html = component.render()

            assert "rounded" in html
            assert "border-gray-300" in html
            assert 'type="checkbox"' in html

    def test_get_options_marks_selected(self):
        """Options should have selected flag set correctly."""
        mock_obj1 = MagicMock()
        mock_obj1.pk = 1
        mock_obj1.name = "Selected"

        mock_obj2 = MagicMock()
        mock_obj2.pk = 2
        mock_obj2.name = "Not Selected"

        mock_qs = MagicMock()
        mock_qs.__getitem__ = lambda self, key: [mock_obj1, mock_obj2]

        component = ManyToManySelect()
        component.mount(
            name="tags",
            queryset=mock_qs,
            label_field="name",
            values=[1],
        )

        options = component.get_options()

        assert options[0]["selected"] is True
        assert options[1]["selected"] is False


class TestComponentIntegration:
    """Integration tests for form components."""

    def test_foreignkey_in_components_module(self):
        """ForeignKeySelect should be importable from djust.components."""
        from djust.components import ForeignKeySelect as FK

        assert FK is not None

    def test_manytomany_in_components_module(self):
        """ManyToManySelect should be importable from djust.components."""
        from djust.components import ManyToManySelect as M2M

        assert M2M is not None

    def test_component_ids_unique(self):
        """Each component should get a unique ID."""
        comp1 = ForeignKeySelect()
        comp1.mount(name="author1")

        comp2 = ForeignKeySelect()
        comp2.mount(name="author2")

        assert comp1.component_id != comp2.component_id
