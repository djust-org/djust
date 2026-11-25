"""The legacy branches for embedded, lazy and sticky children are gone (#4200).

The legacy exposure policy is refused (#4199), so every child takes the
explicit path. These tests pin the removal: no module that held a legacy arm
names the policy predicate, the removed internals do not exist, and a released
view refuses new waiters through child disposal (the job ``_close_waiters`` did
for a legacy view).
"""

import asyncio
import pathlib

import pytest

import djust
from djust import LiveView

PACKAGE = pathlib.Path(djust.__file__).resolve().parent

WALK_CHILD_FILES = (
    "_lazy_containers.py",
    "mixins/sticky.py",
    "_child_lifecycle.py",
    "_child_async.py",
    "_child_rendering.py",
    "templatetags/live_tags.py",
    "mixins/waiters.py",
    "audio.py",
)


@pytest.mark.parametrize("relative", WALK_CHILD_FILES)
def test_walk_child_files_have_no_legacy_branch(relative):
    source = (PACKAGE / relative).read_text()
    assert "uses_legacy_exposure" not in source


def test_removed_internals_are_gone():
    from djust import _child_async, _child_lifecycle, _lazy_containers
    from djust.mixins.waiters import WaiterMixin

    assert not hasattr(_child_async, "dispatch_legacy_child_work")
    assert not hasattr(_child_lifecycle, "release_legacy_child")
    assert not hasattr(_child_lifecycle, "_close_view_waiters")
    assert not hasattr(WaiterMixin, "_close_waiters")
    assert not hasattr(LiveView, "_get_private_state")
    assert not hasattr(LiveView, "_restore_private_state")
    assert not hasattr(_lazy_containers, "save_http_lazy")


def test_rust_lazy_registration_binding_is_gone():
    """Read from the crate source: the installed extension only drops it once rebuilt."""
    lib = PACKAGE.parents[1] / "crates" / "djust_live" / "src" / "lib.rs"
    if not lib.exists():
        pytest.skip("crate source not in this install")
    source = lib.read_text()
    assert "lazy_registration_allowed" not in source
    assert "fn static_lazy_tree" not in source


class _Released(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root><span>x</span></div>"


@pytest.mark.asyncio
async def test_released_explicit_view_refuses_new_waiters():
    from djust._child_lifecycle import release_root_view

    view = _Released()
    release_root_view(view, navigation=True, reason="view_unmount", clear_live_handles=False)

    assert view._djust_child_disposed is True
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(
            view.wait_for_event("x"), timeout=10
        )  # a refusal is immediate; the bound only stops a hang
