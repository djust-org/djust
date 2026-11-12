"""Accessibility and strict-CSP markup of five component tags (#3971, #3970, #3969, #3961).

Each case renders through EVERY renderer the tag has, because every one of these bugs was
present in more than one place (the first HTTP render uses the Django tag, every socket render
uses the Rust handler, and ``Stepper`` / ``Progress`` / ``StatusIndicator`` also exist as
classes):

* ``{% status_indicator %}`` was a polite live region on every instance (#3971).
* ``{% stepper %}`` was ``<div dj-click>`` steps under the Rust engine: not focusable, no
  role, no current step, no list (#3970).
* ``{% progress %}`` drew its fill with ``style="width:N%"``, which a ``style-src`` without
  ``'unsafe-inline'`` refuses, and the Rust engine's bar had no ``progressbar`` role (#3969).
* ``{% dj_radio_group %}`` is the group the single ``{% dj_radio %}`` never had, and
  ``{% live_input type="radio" %}`` rendered loose radios (#3961).

The assertions parse the markup instead of matching substrings, so a moved attribute or a
re-nested element is a failure and not a pass.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest
from django import forms
from django.template import Context, Engine
from django.utils.safestring import mark_safe

import djust.components
from djust.components.components.progress import Progress
from djust.components.components.status_indicator import StatusIndicator
from djust.components.components.stepper import Stepper
from djust.templatetags.live_tags import live_input
from djust.tests.test_components_csp_aria import _rust

_ENGINE = Engine(
    libraries={
        "djust_components": "djust.components.templatetags.djust_components",
        "live_tags": "djust.templatetags.live_tags",
    },
    dirs=[str(Path(djust.components.__file__).parent / "templates")],
)

CSS = (
    Path(djust.components.__file__).parent / "static" / "djust_components" / "components.css"
).read_text()


def _django(src: str, ctx: dict | None = None) -> str:
    return _ENGINE.from_string("{% load djust_components %}" + src).render(Context(ctx or {}))


ENGINES = {"django": _django, "rust": _rust}


class Node:
    def __init__(self, tag: str, attrs: dict[str, str | None], parent: "Node | None") -> None:
        self.tag = tag
        self.attrs = attrs
        self.parent = parent
        self.children: list[Node] = []
        self.content: list[str | Node] = []

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()

    def find_all(self, tag: str) -> list["Node"]:
        return [n for n in self.walk() if n.tag == tag]

    def ancestors(self):
        node = self.parent
        while node is not None:
            yield node
            node = node.parent

    def all_text(self) -> str:
        """The text in document order, as an accessible name reads it."""
        return "".join(c if isinstance(c, str) else c.all_text() for c in self.content)

    def cls(self) -> list[str]:
        return (self.attrs.get("class") or "").split()


_VOID = {"input", "br", "hr", "img", "meta", "link"}


class _Builder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", {}, None)
        self.cur = self.root

    def handle_starttag(self, tag, attrs):
        node = Node(tag, dict(attrs), self.cur)
        self.cur.children.append(node)
        self.cur.content.append(node)
        if tag not in _VOID:
            self.cur = node

    def handle_endtag(self, tag):
        node = self.cur
        while node is not self.root and node.tag != tag:
            node = node.parent
        if node is not self.root:
            self.cur = node.parent

    def handle_data(self, data):
        self.cur.content.append(data)


def parse(markup: str) -> Node:
    builder = _Builder()
    builder.feed(markup)
    builder.close()
    return builder.root


def no_inline_style(markup: str) -> None:
    """The page's ``style-src`` may not carry ``'unsafe-inline'``: no ``style`` ATTRIBUTE."""
    styled = [n.tag for n in parse(markup).walk() if "style" in n.attrs]
    assert not styled, f"elements with an inline style attribute: {styled} in: {markup}"


# ---------------------------------------------------------------------------
# {% status_indicator %} (#3971)
# ---------------------------------------------------------------------------


