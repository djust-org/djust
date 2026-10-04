"""``theme_context`` renders its HTML lazily; its settings values stay eager (#3028).

The processor used to render ``theme_head`` / ``theme_panel`` /
``theme_mode_toggle`` / ``theme_preset_selector`` / ``theme_switcher`` on every
request that built a ``RequestContext`` (~23 ms with a large pack), including
requests whose templates never print them. Each is now a ``LazyThemeHTML`` that
renders on first read, to the same bytes as before, for the request it was built
for.
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
from contextlib import ExitStack
from unittest.mock import patch

import pytest
from django.conf import settings
from django.template import engines
from django.test import RequestFactory, override_settings

from djust.theming import context_processors as cp
from djust.theming._lazy_html import LazyThemeHTML
from djust.theming.manager import ThemeManager

try:
    from djust import LiveView, RustLiveView
except ImportError:  # pragma: no cover
    LiveView = None
    RustLiveView = None

pytestmark = pytest.mark.theming

PROCESSOR = "djust.theming.context_processors.theme_context"
TAGS = ("theme_head", "theme_panel", "theme_mode_toggle", "theme_preset_selector")
CHUNK_KEYS = (
    "theme_head",
    "theme_panel",
    "theme_mode_toggle",
    "theme_preset_selector",
    "theme_switcher",
)
ALL_THEME_VARS = (
    "{{ theme_head }}|{{ theme_switcher }}|{{ theme_panel }}|{{ theme_mode_toggle }}"
    "|{{ theme_preset_selector }}"
)


class _Session(dict):
    session_key = "lazy-3028"


def _request(preset=None, mode="light", nonce=None):
    request = RequestFactory().get("/")
    key = ThemeManager(request=None)._session_key
    request.session = _Session({key: {"mode": mode}})
    if preset:
        request.COOKIES["djust_theme_preset"] = preset
    if nonce is not None:
        request.csp_nonce = nonce
    return request


def _two_presets():
    from djust.theming._registry_accessor import get_registry

    names = sorted(get_registry().list_presets())
    assert len(names) >= 2
    return names[0], names[-1]


@pytest.fixture(autouse=True)
def _fresh_cache():
    cp.clear_theme_context_cache()
    yield
    cp.clear_theme_context_cache()


@pytest.fixture
def spies():
    """Count every render the processor can trigger, without changing what runs."""
    from djust.theming.templatetags import theme_tags

    counts = {}
    with ExitStack() as stack:
        for name in TAGS:
            counts[name] = stack.enter_context(
                patch.object(theme_tags, name, wraps=getattr(theme_tags, name))
            )
        counts["switcher"] = stack.enter_context(
            patch.object(cp, "_render_theme_outputs", wraps=cp._render_theme_outputs)
        )
        yield counts


def _total_renders(spies):
    return sum(spy.call_count for spy in spies.values())


def _template(body):
    return engines["django"].from_string("{% load theme_tags %}" + body)


class TestLazyNotEvaluatedWhenUnused:
    def test_a_page_that_reads_no_theme_html_renders_none_of_it(self, spies):
        _template("<p>hello</p>").render({}, _request())
        assert _total_renders(spies) == 0, {k: v.call_count for k, v in spies.items()}

    def test_reading_the_settings_values_renders_none_of_the_html(self, spies):
        html = _template(
            "{{ theme_preset }}|{{ theme_mode }}|{{ theme_resolved_mode }}|{{ theme_pack }}"
            "|{% for p in theme_presets %}{{ p.name }},{% endfor %}"
        ).render({}, _request(mode="dark"))
        assert html.startswith("default|dark|dark|")
        assert _total_renders(spies) == 0

    def test_the_processor_returns_lazy_chunks_and_plain_settings(self, spies):
        ctx = cp.theme_context(_request())
        for key in CHUNK_KEYS:
            assert isinstance(ctx[key], LazyThemeHTML), key
            assert not ctx[key].evaluated, key
        # Settings are plain values a template, a check or a filter can compare.
        assert type(ctx["theme_preset"]) is str
        assert type(ctx["theme_mode"]) is str
        assert type(ctx["theme_resolved_mode"]) is str
        assert type(ctx["theme_presets"]) is list
        assert _total_renders(spies) == 0

    def test_each_chunk_renders_only_the_one_that_is_read(self, spies):
        _template("{{ theme_panel }}").render({}, _request())
        assert spies["theme_panel"].call_count == 1
        assert _total_renders(spies) == 1

    def test_a_chunk_read_twice_on_a_page_renders_once(self, spies):
        _template("{{ theme_head }}{{ theme_head }}{{ theme_head|length }}").render({}, _request())
        assert spies["theme_head"].call_count == 1


class TestLazyHTMLObject:
    def test_the_factory_runs_once(self):
        calls = []
        chunk = LazyThemeHTML(lambda: calls.append(1) or "<i>x</i>")
        assert not chunk.evaluated and calls == []
        assert str(chunk) == "<i>x</i>" and chunk.__html__() == "<i>x</i>" and len(chunk) == 8
        assert calls == [1] and chunk.evaluated

    def test_a_failure_renders_empty_and_is_not_retried(self):
        calls = []

        def boom():
            calls.append(1)
            raise RuntimeError("manifest")

        chunk = LazyThemeHTML(boom)
        assert str(chunk) == "" and not chunk and str(chunk) == ""
        assert calls == [1]

    def test_private_names_never_render_it(self):
        chunk = LazyThemeHTML(lambda: pytest.fail("rendered by a private-name probe"))
        with pytest.raises(AttributeError):
            chunk._nope
        import copy as copy_module

        copy_module.copy(chunk)
        assert not chunk.evaluated


class TestRendersTheSameText:
    def test_theme_head_variable_equals_the_tag(self):
        request = _request()
        as_variable = _template("{{ theme_head }}").render({}, request)
        as_tag = _template("{% theme_head %}").render({}, request)
        assert as_variable == as_tag
        assert "<style" in as_variable

    @pytest.mark.parametrize(
        "key,tag",
        [
            ("theme_panel", "theme_panel"),
            ("theme_mode_toggle", "theme_mode_toggle"),
            ("theme_preset_selector", "theme_preset_selector"),
        ],
    )
    def test_each_tag_variable_equals_its_tag(self, key, tag):
        request = _request()
        assert _template("{{ %s }}" % key).render({}, request) == _template(
            "{%% %s %%}" % tag
        ).render({}, request)

    def test_the_value_is_not_escaped(self):
        html = _template("{{ theme_head }}").render({}, _request())
        assert "<style" in html and "&lt;" not in html

    def test_autoescape_off_and_safe_filter_give_the_same_text(self):
        request = _request()
        plain = _template("{{ theme_head }}").render({}, request)
        assert (
            _template("{% autoescape off %}{{ theme_head }}{% endautoescape %}").render({}, request)
            == plain
        )
        assert _template("{{ theme_head|safe }}").render({}, request) == plain

    def test_filters_a_template_might_apply_see_the_rendered_text(self):
        request = _request()
        text = str(cp.theme_context(request)["theme_head"])
        assert _template("{{ theme_head|length }}").render({}, request) == str(len(text))
        assert _template("{% if theme_head %}Y{% else %}N{% endif %}").render({}, request) == "Y"
        assert _template('{{ theme_head|default:"d" }}').render({}, request) == text
        assert _template("{{ theme_head|slice:':20' }}").render({}, request) == text[:20]

    def test_str_equality_and_hash_follow_the_text(self):
        chunk = LazyThemeHTML(lambda: "<b>x</b>")
        assert chunk == "<b>x</b>" and "<b>x</b>" == chunk
        assert chunk == LazyThemeHTML(lambda: "<b>x</b>")
        assert hash(chunk) == hash("<b>x</b>")
        assert "<b>" in chunk and chunk[0] == "<" and chunk + "!" == "<b>x</b>!"
        assert chunk.strip() == "<b>x</b>" and f"{chunk}" == "<b>x</b>"

    def test_it_is_safe_data_so_django_leaves_it_unescaped(self):
        from django.utils.html import conditional_escape
        from django.utils.safestring import SafeData

        chunk = LazyThemeHTML(lambda: "<i>")
        assert isinstance(chunk, SafeData)
        assert conditional_escape(chunk) == "<i>"

    def test_it_does_not_look_like_a_str_to_isinstance(self):
        """So djust's ``isinstance(value, str)`` probes leave it unrendered."""
        chunk = LazyThemeHTML(lambda: pytest.fail("rendered by an isinstance probe"))
        assert not isinstance(chunk, str)
        assert callable(chunk) is False
        repr(chunk)
        assert not chunk.evaluated

    def test_a_plain_str_from_a_non_head_tag_is_still_escaped(self):
        """The eager processor passed the tag's return through, so a plain str
        (a shadowed tag) was escaped by autoescape. Laziness must not trust it."""
        from djust.theming.templatetags import theme_tags

        with patch.object(theme_tags, "theme_panel", return_value="<script>x</script>"):
            html = _template("{{ theme_panel }}").render({}, _request())
        assert html == "&lt;script&gt;x&lt;/script&gt;"

    def test_one_broken_tag_blanks_only_itself(self):
        from djust.theming.templatetags import theme_tags

        request = _request()
        with patch.object(theme_tags, "theme_panel", side_effect=RuntimeError("manifest")):
            html = _template("[{{ theme_panel }}][{{ theme_mode_toggle }}]").render({}, request)
        assert html.startswith("[][") and html != "[][]"


