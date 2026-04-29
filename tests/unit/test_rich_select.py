"""Tests for RichSelect component — rendering, variants, trigger behaviour."""

from djust.components.components.rich_select import RichSelect


# --------------------------------------------------------------------------
# Rendering — baseline
# --------------------------------------------------------------------------


def test_renders_with_empty_options():
    picker = RichSelect(name="status", value="", event="set_status")
    html = picker.render()

    assert '<div class="rich-select">' in html
    assert '<input type="hidden" name="status" value="">' in html
    # Placeholder shown when nothing selected
    assert '<span class="rich-select-placeholder">Select...</span>' in html
    # Dropdown list is present but empty
    assert '<div class="rich-select-dropdown" role="listbox">' in html


def test_renders_options_with_selected_value():
    picker = RichSelect(
        name="status",
        options=[
            {"value": "A", "label": "Apple"},
            {"value": "B", "label": "Banana"},
        ],
        value="B",
        event="pick",
    )
    html = picker.render()

    # Trigger mirrors the selected option's label
    assert ">Banana<" in html
    # Active class on the selected row, aria-selected set
    assert "rich-select-option--active" in html
    assert 'aria-selected="true"' in html
    # Hidden input carries the value
    assert 'value="B"' in html


def test_disabled_omits_toggle_handlers_and_trigger_variant():
    picker = RichSelect(
        name="status",
        options=[{"value": "X", "label": "X", "variant": "danger"}],
        value="X",
        event="pick",
        disabled=True,
    )
    html = picker.render()

    assert "rich-select--disabled" in html
    # Disabled attribute present
    assert " disabled" in html
    # Toggle onclick is suppressed (otherwise it'd defeat the disabled state)
    trigger_block = html.split('class="rich-select-trigger')[1].split("</div>")[0]
    assert "classList.toggle" not in trigger_block
    # Trigger does NOT inherit a variant when disabled
    assert "rich-select-trigger--variant-" not in html


# --------------------------------------------------------------------------
# Variants — per-option
# --------------------------------------------------------------------------


def test_per_option_variant_emits_class():
    picker = RichSelect(
        name="s",
        options=[
            {"value": "ok", "label": "OK", "variant": "success"},
            {"value": "fail", "label": "Fail", "variant": "danger"},
        ],
        value="",
        event="pick",
    )
    html = picker.render()

    assert "rich-select-option--variant-success" in html
    assert "rich-select-option--variant-danger" in html


def test_option_without_variant_emits_no_variant_class():
    picker = RichSelect(
        name="s",
        options=[{"value": "plain", "label": "Plain"}],
        value="",
        event="pick",
    )
    html = picker.render()

    assert "rich-select-option--variant-" not in html
    # Default rule stays plain
    assert 'class="rich-select-option"' in html


def test_unknown_but_well_formed_variant_is_accepted():
    """Downstream projects add their own variants by shipping CSS rules
    like ``.rich-select-option--variant-indigo { ... }``. The component
    accepts any well-formed variant name and emits the class; whether
    it renders is up to the consumer's stylesheet."""
    picker = RichSelect(
        name="s",
        options=[{"value": "x", "label": "X", "variant": "cerulean"}],
        value="",
        event="pick",
    )
    html = picker.render()

    assert "rich-select-option--variant-cerulean" in html


def test_builtin_variants_all_supported():
    """Spot-check that every built-in variant renders with its class."""
    picker = RichSelect(
        name="s",
        options=[
            {"value": "a", "label": "A", "variant": "info"},
            {"value": "b", "label": "B", "variant": "success"},
            {"value": "c", "label": "C", "variant": "warning"},
            {"value": "d", "label": "D", "variant": "danger"},
            {"value": "e", "label": "E", "variant": "muted"},
            {"value": "f", "label": "F", "variant": "primary"},
            {"value": "g", "label": "G", "variant": "secondary"},
        ],
        value="",
        event="pick",
    )
    html = picker.render()

    for v in ("info", "success", "warning", "danger", "muted", "primary", "secondary"):
        assert f"rich-select-option--variant-{v}" in html


# --------------------------------------------------------------------------
# Variants — trigger tinting
# --------------------------------------------------------------------------


def test_trigger_inherits_selected_option_variant():
    picker = RichSelect(
        name="s",
        options=[
            {"value": "a", "label": "A", "variant": "info"},
            {"value": "b", "label": "B", "variant": "success"},
        ],
        value="b",
        event="pick",
    )
    html = picker.render()

    # Trigger's variant matches the currently-selected option
    assert "rich-select-trigger--variant-success" in html
    assert "rich-select-trigger--variant-info" not in html


def test_trigger_has_no_variant_when_nothing_selected():
    picker = RichSelect(
        name="s",
        options=[{"value": "a", "label": "A", "variant": "info"}],
        value="",
        event="pick",
    )
    html = picker.render()

    assert "rich-select-trigger--variant-" not in html


