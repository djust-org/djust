"""Tests for scripts/check-doc-snippets.py — #1500.

Mirrors tests/test_check_adr_status.py: temp-dir fixtures driven through
the script via subprocess with path-override flags. Each `*_fails` test is
tautology-guarded (Action #1200 / #254) — it asserts BOTH the exit code AND
a specific substring in the message, so it cannot pass if the script merely
exits 1 for an unrelated reason.
"""

import pathlib
import subprocess
import sys
import tempfile
import textwrap

import pytest

_SELF = pathlib.Path(__file__).resolve()
_REPO = _SELF.parents[1]
_LINTER = _REPO / "scripts" / "check-doc-snippets.py"

# A 51 KB bundle stand-in — within the +/-3 KB band of a "~53 KB" claim.
_BUNDLE_51KB = b"x" * (51 * 1024)
# A real pyproject Django-floor line.
_PYPROJECT_42 = 'dependencies = [\n    "Django>=4.2.29,<6",\n]\n'


def _write(directory, name, content):
    p = directory / name
    p.write_text(textwrap.dedent(content).lstrip("\n"))
    return p


def _write_bytes(directory, name, data):
    p = directory / name
    p.write_bytes(data)
    return p


def _run(
    *,
    readme=None,
    quickstart=None,
    pyproject=None,
    bundle=None,
    guides_dir=None,
    no_guides=False,
):
    """Run the linter via subprocess with explicit path overrides.

    Returns (exit_code, stdout).
    """
    args = [sys.executable, str(_LINTER)]
    if readme is not None:
        args += ["--readme", str(readme)]
    if quickstart is not None:
        args += ["--quickstart", str(quickstart)]
    if pyproject is not None:
        args += ["--pyproject", str(pyproject)]
    if bundle is not None:
        args += ["--bundle", str(bundle)]
    if guides_dir is not None:
        args += ["--guides-dir", str(guides_dir)]
    if no_guides:
        args += ["--no-guides"]
    result = subprocess.run(args, capture_output=True, text=True, cwd=str(_REPO))
    return result.returncode, result.stdout


def _minimal_fixture(d, *, readme_body="", quickstart_body=""):
    """Write a complete passing fixture set, then return the paths.

    `readme_body` / `quickstart_body` are appended after a correct Django
    badge so callers can inject just the block under test. Each body is
    dedented independently so callers can indent the triple-quoted string
    to match their own code style without breaking the markdown.
    """
    base_readme = textwrap.dedent(
        """
        # Demo

        [![Django 4.2+](https://img.shields.io/badge/django-4.2+-green.svg)](x)
        - ~53 KB gzipped minified client JavaScript
        """
    ).lstrip("\n")
    readme = _write(
        d,
        "README.md",
        base_readme + "\n" + textwrap.dedent(readme_body).lstrip("\n"),
    )
    quickstart = _write(
        d,
        "QUICKSTART.md",
        "# Quickstart\n\n- Django 4.2+\n" + textwrap.dedent(quickstart_body).lstrip("\n"),
    )
    pyproject = _write(d, "pyproject.toml", _PYPROJECT_42)
    bundle = _write_bytes(d, "client.min.js.gz", _BUNDLE_51KB)
    return readme, quickstart, pyproject, bundle


