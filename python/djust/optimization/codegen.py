"""
Serializer Code Generation

Generates optimized Python serializer functions for specific variable access patterns.
"""

import hashlib
import inspect
from typing import List, Dict, Callable


def generate_serializer_code(
    model_name: str, variable_paths: List[str], func_name: str = None
) -> str:
    """
    Generate Python code for a custom serializer function.

    Args:
        model_name: Name of the model (e.g., "Lease")
        variable_paths: List of paths to serialize (e.g., ["property.name", "tenant.user.email"])
        func_name: Optional function name to use (if None, generates one automatically)

    Returns:
        Python source code as string

    Example:
        >>> code = generate_serializer_code("Lease", ["property.name", "tenant.user.email"])
        >>> print(code)
        def serialize_lease_a4f8b2(obj):
            result = {}
            # property.name
            if hasattr(obj, 'property') and obj.property is not None:
                if 'property' not in result:
                    result['property'] = {}
                result['property']['name'] = obj.property.name
            ...
            return result
    """
    # Generate unique function name based on paths if not provided
    if func_name is None:
        func_hash = hashlib.sha256("".join(sorted(variable_paths)).encode()).hexdigest()[:6]
        func_name = f"serialize_{model_name.lower()}_{func_hash}"

    lines = [
        f"def {func_name}(obj):",
        "    '''Auto-generated serializer'''",
        "    result = {}",
        "",
    ]

    # Build path tree to avoid redundant checks
    path_tree = _build_path_tree(variable_paths)

    # Generate code for each path
    for root_attr, nested_paths in path_tree.items():
        _generate_nested_access(lines, [], nested_paths, "obj", "result", root_attr)

    lines.append("    return result")

    return "\n".join(lines)


def _build_path_tree(paths: List[str]) -> Dict:
    """
    Build tree structure from flat paths for efficient code generation.

    Args:
        paths: ["property.name", "property.address", "tenant.user.email", "leases.0.property.name"]

    Returns:
        {
            "property": {
                "name": {},
                "address": {}
            },
            "tenant": {
                "user": {
                    "email": {}
                }
            },
            "leases": {
                "__list_item__": {
                    "property": {
                        "name": {}
                    }
                }
            }
        }

    Note: Numeric indices (e.g., "leases.0.property") are converted to "__list_item__"
    to indicate the serializer should iterate over the list.
    """
    tree = {}

    for path in paths:
        parts = path.split(".")
        current = tree

        i = 0
        while i < len(parts):
            part = parts[i]

            # Check if next part is a numeric index
            if i + 1 < len(parts) and parts[i + 1].isdigit():
                # This is a list/queryset attribute (e.g., "leases" in "leases.0.property")
                # Create entry for this attribute with __list_item__ for nested access
                if part not in current:
                    current[part] = {}

                if "__list_item__" not in current[part]:
                    current[part]["__list_item__"] = {}

                # Skip the numeric index and continue with remaining parts
                current = current[part]["__list_item__"]
                i += 2  # Skip both attribute name and numeric index
                continue

            # Regular attribute access
            if part not in current:
                current[part] = {}
            current = current[part]
            i += 1

    return tree


