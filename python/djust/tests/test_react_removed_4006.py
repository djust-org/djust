"""djust.react, ReactMixin and capitalized component tags are removed (#4006)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1]
REPO = PACKAGE.parents[1]


@pytest.mark.parametrize("name", ["ReactMixin", "react_components", "register_react_component"])
def test_removed_names_raise_naming_the_hook_contract(name):
    with pytest.raises(ImportError) as exc:
        exec("from djust import %s" % name, {})
    text = str(exc.value)
    assert "removed in djust 2.0" in text
    assert "dj-hook" in text
    assert "migrating-to-2.0" in text


@pytest.mark.parametrize(
    "src",
    ['<p><Button label="x" /></p>', "<Widget>child</Widget>", '<Chart\n data="1">'],
)
def test_capitalized_tag_is_a_template_error_under_the_native_engine(src):
    from djust._rust import render_template

    with pytest.raises(Exception) as exc:
        render_template(src, {})
    assert "removed in djust 2.0" in str(exc.value)
    assert "dj-hook" in str(exc.value)


@pytest.mark.parametrize(
    "src,expected",
    [
        ("<TextArea>{{ v }}</TextArea>", "<TextArea>a</TextArea>"),
        ("<DIV>{{ v }}</DIV>", "<DIV>a</DIV>"),
        ("<Button>{{ v }}</Button>", "<Button>a</Button>"),
        ("<BR/>", "<BR/>"),
        ('<Input name="n" />', '<Input name="n" />'),
    ],
)
def test_a_standard_element_in_any_case_is_still_html(src, expected):
    from djust._rust import render_template

    assert render_template(src, {"v": "a"}) == expected


@pytest.mark.parametrize("src", ["<TextArea />", "<Header />", '<Button label="x"/>'])
def test_a_self_closing_non_void_element_is_the_component_shape_in_any_case(src):
    from djust._rust import render_template

    with pytest.raises(Exception) as exc:
        render_template(src, {})
    assert "removed in djust 2.0" in str(exc.value)


def test_button_tag_error_names_the_hook_replacement():
    from djust._rust import render_template

    with pytest.raises(Exception) as exc:
        render_template('<Button label="x" />', {})
    assert 'dj-hook="Button"' in str(exc.value)


@pytest.mark.parametrize(
    ("src", "needle"),
    [("<DIV>x</DIV>", "<div"), ("x<BR>y", "<br"), ("<SPAN>s</SPAN>", "<span"), ("<P>p</P>", "<p")],
)
def test_uppercase_html_element_names_still_render_as_html(src, needle):
    from djust._rust import render_template

    out = render_template(src, {})
    assert needle in out.lower()
    assert "data-react" not in out


def test_capitalized_tag_in_a_comment_block_is_ignored():
    from djust._rust import render_template

    assert render_template("{% comment %}<Button/>{% endcomment %}ok", {}) == "ok"


def test_less_than_before_an_uppercase_letter_in_text_stays_text():
    from djust._rust import render_template

    out = render_template("a<B)", {})
    assert "B)" in out


def test_nothing_in_the_package_references_react():
    pattern = re.compile(
        r"djust\.react\b|ReactMixin|react_components|register_react_component|data-react-"
        r"|JsxComponent|ReactComponent|initReactCounters"
    )
    hits = []
    roots = [PACKAGE, REPO / "crates"]
    for root in roots:
        for path in root.rglob("*"):
            if path.suffix not in {".py", ".js", ".html", ".rs"}:
                continue
            rel = path.relative_to(REPO).as_posix()
            if "/tests/" in rel or path.name == "_removed_settings.py":
                continue
            if "/static/djust/client" in rel or "/target" in rel or "node_modules" in rel:
                continue
            for number, line in enumerate(path.read_text(errors="ignore").splitlines(), 1):
                # The refusal text names what was removed (#4006).
                if pattern.search(line) and "(#4006)" not in line:
                    hits.append("%s:%d" % (rel, number))
    assert hits == []


@pytest.mark.parametrize(
    "src",
    [
        "<script>if (a<MAX && b){}</script>",
        "<script>x<Y>z</script>",
        "<!-- <Header /> -->",
        "<style>a<B >c</style>",
    ],
)
def test_capitalized_shapes_in_script_style_and_comments_still_render(src):
    from djust._rust import render_template

    assert render_template(src, {}) == src


def test_capitalized_tag_error_names_its_line():
    from djust._rust import render_template

    with pytest.raises(Exception) as exc:
        render_template("<p>a</p>\n<Widget />", {})
    assert "line 2" in str(exc.value)
