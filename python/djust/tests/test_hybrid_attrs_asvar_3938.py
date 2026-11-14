"""A ``{% blocktranslate ... asvar name %}`` variable printed in an attribute is escaped exactly
once (#3938).

Django stores the block's output as a marked-safe string: a context variable escaped once, a
marked-safe one not at all. The hybrid path ends every attribute slot in ``|force_escape``, so a
plain ``A & B`` printed ``A &amp;amp; B`` (escaped twice), while dropping ``|force_escape`` would
let a marked-safe value out of the attribute. The rewrite adds a twin of the block (each
variable it reads made ``|force_escape`` in its own ``with`` clause, stored under a synthetic
name) and points the slot at it. The twin runs inside ``{% autoescape off %}`` so it stores
plain text, and the slot prints it with ``|force_escape``: escaped exactly once, the block's own
literal text included. That text (the msgid, or a translator's msgstr) is where this is stricter
than Django, which prints it as it is: a catalog is an input. Every case is compared with Django
rendering the same template.
"""

from __future__ import annotations

from html.parser import HTMLParser

import pytest
from django.template import Context, Template
from django.utils.safestring import mark_safe

from djust.components import _hybrid_attrs as hybrid
from djust.components.base import Component

pytestmark = pytest.mark.components

LOAD = "{% load i18n %}"
BLOCK = "{% blocktranslate asvar label %}Delete {{ item }}{% endblocktranslate %}"
#: Plain values a template prints: Django's bytes are the expected bytes.
PLAIN = ["A & B", 'say "hi"', "<b>x</b>", "it's", "plain"]
#: Ends a double- and a single-quoted attribute alike when printed as it is.
BREAKOUT = mark_safe("'\"><img src=x onerror=alert(1)>")


@pytest.fixture(autouse=True)
def _bootstrap(settings):
    settings.DJUST_CONFIG = {"css_framework": "bootstrap5"}


def _draw(template: str, **context) -> str:
    class Box(Component):
        pass

    Box.template = template
    Box.get_context_data = lambda self: dict(context)  # type: ignore[method-assign]
    return str(Box().render())


def _django(template: str, **context) -> str:
    return Template(template).render(Context(context))


def _starttags(markup: str) -> list[tuple[str, dict[str, str | None]]]:
    found: list[tuple[str, dict[str, str | None]]] = []

    class P(HTMLParser):
        def handle_starttag(self, tag, attrs):
            found.append((tag, dict(attrs)))

    P(convert_charrefs=True).feed(markup)
    return found


SINKS = {
    "attribute": '<button aria-label="{{ label }}">x</button>',
    "single-quoted": "<button aria-label='{{ label }}'>x</button>",
    "textarea": "<textarea>{{ label }}</textarea>",
    "title": "<title>{{ label }}</title>",
}


class TestEscapedOnce:
    @pytest.mark.parametrize("value", PLAIN)
    @pytest.mark.parametrize("sink", list(SINKS))
    def test_a_plain_value_prints_what_django_prints(self, sink: str, value: str) -> None:
        template = LOAD + BLOCK + SINKS[sink]
        assert _draw(template, item=value) == _django(template, item=value)

    @pytest.mark.parametrize("sink", ["attribute", "single-quoted"])
    def test_a_marked_safe_value_stays_inside_the_attribute(self, sink: str) -> None:
        """Django lets it out; the twin escapes it once."""
        template = LOAD + BLOCK + SINKS[sink]
        assert [t for t, _a in _starttags(_django(template, item=BREAKOUT))] == ["button", "img"]
        tags = _starttags(_draw(template, item=BREAKOUT))
        assert [t for t, _a in tags] == ["button"], tags
        assert tags[0][1]["aria-label"] == "Delete " + str(BREAKOUT)

    @pytest.mark.parametrize("value", PLAIN)
    def test_a_with_argument_and_a_count(self, value: str) -> None:
        template = (
            LOAD
            + "{% blocktranslate asvar label count c=num with n=item %}"
            + "{{ c }} {{ n }}{% plural %}{{ c }} {{ n }}s{% endblocktranslate %}"
            + '<b title="{{ label }}">'
        )
        for num in (1, 2):
            assert _draw(template, item=value, num=num) == _django(template, item=value, num=num)
        tags = _starttags(_draw(template, item=BREAKOUT, num=2))
        assert [t for t, _a in tags] == ["b"], tags

    def test_a_text_slot_and_a_filtered_slot_are_left_as_django_prints_them(self) -> None:
        """The author's block is not touched: other uses of the variable are Django's own."""
        template = LOAD + BLOCK + '<span>{{ label }}</span><i title="{{ label|upper }}">'
        for value in [*PLAIN, BREAKOUT]:
            assert _draw(template, item=value).startswith(
                _django(template, item=value).split("<i ")[0]
            )
        assert _draw(template, item="A & B") == _django(template, item="A & B")

    def test_the_twin_is_placed_after_the_block_and_read_by_the_slot(self) -> None:
        rewritten = hybrid.plan(LOAD + BLOCK + SINKS["attribute"]).template
        assert rewritten.index(BLOCK) < rewritten.index("asvar djust_asvar_")
        assert 'aria-label="{{ djust_asvar_' in rewritten
        assert "{% autoescape off %}{% blocktranslate asvar djust_asvar_" in rewritten
        assert "|force_escape }}" in rewritten


