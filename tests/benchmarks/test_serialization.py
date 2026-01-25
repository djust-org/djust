"""
Benchmarks for serialization performance.

These tests measure the performance of djust's code-generated serializers
compared to manual serialization.
"""

import pytest

from djust.optimization.codegen import generate_serializer_code, compile_serializer


class TestSerializerGeneration:
    """Benchmarks for serializer code generation."""

    @pytest.mark.benchmark(group="serializer_codegen")
    def test_simple_codegen(self, benchmark):
        """Benchmark code generation for simple paths."""
        paths = ["name", "email", "active"]
        result = benchmark(generate_serializer_code, "User", paths)
        # Function name is lowercase with hash: serialize_user_xxxxx
        assert "def serialize_user" in result

    @pytest.mark.benchmark(group="serializer_codegen")
    def test_nested_codegen(self, benchmark):
        """Benchmark code generation for nested paths."""
        paths = [
            "property.name",
            "property.address",
            "tenant.user.email",
        ]
        result = benchmark(generate_serializer_code, "Lease", paths)
        assert "def serialize_lease" in result

    @pytest.mark.benchmark(group="serializer_codegen")
    def test_many_paths_codegen(self, benchmark):
        """Benchmark code generation for many paths."""
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
        result = benchmark(generate_serializer_code, "Lease", paths)
        assert "def serialize_lease" in result


class TestSerializerCompilation:
    """Benchmarks for serializer compilation."""

    @pytest.mark.benchmark(group="serializer_compile")
    def test_simple_compile(self, benchmark):
        """Benchmark compilation of simple serializer."""
        code = generate_serializer_code("User", ["name", "email"])
        func_name = code.split("def ")[1].split("(")[0]

        result = benchmark(compile_serializer, code, func_name)
        assert callable(result)

    @pytest.mark.benchmark(group="serializer_compile")
    def test_complex_compile(self, benchmark):
        """Benchmark compilation of complex serializer."""
        code = generate_serializer_code(
            "Lease",
            [
                "property.name",
                "property.address",
                "tenant.user.email",
                "tenant.phone",
                "start_date",
            ],
        )
        func_name = code.split("def ")[1].split("(")[0]

        result = benchmark(compile_serializer, code, func_name)
        assert callable(result)


class TestSerializerExecution:
    """Benchmarks for serializer execution."""

    @pytest.mark.benchmark(group="serializer_exec")
    def test_generated_vs_manual(self, benchmark, mock_lease):
        """Benchmark generated serializer execution."""
        paths = [
            "property.name",
            "property.address",
            "tenant.user.email",
        ]
        code = generate_serializer_code("Lease", paths)
        func_name = code.split("def ")[1].split("(")[0]
        serializer = compile_serializer(code, func_name)

        result = benchmark(serializer, mock_lease)
        assert result["property"]["name"] == "Sunset Apartments #101"

    @pytest.mark.benchmark(group="serializer_exec")
    def test_manual_serialization(self, benchmark, mock_lease):
        """Benchmark manual serialization for comparison."""

        def manual_serialize(lease):
            return {
                "property": {
                    "name": lease.property.name,
                    "address": lease.property.address,
                },
                "tenant": {
                    "user": {
                        "email": lease.tenant.user.email,
                    }
                },
            }

        result = benchmark(manual_serialize, mock_lease)
        assert result["property"]["name"] == "Sunset Apartments #101"

    @pytest.mark.benchmark(group="serializer_exec")
    def test_batch_serialization(self, benchmark, mock_leases):
        """Benchmark batch serialization of many objects."""
        paths = [
            "property.name",
            "property.address",
            "tenant.user.email",
        ]
        code = generate_serializer_code("Lease", paths)
        func_name = code.split("def ")[1].split("(")[0]
        serializer = compile_serializer(code, func_name)

        def serialize_batch():
            return [serializer(lease) for lease in mock_leases]

        results = benchmark(serialize_batch)
        assert len(results) == 100


class TestDeepNesting:
    """Benchmarks for deeply nested serialization."""

    @pytest.fixture
    def deep_object(self):
        """Create deeply nested object."""

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

        return Root()

    @pytest.mark.benchmark(group="serializer_deep")
    def test_deep_nesting_generated(self, benchmark, deep_object):
        """Benchmark deep nesting with generated serializer."""
        paths = ["level1.level2.level3.level4.level5.value"]
        code = generate_serializer_code("Root", paths)
        func_name = code.split("def ")[1].split("(")[0]
        serializer = compile_serializer(code, func_name)

        result = benchmark(serializer, deep_object)
        assert result["level1"]["level2"]["level3"]["level4"]["level5"]["value"] == "deep"

    @pytest.mark.benchmark(group="serializer_deep")
    def test_deep_nesting_manual(self, benchmark, deep_object):
        """Benchmark deep nesting with manual access."""

        def manual_access(obj):
            return {
                "level1": {
                    "level2": {
                        "level3": {
                            "level4": {
                                "level5": {"value": obj.level1.level2.level3.level4.level5.value}
                            }
                        }
                    }
                }
            }

        result = benchmark(manual_access, deep_object)
        assert result["level1"]["level2"]["level3"]["level4"]["level5"]["value"] == "deep"


class TestManyFields:
    """Benchmarks for serialization with many fields."""

    @pytest.fixture
    def wide_object(self):
        """Create object with many fields."""

        class WideModel:
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

        return WideModel()

    @pytest.mark.benchmark(group="serializer_wide")
    def test_many_fields_generated(self, benchmark, wide_object):
        """Benchmark many fields with generated serializer."""
        paths = [f"field{i}" for i in range(1, 11)]
        code = generate_serializer_code("Model", paths)
        func_name = code.split("def ")[1].split("(")[0]
        serializer = compile_serializer(code, func_name)

        result = benchmark(serializer, wide_object)
        assert result["field1"] == "value1"
        assert result["field10"] == "value10"

    @pytest.mark.benchmark(group="serializer_wide")
    def test_many_fields_manual(self, benchmark, wide_object):
        """Benchmark many fields with manual serialization."""

        def manual_serialize(obj):
            return {
                "field1": obj.field1,
                "field2": obj.field2,
                "field3": obj.field3,
                "field4": obj.field4,
                "field5": obj.field5,
                "field6": obj.field6,
                "field7": obj.field7,
                "field8": obj.field8,
                "field9": obj.field9,
                "field10": obj.field10,
            }

        result = benchmark(manual_serialize, wide_object)
        assert result["field1"] == "value1"
