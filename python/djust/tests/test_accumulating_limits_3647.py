"""The limits of per-container ``dj-update`` accumulation, resolved (#3647).

#3645 decides which ``dj-update="append"`` / ``"prepend"`` containers accumulate
by reading the view's template source. Six limits of that reading were listed on
#3647; each class below is one of them, driven through the public entry points
(``accumulating_container_ids``, ``view_accumulating_container_ids`` and a real
WebSocket) so that each is red on the code as it stood.

Which direction is safe: a container is only ever left OUT of the accumulating set
on evidence that it reads no temporary key (the template is authoritative there:
removals and edits reach the page). Anything the scan cannot see through (a
dynamic ``{% include %}`` name, an unreadable file, a cycle, a source too large to
scan) keeps the #3645 reading, the container accumulates.
"""

from __future__ import annotations

import copy
import time
from contextlib import contextmanager
from dataclasses import dataclass

import pytest
from django.conf import settings
from django.test import override_settings

from djust import LiveView
from djust._accumulating_containers import (
    accumulating_container_ids,
    view_accumulating_container_ids,
)
from djust.decorators import event_handler

from .test_stream_only_handlers_3639 import drive as _drive_over_a_real_socket


@contextmanager
def template_files(tmp_path, files):
    """Make ``files`` loadable by name: the dirs the Rust engine searches for an
    ``{% include %}`` and the dirs Django's loaders search are the same."""
    for name, source in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
    config = copy.deepcopy(settings.TEMPLATES[0])
    config["DIRS"] = [str(tmp_path), *config.get("DIRS", [])]
    with override_settings(TEMPLATES=[config]):
        yield


def container(inner: str, cid: str = "list", mode: str = "append") -> str:
    return f'<div><ul id="{cid}" dj-update="{mode}">{inner}</ul></div>'


# ---------------------------------------------------------------------------
# 1. {% include %}: the included file is read
# ---------------------------------------------------------------------------


