#!/usr/bin/env python
"""Test script for Rust Button component."""

from djust.rust_components import Button

# Test basic button creation
print("Testing basic button creation...")
btn1 = Button("btn1", "Click Me")
html1 = btn1.render()
print(f"Basic button: {html1}\n")

# Test with different variants
print("Testing variants...")
for variant in ["primary", "secondary", "success", "danger", "warning", "info"]:
    btn = Button(f"btn-{variant}", variant.capitalize(), variant=variant)
    print(f"{variant}: {btn.render()}")
print()

# Test with different sizes
print("Testing sizes...")
for size in ["sm", "md", "lg"]:
    btn = Button(f"btn-{size}", f"Size {size}", size=size)
    print(f"{size}: {btn.render()}")
print()

# Test builder pattern
print("Testing builder pattern...")
btn_builder = Button("btn-builder", "Builder Pattern") \
    .with_variant("success") \
    .with_size("lg") \
    .with_icon("✓")
print(f"Builder: {btn_builder.render()}\n")

# Test with kwargs
print("Testing kwargs...")
btn_kwargs = Button(
    "btn-kwargs",
    "Submit Form",
    variant="success",
    size="large",
    outline=False,
    disabled=False,
    full_width=True,
    on_click="handleSubmit"
)
print(f"Kwargs: {btn_kwargs.render()}\n")

# Test different frameworks
print("Testing different frameworks...")
btn_framework = Button("btn-fw", "Framework Test", variant="primary")
print(f"Bootstrap5: {btn_framework.render_with_framework('bootstrap5')}")
print(f"Tailwind: {btn_framework.render_with_framework('tailwind')}")
print(f"Plain: {btn_framework.render_with_framework('plain')}")
print()

# Test disabled state
print("Testing disabled state...")
btn_disabled = Button("btn-disabled", "Disabled", disabled=True)
print(f"Disabled: {btn_disabled.render()}")
btn_disabled.disabled = False
print(f"Enabled: {btn_disabled.render()}\n")

# Test label updates
print("Testing label updates...")
btn_label = Button("btn-label", "Original")
print(f"Original: {btn_label.render()}")
btn_label.label = "Updated"
print(f"Updated: {btn_label.render()}\n")

print("All tests passed! ✓")