class TestStatusIndicator:
    SRC = '{% status_indicator status="online" label="Done" %}'

    @pytest.mark.parametrize("engine", ENGINES)
    def test_a_static_label_is_not_a_live_region(self, engine):
        out = ENGINES[engine](self.SRC)
        root = parse(out)
        (indicator,) = [n for n in root.walk() if "dj-status-indicator" in n.cls()]
        assert "role" not in indicator.attrs, out
        assert not [n for n in root.walk() if "aria-live" in n.attrs], out
        assert "Done" in indicator.all_text()

    @pytest.mark.parametrize("engine", ENGINES)
    @pytest.mark.parametrize("flag", ["True", "1", '"yes"'])
    def test_live_opts_in_to_a_status_region(self, engine, flag):
        out = ENGINES[engine](
            '{% status_indicator status="online" label="Done" live=' + flag + " %}"
        )
        (indicator,) = [n for n in parse(out).walk() if "dj-status-indicator" in n.cls()]
        assert indicator.attrs.get("role") == "status", out

    @pytest.mark.parametrize("engine", ENGINES)
    @pytest.mark.parametrize("flag", ["False", "false", '""'])
    def test_a_false_live_is_not_a_live_region(self, engine, flag):
        out = ENGINES[engine](
            '{% status_indicator status="online" label="Done" live=' + flag + " %}"
        )
        (indicator,) = [n for n in parse(out).walk() if "dj-status-indicator" in n.cls()]
        assert "role" not in indicator.attrs, out

    @pytest.mark.parametrize("engine", ENGINES)
    def test_one_indicator_per_row_makes_no_live_regions(self, engine):
        rows = ENGINES[engine](self.SRC) * 25
        assert not [n for n in parse(rows).walk() if n.attrs.get("role") == "status"]

    def test_the_two_engines_agree(self):
        for extra in ("", " pulse=True", ' size="sm"', " live=True"):
            src = '{% status_indicator status="degraded" label="API"' + extra + " %}"
            assert _django(src) == _rust(src), src

    def test_the_component_class_is_not_a_live_region_either(self):
        out = StatusIndicator(status="online", label="API")._render_custom()
        assert not [n for n in parse(out).walk() if "role" in n.attrs], out


# ---------------------------------------------------------------------------
# {% stepper %} (#3970)
# ---------------------------------------------------------------------------

STEPS = [{"label": "Account"}, {"label": "Profile", "complete": True}, {"label": "Review"}]


def _stepper_markup(renderer: str) -> str:
    if renderer == "class":
        return Stepper(steps=STEPS, active=2)._render_custom()
    return ENGINES[renderer]("{% stepper steps=steps active=2 %}", {"steps": STEPS})


STEPPER_RENDERERS = ["django", "rust", "class"]


