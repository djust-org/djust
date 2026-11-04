"""A test must not leave a ``User`` row behind for the next test on its xdist worker.

``@pytest.mark.django_db`` wraps the TEST THREAD's connection in a transaction and rolls it back.
``sync_to_async(User.objects.create_user)(...)`` does not run on that thread: asgiref hands it to an
executor thread, which has its own connection. The write autocommits there, the rollback never sees
it, and the row stays for the rest of the process. Nothing fails at the polluter. Whichever test
later reads the table by position (``User.objects.first()`` is the lowest pk) reads the stray row,
and which tests share a worker is the scheduler's choice, so it fails on one Python version of one
CI run and passes on the others (main, run 41774238410: ``obj.users.first.password`` printed ``""``,
the password of a leftover ``User.objects.create(username=...)`` from
``tests/integration/test_object_permission_event.py``).

The cure for a test that has to write through ``sync_to_async`` is ``django_db(transaction=True)``:
a transactional test flushes at teardown. This file pins that, mechanically: no test that holds a
plain (rolled-back) database may write a user through ``sync_to_async``. It scans ``tests/``,
``python/tests/`` and ``python/djust/tests/`` by AST, so a new polluter fails here, at the polluter,
naming the test, rather than as an unrelated failure far away.

Scope, stated: this pins USER rows written through ``sync_to_async`` / ``database_sync_to_async``.
Other tables (``Session`` rows saved through ``sync_to_async``) leak the same way but nothing reads
them by position; they are not pinned.

Gate-off: the scanner is exercised against synthetic sources, so a scanner that stopped matching
(the mutation text not found, or the mark logic inverted) fails ``TestTheScanner`` rather than
silently passing the repo scan.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ROOTS = ("tests", "python/tests", "python/djust/tests")

#: A user write inside the callable handed to ``sync_to_async``: a manager call on the user model,
#: or the ``create_user`` / ``create_superuser`` helpers.
USER_WRITE = re.compile(
    r"(?:\bUser|get_user_model\(\)|\bdjango_user_model)\.objects\.(?:create|create_user"
    r"|create_superuser|get_or_create|update_or_create|bulk_create)\b"
    r"|\.create_user\b|\.create_superuser\b"
)
TRANSACTIONAL_FIXTURES = {"transactional_db", "live_server", "django_db_serialized_rollback"}


def _marks_of(node: ast.AST) -> list[str]:
    return [ast.unparse(d) for d in getattr(node, "decorator_list", [])]


def _pytestmarks(body: list[ast.stmt]) -> list[str]:
    out: list[str] = []
    for node in body:
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", "") == "pytestmark" for t in node.targets
        ):
            out.append(ast.unparse(node.value))
    return out


def _has_database(marks: list[str], fixtures: list[str]) -> bool:
    return (
        any("django_db" in m for m in marks)
        or "db" in fixtures
        or "django_user_model" in fixtures
        or bool(TRANSACTIONAL_FIXTURES & set(fixtures))
    )


def _is_transactional(marks: list[str], fixtures: list[str]) -> bool:
    return any("django_db" in m and "transaction=True" in m for m in marks) or bool(
        TRANSACTIONAL_FIXTURES & set(fixtures)
    )


def _writes_a_user_through_sync_to_async(fn: ast.AST) -> bool:
    for call in ast.walk(fn):
        if not isinstance(call, ast.Call):
            continue
        name = ast.unparse(call.func)
        if not name.endswith("sync_to_async"):
            continue
        if any(USER_WRITE.search(ast.unparse(arg)) for arg in call.args):
            return True
    return False


def scan(source: str) -> tuple[list[str], list[str]]:
    """Return ``(leaking, transactional)`` test names in ``source``.

    ``leaking``: holds a rolled-back database and writes a user through ``sync_to_async``.
    ``transactional``: writes one but under a transactional mark (counted so the scan is shown
    to be looking at something).
    """
    tree = ast.parse(source)
    leaking: list[str] = []
    transactional: list[str] = []

    def visit(body: list[ast.stmt], inherited: list[str]) -> None:
        for node in body:
            if isinstance(node, ast.ClassDef):
                visit(node.body, inherited + _marks_of(node) + _pytestmarks(node.body))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
                "test"
            ):
                if not _writes_a_user_through_sync_to_async(node):
                    continue
                marks = inherited + _marks_of(node)
                fixtures = [a.arg for a in node.args.args]
                if not _has_database(marks, fixtures):
                    continue
                (transactional if _is_transactional(marks, fixtures) else leaking).append(node.name)

    visit(tree.body, _pytestmarks(tree.body))
    return leaking, transactional


class TestTheScanner:
    """The scan must be able to see a polluter, or the repo test below proves nothing."""

    POLLUTER = """
