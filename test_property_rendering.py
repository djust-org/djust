"""Test script to verify property object rendering"""
import json
from djust.live_view import DjangoJSONEncoder

# Simulate a property dict like Django would create
property_dict = {
    'id': 6,
    'name': '123 Oak Street',
    'bedrooms': 3,
    'bathrooms': 2.0,
    'monthly_rent': 3200.0,
}

# Serialize like LiveView does
json_str = json.dumps({'property': property_dict}, cls=DjangoJSONEncoder)
context = json.loads(json_str)

print("Python context:")
print(f"  property type: {type(context['property'])}")
print(f"  property.name: {context['property']['name']}")
print(f"  property.bedrooms: {context['property']['bedrooms']}")

# Test rendering with Rust
from djust._rust import RustLiveView

template = """
<div>
  Name: {{ property.name }}
  Bedrooms: {{ property.bedrooms }}
  Bathrooms: {{ property.bathrooms }}
  Rent: ${{ property.monthly_rent }}
</div>
"""

rust_view = RustLiveView(template)
rust_view.update_state(context)
html = rust_view.render()

print("\nRendered HTML:")
print(html)

# Check if values are in the HTML
if '123 Oak Street' in html:
    print("\n✓ Property name rendered correctly!")
else:
    print("\n✗ Property name NOT in HTML!")

if '3' in html:
    print("✓ Bedrooms rendered correctly!")
else:
    print("✗ Bedrooms NOT in HTML!")