class TestRequestIsolation:
    def test_interleaved_requests_each_render_their_own_nonce_preset_and_mode(self):
        first_preset, last_preset = _two_presets()
        req_a = _request(preset=first_preset, mode="light", nonce="nonce-A")
        req_b = _request(preset=last_preset, mode="dark", nonce="nonce-B")
        # Both processors run before either chunk is read, then they are read
        # in the opposite order from construction.
        ctx_a = cp.theme_context(req_a)
        ctx_b = cp.theme_context(req_b)
        head_b = str(ctx_b["theme_head"])
        head_a = str(ctx_a["theme_head"])
        assert 'nonce="nonce-A"' in head_a and "nonce-B" not in head_a
        assert 'nonce="nonce-B"' in head_b and "nonce-A" not in head_b
        # And each is what the tag renders for that request alone.
        for request, head in ((req_a, head_a), (req_b, head_b)):
            assert head == _template("{% theme_head %}").render({}, request)
        assert head_a != head_b
        assert ctx_a["theme_preset"] == first_preset and ctx_b["theme_preset"] == last_preset
        assert str(ctx_a["theme_switcher"]) != str(ctx_b["theme_switcher"])

    def test_a_chunk_read_late_still_uses_its_own_request(self):
        req_a = _request(nonce="late-A")
        ctx_a = cp.theme_context(req_a)
        # A different request goes through the processor and renders first.
        ctx_b = cp.theme_context(_request(nonce="late-B"))
        assert 'nonce="late-B"' in str(ctx_b["theme_head"])
        assert 'nonce="late-A"' in str(ctx_a["theme_head"])

    def test_the_per_process_cache_never_sees_the_nonce(self):
        """The only per-process cache is the switcher render, keyed on theme
        state alone. A nonce can only live on the request."""
        request = _request(nonce="secret-nonce")
        ctx = cp.theme_context(request)
        assert 'nonce="secret-nonce"' in str(ctx["theme_head"])
        str(ctx["theme_switcher"])
        state = ThemeManager(request=request).get_state()
        presets = ThemeManager(request=request).get_available_presets()
        head, switcher = cp._render_theme_outputs(
            state.theme,
            state.preset,
            state.pack,
            state.mode,
            state.resolved_mode,
            state.layout,
            cp._presets_to_cache_key(presets),
        )
        assert "secret-nonce" not in head + switcher
        assert cp._render_theme_outputs.cache_info().currsize == 1

    def test_two_requests_share_no_chunk_objects(self):
        a = cp.theme_context(_request())
        b = cp.theme_context(_request())
        for key in CHUNK_KEYS:
            assert a[key] is not b[key]

    def test_the_same_request_reuses_its_rendered_chunk(self, spies):
        request = _request()
        first = cp.theme_context(request)
        str(first["theme_head"])
        second = cp.theme_context(request)
        assert second["theme_head"] is first["theme_head"]
        str(second["theme_head"])
        assert spies["theme_head"].call_count == 1

    def test_a_theme_switch_on_the_same_request_builds_fresh_chunks(self):
        first_preset, last_preset = _two_presets()
        request = _request(preset=first_preset)
        before = cp.theme_context(request)
        old_switcher = str(before["theme_switcher"])
        request.COOKIES["djust_theme_preset"] = last_preset
        request._djust_theme_manager = ThemeManager(request=request)
        after = cp.theme_context(request)
        assert after["theme_head"] is not before["theme_head"]
        assert after["theme_preset"] == last_preset
        assert str(after["theme_switcher"]) != old_switcher
        assert f'value="{last_preset}" selected' in str(after["theme_switcher"])


