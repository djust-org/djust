"""The reviewed baseline of the safe-markup taint guard outside ``djust.theming`` (#3774).

``scripts/lib/safe_markup_taint.py`` follows a value through ONE function to ``mark_safe`` /
``SafeString`` / the format string of ``format_html``. Over ``python/djust`` (tests and
``djust/theming`` excluded; theming has its own, empty, list in ``test_safe_markup_guard_3766.py``)
it flagged 125 sites. This module is the record of reading each one.

Every entry is a ``(path, function)`` pair, the number of flagged sinks in that function, the
reason the sinks are safe (what escapes or validates which value), and EVIDENCE: ``(path,
literal)`` pairs that must still appear as live code in their scope (the audited function itself,
or the named function or file; comments, docstrings, dead strings, ``if False:`` branches, code
after a ``return`` and nested ``def``s nothing reads do not count, see ``_dead_statements``). The test
(``python/tests/test_safe_markup_audit_3774.py``) fails when

* a site is flagged that has no entry (a new interpolated ``mark_safe``: classify it);
* a function has MORE sinks than audited (a sink was added to an audited function);
* an entry has no site any more, or a different number (delete or update it);
* an entry has no reason, or no evidence, or evidence that is no longer live code in its scope.

The guard is name-based and single-function (see its docstring), so "audited" means a person
traced each flagged value to its source, not that the guard proved it.

What this baseline can NOT see: an audited function is judged by its sink COUNT, so replacing an
escaped value with a raw one INSIDE an audited function leaves the guard exactly as flagged as it was.
That regression is what ``TestPayloadSweep`` is for; the baseline notices it only when the removed
line is one of the pinned evidence literals (``RibbonNode``'s ``e_text`` is not, and removing it fails only the sweep). The table in
``docs/internal/mark-safe-audit-3774.md`` is generated from this file by
``scripts/lib/safe_markup_audit.py --table`` (it resolves every evidence literal to a line).

Verdict codes: ``S`` safe (inputs are constants, escaped or validated). No ``U`` (unescaped raw
value reaching the markup) and no ``N`` (needs a decision) sites remain in this list: the one
``U`` the audit found, ``live_input``'s attribute NAME (``event=`` becomes ``dj-...`` unescaped),
was fixed in ``djust._html.attr_name`` rather than allowlisted, and the ``N`` items were filed
as their own issues: #3818 (live_input passthrough policy), #3819 (raw-markup slots), and #3820 (teaching the guard
so this list can shrink). #3818 and #3819 are decided and fixed (``djust._attr_policy``; the
``live_render`` placeholder follows Django's escaping rule; the route map is script-escaped), so those
entries cite the fix rather than a verdict N.

Refs #3774, #3766, #3775, #1859, #1468.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PKG = "python/djust/"
TT = PKG + "components/templatetags/"
DC = TT + "djust_components.py"
ADV = TT + "_advanced.py"
CH = TT + "_charts.py"
FM = TT + "_forms.py"
UT = PKG + "components/utils.py"
LT = PKG + "templatetags/live_tags.py"
PWA = PKG + "templatetags/djust_pwa.py"
HTML = PKG + "_html.py"

#: ``(path, literal)`` or ``(path, literal, scope)``. The literal must appear in the CODE (comments
#: and docstrings do not count) of its scope: by default the audited function itself when ``path``
#: is the audited function's file, else the whole file; a third element names a dotted function or
#: class in ``path`` (or ``"<module>"``) that holds it. At least one literal per entry must be in the
#: audited function's own body.
Evidence = tuple


@dataclass(frozen=True)
class Audit:
    sites: int
    reason: str
    evidence: tuple[Evidence, ...]
    #: Non-empty when the payload sweep cannot reach the sink: why not. Such an entry is held to
    #: the claim (the test fails if the sweep DOES reach it).
    unreached: str = ""


def S(sites: int, reason: str, *evidence: Evidence, unreached: str = "") -> Audit:
    return Audit(sites, reason, evidence, unreached)


# Evidence shared by many entries: the helper that vouches for a value.
SAFE_URL = (UT, "def safe_url(value: Any) -> str:")
URL_ATTR = (UT, "def url_attr(value: Any, *, image: bool = False, honor_safe: bool = True) -> str:")
CSS_COLOR = (UT, 'def safe_css_color(value: Any, default: str = "") -> str:')
CSS_LENGTH = (UT, 'def safe_css_length(value: Any, default: str = "") -> str:')
RICH_HTML = (UT, "def rich_html(value: Any) -> str:")
INTERP = (UT, 'return f"#{r:02x}{g:02x}{b:02x}"')
CE_IMPORT = "from django.utils.html import conditional_escape"

#: ``(path, function) -> Audit``
AUDITED: dict[tuple[str, str], Audit] = {
    (PKG + "assets/tags.py", "asset_tags"): S(
        1,
        "joins _tag() results; _tag builds every branch with format_html, so src and integrity are escaped",
        (PKG + "assets/tags.py", 'mark_safe("\\n".join(_tag(f) for f in files))'),
        (PKG + "assets/tags.py", "def _tag(file: AssetFile) -> SafeString:", "_tag"),
        (PKG + "assets/tags.py", '\'<script src="{}" integrity="{}"{}></script>\'', "_tag"),
    ),
    (PKG + "components/base.py", "Component.asset_tags"): S(
        1,
        "joins djust.assets.tags.asset_tags() results, which are format_html SafeStrings",
        (PKG + "components/base.py", "from djust.assets.tags import asset_tags"),
        (PKG + "assets/tags.py", "def asset_tags(name: str, variant: str | None = None"),
    ),
    (PKG + "components/icons.py", "render_icon"): S(
        1,
        "class names are the constant size label plus html.escape(custom_class); every extra attribute VALUE is html.escape'd and every extra attribute NAME is checked by attr_name (before #3774 the name was only html.escape'd, which cannot make a name safe: `x onfocus=alert(1) y` rendered a live handler; fixed, python/tests/test_attr_name_injection_3774.py); since #3818 the extra attributes first pass djust._attr_policy.passthrough_attrs (on* refused, script-bearing URLs refused / neutralised); px is int(); svg_inner is the bundled / DJUST_COMPONENTS_ICON_SETS (developer settings) markup keyed by name, never the caller's text",
        (PKG + "components/icons.py", "classes.append(_html.escape(custom_class))"),
        (PKG + "components/icons.py", "attr_val = _html.escape(str(val))"),
        (PKG + "components/icons.py", "attr_name = _checked_attr_name(key)"),
        (PKG + "components/icons.py", "html_attrs = passthrough_attrs({key.replace"),
        (PKG + "components/icons.py", "px = int(size)"),
        (PKG + "components/icons.py", "svg_inner = icon_data.get(name)"),
    ),
    (PKG + "forms.py", "FormMixin.as_live"): S(
        1,
        "joins adapter.render_field() output; BaseAdapter escapes wrapper/label/help text/errors/choices/values and (since #3774) checks attribute names in _build_tag; since #3818 the widget attrs it merges pass djust._attr_policy.passthrough_attrs (inline event handlers, script-bearing URLs and a submitting `type` are refused); a custom adapter owes the same (FrameworkAdapter contract)",
        (PKG + "forms.py", "html += self.as_live_field(field_name, adapter=adapter, **kwargs)"),
        (PKG + "frameworks.py", "escape(field.help_text)"),
        (PKG + "frameworks.py", '{attr_name(k)}="{escape(str(v))}"'),
        (PKG + "frameworks.py", "attrs.update(passthrough_attrs(merged))"),
        (PKG + "frameworks.py", 'passthrough_attrs({"type": widget_type})'),
    ),
    (PKG + "template_tags/lorem.py", "LoremTagHandler.render"): S(
        1,
        "wraps the paragraphs of django.utils.lorem_ipsum (a fixed corpus) in <p>; same as Django's own lorem tag",
        (PKG + "template_tags/lorem.py", "paras = paragraphs(count, common=common)"),
        (
            PKG + "template_tags/lorem.py",
            "from django.utils.lorem_ipsum import paragraphs, words",
            "<module>",
        ),
    ),
    (PWA, "djust_sw_register"): S(
        1,
        "nonce_attr is escape()'d in _nonce_attr; the URL and scope are _script_json(): json.dumps with < > & and U+2028/9 escaped, so neither can end the script element",
        (PWA, "safe_sw_url = _script_json(sw_url)"),
        (PWA, 'return f\' nonce="{escape(nonce)}"\' if nonce else ""', "_nonce_attr"),
        (PWA, "return json.dumps(value).translate(_SCRIPT_JSON_ESCAPES)", "_script_json"),
    ),
    (PWA, "djust_offline_indicator"): S(
        1,
        "texts and classes are escape()'d locals; show_when selects a constant visibility attribute; the style block is a constant plus the escaped nonce",
        (PWA, "safe_online_text = escape(online_text)"),
        (PWA, "safe_offline_class = escape(offline_class)"),
    ),
    (PWA, "djust_offline_styles"): S(
        1,
        "constant CSS plus the nonce attribute built by _nonce_attr (escape()'d)",
        (PWA, "nonce_attr = _nonce_attr(context)"),
        (PWA, 'return f\' nonce="{escape(nonce)}"\' if nonce else ""', "_nonce_attr"),
    ),
    (LT, "_client_config_html"): S(
        1,
        "the three meta prefixes are escape()'d; the route-map script is routing.get_route_map_script (format_html around json.dumps of developer URL config, which since #3819 is passed through escape_json_for_script so `<`, `>` and `&` cannot end the script whatever the config holds)",
        (LT, "api_escaped = escape(api_prefix)"),
        (PKG + "routing.py", "def get_route_map_script(request: Any = None) -> str:"),
        (PKG + "routing.py", "route_json = escape_json_for_script(json.dumps(all_routes))"),
    ),
    (LT, "live_input"): S(
        1,
        "html is build_tag output: every attribute VALUE is escape()'d, select options escape()'d, textarea content escaped; attribute NAMES (event=, passthrough keys) were not checked until #3774 (the audit's one U, fixed in djust._html.attr_name, python/tests/test_attr_name_injection_3774.py); the passthrough attributes are judged by djust._attr_policy.passthrough_attrs (#3818: on* and a submitting `type` refused, script-bearing URLs refused, other rejected schemes rendered `#`; python/tests/test_passthrough_attr_policy_3818.py)",
        (LT, "html = _render_textarea("),
        (LT, "return passthrough_attrs(out)", "_collect_passthrough_attrs"),
        (
            PKG + "_attr_policy.py",
            "def passthrough_attrs(attrs: Mapping[str, Any]) -> dict[str, Any]:",
        ),
        (HTML, "def attr_name(name: Any) -> str:"),
        (HTML, "parts.append(f'{key}=\"{escape(str(value))}\"')"),
        (
            LT,
            "options_html += f'<option value=\"{escape(str(cv))}\" {selected}>{escape(str(cl))}</option>'",
            "_render_select",
        ),
    ),
    (LT, "ColocatedHookNode.render"): S(
        1,
        "the name is the tag's parse-time literal (plus the view's module.qualname): template/code, not request data; it is escape()'d into data-hook; the body is </script>-escaped; the nonce is escape()'d. NOT escaped: the JS-comment banner `/* COLOCATED HOOK: <name> */` interpolates the name raw, so a name carrying `</script>` or `*/` would end the script or the comment; it is a parse-time literal the template author wrote, hence not exploitable, and the sweep drives this tag with a plain name for that reason",
        (LT, "safe_hook_name = escape(namespaced)"),
        (LT, "safe_body = _SCRIPT_CLOSE_RE.sub("),
        (LT, "nonce_attr = ' nonce=\"' + escape(nonce) + '\"' if nonce else \"\""),
    ),
    (LT, "djust_skeleton"): S(
        1,
        "shape is checked against _SKELETON_VALID_SHAPES; width/height pass _SKELETON_SIZE_RE; class_ goes through build_tag (escaped); count is int()-clamped",
        (LT, "if shape not in _SKELETON_VALID_SHAPES:"),
        (LT, "_SKELETON_SIZE_RE = re.compile(", "<module>"),
        (LT, "count_int = max(1, min(100, count_int))"),
    ),
    (LT, "_render_sticky_child_html"): S(
        2,
        "the wrapper's ids are escape()'d; the body is the child's own template render (autoescaped by the engine) stamped by _stamp_view_id, which escape()s view_id",
        (LT, "escaped_id = escape(view_id)"),
        (LT, "escaped_sticky_id = escape(sticky_id_value)"),
        (LT, "rendered_stamped = _stamp_view_id(rendered_inner, view_id)"),
    ),
    (LT, "live_render"): S(
        2,
        "ids and the CSP nonce are escape()'d, lazy['trigger'] is checked against ('flush', 'visible'), the child body is a template render stamped by _stamp_view_id. The one formerly raw piece, lazy['placeholder'], goes through conditional_escape since #3819: a marked-safe value is markup, a plain str is text (Django's own rule)",
        (LT, 'if lazy_config["trigger"] not in ("flush", "visible"):'),
        (
            LT,
            "_csp_nonce_attr = ' nonce=\"' + escape(_csp_nonce_raw) + '\"' if _csp_nonce_raw else \"\"",
        ),
        (LT, 'custom_placeholder = lazy_config["placeholder"]'),
        (LT, "inner = str(conditional_escape(custom_placeholder))"),
    ),
    (CH, "BarChartNode.render"): S(
        1,
        "width/height int() with a numeric fallback; values float(); title, labels, colour and class conditional_escape'd; every other interpolation is a float",
        (CH, "from django.utils.html import conditional_escape", "<module>"),
        (CH, 'lbl = conditional_escape(str(labels[i])) if i < len(labels) else ""'),
    ),
    (CH, "LineChartNode.render"): S(
        1,
        "width/height int(); values float(); series name and colour conditional_escape'd; coordinates are floats",
        (CH, "from django.utils.html import conditional_escape", "<module>"),
        (CH, 'name = conditional_escape(str(s.get("name", f"Series {si + 1}")))'),
    ),
    (CH, "PieChartNode.render"): S(
        1,
        "width/height int(), inner_radius float(); segment colour and label conditional_escape'd; the path data is floats",
        (CH, "from django.utils.html import conditional_escape", "<module>"),
        (CH, 'label = conditional_escape(str(seg.get("label", "")))'),
    ),
    (CH, "SparklineNode.render"): S(
        1,
        "width/height int(), stroke_width float(); colour conditional_escape'd; path data is floats",
        (CH, "stroke_width = float(stroke_width)"),
        (CH, 'e_color = conditional_escape(str(color)) if color else ""'),
    ),
    (CH, "HeatmapNode.render"): S(
        1,
        "cell_size int(); cell colours come from interpolate_color (a #rrggbb string built from ints); axis labels and title conditional_escape'd",
        (UT, 'return f"#{r:02x}{g:02x}{b:02x}"'),
        (CH, "cell_size = int(cell_size)"),
        (CH, "conditional_escape(str(lbl))"),
    ),
    (CH, "TreemapNode.render"): S(
        1,
        "width/height int(); the fill is a constant palette entry; label and title conditional_escape'd",
        (CH, "e_label = conditional_escape(label)"),
        (CH, "color = conditional_escape(str(self.COLORS[idx % len(self.COLORS)]))"),
    ),
    (CH, "CalendarHeatmapNode.render"): S(
        1,
        "year, cell_size, cell_gap int(); fills are the constant LEVELS; date_str is date.isoformat(); title conditional_escape'd",
        (CH, "year = int(year)"),
        (
            CH,
            'LEVELS = ["#ebedf0", "#9be9a8", "#40c463", "#30a14e", "#216e39"]',
            "CalendarHeatmapNode",
        ),
        (CH, "date_str = current.isoformat()"),
    ),
    (FM, "slider"): S(
        1,
        "min/max/step/value/value_end are int()'d; name, label, event and class conditional_escape'd",
        (FM, "value = int(value)"),
        (FM, "e_name = conditional_escape(name)"),
    ),
    (FM, "render_confirm_dialog"): S(
        1,
        "every kwarg is conditional_escape'd; `body` is the already-rendered tag body (Django nodelist.render, Rust block handler's rendered children), autoescaped by the engine",
        (FM, "e_confirm_event = conditional_escape(confirm_event)"),
        (
            FM,
            'body = self.nodelist.render(context) if self.nodelist is not None else ""',
            "ConfirmDialogNode.render",
        ),
        (PKG + "components/rust_handlers.py", "return str(render_confirm_dialog(kw, body))"),
    ),
    (FM, "render_popconfirm"): S(
        1,
        "every kwarg is conditional_escape'd; `content` is the rendered tag body (nodelist.render / the Rust block handler's children)",
        (FM, "e_cancel_event = conditional_escape(cancel_event)"),
        (
            FM,
            "return render_popconfirm(kw, self.nodelist.render(context))",
            "PopconfirmNode.render",
        ),
        (
            PKG + "components/rust_handlers.py",
            "return str(render_popconfirm(_parse_args(args, context), content))",
        ),
    ),
    (FM, "dj_form"): S(
        1,
        "_render_field escapes every value (label, help, errors, choices, widget attrs); the action goes through safe_url; hidden-field names and values conditional_escape'd",
        (FM, "e_action = safe_url(action)"),
        (
            FM,
            'def _render_field(bound_field: Any, event_prefix: Any = "") -> Any:',
            "_render_field",
        ),
        (UT, "def safe_url(value: Any) -> str:"),
    ),
    (FM, "model_table"): S(
        1,
        "every row value is conditional_escape(str(v))'d before the table is built; page and total_pages int(); event names and labels conditional_escape'd",
        (FM, 'safe_row[k] = conditional_escape(str(v)) if v is not None else ""'),
        (FM, "total_pages = int(total_pages)"),
    ),
    (ADV, "CalendarViewNode.render"): S(
        1,
        "year, month, start_day int(); month and day names from calendar; event titles conditional_escape'd; event colours through safe_css_color",
        (ADV, "year = int(year)"),
        (ADV, "safe_css_color("),
        (ADV, "e_month = conditional_escape(month_name)"),
    ),
    (ADV, "GanttChartNode.render"): S(
        1,
        "width/row_height int(); start/duration/progress float(); task name, colour and unit label conditional_escape'd",
        (ADV, "e_name = conditional_escape(name)"),
        (ADV, "row_height = int(row_height)"),
    ),
    (ADV, "PivotTableNode.render"): S(
        1,
        "row and column headers, title and class conditional_escape'd; cells print _format_val (int/float text)",
        (ADV, "def _format_val(v: Any) -> Any:", "PivotTableNode._format_val"),
        (ADV, "conditional_escape(rk)"),
    ),
    (ADV, "OrgChartNode.render"): S(
        1,
        "the tree comes from _render_node, which conditional_escape's id, name and title and sends the avatar through url_attr",
        (ADV, "tree_html = self._render_node(root_id, node_map, children, e_event)"),
        (
            ADV,
            "def _render_node(self, nid: Any, node_map: Any, children: Any, e_event: Any) -> Any:",
            "OrgChartNode._render_node",
        ),
        (ADV, 'src="{url_attr(avatar, image=True)}"', "OrgChartNode._render_node"),
    ),
    (ADV, "ComparisonTableNode.render"): S(
        1,
        "_render_value returns constants or conditional_escape(str(val)); plan and feature names escaped",
        (ADV, 'name = conditional_escape(str(plan.get("name", "")))'),
        (ADV, "def _render_value(val: Any) -> Any:", "ComparisonTableNode._render_value"),
        (ADV, "return conditional_escape(str(val))", "ComparisonTableNode._render_value"),
    ),
    (ADV, "MasonryGridNode.render"): S(
        1,
        "columns and gap int(); item content and class conditional_escape'd",
        (ADV, "columns = max(1, int(columns))"),
        (ADV, 'content = conditional_escape(item.get("content", ""))'),
    ),
    (ADV, "LiveIndicatorNode.render"): S(
        1,
        "name, field, action conditional_escape'd; the avatar goes through url_attr(image=True)",
        (ADV, "e_avatar = url_attr(avatar, image=True)"),
        (UT, "def url_attr(value: Any, *, image: bool = False, honor_safe: bool = True) -> str:"),
    ),
    (ADV, "ActivityFeedNode.render"): S(
        1,
        "max int(); every event field conditional_escape'd; the avatar goes through url_attr(image=True)",
        (ADV, 'max_items = int(kw.get("max", 50))'),
        (ADV, 'avatar_src = url_attr(event.get("avatar", ""), image=True)'),
    ),
    (ADV, "MapPickerNode.render"): S(
        1,
        "lat/lng float(), zoom int(); height through safe_css_length; the Leaflet attributes come from map_picker_attrs, which html.escape's every value and int()s the zoom",
        (ADV, 'e_height = conditional_escape(safe_css_length(height, "400px"))'),
        (PKG + "components/components/map_picker.py", "def map_picker_attrs("),
        (PKG + "components/components/map_picker.py", "f' data-tile-url=\"{html.escape(tiles)}\"'"),
    ),
    (ADV, "PromptEditorNode.render"): S(
        1,
        "the template text is conditional_escape'd FIRST; {{var}} tokens are \\w+ and are replaced by conditional_escape'd values inside <mark>; rows int()",
        (ADV, "preview_text = conditional_escape(str(tmpl))"),
        (ADV, "e_v = conditional_escape(v)"),
    ),
    (ADV, "VoiceInputNode.render"): S(
        1,
        "event and lang conditional_escape'd; the wrapper, disclosure and status come from voice_extras, which html.escape's the disclosure and int()s max_seconds",
        (ADV, 'extras = voice_extras(kw.get("disclosure"), kw.get("max_seconds", 60))'),
        (
            PKG + "components/components/voice_input.py",
            "def voice_extras(disclosure: Any = None, max_seconds: Any = 60) -> VoiceExtras:",
        ),
        (PKG + "components/components/voice_input.py", "html.escape(text)"),
    ),
    (ADV, "ErrorPageNode.render"): S(
        1,
        "code int(); the action URL goes through safe_url; title, message and label conditional_escape'd",
        (ADV, "e_url = safe_url(str(action_url))"),
        (UT, "def safe_url(value: Any) -> str:"),
    ),
    (ADV, "ImageUploadPreviewNode.render"): S(
        1,
        "the upload attributes come from upload_attrs (html.escape'd slot, int() size); thumbnails go through url_attr(image=True); name, event, accept and class conditional_escape'd; max int()",
        (PKG + "components/components/image_upload_preview.py", "def upload_attrs("),
        (PKG + "components/components/image_upload_preview.py", "html.escape(slot)"),
        (ADV, "e_url = url_attr(url, image=True)"),
    ),
    (ADV, "AnimatedNumberNode.render"): S(
        1,
        "value float(); duration and decimals int(); prefix, suffix, separator and the formatted number conditional_escape'd",
        (ADV, "e_formatted = conditional_escape(formatted)"),
        (ADV, "decimals = int(decimals)"),
    ),
    (ADV, "RibbonNode.render"): S(
        1,
        "position is checked against the four corner names; the variant class is a constant from VARIANT_MAP; text and class conditional_escape'd",
        (ADV, 'variant_cls = self.VARIANT_MAP.get(str(variant), "dj-ribbon--primary")'),
        (ADV, 'classes.append(f"dj-ribbon--{pos}")'),
    ),
    (ADV, "BreadcrumbDropdownNode.render"): S(
        1,
        "labels conditional_escape'd; hrefs go through safe_url; the separator is conditional_escape'd",
        (ADV, "e_url = safe_url(str(url))"),
        (ADV, "e_sep = conditional_escape(str(separator))"),
    ),
    (ADV, "DataCardGridNode.render"): S(
        1,
        "title, description and category are conditional_escape'd (the raw first `cat` only feeds escape()); the image goes through url_attr; columns int()",
        (ADV, 'cat = conditional_escape(str(it.get(fk, "")))'),
        (ADV, "e_img = url_attr(image, image=True)"),
    ),
    (ADV, "AgentStepNode.render"): S(
        1,
        "status is checked against STATUS_ICONS' keys before it reaches the class; the icon is a constant entity; tool, duration and content conditional_escape'd",
        (ADV, 'status = str(status) if str(status) in self.STATUS_ICONS else "pending"'),
        (ADV, "STATUS_ICONS = {", "AgentStepNode"),
    ),
    (ADV, "QRCodeNode.render"): S(
        1,
        "px comes from SIZE_MAP's ints or int(); fg, bg and data conditional_escape'd; the modules are floats",
        (ADV, "px = self.SIZE_MAP[size]"),
        (ADV, "e_data = conditional_escape(str(data))"),
    ),
    (DC, "render_modal_description"): S(
        1,
        "`content` is the rendered {% modal_description %} body (nodelist.render / the Rust block handler)",
        (DC, "f'<div class=\"dj-modal__description\"{_MODAL_DESCRIPTION_MARKER}>{content}</div>'"),
        (
            DC,
            "return render_modal_description(self.nodelist.render(context))",
            "ModalDescriptionNode.render",
        ),
        (PKG + "components/rust_handlers.py", "return str(render_modal_description(content))"),
    ),
    (DC, "render_modal"): S(
        2,
        "title, close event and ids conditional_escape'd; size from a four-key dict, role from a two-way test, heading level int() in 2..6; `content` is the rendered tag body",
        (DC, 'modal_id = conditional_escape(kw.get("id", "modal"))'),
        (DC, "if not 2 <= level <= 6:"),
        (DC, "return render_modal(kw, self.nodelist.render(context))", "ModalNode.render"),
    ),
    (DC, "TooltipNode.render"): S(
        1,
        "text, position and component_id conditional_escape'd; tip_id is the escaped component id or a counter; content is the rendered body",
        (DC, 'tip_id = f"{conditional_escape(component_id)}-tip"'),
        (DC, "_tooltip_id_counter = itertools.count(1)", "<module>"),
    ),
    (DC, "dj_textarea"): S(
        1,
        "rows int(); name, label, value, placeholder, error, helper and event conditional_escape'd",
        (DC, "e_value = conditional_escape(value)"),
        (DC, "rows = int(rows)"),
    ),
    (DC, "breadcrumb"): S(
        1,
        "labels conditional_escape'd; urls go through safe_url",
        (DC, "e_url = safe_url(url)"),
        (UT, "def safe_url(value: Any) -> str:"),
    ),
    (DC, "code_block"): S(
        1,
        "theme must match [A-Za-z0-9_-]+ and is escaped; language, filename and code conditional_escape'd; the stylesheet tag is asset_tags (format_html); the hljs JSON is conditional_escape'd into data-hljs",
        (DC, "safe_theme = theme if _re.match("),
        (DC, 'data-hljs="{conditional_escape(hljs_json)}"'),
    ),
    (DC, "combobox"): S(
        1,
        "ids, names, labels, values and options conditional_escape'd; aria attributes come from field_aria_attrs, which escapes its ids",
        (DC, "aria = field_aria_attrs(str(id_prefix), error, helper, describedby, invalid)"),
        (DC, "def field_aria_attrs(", "field_aria_attrs"),
        (DC, 'ov = conditional_escape(str(opt.get("value", "")))'),
    ),
    (DC, "render_popover"): S(
        1,
        "trigger, placement, title and ids conditional_escape'd; `content` is the rendered tag body",
        (DC, "e_placement = conditional_escape(placement)"),
        (DC, "return render_popover(kw, self.nodelist.render(context))", "PopoverNode.render"),
    ),
    (DC, "render_collapsible"): S(
        1,
        "trigger, event and ids conditional_escape'd; `content` is the rendered tag body",
        (
            DC,
            "return render_collapsible(kw, self.nodelist.render(context))",
            "CollapsibleNode.render",
        ),
        (DC, "f'<span class=\"collapsible-label\">{conditional_escape(trigger)}</span>'"),
    ),
    (DC, "notification_center"): S(
        1,
        "unread_count int(); id, message, time and event names conditional_escape'd",
        (DC, "unread_count = int(unread_count)"),
        (DC, 'msg = conditional_escape(str(n.get("message", n.get("msg", ""))))'),
    ),
    (DC, "carousel"): S(
        1,
        "active int(); src through url_attr(image=True); alt, caption and event names conditional_escape'd",
        (DC, 'src = url_attr(img.get("src", img.get("url", "")), image=True)'),
        (DC, "active = int(active)"),
    ),
    (DC, "tree_view"): S(
        1,
        "node id and label conditional_escape'd; depth is a number; event names conditional_escape'd",
        (DC, 'nid = conditional_escape(str(node.get("id", "")))'),
        (DC, "indent = depth * 1.25"),
    ),
    (DC, "render_context_menu"): S(
        1,
        "label and id conditional_escape'd; `content` is the rendered tag body",
        (
            DC,
            "return render_context_menu(kw, self.nodelist.render(context))",
            "ContextMenuNode.render",
        ),
        (DC, "f'<div class=\"ctx-trigger\">{conditional_escape(label)}</div>'"),
    ),
    (DC, "date_picker"): S(
        1,
        "year and month int() (month_name is calendar's); the selected, range, label, error and event values conditional_escape'd; date_str is built from ints",
        (DC, "year = int(year) if year else today.year"),
        (DC, "e_selected = conditional_escape(selected)"),
    ),
    (DC, "table_of_contents"): S(
        1,
        "level int(); id and label conditional_escape'd",
        (DC, 'level = int(item.get("level", 1))'),
        (DC, 'iid = conditional_escape(str(item.get("id", "")))'),
    ),
    (DC, "virtual_list"): S(
        1,
        "total, page, page_size and item_height int() (one except resets all four); labels and the event conditional_escape'd",
        (DC, "item_height = int(item_height)"),
        (DC, 'label = conditional_escape(str(item.get("label", item.get("title", str(item)))))'),
    ),
    (DC, "rich_text_editor"): S(
        1,
        "the value goes through rich_html (the Markdown sanitizer unless it is SafeData); height through safe_css_length; name, event, label and placeholder conditional_escape'd",
        (UT, "def rich_html(value: Any) -> str:"),
        (DC, "e_value = rich_html(value)"),
        (UT, 'def safe_css_length(value: Any, default: str = "") -> str:'),
    ),
    (DC, "otp_input"): S(
        1,
        "digits int() clamped to 1..12; the id is a uuid hex; name, label and event conditional_escape'd",
        (DC, "digits = max(1, min(12, digits))"),
        (DC, 'uid = f"otp-{uuid.uuid4().hex[:6]}"'),
    ),
    (DC, "number_stepper"): S(
        1,
        "value, step, min and max int(); name, label and event conditional_escape'd",
        (DC, 'min_attr = f\' min="{int(min_val)}"\' if min_val is not None else ""'),
        (DC, "step = int(step)"),
    ),
    (DC, "fab"): S(
        1,
        "position is checked against four names; icon, event, label, size and variant conditional_escape'd",
        (DC, 'pos_cls = position if position in valid_positions else "bottom-right"'),
        (DC, "e_icon = conditional_escape(icon)"),
    ),
    (DC, "CalloutNode.render"): S(
        1,
        "the icon is ICONS[callout_type] (a constant) only when callout_type is a key; the type, title, icon text and class are conditional_escape'd; content is the rendered body",
        (DC, "elif callout_type in self.ICONS:"),
        (DC, "e_type = conditional_escape(str(callout_type))"),
    ),
    (DC, "rich_select"): S(
        1,
        "variant names match ^[a-z0-9][a-z0-9-]{0,31}$ (_VARIANT_NAME_RE); ids, names, values, labels, placeholder escaped; aria from field_aria_attrs; option bodies from _rich_select_option_html, which escapes",
        (
            PKG + "components/components/rich_select.py",
            '_VARIANT_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")',
        ),
        (
            DC,
            "def _rich_select_option_html(opt: Any, is_display: Any = False) -> Any:",
            "_rich_select_option_html",
        ),
        (DC, 'uid = conditional_escape(id or f"rs-{name}")'),
    ),
    (DC, "ToastContainerNode.render"): S(
        1,
        "position is checked against ALLOWED_POSITIONS; max_toasts int(); class conditional_escape'd",
        (DC, "if position not in self.ALLOWED_POSITIONS:"),
        (DC, "max_toasts = int(max_toasts)"),
    ),
    (DC, "responsive_image"): S(
        1,
        "src and placeholder through url_attr(image=True); srcset through srcset_attr; aspect-ratio through safe_css_ratio; alt, sizes and class conditional_escape'd",
        (DC, "e_srcset = srcset_attr(srcset)"),
        (DC, 'aspect_ratio = safe_css_ratio(aspect_ratio, "") if aspect_ratio else ""'),
        (UT, "def srcset_attr(value: Any, *, honor_safe: bool = True) -> str:"),
    ),
    (DC, "render_theme_toggle"): S(
        1,
        "mode, icon and label come from the constant _THEME_TOGGLE_MODES; current, event, class and id conditional_escape'd; the icons are render_icon output",
        (DC, "_THEME_TOGGLE_MODES = (", "<module>"),
        (DC, "e_current = conditional_escape(current)"),
    ),
    (DC, "PageHeaderNode.render"): S(
        1,
        "title, subtitle, description and class conditional_escape'd; the breadcrumb and actions slots are rendered nodelists",
        (DC, "e_title = conditional_escape(str(title))"),
        (DC, "breadcrumb_content = self.nodelist.render(context)"),
    ),
    (DC, "SidebarNode.render"): S(
        1,
        "title, class, ids and events conditional_escape'd; items come from _render_item (labels escaped, hrefs through safe_url)",
        (DC, 'menu_html = f\'<ul class="dj-sidebar__menu">{"".join(parts)}</ul>\''),
        (
            DC,
            "def _render_item(self, item: Any, context: Any, active_path: Any, level: Any = 0) -> Any:",
            "SidebarNode._render_item",
        ),
        (DC, "f'href=\"{safe_url(href)}\">'", "SidebarNode._render_item"),
    ),
    (DC, "NavMenuNode.render"): S(
        1,
        "brand, class, ids and events conditional_escape'd; brand_href through safe_url; items from _render_nav_item (escaped, safe_url)",
        (
            DC,
            'items_html = "".join(self._render_nav_item(item, context, active) for item in items)',
        ),
        (
            DC,
            "def _render_nav_item(self, item: Any, context: Any, active_path: Any) -> Any:",
            "NavMenuNode._render_nav_item",
        ),
        (DC, 'f\'<a class="dj-nav__brand" href="{safe_url(brand_href)}">\''),
    ),
    (DC, "FilterBarNode.render"): S(
        1,
        "every control's name, label, value, option and event is conditional_escape'd; debounce int()",
        (DC, 'name = conditional_escape(nkw.get("name", ""))'),
        (DC, 'dj-debounce="{int(debounce)}"'),
    ),
    (DC, "AvatarGroupNode.render"): S(
        1,
        "size, class, name and initials conditional_escape'd; src through url_attr(image=True); max int()",
        (DC, "e_size = conditional_escape(str(size))"),
        (DC, 'max_display = int(kw.get("max", 5))'),
    ),
    (DC, "ThinkingIndicatorNode.render"): S(
        1,
        "status is checked against VALID_STATUSES before it reaches the class; label and class escaped",
        (
            DC,
            'VALID_STATUSES = {"thinking", "searching", "generating", "tool_use", "idle"}',
            "ThinkingIndicatorNode",
        ),
        (DC, 'safe_status = status if status in self.VALID_STATUSES else "thinking"'),
    ),
    (DC, "FeedbackWidgetNode.render"): S(
        1,
        "mode is checked against VALID_MODES; the buttons are constants plus the conditional_escape'd event, an int star index and escaped emoji keys",
        (DC, 'VALID_MODES = {"thumbs", "stars", "emoji"}', "FeedbackWidgetNode"),
        (DC, "if mode not in self.VALID_MODES:"),
    ),
    (DC, "ApprovalGateNode.render"): S(
        1,
        "risk is checked against VALID_RISKS before it reaches the class; message, labels, events and class conditional_escape'd; the icons are constants",
        (DC, 'VALID_RISKS = {"low", "medium", "high", "critical"}', "ApprovalGateNode"),
        (DC, "if risk not in self.VALID_RISKS:"),
    ),
    (DC, "SourceCitationNode.render"): S(
        1,
        "idx int(); title conditional_escape'd; the url is safe_url (escaped, scheme-checked) and is also the link text; relevance float()",
        (DC, 'e_url = safe_url(str(url)) if url else ""'),
        (DC, "idx = int(index)"),
    ),
    (DC, "ModelSelectorNode.render"): S(
        1,
        "name, event, placeholder, class, value and every option conditional_escape'd (_option_inner escapes label, description, context window and tier)",
        (DC, "def _option_inner(self, opt: Any) -> Any:", "ModelSelectorNode._option_inner"),
        (DC, 'data-value="{conditional_escape(ov)}"'),
    ),
    (DC, "ChatBubbleNode.render"): S(
        1,
        "name, text, time, class conditional_escape'd; the avatar through url_attr(image=True); status must be in VALID_STATUSES; side is one of two constants",
        (DC, "e_avatar = url_attr(avatar_src, image=True)"),
        (DC, "if status and status in self.VALID_STATUSES:"),
    ),
    (DC, "PresenceAvatarsNode.render"): S(
        1,
        "max int(); name and initials conditional_escape'd; src through url_attr(image=True); status must be in VALID_STATUSES",
        (DC, 'safe_status = status if status in self.VALID_STATUSES else "online"'),
        (DC, 'max_display = int(kw.get("max", 5))'),
    ),
    (DC, "MentionsInputNode.render"): S(
        1,
        "id, name, initials, placeholder, event and class conditional_escape'd; the avatar through url_attr(image=True); the users JSON is conditional_escape'd into data-users",
        (DC, "users_json = conditional_escape(_json.dumps(users, default=str))"),
        (DC, 'uname = conditional_escape(str(user.get("name", "")))'),
    ),
    (DC, "CookieConsentNode.render"): S(
        1,
        "the privacy URL goes through safe_url; message, labels and events conditional_escape'd",
        (DC, "e_url = safe_url(str(privacy_url))"),
        (UT, "def safe_url(value: Any) -> str:"),
    ),
    (DC, "FormArrayNode.render"): S(
        1,
        "min and max int(); name, events, labels and each row value conditional_escape'd; a custom row body is a rendered nodelist",
        (DC, "min_rows = int(min_rows)"),
        (DC, "val = conditional_escape("),
    ),
    (DC, "render_dropdown_menu"): S(
        1,
        "label, event, class and align conditional_escape'd; items come from render_menu_item (labels escaped, href through url_attr); a block body is the rendered tag body",
        (DC, "e_label = conditional_escape(str(label))"),
        (
            DC,
            "def render_menu_item(item: Any, event_attrs: Any = None) -> str:",
            "render_menu_item",
        ),
        (DC, 'href="{url_attr(item["href"])}"', "render_menu_item"),
    ),
    (DC, "MeterNode.render"): S(
        1,
        "total int(), value float(); the colour goes through safe_css_color; labels and legend values conditional_escape'd",
        (DC, 'color = conditional_escape(safe_css_color(seg.get("color", ""), ""))'),
        (DC, "total = int(total)"),
        (UT, 'def safe_css_color(value: Any, default: str = "") -> str:'),
    ),
    (DC, "SortableGridNode.render"): S(
        1,
        "columns int(); gap through safe_css_length then conditional_escape; id and label conditional_escape'd; thumbnail through url_attr(image=True)",
        (DC, 'gap = safe_css_length(kw.get("gap", "0.75rem"), "0.75rem")'),
        (DC, "e_thumb = url_attr(thumbnail, image=True)"),
    ),
    (DC, "ImageCropperNode.render"): S(
        1,
        "src through url_attr(image=True); event, class, ratio and alt conditional_escape'd; min sizes int(); cropper_extras is constant markup",
        (DC, "e_src = url_attr(src, image=True)"),
        (PKG + "components/components/image_cropper.py", "def cropper_extras() -> CropperExtras:"),
    ),
    (DC, "SignaturePadNode.render"): S(
        1,
        "name, event, pen colour and class conditional_escape'd; width, height and pen width int(); pad_extras html.escape's the label and int()s max_bytes",
        (PKG + "components/components/signature_pad.py", "e_label = html.escape(text)"),
        (DC, "pw = int(pen_width)"),
    ),
    (DC, "ResizablePanelNode.render"): S(
        1,
        "direction is reset unless it is 'horizontal' or 'vertical'; min, max and initial size go through safe_css_length then conditional_escape; class and event conditional_escape'd",
        (DC, 'if direction not in ("horizontal", "vertical"):'),
        (DC, 'e_initial = conditional_escape(safe_css_length(initial_size, "50%"))'),
        (UT, 'def safe_css_length(value: Any, default: str = "") -> str:'),
    ),
    (DC, "LightboxNode.render"): S(
        1,
        "the image through url_attr(image=True); alt, caption, events and class conditional_escape'd; idx int()",
        (DC, 'e_src = url_attr(img.get("src", ""), image=True)'),
        (DC, "idx = int(active)"),
    ),
    (DC, "DashboardGridNode.render"): S(
        1,
        "columns and every panel cell are clamp_columns / clamp_cells ints; gap and row height through safe_css_length then conditional_escape; id, title and content conditional_escape'd",
        (DC, "col, row, w, h = clamp_cells("),
        (PKG + "components/components/dashboard_grid.py", "def clamp_cells("),
        (DC, 'e_row_height = conditional_escape(safe_css_length(row_height, "200px"))'),
    ),
}

#: Flagged sites that were fixed by sourcing the escaper from where the guard can see it (#3774):
#: ``_advanced.py`` / ``_charts.py`` / ``_forms.py`` imported ``conditional_escape`` through
#: ``._registry`` (a re-export), and the guard trusts an escaper only by ITS ORIGIN
#: (``django.utils.html``), never by spelling. They are no longer flagged, so they have no entry above.
RESOURCED: dict[str, tuple[str, ...]] = {
    "_advanced.py": (
        "TourNode.render",
        "GanttChartNode.render (2 sites)",
        "DiffViewerNode.render (2 sites)",
        "PivotTableNode.render",
        "OrgChartNode.render",
        "ComparisonTableNode.render",
        "MasonryGridNode.render",
        "CursorsOverlayNode.render",
        "LiveIndicatorNode.render",
        "CollabSelectionNode.render",
        "ReactionsNode.render",
        "CronInputNode.render",
    ),
    "_charts.py": (
        "BarChartNode.render",
        "LineChartNode.render",
        "PieChartNode.render (2 sites)",
        "SparklineNode.render",
        "HeatmapNode.render (2 sites)",
        "TreemapNode.render",
    ),
    "_forms.py": (
        "search_input",
        "password_input",
        "autocomplete",
        "dependent_select",
        "currency_input",
        "form_errors",
        "field_error",
    ),
}

#: Directories the scan skips: tests, and ``djust/theming`` (its list lives in the guard's own test).
EXCLUDED_PARTS = frozenset({"tests", "theming"})


def package_files(root: Path = ROOT) -> list[Path]:
    out = []
    for path in sorted((root / "python/djust").rglob("*.py")):
        rel = path.relative_to(root / "python/djust")
        if (
            EXCLUDED_PARTS & set(rel.parts)
            or path.name.startswith("test_")
            or path.name == "conftest.py"
        ):
            continue
        out.append(path)
    return out


def scan(root: Path = ROOT) -> dict[tuple[str, str], list[str]]:
    """``(path, function) -> [finding, ...]`` for every flagged sink in the audited tree."""
    from scripts.lib.safe_markup_taint import find_unsafe_markup

    found: dict[tuple[str, str], list[str]] = {}
    for path in package_files(root):
        rel = str(path.relative_to(root))
        for finding in find_unsafe_markup(path.read_text()):
            found.setdefault((rel, finding.function), []).append(str(finding))
    return found


def reconcile(
    found: dict[tuple[str, str], list[str]], audited: dict[tuple[str, str], Audit]
) -> dict[str, list]:
    """Everything wrong between what the guard flags and what was audited, by kind."""
    problems: dict[str, list] = {
        "unaudited": [],  # flagged, no entry
        "more_sites": [],  # flagged more sinks than the entry audited
        "stale": [],  # entry, nothing flagged
        "fewer_sites": [],  # entry audited more sinks than are flagged now
        "no_reason": [],
        "no_evidence": [],
        "no_own_evidence": [],  # no evidence literal pinned to the audited function's own body
    }
    for key, hits in found.items():
        entry = audited.get(key)
        if entry is None:
            problems["unaudited"].append((key, hits))
        elif len(hits) > entry.sites:
            problems["more_sites"].append((key, entry.sites, hits))
    for key, entry in audited.items():
        hits = found.get(key)
        if hits is None:
            problems["stale"].append(key)
        elif len(hits) < entry.sites:
            problems["fewer_sites"].append((key, entry.sites, len(hits)))
        if not entry.reason.strip():
            problems["no_reason"].append(key)
        if not entry.evidence:
            problems["no_evidence"].append(key)
        elif not any(_scope_of(key, ev) == key[1] and ev[0] == key[0] for ev in entry.evidence):
            problems["no_own_evidence"].append(key)
    return problems


def _scope_of(key: tuple[str, str], evidence: tuple) -> str:
    """Where an evidence literal must appear: the audited function itself when it is in the audited
    function's file, the whole file (``<module>``) for a helper elsewhere, or the dotted scope the
    entry names as a third element."""
    if len(evidence) > 2:
        return evidence[2]
    return key[1] if evidence[0] == key[0] else MODULE


MODULE = "<module>"


@lru_cache(maxsize=None)
def _text(path: str, root: Path) -> str:
    return (root / path).read_text()


@lru_cache(maxsize=None)
def _tree(path: str, root: Path) -> ast.Module:
    return ast.parse(_text(path, root))


def _constant_truth(test: ast.expr) -> bool | None:
    """The truth of a condition that is a literal (``False``, ``0``, ``None``, ``not True``), else
    ``None``: a name, a call or a comparison is not decided here."""
    if isinstance(test, ast.Constant):
        return bool(test.value)
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        inner = _constant_truth(test.operand)
        return None if inner is None else not inner
    return None


def _is_inert_statement(stmt: ast.stmt) -> bool:
    """A bare constant (a docstring, a string left in a function body, ``...``): it does nothing."""
    if not isinstance(stmt, ast.Expr):
        return False
    value = stmt.value
    if isinstance(value, ast.Constant):
        return True
    return isinstance(value, ast.JoinedStr) and all(
        isinstance(part, ast.Constant) for part in value.values
    )


def _blocks(node: ast.AST) -> list[list[ast.stmt]]:
    """Every list of statements a node directly holds (body, else, finally, each handler)."""
    out = []
    for field in ("body", "orelse", "finalbody"):
        block = getattr(node, field, None)
        if isinstance(block, list) and block and isinstance(block[0], ast.stmt):
            out.append(block)
    for handler in getattr(node, "handlers", ()):
        out.append(handler.body)
    for case in getattr(node, "cases", ()):
        out.append(case.body)
    return out


def _dead_statements(tree: ast.Module) -> set[ast.stmt]:
    """Statements that cannot change what a function does, so a literal in one proves nothing.

    * a bare constant statement (a string dropped into the body: ``"e_text = escape(v)"``);
    * the branch of ``if False:`` / ``while 0:`` that never runs (and the ``else`` of ``if True:``);
    * statements after an unconditional ``return`` / ``raise`` / ``break`` / ``continue`` in the
      same block;
    * a nested ``def`` that has no decorator and whose name nothing live in its enclosing function
      ever reads (defined, never called, passed or returned).

    What this does NOT see: a nested ``def``, ``lambda`` or branch that IS referenced but never
    runs at runtime (``if some_flag():`` that is always false; a callback registered and never
    fired). That is a runtime fact; the payload sweep is the check for it.
    """
    dead: set[ast.stmt] = set()

    def mark(stmts: list[ast.stmt]) -> None:
        dead.update(stmts)

    for node in ast.walk(tree):
        if isinstance(node, (ast.If, ast.While)):
            truth = _constant_truth(node.test)
            if truth is False:
                mark(node.body)
            elif truth is True and isinstance(node, ast.If):
                mark(node.orelse)
        for block in _blocks(node):
            for index, stmt in enumerate(block):
                if _is_inert_statement(stmt):
                    dead.add(stmt)
                if isinstance(stmt, (ast.Return, ast.Raise, ast.Break, ast.Continue)):
                    mark(block[index + 1 :])
                    break

    # Nested defs nothing reads. A fixpoint: a def read only by another dead def is dead too.
    for outer in ast.walk(tree):
        if not isinstance(outer, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        nested = [fn for fn in _direct_defs(outer) if not fn.decorator_list]
        changed = bool(nested)
        while changed:
            changed = False
            for fn in nested:
                if fn in dead:
                    continue
                # Read anywhere else in live code (its own body does not count: recursion is
                # not a use).
                reads = {
                    name.id
                    for name in _live_names(outer, dead | {fn})
                    if isinstance(name.ctx, ast.Load)
                }
                if fn.name not in reads:
                    dead.add(fn)
                    changed = True
    return dead


def _direct_defs(outer: ast.AST) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    """The ``def``s whose nearest enclosing function is ``outer``, not those of a class body (a
    method is reached through an attribute, never by a bare name) or of another nested function."""
    found: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    stack = list(ast.iter_child_nodes(outer))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found.append(node)
        elif not isinstance(node, (ast.ClassDef, ast.Lambda)):
            stack.extend(ast.iter_child_nodes(node))
    return found


def _live_names(node: ast.AST, dead: set[ast.stmt]) -> list[ast.Name]:
    """Every ``Name`` under ``node`` that is not inside a dead statement."""
    names: list[ast.Name] = []

    def visit(current: ast.AST) -> None:
        for child in ast.iter_child_nodes(current):
            if isinstance(child, ast.stmt) and child in dead:
                continue
            if isinstance(child, ast.Name):
                names.append(child)
            visit(child)

    visit(node)
    return names


@lru_cache(maxsize=None)
def _code_lines(path: str, root: Path) -> tuple[str, ...]:
    """The file's lines with everything that is not live CODE blanked (same line structure).

    Evidence must be code that can run: a literal that survives only in a comment, a docstring, a
    string dropped into a body, an ``if False:`` branch, the tail after a ``return`` or a nested
    ``def`` nothing reads (the entry for ``PromptEditorNode.render`` once pointed at a comment)
    proves nothing about what runs. See :func:`_dead_statements` for exactly what is excluded.
    """
    import io
    import tokenize

    lines = _text(path, root).split("\n")
    spans: list[tuple[int, int, int, int]] = []
    for tok in tokenize.generate_tokens(io.StringIO(_text(path, root)).readline):
        if tok.type == tokenize.COMMENT:
            spans.append((*tok.start, *tok.end))
    for stmt in _dead_statements(_tree(path, root)):
        spans.append((stmt.lineno, stmt.col_offset, stmt.end_lineno, stmt.end_col_offset))
    for r1, c1, r2, c2 in spans:
        for r in range(r1, r2 + 1):
            line = lines[r - 1]
            start = c1 if r == r1 else 0
            end = c2 if r == r2 else len(line)
            lines[r - 1] = line[:start] + " " * (end - start) + line[end:]
    return tuple(lines)


def _scope_range(path: str, scope: str, root: Path) -> tuple[int, int] | None:
    """1-based inclusive line range of ``scope`` (``Class.method``, ``name``, or ``<module>``)."""
    if scope == MODULE:
        return 1, len(_code_lines(path, root))

    def walk(body, rest):
        for node in body:
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and node.name == rest[0]
            ):
                if len(rest) == 1:
                    return node.lineno, node.end_lineno
                return walk(node.body, rest[1:])
        return None

    return walk(_tree(path, root).body, scope.split("."))


def _find(path: str, literal: str, scope: str, root: Path) -> int | None:
    """The absolute 1-based line of the first occurrence of ``literal`` in ``scope``'s CODE."""
    try:
        rng = _scope_range(path, scope, root)
    except (OSError, SyntaxError):
        return None
    if rng is None:
        return None
    lines = _code_lines(path, root)
    text = "\n".join(lines[rng[0] - 1 : rng[1]])
    at = text.find(literal)
    return None if at < 0 else rng[0] + text.count("\n", 0, at)


