import pytest
from django.template import Context, Engine
from django.utils.safestring import mark_safe
from djust.template import DjustTemplateBackend


@pytest.mark.parametrize(
    "source",
    [
        "{{ p|lower|add:q }}",
        "{{ p|lower|add:plain }}",
        "{{ p|add:q }}",
        '{{ p|add:"<&" }}',
        "{{ p|add:plain }}",
        "{% if p == plain %}yes{% else %}no{% endif %}",
        "{% if p < q %}yes{% else %}no{% endif %}",
        "{% if p in haystack %}yes{% else %}no{% endif %}",
        "{{ p.1 }}",
        "{{ rows|last }}",
        '{{ rows|slice:"1:"|first }}',
        '{{ rows|join:"|" }}',
        "{{ rows|unordered_list }}",
        '{% with seq=rows|safeseq %}{{ seq|join:"|" }}{% endwith %}',
        '{% with seq=rows|escapeseq %}{{ seq|join:"|" }}{% endwith %}',
        '{{ records|dictsort:"name"|first }}',
        '{{ empty|date:"\\x" }}',
        "{{ p|upper }}",
        "{{ p|lower }}",
    ],
)
def test_string_operations_preserve_django_safety(source):
    values = {
        "p": mark_safe("a&"),
        "q": mark_safe("b&"),
        "plain": "a&",
        "haystack": "za&z",
        "rows": ["a&", mark_safe("b&")],
        "records": [{"name": mark_safe("z")}, {"name": "a"}],
        "empty": mark_safe(""),
    }
    backend = DjustTemplateBackend({"NAME": "test", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}})
    expected = Engine().from_string(source).render(Context(values))
    assert backend.from_string(source).render(values) == expected


@pytest.mark.parametrize("operation", ['dictsort:"rank"', 'slice:"::-1"'])
def test_reordered_rows_keep_safety_attached_to_the_selected_value(operation):
    values = {
        "rows": [
            {"rank": 2, "text": mark_safe("<b>safe</b>")},
            {"rank": 1, "text": "<img src=x onerror=alert(1)>"},
        ]
    }
    source = (
        "{% with selected=rows|"
        + operation
        + " %}{% for row in selected %}[{{ row.text }}]{% endfor %}{% endwith %}"
    )
    backend = DjustTemplateBackend({"NAME": "test", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}})
    expected = Engine().from_string(source).render(Context(values))
    assert "<img" not in expected
    assert "<b>safe</b>" in expected
    assert backend.from_string(source).render(values) == expected
