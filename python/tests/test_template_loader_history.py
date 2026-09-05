"""Same-name inheritance skips source origins without leaking history to includes."""

from pathlib import Path

import pytest
from django.template import Context, Engine, TemplateDoesNotExist

from djust.template import DjustTemplateBackend
from scripts.lib.django_template_suite.adapter import DjustEngine


def backends(dirs):
    return (
        Engine(dirs=dirs),
        DjustTemplateBackend({"NAME": "history", "DIRS": dirs, "APP_DIRS": False, "OPTIONS": {}}),
    )


def write_layers(tmp_path, layers):
    dirs = []
    for index, files in enumerate(layers):
        directory = tmp_path / str(index)
        directory.mkdir()
        dirs.append(str(directory))
        for name, source in files.items():
            path = directory / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source)
    return dirs


@pytest.mark.parametrize("operand", ['"base.html"', "parent"])
def test_same_name_extends_uses_next_directory(tmp_path, operand):
    dirs = write_layers(
        tmp_path,
        [
            {
                "base.html": "{% extends "
                + operand
                + " %}{% block c %}{{ block.super }} first{% endblock %}"
            },
            {
                "base.html": '{% extends "base.html" %}{% block c %}{{ block.super }} second{% endblock %}'
            },
            {"base.html": "{% block c %}third{% endblock %}"},
        ],
    )
    django, djust = backends(dirs)
    data = {"parent": "base.html"}
    expected = django.get_template("base.html").render(Context(data))
    assert expected == "third second first"
    # Repeated renders must have fresh history, even when compiled sources are cached.
    for _ in range(2):
        assert djust.get_template("base.html").render(data) == expected


def test_include_starts_its_own_inheritance_history(tmp_path):
    dirs = write_layers(
        tmp_path,
        [
            {
                "base.html": '{% extends "base.html" %}{% block c %}{{ block.super }}2{% endblock %}',
                "included.html": '{% extends "included.html" %}{% block i %}{{ block.super }}B{% endblock %}',
            },
            {
                "base.html": '{% block c %}1{% endblock %}{% include "included.html" %}',
                "included.html": "{% block i %}A{% endblock %}",
            },
        ],
    )
    django, djust = backends(dirs)
    assert django.get_template("base.html").render(Context()) == "12AB"
    assert djust.get_template("base.html").render({}) == "12AB"


@pytest.mark.parametrize(
    "source", ['{% extends "base.html" %}', '{% extends "sub/../base.html" %}']
)
def test_exhausted_history_reports_skipped_origins(tmp_path, source):
    dirs = write_layers(tmp_path, [{"base.html": source}, {}])
    # Django normalizes this path before reading; the intermediate folder exists.
    (Path(dirs[0]) / "sub").mkdir()
    django, djust = backends(dirs)
    failures = []
    for engine in (django, djust):
        with pytest.raises(TemplateDoesNotExist) as caught:
            engine.get_template("base.html").render(Context())
        failures.append([(o.name, reason) for o, reason in caught.value.tried])
    assert failures[1] == failures[0]
    assert failures[1][0][1] == "Skipped to avoid recursion"


def test_adapter_keeps_distinct_locmem_sources(tmp_path):
    loaders = [
        ("django.template.loaders.locmem.Loader", {"base.html": "first"}),
        ("django.template.loaders.locmem.Loader", {"base.html": "second"}),
    ]
    engine = DjustEngine(loaders=loaders)
    dirs = engine.template_dirs
    assert len(set(dirs)) == 2
    assert [(d / "base.html").read_text() for d in dirs] == ["first", "second"]


def test_adapter_same_name_inheritance_and_include_match_django():
    loaders = [
        (
            "django.template.loaders.locmem.Loader",
            {
                "base.html": '{% extends "base.html" %}{% block c %}{{ block.super }}2{% endblock %}',
                "included.html": '{% extends "included.html" %}{% block i %}{{ block.super }}B{% endblock %}',
            },
        ),
        (
            "django.template.loaders.locmem.Loader",
            {
                "base.html": '{% block c %}1{% endblock %}{% include "included.html" %}',
                "included.html": "{% block i %}A{% endblock %}",
            },
        ),
    ]
    assert Engine(loaders=loaders).get_template("base.html").render(Context()) == "12AB"
    assert DjustEngine(loaders=loaders).get_template("base.html").render(Context()) == "12AB"
