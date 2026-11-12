"""The guard that keeps test LiveViews pinned to an exposure policy (plan PR 0d).

``tests/exposure_policy_pin_guard.py`` fails, per test, a view created inside a
test. Views created while a test module is imported have no test to attach to,
so they are reported here. Under xdist every worker imports every collected
module, so this one test sees all of them.
"""

from djust import LiveView
from tests import exposure_policy_pin_guard as guard


def test_no_module_level_liveview_relies_on_the_default_policy():
    offenders = guard.import_time_offenders()
    assert not offenders, (
        "LiveViews defined at module level without an exposure_policy "
        '(set exposure_policy = "legacy" or "explicit"): ' + ", ".join(offenders)
    )


def test_the_recorder_is_installed():
    """Without the recorder nothing is recorded and both checks pass vacuously."""
    from djust import _component_subscriptions

    assert getattr(_component_subscriptions.compile_subscriptions, "_djust_pin_guard", False)


def test_a_pinned_view_passes_and_is_recorded():
    before = len(guard._created)

    class Pinned(LiveView):
        exposure_policy = "legacy"

    class Child(Pinned):
        pass

    assert len(guard._created) == before + 2
    assert not guard.relies_on_default(Pinned)
    assert not guard.relies_on_default(Child)
    assert guard._offenders(guard._created[before:]) == []


def test_an_unpinned_view_is_an_offender_until_a_policy_is_assigned():
    before = len(guard._created)
    unpinned = type("Unpinned", (LiveView,), {"exposure_policy": "legacy"})
    del unpinned.exposure_policy
    assert guard.relies_on_default(unpinned)
    assert [name.rsplit(".", 1)[-1] for name in guard._offenders(guard._created[before:])] == [
        "Unpinned"
    ]
    unpinned.exposure_policy = "explicit"  # also keeps this test's own teardown check quiet
    assert guard._offenders(guard._created[before:]) == []


def test_a_shipped_view_is_not_the_tests_to_pin():
    shipped = type("Shipped", (LiveView,), {"__module__": "djust.somewhere", "x": 1})
    assert not guard.relies_on_default(shipped)


def test_the_fixture_fails_a_test_that_defines_an_unpinned_view(pytester):
    before = len(guard._created)
    pytester.makepyfile(
        """
        from djust import LiveView

        def test_unpinned():
            class V(LiveView):
                template = "<div dj-root></div>"

        def test_pinned():
            class V(LiveView):
                exposure_policy = "legacy"
                template = "<div dj-root></div>"
        """
    )
    # In-process: Django is already set up by the outer session (hence no:django).
    result = pytester.runpytest_inprocess(
        "-p", "tests.exposure_policy_pin_guard", "-p", "no:cacheprovider", "-p", "no:django"
    )
    # The inner run shares this process, so drop its classes from this test's own record.
    del guard._created[before:]
    result.assert_outcomes(passed=2, errors=1)
    result.stdout.fnmatch_lines(["*LiveView defined without an exposure_policy*test_unpinned*V*"])