class TestSerialisation:
    def test_it_is_not_json_serialisable_and_probing_does_not_render(self):
        from djust.mixins.context import _is_json_serializable

        chunk = LazyThemeHTML(lambda: pytest.fail("rendered by a serialisability probe"))
        assert _is_json_serializable(chunk) is False
        with pytest.raises(TypeError):
            json.dumps(chunk)
        assert not chunk.evaluated

    def test_django_json_encoder_serialises_the_rendered_text(self):
        from django.core.serializers.json import DjangoJSONEncoder

        chunk = LazyThemeHTML(lambda: "<p>é</p>")
        assert json.loads(json.dumps({"k": chunk}, cls=DjangoJSONEncoder)) == {"k": "<p>é</p>"}

    def test_json_script_filter_works_on_a_chunk(self):
        request = _request()
        html = _template('{{ theme_mode_toggle|json_script:"t" }}').render({}, request)
        eager = _template('{{ eager|json_script:"t" }}').render(
            {"eager": str(cp.theme_context(_request())["theme_mode_toggle"])}, request
        )
        assert html == eager and html.startswith('<script id="t" type="application/json">')

    def test_normalize_django_value_answers_the_text_without_a_warning(self, caplog):
        from djust.serialization import normalize_django_value

        chunk = LazyThemeHTML(lambda: "<b>x</b>")
        with caplog.at_level("WARNING"):
            assert normalize_django_value({"k": chunk}) == {"k": "<b>x</b>"}
        assert not [r for r in caplog.records if "non-serializable" in r.getMessage()]

    def test_the_rendered_text_is_what_str_gives(self):
        chunk = LazyThemeHTML(lambda: "<p>é</p>")
        assert str(chunk) == "<p>é</p>"
        assert json.dumps(str(chunk)) == '"<p>\\u00e9</p>"'