class TestIncludedTemplatesAreScanned:
    def test_an_include_that_reads_no_temporary_key_does_not_accumulate(self, tmp_path):
        files = {
            "plain.html": "{% for r in items %}<li id='i{{ r.id }}'>{{ r.body }}</li>{% endfor %}"
        }
        with template_files(tmp_path, files):
            found = accumulating_container_ids(container('{% include "plain.html" %}'), ("rows",))
        assert found == ()

    def test_an_include_that_reads_one_does(self, tmp_path):
        files = {
            "temp.html": "{% for r in rows %}<li id='i{{ r.id }}'>{{ r.body }}</li>{% endfor %}"
        }
        with template_files(tmp_path, files):
            found = accumulating_container_ids(container('{% include "temp.html" %}'), ("rows",))
        assert found == ("list",)

    def test_single_quotes_and_a_with_clause_are_read(self, tmp_path):
        files = {"plain.html": "{{ item }}"}
        with template_files(tmp_path, files):
            plain = container("{% include 'plain.html' with item=other %}")
            passes_it = container("{% include 'plain.html' with item=rows %}")
            assert accumulating_container_ids(plain, ("rows",)) == ()
            assert accumulating_container_ids(passes_it, ("rows",)) == ("list",)

    def test_a_file_name_that_contains_the_key_is_not_a_read(self, tmp_path):
        """``rows.html`` names the key in a STRING; nothing in the file reads it."""
        files = {"rows.html": "<li id='x'>fixed</li>"}
        with template_files(tmp_path, files):
            found = accumulating_container_ids(container('{% include "rows.html" %}'), ("rows",))
        assert found == ()

    def test_only_hides_the_view_context_from_the_included_file(self, tmp_path):
        files = {"temp.html": "{% for r in rows %}x{% endfor %}"}
        with template_files(tmp_path, files):
            hidden = container('{% include "temp.html" only %}')
            passed = container('{% include "temp.html" with rows=rows only %}')
            assert accumulating_container_ids(hidden, ("rows",)) == ()
            assert accumulating_container_ids(passed, ("rows",)) == ("list",)

    def test_includes_inside_includes_are_followed(self, tmp_path):
        files = {
            "outer.html": '<div>{% include "inner.html" %}</div>',
            "inner.html": "{% for r in rows %}x{% endfor %}",
            "outer_plain.html": '<div>{% include "inner_plain.html" %}</div>',
            "inner_plain.html": "{% for r in items %}x{% endfor %}",
        }
        with template_files(tmp_path, files):
            assert accumulating_container_ids(
                container('{% include "outer.html" %}'), ("rows",)
            ) == ("list",)
            assert (
                accumulating_container_ids(container('{% include "outer_plain.html" %}'), ("rows",))
                == ()
            )

    def test_a_relative_include_resolves_against_the_including_template(self, tmp_path):
        files = {
            "pages/row.html": "{% for r in rows %}x{% endfor %}",
            "pages/plain.html": "{% for r in items %}x{% endfor %}",
        }
        with template_files(tmp_path, files):
            reads = accumulating_container_ids(
                container('{% include "./row.html" %}'), ("rows",), origin="pages/list.html"
            )
            plain = accumulating_container_ids(
                container('{% include "./plain.html" %}'), ("rows",), origin="pages/list.html"
            )
        assert reads == ("list",)
        assert plain == ()

    def test_a_relative_include_with_no_known_origin_stays_conservative(self, tmp_path):
        with template_files(tmp_path, {"plain.html": "x"}):
            found = accumulating_container_ids(container('{% include "./plain.html" %}'), ("rows",))
        assert found == ("list",)

    def test_a_relative_include_cannot_climb_out_of_the_template_dirs(self, tmp_path):
        (tmp_path.parent / "outside.html").write_text("plain")
        with template_files(tmp_path, {"pages/x.html": "x"}):
            found = accumulating_container_ids(
                container('{% include "../../outside.html" %}'), ("rows",), origin="pages/list.html"
            )
        assert found == ("list",)

    def test_a_dynamic_name_a_missing_file_and_a_filtered_name_stay_conservative(self, tmp_path):
        with template_files(tmp_path, {"plain.html": "x"}):
            for tag in (
                "{% include which %}",
                '{% include "nope.html" %}',
                '{% include "plain.html"|add:suffix %}',
            ):
                found = accumulating_container_ids(container(tag), ("rows",))
                assert found == ("list",), tag

    def test_an_include_cycle_stays_conservative_and_terminates(self, tmp_path):
        files = {"a.html": '{% include "b.html" %}', "b.html": '{% include "a.html" %}'}
        with template_files(tmp_path, files):
            found = accumulating_container_ids(container('{% include "a.html" %}'), ("rows",))
        assert found == ("list",)

    def test_a_chain_deeper_than_the_limit_stays_conservative(self, tmp_path):
        files = {f"d{i}.html": '{%% include "d%d.html" %%}' % (i + 1) for i in range(30)}
        files["d30.html"] = "plain"
        with template_files(tmp_path, files):
            found = accumulating_container_ids(container('{% include "d0.html" %}'), ("rows",))
        assert found == ("list",)

    def test_more_included_files_than_the_limit_stay_conservative(self, tmp_path):
        from djust import _accumulating_containers as mod

        files = {f"p{i}.html": "plain" for i in range(mod.MAX_INCLUDED_TEMPLATES + 6)}

        def page(count):
            return container("".join('{%% include "p%d.html" %%}' % i for i in range(count)))

        with template_files(tmp_path, files):
            assert accumulating_container_ids(page(mod.MAX_INCLUDED_TEMPLATES), ("rows",)) == ()
            assert accumulating_container_ids(page(mod.MAX_INCLUDED_TEMPLATES + 1), ("rows",)) == (
                "list",
            )

    def test_a_cycle_is_cut_at_the_second_visit_not_at_the_depth_limit(self, tmp_path, monkeypatch):
        """Either would end the scan with the same answer; only the cycle guard does it
        without reading the files over and over."""
        from djust import _accumulating_containers as mod

        reads = []
        real = mod._resolve_include

        def counting(name, origin):
            reads.append(name)
            return real(name, origin)

        monkeypatch.setattr(mod, "_resolve_include", counting)
        files = {"a.html": '{% include "b.html" %}', "b.html": '{% include "a.html" %}'}
        with template_files(tmp_path, files):
            assert accumulating_container_ids(container('{% include "a.html" %}'), ("rows",)) == (
                "list",
            )
        # a, b, and a again (found to be in progress); without the guard the walk
        # goes on to the depth limit.
        assert reads == ["a.html", "b.html", "a.html"], reads

    def test_an_included_file_that_extends_stays_conservative(self, tmp_path):
        files = {"child.html": '{% extends "base.html" %}{% block b %}x{% endblock %}'}
        with template_files(tmp_path, files):
            found = accumulating_container_ids(container('{% include "child.html" %}'), ("rows",))
        assert found == ("list",)

    def test_a_template_included_twice_is_read_once_per_scan(self, tmp_path):
        files = {"plain.html": "{{ item }}"}
        with template_files(tmp_path, files):
            page = container('{% include "plain.html" %}{% include "plain.html" %}')
            assert accumulating_container_ids(page, ("rows",)) == ()

    def test_streams_in_an_included_file_count(self, tmp_path):
        files = {"feed.html": "{% for r in streams.feed %}x{% endfor %}"}
        with template_files(tmp_path, files):
            found = accumulating_container_ids(container('{% include "feed.html" %}'), ("other",))
        assert found == ("list",)


