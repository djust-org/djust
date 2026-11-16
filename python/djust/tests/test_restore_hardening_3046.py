"""#3046: private-state restore hardening.

``_restore_private_state`` used a raw ``setattr`` for every ``_``-prefixed
key, unscreened against ``DANGEROUS_ATTRIBUTES`` (unlike the public path).

The component half (``restore_components_snapshot`` skipping a key that names
a component method) went with time-travel debugging, its only caller (#4184).
"""

from djust import LiveView


class HardenPage(LiveView):
    exposure_policy = "legacy"
    template = "<div dj-root></div>"


def test_private_restore_skips_dangerous_and_dunder_keys():
    view = HardenPage()
    original_class = view.__class__
    view._restore_private_state(
        {
            "__class__": dict,
            "__dict__": {},
            "__custom__": 1,
            "_note": "kept",
        }
    )
    assert view.__class__ is original_class
    assert "__custom__" not in view.__dict__
    assert view._note == "kept"
    assert "_note" in view._user_private_keys
