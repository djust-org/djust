import pytest
from djust.optimization.codegen import (
    generate_serializer_code,
    compile_serializer,
    _build_path_tree,
)


def test_build_path_tree():
    """Test path tree construction."""
    paths = ["property.name", "property.address", "tenant.user.email"]
    tree = _build_path_tree(paths)

    assert "property" in tree
    assert "name" in tree["property"]
    assert "address" in tree["property"]

    assert "tenant" in tree
    assert "user" in tree["tenant"]
    assert "email" in tree["tenant"]["user"]


def test_build_path_tree_single_level():
    """Test path tree with single-level paths."""
    paths = ["name", "email", "age"]
    tree = _build_path_tree(paths)

    assert "name" in tree
    assert "email" in tree
    assert "age" in tree
    # Each should be a leaf (empty dict)
    assert tree["name"] == {}
    assert tree["email"] == {}
    assert tree["age"] == {}


def test_build_path_tree_deep_nesting():
    """Test path tree with deeply nested paths."""
    paths = ["a.b.c.d.e"]
    tree = _build_path_tree(paths)

    assert "a" in tree
    assert "b" in tree["a"]
    assert "c" in tree["a"]["b"]
    assert "d" in tree["a"]["b"]["c"]
    assert "e" in tree["a"]["b"]["c"]["d"]


def test_generate_simple_code():
    """Test code generation for simple path."""
    code = generate_serializer_code("Lease", ["property.name"])

    assert "def serialize_lease_" in code
    assert "result = {}" in code
    assert "obj.property" in code
    assert "['name']" in code
    assert "return result" in code


def test_generate_nested_code():
    """Test code generation for nested path."""
    code = generate_serializer_code("Lease", ["tenant.user.email"])

    assert "hasattr(obj, 'tenant')" in code
    assert "obj.tenant is not None" in code
    assert "hasattr(obj.tenant, 'user')" in code
    assert "obj.tenant.user is not None" in code
    assert "['email']" in code


def test_generate_multiple_paths():
    """Test code generation for multiple paths."""
    code = generate_serializer_code(
        "Lease", ["property.name", "property.address", "tenant.user.email"]
    )

    # Should have safety checks
    assert "hasattr(obj, 'property')" in code
    assert "hasattr(obj, 'tenant')" in code

    # Should access all fields
    assert "['name']" in code
    assert "['address']" in code
    assert "['email']" in code


def test_generate_method_call():
    """Test code generation for method calls (get_* pattern)."""
    code = generate_serializer_code("User", ["get_full_name"])

    # Should handle method calls with try/except
    assert "try:" in code
    assert "get_full_name()" in code
    assert "except Exception:" in code


def test_compile_and_execute():
    """Test compiling and executing generated code."""

    # Create mock object
    class MockProperty:
        name = "123 Main St"
        address = "Main Street"

    class MockLease:
        def __init__(self):
            self.property = MockProperty()

    # Generate and compile
    code = generate_serializer_code("Lease", ["property.name", "property.address"])
    func_name = code.split("def ")[1].split("(")[0]
    func = compile_serializer(code, func_name)

    # Execute
    lease = MockLease()
    result = func(lease)

    # Verify
    assert result["property"]["name"] == "123 Main St"
    assert result["property"]["address"] == "Main Street"


def test_compile_single_level():
    """Test compiling single-level attribute access."""

    class MockUser:
        name = "John Doe"
        email = "john@example.com"

    code = generate_serializer_code("User", ["name", "email"])
    func_name = code.split("def ")[1].split("(")[0]
    func = compile_serializer(code, func_name)

    user = MockUser()
    result = func(user)

    assert result["name"] == "John Doe"
    assert result["email"] == "john@example.com"


def test_none_safety():
    """Test None safety in generated code."""

    class MockLease:
        property = None  # Simulate deleted related object

    code = generate_serializer_code("Lease", ["property.name"])
    func_name = code.split("def ")[1].split("(")[0]
    func = compile_serializer(code, func_name)

    lease = MockLease()
    result = func(lease)

    # Should not raise exception, just skip
    # Result might be empty dict or have empty property
    assert isinstance(result, dict)


def test_method_call():
    """Test method call serialization."""

    class MockUser:
        first_name = "John"
        last_name = "Doe"

        def get_full_name(self):
            return f"{self.first_name} {self.last_name}"

    class MockTenant:
        def __init__(self):
            self.user = MockUser()

    class MockLease:
        def __init__(self):
            self.tenant = MockTenant()

    code = generate_serializer_code("Lease", ["tenant.user.get_full_name"])
    func_name = code.split("def ")[1].split("(")[0]
    func = compile_serializer(code, func_name)

    lease = MockLease()
    result = func(lease)

    assert result["tenant"]["user"]["get_full_name"] == "John Doe"


