"""
URL tag resolution utility for djust templates.

This module provides URL resolution for {% url %} tags that can be used
by both the HTTP rendering path (template_backend.py) and the WebSocket
rendering path (live_view.py).

The Rust template engine doesn't have access to Django's URL resolver,
so we pre-process templates to replace {% url %} tags with resolved URLs.

For loop variables (e.g., {% url 'post' post.slug %} inside {% for post in posts %}),
we use a two-pass approach:
1. Pre-process: Convert to markers with Rust variable syntax
2. Post-process: After Rust renders, resolve the markers to actual URLs
"""

import logging
import re
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# Regex pattern for {% url %} tag
# Matches: {% url 'name' %}, {% url 'name' arg1 %}, {% url 'name' key=val %},
#          {% url 'name' as var %}, etc.
# The negative lookahead (?!as\s) prevents 'as' from being captured as an argument
URL_TAG_RE = re.compile(
    r"{%\s*url\s+"
    r"['\"]([^'\"]+)['\"]"  # URL name (required, in quotes)
    r"((?:\s+(?!as\s)(?:[a-zA-Z_][a-zA-Z0-9_.]*(?:=[^\s%}]+)?|['\"][^'\"]*['\"]|\d+))*)"  # args/kwargs (excluding 'as')
    r"(?:\s+as\s+([a-zA-Z_][a-zA-Z0-9_]*))?"  # optional 'as variable'
    r"\s*%}",
    re.DOTALL,
)

# Marker format for deferred URL resolution (used for loop variables)
# Format: <!--__DJUST_URL__:url_name:arg1:arg2:kwarg1=val1:__END__-->
# Or for URLs with no args: <!--__DJUST_URL__:url_name:__END__-->
URL_MARKER_START = "<!--__DJUST_URL__:"
URL_MARKER_END = ":__END__-->"
URL_MARKER_RE = re.compile(
    r"<!--__DJUST_URL__:([^:]+):(.*?):__END__-->",
    re.DOTALL,
)


def resolve_url_tags(template_source: str, context_dict: Dict[str, Any]) -> str:
    """
    Resolve {% url %} tags by replacing them with actual URLs.

    This preprocessing step allows the Rust rendering engine to work with
    resolved URLs since it doesn't have access to Django's URL resolver.

    Supports:
    - Basic: {% url 'name' %}
    - With args: {% url 'name' arg1 arg2 %}
    - With kwargs: {% url 'name' key=value %}
    - With context variables: {% url 'name' post.slug %}
    - As variable: {% url 'name' as var_name %}

    Args:
        template_source: Template string containing {% url %} tags
        context_dict: Context dictionary for resolving variable arguments

    Returns:
        Template string with {% url %} tags replaced by resolved URLs
    """
    from django.urls import NoReverseMatch, reverse

    def resolve_value(value_str: str, context: Dict[str, Any]) -> Any:
        """Resolve a value from the context or return the literal value."""
        value_str = value_str.strip()

        # String literal (single or double quotes)
        if (value_str.startswith("'") and value_str.endswith("'")) or (
            value_str.startswith('"') and value_str.endswith('"')
        ):
            return value_str[1:-1]

        # Integer literal
        if value_str.isdigit():
            return int(value_str)

        # Context variable (possibly with dot notation)
        if "." in value_str:
            parts = value_str.split(".")
            value = context.get(parts[0])
            for part in parts[1:]:
                if value is None:
                    return None
                if isinstance(value, dict):
                    value = value.get(part)
                else:
                    value = getattr(value, part, None)
            return value
        else:
            return context.get(value_str)

    def replace_url_tag(match: re.Match) -> str:
        """Replace a single {% url %} tag with its resolved URL."""
        url_name = match.group(1)
        args_string = match.group(2) or ""
        as_variable = match.group(3)

        # Parse arguments and keyword arguments
        args = []
        kwargs = {}
        tokens = []

        # Tokenize the arguments string
        if args_string.strip():
            # Simple tokenization - handle quoted strings and key=value pairs
            current_token = ""
            in_quotes = False
            quote_char = None

            for char in args_string:
                if char in "\"'" and not in_quotes:
                    in_quotes = True
                    quote_char = char
                    current_token += char
                elif char == quote_char and in_quotes:
                    in_quotes = False
                    quote_char = None
                    current_token += char
                elif char.isspace() and not in_quotes:
                    if current_token:
                        tokens.append(current_token)
                        current_token = ""
                else:
                    current_token += char

            if current_token:
                tokens.append(current_token)

            # Process tokens into args and kwargs
            for token in tokens:
                if "=" in token and not token.startswith("'") and not token.startswith('"'):
                    # Keyword argument
                    key, value = token.split("=", 1)
                    resolved_value = resolve_value(value, context_dict)
                    if resolved_value is not None:
                        kwargs[key] = resolved_value
                else:
                    # Positional argument
                    resolved_value = resolve_value(token, context_dict)
                    if resolved_value is not None:
                        args.append(resolved_value)

        # Check if any args/kwargs couldn't be resolved (value is None)
        # This happens when the URL references loop variables like post.slug
        # In this case, leave the original tag - it can't be resolved yet
        has_unresolved = False
        if args_string.strip():
            for token in tokens:
                if "=" in token and not token.startswith("'") and not token.startswith('"'):
                    # Keyword argument
                    _, value = token.split("=", 1)
                    if resolve_value(value, context_dict) is None and not (
                        (value.startswith("'") and value.endswith("'"))
                        or (value.startswith('"') and value.endswith('"'))
                        or value.isdigit()
                    ):
                        has_unresolved = True
                        break
                else:
                    # Positional argument
                    if resolve_value(token, context_dict) is None and not (
                        (token.startswith("'") and token.endswith("'"))
                        or (token.startswith('"') and token.endswith('"'))
                        or token.isdigit()
                    ):
                        has_unresolved = True
                        break

        if has_unresolved:
            # Convert to a deferred URL marker that Rust will render with variable values.
            # This handles loop variables like {% url 'post' post.slug %} inside {% for %}.
            #
            # The marker format uses HTML comments (which Rust passes through) with
            # Rust variable syntax for the unresolved parts:
            #   {% url 'post_detail' post.slug %}
            # becomes:
            #   <!--__DJUST_URL__:post_detail:{{ post.slug }}:__END__-->
            #
            # After Rust renders the loop, post.slug is substituted:
            #   <!--__DJUST_URL__:post_detail:my-actual-slug:__END__-->
            #
            # Then post_process_url_markers() resolves to the actual URL.
            marker_parts = [url_name]
            for token in tokens:
                if "=" in token and not token.startswith("'") and not token.startswith('"'):
                    # Keyword argument: key=value or key={{ var }}
                    key, value = token.split("=", 1)
                    if (value.startswith("'") and value.endswith("'")) or (
                        value.startswith('"') and value.endswith('"')
                    ):
                        # Literal value
                        marker_parts.append(f"{key}={value[1:-1]}")
                    elif value.isdigit():
                        marker_parts.append(f"{key}={value}")
                    else:
                        # Variable - use Rust syntax
                        marker_parts.append(f"{key}={{{{ {value} }}}}")
                else:
                    # Positional argument
                    if (token.startswith("'") and token.endswith("'")) or (
                        token.startswith('"') and token.endswith('"')
                    ):
                        marker_parts.append(token[1:-1])
                    elif token.isdigit():
                        marker_parts.append(token)
                    else:
                        # Variable - use Rust syntax
                        marker_parts.append(f"{{{{ {token} }}}}")

            # Join parts with colons. Add empty string if no args to maintain format.
            args_str = ":".join(marker_parts[1:]) if len(marker_parts) > 1 else ""
            marker = f"{URL_MARKER_START}{marker_parts[0]}:{args_str}{URL_MARKER_END}"
            logger.debug(
                "URL tag with loop variables converted to marker: %s -> %s",
                match.group(0),
                marker,
            )
            return marker

        # Resolve the URL
        try:
            url = reverse(url_name, args=args if args else None, kwargs=kwargs if kwargs else None)

            if as_variable:
                # Store in context and return empty string
                # We'll handle this by adding to context_dict
                context_dict[as_variable] = url
                return ""
            else:
                return url
        except NoReverseMatch as e:
            # Re-raise to match Django's behavior
            raise NoReverseMatch(
                f"Reverse for '{url_name}' not found. "
                f"'{url_name}' is not a valid view function or pattern name."
            ) from e

    # Replace all {% url %} tags
    return URL_TAG_RE.sub(replace_url_tag, template_source)


