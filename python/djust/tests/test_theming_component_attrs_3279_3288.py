"""Component tags carry bindings and attributes (#3279) and the select takes a value (#3288).

Every case renders through the real ``{% theme_X %}`` tag in the Django engine,
the path a LiveView page template takes, not the template file with a
hand-built context.
"""

from __future__ import annotations

import pytest
from pathlib import Path

from django.template import engines
from django.template.loader import render_to_string
from django.utils.safestring import mark_safe

from djust.theming.templatetags.theme_components import _passthrough_attrs

pytestmark = pytest.mark.theming


def render(source: str, **ctx) -> str:
    template = engines["django"].from_string("{% load theme_components %}" + source)
    return template.render(ctx)


# ---------------------------------------------------------------------------
# #3279 — theme_button
# ---------------------------------------------------------------------------


class TestButtonBindings:
    def test_dj_click_and_value_attrs_reach_the_button(self):
        html = render(
            '{% theme_button "Advance" dj_click="advance" dj_value_id=item_id name="go" value="1" %}',
            item_id=3,
        )
        assert 'dj-click="advance"' in html
        assert 'dj-value-id="3"' in html
        assert 'name="go"' in html
        assert 'value="1"' in html
        assert html.lstrip().startswith("<button")

    def test_each_attribute_value_is_escaped(self):
        html = render('{% theme_button "x" dj_value_id=evil %}', evil='"><script>alert(1)</script>')
        assert "<script>" not in html
        assert 'dj-value-id="&quot;&gt;&lt;script&gt;alert(1)&lt;/script&gt;"' in html

    def test_true_is_a_bare_attribute_and_false_is_omitted(self):
        html = render('{% theme_button "x" disabled=True hidden=False %}')
        assert " disabled" in html
        assert "hidden" not in html

    def test_data_and_aria_prefixes_become_hyphenated_names(self):
        html = render('{% theme_button "x" data_row_id=4 aria_label="Go" %}')
        assert 'data-row-id="4"' in html
        assert 'aria-label="Go"' in html

    def test_class_id_type_and_onclick_are_unchanged(self):
        html = render('{% theme_button "x" class="extra" id="b1" type="submit" onclick="f()" %}')
        assert 'id="b1"' in html
        assert 'type="submit"' in html
        assert 'onclick="f()"' in html
        assert "extra" in html
        # Handled by the template, so not also emitted as a pass-through.
        assert html.count('id="b1"') == 1
        assert html.count('type="submit"') == 1

    def test_other_event_handler_attributes_are_refused(self):
        with pytest.raises(ValueError, match="event-handler"):
            render('{% theme_button "x" onmouseover="steal()" %}')

    def test_a_default_button_has_no_stray_attributes(self):
        html = render('{% theme_button "Go" %}')
        assert html.count("<button") == 1
        assert "href" not in html and "dj-" not in html


class TestButtonAsLink:
    def test_href_renders_an_anchor_with_the_button_classes(self):
        html = render('{% theme_button "Open" href="/records/1/" variant="secondary" %}')
        assert html.lstrip().startswith("<a ")
        assert 'href="/records/1/"' in html
        assert "btn btn-secondary btn-md" in html
        assert "<button" not in html and "</button>" not in html
        assert "</a>" in html
        assert "type=" not in html

    def test_an_anchor_carries_bindings_too(self):
        html = render('{% theme_button "Open" href="/x/" dj_navigate=True target="_blank" %}')
        assert "dj-navigate" in html
        assert 'target="_blank"' in html

    def test_element_a_without_href_is_an_anchor(self):
        html = render('{% theme_button "Open" element="a" dj_click="open" %}')
        assert html.lstrip().startswith("<a ")
        assert "href" not in html

    def test_href_with_element_button_is_refused(self):
        with pytest.raises(ValueError, match="href"):
            render('{% theme_button "x" href="/x/" element="button" %}')

    def test_an_unknown_element_is_refused(self):
        with pytest.raises(ValueError, match="element"):
            render('{% theme_button "x" element="div" %}')

    @pytest.mark.parametrize(
        "url",
        [
            "javascript:alert(1)",
            "  JavaScript:alert(1)",
            "java\tscript:alert(1)",
            "data:text/html,x",
        ],
    )
    def test_script_urls_are_refused(self, url):
        with pytest.raises(ValueError, match="refuses"):
            render('{% theme_button "x" href=url %}', url=url)

    def test_a_relative_and_an_https_url_are_fine(self):
        assert 'href="?page=2"' in render('{% theme_button "x" href="?page=2" %}')
        # A template literal is safe text to Django; a variable is escaped.
        assert 'href="https://example.org/a?b=1&amp;c=2"' in render(
            '{% theme_button "x" href=url %}', url="https://example.org/a?b=1&c=2"
        )

    @pytest.mark.parametrize("theme", ["fluent", "ios", "material", "neo_brutalist", "playful"])
    @pytest.mark.parametrize("tag", ["a", "button"])
    def test_every_theme_override_supports_both_elements(self, theme, tag):
        html = render_to_string(
            f"djust_theming/themes/{theme}/components/button.html",
            {
                "text": "Go",
                "variant": "primary",
                "size": "md",
                "tag": tag,
                "href": "/x/" if tag == "a" else None,
                "attrs": {"type": "submit"},
                "extra_attrs": mark_safe('dj-click="go"'),
                "css_prefix": "",
            },
        )
        assert 'dj-click="go"' in html
        if tag == "a":
            assert 'href="/x/"' in html and "<button" not in html and "</a>" in html
        else:
            assert html.count("<button") == 1 and "<a " not in html and "</button>" in html


