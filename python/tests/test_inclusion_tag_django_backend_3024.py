"""Third-party inclusion tags in a LiveView template, Django backend only (#3024).

With no djust template backend configured, the stub engine the bridge hands
Django's ``InclusionNode`` resolved the inclusion template through Django's
loader, which returns the BACKEND ``Template`` wrapper. Its ``render()`` takes
only a dict, and the node passes a ``Context``: every app ``inclusion_tag`` in
a LiveView template raised "context must be a dict rather than Context".

Parity is asserted against the real Django engine rendering the same source.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("django")

from django.template.backends.django import DjangoTemplates  # noqa: E402
from django.test import override_settings  # noqa: E402

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from test_load_imports_django_libraries_2547 import (  # noqa: E402
    LIBRARIES,
    TEMPLATE_DIR,
    liveview_render,
)

DJANGO_ONLY = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [str(TEMPLATE_DIR)],
        "APP_DIRS": True,
        "OPTIONS": {"libraries": LIBRARIES},
    }
]

CASES = [
    "{% load lib2547_tags %}{% incl_one2547 value %}",
    "{% load lib2547_tags %}{% incl_ctx2547 %}",
    "{% load lib2547_tags %}{% incl_one2547 hostile %}",
]
CTX = {"value": 42, "hostile": "<img src=x onerror=alert(1)>"}


def _django(source: str) -> str:
    engine = DjangoTemplates(
        {
            "NAME": "django3024",
            "DIRS": [str(TEMPLATE_DIR)],
            "APP_DIRS": False,
            "OPTIONS": {"libraries": LIBRARIES},
        }
    )
    return str(engine.from_string(source).render(dict(CTX)))


@pytest.mark.parametrize("source", CASES)
def test_inclusion_tag_renders_like_django_with_only_the_django_backend(source):
    with override_settings(TEMPLATES=DJANGO_ONLY):
        got = liveview_render(source, CTX)
    assert got == _django(source)
    assert "&lt;img" in _django(CASES[2])  # user values stay escaped


def test_the_stub_engine_hands_out_an_engine_level_template():
    """The mechanism: the backend wrapper is unwrapped to the
    ``django.template.base.Template`` that accepts a ``Context``."""
    from django.template.base import Template

    from djust.template_libraries import _StubEngine

    with override_settings(TEMPLATES=DJANGO_ONLY):
        assert isinstance(_StubEngine().get_template("lib2547_incl.html"), Template)
        assert isinstance(_StubEngine().select_template(["lib2547_incl.html"]), Template)