class TestCheckDocSnippets:
    """Core checks for the doc-snippet smoke test + claim assertions."""

    def test_valid_module_snippet_passes(self):
        """A complete `from djust import LiveView` module → exit 0."""
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            r, q, pp, b = _minimal_fixture(
                d,
                readme_body="""
                ```python
                from djust import LiveView, event_handler

                class CounterView(LiveView):
                    def mount(self, request, **kwargs):
                        self.count = 0
                ```
                """,
            )
            code, out = _run(readme=r, quickstart=q, pyproject=pp, bundle=b)
            assert code == 0, f"expected exit 0, got {code}: {out}"
            assert "OK" in out

    def test_fragment_snippet_passes(self):
        """A bare method (no import, no enclosing class) → fragment →
        AST-only → exit 0 even though it references undefined names."""
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            r, q, pp, b = _minimal_fixture(
                d,
                readme_body="""
                ```python
                @event_handler
                def add_todo(self, text):
                    self.todos.append({'text': text})
                    Product.objects.filter(active=True)
                ```
                """,
            )
            code, out = _run(readme=r, quickstart=q, pyproject=pp, bundle=b)
            assert code == 0, f"expected exit 0, got {code}: {out}"
            assert "OK" in out

    def test_syntax_error_snippet_fails(self):
        """A ```python block that is not valid Python → exit 1."""
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            r, q, pp, b = _minimal_fixture(
                d,
                readme_body="""
                ```python
                def broken(:
                    pass
                ```
                """,
            )
            code, out = _run(readme=r, quickstart=q, pyproject=pp, bundle=b)
            assert code == 1, f"expected exit 1, got {code}: {out}"
            assert "syntax error" in out
            assert "README.md:" in out

    def test_phantom_import_fails(self):
        """A module snippet importing a nonexistent djust symbol → exit 1
        naming the missing symbol."""
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            r, q, pp, b = _minimal_fixture(
                d,
                readme_body="""
                ```python
                from djust import nonexistent_thing

                class V:
                    pass
                ```
                """,
            )
            code, out = _run(readme=r, quickstart=q, pyproject=pp, bundle=b)
            assert code == 1, f"expected exit 1, got {code}: {out}"
            assert "nonexistent_thing" in out

    def test_phantom_module_import_fails(self):
        """A module snippet importing a nonexistent module → exit 1."""
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            r, q, pp, b = _minimal_fixture(
                d,
                readme_body="""
                ```python
                import djust.totally_not_a_real_module

                def f():
                    pass
                ```
                """,
            )
            code, out = _run(readme=r, quickstart=q, pyproject=pp, bundle=b)
            assert code == 1, f"expected exit 1, got {code}: {out}"
            assert "totally_not_a_real_module" in out

    def test_django_version_match_passes(self):
        """A README badge `django-4.2+` against a `Django>=4.2` floor → 0."""
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            r, q, pp, b = _minimal_fixture(d)
            code, out = _run(readme=r, quickstart=q, pyproject=pp, bundle=b)
            assert code == 0, f"expected exit 0, got {code}: {out}"
            assert "OK" in out

    def test_django_version_mismatch_fails(self):
        """A README badge `django-3.2+` against a `Django>=4.2` floor →
        exit 1, naming both versions."""
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            readme = _write(
                d,
                "README.md",
                """
                # Demo

                [![Django 3.2+](https://img.shields.io/badge/django-3.2+-green.svg)](x)
                """,
            )
            quickstart = _write(d, "QUICKSTART.md", "# Quickstart\n")
            pyproject = _write(d, "pyproject.toml", _PYPROJECT_42)
            bundle = _write_bytes(d, "client.min.js.gz", _BUNDLE_51KB)
            code, out = _run(
                readme=readme,
                quickstart=quickstart,
                pyproject=pyproject,
                bundle=bundle,
            )
            assert code == 1, f"expected exit 1, got {code}: {out}"
            assert "Django" in out
            assert "3.2" in out
            assert "4.2" in out

    def test_django_prose_mismatch_fails(self):
        """A QUICKSTART prose claim `Django 3.2+` against a 4.2 floor →
        exit 1."""
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            readme = _write(d, "README.md", "# Demo\n")
            quickstart = _write(d, "QUICKSTART.md", "# Quickstart\n\n- Django 3.2+\n")
            pyproject = _write(d, "pyproject.toml", _PYPROJECT_42)
            bundle = _write_bytes(d, "client.min.js.gz", _BUNDLE_51KB)
            code, out = _run(
                readme=readme,
                quickstart=quickstart,
                pyproject=pyproject,
                bundle=bundle,
            )
            assert code == 1, f"expected exit 1, got {code}: {out}"
            assert "QUICKSTART.md:" in out
            assert "3.2" in out

    def test_js_size_within_tolerance_passes(self):
        """A 51 KB bundle + README `~53 KB` claim → within band → exit 0."""
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            r, q, pp, b = _minimal_fixture(d)
            code, out = _run(readme=r, quickstart=q, pyproject=pp, bundle=b)
            assert code == 0, f"expected exit 0, got {code}: {out}"
            assert "OK" in out

    def test_js_size_out_of_band_fails(self):
        """A 51 KB bundle + a stale README `~29 KB` claim → out of band →
        exit 1."""
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            readme = _write(
                d,
                "README.md",
                """
                # Demo

                [![Django 4.2+](https://img.shields.io/badge/django-4.2+-green.svg)](x)
                - ~29 KB gzipped minified client JavaScript
                """,
            )
            quickstart = _write(d, "QUICKSTART.md", "# Quickstart\n")
            pyproject = _write(d, "pyproject.toml", _PYPROJECT_42)
            bundle = _write_bytes(d, "client.min.js.gz", _BUNDLE_51KB)
            code, out = _run(
                readme=readme,
                quickstart=quickstart,
                pyproject=pyproject,
                bundle=bundle,
            )
            assert code == 1, f"expected exit 1, got {code}: {out}"
            assert "29" in out
            assert "tolerance band" in out

    def test_missing_bundle_warns_not_fails(self):
        """An absent bundle file → size sub-check skipped → exit 0 +
        WARNING (a fresh pre-build checkout must not fail)."""
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            r, q, pp, _ = _minimal_fixture(d)
            missing = d / "no-such-bundle.js.gz"
            code, out = _run(readme=r, quickstart=q, pyproject=pp, bundle=missing)
            assert code == 0, f"expected exit 0, got {code}: {out}"
            assert "WARNING" in out
            assert "skipped" in out

    def test_skip_marker_respected(self):
        """A block preceded by the skip marker that would otherwise fail
        (syntax error) → skipped → exit 0."""
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            r, q, pp, b = _minimal_fixture(
                d,
                readme_body="""
                <!-- doc-snippet-check: skip -->
                ```python
                def broken(:
                    pass
                ```
                """,
            )
            code, out = _run(readme=r, quickstart=q, pyproject=pp, bundle=b)
            assert code == 0, f"expected exit 0, got {code}: {out}"
            assert "OK" in out

    def test_missing_input_file_usage_error(self):
        """An explicitly-passed --readme that does not exist → exit 2."""
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            quickstart = _write(d, "QUICKSTART.md", "# Quickstart\n")
            pyproject = _write(d, "pyproject.toml", _PYPROJECT_42)
            code, out = _run(
                readme=d / "nonexistent-readme.md",
                quickstart=quickstart,
                pyproject=pyproject,
            )
            assert code == 2, f"expected exit 2, got {code}: {out}"
            assert "not found" in out

    @pytest.mark.slow
    def test_real_docs_pass(self):
        """Dogfood gate (Action #1060): the real README/QUICKSTART pass.

        This is the assertion that #1497's snippet/claim cleanup is
        complete — parts (a) and (b) must be green against the real repo.
        """
        code, out = _run()
        assert code == 0, f"real docs must pass: {out}"
        assert "OK" in out


