"""
CSS Framework Adapters for djust

Provides pluggable adapters for different CSS frameworks (Bootstrap 5, Tailwind, etc.)
to render form fields with appropriate styling.
"""

from abc import ABC, abstractmethod
import re
from typing import Any, Dict, List, NamedTuple, Optional, Tuple
from django import forms
from django.utils.html import escape
from ._attr_policy import passthrough_attrs
from ._html import attr_name
from .config import config

#: The theme slots an adapter's classes are set by, and the
#: ``LIVEVIEW_CONFIG[<framework>]`` keys each one reads, in order, when neither
#: the adapter's ``css_classes`` nor ``LIVEVIEW_CONFIG["form_classes"]`` names it.
FORM_CLASS_SLOTS: Dict[str, Tuple[str, ...]] = {
    "wrapper": ("field_wrapper_class",),
    "label": ("label_class",),
    "input": ("field_class",),
    "input_invalid": ("field_class_invalid",),
    "textarea": ("textarea_class",),
    "select": ("select_class",),
    "invalid": ("invalid_class",),
    "help": ("help_text_class",),
    "error": ("error_class_block", "error_class"),
    "checkbox_wrapper": ("checkbox_wrapper_class",),
    "checkbox": ("checkbox_class",),
    "checkbox_label": ("checkbox_label_class",),
    "legend": ("legend_class", "label_class"),
    "radio_group": ("radio_group_class",),
    "checkbox_group": ("checkbox_group_class", "radio_group_class"),
    "radio_option": ("radio_wrapper_class", "checkbox_wrapper_class"),
    "radio_input": ("radio_class", "checkbox_class"),
    "radio_label": ("radio_label_class", "checkbox_label_class"),
}

_VARIANT_RE = re.compile(r"[a-z0-9][a-z0-9_-]*")


class _FieldIds(NamedTuple):
    """The ids one rendered field uses (``help`` / ``error`` empty when not rendered)."""

    control: str
    help: str
    error: str
    invalid: bool


class FrameworkAdapter(ABC):
    """
    Abstract base class for CSS framework adapters.

    Each adapter implements field rendering logic for a specific CSS framework.

    **Escaping contract.** ``render_field`` returns HTML that
    ``FormMixin.as_live_field`` / ``as_live`` and the ``{% live_form %}`` /
    ``{% live_field %}`` tags mark safe (#3043), so an adapter must escape
    every value it interpolates — labels, help text, errors, choice labels
    and values, field values, and attribute values — as the built-in
    adapters do with :func:`django.utils.html.escape`.
    """

    @abstractmethod
    def render_field(
        self, field: forms.Field, field_name: str, value: Any, errors: List[str], **kwargs: Any
    ) -> str:
        """Render a form field with framework-specific styling."""
        pass

    @abstractmethod
    def render_errors(self, errors: List[str], **kwargs: Any) -> str:
        """Render field errors with framework-specific styling."""
        pass

    @abstractmethod
    def get_field_class(self, field: forms.Field, has_errors: bool = False) -> str:
        """Get CSS classes for a field widget."""
        pass


