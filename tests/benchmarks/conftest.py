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
    """Assert benchmark mean < target, but gracefully degrade under xdist.

    pytest-benchmark's stats collection is disabled when running under
    pytest-xdist (the `-n auto` CI invocation), so `benchmark.stats["mean"]`
    raises because `stats` is empty. In that case the function is still
    executed for correctness, but the threshold assertion is skipped —
    the benchmark-gated CI job (`--benchmark-only` serial) enforces it.
    """
    if getattr(benchmark, "disabled", False):
        return
    try:
        mean = benchmark.stats["mean"]
    except (KeyError, TypeError, AttributeError):
        return
    assert mean < target_s, (
        f"{label} mean {mean * 1000:.2f}ms exceeds {target_s * 1000:.0f}ms target"
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
