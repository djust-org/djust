#!/usr/bin/env python
"""
Demo of Rust Components in djust
==================================

This example demonstrates the high-performance Rust-backed UI components.

Run this file from the examples directory:
    python rust_components_demo.py
"""

from djust.rust_components import Button

def demo_basic():
    """Basic button creation"""
    print("=" * 60)
    print("BASIC BUTTON CREATION")
    print("=" * 60)

    btn = Button("demo-btn", "Click Me!")
    print(f"\nPython code:")
    print('  btn = Button("demo-btn", "Click Me!")')
    print(f"\nHTML output:")
    print(f"  {btn.render()}")


def demo_variants():
    """Different button variants"""
    print("\n" + "=" * 60)
    print("BUTTON VARIANTS")
    print("=" * 60)

    variants = ["primary", "secondary", "success", "danger", "warning", "info"]

    for variant in variants:
        btn = Button(f"btn-{variant}", variant.capitalize(), variant=variant)
        print(f"\n{variant.upper()}:")
        print(f"  {btn.render()}")


def demo_sizes():
    """Different button sizes"""
    print("\n" + "=" * 60)
    print("BUTTON SIZES")
    print("=" * 60)

    sizes = [("sm", "Small"), ("md", "Medium"), ("lg", "Large")]

    for size, label in sizes:
        btn = Button(f"btn-{size}", label, size=size)
        print(f"\n{label.upper()}:")
        print(f"  {btn.render()}")


def demo_builder_pattern():
    """Builder pattern for complex buttons"""
    print("\n" + "=" * 60)
    print("BUILDER PATTERN")
    print("=" * 60)

    btn = Button("builder-btn", "Save Changes") \
        .with_variant("success") \
        .with_size("lg") \
        .with_icon("💾")

    print(f"\nPython code:")
    print('  btn = Button("builder-btn", "Save Changes") \\')
    print('      .with_variant("success") \\')
    print('      .with_size("lg") \\')
    print('      .with_icon("💾")')

    print(f"\nHTML output:")
    print(f"  {btn.render()}")


def demo_frameworks():
    """Rendering with different CSS frameworks"""
    print("\n" + "=" * 60)
    print("FRAMEWORK-SPECIFIC RENDERING")
    print("=" * 60)

    btn = Button("fw-btn", "Framework Test", variant="primary", size="lg")

    print("\nBOOTSTRAP 5:")
    print(f"  {btn.render_with_framework('bootstrap5')}")

    print("\nTAILWIND CSS:")
    tailwind = btn.render_with_framework('tailwind')
    # Truncate for display
    if len(tailwind) > 80:
        print(f"  {tailwind[:80]}...")
    else:
        print(f"  {tailwind}")

    print("\nPLAIN HTML:")
    print(f"  {btn.render_with_framework('plain')}")


def demo_interactive():
    """Interactive button with event handler"""
    print("\n" + "=" * 60)
    print("INTERACTIVE BUTTON (with LiveView)")
    print("=" * 60)

    btn = Button(
        "interactive-btn",
        "Submit Form",
        variant="success",
        size="lg",
        full_width=True,
        on_click="handleSubmit"
    )

    print(f"\nPython code:")
    print('''  btn = Button(
      "interactive-btn",
      "Submit Form",
      variant="success",
      size="lg",
      full_width=True,
      on_click="handleSubmit"
  )''')

    print(f"\nHTML output:")
    print(f"  {btn.render()}")

    print("\nNote: The @click='handleSubmit' attribute integrates with")
    print("      djust's LiveView event system for reactive updates.")


def demo_state_management():
    """Dynamic button state changes"""
    print("\n" + "=" * 60)
    print("DYNAMIC STATE MANAGEMENT")
    print("=" * 60)

    btn = Button("state-btn", "Click to Disable", variant="primary")

    print("\nInitial state:")
    print(f"  {btn.render()}")

    # Simulate state change
    btn.disabled = True
    btn.label = "Button Disabled"

    print("\nAfter state change (disabled=True):")
    print(f"  {btn.render()}")

    # Re-enable
    btn.disabled = False
    btn.label = "Button Enabled"

    print("\nAfter re-enabling:")
    print(f"  {btn.render()}")


def demo_performance():
    """Performance demonstration"""
    print("\n" + "=" * 60)
    print("PERFORMANCE DEMONSTRATION")
    print("=" * 60)

    import time

    # Create many buttons
    count = 1000
    start = time.perf_counter()

    buttons = [
        Button(f"btn-{i}", f"Button {i}", variant="primary")
        for i in range(count)
    ]

    creation_time = time.perf_counter() - start

    # Render all buttons
    start = time.perf_counter()
    html_output = [btn.render() for btn in buttons]
    render_time = time.perf_counter() - start

    print(f"\nCreated {count} buttons in {creation_time*1000:.2f}ms")
    print(f"Rendered {count} buttons in {render_time*1000:.2f}ms")
    print(f"Average: {render_time/count*1000000:.2f}μs per button")

    print(f"\nTotal HTML size: {sum(len(html) for html in html_output):,} bytes")


def main():
    """Run all demos"""
    print("\n" + "🦀" * 30)
    print("RUST COMPONENTS DEMO".center(60))
    print("High-performance UI components for djust".center(60))
    print("🦀" * 30)

    demo_basic()
    demo_variants()
    demo_sizes()
    demo_builder_pattern()
    demo_frameworks()
    demo_interactive()
    demo_state_management()
    demo_performance()

    print("\n" + "=" * 60)
    print("Demo complete! 🎉")
    print("=" * 60)
    print("\nFor more information, see: docs/RUST_COMPONENTS.md")
    print()


if __name__ == "__main__":
    main()
