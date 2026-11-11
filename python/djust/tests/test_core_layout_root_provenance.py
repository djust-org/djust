"""Core layout wrappers must preserve source root proof without trusting tag names."""

import pytest
from django.core.exceptions import ImproperlyConfigured
from djust._rust import RustLiveView
from djust.components.rust_handlers import register_with_rust_engine


@pytest.fixture(autouse=True)
def layout_handlers():
    register_with_rust_engine()
    yield
    register_with_rust_engine()


@pytest.mark.parametrize("sidebar_first", [False, True])
def test_nested_core_layout_preserves_included_root_and_model_binding(tmp_path, sidebar_first):
    (tmp_path / "root.html").write_text('<section dj-root>é😀<input dj-model="query"></section>')
    sidebar = "{% app_sidebar %}NAV{% endapp_sidebar %}"
    content = '{% app_header %}HEADER{% endapp_header %}{% app_content %}{% include "root.html" %}{% endapp_content %}'
    view = RustLiveView(
        "{% app_shell %}"
        + (sidebar + content if sidebar_first else content + sidebar)
        + "{% endapp_shell %}",
        [str(tmp_path)],
    )
    view.set_authenticated_partial_root(True)
    html, _, _, _ = view.prepare_authenticated_page()
    assert "dj-app-shell__main" in html and "é😀" in html
    assert view.prepared_page_has_root()
    view.validate_prepared_page(html, view.prepared_page_root_spans())
    view.commit_authenticated_page()
    assert view.dj_model_fields() == ["query"]


def test_replaced_handler_does_not_gain_core_layout_authority():
    from djust._rust import register_block_tag_handler

    class Opaque:
        def _render_root_parts(self, args, content, context):
            return [(None, "<div>"), ((0, len(content.encode())), ""), (None, "</div>")]

        def render(self, args, content, context):
            return "<div>" + content + "</div>"

    register_block_tag_handler("app_content", "endapp_content", Opaque())
    view = RustLiveView("{% app_content %}<main dj-root>body</main>{% endapp_content %}", [])
    view.set_authenticated_partial_root(True)
    with pytest.raises(ImproperlyConfigured, match="authored"):
        view.prepare_authenticated_page()


def test_core_layout_does_not_authorize_an_opaque_root():
    view = RustLiveView("{% app_content %}{{ payload|safe }}{% endapp_content %}", [])
    view.set_authenticated_partial_root(True)
    view.update_state({"payload": "<main dj-root><input dj-model=admin></main>"})
    with pytest.raises(ImproperlyConfigured, match="authored"):
        view.prepare_authenticated_page()


def test_core_layout_cannot_activate_a_source_inert_root():
    view = RustLiveView(
        "{% app_content %}<textarea>{{ closer|safe }}<section dj-root>hidden</section></textarea>{% endapp_content %}",
        [],
    )
    view.set_authenticated_partial_root(True)
    view.update_state({"closer": "</textarea>"})
    with pytest.raises(ImproperlyConfigured, match="authored"):
        view.prepare_authenticated_page()


def test_core_layout_model_proof_stays_separate_from_opaque_and_lazy_output():
    view = RustLiveView(
        '{% app_content %}<section dj-root><input dj-model="query">{{ payload|safe }}<div dj-view="app.Child" dj-lazy></div></section>{% endapp_content %}',
        [],
    )
    view.set_authenticated_partial_root(True)
    view.update_state({"payload": "<input dj-model=admin>"})
    html, lazy_spans, _, _ = view.prepare_authenticated_page()
    assert lazy_spans == []  # Root-copy support never grants lazy-registration authority.
    view.validate_prepared_page(html, view.prepared_page_root_spans())
    view.commit_authenticated_page()
    assert view.dj_model_fields() == ["query"]


def test_core_layout_cannot_change_the_source_root_namespace():
    view = RustLiveView(
        "<svg>{% app_shell %}<g dj-root><text>body</text></g>{% endapp_shell %}</svg>", []
    )
    view.set_authenticated_partial_root(True)
    with pytest.raises(ImproperlyConfigured, match="authored"):
        view.prepare_authenticated_page()


