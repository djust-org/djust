"""``scripts/check-template-comments.py`` covers templates embedded in Python (#3141).

``djust_theme init --with-examples`` wrote a template with a two-line ``{# #}``
from a Python string, which the ``.html``-only scan could not see.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location(
        "check_template_comments", ROOT / "scripts" / "check-template-comments.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MULTILINE_IN_STRING = '''\
TEMPLATE = """<div>
    {# first line
       second line #}
</div>
"""
'''


def test_multiline_comment_in_a_command_string_is_found(tmp_path):
    script = _load()
    module = tmp_path / "app" / "management" / "commands" / "gen.py"
    module.parent.mkdir(parents=True)
    module.write_text(MULTILINE_IN_STRING)

    assert list(script.iter_embedded_template_modules(tmp_path)) == [module]
    assert script.find_multiline_comments_in_python(module) == [(2, "first line")]


def test_scaffolding_modules_are_scanned_and_others_are_not(tmp_path):
    script = _load()
    scaffold = tmp_path / "scaffolding" / "templates.py"
    other = tmp_path / "views.py"
    for path in (scaffold, other):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(MULTILINE_IN_STRING)

    assert list(script.iter_embedded_template_modules(tmp_path)) == [scaffold]


def test_single_line_comments_and_python_comments_pass(tmp_path):
    script = _load()
    module = tmp_path / "management" / "ok.py"
    module.parent.mkdir(parents=True)
    module.write_text(
        '# {# a Python comment,\n# spanning lines #}\nT = "{# one line #}\\n{# another #}"\n'
    )

    assert script.find_multiline_comments_in_python(module) == []


def test_the_tree_has_no_multiline_comments():
    """The pre-commit hook only runs on commit; this keeps CI honest too."""
    script = _load()
    assert script.main([]) == 0
