"""Local model bindings must resolve their own object, not a stale raw root."""

import pytest
from django.contrib.auth.models import User
from django.template import Context, Engine
from djust import _rust
from djust.template import DjustTemplateBackend

SOURCES = [
    '{% with p="local" %}{{ p.get_full_name }}{% endwith %}|{{ p.get_full_name }}',
    '{% with p="local" %}{% with q=p %}{{ q.get_full_name }}{% endwith %}{% endwith %}',
    "{% with p=p %}{{ p.get_full_name }}{% endwith %}",
    '{% with q=p %}{% with p="local" %}{{ q.get_full_name }}{% endwith %}{% endwith %}',
    "{% with p=other %}{{ p.get_full_name }}{% endwith %}|{{ p.get_full_name }}",
    "{% for p in people %}{{ p.get_full_name }};{% endfor %}|{{ p.get_full_name }}",
    "{% with p=other other=p %}{{ p.get_full_name }}|{{ other.get_full_name }}{% endwith %}",
]


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("entry", ["backend", "rust", "dirs"])
def test_model_shadowing_matches_django(source, entry):
    original = User(username="original", first_name="Original&A", last_name="User")
    other = User(username="other", first_name="Other&B", last_name="User")
    context = {
        "p": original,
        "other": other,
        "people": [other],
        "q": User(username="ghost", first_name="Ghost", last_name="User"),
    }
    expected = Engine().from_string(source).render(Context(context))
    if entry == "backend":
        backend = DjustTemplateBackend(
            {"NAME": "djust", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}}
        )
        actual = backend.from_string(source).render(context)
    elif entry == "rust":
        actual = _rust.render_template(source, context)
    else:
        actual = _rust.render_template_with_dirs(source, context, [])
    assert actual == expected
