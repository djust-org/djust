"""Tests for #1628 — V008 false-positive on stdlib module functions.

Follow-up to #1609/#1623. PR #1623 extended SAFE_TYPES with bare builtins
(`max`, `min`, ...) but V008 still false-fired on qualified stdlib calls
like `inspect.getsource()`, `os.path.join()`, `json.dumps()` because
those resolve to dotted names that didn't match the bare-name set.
"""

import textwrap
from unittest.mock import patch


def _run_v008(tmp_path, source):
    py_file = tmp_path / "views.py"
    py_file.write_text(textwrap.dedent(source))

    from djust.checks import _check_non_primitive_assignments_in_mount

    errors = []
    with patch("djust.checks._get_project_app_dirs", return_value=[str(tmp_path)]):
        _check_non_primitive_assignments_in_mount(errors)
    return [e for e in errors if e.id == "djust.V008"]


class TestV008StdlibQualified1628:
    """#1628: V008 must NOT fire on stdlib module functions returning primitives."""

    def test_inspect_getsource_does_not_fire_1628(self, tmp_path):
        """Reporter's exact case: self.x = inspect.getsource(method)."""
        v008 = _run_v008(
            tmp_path,
            """\
            class MyView:
                def mount(self, request, **kwargs):
                    self.src = inspect.getsource(self.handler)
            """,
        )
        assert v008 == [], "V008 should NOT fire on inspect.getsource — returns str. Got: %r" % v008

    def test_inspect_family_does_not_fire(self, tmp_path):
        """getsourcefile, getmodule, getdoc all return Optional[str]."""
        v008 = _run_v008(
            tmp_path,
            """\
            class MyView:
                def mount(self, request, **kwargs):
                    self.f = inspect.getsourcefile(self.handler)
                    self.m = inspect.getmodule(self.handler)
                    self.d = inspect.getdoc(self.handler)
            """,
        )
        assert v008 == [], "inspect.* should not fire V008. Got: %r" % v008

    def test_os_path_does_not_fire(self, tmp_path):
        """os.path.join/basename/dirname/exists/etc all return str or bool."""
        v008 = _run_v008(
            tmp_path,
            """\
            class MyView:
                def mount(self, request, **kwargs):
                    self.p = os.path.join("/a", "b")
                    self.b = os.path.basename(self.p)
                    self.d = os.path.dirname(self.p)
                    self.e = os.path.exists(self.p)
                    self.f = os.path.isfile(self.p)
                    self.di = os.path.isdir(self.p)
                    self.a = os.path.abspath(self.p)
                    self.r = os.path.relpath(self.p)
            """,
        )
        assert v008 == [], "os.path.* should not fire V008. Got: %r" % v008

    def test_os_getenv_getcwd_do_not_fire(self, tmp_path):
        v008 = _run_v008(
            tmp_path,
            """\
            class MyView:
                def mount(self, request, **kwargs):
                    self.k = os.getenv("KEY")
                    self.c = os.getcwd()
            """,
        )
        assert v008 == [], "os.getenv/getcwd should not fire V008. Got: %r" % v008

    def test_pathlib_qualified_class_methods_do_not_fire(self, tmp_path):
        """Unbound class-method form: pathlib.Path.read_text(p) etc."""
        v008 = _run_v008(
            tmp_path,
            """\
            class MyView:
                def mount(self, request, **kwargs):
                    self.t = pathlib.Path.read_text(self.p)
                    self.e = pathlib.Path.exists(self.p)
                    self.f = pathlib.Path.is_file(self.p)
                    self.d = pathlib.Path.is_dir(self.p)
            """,
        )
        assert v008 == [], "pathlib.Path.* should not fire V008. Got: %r" % v008

    def test_pathlib_chained_call_isoformat_read_text_do_not_fire(self, tmp_path):
        """Chained call form: Path(p).read_text() resolves to bare `read_text`.
        Bare method names `isoformat` and `read_text` are in SAFE_TYPES."""
        v008 = _run_v008(
            tmp_path,
            """\
            class MyView:
                def mount(self, request, **kwargs):
                    self.text = pathlib.Path("/etc/hosts").read_text()
                    self.now = datetime.datetime.now().isoformat()
                    self.d = datetime.date.today().isoformat()
            """,
        )
        assert v008 == [], "chained Path/datetime should not fire V008. Got: %r" % v008

    def test_json_dumps_does_not_fire(self, tmp_path):
        v008 = _run_v008(
            tmp_path,
            """\
            class MyView:
                def mount(self, request, **kwargs):
                    self.config = json.dumps({"k": "v"})
            """,
        )
        assert v008 == [], "json.dumps should not fire V008. Got: %r" % v008

    def test_datetime_qualified_isoformat_does_not_fire(self, tmp_path):
        """Unbound class form: datetime.date.isoformat(d)."""
        v008 = _run_v008(
            tmp_path,
            """\
            class MyView:
                def mount(self, request, **kwargs):
                    self.s = datetime.date.isoformat(self.d)
            """,
        )
        assert v008 == [], "datetime.*.isoformat should not fire V008. Got: %r" % v008

    def test_custom_class_still_fires_v008(self, tmp_path):
        """Regression backstop: non-primitive custom classes STILL fire V008."""
        v008 = _run_v008(
            tmp_path,
            """\
            class MyView:
                def mount(self, request, **kwargs):
                    self.report = ReportBuilder()
            """,
        )
        assert len(v008) == 1, "V008 must still fire on custom class. Got: %r" % v008

    def test_arbitrary_obj_exists_still_fires(self, tmp_path):
        """Bare `exists()` from arbitrary user object SHOULD still fire V008.
        We intentionally don't add bare `exists` to SAFE_TYPES — too ambiguous
        with user code."""
        v008 = _run_v008(
            tmp_path,
            """\
            class MyView:
                def mount(self, request, **kwargs):
                    self.x = some_object.exists()
            """,
        )
        assert len(v008) == 1, (
            "bare exists() should still fire V008 (intentionally not in set). Got: %r" % v008
        )