# ---------------------------------------------------------------------------
# 2. Fallbacks to the replace behaviour that can be removed
# ---------------------------------------------------------------------------


class TestAliasesOfATemporaryKey:
    def test_a_with_alias_wrapped_around_the_container_is_followed(self):
        page = (
            "{% with items=rows %}"
            + container("{% for r in items %}<li id='i{{ r.id }}'></li>{% endfor %}")
            + "{% endwith %}"
        )
        assert accumulating_container_ids(page, ("rows",)) == ("list",)

    def test_the_legacy_as_spelling_is_followed(self):
        page = "{% with rows as items %}" + container("{{ items }}") + "{% endwith %}"
        assert accumulating_container_ids(page, ("rows",)) == ("list",)

    def test_an_alias_of_an_alias_is_followed(self):
        page = (
            "{% with a=rows %}{% with b=a %}" + container("{{ b }}") + "{% endwith %}{% endwith %}"
        )
        assert accumulating_container_ids(page, ("rows",)) == ("list",)

    def test_an_alias_of_something_else_is_not_one(self):
        page = "{% with items=other %}" + container("{{ items }}") + "{% endwith %}"
        assert accumulating_container_ids(page, ("rows",)) == ()

    def test_only_the_bound_name_is_an_alias(self):
        page = "{% with a=rows b=other %}" + container("{{ b }}") + "{% endwith %}"
        assert accumulating_container_ids(page, ("rows",)) == ()

    def test_an_alias_chain_is_followed_up_to_the_limit_and_no_further(self):
        from djust import _accumulating_containers as mod

        def chain(links):
            opens = "{% with a0=rows %}" + "".join(
                "{%% with a%d=a%d %%}" % (i + 1, i) for i in range(links)
            )
            return opens + container("{{ a%d }}" % links) + "{% endwith %}" * (links + 1)

        assert accumulating_container_ids(chain(mod.MAX_ALIASES - 1), ("rows",)) == ("list",)
        assert accumulating_container_ids(chain(mod.MAX_ALIASES + 5), ("rows",)) == ()

    def test_a_with_that_reuses_the_key_name_for_something_else_is_still_the_key(self):
        """Documented: shadowing is not followed, the name is treated as the key."""
        page = "{% with rows=other %}" + container("{{ rows }}") + "{% endwith %}"
        assert accumulating_container_ids(page, ("rows",)) == ("list",)

    def test_after_endwith_the_alias_is_gone(self):
        page = "{% with items=rows %}<p>{{ items }}</p>{% endwith %}" + container(
            "{% for r in items %}x{% endfor %}"
        )
        assert accumulating_container_ids(page, ("rows",)) == ()

    def test_a_with_that_filters_the_key_is_still_an_alias(self):
        page = '{% with items=rows|slice:":5" %}' + container("{{ items }}") + "{% endwith %}"
        assert accumulating_container_ids(page, ("rows",)) == ("list",)

    def test_extra_names_given_by_the_caller_are_keys_too(self):
        page = container("{% for r in items %}x{% endfor %}")
        assert accumulating_container_ids(page, ("rows",)) == ()
        assert accumulating_container_ids(page, ("rows", "items")) == ("list",)