class BaseAdapter(FrameworkAdapter):
    """
    Shared rendering logic for all CSS framework adapters.

    Subclasses override class attributes to customize per-framework behavior.
    CSS classes are resolved via ``config.get_framework_class()`` so the user's
    LIVEVIEW_CONFIG is respected automatically.

    **How the field name reaches the handler — do not add a ``data-*``
    attribute for it (#2145).** These renderers bind ``dj-change`` (or the
    caller's ``dom_event``) and set ``name="<field_name>"``. The client's
    ``buildFormEventParams`` (``09-event-binding.js:525``) hardcodes the key
    ``field`` and sources it from ``getFieldName`` (``:504``) — ``data-field``,
    then the element's ``name``, then its ``id``. It merges only
    ``dj-value-*``; it does **not** collect ``data-*``. The ``name`` these
    renderers already set is what carries the field name, on both the live
    event path and the reconnect path (``_processFormRecovery``, ``:1773``,
    reads ``field.name || field.id``).

    Until #2145 this code also emitted ``data-field_name="<field_name>"``,
    commented *"so event handler knows which field changed"*. Nothing read
    it: ``data-*`` is collected only by ``extractTypedParams``
    (``08-event-parsing.js:248``) and by ``_processAutoRecover``
    (``09-event-binding.js:1684``), and neither runs on an element whose only
    djust directive is ``dj-change``/``dj-input``. The attribute was inert —
    but it made the false mechanism *"djust maps ``data-*`` to handler
    parameters"* look true, which is the belief that produced #2137 (the
    ``validate_field`` signature taking ``field_name`` while the client sends
    ``field``). Adding one back would re-arm that trap.
    """

    # Framework-specific markers — override in subclasses
    required_marker: str = ""
    help_text_tag: str = "div"
    help_text_class: str = ""
    error_wrapper: bool = True

    #: The ``LIVEVIEW_CONFIG`` section this adapter reads its classes from
    #: (``"bootstrap5"`` reads ``LIVEVIEW_CONFIG["bootstrap5"]``). ``None`` reads
    #: the section named by ``css_framework``, which is what a registered
    #: custom adapter gets unless it sets one.
    framework_name: Optional[str] = None

    #: Theme classes for this adapter, by slot (see :data:`FORM_CLASS_SLOTS`).
    #: A subclass sets only the slots it changes; ``""`` means "no class".
    #: These win over ``LIVEVIEW_CONFIG["form_classes"]``, which wins over the
    #: framework's own classes.
    css_classes: Dict[str, str] = {}

    # --- public API (implements FrameworkAdapter) ---

    def css_class(self, slot: str) -> str:
        """The CSS class for a theme *slot* (``"input"``, ``"label"``, ``"radio_group"``, …).

        Resolved in order: the adapter's :attr:`css_classes`, then
        ``LIVEVIEW_CONFIG["form_classes"]``, then the framework section's own
        keys (:data:`FORM_CLASS_SLOTS`), then the adapter's built-in default.
        An empty string at any of the first two levels means "no class".
        """
        if slot in self.css_classes:
            return str(self.css_classes[slot] or "")
        overrides = config.get("form_classes") or {}
        if isinstance(overrides, dict) and slot in overrides:
            return str(overrides[slot] or "")
        for key in FORM_CLASS_SLOTS.get(slot, ()):
            value = self._framework_class(key)
            if value:
                return value
        return self._default_class(slot)

    def render_field(
        self, field: forms.Field, field_name: str, value: Any, errors: List[str], **kwargs: Any
    ) -> str:
        has_errors = len(errors) > 0
        field_type = self._get_field_type(field)

        # Stable ids: the control is ``<auto_id>`` (``id_<name>`` by default),
        # its help text ``<auto_id>-help`` and its errors ``<auto_id>-error``.
        control_id = str(kwargs.get("auto_id") or f"id_{field_name}")
        show_help = bool(
            kwargs.get("render_help_text", config.get("render_help_text", True)) and field.help_text
        )
        show_errors = bool(kwargs.get("render_errors", config.get("render_errors", True)))
        show_errors = show_errors and has_errors
        ids = _FieldIds(
            control=control_id,
            help=f"{control_id}-help" if show_help else "",
            error=f"{control_id}-error" if show_errors else "",
            invalid=has_errors,
        )
        kwargs = {**kwargs, "_dj_ids": ids}

        wrapper_class = kwargs.get("wrapper_class", self.css_class("wrapper"))
        html = f'<div class="{escape(wrapper_class)}">' if wrapper_class else "<div>"

        render_label = kwargs.get("render_label", config.get("render_labels", True))

        # Widget. A checkbox carries its own label, and a choice group names
        # itself with a <legend>, so neither gets the label above the control.
        if field_type == "checkbox":
            html += self._render_checkbox(field, field_name, value, has_errors, **kwargs)
        elif field_type in ("radio", "checkbox_multiple"):
            html += self._render_choice_group(
                field, field_name, value, has_errors, field_type, **kwargs
            )
        else:
            if render_label:
                html += self._render_label(field, field_name, **kwargs)
            html += self._render_input(field, field_name, value, has_errors, field_type, **kwargs)

        # Help text
        if show_help:
            help_class = self.css_class("help")
            cls_attr = f' class="{escape(help_class)}"' if help_class else ""
            html += (
                f'<{self.help_text_tag} id="{escape(ids.help)}"{cls_attr}>'
                f"{escape(field.help_text)}</{self.help_text_tag}>"
            )

        # Errors
        if show_errors:
            html += self.render_errors(errors, error_id=ids.error)

        html += "</div>"
        return html

    def render_errors(self, errors: List[str], **kwargs: Any) -> str:
        error_class = self.css_class("error")
        error_id = kwargs.get("error_id") or ""
        id_attr = f' id="{escape(error_id)}"' if error_id else ""
        if self.error_wrapper:
            cls_attr = f' class="{escape(error_class)}"' if error_class else ""
            html = f"<div{id_attr}{cls_attr}>"
            for error in errors:
                html += f"<div>{escape(error)}</div>"
            html += "</div>"
        else:
            html = ""
            for error in errors:
                html += (
                    f'<p class="{escape(error_class)}">{escape(error)}</p>'
                    if error_class
                    else f"<p>{escape(error)}</p>"
                )
            if error_id:
                # One element carries the id ``aria-describedby`` points at.
                html = f"<div{id_attr}>{html}</div>"
        return html

    def get_field_class(self, field: forms.Field, has_errors: bool = False) -> str:
        widget = getattr(field, "widget", None)
        if isinstance(field, forms.BooleanField):
            return self.css_class("checkbox")
        if isinstance(field, forms.ChoiceField) and isinstance(
            widget, (forms.RadioSelect, forms.CheckboxSelectMultiple)
        ):
            return self.css_class("input_invalid") if has_errors else self.css_class("input")
        own = ""
        if isinstance(field, forms.ChoiceField):
            # Select widgets use the select class when one is set (e.g.
            # Bootstrap 4 "custom-select"), else the generic input class.
            own = self.css_class("select")
        elif isinstance(widget, forms.Textarea):
            own = self.css_class("textarea")
        if own:
            invalid = self.css_class("invalid") if has_errors else ""
            return f"{own} {invalid}" if invalid else own
        return self.css_class("input_invalid") if has_errors else self.css_class("input")

    # --- shared private helpers ---

    def _framework_class(self, key: str) -> str:
        """A class from this adapter's ``LIVEVIEW_CONFIG`` framework section."""
        if self.framework_name:
            result = config.get(f"{self.framework_name}.{key}", "")
            return str(result) if result is not None else ""
        return config.get_framework_class(key)

    def _default_class(self, slot: str) -> str:
        """The adapter's own class for *slot* when no configuration names one."""
        if slot == "help":
            return self.help_text_class
        return ""

    @staticmethod
    def _aria_attrs(ids: Optional["_FieldIds"], attrs: Dict[str, Any]) -> None:
        """Describe a control by its help text and errors, and mark it invalid.

        Widget ``aria-describedby`` ids, if any, follow djust's own.
        """
        if ids is None:
            return
        described = [i for i in (ids.help, ids.error) if i]
        if described:
            attrs["aria-describedby"] = " ".join(described)
        if ids.invalid:
            attrs["aria-invalid"] = "true"

    @staticmethod
    def _merge_widget_attrs(field: forms.Field, attrs: Dict[str, Any]) -> None:
        """Merge ``field.widget.attrs`` into *attrs* without overriding existing keys.

        Widget-defined attributes (``type``, ``placeholder``, ``pattern``,
        ``min``, ``max``, custom ``data-*``, etc.) are applied first so that
        djust-specific attributes (``dj-change``, ``class``, ``name``, …)
        always take precedence. A widget's ``aria-describedby`` ids are added
        after djust's own help/error ids rather than dropped.

        Boolean HTML attributes (``True``/``False``) and ``None`` values are
        handled downstream by ``_build_tag`` / ``build_tag`` — we just pass
        them through unchanged.
        """
        widget_attrs = getattr(field, "widget", None)
        if widget_attrs is None:
            return
        widget_attrs = getattr(widget_attrs, "attrs", None)
        if not widget_attrs:
            return
        merged: Dict[str, Any] = {}
        for key, value in widget_attrs.items():
            if key not in attrs:
                # Skip None / False — these mean "attribute not present".
                # True is kept so _build_tag can render it as a boolean attr.
                if value is None or value is False:
                    continue
                merged[key] = value
            elif key == "aria-describedby" and value and isinstance(value, str):
                attrs[key] = f"{attrs[key]} {value}"
        # Only the attributes that will actually be written are judged (#3818): an inline event
        # handler name raises ``ValueError``, a rejected URL renders ``#`` and a ``type`` that
        # submits is left out, as for ``live_input``; the widget's ``type`` is ignored when the
        # adapter already set one.
        attrs.update(passthrough_attrs(merged))

    @staticmethod
    def _get_field_type(field: forms.Field) -> str:
        # If the widget has an explicit input_type that differs from the
        # default for its class, honour it. Django moves type= from
        # widget attrs into widget.input_type during __init__, so
        # TextInput(attrs={"type": "tel"}) sets input_type="tel" and
        # removes "type" from attrs.
        widget = field.widget
        widget_type = getattr(widget, "input_type", None)
        if widget_type:
            # Check if this is a user override (not just the widget's default).
            # Compare against the widget class's default input_type.
            default_type = getattr(type(widget), "input_type", None)
            if widget_type != default_type:
                # This is where a widget's ``attrs={"type": ...}`` becomes the tag's own
                # ``type`` (Django moves it onto the widget), so the passthrough policy for
                # ``type`` applies here: ``image`` / ``submit`` / ``reset`` / ``button`` are
                # left out (and logged), so the field falls back to its class's own type
                # below (#3818).
                judged = passthrough_attrs({"type": widget_type})
                if "type" in judged:
                    return str(judged["type"])

        # Check EmailField before CharField (EmailField inherits from CharField)
        if isinstance(field, forms.EmailField):
            return "email"
        elif isinstance(field, forms.CharField):
            if isinstance(field.widget, forms.Textarea):
                return "textarea"
            elif isinstance(field.widget, forms.PasswordInput):
                return "password"
            else:
                return "text"
        elif isinstance(field, forms.IntegerField):
            return "number"
        elif isinstance(field, forms.BooleanField):
            return "checkbox"
        elif isinstance(field, forms.ChoiceField):
            # CheckboxSelectMultiple subclasses RadioSelect: test it first.
            if isinstance(field.widget, forms.CheckboxSelectMultiple):
                return "checkbox_multiple"
            elif isinstance(field.widget, forms.RadioSelect):
                return "radio"
            else:
                return "select"
        elif isinstance(field, forms.DateField):
            return "date"
        elif isinstance(field, forms.DateTimeField):
            return "datetime-local"
        else:
            return "text"

    def _label_text(self, field: forms.Field, field_name: str, **kwargs: Any) -> str:
        return str(kwargs.get("label", field.label or field_name.replace("_", " ").title()))

    def _render_label(self, field: forms.Field, field_name: str, **kwargs: Any) -> str:
        label_class = self.css_class("label")
        label_text = self._label_text(field, field_name, **kwargs)
        required = self.required_marker if field.required else ""
        cls_attr = f' class="{escape(label_class)}"' if label_class else ""
        ids = kwargs.get("_dj_ids")
        control_id = ids.control if ids else f"id_{field_name}"
        return f'<label for="{escape(control_id)}"{cls_attr}>{escape(label_text)}{required}</label>'

    def _build_tag(self, tag: str, attrs: Dict[str, str], content: Optional[str] = None) -> str:
        attrs_str = " ".join(f'{attr_name(k)}="{escape(str(v))}"' for k, v in attrs.items())
        if content is not None:
            return f"<{tag} {attrs_str}>{content}</{tag}>"
        else:
            return f"<{tag} {attrs_str} />"

    def _render_input(
        self,
        field: forms.Field,
        field_name: str,
        value: Any,
        has_errors: bool,
        field_type: str,
        **kwargs: Any,
    ) -> str:
        field_class = self.get_field_class(field, has_errors)
        ids = kwargs.get("_dj_ids")
        attrs: Dict[str, str] = {
            "name": field_name,
            "id": ids.control if ids else f"id_{field_name}",
        }
        if field_class:
            attrs["class"] = field_class
        if field.required:
            attrs["required"] = "required"
        self._aria_attrs(ids, attrs)
        if kwargs.get("auto_validate", config.get("auto_validate_on_change", True)):
            attrs[kwargs.get("dom_event", "dj-change")] = kwargs.get("event_name", "validate_field")

        if field_type == "textarea":
            # Merge widget.attrs (placeholder, rows, cols, etc.) — existing
            # keys (name, id, class, dj-change) take precedence.
            self._merge_widget_attrs(field, attrs)
            return self._build_tag("textarea", attrs, escape(str(value)))
        elif field_type == "select":
            self._merge_widget_attrs(field, attrs)
            return self._render_select(field, field_name, value, has_errors, attrs)
        else:
            attrs["type"] = field_type
            attrs["value"] = str(value) if value else ""
            # Merge widget.attrs (placeholder, pattern, min, max, etc.) —
            # existing keys (type, value, name, id, class, dj-change) take
            # precedence over widget defaults.
            self._merge_widget_attrs(field, attrs)
            return self._build_tag("input", attrs)

    @staticmethod
    def _selected_values(value: Any) -> set:
        """The submitted value(s) as strings, for ``selected`` / ``checked``."""
        if value is None:
            return set()
        if isinstance(value, (list, tuple, set, frozenset)):
            return {str(v) for v in value if v is not None}
        return {str(value)}

    @staticmethod
    def _flat_choices(choices: Any) -> List[Any]:
        """``(value, label)`` pairs with option groups flattened."""
        flat: List[Any] = []
        for choice_value, choice_label in choices:
            if isinstance(choice_label, (list, tuple)):
                flat.extend(choice_label)
            else:
                flat.append((choice_value, choice_label))
        return flat

    def _render_select(
        self,
        field: forms.Field,
        field_name: str,
        value: Any,
        has_errors: bool,
        attrs: Dict[str, str],
    ) -> str:
        choices = list(field.choices)
        selected = self._selected_values(value)

        def option(choice_value: Any, choice_label: Any) -> str:
            raw = "" if choice_value is None else str(choice_value)
            sel = " selected" if raw in selected else ""
            return f'<option value="{escape(raw)}"{sel}>{escape(str(choice_label))}</option>'

        options_html = ""
        # An optional field gets an empty option, unless its choices already
        # have one (a ModelChoiceField's empty_label, a ("", "Pick one")
        # entry): a second blank would be a duplicate.
        has_blank = any(v is None or str(v) == "" for v, _ in self._flat_choices(choices))
        if not field.required and not has_blank:
            options_html += '<option value="">---------</option>'
        for choice_value, choice_label in choices:
            if isinstance(choice_label, (list, tuple)):
                options_html += f'<optgroup label="{escape(str(choice_value))}">'
                options_html += "".join(option(v, lbl) for v, lbl in choice_label)
                options_html += "</optgroup>"
            else:
                options_html += option(choice_value, choice_label)
        return self._build_tag("select", attrs, options_html)

    def _render_checkbox(
        self, field: forms.Field, field_name: str, value: Any, has_errors: bool, **kwargs: Any
    ) -> str:
        wrapper_class = self.css_class("checkbox_wrapper")
        field_class = self.get_field_class(field, has_errors)
        label_class = self.css_class("checkbox_label")
        ids = kwargs.get("_dj_ids")
        control_id = ids.control if ids else f"id_{field_name}"

        attrs: Dict[str, str] = {"type": "checkbox", "name": field_name, "id": control_id}
        if field_class:
            attrs["class"] = field_class
        if value:
            attrs["checked"] = "checked"
        if field.required:
            attrs["required"] = "required"
        self._aria_attrs(ids, attrs)
        if kwargs.get("auto_validate", config.get("auto_validate_on_change", True)):
            attrs[kwargs.get("dom_event", "dj-change")] = kwargs.get("event_name", "validate_field")

        # Merge widget.attrs before building the tag
        self._merge_widget_attrs(field, attrs)

        label_text = self._label_text(field, field_name, **kwargs)
        required = self.required_marker if field.required else ""
        wrap_cls = f' class="{escape(wrapper_class)}"' if wrapper_class else ""
        lbl_cls = f' class="{escape(label_class)}"' if label_class else ""

        return (
            f"<div{wrap_cls}>"
            f"{self._build_tag('input', attrs)}"
            f'<label{lbl_cls} for="{escape(control_id)}">{escape(label_text)}{required}</label>'
            f"</div>"
        )

    def _render_radio(
        self, field: forms.Field, field_name: str, value: Any, has_errors: bool, **kwargs: Any
    ) -> str:
        """A RadioSelect as an accessible group (kept for subclasses that call it)."""
        return self._render_choice_group(field, field_name, value, has_errors, "radio", **kwargs)

    def _render_choice_group(
        self,
        field: forms.Field,
        field_name: str,
        value: Any,
        has_errors: bool,
        field_type: str,
        **kwargs: Any,
    ) -> str:
        """A ``RadioSelect`` or ``CheckboxSelectMultiple`` as a ``<fieldset>`` group.

        The ``<legend>`` names the group; a radio group is also
        ``role="radiogroup"`` (so it can be ``aria-required``). Help text and
        errors describe the group. Each choice is an ``<input>`` with its own
        ``<label for>`` and an id ``<auto_id>_<index>`` (Django's scheme), so
        ids never carry the choice value. Hook classes for themes:
        ``dj-radio-group`` / ``dj-checkbox-group`` on the group,
        ``dj-radio-option`` / ``dj-checkbox-option`` on each choice, and
        ``dj-radio-group--<variant>`` with ``group_variant="segmented"``.
        """
        if not hasattr(field, "choices"):
            return "<!-- ERROR: Radio field must have choices -->"

        radio = field_type == "radio"
        render_label = kwargs.get("render_label", config.get("render_labels", True))
        kind = "radio" if radio else "checkbox"
        ids: Optional[_FieldIds] = kwargs.get("_dj_ids")
        group_id = ids.control if ids else f"id_{field_name}"
        legend_id = f"{group_id}-legend"

        group_classes = [f"dj-{kind}-group"]
        variant = kwargs.get("group_variant") or ""
        if isinstance(variant, str) and _VARIANT_RE.fullmatch(variant):
            group_classes.append(f"dj-{kind}-group--{variant}")
        themed = self.css_class("radio_group" if radio else "checkbox_group")
        if themed:
            group_classes.append(themed)

        group_attrs: Dict[str, str] = {"id": group_id, "class": " ".join(group_classes)}
        if radio:
            group_attrs["role"] = "radiogroup"
        label_text = self._label_text(field, field_name, **kwargs)
        if render_label:
            group_attrs["aria-labelledby"] = legend_id
        else:
            group_attrs["aria-label"] = label_text
        if radio and field.required:
            group_attrs["aria-required"] = "true"
        self._aria_attrs(ids, group_attrs)

        if radio:
            option_class = self.css_class("radio_option")
            input_class = self.css_class("radio_input")
            label_class = self.css_class("radio_label")
        else:
            option_class = self.css_class("checkbox_wrapper")
            input_class = self.css_class("checkbox")
            label_class = self.css_class("checkbox_label")
        option_classes = f"dj-{kind}-option {option_class}".strip()

        html = f"<fieldset {self._attrs_str(group_attrs)}>"
        if render_label:
            legend_class = self.css_class("legend")
            cls_attr = f' class="{escape(legend_class)}"' if legend_class else ""
            required = self.required_marker if field.required else ""
            html += f'<legend id="{escape(legend_id)}"{cls_attr}>{escape(label_text)}{required}</legend>'

        selected = self._selected_values(value)
        live = kwargs.get("auto_validate", config.get("auto_validate_on_change", True))
        for index, (choice_value, choice_label) in enumerate(self._flat_choices(field.choices)):
            raw = "" if choice_value is None else str(choice_value)
            option_id = f"{group_id}_{index}"
            attrs: Dict[str, str] = {
                "type": kind,
                "name": field_name,
                "id": option_id,
                "value": raw,
            }
            if input_class:
                attrs["class"] = input_class
            if raw in selected:
                attrs["checked"] = "checked"
            if radio and field.required:
                # One radio of the group must be checked. (On a checkbox
                # group ``required`` would demand every box.)
                attrs["required"] = "required"
            if live and (radio or kwargs.get("dom_event")):
                # The client sends ``field`` (this input's name) and ``value``
                # (the checked radio's value): exactly what validate_field takes.
                # A checkbox group is bound only when the caller names the
                # event (WizardMixin does): a checkbox change sends its checked
                # state, not the group's list of values, so the default
                # ``validate_field`` would store a boolean for the field.
                attrs[kwargs.get("dom_event") or "dj-change"] = kwargs.get(
                    "event_name", "validate_field"
                )
            # Merge widget.attrs (data-*, custom classes, etc.)
            self._merge_widget_attrs(field, attrs)

            lbl_cls = f' class="{escape(label_class)}"' if label_class else ""
            html += (
                f'<div class="{escape(option_classes)}">'
                f"{self._build_tag('input', attrs)}"
                f'<label{lbl_cls} for="{escape(option_id)}">'
                f"{escape(str(choice_label))}</label>"
                f"</div>"
            )
        html += "</fieldset>"
        return html

    @staticmethod
    def _attrs_str(attrs: Dict[str, str]) -> str:
        return " ".join(f'{attr_name(k)}="{escape(str(v))}"' for k, v in attrs.items())


