"""
Benchmark fixtures and configuration for pytest-benchmark.
"""

import pytest


# Configure benchmark settings
def pytest_configure(config):
    """Configure pytest-benchmark defaults."""
    # These can be overridden via command line
    config.addinivalue_line(
        "markers", "benchmark: marks tests as benchmarks"
    )


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
                }
            }
        },
        "items": [
            {"id": i, "name": f"Item {i}", "price": i * 10.5}
            for i in range(10)
        ],
        "site": {
            "name": "My Site",
            "version": "1.0.0",
        }
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