class TestATagLevelConditionalBeforeDjUpdate:
    def test_a_greater_than_in_the_tag_does_not_end_it(self):
        page = (
            '<div><ul {% if a > b %}class="wide"{% endif %} id="list" dj-update="append">'
            "{{ rows }}</ul></div>"
        )
        assert accumulating_container_ids(page, ("rows",)) == ("list",)

    def test_less_than_quotes_and_nested_tags_in_the_tag_are_handled(self):
        page = (
            "<div><ul {% if a < b %}title=\"x\"{% endif %} {% if q == 'a>b' %}hidden{% endif %}"
            ' id="list" dj-update="prepend">{{ rows }}</ul></div>'
        )
        assert accumulating_container_ids(page, ("rows",)) == ("list",)

    def test_a_comparison_in_a_body_tag_is_harmless(self):
        page = container("{% if n > 3 %}<b>many</b>{% endif %}{% for r in rows %}x{% endfor %}")
        assert accumulating_container_ids(page, ("rows",)) == ("list",)

    def test_a_control_character_in_the_page_does_not_shift_the_bodies(self):
        """``str.splitlines`` splits on form feeds and U+2028; the HTML parser counts
        only newlines, so line starts built from ``splitlines`` pointed past the real
        body and a container read the wrong text."""
        padding = ("\x0c" + chr(0x2028) + "\x1c") * 60
        page = (
            padding
            + "\n"
            + container("{% for r in items %}x{% endfor %}")
            + container("{{ rows }}", "two")
        )
        assert accumulating_container_ids(page, ("rows",)) == ("two",)


# ---------------------------------------------------------------------------
# 4. Nested containers
# ---------------------------------------------------------------------------


class TestNestedContainers:
    NESTED = (
        '<div id="outer" dj-update="append">'
        '<ul id="inner" dj-update="append">{% for r in rows %}<li id="i{{ r.id }}"></li>{% endfor %}</ul>'
        "{% for c in comments %}<p id='c{{ c.id }}'></p>{% endfor %}"
        "</div>"
    )

    def test_the_outer_container_is_not_fed_by_what_only_the_inner_one_reads(self):
        assert accumulating_container_ids(self.NESTED, ("rows",)) == ("inner",)

    def test_an_outer_container_that_reads_one_itself_still_accumulates(self):
        page = self.NESTED.replace("{% for c in comments %}", "{% for c in rows %}")
        assert accumulating_container_ids(page, ("rows",)) == ("inner", "outer")

    def test_the_outer_is_not_fed_through_a_deeper_nesting_either(self):
        page = (
            '<div id="a" dj-update="append"><section><div id="b" dj-update="prepend">'
            '<ul id="c" dj-update="append">{{ rows }}</ul></div></section></div>'
        )
        assert accumulating_container_ids(page, ("rows",)) == ("c",)

    def test_a_dynamic_id_inner_container_is_excluded_from_the_outer_too(self):
        page = (
            '<div id="outer" dj-update="append">'
            '<ul id="in{{ n }}" dj-update="append">{{ rows }}</ul></div>'
        )
        assert accumulating_container_ids(page, ("rows",)) == ()

    def test_a_replace_container_inside_is_part_of_the_outer_body(self):
        page = (
            '<div id="outer" dj-update="append">'
            '<ul id="inner" dj-update="replace">{{ rows }}</ul></div>'
        )
        assert accumulating_container_ids(page, ("rows",)) == ("outer",)

    def test_siblings_are_independent(self):
        page = container("{{ rows }}", "a") + container("{{ other }}", "b")
        assert accumulating_container_ids(page, ("rows",)) == ("a",)


# ---------------------------------------------------------------------------
# 5. The scan is bounded and a template edit is seen
# ---------------------------------------------------------------------------


def _took(fn) -> float:
    start = time.perf_counter()
    fn()
    return time.perf_counter() - start


