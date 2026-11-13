"""#3956: ``DjustTemplateBackend`` warned that the supported OPTIONS keys ``libraries`` and
``builtins`` were unsupported, because the unknown-key check ran before they were read."""

from __future__ import annotations

import logging

import pytest

from djust.template import DjustTemplateBackend

LOGGER = "djust.template.backend"


def _build(options: dict) -> DjustTemplateBackend:
    return DjustTemplateBackend(
        params={"NAME": "djust", "DIRS": [], "APP_DIRS": False, "OPTIONS": options}
    )


def _warnings(caplog) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == LOGGER and r.levelno == logging.WARNING and "OPTIONS" in r.getMessage()
    ]


@pytest.mark.parametrize(
    "options",
    [
        {"libraries": {"djust_3956": "django.templatetags.static"}},
        {"builtins": ["django.templatetags.static"]},
        {
            "libraries": {"djust_3956": "django.templatetags.static"},
            "builtins": ["django.templatetags.static"],
            "autoescape": True,
            "debug": False,
            "string_if_invalid": "",
            "context_processors": [],
        },
    ],
    ids=["libraries", "builtins", "every-supported-key"],
)
def test_a_supported_key_is_not_reported(caplog, options) -> None:
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        _build(options)
    assert _warnings(caplog) == []


def test_an_unknown_key_is_still_reported_once(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        _build({"libraries": {"djust_3956": "django.templatetags.static"}, "loaders": []})
    warnings = _warnings(caplog)
    assert len(warnings) == 1
    assert "loaders" in warnings[0] and "libraries" not in warnings[0]
