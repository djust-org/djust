"""The views the per-view memory accounting measures (#3998).

Five shapes: a counter, a page that is mostly static markup, a list of plain
rows, a page of class-level components and a form. The static page and the
list come in sizes, so a growth law can be fitted to them.
"""

from __future__ import annotations

from typing import Any, Dict, List

from django import forms

from djust import LiveView, event_handler
from djust.components.descriptors.base import LiveComponent, TypedState
from djust.decorators import state
from djust.forms import FormMixin

MODULE = __name__


class _Base(LiveView):
    exposure_policy = "explicit"  # every subclass states its own; the guard reads the base too

    def _get_context_processors(self):
        return []


class Counter(_Base):
    exposure_policy = "explicit"
    template = "<div dj-root><p>Count: {{ count }}</p><i>{{ label }}</i></div>"
    count = state(0)
    label = state("a")

    @event_handler()
    def inc(self, **kwargs):
        self.count += 1


# --- a page of static markup ----------------------------------------------------------

#: One block of ordinary page markup: a heading, a paragraph, a list with
#: links, a figure. 1 000 bytes, so ``static_page(50)`` is a 50 KB page.
_BLOCK_BODY = (
    '<section class="card card--wide" data-kind="article">'
    '<h2 class="card__title">Quarterly figures for the northern region</h2>'
    '<p class="card__lead">Revenue rose by four percent against the same quarter of '
    "last year, while the cost of delivery fell for the third quarter in a row.</p>"
    '<ul class="card__links">'
    '<li><a href="/reports/north/summary/" class="link">Summary</a></li>'
    '<li><a href="/reports/north/detail/" class="link">Detail</a></li>'
    '<li><a href="/reports/north/history/" class="link link--muted">History</a></li>'
    "</ul>"
    '<figure class="card__figure"><img src="/static/img/north.svg" alt="Northern region" '
    'width="320" height="180" loading="lazy"><figcaption>Deliveries per week, by depot'
    "</figcaption></figure>"
    '<footer class="card__footer"><span class="tag">finance</span><span class="tag">'
    'logistics</span><time datetime="2026-10-01">1 October 2026</time></footer>'
)
BLOCK = (
    _BLOCK_BODY
    + "<!--"
    + "." * (1000 - len(_BLOCK_BODY) - len("<!---->") - len("</section>"))
    + "-->"
    + "</section>"
)
assert len(BLOCK) == 1000, len(BLOCK)


def _static_template(kilobytes: int) -> str:
    return "<div dj-root><p>Count: {{ count }}</p>" + BLOCK * kilobytes + "<i>{{ label }}</i></div>"


def static_page(kilobytes: int) -> type:
    """A view whose page is *kilobytes* KB of static markup and two values."""
    name = f"Static{kilobytes}"
    existing = globals().get(name)
    if existing is not None:
        return existing

    def inc(self, **kwargs):
        self.count += 1

    made = type(
        name,
        (_Base,),
        {
            "__module__": MODULE,
            "exposure_policy": "explicit",
            "template": _static_template(kilobytes),
            "count": state(0),
            "label": state("a"),
            "inc": event_handler()(inc),
        },
    )
    globals()[name] = made
    return made


# --- a list of plain rows -------------------------------------------------------------


def rows(n: int) -> List[Dict[str, Any]]:
    return [
        {
            "id": i,
            "name": f"item {i}",
            "qty": i % 7,
            "active": i % 3 != 0,
            "tags": ["alpha", f"t{i}"],
        }
        for i in range(n)
    ]


TABLE = (
    '<table>{% for row in items %}<tr id="row-{{ row.id }}"><td>{{ row.name }}</td>'
    "<td>{{ row.qty }}</td><td>{% for t in row.tags %}{{ t }} {% endfor %}</td></tr>"
    "{% endfor %}</table>"
)


def row_list(n: int) -> type:
    """A view holding *n* plain rows and rendering them as a table."""
    name = f"Rows{n}"
    existing = globals().get(name)
    if existing is not None:
        return existing

    def mount(self, request, **kwargs):
        self.items = rows(n)

    def touch(self, **kwargs):
        self.items[0]["name"] += "!"

    def retitle(self, **kwargs):
        self.title += "!"

    made = type(
        name,
        (_Base,),
        {
            "__module__": MODULE,
            "exposure_policy": "explicit",
            "template": "<div dj-root><h1>{{ title }}</h1>" + TABLE + "</div>",
            "title": state("Rows"),
            "items": state(default_factory=list),
            "mount": mount,
            "touch": event_handler()(touch),
            "retitle": event_handler()(retitle),
        },
    )
    globals()[name] = made
    return made


# --- components and a form ------------------------------------------------------------


class Tabs(LiveComponent):
    class State(TypedState):
        active: str = "overview"
        seen: tuple = ()

    template = "<p>{{ active }}<b>{{ component_id }}</b>{% for s in seen %}{{ s }}{% endfor %}</p>"

    @event_handler()
    def select(self, value: str = "", **kwargs: Any) -> None:
        self.state.active = value


def _components_page(count: int) -> type:
    names = [f"tabs{i}" for i in range(count)]

    def mount(self, request, **kwargs):
        self.title = "Page"

    def rename(self, value: str = "", **kwargs):
        self.title = value

    body: Dict[str, Any] = {
        "__module__": MODULE,
        "exposure_policy": "legacy",
        "template": "<div dj-root><h1>{{ title }}</h1>"
        + "".join("{{ %s }}" % name for name in names)
        + "</div>",
        "mount": mount,
        "rename": event_handler()(rename),
    }
    for name in names:
        body[name] = Tabs()
    return type("Components20", (_Base,), body)


Components20 = _components_page(20)


class SignupForm(forms.Form):
    name = forms.CharField(max_length=5, required=False)
    email = forms.EmailField()


class FormView(FormMixin, _Base):
    exposure_policy = "explicit"
    form_class = SignupForm
    template = (
        '<div dj-root><form dj-submit="submit_form">'
        '<input name="name" value="{{ form_data.name }}">'
        '<input name="email" value="{{ form_data.email }}">'
        "{% for e in field_errors.name %}<i>name:{{ e }}</i>{% endfor %}"
        "{% for e in field_errors.email %}<i>email:{{ e }}</i>{% endfor %}"
        "<p>valid={{ is_valid }}</p></form></div>"
    )
