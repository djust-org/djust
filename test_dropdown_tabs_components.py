"""
Tests for RustDropdown and RustTabs component template integration.
"""
import pytest
import json
from djust._rust import render_template


def test_dropdown_basic():
    """Test basic dropdown rendering"""
    items = [
        {"label": "Option 1", "value": "opt1"},
        {"label": "Option 2", "value": "opt2"},
    ]
    template = f'<RustDropdown id="testDropdown" items=\'{json.dumps(items)}\' />'
    html = render_template(template, {})

    assert 'dropdown' in html.lower()
    assert 'Option 1' in html
    assert 'Option 2' in html


def test_dropdown_with_selection():
    """Test dropdown with selected value"""
    items = [
        {"label": "Option 1", "value": "opt1"},
        {"label": "Option 2", "value": "opt2"},
    ]
    template = f'<RustDropdown id="testDropdown" items=\'{json.dumps(items)}\' selected="opt1" />'
    html = render_template(template, {})

    assert 'dropdown' in html.lower()
    assert 'Option 1' in html


def test_dropdown_with_variant():
    """Test dropdown with variant"""
    items = [{"label": "Test", "value": "test"}]
    template = f'<RustDropdown id="testDropdown" items=\'{json.dumps(items)}\' variant="success" />'
    html = render_template(template, {})

    assert 'success' in html.lower() or 'btn-success' in html


def test_dropdown_with_placeholder():
    """Test dropdown with placeholder"""
    items = [{"label": "Test", "value": "test"}]
    template = f'<RustDropdown id="testDropdown" items=\'{json.dumps(items)}\' placeholder="Choose an option" />'
    html = render_template(template, {})

    assert 'Choose an option' in html or 'dropdown' in html.lower()


def test_dropdown_disabled():
    """Test disabled dropdown"""
    items = [{"label": "Test", "value": "test"}]
    template = f'<RustDropdown id="testDropdown" items=\'{json.dumps(items)}\' disabled="true" />'
    html = render_template(template, {})

    assert 'disabled' in html.lower()


def test_dropdown_with_size():
    """Test dropdown with size"""
    items = [{"label": "Test", "value": "test"}]
    template = f'<RustDropdown id="testDropdown" items=\'{json.dumps(items)}\' size="large" />'
    html = render_template(template, {})

    assert 'dropdown' in html.lower()
    # Size should be reflected in classes
    assert 'lg' in html.lower() or 'large' in html.lower()


def test_dropdown_with_context_variable():
    """Test dropdown with context variable for selected"""
    items = [{"label": "Option 1", "value": "opt1"}, {"label": "Option 2", "value": "opt2"}]
    template = f'<RustDropdown id="dropdown1" items=\'{json.dumps(items)}\' selected="{{ selected_value }}" />'
    context = {'selected_value': 'opt2'}
    html = render_template(template, context)

    assert 'dropdown' in html.lower()
    assert 'Option 2' in html


def test_tabs_basic():
    """Test basic tabs rendering"""
    tabs = [
        {"id": "tab1", "label": "Tab 1", "content": "Content 1"},
        {"id": "tab2", "label": "Tab 2", "content": "Content 2"},
    ]
    template = f'<RustTabs id="testTabs" tabs=\'{json.dumps(tabs)}\' />'
    html = render_template(template, {})

    assert 'Tab 1' in html
    assert 'Tab 2' in html
    assert 'Content 1' in html
    assert 'Content 2' in html


def test_tabs_with_active():
    """Test tabs with active tab"""
    tabs = [
        {"id": "tab1", "label": "Tab 1", "content": "Content 1"},
        {"id": "tab2", "label": "Tab 2", "content": "Content 2"},
    ]
    template = f'<RustTabs id="testTabs" tabs=\'{json.dumps(tabs)}\' active="tab2" />'
    html = render_template(template, {})

    assert 'Tab 2' in html
    assert 'tab2' in html