def missing_evidence(audited: dict[tuple[str, str], Audit], root: Path = ROOT) -> list[tuple]:
    """Evidence literals that are not in their scope's code (comments and docstrings excluded)."""
    missing = []
    for key, entry in audited.items():
        for ev in entry.evidence:
            path, literal = ev[0], ev[1]
            if _find(path, literal, _scope_of(key, ev), root) is None:
                missing.append((key, path, literal, _scope_of(key, ev)))
    return missing


def evidence_line(key: tuple[str, str], evidence: tuple, root: Path = ROOT) -> int | None:
    """The absolute line the evidence literal is found on, within its scope."""
    return _find(evidence[0], evidence[1], _scope_of(key, evidence), root)


def function_line(path: str, function: str, root: Path = ROOT) -> int | None:
    """The ``def`` line of ``function`` (``Class.method`` or ``name``)."""
    rng = _scope_range(path, function, root)
    return None if rng is None else rng[0]


@lru_cache(maxsize=None)
def sink_groups(key: tuple[str, str], root: Path = ROOT) -> list[frozenset[int]]:
    """One set of lines per FLAGGED sink of the audited function (the call and its arguments).

    A sink is reached when the sweep executed any of its lines. Coverage is per sink, not per
    function: ``render_modal`` has two.
    """
    from scripts.lib.safe_markup_taint import _Module  # the guard's own scopes: same sinks

    path, function = key
    module = _Module(_text(path, root))
    flagged = {f.lineno for f in module.findings() if f.function == function}
    groups: list[frozenset[int]] = []
    for scope in module.scopes:
        if scope.name != function:
            continue
        for call in scope.sinks:
            if call.lineno in flagged:
                groups.append(frozenset(range(call.lineno, (call.end_lineno or call.lineno) + 1)))
    return groups


def table(root: Path = ROOT) -> str:
    """The Markdown audit table, with every line number resolved against the files as they are."""
    rows = []
    for key, entry in AUDITED.items():
        path, function = key
        fl = function_line(path, function, root)
        ev = [
            f"`{ev[0].removeprefix(PKG)}:{evidence_line(key, ev, root)}`" for ev in entry.evidence
        ]
        reach = (
            f" **Not reached by the payload sweep:** {entry.unreached}" if entry.unreached else ""
        )
        rows.append(
            f"| `{path.removeprefix(PKG)}:{fl}` | `{function}` | {entry.sites} | S | "
            f"{entry.reason.replace('|', '/')}{reach} | {'<br>'.join(ev)} |"
        )
    return "\n".join(rows)


if __name__ == "__main__":  # pragma: no cover - a CLI over the same functions
    sys.path.insert(0, str(ROOT))
    if "--table" in sys.argv:
        print(table())
    else:
        problems = reconcile(scan(), AUDITED)
        for kind, items in problems.items():
            for item in items:
                print(kind, item)
        for item in missing_evidence(AUDITED):
            print("missing_evidence", item)
        raise SystemExit(1 if any(problems.values()) or missing_evidence(AUDITED) else 0)