class TestCheckGuides:
    """#1707 — symbol/import resolvability over docs/website/guides/*.md.

    The guard that would have caught #1559/#1699's hallucinated
    `djust.tenants` symbols. Guides get part (a) ONLY (AST + import/symbol
    resolution); not part (b)/(c). Each `*_fails` test is tautology-guarded
    (Action #1200 / #254): it asserts BOTH the exit code AND a substring of
    the verdict.
    """

    def _passing_main_docs(self, d):
        """Write a minimal passing README/QUICKSTART/pyproject/bundle set
        (no guides) so a guides-dir override is the only variable."""
        return _minimal_fixture(d)

    def test_guide_with_hallucinated_symbol_fails(self):
        """A guide importing a nonexistent djust.tenants symbol → exit 1
        naming it (the #1559/#1699 bug class)."""
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            r, q, pp, b = self._passing_main_docs(d)
            guides = d / "guides"
            guides.mkdir()
            _write(
                guides,
                "bad.md",
                """
                # Bad guide

                ```python
                from djust.tenants import tenant_queryset

                class TenantView:
                    pass
                ```
                """,
            )
            code, out = _run(readme=r, quickstart=q, pyproject=pp, bundle=b, guides_dir=guides)
            assert code == 1, f"expected exit 1, got {code}: {out}"
            assert "tenant_queryset" in out
            assert "bad.md:" in out

    def test_no_guides_flag_skips_the_scan(self):
        """`--no-guides` skips the guide scan — the SAME bad guide that
        fails without the flag passes with it (gate-off proof, Action #254)."""
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            r, q, pp, b = self._passing_main_docs(d)
            guides = d / "guides"
            guides.mkdir()
            _write(
                guides,
                "bad.md",
                """
                # Bad guide

                ```python
                from djust.tenants import tenant_queryset

                class TenantView:
                    pass
                ```
                """,
            )
            # WITHOUT --no-guides → exit 1 (proves the guide is what's scanned).
            code, out = _run(readme=r, quickstart=q, pyproject=pp, bundle=b, guides_dir=guides)
            assert code == 1, f"unguarded guide must fail: {out}"
            assert "tenant_queryset" in out
            # WITH --no-guides → exit 0.
            code, out = _run(
                readme=r,
                quickstart=q,
                pyproject=pp,
                bundle=b,
                guides_dir=guides,
                no_guides=True,
            )
            assert code == 0, f"--no-guides must skip the scan: {out}"
            assert "OK" in out

    def test_guide_skip_marker_respected(self):
        """A guide block preceded by the skip marker that would otherwise
        fail (phantom import) → skipped → exit 0."""
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            r, q, pp, b = self._passing_main_docs(d)
            guides = d / "guides"
            guides.mkdir()
            _write(
                guides,
                "illustrative.md",
                """
                # Illustrative guide

                <!-- doc-snippet-check: skip -->
                ```python
                from yourapp.models import Project

                class V:
                    pass
                ```
                """,
            )
            code, out = _run(readme=r, quickstart=q, pyproject=pp, bundle=b, guides_dir=guides)
            assert code == 0, f"skip-marked guide block must pass: {out}"
            assert "OK" in out

    def test_guide_submodule_import_resolves(self):
        """`from django.db import migrations, models` — `migrations` is a
        genuine submodule, not an attribute → must resolve, not false-fail.

        Regression for the submodule-fallback path in _resolve_imports.
        """
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            r, q, pp, b = self._passing_main_docs(d)
            guides = d / "guides"
            guides.mkdir()
            _write(
                guides,
                "migration.md",
                """
                # Migration guide

                ```python
                from django.db import migrations, models


                class Migration(migrations.Migration):
                    operations = [migrations.RunPython(models.Model)]
                ```
                """,
            )
            code, out = _run(readme=r, quickstart=q, pyproject=pp, bundle=b, guides_dir=guides)
            assert code == 0, f"submodule import must resolve: {out}"
            assert "OK" in out

    def test_explicit_missing_guides_dir_is_usage_error(self):
        """An explicitly-passed --guides-dir that does not exist → exit 2."""
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            r, q, pp, b = self._passing_main_docs(d)
            code, out = _run(
                readme=r,
                quickstart=q,
                pyproject=pp,
                bundle=b,
                guides_dir=d / "no-such-guides-dir",
            )
            assert code == 2, f"expected exit 2, got {code}: {out}"
            assert "not found" in out

    @pytest.mark.slow
    def test_real_guides_pass(self):
        """Dogfood gate (Action #1060): the real docs/website/guides/*.md
        pass the part-(a) scan — #1707's cleanup is complete."""
        code, out = _run()
        assert code == 0, f"real guides must pass: {out}"
        assert "OK" in out


