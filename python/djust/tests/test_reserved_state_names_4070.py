"""One reserved-name list for declared state (exposure-flip plan row 10, #4070).

``_exposure.reserved_state_names()`` is the single list the exposure contract
refuses, the rendered-state names leave out and the class-creation refusal
uses (owner decision 2026-11-12). It replaced the explicit path's reads of
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

    # Reserved: never rendered from a declaration. A class statement refuses it
    # (#4070); assigned afterwards it still reaches the class namespace.
    Page.sync_safe = state(True)
    names = rendered_state_names(Page)
    assert "count" in names
    assert "sync_safe" not in names


# --------------------------------------------------------------------------- #
# The class-creation refusal (owner decision 2026-11-12): every way a field
# can be declared.
# --------------------------------------------------------------------------- #


def test_a_reserved_field_in_the_class_body_is_refused_naming_it_with_a_rename():
    with pytest.raises(ExposureConfigurationError) as excinfo:

        class Refused(LiveView):
            exposure_policy = "explicit"
            template = "<div dj-root></div>"
            temporary_assigns = state(None)

    message = str(excinfo.value)
    assert "Refused" in message and "'temporary_assigns'" in message
    assert "Rename" in message and "temporary_assigns_value" in message


@pytest.mark.parametrize("policy", ["explicit", "legacy"])
@pytest.mark.parametrize("name", ["template_name", "request", "page_title", "mount", "view"])
def test_each_policy_and_kind_of_reserved_name_is_refused(policy, name):
    with pytest.raises(ExposureConfigurationError, match=repr(name)):
        type(
            "Refused",
            (LiveView,),
            {"exposure_policy": policy, "template": "<div dj-root></div>", name: state("x")},
        )


def test_an_ordinary_field_is_accepted():
    class Fine(LiveView):
        exposure_policy = "explicit"
        template = "<div dj-root></div>"
        count = state(0)

    assert Fine.__name__ == "Fine"


def test_a_reserved_field_declared_by_a_plain_mixin_is_refused_on_the_view():
    class TemplateMixin:  # not a LiveView: nothing of ours runs at its creation
        template_name = state("from-a-mixin.html")

    with pytest.raises(ExposureConfigurationError, match="'template_name'"):

        class Composed(TemplateMixin, LiveView):
            exposure_policy = "explicit"
            template = "<div dj-root></div>"


def test_a_subclass_adding_a_reserved_field_to_a_base_view_is_refused():
    class Base(LiveView):
        exposure_policy = "explicit"
        template = "<div dj-root></div>"
        count = state(0)

    with pytest.raises(ExposureConfigurationError, match="'kwargs'"):

        class Child(Base):
            kwargs = state(None)


def test_a_subclass_shadowing_a_mixins_reserved_field_with_a_setting_is_accepted():
    """The field a view would declare is the one attribute lookup finds: a
    plain setting in the subclass replaces the mixin's state() field, as the
    exposure contract (most-derived first) sees it."""

    class TemplateMixin:
        template_name = state("from-a-mixin.html")

    class Composed(TemplateMixin, LiveView):
        exposure_policy = "explicit"
        template_name = "real.html"

    assert Composed.template_name == "real.html"


def test_a_mixin_that_skips_super_init_subclass_does_not_bypass_the_refusal():
    """``LiveView.__init_subclass__`` never runs when a mixin before it in the
    MRO does not call ``super().__init_subclass__()``; the field's own
    ``__set_name__`` refuses it instead."""

    class Swallows:
        def __init_subclass__(cls, **kwargs):  # no super() call
            pass

    with pytest.raises(ExposureConfigurationError, match="'static_assigns'"):

        class Composed(Swallows, LiveView):
            exposure_policy = "explicit"
            template = "<div dj-root></div>"
            static_assigns = state(None)


def _mount(view_class):
    from djust.testing import LiveViewTestClient

    return LiveViewTestClient(view_class).mount()


@pytest.mark.django_db
def test_a_super_skipping_mixin_that_declares_the_field_is_refused_at_mount():
    """Nothing of ours runs at class creation here (the mixin is not a
    LiveView and skips super), so the mount path refuses it, as it refuses a
    removed 2.0 attribute (``_params_form._refuse_removed_configuration``)."""

    class SwallowsAndDeclares:
        sticky = state(False)

        def __init_subclass__(cls, **kwargs):  # no super() call
            pass

    class Composed(SwallowsAndDeclares, LiveView):
        exposure_policy = "explicit"
        template = "<div dj-root></div>"

    with pytest.raises(ExposureConfigurationError, match="'sticky'"):
        _mount(Composed)


@pytest.mark.django_db
def test_a_state_assigned_to_the_class_after_creation_is_refused_at_mount():
    """``state()`` does not support assignment after the class statement (its
    ``__set_name__`` never runs, so an instance cannot read it); the mount path
    still refuses the reserved name."""

    class Late(LiveView):
        exposure_policy = "explicit"
        template = "<div dj-root></div>"

    Late.template_name = state("late.html")
    with pytest.raises(AssertionError):
        Late().template_name  # not a working field
    with pytest.raises(ExposureConfigurationError, match="'template_name'"):
        _mount(Late)


def test_the_switch_is_gone():
    import sys

    assert not hasattr(
        sys.modules["djust.live_view"], "_REFUSE_RESERVED_STATE_NAMES_AT_CLASS_CREATION"
    )


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


def test_the_introspection_guards_are_still_reachable_after_class_creation():
    """Why the #4062 type guards in ``djust_audit`` and ``schema`` stay.

    The class-creation refusal leaves no class statement that can declare a
    ``state()`` field named ``template_name``, but a ``state()`` assigned to the
    class afterwards (and a ``@property template``) still puts a non-string on
    the class, and those enumerators read it straight off the class. With the
    guards removed this raised from ``json.dumps`` (#4050).
    """
    import json

    from djust.management.commands.djust_audit import _audit_class, _extract_vars
    from djust.schema import _extract_view_schema

    class Late(LiveView):
        exposure_policy = "explicit"
        template = "<div dj-root></div>"

    Late.template_name = state("late.html")

    class PropertyTemplate(LiveView):
        exposure_policy = "explicit"

        @property
        def template(self):
            return "<div dj-root></div>"

    for view in (Late, PropertyTemplate):
        json.dumps(_extract_view_schema(view, [LiveView]))
        json.dumps(_audit_class(view, "LiveView", verbose=True, base_classes=[LiveView]))
        _extract_vars(view)


@pytest.mark.parametrize("name", ["temporary_assigns", "static_assigns"])
@pytest.mark.parametrize("shape", ["assigned-after-creation", "property"])
def test_check_liveviews_reads_the_assigns_settings_only_when_they_are_collections(name, shape):
    """``check_liveviews`` (Q007) called ``.keys()`` on ``temporary_assigns`` and
    ``set()`` on ``static_assigns``; the two shapes class creation cannot refuse
    put a non-collection there, and the check crashed (#4070)."""
    import gc

    from djust.checks.components import check_liveviews

    if shape == "property":
        view = type(
            "AssignsShape",
            (LiveView,),
            {
                "__module__": __name__,
                "exposure_policy": "explicit",
                "template": "<div dj-root></div>",
                name: property(lambda self: None),
            },
        )
    else:
        view = type(
            "AssignsShape",
            (LiveView,),
            {
                "__module__": __name__,
                "exposure_policy": "explicit",
                "template": "<div dj-root></div>",
            },
        )
        setattr(view, name, state(None))
    try:
        check_liveviews(None)  # must not raise
    finally:
        del view
        gc.collect()
