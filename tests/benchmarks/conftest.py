"""
Benchmark fixtures and configuration for pytest-benchmark.

Shared budgets and helpers live here so additional benchmark files in this
directory can use them without duplication. See `_assert_benchmark_under`
for the xdist-safe assertion contract.
"""

import pytest


# Per-segment budgets (ROADMAP v0.6.0 perf-profile targets — see
# docs/performance/v0.6.0-profile.md). New benchmark files should import these
# rather than redefining them.
TARGET_PER_EVENT_S = 0.002  # 2 ms
TARGET_LIST_UPDATE_S = 0.005  # 5 ms
# WebSocket mount runs the full HTTP-render pipeline plus channels-layer +
# Redis serialization round-trip + initial state diff, so it gets a 100 ms
# budget (20× the list-update budget). Named explicitly so the rationale
# lives in the constant name, not the multiplier — see #1034.
TARGET_WS_MOUNT_S = 0.100  # 100 ms (20× list-update)


def _assert_benchmark_under(benchmark, target_s: float, label: str) -> None:
    """Assert benchmark MEDIAN < target, but gracefully degrade under xdist.

    Uses the **median**, not the mean: the mean is dragged past the SLA by a
    handful of GC / scheduling-pause outliers (a single 30ms spike among
    thousands of ~4ms rounds), so a mean-based threshold false-fails on a
    loaded machine even when typical performance is comfortably under target.
    The median reflects the actual per-call cost and is immune to those
    outliers — the right statistic for a latency SLA. (Same outlier-sensitivity
    fragility class as the #1795 wall-clock flaky test; canonicalized in the
    v1.0.5-4/-5 retro arc — assert a robust statistic, not an outlier-prone one.)

    pytest-benchmark's stats collection is disabled when running under
    pytest-xdist (the `-n auto` CI invocation), so `benchmark.stats[...]`
    raises because `stats` is empty. In that case the function is still
    executed for correctness, but the threshold assertion is skipped — the
    ``benchmarks`` job in `.github/workflows/test.yml` (serial,
    ``--benchmark-only``) enforces it.

    That sentence used to name a job that **did not exist** (#2156). Every
    other CI job runs ``-n auto``, so nothing enforced these thresholds except
    the serial pre-push hook — in a process that had just executed 10,000
    tests, where a warm and fragmented heap makes the median systematically
    slower. ``test_vdom_diff_list_reorder`` measured 7.57ms there against its
    5ms target, so it blocked every push on main while measuring the
    environment rather than the code.

    Numbers below are from the enforcing job itself, since that is the only
    environment where a latency figure means anything. On the runner:

        vdom_diff_list_reorder   median 0.656 ms  target   5 ms  (13% of budget)
        vdom_diff_list_append    median 0.644 ms  target   5 ms  (13%)
        websocket_mount_counter  median 11.4  ms  target 100 ms  (11%)

    All 58 pass, so no target was loosened. Reaching for a bigger number is the
    reflex this issue exists to stop: it is what turned a 10ms bound into 100ms
    in ``test_redis_serialization_performance``, which then flaked again.

    Do NOT tune these against a local run. The same benchmark spans roughly
    11x across environments (0.656 ms on the runner, ~3.8 ms on a contended
    laptop, 7.57 ms after 10,000 tests in-process), which is the entire finding
    of #2156.

    `test_benchmark_enforcement_2156.py` pins the job's existence, so this
    docstring cannot quietly become a false claim a second time.
    """
    if getattr(benchmark, "disabled", False):
        return
    try:
        median = benchmark.stats["median"]
    except (KeyError, TypeError, AttributeError):
        return
    assert median < target_s, (
        f"{label} median {median * 1000:.2f}ms exceeds {target_s * 1000:.0f}ms target"
    )


# Configure benchmark settings
def pytest_configure(config):
    """Configure pytest-benchmark defaults."""
    # These can be overridden via command line
    config.addinivalue_line("markers", "benchmark: marks tests as benchmarks")


@pytest.fixture
def simple_context():
    """A simple template context for basic benchmarks."""
    return {
        "name": "World",
        "count": 42,
        "active": True,
    }


@pytest.fixture
def nested_context():
    """A nested context for more complex benchmarks."""
    return {
        "user": {
            "name": "John Doe",
            "email": "john@example.com",
            "profile": {
                "bio": "Developer",
                "settings": {
                    "theme": "dark",
                    "notifications": True,
                },
            },
        },
        "items": [{"id": i, "name": f"Item {i}", "price": i * 10.5} for i in range(10)],
        "site": {
            "name": "My Site",
            "version": "1.0.0",
        },
    }


@pytest.fixture
def large_list_context():
    """A context with a large list for iteration benchmarks."""
    return {
        "items": [
            {
                "id": i,
                "name": f"Product {i}",
                "description": f"Description for product {i}",
                "price": i * 10.5,
                "in_stock": i % 2 == 0,
            }
            for i in range(100)
        ]
    }


@pytest.fixture
def mock_lease():
    """Mock lease object for serialization benchmarks."""

    class User:
        email = "john@example.com"
        first_name = "John"
        last_name = "Doe"

        def get_full_name(self):
            return f"{self.first_name} {self.last_name}"

    class Tenant:
        def __init__(self):
            self.user = User()
            self.phone = "555-1234"

    class Property:
        name = "Sunset Apartments #101"
        address = "123 Main St"
        city = "San Francisco"
        monthly_rent = 2500

    class Lease:
        def __init__(self):
            self.property = Property()
            self.tenant = Tenant()
            self.start_date = "2024-01-01"
            self.end_date = "2025-01-01"
            self.security_deposit = 5000

    return Lease()


@pytest.fixture
def mock_leases(mock_lease):
    """List of mock lease objects."""

    # Create 100 lease instances
    class User:
        email = "john@example.com"
        first_name = "John"
        last_name = "Doe"

        def get_full_name(self):
            return f"{self.first_name} {self.last_name}"

    class Tenant:
        def __init__(self):
            self.user = User()
            self.phone = "555-1234"

    class Property:
        name = "Sunset Apartments #101"
        address = "123 Main St"
        city = "San Francisco"
        monthly_rent = 2500

    class Lease:
        def __init__(self):
            self.property = Property()
            self.tenant = Tenant()
            self.start_date = "2024-01-01"
            self.end_date = "2025-01-01"
            self.security_deposit = 5000

    return [Lease() for _ in range(100)]
