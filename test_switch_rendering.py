#!/usr/bin/env python
"""
Test script to compare Switch component rendering.
"""

from python.djust.components.ui import Switch

# Test Python-only rendering (with Rust disabled in switch_simple.py)
switch = Switch(
    name="notifications",
    label="Enable notifications",
    checked=True
)

print("=" * 80)
print("CHECKED=True (initial state):")
print("=" * 80)
print(switch.render())
print()

# Now test with checked=False (toggle state)
switch.update(checked=False)

print("=" * 80)
print("CHECKED=False (after toggle):")
print("=" * 80)
print(switch.render())
print()

# Count the structure
html_checked = switch.render()
print("=" * 80)
print("HTML Structure Analysis:")
print("=" * 80)
print(f"Total length: {len(html_checked)} chars")
print(f"Lines: {len(html_checked.splitlines())} lines")

# Print with indentation visible
print("\nWith visible structure:")
for i, line in enumerate(html_checked.splitlines(), 1):
    print(f"{i:2}: {repr(line)}")
