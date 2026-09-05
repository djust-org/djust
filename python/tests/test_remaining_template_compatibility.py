"""Behavioral compatibility retained alongside explicitly unsupported internals."""

import pytest
from django.template import Context, Engine, TemplateSyntaxError
from django.utils.safestring import SafeData
from djust.template import DjustTemplateBackend


def backend(tmp_path):
    return DjustTemplateBackend(
        {"NAME": "remaining", "DIRS": [tmp_path], "APP_DIRS": False, "OPTIONS": {"debug": True}}
    )


@pytest.mark.parametrize("use_context", [False, True])
@pytest.mark.parametrize(
    "source",
    [
        "{% firstof a b as result %}",
        "{% firstof 0 False as result %}",
        "{% autoescape off %}{% firstof a as result %}{% endautoescape %}",
        "{% if True %}{% firstof a as result %}{% endif %}",
        "{% for i in items %}{% firstof a as result %}{% endfor %}",
        "{% with a='inner' %}{% firstof a as result %}{% endwith %}",
        "{% block main %}{% firstof a as result %}{% endblock %}",
        '{% include "included.html" %}',
        "{% cycle a b as result %}",
        "{% widthratio 2 4 100 as result %}",
    ],
)
def test_assignments_escape_only_their_scope(tmp_path, use_context, source):
    (tmp_path / "included.html").write_text("{% firstof a as result %}")
    observations = []
    for engine in [Engine(dirs=[tmp_path]), backend(tmp_path)]:
        data = {"a": "<a>", "b": "b", "items": [1, 2], "result": "original"}
        context = Context(data) if use_context or isinstance(engine, Engine) else data
        output = engine.from_string(source).render(context)
        observations.append((output, data["result"], isinstance(data["result"], SafeData)))
    assert observations[1] == observations[0]


def test_assignments_before_exception_survive(tmp_path):
    for engine in [Engine(), backend(tmp_path)]:
        data = {"boom": lambda: 1 / 0}
        with pytest.raises(ZeroDivisionError):
            engine.from_string("{% firstof 'set' as result %}{{ boom }}").render(Context(data))
        assert data["result"] == "set"
        assert isinstance(data["result"], SafeData)


@pytest.mark.parametrize(
    "body,raises",
    [
        ("{{ block.super }}", True),
        ("{{ block.super|default:'fallback' }}", True),
        ("{% firstof block.super 'fallback' %}", True),
        ("{% if block.super %}yes{% endif %}", True),
        ("{% if False %}{{ block.super }}{% endif %}", False),
        ("{% with b=block %}{{ b.super }}{% endwith %}", True),
        ("{% with block=user_block %}{{ block.super }}{% endwith %}", False),
    ],
)
def test_base_block_super_is_lazy(tmp_path, body, raises):
    source = "{% block main %}" + body + "{% endblock %}"
    for engine in [Engine(), backend(tmp_path)]:
        template = engine.from_string(source)
        context = Context({"user_block": {"super": "user"}})
        if raises:
            with pytest.raises(TemplateSyntaxError, match="base template"):
                template.render(context)
        else:
            template.render(context)


def test_inherited_base_block_super_is_empty(tmp_path):
    (tmp_path / "base.html").write_text("{% block main %}base {{ block.super }}{% endblock %}")
    source = '{% extends "base.html" %}{% block main %}child {{ block.super }}{% endblock %}'
    for engine in [Engine(dirs=[tmp_path]), backend(tmp_path)]:
        assert engine.from_string(source).render(Context()) == "child base "


@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize("nested", [False, True])
def test_include_identity_matches_loader_policy(tmp_path, cached, nested):
    from djust._rust import render_template_with_dirs

    templates = {"include": "{% ifchanged %}{{ x }}{% endifchanged %}"}
    if nested:
        templates["outer"] = '{% include "include" %}{% include "include" %}'
    target = "outer" if nested else "include"
    source = (
        '{% for x in values %}{% include "'
        + target
        + '" %}{% include "'
        + target
        + '" %}{% endfor %}'
    )
    loader = ("django.template.loaders.locmem.Loader", templates)
    loaders = [("django.template.loaders.cached.Loader", [loader])] if cached else [loader]
    expected = (
        Engine(loaders=loaders).from_string(source).render(Context({"values": [1, 1, 2, 2, 3, 3]}))
    )
    for name, content in templates.items():
        (tmp_path / name).write_text(content)
    for _ in range(2):
        actual = render_template_with_dirs(
            source,
            {"values": [1, 1, 2, 2, 3, 3]},
            [str(tmp_path)],
            uncached_template_dirs=[] if cached else [str(tmp_path)],
        )
        assert actual == expected


def test_failed_renders_do_not_cache_caller_tracebacks(tmp_path):
    import gc
    import weakref
    from django.template import TemplateDoesNotExist

    class Payload:
        pass

    template = backend(tmp_path).from_string('{% include "missing.html" %}')

    def fail():
        payload = Payload()
        reference = weakref.ref(payload)
        try:
            template.render({"payload": payload})
        except TemplateDoesNotExist:
            pass
        return reference

    references = [fail(), fail()]
    gc.collect()
    assert all(reference() is None for reference in references)


def test_compiled_template_state_is_per_render(tmp_path):
    template = backend(tmp_path).from_string(
        "{% for x in values %}{% ifchanged %}{{ x }}{% endifchanged %}{% endfor %}"
    )
    handle = template._compiled_template
    assert template.render({"values": [1, 1, 2]}) == "12"
    assert template.render({"values": [1, 1, 2]}) == "12"
    assert template._compiled_template is handle


def test_caller_assignment_order(tmp_path):
    source = "{% firstof 'one' as z %}{% firstof 'two' as a %}{% firstof 'three' as z %}"
    for engine in [Engine(), backend(tmp_path)]:
        data = {"original": 1}
        engine.from_string(source).render(Context(data))
        assert list(data) == ["original", "z", "a"]
        assert data["z"] == "three"


def test_uncached_policy_accepts_relative_directories(tmp_path, monkeypatch):
    from djust._rust import render_template_with_dirs

    monkeypatch.chdir(tmp_path)
    (tmp_path / "templates").mkdir()
    (tmp_path / "templates/include").write_text("{% ifchanged %}{{ x }}{% endifchanged %}")
    source = '{% for x in values %}{% include "include" %}{% include "include" %}{% endfor %}'
    assert (
        render_template_with_dirs(
            source, {"values": [1, 1, 2, 2]}, ["templates"], uncached_template_dirs=["templates/."]
        )
        == "1122"
    )


def test_cycle_updates_caller_through_block_super(tmp_path):
    (tmp_path / "base.html").write_text("{% block main %}base{% endblock %}")
    source = '{% extends "base.html" %}{% block main %}{% cycle "one" "two" as result %}{{ block.super }}{% endblock %}'
    for engine in [Engine(dirs=[tmp_path]), backend(tmp_path)]:
        data = {"result": "original"}
        assert engine.from_string(source).render(Context(data)) == "onebase"
        assert data["result"] == "one"


def test_inherited_base_block_super_alias_is_valid(tmp_path):
    (tmp_path / "base.html").write_text(
        "{% block main %}{% with b=block %}base {{ b.super }}{% endwith %}{% endblock %}"
    )
    for engine in [Engine(dirs=[tmp_path]), backend(tmp_path)]:
        assert engine.from_string('{% extends "base.html" %}').render(Context()) == "base "
