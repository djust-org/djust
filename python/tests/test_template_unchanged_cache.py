"""Dynamic template re-evaluation keeps the retained render fast paths."""

from djust._rust import RustLiveView


TEMPLATE = '<div dj-id="0"><p>{{ label }}</p><ul>{% for row in rows %}<li>{{ row }}</li>{% endfor %}</ul></div>'


def mounted():
    view = RustLiveView(TEMPLATE)
    view.update_state({"label": "before", "rows": list(range(100))})
    view.render_with_diff()
    return view


def test_same_template_preserves_text_region_fast_path():
    view = mounted()
    view.update_template(TEMPLATE)
    view.update_state({"label": "after"})
    view.set_changed_keys(["label"])
    html, patches, _ = view.render_with_diff()
    assert "after" in html
    assert "SetText" in patches
    assert view.get_render_timing()["fast_path"] == 2.0


def test_changed_template_renders_new_markup_without_stale_fragments():
    view = mounted()
    view.update_template(TEMPLATE.replace("<p>{{ label }}</p>", "<strong>{{ label }}</strong>"))
    view.update_state({"label": "after"})
    view.set_changed_keys(["label"])
    html, patches, _ = view.render_with_diff()
    assert "<strong" in html
    assert "after" in html
    assert "<p" not in html
    assert patches
    assert view.get_render_timing()["fast_path"] == 0.0


def test_same_source_rechecks_external_filter_state():
    from djust import _rust

    name = "dynamic_cache_external_filter"
    external = {"text": "first"}
    _rust.register_custom_filter(name, lambda value, arg=None: external["text"], False, False)
    try:
        template = (
            "<div><p>{{ label }}</p><span>{{ value|dynamic_cache_external_filter }}</span></div>"
        )
        view = RustLiveView(template)
        view.update_state({"label": "before", "value": "constant"})
        html, _, _ = view.render_with_diff()
        assert "first" in html
        external["text"] = "second"
        view.update_template(template)
        view.update_state({"label": "after"})
        view.set_changed_keys(["label"])
        html, patches, _ = view.render_with_diff()
        assert "second" in html
        assert "after" in html
        assert patches
    finally:
        _rust.unregister_custom_filter(name)


def test_same_source_rechecks_replaced_filter():
    from djust import _rust

    name = "dynamic_cache_replaced_filter"
    template = "<div><p>{{ label }}</p><span>{{ value|dynamic_cache_replaced_filter }}</span></div>"
    _rust.register_custom_filter(name, lambda value, arg=None: "first", False, False)
    try:
        view = RustLiveView(template)
        view.update_state({"label": "before", "value": "constant"})
        html, _, _ = view.render_with_diff()
        assert "first" in html
        _rust.unregister_custom_filter(name)
        _rust.register_custom_filter(name, lambda value, arg=None: "second", False, False)
        view.update_template(template)
        view.update_state({"label": "after"})
        view.set_changed_keys(["label"])
        html, patches, _ = view.render_with_diff()
        assert "second" in html
        assert "after" in html
        assert patches
    finally:
        _rust.unregister_custom_filter(name)


def test_same_source_rechecks_changed_render_environment():
    from djust import _rust

    template = "<div><p>{{ label }}</p><span>{{ value }}</span></div>"
    _rust.set_number_format(None)
    try:
        view = RustLiveView(template)
        view.capture_render_env()
        view.update_state({"label": "before", "value": 1234.5})
        html, _, _ = view.render_with_diff()
        assert "1234.5" in html
        _rust.set_number_format(",", ".", [3, 0], False)
        view.update_template(template)
        view.capture_render_env()
        view.update_state({"label": "after"})
        view.set_changed_keys(["label"])
        html, patches, _ = view.render_with_diff()
        assert "1234,5" in html
        assert "after" in html
        assert patches
    finally:
        _rust.set_number_format(None)


def test_liveview_property_reuses_text_region_on_unchanged_source():
    from djust import LiveView

    class DynamicView(LiveView):
        @property
        def template(self):
            return TEMPLATE

        def get_context_data(self, **kwargs):
            return {"label": self.label, "rows": self.rows}

    view = DynamicView()
    view.label = "before"
    view.rows = list(range(100))
    view.render_with_diff()
    view.label = "after"
    html, patches, _ = view.render_with_diff()
    assert "after" in html
    assert patches and "SetText" in patches
    assert view._rust_view.get_render_timing()["fast_path"] == 2.0
