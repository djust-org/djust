"""Library tags validate at compilation, without rendering application code."""

import pytest
from django.template import Context, Engine, Library, TemplateSyntaxError

from djust import _rust
from djust.template import DjustTemplateBackend
from djust.template_libraries import LibraryBlockTagHandler, LibraryTagHandler


@pytest.fixture
def engines():
    library = Library()
    calls = []

    @library.simple_tag(name="compile_inline")
    def inline(required):
        calls.append(required)
        return required

    @library.simple_block_tag(name="compile_block")
    def block(content, required):
        calls.append(required)
        return content + required

    django = Engine()
    django.template_builtins.append(library)
    backend = DjustTemplateBackend(
        {"NAME": "validation", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}}
    )
    _rust.register_tag_handler(
        "compile_inline",
        LibraryTagHandler("test", "compile_inline", library.tags["compile_inline"]),
    )
    _rust.register_block_tag_handler(
        "compile_block",
        "endcompile_block",
        LibraryBlockTagHandler("test", "compile_block", library.tags["compile_block"]),
    )
    try:
        yield django, backend, calls
    finally:
        _rust.unregister_tag_handler("compile_inline")
        _rust.unregister_block_tag_handler("compile_block")


@pytest.mark.parametrize("tag", ["compile_inline", "compile_block"])
@pytest.mark.parametrize("args", ["", "1 2", "unknown=1", "required=1 required=2"])
@pytest.mark.parametrize("dead_branch", [False, True])
def test_invalid_arguments_refused_at_construction(engines, tag, args, dead_branch):
    django, backend, calls = engines
    source = "{% " + tag + " " + args + " %}"
    if tag == "compile_block":
        source += "body{% endcompile_block %}"
    if dead_branch:
        source = "{% if False %}" + source + "{% endif %}"
    with pytest.raises(TemplateSyntaxError) as expected:
        django.from_string(source)
    with pytest.raises(TemplateSyntaxError) as actual:
        backend.from_string(source)
    assert str(actual.value) == str(expected.value)
    assert calls == []


def test_compilation_does_not_execute_tag(engines):
    django, backend, calls = engines
    source = '{% compile_inline "value" %}{% compile_block "tail" %}body{% endcompile_block %}'
    template = backend.from_string(source)
    assert calls == []
    assert template.render({}) == django.from_string(source).render(Context())


def test_compile_exception_keeps_identity_and_innermost_source():
    error = RuntimeError("compile failure")

    def compile_bad(parser, token):
        raise error

    name = "compile_raises"
    _rust.register_tag_handler(name, LibraryTagHandler("test", name, compile_bad))
    try:
        backend = DjustTemplateBackend(
            {"NAME": "validation", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}}
        )
        with pytest.raises(RuntimeError) as actual:
            backend.from_string("é\n{% if True %}{% compile_raises %}{% endif %}")
        assert actual.value is error
        assert error.template_debug["during"] == "{% compile_raises %}"
        assert error.template_debug["line"] == 2
    finally:
        _rust.unregister_tag_handler(name)
