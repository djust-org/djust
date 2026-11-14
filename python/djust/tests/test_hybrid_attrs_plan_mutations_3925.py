"""Three mutations of the hybrid attribute path plan that the #3900 tests let survive (#3925): a
loop that never runs twice, an ignored ``{% empty %}`` literal and a one-round neutralisation.
One case per mutation, each compared with Django rendering the same template, and the check
that the neutralisation converged.
"""

from __future__ import annotations

from html.parser import HTMLParser

import pytest
from django.template import Context, Template

from djust.components import _hybrid_attrs as hybrid
from djust.components.base import Component
from djust.components.utils import url_allowed

pytestmark = pytest.mark.components


@pytest.fixture(autouse=True)
def _bootstrap(settings):
    settings.DJUST_CONFIG = {"css_framework": "bootstrap5"}


def _draw(template: str, **context) -> str:
    class Box(Component):
        pass

    Box.template = template
    Box.get_context_data = lambda self: dict(context)  # type: ignore[method-assign]
    return str(Box().render())


def _django(template: str, **context) -> str:
    return Template(template).render(Context(context))


def _href(markup: str) -> str | None:
    found: dict[str, str | None] = {}

    class P(HTMLParser):
        def handle_starttag(self, t, attrs):
            if t == "a" and not found:
                found.update(attrs)

    P(convert_charrefs=True).feed(markup)
    return found.get("href")


def _is_threat(value: str | None) -> bool:
    return value is not None and not url_allowed(value)


class TestThePlanMutations:
    def test_a_loop_is_followed_running_twice(self) -> None:
        """Once, ``http`` + ``:x`` is allowed; twice, ``httphttp:x`` is a scheme outside the
        allowlist. A plan that never runs a loop twice lets it through."""
        template = '<a href="{% for i in items %}{{ s }}{% endfor %}:x">'
        (group,) = hybrid.plan(template).groups
        assert any(sum(k == "slot" for k, _v in p) == 2 for p in group.paths)
        assert _href(_draw(template, items=[1], s="http")) != "http:x"  # fail closed
        out = _draw(template, items=[1, 2], s="http")
        assert "httphttp:" not in out, out

    def test_the_empty_branch_literal_is_part_of_the_value(self) -> None:
        """``:alert(1)`` alone is relative; after the ``{% empty %}`` literal it is a script URL."""
        template = '<a href="{% for i in items %}/{% empty %}javascript{% endfor %}{{ s }}">'
        assert _is_threat(_href(_django(template, items=[], s=":alert(1)")))
        out = _draw(template, items=[], s=":alert(1)")
        assert not _is_threat(_href(out)), out

    def test_neutralisation_runs_until_every_path_is_allowed(self) -> None:
        """Round one empties ``y`` for the refused ``x``-path; that joins ``java`` + ``script:``
        in the other branch, refused in round two. One round leaves it."""
        template = '<a href="{% if a %}{{ x }}{% else %}java{% endif %}{{ y }}script:alert(1)">'
        assert not _is_threat(_href(_django(template, a=False, x="vbscript:", y="/")))
        out = _draw(template, a=False, x="vbscript:", y="/")
        assert out == '<a href="java#script:alert(1)">', out

    def test_a_neutralisation_that_does_not_converge_fails_closed(self, monkeypatch) -> None:
        """If the replacement did not make every path allowed, the value is never printed."""
        monkeypatch.setattr(hybrid, "_NEUTRAL", "javascript:")
        hybrid.plan.cache_clear()
        with pytest.raises(RuntimeError, match="could not be made safe"):
            hybrid.harden_context('<a href="{{ s }}">', {"s": "javascript:alert(1)"})
