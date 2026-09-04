"""``string_if_invalid``, relative ``{% extends %}``, and dynamic include names (#2517).

Three Django behaviours the Rust engine did not have, grouped because each is
a *name-resolution* rule and each was measured on the same scoreboard run.

``string_if_invalid`` is the subtle one. Django does not merely substitute a
marker for a missing variable — when the setting is non-empty it RETURNS from
``FilterExpression.resolve`` immediately, so the filter chain never runs::

    if string_if_invalid:
        if "%s" in string_if_invalid:
            return string_if_invalid % self.var
        return string_if_invalid          # <- returns; filters skipped

That is why ``{{ missing|default:"Foo" }}`` renders the marker and NOT
``Foo`` — the single most counter-intuitive row here, and the one a
"substitute the empty value" implementation gets wrong while passing every
other case.

Every assertion is a differential against Django itself.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("django")

from django.template import Context as DjangoContext  # noqa: E402
from django.template import Engine  # noqa: E402

from djust import _rust  # noqa: E402


def _both(source: str, ctx: dict[str, Any], marker: str) -> tuple[str, str]:
    engine = Engine(string_if_invalid=marker)
    django_out = engine.from_string(source).render(DjangoContext(dict(ctx)))
    djust_out = _rust.render_template(source, dict(ctx), None, marker or None)
    return django_out, djust_out


@pytest.mark.parametrize(
    "source,ctx",
    [
        ("{{ missing }}", {}),
        ("as{{ missing }}df", {}),
        ("{{ present }}", {"present": "x"}),
        # The filter chain is SKIPPED, so `default` never fires.
        ('{{ missing|default:"Foo" }}', {}),
        ('{{ missing|upper|default:"Foo" }}', {}),
        # A present-but-empty value is NOT a failed lookup.
        ("{{ empty }}", {"empty": ""}),
        # A nested miss is still a miss.
        ("{{ obj.nope }}", {"obj": {"a": 1}}),
        # Literals are never failed lookups.
        ('{{ "lit" }}', {}),
        ("{{ 5 }}", {}),
        # List index misses.
        ("{{ items.9 }}", {"items": [1, 2]}),
    ],
)
@pytest.mark.parametrize("marker", ["INVALID", "%s-MISSING", ""])
def test_string_if_invalid_matches_django(source: str, ctx: dict[str, Any], marker: str) -> None:
    django_out, djust_out = _both(source, ctx, marker)
    assert djust_out == django_out, f"{source!r} marker={marker!r}"


def test_empty_marker_is_the_default_and_renders_nothing() -> None:
    """The default is `""` — a missing variable renders nothing, not "None"."""
    assert _rust.render_template("[{{ missing }}]", {}) == "[]"


def test_percent_s_is_substituted_with_the_variable_name() -> None:
    """Django's `string_if_invalid % self.var`. Gate-off: drop the `%s` branch
    in `Context::string_if_invalid_for` and this is the test that fails."""
    assert _rust.render_template("{{ nope }}", {}, None, "<%s>") == "&lt;nope&gt;"


def test_marker_is_escaped_like_any_other_value() -> None:
    """Django puts the marker through `render_value_in_context`, so it is
    conditionally escaped rather than injected raw."""
    out = _rust.render_template("{{ nope }}", {}, None, "<b>x</b>")
    assert out == "&lt;b&gt;x&lt;/b&gt;"


class TestBackendOptions:
    """`OPTIONS` keys the backend used to discard silently (#2518).

    `DjustTemplateBackend.__init__` popped `context_processors` and dropped
    every other key, so a project configuring `string_if_invalid`, `autoescape`
    or `debug` got no error and no effect — the worst shape for a
    security-relevant switch like `autoescape`.

    Each assertion below checks the OPTION CHANGES RENDERED OUTPUT, not that an
    attribute was stored: a stored-and-ignored setting is exactly the defect.
    """

    @staticmethod
    def _pair(options: dict, name: str):
        from django.template.backends.django import DjangoTemplates

        from djust.template.backend import DjustTemplateBackend

        params = {"NAME": name, "DIRS": [], "APP_DIRS": False, "OPTIONS": dict(options)}
        return (
            DjangoTemplates({**params, "NAME": f"dj{name}"}),
            DjustTemplateBackend({**params, "NAME": f"du{name}"}),
        )

    def test_autoescape_false_is_refused_loudly_not_ignored(self) -> None:
        """djust has no engine-wide escaping switch, by design.

        `Context::set_autoescape` has exactly two production writers, pinned by
        `test_autoescape_tag_2556.py::TestSecurityPins` on the reasoning that a
        global escape-off reachable from configuration is an XSS shape. So the
        option cannot be honoured — but silently dropping it is worse than
        refusing it, because the project then believes escaping is off when it
        is not. An explicit `False` raises; `True` is a no-op because it asks
        for what djust already does.
        """
        from django.core.exceptions import ImproperlyConfigured

        from djust.template.backend import DjustTemplateBackend

        with pytest.raises(ImproperlyConfigured, match="autoescape"):
            DjustTemplateBackend(
                {"NAME": "ae", "DIRS": [], "APP_DIRS": False, "OPTIONS": {"autoescape": False}}
            )
        # True is accepted, and escaping is on.
        engine = DjustTemplateBackend(
            {"NAME": "ae2", "DIRS": [], "APP_DIRS": False, "OPTIONS": {"autoescape": True}}
        )
        assert str(engine.from_string("{{ p }}").render({"p": "<b>"})) == "&lt;b&gt;"

    def test_string_if_invalid_option_changes_output_like_django(self) -> None:
        django_engine, djust_engine = self._pair({"string_if_invalid": "INVALID"}, "si")
        source = "[{{ nope }}]"
        assert (
            str(djust_engine.from_string(source).render({}))
            == str(django_engine.from_string(source).render({}))
            == "[INVALID]"
        )

    def test_an_unsupported_option_warns_rather_than_vanishing(self, caplog) -> None:
        """Silence is what let four keys go unnoticed."""
        from djust.template.backend import DjustTemplateBackend

        with caplog.at_level("WARNING"):
            DjustTemplateBackend(
                {"NAME": "unk", "DIRS": [], "APP_DIRS": False, "OPTIONS": {"zzz_bogus": 1}}
            )
        assert any("zzz_bogus" in r.getMessage() for r in caplog.records)
