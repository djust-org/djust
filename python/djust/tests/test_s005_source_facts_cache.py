"""S005 reads each LiveView's source once per class object, not once per check run.

``check_configuration`` scans every LiveView subclass alive in the process;
for each it ran ``_extract_exposed_state`` (``inspect.getsource`` +
``ast.parse`` per method) and ``inspect.getsourcelines(cls)`` (a parse of the
class's whole module). A test process holding thousands of LiveView classes
paid that on every call. The per-class facts are now kept in a weak-keyed
cache; these tests pin that the cache is used, that it does not freeze the
auth decision, and that it lets a class go.
"""

import gc
import inspect

from djust.checks import check_configuration
from djust.checks.configuration import _S005_SOURCE_FACTS
from djust.live_view import LiveView
from djust.management.commands import djust_audit


def _make_view(name):
    def mount(self, request, **kwargs):
        self.user_data = {"email": "x@example.com"}

    return type(
        name,
        (LiveView,),
        {
            "exposure_policy": "legacy",
            "__module__": __name__,
            "template_name": "t.html",
            "mount": mount,
        },
    )


def _s005_for(errors, name):
    return [e for e in errors if e.id == "djust.S005" and name in e.msg]


def _counting(monkeypatch):
    calls = []
    original = djust_audit._extract_exposed_state

    def counted(cls):
        calls.append(cls)
        return original(cls)

    monkeypatch.setattr(djust_audit, "_extract_exposed_state", counted)
    return calls


def test_a_second_check_run_does_not_rescan_the_same_class(monkeypatch):
    calls = _counting(monkeypatch)

    # A ``class`` statement (not ``type()``) so the source location is real.
    class S005CacheOnce(LiveView):
        exposure_policy = "legacy"
        template_name = "t.html"

        def mount(self, request, **kwargs):
            self.user_data = {"email": "x@example.com"}

    cls = S005CacheOnce
    del S005CacheOnce
    try:
        first = _s005_for(check_configuration(None), "S005CacheOnce")
        second = _s005_for(check_configuration(None), "S005CacheOnce")
        assert len(first) == 1 and len(second) == 1
        assert calls.count(cls) == 1, "the second run re-read the class source"
        # The cached location is the real one, on both runs.
        expected_line = inspect.getsourcelines(cls)[1]
        assert first[0].line_number == second[0].line_number == expected_line
        assert first[0].file_path == second[0].file_path == inspect.getfile(cls)
    finally:
        del cls
        gc.collect()


def test_auth_added_after_a_run_still_silences_s005():
    cls = _make_view("S005CacheAuthLater")
    try:
        assert _s005_for(check_configuration(None), "S005CacheAuthLater")
        cls.login_required = True
        assert not _s005_for(check_configuration(None), "S005CacheAuthLater")
    finally:
        del cls
        gc.collect()


def test_a_collected_class_leaves_the_cache():
    cls = _make_view("S005CacheCollected")
    check_configuration(None)
    assert cls in _S005_SOURCE_FACTS
    size_with = len(_S005_SOURCE_FACTS)
    del cls
    gc.collect()
    assert len(_S005_SOURCE_FACTS) == size_with - 1
