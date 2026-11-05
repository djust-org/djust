# Audit of the `mark_safe` sites the taint guard flags outside `djust.theming` (#3774)

`scripts/lib/safe_markup_taint.py` (the guard of #3766) flagged **125** `mark_safe` / `SafeString` /
`SafeText` call sites in `python/djust` (tests and `djust/theming` excluded) as markup built from a
value it cannot show is escaped. This is the record of reading every one of them, not of counting
them. It covers those sites, and only those: see "Out of scope" for what the guard cannot see.

## Result

| verdict | meaning | count |
|---|---|---|
| **S** | safe: every raw-looking value is a constant, escaped, coerced or validated (evidence cited) | **125** sites: 96 sinks in 93 functions kept in the audited baseline, 29 re-sourced (below) |
| **U** | a raw value reaches the markup unescaped | **0** in the flagged sinks; one CLASS found one call below them and fixed: attribute NAMES (below) |
| **N** | needs a decision | **3** items, filed as #3818, #3819 (two items), all since decided and fixed (below); one adjacent finding filed as #3824, also fixed |

None of the 125 sinks is vulnerable. They are flagged because the guard is name-based and
single-function (its docstring says so): it does not follow a value across a call into a helper that
escapes it, treats a name that is reassigned (`width = int(width)`) as still raw, and does not know
the project's own escapers. #3820 files teaching it those, so this list can shrink.

## How each site was read

For every flagged function the raw-looking interpolations were listed (a script over the guard's own
scopes, so it uses the same notion of "raw"), each was traced to where its value comes from, and the
function and the helpers it calls were read. The rule for **S**: the value is (a) a constant, (b)
`conditional_escape` / `escape` / `html.escape` / `format_html` output, (c) coerced with `int()` /
`float()` with a numeric fallback, (d) checked against a constant set or a regex before it is used,
(e) the output of a helper that does one of those internally (`safe_url`, `url_attr`,
`safe_css_color`, `safe_css_length`, `rich_html`, `upload_attrs`, `voice_extras`,
`map_picker_attrs`, `pad_extras`, `interpolate_color`, `field_aria_attrs`; each one read), or (f) the
rendered body of the tag (`nodelist.render(context)`, or the Rust block handler's rendered children,
which `crates/djust_templates/src/renderer.rs` produces with the engine's autoescape).

Two helpers keep a trailing newline: `safe_css_length("10px\n")` and `safe_css_color("red\n")` return
their input unstripped. Neither can carry a quote, `<` or `;`, so inside an attribute or a
`<style>` value the newline is whitespace; noted, not changed.

## The baseline is machine-checked

`scripts/lib/safe_markup_audit.py` holds one entry per `(file, function)`: the number of flagged
sinks, the reason, and **evidence**: `(file, literal)` pairs, each of which must appear in LIVE CODE
of its scope: comments, docstrings, a string dropped into a body, an `if False:` / `while 0:` branch
(or the `else` of `if True:`), statements after a `return` / `raise` / `break` / `continue`, and a
nested `def` with no decorator that nothing in its function reads do not count (#3827). That is
decided on the AST (`_dead_statements`) and stops there: a branch that is referenced but never runs
at runtime (`if flag():` with a flag that is always false), a callback that is registered and never
fired, and a `lambda` are still counted, and a runtime fact is the payload sweep's to catch. The
scope defaults to the audited function
itself when the literal is in the audited function's file, so a twin of the escape elsewhere in the
file does not satisfy it (the first version checked the whole file, and `slider`'s
`e_name = conditional_escape(name)` occurs in several functions); a helper's literal names its own
scope as a third element. Every entry needs at least one literal in its own body.

`python/tests/test_safe_markup_audit_3774.py` fails on a flagged site with no entry, on a function
that gained a sink (the older theming allowlist, keyed by function only, would have waved it
through), on an entry with no site or a different count, on an entry without a reason or evidence,
on evidence that is not live code in its scope, and on an entry with no literal in its own body. The
table below is generated from the same data (`python scripts/lib/safe_markup_audit.py --table`); the
test compares it, without line numbers, to the table in this file.

## The payload sweep, and what it does and does not reach

`python/tests/payload_sweep_3774.py` and `payload_drivers_3774.py` drive the audited sinks with a
hostile value and PARSE the output (an event-handler attribute, an element the payload opened, a
`style` carrying the payload, or a navigation attribute whose value starts with `javascript:` /
`vbscript:` / `data:text/html` fails). Two payload modes: `markup`
(`ZQ"onmouseover=ZQ<b id=ZQ>'ZQ</script></textarea></title><svg onload=ZQ>` in every argument) and `url` (the same, but an argument that looks like a URL gets
`javascript:ZQ(1)`: escaping alone does not stop that, only `safe_url` / `url_attr` do). Each
argument takes the shape the code coerces it from: a keyword the tags `int()` is a number, a
parameter whose default is a number is a number, a list parameter gets dicts carrying the payload
under every key the tags read (with a second row whose flags are false, because a flag holding the
payload is truthy and switches off the very branch under test), a flag is run both ways.

The first version of this sweep said it covered every audited sink and reached 74 of the 96:
`int(PAYLOAD)` raises, or the tag never got a valid data shape. Now **`TestEverySinkIsReached` takes
line coverage over the sweep and fails when an audited sink's lines never executed**, unless the
entry carries an `unreached` reason (and fails if such an entry IS reached). Sinks not reached
today: 0.

Thirteen gate-offs, each removing one escape or validation inside an AUDITED sink, all fail the sweep:
`slider` name, `number_stepper` name and label, `PivotTable` column header, `breadcrumb` `safe_url` ->
`conditional_escape` (the URL mode), `live_render` id, `render_modal` title, `FormMixin.as_live`
help text, `model_table` cell, `ColocatedHook` nonce, `_client_config_html` path, `live_input`
option label, `djust_sw_register` scope and `djust_skeleton`'s size regex (the `style` check). The
baseline alone cannot be relied on for this: it judges a function by its sink COUNT, so swapping an
escaped value for a raw one inside an audited function leaves the guard exactly as flagged as
before, and it notices only if the removed line happens to be one of the pinned evidence literals.
The sweep does not depend on someone having pinned that line.

The payload carries `</textarea></title>` ahead of its `<svg onload=ZQ>` (#3827). The contents of
`<textarea>` and `<title>` are RCDATA: the parser never turns text inside them into elements, so a
payload that is interpolated raw INTO one stayed inert text and the sweep reported it clean, while a
browser ends the element at the first `</textarea>` / `</title>` it finds in that text. Four gate-offs
passed the sweep without the closers and fail it with them: `build_tag` printing a `<textarea>`
body raw, `PromptEditor`'s `{tmpl}</textarea>`, `dj_textarea`'s `{value}</textarea>` and the Gantt
bar's SVG `<title>{name}`. The unmutated tree stays at zero findings.

What the sweep does not do: it drives the Django-engine functions, not the Rust handlers (which call
the same `render_*` functions for the block tags); `_dev_tools.py` is rendered but not audited by the
guard; `ColocatedHookNode` is not given a hostile NAME (see its row). Since #3818 / #3819 the
`live_input` driver also passes an `on*` attribute and URL-valued attributes holding the payload
(they must be refused or neutralised), and `live_render` is given a plain-`str` placeholder holding the
payload (it must come out escaped).

## Out of scope: sinks the guard cannot see

The guard matches `mark_safe`, `SafeString` and `SafeText` (and the format string of `format_html`)
by name. The wrappers `_safe(...)` (187 call sites in `components/rust_handlers.py`) and
`djust._html.safe_html(...)` are invisible to it, and so to this baseline and this sweep: adding both
names to `SINKS` flags 103 more sinks (98 in `rust_handlers.py`, 5 in `_dev_tools.py`), and a new
`def _zz(v): return safe_html(f"<div>{v}</div>")` passes all three. That is #3820.

## U (fixed): an attribute NAME was written unescaped

`djust._html.build_tag` is documented as the one place every value is escaped. A **value** was; a
**name** was interpolated raw. `{% live_input ... event=var %}` takes `event` from a template
argument and uses it as the attribute name, so a name can come from data:

```
live_input("text", handler="h", event="dj-input onfocus=alert(1) autofocus y")
before: <input type="text" ... name="h" dj-input onfocus=alert(1) autofocus y="h" />
after:  ValueError: 'dj-input onfocus=alert(1) autofocus y' is not a valid HTML attribute name
```

Escaping cannot repair a name (the payload has nothing to escape), so `djust._html.attr_name` checks
it against the HTML spec's rule (no whitespace, controls, quotes, `<`, `>`, `/` or `=`). Every real
name (`dj-input`, `aria-label`, `@click`, `:class`, `dj-keydown.enter`) passes byte-identically.
`FormMixin.as_live`'s `BaseAdapter._build_tag` uses the same check.

The review of the first version found the same class in the writers that do not go through
`build_tag`; none is reachable from template data today (a name is a keyword or a dict key the
developer wrote), which makes them the parallel-path twin rather than a new hole, and each now calls
`attr_name`: `render_icon`'s extra attributes (it `html.escape`d the NAME, which cannot make one
safe), `Button` and `Card` `data-*` keys, `PaginationComponent._click`'s `data-*` keys, and
`LiveComponent.event_attrs`'s trigger and `dj-value-*` keys. `components/ui/dropdown.py` already
guarded its `data-*` keys with `_DATA_KEY_RE`. Tests: `python/tests/test_attr_name_injection_3774.py`,
each fix gate-offed on its own.

## N: decided and fixed

* **#3818** — `live_input` passthrough attributes had no URL-scheme / `on*` / `type`-override policy
  (`formaction="javascript:..."` rendered as given; theming's `_passthrough_attrs` rejected it). Decided:
  one policy in `djust._attr_policy.passthrough_attrs`, used by every passthrough writer (`live_input`,
  `FormMixin.as_live` widget attrs, `render_icon` extras, and with it the native `{% icon %}` handler):
  an `on*` NAME (and `@`, `x-on`, `v-on`, `hx-on`) raises `ValueError`; a URL VALUE the shared allowlist
  rejects renders `#` in every URL attribute (`href` / `src` / `action` / `formaction`, `poster`, `srcset`,
  `ping`, `data`, `background`, `xlink:href`, `dj-navigate` ...) and never raises, because a value can come
  from data and an error page it triggers for every viewer is its own hole; a `type` VALUE of `image` /
  `submit` / `reset` / `button` is left out; everything else unchanged.
  `python/tests/test_passthrough_attr_policy_3818.py` pins it on both engines and against theming's
  own implementation.
* **#3819** — two raw-markup slots with no escaping contract. Decided: `live_render`'s
  `lazy={"placeholder": ...}` follows Django's rule (`conditional_escape`): a marked-safe value is markup,
  a plain `str` is text. `routing.get_route_map_script` (and the two other developer-config JSON blobs
  inlined in a `<script>`: the handler-metadata script and `DJUST_LOADING_GROUPING_CLASSES`) now pass
  their JSON through `djust.security.json_script.escape_json_for_script`.
  `python/tests/test_raw_slots_3819.py`.
* **#3824** (adjacent, found by the writer grep) — `template_tags/pwa.py` wrote resolved tag
  arguments into Django template SOURCE, so a context value reaching `{% djust_pwa_head name=var %}`
  could end the tag and print markup. Fixed in #3828.

## Re-sourced: flagged only because the escaper was imported through a re-export

`_advanced.py`, `_charts.py` and `_forms.py` imported `conditional_escape` from `._registry`, which
re-exports `django.utils.html.conditional_escape`. The guard trusts an escaper by its **origin**, never
its spelling (`from re import escape` must not count), so every site those modules escaped was flagged.
Importing it from `django.utils.html` makes the origin visible, and 29 of the 125 sinks
stop being flagged without any behaviour change. Each is clean under the guard's full rules, and the
payload sweep renders them.

* `components/templatetags/_advanced.py`: `TourNode.render`, `GanttChartNode.render (2 sites)`, `DiffViewerNode.render (2 sites)`, `PivotTableNode.render`, `OrgChartNode.render`, `ComparisonTableNode.render`, `MasonryGridNode.render`, `CursorsOverlayNode.render`, `LiveIndicatorNode.render`, `CollabSelectionNode.render`, `ReactionsNode.render`, `CronInputNode.render`
* `components/templatetags/_charts.py`: `BarChartNode.render`, `LineChartNode.render`, `PieChartNode.render (2 sites)`, `SparklineNode.render`, `HeatmapNode.render (2 sites)`, `TreemapNode.render`
* `components/templatetags/_forms.py`: `search_input`, `password_input`, `autocomplete`, `dependent_select`, `currency_input`, `form_errors`, `field_error`

## The audited baseline

Verdict **S** for every row. Paths are relative to `python/djust/`. Line numbers are those of the
branch this file was generated on; regenerate with `python scripts/lib/safe_markup_audit.py --table`.
Every cited line is resolved by the script against the files, not typed; an evidence line is the
first match inside its scope.

| file:line (the def) | function | sinks | verdict | why it is safe | evidence (file:line) |
|---|---|---|---|---|---|
<!-- audit-table:begin -->
| `assets/tags.py:214` | `asset_tags` | 1 | S | joins _tag() results; _tag builds every branch with format_html, so src and integrity are escaped | `assets/tags.py:219`<br>`assets/tags.py:190`<br>`assets/tags.py:201` |
| `components/base.py:317` | `Component.asset_tags` | 1 | S | joins djust.assets.tags.asset_tags() results, which are format_html SafeStrings | `components/base.py:319`<br>`assets/tags.py:214` |
| `components/icons.py:174` | `render_icon` | 1 | S | class names are the constant size label plus html.escape(custom_class); every extra attribute VALUE is html.escape'd and every extra attribute NAME is checked by attr_name (before #3774 the name was only html.escape'd, which cannot make a name safe: `x onfocus=alert(1) y` rendered a live handler; fixed, python/tests/test_attr_name_injection_3774.py); since #3818 the extra attributes first pass djust._attr_policy.passthrough_attrs (an on* name is refused with ValueError, a URL the allowlist rejects renders `#`); px is int(); svg_inner is the bundled / DJUST_COMPONENTS_ICON_SETS (developer settings) markup keyed by name, never the caller's text | `components/icons.py:215`<br>`components/icons.py:225`<br>`components/icons.py:224`<br>`components/icons.py:221`<br>`components/icons.py:207`<br>`components/icons.py:199` |
| `forms.py:856` | `FormMixin.as_live` | 1 | S | joins adapter.render_field() output; BaseAdapter escapes wrapper/label/help text/errors/choices/values and (since #3774) checks attribute names in _build_tag; since #3818 the widget attrs it merges pass djust._attr_policy.passthrough_attrs (an inline event handler name is refused with ValueError, a rejected URL renders `#`, a submitting `type` is left out); a custom adapter owes the same (FrameworkAdapter contract) | `forms.py:897`<br>`frameworks.py:115`<br>`frameworks.py:261`<br>`frameworks.py:200`<br>`frameworks.py:221` |
| `template_tags/lorem.py:42` | `LoremTagHandler.render` | 1 | S | wraps the paragraphs of django.utils.lorem_ipsum (a fixed corpus) in <p>; same as Django's own lorem tag | `template_tags/lorem.py:64`<br>`template_tags/lorem.py:26` |
| `templatetags/djust_pwa.py:190` | `djust_sw_register` | 1 | S | nonce_attr is escape()'d in _nonce_attr; the URL and scope are _script_json(): json.dumps with < > & and U+2028/9 escaped, so neither can end the script element | `templatetags/djust_pwa.py:216`<br>`templatetags/djust_pwa.py:90`<br>`templatetags/djust_pwa.py:100` |
| `templatetags/djust_pwa.py:256` | `djust_offline_indicator` | 1 | S | texts and classes are escape()'d locals; show_when selects a constant visibility attribute; the style block is a constant plus the escaped nonce | `templatetags/djust_pwa.py:307`<br>`templatetags/djust_pwa.py:310` |
| `templatetags/djust_pwa.py:394` | `djust_offline_styles` | 1 | S | constant CSS plus the nonce attribute built by _nonce_attr (escape()'d) | `templatetags/djust_pwa.py:413`<br>`templatetags/djust_pwa.py:90` |
| `templatetags/live_tags.py:254` | `_client_config_html` | 1 | S | the three meta prefixes are escape()'d; the route-map script is routing.get_route_map_script (format_html around json.dumps of developer URL config, which since #3819 is passed through escape_json_for_script so `<`, `>` and `&` cannot end the script whatever the config holds) | `templatetags/live_tags.py:279`<br>`routing.py:472`<br>`routing.py:535` |
| `templatetags/live_tags.py:810` | `live_input` | 1 | S | html is build_tag output: every attribute VALUE is escape()'d, select options escape()'d, textarea content escaped; attribute NAMES (event=, passthrough keys) were not checked until #3774 (the audit's one U, fixed in djust._html.attr_name, python/tests/test_attr_name_injection_3774.py); the passthrough attributes are judged by djust._attr_policy.passthrough_attrs (#3818: an on* name is refused with ValueError, a URL the allowlist rejects renders `#`, a submitting `type` is left out; python/tests/test_passthrough_attr_policy_3818.py) | `templatetags/live_tags.py:910`<br>`templatetags/live_tags.py:633`<br>`_attr_policy.py:171`<br>`_html.py:26`<br>`_html.py:84`<br>`templatetags/live_tags.py:717` |
| `templatetags/live_tags.py:1035` | `ColocatedHookNode.render` | 1 | S | the name is the tag's parse-time literal (plus the view's module.qualname): template/code, not request data; it is escape()'d into data-hook; the body is </script>-escaped; the nonce is escape()'d. NOT escaped: the JS-comment banner `/* COLOCATED HOOK: <name> */` interpolates the name raw, so a name carrying `</script>` or `*/` would end the script or the comment; it is a parse-time literal the template author wrote, hence not exploitable, and the sweep drives this tag with a plain name for that reason | `templatetags/live_tags.py:1050`<br>`templatetags/live_tags.py:1045`<br>`templatetags/live_tags.py:1062` |
| `templatetags/live_tags.py:1158` | `djust_skeleton` | 1 | S | shape is checked against _SKELETON_VALID_SHAPES; width/height pass _SKELETON_SIZE_RE; class_ goes through build_tag (escaped); count is int()-clamped | `templatetags/live_tags.py:1204`<br>`templatetags/live_tags.py:1145`<br>`templatetags/live_tags.py:1213` |
| `templatetags/live_tags.py:1803` | `_render_sticky_child_html` | 2 | S | the wrapper's ids are escape()'d; the body is the child's own template render (autoescaped by the engine) stamped by _stamp_view_id, which escape()s view_id | `templatetags/live_tags.py:1864`<br>`templatetags/live_tags.py:1870`<br>`templatetags/live_tags.py:1863` |
| `templatetags/live_tags.py:1986` | `live_render` | 2 | S | ids and the CSP nonce are escape()'d, lazy['trigger'] is checked against ('flush', 'visible'), the child body is a template render stamped by _stamp_view_id. The one formerly raw piece, lazy['placeholder'], goes through conditional_escape since #3819: a marked-safe value is markup, a plain str is text (Django's own rule) | `templatetags/live_tags.py:2265`<br>`templatetags/live_tags.py:2313`<br>`templatetags/live_tags.py:2494`<br>`templatetags/live_tags.py:2496` |
| `components/templatetags/_charts.py:33` | `BarChartNode.render` | 1 | S | width/height int() with a numeric fallback; values float(); title, labels, colour and class conditional_escape'd; every other interpolation is a float | `components/templatetags/_charts.py:13`<br>`components/templatetags/_charts.py:112` |
| `components/templatetags/_charts.py:155` | `LineChartNode.render` | 1 | S | width/height int(); values float(); series name and colour conditional_escape'd; coordinates are floats | `components/templatetags/_charts.py:13`<br>`components/templatetags/_charts.py:230` |
| `components/templatetags/_charts.py:329` | `PieChartNode.render` | 1 | S | width/height int(), inner_radius float(); segment colour and label conditional_escape'd; the path data is floats | `components/templatetags/_charts.py:13`<br>`components/templatetags/_charts.py:412` |
| `components/templatetags/_charts.py:489` | `SparklineNode.render` | 1 | S | width/height int(), stroke_width float(); colour conditional_escape'd; path data is floats | `components/templatetags/_charts.py:517`<br>`components/templatetags/_charts.py:540` |
| `components/templatetags/_charts.py:612` | `HeatmapNode.render` | 1 | S | cell_size int(); cell colours come from interpolate_color (a #rrggbb string built from ints); axis labels and title conditional_escape'd | `components/utils.py:145`<br>`components/templatetags/_charts.py:638`<br>`components/templatetags/_charts.py:691` |
| `components/templatetags/_charts.py:788` | `TreemapNode.render` | 1 | S | width/height int(); the fill is a constant palette entry; label and title conditional_escape'd | `components/templatetags/_charts.py:854`<br>`components/templatetags/_charts.py:853` |
| `components/templatetags/_charts.py:909` | `CalendarHeatmapNode.render` | 1 | S | year, cell_size, cell_gap int(); fills are the constant LEVELS; date_str is date.isoformat(); title conditional_escape'd | `components/templatetags/_charts.py:930`<br>`components/templatetags/_charts.py:889`<br>`components/templatetags/_charts.py:1019` |
| `components/templatetags/_forms.py:31` | `slider` | 1 | S | min/max/step/value/value_end are int()'d; name, label, event and class conditional_escape'd | `components/templatetags/_forms.py:81`<br>`components/templatetags/_forms.py:83` |
| `components/templatetags/_forms.py:463` | `render_confirm_dialog` | 1 | S | every kwarg is conditional_escape'd; `body` is the already-rendered tag body (Django nodelist.render, Rust block handler's rendered children), autoescaped by the engine | `components/templatetags/_forms.py:486`<br>`components/templatetags/_forms.py:536`<br>`components/rust_handlers.py:4195` |
| `components/templatetags/_forms.py:566` | `render_popconfirm` | 1 | S | every kwarg is conditional_escape'd; `content` is the rendered tag body (nodelist.render / the Rust block handler's children) | `components/templatetags/_forms.py:583`<br>`components/templatetags/_forms.py:615`<br>`components/rust_handlers.py:4204` |
| `components/templatetags/_forms.py:1200` | `dj_form` | 1 | S | _render_field escapes every value (label, help, errors, choices, widget attrs); the action goes through safe_url; hidden-field names and values conditional_escape'd | `components/templatetags/_forms.py:1234`<br>`components/templatetags/_forms.py:1003`<br>`components/utils.py:368` |
| `components/templatetags/_forms.py:1443` | `model_table` | 1 | S | every row value is conditional_escape(str(v))'d before the table is built; page and total_pages int(); event names and labels conditional_escape'd | `components/templatetags/_forms.py:1537`<br>`components/templatetags/_forms.py:1562` |
| `components/templatetags/_advanced.py:141` | `CalendarViewNode.render` | 1 | S | year, month, start_day int(); month and day names from calendar; event titles conditional_escape'd; event colours through safe_css_color | `components/templatetags/_advanced.py:163`<br>`components/templatetags/_advanced.py:222`<br>`components/templatetags/_advanced.py:187` |
| `components/templatetags/_advanced.py:279` | `GanttChartNode.render` | 1 | S | width/row_height int(); start/duration/progress float(); task name, colour and unit label conditional_escape'd | `components/templatetags/_advanced.py:387`<br>`components/templatetags/_advanced.py:302` |
| `components/templatetags/_advanced.py:618` | `PivotTableNode.render` | 1 | S | row and column headers, title and class conditional_escape'd; cells print _format_val (int/float text) | `components/templatetags/_advanced.py:613`<br>`components/templatetags/_advanced.py:685` |
| `components/templatetags/_advanced.py:775` | `OrgChartNode.render` | 1 | S | the tree comes from _render_node, which conditional_escape's id, name and title and sends the avatar through url_attr | `components/templatetags/_advanced.py:820`<br>`components/templatetags/_advanced.py:734`<br>`components/templatetags/_advanced.py:748` |
| `components/templatetags/_advanced.py:851` | `ComparisonTableNode.render` | 1 | S | _render_value returns constants or conditional_escape(str(val)); plan and feature names escaped | `components/templatetags/_advanced.py:881`<br>`components/templatetags/_advanced.py:844`<br>`components/templatetags/_advanced.py:849` |
| `components/templatetags/_advanced.py:942` | `MasonryGridNode.render` | 1 | S | columns and gap int(); item content and class conditional_escape'd | `components/templatetags/_advanced.py:958`<br>`components/templatetags/_advanced.py:987` |
| `components/templatetags/_advanced.py:1112` | `LiveIndicatorNode.render` | 1 | S | name, field, action conditional_escape'd; the avatar goes through url_attr(image=True) | `components/templatetags/_advanced.py:1136`<br>`components/utils.py:457` |
| `components/templatetags/_advanced.py:1277` | `ActivityFeedNode.render` | 1 | S | max int(); every event field conditional_escape'd; the avatar goes through url_attr(image=True) | `components/templatetags/_advanced.py:1281`<br>`components/templatetags/_advanced.py:1312` |
| `components/templatetags/_advanced.py:1447` | `MapPickerNode.render` | 1 | S | lat/lng float(), zoom int(); height through safe_css_length; the Leaflet attributes come from map_picker_attrs, which html.escape's every value and int()s the zoom | `components/templatetags/_advanced.py:1471`<br>`components/components/map_picker.py:99`<br>`components/components/map_picker.py:135` |
| `components/templatetags/_advanced.py:1514` | `PromptEditorNode.render` | 1 | S | the template text is conditional_escape'd FIRST; {{var}} tokens are \w+ and are replaced by conditional_escape'd values inside <mark>; rows int() | `components/templatetags/_advanced.py:1562`<br>`components/templatetags/_advanced.py:1547` |
| `components/templatetags/_advanced.py:1604` | `VoiceInputNode.render` | 1 | S | event and lang conditional_escape'd; the wrapper, disclosure and status come from voice_extras, which html.escape's the disclosure and int()s max_seconds | `components/templatetags/_advanced.py:1634`<br>`components/components/voice_input.py:58`<br>`components/components/voice_input.py:78` |
| `components/templatetags/_advanced.py:1732` | `ErrorPageNode.render` | 1 | S | code int(); the action URL goes through safe_url; title, message and label conditional_escape'd | `components/templatetags/_advanced.py:1749`<br>`components/utils.py:368` |
| `components/templatetags/_advanced.py:1790` | `ImageUploadPreviewNode.render` | 1 | S | the upload attributes come from upload_attrs (html.escape'd slot, int() size); thumbnails go through url_attr(image=True); name, event, accept and class conditional_escape'd; max int() | `components/components/image_upload_preview.py:18`<br>`components/components/image_upload_preview.py:39`<br>`components/templatetags/_advanced.py:1818` |
| `components/templatetags/_advanced.py:1877` | `AnimatedNumberNode.render` | 1 | S | value float(); duration and decimals int(); prefix, suffix, separator and the formatted number conditional_escape'd | `components/templatetags/_advanced.py:1916`<br>`components/templatetags/_advanced.py:1896` |
| `components/templatetags/_advanced.py:1959` | `RibbonNode.render` | 1 | S | position is checked against the four corner names; the variant class is a constant from VARIANT_MAP; text and class conditional_escape'd | `components/templatetags/_advanced.py:1970`<br>`components/templatetags/_advanced.py:1978` |
| `components/templatetags/_advanced.py:2008` | `BreadcrumbDropdownNode.render` | 1 | S | labels conditional_escape'd; hrefs go through safe_url; the separator is conditional_escape'd | `components/templatetags/_advanced.py:2039`<br>`components/templatetags/_advanced.py:2024` |
| `components/templatetags/_advanced.py:2106` | `DataCardGridNode.render` | 1 | S | title, description and category are conditional_escape'd (the raw first `cat` only feeds escape()); the image goes through url_attr; columns int() | `components/templatetags/_advanced.py:2158`<br>`components/templatetags/_advanced.py:2163` |
| `components/templatetags/_advanced.py:2214` | `AgentStepNode.render` | 1 | S | status is checked against STATUS_ICONS' keys before it reaches the class; the icon is a constant entity; tool, duration and content conditional_escape'd | `components/templatetags/_advanced.py:2223`<br>`components/templatetags/_advanced.py:2203` |
| `components/templatetags/_advanced.py:2319` | `QRCodeNode.render` | 1 | S | px comes from SIZE_MAP's ints or int(); fg, bg and data conditional_escape'd; the modules are floats | `components/templatetags/_advanced.py:2337`<br>`components/templatetags/_advanced.py:2328` |
| `components/templatetags/djust_components.py:136` | `render_modal_description` | 1 | S | `content` is the rendered {% modal_description %} body (nodelist.render / the Rust block handler) | `components/templatetags/djust_components.py:140`<br>`components/templatetags/djust_components.py:269`<br>`components/rust_handlers.py:260` |
| `components/templatetags/djust_components.py:143` | `render_modal` | 2 | S | title, close event and ids conditional_escape'd; size from a four-key dict, role from a two-way test, heading level int() in 2..6; `content` is the rendered tag body | `components/templatetags/djust_components.py:179`<br>`components/templatetags/djust_components.py:187`<br>`components/templatetags/djust_components.py:200`<br>`components/templatetags/djust_components.py:261` |
| `components/templatetags/djust_components.py:595` | `TooltipNode.render` | 1 | S | text, position and component_id conditional_escape'd; tip_id is the escaped component id or a counter; content is the rendered body | `components/templatetags/djust_components.py:610`<br>`components/templatetags/djust_components.py:587` |
| `components/templatetags/djust_components.py:1545` | `dj_textarea` | 1 | S | rows int(); name, label, value, placeholder, error, helper and event conditional_escape'd | `components/templatetags/djust_components.py:1569`<br>`components/templatetags/djust_components.py:1563` |
| `components/templatetags/djust_components.py:1717` | `breadcrumb` | 1 | S | labels conditional_escape'd; urls go through safe_url | `components/templatetags/djust_components.py:1736`<br>`components/utils.py:368` |
| `components/templatetags/djust_components.py:2038` | `code_block` | 1 | S | theme must match [A-Za-z0-9_-]+ and is escaped; language, filename and code conditional_escape'd; the stylesheet tag is asset_tags (format_html); the hljs JSON is conditional_escape'd into data-hljs | `components/templatetags/djust_components.py:2061`<br>`components/templatetags/djust_components.py:2099` |
| `components/templatetags/djust_components.py:2172` | `combobox` | 1 | S | ids, names, labels, values and options conditional_escape'd; aria attributes come from field_aria_attrs, which escapes its ids | `components/templatetags/djust_components.py:2344`<br>`components/templatetags/djust_components.py:2150`<br>`components/templatetags/djust_components.py:2297` |
| `components/templatetags/djust_components.py:2405` | `render_popover` | 1 | S | trigger, placement, title and ids conditional_escape'd; `content` is the rendered tag body | `components/templatetags/djust_components.py:2418`<br>`components/templatetags/djust_components.py:2446` |
| `components/templatetags/djust_components.py:2557` | `render_collapsible` | 1 | S | trigger, event and ids conditional_escape'd; `content` is the rendered tag body | `components/templatetags/djust_components.py:2593`<br>`components/templatetags/djust_components.py:2578` |
| `components/templatetags/djust_components.py:2660` | `notification_center` | 1 | S | unread_count int(); id, message, time and event names conditional_escape'd | `components/templatetags/djust_components.py:2675`<br>`components/templatetags/djust_components.py:2690` |
| `components/templatetags/djust_components.py:2796` | `carousel` | 1 | S | active int(); src through url_attr(image=True); alt, caption and event names conditional_escape'd | `components/templatetags/djust_components.py:2824`<br>`components/templatetags/djust_components.py:2808` |
| `components/templatetags/djust_components.py:2870` | `tree_view` | 1 | S | node id and label conditional_escape'd; depth is a number; event names conditional_escape'd | `components/templatetags/djust_components.py:2895`<br>`components/templatetags/djust_components.py:2904` |
| `components/templatetags/djust_components.py:3078` | `render_context_menu` | 1 | S | label and id conditional_escape'd; `content` is the rendered tag body | `components/templatetags/djust_components.py:3102`<br>`components/templatetags/djust_components.py:3089` |
| `components/templatetags/djust_components.py:3136` | `date_picker` | 1 | S | year and month int() (month_name is calendar's); the selected, range, label, error and event values conditional_escape'd; date_str is built from ints | `components/templatetags/djust_components.py:3175`<br>`components/templatetags/djust_components.py:3188` |
| `components/templatetags/djust_components.py:3376` | `table_of_contents` | 1 | S | level int(); id and label conditional_escape'd | `components/templatetags/djust_components.py:3398`<br>`components/templatetags/djust_components.py:3396` |
| `components/templatetags/djust_components.py:3419` | `virtual_list` | 1 | S | total, page, page_size and item_height int() (one except resets all four); labels and the event conditional_escape'd | `components/templatetags/djust_components.py:3438`<br>`components/templatetags/djust_components.py:3451` |
| `components/templatetags/djust_components.py:3593` | `rich_text_editor` | 1 | S | the value goes through rich_html (the Markdown sanitizer unless it is SafeData); height through safe_css_length; name, event, label and placeholder conditional_escape'd | `components/utils.py:582`<br>`components/templatetags/djust_components.py:3613`<br>`components/utils.py:277` |
| `components/templatetags/djust_components.py:3778` | `otp_input` | 1 | S | digits int() clamped to 1..12; the id is a uuid hex; name, label and event conditional_escape'd | `components/templatetags/djust_components.py:3796`<br>`components/templatetags/djust_components.py:3803` |
| `components/templatetags/djust_components.py:3831` | `number_stepper` | 1 | S | value, step, min and max int(); name, label and event conditional_escape'd | `components/templatetags/djust_components.py:3869`<br>`components/templatetags/djust_components.py:3860` |
| `components/templatetags/djust_components.py:4196` | `fab` | 1 | S | position is checked against four names; icon, event, label, size and variant conditional_escape'd | `components/templatetags/djust_components.py:4228`<br>`components/templatetags/djust_components.py:4223` |
| `components/templatetags/djust_components.py:4429` | `CalloutNode.render` | 1 | S | the icon is ICONS[callout_type] (a constant) only when callout_type is a key; the type, title, icon text and class are conditional_escape'd; content is the rendered body | `components/templatetags/djust_components.py:4452`<br>`components/templatetags/djust_components.py:4436` |
| `components/templatetags/djust_components.py:4975` | `rich_select` | 1 | S | variant names match ^[a-z0-9][a-z0-9-]{0,31}$ (_VARIANT_NAME_RE); ids, names, values, labels, placeholder escaped; aria from field_aria_attrs; option bodies from _rich_select_option_html, which escapes | `components/components/rich_select.py:35`<br>`components/templatetags/djust_components.py:5141`<br>`components/templatetags/djust_components.py:5049` |
| `components/templatetags/djust_components.py:5512` | `ToastContainerNode.render` | 1 | S | position is checked against ALLOWED_POSITIONS; max_toasts int(); class conditional_escape'd | `components/templatetags/djust_components.py:5515`<br>`components/templatetags/djust_components.py:5523` |
| `components/templatetags/djust_components.py:5631` | `responsive_image` | 1 | S | src and placeholder through url_attr(image=True); srcset through srcset_attr; aspect-ratio through safe_css_ratio; alt, sizes and class conditional_escape'd | `components/templatetags/djust_components.py:5675`<br>`components/templatetags/djust_components.py:5667`<br>`components/utils.py:557` |
| `components/templatetags/djust_components.py:5832` | `render_theme_toggle` | 1 | S | mode, icon and label come from the constant _THEME_TOGGLE_MODES; current, event, class and id conditional_escape'd; the icons are render_icon output | `components/templatetags/djust_components.py:5825`<br>`components/templatetags/djust_components.py:5849` |
| `components/templatetags/djust_components.py:5922` | `PageHeaderNode.render` | 1 | S | title, subtitle, description and class conditional_escape'd; the breadcrumb and actions slots are rendered nodelists | `components/templatetags/djust_components.py:5929`<br>`components/templatetags/djust_components.py:5937` |
| `components/templatetags/djust_components.py:6081` | `SidebarNode.render` | 1 | S | title, class, ids and events conditional_escape'd; items come from _render_item (labels escaped, hrefs through safe_url) | `components/templatetags/djust_components.py:6118`<br>`components/templatetags/djust_components.py:6035`<br>`components/templatetags/djust_components.py:6064` |
| `components/templatetags/djust_components.py:6243` | `NavMenuNode.render` | 1 | S | brand, class, ids and events conditional_escape'd; brand_href through safe_url; items from _render_nav_item (escaped, safe_url) | `components/templatetags/djust_components.py:6272`<br>`components/templatetags/djust_components.py:6175`<br>`components/templatetags/djust_components.py:6262` |
| `components/templatetags/djust_components.py:6577` | `FilterBarNode.render` | 1 | S | every control's name, label, value, option and event is conditional_escape'd; debounce int() | `components/templatetags/djust_components.py:6602`<br>`components/templatetags/djust_components.py:6656` |
| `components/templatetags/djust_components.py:6712` | `AvatarGroupNode.render` | 1 | S | size, class, name and initials conditional_escape'd; src through url_attr(image=True); max int() | `components/templatetags/djust_components.py:6719`<br>`components/templatetags/djust_components.py:6715` |
| `components/templatetags/djust_components.py:7025` | `ThinkingIndicatorNode.render` | 1 | S | status is checked against VALID_STATUSES before it reaches the class; label and class escaped | `components/templatetags/djust_components.py:7020`<br>`components/templatetags/djust_components.py:7031` |
| `components/templatetags/djust_components.py:7168` | `FeedbackWidgetNode.render` | 1 | S | mode is checked against VALID_MODES; the buttons are constants plus the conditional_escape'd event, an int star index and escaped emoji keys | `components/templatetags/djust_components.py:7163`<br>`components/templatetags/djust_components.py:7175` |
| `components/templatetags/djust_components.py:7268` | `ApprovalGateNode.render` | 1 | S | risk is checked against VALID_RISKS before it reaches the class; message, labels, events and class conditional_escape'd; the icons are constants | `components/templatetags/djust_components.py:7263`<br>`components/templatetags/djust_components.py:7278` |
| `components/templatetags/djust_components.py:7342` | `SourceCitationNode.render` | 1 | S | idx int(); title conditional_escape'd; the url is safe_url (escaped, scheme-checked) and is also the link text; relevance float() | `components/templatetags/djust_components.py:7356`<br>`components/templatetags/djust_components.py:7351` |
| `components/templatetags/djust_components.py:7414` | `ModelSelectorNode.render` | 1 | S | name, event, placeholder, class, value and every option conditional_escape'd (_option_inner escapes label, description, context window and tier) | `components/templatetags/djust_components.py:7485`<br>`components/templatetags/djust_components.py:7459` |
| `components/templatetags/djust_components.py:7604` | `ChatBubbleNode.render` | 1 | S | name, text, time, class conditional_escape'd; the avatar through url_attr(image=True); status must be in VALID_STATUSES; side is one of two constants | `components/templatetags/djust_components.py:7622`<br>`components/templatetags/djust_components.py:7647` |
| `components/templatetags/djust_components.py:7704` | `PresenceAvatarsNode.render` | 1 | S | max int(); name and initials conditional_escape'd; src through url_attr(image=True); status must be in VALID_STATUSES | `components/templatetags/djust_components.py:7731`<br>`components/templatetags/djust_components.py:7707` |
| `components/templatetags/djust_components.py:7784` | `MentionsInputNode.render` | 1 | S | id, name, initials, placeholder, event and class conditional_escape'd; the avatar through url_attr(image=True); the users JSON is conditional_escape'd into data-users | `components/templatetags/djust_components.py:7839`<br>`components/templatetags/djust_components.py:7814` |
| `components/templatetags/djust_components.py:8644` | `CookieConsentNode.render` | 1 | S | the privacy URL goes through safe_url; message, labels and events conditional_escape'd | `components/templatetags/djust_components.py:8672`<br>`components/utils.py:368` |
| `components/templatetags/djust_components.py:8715` | `FormArrayNode.render` | 1 | S | min and max int(); name, events, labels and each row value conditional_escape'd; a custom row body is a rendered nodelist | `components/templatetags/djust_components.py:8733`<br>`components/templatetags/djust_components.py:8799` |
| `components/templatetags/djust_components.py:9050` | `render_dropdown_menu` | 1 | S | label, event, class and align conditional_escape'd; items come from render_menu_item (labels escaped, href through url_attr); a block body is the rendered tag body | `components/templatetags/djust_components.py:9064`<br>`components/templatetags/djust_components.py:8969`<br>`components/templatetags/djust_components.py:9030` |
| `components/templatetags/djust_components.py:9151` | `MeterNode.render` | 1 | S | total int(), value float(); the colour goes through safe_css_color; labels and legend values conditional_escape'd | `components/templatetags/djust_components.py:9191`<br>`components/templatetags/djust_components.py:9167`<br>`components/utils.py:226` |
| `components/templatetags/djust_components.py:9635` | `SortableGridNode.render` | 1 | S | columns int(); gap through safe_css_length then conditional_escape; id and label conditional_escape'd; thumbnail through url_attr(image=True) | `components/templatetags/djust_components.py:9640`<br>`components/templatetags/djust_components.py:9674` |
| `components/templatetags/djust_components.py:9712` | `ImageCropperNode.render` | 1 | S | src through url_attr(image=True); event, class, ratio and alt conditional_escape'd; min sizes int(); cropper_extras is constant markup | `components/templatetags/djust_components.py:9722`<br>`components/components/image_cropper.py:24` |
| `components/templatetags/djust_components.py:9787` | `SignaturePadNode.render` | 1 | S | name, event, pen colour and class conditional_escape'd; width, height and pen width int(); pad_extras html.escape's the label and int()s max_bytes | `components/components/signature_pad.py:35`<br>`components/templatetags/djust_components.py:9819` |
| `components/templatetags/djust_components.py:9869` | `ResizablePanelNode.render` | 1 | S | direction is reset unless it is 'horizontal' or 'vertical'; min, max and initial size go through safe_css_length then conditional_escape; class and event conditional_escape'd | `components/templatetags/djust_components.py:9881`<br>`components/templatetags/djust_components.py:9893`<br>`components/utils.py:277` |
| `components/templatetags/djust_components.py:9940` | `LightboxNode.render` | 1 | S | the image through url_attr(image=True); alt, caption, events and class conditional_escape'd; idx int() | `components/templatetags/djust_components.py:9978`<br>`components/templatetags/djust_components.py:9967` |
| `components/templatetags/djust_components.py:10044` | `DashboardGridNode.render` | 1 | S | columns and every panel cell are clamp_columns / clamp_cells ints; gap and row height through safe_css_length then conditional_escape; id, title and content conditional_escape'd | `components/templatetags/djust_components.py:10090`<br>`components/components/dashboard_grid.py:22`<br>`components/templatetags/djust_components.py:10057` |
<!-- audit-table:end -->

Refs #3774, #3766, #3775, #3818, #3819, #3820, #3824.