def test_real_world_lease():
    """Test with structure matching real Lease model."""

    class User:
        def __init__(self):
            self.email = "john@example.com"
            self.first_name = "John"
            self.last_name = "Doe"

        def get_full_name(self):
            return f"{self.first_name} {self.last_name}"

    class Tenant:
        def __init__(self):
            self.user = User()

    class Property:
        name = "123 Main St"
        address = "Main Street"
        monthly_rent = 1500

    class Lease:
        def __init__(self):
            self.property = Property()
            self.tenant = Tenant()
            self.end_date = "2026-01-01"

    paths = [
        "property.name",
        "property.address",
        "property.monthly_rent",
        "tenant.user.email",
        "tenant.user.get_full_name",
        "end_date",
    ]

    code = generate_serializer_code("Lease", paths)
    func_name = code.split("def ")[1].split("(")[0]
    func = compile_serializer(code, func_name)

    lease = Lease()
    result = func(lease)

    # Verify all fields serialized
    assert result["property"]["name"] == "123 Main St"
    assert result["property"]["address"] == "Main Street"
    assert result["property"]["monthly_rent"] == 1500
    assert result["tenant"]["user"]["email"] == "john@example.com"
    assert result["tenant"]["user"]["get_full_name"] == "John Doe"
    assert result["end_date"] == "2026-01-01"


def test_missing_attribute():
    """Test handling of missing attributes."""

    class MockUser:
        email = "john@example.com"
        # missing 'name' attribute

    code = generate_serializer_code("User", ["email", "name"])
    func_name = code.split("def ")[1].split("(")[0]
    func = compile_serializer(code, func_name)

    user = MockUser()
    result = func(user)

    # Should have email but not name
    assert result["email"] == "john@example.com"
    assert "name" not in result


def test_nested_none():
    """Test nested None handling."""

    class MockUser:
        profile = None  # Missing profile

    class MockProfile:
        bio = "Test bio"

    code = generate_serializer_code("User", ["profile.bio"])
    func_name = code.split("def ")[1].split("(")[0]
    func = compile_serializer(code, func_name)

    user = MockUser()
    result = func(user)

    # Should handle None gracefully
    assert isinstance(result, dict)
    # profile.bio should not be in result
    assert "profile" not in result or "bio" not in result.get("profile", {})


def test_method_call_failure():
    """Test graceful handling of method call failures."""

    class MockUser:
        def get_full_name(self):
            raise ValueError("Method failed")

    code = generate_serializer_code("User", ["get_full_name"])
    func_name = code.split("def ")[1].split("(")[0]
    func = compile_serializer(code, func_name)

    user = MockUser()
    result = func(user)

    # Should not crash, just skip the failed method
    assert isinstance(result, dict)
    # get_full_name should not be in result due to exception
    assert "get_full_name" not in result


def test_empty_paths():
    """Test serializer with no paths."""
    code = generate_serializer_code("User", [])
    func_name = code.split("def ")[1].split("(")[0]
    func = compile_serializer(code, func_name)

    class MockUser:
        name = "Test"

    user = MockUser()
    result = func(user)

    # Should return empty dict
    assert result == {}


def test_duplicate_paths():
    """Test handling of duplicate paths."""
    code = generate_serializer_code("User", ["email", "email", "name", "name", "email"])
    func_name = code.split("def ")[1].split("(")[0]
    func = compile_serializer(code, func_name)

    class MockUser:
        email = "test@example.com"
        name = "Test User"

    user = MockUser()
    result = func(user)

    # Should deduplicate automatically
    assert result["email"] == "test@example.com"
    assert result["name"] == "Test User"


def test_code_determinism():
    """Test that same paths produce same code."""
    paths1 = ["property.name", "tenant.user.email"]
    paths2 = ["property.name", "tenant.user.email"]

    code1 = generate_serializer_code("Lease", paths1)
    code2 = generate_serializer_code("Lease", paths2)

    # Should be identical
    assert code1 == code2


def test_code_uniqueness():
    """Test that different paths produce different code."""
    paths1 = ["property.name"]
    paths2 = ["property.address"]

    code1 = generate_serializer_code("Lease", paths1)
    code2 = generate_serializer_code("Lease", paths2)

    # Should be different (different hash in function name)
    assert code1 != code2


def test_method_call_with_subtree_generates_iteration():
    """When .all() has nested children (e.g., tags.all.name), codegen should iterate."""
    code = generate_serializer_code("Post", ["tags.all.name", "tags.all.url"])
    assert "for" in code
    assert ".all()" in code

    class Tag:
        def __init__(self, n, u):
            self.name = n
            self.url = u

    class TagManager:
        def __init__(self, tags):
            self._tags = tags

        def all(self):
            return self._tags

    class Post:
        def __init__(self):
            self.tags = TagManager([Tag("py", "/py"), Tag("dj", "/dj")])

    func_name = code.split("def ")[1].split("(")[0]
    func = compile_serializer(code, func_name)
    result = func(Post())
    assert len(result["tags"]["all"]) == 2
    assert result["tags"]["all"][0]["name"] == "py"


def test_method_call_leaf_still_calls_method():
    """When .all() is a leaf (no subtree), it should call the method directly."""
    code = generate_serializer_code("Post", ["tags.all"])
    assert ".all()" in code
    func_name = code.split("def ")[1].split("(")[0]

    class TagManager:
        def all(self):
            return ["a", "b"]

    class Post:
        def __init__(self):
            self.tags = TagManager()

    func = compile_serializer(code, func_name)
    result = func(Post())
    assert result["tags"]["all"] == ["a", "b"]


def test_compile_syntax_error():
    """Test handling of syntax errors in compilation."""
    # Create intentionally broken code
    bad_code = "def broken_func(:\n    pass"

    with pytest.raises(SyntaxError):
        compile_serializer(bad_code, "broken_func")
