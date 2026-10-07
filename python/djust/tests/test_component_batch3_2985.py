"""ActivityFeed, Terminal and Tour ship their interactions (#2985, batch 3).

Each component rendered a ``dj-hook`` that no shipped script answered; each
now has a script under ``djust_components/``. What has to hold on every render
path (the component class, the Django template tag and the Rust-engine
handler) is that the structure the scripts read is still emitted, that the two
additive attributes the scripts need (``data-max-items`` on a streaming feed,
``data-line-numbers`` / ``data-max-lines`` on a terminal) appear identically
everywhere, and that the catalogue finds each script.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.template import Context, Template

from djust.components import rust_handlers as rh
from djust.components.components.activity_feed import ActivityFeed
from djust.components.components.terminal import Terminal
from djust.components.components.tour import Tour

STATIC = Path(rh.__file__).resolve().parent / "static" / "djust_components"

EVENTS = [
    {"user": "Alice Cooper", "action": "commented on", "target": "Issue #42", "time": "2m ago"},
    {"user": "Bob", "action": "merged", "avatar": "/img/b.png", "icon": "*"},
]
STEPS = [
    {"target": "#a", "title": "One", "content": "First."},
    {"target": "#b", "title": "Two", "content": "Second."},
    {"target": "#c", "title": "Three", "content": "Third."},
]
OUTPUT = ["$ ls", "\x1b[32mok\x1b[0m done"]


def _tag(source: str, **context) -> str:
    return Template("{% load djust_components %}" + source).render(Context(context))


def _feed_paths(**kw) -> dict[str, str]:
    max_items = kw.get("max_items", 50)
    return {
        "class": ActivityFeed(events=EVENTS, stream_event="act", max_items=max_items).render(),
        "tag": _tag(
            f'{{% activity_feed events=events stream="act" max={max_items} %}}', events=EVENTS
        ),
        "handler": str(
            rh.ActivityFeedHandler().render(
                ["events=events", "stream='act'", f"max={max_items}"], {"events": EVENTS}
            )
        ),
    }


def _terminal_paths(**kw) -> dict[str, str]:
    numbers = kw.get("show_line_numbers", False)
    max_lines = kw.get("max_lines", 0)
    auto_scroll = kw.get("auto_scroll", True)
    return {
        "class": Terminal(
            output=OUTPUT,
            title="Build",
            stream_event="out",
            show_line_numbers=numbers,
            max_lines=max_lines,
            auto_scroll=auto_scroll,
        ).render(),
        "tag": _tag(
            "{% terminal output=output title='Build' stream_event='out' "
            f"show_line_numbers={numbers} max_lines={max_lines} auto_scroll={auto_scroll} %}}",
            output=OUTPUT,
        ),
        "handler": str(
            rh.TerminalHandler().render(
                [
                    "output=output",
                    "title='Build'",
                    "stream_event='out'",
                    f"show_line_numbers={numbers}",
                    f"max_lines={max_lines}",
                    f"auto_scroll={auto_scroll}",
                ],
                {"output": OUTPUT},
            )
        ),
    }


def _tour_paths(active: int = 1) -> dict[str, str]:
    return {
        "class": Tour(steps=STEPS, active=active).render(),
        "tag": _tag(f"{{% tour steps=steps active={active} %}}", steps=STEPS),
        "handler": str(
            rh.TourHandler().render(["steps=steps", f"active={active}"], {"steps": STEPS})
        ),
    }


class TestActivityFeedMarkup:
    def test_a_streaming_feed_tells_the_hook_how_many_rows_to_keep(self):
        for path, html in _feed_paths(max_items=7).items():
            assert 'dj-hook="ActivityFeed"' in html, path
            assert 'data-max-items="7"' in html, path
            assert 'data-stream-event="act"' in html, path

    def test_a_feed_that_does_not_stream_has_no_hook_and_no_extra_attribute(self):
        assert "data-max-items" not in ActivityFeed(events=EVENTS).render()
        assert "dj-hook" not in ActivityFeed(events=EVENTS).render()
        assert "data-max-items" not in _tag("{% activity_feed events=events %}", events=EVENTS)

    def test_the_structure_the_hook_reads_and_copies(self):
        for path, html in _feed_paths().items():
            assert 'role="feed"' in html and 'aria-label="Activity feed"' in html, path
            assert html.count('class="dj-activity-feed__item" role="article"') == 2, path
            for cls in (
                "dj-activity-feed__avatar-initials",
                "dj-activity-feed__avatar-img",
                "dj-activity-feed__text",
                "dj-activity-feed__user",
                "dj-activity-feed__target",
                "dj-activity-feed__time",
                "dj-activity-feed__icon",
            ):
                assert f'class="{cls}"' in html, (path, cls)
            assert '<span class="dj-activity-feed__avatar-initials">AC</span>' in html, path

    def test_the_three_render_paths_agree(self):
        paths = _feed_paths()
        assert paths["class"] == paths["tag"] == paths["handler"]


class TestTerminalMarkup:
    def test_the_hook_is_told_how_to_render_a_streamed_line(self):
        for path, html in _terminal_paths(show_line_numbers=True, max_lines=100).items():
            assert 'data-line-numbers="true"' in html, path
            assert 'data-max-lines="100"' in html, path
            assert 'data-stream-event="out"' in html, path

    def test_no_options_no_attributes(self):
        for path, html in _terminal_paths().items():
            assert "data-line-numbers" not in html, path
            assert "data-max-lines" not in html, path

    @pytest.mark.parametrize("bad", [-5, "abc", None])
    def test_a_junk_max_lines_adds_nothing(self, bad):
        assert "data-max-lines" not in Terminal(output=OUTPUT, max_lines=bad).render()

    def test_the_structure_the_hook_reads(self):
        for path, html in _terminal_paths(show_line_numbers=True).items():
            assert 'dj-hook="Terminal"' in html, path
            assert 'class="dj-terminal__body"' in html, path
            assert 'class="dj-terminal__title"' in html, path
            assert '<span class="dj-terminal__line-num">1</span>' in html, path
            assert '<span class="dj-terminal__text">$ ls</span>' in html, path
            assert '<span style="color:#2ecc71">ok</span>' in html, path

    def test_the_three_render_paths_agree(self):
        paths = _terminal_paths(show_line_numbers=True, max_lines=100)
        assert paths["class"] == paths["tag"] == paths["handler"]

    def test_auto_scroll_false_is_the_only_thing_that_adds_an_attribute(self):
        for path, html in _terminal_paths(auto_scroll=False).items():
            assert 'data-auto-scroll="false"' in html, path
        for path, html in _terminal_paths().items():
            assert "data-auto-scroll" not in html, path  # the default markup is unchanged

    def test_auto_scroll_agrees_on_the_three_render_paths(self):
        paths = _terminal_paths(show_line_numbers=True, max_lines=100, auto_scroll=False)
        assert paths["class"] == paths["tag"] == paths["handler"]

    def test_max_lines_is_additive_and_defaults_to_the_old_behaviour(self):
        assert Terminal(output=OUTPUT).max_lines == 0


class TestTourMarkup:
    def test_the_structure_the_hook_reads(self):
        for path, html in _tour_paths().items():
            assert 'dj-hook="Tour"' in html, path
            assert 'data-target="#b"' in html and 'data-step="1"' in html, path
            assert 'data-total="3"' in html and 'role="dialog" aria-modal="true"' in html, path
            for cls in (
                "dj-tour__overlay",
                "dj-tour__popover",
                "dj-tour__title",
                "dj-tour__content",
                "dj-tour__skip",
                "dj-tour__prev",
                "dj-tour__next",
            ):
                assert f'class="{cls}"' in html, (path, cls)

    def test_the_hook_presses_buttons_that_are_event_sources(self):
        """Next, Back and Skip carry the events the hook triggers by clicking them."""
        for path, html in _tour_paths().items():
            for cls in ("skip", "prev", "next"):
                button = re.search(rf'<button class="dj-tour__{cls}"[^>]*>', html)
                assert button, (path, cls)
                assert "dj-click" in button.group(0) or "data-dj-" in button.group(0), (path, cls)

    def test_the_last_step_offers_finish_and_no_skip(self):
        for path, html in _tour_paths(active=2).items():
            assert ">Finish</button>" in html, path
            assert "dj-tour__skip" not in html, path

    def test_an_empty_tour_renders_nothing_so_the_hook_ends_with_it(self):
        assert Tour(steps=[]).render() == ""

    def test_the_three_render_paths_agree_on_what_the_hook_reads(self):
        for active in (0, 1, 2):
            paths = _tour_paths(active)
            for key in ("class", "tag", "handler"):
                html = paths[key]
                assert f'data-step="{active}"' in html
                assert re.findall(r'class="(dj-tour__[a-z-]+)"', html) == re.findall(
                    r'class="(dj-tour__[a-z-]+)"', paths["class"]
                )


@pytest.mark.parametrize(
    "component, hook, script",
    [
        ("activity_feed", "ActivityFeed", "activity-feed.js"),
        ("terminal", "Terminal", "terminal.js"),
        ("tour", "Tour", "tour.js"),
    ],
)
class TestScriptsAreShippedAndFound:
    def test_the_catalogue_reports_the_script_and_the_hook(self, component, hook, script):
        from djust.theming.gallery.component_registry import component_client

        assert component_client(component) == {
            "hook": hook,
            "script": f"djust_components/{script}",
            "hook_shipped": True,
        }

    def test_the_script_registers_without_replacing_an_app_hook(self, component, hook, script):
        source = (STATIC / script).read_text()
        assert f"window.DjustHooks.{hook} = " in source
        assert f"!appHooks.{hook} && !window.DjustHooks.{hook}" in source

    def test_the_script_never_writes_html_from_data(self, component, hook, script):
        """Names, lines and step text reach the page as text, attributes or CSSOM only."""
        source = (STATIC / script).read_text()
        for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
            assert sink not in source, sink
        assert "console." not in source
