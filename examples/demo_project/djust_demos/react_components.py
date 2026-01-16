"""
React component definitions for djust demos

This module registers React component server-side renderers for the React demo page.
"""

from djust.react import register_react_component


@register_react_component("Button", module_path="./components/Button.jsx")
def render_button(props, children):
    """
    Server-side renderer for Button component.
    This provides the initial HTML that will be hydrated by React on the client.
    """
    variant = props.get('variant', 'primary')
    disabled = props.get('disabled', False)

    # Map variant to modern button classes
    variant_classes = {
        'primary': 'btn-primary-modern',
        'secondary': 'btn-secondary-modern',
        'danger': 'btn-secondary-modern',
    }
    class_name = variant_classes.get(variant, 'btn-primary-modern')

    if variant == 'danger':
        style = 'border-color: var(--color-error); color: var(--color-error);'
    else:
        style = ''

    if disabled:
        class_name += ' disabled'

    return f'<button class="{class_name}" type="button" style="{style}">{children}</button>'


@register_react_component("Card", module_path="./components/Card.jsx")
def render_card(props, children):
    """Server-side renderer for Card component."""
    title = props.get('title', '')
    class_name = props.get('className', '')

    html = f'<div class="demo-card-modern {class_name}" style="margin-top: 0;">'
    if title:
        html += f'<div class="p-3 border-b" style="border-color: var(--color-border);"><h4 style="margin: 0; color: var(--color-text);">{title}</h4></div>'
    html += f'<div class="p-3" style="background: var(--color-bg-elevated); color: var(--color-text);">{children}</div>'
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
    <div class="counter-widget" style="text-align: center;">
        <div style="font-size: 0.875rem; color: var(--color-text-muted); margin-bottom: 0.5rem;">{label}</div>
        <div class="counter-display" style="font-size: 3rem; font-weight: 800; color: var(--color-accent); margin: 1rem 0;">{initial_count}</div>
        <div style="display: flex; gap: 0.5rem; justify-content: center;">
            <button class="btn-secondary-modern" style="width: 3rem; height: 3rem; font-size: 1.5rem;">-</button>
            <button class="btn-secondary-modern" style="width: 3rem; height: 3rem; font-size: 1.5rem;">+</button>
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

    # Escape text for use in data attribute
    escaped_text = text.replace('"', '&quot;').replace("'", '&#39;')

    checked = 'checked' if completed else ''
    text_style = 'text-decoration: line-through; opacity: 0.6;' if completed else ''

    return f'''
    <div class="flex items-center gap-3 p-2" style="background: var(--color-bg); border: 1px solid var(--color-border); border-radius: var(--radius-sm); margin-bottom: 0.5rem;" data-todo-text="{escaped_text}">
        <input type="checkbox" class="todo-checkbox" {checked} style="width: 18px; height: 18px; cursor: pointer;" />
        <span class="todo-text" style="flex: 1; color: var(--color-text); {text_style}">{text}</span>
        <button class="btn-secondary-modern todo-delete" data-todo-text="{escaped_text}" style="padding: 0.25rem 0.75rem; border-color: var(--color-error); color: var(--color-error);">×</button>
    </div>
    '''


@register_react_component("Alert", module_path="./components/Alert.jsx")
def render_alert(props, children):
    """Server-side renderer for Alert component."""
    alert_type = props.get('type', 'info')
    dismissible = props.get('dismissible', False)

    # Map alert types to colors
    type_colors = {
        'success': 'var(--color-success)',
        'warning': 'var(--color-warning)',
        'error': 'var(--color-error)',
        'info': 'var(--color-accent)',
    }
    color = type_colors.get(alert_type, type_colors['info'])

    html = f'<div style="padding: 0.75rem 1rem; background: {color}15; border: 1px solid {color}40; border-radius: var(--radius-md); color: var(--color-text); margin-bottom: 0.75rem; display: flex; align-items: center; justify-content: space-between;" role="alert">'
    html += f'<span>{children}</span>'

    if dismissible:
        html += f'<button type="button" style="background: none; border: none; color: {color}; font-size: 1.25rem; cursor: pointer; padding: 0; margin-left: 1rem;" aria-label="Close">'
        html += '<span aria-hidden="true">&times;</span></button>'

    html += '</div>'

    return html
