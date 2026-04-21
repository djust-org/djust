"""Template tags for Django formset / inline-formset patterns in LiveView templates.

v0.5.1 — djust-native ``{% inputs_for %}`` block tag that iterates a Django
formset and exposes each child form as a context variable with its correct
per-row prefix intact. Pair with :class:`djust.forms.FormSetHelpers` on the
LiveView side for add/remove row handlers.

Usage::

    {% load djust_formsets %}
    <form dj-submit="save">
        {{ addresses_formset.management_form }}
        {% inputs_for addresses_formset as form %}
            <div dj-inputs-for="{{ form.prefix }}">
                {{ form.id }}
                {{ form.street }}
                {{ form.city }}
                <button type="button" dj-click="remove_row"
                        dj-value-formset="addresses"
                        dj-value-prefix="{{ form.prefix }}">remove</button>
            </div>
        {% endinputs_for %}
        <button type="button" dj-click="add_row"
                dj-value-formset="addresses">+ add</button>
    </form>

The tag performs no server↔client wiring — `add_row` / `remove_row` are
plain event handlers the developer writes (helpers in
``djust.forms.FormSetHelpers`` provide the mutation boilerplate).
"""

from __future__ import annotations

from django import template
from django.template.base import Node, NodeList, TemplateSyntaxError

register = template.Library()


class InputsForNode(Node):
    """Block node that iterates a Django formset and exposes each child form.

    The child's ``prefix`` (e.g. ``"addresses-0"``) is preserved so rendered
    inputs use the prefixed name Django expects on submit.
    """

    def __init__(self, formset_var: template.Variable, loop_var: str, nodelist: NodeList):
        self.formset_var = formset_var
        self.loop_var = loop_var
        self.nodelist = nodelist

    def render(self, context) -> str:
        try:
            formset = self.formset_var.resolve(context)
        except template.VariableDoesNotExist:
            return ""
        if formset is None:
            return ""

        # Accept either a Django BaseFormSet or any iterable of bound forms.
        forms = list(formset) if hasattr(formset, "__iter__") else []
        parts = []
        for form in forms:
            with context.push():
                context[self.loop_var] = form
                # Expose loop metadata matching {% for %} conventions so
                # templates can use forloop.counter0 etc. for CSS classes.
                context["inputs_for_loop"] = {
                    "counter0": forms.index(form),
                    "counter": forms.index(form) + 1,
                    "first": form is forms[0],
                    "last": form is forms[-1],
                }
                parts.append(self.nodelist.render(context))
        return "".join(parts)


@register.tag("inputs_for")
def do_inputs_for(parser, token):
    """Parse ``{% inputs_for formset as form %}...{% endinputs_for %}``.

    The ``as <name>`` clause is required so templates are explicit about the
    name the child form binds to (parallels Django's ``{% with %}`` tag and
    avoids the ambiguity of bare ``{% for %}``).
    """
    try:
        _tag, formset_expr, as_kw, loop_var = token.split_contents()
    except ValueError:
        raise TemplateSyntaxError(
            "{% inputs_for %} requires the form 'inputs_for FORMSET as VAR'. "
            "Example: {% inputs_for addresses as form %}"
        )
    if as_kw != "as":
        raise TemplateSyntaxError(
            f"{{% inputs_for %}} expected 'as' between formset and variable name, got {as_kw!r}"
        )
    nodelist = parser.parse(("endinputs_for",))
    parser.delete_first_token()
    return InputsForNode(template.Variable(formset_expr), loop_var, nodelist)
