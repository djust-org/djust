"""Deep partials match Django while recursive cycles fail safely."""

import subprocess
import sys

import django
import pytest
from django.template import Context, Engine

from djust._rust import render_template

pytestmark = pytest.mark.skipif(
    django.VERSION < (6, 0), reason="Django template partials require 6.0+"
)

SOURCE = (
    "{% partialdef p %}X{% if node.child %}{% with node=node.child %}"
    "{% partial p %}{% endwith %}{% endif %}{% endpartialdef %}{% partial p %}"
)


def make_tree(depth):
    node = None
    for _ in range(depth):
        node = {"child": node}
    return {"node": node}


@pytest.mark.parametrize("depth", [65, 80, 130])
def test_finite_partial_depth_matches_django(depth):
    previous = sys.getrecursionlimit()
    try:
        sys.setrecursionlimit(max(previous, 2500))
        expected = Engine().from_string(SOURCE).render(Context(make_tree(depth)))
        assert render_template(SOURCE, make_tree(depth)) == expected == "X" * depth
    finally:
        sys.setrecursionlimit(previous)


def test_raised_python_recursion_limit_allows_deep_finite_partials():
    source = (
        '{% partialdef p %}{% if n %}X{% with n=n|add:"-1" %}'
        "{% partial p %}{% endwith %}{% endif %}{% endpartialdef %}{% partial p %}"
    )
    previous = sys.getrecursionlimit()
    try:
        sys.setrecursionlimit(20000)
        expected = Engine().from_string(source).render(Context({"n": 1100}))
        assert render_template(source, {"n": 1100}) == expected == "X" * 1100
    finally:
        sys.setrecursionlimit(previous)


def test_partial_limit_tracks_python_recursion_limit_between_renders():
    source = (
        '{% partialdef p %}{% if n %}X{% with n=n|add:"-1" %}'
        "{% partial p %}{% endwith %}{% endif %}{% endpartialdef %}{% partial p %}"
    )
    previous = sys.getrecursionlimit()
    try:
        sys.setrecursionlimit(150)
        with pytest.raises(RecursionError):
            render_template(source, {"n": 200})
        sys.setrecursionlimit(500)
        assert render_template(source, {"n": 200}) == "X" * 200
    finally:
        sys.setrecursionlimit(previous)


def test_deep_partial_can_resolve_python_property():
    class Target:
        @property
        def label(self):
            return "from Python"

    source = (
        '{% partialdef p %}{% if n %}{% with n=n|add:"-1" %}'
        "{% partial p %}{% endwith %}{% else %}{{ target.label }}"
        "{% endif %}{% endpartialdef %}{% partial p %}"
    )
    previous = sys.getrecursionlimit()
    try:
        sys.setrecursionlimit(20000)
        values = {"n": 1100, "target": Target()}
        expected = Engine().from_string(source).render(Context(values))
        assert render_template(source, values) == expected == "from Python"
    finally:
        sys.setrecursionlimit(previous)


def test_partial_recursion_raises_recursion_error_and_restores_state():
    source = "{% partialdef p %}{% partial p %}{% endpartialdef %}{% partial p %}"
    for _ in range(2):
        with pytest.raises(RecursionError):
            render_template(source, {})
        assert render_template("{% partialdef p %}OK{% endpartialdef %}{% partial p %}", {}) == "OK"


def test_finite_and_recursive_partials_are_safe_on_small_thread_stack():
    # Run in a child so a native stack overflow is a test failure, not a crash
    # of the whole suite. A template cycle is bounded by Python's recursion limit.
    program = r"""
import sys
import threading
from djust._rust import render_template
sys.setrecursionlimit(2500)
source = "{% partialdef p %}X{% if node.child %}{% with node=node.child %}{% partial p %}{% endwith %}{% endif %}{% endpartialdef %}{% partial p %}"
recursive = "{% partialdef p %}{% partial p %}{% endpartialdef %}{% partial p %}"
node = None
for _ in range(130):
    node = {"child": node}
def run():
    assert render_template(source, {"node": node}) == "X" * 130
    for _ in range(2):
        try:
            render_template(recursive, {})
        except RecursionError:
            pass
        else:
            raise AssertionError("unbounded partial did not raise RecursionError")
        assert render_template("{% partialdef p %}OK{% endpartialdef %}{% partial p %}", {}) == "OK"
threading.stack_size(512 * 1024)
def checked():
    try:
        run()
    except BaseException as exc:
        failures.append(exc)
failures = []
t = threading.Thread(target=checked)
t.start()
t.join()
if failures:
    raise failures[0]
"""
    result = subprocess.run(
        [sys.executable, "-c", program], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr
