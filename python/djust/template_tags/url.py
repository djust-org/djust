"""
Django URL template tag handler for djust.

This module provides the {% url %} template tag handler that integrates
Django's URL resolution with djust's Rust template engine.

Usage in templates:
    {% url 'view_name' %}
    {% url 'view_name' arg1 arg2 %}
    {% url 'view_name' kwarg1=value1 %}
    {% url 'view_name' post.slug %}
"""

import logging
from typing import List, Dict, Any

from . import TagHandler, register

logger = logging.getLogger(__name__)


@register("url")
class UrlTagHandler(TagHandler):
    """
    Handler for the {% url %} template tag.

    Resolves Django URL patterns using django.urls.reverse().

    Supports:
    - Named URL patterns: {% url 'view_name' %}
    - Positional args: {% url 'view_name' arg1 arg2 %}
    - Keyword args: {% url 'view_name' pk=1 %}
    - Context variables: {% url 'view_name' post.slug %}
    - Mixed args: {% url 'view_name' 'static' post.id %}

    Examples
    --------
    ```django
    {# Simple URL #}
    <a href="{% url 'home' %}">Home</a>

    {# With positional arg #}
    <a href="{% url 'post_detail' post.id %}">View Post</a>

    {# With keyword arg #}
    <a href="{% url 'user_profile' username=user.username %}">Profile</a>

    {# Inside a loop #}
    {% for post in posts %}
        <a href="{% url 'post_detail' post.slug %}">{{ post.title }}</a>
    {% endfor %}
    ```
    """

    def render(self, args: List[str], context: Dict[str, Any]) -> str:
        """
        Render the URL by calling Django's reverse().

        Parameters
        ----------
        args : list
            First arg is the URL name (quoted string).
            Subsequent args are positional or keyword arguments.
            Note: Rust has already resolved context variables to their values.

        context : dict
            Template context (for additional variable resolution if needed).

        Returns
        -------
        str
            The resolved URL path, or empty string if resolution fails.
        """
        if not args:
            logger.warning("{% url %} tag requires at least a URL name")
            return ""

        try:
            from django.urls import reverse, NoReverseMatch
        except ImportError:
            logger.error("Django not installed - cannot resolve URLs")
            return ""

        # First argument is the URL name (strip quotes)
        url_name = self._resolve_arg(args[0], context)
        if isinstance(url_name, str):
            url_name = url_name.strip("'\"")

        # Parse remaining args into positional and keyword arguments
        url_args = []
        url_kwargs = {}

        for arg in args[1:]:
            resolved = self._resolve_arg(arg, context)

            if isinstance(resolved, tuple):
                # Named parameter: (key, value)
                key, value = resolved
                url_kwargs[key] = value
            else:
                # Positional argument
                url_args.append(resolved)

        # Call Django's reverse()
        try:
            if url_kwargs:
                return reverse(url_name, kwargs=url_kwargs)
            elif url_args:
                return reverse(url_name, args=url_args)
            else:
                return reverse(url_name)
        except NoReverseMatch as e:
            logger.warning(
                f"NoReverseMatch for URL '{url_name}' with args={url_args}, kwargs={url_kwargs}: {e}"
            )
            return ""
        except Exception as e:
            logger.error(f"Error resolving URL '{url_name}': {e}")
            return ""
