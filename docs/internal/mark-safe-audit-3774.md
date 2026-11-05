# Audit of the `mark_safe` sites the taint guard flags outside `djust.theming` (#3774)

`scripts/lib/safe_markup_taint.py` (the guard of #3766) flagged **125** `mark_safe` / `SafeString` /
`SafeText` call sites in `python/djust` (tests and `djust/theming` excluded) as markup built from a
value it cannot show is escaped. This is the record of reading every one of them, not of counting
them. #3820 then made the two wrappers around `mark_safe` visible to the guard (`_safe`, `safe_html`:
103 more sinks) and taught it the cases that made most of the 125 need a person; what is left is the
baseline below. Section "#3820" has the numbers and what each lesson was worth.

## Result

| verdict | meaning | count |
|---|---|---|
| **S** | safe: every raw-looking value is a constant, escaped, coerced or validated (evidence cited) | #3774: **125** sites, 96 sinks in 93 functions audited, 29 re-sourced (below). #3820: **199** sinks once the wrappers count; the guard proves 144 of them itself and **55 sinks in 51 functions** are still audited by hand (40 sinks in 37 of the original functions, 15 sinks in 14 functions the wrappers and the review of #3848 added) |
| **U** | a raw value reaches the markup unescaped | **0** in the flagged sinks; one CLASS found one call below them and fixed: attribute NAMES (below). #3820 read 15 more sinks and found none; it makes no claim about the 144 the guard proves, beyond what the guard and the sweep show |
| **N** | needs a decision | **3** items, filed as #3818, #3819 (two items), all since decided and fixed (below); one adjacent finding filed as #3824, also fixed |

The sinks were flagged because the guard was name-based and single-function: it did not follow a value
across a call into a helper that escapes it, treated a name that is reassigned (`width = int(width)`) as
still raw, and did not know the project's own escapers. #3820 taught it those, so this list could shrink:
from 199 flagged sinks to 55. A flag is not a vulnerability and a clean verdict is not a proof: the guard has
known false negatives (see "What the guard still does not see").

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

Since #3820 the sweep also drives the **Rust-native handlers** (`rust_handlers.py`): every handler the
module registers is called as the engine calls it, `handler.render(args, [content,] context)`, with the
operands `key=__v_key` resolved from the context and the block body a `SafeString` (the engine's
contract, #2379), in every payload shape and both modes, and once more with every flag key false (a
flag holding the payload is truthy, and `DataTableHandler` returns its loading skeleton before it
builds the table). Their output is parsed like the Django tags'. So the handlers the guard now
proves, the 14 it still flags and the dev-tools nodes are all rendered under the payload.

What the sweep does not do: `ColocatedHookNode` is not given a hostile NAME (see its row). Since #3818 / #3819 the
`live_input` driver also passes an `on*` attribute and URL-valued attributes holding the payload
(they must be refused or neutralised), and `live_render` is given a plain-`str` placeholder holding the
payload (it must come out escaped).

## #3820: the wrapper sinks, and what the guard learned