def test_core_layout_keeps_lazy_cutoff_for_following_source():
    view = RustLiveView(
        "{% app_header %}HEADER{% endapp_header %}<main dj-root>"
        '<div dj-view="app.Child" dj-lazy></div><input dj-model="query"></main>',
        [],
    )
    view.set_authenticated_partial_root(True)
    html, lazy_spans, _, _ = view.prepare_authenticated_page()
    assert lazy_spans == []
    assert view.prepared_page_root_spans()
    view.validate_prepared_page(html, view.prepared_page_root_spans())
    view.commit_authenticated_page()
    assert view.dj_model_fields() == ["query"]


@pytest.mark.parametrize("broken", ["missing", "duplicate", "utf8"])
def test_invalid_core_body_copy_plan_is_rejected(monkeypatch, broken):
    from djust.components.rust_handlers import AppContentHandler

    def invalid(self, args, content, context):
        size = len(content.encode())
        if broken == "missing":
            return [(None, "<main dj-root>forged</main>")]
        if broken == "duplicate":
            return [((0, size), ""), ((0, size), "")]
        return [((1, size), "")]

    monkeypatch.setattr(AppContentHandler, "_render_root_parts", invalid)
    view = RustLiveView("{% app_content %}é<main dj-root>body</main>{% endapp_content %}", [])
    view.set_authenticated_partial_root(True)
    with pytest.raises(Exception, match="[Cc]ore layout"):
        view.prepare_authenticated_page()


def test_core_layout_protocol_preserves_python_exception_type(monkeypatch):
    from django.urls import NoReverseMatch
    from djust.components.rust_handlers import AppContentHandler

    def failure(self, args, content, context):
        raise NoReverseMatch("layout reverse failure")

    monkeypatch.setattr(AppContentHandler, "_render_root_parts", failure)
    view = RustLiveView("{% app_content %}<main dj-root>body</main>{% endapp_content %}", [])
    view.set_authenticated_partial_root(True)
    with pytest.raises(NoReverseMatch, match="layout reverse failure"):
        view.prepare_authenticated_page()


def test_core_layout_bridge_keeps_lazy_super_snapshot_alive(tmp_path, monkeypatch):
    from djust.components.rust_handlers import AppContentHandler

    (tmp_path / "base.html").write_text(
        "<section dj-root>{% block body %}PARENT{% endblock %}</section>"
    )
    (tmp_path / "child.html").write_text(
        '{% extends "base.html" %}{% block body %}'
        "{% if False %}{{ block.super }}{% endif %}"
        "{% app_content %}CHILD{% endapp_content %}{% endblock %}"
    )
    original = AppContentHandler._render_root_parts
    seen = []

    def inspect_context(self, args, content, context):
        seen.append(context["block"].super())
        return original(self, args, content, context)

    monkeypatch.setattr(AppContentHandler, "_render_root_parts", inspect_context)
    view = RustLiveView('{% include "child.html" %}', [str(tmp_path)])
    view.set_authenticated_partial_root(True)
    html, _, _, _ = view.prepare_authenticated_page()
    assert seen == ["PARENT"]
    assert "CHILD" in html


# ---- the framework's argument wrappers (main #3676 / #3871) around the layout handlers ----


def _registered_chain(name):
    """The handler classes ``register_with_rust_engine`` builds for ``name``, outermost first."""
    from djust._positional_args import with_argument_contract
    from djust.components import rust_handlers
    from djust.components.templatetags.djust_components import register as library

    inner = dict((tag, handler) for tag, _end, handler in rust_handlers.BLOCK_HANDLERS)[name]
    wrapped = with_argument_contract(name, rust_handlers._with_typed_block_operands(inner), library)
    chain = [type(wrapped).__name__]
    while "_handler" in vars(wrapped):
        wrapped = vars(wrapped)["_handler"]
        chain.append(type(wrapped).__name__)
    return chain


@pytest.mark.parametrize(
    "name,cls",
    [
        ("app_shell", "AppShellHandler"),
        ("app_sidebar", "AppSidebarHandler"),
        ("app_header", "AppHeaderHandler"),
        ("app_content", "AppContentHandler"),
    ],
)
def test_the_layout_handlers_are_registered_inside_exactly_the_two_wrappers_the_engine_peels(
    name, cls
):
    """The engine recognises a core layout by peeling these two classes and no others. If the
    registration ever wraps differently, the layouts silently lose root proof, so pin the shape."""
    assert _registered_chain(name) == ["DjangoCheckedTagHandler", "_TypedBlockOperands", cls]