class TestStepper:
    @pytest.mark.parametrize("renderer", STEPPER_RENDERERS)
    def test_the_steps_are_an_ordered_list(self, renderer):
        root = parse(_stepper_markup(renderer))
        (lst,) = root.find_all("ol")
        assert "stepper" in lst.cls()
        items = [c for c in lst.children]
        assert [c.tag for c in items] == ["li", "li", "li"]
        assert all("stepper-step" in c.cls() for c in items)

    @pytest.mark.parametrize("renderer", STEPPER_RENDERERS)
    def test_every_step_is_keyboard_operable(self, renderer):
        """A step is a real button: focusable, Enter and Space activate it, and it never
        submits a form it sits in."""
        root = parse(_stepper_markup(renderer))
        steps = root.find_all("li")
        buttons = root.find_all("button")
        assert len(buttons) == len(steps) == 3
        for button in buttons:
            assert button.attrs.get("type") == "button"
            assert button.attrs.get("dj-click") == "set_step"
            assert button.parent.tag == "li"
        # nothing that carries a click handler is a non-interactive element
        assert [n.tag for n in root.walk() if "dj-click" in n.attrs] == ["button"] * 3

    @pytest.mark.parametrize("renderer", STEPPER_RENDERERS)
    def test_the_current_step_is_marked_and_only_that_one(self, renderer):
        root = parse(_stepper_markup(renderer))
        current = [n for n in root.walk() if n.attrs.get("aria-current") == "step"]
        assert len(current) == 1
        (li,) = [n for n in root.find_all("li") if "stepper-step-active" in n.cls()]
        assert li.attrs.get("aria-current") == "step" or any(
            c.attrs.get("aria-current") == "step" for c in li.walk()
        )
        assert li.find_all("button")[0].attrs.get("aria-current") == "step"

    @pytest.mark.parametrize("renderer", STEPPER_RENDERERS)
    def test_a_completed_step_says_so_in_text(self, renderer):
        """The state is text, not only a colour: a step wears the complete class exactly when
        its text says so (the current step, which is not "done" yet, never does).

        Which steps count as complete is the engines' own rule (the Rust handler also counts
        every step before the current one, #3663); ``Profile`` is complete in all three.
        """
        root = parse(_stepper_markup(renderer))
        steps = root.find_all("li")
        said = []
        for li in steps:
            complete = "stepper-step-complete" in li.cls()
            current = "stepper-step-active" in li.cls()
            says = "completed" in li.all_text().lower()
            assert says == (complete and not current), (li.cls(), li.all_text())
            said.append((li.all_text().strip(), says))
        assert any("Profile" in text and says for text, says in said), said
        assert not any("Review" in text and says for text, says in said), said

    @pytest.mark.parametrize("renderer", STEPPER_RENDERERS)
    def test_the_step_number_and_label_do_not_run_together(self, renderer):
        """Spans have no whitespace between them, so an accessible name reads ``1Account``
        unless a space separates the number from the label."""
        root = parse(_stepper_markup(renderer))
        first = root.find_all("button")[0]
        assert re.search(r"1\s+Account", first.all_text()), first.all_text()

    @pytest.mark.parametrize("renderer", STEPPER_RENDERERS)
    def test_no_block_element_sits_inside_a_button(self, renderer):
        root = parse(_stepper_markup(renderer))
        for button in root.find_all("button"):
            inner = {n.tag for n in button.walk()} - {"button"}
            assert inner <= {"span"}, inner

    def test_the_two_engines_agree_on_the_structure(self):
        """Tag names, roles and dj- wiring match; the classes of the inner spans are the
        template-library parity issue (#3663), not this one."""

        def shape(markup):
            return [
                (n.tag, n.attrs.get("type"), n.attrs.get("dj-click"), n.attrs.get("aria-current"))
                for n in parse(markup).walk()
                if n.tag in ("ol", "li", "button")
            ]

        steps = [{"label": "Account"}, {"label": "Profile"}, {"label": "Review"}]
        ctx = {"steps": steps}
        src = "{% stepper steps=steps active=1 %}"
        assert shape(_django(src, ctx)) == shape(_rust(src, ctx))

    @pytest.mark.parametrize("engine", ENGINES)
    def test_a_hostile_label_is_escaped(self, engine):
        evil = '<img src=x onerror="alert(1)">'
        out = ENGINES[engine]("{% stepper steps=steps %}", {"steps": [{"label": evil}]})
        assert "<img" not in out and "&lt;img" in out

    @pytest.mark.parametrize("engine", ENGINES)
    def test_no_steps_renders_an_empty_list(self, engine):
        out = ENGINES[engine]("{% stepper steps=steps %}", {"steps": []})
        assert not parse(out).find_all("button")


# ---------------------------------------------------------------------------
# {% progress %} (#3969)
# ---------------------------------------------------------------------------


def _progress_markup(renderer: str, value: object = 40, label: str = "Uploading") -> str:
    if renderer == "class":
        return Progress(value=value, label=label).render()  # type: ignore[arg-type]
    src = '{% progress value=v label="' + label + '" %}'
    return ENGINES[renderer](src, {"v": value})


PROGRESS_RENDERERS = ["django", "rust", "class"]
WIDTH_CLASS = re.compile(r"^dj-progress-w-(\d+)$")