def post_process_url_markers(rendered_html: str) -> str:
    """
    Resolve URL markers in rendered HTML to actual URLs.

    This is the second pass of URL resolution, called after Rust renders
    the template. It handles markers created for loop variables where the
    variable values weren't available during pre-processing.

    Marker format: <!--__DJUST_URL__:url_name:arg1:arg2:kwarg=val:__END__-->

    Args:
        rendered_html: HTML rendered by Rust, potentially containing URL markers

    Returns:
        HTML with URL markers replaced by resolved URLs
    """
    from django.urls import NoReverseMatch, reverse

    # Quick check: skip if no markers
    if URL_MARKER_START not in rendered_html:
        return rendered_html

    def resolve_marker(match: re.Match) -> str:
        """Resolve a single URL marker to its URL."""
        url_name = match.group(1)
        args_string = match.group(2)

        # Parse the colon-separated arguments
        args: List[Any] = []
        kwargs: Dict[str, Any] = {}

        # Split by colon, but be careful with kwargs that contain colons
        parts = args_string.split(":")

        for part in parts:
            part = part.strip()
            if not part:
                continue

            if "=" in part:
                # Keyword argument
                key, value = part.split("=", 1)
                # Try to convert to int if it looks like a number
                if value.isdigit():
                    kwargs[key] = int(value)
                else:
                    kwargs[key] = value
            else:
                # Positional argument
                if part.isdigit():
                    args.append(int(part))
                else:
                    args.append(part)

        try:
            # Django doesn't allow mixing args and kwargs in reverse()
            if kwargs:
                url = reverse(url_name, kwargs=kwargs)
            elif args:
                url = reverse(url_name, args=args)
            else:
                url = reverse(url_name)
            return url
        except NoReverseMatch as e:
            # Log warning but return empty string to avoid breaking the page
            logger.warning(
                "Failed to resolve URL marker: url_name=%s, args=%s, kwargs=%s, error=%s",
                url_name,
                args,
                kwargs,
                e,
            )
            return ""
        except Exception as e:
            # Catch any other errors (e.g., invalid URL name)
            logger.warning("URL marker resolution failed: %s", e)
            return ""

    return URL_MARKER_RE.sub(resolve_marker, rendered_html)
