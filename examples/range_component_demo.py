#!/usr/bin/env python3
"""
Demo showing Range component usage.

This demonstrates various use cases for the Range (Slider) component.
"""

from djust._rust import RustRange

print("=" * 70)
print("Range Component Demo - Various Use Cases")
print("=" * 70)
print()

# Example 1: Basic Volume Slider
print("1. Basic Volume Slider")
print("-" * 70)
volume_slider = RustRange(
    name="volume",
    label="Volume",
    value=75,
    min_value=0,
    max_value=100,
    step=5,
    show_value=True
)
print(volume_slider.render())
print()

# Example 2: Brightness Control
print("2. Brightness Control (0-100)")
print("-" * 70)
brightness = RustRange(
    name="brightness",
    label="Screen Brightness",
    value=80,
    help_text="Adjust screen brightness level"
)
print(brightness.render())
print()

# Example 3: Opacity Slider (Decimal)
print("3. Opacity Slider (0.0-1.0)")
print("-" * 70)
opacity = RustRange(
    name="opacity",
    label="Opacity",
    value=0.7,
    min_value=0.0,
    max_value=1.0,
    step=0.1,
    show_value=True,
    help_text="Set element transparency"
)
print(opacity.render())
print()

# Example 4: Temperature Control (Negative values)
print("4. Temperature Control (-10 to 40°C)")
print("-" * 70)
temperature = RustRange(
    name="temperature",
    label="Room Temperature",
    value=22.5,
    min_value=-10,
    max_value=40,
    step=0.5,
    show_value=True,
    help_text="Target temperature in Celsius"
)
print(temperature.render())
print()

# Example 5: Zoom Level
print("5. Zoom Level (10% - 200%)")
print("-" * 70)
zoom = RustRange(
    name="zoom",
    label="Zoom Level",
    value=100,
    min_value=10,
    max_value=200,
    step=10,
    show_value=True,
    help_text="Adjust zoom percentage"
)
print(zoom.render())
print()

# Example 6: Disabled Slider
print("6. Disabled Slider (Locked Setting)")
print("-" * 70)
locked = RustRange(
    name="locked_setting",
    label="System Volume (Locked)",
    value=50,
    disabled=True,
    help_text="This setting is locked by administrator"
)
print(locked.render())
print()

# Example 7: Price Range Filter
print("7. Price Range Filter")
print("-" * 70)
price = RustRange(
    name="max_price",
    label="Maximum Price",
    value=500,
    min_value=0,
    max_value=1000,
    step=50,
    show_value=True,
    help_text="Filter products by maximum price"
)
print(price.render())
print()

# Example 8: Quality Slider
print("8. Quality Slider")
print("-" * 70)
quality = RustRange(
    name="quality",
    label="Image Quality",
    value=80,
    min_value=1,
    max_value=100,
    step=1,
    show_value=True,
    help_text="Higher quality = larger file size"
)
print(quality.render())
print()

print("=" * 70)
print("Usage in LiveView:")
print("=" * 70)
print("""
from djust import LiveView
from djust.components.ui import Range

class VolumeControlView(LiveView):
    template = '''
    <div class="container">
        <h2>Audio Settings</h2>
        {{ volume_slider.render|safe }}
        <p>Current volume: {{ volume }}%</p>
    </div>
    '''

    def mount(self, request):
        self.volume = 75
        self.volume_slider = Range(
            name="volume",
            label="Volume",
            value=self.volume,
            min_value=0,
            max_value=100,
            step=5,
            show_value=True
        )

    def update_volume(self, value: str):
        '''Event handler for volume changes'''
        self.volume = int(value)
        self.volume_slider = Range(
            name="volume",
            label="Volume",
            value=self.volume,
            min_value=0,
            max_value=100,
            step=5,
            show_value=True
        )

# In template, use @input or @change event:
# <input type="range" @change="update_volume" name="volume" ... >
""")
print()
