"""1.3 deprecations: everything djust 2.0 removes warns first (#3500).

Each warning names its replacement and ``2.0.0``, through the standard
``warn_deprecated`` helper. Where a test asserts the warning's filename, the
property is that the warning points at the caller (this file), not at djust.
"""

import os
import warnings

import pytest
from django.test import RequestFactory

THIS_FILE = os.path.basename(__file__)


def _one_deprecation(record, needle):
    hits = [
        w for w in record if issubclass(w.category, DeprecationWarning) and needle in str(w.message)
    ]
    assert len(hits) == 1, [str(w.message) for w in record]
    assert "2.0.0" in str(hits[0].message)
    return hits[0]


# --- component mixins (DEP-002) ---------------------------------------------

MIXINS = [
    ("AccordionMixin", "Accordion"),
    ("TabsMixin", "Tabs"),
    ("ModalMixin", "Modal"),
    ("CollapsibleMixin", "Collapsible"),
    ("SheetMixin", "Sheet"),
    ("DropdownMixin", "Dropdown"),
    ("TooltipMixin", "Tooltip"),
    ("CarouselMixin", "Carousel"),
]


@pytest.mark.parametrize("mixin_name,replacement", MIXINS)
def test_subclassing_a_deprecated_mixin_warns_at_the_class_statement(mixin_name, replacement):
    from djust.components import mixins

    mixin = getattr(mixins, mixin_name)
    with warnings.catch_warnings(record=True) as record:
        warnings.simplefilter("always")

        class _Page(mixin):  # <- the user frame
            pass

    hit = _one_deprecation(record, mixin_name)
    assert "djust.components.descriptors" in str(hit.message)
    assert replacement in str(hit.message)
    assert os.path.basename(hit.filename) == THIS_FILE, hit.filename


def test_a_subclass_of_a_subclass_does_not_warn_again():
    from djust.components.mixins import TabsMixin

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")

        class _Base(TabsMixin):
            pass

    with warnings.catch_warnings(record=True) as record:
        warnings.simplefilter("always")

        class _Child(_Base):
            pass

    assert [w for w in record if "TabsMixin" in str(w.message)] == []


def test_the_warning_follows_the_user_frame_behind_liveview_init_subclass():
    """LiveView first: its ``__init_subclass__`` chains to the mixin's."""
    from djust import LiveView
    from djust.components.mixins import ModalMixin

    with warnings.catch_warnings(record=True) as record:
        warnings.simplefilter("always")

        class _Page(LiveView, ModalMixin):  # <- the user frame
            exposure_policy = "legacy"
            template = "<div dj-root></div>"

    hit = _one_deprecation(record, "ModalMixin")
    assert os.path.basename(hit.filename) == THIS_FILE, hit.filename


def test_using_the_descriptor_components_does_not_warn():
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        from djust.components.descriptors import Accordion  # noqa: F401


# --- social_auth_providers ----------------------------------------------------


@pytest.mark.django_db
def test_social_auth_providers_warns_through_the_policy_helper():
    from djust.auth.social import social_auth_providers

    with warnings.catch_warnings(record=True) as record:
        warnings.simplefilter("always")
        social_auth_providers(RequestFactory().get("/"))
    hit = _one_deprecation(record, "social_auth_providers")
    message = str(hit.message)
    assert "deprecated since djust 1.3" in message
    assert "{% auth_providers auth %}" in message
    assert os.path.basename(hit.filename) == THIS_FILE, hit.filename


@pytest.mark.parametrize("mixin_name,replacement", MIXINS)
def test_a_mixin_docstring_names_the_real_replacement_module(mixin_name, replacement):
    from djust.components import mixins

    doc = getattr(mixins, mixin_name).__doc__
    assert "djust.components.descriptors.%s" % replacement in doc, doc
    assert "djust_components.descriptors" not in doc
