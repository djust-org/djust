"""
Demonstration of type stub benefits for the djust._rust module.

This file shows how type stubs enable:
1. IDE autocomplete for Rust-exported functions
2. Type checking to catch errors before runtime
3. Better documentation and discoverability

Run with mypy to see type checking in action:
    mypy examples/stub_demo.py
"""

from djust._rust import (
    render_template,
    render_template_with_dirs,
    diff_html,
    extract_template_variables,
    RustButton,
    create_session_actor,
)


def demo_render_template():
    """Demo: render_template with type checking."""
    # ✅ Correct usage - type checker knows the signature
    html = render_template("<h1>{{ title }}</h1>", {"title": "Hello"})
    print(f"Rendered: {html}")

    # ❌ This would be caught by type checker:
    # html = render_template(123, {})  # Error: int is not str
    # html = render_template("<h1>Test</h1>")  # Error: missing required argument

    return html


def demo_render_with_dirs():
    """Demo: render_template_with_dirs with type checking."""
    # ✅ Correct usage with all parameters (simple template without includes)
    html = render_template_with_dirs(
        template_source="<h1>{{ greeting }}, {{ user }}!</h1>",
        context={"user": "John", "greeting": "Hello"},
        template_dirs=["/tmp"],  # Not used if no includes
        safe_keys=None,
    )
    print(f"Rendered with dirs: {html}")

    # ❌ These would be caught by type checker:
    # html = render_template_with_dirs("<h1>Test</h1>", {}, 123)  # Error: int is not List[str]
    # html = render_template_with_dirs()  # Error: missing required arguments

    return html


def demo_diff_html():
    """Demo: diff_html with type checking."""
    # ✅ Correct usage
    old = "<div>Old content</div>"
    new = "<div>New content</div>"
    patches = diff_html(old, new)
    print(f"Patches: {patches}")

    # ❌ This would be caught by type checker:
    # patches = diff_html(old)  # Error: missing required argument
    # patches = diff_html(123, 456)  # Error: int is not str

    return patches


def demo_extract_template_variables():
    """Demo: extract_template_variables with type checking."""
    # ✅ Correct usage - returns Dict[str, List[str]]
    template = "{{ user.name }} - {{ user.email }}"
    vars_dict = extract_template_variables(template)

    # Type checker knows this is Dict[str, List[str]]
    for var_name, paths in vars_dict.items():
        print(f"{var_name}: {paths}")

    # ❌ This would be caught by type checker:
    # vars_dict = extract_template_variables(123)  # Error: int is not str
    # vars_dict = extract_template_variables()  # Error: missing required argument

    return vars_dict


def demo_rust_button():
    """Demo: RustButton with type checking."""
    # ✅ Correct usage with type-checked parameters
    button = RustButton("submit-btn", "Submit Form")

    html = button.render()
    print(f"Button rendered successfully")

    # ❌ These would be caught by type checker:
    # button = RustButton()  # Error: missing required arguments
    # button = RustButton("id", 123)  # Error: int is not str
    # button.nonexistent_method()  # Error: method doesn't exist

    return button


async def demo_session_actor():
    """Demo: create_session_actor with async/await."""
    # ✅ Correct usage - type checker knows this returns Awaitable
    handle = await create_session_actor("session-123")

    # Type checker knows handle is SessionActorHandle
    # and has these async methods:
    view_id, html = await handle.mount(
        view_path="app.views.Counter",
        params={},
        request_meta={},
    )

    # ❌ These would be caught by type checker:
    # handle = create_session_actor()  # Error: missing required argument
    # handle = create_session_actor(123)  # Error: int is not str
    # await handle.nonexistent_method()  # Error: method doesn't exist

    return handle


def demo_wrong_method_name():
    """Demo: Type stubs catch typos in method names."""
    # ❌ Without type stubs, this typo would only be caught at runtime:
    # html = render_tempalte("<h1>Test</h1>", {})  # NameError at runtime

    # ✅ With type stubs, mypy/pyright catches this at lint time:
    # html = render_tempalte("<h1>Test</h1>", {})
    # Error: Cannot find reference 'render_tempalte' in '__init__.pyi'

    # ✅ Correct spelling works:
    html = render_template("<h1>Test</h1>", {})
    return html


if __name__ == "__main__":
    print("=== Type Stub Demo ===")
    print("\nRun 'mypy examples/stub_demo.py' to see type checking in action!\n")

    # Run demos
    demo_render_template()
    demo_render_with_dirs()
    demo_diff_html()
    demo_extract_template_variables()
    demo_rust_button()

    print("\n✅ All demos completed successfully!")
    print("\nBenefits of type stubs:")
    print("1. IDE autocomplete for all Rust functions")
    print("2. Type checking catches errors before runtime")
    print("3. Better documentation and API discoverability")
    print("4. Catches typos like 'live_navigate' (nonexistent) at lint time")
