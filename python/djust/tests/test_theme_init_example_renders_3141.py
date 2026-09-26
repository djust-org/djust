"""`djust_theme init --with-examples` writes a template that renders (#3141).

The example template carried a two-line ``{# #}`` comment. Django's ``{# #}``
is single-line only, so the text was parsed as template source and the
``{% end_theme_card %}`` quoted inside it raised ``TemplateSyntaxError``.
"""

import pytest
from django.core.management import call_command
from django.template import Context, Engine
from django.test import RequestFactory

pytestmark = pytest.mark.theming


def test_generated_example_template_renders(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    call_command("djust_theme", "init", "--with-examples")

    engine = Engine(
        dirs=[str(tmp_path / "templates")],
        libraries={
            "static": "django.templatetags.static",
            "theme_tags": "djust.theming.templatetags.theme_tags",
            "theme_components": "djust.theming.templatetags.theme_components",
        },
    )
    request = RequestFactory().get("/")
    html = engine.get_template("examples/theme_example.html").render(Context({"request": request}))

    assert "djust-theming Example" in html
    # The comment is a comment: none of its text reaches the page.
    assert "simple_tag" not in html
