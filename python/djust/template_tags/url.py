"""
Django URL template tag handler for djust.

This module provides the {% url %} template tag handler that integrates
Django's URL resolution with djust's Rust template engine.

Usage in templates:
    {% url 'view_name' %}
    {% url 'view_name' arg1 arg2 %}
    {% url 'view_name' kwarg1=value1 %}
    {% url 'view_name' post.slug %}
    {% url 'view_name' as the_url %}

Failure semantics are Django's (#2563): a reverse that fails RAISES
``NoReverseMatch``; only the ``as var`` form is the escape hatch, and it
stores ``''`` in the variable. Until #2563 this handler swallowed the
exception and rendered ``''`` — a blank ``href`` in production is exactly
the fail-soft that hides a broken link, and Django's own suite asserts
the raise (``template_tests/syntax_tests/test_url.py``).
"""

from typing import Any, Dict, List, Tuple

from . import AsVarName, TagHandler, register


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
    - Assignment: {% url 'view_name' as the_url %}

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

    {# A reverse that may fail: `as var` stores '' instead of raising #}
    {% url 'optional_feature' as feature_url %}
    {% if feature_url %}<a href="{{ feature_url }}">Feature</a>{% endif %}
    ```

    All rendering entry points resolve URLs through the Rust ``CustomTag``
    channel at the node's position. Its emission sink applies the active
    autoescape setting, and ``as var`` binds only subsequent sibling nodes.
    """

    #: ``render`` returns ``(output, bindings)`` (#2547) so the ``as var``
    #: form can bind the name for the siblings that follow. The 2-tuple is
    #: what this handler needs from #2547; the exception passthrough it also
    #: brought is what carries the ``NoReverseMatch`` out, and that half has
    #: been whole since #2547 itself.
    RETURNS_BINDINGS = True

    #: The engine hands the trailing ``as <name>`` over as two literal
    #: TOKENS instead of resolving them as variables (#2563), exactly
    #: Django's ``bits[-2] == "as"`` rule in ``defaulttags.url`` — and it
    #: applies that rule to the RAW tokens, marking the NAME as
    #: :class:`~djust.template_tags.AsVarName`. This handler reads that
    #: marker; it must never re-run the ``"as"`` test on its own resolved
    #: arguments (see ``render``).
    ACCEPTS_AS_VAR = True

    def render(self, args: List[str], context: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        """
        Render the URL by calling Django's reverse().

        Parameters
        ----------
        args : list
            First arg is the URL name (quoted string).
            Subsequent args are positional or keyword arguments.
            Note: Rust has already resolved context variables to their values,
            except for a trailing ``as <name>`` pair, which arrives verbatim
            with the NAME wearing
            :class:`~djust.template_tags.AsVarName`.

        context : dict
            Template context (for additional variable resolution if needed).

        Returns
        -------
        tuple
            ``(url, {})``, or ``("", {name: url})`` for the ``as var`` form —
            with ``url == ""`` when the reverse failed, as on Django.

        Raises
        ------
        django.template.TemplateSyntaxError
            No URL name was given.
        django.urls.NoReverseMatch
            The reverse failed and no ``as var`` was given — Django's
            exception, unwrapped, with Django's message.
        """
        from django.template import TemplateSyntaxError
        from django.urls import NoReverseMatch, reverse

        # Django's `bits[-2] == "as"` rule was already applied — ONCE, to the
        # RAW tokens, in `renderer.rs::resolve_custom_tag_args` — and the NAME
        # arrived wearing `AsVarName`. Reading the marker instead of re-testing
        # `args[-2] == "as"` is what keeps the two sides from disagreeing:
        # every other position here is RESOLVED, so `{% url named 'as' v %}`
        # and `{% url named sep v %}` with `sep = "as"` both manufacture the
        # literal the old test matched, and both silently became `as var` forms
        # that swallowed the `NoReverseMatch` (#2563 review, #1646).
        as_variable = None
        if len(args) >= 2 and isinstance(args[-1], AsVarName):
            as_variable = str(args[-1])
            args = args[:-2]

        if not args:
            raise TemplateSyntaxError("'url' takes at least one argument, a URL pattern name.")

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

        # Call Django's reverse() (Django is untyped under the lenient global
        # config, so reverse() is seen as ``Any``; coerce to ``str`` at the
        # boundary — reverse always returns a real ``str`` at runtime).
        #
        # Django's `URLNode.render` shape: `url = ""`, reverse, and on
        # `NoReverseMatch` re-raise unless `asvar` — in which case the empty
        # string is what gets stored. No catch-all arm: any other failure
        # is the project's own bug and crosses whole.
        url = ""
        try:
            if url_kwargs:
                url = str(reverse(url_name, kwargs=url_kwargs))
            elif url_args:
                url = str(reverse(url_name, args=url_args))
            else:
                url = str(reverse(url_name))
        except NoReverseMatch:
            if as_variable is None:
                raise

        if as_variable is not None:
            return "", {as_variable: url}
        return url, {}
