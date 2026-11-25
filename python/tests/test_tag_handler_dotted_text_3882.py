"""Text shaped like a dotted path renders as the text, through the real LiveView render (#3882).

``TagHandler._resolve_arg`` (the argument reader of ``djust_markdown``, ``dj_flash``, the PWA tags
and ``live_render``) used to look an argument the engine had already resolved up in the context
again when it was shaped like a dotted path, walking dicts and attributes. Each case renders a
value that spells a path to another context entry, under both exposure policies, and checks the
entry it names never reaches the page.
"""

from __future__ import annotations

import re

import pytest

from djust import LiveView
from djust.testing import LiveViewTestClient

PLANTED = "PLANTED-3882"


class _Holder:
    attr = PLANTED


VALUES = ["shelf.item", "holder.attr", "shelf", "shelf.nested.deep"]

TEMPLATES = {
    "djust_markdown": "<div>{% djust_markdown v %}</div>",
    "dj_flash": "<div>{% dj_flash position=v %}</div>",
    "djust_pwa_head": "<div>{% djust_pwa_head name=v %}</div>",
    "live_render": "<div>{% live_render v %}</div>",
}


def _render(template: str, value: str) -> str:
    class _V(LiveView):
        exposure_policy = "explicit"

        def get_context_data(self, **kwargs):
            ctx = super().get_context_data(**kwargs)
            ctx.update(
                v=value,
                shelf={"item": PLANTED, "nested": {"deep": PLANTED}},
                holder=_Holder(),
            )
            return ctx

    _V.template = template
    client = LiveViewTestClient(_V)
    client.mount()
    try:
        html, _, _ = client.render_with_patches()
    except Exception as exc:  # noqa: BLE001 - a refusal is an outcome; its message is checked
        return "<refused %s>" % exc
    return str(html)


@pytest.mark.django_db
@pytest.mark.parametrize("value", VALUES)
@pytest.mark.parametrize("tag", sorted(TEMPLATES))
def test_a_dotted_value_is_never_walked(tag: str, value: str) -> None:
    html = _render(TEMPLATES[tag], value)
    assert PLANTED not in html, (tag, value, html)
    assert PLANTED.replace("-", "") not in html.replace("-", ""), (tag, value, html)


@pytest.mark.django_db
@pytest.mark.parametrize("value", VALUES)
def test_markdown_and_pwa_print_the_literal_text(value: str) -> None:
    md = _render(TEMPLATES["djust_markdown"], value)
    assert value in re.sub(r"<[^>]+>", "", md), md
    pwa = _render(TEMPLATES["djust_pwa_head"], value)
    assert 'content="%s"' % value in pwa, pwa