**The sinks it could not see.** The guard matched `mark_safe`, `SafeString` and `SafeText` (and the
format string of `format_html`) by name. `_safe(...)` (187 call sites in `components/rust_handlers.py`)
and `djust._html.safe_html(...)` are `cast(str, mark_safe(html))`, so a new `def _zz(v): return
safe_html(f"<div>{v}</div>")` passed the guard, this baseline and the sweep. Both are sinks now
(`WRAPPER_SINKS`), and a test fails when another function whose body is one call of a sink on its own
parameter is not named a sink, so the next wrapper cannot be invisible. With them the first guard
flagged **199** sinks (96 audited in #3774 plus 103: 98 in `rust_handlers.py`, 5 in `_dev_tools.py`).

**What it learned**, and what each lesson was worth on this tree (sinks that come back when ONLY that
lesson is switched off, over the final 54; the lessons overlap, so the rows do not add up):

| lesson | sinks it clears |
|---|---|
| flow: only the bindings that REACH a use count (`width = int(width)`), over `if` / `for` / `while` / `try` / `with` / `match` | 53 |
| the project's escapers, trusted by the module that defines them (`safe_url`, `url_attr`, `srcset_attr`, `safe_css_*`), in the position they are clean in, and relative imports resolved to that origin | 41 |
| the block `content` of a Rust handler's `render(self, args, content, context)`: the engine hands it over as a `SafeString` | 41 |
| a call to a helper this file defines, judged by what it returns | 21 |
| a class table (`self.ICONS.get(k, "")`) and class constants | 17 |
| record builders (`upload_attrs`, `pad_extras`, `clamp_cells`, `rich_html`, `render_markdown`, ...) and their fields | 15 |
| narrowing: `x in CONSTANT`, `x == "a"`, `isinstance(x, int)` on the branch where they hold | 12 |
| an imported string constant (`ITEMS_HTML`) | 2 |
| `safe_css_*` clean when their `default` is, `sum` of clean values | 0 on this tree (pinned by tests) |

Everything it learned errs towards NOT clearing: a coercion in one branch, past a `continue` / `break`,
in a loop that comes back round, in a `try` body whose handler runs, in a `with` that can swallow an
exception, after a `+=` / `append`, in a closure, under `global` / `nonlocal` / `:=` / `finally`,
beside an `import` or `match` capture of the same name, or in a function that calls `locals()` keeps
the name raw (the cases are in `test_safe_markup_guard_3820.py`). `assert` narrows nothing, because
`python -O` removes it.

**The lesson that was a bug.** While teaching it class tables: `TABLE.get(key, default)` was judged by
the table alone, so a raw `default` came out clean. No site in the tree used it. It is flagged now.

**Re-exports.** `safe_url` (three tag modules), `conditional_escape` (`_dev_tools.py`) and
`escape_json_for_script` (three call sites) were imported through a re-export, which the guard does not
trust; they import from the defining module. `TestNoEscaperIsImportedThroughAReExport` finds the class
without running the guard.

**What the analysis is checked against.** `python/tests/flow_fuzz_3820.py` generates random functions,
RUNS them with a marker as the raw value and fails when a `mark_safe` that received the marker was
not flagged: 0 false negatives in 40 000 programs across 16 seeds, and 57 unsafe functions from an independent review's corpus are RUN (each must emit the payload raw) and flagged. Twenty-two deliberately broken analyses (a loop that
forgets its `break` or `continue` states, an `if` that follows one branch, a merge that keeps the first
path, narrowing on the wrong edge, an `assert` that narrows, an exception that sees only the state
before the `try`, a helper that is always clean, a mutated collection still taken as constant, `.get`
ignoring its default, ...) are each caught by the fuzz or a directed row.

