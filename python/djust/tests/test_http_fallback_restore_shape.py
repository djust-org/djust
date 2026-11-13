"""The saved-context restore never writes over a method or property (#1646).

The saved public state used to be written back with ``setattr`` for every
key: a context key named like a method or property of the view replaced the
method on the instance (``self.images_go_out`` a bool instead of a method),
and the next render that called it failed. ``restore_public_state`` never
writes over a callable or property the class defines, and says so.

2.0 removed the HTTP page-POST fallback's session restore (and with it the
private-attribute rebuild this module also covered); the helper still serves
the legacy lazy child (``_lazy_containers.py``).
"""


def test_restore_helper_skips_methods_and_properties_keeps_data():
    from djust._state_restore import restore_public_state

    class Plain:
        label = "default"

        def go(self):
            return True

        @property
        def shown(self):
            return "computed"

    target = Plain()
    applied = restore_public_state(
        target, {"go": False, "shown": "saved", "label": "saved", "extra": 3, "_x": 1}
    )
    assert applied == {"label": "saved", "extra": 3}
    assert target.go() is True
    assert target.shown == "computed"
    assert "_x" not in target.__dict__


def test_every_context_restore_site_goes_through_the_helper():
    """The rule holds on each path that writes saved context back (#1646)."""
    from pathlib import Path

    import djust

    root = Path(djust.__file__).parent
    sites = {
        "mixins/request.py": 0,  # the page-POST fallback's session restore was removed in 2.0
        "runtime.py": 0,  # the socket session restore was removed in 2.0
        "mixins/sticky.py": 0,  # the sticky child restore was removed in 2.0
        "_lazy_containers.py": 1,  # lazily mounted child
        "live_view.py": 0,  # the legacy signed snapshot restore was removed in 2.0
    }
    found = {name: (root / name).read_text().count("restore_public_state(") for name in sites}
    assert found == sites