def _fill(root: Node) -> Node:
    fills = [
        n
        for n in root.walk()
        if any(c in ("progress-bar", "dj-progress__fill", "dj-progress-bar") for c in n.cls())
    ]
    assert len(fills) == 1, [n.cls() for n in fills]
    return fills[0]


class TestProgress:
    @pytest.mark.parametrize("renderer", PROGRESS_RENDERERS)
    def test_no_inline_style_so_a_strict_style_src_still_draws_the_bar(self, renderer):
        no_inline_style(_progress_markup(renderer))

    @pytest.mark.parametrize("renderer", PROGRESS_RENDERERS)
    @pytest.mark.parametrize("value", [0, 1, 40, 99, 100])
    def test_the_fill_width_is_a_class_that_names_the_value(self, renderer, value):
        fill = _fill(parse(_progress_markup(renderer, value)))
        widths = [WIDTH_CLASS.match(c) for c in fill.cls()]
        (width,) = [m for m in widths if m]
        assert int(width.group(1)) == value

    @pytest.mark.parametrize("renderer", PROGRESS_RENDERERS)
    @pytest.mark.parametrize(("given", "drawn"), [(-5, 0), (150, 100)])
    def test_the_width_is_clamped(self, renderer, given, drawn):
        fill = _fill(parse(_progress_markup(renderer, given)))
        assert f"dj-progress-w-{drawn}" in fill.cls()

    def test_every_width_class_a_bar_can_render_has_a_rule(self):
        """101 classes, derived from the clamp's range: a value with no rule draws an empty bar."""
        for n in range(0, 101):
            assert re.search(rf"\.dj-progress-w-{n}\s*\{{\s*width:\s*{n}%;?\s*\}}", CSS), n
        assert not re.search(r"\.dj-progress-w-(10[1-9]|1[1-9]\d|[2-9]\d\d)\b", CSS)

    @pytest.mark.parametrize("renderer", PROGRESS_RENDERERS)
    def test_the_bar_exposes_a_role_a_value_and_a_name(self, renderer):
        out = _progress_markup(renderer, 40)
        bars = [n for n in parse(out).walk() if n.attrs.get("role") == "progressbar"]
        assert len(bars) == 1, out
        bar = bars[0]
        assert float(bar.attrs["aria-valuenow"]) == 40
        assert bar.attrs["aria-valuemin"] == "0"
        assert float(bar.attrs["aria-valuemax"]) == 100
        assert bar.attrs.get("aria-label") == "Uploading"

    @pytest.mark.parametrize("renderer", PROGRESS_RENDERERS)
    def test_an_unlabelled_bar_is_not_given_an_invented_name(self, renderer):
        """Named by its label only when there is one (the existing contract of the Python tag,
        ``test_no_aria_label_when_no_label``); no renderer makes one up."""
        if renderer == "class":
            out = Progress(value=40).render()
        else:
            out = ENGINES[renderer]("{% progress value=40 %}")
        (bar,) = [n for n in parse(out).walk() if n.attrs.get("role") == "progressbar"]
        assert "aria-label" not in bar.attrs

    @pytest.mark.parametrize("engine", ENGINES)
    def test_a_hostile_label_is_escaped_in_the_name(self, engine):
        out = ENGINES[engine]("{% progress value=40 label=l %}", {"l": '"><b>x</b>'})
        assert "<b>" not in out

    def test_the_class_rounds_a_fractional_percentage_to_a_whole_class(self):
        out = Progress(value=33.6).render()
        assert "dj-progress-w-34" in out
        assert 'aria-valuenow="33.6"' in out

    def test_the_class_clamps_a_value_over_its_max(self):
        out = Progress(value=250, max=200).render()
        assert "dj-progress-w-100" in out
        no_inline_style(out)

    @pytest.mark.parametrize(
        ("renderer", "value", "error"),
        [
            ("django", "abc", ValueError),
            ("django", None, TypeError),
            ("rust", "abc", ValueError),
            ("rust", None, TypeError),
            ("class", "abc", TypeError),
            ("class", None, TypeError),
        ],
    )
    def test_a_value_that_is_not_a_number_is_refused_not_drawn(self, renderer, value, error):
        """What every renderer does today, asserted per renderer: it RAISES, so no width class
        (and no width) is ever built from a non-numeric value. The tags share the tag's own
        ``int()`` clamp (#3667), the class divides by ``max``."""
        with pytest.raises(error):
            _progress_markup(renderer, value)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_a_numeric_string_is_drawn_as_its_whole_percent(self, engine):
        fill = _fill(parse(_progress_markup(engine, "50")))
        assert "dj-progress-w-50" in fill.cls()