# --- the Rust renderer (LiveView) path -------------------------------------


needs_rust = pytest.mark.skipif(
    LiveView is None or RustLiveView is None, reason="djust.LiveView / RustLiveView not available"
)


def _rust_templates(template_dir):
    templates = copy.deepcopy(settings.TEMPLATES)
    templates[0]["DIRS"] = list(templates[0].get("DIRS", [])) + [template_dir]
    assert PROCESSOR in templates[0]["OPTIONS"]["context_processors"]
    return templates


@pytest.fixture
def page_dir():
    with tempfile.TemporaryDirectory() as tmp:
        pages = {
            "_3028_unused.html": "<div dj-root><p>Count: {{ count }}</p></div>",
            "_3028_used.html": "<div dj-root>[{{ theme_head }}]|[{{ theme_switcher }}]"
            "|[{{ theme_panel }}]|[{{ theme_preset }}]</div>",
            "_3028_settings.html": "<div dj-root>{{ theme_preset }}/{{ theme_mode }}/{{ count }}</div>",
            "_3028_bridged.html": "{% load theme_tags %}<div dj-root>{% theme_mode_toggle %}|{{ count }}</div>",
        }
        for name, body in pages.items():
            with open(os.path.join(tmp, name), "w") as handle:
                handle.write(body)
        yield tmp


