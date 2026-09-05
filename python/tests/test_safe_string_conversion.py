import pytest
from django.template import Context, Engine
from django.utils.safestring import mark_safe
from djust import _rust
from djust.template import DjustTemplateBackend


class TextObject:
    def __init__(self, text, safe):
        self._text = text
        self._safe = safe

    def __str__(self):
        return mark_safe(self._text) if self._safe else self._text


FILTERS = [
    "addslashes",
    "capfirst",
    "escapejs",
    "iriencode",
    "linenumbers",
    "lower",
    "make_list",
    "slugify",
    "title",
    "truncatechars:6",
    "truncatechars_html:6",
    "truncatewords:2",
    "truncatewords_html:2",
    "upper",
    "urlencode",
    "urlize",
    "urlizetrunc:8",
    "wordcount",
    "wordwrap:8",
    "ljust:20",
    "rjust:20",
    "center:20",
    'cut:";"',
    'cut:"you"',
    "escape",
    "force_escape",
    "linebreaks",
    "linebreaksbr",
    "safe",
    "striptags",
    "capfirst|upper",
    'default:"fallback"',
    'yesno:"yes,no"',
]
SOURCES = [
    "{{ p }}",
    "{% with q=p %}{{ q }}{% endwith %}",
    "{% firstof p %}",
    "{% cycle p p %}",
    "{{ rows|first }}",
    '{{ rows|join:"|" }}',
    "{{ rows|unordered_list }}",
]
SOURCES += ["{{ p|" + spec + " }}" for spec in FILTERS]


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("safe", [False, True])
@pytest.mark.parametrize("text", ["you &gt; me", "<b>& hello</b>", ""])
@pytest.mark.parametrize("entry", ["backend", "rust", "live"])
def test_string_conversion_safety_matches_django(source, safe, text, entry):
    value = TextObject(text, safe)
    context = {"p": value, "rows": [value]}
    expected = Engine().from_string(source).render(Context(context))
    if entry == "backend":
        backend = DjustTemplateBackend(
            {"NAME": "test", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}}
        )
        actual = backend.from_string(source).render(context)
    elif entry == "rust":
        actual = _rust.render_template(source, context)
    else:
        view = _rust.RustLiveView(source, [])
        view.set_raw_py_values(context)
        actual = view.render()
    assert actual == expected


def test_state_restore_does_not_restore_string_conversion_safety():
    view = _rust.RustLiveView("{{ p }}", [])
    view.set_state("p", TextObject("<b>&</b>", True))
    assert view.render() == "<b>&</b>"
    restored = _rust.RustLiveView.deserialize_msgpack(view.serialize_msgpack())
    assert restored.render() == "&lt;b&gt;&amp;&lt;/b&gt;"


def test_builtin_stringfilter_table_matches_django():
    import ast
    import inspect
    import pathlib
    import re
    from django.template import defaultfilters

    aliases = {
        "escapejs_filter": "escapejs",
        "escape_filter": "escape",
        "linebreaks_filter": "linebreaks",
    }
    parsed = ast.parse(inspect.getsource(defaultfilters))
    expected = {
        aliases.get(node.name, node.name)
        for node in parsed.body
        if isinstance(node, ast.FunctionDef)
        and any(isinstance(d, ast.Name) and d.id == "stringfilter" for d in node.decorator_list)
    }
    repo = pathlib.Path(__file__).resolve().parents[2]
    source = (repo / "crates/djust_templates/src/filters.rs").read_text()
    table = source.split("const STRING_FILTERS: &[&str] = &[", 1)[1].split("];", 1)[0]
    assert set(re.findall(r'"([a-z_]+)"', table)) == expected


@pytest.mark.parametrize("safe", [False, True])
def test_string_conversion_does_not_invoke_numeric_protocols(safe):
    from django.utils.safestring import SafeString

    class NumericString(SafeString if safe else str):
        def __float__(self):
            return 7.0

    value = TextObject(NumericString("<b>&</b>"), False)
    expected = Engine().from_string("{{ p }}").render(Context({"p": value}))
    assert _rust.render_template("{{ p }}", {"p": value}) == expected
