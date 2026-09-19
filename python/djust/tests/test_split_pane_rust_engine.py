"""`{% split_pane %}` renders on the Rust engine, with Django's own structure.

It is a **two-segment** tag — `{% split_pane %}…{% pane %}…{% endsplit_pane %}` —
and the native block path declares exactly one end tag. So the parser met
`{% pane %}` and raised:

    Invalid block tag on line 1: 'pane', expected 'endsplit_pane'

The handler registered on that path, `SplitPaneHandler`, was a stub in a second
way: it wrapped the pre-rendered body in a single `<div>` and ignored the split
entirely. So the tag failed to parse *and* would have diverged from Django had
it parsed.

It now registers on the **raw** path. The Rust parser hands the body over
un-rendered — `{% pane %}` and all — and `LibraryRawBlockTagHandler` re-lexes
it, appends `{% endsplit_pane %}`, and calls Django's own `do_split_pane` on a
synthetic parser. The output is Django's node's output, which is what these
tests assert: structure, not merely "no exception".

Django's own engine renders `{% split_pane %}` fine via `{% load
djust_components %}`; these tests drive `DjustTemplateBackend` so the Rust path
is what is actually exercised. The suite's configured backend is
`DjangoTemplates`, so a test that went through `engines[...]` would pass
without touching Rust at all.
"""

import pytest


@pytest.fixture(scope="module")
def rust_backend():
    """A DjustTemplateBackend, with the component tags registered."""
    from djust.components.rust_handlers import register_with_rust_engine
    from djust.template.backend import DjustTemplateBackend

    register_with_rust_engine()
    return DjustTemplateBackend(
        {"NAME": "split-pane-test", "DIRS": [], "APP_DIRS": True, "OPTIONS": {}}
    )


def _render(rust_backend, source: str) -> str:
    template = rust_backend.from_string("{% load djust_components %}" + source)
    return template.render({})


# ---------------------------------------------------------------------------
# Behaviour — the structure Django's own node emits
# ---------------------------------------------------------------------------


def test_split_pane_renders_instead_of_raising(rust_backend):
    """The regression itself: this raised `Invalid block tag … 'pane'`."""
    html = _render(
        rust_backend,
        '{% split_pane direction="horizontal" %}LEFT{% pane %}RIGHT{% endsplit_pane %}',
    )

    assert "LEFT" in html and "RIGHT" in html


def test_split_pane_emits_djangos_two_pane_structure(rust_backend):
    """Parity, not just absence of an error.

    The stub handler emitted `<div class="split-pane …">{content}</div>` — no
    panes, no handle. Django's node emits `sp-pane-1` / `sp-pane-2` with an
    `sp-handle` between them and the drag script. Asserting the handle and the
    direction class is what distinguishes parity from the stub.
    """
    html = _render(
        rust_backend,
        '{% split_pane direction="horizontal" %}LEFT{% pane %}RIGHT{% endsplit_pane %}',
    )

    assert "split-pane-horizontal" in html
    assert "sp-handle" in html
    assert "sp-pane-1" in html
    assert "sp-pane-2" in html


def test_split_pane_direction_reaches_the_output(rust_backend):
    """`direction` is a keyword argument, so it exercises arg passing too."""
    html = _render(
        rust_backend,
        '{% split_pane direction="vertical" %}A{% pane %}B{% endsplit_pane %}',
    )

    assert "split-pane-vertical" in html
    assert "split-pane-horizontal" not in html


# ---------------------------------------------------------------------------
# Registration — so the stub handler cannot quietly return
# ---------------------------------------------------------------------------
#
# The raw/block registry lookups (`raw_block_handler_exists`,
# `block_handler_exists`) are Rust-internal — the parser calls them across the
# FFI boundary but they are not PyO3 exports, so a test cannot query them from
# Python. The three rendering tests above are what prove the registration took
# the raw path; they fail on the old block-path registration, which is the
# behaviour that matters. What IS queryable from Python is the table the old
# stub lived in.


def test_no_split_pane_stub_handler_remains():
    """`SplitPaneHandler` was a diverging stub; it must not come back."""
    from djust.components import rust_handlers

    assert not hasattr(rust_handlers, "SplitPaneHandler")
    assert all(tag != "split_pane" for tag, _end, _handler in rust_handlers.BLOCK_HANDLERS), (
        "split_pane is back in BLOCK_HANDLERS — it would hit the two-segment parse error"
    )