class TestTheScanIsBounded:
    BUDGET = 1.0

    @pytest.mark.parametrize(
        "junk",
        [
            "{{" * 20000,
            "{%" * 20000,
            "{{ x }" * 20000,
            "{% include " * 5000,
            "<a " * 20000,
            "<" * 20000,
            "<!--" * 20000,
            "<![CDATA[" * 5000,
            '<ul id="a" dj-update="append">' * 5000,
            "{% with a=rows %}" * 5000,
        ],
        ids=[
            "unterminated-var",
            "unterminated-tag",
            "single-brace",
            "include-spam",
            "open-tags",
            "less-than",
            "comments",
            "cdata",
            "deep-containers",
            "with-spam",
        ],
    )
    @pytest.mark.parametrize("where", ["inside a container", "outside any container"])
    def test_pathological_input_is_scanned_in_bounded_time(self, junk, where):
        if where == "inside a container":
            page = container("{{ rows }}" + junk)
        else:
            page = container("{{ rows }}") + junk
        elapsed = _took(lambda: accumulating_container_ids(page, ("rows",)))
        assert elapsed < self.BUDGET, f"{elapsed:.2f}s"

    def test_a_source_over_the_cap_is_not_scanned_and_accumulates_nothing(self, caplog):
        import logging

        from djust import _accumulating_containers as mod

        page = container("{{ rows }}") + " " * (mod.MAX_SOURCE_CHARS + 1)
        with caplog.at_level(logging.WARNING, logger=mod.logger.name):
            elapsed = _took(lambda: accumulating_container_ids(page, ("rows",)))
            assert accumulating_container_ids(page, ("rows",)) == ()
        assert elapsed < 0.5
        assert any("too large" in r.getMessage() for r in caplog.records)

    def test_a_scan_that_outlasts_its_budget_accumulates_nothing(self, monkeypatch, caplog):
        import logging

        from djust import _accumulating_containers as mod

        monkeypatch.setattr(mod, "SCAN_BUDGET_SECONDS", 0.0)
        with caplog.at_level(logging.WARNING, logger=mod.logger.name):
            assert accumulating_container_ids(container("{{ rows }}"), ("rows",)) == ()
        assert any("took more than" in r.getMessage() for r in caplog.records)

    def test_markup_nested_absurdly_deep_accumulates_nothing(self, caplog):
        import logging

        from djust import _accumulating_containers as mod

        page = container("{{ rows }}" + "<b>" * (mod.MAX_ELEMENT_DEPTH + 1))
        with caplog.at_level(logging.WARNING, logger=mod.logger.name):
            assert accumulating_container_ids(page, ("rows",)) == ()
        assert any("nests more than" in r.getMessage() for r in caplog.records)

    def test_many_unmatched_end_tags_over_a_deep_stack_are_bounded(self):
        page = container("{{ rows }}" + "<b>" * 900 + "</i>" * 60000)
        assert _took(lambda: accumulating_container_ids(page, ("rows",))) < self.BUDGET

    def test_a_with_alias_per_container_is_bounded(self):
        n = 5000
        page = "".join(
            f'<ul id="a{i}" dj-update="append">{{% with x{i}=rows %}}' for i in range(n)
        ) + "".join("</ul>{% endwith %}" for _ in range(n))
        assert _took(lambda: accumulating_container_ids(page, ("rows",))) < self.BUDGET

    def test_a_large_ordinary_page_is_scanned_in_linear_time(self):
        rows = "".join(f'<li id="r{i}">{{{{ r.v{i} }}}}</li>' for i in range(4000))
        page = container("{% for r in rows %}" + rows + "{% endfor %}")
        assert accumulating_container_ids(page, ("rows",)) == ("list",)
        assert _took(lambda: accumulating_container_ids(page, ("rows",))) < self.BUDGET


class _Page(LiveView):
    temporary_assigns = {"rows": []}
    template = container("{% for r in rows %}x{% endfor %}")