class TestTheBlocksOwnText:
    """The block's literal text is escaped once too (stricter than Django: a catalog is an
    input)."""

    @pytest.mark.parametrize("sink", list(SINKS))
    def test_quotes_and_markup_in_the_body(self, sink: str) -> None:
        body = 'Say "hi"><b>it\'s</b>'
        template = LOAD + "{% blocktranslate asvar label %}" + body + "{% endblocktranslate %}"
        out = _draw(template + SINKS[sink])
        tags = [t for t, _a in _starttags(out)]
        assert "b" not in tags, out
        assert "&quot;hi&quot;&gt;&lt;b&gt;" in out, out

    def test_the_textarea_cannot_be_ended_by_the_body(self) -> None:
        template = (
            LOAD
            + "{% blocktranslate asvar label %}</textarea><script>1</script>"
            + "{% endblocktranslate %}"
            + SINKS["textarea"]
        )
        assert "<script>" in _django(template)
        out = _draw(template)
        assert out == "<textarea>&lt;/textarea&gt;&lt;script&gt;1&lt;/script&gt;</textarea>", out

    def test_a_catalog_msgstr_with_markup(self, monkeypatch) -> None:
        from django.utils import translation

        catalog = {"Delete %(item)s": 'Remove "%(item)s"><img src=x onerror=alert(1)>'}
        real = translation.gettext
        monkeypatch.setattr(translation, "gettext", lambda m: catalog.get(m, real(m)))
        template = LOAD + BLOCK + SINKS["attribute"]
        assert "<img" in _django(template, item="A & B")
        tags = _starttags(_draw(template, item="A & B"))
        assert [t for t, _a in tags] == ["button"], tags
        assert tags[0][1]["aria-label"] == 'Remove "A & B"><img src=x onerror=alert(1)>'

    def test_a_variable_with_an_ampersand_is_escaped_once(self) -> None:
        template = LOAD + BLOCK + SINKS["attribute"]
        out = _draw(template, item="A & B")
        assert out == '<button aria-label="Delete A &amp; B">x</button>', out


class TestWhereTheTwinIsNotUsed:
    """Where the slot may not read that block's value, it keeps ``|force_escape``: escaped twice,
    never let out. Each case also proves the slot reads what Django's would."""

    @pytest.mark.parametrize("show", [False, True])
    def test_a_block_inside_a_conditional(self, show: bool) -> None:
        template = (
            LOAD
            + "{% if show %}"
            + BLOCK
            + "{% endif %}"
            + '<button aria-label="{{ label }}">x</button>'
        )
        assert "djust_asvar_" not in hybrid.plan(template).template
        out = _draw(template, show=show, item="A", label="from the context")
        expected = "Delete A" if show else "from the context"
        assert _starttags(out)[0][1]["aria-label"] == expected, out

    def test_a_slot_before_the_block_reads_the_context(self) -> None:
        template = LOAD + '<button aria-label="{{ label }}">x</button>' + BLOCK
        out = _draw(template, item="A", label=BREAKOUT)
        tags = _starttags(out)
        assert [t for t, _a in tags] == ["button"], out
        assert tags[0][1]["aria-label"] == str(BREAKOUT)

    def test_two_blocks_with_the_same_name(self) -> None:
        template = LOAD + BLOCK + BLOCK.replace("Delete", "Remove") + SINKS["attribute"]
        assert "djust_asvar_" not in hybrid.plan(template).template
        out = _draw(template, item="A")
        assert _starttags(out)[0][1]["aria-label"] == "Remove A", out

    def test_a_name_another_tag_binds(self) -> None:
        template = (
            LOAD
            + BLOCK
            + '{% with label="x" %}<b title="{{ label }}">{% endwith %}'
            + SINKS["attribute"]
        )
        assert "djust_asvar_" not in hybrid.plan(template).template

    def test_a_name_a_silent_cycle_binds(self) -> None:
        """``{% cycle ... as x silent %}`` binds ``x``: the slot reads the cycle, as Django's does."""
        template = (
            LOAD
            + "{% blocktranslate asvar x %}D{% endblocktranslate %}"
            + '{% cycle "a" v as x silent %}{% cycle x %}<b title="{{ x }}">'
        )
        assert "djust_asvar_" not in hybrid.plan(template).template
        assert _draw(template, v="b") == _django(template, v="b")

    def test_a_legacy_with_form_is_not_rewritten(self) -> None:
        template = (
            LOAD
            + "{% blocktrans with item as n asvar label %}{{ n }}{% endblocktrans %}"
            + SINKS["attribute"]
        )
        assert "djust_asvar_" not in hybrid.plan(template).template
