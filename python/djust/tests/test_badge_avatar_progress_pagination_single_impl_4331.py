"""``badge``, ``avatar``, ``progress`` and ``pagination`` are one implementation (#4331).

Each was drawn twice: by a native handler (the Rust engine) and by an inclusion template (the
Django engine), with different markup. The ``dj-*`` BEM markup of the templates won (it is the
reference under #3669); the handler draws it and the Django tags are simple tags that call it
(``render_badge`` ...), as ``data_table`` has since #3869. The four templates are gone.
"""

from __future__ import annotations

import pytest
from django.template import Engine, TemplateDoesNotExist
from django.utils.safestring import mark_safe

from djust.tests.test_component_dj_attr_parity_3669 import _django, _rust

HOSTILE = "<script>alert(\"x\")</script> 'q' & co"

SOURCES = {
    "badge": [
        '{% badge label="New" status="online" pulse=True %}',
        '{% badge label="Hi" %}',
        '{% badge label="Hi" status="default" %}',
        "{% badge label=label status=status pulse=pulse %}",
        '{% badge "Pos" "warning" True %}',
        "{% badge %}",
    ],
    "avatar": [
        '{% avatar src="/a.png" alt="Ann Lee" size="lg" status="online" %}',
        '{% avatar alt="Ann Lee" %}',
        '{% avatar initials="JD" size="xs" %}',
        "{% avatar src=src alt=label initials=label size=size status=status %}",
        '{% avatar "/p.png" "Al" "AL" "sm" "busy" %}',
        "{% avatar %}",
    ],
    "progress": [
        '{% progress value=40 label="Upload" size="lg" color="success" %}',
        "{% progress value=40 show_label=False %}",
        "{% progress value=250 %}",
        "{% progress value=-5 label=label color=color %}",
        '{% progress 30 "Pos" "sm" "danger" False %}',
        "{% progress %}",
    ],
    "pagination": [
        "{% pagination page=1 total_pages=1 %}",
        "{% pagination page=3 total_pages=10 %}",
        "{% pagination page=10 total_pages=10 %}",
        '{% pagination page=2 total_pages=5 prev_event=label next_event="go" %}',
        "{% pagination 4 9 %}",
        "{% pagination page=pg total_pages=tp %}",
        "{% pagination %}",
    ],
}

CONTEXTS = {
    "typical": {"label": "Ann", "status": "online", "pulse": True, "size": "sm", "color": "danger"},
    "hostile": {
        "label": HOSTILE,
        "status": HOSTILE,
        "src": "javascript:alert(1)",
        "size": HOSTILE,
        "color": HOSTILE,
    },
    "safe": {"label": mark_safe("<b>bold</b>"), "status": mark_safe("<i>x</i>")},
    "literal": {"label": "&lt;b&gt;", "src": "/img/a b.png"},
    "missing": {},
    "none": {"label": None, "status": None, "src": None, "size": None, "color": None},
    "pager": {"pg": "2", "tp": "7", "label": "e"},
}

CASES = [(tag, src) for tag, sources in SOURCES.items() for src in sources]


@pytest.mark.parametrize("ctx", sorted(CONTEXTS))
@pytest.mark.parametrize("tag,src", CASES, ids=[f"{t}-{i}" for i, (t, _) in enumerate(CASES)])
def test_both_engines_draw_the_same_bytes(tag: str, src: str, ctx: str) -> None:
    context = CONTEXTS[ctx]
    try:
        expected = _rust(src, context)
    except Exception as exc:  # a refusal is the same refusal on both engines
        with pytest.raises(type(exc)):
            _django(src, context)
        return
    assert _django(src, context) == expected


def test_marked_safe_label_is_escaped_by_the_one_renderer() -> None:
    """D3: a marked-safe operand is text in a handler, on both engines."""
    for engine in (_django, _rust):
        out = engine("{% badge label=label %}", {"label": mark_safe("<b>x</b>")})
        assert "<b>x</b>" not in out
        assert "&lt;b&gt;x&lt;/b&gt;" in out


# --- the bugs the native markup had ------------------------------------------------------------


@pytest.mark.parametrize("engine", [_django, _rust])
class TestRegressions:
    def test_pagination_has_a_nav_landmark_and_labelled_buttons(self, engine) -> None:
        out = engine("{% pagination page=2 total_pages=5 %}")
        assert '<nav class="dj-pagination" aria-label="Pagination">' in out
        assert 'aria-label="Previous page"' in out and 'aria-label="Next page"' in out
        assert 'aria-label="Page 3"' in out

    def test_pagination_disables_next_on_the_last_page(self, engine) -> None:
        last = engine("{% pagination page=5 total_pages=5 %}")
        assert 'aria-label="Next page" dj-click="page_next" disabled>' in last
        assert 'aria-label="Previous page" dj-click="page_prev">' in last
        first = engine("{% pagination page=1 total_pages=5 %}")
        assert 'aria-label="Previous page" dj-click="page_prev" disabled>' in first
        assert 'aria-label="Next page" dj-click="page_next">' in first

    def test_badge_writes_its_status_for_a_screen_reader(self, engine) -> None:
        assert '<span class="sr-only">Status: online</span>' in engine(
            '{% badge label="x" status="online" %}'
        )
        assert "sr-only" not in engine('{% badge label="x" %}')

    def test_progress_class_list_has_no_trailing_space(self, engine) -> None:
        out = engine("{% progress value=40 %}")
        assert 'class="dj-progress__fill dj-progress__fill--primary dj-progress-w-40"' in out
        assert '" ' not in out.split('class="dj-progress__fill', 1)[1].split(">", 1)[0][:-1]

    def test_a_hex_colour_does_not_leak_into_the_class_list(self, engine) -> None:
        out = engine('{% progress value=40 color="#336699" %}')
        assert "#336699" not in out
        assert "dj-progress__fill--primary" in out
        # a value with a space would add a class of its own; it is ignored too
        spaced = engine('{% progress value=40 color="red big" %}')
        assert "big" not in spaced

    def test_progress_still_refuses_a_value_that_is_not_a_number(self, engine) -> None:
        with pytest.raises((TypeError, ValueError)):
            engine("{% progress value=v %}", {"v": None})


def test_the_templates_are_gone() -> None:
    engine = Engine.get_default()
    for name in ("badge", "avatar", "progress", "pagination"):
        with pytest.raises(TemplateDoesNotExist):
            engine.get_template(f"djust_components/{name}.html")


def test_the_django_tags_call_the_handlers(monkeypatch) -> None:
    """Gate-off: put markup back in a Django tag and this fails."""
    from djust.components import rust_handlers

    for tag, handler, src in (
        ("badge", "BadgeHandler", "{% badge %}"),
        ("avatar", "AvatarHandler", "{% avatar %}"),
        ("progress", "ProgressHandler", "{% progress %}"),
        ("pagination", "PaginationHandler", "{% pagination %}"),
    ):
        monkeypatch.setattr(
            getattr(rust_handlers, handler),
            "render_kwargs",
            lambda self, kw, ctx, _h=handler: mark_safe("<" + _h + ">"),
        )
        assert _django(src) == "<" + handler + ">", tag
