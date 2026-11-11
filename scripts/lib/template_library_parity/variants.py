"""Argument generation for the parity differential — pure, no Django (#3663).

A tag's calls are generated from the parameter NAMES it reads (its Python
signature, or the keys its compile function and its native handler look up):
``url``/``href`` get a path, ``show_*``/``is_*`` get ``True``, ``rows`` gets a
list. The heuristics are a sampling strategy, not a model of each tag: a tag
with an unusual shape is exercised less deeply than a simple one, and the
baseline's ``compared`` column says how many variants actually rendered in both
engines. Everything here is a fixed table or a pure function of its inputs, so
the generated templates are identical on every interpreter and every run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

SIMPLE_KINDS = ("simple_tag", "inclusion_tag")


@dataclass
class TagInfo:
    lib: str  # the {% load %} label
    name: str
    py_kind: str  # simple_tag / inclusion_tag / raw (…)
    sig: list[tuple[str, str, str]] = field(default_factory=list)  # (name, req|opt, kind)
    py_keys: list[str] = field(default_factory=list)  # raw tags: keys read in the source
    rust_keys: Optional[list[str]] = None  # None: no handler class found
    native: list[str] = field(default_factory=list)  # tag / block / assign / raw
    is_block: bool = False

    def readable(self) -> set[str]:
        """Every parameter some implementation of this tag can read.

        The Python signature (or the keys its compile function and node read),
        plus the keys the native handler reads, each followed through the
        ``render_*`` helpers they delegate to (``harness.discover``).
        """
        names = {
            s[0]
            for s in self.sig
            if s[0] not in ("context", "self") and s[2] not in ("VAR_POSITIONAL", "VAR_KEYWORD")
        }
        return names | set(self.py_keys) | set(self.rust_keys or ())

    def has_varargs(self) -> bool:
        return any(s[2] == "VAR_POSITIONAL" for s in self.sig)


BOOL_EXACT = {
    "open", "active", "disabled", "checked", "required", "collapsed", "dismissible", "loading",
    "readonly", "multiple", "autofocus", "expanded", "sticky", "animated", "donut", "inline",
    "bordered", "striped", "hoverable", "compact", "clearable", "searchable", "sortable",
    "filterable", "selectable", "paginated", "resizable", "draggable", "closable", "visible",
    "indeterminate", "dense", "loop", "autoplay", "pulse", "live", "editable", "primary", "block",
    "full_width", "fullwidth", "hover", "arrows", "dots", "wrap", "mask", "outline", "ghost",
}  # fmt: skip
BOOL_PREFIX = ("show_", "is_", "has_", "enable_", "allow_", "auto_", "can_")
NUM_EXACT = {
    "max_lines", "delay_in", "delay_out", "debounce", "unread_count", "page", "total_pages",
    "total", "count", "max", "min", "min_val", "max_val", "step", "width", "height", "delay",
    "duration", "limit", "max_toasts", "max_items", "max_size", "depth", "level", "percent",
    "progress", "current", "current_step", "length", "cols", "gap", "radius", "inner_radius",
    "stroke", "opacity", "speed", "seconds", "timeout", "index", "offset", "lines", "stars",
    "interval", "minutes", "hour", "value", "max_length", "rows_count", "per_page", "page_size",
    "threshold", "zoom", "scale", "amount", "decimals", "digits",
}  # fmt: skip
LIST_EXACT = {
    "items", "options", "steps", "rows", "segments", "nodes", "users", "data", "tabs", "events",
    "series", "points", "tags", "links", "messages", "suggestions", "results", "breadcrumbs",
    "columns", "actions", "cells", "files", "previews", "logs", "entries", "children", "panels",
    "pages", "nav", "sections", "selected_items", "values", "labels", "categories", "datasets",
    "bars", "slices", "cards", "members", "participants", "attachments", "mentions", "sources",
    "models", "commands", "notifications", "reactions", "cursors", "fields", "headers", "tasks",
    "milestones", "nodes_data", "records", "images", "slides", "stops", "pairs", "list",
}  # fmt: skip
URL_EXACT = {
    "url", "href", "src", "link", "action", "image", "endpoint", "redirect", "avatar_url",
    "image_url", "logo", "poster", "download", "upload", "upload_url", "src_url", "action_url",
}  # fmt: skip
COLOR_EXACT = {"color", "bg", "fill", "stroke", "bg_color", "text_color", "accent"}

ENUM = {
    "size": "md", "variant": "primary", "status": "success", "type": "info", "position": "top",
    "align": "left", "theme": "light", "kind": "info", "severity": "info", "tier": "pro",
    "state": "active", "mode": "view", "direction": "horizontal", "orientation": "horizontal",
    "shape": "circle", "style": "solid", "layout": "grid", "placement": "bottom",
    "language": "python", "currency": "USD", "tone": "info", "role": "user", "format": "short",
    "sort": "asc", "method": "post", "icon": "star", "id": "id1", "name": "fld",
    "for_input": "fld", "class": "my-class", "custom_class": "my-class", "lang": "python",
    "currency_code": "USD", "unit": "px",
}  # fmt: skip

# Child-only tags are only meaningful inside their parent.
PARENT = {
    "accordion_item": "accordion", "timeline_item": "timeline", "modal_description": "modal",
    "sidebar_item": "sidebar", "sidebar_section": "sidebar", "nav_item": "nav_menu",
    "app_sidebar": "app_shell", "app_header": "app_shell", "app_content": "app_shell",
    "toolbar_separator": "toolbar", "toolbar_overflow": "toolbar", "filter_select": "filter_bar",
    "filter_date_range": "filter_bar", "filter_search": "filter_bar", "menu_item": "dropdown_menu",
    "menu_divider": "dropdown_menu", "page_header_actions": "page_header",
    "context_menu_item": "context_menu", "palette_item": "command_palette",
    "input_addon": "input_group", "tab": "tabs",
}  # fmt: skip

BODY = "BODY {{ evil }} <b>lit</b>"

#: Hand-written composites for tags whose useful call is not a flat argument list.
EXTRA: dict[str, list[tuple[str, str]]] = {
    "split_pane": [
        (
            "two_panes",
            '{% split_pane direction="horizontal" id="sp1" initial="40" %}'
            "LEFT {{ evil }}{% pane %}RIGHT{% endsplit_pane %}",
        ),
        ("two_panes_noargs", "{% split_pane %}L{% pane %}R{% endsplit_pane %}"),
    ],
    "tabs": [
        (
            "tabs_with_tab",
            '{% tabs id="t1" active="a" %}{% tab id="a" label="A" %}AAA{% endtab %}'
            '{% tab id="b" label="B<x>" %}BBB {{ evil }}{% endtab %}{% endtabs %}',
        ),
        (
            "tabs_evil_label",
            '{% tabs id="t1" active=evil %}{% tab id=evil label=evil %}AAA{% endtab %}{% endtabs %}',
        ),
    ],
    "kbd": [
        ("kbd_positional", '{% kbd "Ctrl" "K" %}'),
        ("kbd_one", '{% kbd "Esc" %}'),
        ("kbd_evil", "{% kbd evil %}"),
    ],
    "icon": [
        (
            "icon_sizes",
            '{% icon name="check" size="lg" %}{% icon name="home" set="heroicons" %}'
            "{% icon name=evil %}",
        )
    ],
    "modal_description": [
        (
            "modal_with_description",
            '{% modal id="m1" title="T" open=True %}{% modal_description %}DESC {{ evil }}'
            "{% endmodal_description %}BODYTEXT{% endmodal %}",
        )
    ],
    "form_errors": [
        ("form_bound_errors", "{% form_errors form=bound_form %}"),
        ("form_errors_class", '{% form_errors form=bound_form custom_class="x" %}'),
    ],
    "field_error": [("field_bound_error", "{% field_error field=bound_form.name %}")],
    "expandable_text": [
        (
            "expandable_ok",
            '{% expandable_text max_lines=2 expanded=False toggle_event="t" more_label="More" '
            'less_label="Less" %}LONG TEXT {{ evil }}{% endexpandable_text %}',
        )
    ],
    "hover_card": [
        (
            "hover_ok",
            '{% hover_card position="top" delay_in=100 delay_out=100 %}CARD {{ evil }}'
            "{% endhover_card %}",
        )
    ],
    "markdown_textarea": [
        ("md_ok", '{% markdown_textarea name="body" value="# hi" rows=4 preview=False %}')
    ],
    "notification_popover": [
        (
            "np_ok",
            '{% notification_popover id="np" notifications=lst_dict unread_count=2 open=True '
            'toggle_event="t" mark_read_event="m" %}',
        )
    ],
    "filter_search": [
        (
            "fs_ok",
            '{% filter_search name="q" value="x" debounce=300 event="f" placeholder="Find" %}',
        )
    ],
    "component_preview": [
        (
            "cp_valid",
            '{% component_preview component_name="badge" component_type="inline" '
            "examples=cp_examples values=cp_values %}",
        )
    ],
    "tab": [
        (
            "tab_evil_in_tabs",
            '{% tabs id="t1" %}{% tab id=evil label=evil icon=evil %}X{% endtab %}{% endtabs %}',
        )
    ],
}

#: ``presets`` demands real registered preset names.
DROP = {"theme_preset_selector": {"presets"}}

#: Per-tag typical values where the generic heuristics produce unusable input.
TAG_OVERRIDES = {
    "skeleton_for": {"rows": "3", "columns": "2", "component": '"card"'},
    "icon": {"name": '"check"'},
    "markdown_textarea": {"rows": "4"},
}

_FALLBACK_NAMES = ("id", "class", "label", "title", "name", "value", "text")


def kind_of(param: str) -> str:
    if param.endswith("event"):
        return "event"
    if param in URL_EXACT:
        return "url"
    if param in COLOR_EXACT:
        return "color"
    if param in BOOL_EXACT or param.startswith(BOOL_PREFIX):
        return "bool"
    if param in NUM_EXACT:
        return "num"
    if param in LIST_EXACT:
        return "list"
    return "str"


def token(param: str, mode: str) -> str:
    """The template expression passed for ``param`` in the given mode."""
    k = kind_of(param)
    if mode == "typical":
        if k == "str" and param in ENUM:
            return '"%s"' % ENUM[param]
        return {
            "event": '"my_event"',
            "url": '"/path/"',
            "color": '"#336699"',
            "bool": "True",
            "num": "3",
            "list": "lst_dict",
            "str": '"Sample text"',
        }[k]
    if mode == "typical_str":
        return {"list": "lst_str"}.get(k) or token(param, "typical")
    if mode == "evil":
        return {
            "event": "evil",
            "url": "evil_url",
            "color": "evil",
            "str": "evil",
            "list": "lst_evil",
        }.get(k) or token(param, "typical")
    if mode == "evil_str":
        return {"list": "lst_evil_str"}.get(k) or token(param, "evil")
    if mode == "literal":  # a string LITERAL with HTML specials
        literal = '"a<i>b</i>&amp;\'c"'
        return {
            "event": literal,
            "url": '"/p?a=1&b=<2>"',
            "color": literal,
            "str": literal,
        }.get(k) or token(param, "typical")
    if mode == "safe":  # a mark_safe() string variable
        return {"str": "safe_html", "event": "safe_html"}.get(k) or token(param, "typical")
    if mode == "missing":
        return {
            "str": "undefined_var",
            "event": "undefined_var",
            "url": "undefined_var",
            "list": "undefined_var",
            "color": "undefined_var",
        }.get(k) or token(param, "typical")
    if mode == "none":
        return {
            "str": "none",
            "event": "none",
            "url": "none",
            "list": "none",
            "color": "none",
            "num": "none",
        }.get(k) or token(param, "typical")
    raise ValueError(mode)


def param_names(info: TagInfo) -> tuple[list[str], list[str], list[str]]:
    """``(names to pass, python signature names, names only the Rust side reads)``."""
    sig = [
        s[0]
        for s in info.sig
        if s[0] not in ("context", "self") and s[2] not in ("VAR_POSITIONAL", "VAR_KEYWORD")
    ]
    if info.py_kind in SIMPLE_KINDS:
        names = list(sig)  # strict signature: anything else is a TemplateSyntaxError in Django
    else:
        names = sorted(set(info.py_keys) | set(info.rust_keys or ()))
    rust_only = (
        sorted(set(info.rust_keys or ()) - set(sig))
        if (info.rust_keys and info.py_kind in SIMPLE_KINDS)
        else []
    )
    return names, sig, rust_only


def params_of(info: Optional[TagInfo]) -> list[str]:
    """The parameter names a CHILD tag is passed when rendered inside its parent."""
    if info is None:
        return []
    names, _, _ = param_names(info)
    if not names and info.py_kind not in SIMPLE_KINDS:
        names = list(_FALLBACK_NAMES)
    return names


@dataclass(frozen=True)
class Variant:
    name: str
    src: str
    invalid_usage: bool = False  # a call the Python tag must reject (required args missing)
    #: The same template WITHOUT the tag, for a tag that only renders inside a
    #: parent: what the tag itself contributed is the difference.
    bare: Optional[str] = None


#: Parameter kinds whose value is a string a template author can hand a
#: ``mark_safe`` value or hostile text.
STRING_KINDS = ("str", "event", "url", "color")


def passed_names(info: TagInfo) -> list[str]:
    """The parameter names the generated calls pass."""
    names, _, _ = param_names(info)
    names = [n for n in names if n not in DROP.get(info.name, ())]
    if not names and info.py_kind not in SIMPLE_KINDS:
        names = list(_FALLBACK_NAMES)
    return names


def passed_count(info: TagInfo) -> int:
    """How many distinct parameters the generated calls pass."""
    names = set(passed_names(info))
    if info.py_kind in SIMPLE_KINDS:
        names |= set(param_names(info)[2])  # the ``rust_only_keys`` call
    return len(names)


def wrap(
    label: str,
    name: str,
    args: str,
    is_block: bool,
    parent: Optional[str] = None,
    parent_args: str = "",
) -> str:
    head = "{% " + name + (" " + args if args else "") + " %}"
    inner = (head + BODY + "{% end" + name + " %}") if is_block else head
    if parent:
        open_tag = "{% " + parent + (" " + parent_args if parent_args else "") + " %}"
        inner = open_tag + inner + "{% end" + parent + " %}"
    return "{% load " + label + " %}" + inner


def make_variants(
    info: TagInfo,
    lookup: Callable[[str], Optional[TagInfo]],
    blockness: dict[str, bool],
) -> list[Variant]:
    """The calls to render for one tag.

    ``lookup`` finds a sibling tag of the same library (for a parent's
    children); ``blockness`` says which tags consume a body.
    """
    label, name = info.lib, info.name
    names = passed_names(info)
    parent = PARENT.get(name)
    parent_info = lookup(parent) if parent else None
    # A child is rendered inside its parent OPEN, or the parent hides it and
    # the child's own output never appears in any result.
    parent_args = "open=True" if parent_info and "open" in parent_info.readable() else ""
    bare = (
        "{% load "
        + label
        + " %}{% "
        + parent
        + (" " + parent_args if parent_args else "")
        + " %}{% end"
        + parent
        + " %}"
        if parent
        else None
    )
    out: list[Variant] = []

    def mk(variant: str, argstr: str) -> None:
        out.append(
            Variant(
                variant, wrap(label, name, argstr, info.is_block, parent, parent_args), False, bare
            )
        )

    required = [
        s[0]
        for s in info.sig
        if s[1] == "req" and s[0] != "context" and s[2] in ("POSITIONAL_OR_KEYWORD", "KEYWORD_ONLY")
    ]
    has_list = any(kind_of(n) == "list" for n in names)
    overrides = TAG_OVERRIDES.get(name, {})

    def args_with(mode: str) -> str:
        return " ".join(
            "%s=%s"
            % (
                n,
                overrides[n]
                if (n in overrides and mode not in ("missing", "none"))
                else token(n, mode),
            )
            for n in names
        )

    bools = [n for n in names if kind_of(n) == "bool"]
    fixed = list(dict.fromkeys(bools + [r for r in required if r in names]))

    for mode in (
        "typical",
        "typical_str",
        "evil",
        "evil_str",
        "literal",
        "safe",
        "missing",
        "none",
    ):
        if mode in ("typical_str", "evil_str") and not has_list:
            continue
        mk(mode, args_with(mode))
    # Escaping behaviour per parameter: ONE hostile / mark_safe value, set alone
    # (plus the boolean switches and required parameters, which a tag needs to
    # render at all: ``open=True`` for a modal). Setting every parameter at once
    # lets a branch-selecting one (``divider=``, ``heading=``) decide what is
    # rendered and hides the rest; a handler that mishandles ONE argument is
    # seen only when that argument is the one that varies.
    for mode in ("safe", "evil"):
        for n in names:
            if kind_of(n) in STRING_KINDS and token(n, mode) != token(n, "typical"):
                call = ["%s=%s" % (m, token(m, "typical")) for m in fixed if m != n] + [
                    "%s=%s" % (n, token(n, mode))
                ]
                mk("%s:%s" % (mode, n), " ".join(call))
    # only the required params, no others (a minimal valid call)
    if required:
        mk("required_only", " ".join("%s=%s" % (n, token(n, "typical")) for n in required))
    # positional use for simple/inclusion tags (documented: {% dj_button "Click" %})
    if info.py_kind in SIMPLE_KINDS:
        positional = [
            s[0] for s in info.sig if s[2] == "POSITIONAL_OR_KEYWORD" and s[0] != "context"
        ]
        if positional:
            mk("positional_1", token(positional[0], "typical"))
            if len(positional) > 1:
                mk(
                    "positional_2",
                    token(positional[0], "typical") + " " + token(positional[1], "typical"),
                )
        rust_only = param_names(info)[2]
        if rust_only:
            # Keys only the native handler reads: the Python tag rejects them, so
            # this is where "Rust accepts what Python refuses" is recorded.
            mk(
                "rust_only_keys",
                " ".join("%s=%s" % (n, token(n, "typical")) for n in [*names, *rust_only]),
            )
        if info.has_varargs():
            mk("varargs_typical", '"Ctrl" "K"')
            mk("varargs_safe", "safe_html safe_html")
            mk("varargs_literal", '"a<i>b</i>&amp;\'c" "<b>x</b>"')
            mk("varargs_evil", "evil evil")
    # no arguments at all: invalid when the Python tag has required params
    out.append(
        Variant(
            "noargs",
            wrap(label, name, "", info.is_block, parent, parent_args),
            bool(required),
            bare,
        )
    )
    # parent tags: render WITH their children (each child with typical args)
    kids = [c for c, p in PARENT.items() if p == name]
    if kids and info.is_block:
        inner = ""
        for c in kids:
            cargs = " ".join("%s=%s" % (n, token(n, "typical")) for n in params_of(lookup(c)))
            head = "{% " + c + (" " + cargs if cargs else "") + " %}"
            inner += (
                (head + "CHILD " + c + "{% end" + c + " %}") if blockness.get(c, False) else head
            )
        pargs = " ".join("%s=%s" % (n, token(n, "typical")) for n in names)
        out.append(
            Variant(
                "with_children",
                "{% load "
                + label
                + " %}{% "
                + name
                + (" " + pargs if pargs else "")
                + " %}"
                + inner
                + "{% end"
                + name
                + " %}",
            )
        )
    for variant, src in EXTRA.get(name, ()):
        out.append(Variant(variant, "{% load " + label + " %}" + src))
    return out