class Bootstrap4Adapter(BaseAdapter):
    """Bootstrap 4 CSS framework adapter (NYC Core Framework, gov sites, legacy projects)"""

    framework_name = "bootstrap4"
    required_marker = ' <span class="text-danger" aria-hidden="true">*</span>'
    help_text_tag = "small"
    help_text_class = "form-text text-muted"
    error_wrapper = True


class Bootstrap5Adapter(BaseAdapter):
    """Bootstrap 5 CSS framework adapter"""

    framework_name = "bootstrap5"
    required_marker = ' <span class="text-danger" aria-hidden="true">*</span>'
    help_text_tag = "div"
    help_text_class = "form-text"
    error_wrapper = True


class TailwindAdapter(BaseAdapter):
    """Tailwind CSS framework adapter"""

    framework_name = "tailwind"
    required_marker = ' <span class="text-red-600" aria-hidden="true">*</span>'
    help_text_tag = "p"
    help_text_class = "mt-2 text-sm text-gray-500"
    error_wrapper = False


class PlainAdapter(BaseAdapter):
    """Plain HTML adapter (no CSS framework).

    Its classes come from ``LIVEVIEW_CONFIG["plain"]`` (``field_class_invalid``
    ``"error"``, ``error_class`` ``"error-message"``) like any other adapter's.
    """

    framework_name = "plain"
    required_marker = '<span aria-hidden="true"> *</span>'
    help_text_tag = "small"
    help_text_class = ""
    error_wrapper = True


# Registry of available adapters
_adapters: Dict[str, FrameworkAdapter] = {
    "bootstrap4": Bootstrap4Adapter(),
    "bootstrap5": Bootstrap5Adapter(),
    "tailwind": TailwindAdapter(),
    "plain": PlainAdapter(),
}


def get_adapter(framework: Optional[str] = None) -> FrameworkAdapter:
    """
    Get a framework adapter by name.

    Args:
        framework: Framework name ('bootstrap4', 'bootstrap5', 'tailwind', 'plain', or None)
                  If None, uses the configured default

    Returns:
        FrameworkAdapter instance
    """
    if framework is None:
        framework = config.get("css_framework", "bootstrap5")

    if framework is None:
        framework = "plain"

    return _adapters.get(framework, _adapters["plain"])


def register_adapter(name: str, adapter: FrameworkAdapter) -> None:
    """
    Register a custom framework adapter.

    Args:
        name: Adapter name
        adapter: FrameworkAdapter instance
    """
    _adapters[name] = adapter
