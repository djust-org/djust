"""Table anatomy and CSS extension points across all three renderers (#2792)."""

from html.parser import HTMLParser

import pytest

from djust.components.data.table import TableComponent


class TableHTML(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.elements = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))

    def attrs(self, tag):
        return [attrs for name, attrs in self.elements if name == tag]


@pytest.fixture(params=["bootstrap", "tailwind", "plain"])
def render(request):
    return lambda table: getattr(table, "_render_" + request.param)()


def make_table(**kwargs):
    return TableComponent(
        columns=[
            {"key": "name", "label": "Name", "sortable": True},
            {"key": "count", "label": "Count"},
        ],
        rows=[{"id": 1, "name": "Ada", "count": 3}],
        **kwargs,
    )


def test_table_anatomy_and_custom_classes(render):
    table = make_table(
        table_class="align-middle table-dark",
        thead_class="table-light",
        tbody_class="body-custom",
        tfoot_class="footer-custom",
        caption="People & counts",
        caption_class="caption-top",
        footer={"name": "Total", "count": 3},
        selectable=True,
    )
    html = render(table)
    parsed = TableHTML(html)
    assert "align-middle" in parsed.attrs("table")[0]["class"].split()
    assert "table-light" in parsed.attrs("thead")[0]["class"].split()
    assert "body-custom" in parsed.attrs("tbody")[0]["class"].split()
    assert parsed.attrs("tfoot")[0]["class"] == "footer-custom"
    assert '<caption class="caption-top">People &amp; counts</caption>' in html
    assert (
        html.index("<caption") < html.index("<thead") < html.index("<tbody") < html.index("<tfoot")
    )
    footer = html.split("<tfoot", 1)[1]
    assert len(TableHTML("<tfoot" + footer).attrs("td")) == 3
    assert "Total" in footer and ">3</td>" in footer
    assert "checkbox" not in footer
    assert 'dj-click="sort_by"' in html
    assert "data-component-id=" in html
    context = table.get_context()
    assert context["caption"] == "People & counts"
    assert context["footer"] == {"name": "Total", "count": 3}


def test_default_has_no_caption_or_footer(render):
    html = render(make_table())
    assert "<caption" not in html and "<tfoot" not in html
    assert "Ada" in html


def test_new_text_and_class_options_are_escaped(render):
    attack = '"><script>alert(1)</script>'
    html = render(
        make_table(
            caption=attack,
            footer={"name": attack},
            table_class=attack,
            thead_class=attack,
            tbody_class=attack,
            tfoot_class=attack,
            caption_class=attack,
        )
    )
    assert "<script" not in html
    assert "&lt;script&gt;" in html
    parsed = TableHTML(html)
    assert len(parsed.attrs("table")) == 1
    for tag in ("table", "thead", "tbody", "tfoot", "caption"):
        assert set(parsed.attrs(tag)[0]) == {"class"}


def test_footer_does_not_enter_filter_sort_or_selection(render):
    table = make_table(footer={"name": "Total", "count": 99}, selectable=True, filterable=True)
    table.filter_rows(value="no matching rows")
    table.toggle_all(value=True)
    html = render(table)
    assert table.selected_rows == []
    assert "Ada" not in html
    assert "Total" in html and ">99</td>" in html
