"""Python loader confinement must agree with Django for real filesystem reads."""

import pytest
from django.template import TemplateDoesNotExist
from django.template.backends.django import DjangoTemplates
from djust.template import DjustTemplateBackend


@pytest.mark.parametrize("kind", ["parent", "nested", "absolute", "in_root", "normal"])
def test_template_loader_containment(tmp_path, kind):
    root = tmp_path / "templates"
    root.mkdir()
    (root / "sub").mkdir()
    (tmp_path / "outside.txt").write_text("outside-root")
    (root / "ok.html").write_text("inside-root")
    names = {
        "parent": "../outside.txt",
        "nested": "sub/../../outside.txt",
        "absolute": str(tmp_path / "outside.txt"),
        "in_root": "sub/../ok.html",
        "normal": "ok.html",
    }
    for cls in (DjangoTemplates, DjustTemplateBackend):
        backend = cls({"NAME": cls.__name__, "DIRS": [root], "APP_DIRS": False, "OPTIONS": {}})
        if kind in ("parent", "nested", "absolute"):
            with pytest.raises(TemplateDoesNotExist) as error:
                backend.get_template(names[kind])
            assert not error.value.tried
        else:
            assert backend.get_template(names[kind]).render({}) == "inside-root"


def test_loader_keeps_searching_and_reports_origins(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (second / "ok.html").write_text("second-root")
    backend = DjustTemplateBackend(
        {"NAME": "test", "DIRS": [first, second], "APP_DIRS": False, "OPTIONS": {}}
    )
    assert backend.get_template("ok.html").render({}) == "second-root"
    with pytest.raises(TemplateDoesNotExist) as error:
        backend.get_template("missing.html")
    assert [origin.name for origin, _ in error.value.tried] == [
        str(first / "missing.html"),
        str(second / "missing.html"),
    ]
