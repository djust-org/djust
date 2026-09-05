"""``{% url %}`` compiles its argument list at PARSE time, as Django does (#2577).

The defect
----------
Django's ``do_url`` runs ``parser.compile_filter`` over the view name and every
argument while the template is COMPILED, so a malformed argument list —
``id,``, ``id=``, ``a.id=id``, ``a.id!id`` and the two unterminated-string
forms — raises ``TemplateSyntaxError`` before the view name is ever reversed.
djust reached ``{% url %}`` only at RENDER (it compiled to a ``CustomTag`` and
the Python handler ran ``_resolve_url_tags`` later), so:

* the quoted ``{% url "view" id, %}`` spelling (Django ``test_url_fail04`` –
  ``09``) rendered instead of refusing — ``TemplateSyntaxError not raised``;
* the unquoted ``{% url named_url id, %}`` spelling (``test_url_fail14`` –
  ``19``) raised ``NoReverseMatch`` at render, because the missing view was
  reached before the malformed argument was (#2607) — the wrong exception, at
  the wrong time.

The fix puts the refusal in the Rust parser (``crates/djust_templates/src/
parser.rs`` — ``validate_url_args``), which runs inside ``compile_template`` at
``DjustTemplate._compile()`` — the shared parse chokepoint for both the
``DjustTemplateBackend`` and the LiveView path, and BEFORE render. That is what
lets the parse-time ``TemplateSyntaxError`` win the race against the render-time
``NoReverseMatch`` for the ``named_url`` spelling. The separate URL pre-pass was subsequently removed for #2616; these checks
remain in the shared native parser.

Every expectation here is LIVE Django, never a transcription: each malformed
list is compiled on Django and asserted to raise ``TemplateSyntaxError`` in the
same test that asserts djust refuses it, and each well-formed list is compiled
on Django and asserted to build.
"""

from __future__ import annotations

import pytest
from django.template import Engine, TemplateSyntaxError

from djust import _rust

# The argument shapes the issue names, one per Django ``test_url_failNN`` cell.
# The key is the Django test id; the value is the template source. fail04-09
# use the quoted ``"view"`` spelling (Django ``get_template`` refuses at parse);
# fail14-19 are the SAME argument shapes with the unquoted ``named_url``
# spelling (Django ``render_to_string`` refuses, but still at PARSE time inside
# ``do_url``, before the missing view is reversed).
MALFORMED = {
    "fail04": '{% url "view" id, %}',
    "fail05": '{% url "view" id= %}',
    "fail06": '{% url "view" a.id=id %}',
    "fail07": '{% url "view" a.id!id %}',
    "fail08": '{% url "view" id="unterminatedstring %}',
    "fail09": '{% url "view" id=", %}',
    "fail14": "{% url named_url id, %}",
    "fail15": "{% url named_url id= %}",
    "fail16": "{% url named_url a.id=id %}",
    "fail17": "{% url named_url a.id!id %}",
    "fail18": '{% url named_url id="unterminatedstring %}',
    "fail19": '{% url named_url id=", %}',
}

# Well-formed lists that MUST keep compiling — the regression guard against a
# validator that is stricter than Django. Every one compiles on live Django.
WELL_FORMED = [
    '{% url "view" %}',
    '{% url "view" pk=1 %}',
    '{% url "view" 1 2 %}',
    '{% url "view" obj.pk %}',
    "{% url named_url client.id %}",
    '{% url "view" x=obj.pk %}',
    '{% url "view" as u %}',
    '{% url "view" pk=1 as u %}',
    '{% url "view" v|lower %}',
]


def _django_engine() -> Engine:
    """A bare Django engine — no urlconf, so a well-formed ``{% url %}`` COMPILES
    (``do_url`` never reverses at parse) and a malformed one refuses at parse."""
    return Engine(libraries={}, builtins=["django.template.defaulttags"])


def django_compiles(source: str) -> bool:
    try:
        _django_engine().from_string(source)
    except TemplateSyntaxError:
        return False
    return True


def djust_compiles(source: str) -> tuple[bool, str]:
    """``compile_template`` is what ``DjustTemplate._compile()`` calls — the
    parse-time chokepoint. A refusal here is the parse-time refusal the fix is
    about, independent of whether the view name would later resolve."""
    try:
        _rust.compile_template(source)
    except BaseException as exc:  # noqa: BLE001 — a Rust panic is not a refusal
        return False, str(exc)
    return True, ""


class TestMalformedUrlArgumentsRefuseAtParse:
    """Both engines refuse every malformed list, and djust refuses at PARSE."""

    @pytest.mark.parametrize("cell", MALFORMED, ids=list(MALFORMED))
    def test_django_refuses_at_parse(self, cell: str) -> None:
        # The premise, read off live Django rather than transcribed: do_url
        # raises TemplateSyntaxError while compiling, for both the quoted and
        # the named_url spelling.
        assert not django_compiles(MALFORMED[cell]), f"premise: Django compiles {cell!r}"

    @pytest.mark.parametrize("cell", MALFORMED, ids=list(MALFORMED))
    def test_djust_refuses_at_parse_too(self, cell: str) -> None:
        compiled, msg = djust_compiles(MALFORMED[cell])
        assert not compiled, f"{cell}: djust still parsed a malformed url list"
        # Every malformed-LIST cell is the head-atom remainder refusal. The
        # no-args case (`{% url %}`, Django's `len(bits) < 2`) is NOT here: it
        # is a missing-required-argument error owned by the render-time handler
        # (#2563), which raises Django's genuine `TemplateSyntaxError`.
        assert "Could not parse the remainder" in msg, f"{cell}: unexpected message {msg!r}"


class TestWellFormedUrlArgumentsStillCompile:
    """The regression guard: the validator is EQUAL to Django, not stricter."""

    @pytest.mark.parametrize("source", WELL_FORMED)
    def test_django_compiles_it(self, source: str) -> None:
        assert django_compiles(source), f"premise: Django refuses {source!r}"

    @pytest.mark.parametrize("source", WELL_FORMED)
    def test_djust_compiles_it_too(self, source: str) -> None:
        compiled, msg = djust_compiles(source)
        assert compiled, f"djust wrongly refused a well-formed url list: {source!r} -> {msg}"


class TestNamedUrlRaceIsWonAtParse:
    """The #2607 interaction, pinned: the parse-time refusal must beat the
    render-time ``NoReverseMatch`` for the unquoted ``named_url`` spelling.

    ``compile_template`` never renders, so a refusal from it is proof the
    template was rejected at COMPILE — the ``NoReverseMatch`` path (render)
    is never reached. A test that only rendered could not tell a parse-time
    ``TemplateSyntaxError`` from a render-time one.
    """

    @pytest.mark.parametrize("cell", ["fail14", "fail15", "fail16", "fail17", "fail18", "fail19"])
    def test_named_url_shape_refuses_before_any_render(self, cell: str) -> None:
        compiled, msg = djust_compiles(MALFORMED[cell])
        assert not compiled and "Could not parse the remainder" in msg, (
            f"{cell}: {msg!r} — a parse-time refusal must win over NoReverseMatch"
        )
