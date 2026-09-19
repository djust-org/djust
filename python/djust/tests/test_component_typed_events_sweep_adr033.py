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
#: A ``dj-value-*`` whose text is a number but which carries no type suffix —
#: the handler would receive a string and start with ``int(value)`` again.
_UNTYPED_NUMBER = re.compile(r'dj-value-[a-z][a-z0-9-]*="-?\d+(?:\.\d+)?"')


def _examples() -> list:
    return [
        pytest.param(name, kwargs, id=f"{name}[{i}]")
        for name, kwargs_list in sorted(PYTHON_COMPONENT_EXAMPLES.items())
        for i, kwargs in enumerate(kwargs_list)
    ]


def _instance(name: str, kwargs: dict, **extra):
    module = importlib.import_module(f"djust.components.components.{name}")
    cls = getattr(module, _to_class_name(name))
    return cls(**{**kwargs, **extra})


class TestEveryEmitterUsesEventAttrs:
    def test_no_component_source_hand_writes_a_trigger_or_a_data_value(self):
        offenders = []
        for path in sorted(_COMPONENTS_DIR.glob("*.py")):
            src = path.read_text()
            if "data-value=" in src or _HAND_WRITTEN_TRIGGER.search(src):
                offenders.append(path.name)
        assert offenders == [], (
            "these components still hand-write an event attribute; emit it through "
            f"``self.event_attrs(event, trigger=..., value=...)`` (ADR-033 S3): {offenders}"
        )

    @pytest.mark.parametrize("name, kwargs", _examples())
    def test_rendered_example_carries_typed_values(self, name, kwargs):
        html = str(_instance(name, kwargs).render())
        assert "data-value=" not in html, html
        assert not _UNTYPED_NUMBER.search(html), _UNTYPED_NUMBER.search(html).group(0)

    @pytest.mark.parametrize("name, kwargs", _examples())
    def test_a_named_instance_names_every_trigger(self, name, kwargs):
        """D5: one handler serves several instances because every trigger the
        instance renders says which instance it is."""
        html = str(_instance(name, kwargs, name="probe").render())
        triggers = len(_TRIGGER_ATTR.findall(html))
        if not triggers:
            pytest.skip("this example renders no trigger")
        named = html.count('dj-value-name="probe"')
        assert named == triggers, (
            f"{name}: {triggers} trigger(s) but {named} carry dj-value-name — "
            "every emitter must go through self.event_attrs(...)"
        )
