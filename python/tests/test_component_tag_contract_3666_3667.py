"""What the Python component tags accept and refuse, which the Rust engine now renders as they are.

Since 2.0 every ``djust_components`` tag but the core layout ones renders its own Django node under
the Rust engine (one render path per tag, ``djust.components.rust_handlers``). What is left here
from the native-handler fidelity work (#3666, #3667):

* ``TestMissingArguments``: ``djust._positional_args.missing_arguments`` (still used by the native
  handlers that remain: the core layout tags and the ``theme_tags`` handlers) against Django's real
  ``parse_bits`` over a grid of signatures and calls, quirk included.
* ``TestInclusionTag``: ``toast_container``'s template contract and the print stylesheet that hides
  the classes it emits.

Refs #3666, #3667, #3663.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from django.template import TemplateSyntaxError

ROOT = Path(__file__).resolve().parents[2]


class TestInclusionTag:
    NO_APP_DIRS = r"""
import json, os, sys, types
import django
from django.conf import settings

os.environ.pop("DJANGO_SETTINGS_MODULE", None)
urls = types.ModuleType("u")
urls.urlpatterns = []
sys.modules["u"] = urls
settings.configure(
    DEBUG=False, SECRET_KEY="x", USE_TZ=True,
    INSTALLED_APPS=["django.contrib.contenttypes", "django.contrib.auth", "djust",
                    "djust.theming", "djust.components"],
    DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
    TEMPLATES=[
        {"BACKEND": "django.template.backends.django.DjangoTemplates", "NAME": "django",
         "DIRS": [], "APP_DIRS": False, "OPTIONS": {}},
        {"BACKEND": "djust.template.backend.DjustTemplateBackend", "NAME": "djust",
         "DIRS": [], "APP_DIRS": False, "OPTIONS": {}},
    ],
    ROOT_URLCONF="u",
)
django.setup()
from django.template import engines

CASES = {
    "toast_container": ("{% toast_container toasts=t %}", {"t": [{"id": "1", "type": "info", "message": "m"}]}),
    "progress": ("{% progress value=3 %}", {}),
    "field_error": ("{% field_error field=none %}", {"none": None}),
    "avatar_group": ("{% avatar_group users=u %}", {"u": [{"name": "A B"}]}),
}
out = {}
for label, (body, ctx) in CASES.items():
    for name in ("django", "djust"):
        try:
            engines[name].from_string("{% load djust_components %}" + body).render(dict(ctx))
            out[label + "|" + name] = "ok"
        except Exception as exc:
            out[label + "|" + name] = type(exc).__name__
print(json.dumps(out))
"""

    def test_print_css_hides_the_classes_the_bridged_toast_emits(self):
        """``toast_container`` emits ``dj-toast-container`` / ``dj-toast``, not the
        native handler's ``toast-container`` / ``toast``; the print stylesheet that hides toasts
        has to name the ones that are rendered. Read from the template, not typed here."""
        template = (
            ROOT / "python/djust/components/templates/djust_components/toast.html"
        ).read_text(encoding="utf-8")
        emitted = set(re.findall(r'class="(dj-toast(?:-container)?)[ "]', template))
        assert emitted == {"dj-toast", "dj-toast-container"}, emitted
        css = (ROOT / "python/djust/theming/static/djust_theming/css/print.css").read_text(
            encoding="utf-8"
        )
        hidden = set(
            re.findall(r"^\s*\.([\w-]+),?\s*$", css.split("display: none !important")[0], re.M)
        )
        assert emitted <= hidden, sorted(emitted - hidden)

    def test_an_inclusion_tag_needs_the_template_loader_in_both_engines(self, tmp_path):
        """An inclusion tag (``toast_container``, ``progress``, ...) loads its template, so with no
        ``APP_DIRS`` and no ``DIRS`` the Python tag fails with ``TemplateDoesNotExist`` under
        Django's own engine, and, for a bridged one (``toast_container``), under
        ``DjustTemplateBackend`` too: the tag's contract, not the bridge's. A kept native twin
        (``progress``) draws its markup without a template. A tag that
        is not an inclusion tag needs no template file. Measured in a fresh process because the
        engines are configured by settings."""
        script = tmp_path / "no_app_dirs.py"
        script.write_text(self.NO_APP_DIRS, encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if k != "DJANGO_SETTINGS_MODULE"}
        env["PYTHONPATH"] = os.pathsep.join(
            filter(None, [str(ROOT / "python"), env.get("PYTHONPATH")])
        )
        proc = subprocess.run(
            [sys.executable, str(script)],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert proc.returncode == 0, proc.stderr[-1500:]
        result = json.loads(proc.stdout.strip().splitlines()[-1])
        for tag in ("toast_container", "progress"):
            assert result[tag + "|django"] == "TemplateDoesNotExist", result
        for tag in ("toast_container", "field_error", "avatar_group"):
            assert result[tag + "|djust"] == result[tag + "|django"], result
        # ``progress`` keeps its native twin, which draws its markup without a template file.
        for tag in ("progress", "field_error", "avatar_group"):
            assert result[tag + "|djust"] == "ok", result


# --------------------------------------------------------------------------- #


class TestMissingArguments:
    """``missing_arguments`` is a transcription of the end of ``parse_bits``; this compares it
    with the real thing over a grid, so a Django change to the quirk is seen here."""

    SIGNATURES = {
        "req": "def t(a, b): return ''",
        "default": "def t(a, b=1): return ''",
        "all_default": "def t(a=1, b=2): return ''",
        "kwonly": "def t(a, *, k): return ''",
        "kwonly_default": "def t(a, *, k=1): return ''",
        "varargs": "def t(a, *rest, b=2): return ''",
        "varkw": "def t(a, **extra): return ''",
        "none": "def t(): return ''",
    }
    CALLS = [
        "",
        "1",
        "1 2",
        "1 2 3",
        "a=1",
        "b=1",
        "k=1",
        "1 k=1",
        "1 b=2",
        "b=2 a=1",
        "1 2 k=3",
        "a=1 b=2 k=3",
        "zz=1",
    ]

    @staticmethod
    def _library(source: str):
        from django import template

        library = template.Library()
        namespace: dict[str, Any] = {}
        exec(source, namespace)  # noqa: S102 - a fixed table of signatures above
        library.simple_tag(namespace["t"], name="t")
        return library

    def test_missing_arguments_agrees_with_parse_bits(self):
        from django.template.base import Token, TokenType

        from djust._positional_args import missing_arguments, positional_spec
        from djust.template_libraries import _parser

        agreed = 0
        problems = []
        for label, source in self.SIGNATURES.items():
            library = self._library(source)
            compile_func = library.tags["t"]
            spec = positional_spec("t", compile_func)
            for call in self.CALLS:
                bits = call.split()
                missing = bool(missing_arguments(spec, bits))
                token = Token(TokenType.BLOCK, ("t " + call).strip())
                try:
                    compile_func(_parser([]), token)
                    refused = None
                except TemplateSyntaxError as exc:
                    refused = str(exc)
                django_missing = refused is not None and "did not receive value(s)" in refused
                other = refused is not None and not django_missing
                if other:
                    # Refused for another reason (unexpected keyword, too many positional
                    # arguments): the other checks own those, not this function.
                    continue
                agreed += 1
                if missing != django_missing:
                    problems.append(f"{label} {call!r}: ours {missing}, Django {refused}")
        assert agreed > 25, f"only {agreed} cases compared"
        assert not problems, "\n".join(problems)
