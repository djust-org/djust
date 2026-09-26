"""ADR-035 F2 live surface: the managed edit object in a real browser.

Exercised by ``tests/playwright/test_model_form.py`` over WebSocket, SSE and
HTTP-only transports. ``ModelFormDemoView`` has no ``mount()``: the route's
``pk`` selects a ``Product``; inactive products are filtered out by
``get_queryset()`` and "restricted" ones are refused by
``has_object_permission()``. ``model_form_index`` resets three fixed demo
products so each browser run starts from the same data.
"""

from decimal import Decimal

from django import forms
from django.http import HttpResponse
from django.utils.html import format_html_join

from demo_app.models import Product
from djust import LiveView
from djust.forms import ModelFormMixin

DEMO_PRODUCTS = {
    "editable": {"category": "tools", "is_active": True},
    "inactive": {"category": "tools", "is_active": False},
    "restricted": {"category": "restricted", "is_active": True},
}


class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = ["name", "price", "stock"]


class ModelFormDemoView(ModelFormMixin[Product], LiveView):
    template_name = "demos/model_form.html"
    model = Product
    form_class = ProductForm

    def get_queryset(self):
        return super().get_queryset().filter(is_active=True)

    def has_object_permission(self, request, obj):
        return obj.category != "restricted"

    def form_valid(self, form):
        self.object = form.save()
        self.success_message = "Saved %s" % self.object.name


def model_form_index(request):
    """Recreate the demo products and link to each (a plain Django view)."""
    Product.objects.filter(name__startswith="ADR-035 ").delete()
    links = []
    for key, fields in DEMO_PRODUCTS.items():
        product = Product.objects.create(
            name="ADR-035 %s" % key, price=Decimal("10.00"), stock=1, **fields
        )
        links.append((key, product.pk, key))
    body = format_html_join(
        "", '<li><a id="mf-{}" href="/demos/model-form/{}/">{}</a></li>', links
    )
    return HttpResponse("<ul>%s</ul>" % body)