import pytest
from asgiref.sync import sync_to_async

@pytest.mark.django_db
@pytest.mark.asyncio
async def test_leaks():
    from django.contrib.auth.models import User
    await sync_to_async(User.objects.create)(username="x")
"""

    def test_a_plain_django_db_async_writer_is_flagged(self):
        assert scan(self.POLLUTER) == (["test_leaks"], [])

    def test_the_same_writer_under_transaction_true_is_not(self):
        fixed = self.POLLUTER.replace("django_db", "django_db(transaction=True)")
        assert scan(fixed) == ([], ["test_leaks"])

    @pytest.mark.parametrize(
        "spelling",
        [
            "sync_to_async(get_user_model().objects.create_user)('x')",
            "sync_to_async(django_user_model.objects.create_superuser)('x')",
            "sync_to_async(lambda: User.objects.create(username='x'))()",
            "database_sync_to_async(User.objects.get_or_create)(username='x')",
        ],
    )
    def test_every_spelling_the_repo_uses_is_matched(self, spelling):
        src = (
            f"import pytest\n\n@pytest.mark.django_db\nasync def test_w():\n    await {spelling}\n"
        )
        assert scan(src) == (["test_w"], [])

    def test_a_module_level_or_class_level_mark_is_inherited(self):
        module = "import pytest\npytestmark = pytest.mark.django_db\n\nasync def test_w():\n    await sync_to_async(User.objects.create)()\n"
        klass = (
            "import pytest\n\n@pytest.mark.django_db\nclass TestC:\n"
            "    async def test_w(self):\n        await sync_to_async(User.objects.create)()\n"
        )
        assert scan(module)[0] == ["test_w"]
        assert scan(klass)[0] == ["test_w"]

    def test_a_transactional_fixture_counts_as_transactional(self):
        src = (
            "async def test_w(transactional_db):\n    await sync_to_async(User.objects.create)()\n"
        )
        assert scan(src) == ([], ["test_w"])

    def test_a_test_with_no_database_is_not_this_scans_business(self):
        src = "async def test_w():\n    await sync_to_async(User.objects.create)()\n"
        assert scan(src) == ([], [])

    def test_a_write_to_another_model_is_not_flagged(self):
        src = "import pytest\n\n@pytest.mark.django_db\nasync def test_w():\n    await sync_to_async(Group.objects.create)(name='g')\n"
        assert scan(src) == ([], [])


def _repo_scan() -> tuple[dict[str, list[str]], int]:
    leaking: dict[str, list[str]] = {}
    transactional = 0
    for root in ROOTS:
        for path in sorted((ROOT / root).rglob("test_*.py")):
            try:
                found, txn = scan(path.read_text(encoding="utf-8"))
            except SyntaxError:  # pragma: no cover - a broken file fails its own collection
                continue
            if found:
                leaking[str(path.relative_to(ROOT))] = found
            transactional += len(txn)
    return leaking, transactional


def test_no_test_leaves_a_user_row_behind_through_sync_to_async():
    leaking, transactional = _repo_scan()
    assert transactional > 0, (
        "the scan found no transactional user writer at all: it has stopped matching what the "
        "repo writes, and an empty `leaking` below would then mean nothing"
    )
    assert not leaking, (
        "these tests write a User through sync_to_async under a rolled-back django_db; the row "
        "commits on the executor thread's connection and stays for every later test on the "
        "worker. Mark them django_db(transaction=True):\n"
        + "\n".join(f"  {path}: {', '.join(names)}" for path, names in leaking.items())
    )
