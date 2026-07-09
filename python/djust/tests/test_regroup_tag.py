"""Tests for the built-in ``{% regroup %}`` assign-tag handler.

``{% regroup %}`` is a core Django template tag. Before this handler the
Rust template engine treated it as an unsupported tag: rendering a
LiveView template containing ``{% regroup %}`` raised
``RuntimeError: Unsupported template tag`` (the block silently vanished /
the render 500'd).

These tests drive the *real* Rust engine entry points —
``djust._rust.render_template`` and ``RustLiveView.render_with_diff`` —
so they fail if the handler is unregistered (the tag round-trips through
the Rust parser/renderer, not Django's engine). They pin Django parity:
input order preserved, consecutive (un-sorted) grouping, dotted-path
grouper, and the ``[{grouper, list}, ...]`` shape.
"""

from __future__ import annotations

import django
from django.conf import settings

if not settings.configured:
    settings.configure(
        DEBUG=True,
        INSTALLED_APPS=[
            "django.contrib.contenttypes",
            "django.contrib.auth",
            "djust",
        ],
        DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "APP_DIRS": True,
                "DIRS": [],
                "OPTIONS": {"context_processors": []},
            }
        ],
    )
    django.setup()

from djust._rust import RustLiveView, has_assign_tag_handler, render_template

# The template used across most tests: regroup then iterate the groups.
TEMPLATE = (
    "{% regroup cities by country as country_list %}"
    "{% for group in country_list %}"
    "{{ group.grouper }}[{% for city in group.list %}{{ city.name }},{% endfor %}]"
    "{% endfor %}"
)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_regroup_handler_is_registered():
    """The handler auto-registers on ``import djust`` (before any parse)."""
    assert has_assign_tag_handler("regroup"), (
        "{% regroup %} has no Rust assign-tag handler — the tag is unusable "
        "in the Rust engine and renders as an unsupported tag."
    )


# ---------------------------------------------------------------------------
# Core semantics through the Rust render engine
# ---------------------------------------------------------------------------


def test_regroup_basic_grouping_renders():
    """Consecutive same-grouper rows collapse into one group each."""
    ctx = {
        "cities": [
            {"name": "Mumbai", "country": "India"},
            {"name": "Calcutta", "country": "India"},
            {"name": "New York", "country": "USA"},
            {"name": "Chicago", "country": "USA"},
        ]
    }
    out = render_template(TEMPLATE, ctx)
    assert out == "India[Mumbai,Calcutta,]USA[New York,Chicago,]"


def test_regroup_preserves_input_order_and_does_not_pre_sort():
    """Django parity: grouping is *consecutive*, never pre-sorted.

    India, USA, India (interleaved) must yield THREE groups — not two —
    because regroup groups adjacent equal keys only. A buggy
    implementation that sorts first would produce ``India[...]USA[...]``
    (two groups) and fail here.
    """
    ctx = {
        "cities": [
            {"name": "Mumbai", "country": "India"},
            {"name": "New York", "country": "USA"},
            {"name": "Delhi", "country": "India"},
        ]
    }
    out = render_template(TEMPLATE, ctx)
    assert out == "India[Mumbai,]USA[New York,]India[Delhi,]"


def test_regroup_nested_dotted_attribute():
    """``by author.team`` resolves a nested dotted path per item."""
    template = (
        "{% regroup posts by author.team as teams %}"
        "{% for group in teams %}"
        "{{ group.grouper }}({% for p in group.list %}{{ p.title }} {% endfor %})"
        "{% endfor %}"
    )
    ctx = {
        "posts": [
            {"title": "A", "author": {"team": "Platform"}},
            {"title": "B", "author": {"team": "Platform"}},
            {"title": "C", "author": {"team": "Growth"}},
        ]
    }
    out = render_template(template, ctx)
    assert out == "Platform(A B )Growth(C )"


def test_regroup_empty_source_yields_no_groups():
    """An empty list produces an empty ``as`` variable (no groups)."""
    out = render_template(TEMPLATE, {"cities": []})
    assert out == ""


def test_regroup_missing_source_variable_is_safe():
    """A missing source var degrades to empty rather than raising."""
    out = render_template(TEMPLATE, {})
    assert out == ""


# ---------------------------------------------------------------------------
# LiveView diff path (render_with_diff)
# ---------------------------------------------------------------------------


