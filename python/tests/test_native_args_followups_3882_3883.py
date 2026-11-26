"""Follow-ups from the review of the #3882 / #3883 fix.

* A parameter that is numeric by contract still takes a numeric STRING (``?page=2`` read from
  ``request.GET``): a resolved string now arrives as the string it is, so each such handler
  converts its own parameter (``rust_handlers._number``), with an error naming the tag for text
  that is not a number. A conversion of the parameter's value, never a context lookup.
* ``_parse_args``'s text path, which a template no longer reaches for a bare word (the engine
  hands every resolved scalar over typed), still never looks a word up for a direct caller.
* ``{% dj_suspense await=v %}``: the awaited names are the template's own words.
* ``TagHandler`` keyword values pass through as text: not split again, padding kept.
"""

from __future__ import annotations

import pytest

from djust.components import rust_handlers
from djust.tests.test_components_csp_aria import _rust
from djust.template_tags import TagHandler

# --------------------------------------------------------------------------- #
# 1. Numeric parameters take a numeric string


def _same(src_str: str, src_int: str, ctx_str: dict, ctx_int: dict) -> None:
    assert _rust(src_str, ctx_str) == _rust(src_int, ctx_int)


def test_pagination_takes_page_numbers_as_strings() -> None:
    _same(
        "{% pagination page=p total_pages=t %}",
        "{% pagination page=p total_pages=t %}",
        {"p": "2", "t": "5"},
        {"p": 2, "t": 5},
    )
    html = _rust("{% pagination page=p total_pages=t %}", {"p": "2", "t": "5"})
    # page 2 is the current one of five (the BEM markup says it with aria-current, #4331)
    assert 'aria-current="page" aria-label="Page 2"' in html, html
    assert 'aria-label="Page 5"' in html and 'aria-label="Page 6"' not in html, html


def test_pagination_refuses_text_that_is_not_a_number_by_name() -> None:
    with pytest.raises(Exception, match=r"pagination.*page= takes a number"):
        _rust("{% pagination page=p total_pages=5 %}", {"p": "two"})


def test_progress_takes_its_value_as_a_string() -> None:
    _same("{% progress value=v %}", "{% progress value=v %}", {"v": "40"}, {"v": 40})
    _same("{% progress value=v %}", "{% progress value=v %}", {"v": "2.5"}, {"v": 2.5})


def test_progress_refuses_text_that_is_not_a_number_by_name() -> None:
    with pytest.raises(Exception, match=r"progress.*value= takes a number"):
        _rust("{% progress value=v %}", {"v": "lots"})


def test_form_array_takes_min_and_max_as_strings() -> None:
    src = '{% form_array name="x" min=a max=b %}{% endform_array %}'
    _same(src, src, {"a": "2", "b": "2.5"}, {"a": 2, "b": 2.5})


def test_filter_search_takes_its_debounce_as_a_string() -> None:
    src = '{% filter_bar %}{% filter_search name="q" debounce=d %}{% endfilter_bar %}'
    _same(src, src, {"d": "250"}, {"d": 250})
    _same(src, src, {"d": "2.5"}, {"d": 2.5})


def test_filter_search_refuses_a_debounce_that_is_not_a_number_by_name() -> None:
    src = '{% filter_bar %}{% filter_search name="q" debounce=d %}{% endfilter_bar %}'
    with pytest.raises(Exception, match=r"filter_search.*debounce= takes a number"):
        _rust(src, {"d": "soon"})


@pytest.mark.parametrize(
    "param", ["search_debounce", "page", "frozen_left", "frozen_right", "virtual_row_height"]
)
def test_data_table_takes_its_numeric_parameters_as_strings(param: str) -> None:
    cols = [{"key": "id", "label": "ID"}]
    rows = [{"id": 1}]
    src = "{%% data_table rows=rows columns=cols paginate=True total_pages=9 %s=n %%}" % param
    _same(
        src, src, {"rows": rows, "cols": cols, "n": "3.5"}, {"rows": rows, "cols": cols, "n": 3.5}
    )


def test_number_is_a_conversion_never_a_lookup() -> None:
    assert rust_handlers._number("7") == 7
    assert rust_handlers._number("2.5") == 2.5
    assert rust_handlers._number(4) == 4
    with pytest.raises(rust_handlers.NotANumber):
        rust_handlers._number("seven")
    with pytest.raises(ValueError, match="pagination"):
        rust_handlers._number("page", "pagination", "page")


# --------------------------------------------------------------------------- #
# 4. The text path, which a direct caller still reaches


def test_a_direct_call_never_looks_a_bare_word_up_in_the_context() -> None:
    """The original #3882 line was ``context.get(val, val)`` at the end of ``_parse_args``. A
    template no longer reaches that branch for a bare word (every resolved scalar arrives on the
    typed channel), so the guard is a direct call: a handler called with the token itself."""
    html = rust_handlers.BadgeHandler().render(["label=victim"], {"victim": "PLANTED-3882"})
    assert "PLANTED-3882" not in html and ">victim</span>" in html, html
    assert rust_handlers._parse_args(["label=victim"], {"victim": "S"}) == {"label": "victim"}


# --------------------------------------------------------------------------- #
# 6. dj_suspense: the awaited names come from the template


def _suspense(src: str) -> str:
    """Through the template backend, which hands the handler the context's Python objects (an
    ``AsyncResult`` stays one)."""
    from djust.async_result import AsyncResult
    from djust.template.backend import DjustTemplateBackend

    rust_handlers.register_with_rust_engine()
    engine = DjustTemplateBackend(
        {"NAME": "suspense-3882", "DIRS": [], "APP_DIRS": True, "OPTIONS": {}}
    )
    failed = AsyncResult.errored(RuntimeError("PRIVATE-FAILURE-3882"))
    return str(engine.from_string(src).render({"private_job": failed, "names": "private_job"}))


def test_a_literal_await_still_reports_the_failure_it_names() -> None:
    """Non-regression: the template's own ``await="private_job"`` gates on that result."""
    html = _suspense('{% dj_suspense await="private_job" %}body{% enddj_suspense %}')
    assert "djust-suspense-error" in html and "body" not in html, html


def test_a_variable_holding_names_is_not_read_as_names() -> None:
    html = _suspense("{% dj_suspense await=names %}body{% enddj_suspense %}")
    assert "PRIVATE-FAILURE-3882" not in html, html
    assert "djust-suspense-fallback" in html, html


# --------------------------------------------------------------------------- #
# 7. TagHandler keyword values pass through as text


class _Probe(TagHandler):
    def render(self, args, context):  # pragma: no cover - not exercised
        return ""


@pytest.mark.parametrize(
    "arg, expected",
    [
        ("name=label=other_var", ("name", "label=other_var")),
        ("name= padded ", ("name", " padded ")),
        ("name=007", ("name", "007")),
        ("name=5", ("name", 5)),
        ('name="lit"', ("name", "lit")),
    ],
)
def test_a_keyword_value_is_read_once(arg: str, expected: tuple) -> None:
    assert _Probe()._resolve_arg(arg, {"label": "PLANTED"}) == expected


def test_a_resolved_keyword_value_shaped_like_a_keyword_renders_as_text() -> None:
    html = _rust("{% djust_pwa_manifest name=v %}", {"v": "label=other_var", "other_var": "S"})
    assert "('label'" not in html, html


def test_a_padded_positional_value_keeps_its_padding() -> None:
    assert _Probe()._resolve_arg("  x  ", {}) == "  x  "


# --------------------------------------------------------------------------- #
# Second review: hostile numeric text, the page window, suspense order and fallback


HOSTILE_NUMBERS = ["inf", "-inf", "nan", "1e999", "1_000", "٢", "--5", "9" * 4301, "0x10"]


@pytest.mark.parametrize("text", HOSTILE_NUMBERS, ids=lambda t: t[:12])
def test_number_refuses_what_is_not_a_plain_decimal(text: str) -> None:
    with pytest.raises(rust_handlers.NotANumber):
        rust_handlers._number(text, "pagination", "page")


def test_number_refuses_a_float_that_is_not_finite() -> None:
    for value in (float("inf"), float("nan")):
        with pytest.raises(rust_handlers.NotANumber):
            rust_handlers._number(value)


@pytest.mark.parametrize("text", HOSTILE_NUMBERS, ids=lambda t: t[:12])
def test_a_handler_with_a_default_falls_back_for_hostile_text(text: str) -> None:
    cols, rows = [{"key": "id", "label": "ID"}], [{"id": 1}]
    src = "{% data_table rows=rows columns=cols paginate=True total_pages=9 page=p %}"
    assert _rust(src, {"rows": rows, "cols": cols, "p": text}) == _rust(
        src, {"rows": rows, "cols": cols, "p": "nope"}
    )
    fa = '{% form_array name="x" min=p %}{% endform_array %}'
    assert _rust(fa, {"p": text}) == _rust(fa, {"p": "nope"})