# --------------------------------------------------------------------------
# Variants — variant_map convenience
# --------------------------------------------------------------------------


def test_variant_map_applies_when_option_has_no_variant():
    picker = RichSelect(
        name="s",
        options=[
            {"value": "NEW", "label": "New"},
            {"value": "DONE", "label": "Done"},
        ],
        value="NEW",
        event="pick",
        variant_map={"NEW": "info", "DONE": "success"},
    )
    html = picker.render()

    assert "rich-select-option--variant-info" in html
    assert "rich-select-option--variant-success" in html
    # Trigger mirrors the selected value via the map
    assert "rich-select-trigger--variant-info" in html


def test_per_option_variant_wins_over_variant_map():
    picker = RichSelect(
        name="s",
        options=[{"value": "x", "label": "X", "variant": "danger"}],
        value="x",
        event="pick",
        variant_map={"x": "success"},
    )
    html = picker.render()

    assert "rich-select-option--variant-danger" in html
    assert "rich-select-option--variant-success" not in html
    assert "rich-select-trigger--variant-danger" in html


# --------------------------------------------------------------------------
# Interaction handlers
# --------------------------------------------------------------------------


def test_trigger_carries_open_close_onclick_and_keyboard_handlers():
    """Regression — the programmatic class used to omit these, forcing
    consumers to monkey-patch the rendered HTML. They should now match the
    ``{% rich_select %}`` template-tag variant by default."""
    picker = RichSelect(name="s", options=[{"value": "a", "label": "A"}], value="", event="pick")
    html = picker.render()

    trigger_block = html.split('class="rich-select-trigger')[1].split("</div>")[0]
    assert "classList.toggle('rich-select--open')" in trigger_block
    assert "event.key==='Enter'" in trigger_block
    assert "event.key===' '" in trigger_block


def test_option_click_closes_the_dropdown():
    """Picking an option should close the panel; the subsequent dj-click
    round-trip re-renders with the new selected state."""
    picker = RichSelect(name="s", options=[{"value": "a", "label": "A"}], value="", event="pick")
    html = picker.render()

    # The option row should carry an onclick that removes the open class from
    # the nearest .rich-select ancestor.
    assert "this.closest('.rich-select').classList.remove('rich-select--open')" in html


# --------------------------------------------------------------------------
# HTML escaping — variant must not allow class-attribute escape
# --------------------------------------------------------------------------


def test_variant_with_malicious_value_is_dropped():
    """An attacker-controlled variant string with quotes / spaces / tags
    must be rejected by the validator and fall back to ``default``,
    otherwise an attribute-injection escape is possible."""
    picker = RichSelect(
        name="s",
        options=[{"value": "x", "label": "X", "variant": 'success" onerror="alert(1)'}],
        value="x",
        event="pick",
    )
    html = picker.render()

    assert 'onerror="alert(1)' not in html
    assert "rich-select-option--variant-" not in html


def test_variant_name_length_is_bounded():
    """Extremely long variant strings must be rejected even if they'd
    otherwise match ``[a-z0-9-]+``, to keep rendered class attributes sane."""
    picker = RichSelect(
        name="s",
        options=[{"value": "x", "label": "X", "variant": "a" * 128}],
        value="x",
        event="pick",
    )
    html = picker.render()

    assert "rich-select-option--variant-" not in html


def test_variant_with_uppercase_is_rejected():
    """Variant names are normalised lowercase in CSS; accepting uppercase
    would produce class rules that don't match any stylesheet."""
    picker = RichSelect(
        name="s",
        options=[{"value": "x", "label": "X", "variant": "Info"}],
        value="x",
        event="pick",
    )
    html = picker.render()

    assert "rich-select-option--variant-" not in html


def test_option_label_and_value_are_html_escaped():
    picker = RichSelect(
        name="s",
        options=[{"value": "<v>", "label": "<l>"}],
        value="<v>",
        event="pick",
    )
    html = picker.render()

    assert "<v>" not in html.replace("&lt;v&gt;", "")
    assert "<l>" not in html.replace("&lt;l&gt;", "")


# --------------------------------------------------------------------------
# Parity assertion — the ALLOWED_VARIANTS enum matches our Badge/Alert set
# --------------------------------------------------------------------------


def test_builtin_variants_include_peer_component_set():
    """The built-in variants must at minimum cover what Badge/Alert/Tag/Button
    already support — otherwise consumers lose the "single theme vocabulary"
    the API promises. New names may be added but none may be removed
    without a matching cross-component change."""
    from djust.components.components.rich_select import _BUILTIN_VARIANTS

    assert {"default", "info", "success", "warning", "danger", "muted"}.issubset(_BUILTIN_VARIANTS)
    # primary/secondary are included for projects with > 5 categories
    assert "primary" in _BUILTIN_VARIANTS
    assert "secondary" in _BUILTIN_VARIANTS
