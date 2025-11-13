#!/usr/bin/env python
"""
Manual test script to reproduce the optimistic counter bug.

This script simulates the HTTP requests that happen when a user clicks
the increment button multiple times.

Run with: python test_counter_manually.py
"""

import os
import sys
import django

# Setup Django
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'examples/demo_project'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'demo_project.settings')
django.setup()

from django.test import Client
import json


def test_repeated_increments():
    """
    Test clicking increment button 5 times.

    This should work, but currently fails on 2nd+ clicks.
    """
    client = Client()

    print("=" * 80)
    print("OPTIMISTIC COUNTER TEST")
    print("=" * 80)

    # Step 1: Initial page load
    print("\n[STEP 1] Initial GET request")
    response = client.get('/demos/optimistic-counter/')
    print(f"  Status: {response.status_code}")
    assert response.status_code == 200
    assert b'5' in response.content, "Initial count should be 5"
    print("  ✅ Initial page loaded, count = 5")

    # Step 2: Click increment 5 times
    for i in range(1, 6):
        print(f"\n[STEP {i+1}] POST increment (click #{i})")

        response = client.post(
            '/demos/optimistic-counter/',
            data=json.dumps({"event": "increment", "params": {}}),
            content_type='application/json'
        )

        print(f"  Status: {response.status_code}")

        if response.status_code != 200:
            print(f"  ❌ FAILED: HTTP {response.status_code}")
            print(f"  Response: {response.content}")
            return False

        data = response.json()
        print(f"  Version: {data.get('version')}")
        print(f"  Has 'patches' field: {'patches' in data}")
        print(f"  Has 'html' field: {'html' in data}")

        if 'patches' in data:
            patches = data['patches']
            print(f"  Patches count: {len(patches)}")
            if len(patches) == 0:
                print(f"  ⚠️  WARNING: Empty patches array")
            else:
                print(f"  ✅ Patches: {len(patches)} patch(es)")

        if 'html' in data:
            html = data['html']
            print(f"  HTML length: {len(html)} chars")
            if '<!DOCTYPE html>' in html:
                print(f"  ✅ HTML: Valid full document")
            else:
                print(f"  ⚠️  HTML: Fragment (no DOCTYPE)")

        # CRITICAL CHECK: Must have patches OR html
        if 'patches' not in data and 'html' not in data:
            print(f"  ❌ FAILED: Response has neither 'patches' nor 'html' field")
            print(f"  Response data: {json.dumps(data, indent=2)}")
            return False

        # If patches present, must not be empty
        if 'patches' in data and len(data['patches']) == 0 and 'html' not in data:
            print(f"  ❌ FAILED: Empty patches array without html fallback")
            print(f"  This causes client to have no actionable data!")
            return False

        print(f"  ✅ Click #{i} successful")

    print("\n" + "=" * 80)
    print("TEST PASSED: All 5 increments worked correctly!")
    print("=" * 80)
    return True


if __name__ == '__main__':
    success = test_repeated_increments()
    sys.exit(0 if success else 1)
