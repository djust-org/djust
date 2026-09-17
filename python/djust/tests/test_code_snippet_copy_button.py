"""The code snippet's copy button has to do something when it is clicked.

It rendered as a working control and was wired to nothing: the markup carried
`class="dj-code-snippet__copy"`, an `aria-label` and `type="button"`, and no
handler of any kind. A button that looks interactive and silently does nothing
is the worst version of this — there is no error, no console warning, and no
visual difference from a working one.

The fix uses the framework's own `dj-copy` attribute, which does the copy
client-side (`09-event-binding.js:_handleDjCopy`): it reads a CSS selector off
the attribute, copies the named element's `textContent`, and runs the
`dj-copy-feedback` / `dj-copy-class` feedback. That is deliberately not a server
event — a clipboard write is a browser API, so there is nothing for the server
to do.

These tests pin the two halves of the contract that make that work: the button
names a target, and the target exists with a matching id.
"""

import re

from djust.components.components.code_snippet import CodeSnippet

_ID_RE = re.compile(r'id="(dj-code-snippet-[0-9a-f]+)"')


def _render(**kwargs) -> str:
    return CodeSnippet(**kwargs).render()


def test_copy_button_dispatches_dj_copy():
    """The regression. Before this, the button had no handler at all."""
    html = _render(code="pip install djust")

    assert "dj-copy=" in html, f"the copy button is wired to nothing: {html}"


def test_copy_target_exists_and_matches():
    """`dj-copy="#id"` selects by id — a dangling selector copies the literal
    text `#dj-code-snippet-…` instead of the code, and still reports success."""
    html = _render(code="pip install djust")

    target = _ID_RE.search(html)
    assert target, f"the copy target has no id to select: {html}"

    assert f'dj-copy="#{target.group(1)}"' in html, (
        f"the button points at a different id than the code block carries: {html}"
    )


def test_ids_are_unique_per_instance():
    """Two snippets on one page must not both answer to the same id.

    A shared id would make every copy button on the page copy the first
    snippet's code — silently, and only visible once there are two.
    """
    first = _ID_RE.search(_render(code="one")).group(1)
    second = _ID_RE.search(_render(code="two")).group(1)

    assert first != second


def test_the_target_holds_the_raw_code():
    """`textContent` is what gets copied, so it must be the code unescaped.

    The `<code>` element escapes its content for HTML; `textContent` returns it
    decoded, which is why copying from the DOM is correct here rather than
    copying the escaped source.
    """
    html = _render(code="a < b && c > d")
    target = _ID_RE.search(html).group(1)

    body = re.search(rf'<code[^>]*id="{target}"[^>]*>(.*?)</code>', html, re.DOTALL)
    assert body, f"could not find the code block for {target}: {html}"
    assert "&lt;" in body.group(1), "the code block should carry escaped markup"
    assert "&amp;&amp;" in body.group(1)


def test_the_button_keeps_its_accessible_name():
    """Whatever the wiring, it is still a labelled button."""
    html = _render(code="x")

    assert 'aria-label="Copy code"' in html
    assert 'type="button"' in html