class TestPassthroughHelper:
    @pytest.mark.parametrize("name", ['a"b', "a b", "1abc", "a=b", "a>b", "a/b", ""])
    def test_a_name_that_is_not_an_attribute_identifier_is_refused(self, name):
        with pytest.raises(ValueError, match="valid HTML attribute name"):
            _passthrough_attrs({name: "v"}, skip=())

    def test_skipped_and_empty_values_are_dropped(self):
        assert _passthrough_attrs({"class": "c", "a": None, "b": False, "dj_x": 1}, ("class",)) == (
            'dj-x="1"'
        )

    def test_on_prefixed_names_are_refused(self):
        with pytest.raises(ValueError, match="event-handler"):
            _passthrough_attrs({"onfocus": "x()"}, skip=())

    @pytest.mark.parametrize("name", ["href", "src", "action", "formaction"])
    def test_every_url_attribute_is_checked(self, name):
        with pytest.raises(ValueError, match="refuses"):
            _passthrough_attrs({name: "javascript:x()"}, skip=())


# ---------------------------------------------------------------------------
# #3279 — id independent of name; #3279 — select passthrough
# ---------------------------------------------------------------------------


class TestIndependentIds:
    def test_input_id_defaults_to_name(self):
        html = render('{% theme_input "note" label="Note" %}')
        assert 'id="note"' in html and 'for="note"' in html

    def test_input_id_can_differ_from_name_and_the_label_follows(self):
        html = render('{% theme_input "note" label="Note" id="note-7" %}')
        assert 'name="note"' in html
        assert 'id="note-7"' in html and 'for="note-7"' in html
        assert 'id="note"' not in html

    def test_repeated_rows_get_unique_ids(self):
        html = "".join(
            render('{% theme_input "note" id=row_id %}', row_id=f"note-{i}") for i in (1, 2, 3)
        )
        ids = [part.split('"')[0] for part in html.split(' id="')[1:]]
        assert ids == ["note-1", "note-2", "note-3"]

    def test_select_and_textarea_take_an_id_too(self):
        select = render('{% theme_select "s" label="S" options=opts id="s-2" %}', opts=[])
        assert 'id="s-2"' in select and 'for="s-2"' in select
        area = render('{% theme_textarea "t" label="T" id="t-2" %}')
        assert 'id="t-2"' in area and 'for="t-2"' in area

    def test_input_dj_bindings_still_pass_through(self):
        assert 'dj-input="search"' in render('{% theme_input "q" dj_input="search" %}')


OPTS = [
    {"value": "a", "label": "Alpha"},
    {"value": 2, "label": "Beta"},
    {"value": "c", "label": "Gamma"},
]


def selected_values(html: str) -> list[str]:
    """The ``value`` of every ``<option ... selected>`` in ``html``."""
    out = []
    for chunk in html.split("<option")[1:]:
        tag = chunk.split(">")[0]
        if " selected" in tag:
            out.append(tag.split('value="')[1].split('"')[0])
    return out


class TestSelectValue:
    def test_value_marks_the_matching_option_selected(self):
        html = render('{% theme_select "f" options=opts value="c" %}', opts=OPTS)
        assert selected_values(html) == ["c"]

    def test_the_comparison_is_by_string(self):
        # The option carries the int 2; a form or URL hands back "2".
        html = render('{% theme_select "f" options=opts value="2" %}', opts=OPTS)
        assert selected_values(html) == ["2"]
        html = render('{% theme_select "f" options=opts value=2 %}', opts=OPTS)
        assert selected_values(html) == ["2"]

    def test_value_is_not_rendered_as_an_attribute_of_the_select(self):
        html = render('{% theme_select "f" options=opts value="c" %}', opts=OPTS)
        select_tag = html.split("<select")[1].split(">")[0]
        assert "value=" not in select_tag

    def test_an_option_dicts_own_selected_still_works(self):
        opts = [{"value": "a", "label": "A"}, {"value": "b", "label": "B", "selected": True}]
        assert selected_values(render('{% theme_select "f" options=opts %}', opts=opts)) == ["b"]

    def test_the_placeholder_is_selected_only_when_nothing_else_is(self):
        none = render('{% theme_select "f" options=opts placeholder="Pick" %}', opts=OPTS)
        assert selected_values(none) == [""]
        chosen = render(
            '{% theme_select "f" options=opts placeholder="Pick" value="a" %}', opts=OPTS
        )
        assert selected_values(chosen) == ["a"]

    def test_a_value_matching_nothing_selects_nothing_extra(self):
        html = render('{% theme_select "f" options=opts value="zzz" %}', opts=OPTS)
        assert selected_values(html) == []

    def test_the_callers_option_dicts_are_not_mutated(self):
        opts = [{"value": "a", "label": "A"}]
        render('{% theme_select "f" options=opts value="a" %}', opts=opts)
        assert opts == [{"value": "a", "label": "A"}]

    def test_an_edit_form_reopens_on_the_stored_choice(self):
        """The reported scenario: ``value=current`` from view state."""
        html = render(
            '{% theme_select "category" options=opts value=current %}', opts=OPTS, current="c"
        )
        assert selected_values(html) == ["c"]


