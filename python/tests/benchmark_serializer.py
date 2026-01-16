import time
from djust.optimization.codegen import generate_serializer_code, compile_serializer


def benchmark_serializer():
    """Benchmark generated serializer vs manual serialization."""

    # Create mock objects (100 items)
    class User:
        email = "john@example.com"
        first_name = "John"
        last_name = "Doe"

        def get_full_name(self):
            return f"{self.first_name} {self.last_name}"

    class Tenant:
        def __init__(self):
            self.user = User()

    class Property:
        name = "123 Main St"
        address = "Main Street"

    class Lease:
        def __init__(self):
            self.property = Property()
            self.tenant = Tenant()

    leases = [Lease() for _ in range(100)]

    # Manual serialization
    def manual_serialize(lease):
        return {
            "property": {
                "name": lease.property.name,
                "address": lease.property.address,
            },
            "tenant": {"user": {"email": lease.tenant.user.email}},
        }

    start = time.time()
    for _ in range(1000):
        [manual_serialize(lease) for lease in leases]
    manual_time = time.time() - start

    # Generated serializer
    code = generate_serializer_code(
        "Lease", ["property.name", "property.address", "tenant.user.email"]
    )
    func_name = code.split("def ")[1].split("(")[0]
    func = compile_serializer(code, func_name)

    start = time.time()
    for _ in range(1000):
        [func(lease) for lease in leases]
    generated_time = time.time() - start

    print(f"Manual serialization:    {manual_time:.3f}s")
    print(f"Generated serialization: {generated_time:.3f}s")
    overhead_pct = (generated_time / manual_time - 1) * 100
    print(f"Overhead: {overhead_pct:.1f}%")

    # Generated should be within 3x of manual (allows for safety checks)
    # The extra overhead is from hasattr + None checks, which provide valuable safety
    # In real usage, this is amortized by caching and query optimization gains
    assert (
        generated_time < manual_time * 3.0
    ), f"Generated serializer too slow: {overhead_pct:.1f}% overhead"
    print(f"✓ Performance acceptable (overhead: {overhead_pct:.1f}%)")
    print("  Note: Overhead is from safety checks (hasattr + None handling)")


def benchmark_code_generation():
    """Benchmark code generation speed."""
    paths = [
        "property.name",
        "property.address",
        "property.monthly_rent",
        "tenant.user.email",
        "tenant.user.get_full_name",
        "tenant.phone",
        "start_date",
        "end_date",
        "monthly_rent",
        "security_deposit",
    ]

    # Warmup
    for _ in range(10):
        _ = generate_serializer_code("Lease", paths)

    # Benchmark
    iterations = 1000
    start = time.time()
    for _ in range(iterations):
        _ = generate_serializer_code("Lease", paths)
    elapsed = time.time() - start

    avg_time_ms = (elapsed / iterations) * 1000
    print(f"\nCode generation: {avg_time_ms:.3f}ms per call")
    print(f"Total: {elapsed:.3f}s for {iterations} iterations")

    # Should be fast (< 5ms per generation)
    assert avg_time_ms < 5.0, f"Code generation too slow: {avg_time_ms:.3f}ms"
    print("✓ Code generation performance target met (<5ms)")


def benchmark_compilation():
    """Benchmark code compilation speed."""
    paths = [
        "property.name",
        "property.address",
        "tenant.user.email",
    ]

    code = generate_serializer_code("Lease", paths)
    func_name = code.split("def ")[1].split("(")[0]

    # Warmup
    for _ in range(10):
        _ = compile_serializer(code, func_name)

    # Benchmark
    iterations = 1000
    start = time.time()
    for _ in range(iterations):
        _ = compile_serializer(code, func_name)
    elapsed = time.time() - start

    avg_time_ms = (elapsed / iterations) * 1000
    print(f"\nCode compilation: {avg_time_ms:.3f}ms per call")
    print(f"Total: {elapsed:.3f}s for {iterations} iterations")

    # Should be fast (< 10ms per compilation)
    assert avg_time_ms < 10.0, f"Compilation too slow: {avg_time_ms:.3f}ms"
    print("✓ Compilation performance target met (<10ms)")


def benchmark_deep_nesting():
    """Benchmark serialization with deeply nested paths."""

    # Create deeply nested mock structure
    class Level5:
        value = "deep"

    class Level4:
        def __init__(self):
            self.level5 = Level5()

    class Level3:
        def __init__(self):
            self.level4 = Level4()

    class Level2:
        def __init__(self):
            self.level3 = Level3()

    class Level1:
        def __init__(self):
            self.level2 = Level2()

    class Root:
        def __init__(self):
            self.level1 = Level1()

    # Generate serializer for deep path
    paths = ["level1.level2.level3.level4.level5.value"]
    code = generate_serializer_code("Root", paths)
    func_name = code.split("def ")[1].split("(")[0]
    func = compile_serializer(code, func_name)

    # Create objects
    roots = [Root() for _ in range(100)]

    # Benchmark
    iterations = 1000
    start = time.time()
    for _ in range(iterations):
        [func(root) for root in roots]
    elapsed = time.time() - start

    total_ms = elapsed * 1000
    per_call_us = (elapsed / (iterations * 100)) * 1000000

    print(f"\nDeep nesting (5 levels): {total_ms:.1f}ms for {iterations * 100} calls")
    print(f"Per call: {per_call_us:.2f}µs")

    # Should still be fast
    assert per_call_us < 100, f"Deep nesting too slow: {per_call_us:.2f}µs"
    print("✓ Deep nesting performance acceptable (<100µs per call)")


def benchmark_many_paths():
    """Benchmark serialization with many parallel paths."""

    class MockModel:
        field1 = "value1"
        field2 = "value2"
        field3 = "value3"
        field4 = "value4"
        field5 = "value5"
        field6 = "value6"
        field7 = "value7"
        field8 = "value8"
        field9 = "value9"
        field10 = "value10"

    # Generate serializer for 10 fields
    paths = [f"field{i}" for i in range(1, 11)]
    code = generate_serializer_code("Model", paths)
    func_name = code.split("def ")[1].split("(")[0]
    func = compile_serializer(code, func_name)

    # Create objects
    objects = [MockModel() for _ in range(100)]

    # Benchmark
    iterations = 1000
    start = time.time()
    for _ in range(iterations):
        [func(obj) for obj in objects]
    elapsed = time.time() - start

    total_ms = elapsed * 1000
    per_call_us = (elapsed / (iterations * 100)) * 1000000

    print(f"\nMany paths (10 fields): {total_ms:.1f}ms for {iterations * 100} calls")
    print(f"Per call: {per_call_us:.2f}µs")

    # Should still be fast
    assert per_call_us < 50, f"Many paths too slow: {per_call_us:.2f}µs"
    print("✓ Many paths performance acceptable (<50µs per call)")


if __name__ == "__main__":
    print("=" * 60)
    print("Serializer Performance Benchmarks")
    print("=" * 60)

    print("\n1. Generated vs Manual Serialization")
    print("-" * 60)
    benchmark_serializer()

    print("\n2. Code Generation Speed")
    print("-" * 60)
    benchmark_code_generation()

    print("\n3. Code Compilation Speed")
    print("-" * 60)
    benchmark_compilation()

    print("\n4. Deep Nesting Performance")
    print("-" * 60)
    benchmark_deep_nesting()

    print("\n5. Many Paths Performance")
    print("-" * 60)
    benchmark_many_paths()

    print("\n" + "=" * 60)
    print("All benchmarks passed!")
    print("=" * 60)