class TestCheckSecurityStyle:
    """Part (c) — #1509: doc-example security/style lint.

    Each `*_fails` test is tautology-guarded (Action #1200 / #254): it
    asserts BOTH the exit code AND a substring of the verdict message, so
    a test cannot pass because the script exited 1 for an unrelated reason.
    """

    def test_print_call_in_snippet_fails(self):
        """A module snippet with a plain `print(...)` → exit 1, names print."""
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            r, q, pp, b = _minimal_fixture(
                d,
                readme_body="""
                ```python
                from djust import LiveView

                class DebugView(LiveView):
                    def mount(self, request, **kwargs):
                        print("hi")
                ```
                """,
            )
            code, out = _run(readme=r, quickstart=q, pyproject=pp, bundle=b)
            assert code == 1, f"expected exit 1, got {code}: {out}"
            assert "print(" in out
            assert "README.md:" in out

    def test_fstring_print_fails(self):
        """`print(f"...")` → exit 1, message names the f-string form."""
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            r, q, pp, b = _minimal_fixture(
                d,
                readme_body="""
                ```python
                from djust import LiveView

                class DebugView(LiveView):
                    def mount(self, request, **kwargs):
                        print(f"mounting for {request.user}")
                ```
                """,
            )
            code, out = _run(readme=r, quickstart=q, pyproject=pp, bundle=b)
            assert code == 1, f"expected exit 1, got {code}: {out}"
            assert "f-string" in out

    def test_mark_safe_fstring_fails(self):
        """`mark_safe(f"<b>{x}</b>")` with interpolation → exit 1."""
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            r, q, pp, b = _minimal_fixture(
                d,
                readme_body="""
                ```python
                from django.utils.safestring import mark_safe
                from djust import LiveView

                class V(LiveView):
                    def render_label(self, x):
                        return mark_safe(f"<b>{x}</b>")
                ```
                """,
            )
            code, out = _run(readme=r, quickstart=q, pyproject=pp, bundle=b)
            assert code == 1, f"expected exit 1, got {code}: {out}"
            assert "mark_safe" in out

    def test_mark_safe_constant_passes(self):
        """`mark_safe("<b>static</b>")` — no interpolation → exit 0."""
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            r, q, pp, b = _minimal_fixture(
                d,
                readme_body="""
                ```python
                from django.utils.safestring import mark_safe
                from djust import LiveView

                class V(LiveView):
                    def render_label(self):
                        return mark_safe("<b>static</b>")
                ```
                """,
            )
            code, out = _run(readme=r, quickstart=q, pyproject=pp, bundle=b)
            assert code == 0, f"expected exit 0, got {code}: {out}"
            assert "OK" in out

    def test_format_html_passes(self):
        """`format_html("<b>{}</b>", x)` — the correct API → exit 0."""
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            r, q, pp, b = _minimal_fixture(
                d,
                readme_body="""
                ```python
                from django.utils.html import format_html
                from djust import LiveView

                class V(LiveView):
                    def render_label(self, x):
                        return format_html("<b>{}</b>", x)
                ```
                """,
            )
            code, out = _run(readme=r, quickstart=q, pyproject=pp, bundle=b)
            assert code == 0, f"expected exit 0, got {code}: {out}"
            assert "OK" in out

    def test_bare_except_pass_fails(self):
        """A bare `except: pass` → exit 1."""
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            r, q, pp, b = _minimal_fixture(
                d,
                readme_body="""
                ```python
                from djust import LiveView

                class V(LiveView):
                    def mount(self, request, **kwargs):
                        try:
                            risky()
                        except:
                            pass
                ```
                """,
            )
            code, out = _run(readme=r, quickstart=q, pyproject=pp, bundle=b)
            assert code == 1, f"expected exit 1, got {code}: {out}"
            assert "except" in out

    def test_except_with_logging_passes(self):
        """`except Exception: logger.exception(...)` → exit 0 (body handled)."""
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            r, q, pp, b = _minimal_fixture(
                d,
                readme_body="""
                ```python
                import logging

                from djust import LiveView

                logger = logging.getLogger(__name__)

                class V(LiveView):
                    def mount(self, request, **kwargs):
                        try:
                            risky()
                        except Exception:
                            logger.exception("risky failed")
                ```
                """,
            )
            code, out = _run(readme=r, quickstart=q, pyproject=pp, bundle=b)
            assert code == 0, f"expected exit 0, got {code}: {out}"
            assert "OK" in out

    def test_fstring_logging_fails(self):
        """`logger.error(f"oops {e}")` → exit 1."""
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            r, q, pp, b = _minimal_fixture(
                d,
                readme_body="""
                ```python
                import logging

                from djust import LiveView

                logger = logging.getLogger(__name__)

                class V(LiveView):
                    def mount(self, request, **kwargs):
                        e = "boom"
                        logger.error(f"oops {e}")
                ```
                """,
            )
            code, out = _run(readme=r, quickstart=q, pyproject=pp, bundle=b)
            assert code == 1, f"expected exit 1, got {code}: {out}"
            assert "logging" in out or "logger" in out

    def test_percent_logging_passes(self):
        """`logger.error("oops %s", e)` — the correct %s form → exit 0."""
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            r, q, pp, b = _minimal_fixture(
                d,
                readme_body="""
                ```python
                import logging

                from djust import LiveView

                logger = logging.getLogger(__name__)

                class V(LiveView):
                    def mount(self, request, **kwargs):
                        e = "boom"
                        logger.error("oops %s", e)
                ```
                """,
            )
            code, out = _run(readme=r, quickstart=q, pyproject=pp, bundle=b)
            assert code == 0, f"expected exit 0, got {code}: {out}"
            assert "OK" in out

    def test_anti_pattern_marker_suppresses_style_check(self):
        """The `anti-pattern` marker opts a block out of the style verdict.

        Gate-the-change-off proof (Action #254): the SAME `print(f"...")`
        block WITHOUT the marker exits 1, WITH the marker exits 0 — so the
        marker is load-bearing, not a tautology.
        """
        block = """
            ```python
            from djust import LiveView

            class DebugView(LiveView):
                def mount(self, request, **kwargs):
                    print(f"debug {request.user}")
            ```
            """
        marked = "<!-- doc-snippet-check: anti-pattern -->\n" + textwrap.dedent(block).lstrip("\n")
        # WITH marker → exit 0.
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            r, q, pp, b = _minimal_fixture(d, readme_body=marked)
            code, out = _run(readme=r, quickstart=q, pyproject=pp, bundle=b)
            assert code == 0, f"marked block must pass, got {code}: {out}"
            assert "OK" in out
        # WITHOUT marker → exit 1 (proves the marker is what suppressed it).
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            r, q, pp, b = _minimal_fixture(d, readme_body=block)
            code, out = _run(readme=r, quickstart=q, pyproject=pp, bundle=b)
            assert code == 1, f"unmarked block must fail, got {code}: {out}"
            assert "f-string" in out

    def test_anti_pattern_marker_still_syntax_checks(self):
        """An anti-pattern-marked block with a syntax error → still exit 1.

        The marker suppresses only part (c) — part (a) syntax checks stay on.
        """
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            r, q, pp, b = _minimal_fixture(
                d,
                readme_body="""
                <!-- doc-snippet-check: anti-pattern -->
                ```python
                def broken(:
                    pass
                ```
                """,
            )
            code, out = _run(readme=r, quickstart=q, pyproject=pp, bundle=b)
            assert code == 1, f"expected exit 1, got {code}: {out}"
            assert "syntax error" in out

    def test_skip_marker_suppresses_style_check_too(self):
        """The `skip` marker drops part (c) as well — it drops everything."""
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            r, q, pp, b = _minimal_fixture(
                d,
                readme_body="""
                <!-- doc-snippet-check: skip -->
                ```python
                from djust import LiveView

                class V(LiveView):
                    def mount(self, request, **kwargs):
                        print(f"skipped {request.user}")
                ```
                """,
            )
            code, out = _run(readme=r, quickstart=q, pyproject=pp, bundle=b)
            assert code == 0, f"expected exit 0, got {code}: {out}"
            assert "OK" in out

    def test_csrf_exempt_warns_not_fails(self):
        """A `@csrf_exempt`-decorated snippet → exit 0 + a WARNING line."""
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            r, q, pp, b = _minimal_fixture(
                d,
                readme_body="""
                ```python
                from django.views.decorators.csrf import csrf_exempt
                from djust import LiveView

                class V(LiveView):
                    @csrf_exempt
                    def handler(self):
                        pass
                ```
                """,
            )
            code, out = _run(readme=r, quickstart=q, pyproject=pp, bundle=b)
            assert code == 0, f"expected exit 0, got {code}: {out}"
            assert "WARNING" in out
            assert "csrf_exempt" in out

    @pytest.mark.slow
    def test_real_docs_pass_security_style(self):
        """Dogfood gate (Action #1060): the real README/QUICKSTART have no
        part-(c) security/style violations → exit 0."""
        code, out = _run()
        assert code == 0, f"real docs must pass security/style: {out}"
        assert "OK" in out