def test_regroup_works_in_liveview_diff_path():
    """``{% regroup %}`` renders and re-diffs through RustLiveView.

    Exercises the render_with_diff code path (the LiveView update loop),
    not just the one-shot render_template entry point.
    """
    view = RustLiveView(TEMPLATE)
    view.set_state(
        "cities",
        [
            {"name": "Mumbai", "country": "India"},
            {"name": "Calcutta", "country": "India"},
            {"name": "New York", "country": "USA"},
        ],
    )

    # RustLiveView wraps fragments in an <html><head><body> scaffold, so
    # assert the grouped fragment is embedded (containment), not equality.
    html1, _patches1, _v1 = view.render_with_diff()
    assert "India[Mumbai,Calcutta,]USA[New York,]" in html1

    # Change the underlying list; regroup must re-run on the diff render.
    view.set_state(
        "cities",
        [
            {"name": "Mumbai", "country": "India"},
            {"name": "New York", "country": "USA"},
            {"name": "Chicago", "country": "USA"},
        ],
    )
    html2, patches2, _v2 = view.render_with_diff()
    assert "India[Mumbai,]USA[New York,Chicago,]" in html2
    assert "India[Mumbai,Calcutta,]" not in html2
    # The output changed, so the diff must carry a patch payload.
    assert patches2 is not None


# ---------------------------------------------------------------------------
# Direct handler unit checks (fast, no Rust round-trip)
# ---------------------------------------------------------------------------


def test_handler_returns_grouped_result_shape():
    """Handler returns ``{var: [{grouper, list}, ...]}`` from raw args."""
    from djust.template_tags.regroup import RegroupTagHandler

    handler = RegroupTagHandler()
    args = ["cities", "by", "country", "as", "country_list"]
    context = {
        "cities": [
            {"name": "Mumbai", "country": "India"},
            {"name": "Delhi", "country": "India"},
            {"name": "NYC", "country": "USA"},
        ]
    }
    result = handler.render(args, context)
    assert list(result.keys()) == ["country_list"]
    groups = result["country_list"]
    assert [g["grouper"] for g in groups] == ["India", "USA"]
    assert [c["name"] for c in groups[0]["list"]] == ["Mumbai", "Delhi"]
    assert [c["name"] for c in groups[1]["list"]] == ["NYC"]


def test_handler_malformed_args_is_noop():
    """Malformed ``regroup`` args merge nothing (no crash)."""
    from djust.template_tags.regroup import RegroupTagHandler

    handler = RegroupTagHandler()
    assert handler.render(["cities", "as", "x"], {"cities": []}) == {}


def test_handler_matches_django_regroup_grouper_values():
    """Grouper values match Django's own RegroupNode for the same input.

    Parity anchor: run the identical template+context through Django's
    template engine and assert the rendered grouper sequence agrees.
    """
    from django.template import Context, Template

    data = [
        {"name": "Mumbai", "country": "India"},
        {"name": "NYC", "country": "USA"},
        {"name": "Delhi", "country": "India"},
    ]
    django_tpl = Template(
        "{% regroup cities by country as gl %}{% for g in gl %}{{ g.grouper }},{% endfor %}"
    )
    django_out = django_tpl.render(Context({"cities": data}))
    rust_out = render_template(
        "{% regroup cities by country as gl %}{% for g in gl %}{{ g.grouper }},{% endfor %}",
        {"cities": data},
    )
    assert rust_out == django_out == "India,USA,India,"


def test_handler_warns_when_attr_operand_is_shadowed(caplog):
    """A non-identifier ``<attr>`` (a shadowing context key resolved the
    attr name to a value) emits an actionable warning.

    The Rust engine resolves all assign-tag args before the handler runs,
    so a context key named after the ``<attr>`` token arrives as that
    key's *value*. We can't recover the intended attribute name from that
    vantage point, but a value that isn't a bare identifier is the signal
    to warn (see the module docstring + #2041).
    """
    import logging

    from djust.template_tags.regroup import RegroupTagHandler

    handler = RegroupTagHandler()
    # ``args[2]`` is what a shadowed attr resolves to — a value, not a name.
    shadowed_args = ["cities", "by", "United States", "as", "gl"]
    context = {"cities": [{"name": "NYC", "country": "USA"}]}

    with caplog.at_level(logging.WARNING, logger="djust.template_tags.regroup"):
        handler.render(shadowed_args, context)

    assert any(
        "resolved to a non-identifier" in rec.message and "United States" in rec.message
        for rec in caplog.records
    ), "expected a shadowing warning for a non-identifier attr operand"


def test_handler_does_not_warn_for_normal_identifier_attr(caplog):
    """A well-formed bare/dotted ``<attr>`` never triggers the shadow warning.

    Gate for the warning above: without this, a warning on every render
    would pass the positive test tautologically.
    """
    import logging

    from djust.template_tags.regroup import RegroupTagHandler

    handler = RegroupTagHandler()
    context = {
        "cities": [
            {"name": "Mumbai", "author": {"team": "A"}},
            {"name": "Delhi", "author": {"team": "A"}},
        ]
    }

    with caplog.at_level(logging.WARNING, logger="djust.template_tags.regroup"):
        handler.render(["cities", "by", "author.team", "as", "gl"], context)

    assert not any("resolved to a non-identifier" in rec.message for rec in caplog.records), (
        "dotted identifier attr must not trigger the shadow warning"
    )
