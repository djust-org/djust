# Secure Defaults — Pattern Catalog

This document names the four **proven secure-by-default patterns** the djust
framework uses so that a *new* feature inherits the same posture instead of
re-deriving (and re-getting-wrong) the controls. Each pattern below lists:

- **Threat** — what it defends against.
- **Canonical site** — the verified `file:symbol` to read and copy.
- **Shape to copy** — the minimal skeleton for a new feature.

> Companion docs: `docs/SECURITY_GUIDELINES.md` is the *how-to-use* guide for the
> `djust.security` utilities and banned patterns; `docs/PULL_REQUEST_CHECKLIST.md`
> has the per-PR "Secure defaults" review subsection that cross-references the four
> patterns here. This file is the *architectural* catalog: the shapes that make a
> feature secure-by-default.

All file:line citations below were grep-verified at write time (#1197 canon-doc
citation discipline). Line numbers drift — if a citation looks off, grep the
named symbol; the symbol name is the load-bearing reference.

---

## 1. Denylist serialization

**Threat.** A model instance reaches the wire (template context, JIT
serialization, state snapshot) and leaks a sensitive field — `password`, an auth
flag, a PII column — because serialization defaulted to *allow everything*.

> [!IMPORTANT]
> **The floor governs the template getattr sidecar too (#1986).** Besides the
> eager serialization dict, the Rust template engine has a *lazy* fallback: for
> a `{{ obj.attr }}` path not in the eager dict, it `getattr`-walks the live
> model instance (reverse relations, managers, methods — ADR-024). That walk
> would bypass this floor via **two mechanisms** — a floor field read off a
> raw model during the getattr walk, and a raw model `__dict__`-dumped during
> value conversion — reachable on SEVEN surface paths (direct field;
> manager/queryset traversal `{{ x.mgr.first.password }}`; `{% for %}`
> iteration; `_`-prefixed access `{{ obj._meta }}`, which also segfaulted the
> worker; `.values()`/`.values_list()` projections; a non-model intermediary
> object `{{ presenter.user.password }}` / a model **method** returning a
> model; and a raw `list`/`tuple` of models via an intermediary). The fix is
> **two structural chokepoints** (one per mechanism, #1646), plus the sidecar
> proxies:
>
> - **Proxies** — every model/manager/queryset entering the sidecar is wrapped
>   in `_SidecarModelProxy` / `_SidecarQuerySetProxy` (`serialization.py`),
>   which **transitively** protect everything they return
>   (`_protect_sidecar_value`), refuse exactly what the eager dict refuses —
>   the floor fields, the sensitive method NAMES and `_`-prefixed names — by
>   calling the SAME per-attribute authority the eager loops call
>   (`DjangoJSONEncoder._attr_is_serializable`, #2614; see below), and
>   **refuse `.values()`/`.values_list()` projections wholesale** (their rows
>   have no model identity to floor; precompute in `get_context_data()`).
> - **Chokepoint 1 (getattr walk)** — the Rust resolve walk
>   (`crates/djust_core/src/context.rs`) routes every just-materialized value —
>   after `getattr` AND the auto-call — through `_protect_sidecar_value`, so a
>   model is floor-wrapped **however it was reached** (a raw intermediary
>   object, a Rust-auto-called method result). Python proxies alone can't cover
>   those.
> - **Chokepoint 2 (value-conversion root)** — `FromPyObject for Value`
>   (`crates/djust_core/src/lib.rs`) routes **any** raw Django model through
>   `normalize_django_value` (denylist serializer) instead of the `__dict__`
>   bulk-dump, so a raw model reaching a `Value` via a list/tuple/dict
>   container is floor-filtered too. A djust proxy's `__djust_serialize__` hook
>   is preferred first.
>
> A refused name raises `AttributeError` → empty render. Auto-call itself is
> always on: 2.0 removed the `template_auto_call` kill switch.
>
> **A model form holds the model's values behind a plain `dict` (#3790).**
> `ModelForm.initial` is `model_to_dict(instance)`, so by the time a template
> reads `{{ form.initial.password }}` / `{{ form.password.value }}` the value is
> a string and no `Model` is left for the proxies to refuse. The walk's floor is
> therefore `_protect_sidecar_walk` (the leaf `_protect_sidecar_value` plus a
> `_SidecarFormProxy` for a `ModelForm`, a `_SidecarFormSetProxy` for a formset
> over a model, and a `_SidecarBoundFieldProxy` for the `BoundField` of a floor
> field), asked by the three template-walk chokepoints and by the `{% load %}`
> bridge's operand walk, with the model's floor read off the form's `instance`
> (or the formset's model) through the same `_attr_is_serializable`. `data` is
> matched through its prefix (`u-password`, `form-0-password`). A refused read
> logs once, by field name, so the breaking change is never silent.
>
> **A second floor, keyed on the WIDGET (#3798).** What a person *typed* into a
> password input is no column of any model (`UserCreationForm.password1`), so
> the model floor cannot name it. A field whose widget is a password input
> (`PasswordInput` or a subclass, any widget with `input_type == "password"`, or
> a `MultiWidget` with such a part) is therefore a floor field **by type**,
> whatever its name and whatever the form is: a `ModelForm` or a plain `Form`.
> It is the same chokepoint (`_model_form_floor` -> `_floor_field_readable`), so
> the form, bound-field and formset proxies and the conversion floor all hold it:
> `{{ form.password1.value }}`, `.data`, `.initial`, `{{ form.data.password1 }}`
> (and `u-password1`, and a `MultiWidget`'s `pair_0` / `pair_1`),
> `{{ form.cleaned_data.password1 }}`, `subwidgets`, a loop variable, and a
> formset's `data` / `cleaned_data` / `initial` (read from the forms it built, so
> a field added in a form's `__init__` is held too). The test is
> `djust.serialization.field_has_password_widget`, which
> `djust.forms.is_sensitive_form_field` (`persisted_form_input`) asks too, so the
> two cannot disagree about what a password field is. Nothing a re-save needs is
> lost: Django's `PasswordInput` never writes a value back into the control, so
> `{{ form.password1 }}` of a typed-but-invalid form is byte-identical with and
> without this floor (`python/tests/test_password_widget_floor_3798.py`,
> `TestTheControlStillDraws`); a template that *branches on* the typed value
> (`{% if form.password1.value %}`) now takes the else branch, which is the one
> breaking change, and logs once by field name. djust's own component tags draw a
> password field blank, editable or not (`form_field_floor_state` -> `"password"`).
>
> **What the form floor does NOT do (stated as limits, each pinned by a test in
> `python/tests/test_form_floor_followups_3790.py`).**
>
> - *A SECRET floor field's control is drawn without its stored value, and the column
>   is protected by `disabled=True` (#3797).* The floor fields that are **secrets**
>   (the predicate below) are never rendered with their value: `{{ form.field }}`,
>   `as_widget`, `as_field_group`, `as_p` / `as_table` / `as_ul` / `as_div` /
>   `render` / `{{ form }}`, a loop's `{{ x }}`, `visible_fields`, a formset's
>   `as_p`, the `{name: html}` snapshot of a form kept in state, and the `BoundField`
>   handed to a bridged `{% load %}` tag or filter (`{% render_field form.password %}`,
>   `|add_class`) draw the control the way Django draws a field that has none: a text
>   control with no `value=`, an empty `<textarea>`, no `selected` option, an
>   unchecked checkbox, a `MultiWidget` with empty parts
>   (`_floor_blank_bound_field`). Django's own widget does the drawing, so a custom
>   widget needs no cooperation. Validation messages of such a field that interpolate
>   the value (`ChoiceField`'s `invalid_choice`, which a `disabled` field is validated
>   with against its STORED value) are replaced by a generic one.
>   **The predicate** (`_floor_field_is_secret`, decided from what the floor already
>   classifies): the field's widget is a password input; or its name matches
>   by SUBSTRING, anywhere in the name (`password`, `passwd`, `secret`, `token`,
>   `apikey`: `newpassword`, `api_token`), or has a whole WORD (a segment split on
>   `_`, `-` or a camelCase boundary, so `keyboard`, `monkey`, `hashtag`, `pinned` and
>   `pinyin` do not match) from `passphrase` / `pwd` / `key` / `hash` / `salt` /
>   `credential` / `credentials` / `otp` / `signature` / `pin` (`secret_key`,
>   `auth_hash`, `pin_code`);
>   or the model's column is of a `sensitive_field_types` type. Everything else on the
>   floor keeps drawing its REAL state in the control: privilege flags (`is_staff`,
>   `is_superuser`), selects (`role`, `groups`) and ordinary declared-sensitive text
>   (`email`, `first_name`). The reason: those are not secrets-in-HTML, and drawing a
>   control empty is not harmless, because **an empty control is posted back empty**: a
>   blank text control clears the column, an unselected `<select>` posts its first
>   option, and an unchecked checkbox posts nothing, so a classic `ModelForm` POST
>   saves `False` (the stock `UserChangeForm` would demote a superuser). Nothing at
>   render time can prevent that for a secret (HTML `disabled` / `readonly` and leaving
>   the control out still save `""`); only `disabled=True` **on the form field** does,
>   because Django then ignores the posted value and keeps the initial one.
>   **So the protection lives in the form**: `FormMixin.get_form()` (and so
>   `ModelFormMixin`) marks every blanked field `disabled=True` on a form that edits
>   an existing row (`protect_blanked_floor_columns`; a new record has nothing to lose,
>   and a password-widget field is not touched: Django already draws it empty and the
>   person types a new value into it). A plain `ModelForm` in a classic view cannot be
>   fixed at render time: the system check **`djust.S014`** reports an editable secret
>   floor field in a `ModelForm` (declare `disabled=True`, leave it out, or opt the
>   model field out). The escape hatch is the model's
>   `djust_serialize_sensitive_fields`: a field named there is not a floor field, so it
>   is drawn as Django draws it. The stock `django.template` engine is the oracle and is
>   not changed; a view's own Python (`form["password"].value()`) reads the real value.
>   Two widgets keep their own rendering by their own contract: Django's
>   `ReadOnlyPasswordHashWidget`, matched by exact type (a masked summary: algorithm,
>   iterations, the first six characters of the salt and hash; a subclass is not
>   trusted), and `PasswordInput(render_value=True)`, the form author's explicit echo.
>   **Not covered**: a value the project's own code puts elsewhere (`widget.attrs`,
>   `help_text`, a closure that holds the form), a form class's own custom render
>   method or renderer template that reads `form.instance` / `form.data` itself, and a
>   bridged tag that walks the real FORM it is handed (it receives the real form, as
>   before; only a `BoundField` operand is a blank copy).
> - *A secret that is also read-only.* A floor field with a secret-like name on a
>   `FormMixin` edit form is drawn empty AND made `disabled=True` (so the empty
>   control cannot overwrite the column), which also means what a person types into
>   it is ignored. That reaches a field that only LOOKS secret (`sort_key`,
>   `token_count` named in `djust_exclude_fields`) and a "set a new password" box on an
>   existing row. djust logs it once per (model, field); the ways out are the model's
>   `djust_serialize_sensitive_fields`, a field name without a secret word, or
>   declaring `disabled=True` yourself.
> - *Known limits of a name heuristic.* Not matched: `pass`, `passcode`, `pw`, `ssn`,
>   `cvv`, `card_number`, `auth_code`, `bearer`, `jwt`, `totp_seed`, `recovery_code`.
>   A project adds more through the model's `DJUST_SENSITIVE_FIELDS` /
>   `djust_exclude_fields` (those make a field a FLOOR field; to also have it drawn
>   empty its name must match the predicate, so rename it or use a password widget).
>   A `get_form()` / `_create_form()` override that does not call `super()` is not
>   protected (the field is drawn empty and NOT disabled); `djust.S014` flags the
>   form class. `djust.S014` also reports a form that is used only from a `FormMixin`
>   view, which is protected at runtime.
> - *Components draw a secret floor field without its value, editable or not; another
>   editable floor field keeps its value.* `dj_form`, `combobox` and `rich_select`
>   read `bound_field.value()` through `_bound_field_value`: `None` for a `disabled` or
>   secret floor field, the real value for another editable one (the component says
>   so once; stripping it would clear it on the next save, #3790).
> - *A management form handed to the template DIRECTLY* as a context variable is a plain
>   `Form` with no floor ([#3799](https://github.com/djust-org/djust/issues/3799)); only a
>   formset's own `management_form` is pinned. The same goes for any plain `Form` that
>   carries `self.instance = <Model>`: the floor is a `ModelForm`'s. **This holds for a view
>   that keeps `formset.management_form` as an attribute** ([#3767](https://github.com/djust-org/djust/issues/3767)):
>   a LiveView used to flatten it to `{TOTAL_FORMS: html, ...}` (no data readable), and it now
>   reaches the template as the form, so `{{ mf.data }}` prints the formset's whole submitted
>   data, as on the plain backend. Read the counts in the view, or render `{{ mf }}`.
> - *A plain `Form` has no MODEL floor*
>   ([#3799](https://github.com/djust-org/djust/issues/3799)): only its
>   password-widget fields are held (above). A plain `Form` that is handed
>   `initial=model_to_dict(user)` still reads that `password` hash back through a
>   field with a text widget, and a field merely *named* `password` over a
>   `TextInput` is not a password field. The floor is the widget's, not a name's:
>   applying the base names to every form would make a login form's typed
>   `password` read back empty, a product decision.
> - *`PasswordInput(render_value=True)` writes the value into the control.* That
>   is the form author's explicit choice, so `{{ form.pw }}` still prints it; the
>   template's READS of the value are still refused. Pinned in
>   `TestTheControlStillDraws` and `test_floor_widget_value_3797.py`.
> - *Values held in plain data are not forms.* A view's own dict
>   (`form_data`, `{"pw": request.POST["password"]}`) is not a `Form`: the floor
>   guards forms and models, never a dict the author built. (`FormMixin`'s
>   explicit exposure blanks sensitive fields in `form_data` already, ADR-038.)
>
> **The invariant**: a template walk never receives a real `BoundField` of a
> floor field, nor the real form (or management form) behind one (a bridged tag's
> operand is the exception named above: a blank copy for a secret, the real
> form). Path segments
> are floored by `_protect_sidecar_walk`; every other producer (a loop
> variable, `visible_fields`, `get_context().fields`, a slice, an alias, a
> formset loop) goes through the one value conversion, which keeps the floored
> object as the value's live handle (`floored_form_handle`). The grammar test
> `python/tests/test_form_floor_differential_3790.py` is what says the list of
> producers is complete.
>
> An object handed to a *tag* (the handler dict, a loop variable in a bridged
> context) keeps the leaf floor and the real form: a tag is the application's own
> code, and a proxy would make `bound_field.value()` read empty.
>
> **The TYPE floor (#1987) — a name-independent second axis.** Besides the
> name/method floor above, a field whose *type* should never reach the client is
> dropped regardless of its name, via the single shared authority
> `_field_type_is_excluded` (called by BOTH the eager loop and the sidecar
> proxy, so they can't drift — #1646): `BinaryField` always; best-effort
> encrypted-field types (an MRO class name **case-insensitively** containing
> `encrypted`/`fernet`, for django-encrypted-fields / django-fernet-fields — no
> hard dependency, excluded fail-closed, with a one-shot DEBUG breadcrumb per
> class so a heuristic false-positive is diagnosable); and any class named in
> `LIVEVIEW_CONFIG['sensitive_field_types']` (a project-configurable list, empty
> by default; case-exact). `FileField`/`ImageField` are
> explicitly **NOT** excluded — they serialize a URL, the intended payload
> ([#1987](https://github.com/djust-org/djust/issues/1987)).
>
> The type floor is unconditional: a per-model `djust_serializable_fields`
> allowlist or a `djust_serialize_sensitive_fields` opt-out names a column and
> does not lift the column's type. The JIT attribute gate
> (`optimization/codegen.py::emittable_names`, which the compiled serializers
> and the Rust serializers call) applies it through
> `DjangoJSONEncoder._attr_is_readable`, the name rules followed by this floor,
> so a template naming such a column reads it as empty on every route. The floor
> governs columns: a `@property`, method, annotation or `values_list` row that
> carries the same value is outside it, as on the eager path. Its cache key
> carries the model class and the configured types, because the verdict depends
> on a column's type.

**Canonical site.**
`python/djust/serialization.py`:

- `_ALWAYS_EXCLUDED_FIELDS = frozenset({"password", "is_superuser", "is_staff"})`
  (line 56) — the secure-by-default **floor**. Dropped on the auto-serialization
  paths regardless of `settings.DJUST_SENSITIVE_FIELDS` configuration **and
  regardless of a per-model `djust_serializable_fields` allowlist** (#1868 — the
  floor is now UNCONDITIONAL; an allowlist may only NARROW, never re-expose a
  floor field; module comment lines 30–40).
- `_resolve_sensitive_fields()` (line 63) — **unions** the floor with the
  optional project-wide `settings.DJUST_SENSITIVE_FIELDS`. Degrades gracefully
  (a missing setting, unconfigured Django, or non-iterable value falls back to
  the floor — serialization never raises because of this lookup).
- `_serialize_model_safely(self, obj)` (line 226) — the serializer entry point.
  A model-level `to_dict()` (read at line 242) is the full opt-out and overrides
  everything. Otherwise it resolves the effective denylist
  (`_get_denied_fields`, line 329 — floor ∪ `DJUST_SENSITIVE_FIELDS` ∪ per-model
  `djust_exclude_fields`, read at line 336), the per-model
  `djust_serializable_fields` **allowlist** (`_get_allowlist_fields`, line 348 —
  read at line 355), and the per-model `djust_serialize_sensitive_fields`
  **opt-out** (`_get_sensitive_optout_fields`, line 368 — read at line 378),
  then decides each field via `_field_is_serializable` (line 391).
- `_field_is_serializable(field_name, denied, allowed, optout=frozenset())`
  (line 391) — the actual per-field precedence, **floor-first (#1868)**
  (body lines 406–417):
  1. **Identity keys** (`_IDENTITY_KEYS = {"pk", "id", "__str__", "__model__"}`)
     always pass. `model_identity(obj)` is the **single producer** of that map
     (#2322) — every model shorthand in the codebase calls it: the main path,
     the depth-limited FK, the max-depth shorthand, the JIT identity-only
     subset, and both `template/rendering.py` fallbacks. Before that they were
     six hand-rolled literals and only two carried `__model__`, so whether a
     serialized model was recognisable as one depended on prefetch depth. If
     you are writing a consumer that must tell a serialized model from a plain
     dict, key on `"__str__"` — that is what the engine itself does
     (`Value::object_str`), and it is the marker every producer has always
     emitted.
  2. **The floor wins first.** If `field_name` is in `denied` (floor ∪
     `DJUST_SENSITIVE_FIELDS` ∪ `djust_exclude_fields`) it is dropped
     **regardless of any allowlist** — UNLESS the developer deliberately
     re-includes it via the per-model `djust_serialize_sensitive_fields`
     opt-out (`optout`). A `djust_serializable_fields` allowlist **alone can NOT
     re-expose** a floor field (e.g. `djust_serializable_fields = ['is_staff',
     'password']` no longer ships those fields).
  3. **If a per-model allowlist is set, it NARROWS** the remaining (now
     floor-cleared) fields — a field passes iff it is in the allowlist (or was
     explicitly opted in via `optout`).
  4. Otherwise the field is serialized.
- `_attr_is_serializable(name, denied, allowed, optout=frozenset())` — the
  **single per-attribute authority** (#2614) that every channel deciding
  whether a model attribute ships calls, and the ONLY production reader of
  `_SENSITIVE_MODEL_METHODS`: the eager field loop (`_serialize_model_safely`),
  the eager `get_*` method loop (`_add_safe_model_methods`), the eager
  `@property` loop (`_add_property_values`), the sidecar proxy
  (`_SidecarModelProxy.__getattr__`), the JIT codegen path and the **Rust
  queryset serializer** — every attribute the generated serializer reads, and
  every attribute `crates/djust_live/src/lib.rs::serialize_object_with_paths`
  reads, is gated by
  `python/djust/optimization/codegen.py::emittable_names`, which asks
  `_attr_is_readable` (this, then the column-TYPE floor below) and nothing
  else (#2685 / #2688; a denied name is omitted from the dict, so
  the template renders `string_if_invalid`). Rust holds no policy of its own:
  `resolve_attr_gate` imports that one Python function and `gated_names` calls
  it once per object LEVEL, threading the result through both recursions — so
  the denylist cannot be duplicated, and therefore cannot drift (#1646). An
  import or call failure propagates out of `serialize_queryset`, and
  `mixins/jit.py::_jit_serialize_queryset` falls back to
  `normalize_django_value` (itself gated) — never to an ungated row.
  Fail-closed precedence, each step can
  only deny: (1) a `_`-prefixed name is refused; (2) a sensitive / expensive
  model-method NAME (`_SENSITIVE_MODEL_METHODS` + the `get_next_by_` /
  `get_previous_by_` prefixes) is refused **whatever kind of attribute carries
  it** — a method, a `@property` that shadows it, or a field — and neither the
  `djust_serializable_fields` allowlist nor the `djust_serialize_sensitive_fields`
  opt-out lifts it (the opt-out is documented for floor FIELDS only; widening
  it to the method set would re-expose `get_session_auth_hash` behind a flag
  named for fields, #1868); (3) otherwise `_field_is_serializable` above.

  Before #2614 the `@property` loop consulted only `_field_is_serializable`
  while the proxy also refused the method set, so a `@property` named
  `get_session_auth_hash` was serialized eagerly and refused lazily — and the
  permissive channel won, because the Rust engine reads the eager dict BEFORE
  it falls back to the sidecar (the proxy's refusal was moot for that name).
  `python/djust/tests/test_property_shadow_parity_2614.py` pins the parity
  matrix (every floor name, every sensitive-method name, both prefixes, a
  `_`-prefixed name and the exact-match spelling variants) against both
  channels, each channel independently, a real GET + WS mount/event, and the
  structural pin that all four sites call the chokepoint. The same file's
  `TestJitCodegenChannel` / `TestJitCodegenParity` / `TestJitRealRenderPaths`
  pin the codegen channel (#2685 — before it, `{{ m.password }}` on a public
  `self.m` shipped the field through `mixins/context.py` →
  `_jit_serialize_model` → codegen, which consulted no floor at all), and
  `TestRustQuerySetChannel` / `TestRustQuerySetRealRenderPaths` pin the Rust
  queryset channel (#2688 — gating codegen alone left `self.users =
  User.objects.all()` + `{{ u.password }}` shipping the pbkdf2 hash on the GET
  html and in **both** WS frames, because a QuerySet never reaches codegen).
  `TestStructuralChokepointCodegen` pins codegen as a caller: the helper calls
  the chokepoint, every write into the result dict sits under the gate for its
  own key, and `compile_serializer` binds `_djust_gate` to the helper. That
  guard check walks the generated code's **AST** rather than grepping for
  `hasattr(` — the text version only inspected lines that already contained
  `hasattr(`, so a `getattr`-shaped emission was invisible to it;
  `test_the_guard_check_catches_an_unguarded_getattr_site` is the empirical
  canary (#1459) that feeds exactly that shape in and asserts it is reported.
- `_field_type_is_excluded(field)` + `_field_type_excluded_for(model_class, name)`
  (the #1987 **TYPE floor**) — the single authority the eager loop
  (`_serialize_model_safely`, checked right after `_field_is_serializable`) and
  the sidecar proxy (`_SidecarModelProxy.__getattr__`) both call, and the JIT
  attribute gate via `DjangoJSONEncoder._attr_is_readable`, so the paths can't
  drift (#1646). Drops `BinaryField` unconditionally, any type in
  `LIVEVIEW_CONFIG['sensitive_field_types']`, and best-effort encrypted-field
  types (MRO class name contains `Encrypted`/`Fernet`); never
  `FileField`/`ImageField` (they serialize a URL).

> [!IMPORTANT]
> **The floor is now an unconditional invariant (#1868).** A per-model
> `djust_serializable_fields` allowlist may only **narrow** the serialized set —
> it can never opt a hardcore-floor field (`password` / `is_superuser` /
> `is_staff`) back in. The **only** way to re-include a floor field is the
> deliberate, loudly-named per-model **`djust_serialize_sensitive_fields`**
> opt-out (default is always deny). This was the open question surfaced by the
> #1867 review and resolved in
> [#1868](https://github.com/johnrtipton/djust/issues/1868) by making the floor
> win — the prior allowlist-first behavior (where
> `djust_serializable_fields = ['is_staff', 'password']` re-exposed those fields)
> is gone. **Migration:** a model that previously relied on the allowlist to
> ship a floor field must now name it in `djust_serialize_sensitive_fields` as
> well — a second, explicit declaration that you accept the disclosure.

> [!WARNING]
> **A row's `__str__` is public text (#3806).** The floor and the per-model
> `djust_exclude_fields` / `djust_serializable_fields` decide which *fields* of a
> row a template can read. They do **not** decide what `str(row)` says:
> `"__str__"` is one of the identity keys (`_IDENTITY_KEYS`, above) that passes
> every one of those rules, `model_identity()` and the full serializer both
> carry it, and `{{ row }}` renders it. This is Django's own behaviour (the same
> template under Django's engine prints the same text), and it was measured
> rather than assumed (`python/tests/test_row_str_is_public_text_3806.py`, run on
> the `DjustTemplateBackend` and on a `LiveView`, with Django's engine as the
> control). `str(row)` ships on each of these paths, whatever the model excludes:
>
> - a top-level loop over a QuerySet, `{% for r in rows %}{{ r }}`;
> - a model in the context, `{{ row }}`, and a foreign key read whole,
>   `{{ row.shelf }}`;
> - a row reached through a relation, `{{ shelf.rows.all.first }}` (and
>   `.last`);
> - on the `DjustTemplateBackend`, a QuerySet's `{{ rows.first }}` /
>   `{{ rows.last }}`, also beside a field read (`{{ rows.first }}|{{ rows.first.name }}`).
>   A LiveView attribute that is a top-level QuerySet renders `{{ rows.first }}`
>   as nothing today (a parity gap that withholds more, not less; pinned as a
>   strict `xfail` so the list above is updated when it is fixed).
>
> A model whose `__str__` builds its text from a field the floor withholds
> therefore shows that field's value in the page: `__str__` returning
> `f"{self.name} <{self.password}>"` renders the password for `{{ row }}` while
> `{{ row.password }}` is empty, and `djust_exclude_fields = ("secret",)` does
> not stop `__str__` from printing `secret`. The stock `User.__str__` is the
> username, so no stock Django model leaks; the author of a model is the only one
> who can. **Author guidance: build `__str__` from public fields only**, the way
> you would for the admin or a log line. The framework does not rewrite
> `__str__` (that would make `{{ row }}` differ from Django's, and a model's
> display text is its author's decision); a field that must never be printed
> belongs in `djust_exclude_fields` *and* out of `__str__`.

**Deliberate opt-out — re-include a floor field only when you mean it:**

```python
class AuditEntry(models.Model):
    # The allowlist narrows what ships; it can NOT re-expose the floor on its own.
    djust_serializable_fields = ["actor", "action", "is_staff"]
    # The floor still drops is_staff above — UNLESS you also opt it in explicitly:
    djust_serialize_sensitive_fields = ["is_staff"]   # I accept shipping this flag.
    # `password` / `is_superuser` are NOT opted in → they stay dropped forever.
```

**Recommended shape for your own code** that serializes user/model data — the
framework now does floor-first internally, but if you hand-roll a serializer,
mirror it: read the floor FIRST and let it win.

```python
# Read the global denylist FIRST — never start from "expose all fields".
from djust.serialization import (
    _resolve_sensitive_fields,   # floor ∪ DJUST_SENSITIVE_FIELDS
    _ALWAYS_EXCLUDED_FIELDS,     # the floor: password / is_superuser / is_staff
)

denied = _resolve_sensitive_fields()
denied |= frozenset(getattr(type(obj), "djust_exclude_fields", ()) or ())
allow = getattr(type(obj), "djust_serializable_fields", None)
# Mirror the framework's deliberate opt-out: only this set lifts the floor.
optout = frozenset(getattr(type(obj), "djust_serialize_sensitive_fields", ()) or ())

for name, value in fields(obj):
    # Floor wins first — unless deliberately opted in.
    if name in denied and name not in optout:
        continue
    # Allowlist only narrows the remaining set.
    if allow is not None and name not in allow and name not in optout:
        continue
    emit(name, value)
```

The principle: the floor must win **before** the allowlist is consulted, so a
developer's allowlist can never silently re-expose `password` / `is_superuser` /
`is_staff` — only the explicit `djust_serialize_sensitive_fields` opt-out can.

---

## 2. HMAC-signed snapshots

**Threat.** State that round-trips through the *client* (a back-navigation state
snapshot the browser echoes back, a resumable token) is attacker-controllable.
Trusting it verbatim is CWE-345 (insufficient authenticity) / CWE-915 (mass
assignment).

**Canonical site.**
`python/djust/_exposure_snapshots.py` (the signed client snapshot of a view's
`state(..., persist="client", client=True)` fields):

- `ClientSnapshot.capture(view)` signs only the fields the view declared for
  the client, in a `django.core.signing.TimestampSigner` with its own salt
  (`_SALT`; keyed on `SECRET_KEY`). The envelope **binds** the payload to the
  session, the user, the tenant and the route (`request_binding()` in
  `python/djust/_exposure_sessions.py`), and carries a `created` timestamp and
  a digest of the declared schema. It is signed, not encrypted, so it carries
  only the binding digest, never the session key itself.
- `ClientSnapshot.restore(token)` verifies signature **+ TTL** (`get_max_age()`
  in the same module: `DJUST_STATE_SNAPSHOT_MAX_AGE`, 3600 s by default, capped
  at 86400) **+ binding + exact keyset**. It returns **`None`** (fail-closed)
  on any of: non-string input, tamper, expiry, a token for another session,
  user, tenant or route, an unknown or missing key, or a value outside the
  declared bounds. A `None` return means the caller MUST discard the token and
  keep the freshly mounted state.

The wiring lives in `ViewRuntime.dispatch_mount` (`python/djust/runtime.py`):
the server signs on emit (`state_snapshot_signed` on the mount and event
frames) and verifies on receive, after the pre-mount auth sequence, before any
client byte reaches the view.

The legacy signed snapshot of djust 1.x (`sign_snapshot` / `unsign_snapshot`,
which signed every public attribute of an `enable_state_snapshot` view) was
removed in 2.0; a token it issued is refused.

**Shape to copy** for a new feature that accepts state back from the client:

```python
# EMIT — sign before sending to the client, bound to who may send it back.
signer = TimestampSigner(salt="myapp.my-token.v1")
response["my_token_signed"] = signer.sign(json.dumps({"binding": binding, "state": state}))

# RECEIVE — verify before trusting. Any failure ⇒ reject and fall through.
try:
    envelope = json.loads(signer.unsign(signed_blob, max_age=max_age))
except (BadSignature, ValueError):
    envelope = None  # rejected: tampered / expired / not ours
if envelope is None or envelope.get("binding") != binding:
    ...  # fall through to the unprivileged default (e.g. the fresh mount()).
```

### Private-attr boundary — READ THIS

The signature covers **declared client fields only**. Nothing a view does not
declare with `persist="client"` is ever in the signed token.

A legacy view's private (`_*`) attributes travel on a **separate, unsigned**
path: `_restore_private_state()` (`python/djust/live_view.py`) assigns each
saved `_*` key from the **server-side Django session**
(`request.session[...__private]`). That path is trusted *because it is
server-side session storage*, not because it is signed.

**Implication:** do **not** store auth/ownership/PII state in `_*` attributes
expecting cryptographic integrity. For private state the guarantee is "lives in
the server-side session, never signed." If a private attr's value would be
dangerous to trust when the session backend is compromised, that is the wrong
place for it — re-derive ownership/auth from the request on each event instead
of persisting it in `_*`.

---

## 3. Fail-closed precedence gate

**Threat.** A diagnostic / introspection / schema endpoint exposes internal
surface (live cross-session state, tracebacks, the full API attack-surface map)
to anyone, because it defaulted to *serve unless explicitly locked down*.

**Canonical site.** Two sibling gates, both **fail-closed with a
non-disclosing 404** (not 403 — a 403 confirms the endpoint exists):

- **OpenAPI schema gate** — `python/djust/api/openapi.py`, `_openapi_gate(request)`
  (line 189). This is the canonical *DEBUG → opt-in-setting → authenticated →
  non-disclosing-404* ladder (first match wins):
  1. `settings.DEBUG` is True → serve (dev convenience).
  2. `settings.DJUST_API_OPENAPI_PUBLIC` is True → serve (operator opted in).
  3. `request.user.is_authenticated` → serve.
  4. Otherwise → `HttpResponse(status=404)`.
  The auth check is fail-closed: a missing `request.user` (no
  `AuthenticationMiddleware`) or an anonymous user falls through to the 404.

- **Observability gate** — `python/djust/observability/views.py`, `_gate(request)`
  A sibling ladder: `settings.DEBUG` must be on, **AND** the request must come
  straight from a loopback peer with no reverse-proxy headers
  (`is_direct_local_request`), **AND** it must carry the project's observability
  token (`has_valid_token`, an HMAC of `SECRET_KEY` sent in
  `X-Djust-Observability-Token`); otherwise 404. The token rung exists because a
  proxy on the same host connects from loopback, so the peer address alone
  cannot prove a request is local. (Pick the rungs that match your endpoint's
  threat model; all return the same non-disclosing 404.) Every endpoint calls
  `_gate(request)` first and returns its 404 response if non-`None`.

A third instance of the *single-source-the-sequence, fail-closed* shape is the
**mount-auth** chokepoint: `python/djust/auth/core.py`,
`run_pre_mount_auth(view_instance, request)` (line 396, added in #1853). It
single-sources the canonical pre-mount order — view-level auth
(`check_view_auth`, line 38) → on success, tenant resolve (`_ensure_tenant`) →
tenant ContextVar bind — across the WebSocket, runtime, and SSE mount paths so a
future edit cannot reorder the steps or drop one on a single transport
(parallel-path drift, #1646). Auth denial **skips** tenant resolve/bind (returns
the redirect immediately); a non-`PermissionDenied` error aborts **fail-closed**.
The post-mount object-permission check is `enforce_object_permission(view, request)`
(`auth/core.py`, line 328).

**Shape to copy** for a new diagnostic / privileged endpoint:

```python
def _my_gate(request):
    # First match wins; default is DENY.
    if settings.DEBUG:
        return None                       # dev convenience
    if getattr(settings, "MY_FEATURE_PUBLIC", False):
        return None                       # explicit operator opt-in
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        return None                       # authenticated
    return HttpResponse(status=404)       # non-disclosing — never 403

def my_view(request):
    gate = _my_gate(request)
    if gate is not None:
        return gate
    ...  # the privileged work
```

Rules: **deny is the default branch**, the gate is enforced **inside the view**
(not only in opt-in middleware — middleware can be omitted from a project's
setup), and the rejection is a **404, never 403**.

---

## 4. `safe_setattr` — guarded attribute writes

**Threat.** Setting an attribute whose *name* comes from client input (state
restoration, deserialized event params) enables prototype-pollution-style
attacks: `__class__`, `__init__`, `__dict__`, or a `_private` framework attr
overwritten via a crafted key.

**Canonical site.**
`python/djust/security/attribute_guard.py`:

- `safe_setattr(obj, name, value, allow_private=False, ...)` (line 191) — use
  this instead of raw `setattr()` whenever the name is untrusted. Returns
  `False` (or raises `AttributeSecurityError` with `raise_on_blocked=True`) for a
  blocked name; otherwise performs the `setattr`.
- `is_safe_attribute_name(name, allow_private=False, ...)` (line 134) — the
  underlying check, which rejects in three ways:
  1. **Dunder/dangerous names** — `DANGEROUS_ATTRIBUTES` (line 28: `__class__`,
     `__bases__`, `__mro__`, `__subclasses__`, `__init__`, `__dict__`, …) plus
     any `__…__` name.
  2. **Private names** — a leading-underscore name is rejected unless
     `allow_private=True`.
  3. **Format/injection guard** — the name must match
     `SAFE_ATTRIBUTE_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")` (line
     125), blocking dots, dashes, spaces, and other special chars.

This is the guard used by the public-state restore path in `websocket.py`
(`safe_setattr(self.view_instance, key, value, allow_private=False)`) and by
`LiveView`'s default restore loop (`live_view.py`, line 885,
`safe_setattr(self, key, value, allow_private=False)`).

**Shape to copy** whenever an attribute *name* originates from untrusted input:

```python
from djust.security import safe_setattr

# Client-controlled keys: keep allow_private=False (the secure default).
for key, value in client_supplied_dict.items():
    safe_setattr(view, key, value)               # silently skips blocked names
    # or, to reject loudly:
    # safe_setattr(view, key, value, raise_on_blocked=True)
```

Only pass `allow_private=True` when the keys come from a **trusted, server-side**
source (e.g. the framework's own session-backed private-state dict) — never for
client-supplied keys.

---

## 5. Template auto-call guards (`alters_data` / `do_not_call_in_templates`)

**The default**: template variable resolution auto-calls no-argument callables
(Django parity, ADR-024) — but a callable with `alters_data = True` is **never
invoked** (the expression renders empty), and one with
`do_not_call_in_templates = True` is used as-is. Every auto-call site shares
these guards: the Rust sidecar resolution walk
(`crates/djust_core/src/context.rs::maybe_call`), the JIT serializer's
generated code (`python/djust/optimization/codegen.py`), and eager model
serialization (`python/djust/serialization.py::_add_safe_model_methods`).

The same guard covers two calls the framework makes by a fixed name on an object a template hands it (#3815). `{% include obj %}` calls `obj.render()` (`python/djust/template/operands.py::render_template_object`) on the five entries `DjustTemplateBackend`, `_rust.render_template`, `RustLiveView`, a LiveView's WebSocket render and a LiveView's HTTP GET render, and (#3880) wherever Django's own engine renders a view's or component's context (below); the component tags (`avatar_group`, `AvatarGroup`, `PresenceAvatars`) call `user.get_full_name()`. A `render` or `get_full_name` whose `alters_data` or `do_not_call_in_templates` is truthy (truthiness, as in Django and the Rust guard) is not called: the include renders the empty string, the avatar falls back to `str(user)`, and a WARNING is logged once per type and method. **Stricter than Django on purpose** — stock Django's `{% include obj %}` calls a `render` marked `alters_data`. A marker read that raises anything but `AttributeError` also refuses (fail closed; `python/djust/_template_guards.py::refused_marker`). The method is looked up once and the markers are read off that same object, so a property that returns a different callable on each read cannot pass the check and have another one called.

Django's stock engine renders the templates of embedded, sticky and `{% live_render %}` child views, streamed fragments, pending layouts, the native renderer, a component's Django-engine fallback, an inclusion tag bridged into a LiveView render, and the test client's `engine="django"` (#3880). The engine is not replaced there: those sites enter `python/djust/_include_guard.py::include_guard_scope`, and a patched `IncludeNode.render` applies the same `refused_marker` check to the operand, which it resolves once and runs through the one bound `render` it checked. Outside the scope the patch is inert, so a render that no djust view or component is making keeps Django's behaviour. A new site that renders through Django's engine must enter the scope or be listed with a reason (`test_every_django_engine_render_is_inside_the_scope_or_says_why_not`). The guard reads the markers off the bound `render` it will call, so a `functools.partial`, a lambda that wraps a flagged function, or a marker set on the class instead of on `render` is not refused, exactly as on the five #3879 entries. A patch of `IncludeNode.render` made by someone else (with or without `functools.wraps`, including a wrapper that names the guard) is held to the guard from the next scope entry on: the entry puts the guard on top of it, and the guard delegates to that patch, outside a scope untouched and inside one with the operand already checked (#3931). One gap remains: a patch swapped in while a scope is already active renders unguarded until the next scope entry.

**Why it matters**: without the `alters_data` refusal, a template typo like
`{{ user.delete }}` or `{{ qs.update }}` would execute a destructive ORM
operation on first render. Django stamps `alters_data = True` on
`Model.save`/`Model.delete`, `QuerySet.delete`/`update`/etc.; djust honors the
same attribute.

**Your responsibility**: stamp `alters_data = True` on any custom model/helper
method that mutates state, exactly as you would in classic Django:

```python
class Invoice(models.Model):
    def cancel(self):
        ...
    cancel.alters_data = True   # {{ invoice.cancel }} renders empty, never runs
```

Regression tests: `python/djust/tests/test_template_auto_call_1985.py`
(side-effect sentinels prove the guarded callables are not executed).

---

## 6. Filter output safety is earned, never declared

**The default**: a template filter's output is auto-escaped. A *whole-value*
grant is decided at one sink
(`crates/djust_templates/src/renderer.rs::filter_output_is_safe`) and needs
one of exactly three things: the filter itself returned a `SafeString`; the
filter is one of the ten built-ins that escape internally (`safe`, `escape`,
`json_script`, `urlize`, …); or the **input was already safe** AND the filter
is `is_safe` — whether a built-in (#2274) or a project filter registered
`is_safe=True` (#2548). Those last two share one `input_was_safe &&`
conjunction so that a future grant has to choose a side.

A sequence's *items* are decided separately, at
`filter_output_items_are_safe` in the same file: `safeseq` / `escapeseq` are
Django's explicit per-item opt-in, and `slice` preserves item safety it was
given. So `{{ L|safeseq|join:"" }}` renders its items raw — Django does the
same, and it is the author's explicit act, but it means "one sink" describes
the value rule, not the whole escaping surface.

**Why it matters**: `is_safe=True` is what Django's docs recommend for every
string-transforming filter, and `django.contrib.humanize` ships three. Until
#2548 the project-filter term stood alone, so `{{ user_text|intcomma }}`
rendered a non-numeric user string raw — stored XSS with no project code.

**Your responsibility**: a filter that produces markup must mark its own
output (`format_html` / `mark_safe` on an escaped body). Never set
`is_safe=True` to "stop double escaping"; it does not do that.

Regression tests: `python/tests/test_custom_filter_is_safe_requires_safe_input_2548.py`
(all three render entries against Django in-process, including the encoded
and attribute-breakout variants). Item-rule
coverage lives in `python/tests/test_context_item_safety_2287.py`.

---

## How to make a NEW feature secure-by-default

When you add a feature that emits data, accepts client-echoed state, exposes an
endpoint, or writes attributes from input, walk this checklist before writing the
happy path:

1. **Does it serialize models/user data?** Start from the denylist
   (`_resolve_sensitive_fields()`), not "expose all fields". The
   `_ALWAYS_EXCLUDED_FIELDS` floor is unconditional. → Pattern 1.

2. **Does it accept state back from the client?** Sign on emit, verify on
   receive (`ClientSnapshot.capture` / `.restore` is the model), bind the token
   to who may send it back, and treat a failed verdict as "reject and fall
   through to the unprivileged default". Remember the signing boundary: the
   signature covers *declared client fields* only — don't put auth/ownership/
   PII in `_*` attrs expecting integrity. → Pattern 2.

3. **Does it expose an endpoint or diagnostic surface?** Gate it with a
   fail-closed precedence ladder whose **default branch denies**, enforced inside
   the view, returning a **non-disclosing 404**. → Pattern 3.

4. **Does it write attributes from input?** Route every untrusted-key write
   through `safe_setattr` with `allow_private=False`. → Pattern 4.

5. **Does it add a security control on a transport?** Add the control in
   `security/` or `runtime.py` so *every* transport inherits it (never hand-rolled
   per transport — parallel-path drift, #1646), and extend the anti-drift nets:
   a parity axis in `python/djust/tests/test_transport_parity_security.py` and a
   concern-4 pin in `python/djust/tests/test_mount_chokepoint_structural.py`
   (`TestMountOrchestrationChokepoint`). See the PR-checklist "Secure defaults"
   and "Transport chokepoint" subsections.

6. **Does it add a way for filter output to skip escaping?** The grant must be
   `input_was_safe &&` gated, or the filter must mark its own output. → Pattern 6.

The throughline of all six patterns: **the default branch is the safe one.**
A new feature is secure-by-default when removing every project-specific override
still leaves it locked down.

---

## Audit cadence

The four patterns above only stay load-bearing if new transports and endpoints
keep routing through them. To catch drift, do a **quarterly** lightweight
re-review (this is a documented checklist, deliberately **not** a STRIDE/DFD
exercise):

1. **Re-inventory the transport chokepoints.** Confirm the mount / event /
   state-apply surface is still reached only through the shared
   `djust.security` / `runtime.py` chokepoints. Grep for new transport entry
   points (anything new that calls `mount()` / dispatches events / applies
   client state) and confirm each routes through `run_pre_mount_auth`
   (`auth/core.py`) and the `ViewRuntime` dispatch path.

2. **Re-run the anti-drift nets against any new transport.** Run
   `pytest python/djust/tests/test_transport_parity_security.py` and
   `pytest python/djust/tests/test_mount_chokepoint_structural.py`. If a new
   transport was added since the last review, confirm it appears in the parity
   axes (`TestViewAuthParity`, `TestObjectPermissionParity`,
   `TestRateLimitParity`, `TestOriginParity`) and in the concern-4 structural
   pin (`TestMountOrchestrationChokepoint`) — a transport not covered by these
   nets is a drift gap.

3. **Spot-check the four patterns for new sites.** For each pattern, grep for
   new call sites added since last review and confirm they copy the canonical
   shape (denylist floor honored, snapshot signed+verified, gate fail-closed,
   `safe_setattr` for untrusted keys).

This is intended to ride along with the existing release cadence — fold it into
the pre-release security audit rather than scheduling a separate event.
