"""generate_serializer_code accepts only identifier path segments.

Path segments, the model name and the function name are pasted into the
generated source that compile_serializer executes, so anything that is not a
plain Python identifier (or a digit list index) is rejected before any code is
built. The JIT callers catch the ValueError and fall back to regular
serialization.
"""

from __future__ import annotations

import pytest

from djust.optimization.codegen import compile_serializer, generate_serializer_code


@pytest.mark.parametrize(
    "paths",
    [
        ["property.name"],
        ["tenant.user.email", "property.address"],
        ["leases.0.property.name"],
        ["ünïcode.field"],
    ],
)
def test_identifier_paths_generate_and_compile(paths):
    code = generate_serializer_code("Lease", paths)
    fn = compile_serializer(code, code.split("(")[0].removeprefix("def ").strip())
    assert callable(fn)


@pytest.mark.parametrize(
    "bad_path",
    [
        "a']);__import__('os').system('id');#",
        "obj.x'y",
        "obj.a-b",
        "obj.a b",
        "obj.x\ny",
        "obj.",
        ".obj",
        "obj.class",  # keyword
        "obj.None",  # keyword
    ],
)
def test_non_identifier_segment_is_rejected(bad_path):
    with pytest.raises(ValueError, match="unsupported serializer path segment"):
        generate_serializer_code("Lease", ["property.name", bad_path])


@pytest.mark.parametrize("model_name", ["Lease'", "Lease()", "", "class"])
def test_non_identifier_model_name_is_rejected(model_name):
    with pytest.raises(ValueError, match="unsupported model name"):
        generate_serializer_code(model_name, ["property.name"])


def test_non_identifier_func_name_is_rejected():
    with pytest.raises(ValueError, match="unsupported serializer function name"):
        generate_serializer_code("Lease", ["property.name"], func_name="f(); import os")


def test_serializer_cache_is_gone():
    import djust.optimization as optimization

    assert not hasattr(optimization, "SerializerCache")
    with pytest.raises(ModuleNotFoundError):
        __import__("djust.optimization.cache")
