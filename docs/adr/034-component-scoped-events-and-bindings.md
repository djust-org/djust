# ADR-034: Typed component bindings and instance-scoped events

**Status**: Proposed
**Date**: 2026-09-19
**Deciders**: Project maintainers
**Evidence baseline**: `40786f668` on `feat/components-catalogue`.
**Related**:

- [ADR-031](031-class-level-component-rendering.md): per-view component bindings.
- [ADR-032](032-component-scoped-rendering.md): conditional subtree rendering.
- [ADR-033](033-plain-component-state-and-identity.md): plain-component state and identity.
- [ADR-035](035-django-native-form-and-object-lifecycle.md): proposed Django-native form/object lifecycle.
- [ADR-036](036-typed-event-parameter-contracts.md): proposed strict parameter contracts and canonical markup.
- [ADR-037](037-event-contract-checks-and-executable-documentation.md): proposed shared checks and fixture-backed docs.
- [ADR-038](038-explicit-context-and-state-exposure.md): proposed explicit context, persistence, and browser exposure.

## Summary

A component owns its UI mechanics; the owner of application behavior determines
where an event goes. Ordinary row actions pass record IDs to a view handler without
creating a server-side component per row. When a reusable, stateful component owns
the behavior, the view subscribes to its typed outputs using Python references:

```python
# Proposed API; not available in the current release.
@project_menu.on.selected
def on_project_menu_selected(self, component: DropdownMenu, value: str) -> None:
    self.selected_action = value
```

Opening, closing, and validating a selection belong to `DropdownMenu`. The view
handles what a valid selection means to the application. Each view instance has
its own component state; the framework supplies the actual originating component
to the callback. Repeated stateful components use the same subscription convention;
they are not required just to repeat an action button.

**Static analyzability is a release requirement, not a later enhancement.**
`project_menu` and `selected` must be real, typed Python symbols. Implementing
`.on` with an unrestricted `__getattr__`, `Any`, or an untyped decorator would
undermine this decision.

New component APIs and imports below are proposals; the delegated row example
uses existing view-handler syntax. This ADR neither implements the proposed APIs
nor changes the status of ADR-031 through ADR-033.

## Context and problem

The component catalogue exposed two related problems:

1. The dropdown-menu demo emitted `toggle_menu` without a host handler, producing
   a server error. Adding that handler fixes the demo but leaves an application
   author responsible for rediscovering the component's basic behavior.
2. Two dropdowns emit the same event names. Developers need an undocumented or
   easily missed identity convention to update only the originating dropdown.

At the evidence baseline:

- [DropdownMenu](../../python/djust/components/components/dropdown_menu.py) is a
  plain `Component`; it renders `toggle_event` and per-item `event` names rather
  than implementing local handlers.
- [Component.event_attrs](../../python/djust/components/base.py) can attach a
  `name` parameter. Components with their own form-field `name` do not use that
  parameter as component identity. It is not a universal scoping mechanism.
- `LiveComponent` descriptors and `BoundComponent` already provide per-view
  state and component-local dispatch. However, descriptor access currently
  returns an untyped proxy; simply adding new decorator syntax is insufficient.
- [ViewRuntime._dispatch_component_event](../../python/djust/runtime.py) already
  resolves component IDs against the view's registry. The proposed API should
  build on this dispatch path, not invent a second browser event system.
- [event_handler](../../python/djust/decorators.py) has no `component=` or
  `event=` subscription arguments today.
- The [website guide](../website/guides/components.md) presents multiple models,
  while the [AI reference](../ai/components.md) describes plain components as
  having no events. Neither is a sufficient, consistent authoring contract.

The initial design suggestion was
`@event_handler(component="project_menu", event="selected")`. It makes routing
explicit, but misspelled names remain ordinary valid Python strings. Editors
cannot reliably rename them or infer the callback contract without custom
framework analysis. Requiring authors and AI tools to reproduce those strings
is avoidable.