**What the guard still does not prove**, and why 14 functions are audited by hand (13 below and `JSChain.__html__`, which sits behind a `__getattr__`): a helper method
whose parameter the caller escaped (`_render_tree_node`'s `depth`, `_render_node`'s `e_event`,
`FeedbackWidgetHandler`'s `e_event`), a nested function called with literal arguments (`_dbtn`), a
value the standard library makes digits (`date.isoformat()`, `calendar.monthdayscalendar`), and a
`str(x) in TABLE` test (narrowing reads `x`, not `str(x)`). Each needs a rule with its own proof
obligation (all callers visible; a type fact), which is a different change.

**The review of the first version of this change** (#3848) found that it trusted too much, and 27 of what it
found had been flagged by the first guard and were not any more. What changed, each pinned by the corpus the
reviewer wrote (run, not read):

* a class attribute read through `self.X` is a constant only if nothing writes it: not under `setattr`,
  `__dict__`, `__setattr__`, `vars`, `globals()` anywhere in the file, not in a class that inherits (anywhere in
  the package) from one that writes its constructor keywords with `setattr` (`Component` does, for every
  keyword of `__init__` and `update()`), not when changed through `self` / `cls` / an alias, not when a
  class of the same file overrides it, not for a dataclass-style field;
* a collection a membership test narrows against, and a module or class table read from a function, is a
  constant only if nothing in the file changes it in place (any mutating method, `D[k] = v`, `|=`), declares
  it `global`, or hands it on (`a = D`, `f(D)`, `return D`); a LOCAL list is judged by its own function;
* a helper is a helper only if nothing rebinds it (`global`, `globals()`, `setattr`) and a bare name in a
  method is the module's, not the class body's (Python's own scoping);
* equality narrows only against a literal; an import the file rebinds is not the import; a function whose
  every returned value is a sink applied to a parameter (through `str`, `.strip()`, `cast`, `*args`, a
  lambda, a `partial`) is a wrapper and a sink, found by data flow;
* the depth bound flags instead of clearing; a `global` / `nonlocal` write from another scope is a raw
  binding; the handlers of an `except*` run one after another;
* `attr_name` is a legal NAME, and `onmouseover` is legal, so it is not cleared; `escape_json_for_script`
  leaves `"` alone, so it is clean only for `json.dumps` output in script TEXT; a `safe_url` / `safe_css_*`
  value is clean in text or a quoted attribute, not in `href={...}`; a `*args` / `**kw` may hide the
  default;
* `str.replace` with a raw argument, `re.sub`, `str * n`, `a.__add__(b)`, `d.update(k=raw)`,
  `d.setdefault(k, raw)`, `d |= {...}`, `items += more` and more ways to alias a sink are judged.

**What the guard still does not see**, probed by the review and listed in one issue: a dict KEY built from
raw data and then iterated, a closure that appends to the enclosing function's list, `format_html_join`'s
format string, an `await` or `*args` as the sink argument, `@html_safe` / `__html__` and a `SafeString`
subclass, a sink in a default argument or a decorator, `str.translate`, a module-level or class-body loop,
Python 3.14 t-strings. Its docstring says so.

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

Verdict **S** for every row. Paths are relative to `python/djust/`. The 37 original functions that
remain are the ones the guard still cannot prove; the 14 `*Handler.render` / `FileTreeNode.render` / `JSChain.__html__` rows
are the wrapper sinks it cannot prove. Line numbers are those of the
branch this file was generated on; regenerate with `python scripts/lib/safe_markup_audit.py --table`.
Every cited line is resolved by the script against the files, not typed; an evidence line is the
first match inside its scope.

| file:line (the def) | function | sinks | verdict | why it is safe | evidence (file:line) |
|---|---|---|---|---|---|
<!-- audit-table:begin -->
| `components/base.py:317` | `Component.asset_tags` | 1 | S | joins djust.assets.tags.asset_tags() results, which are format_html SafeStrings | `components/base.py:319`<br>`assets/tags.py:214` |
| `components/icons.py:174` | `render_icon` | 1 | S | class names are the constant size label plus html.escape(custom_class); every extra attribute VALUE is html.escape'd and every extra attribute NAME is checked by attr_name (before #3774 the name was only html.escape'd, which cannot make a name safe: `x onfocus=alert(1) y` rendered a live handler; fixed, python/tests/test_attr_name_injection_3774.py); since #3818 the extra attributes first pass djust._attr_policy.passthrough_attrs (an on* name is refused with ValueError, a URL the allowlist rejects renders `#`); px is int(); svg_inner is the bundled / DJUST_COMPONENTS_ICON_SETS (developer settings) markup keyed by name, never the caller's text | `components/icons.py:215`<br>`components/icons.py:225`<br>`components/icons.py:224`<br>`components/icons.py:221`<br>`components/icons.py:207`<br>`components/icons.py:199` |
| `forms.py:856` | `FormMixin.as_live` | 1 | S | joins adapter.render_field() output; BaseAdapter escapes wrapper/label/help text/errors/choices/values and (since #3774) checks attribute names in _build_tag; since #3818 the widget attrs it merges pass djust._attr_policy.passthrough_attrs (an inline event handler name is refused with ValueError, a rejected URL renders `#`, a submitting `type` is left out); a custom adapter owes the same (FrameworkAdapter contract) | `forms.py:897`<br>`frameworks.py:115`<br>`frameworks.py:261`<br>`frameworks.py:200`<br>`frameworks.py:221` |
| `template_tags/lorem.py:42` | `LoremTagHandler.render` | 1 | S | wraps the paragraphs of django.utils.lorem_ipsum (a fixed corpus) in <p>; same as Django's own lorem tag | `template_tags/lorem.py:64`<br>`template_tags/lorem.py:26` |
| `templatetags/djust_pwa.py:190` | `djust_sw_register` | 1 | S | nonce_attr is escape()'d in _nonce_attr; the URL and scope are _script_json(): json.dumps with < > & and U+2028/9 escaped, so neither can end the script element | `templatetags/djust_pwa.py:216`<br>`templatetags/djust_pwa.py:90`<br>`templatetags/djust_pwa.py:100` |
| `templatetags/live_tags.py:254` | `_client_config_html` | 1 | S | the three meta prefixes are escape()'d; the route-map script is routing.get_route_map_script (format_html around json.dumps of developer URL config, which since #3819 is passed through escape_json_for_script so `<`, `>` and `&` cannot end the script whatever the config holds) | `templatetags/live_tags.py:279`<br>`routing.py:472`<br>`routing.py:535` |
| `templatetags/live_tags.py:810` | `live_input` | 1 | S | html is build_tag output: every attribute VALUE is escape()'d, select options escape()'d, textarea content escaped; attribute NAMES (event=, passthrough keys) were not checked until #3774 (the audit's one U, fixed in djust._html.attr_name, python/tests/test_attr_name_injection_3774.py); the passthrough attributes are judged by djust._attr_policy.passthrough_attrs (#3818: an on* name is refused with ValueError, a URL the allowlist rejects renders `#`, a submitting `type` is left out; python/tests/test_passthrough_attr_policy_3818.py) | `templatetags/live_tags.py:910`<br>`templatetags/live_tags.py:633`<br>`_attr_policy.py:171`<br>`_html.py:26`<br>`_html.py:84`<br>`templatetags/live_tags.py:717` |
| `templatetags/live_tags.py:1035` | `ColocatedHookNode.render` | 1 | S | the name is the tag's parse-time literal (plus the view's module.qualname): template/code, not request data; it is escape()'d into data-hook; the body is </script>-escaped; the nonce is escape()'d. NOT escaped: the JS-comment banner `/* COLOCATED HOOK: <name> */` interpolates the name raw, so a name carrying `</script>` or `*/` would end the script or the comment; it is a parse-time literal the template author wrote, hence not exploitable, and the sweep drives this tag with a plain name for that reason | `templatetags/live_tags.py:1050`<br>`templatetags/live_tags.py:1045`<br>`templatetags/live_tags.py:1062` |
| `templatetags/live_tags.py:1158` | `djust_skeleton` | 1 | S | shape is checked against _SKELETON_VALID_SHAPES; width/height pass _SKELETON_SIZE_RE; class_ goes through build_tag (escaped); count is int()-clamped | `templatetags/live_tags.py:1204`<br>`templatetags/live_tags.py:1145`<br>`templatetags/live_tags.py:1213` |
| `templatetags/live_tags.py:1803` | `_render_sticky_child_html` | 2 | S | the wrapper's ids are escape()'d; the body is the child's own template render (autoescaped by the engine) stamped by _stamp_view_id, which escape()s view_id | `templatetags/live_tags.py:1864`<br>`templatetags/live_tags.py:1870`<br>`templatetags/live_tags.py:1863` |
| `templatetags/live_tags.py:1986` | `live_render` | 2 | S | ids and the CSP nonce are escape()'d, lazy['trigger'] is checked against ('flush', 'visible'), the child body is a template render stamped by _stamp_view_id. The one formerly raw piece, lazy['placeholder'], goes through conditional_escape since #3819: a marked-safe value is markup, a plain str is text (Django's own rule) | `templatetags/live_tags.py:2265`<br>`templatetags/live_tags.py:2313`<br>`templatetags/live_tags.py:2494`<br>`templatetags/live_tags.py:2496` |
| `components/templatetags/_charts.py:612` | `HeatmapNode.render` | 1 | S | cell_size int(); cell colours come from interpolate_color (a #rrggbb string built from ints); axis labels and title conditional_escape'd | `components/utils.py:145`<br>`components/templatetags/_charts.py:638`<br>`components/templatetags/_charts.py:691` |
| `components/templatetags/_charts.py:909` | `CalendarHeatmapNode.render` | 1 | S | year, cell_size, cell_gap int(); fills are the constant LEVELS; date_str is date.isoformat(); title conditional_escape'd | `components/templatetags/_charts.py:930`<br>`components/templatetags/_charts.py:889`<br>`components/templatetags/_charts.py:1019` |
| `components/templatetags/_forms.py:31` | `slider` | 1 | S | min/max/step/value/value_end are int()'d; name, label, event and class conditional_escape'd | `components/templatetags/_forms.py:81`<br>`components/templatetags/_forms.py:83` |
| `components/templatetags/_forms.py:463` | `render_confirm_dialog` | 1 | S | every kwarg is conditional_escape'd; `body` is the already-rendered tag body (Django nodelist.render, Rust block handler's rendered children), autoescaped by the engine | `components/templatetags/_forms.py:486`<br>`components/templatetags/_forms.py:536`<br>`components/rust_handlers.py:4195` |
| `components/templatetags/_forms.py:566` | `render_popconfirm` | 1 | S | every kwarg is conditional_escape'd; `content` is the rendered tag body (nodelist.render / the Rust block handler's children) | `components/templatetags/_forms.py:583`<br>`components/templatetags/_forms.py:615`<br>`components/rust_handlers.py:4204` |
| `components/templatetags/_forms.py:1443` | `model_table` | 1 | S | every row value is conditional_escape(str(v))'d before the table is built; page and total_pages int(); event names and labels conditional_escape'd | `components/templatetags/_forms.py:1537`<br>`components/templatetags/_forms.py:1562` |
| `components/templatetags/_advanced.py:144` | `CalendarViewNode.render` | 1 | S | year, month, start_day int(); month and day names from calendar; event titles conditional_escape'd; event colours through safe_css_color | `components/templatetags/_advanced.py:166`<br>`components/templatetags/_advanced.py:225`<br>`components/templatetags/_advanced.py:190` |
| `components/templatetags/_advanced.py:778` | `OrgChartNode.render` | 1 | S | the tree comes from _render_node, which conditional_escape's id, name and title and sends the avatar through url_attr | `components/templatetags/_advanced.py:823`<br>`components/templatetags/_advanced.py:737`<br>`components/templatetags/_advanced.py:751` |
| `components/templatetags/_advanced.py:1517` | `PromptEditorNode.render` | 1 | S | the template text is conditional_escape'd FIRST; {{var}} tokens are \w+ and are replaced by conditional_escape'd values inside <mark>; rows int() | `components/templatetags/_advanced.py:1565`<br>`components/templatetags/_advanced.py:1550` |
| `components/templatetags/_advanced.py:1962` | `RibbonNode.render` | 1 | S | position is checked against the four corner names; the variant class is a constant from VARIANT_MAP; text and class conditional_escape'd | `components/templatetags/_advanced.py:1973`<br>`components/templatetags/_advanced.py:1981` |
| `components/templatetags/_advanced.py:2217` | `AgentStepNode.render` | 1 | S | status is checked against STATUS_ICONS' keys before it reaches the class; the icon is a constant entity; tool, duration and content conditional_escape'd | `components/templatetags/_advanced.py:2226`<br>`components/templatetags/_advanced.py:2206` |
| `components/templatetags/djust_components.py:136` | `render_modal_description` | 1 | S | `content` is the rendered {% modal_description %} body (nodelist.render / the Rust block handler) | `components/templatetags/djust_components.py:140`<br>`components/templatetags/djust_components.py:269`<br>`components/rust_handlers.py:260` |
| `components/templatetags/djust_components.py:143` | `render_modal` | 2 | S | title, close event and ids conditional_escape'd; size from a four-key dict, role from a two-way test, heading level int() in 2..6; `content` is the rendered tag body | `components/templatetags/djust_components.py:179`<br>`components/templatetags/djust_components.py:187`<br>`components/templatetags/djust_components.py:200`<br>`components/templatetags/djust_components.py:261` |
| `components/templatetags/djust_components.py:595` | `TooltipNode.render` | 1 | S | text, position and component_id conditional_escape'd; tip_id is the escaped component id or a counter; content is the rendered body | `components/templatetags/djust_components.py:610`<br>`components/templatetags/djust_components.py:587` |
| `components/templatetags/djust_components.py:2038` | `code_block` | 1 | S | theme must match [A-Za-z0-9_-]+ and is escaped; language, filename and code conditional_escape'd; the stylesheet tag is asset_tags (format_html); the hljs JSON is conditional_escape'd into data-hljs | `components/templatetags/djust_components.py:2061`<br>`components/templatetags/djust_components.py:2099` |
| `components/templatetags/djust_components.py:2405` | `render_popover` | 1 | S | trigger, placement, title and ids conditional_escape'd; `content` is the rendered tag body | `components/templatetags/djust_components.py:2418`<br>`components/templatetags/djust_components.py:2446` |
| `components/templatetags/djust_components.py:2557` | `render_collapsible` | 1 | S | trigger, event and ids conditional_escape'd; `content` is the rendered tag body | `components/templatetags/djust_components.py:2593`<br>`components/templatetags/djust_components.py:2578` |
| `components/templatetags/djust_components.py:2870` | `tree_view` | 1 | S | node id and label conditional_escape'd; depth is a number; event names conditional_escape'd | `components/templatetags/djust_components.py:2895`<br>`components/templatetags/djust_components.py:2904` |
| `components/templatetags/djust_components.py:3078` | `render_context_menu` | 1 | S | label and id conditional_escape'd; `content` is the rendered tag body | `components/templatetags/djust_components.py:3102`<br>`components/templatetags/djust_components.py:3089` |
| `components/templatetags/djust_components.py:3136` | `date_picker` | 1 | S | year and month int() (month_name is calendar's); the selected, range, label, error and event values conditional_escape'd; date_str is built from ints | `components/templatetags/djust_components.py:3175`<br>`components/templatetags/djust_components.py:3188` |
| `components/templatetags/djust_components.py:4975` | `rich_select` | 1 | S | variant names match ^[a-z0-9][a-z0-9-]{0,31}$ (_VARIANT_NAME_RE); ids, names, values, labels, placeholder escaped; aria from field_aria_attrs; option bodies from _rich_select_option_html, which escapes | `components/components/rich_select.py:35`<br>`components/templatetags/djust_components.py:5141`<br>`components/templatetags/djust_components.py:5049` |
| `components/templatetags/djust_components.py:5832` | `render_theme_toggle` | 1 | S | mode, icon and label come from the constant _THEME_TOGGLE_MODES; current, event, class and id conditional_escape'd; the icons are render_icon output | `components/templatetags/djust_components.py:5825`<br>`components/templatetags/djust_components.py:5849` |
| `components/templatetags/djust_components.py:5922` | `PageHeaderNode.render` | 1 | S | title, subtitle, description and class conditional_escape'd; the breadcrumb and actions slots are rendered nodelists | `components/templatetags/djust_components.py:5929`<br>`components/templatetags/djust_components.py:5937` |
| `components/templatetags/djust_components.py:6081` | `SidebarNode.render` | 1 | S | title, class, ids and events conditional_escape'd; items come from _render_item (labels escaped, hrefs through safe_url) | `components/templatetags/djust_components.py:6118`<br>`components/templatetags/djust_components.py:6035`<br>`components/templatetags/djust_components.py:6064` |
| `components/templatetags/djust_components.py:7168` | `FeedbackWidgetNode.render` | 1 | S | mode is checked against VALID_MODES; the buttons are constants plus the conditional_escape'd event, an int star index and escaped emoji keys | `components/templatetags/djust_components.py:7163`<br>`components/templatetags/djust_components.py:7175` |
| `components/templatetags/djust_components.py:9050` | `render_dropdown_menu` | 1 | S | label, event, class and align conditional_escape'd; items come from render_menu_item (labels escaped, href through url_attr); a block body is the rendered tag body | `components/templatetags/djust_components.py:9064`<br>`components/templatetags/djust_components.py:8969`<br>`components/templatetags/djust_components.py:9030` |
| `components/rust_handlers.py:10739` | `AgentStepHandler.render` | 1 | S | status is reset to 'pending' unless it is a key of the class's STATUS_ICONS table, so the class name and the icon (the table's constant entities) are fixed text; tool, duration, class and the content are conditional_escape'd before they are interpolated; same as AgentStepNode | `components/rust_handlers.py:10746`<br>`components/rust_handlers.py:10749`<br>`components/rust_handlers.py:10758`<br>`components/rust_handlers.py:10732` |
| `components/rust_handlers.py:4650` | `AppShellHandler.render` | 1 | S | id and class conditional_escape'd; `sidebar` and `rest` are the two halves of `content`, the block body the engine hands a handler as an already-rendered SafeString (registry.rs, call_block_handler_with_py_sidecar, #2379; pinned by test_safe_markup_guard_3820.py), split at a parsed </aside> and emitted in the same markup, strip() only trims whitespace | `components/rust_handlers.py:4652`<br>`components/rust_handlers.py:4663`<br>`components/rust_handlers.py:4639` |
| `components/rust_handlers.py:8138` | `CalendarHeatmapHandler.render` | 1 | S | year, cell size and gap int() (a failed int falls back to today's year / constants), so every coordinate is arithmetic on ints and on date calendar math; date_str is date.isoformat() of a date built from the int year; val is float(); the cell colour is _get_color(): a constant of LEVELS; month labels come from a constant list; the title is conditional_escape'd; same as CalendarHeatmapNode | `components/rust_handlers.py:8158`<br>`components/rust_handlers.py:8162`<br>`components/rust_handlers.py:8247`<br>`components/rust_handlers.py:8249`<br>`components/rust_handlers.py:8252`<br>`components/rust_handlers.py:8121` |
| `components/rust_handlers.py:8957` | `CalendarViewHandler.render` | 1 | S | month, year and start_day int() (a failed int falls back to a constant); the month name is calendar.month_name[month], conditional_escape'd; the weeks come from calendar.Calendar.monthdayscalendar, so `day` is an int and date_str is digits (numeric format specs); event titles conditional_escape'd and event colours go through safe_css_color; same as CalendarViewNode | `components/rust_handlers.py:8975`<br>`components/rust_handlers.py:9016`<br>`components/rust_handlers.py:9029`<br>`components/rust_handlers.py:9034`<br>`components/rust_handlers.py:9002` |
| `components/rust_handlers.py:933` | `DataTableHandler.render` | 2 | S | everything the guard cannot see through is the nested _dbtn(val, label), whose only call sites pass the literals ('compact', 'comfortable', 'spacious') and their labels, and whose `density_event` was conditional_escape'd at the top; the rest of the table (cells, toolbar, search, pagination, footer) is proved by the guard: every kw value is conditional_escape'd or int()-coerced where it is read, the footer aggregates are sum/min/max/round/len of float() values | `components/rust_handlers.py:987`<br>`components/rust_handlers.py:1192`<br>`components/rust_handlers.py:1193`<br>`components/rust_handlers.py:1194`<br>`components/rust_handlers.py:1931`<br>`components/rust_handlers.py:1940` |
| `components/rust_handlers.py:5083` | `FeedbackWidgetHandler.render` | 1 | S | mode is checked against VALID_MODES before it reaches the class; the three button builders take the conditional_escape'd event and render constants, an int star index (range) and escaped emoji keys; `value` is only compared or digit-checked, never printed; same as FeedbackWidgetNode | `components/rust_handlers.py:5090`<br>`components/rust_handlers.py:5093`<br>`components/rust_handlers.py:5081`<br>`components/rust_handlers.py:5131`<br>`components/rust_handlers.py:5156` |
| `components/rust_handlers.py:8807` | `FileTreeHandler.render` | 1 | S | class, event and selected conditional_escape'd; the tree is _render_tree_node, which conditional_escape's name and type, takes its icons from class constants, and whose `depth` is an int at every call site (0 here, depth + 1 in the recursion); same as FileTreeNode | `components/rust_handlers.py:8816`<br>`components/rust_handlers.py:8831`<br>`components/rust_handlers.py:8760`<br>`components/rust_handlers.py:8776`<br>`components/rust_handlers.py:8788` |
| `components/rust_handlers.py:7871` | `HeatmapHandler.render` | 1 | S | cell_size int(); every coordinate is arithmetic on it and on len() of the data; values are float(); cell colours come from interpolate_color (a #rrggbb string built from ints; it raises on a colour that is not hex); axis labels and title conditional_escape'd; same as HeatmapNode | `components/rust_handlers.py:7896`<br>`components/rust_handlers.py:7965`<br>`components/rust_handlers.py:7969`<br>`components/rust_handlers.py:7869`<br>`components/utils.py:145`<br>`components/rust_handlers.py:7929` |
| `components/rust_handlers.py:9514` | `OrgChartHandler.render` | 1 | S | the tree comes from _render_node, which conditional_escape's id, name and title, sends the avatar through url_attr and takes the already-escaped event (e_event here) from this method; same as OrgChartNode | `components/rust_handlers.py:9555`<br>`components/rust_handlers.py:9556`<br>`components/rust_handlers.py:9495`<br>`components/rust_handlers.py:9487` |
| `components/rust_handlers.py:431` | `ProgressHandler.render` | 1 | S | value is the Python tag's clamp, max(0, min(100, int(value))): an int in 0..100; label, size and colour conditional_escape'd; the track size is a constant looked up by the escaped size | `components/rust_handlers.py:437`<br>`components/rust_handlers.py:438`<br>`components/templatetags/djust_components.py:655` |
| `components/rust_handlers.py:10159` | `PromptEditorHandler.render` | 1 | S | the template text is conditional_escape'd FIRST; {{var}} tokens are \w+ and are replaced by conditional_escape'd values inside <mark>; rows int(); same as PromptEditorNode | `components/rust_handlers.py:10205`<br>`components/rust_handlers.py:10191`<br>`components/rust_handlers.py:10173` |
| `components/rust_handlers.py:10524` | `RibbonHandler.render` | 1 | S | position is checked against the four corner names; the variant class is a constant from VARIANT_MAP; text and class conditional_escape'd; same as RibbonNode | `components/rust_handlers.py:10535`<br>`components/rust_handlers.py:10543`<br>`components/rust_handlers.py:10532` |
| `components/templatetags/_dev_tools.py:586` | `FileTreeNode.render` | 1 | S | class, event and selected conditional_escape'd; the tree is _render_tree_node, which conditional_escape's name and type, takes its icons from class constants, and whose `depth` is an int at every call site (0 here, depth + 1 in the recursion) | `components/templatetags/_dev_tools.py:595`<br>`components/templatetags/_dev_tools.py:609`<br>`components/templatetags/_dev_tools.py:539`<br>`components/templatetags/_dev_tools.py:555` |
| `js.py:330` | `JSChain.__html__` | 1 | S | the value is json.dumps(self.ops) through escape_json_for_script, which neutralises < > & and U+2028/9, so it cannot end an inline <script> element; it is NOT attribute-safe (the JSON's own quotes end a quoted value) and the docstring says so. Flagged since #3848 because the guard clears escape_json_for_script only for json.dumps output placed in script text, and cannot follow self.to_json() through JSChain.__getattr__ | `js.py:355`<br>`js.py:325` |
<!-- audit-table:end -->

Refs #3774, #3766, #3775, #3818, #3819, #3820, #3824.