def test_a_wrapped_layout_handler_keeps_core_layout_authority():
    view = RustLiveView("{% app_content %}<main dj-root>body</main>{% endapp_content %}", [])
    view.set_authenticated_partial_root(True)
    html, _, _, _ = view.prepare_authenticated_page()
    assert view.prepared_page_has_root()
    assert "<main dj-root>body</main>" in html


def _wrap_like_the_framework(handler):
    from djust._positional_args import DjangoCheckedTagHandler
    from djust.components.rust_handlers import _TypedBlockOperands
    from djust.components.templatetags.djust_components import register as library

    return DjangoCheckedTagHandler(
        _TypedBlockOperands(handler), "app_content", library.tags["app_content"]
    )


class _OpaqueLayout:
    def _render_root_parts(self, args, content, context):
        return [(None, "<div>"), ((0, len(content.encode())), ""), (None, "</div>")]

    def render(self, args, content, context):
        return "<div>" + content + "</div>"


def _subclass_of(base):
    return type("Lookalike", (base,), {})


def _wrapper_variants():
    from djust._positional_args import DjangoCheckedTagHandler
    from djust.components.rust_handlers import AppContentHandler, _TypedBlockOperands
    from djust.components.templatetags.djust_components import register as library

    compile_func = library.tags["app_content"]
    return {
        # the real wrappers around something that is not a layout class
        "framework-wrappers-around-opaque": lambda: _wrap_like_the_framework(_OpaqueLayout()),
        # the real wrappers around a SUBCLASS of a layout class
        "framework-wrappers-around-subclass": lambda: _wrap_like_the_framework(
            _subclass_of(AppContentHandler)()
        ),
        # a subclass of the outer wrapper around the real layout
        "subclassed-outer-wrapper": lambda: _subclass_of(DjangoCheckedTagHandler)(
            _TypedBlockOperands(AppContentHandler()), "app_content", compile_func
        ),
        # a subclass of the inner wrapper around the real layout
        "subclassed-inner-wrapper": lambda: DjangoCheckedTagHandler(
            _subclass_of(_TypedBlockOperands)(AppContentHandler()), "app_content", compile_func
        ),
        # a look-alike wrapper that only carries a `_handler` attribute
        "lookalike-wrapper": lambda: type(
            "DjangoCheckedTagHandler",
            (),
            {
                "__init__": lambda self: setattr(self, "_handler", AppContentHandler()),
                "render": lambda self, a, c, x: self._handler.render(a, c, x),
                "_render_root_parts": lambda self, a, c, x: self._handler._render_root_parts(
                    a, c, x
                ),
            },
        )(),
    }


@pytest.mark.parametrize("variant", sorted(_wrapper_variants()))
def test_only_the_exact_wrapper_chain_around_an_exact_layout_class_gains_authority(variant):
    from djust._rust import register_block_tag_handler

    register_block_tag_handler("app_content", "endapp_content", _wrapper_variants()[variant]())
    view = RustLiveView("{% app_content %}<main dj-root>body</main>{% endapp_content %}", [])
    view.set_authenticated_partial_root(True)
    with pytest.raises(ImproperlyConfigured, match="authored"):
        view.prepare_authenticated_page()


def test_the_core_layout_call_still_blanks_an_operand_that_did_not_resolve():
    """#3871 through the core protocol: `class=nope` names a variable that is not in the context,
    so the class is empty; the variable's NAME must not become the class. `app_shell` is the
    layout tag that reads `class`."""
    source = "{% app_shell class=NAME %}<main dj-root>body</main>{% endapp_shell %}"

    view = RustLiveView(source.replace("NAME", "nope"), [])
    view.set_authenticated_partial_root(True)
    html, _, _, _ = view.prepare_authenticated_page()
    assert view.prepared_page_has_root()
    assert "nope" not in html

    resolved = RustLiveView(source.replace("NAME", "extra"), [])
    resolved.set_authenticated_partial_root(True)
    resolved.update_state({"extra": "wide"})
    html, _, _, _ = resolved.prepare_authenticated_page()
    assert resolved.prepared_page_has_root()
    assert 'class="dj-app-shell wide"' in html
    # the tracked render and the ordinary one agree on the wrapper's markup
    plain = RustLiveView(source.replace("NAME", "extra"), [])
    plain.update_state({"extra": "wide"})
    assert 'class="dj-app-shell wide"' in plain.render()
