"""Drivers for the audited sinks that are not template tags the sweep can call by name (#3774).

Each driver is ``(label, produce)`` where ``produce(mode)`` returns the markup it rendered with the
hostile payload in every input the sink reads (``mode`` is ``"markup"`` or ``"url"``, see
``payload_sweep_3774``). A driver that cannot build its inputs raises: the sweep records that as a
failure, not a pass.
"""

from __future__ import annotations

from types import SimpleNamespace

from django import forms
from django.template import Context, NodeList
from django.utils.html import conditional_escape
from django.template.base import TextNode
from django.test import override_settings

from djust import LiveView
from payload_sweep_3774 import PAYLOAD


def _text(mode: str) -> str:
    return PAYLOAD


def _request():
    return SimpleNamespace(csp_nonce=PAYLOAD, user=None, path="/")


def live_input(mode):
    from djust.templatetags.live_tags import live_input as tag

    out = []
    for field_type in (
        "text",
        "password",
        "email",
        "number",
        "textarea",
        "select",
        "checkbox",
        "radio",
        "hidden",
    ):
        out.append(
            tag(
                field_type,
                handler=PAYLOAD,
                value=PAYLOAD,
                name=PAYLOAD,
                css_class=PAYLOAD,
                event="input",
                debounce=PAYLOAD,
                throttle=PAYLOAD,
                choices=[(PAYLOAD, PAYLOAD), PAYLOAD],
                checked=True,
                placeholder=PAYLOAD,
                aria_label=PAYLOAD,
                title=PAYLOAD,
            )
        )
    out.append(tag(PAYLOAD, handler="h"))  # the unknown-type comment
    return out


def colocated_hook(mode):
    from djust.templatetags.live_tags import ColocatedHookNode

    node = ColocatedHookNode("Chart", NodeList([TextNode(PAYLOAD)]))
    return [node.render(Context({"request": _request()}))]


def client_config(mode):
    from djust.templatetags.live_tags import _client_config_html

    with override_settings(DJUST_WS_PATH=PAYLOAD):
        return [_client_config_html(_request())]


def lorem(mode):
    from djust.template_tags.lorem import LoremTagHandler

    handler = LoremTagHandler()
    return [handler.render(["2", "p"], {}), handler.render(["3", "w"], {})]


def assets(mode):
    from djust.assets.tags import asset_tags
    from djust.components.base import Component

    class WithAssets(Component):
        requires_assets = ("leaflet",)

        def _render_custom(self):  # pragma: no cover - not rendered
            return ""

    return [asset_tags("leaflet"), WithAssets().asset_tags()]


def sticky_child(mode):
    from djust.templatetags.live_tags import _render_sticky_child_html

    child = SimpleNamespace(
        template="<button dj-click='go'>{{ v }}</button>",
        get_context_data=lambda: {"v": PAYLOAD},
    )
    return [
        _render_sticky_child_html(child, PAYLOAD, PAYLOAD, _request(), "x.Child"),
        _render_sticky_child_html(child, PAYLOAD, None, _request(), "x.Child"),
    ]


class _PayloadForm(forms.Form):
    name = forms.CharField(
        label=PAYLOAD, help_text=PAYLOAD, widget=forms.TextInput(attrs={"placeholder": PAYLOAD})
    )
    kind = forms.ChoiceField(label=PAYLOAD, choices=[(PAYLOAD, PAYLOAD)])
    agree = forms.BooleanField(label=PAYLOAD, required=False)
    pick = forms.ChoiceField(label=PAYLOAD, choices=[(PAYLOAD, PAYLOAD)], widget=forms.RadioSelect)
    notes = forms.CharField(label=PAYLOAD, widget=forms.Textarea)


def as_live(mode):
    from djust.forms import FormMixin

    class View(FormMixin):
        pass

    view = View()
    view._form_instance = _PayloadForm(data={"name": PAYLOAD, "kind": PAYLOAD, "notes": PAYLOAD})
    view.form_instance  # noqa: B018 - the property must resolve
    out = []
    for framework in (None, "bootstrap5", "tailwind", "plain", "bootstrap4"):
        try:
            out.append(view.as_live(framework=framework) if framework else view.as_live())
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"as_live({framework!r}) raised {exc!r}") from exc
    return out


def model_table(mode):
    from django.contrib.auth.models import User

    from djust.components.templatetags._forms import model_table as tag

    class Rows(list):
        model = User

    users = Rows(
        [User(id=1, username=PAYLOAD, first_name=PAYLOAD, last_name=PAYLOAD, email=PAYLOAD)]
    )
    return [
        tag(
            users,
            search=True,
            search_query=PAYLOAD,
            selectable=True,
            selected_rows=["1"],
            paginate=True,
            page=2,
            total_pages=3,
            custom_class=PAYLOAD,
            sort_by=PAYLOAD,
            filters={"username": PAYLOAD},
        ),
        tag(Rows([]), empty_title=PAYLOAD, empty_description=PAYLOAD),
        tag(users, loading=True),
    ]


class Child(LiveView):
    """A non-sticky child view for ``{% live_render %}`` (resolved by dotted path)."""

    template = "<button dj-click='go'>{{ v }}</button>"

    def mount(self, request, **kwargs):
        self.v = kwargs.get("v", "x")


class StickyChild(Child):
    sticky = True
    sticky_id = PAYLOAD


class Parent(LiveView):
    template = "<div></div>"


def live_render(mode):
    from django.http import HttpRequest

    from djust.templatetags.live_tags import live_render as tag

    out = []
    for kwargs in (
        {},
        {"sticky": True},
        {"lazy": True},
        {"lazy": "visible"},
        {
            "lazy": {
                "trigger": "visible",
                "on_error": "close",
                "timeout_s": 3,
                "placeholder": "<i>wait</i>",
            }
        },
    ):
        request = HttpRequest()
        request.method = "GET"
        request.csp_nonce = PAYLOAD
        parent = Parent()
        parent.request = request
        context = Context({"view": parent, "request": request})
        path = "payload_drivers_3774." + ("StickyChild" if kwargs.get("sticky") else "Child")
        out.append(tag(context, path, v=PAYLOAD, view_id=PAYLOAD, **kwargs))
    return out


def modal(mode):
    from djust.components.templatetags.djust_components import render_modal

    base = {
        "title": PAYLOAD,
        "size": PAYLOAD,
        "close_event": PAYLOAD,
        "component_id": PAYLOAD,
        "id": PAYLOAD,
        "role": "alertdialog",
        "heading_level": 3,
        "description": PAYLOAD,
        "describedby": PAYLOAD,
    }
    body = conditional_escape(PAYLOAD)
    return [
        render_modal({**base, "native": native, "close_button": close}, body)
        for native in (True, False)
        for close in (True, False)
    ] + [render_modal({"title": PAYLOAD}, body + " data-dj-modal-description ")]


DRIVERS = [
    ("live_render", live_render),
    ("render_modal", modal),
    ("live_input", live_input),
    ("ColocatedHookNode", colocated_hook),
    ("_client_config_html", client_config),
    ("lorem", lorem),
    ("assets", assets),
    ("_render_sticky_child_html", sticky_child),
    ("FormMixin.as_live", as_live),
    ("model_table", model_table),
]
