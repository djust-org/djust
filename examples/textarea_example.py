"""
Example usage of TextArea component.

This demonstrates various configurations of the TextArea component.
"""

# Configure Django before importing djust
import django
from django.conf import settings

if not settings.configured:
    settings.configure(
        DEBUG=True,
        SECRET_KEY='example-secret-key',
    )
    django.setup()

from djust.components.ui import TextArea


def example_basic():
    """Basic textarea"""
    textarea = TextArea(
        name="description",
        label="Description",
        placeholder="Enter description...",
        rows=5
    )
    print("Basic TextArea:")
    print(textarea.render())
    print()


def example_with_validation():
    """TextArea with validation states"""
    # Invalid
    invalid = TextArea(
        name="comment",
        label="Comment",
        value="Too short",
        validation_state="invalid",
        validation_message="Comment must be at least 20 characters",
        rows=3
    )
    print("Invalid TextArea:")
    print(invalid.render())
    print()

    # Valid
    valid = TextArea(
        name="comment",
        label="Comment",
        value="This is a sufficiently long comment that meets our requirements.",
        validation_state="valid",
        validation_message="Looks good!",
        rows=3
    )
    print("Valid TextArea:")
    print(valid.render())
    print()


def example_with_help_text():
    """TextArea with help text"""
    textarea = TextArea(
        name="bio",
        label="Biography",
        placeholder="Tell us about yourself...",
        help_text="Maximum 500 characters. This will appear on your public profile.",
        rows=6,
        required=True
    )
    print("TextArea with help text and required:")
    print(textarea.render())
    print()


def example_readonly():
    """Readonly and disabled states"""
    readonly = TextArea(
        name="terms",
        label="Terms and Conditions",
        value="These are the terms and conditions that you must agree to...",
        readonly=True,
        rows=4
    )
    print("Readonly TextArea:")
    print(readonly.render())
    print()

    disabled = TextArea(
        name="archived",
        label="Archived Note",
        value="This note has been archived and cannot be edited.",
        disabled=True,
        rows=3
    )
    print("Disabled TextArea:")
    print(disabled.render())
    print()


def example_form_integration():
    """Example of TextArea in a form context"""
    print("Complete Form Example:")
    print("<form>")

    # Name field
    name = TextArea(
        name="full_name",
        label="Full Name",
        placeholder="Enter your full name",
        rows=1,
        required=True
    )
    print(name.render())

    # Address field
    address = TextArea(
        name="address",
        label="Address",
        placeholder="Enter your full address",
        help_text="Include street, city, state, and zip code",
        rows=4,
        required=True
    )
    print(address.render())

    # Comments field
    comments = TextArea(
        name="comments",
        label="Additional Comments",
        placeholder="Any additional information you'd like to share...",
        rows=5
    )
    print(comments.render())

    print('  <button type="submit" class="btn btn-primary">Submit</button>')
    print("</form>")
    print()


if __name__ == "__main__":
    print("=" * 70)
    print("TextArea Component Examples")
    print("=" * 70)
    print()

    example_basic()
    example_with_validation()
    example_with_help_text()
    example_readonly()
    example_form_integration()

    print("=" * 70)
    print("All examples rendered successfully!")