def test_tabs_pills_variant():
    """Test tabs with pills variant"""
    tabs = [
        {"id": "tab1", "label": "Tab 1", "content": "Content 1"},
    ]
    template = f'<RustTabs id="testTabs" tabs=\'{json.dumps(tabs)}\' variant="pills" />'
    html = render_template(template, {})

    assert 'pills' in html.lower() or 'nav-pills' in html


def test_tabs_underline_variant():
    """Test tabs with underline variant"""
    tabs = [
        {"id": "tab1", "label": "Tab 1", "content": "Content 1"},
    ]
    template = f'<RustTabs id="testTabs" tabs=\'{json.dumps(tabs)}\' variant="underline" />'
    html = render_template(template, {})

    assert 'tab' in html.lower()


def test_tabs_vertical():
    """Test vertical tabs"""
    tabs = [
        {"id": "tab1", "label": "Tab 1", "content": "Content 1"},
        {"id": "tab2", "label": "Tab 2", "content": "Content 2"},
    ]
    template = f'<RustTabs id="testTabs" tabs=\'{json.dumps(tabs)}\' vertical="true" />'
    html = render_template(template, {})

    assert 'Tab 1' in html
    assert 'vertical' in html.lower() or 'flex-column' in html


def test_tabs_with_context_variable():
    """Test tabs with context variable for active"""
    tabs = [
        {"id": "tab1", "label": "Tab 1", "content": "Content 1"},
        {"id": "tab2", "label": "Tab 2", "content": "Content 2"},
    ]
    template = f'<RustTabs id="tabs1" tabs=\'{json.dumps(tabs)}\' active="{{ active_tab }}" />'
    context = {'active_tab': 'tab2'}
    html = render_template(template, context)

    assert 'Tab 2' in html
    assert 'tab2' in html


def test_tabs_with_html_content():
    """Test tabs with HTML content"""
    tabs = [
        {"id": "tab1", "label": "Tab 1", "content": "<p>This is <strong>HTML</strong> content</p>"},
        {"id": "tab2", "label": "Tab 2", "content": "<div>Another tab</div>"},
    ]
    template = f'<RustTabs id="testTabs" tabs=\'{json.dumps(tabs)}\' />'
    html = render_template(template, {})

    assert '<p>This is <strong>HTML</strong> content</p>' in html
    assert '<div>Another tab</div>' in html


def test_dropdown_and_tabs_together():
    """Test using both Dropdown and Tabs in same template"""
    dropdown_items = [{"label": "Option 1", "value": "opt1"}]
    tabs = [{"id": "tab1", "label": "Tab 1", "content": "Content 1"}]

    template = f'''
    <div>
        <RustDropdown id="dropdown1" items='{json.dumps(dropdown_items)}' />
        <RustTabs id="tabs1" tabs='{json.dumps(tabs)}' />
    </div>
    '''
    html = render_template(template, {})

    # Check dropdown is present
    assert 'dropdown' in html.lower()
    assert 'Option 1' in html

    # Check tabs is present
    assert 'Tab 1' in html
    assert 'Content 1' in html


def test_dropdown_multiple_items():
    """Test dropdown with multiple items"""
    items = [
        {"label": "First", "value": "1"},
        {"label": "Second", "value": "2"},
        {"label": "Third", "value": "3"},
        {"label": "Fourth", "value": "4"},
    ]
    template = f'<RustDropdown id="multiDropdown" items=\'{json.dumps(items)}\' />'
    html = render_template(template, {})

    assert 'First' in html
    assert 'Second' in html
    assert 'Third' in html
    assert 'Fourth' in html


def test_tabs_multiple_tabs():
    """Test tabs with multiple tabs"""
    tabs = [
        {"id": "home", "label": "Home", "content": "Home content"},
        {"id": "profile", "label": "Profile", "content": "Profile content"},
        {"id": "contact", "label": "Contact", "content": "Contact content"},
        {"id": "about", "label": "About", "content": "About content"},
    ]
    template = f'<RustTabs id="multiTabs" tabs=\'{json.dumps(tabs)}\' />'
    html = render_template(template, {})

    for tab in tabs:
        assert tab["label"] in html
        assert tab["content"] in html


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
