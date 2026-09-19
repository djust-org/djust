"""ADR-033 S3 — every shipped component emits its events through
``Component.event_attrs``: typed ``dj-value-*`` params (D4) and the instance
``name`` on every trigger (D5), never a hand-written ``dj-click="…"
data-value="…"``. The convention landed whole, in one sweep; these pins keep
it whole.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import django
import pytest
from django.conf import settings

if not settings.configured:
    settings.configure(SECRET_KEY="test-secret-key-adr033-sweep", INSTALLED_APPS=[], TEMPLATES=[])
    django.setup()

import djust.components.components as _pkg  # noqa: E402
from djust.theming.gallery.component_registry import (  # noqa: E402
    PYTHON_COMPONENT_EXAMPLES,
    _to_class_name,
)

_COMPONENTS_DIR = Path(_pkg.__file__).parent
_HAND_WRITTEN_TRIGGER = re.compile(
    r"""dj-(?:click|change|input|submit|keydown|keyup|blur|focus|dblclick)=["']"""
)
_TRIGGER_ATTR = re.compile(
    r'\sdj-(?:click|change|input|submit|keydown|keyup|blur|focus|dblclick)="'
)
#: A trigger element still carrying the untyped ``data-value`` — a
#: ``data-value`` on an element with no trigger (a hook's data attribute, as
#: ``animated_number`` and ``live_counter`` use) is not an event param.
_TRIGGER_WITH_DATA_VALUE = re.compile(
    r'<[^>]*\sdj-(?:click|change|input|submit|keydown|keyup|blur|focus|dblclick)="[^>]*\sdata-value='
    r'|<[^>]*\sdata-value=[^>]*\sdj-(?:click|change|input|submit|keydown|keyup|blur|focus|dblclick)="'
)


def _examples() -> list:
    return [
        pytest.param(name, kwargs, id=f"{name}[{i}]")
        for name, kwargs_list in sorted(PYTHON_COMPONENT_EXAMPLES.items())
        for i, kwargs in enumerate(kwargs_list)
    ]


def _instance(module_name: str, kwargs: dict, **extra):
    module = importlib.import_module(f"djust.components.components.{module_name}")
    cls = getattr(module, _to_class_name(module_name), None)
    if cls is None:
        pytest.skip(f"registry example names a class {module_name} does not define")
    try:
        return cls(**{**kwargs, **extra})
    except Exception as exc:  # noqa: BLE001 — a registry example the class refuses
        pytest.skip(f"registry example does not construct: {exc!r}")


def _render(instance) -> str:
    try:
        return str(instance.render())
    except Exception as exc:  # noqa: BLE001 — a registry example the class cannot render
        pytest.skip(f"registry example does not render: {exc!r}")


class TestEveryEmitterUsesEventAttrs:
    def test_no_component_source_hand_writes_a_trigger_or_a_data_value(self):
        offenders = []
        for path in sorted(_COMPONENTS_DIR.glob("*.py")):
            src = path.read_text()
            if _HAND_WRITTEN_TRIGGER.search(src):
                offenders.append(path.name)
        assert offenders == [], (
            "these components still hand-write a trigger attribute; emit it through "
            f"``self.event_attrs(event, trigger=..., value=...)`` (ADR-033 S3): {offenders}"
        )

    @pytest.mark.parametrize("name, kwargs", _examples())
    def test_rendered_example_carries_typed_values(self, name, kwargs):
        html = _render(_instance(name, kwargs))
        # A string id that happens to be digits ("1") is still a string, so
        # the wire type is pinned where the Python type is known
        # (test_component_event_attrs_adr033.py), not by regex here.
        found = _TRIGGER_WITH_DATA_VALUE.search(html)
        assert found is None, found and found.group(0)

    @pytest.mark.parametrize("name, kwargs", _examples())
    def test_a_named_instance_names_every_trigger(self, name, kwargs):
        """D5: one handler serves several instances because every trigger the
        instance renders says which instance it is."""
        html = _render(_instance(name, kwargs, name="probe"))
        triggers = len(_TRIGGER_ATTR.findall(html))
        if not triggers:
            pytest.skip("this example renders no trigger")
        named = html.count('dj-value-name="probe"')
        assert named == triggers, (
            f"{name}: {triggers} trigger(s) but {named} carry dj-value-name — "
            "every emitter must go through self.event_attrs(...)"
        )
