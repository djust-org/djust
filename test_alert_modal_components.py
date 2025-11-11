"""
Tests for RustAlert and RustModal component template integration.
"""
import pytest
from djust._rust import render_template


def test_alert_basic():
    """Test basic alert rendering"""
    template = '<RustAlert message="Test alert" />'
    html = render_template(template, {})

    assert 'alert' in html.lower()
    assert 'Test alert' in html


def test_alert_success():
    """Test success alert variant"""
    template = '<RustAlert message="Success!" variant="success" />'
    html = render_template(template, {})

    assert 'alert-success' in html or 'bg-green' in html
    assert 'Success!' in html


def test_alert_danger():
    """Test danger/error alert variant"""
    template = '<RustAlert message="Error occurred" variant="danger" />'
    html = render_template(template, {})

    assert 'alert-danger' in html or 'bg-red' in html
    assert 'Error occurred' in html


def test_alert_dismissible():
    """Test dismissible alert"""
    template = '<RustAlert message="Dismissible alert" dismissible="true" />'
    html = render_template(template, {})

    assert 'dismissible' in html.lower() or 'close' in html.lower()
    assert 'Dismissible alert' in html


def test_alert_with_icon():
    """Test alert with icon"""
    template = '<RustAlert message="Info message" icon="ℹ️" variant="info" />'
    html = render_template(template, {})

    assert 'ℹ️' in html
    assert 'Info message' in html


def test_alert_with_id():
    """Test alert with custom ID"""
    template = '<RustAlert message="Alert with ID" id="custom-alert" />'
    html = render_template(template, {})

    assert 'id="custom-alert"' in html or "id='custom-alert'" in html
    assert 'Alert with ID' in html


def test_alert_with_context_variable():
    """Test alert with context variable for message"""
    template = '<RustAlert message="{{ alert_msg }}" variant="warning" />'
    context = {'alert_msg': 'Warning from context'}
    html = render_template(template, context)

    assert 'Warning from context' in html
    assert 'warning' in html.lower()


def test_modal_basic():
    """Test basic modal rendering"""
    template = '<RustModal id="testModal" body="Modal content" />'
    html = render_template(template, {})

    assert 'modal' in html.lower()
    assert 'testModal' in html
    assert 'Modal content' in html


def test_modal_with_title():
    """Test modal with title"""
    template = '<RustModal id="titleModal" body="Body content" title="Modal Title" />'
    html = render_template(template, {})

    assert 'Modal Title' in html
    assert 'Body content' in html


def test_modal_with_footer():
    """Test modal with footer"""
    template = '''<RustModal
        id="footerModal"
        body="Main content"
        footer="<button>Close</button>"
    />'''
    html = render_template(template, {})

    assert 'Main content' in html
    assert '<button>Close</button>' in html


def test_modal_large_size():
    """Test modal with large size"""
    template = '<RustModal id="largeModal" body="Large modal body" size="large" />'
    html = render_template(template, {})

    assert 'modal-lg' in html or 'max-w-2xl' in html
    assert 'Large modal body' in html


def test_modal_centered():
    """Test centered modal"""
    template = '<RustModal id="centeredModal" body="Centered content" centered="true" />'
    html = render_template(template, {})

    assert 'centered' in html.lower() or 'items-center' in html
    assert 'Centered content' in html


def test_modal_scrollable():
    """Test scrollable modal"""
    template = '<RustModal id="scrollModal" body="Scrollable content" scrollable="true" />'
    html = render_template(template, {})

    assert 'scrollable' in html.lower() or 'overflow-y' in html
    assert 'Scrollable content' in html


def test_modal_complete():
    """Test modal with all options"""
    template = '''<RustModal
        id="completeModal"
        title="Complete Modal"
        body="This is the body content"
        footer="<button>Save</button><button>Cancel</button>"
        size="large"
        centered="true"
    />'''
    html = render_template(template, {})

    assert 'Complete Modal' in html
    assert 'This is the body content' in html
    assert '<button>Save</button>' in html
    assert '<button>Cancel</button>' in html
    assert 'modal-lg' in html or 'max-w-2xl' in html
    assert 'centered' in html.lower() or 'items-center' in html


def test_modal_with_context_variables():
    """Test modal with context variables"""
    template = '''<RustModal
        id="{{ modal_id }}"
        title="{{ modal_title }}"
        body="{{ modal_body }}"
    />'''
    context = {
        'modal_id': 'dynamicModal',
        'modal_title': 'Dynamic Title',
        'modal_body': 'Dynamic body content',
    }
    html = render_template(template, context)

    assert 'dynamicModal' in html
    assert 'Dynamic Title' in html
    assert 'Dynamic body content' in html


def test_alert_and_modal_together():
    """Test using both Alert and Modal in same template"""
    template = '''
    <div>
        <RustAlert message="Form saved successfully!" variant="success" />
        <RustModal
            id="confirmModal"
            title="Confirm Action"
            body="Are you sure you want to proceed?"
            footer="<button>Yes</button><button>No</button>"
        />
    </div>
    '''
    html = render_template(template, {})

    # Check alert is present
    assert 'Form saved successfully!' in html
    assert 'success' in html.lower()

    # Check modal is present
    assert 'confirmModal' in html
    assert 'Confirm Action' in html
    assert 'Are you sure you want to proceed?' in html
    assert '<button>Yes</button>' in html


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
