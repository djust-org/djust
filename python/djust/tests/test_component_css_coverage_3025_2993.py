"""Every class these components render has a rule in CSS ``{% theme_head %}``
links (#3025, #2993).

``{% badge %}``, ``{% avatar %}``, ``{% progress %}`` and ``{% toast_container %}``
render BEM class names, and ``Alert`` / ``Progress`` / ``Avatar`` render their
own ``dj-*`` names. Until 1.3 no shipped stylesheet had a rule for them, so
they rendered as bare markup. Each case below renders the component through
its real code path (the template tag or the Python class), collects the
classes it emits, and asks the catalogue's own lookup (``styles_for``) which
stylesheets define them. Only the two files ``{% theme_head %}`` links count:
a rule in a file the page never loads styles nothing.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.template import engines

from djust.components.components.alert import Alert
from djust.components.components.avatar import Avatar
from djust.components.components.progress import Progress
from djust.components.components.toast import Toast
from djust.theming.gallery.catalogue import _classes_in, styles_for

#: What ``{% theme_head %}`` links (``theme_head.html``): the theming app's
#: components.css always, the components app's whenever ``djust.components``
#: is installed. The ``path`` values ``styles_for`` reports for them.
LINKED = {"djust_theming/components.css", "djust_components/components.css"}

#: Classes that are hooks or markers rather than styling, with the reason.
NOT_STYLING = {
    # The screen-reader label; styled globally, not per component.
    "sr-only": "shared utility",
    # An empty placeholder div `{% toast_container %}` renders with no toasts.
    "dj-toast-container--empty": "empty placeholder",
}

LINKED_CSS = (
    Path(__file__).resolve().parents[1]
    / "components"
    / "static"
    / "djust_components"
    / "components.css"
)


def _tag(source: str, context: dict | None = None) -> str:
    return (
        engines["django"].from_string("{% load djust_components %}" + source).render(context or {})
    )


def _cases() -> list[tuple[str, str]]:
    cases: list[tuple[str, str]] = []
    for status in ("default", "online", "offline", "warning", "error", "info"):
        cases.append(
            (f"badge-{status}", _tag('{% badge label="x" status=s pulse=True %}', {"s": status}))
        )
    for size in ("xs", "sm", "md", "lg", "xl"):
        cases.append((f"avatar-tag-{size}", _tag('{% avatar alt="Ada" size=s %}', {"s": size})))
    for status in ("online", "offline", "busy", "away"):
        cases.append(
            (
                f"avatar-tag-img-{status}",
                _tag('{% avatar src="/a.png" status=s %}', {"s": status}),
            )
        )
    for color in ("primary", "success", "warning", "danger"):
        for size in ("sm", "md", "lg"):
            cases.append(
                (
                    f"progress-tag-{color}-{size}",
                    _tag(
                        '{% progress value=40 label="Up" color=c size=s %}',
                        {"c": color, "s": size},
                    ),
                )
            )
    toasts = [
        {"id": i, "type": t, "message": "m"}
        for i, t in enumerate(("success", "error", "warning", "info"))
    ]
    cases.append(("toast", _tag("{% toast_container toasts %}", {"toasts": toasts})))
    cases.append(("toast-empty", _tag("{% toast_container toasts %}", {"toasts": []})))
    # The prev/next pagination `{% data_table %}` keeps for older call sites.
    table = _tag(
        "{% data_table rows=rows columns=columns page=2 total_pages=3 %}",
        {"rows": [{"id": 1, "name": "a"}], "columns": [{"key": "name", "label": "Name"}]},
    )
    cases.append(("table-pagination", table[table.index('<div class="dj-table__pagination">') :]))
    for variant in ("info", "success", "warning", "danger"):
        cases.append(
            (
                f"Alert-{variant}",
                Alert("m", variant=variant, dismissible=True, icon="!", action="x").render(),
            )
        )
    for variant in ("default", "success", "info", "warning", "danger"):
        for size in ("sm", "md", "lg"):
            cases.append(
                (
                    f"Progress-{variant}-{size}",
                    Progress(
                        value=3, label="L", variant=variant, size=size, show_value=True
                    ).render(),
                )
            )
    for size in ("xs", "sm", "md", "lg", "xl"):
        cases.append((f"Avatar-{size}", Avatar(initials="AL", size=size).render()))
    for status in ("online", "offline", "busy", "away"):
        cases.append((f"Avatar-img-{status}", Avatar(src="/a.png", status=status).render()))
    # The Python Toast class renders single-dash names (#3166).
    for kind in sorted(Toast.ALLOWED_TYPES):
        cases.append((f"Toast-{kind}", Toast("m", type=kind, action="x").render()))
    return cases


CASES = _cases()


@pytest.mark.parametrize("name,html", CASES, ids=[c[0] for c in CASES])
def test_every_class_has_a_rule_in_a_linked_stylesheet(name, html):
    classes = set(_classes_in(html)) - set(NOT_STYLING)
    assert classes, f"{name} rendered no classes: {html!r}"
    covered: set[str] = set()
    for row in styles_for(html):
        if row["path"] in LINKED:
            covered.update(row["classes"])
    missing = sorted(classes - covered)
    assert not missing, f"{name}: no rule in {sorted(LINKED)} for {missing}"


def test_the_cases_cover_every_variant_class_the_components_emit():
    """The table above must reach every modifier, or a missing rule for an
    unlisted variant would go unnoticed (#1543: enumerate every variant)."""
    emitted = set()
    for _name, html in CASES:
        emitted.update(_classes_in(html))
    for expected in (
        "dj-badge--info",
        "dj-badge__dot--pulse",
        "dj-avatar--xl",
        "dj-avatar__status--away",
        "dj-progress__fill--danger",
        "dj-progress--lg",
        "dj-toast--warning",
        "dj-alert-danger",
        "dj-alert-dismissible",
        "dj-progress-danger",
        "dj-progress-sm",
        "dj-avatar-xl",
        "dj-avatar-status-busy",
        "dj-toast-error",
        "dj-toast-message",
        "dj-toast-dismiss",
    ):
        assert expected in emitted, expected


def _selectors(css: str) -> str:
    """Every selector list in ``css``, with comments and declarations removed,
    so a class named only in a comment or a value does not count."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return "\n".join(re.findall(r"([^{}]+)\{", css))


def test_the_rules_are_selectors_not_comments():
    """``styles_for`` matches by line, so a class named in a comment would
    satisfy it. Check the new classes against real selectors too."""
    selectors = _selectors(LINKED_CSS.read_text())
    for _name, html in CASES:
        for cls in set(_classes_in(html)) - set(NOT_STYLING):
            if cls.startswith(("dj-badge", "dj-alert", "dj-progress", "dj-avatar", "dj-toast")):
                assert re.search(r"\." + re.escape(cls) + r"(?![\w-])", selectors), cls


def test_the_template_badge_rule_does_not_match_the_badge_class_markup():
    """``Badge`` (the Python class) renders ``dj-badge dj-badge-success`` and is
    styled in ``components-classes.css`` inside ``@layer djust-components``.
    An unlayered ``.dj-badge`` rule here would beat its variant colours
    whenever both files load (the catalogue loads both), so the tag's base
    rule is keyed on the ``dj-badge--<status>`` modifier the class never
    emits."""
    from djust.components.components.badge import Badge

    selectors = _selectors(LINKED_CSS.read_text())
    assert not re.search(r"(^|[\s,])\.dj-badge\s*(,|$)", selectors, re.M)
    html = Badge("New", variant="success").render()
    assert "dj-badge--" not in html


GUIDE = Path(__file__).resolve().parents[3] / "docs" / "website" / "guides" / "components.md"


@pytest.mark.parametrize(
    "module,cls", [("alert", "Alert"), ("progress", "Progress"), ("avatar", "Avatar")]
)
def test_the_docstrings_no_longer_call_the_classes_unstyled(module, cls):
    import importlib

    doc = getattr(importlib.import_module(f"djust.components.components.{module}"), cls).__doc__
    assert "No stylesheet djust ships" not in doc
    assert "Nothing djust ships reads them" not in doc
    assert "djust_components/components.css" in doc


def test_the_badge_docstring_and_guide_no_longer_say_it_ships_no_css():
    from djust.components.templatetags.djust_components import badge

    assert "Styling is yours" not in (badge.__doc__ or "")
    guide = GUIDE.read_text()
    assert "ships no CSS" not in guide.split("## djust-theming")[0]
    assert "Unstyled Python components" not in guide


@pytest.mark.django_db
@pytest.mark.parametrize("name", ["alert", "progress", "avatar"])
def test_the_catalogue_no_longer_calls_the_python_class_unstyled(client, name):
    from django.test import override_settings

    from djust.tests.test_catalogue_ux import _SETTINGS

    with override_settings(**_SETTINGS):
        body = client.get(f"/theme/components/{name}/").content.decode()
    assert "<strong>unstyled</strong>" not in body


#: Interaction states. An app overriding `.dj-alert-dismiss:hover` writes the
#: same pseudo-class, so they do not count against the budget below.
_STATES = re.compile(r":not\(:disabled\)|:hover|:focus-visible|:disabled")


def _strip_where(selector: str) -> str:
    """Remove every ``:where(...)``, which contributes zero specificity."""
    out, i = [], 0
    while i < len(selector):
        if selector.startswith(":where(", i):
            depth, i = 1, i + len(":where(")
            while depth:
                depth += {"(": 1, ")": -1}.get(selector[i], 0)
                i += 1
        else:
            out.append(selector[i])
            i += 1
    return "".join(out)


def specificity(selector: str) -> tuple[int, int, int]:
    """(ids, classes/attributes/pseudo-classes, types) of one complex
    selector, enough for the selectors in this block: ``:where()`` is zero,
    ``:not(x)`` counts as ``x``."""
    s = _strip_where(selector)
    s = re.sub(r":not\(([^()]*)\)", r"\1", s)
    ids = len(re.findall(r"#[\w-]+", s))
    classes = len(re.findall(r"\.[\w-]+", s)) + len(re.findall(r"\[[^\]]*\]", s))
    classes += len(re.findall(r"(?<!:):(?!:)[\w-]+", s))
    types = len(re.findall(r"(?:^|[\s>+~])([a-zA-Z][\w-]*)", s))
    return ids, classes, types


def _new_block_selectors() -> list[str]:
    css = LINKED_CSS.read_text()
    block = css[css.rindex("/*", 0, css.index("Template-tag BEM components and the Alert")) :]
    block = re.sub(r"/\*.*?\*/", "", block, flags=re.S)
    selectors = []
    for group in re.findall(r"([^{}]+)\{[^{}]*\}", block):
        selectors += [s.strip() for s in group.split(",") if s.strip()]
    return selectors


def test_the_specificity_helper_is_not_vacuous():
    assert specificity(".a") == (0, 1, 0)
    assert specificity('.a[class*="b"]') == (0, 2, 0)
    assert specificity('.a:where([class*="b"])') == (0, 1, 0)
    assert specificity(".a:not(.b)") == (0, 2, 0)
    assert specificity(":where(.a) .b") == (0, 1, 0)
    assert specificity(".a .b") == (0, 2, 0)
    assert specificity("div.a") == (0, 1, 1)


def test_a_one_class_app_rule_loaded_later_beats_every_new_rule():
    """Review of #3161: `.dj-badge[class*="dj-badge--"]` was (0,2,0), so an
    app's own `.dj-badge { … }` after `components.css` silently lost. Every
    new selector must be at most (0,1,0) once interaction states (which the
    app's override repeats) are set aside; source order then lets the app
    win."""
    selectors = _new_block_selectors()
    assert len(selectors) > 60, selectors  # the block was found and parsed
    heavy = [s for s in selectors if specificity(_STATES.sub("", s)) > (0, 1, 0)]
    assert not heavy, heavy


def test_labels_stay_on_the_foreground_token():
    """The status hue goes on the fill, border, dot or bar, never on the label
    text, so no label's contrast depends on a status colour (#2996)."""
    css = LINKED_CSS.read_text()
    for selector in (
        '.dj-badge:where([class*="dj-badge--"])',
        ".dj-alert",
        ".dj-toast",
        ".dj-toast-info",
        ".dj-toast-success",
        ".dj-toast-warning",
        ".dj-toast-error",
    ):
        block = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css).group(1)
        color = re.search(r"(?<![\w-])color:\s*([^;]+);", block).group(1)
        assert "--foreground" in color or "--card-foreground" in color, (selector, color)


def test_the_toast_class_properties_are_read_by_the_stylesheet():
    """Every ``--dj-toast-*`` property the ``Toast`` docstring lists is read by
    a declaration in the linked stylesheet, and each type's properties sit on
    that type's rule (#3166). Until then no stylesheet read any of them."""
    names = set(re.findall(r"--dj-toast-[\w-]+", Toast.__doc__ or ""))
    kinds = sorted(Toast.ALLOWED_TYPES)
    assert {"--dj-toast-bg", "--dj-toast-shadow"} <= names
    assert {"--dj-toast-%s-%s" % (k, p) for k in kinds for p in ("bg", "fg", "border")} <= names
    css = re.sub(r"/\*.*?\*/", "", LINKED_CSS.read_text(), flags=re.S)
    unread = sorted(n for n in names if "var(" + n + "," not in css and "var(" + n + ")" not in css)
    assert not unread, unread
    for kind in kinds:
        rule = re.search(r"\.dj-toast-%s\s*\{([^}]*)\}" % kind, css).group(1)
        for prop in ("bg", "fg", "border"):
            assert "var(--dj-toast-%s-%s," % (kind, prop) in rule, (kind, prop)
