"""djust.V027: a system check that names each class stating ``exposure_policy = "legacy"`` (#4047).

The 2.0 plans described a check listing the views that still run under the
legacy exposure policy, with a pointer to ``djust_exposure_inventory``. It was
never written; stating ``"legacy"`` raised only a ``DeprecationWarning`` at
import time, which test runners and servers usually hide.
"""

from __future__ import annotations

import warnings

from djust import LiveView
from djust.checks.components import check_legacy_exposure_policy

with warnings.catch_warnings():
    # Stating "legacy" warns at class creation; these classes exist to be reported.
    warnings.simplefilter("ignore", DeprecationWarning)

    class LegacyView(LiveView):
        exposure_policy = "legacy"
        template = "<div dj-root></div>"

    class InheritsLegacy(LegacyView):
        pass

    class LegacyMixin:
        exposure_policy = "legacy"

    class ViaMixin(LegacyMixin, LiveView):
        template = "<div dj-root></div>"

    class BackToExplicit(LegacyView):
        exposure_policy = "explicit"


class ExplicitView(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root></div>"


class DefaultView(LiveView):
    template = "<div dj-root></div>"


with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)

    # A base that djust ships pinned to "legacy". Built here with a djust module
    # name, so the test does not depend on which shipped class (PersistentLiveView,
    # an admin view) still states it.
    FrameworkLegacyBase = type(
        "FrameworkLegacyBase",
        (LiveView,),
        {"__module__": "djust.shipped_for_test", "exposure_policy": "legacy", "abstract": True},
    )

    class OnAFrameworkLegacyBase(FrameworkLegacyBase):
        """Inherits "legacy" from a class djust ships: not this project's line to change."""

        template = "<div dj-root></div>"


def _v026(*classes):
    labels = {"%s.%s" % (c.__module__, c.__qualname__) for c in classes}
    return [
        m
        for m in check_legacy_exposure_policy(None)
        if any(m.msg.startswith(label + " ") for label in labels)
    ]


class TestV027:
    def test_a_class_stating_legacy_is_reported_once_with_its_users(self):
        (message,) = _v026(LegacyView)
        assert message.id == "djust.V027"
        assert message.level == 30  # WARNING
        assert 'exposure_policy = "legacy"' in message.msg
        assert "%s.InheritsLegacy" % __name__ in message.msg
        assert "djust_exposure_inventory" in message.hint
        assert "migrating-to-2.0" in message.hint
        # The subclass inherits the statement; it is not reported on its own.
        assert _v026(InheritsLegacy) == []

    def test_a_policy_stated_on_a_mixin_is_reported_against_the_mixin(self):
        assert _v026(ViaMixin) == []
        (message,) = _v026(LegacyMixin)
        assert "%s.ViaMixin" % __name__ in message.msg

    def test_explicit_and_default_views_are_not_reported(self):
        assert _v026(ExplicitView, DefaultView, BackToExplicit) == []

    def test_a_view_on_a_framework_legacy_base_is_not_reported(self):
        assert _v026(OnAFrameworkLegacyBase) == []
        text = " ".join(m.msg for m in check_legacy_exposure_policy(None))
        assert "djust.shipped_for_test" not in text
        assert "djust.admin_ext" not in text

    def test_it_can_be_suppressed(self, settings):
        settings.DJUST_CONFIG = {"suppress_checks": ["V027"]}
        assert _v026(LegacyView, LegacyMixin) == []

    def test_it_is_registered_with_the_djust_checks(self):
        from django.core.checks.registry import registry

        assert check_legacy_exposure_policy in registry.get_checks()
        assert "djust" in check_legacy_exposure_policy.tags
