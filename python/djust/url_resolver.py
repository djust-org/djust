"""
URL tag resolution utility for djust templates.

This module provides URL resolution for {% url %} tags that can be used
by both the HTTP rendering path (template_backend.py) and the WebSocket
rendering path (live_view.py).

The Rust template engine doesn't have access to Django's URL resolver,
so we pre-process templates to replace {% url %} tags with resolved URLs.
"""

import logging
import re
from typing import Any, Dict

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
            # Leave the original tag in place - it references variables
            # that don't exist in the context yet (e.g., loop variables)
            # The Rust engine will treat this as an unknown tag (empty output)
            logger.debug(
                "URL tag with unresolved variables (likely loop variable): %s",
                match.group(0),
            )
            return match.group(0)

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