class TestATemplateEditIsSeen:
    def test_the_cached_ids_are_dropped_by_the_template_watcher(self):
        from djust.hot_view_replacement import note_template_change

        class Edited(_Page):
            template = container("{% for r in rows %}x{% endfor %}")

        assert view_accumulating_container_ids(Edited()) == ("list",)
        Edited.template = container("{% for r in items %}x{% endfor %}")
        # Still the stale answer: per class, until the watcher says a file changed.
        assert view_accumulating_container_ids(Edited()) == ("list",)
        note_template_change("/project/templates/page.html")
        assert view_accumulating_container_ids(Edited()) == ()

    def test_an_edit_of_an_included_file_is_seen_too(self, tmp_path):
        from djust.hot_view_replacement import note_template_change

        class WithInclude(_Page):
            template = container('{% include "row.html" %}')

        with template_files(tmp_path, {"row.html": "{% for r in items %}x{% endfor %}"}):
            assert view_accumulating_container_ids(WithInclude()) == ()
            (tmp_path / "row.html").write_text("{% for r in rows %}x{% endfor %}")
            note_template_change(str(tmp_path / "row.html"))
            assert view_accumulating_container_ids(WithInclude()) == ("list",)

    def test_a_subclass_cache_is_dropped_too(self):
        from djust.hot_view_replacement import note_template_change

        class Base(_Page):
            template = container("{{ rows }}")

        class Child(Base):
            pass

        assert view_accumulating_container_ids(Child()) == ("list",)
        Child.template = container("{{ items }}")
        note_template_change("x.html")
        assert view_accumulating_container_ids(Child()) == ()


class TestTheScanNeverRaises:
    def test_a_template_that_cannot_be_read_accumulates_nothing_and_says_so(self, caplog):
        import logging

        class Broken(_Page):
            def get_template(self):
                raise RuntimeError("no such template")

        with caplog.at_level(logging.WARNING):
            assert view_accumulating_container_ids(Broken()) == ()
        assert any("Broken" in r.getMessage() for r in caplog.records)

    def test_a_loader_that_raises_is_a_missing_include(self, monkeypatch):
        import django.template.loader as loader

        def boom(name, *args, **kwargs):
            raise RuntimeError("loader down")

        monkeypatch.setattr(loader, "get_template", boom)
        found = accumulating_container_ids(container('{% include "nope.html" %}'), ("rows",))
        assert found == ("list",)

    def test_a_failure_inside_the_scan_accumulates_nothing_and_says_so(self, monkeypatch, caplog):
        import logging

        from djust import _accumulating_containers as mod

        def boom(self):
            raise RuntimeError("scan blew up")

        monkeypatch.setattr(mod._Scan, "containers", boom)
        with caplog.at_level(logging.WARNING, logger=mod.logger.name):
            assert accumulating_container_ids(container("{{ rows }}"), ("rows",)) == ()
        assert any("scan failed" in r.getMessage() for r in caplog.records)

    def test_garbage_in_gives_a_tuple_out(self):
        for junk in (
            "",
            "{{",
            "<ul dj-update=",
            "\x00\x01",
            "<ul id='a' dj-update='append'>{% with %}{% endwith %}",
        ):
            assert isinstance(accumulating_container_ids(junk, ("rows",)), tuple)


# ---------------------------------------------------------------------------
# Context keys that are the temporary attribute under another name
# ---------------------------------------------------------------------------


class _Renamed(LiveView):
    temporary_assigns = {"rows": []}
    template = container("{% for r in items %}x{% endfor %}")


class TestRenamedContextKeys:
    def test_a_context_key_holding_the_temporary_object_is_an_alias(self):
        view = _Renamed()
        view.rows = []
        assert view_accumulating_container_ids(view, aliases=("items",)) == ("list",)

    def test_without_the_alias_the_container_is_not_fed_by_it(self):
        view = _Renamed()
        assert view_accumulating_container_ids(view) == ()

    def test_aliases_are_found_by_identity_in_the_context(self):
        from djust.mixins.rust_bridge import _temporary_key_aliases

        view = _Renamed()
        view.rows = [1, 2]
        context = {
            "items": view.rows,
            "copy": list(view.rows),
            "rows": view.rows,
            "n": 3,
            "same_value_other_object": [1, 2],
        }
        assert _temporary_key_aliases(view, context) == ("items",)

    def test_an_empty_default_is_not_confused_with_another_empty_list(self):
        from djust.mixins.rust_bridge import _temporary_key_aliases

        view = _Renamed()
        view.rows = []
        assert _temporary_key_aliases(view, {"other": [], "items": view.rows}) == ("items",)

    def test_a_view_without_temporary_assigns_has_no_aliases(self):
        from djust.mixins.rust_bridge import _temporary_key_aliases

        assert _temporary_key_aliases(LiveView(), {"a": []}) == ()


