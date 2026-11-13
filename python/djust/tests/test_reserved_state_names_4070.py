"""One reserved-name list for declared state (exposure-flip plan row 10, #4070).

``_exposure.reserved_state_names()`` is the single list the exposure contract
refuses, the rendered-state names leave out and the (off) class-creation
refusal would use. It replaced the explicit path's reads of
``live_view._FRAMEWORK_INTERNAL_ATTRS``; the change-detection skip list moved
to ``change_detection.CHANGE_DETECTION_IGNORED`` with the same membership.
"""

from __future__ import annotations

import pytest

from djust import LiveView
from djust._exposure import (
    RESERVED_STATE_NAMES,
    ExposureConfigurationError,
    ExposureContract,
    ExposureError,
    reserved_state_names,
)
from djust.decorators import state


def test_every_public_framework_name_is_still_reserved():
    """G1: the reduced set drops only underscore names, which no state field can take."""
    from djust._exposure import _NAME
    from djust.live_view import _FRAMEWORK_INTERNAL_ATTRS

    public = {n for n in _FRAMEWORK_INTERNAL_ATTRS if _NAME.fullmatch(n)}
    assert public <= RESERVED_STATE_NAMES, sorted(public - RESERVED_STATE_NAMES)
    dropped = set(_FRAMEWORK_INTERNAL_ATTRS) - RESERVED_STATE_NAMES
    assert dropped and not any(_NAME.fullmatch(n) for n in dropped)


def test_the_list_holds_the_class_namespace_and_identity():
    names = reserved_state_names()
    for name in ("request", "args", "kwargs", "view", "view_is_async", "mount", "template_name"):
        assert name in names, name
    assert "__proto__" in names


@pytest.mark.parametrize(
    "name", sorted(RESERVED_STATE_NAMES | {"view", "mount", "get_context_data"})
)
def test_the_contract_refuses_each_reserved_name(name):
    from djust._exposure import FieldExposure

    with pytest.raises(ExposureError, match="reserved"):
        ExposureContract("tests.Owner", {name: FieldExposure()})


def test_an_ordinary_name_is_accepted():
    from djust._exposure import FieldExposure

    contract = ExposureContract("tests.Owner", {"count": FieldExposure()})
    assert "count" in contract.fields


def testrendered_state_names_leave_reserved_names_out():
    from djust.mixins.context import rendered_state_names

    class Page(LiveView):
        exposure_policy = "explicit"
        template = "<div dj-root>{{ count }}</div>"
        count = state(0)
        sync_safe = state(True)  # reserved: never rendered from the declaration

    names = rendered_state_names(Page)
    assert "count" in names
    assert "sync_safe" not in names


def test_the_class_creation_refusal_is_off_by_default():
    """#4070: the owner has not decided; a class naming a reserved field imports."""
    import sys

    live_view = sys.modules["djust.live_view"]

    assert live_view._REFUSE_RESERVED_STATE_NAMES_AT_CLASS_CREATION is False

    class Imports(LiveView):
        exposure_policy = "explicit"
        template = "<div dj-root></div>"
        temporary_assigns = state(None)

    assert Imports.__name__ == "Imports"


def test_the_class_creation_refusal_when_switched_on(monkeypatch):
    import sys

    live_view = sys.modules["djust.live_view"]

    monkeypatch.setattr(live_view, "_REFUSE_RESERVED_STATE_NAMES_AT_CLASS_CREATION", True)
    with pytest.raises(ExposureConfigurationError, match="temporary_assigns"):

        class Refused(LiveView):
            exposure_policy = "explicit"
            template = "<div dj-root></div>"
            temporary_assigns = state(None)

    class Fine(LiveView):
        exposure_policy = "explicit"
        template = "<div dj-root></div>"
        count = state(0)

    assert Fine.__name__ == "Fine"


def test_change_detection_skips_the_same_names_as_before():
    from djust.change_detection import CHANGE_DETECTION_IGNORED
    from djust.live_view import _FRAMEWORK_INTERNAL_ATTRS

    assert _FRAMEWORK_INTERNAL_ATTRS is CHANGE_DETECTION_IGNORED
    for name in ("_prev_context_fingerprints", "_changed_keys", "request", "template_name"):
        assert name in CHANGE_DETECTION_IGNORED


def test_the_explicit_path_reads_the_new_names():
    """Structural: the explicit consumers no longer import the legacy name."""
    import pathlib

    import djust

    pkg = pathlib.Path(djust.__file__).parent
    for rel in ("_exposure.py", "websocket.py", "mixins/context.py"):
        code = [
            line
            for line in (pkg / rel).read_text().splitlines()
            if not line.lstrip().startswith("#") and "``" not in line  # docstring prose
        ]
        assert not [line for line in code if "_FRAMEWORK_INTERNAL_ATTRS" in line], rel
