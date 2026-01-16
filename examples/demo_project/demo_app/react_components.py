"""
React component definitions for Django Rust Live demo

This module demonstrates how to register and use React components
within Django Rust Live templates.
"""

from djust import register_react_component


@register_react_component("Button", module_path="./components/Button.jsx")
def render_button(props, children):
    """
    Server-side renderer for Button component.
    This provides the initial HTML that will be hydrated by React on the client.
    """
    variant = props.get('variant', 'primary')
    disabled = props.get('disabled', False)
    class_name = f'btn btn-{variant}'

    if disabled:
        class_name += ' disabled'

    return f'<button class="{class_name}" type="button">{children}</button>'


@register_react_component("Card", module_path="./components/Card.jsx")
def render_card(props, children):
    """Server-side renderer for Card component."""
    title = props.get('title', '')
    class_name = props.get('className', '')

    html = f'<div class="card {class_name}">'
    if title:
        html += f'<div class="card-header"><h3>{title}</h3></div>'
    html += f'<div class="card-body">{children}</div>'
    html += '</div>'

    return html


@register_react_component("Counter", module_path="./components/Counter.jsx")
def render_counter(props, children):
    """
    Server-side renderer for interactive Counter component.
    Initial count is passed as a prop.
    """
    initial_count = props.get('initialCount', 0)
    label = props.get('label', 'Count')

    return f'''
    <div class="counter-widget">
        <div class="counter-label">{label}</div>
        <div class="counter-display">{initial_count}</div>
        <div class="counter-controls">
            <button class="btn btn-sm btn-secondary">-</button>
            <button class="btn btn-sm btn-secondary">+</button>
        </div>
    </div>
    '''


@register_react_component("TodoItem", module_path="./components/TodoItem.jsx")
def render_todo_item(props, children):
    """Server-side renderer for Todo item component."""
    text = props.get('text', '')
    completed_val = props.get('completed', False)
    # Handle boolean string values
    if isinstance(completed_val, str):
        completed = completed_val.lower() in ('true', '1', 'yes')
    else:
        completed = bool(completed_val)

    class_name = 'todo-item' + (' completed' if completed else '')
    # Escape text for use in data attribute
    escaped_text = text.replace('"', '&quot;').replace("'", '&#39;')

    return f'''
    <div class="{class_name}" data-todo-text="{escaped_text}">
        <input type="checkbox" class="todo-checkbox" {'checked' if completed else ''} />
        <span class="todo-text">{text}</span>
        <button class="btn btn-sm btn-danger todo-delete" data-todo-text="{escaped_text}">Delete</button>
    </div>
    '''


@register_react_component("Alert", module_path="./components/Alert.jsx")
def render_alert(props, children):
    """Server-side renderer for Alert component."""
    alert_type = props.get('type', 'info')
    dismissible = props.get('dismissible', False)

    html = f'<div class="alert alert-{alert_type}" role="alert">'
    html += children
    if dismissible:
        html += '<button type="button" class="close" aria-label="Close">'
        html += '<span aria-hidden="true">&times;</span></button>'
    html += '</div>'

    return html
