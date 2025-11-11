#!/usr/bin/env python3
"""
Demonstration of Switch component.
Shows both checked and unchecked states.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'python'))

# Import base classes directly to avoid Django config
from djust.components.base import Component

# Define Switch inline to avoid import issues
class Switch(Component):
    """Switch component for demonstration."""

    template_string = '''<div class="mb-3">
    <div class="form-check form-switch{% if inline %} form-check-inline{% endif %}">
        <input class="form-check-input"
               type="checkbox"
               role="switch"
               id="{{ switch_id }}"
               name="{{ name }}"
               value="{{ value }}"{% if checked %} checked{% endif %}{% if disabled %} disabled{% endif %}>
        <label class="form-check-label" for="{{ switch_id }}">
            {{ label }}
        </label>
    </div>{% if help_text %}
    <div class="form-text">{{ help_text }}</div>{% endif %}
</div>'''

    def __init__(self, name, label, id=None, checked=False, disabled=False, help_text=None, value="on", inline=False):
        self.name = name
        self.label = label
        self.switch_id = id or name
        self.checked = checked
        self.disabled = disabled
        self.help_text = help_text
        self.value = value
        self.inline = inline
        super().__init__(
            name=name, label=label, id=self.switch_id,
            checked=checked, disabled=disabled, help_text=help_text,
            value=value, inline=inline
        )

    def get_context_data(self):
        return {
            'name': self.name,
            'label': self.label,
            'switch_id': self.switch_id,
            'checked': self.checked,
            'disabled': self.disabled,
            'help_text': self.help_text,
            'value': self.value,
            'inline': self.inline,
        }

# Try Rust implementation
try:
    from djust._rust import RustSwitch
    HAS_RUST = True
except ImportError:
    HAS_RUST = False

print('=' * 60)
print('Switch Component Demonstration')
print('=' * 60)
print()

# Test 1: Basic unchecked
print('1. Basic Unchecked Switch:')
s1 = Switch('notifications', 'Enable notifications')
print(s1.render())

# Test 2: Checked switch
print('\n2. Checked Switch (Dark Mode):')
s2 = Switch('dark_mode', 'Dark Mode', checked=True)
print(s2.render())

# Test 3: Disabled checked
print('\n3. Disabled Checked Switch:')
s3 = Switch('premium', 'Premium Features', checked=True, disabled=True, help_text='Upgrade to enable')
print(s3.render())

# Test 4: Inline switch
print('\n4. Inline Switch:')
s4 = Switch('inline', 'Toggle', inline=True)
print(s4.render())

if HAS_RUST:
    print('\n5. Rust Implementation (checked):')
    s5 = RustSwitch('rust_switch', 'Rust-powered switch', checked=True, help_text='Rendered in ~1μs')
    print(s5.render())
    print('\n✓ Rust implementation available and working!')
else:
    print('\n⚠ Rust implementation not available (using template fallback)')

print('\n' + '=' * 60)
print('All demonstrations completed successfully')
print('=' * 60)
