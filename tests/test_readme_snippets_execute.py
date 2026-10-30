"""Execute README.md's LiveView snippets, not just parse them.

``scripts/check-doc-snippets.py`` AST-parses and import-resolves every fenced
Python block in the README, which cannot catch a class attribute that exists
in no mixin: the hero example shipped with ``template_string = ...`` (read by
nothing on ``LiveView``) and raised ``ValueError: Either template_name or
template must be set`` on first GET while every doc check stayed green.

This test executes each README block that defines a ``LiveView`` subclass,
and for every subclass that carries an inline ``template`` it mounts the view
through ``LiveViewTestClient``, renders it, asserts the mounted state reaches
the HTML, and invokes every ``@event_handler`` on it. A snippet that needs a
template file (``template_name = "counter.html"``) is executed for its class
definition only; the file is the reader's to write.

Gate-off: with ``template = `` reverted to ``template_string = `` in the hero
block, ``test_readme_liveview_snippets_mount_and_render`` fails on the
``ValueError`` above.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
import types

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
README = REPO_ROOT / "README.md"
_CHECKER = REPO_ROOT / "scripts" / "check-doc-snippets.py"


def _load_extractor():
    spec = importlib.util.spec_from_file_location("check_doc_snippets", _CHECKER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module.extract_python_blocks


def _liveview_blocks() -> list[tuple[int, str]]:
    """Every README python block whose source defines a LiveView subclass."""
    extract = _load_extractor()
    return [(lineno, code) for lineno, code, _marker in extract(README) if "(LiveView)" in code]


def _exec_block(lineno: int, code: str) -> dict:
    """Execute a README block as a throwaway module; return its namespace."""
    name = f"readme_snippet_line_{lineno}"
    module = types.ModuleType(name)
    module.__file__ = f"{README}#L{lineno}"
    # ``from myapp.views import CounterView`` style blocks reference modules
    # the reader writes; register a stub only for names a sibling block
    # defined, so the urls.py block resolves without a real app package.
    sys.modules[name] = module
    try:
        exec(compile(code, module.__file__, "exec"), module.__dict__)
    finally:
        sys.modules.pop(name, None)
    return module.__dict__


def _liveview_classes(namespace: dict) -> list[type]:
    from djust import LiveView

    return [
        obj
        for obj in namespace.values()
        if isinstance(obj, type) and issubclass(obj, LiveView) and obj is not LiveView
    ]


def _event_handlers(view_cls: type) -> list[str]:
    from djust.decorators import is_event_handler

    return sorted(
        name
        for name in dir(view_cls)
        if not name.startswith("_")
        and callable(getattr(view_cls, name, None))
        and is_event_handler(getattr(view_cls, name))
    )


def test_readme_defines_liveview_snippets():
    blocks = _liveview_blocks()
    assert blocks, "README.md no longer has a fenced LiveView example to execute"


@pytest.mark.parametrize(
    "lineno,code",
    [pytest.param(lineno, code, id=f"README.md:L{lineno}") for lineno, code in _liveview_blocks()],
)
def test_readme_liveview_snippets_mount_and_render(lineno: int, code: str, settings):
    """Each README LiveView snippet executes, mounts, renders and handles events."""
    from djust.testing import LiveViewTestClient

    settings.LIVEVIEW_ALLOWED_MODULES = [f"readme_snippet_line_{lineno}"]

    try:
        namespace = _exec_block(lineno, code)
    except ModuleNotFoundError as exc:
        # ``from myapp.views import CounterView`` imports the reader's own app;
        # that block is a wiring example, not a view definition.
        pytest.skip(f"README.md:L{lineno} imports the reader's app ({exc.name})")

    views = _liveview_classes(namespace)
    assert views, f"README.md:L{lineno} defines no LiveView subclass"

    for view_cls in views:
        inline_template = getattr(view_cls, "template", None)
        if not inline_template:
            # template_name only: the class definition executing is the check.
            assert getattr(view_cls, "template_name", None), (
                f"{view_cls.__name__} (README.md:L{lineno}) sets neither "
                "`template` nor `template_name`; a reader copying it gets "
                "`ValueError: Either template_name or template must be set`"
            )
            continue

        client = LiveViewTestClient(view_cls).mount()
        state = client.get_state()
        html = client.render()
        assert "dj-root" in html, f"{view_cls.__name__} rendered without a dj-root"
        for key, value in state.items():
            if isinstance(value, (int, str)) and f"{{{{ {key} }}}}" in inline_template:
                assert str(value) in html, (
                    f"{view_cls.__name__}: mounted `{key}={value!r}` did not reach the HTML"
                )

        for handler in _event_handlers(view_cls):
            result = client.send_event(handler)
            assert result["success"], f"{view_cls.__name__}.{handler} failed: {result.get('error')}"
            client.render()