## Decision

### D0. Choose ownership before choosing routing syntax

| What owns the behavior? | Opinionated default |
| --- | --- |
| Browser presentation only | Native HTML controls for disclosure, focus, and dismissal where suitable; no server component just to track visibility. |
| The page/application | An ordinary `@event_handler()` receives typed context such as `project_id`; use this for simple repeated row actions. |
| A reusable stateful component | A local action updates that component and emits a typed output; subscribe with `@component.on.output`. |

Repetition alone does not require a component registry, and singleton placement
alone does not require a scoped component. Class-level declarations can represent
collections through a keyed binding, but doing so has real lifecycle costs. Use
that machinery only when individual components actually own state or behavior.

Prefer browser-owned visibility when Python neither reads nor controls it. Native
popover controls can handle showing and dismissal without a server round trip,
but do not themselves implement a complete ARIA menu's keyboard navigation. See
the [HTML Standard's popover guidance](https://html.spec.whatwg.org/multipage/popover.html#the-popover-attribute).
Do not assert literal zero latency or assume CSS alone supplies accessible menu
behavior. Support, keyboard operation, focus restoration, and patch interactions
must be verified against djust's supported browsers.

Server-owned state remains appropriate when application behavior needs to observe,
persist, or control it. A component must declare which side owns each state value;
do not expose a Python `open` property that silently disagrees with a browser-owned
popover. Changing the current dropdown's state ownership is an explicit component
migration, not an incidental side effect of the new decorator syntax.

There is a concrete integration constraint: at the baseline,
[_handleDjClick](../../python/djust/static/djust/src/09-event-binding.js) calls
`preventDefault()`. A native toggle must not also be a `dj-click` control unless
that interaction is deliberately supported. Likewise, do not promise that adding
`popovertargetaction="hide"` to an action button will close it when the framework
cancels its default action. Native behavior, selection dismissal, and VDOM patch
preservation need explicit integration tests. This ADR does not change the client.

### D1. Use one declarative, typed subscription convention

For the stateful-component case, declare fixed interactive components on the view
class, before their callbacks.
Subscribe with `@component.on.output`. The decorator captures the declaration
object; class construction resolves its attribute name and validates ownership.
It does not look up a component by a user-written string.

```python
# Proposed imports and API, not executable against the current release.
from djust import LiveView
from djust.components.interactive import DropdownMenu


class ProjectView(LiveView):
    template_name = "projects/detail.html"

    project_menu = DropdownMenu(
        label="Project",
        items=[
            {"label": "Edit", "value": "edit"},
            {"label": "Archive", "value": "archive"},
        ],
    )
    account_menu = DropdownMenu(
        label="Account",
        items=[{"label": "Settings", "value": "settings"}],
    )

    def mount(self, request, **kwargs):
        self.selected_action = ""
        self.show_account_settings = False

    @project_menu.on.selected
    def on_project_menu_selected(self, component: DropdownMenu, value: str) -> None:
        self.selected_action = value

    @account_menu.on.selected
    def on_account_menu_selected(self, component: DropdownMenu, value: str) -> None:
        self.show_account_settings = value == "settings"
```

```html
{{ project_menu }}
{{ account_menu }}
<p>Selected project action: {{ selected_action }}</p>
{% if show_account_settings %}<p>Account settings selected.</p>{% endif %}
```

`project_menu` exists while the class body runs; `self.project_menu` does not.
The `.on.selected` decorator already identifies both source and output, so a
second `@event_handler()` is neither necessary nor permitted on that callback.
Ordinary view event handlers continue to use `@event_handler()` unchanged.

Callback names use `on_<component>_<output>` for readability, but names do not
control routing. Renaming the callback cannot silently disconnect the binding.
The example's arbitrary application action values are data, not handler names;
selection must still be checked against the component's configured items.

### D2. Make the contract visible to ordinary Python tooling

Each interactive component has an explicit typed output namespace. For example,
`DropdownMenu.on.selected` is a typed decorator for a method receiving the bound
`DropdownMenu` and `value: str`, returning `None` or an awaitable of `None`.
It preserves the decorated method's signature. Payload parameter names matter
because framework invocation uses named arguments.

Requirements:

- Typed source or generated, checked-in stubs expose the concrete outputs.
  A runtime registry alone is not enough. No catch-all attribute or `Any`
  fallback may make unknown outputs appear valid.
- Descriptor access and injected `component` preserve the public component type,
  including typed state such as `open: bool`. The existing `BoundComponent`
  proxy must be adapted or replaced with a typed binding implementation; a cast
  that masks an incompatible runtime object does not satisfy this contract.
- Component item configuration is typed too, for example with `TypedDict`
  definitions for action items and separators. It must not default to `dict`
  of `Any` throughout the public API.
- The supported mypy and Pyright configurations must reject misspelled component
  names, nonexistent outputs, missing or mistyped payload parameters, and a
  callback annotated with the wrong component type. They must accept synchronous
  and asynchronous methods without requiring an application-specific plugin.
- Where generation is needed, one checked schema generates declarations, stubs,
  and reference tables. CI fails on drift. Do not maintain three handwritten
  lists of event names.

Examples that must fail static checks include an undefined `projet_menu`,
`project_menu.on.seleted`, and a selected callback with `value: int`. A plain
linter such as Ruff can catch undefined names, but it does not replace a type
checker for attribute and signature validation. Untyped application code still
gets class-construction and Django system-check diagnostics.

The exact decorator typing must be proven with a small prototype before accepting
this ADR. A pleasing syntax without working negative type tests is not sufficient.

The [C1 typing proof](034-typing-proof.md) now verifies an isolated concrete
descriptor and signature-preserving output decorators with both checkers. It is
not the production subscription implementation; C1 and this ADR remain open.

### D3. Components own mechanics; views handle semantic outputs

The dropdown implements input actions such as toggle, close, and select on the
side that owns the relevant state. Server-owned selection validates that the item
exists and is enabled, updates local state, closes the menu, then emits
`selected(value=...)` through a typed internal output handle. With client-owned
visibility, immediate dismissal stays local, but a semantic server selection
output still requires validation of the submitted item. Output authors must not
introduce a parallel string-based emit API that bypasses that contract.

The view receives an output after the component has applied its own behavior.
For a project-menu callback, `component` is the same bound instance exposed by
`self.project_menu`; writing `component.open` updates that instance's state.
No search by name, generated handler aliases, or application-owned dispatch loop
is required. An unobserved output is allowed: a menu can function without an
application callback.

There is at most one view callback per source/output pair. Duplicate subscriptions
are errors, not implicit multicast. Normal subclass method replacement is allowed;
inherited subscriptions resolve against the effective declaration and must be
revalidated when a subclass replaces it. A declaration reused under two names or
on unrelated owner classes is rejected rather than ambiguously aliased.

Callbacks run in the originating event's dispatch cycle. Async callbacks are
awaited, errors use the normal event error path, and rendering follows the local
action and its callback. This does not introduce automatic database transactions
or promise state rollback after an exception. ADR-032 still decides between a
subtree update and a full view render; changing sibling/view state must not be
lost to an unconditional component-only patch.

### D4. Bind state and routes to an instance, never the class declaration

Class-level declarations are reusable configuration, not mutable user state.
Binding creates independent state, configuration containers, identity, and
subscriptions for each view instance. Callbacks are resolved against that view,
not stored as bound methods on a globally shared declaration.

The browser supplies an opaque component identity and a local action. The server
resolves identity only against the current view's component registry, then checks
the declared action and payload. Unknown or stale identities fail closed for
these new bindings. They never fall back to a similarly named view handler.
Nested markup targets the nearest owning component, not every ancestor.

Semantic outputs such as `selected` are server-side notifications, not additional
browser-callable methods. A client cannot directly invoke `on_project_menu_selected`
to bypass the dropdown's validation. Source component objects and trusted output context are
injected by the framework, never deserialized from client input. A scoped callback
cannot also be exposed as an API endpoint or ordinary client event handler.
Explicitly declared client observations (D8) are a separate input category; they
do not make all output callbacks callable from the browser.

Scoping is not authorization. A malicious client can target any visible instance
and invent values. Existing authentication, CSRF/transport, event-security, rate
limits, and per-event object authorization must remain in force. Business handlers
must still authorize actions against the underlying records.

### D5. Repeated rows delegate actions; repeated stateful components use keys

For a row menu that merely chooses an operation on a record, pass the record ID
directly to the view. No `self.menus` list, component-name search, or per-row Python
callback is necessary. This sketch uses existing view-handler syntax and static
rows; the buttons can sit inside a tested presentation-only menu:

```python
from django.core.exceptions import PermissionDenied
from djust import LiveView, event_handler


class RowActionsView(LiveView):
    template_name = "projects/row_actions.html"

    def mount(self, request, **kwargs):
        self.projects = [{"id": 42, "name": "Alpha"}, {"id": 87, "name": "Beta"}]
        self.selected_project = 0
        self.selected_action = ""

    @event_handler()
    def project_action(self, project_id: int, action: str) -> None:
        if project_id not in {42, 87}:
            raise PermissionDenied
        if action not in {"details", "activity"}:
            raise ValueError("Unknown project action")
        self.selected_project = project_id
        self.selected_action = action
```

```html
{% for project in projects %}
  <span>{{ project.name }}</span>
  <button type="button" dj-click="project_action"
          dj-value-project-id:int="{{ project.id }}" dj-value-action="details">
    Details
  </button>
  <button type="button" dj-click="project_action"
          dj-value-project-id:int="{{ project.id }}" dj-value-action="activity">
    Activity
  </button>
{% endfor %}
<p>{{ selected_project }}: {{ selected_action }}</p>
```

For database rows, replace the static ID allowlist with fresh, per-event object
lookup and authorization. Never treat a supplied ID or a list rendered earlier
as permission to mutate a record. Prefer one handler per business operation when
operations have different permission/validation contracts; that is not one
handler per row. Large lists still need pagination or virtualization.

This path is **not entirely statically type-checked**: `dj-click="project_action"`
and the parameter/action strings live in a template. Template-to-handler audits,
runtime value validation, and browser tests remain necessary. Do not describe it
as eliminating all magic strings or as being verified by Ruff alone.

Only when each repeated item genuinely needs a stateful component should it use
the following collection pattern, for example when server code must inspect and
restore each menu's state. This is an advanced extension, not required plumbing
for the ordinary row-action case above.

Use the component's typed collection factory. It preserves the concrete output
namespace; a generic container that degrades `.on` to `Any` is not an acceptable
shortcut. The same callback receives whichever bound item emitted the output.

```python
# Proposed API. Static rows keep this example independent of a database model.
from djust import LiveView
from djust.components.interactive import DropdownMenu


class ProjectListView(LiveView):
    template_name = "projects/list.html"
    row_menus = DropdownMenu.collection()

    def mount(self, request, **kwargs):
        self.selected_project = ""
        self.selected_action = ""
        self.row_menus.sync([
            ("project-42", DropdownMenu(
                label="Project 42",
                items=[{"label": "Details", "value": "details"}],
            )),
            ("project-87", DropdownMenu(
                label="Project 87",
                items=[{"label": "Details", "value": "details"}],
            )),
        ])

    @row_menus.on.selected
    def on_row_menus_selected(self, component: DropdownMenu, value: str) -> None:
        self.selected_project = component.key
        self.selected_action = value
```

```html
{% for menu in row_menus.values %}
  {{ menu }}
{% endfor %}
<p>{{ selected_project }}: {{ selected_action }}</p>
```

`sync()` accepts ordered `(key, declaration)` pairs so duplicate keys can be
detected before a dictionary silently overwrites them. Keys are nonempty stable
strings, normally derived from record IDs, never list positions. `component.key`
is read-only: the declared attribute name for a fixed component and the supplied
key for a collection member. It is not the opaque transport identity or a form
field's `name`.

Reconciliation preserves live state for retained keys, updates configuration,
initializes new keys, and unregisters removed keys. Constructor state defaults
apply only to new members; reordering must not close or transfer an existing open
menu. If new item configuration invalidates a selection, the component normalizes
that state without inventing a user selection output.

Removing and reintroducing a key creates a new instance lifetime. Delayed events
from the old lifetime are rejected. Snapshots persist serializable configuration,
keys, identity/lifetime data, and state, not callbacks. Restore rebinds declarations
and callbacks from server code and revalidates current membership/permissions
before dispatch. Session restore, reconnect, and navigation must not resurrect a
removed or unauthorized item.

### D6. Validate at the earliest reliable stage

| Mistake | Required detection |
| --- | --- |
| Misspelled component variable | Python name checking; class definition if executed |
| Unknown `.on` output | mypy/Pyright; attribute access during class definition |
| Wrong callback component/payload type | mypy/Pyright; runtime payload validation |
| Missing callback parameter or conflicting decorator | Type checking where expressible; class validation/system checks |
| Foreign declaration or duplicate subscription | Class construction and Django system checks |
| Duplicate/empty collection key | `sync()` before modifying the registry |
| Stale/foreign client identity or undeclared action | Server dispatch before mutation |
| Incorrect template target or nested ownership | Template/catalogue audits and browser tests |

Diagnostics identify the view, component attribute, output, callback, and expected
signature, with an actionable correction. Django checks must discover configured
LiveViews without needing a user to click every component. Dynamic definitions are
also validated when registered. Neither static typing nor a successful initial
render proves the entire event path is correct.

### D7. Teach the ownership rule and test the documentation

Every interactive component page must show:

1. Its ownership mode, typed configuration, state, actions, and output signatures.
2. A complete one-instance example; the basic UI must work without host plumbing.
3. Two instances with separate callbacks and independent state.
4. A delegated repeated-row example for presentation-only menus, or a keyed
   repeated example for stateful components, with the distinction explained.
5. Persistence, keyboard/focus behavior, authorization responsibilities, and the
   distinction between local actions and public outputs.

Use one schema and executable fixtures for generated API tables, catalogue demos,
website examples, and the concise AI reference. Provide an explicit version and
import path so an AI cannot mix the new interactive API with the legacy renderer.
The template audit must understand scoped actions and outputs; demanding that
every rendered `dj-click` name exist on the host view would enforce the wrong model.

Rendered documentation and its website navigation must be verified. A markdown
file in the repository alone is not evidence that developers can find the guide.

### D8. Client-owned interactions can send optional no-op notifications

Client-owned does not mean invisible to Python. For a component whose declared
visibility mode is client-owned, subscribing to its typed `toggled` observation
opts into a server notification after the browser changes visibility:

```python
# Proposed subscription; project_menu has client-owned visibility in this case.
# logger is the application's ordinary Python logger.
@project_menu.on.toggled
def on_project_menu_toggled(self, component: DropdownMenu, open: bool) -> None:
    logger.debug("Menu %s reported open=%s", component.key, open)
```

The convention is **local interaction, optional notification, no-op by default**:

1. The browser performs the interaction immediately, without waiting for a server
   acknowledgement. Notify from the resulting visibility change, not an assumed
   inversion of a click; Escape, outside dismissal, and keyboard activation count.
2. No subscription means no notification traffic. The subscription itself opts
   in; do not require a second flag or an empty handler just to avoid a missing
   handler error. The registry emits the client observation binding automatically.
3. The server validates the registered source and declared `open: bool` payload,
   then supplies the bound component to the observer. This is an explicitly
   permitted client observation route, not direct invocation of a Python callback.
4. If the observer changes no reactive state, return the normal no-op acknowledgement:
   no template render, VDOM diff, or DOM patch solely because notification arrived.
   Authors need no `return NoUpdate`, `render=False`, or special no-op decorator.
5. If it changes reactive state, normal change detection and rendering apply.
   An observer that opens a server-rendered panel must not have that update silently
   suppressed. Notification exceptions follow normal diagnostics and do not roll
   back the browser's already completed interaction.

Receiving a notification does not implicitly assign `component.open` or persist
client visibility as authoritative state. The payload is an observation from an
untrusted client, not proof of what a person saw and not an authorization signal.
Callbacks that need to store an observation can do so explicitly; writing reactive
state means the response is no longer guaranteed to be a no-op. Hidden bookkeeping
such as an acknowledgement sequence must not itself mark the view dirty.

Observations describe current state, not a durable log of user actions. They may
be coalesced while events are pending; handlers must tolerate missed intermediate
states. Use a binding-lifetime identifier and monotonic sequence to reject stale
or reordered reports. While disconnected, toggling remains local. On reconnect,
an enabled observation reports the current value after rebinding, rather than
replaying clicks; subscribers must tolerate repeated values. Document this
best-effort contract and do not use observations for exactly-once business actions.

The same `toggled` contract can be emitted by a server-owned component after its
local handler changes `open`, but that state change normally requires rendering.
Do not label a server-driven toggle as a no-op. Normal security and rate limiting
still apply to notifications, and browser/server feedback loops must not emit a
second observation merely because the server acknowledged the first one.

For presentation-only repeated rows without bound component objects, an optional
notification may delegate typed record context to a view handler, following D5.
It must retain the same no-subscription/no-traffic and no-state-change/no-render
semantics. A keyed stateful collection is not required just to report a row toggle.
The concrete native-event binding syntax must be checked against the existing
client before being published; this ADR does not claim a `dj-toggle` attribute
already exists.

## Alternatives considered

| Alternative | Assessment |
| --- | --- |
| Ordinary view handler with record/action parameters | Chosen for presentation-only row actions. No server component identity is needed, but the template still needs contract checks. |
| Keep `name=` and manually route in view handlers | Compatible today, but duplicates mechanics and source lookup; retain only as a legacy option. |
| Rename every event, e.g. `toggle_project_menu` | Separates fixed widgets but grows boilerplate and string contracts; poor for repeated components. |
| `@event_handler(component="project_menu", event="selected")` | Explicit but typo-prone; requires custom string analysis and weakens ordinary rename/completion support. |
| `@event_handler(component=project_menu, event=DropdownMenu.Events.SELECTED)` | Removes raw strings, but separates two values whose compatibility must be checked; more verbose than a source-owned typed output. |
| Constructor `on_selected=self.callback` in `mount()` | Viable and typeable, including repeated items. Not the default because callback restoration and lifecycle rebinding need an explicit contract; bound methods must not be persisted as component state. Avoid a third equivalent subscription style. |
| `@project_menu.on("selected")` | Fixes the component reference but retains an unchecked output string; rejected. |
| `@project_menu.on_selected` | Equally viable if concretely typed. `.on.selected` is chosen to group outputs in a discoverable namespace distinct from methods; do not expose both spellings. |
| `@project_menu.on.selected` | Chosen: source and output are one typed reference, callback methods remain ordinary Python, and fixed/repeated cases share a convention. |

Do not also offer string aliases or infer subscriptions from callback names in the
new API. Multiple equivalent spellings would undermine the convention. Component
IDs necessarily remain serialized strings on the wire; removing Python string
bindings does not remove the need for runtime validation.

## Compatibility and migration

Introduce the new family explicitly under the proposed
`djust.components.interactive` module. The first implementation is an interactive
`DropdownMenu` that can reuse the existing visual renderer. Do not silently turn
today's plain `DropdownMenu` into a descriptor or change existing event routing.

The existing plain-component imports, `name=`, `toggle_event=`, item `event`
configuration, and view handlers continue to work. New interactive dropdown items
use `value`, and the component owns their action routing; legacy handler-name
options are rejected by the new constructor rather than ambiguously combined.

If accepted, this ADR refines ADR-033 D7: simple repeated actions remain view-owned
and carry record IDs; genuinely stateful repeated widgets use keyed scoped bindings
instead of manual component-name lookup. It does
not undo ADR-033's state, wire typing, or legacy identity behavior. A change to
default imports or removal of the legacy API requires a separate compatibility
decision and migration window.

## Implementation and acceptance gates

1. **Typing proof:** prototype the bound type, `.on.selected`, and `.on.toggled`.
   Add positive and negative mypy/Pyright fixtures, including inherited declarations,
   renamed methods, wrong sources, async callbacks, and misspelled outputs.
2. **Binding and dispatch:** implement isolated bindings and subscription metadata
   on the existing runtime; prove registry-only lookup, source injection, lifecycle
   restoration, and failure behavior. Preserve ordinary handler compatibility.
3. **Dropdown pilot:** document its state owner and implement its local mechanics
   and output contract. Test one instance and two instances, plus a separate
   presentation-only delegated-row fixture. Native visibility requires the D0
   integration gates, not just different markup.
4. **Documentation:** publish the examples and generated reference together; update
   website navigation, catalogue fixtures, and AI guidance in the same rollout.
5. **Stateful repetition:** prove the typed collection and its lifecycle separately
   before advertising it as supported. Do not block ordinary delegated row actions
   on this extension or publish collection snippets as already working.

Required integration coverage includes both HTTP and WebSocket dispatch paths,
the real browser client, and Django and Rust template backends. Specifically test:

- Opening one dropdown does not mutate another, or another user's view.
- Selection invokes only the matching callback, once, with the actual bound source;
  forged, disabled, and unknown values cannot produce a valid selection output.
- Collection reorder, duplicate keys, removal/re-addition, nested ownership,
  reconnect, and restored navigation preserve the correct identity and state.
- Direct client invocation of a subscription callback is rejected; unknown scoped
  targets never fall back to a view handler.
- Callback changes to sibling/view state render correctly; exceptions and async
  callbacks follow the documented event lifecycle without extra callback invocations.
- Legacy applications still use their existing plain-component event handlers.
- Delegated row actions carry the correct record ID after reorder and reject
  unauthorized records. A presentation-only menu needs no server toggle handler.
- Native visibility and focus remain correct through patches and reconnects;
  click default prevention does not break toggling or documented dismissal.
- Without an observation subscription, native toggles emit no server events.
  With one, clicks, Escape, and outside dismissal report the actual visibility and
  correct source without blocking the UI. An unchanged observer produces no render,
  diff, or patch; a state-changing observer does render its changes.
- Notification exceptions, disconnect/rebind, duplicate/reordered reports, and
  removed row identities do not roll back local visibility, replay business
  actions, leak state to another component, or create an acknowledgement loop.
- Every documented output is exercised, and every local action resolves; browser
  tests inspect visible results and server errors, not merely HTTP 200 responses.

This document-only change can validate links and snippet syntax. It cannot claim
that these future runtime or type-checking acceptance gates have passed.

## Consequences and non-goals

Application authors learn one ownership rule and one source-scoped subscription
pattern when they need component-owned behavior. Editors and AI
tools get concrete symbols and signatures rather than prose-only routing rules.
The framework takes on real complexity: typed descriptors, collection lifecycle,
declaration validation, and a coordinated documentation migration.

This is not a generic signal bus, implicit event bubbling, an authorization model,
or a promise that all events produce subtree-only patches. It does not require
application JavaScript. New transport machinery is justified only if the pilot
shows the existing component-target envelope cannot meet the stated invariants.
