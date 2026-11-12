"""#4032: V006 and V008 apply only to views that serialize plain attributes.

Both checks warn that a non-primitive assigned in ``mount()`` cannot be
serialized. Under ``exposure_policy = "explicit"`` a plain attribute is never
put in template context, the session, ``get_state()`` or a snapshot, so the
warning is wrong there; under ``"legacy"`` it is right. The checks are
AST-based, so the policy is read from the imported class (inherited or
defaulted) and, for a class that is not imported, from a literal statement in
its body.
"""

import gc
import importlib.util
import sys
import textwrap
import uuid
from unittest.mock import patch

import pytest

SOURCE = textwrap.dedent(
    """\
    from djust import LiveView


    class PricingService:
        def quote(self, items):
            return len(items) * 10


    class Basket:
        pass


    class ExplicitCart(LiveView):
        exposure_policy = "explicit"
        template = "<div dj-root>ok</div>"

        def mount(self, request, **kwargs):
            self.pricing = PricingService()
            self.basket = Basket()


    class LegacyCart(LiveView):
        exposure_policy = "legacy"
        template = "<div dj-root>ok</div>"

        def mount(self, request, **kwargs):
            self.pricing = PricingService()
            self.basket = Basket()


    class InheritsLegacy(LegacyCart):
        def mount(self, request, **kwargs):
            self.pricing = PricingService()
            self.basket = Basket()


    class InheritsExplicit(ExplicitCart):
        def mount(self, request, **kwargs):
            self.pricing = PricingService()
            self.basket = Basket()
    """
)


@pytest.fixture
def views_dir(tmp_path):
    name = "views_4032_%s" % uuid.uuid4().hex
    path = tmp_path / ("%s.py" % name)
    path.write_text(SOURCE)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    try:
        yield tmp_path
    finally:
        sys.modules.pop(name, None)
        del module
        gc.collect()


def _run(tmp_path):
    from djust.checks import (
        _check_non_primitive_assignments_in_mount,
        _check_service_instances_in_mount,
    )

    errors = []
    with patch("djust.checks._get_project_app_dirs", return_value=[str(tmp_path)]):
        _check_service_instances_in_mount(errors)
        _check_non_primitive_assignments_in_mount(errors)
    return errors


def _lines(errors, check_id):
    return sorted(e.line_number for e in errors if e.id == check_id)


def _mount_lines(class_name):
    """1-based lines of ``class_name``'s two ``mount()`` assignments."""
    lines = SOURCE.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("class %s(" % class_name))
    pricing = next(i for i in range(start, len(lines)) if "self.pricing" in lines[i]) + 1
    return pricing, pricing + 1


def test_explicit_views_get_neither_check(views_dir):
    errors = _run(views_dir)
    for cls in ("ExplicitCart", "InheritsExplicit"):
        pricing, basket = _mount_lines(cls)
        assert pricing not in _lines(errors, "djust.V006"), (cls, errors)
        assert basket not in _lines(errors, "djust.V008"), (cls, errors)


def test_legacy_views_still_get_both(views_dir):
    errors = _run(views_dir)
    for cls in ("LegacyCart", "InheritsLegacy"):
        pricing, basket = _mount_lines(cls)
        assert pricing in _lines(errors, "djust.V006"), (cls, errors)
        assert basket in _lines(errors, "djust.V008"), (cls, errors)


def test_an_unimported_class_stating_explicit_is_skipped(tmp_path):
    (tmp_path / "views.py").write_text(
        textwrap.dedent(
            """\
            class NotImported:
                exposure_policy = "explicit"

                def mount(self, request, **kwargs):
                    self.pricing = PricingService()
                    self.basket = Basket()


            class AlsoNotImported:
                def mount(self, request, **kwargs):
                    self.pricing = PricingService()
                    self.basket = Basket()
            """
        )
    )
    errors = _run(tmp_path)
    assert _lines(errors, "djust.V006") == [11]
    assert _lines(errors, "djust.V008") == [12]