def _view_class(template_name, explicit=False):
    attrs = {"template_name": template_name}
    if explicit:
        from djust.decorators import state

        def get_context_data(self, **kwargs):
            return super(type(self), self).get_context_data(count=self.count, **kwargs)

        attrs["exposure_policy"] = "explicit"
        attrs["count"] = state(0, persist="server")
        attrs["get_context_data"] = get_context_data
    else:

        def mount(self, request, **kwargs):
            self.count = 0

        attrs["mount"] = mount
    return type("V3028", (LiveView,), attrs)


def _mounted(view_cls, request):
    view = view_cls()
    view.setup(request)
    view._initialize_temporary_assigns()
    view.mount(request)
    return view


def _clear_template_caches():
    from djust.mixins.context import _context_processors_cache, _resolved_processors_cache
    from djust.utils import clear_template_dirs_cache

    clear_template_dirs_cache()
    _resolved_processors_cache.clear()
    _context_processors_cache.clear()


@needs_rust
class TestRustPath:
    def test_an_unused_chunk_is_not_rendered_by_a_liveview_render(self, page_dir, spies):
        _clear_template_caches()
        with override_settings(TEMPLATES=_rust_templates(page_dir)):
            request = _request()
            view = _mounted(_view_class("_3028_unused.html"), request)
            html = view.render(request=request)
        assert "Count: 0" in html
        assert _total_renders(spies) == 0, {k: v.call_count for k, v in spies.items()}

    def test_a_settings_only_template_renders_no_html_chunk(self, page_dir, spies):
        _clear_template_caches()
        with override_settings(TEMPLATES=_rust_templates(page_dir)):
            request = _request(mode="dark")
            view = _mounted(_view_class("_3028_settings.html"), request)
            html = view.render(request=request)
        assert "default/dark/0" in html
        assert _total_renders(spies) == 0

    def test_a_bridged_theme_tag_does_not_render_the_other_chunks(self, page_dir, spies):
        """The Rust renderer hands bridged tags the whole context; that must not
        read (and so render) the chunks the template never prints."""
        _clear_template_caches()
        with override_settings(TEMPLATES=_rust_templates(page_dir)):
            request = _request()
            view = _mounted(_view_class("_3028_bridged.html"), request)
            html = view.render(request=request)
        assert "|0" in html
        for name in ("theme_head", "theme_panel", "theme_preset_selector"):
            assert spies[name].call_count == 0, name
        assert spies["switcher"].call_count == 0

    def test_a_used_chunk_renders_once_and_matches_the_tag_text(self, page_dir, spies):
        _clear_template_caches()
        with override_settings(TEMPLATES=_rust_templates(page_dir)):
            request = _request(nonce="rust-nonce")
            view = _mounted(_view_class("_3028_used.html"), request)
            html = view.render(request=request)
        assert 'nonce="rust-nonce"' in html and "<style" in html
        assert "theme-switcher" in html
        assert spies["theme_head"].call_count == 1
        assert spies["theme_panel"].call_count == 1
        assert spies["theme_mode_toggle"].call_count == 0
        assert spies["theme_preset_selector"].call_count == 0

    def test_explicit_views_leave_an_unused_chunk_alone_too(self, page_dir, spies):
        _clear_template_caches()
        with override_settings(TEMPLATES=_rust_templates(page_dir)):
            request = _request()
            view = _mounted(_view_class("_3028_unused.html", explicit=True), request)
            html = view.render(request=request)
        assert "Count: 0" in html
        assert _total_renders(spies) == 0

    def test_explicit_views_render_a_used_chunk(self, page_dir, spies):
        _clear_template_caches()
        with override_settings(TEMPLATES=_rust_templates(page_dir)):
            request = _request(nonce="explicit-nonce")
            view = _mounted(_view_class("_3028_used.html", explicit=True), request)
            html = view.render(request=request)
        assert 'nonce="explicit-nonce"' in html and "theme-switcher" in html
        assert spies["theme_head"].call_count == 1

    def test_a_live_preset_switch_updates_the_rendered_head(self, page_dir):
        _clear_template_caches()
        first_preset, last_preset = _two_presets()
        with override_settings(TEMPLATES=_rust_templates(page_dir)):
            request = _request(preset=first_preset)
            view = _mounted(_view_class("_3028_used.html"), request)
            first_html = view.render(request=request)
            assert f'value="{first_preset}" selected' in first_html
            # The user switches theme between events on the same long-lived request.
            request.COOKIES["djust_theme_preset"] = last_preset
            request._djust_theme_manager = ThemeManager(request=request)
            view._sync_state_to_rust()
            second_html = view.render(request=request)
        assert f'value="{last_preset}" selected' in second_html
        assert f'value="{first_preset}" selected' not in second_html
        assert second_html != first_html

    def test_rust_output_matches_an_eager_processor(self, page_dir):
        """The same page, once through the lazy processor and once through a
        processor that hands Rust the already-rendered SafeStrings (what
        ``theme_context`` returned before #3028)."""
        _clear_template_caches()
        request_args = dict(preset=_two_presets()[0], nonce="eq-nonce")

        with override_settings(TEMPLATES=_rust_templates(page_dir)):
            request = _request(**request_args)
            lazy_html = _mounted(_view_class("_3028_used.html"), request).render(request=request)

        eager_path = f"{__name__}._eager_processor"
        templates = _rust_templates(page_dir)
        processors = templates[0]["OPTIONS"]["context_processors"]
        processors[processors.index(PROCESSOR)] = eager_path
        _clear_template_caches()
        with override_settings(TEMPLATES=templates):
            request = _request(**request_args)
            eager_html = _mounted(_view_class("_3028_used.html"), request).render(request=request)

        assert lazy_html == eager_html
        assert "eq-nonce" in lazy_html


def _eager_processor(request):
    """``theme_context`` as it behaved before #3028: every chunk rendered now."""
    from django.utils.safestring import mark_safe

    ctx = cp.theme_context(request)
    for key in CHUNK_KEYS:
        ctx[key] = mark_safe(str(ctx[key]))
    return ctx


# --- the checks -------------------------------------------------------------


class TestE001IsStillAWarning:
    def test_the_missing_processor_check_is_a_warning_not_an_error(self):
        from django.core.checks import Warning as CheckWarning

        from djust.theming.checks import check_context_processor

        templates = copy.deepcopy(settings.TEMPLATES)
        for config in templates:
            options = config.setdefault("OPTIONS", {})
            options["context_processors"] = [
                p for p in options.get("context_processors", []) if p != PROCESSOR
            ]
        with override_settings(TEMPLATES=templates):
            messages = check_context_processor(None)
        assert [m.id for m in messages] == ["djust_theming.E001"]
        assert isinstance(messages[0], CheckWarning)
        # No claim that the processor is costly any more.
        assert "pre-render" not in messages[0].msg
        assert "every request" not in messages[0].msg
