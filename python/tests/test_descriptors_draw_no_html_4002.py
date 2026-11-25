"""The descriptor components hold state and default events; they draw no HTML (#4002, S2).

"One renderer per component": a component's markup is drawn in one place. A descriptor
(``djust.components.descriptors``: ``Tabs``, ``Modal``, ``Accordion`` ...) is declared on a
view for its per-view state and its default event (``set_tab``, ``toggle`` ...); the markup is
the template tag's (``{% tabs %}`` ...), which reads that state. This pins that no descriptor
grows a renderer of its own, which would be a second renderer for the tag's component.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil

import pytest

import djust.components.descriptors as descriptors_package
from djust.components.descriptors.base import LiveComponent as DescriptorBase

RENDERERS = ("render", "template", "template_name", "_render_custom", "render_html")


def _descriptor_classes() -> dict[str, type]:
    found: dict[str, type] = {}
    for info in pkgutil.iter_modules(descriptors_package.__path__):
        module = importlib.import_module(f"{descriptors_package.__name__}.{info.name}")
        for name, cls in vars(module).items():
            if (
                inspect.isclass(cls)
                and issubclass(cls, DescriptorBase)
                and cls is not DescriptorBase
                and cls.__module__ == module.__name__
            ):
                found[f"{module.__name__}.{name}"] = cls
    return found


DESCRIPTORS = _descriptor_classes()


def test_the_descriptors_are_found() -> None:
    names = {key.rsplit(".", 1)[-1] for key in DESCRIPTORS}
    assert {"Tabs", "Modal", "Accordion", "Tooltip", "Carousel"} <= names
    assert len(DESCRIPTORS) >= 20


@pytest.mark.parametrize("key", sorted(DESCRIPTORS))
def test_a_descriptor_defines_no_renderer(key: str) -> None:
    cls = DESCRIPTORS[key]
    own = {
        name for klass in cls.__mro__ if klass not in DescriptorBase.__mro__ for name in vars(klass)
    }
    assert not own & set(RENDERERS), (key, sorted(own & set(RENDERERS)))