# ---------------------------------------------------------------------------
# {% dj_radio_group %} and {% live_input "radio" %} (#3961)
# ---------------------------------------------------------------------------

PLANS = [
    {"value": "basic", "label": "Basic"},
    {"value": "pro plan", "label": "Pro"},
    {"value": "team", "label": "Team"},
]


def _ids(root: Node) -> list[str]:
    return [n.attrs["id"] for n in root.walk() if "id" in n.attrs]


class TestRadioGroup:
    SRC = '{% dj_radio_group name="plan" label="Plan" options=plans value="pro plan" %}'

    @pytest.mark.parametrize("engine", ENGINES)
    def test_the_group_is_a_named_radiogroup_fieldset(self, engine):
        root = parse(ENGINES[engine](self.SRC, {"plans": PLANS}))
        (group,) = root.find_all("fieldset")
        assert group.attrs["role"] == "radiogroup"
        assert "dj-radio-group" in group.cls()
        (legend,) = group.find_all("legend")
        assert group.attrs["aria-labelledby"] == legend.attrs["id"]
        assert legend.all_text().strip() == "Plan"

    @pytest.mark.parametrize("engine", ENGINES)
    def test_each_option_is_an_input_with_its_own_label_and_a_clean_id(self, engine):
        root = parse(ENGINES[engine](self.SRC, {"plans": PLANS}))
        inputs = root.find_all("input")
        labels = root.find_all("label")
        assert [i.attrs["value"] for i in inputs] == ["basic", "pro plan", "team"]
        assert [lb.all_text() for lb in labels] == ["Basic", "Pro", "Team"]
        for i, lb in zip(inputs, labels):
            assert i.attrs["type"] == "radio" and i.attrs["name"] == "plan"
            assert lb.attrs["for"] == i.attrs["id"]
            assert re.fullmatch(r"[A-Za-z0-9_-]+", i.attrs["id"]), i.attrs["id"]
        ids = _ids(root)
        assert len(ids) == len(set(ids)), ids
        assert all("dj-radio-option" in i.parent.cls() for i in inputs)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_only_the_current_value_is_checked(self, engine):
        root = parse(ENGINES[engine](self.SRC, {"plans": PLANS}))
        assert [i.attrs["value"] for i in root.find_all("input") if "checked" in i.attrs] == [
            "pro plan"
        ]

    @pytest.mark.parametrize("engine", ENGINES)
    def test_a_required_group_says_so_on_the_group_and_on_each_radio(self, engine):
        out = ENGINES[engine](
            '{% dj_radio_group name="plan" label="Plan" options=plans required=True %}',
            {"plans": PLANS},
        )
        root = parse(out)
        (group,) = root.find_all("fieldset")
        assert group.attrs.get("aria-required") == "true"
        assert all("required" in i.attrs for i in root.find_all("input"))

    @pytest.mark.parametrize("engine", ENGINES)
    def test_an_error_and_help_text_describe_the_group(self, engine):
        out = ENGINES[engine](
            '{% dj_radio_group name="plan" label="Plan" options=plans '
            'error="Pick one" helper="Billed monthly" %}',
            {"plans": PLANS},
        )
        root = parse(out)
        (group,) = root.find_all("fieldset")
        by_id = {n.attrs["id"]: n for n in root.walk() if "id" in n.attrs}
        described = group.attrs["aria-describedby"].split()
        assert len(described) == 2
        assert {by_id[i].all_text() for i in described} == {"Pick one", "Billed monthly"}
        assert group.attrs.get("aria-invalid") == "true"
        # the error is not announced twice: it is described, not a live region
        assert not [n for n in root.walk() if n.attrs.get("role") == "alert"]

    @pytest.mark.parametrize("engine", ENGINES)
    def test_no_error_means_no_invalid_state(self, engine):
        root = parse(ENGINES[engine](self.SRC, {"plans": PLANS}))
        (group,) = root.find_all("fieldset")
        assert "aria-invalid" not in group.attrs and "aria-describedby" not in group.attrs

    @pytest.mark.parametrize("engine", ENGINES)
    def test_the_event_is_wired_on_every_radio(self, engine):
        out = ENGINES[engine](
            '{% dj_radio_group name="plan" label="Plan" options=plans event="set_plan" %}',
            {"plans": PLANS},
        )
        assert [i.attrs.get("dj-change") for i in parse(out).find_all("input")] == ["set_plan"] * 3

    @pytest.mark.parametrize("engine", ENGINES)
    def test_a_disabled_group_disables_every_radio(self, engine):
        out = ENGINES[engine](
            '{% dj_radio_group name="plan" label="Plan" options=plans disabled=True %}',
            {"plans": PLANS},
        )
        root = parse(out)
        assert all("disabled" in i.attrs for i in root.find_all("input"))

    @pytest.mark.parametrize("engine", ENGINES)
    def test_plain_string_options_are_value_and_label(self, engine):
        out = ENGINES[engine](
            '{% dj_radio_group name="size" label="Size" options=sizes value="m" %}',
            {"sizes": ["s", "m", "l"]},
        )
        root = parse(out)
        assert [i.attrs["value"] for i in root.find_all("input")] == ["s", "m", "l"]
        assert [lb.all_text() for lb in root.find_all("label")] == ["s", "m", "l"]

    @pytest.mark.parametrize("engine", ENGINES)
    def test_a_hostile_value_label_and_name_are_escaped(self, engine):
        evil = '"><script>alert(1)</script>'
        out = ENGINES[engine](
            "{% dj_radio_group name=n label=l options=opts value=v event=e error=l helper=l %}",
            {"n": evil, "l": evil, "v": evil, "e": evil, "opts": [{"value": evil, "label": evil}]},
        )
        assert "<script>" not in out

    def test_the_two_engines_render_the_same_markup(self):
        ctx = {"plans": PLANS}
        for extra in (
            "",
            ' value="team"',
            " required=True",
            ' error="Pick one" helper="Billed monthly"',
            ' event="set_plan" disabled=True',
        ):
            src = '{% dj_radio_group name="plan" label="Plan" options=plans' + extra + " %}"
            assert _django(src, ctx) == _rust(src, ctx), src

    def test_a_bound_field_fills_the_group_and_its_auto_id_is_the_group_id(self):
        class Form(forms.Form):
            plan = forms.ChoiceField(
                choices=[("basic", "Basic"), ("pro", "Pro")],
                widget=forms.RadioSelect,
                label="Your plan",
                help_text="Billed monthly",
            )

        form = Form(data={})
        form.is_valid()
        for engine in ENGINES:
            out = ENGINES[engine]("{% dj_radio_group field=form.plan %}", {"form": form})
            root = parse(out)
            (group,) = root.find_all("fieldset")
            assert group.attrs["id"] == "id_plan"
            assert root.find_all("legend")[0].all_text().strip().startswith("Your plan")
            assert [i.attrs["name"] for i in root.find_all("input")] == ["plan", "plan"]
            assert group.attrs.get("aria-invalid") == "true"
            assert group.attrs.get("aria-required") == "true"
            by_id = {n.attrs["id"]: n for n in root.walk() if "id" in n.attrs}
            texts = {by_id[i].all_text() for i in group.attrs["aria-describedby"].split()}
            assert "Billed monthly" in texts

    def test_it_renders_its_own_node_under_the_rust_engine(self):
        from djust import _rust
        from djust.components import rust_handlers
        from djust.template_libraries import LibraryTagHandler

        rust_handlers.register_with_rust_engine()
        assert _rust.has_tag_handler("dj_radio_group")
        kind, handler = rust_handlers._bridged_handlers(
            __import__(
                "djust.components.templatetags.djust_components", fromlist=["register"]
            ).register
        )["dj_radio_group"]
        assert kind == "tag" and isinstance(handler, LibraryTagHandler)


