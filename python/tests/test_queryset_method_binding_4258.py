"""#4258 -- the #4241 sidecar capture binds ``count`` / ``exists`` / ``first`` /
``last`` to the QuerySet's OWN context name.

#4241 holds a context QuerySet for the render sidecar when the template reads
one of those methods on it. The check matched any extracted path whose first
segment is a method name, and the extractor files a loop row's reads under the
iterable (``{% for o in qs %}{{ o.first }}`` -> ``qs: ["first"]``). So a row
field named like a method held the QuerySet too, and ``{{ qs.query }}`` then
reached the SQL through the sidecar walk. A direct read (``{{ qs.first }}``)
must still work on every native entry point, against Django as the oracle.
"""

from __future__ import annotations

import html as html_lib

import pytest
from django.contrib.auth.models import User

from test_queryset_count_exists_4241 import (  # noqa: F401 -- fixture
    LIVEVIEWS,
    NATIVE,
    _django_render,
    _full,
    users,
)

METHODS = ["count", "exists", "first", "last"]


def _render(render, source: str) -> str:
    return html_lib.unescape(render(source, _full())).replace("<!--dj-if-->", "")


@pytest.mark.django_db
@pytest.mark.usefixtures("users")
class TestLoopRowMethodNameDoesNotHoldQuerySet4258:
    @pytest.mark.parametrize("render", LIVEVIEWS)
    @pytest.mark.parametrize("method", METHODS)
    def test_loop_row_read_does_not_expose_query(self, render, method) -> None:
        source = "[{%% for o in qs %%}{{ o.%s }}{%% endfor %%}][{{ qs.query }}]" % method
        got = _render(render, source)
        assert "SELECT" not in got.upper(), got
        assert "auth_user" not in got, got

    @pytest.mark.parametrize("render", LIVEVIEWS)
    @pytest.mark.parametrize("method", METHODS)
    def test_nested_loop_row_read_does_not_expose_query(self, render, method) -> None:
        source = "[{%% for o in qs %%}{{ o.%s.username }}{%% endfor %%}][{{ qs.query }}]" % method
        got = _render(render, source)
        assert "SELECT" not in got.upper(), got


@pytest.mark.django_db
@pytest.mark.usefixtures("users")
class TestDirectReadStillAnswers4258:
    """The positive half: ``{{ qs.first }}`` read on the QuerySet's own name
    keeps the #4241 behaviour on every native path, alone and beside a loop
    whose rows read a field of the same name."""

    @pytest.mark.parametrize("render", NATIVE)
    @pytest.mark.parametrize(
        "source",
        [
            "[{{ qs.first.username }}]",
            "[{{ qs.count }}/{{ qs.exists }}/{{ qs.last.username }}]",
            "[{% for o in qs %}{{ o.first }}{% endfor %}][{{ qs.first.username }}]",
            "[{% if qs.exists %}y{% endif %}][{% for o in qs %}{{ o.count }}{% endfor %}]",
            # A ``with`` alias IS the value; a filter argument reads the name.
            "[{% with x=qs %}{{ x.count }}{% endwith %}]",
            "[{{ missing|default:qs.count }}]",
        ],
    )
    def test_agrees_with_django(self, render, source) -> None:
        expected = _django_render(source, _full())
        assert _render(render, source) == expected

    def test_django_oracle(self) -> None:
        assert _django_render("[{{ qs.first.username }}]", _full()) == "[u0]"
        assert User.objects.count() == 3


class TestQuerysetMethodRoots4258:
    """``queryset_method_roots``: the one predicate the JIT and the bridge share."""

    @pytest.mark.parametrize(
        "source, expected",
        [
            # The loop variable keeps its own read; nothing is filed under ``qs``.
            ("{% for o in qs %}{{ o.first }}{% endfor %}{{ qs.query }}", {"o"}),
            ("{% for o in qs %}{{ o.count.username }}{% endfor %}", {"o"}),
            ("{{ qs.first.username }}", {"qs"}),
            ("{% if qs.exists %}y{% endif %}", {"qs"}),
            ("{% with x=qs %}{{ x.count }}{% endwith %}", {"qs"}),
            ("{% for o in qs %}{% with y=o %}{{ y.last }}{% endwith %}{% endfor %}", {"o"}),
            ("{{ n|default:qs.count }}", {"qs"}),
            ("{{ qs.query }}", frozenset()),
            ("", frozenset()),
            (None, frozenset()),
            ("{{ qs.count|bogus_filter_4258 }}", frozenset()),
        ],
    )
    def test_roots(self, source, expected) -> None:
        from djust.mixins import jit

        jit._queryset_method_roots_cache.clear()
        assert jit.queryset_method_roots(source) == frozenset(expected)

    def test_cached_per_template(self, monkeypatch) -> None:
        from djust.mixins import jit

        calls = []
        real = jit.extract_own_reads

        def counting(content):
            calls.append(content)
            return real(content)

        jit._queryset_method_roots_cache.clear()
        monkeypatch.setattr(jit, "extract_own_reads", counting)
        source = "{{ qs.count }}"
        assert jit.queryset_method_roots(source) == {"qs"}
        assert jit.queryset_method_roots(source) == {"qs"}
        assert len(calls) == 1

    @pytest.mark.django_db
    @pytest.mark.usefixtures("users")
    def test_bridge_computes_roots_once_per_sync(self, monkeypatch) -> None:
        from djust.mixins import rust_bridge

        calls = []
        real = rust_bridge._queryset_method_roots_for

        def counting(view):
            calls.append(view)
            return real(view)

        monkeypatch.setattr(rust_bridge, "_queryset_method_roots_for", counting)
        syncs = []
        real_sync = rust_bridge.RustBridgeMixin._sync_state_to_rust

        def counting_sync(self, *args, **kwargs):
            syncs.append(1)
            return real_sync(self, *args, **kwargs)

        monkeypatch.setattr(rust_bridge.RustBridgeMixin, "_sync_state_to_rust", counting_sync)
        source = "[{{ a.count }}/{{ b.count }}/{{ c.count }}]"
        context = {
            "a": User.objects.all(),
            "b": User.objects.filter(username="u0"),
            "c": User.objects.none(),
        }
        got = html_lib.unescape(LIVEVIEWS[0].values[0](source, context))
        assert got == _django_render(source, context)
        # Three QuerySets in the context, one lookup per sync (not one per QuerySet).
        assert syncs and len(calls) == len(syncs), (calls, syncs)