# ---------------------------------------------------------------------------
# Over a real WebSocket
# ---------------------------------------------------------------------------

MODULE = __name__

pytestmark_async = [pytest.mark.django_db, pytest.mark.asyncio]


@dataclass
class Row:
    id: int
    body: str


ROWS = [Row(i, f"BODY{i}") for i in range(5)]


def _wrap(view: str, inner: str, before: str = "", after: str = "") -> str:
    return (
        f'<div dj-view="{MODULE}.{view}" dj-id="0">{before}'
        f'<div id="list" dj-update="append">{inner}</div>{after}|{{{{ k }}}}</div>'
    )


class _Base(LiveView):
    temporary_assigns = {"rows": []}

    def mount(self, request, **kwargs):
        self.rows = list(ROWS)
        self.items = list(ROWS)
        self.k = 0

    @event_handler()
    def add(self, **kwargs):
        self.k += 1
        self.rows = [Row(100 + self.k, f"NEW{self.k}")]

    @event_handler()
    def remove_first(self, **kwargs):
        self.items = self.items[1:]

    @event_handler()
    def edit_first(self, **kwargs):
        self.items = [Row(self.items[0].id, "EDITED"), *self.items[1:]]


PLAIN_ROWS = '{% for r in items %}<div id="rows-{{ r.id }}">{{ r.body }}</div>{% endfor %}'
TEMP_ROWS = '{% for r in rows %}<div id="rows-{{ r.id }}">{{ r.body }}</div>{% endfor %}'
FILES = {"plain_rows.html": PLAIN_ROWS, "temp_rows.html": TEMP_ROWS}


class IncludesAPlainList(_Base):
    template = _wrap("IncludesAPlainList", '{% include "plain_rows.html" %}')


class IncludesTheTemporaryList(_Base):
    template = _wrap("IncludesTheTemporaryList", '{% include "temp_rows.html" %}')


class AliasOutsideTheContainer(_Base):
    template = (
        f'<div dj-view="{MODULE}.AliasOutsideTheContainer" dj-id="0">'
        "{% with shown=rows %}"
        '<div id="list" dj-update="append">'
        '{% for r in shown %}<div id="rows-{{ r.id }}">{{ r.body }}</div>{% endfor %}'
        "</div>{% endwith %}|{{ k }}</div>"
    )


