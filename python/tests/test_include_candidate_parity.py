"""Candidate selection preserves order, errors, and list path semantics."""

import pytest
from django.template.backends.django import DjangoTemplates
from djust.template import DjustTemplateBackend


@pytest.mark.parametrize(
    "choices",
    [
        ["missing.html", "part.html"],
        ("missing.html", "part.html"),
        ["part.html", "other.html"],
        ["part.html", 42],
        ["missing.html", 42, "part.html"],
        ["broken.html", "part.html"],
        ["missing.html", "missing.html", "absent.html"],
        [],
        (),
        None,
        False,
        0,
        1,
        {"part.html": "ignored"},
        "",
    ],
)
def test_include_candidates_match_django(tmp_path, choices):
    (tmp_path / "part.html").write_text("selected")
    (tmp_path / "other.html").write_text("other")
    (tmp_path / "broken.html").write_text("{% invalidtag %}")
    params = {"NAME": "test", "DIRS": [str(tmp_path)], "APP_DIRS": False, "OPTIONS": {}}
    results = []
    for engine in [DjangoTemplates(params), DjustTemplateBackend(params)]:
        try:
            results.append(engine.from_string("{% include choices %}").render({"choices": choices}))
        except Exception as error:
            results.append(error)
    expected, actual = results
    if isinstance(expected, Exception):
        assert isinstance(actual, type(expected))
        # Syntax diagnostic spelling is covered separately by the parser suite.
        if choices != ["broken.html", "part.html"]:
            assert str(actual) == str(expected)
        if hasattr(expected, "tried"):
            assert actual.tried == expected.tried
    else:
        assert actual == expected


@pytest.mark.parametrize(
    "operand,invalid",
    [
        ("missing", "fallback.html"),
        ('missing|default:"other.html"', "fallback.html"),
        ('missing|default_if_none:"fallback.html"', ""),
        ("missing", "%s.html"),
    ],
)
def test_include_uses_strict_missing_variable_resolution(tmp_path, operand, invalid):
    for name in ["fallback.html", "other.html", "missing.html"]:
        (tmp_path / name).write_text(name)
    params = {
        "NAME": "test",
        "DIRS": [str(tmp_path)],
        "APP_DIRS": False,
        "OPTIONS": {"string_if_invalid": invalid},
    }
    results = []
    for engine in [DjangoTemplates(params), DjustTemplateBackend(params)]:
        try:
            results.append(engine.from_string("{% include " + operand + " %}").render({}))
        except Exception as error:
            results.append(error)
    expected, actual = results
    if isinstance(expected, Exception):
        assert isinstance(actual, type(expected))
        assert str(actual) == str(expected)
    else:
        assert actual == expected
