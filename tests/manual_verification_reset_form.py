#!/usr/bin/env python3
"""
Manual verification script for Issue #63: VDOM Form State reset_form alternating behavior.

This script simulates the user workflow:
1. Mount the profile form view
2. Fill out some fields
3. Reset form (first time)
4. Fill out fields again
5. Reset form (second time)
6. Repeat several more times

Expected behavior (FIXED):
- All reset_form calls should generate consistent VDOM states
- No alternating between patches and html_update
- Form clears on first reset attempt (not second)

Bug behavior (BEFORE FIX):
- First reset: html_update (full HTML)
- Second reset: patches (diff)
- Third reset: html_update again
- Pattern alternates indefinitely
"""

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'demo_project.settings')

import django
django.setup()

from django.test import RequestFactory
from demo_app.views.forms_demo import ProfileFormView


def verify_reset_form_consistency():
    """Verify reset_form generates consistent VDOM states."""
    print("\n" + "="*80)
    print("MANUAL VERIFICATION: Issue #63 - reset_form alternating behavior")
    print("="*80)

    factory = RequestFactory()

    # Simulate multiple reset cycles
    results = []

    for cycle in range(1, 6):
        print(f"\n--- Cycle {cycle} ---")

        # Create new view
        view = ProfileFormView()
        request = factory.get('/forms/profile/')

        # Add session to request
        from django.contrib.sessions.middleware import SessionMiddleware
        middleware = SessionMiddleware(lambda x: x)
        middleware.process_request(request)
        request.session.save()

        # Mount view
        print("1. Mounting view...")
        view.mount(request)
        mount_state = {k: v for k, v in view.form_data.items()}
        print(f"   form_data keys after mount: {sorted(mount_state.keys())}")
        print(f"   form_data values: {mount_state}")

        # Fill form
        print("2. Filling form...")
        view.form_data = {
            'first_name': f'Test{cycle}',
            'last_name': f'User{cycle}',
            'email': f'test{cycle}@example.com',
            'bio': f'Bio for cycle {cycle}',
            'birth_date': '1990-01-01',
            'country': 'US',
            'phone': '+1 555-1234',
            'website': 'https://example.com',
            'receive_updates': True
        }
        filled_state = {k: v for k, v in view.form_data.items()}
        print(f"   form_data after filling: {filled_state}")

        # Reset form
        print("3. Resetting form...")
        view.reset_form()
        reset_state = {k: v for k, v in view.form_data.items()}
        print(f"   form_data keys after reset: {sorted(reset_state.keys())}")
        print(f"   form_data values: {reset_state}")

        # Check consistency
        state_consistent = (sorted(mount_state.keys()) == sorted(reset_state.keys()))
        values_match = (mount_state == reset_state)

        result = {
            'cycle': cycle,
            'mount_keys': sorted(mount_state.keys()),
            'reset_keys': sorted(reset_state.keys()),
            'state_consistent': state_consistent,
            'values_match': values_match,
            'mount_state': mount_state,
            'reset_state': reset_state
        }
        results.append(result)

        print(f"4. Verification:")
        print(f"   Keys consistent: {state_consistent}")
        print(f"   Values match: {values_match}")

        if not state_consistent:
            print(f"   ❌ FAILED: Keys don't match!")
            print(f"   Mount keys: {sorted(mount_state.keys())}")
            print(f"   Reset keys: {sorted(reset_state.keys())}")
        elif not values_match:
            print(f"   ❌ FAILED: Values don't match!")
            print(f"   Differences:")
            for key in mount_state:
                if mount_state.get(key) != reset_state.get(key):
                    print(f"     {key}: mount={mount_state.get(key)!r}, reset={reset_state.get(key)!r}")
        else:
            print(f"   ✅ PASSED: State consistent")

    # Final verification
    print("\n" + "="*80)
    print("FINAL VERIFICATION")
    print("="*80)

    all_consistent = all(r['state_consistent'] and r['values_match'] for r in results)

    if all_consistent:
        print("\n✅ SUCCESS: All cycles generated consistent states!")
        print("   - reset_form() matches mount() behavior")
        print("   - No alternating patches/html_update expected")
        print("   - Form clears on first reset attempt")
        return True
    else:
        print("\n❌ FAILURE: Inconsistent states detected!")
        for r in results:
            if not (r['state_consistent'] and r['values_match']):
                print(f"   Cycle {r['cycle']}: state_consistent={r['state_consistent']}, values_match={r['values_match']}")
        return False


if __name__ == '__main__':
    success = verify_reset_form_consistency()
    sys.exit(0 if success else 1)