@pytest.mark.parametrize("text", ["inf", "nan", "1e999", "9" * 4301])
def test_pagination_names_the_parameter_for_hostile_text(text: str) -> None:
    with pytest.raises(Exception, match=r"pagination.*page= takes a number"):
        _rust("{% pagination page=p total_pages=5 %}", {"p": text})


def _old_pagination_pages(page, total_pages):
    """The walk ``pagination_pages`` replaced, for the equivalence check."""
    pages: list = []
    for p in range(1, total_pages + 1):
        if p == 1 or p == total_pages or abs(p - page) <= 2:
            pages.append(p)
        elif pages and pages[-1] != "...":
            pages.append("...")
    return pages


def test_the_page_window_is_the_one_the_walk_gave() -> None:
    from djust.components.templatetags.djust_components import pagination_pages

    pages = [-3, 0, 1, 2, 3, 4, 5, 6, 9, 10, 11, 14, 2.5, 7.25, float("nan"), float("inf"), True]
    for total in range(-1, 16):
        for page in pages:
            assert pagination_pages(page, total) == _old_pagination_pages(page, total), (
                page,
                total,
            )
    for total in (0, 1, 2):
        assert pagination_pages("x", total) == _old_pagination_pages("x", total)
    with pytest.raises(TypeError):
        pagination_pages("x", 5)
    with pytest.raises(TypeError):
        pagination_pages(1, "5")


def _bounded(code: str) -> str:
    """Run ``code`` in a child interpreter with a deadline, so a runaway cannot hang the suite."""
    import os
    import subprocess
    import sys

    env = dict(os.environ, PYTHONPATH=os.pathsep.join(p for p in sys.path if p))
    env.setdefault("DJANGO_SETTINGS_MODULE", "demo_project.settings")
    proc = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60, env=env
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    return proc.stdout


def test_a_huge_total_pages_is_answered_without_walking_it() -> None:
    out = _bounded(
        "import django\n"
        "django.setup()\n"
        "from djust.components.templatetags.djust_components import pagination_pages\n"
        "print(pagination_pages(5, 10 ** 30))\n"
    )
    assert out.strip() == "[1, '...', 3, 4, 5, 6, 7, '...', %d]" % 10**30, out


def test_the_native_pagination_refuses_a_30_digit_total_quickly() -> None:
    with pytest.raises(Exception, match=r"total_pages= takes a number"):
        _rust("{% pagination page=1 total_pages=t %}", {"t": "9" * 30})


def test_a_resolved_empty_await_does_not_render_the_body() -> None:
    from djust.async_result import AsyncResult
    from djust.template.backend import DjustTemplateBackend

    rust_handlers.register_with_rust_engine()
    engine = DjustTemplateBackend({"NAME": "s3882b", "DIRS": [], "APP_DIRS": True, "OPTIONS": {}})
    html = str(
        engine.from_string("{% dj_suspense await=e %}BODY{% enddj_suspense %}").render(
            {"e": "", "job": AsyncResult.pending()}
        )
    )
    assert "BODY" not in html and "djust-suspense-fallback" in html, html


def test_a_resolved_fallback_is_not_rendered_as_a_template_name() -> None:
    from djust.template.backend import DjustTemplateBackend

    rust_handlers.register_with_rust_engine()
    engine = DjustTemplateBackend({"NAME": "s3882c", "DIRS": [], "APP_DIRS": True, "OPTIONS": {}})
    src = '{% dj_suspense await="job" fallback=f %}BODY{% enddj_suspense %}'
    html = str(engine.from_string(src).render({"f": "djust_components/markdown_controls.html"}))
    assert "dj-markdown-controls" not in html and "djust-suspense-fallback" in html, html
    literal = '{% dj_suspense await="job" fallback="djust_components/markdown_controls.html" %}'
    html = str(engine.from_string(literal + "BODY{% enddj_suspense %}").render({}))
    assert "dj-markdown-controls" in html, html


@pytest.mark.parametrize("text", ["--5", "²", "9" * 4301, "1_000", "٢"], ids=str.__len__)
def test_a_tag_handler_value_that_is_not_a_plain_integer_is_text(text: str) -> None:
    assert _Probe()._resolve_arg(text, {}) == text
    assert _Probe()._resolve_arg("k=" + text, {}) == ("k", text)
