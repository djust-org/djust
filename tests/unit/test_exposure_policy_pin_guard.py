"""The guard that makes every test LiveView state its exposure policy (plan PR 0d, PR 1).

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
        '(set exposure_policy = "explicit", or "legacy" for a test of that policy): '
        + ", ".join(offenders)
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


def test_the_failure_message_names_the_default_and_both_policies(pytester):
    """The message is the instruction a contributor acts on: it must say what
    the default is now and that a legacy pin is for a test of that policy."""
    before = len(guard._created)
    pytester.makepyfile(
        """
        from djust import LiveView

        def test_unpinned():
            class V(LiveView):
                template = "<div dj-root></div>"
        """
    )
    result = pytester.runpytest_inprocess(
        "-p", "tests.exposure_policy_pin_guard", "-p", "no:cacheprovider", "-p", "no:django"
    )
    del guard._created[before:]
    result.assert_outcomes(passed=1, errors=1)
    result.stdout.fnmatch_lines(
        ['*Set exposure_policy = "explicit" (or "legacy", for a test of the legacy policy)*']
    )
    result.stdout.fnmatch_lines(['*The default is "explicit"*'])


def test_a_view_that_states_explicit_is_not_an_offender():
    """Stating the default is a statement: the guard asks for one, not for legacy."""
    before = len(guard._created)

    class Stated(LiveView):
        exposure_policy = "explicit"

    assert not guard.relies_on_default(Stated)
    assert guard._offenders(guard._created[before:]) == []


def test_a_view_pinned_after_creation_is_judged_by_its_final_policy_even_once_dropped():
    """A snippet executed as written is pinned after the class exists; the test then drops it.

    The merge-queue failure on #4049: ``test_params_form_3689.py`` executes the
    ``handle_params`` docstring, pins the resulting view and lets it go. When the
    class was collected before teardown, the guard fell back to the verdict at
    creation and failed the test, or not, with the collector's timing.
    """
    import gc

    start = len(guard._created)
    late = type("PinnedAfterCreation", (LiveView,), {})
    late.exposure_policy = "legacy"
    del late
    gc.collect()
    assert guard._offenders(guard._created[start:]) == []


def test_a_view_that_stated_its_policy_at_creation_is_not_kept_alive():
    """Only classes that relied on the default are held; the rest stay collectable."""
    held = len(guard._held_until_judged)

    class StatedAtCreation(LiveView):
        exposure_policy = "legacy"

    assert len(guard._held_until_judged) == held