class TestSelectPassthrough:
    def test_dj_change_and_aria_reach_the_select(self):
        html = render(
            '{% theme_select "f" options=opts dj_change="set_f" aria_describedby="help" required=True %}',
            opts=OPTS,
        )
        select_tag = html.split("<select")[1].split(">")[0]
        assert 'dj-change="set_f"' in select_tag
        assert 'aria-describedby="help"' in select_tag
        assert select_tag.count("required") == 1

    def test_event_handler_attributes_are_refused(self):
        with pytest.raises(ValueError, match="event-handler"):
            render('{% theme_select "f" options=opts onchange="x()" %}', opts=OPTS)

    def test_a_plain_select_has_no_stray_attributes(self):
        html = render('{% theme_select "f" options=opts %}', opts=OPTS)
        assert "dj-" not in html


class TestReviewFollowUps:
    def test_a_trailing_newline_is_not_a_valid_attribute_name(self):
        with pytest.raises(ValueError, match="valid HTML attribute name"):
            _passthrough_attrs({"x\n": "v"}, skip=())

    def test_a_disabled_link_button_is_inert_and_says_so(self):
        html = render('{% theme_button "Go" href="/x/" disabled=True %}')
        assert html.lstrip().startswith("<a ")
        assert 'aria-disabled="true"' in html and 'tabindex="-1"' in html
        assert "href" not in html and "disabled>" not in html

    def test_type_on_a_link_button_is_refused_not_dropped(self):
        with pytest.raises(ValueError, match="type="):
            render('{% theme_button "Go" href="/x/" type="submit" %}')

    def test_multiple_select_marks_every_match_of_a_list_value(self):
        html = render(
            '{% theme_select "f" options=opts value=chosen multiple=True %}',
            opts=OPTS,
            chosen=["a", 2],
        )
        assert selected_values(html) == ["a", "2"]
        assert " multiple" in html.split("<select")[1].split(">")[0]

    def test_tuple_options_render_value_and_label_not_none(self):
        html = render(
            '{% theme_select "f" options=opts value="b" %}', opts=[("a", "Alpha"), ("b", "Beta")]
        )
        assert "None" not in html
        assert '<option value="b" selected>Beta</option>' in html.replace("  ", " ").replace(
            " >", ">"
        )
        assert 'value="a"' in html

    def test_an_option_without_a_label_prints_nothing_not_none(self):
        html = render('{% theme_select "f" options=opts %}', opts=[{"value": "a"}])
        assert "None" not in html

    @pytest.mark.parametrize("name", ["input", "select", "textarea"])
    def test_the_field_contracts_name_the_new_context_vars(self, name):
        from djust.theming.contracts import get_contract

        names = {v.name for v in get_contract(name).optional_context}
        assert "field_id" in names
        if name == "select":
            assert {"extra_attrs", "has_selected_option"} <= names
        if name == "input":
            assert "extra_attrs" in names


class TestOverridesThatIgnoreThePassthrough:
    def test_check_compat_warns_about_a_button_override_without_extra_attrs(self, tmp_path):
        from djust.theming.compat import check_theme_compat

        (tmp_path / "components").mkdir()
        (tmp_path / "components" / "button.html").write_text("<button>{{ text }}</button>\n")
        warnings = [i for i in check_theme_compat(tmp_path) if i.severity == "warning"]
        missing = {w.message.split("'")[1] for w in warnings}
        assert missing == {"extra_attrs", "href", "tag"}

    def test_the_shipped_button_template_passes_that_check(self, tmp_path):
        from djust.theming.compat import check_theme_compat

        shipped = (
            Path(__file__).resolve().parents[1]
            / "theming/templates/djust_theming/components/button.html"
        )
        (tmp_path / "components").mkdir()
        (tmp_path / "components" / "button.html").write_text(shipped.read_text())
        assert [i for i in check_theme_compat(tmp_path) if i.severity == "warning"] == []
