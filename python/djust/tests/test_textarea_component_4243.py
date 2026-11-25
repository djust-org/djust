"""Functional rendering tests for ``TextArea`` (ported from the removed
``examples/test_textarea_component.py``, #4243)."""

from djust.components.ui import TextArea


def _html(**kwargs) -> str:
    return str(TextArea(**kwargs).render())


def test_basic_render():
    html = _html(name="description", label="Description", placeholder="Enter description...", rows=5)
    for needle in ("form-control", "description", "Description", "Enter description...", 'rows="5"'):
        assert needle in html, html


def test_initial_value():
    html = _html(name="bio", label="Biography", value="Initial bio text here", rows=4)
    assert "Initial bio text here" in html and "Biography" in html


def test_required_shows_indicator():
    html = _html(name="comment", label="Comment", required=True, rows=3)
    assert "required" in html and "text-danger" in html and "*" in html


def test_invalid_state():
    html = _html(
        name="d",
        label="D",
        value="Short",
        validation_state="invalid",
        validation_message="Description must be at least 10 characters",
    )
    assert "is-invalid" in html and "invalid-feedback" in html
    assert "Description must be at least 10 characters" in html


def test_valid_state():
    html = _html(name="d", label="D", value="ok", validation_state="valid", validation_message="Looks good!")
    assert "is-valid" in html and "valid-feedback" in html and "Looks good!" in html


def test_disabled_and_readonly():
    assert "disabled" in _html(name="t", label="T", value="x", disabled=True)
    assert "readonly" in _html(name="t", label="T", value="x", readonly=True)


def test_help_text():
    html = _html(name="notes", label="Notes", help_text="Maximum 500 characters")
    assert "form-text" in html and "Maximum 500 characters" in html


def test_markup_is_escaped():
    html = _html(name="x", label="<script>alert('xss')</script>", value="<img src=x onerror='alert(1)'>")
    assert "&lt;script&gt;" in html and "&lt;img" in html
    assert "<script>" not in html