class RenamedByGetContextData(_Base):
    template = _wrap(
        "RenamedByGetContextData",
        '{% for r in feed %}<div id="rows-{{ r.id }}">{{ r.body }}</div>{% endfor %}',
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["feed"] = self.rows
        return context


class ComparisonBeforeDjUpdate(_Base):
    template = (
        f'<div dj-view="{MODULE}.ComparisonBeforeDjUpdate" dj-id="0">'
        '<div {% if k > 100 %}class="x"{% endif %} id="list" dj-update="append">'
        + TEMP_ROWS
        + "</div>|{{ k }}</div>"
    )


class NestedOuterPlain(_Base):
    """The outer list is an ordinary attribute; only the inner one reads ``rows``."""

    template = (
        f'<div dj-view="{MODULE}.NestedOuterPlain" dj-id="0">'
        '<div id="outer" dj-update="append">'
        '<div id="inner" dj-update="append">' + TEMP_ROWS + "</div>"
        '{% for r in items %}<div id="items-{{ r.id }}">{{ r.body }}</div>{% endfor %}'
        "</div>|{{ k }}</div>"
    )


class DuplicateIdsPrepend(_Base):
    """A prepend container whose mount rows repeat an ``id``."""

    template = _wrap("DuplicateIdsPrepend", TEMP_ROWS).replace(
        'dj-update="append"', 'dj-update="prepend"'
    )

    def mount(self, request, **kwargs):
        self.rows = [Row(0, "a"), Row(0, "b"), Row(1, "c")]
        self.k = 0

    @event_handler()
    def add_pair(self, **kwargs):
        """A batch whose rows repeat an ``id``."""
        self.k += 1
        base = 500 + 10 * self.k
        self.rows = [Row(base, "p1"), Row(base, "p2"), Row(base + 1, "p3")]


def kinds(frame):
    return [p["type"] for p in frame.get("patches") or []]


async def drive(view, events, files=None, tmp_path=None):
    if files is None:
        return await _drive_over_a_real_socket(view, events, module=MODULE)
    with template_files(tmp_path, files):
        return await _drive_over_a_real_socket(view, events, module=MODULE)


@pytest.mark.django_db
@pytest.mark.asyncio
class TestOverARealSocket:
    async def test_a_plain_list_in_an_include_propagates_a_removal(self, tmp_path):
        (frame,) = await drive("IncludesAPlainList", ["remove_first"], FILES, tmp_path)
        assert "RemoveChild" in kinds(frame), frame

    async def test_a_plain_list_in_an_include_propagates_an_edit(self, tmp_path):
        (frame,) = await drive("IncludesAPlainList", ["edit_first"], FILES, tmp_path)
        assert "EDITED" in str(frame["patches"]), frame

    async def test_a_temporary_list_in_an_include_still_appends(self, tmp_path):
        (frame,) = await drive("IncludesTheTemporaryList", ["add"], FILES, tmp_path)
        assert [p["index"] for p in frame["patches"] if p["type"] == "InsertChild"] == [5], frame
        assert "RemoveChild" not in kinds(frame), frame

    async def test_an_alias_outside_the_container_still_appends(self):
        (frame,) = await drive("AliasOutsideTheContainer", ["add"])
        assert [p["index"] for p in frame["patches"] if p["type"] == "InsertChild"] == [5], frame
        assert "RemoveChild" not in kinds(frame), frame

    async def test_a_key_renamed_in_get_context_data_still_appends(self):
        frames = await drive("RenamedByGetContextData", ["add", "add"])
        for n, frame in enumerate(frames):
            assert [p["index"] for p in frame["patches"] if p["type"] == "InsertChild"] == [
                5 + n
            ], frame
            assert "RemoveChild" not in kinds(frame), frame

    async def test_a_comparison_in_the_container_tag_still_appends(self):
        (frame,) = await drive("ComparisonBeforeDjUpdate", ["add"])
        assert [p["index"] for p in frame["patches"] if p["type"] == "InsertChild"] == [5], frame
        assert "RemoveChild" not in kinds(frame), frame

    async def test_the_outer_list_of_a_nested_pair_propagates_a_removal(self):
        """``items`` rows are the outer container's own: removing one must reach the page."""
        (frame,) = await drive("NestedOuterPlain", ["remove_first"])
        assert "RemoveChild" in kinds(frame), frame

    async def test_the_inner_list_of_a_nested_pair_still_appends(self):
        (frame,) = await drive("NestedOuterPlain", ["add"])
        assert kinds(frame).count("InsertChild") == 1, frame
        assert "RemoveChild" not in kinds(frame), frame

    async def test_a_prepend_container_with_repeated_ids_takes_one_insert_per_row(self):
        """``MoveChild`` for the rows that repeat an id: the client answered
        "1/N patches failed". Each new row is an insert at the front instead."""
        frames = await drive("DuplicateIdsPrepend", ["add", "add_pair", "add"])
        for n, frame in enumerate(frames):
            assert "MoveChild" not in kinds(frame), (n, frame)
            assert "RemoveChild" not in kinds(frame), (n, frame)
        assert [p["index"] for p in frames[0]["patches"] if p["type"] == "InsertChild"] == [0]
        assert [p["index"] for p in frames[1]["patches"] if p["type"] == "InsertChild"] == [0, 1, 2]


class TestTheDocumentedNumbers:
    """BEST_PRACTICES.md states these limits; the guide and the module must agree."""

    def test_limits(self):
        from djust import _accumulating_containers as mod

        assert mod.MAX_INCLUDE_DEPTH == 8
        assert mod.MAX_INCLUDED_TEMPLATES == 64
        assert mod.MAX_SOURCE_CHARS == 1_000_000
        assert mod.MAX_ELEMENT_DEPTH == 1000
        assert mod.SCAN_BUDGET_SECONDS == 2.0
        assert mod.MAX_ALIASES == 64