class TestLiveInputRadio:
    CHOICES = [("free", "Free"), ("pro", "Pro")]

    def _root(self, **kwargs):
        return parse(
            str(
                live_input("radio", handler="set_plan", value="pro", choices=self.CHOICES, **kwargs)
            )
        )

    def test_the_radios_are_a_radiogroup(self):
        root = self._root()
        groups = [n for n in root.walk() if n.attrs.get("role") == "radiogroup"]
        assert len(groups) == 1
        assert {i.parent.parent for i in root.find_all("input")} == {groups[0]}

    def test_a_legend_names_the_group_as_a_fieldset(self):
        root = self._root(legend="Plan")
        (group,) = [n for n in root.walk() if n.attrs.get("role") == "radiogroup"]
        assert group.tag == "fieldset"
        (legend,) = group.find_all("legend")
        assert legend.all_text() == "Plan"
        assert group.attrs["aria-labelledby"] == legend.attrs["id"]
        assert re.fullmatch(r"[A-Za-z0-9_-]+", legend.attrs["id"])

    def test_without_a_legend_the_group_adds_no_browser_default_fieldset_styling(self):
        (group,) = [n for n in self._root().walk() if n.attrs.get("role") == "radiogroup"]
        assert group.tag == "div"

    def test_group_level_aria_goes_on_the_group_not_on_every_radio(self):
        root = self._root(
            aria_label="Plan", aria_describedby="plan-help", aria_invalid="true", required=True
        )
        (group,) = [n for n in root.walk() if n.attrs.get("role") == "radiogroup"]
        assert group.attrs["aria-label"] == "Plan"
        assert group.attrs["aria-describedby"] == "plan-help"
        assert group.attrs["aria-invalid"] == "true"
        assert group.attrs["aria-required"] == "true"
        for radio in root.find_all("input"):
            for name in ("aria-label", "aria-describedby", "aria-invalid", "aria-required"):
                assert name not in radio.attrs, (name, radio.attrs)
            assert "required" in radio.attrs

    def test_the_labels_and_bindings_are_unchanged(self):
        out = str(live_input("radio", handler="set_x", value="a", choices=["a", "b", "c"]))
        assert out.count("<label>") == 3
        assert out.count('dj-change="set_x"') == 3
        assert out.count('checked="checked"') == 1

    def test_a_hostile_legend_is_escaped(self):
        out = str(
            live_input(
                "radio",
                handler="h",
                choices=self.CHOICES,
                legend="<script>alert(1)</script>",
            )
        )
        assert "<script>" not in out


class TestMarkedSafeValuesStayInsideTheirAttributes:
    """``conditional_escape`` leaves a marked-safe value raw, which is right in a text slot and lets
    it out of an attribute (#3898). Every attribute these tags write is escaped whatever it is
    marked as."""

    EVIL = mark_safe('"><script>alert(1)</script>')

    def test_radio_group_attributes(self):
        out = _django(
            "{% dj_radio_group name=e label=lbl options=opts value=e event=e id=e %}",
            {"e": self.EVIL, "lbl": "L", "opts": [{"value": self.EVIL, "label": "x"}]},
        )
        assert "<script>" not in out
        assert not [n for n in parse(out).walk() if n.tag == "script"]

    def test_stepper_event(self):
        out = _django("{% stepper steps=steps event=e %}", {"steps": ["a"], "e": self.EVIL})
        assert "<script>" not in out

    def test_progress_name_in_the_native_handler(self):
        assert "<b>" not in _rust("{% progress value=1 label=e %}", {"e": "<b>"})
