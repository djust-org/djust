"""Missing templates retain Django's searched-origin metadata across Rust."""

import pytest
from django.template import Engine, TemplateDoesNotExist

from djust import _rust
from djust.template import DjustTemplateBackend


def origin_rows(error):
    return [(origin.name, origin.template_name, reason) for origin, reason in error.tried]


@pytest.mark.parametrize("entry", ["load", "extends", "include"])
@pytest.mark.parametrize("name", ["missing.html", "nested/清風.html", "missing\nname.html"])
@pytest.mark.parametrize("directory_count", [0, 2])
@pytest.mark.parametrize("relative", [False, True])
def test_missing_origins_match_django(
    tmp_path, monkeypatch, entry, name, directory_count, relative
):
    from pathlib import Path

    monkeypatch.chdir(tmp_path)
    dirs = [Path(str(i)) if relative else tmp_path / str(i) for i in range(directory_count)]
    for directory in dirs:
        directory.mkdir()
    expected = Engine(dirs=dirs)
    backend = DjustTemplateBackend(
        {"NAME": "djust", "DIRS": dirs, "APP_DIRS": False, "OPTIONS": {}}
    )

    from django.template import Context

    with pytest.raises(TemplateDoesNotExist) as reference:
        if entry == "load":
            expected.get_template(name)
        else:
            expected.from_string("{% " + entry + " target %}").render(Context({"target": name}))
    with pytest.raises(TemplateDoesNotExist) as actual:
        if entry == "load":
            backend.get_template(name)
        else:
            backend.from_string("{% " + entry + " target %}").render({"target": name})
    assert actual.value.args == reference.value.args
    assert origin_rows(actual.value) == origin_rows(reference.value)
    assert [origin_rows(error) for error in actual.value.chain] == [
        origin_rows(error) for error in reference.value.chain
    ]
    assert actual.value.backend is backend
    assert all(origin.loader is backend for origin, _ in actual.value.tried)


@pytest.mark.parametrize("source", ["{% extends target %}", "{% include target %}"])
def test_raw_rust_keeps_runtime_error_with_structured_paths(tmp_path, source):
    missing = "missing\n清風.html"
    with pytest.raises(RuntimeError) as error:
        _rust.render_template_with_dirs(source, {"target": missing}, [str(tmp_path)])
    assert error.value.djust_missing_template_name == missing
    if "include" in source:
        assert not hasattr(error.value, "djust_tried_template_paths")
    else:
        assert error.value.djust_tried_template_paths == [str(tmp_path / missing)]


def test_rejected_traversal_has_no_searched_origins(tmp_path):
    with pytest.raises(RuntimeError) as error:
        _rust.render_template_with_dirs('{% include "../outside.html" %}', {}, [str(tmp_path)])
    assert getattr(error.value, "djust_tried_template_paths", []) == []


@pytest.mark.parametrize("nested_tag", ["extends", "include"])
def test_nested_lookup_preserves_the_failing_operation(tmp_path, nested_tag):
    from django.template import Context

    (tmp_path / "child.html").write_text("{% " + nested_tag + ' "missing.html" %}')
    source = '{% include "child.html" %}'
    engine = Engine(dirs=[tmp_path])
    backend = DjustTemplateBackend(
        {"NAME": "djust", "DIRS": [tmp_path], "APP_DIRS": False, "OPTIONS": {}}
    )
    with pytest.raises(TemplateDoesNotExist) as reference:
        engine.from_string(source).render(Context({}))
    with pytest.raises(TemplateDoesNotExist) as actual:
        backend.from_string(source).render({})
    assert actual.value.args == reference.value.args
    assert origin_rows(actual.value) == origin_rows(reference.value)
    assert [origin_rows(error) for error in actual.value.chain] == [
        origin_rows(error) for error in reference.value.chain
    ]