def _generate_nested_access(
    lines: List[str],
    current_path: List[str],
    tree: Dict,
    obj_var: str,
    result_var: str,
    root_attr: str = None,
    indent: int = 1,
):
    """
    Recursively generate safe nested attribute access code.

    Args:
        lines: List of code lines to append to
        current_path: Current attribute path so far (e.g., ["property"])
        tree: Tree structure to process (e.g., {"name": {}, "address": {}})
        obj_var: Python variable name for object (e.g., "obj", "obj.property")
        result_var: Python variable name for result dict
        root_attr: Root attribute name (used on first call only)
        indent: Current indentation level
    """
    ind = "    " * indent

    # Handle root attribute on first call
    if root_attr is not None:
        current_path = [root_attr]
        obj_access = f"{obj_var}.{root_attr}"

        # Generate safety check for root
        lines.append(f"{ind}if hasattr({obj_var}, '{root_attr}') and {obj_access} is not None:")

        if tree:
            # Has nested attributes - create dict and recurse
            lines.append(f"{ind}    {result_var}['{root_attr}'] = {{}}")
            _generate_nested_access(
                lines,
                current_path,
                tree,
                obj_access,
                result_var,
                None,
                indent + 1,
            )
        else:
            # Leaf node - direct assignment
            if root_attr.startswith("get_"):
                # Method call
                lines.append(f"{ind}    try:")
                lines.append(f"{ind}        {result_var}['{root_attr}'] = {obj_access}()")
                lines.append(f"{ind}    except Exception:")
                lines.append(f"{ind}        pass  # Method call failed")
            else:
                # Direct attribute
                lines.append(f"{ind}    {result_var}['{root_attr}'] = {obj_access}")
        return

    # Process nested tree
    if not tree:
        # Should not happen in normal flow
        return

    for attr_name, subtree in tree.items():
        # Special handling for list iteration marker
        if attr_name == "__list_item__":
            # This is a list - iterate and extract nested fields from each item
            # The subtree contains the fields to extract from each list item
            obj_access = obj_var  # We're already at the list level

            # Generate list iteration code
            list_var = f"item_{indent}"
            dict_path = _build_dict_path(result_var, current_path)
            lines.append(f"{ind}try:")
            lines.append(f"{ind}    {dict_path} = []")
            lines.append(f"{ind}    for {list_var} in {obj_var}:")
            lines.append(f"{ind}        item_result = {{}}")

            # Generate code to extract fields from each list item
            for nested_attr, nested_subtree in subtree.items():
                _generate_nested_access(
                    lines,
                    [],  # Reset path for list item
                    {nested_attr: nested_subtree},
                    list_var,
                    "item_result",
                    None,
                    indent + 2,
                )

            lines.append(f"{ind}        {dict_path}.append(item_result)")
            lines.append(f"{ind}except (TypeError, AttributeError):")
            lines.append(f"{ind}    pass  # Not iterable or access failed")
            continue

        new_path = current_path + [attr_name]
        obj_access = f"{obj_var}.{attr_name}"

        # Generate safety check
        lines.append(f"{ind}if hasattr({obj_var}, '{attr_name}') and {obj_access} is not None:")

        if subtree:
            # Check if subtree contains list iteration marker
            if "__list_item__" in subtree:
                # This is a list/queryset attribute - handle specially
                dict_path = _build_dict_path(result_var, current_path)
                lines.append(f"{ind}    # List iteration for {attr_name}")
                _generate_nested_access(
                    lines,
                    new_path,
                    subtree,
                    obj_access,
                    result_var,
                    None,
                    indent + 1,
                )
            elif attr_name == "all":
                # .all() returns an iterable — iterate results
                # e.g. tags.all has subtree {name: {}, url: {}} meaning
                # template does {% for tag in post.tags.all %} {{ tag.name }}
                dict_path = _build_dict_path(result_var, current_path)
                list_var = f"item_{indent}"
                item_result_var = f"_item_result_{indent}"
                lines.append(f"{ind}    try:")
                lines.append(f"{ind}        {dict_path}['{attr_name}'] = []")
                lines.append(f"{ind}        for {list_var} in {obj_access}():")
                lines.append(f"{ind}            {item_result_var} = {{}}")

                for nested_attr, nested_subtree in subtree.items():
                    _generate_nested_access(
                        lines,
                        [],
                        {nested_attr: nested_subtree},
                        list_var,
                        item_result_var,
                        None,
                        indent + 3,
                    )

                lines.append(
                    f"{ind}            {dict_path}['{attr_name}'].append({item_result_var})"
                )
                lines.append(f"{ind}    except (TypeError, AttributeError):")
                lines.append(f"{ind}        pass  # Method call or iteration failed")
            else:
                # Has nested attributes - create nested dict and recurse
                dict_path = _build_dict_path(result_var, current_path)
                lines.append(f"{ind}    {dict_path}['{attr_name}'] = {{}}")

                _generate_nested_access(
                    lines,
                    new_path,
                    subtree,
                    obj_access,
                    result_var,
                    None,
                    indent + 1,
                )
        else:
            # Leaf node - final assignment
            dict_path_full = _build_dict_path(result_var, new_path[:-1])

            if attr_name.startswith("get_") or attr_name in ("all", "count", "exists"):
                # Method call (includes Django manager/queryset methods)
                lines.append(f"{ind}    try:")
                lines.append(f"{ind}        {dict_path_full}['{attr_name}'] = {obj_access}()")
                lines.append(f"{ind}    except Exception:")
                lines.append(f"{ind}        pass  # Method call failed")
            else:
                # Direct attribute
                lines.append(f"{ind}    {dict_path_full}['{attr_name}'] = {obj_access}")


def _build_dict_path(result_var: str, path: List[str]) -> str:
    """
    Build dictionary access path string.

    Args:
        result_var: Base result variable name (e.g., "result")
        path: Attribute path (e.g., ["property", "owner"])

    Returns:
        "result['property']['owner']"
    """
    if not path:
        return result_var

    return result_var + "".join([f"['{p}']" for p in path])


def compile_serializer(code: str, func_name: str) -> Callable:
    """
    Compile serializer code to bytecode and return the function.

    Args:
        code: Python source code
        func_name: Name of the serializer function

    Returns:
        Compiled function object

    Example:
        >>> code = generate_serializer_code("Lease", ["property.name"])
        >>> func = compile_serializer(code, "serialize_lease_a4f8b2")
        >>> lease = Lease.objects.first()
        >>> serialized = func(lease)
        >>> print(serialized)
        {"property": {"name": "123 Main St"}}
    """
    namespace = {}

    try:
        # Compile to bytecode
        code_obj = compile(code, f"<generated:{func_name}>", "exec")

        # Execute to define function in namespace
        exec(code_obj, namespace)

        # Return the function
        return namespace[func_name]

    except SyntaxError as e:
        # Include generated code in error for debugging
        lines = code.split("\n")
        error_context = "\n".join([f"{i + 1:3}: {line}" for i, line in enumerate(lines)])
        raise SyntaxError(f"Failed to compile generated serializer:\n{error_context}") from e


def get_serializer_source(serializer_func: Callable) -> str:
    """
    Get source code of a compiled serializer function.

    Useful for debugging.

    Args:
        serializer_func: Compiled serializer function

    Returns:
        Source code as string
    """
    try:
        return inspect.getsource(serializer_func)
    except OSError:
        # Function was compiled from string, not a file
        # Return the docstring which contains info
        return f"# Auto-generated serializer\n# {serializer_func.__name__}\n"
