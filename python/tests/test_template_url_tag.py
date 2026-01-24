"""
Tests for {% url %} tag support in djust templates.

The {% url %} tag should resolve Django URLs using the reverse() function.
This is implemented via Python-side preprocessing in the template backend
before passing the template to the Rust rendering engine.
"""

import pytest
from django.test import RequestFactory, override_settings
from django.urls import path


# Define test URL patterns
def home_view(request):
    return None


def blog_view(request):
    return None


def post_detail_view(request, slug):
    return None


def post_by_id_view(request, pk):
    return None


def user_profile_view(request, username):
    return None


def post_comment_view(request, post_id, comment_id):
    return None


def search_view(request, category, query):
    return None


# URL patterns for testing
urlpatterns = [
    path("", home_view, name="home"),
    path("blog/", blog_view, name="blog"),
    path("post/<slug:slug>/", post_detail_view, name="post_detail"),
    path("post/<int:pk>/", post_by_id_view, name="post_by_id"),
    path("user/<str:username>/", user_profile_view, name="user_profile"),
    path("post/<int:post_id>/comment/<int:comment_id>/", post_comment_view, name="post_comment"),
    path("search/<str:category>/<str:query>/", search_view, name="search"),
]

# Namespaced URL patterns
app_name = "marketing"
marketing_urlpatterns = [
    path("", home_view, name="home"),
    path("blog/", blog_view, name="blog"),
    path("about/", home_view, name="about"),
]


class TestUrlTagBasic:
    """Test basic {% url %} tag functionality."""

    @override_settings(ROOT_URLCONF=__name__)
    def test_simple_url_resolution(self):
        """Test basic URL resolution: {% url 'home' %}"""
        from djust.template_backend import DjustTemplateBackend

        backend = DjustTemplateBackend(
            {
                "NAME": "djust",
                "DIRS": [],
                "APP_DIRS": False,
                "OPTIONS": {},
            }
        )

        template = backend.from_string("<a href=\"{% url 'home' %}\">Home</a>")
        request = RequestFactory().get("/")
        result = template.render({}, request)

        assert 'href="/"' in result, f'Expected href="/" but got: {result}'

    @override_settings(ROOT_URLCONF=__name__)
    def test_url_with_name_only(self):
        """Test URL tag with just a name: {% url 'blog' %}"""
        from djust.template_backend import DjustTemplateBackend

        backend = DjustTemplateBackend(
            {
                "NAME": "djust",
                "DIRS": [],
                "APP_DIRS": False,
                "OPTIONS": {},
            }
        )

        template = backend.from_string('{% url "blog" %}')
        request = RequestFactory().get("/")
        result = template.render({}, request)

        assert result.strip() == "/blog/", f"Expected /blog/ but got: {result}"


class TestUrlTagWithArgs:
    """Test {% url %} tag with positional arguments."""

    @override_settings(ROOT_URLCONF=__name__)
    def test_url_with_string_literal_arg(self):
        """Test URL with string literal argument: {% url 'post_detail' 'my-post' %}"""
        from djust.template_backend import DjustTemplateBackend

        backend = DjustTemplateBackend(
            {
                "NAME": "djust",
                "DIRS": [],
                "APP_DIRS": False,
                "OPTIONS": {},
            }
        )

        template = backend.from_string("{% url 'post_detail' 'my-post' %}")
        request = RequestFactory().get("/")
        result = template.render({}, request)

        assert result.strip() == "/post/my-post/", f"Expected /post/my-post/ but got: {result}"

    @override_settings(ROOT_URLCONF=__name__)
    def test_url_with_context_variable_arg(self):
        """Test URL with context variable: {% url 'post_detail' post.slug %}"""
        from djust.template_backend import DjustTemplateBackend

        backend = DjustTemplateBackend(
            {
                "NAME": "djust",
                "DIRS": [],
                "APP_DIRS": False,
                "OPTIONS": {},
            }
        )

        template = backend.from_string("{% url 'post_detail' post_slug %}")
        request = RequestFactory().get("/")
        result = template.render({"post_slug": "hello-world"}, request)

        assert (
            result.strip() == "/post/hello-world/"
        ), f"Expected /post/hello-world/ but got: {result}"

    @override_settings(ROOT_URLCONF=__name__)
    def test_url_with_nested_context_variable(self):
        """Test URL with nested context variable: {% url 'post_detail' post.slug %}"""
        from djust.template_backend import DjustTemplateBackend

        backend = DjustTemplateBackend(
            {
                "NAME": "djust",
                "DIRS": [],
                "APP_DIRS": False,
                "OPTIONS": {},
            }
        )

        template = backend.from_string("{% url 'post_detail' post.slug %}")
        request = RequestFactory().get("/")
        result = template.render({"post": {"slug": "nested-post"}}, request)

        assert (
            result.strip() == "/post/nested-post/"
        ), f"Expected /post/nested-post/ but got: {result}"

    @override_settings(ROOT_URLCONF=__name__)
    def test_url_with_integer_arg(self):
        """Test URL with integer argument: {% url 'post_by_id' 42 %}"""
        from djust.template_backend import DjustTemplateBackend

        backend = DjustTemplateBackend(
            {
                "NAME": "djust",
                "DIRS": [],
                "APP_DIRS": False,
                "OPTIONS": {},
            }
        )

        template = backend.from_string("{% url 'post_by_id' 42 %}")
        request = RequestFactory().get("/")
        result = template.render({}, request)

        assert result.strip() == "/post/42/", f"Expected /post/42/ but got: {result}"


class TestUrlTagWithKwargs:
    """Test {% url %} tag with keyword arguments."""

    @override_settings(ROOT_URLCONF=__name__)
    def test_url_with_kwarg(self):
        """Test URL with keyword argument: {% url 'post_detail' slug='my-post' %}"""
        from djust.template_backend import DjustTemplateBackend

        backend = DjustTemplateBackend(
            {
                "NAME": "djust",
                "DIRS": [],
                "APP_DIRS": False,
                "OPTIONS": {},
            }
        )

        template = backend.from_string("{% url 'post_detail' slug='my-post' %}")
        request = RequestFactory().get("/")
        result = template.render({}, request)

        assert result.strip() == "/post/my-post/", f"Expected /post/my-post/ but got: {result}"

    @override_settings(ROOT_URLCONF=__name__)
    def test_url_with_context_kwarg(self):
        """Test URL with context variable as kwarg: {% url 'post_detail' slug=post.slug %}"""
        from djust.template_backend import DjustTemplateBackend

        backend = DjustTemplateBackend(
            {
                "NAME": "djust",
                "DIRS": [],
                "APP_DIRS": False,
                "OPTIONS": {},
            }
        )

        template = backend.from_string("{% url 'post_detail' slug=post_slug %}")
        request = RequestFactory().get("/")
        result = template.render({"post_slug": "kwarg-post"}, request)

        assert (
            result.strip() == "/post/kwarg-post/"
        ), f"Expected /post/kwarg-post/ but got: {result}"


class TestUrlTagAsVariable:
    """Test {% url %} tag with 'as' variable assignment."""

    @override_settings(ROOT_URLCONF=__name__)
    def test_url_as_variable(self):
        """Test URL assigned to variable: {% url 'home' as home_url %}{{ home_url }}"""
        from djust.template_backend import DjustTemplateBackend

        backend = DjustTemplateBackend(
            {
                "NAME": "djust",
                "DIRS": [],
                "APP_DIRS": False,
                "OPTIONS": {},
            }
        )

        template = backend.from_string("{% url 'home' as home_url %}Link: {{ home_url }}")
        request = RequestFactory().get("/")
        result = template.render({}, request)

        assert "Link: /" in result, f"Expected 'Link: /' but got: {result}"

    @override_settings(ROOT_URLCONF=__name__)
    def test_url_as_variable_with_args(self):
        """Test URL with args assigned to variable."""
        from djust.template_backend import DjustTemplateBackend

        backend = DjustTemplateBackend(
            {
                "NAME": "djust",
                "DIRS": [],
                "APP_DIRS": False,
                "OPTIONS": {},
            }
        )

        template = backend.from_string(
            "{% url 'post_detail' 'my-post' as post_url %}URL: {{ post_url }}"
        )
        request = RequestFactory().get("/")
        result = template.render({}, request)

        assert "URL: /post/my-post/" in result, f"Expected 'URL: /post/my-post/' but got: {result}"


class TestUrlTagInTemplateContext:
    """Test {% url %} tag behavior in various template contexts."""

    @override_settings(ROOT_URLCONF=__name__)
    def test_url_in_href_attribute(self):
        """Test URL in href attribute of anchor tag."""
        from djust.template_backend import DjustTemplateBackend

        backend = DjustTemplateBackend(
            {
                "NAME": "djust",
                "DIRS": [],
                "APP_DIRS": False,
                "OPTIONS": {},
            }
        )

        template = backend.from_string("<a href=\"{% url 'blog' %}\">Blog</a>")
        request = RequestFactory().get("/")
        result = template.render({}, request)

        assert '<a href="/blog/">Blog</a>' in result, f"Unexpected result: {result}"

    @override_settings(ROOT_URLCONF=__name__)
    def test_multiple_url_tags(self):
        """Test multiple URL tags in the same template."""
        from djust.template_backend import DjustTemplateBackend

        backend = DjustTemplateBackend(
            {
                "NAME": "djust",
                "DIRS": [],
                "APP_DIRS": False,
                "OPTIONS": {},
            }
        )

        template = backend.from_string(
            """
            <nav>
                <a href="{% url 'home' %}">Home</a>
                <a href="{% url 'blog' %}">Blog</a>
            </nav>
        """
        )
        request = RequestFactory().get("/")
        result = template.render({}, request)

        assert 'href="/"' in result, f"Expected home URL but got: {result}"
        assert 'href="/blog/"' in result, f"Expected blog URL but got: {result}"

    @override_settings(ROOT_URLCONF=__name__)
    def test_url_in_for_loop(self):
        """Test URL tag inside a for loop with static arguments.

        NOTE: URL tags with dynamic loop variable arguments (e.g., {% url 'post_detail' post.slug %})
        are not yet supported because URL resolution happens before the Rust rendering engine
        processes the for loop. For dynamic URLs inside loops, use a workaround like passing
        pre-resolved URLs in the context.

        This test uses static URL arguments to verify URLs inside for loops work when
        they don't reference loop variables.
        """
        from djust.template_backend import DjustTemplateBackend

        backend = DjustTemplateBackend(
            {
                "NAME": "djust",
                "DIRS": [],
                "APP_DIRS": False,
                "OPTIONS": {},
            }
        )

        # Use static URL inside for loop (no loop variable reference)
        template = backend.from_string(
            """{% for post in posts %}<a href="{% url 'blog' %}">{{ post.title }}</a>{% endfor %}"""
        )
        request = RequestFactory().get("/")
        result = template.render(
            {
                "posts": [
                    {"title": "First"},
                    {"title": "Second"},
                ]
            },
            request,
        )

        # Both links should have the static blog URL
        assert result.count("/blog/") == 2, f"Expected 2 blog URLs but got: {result}"
        assert "First" in result
        assert "Second" in result


class TestUrlTagErrorHandling:
    """Test {% url %} tag error handling."""

    @override_settings(ROOT_URLCONF=__name__)
    def test_url_with_nonexistent_name(self):
        """Test URL with non-existent URL name returns empty or raises error."""
        from djust.template_backend import DjustTemplateBackend

        backend = DjustTemplateBackend(
            {
                "NAME": "djust",
                "DIRS": [],
                "APP_DIRS": False,
                "OPTIONS": {},
            }
        )

        template = backend.from_string("{% url 'nonexistent_url' %}")
        request = RequestFactory().get("/")

        # Should either raise an error or return empty string
        # We prefer raising an error to match Django's behavior
        with pytest.raises(Exception):
            template.render({}, request)

    @override_settings(ROOT_URLCONF=__name__)
    def test_url_with_missing_required_arg(self):
        """Test URL with missing required argument."""
        from djust.template_backend import DjustTemplateBackend

        backend = DjustTemplateBackend(
            {
                "NAME": "djust",
                "DIRS": [],
                "APP_DIRS": False,
                "OPTIONS": {},
            }
        )

        template = backend.from_string("{% url 'post_detail' %}")  # Missing slug
        request = RequestFactory().get("/")

        # Should raise an error for missing required argument
        with pytest.raises(Exception):
            template.render({}, request)


class TestUrlTagWithQuoteVariations:
    """Test {% url %} tag with different quote styles."""

    @override_settings(ROOT_URLCONF=__name__)
    def test_url_with_single_quotes(self):
        """Test URL with single quotes."""
        from djust.template_backend import DjustTemplateBackend

        backend = DjustTemplateBackend(
            {
                "NAME": "djust",
                "DIRS": [],
                "APP_DIRS": False,
                "OPTIONS": {},
            }
        )

        template = backend.from_string("{% url 'home' %}")
        request = RequestFactory().get("/")
        result = template.render({}, request)

        assert result.strip() == "/", f"Expected / but got: {result}"

    @override_settings(ROOT_URLCONF=__name__)
    def test_url_with_double_quotes(self):
        """Test URL with double quotes."""
        from djust.template_backend import DjustTemplateBackend

        backend = DjustTemplateBackend(
            {
                "NAME": "djust",
                "DIRS": [],
                "APP_DIRS": False,
                "OPTIONS": {},
            }
        )

        template = backend.from_string('{% url "home" %}')
        request = RequestFactory().get("/")
        result = template.render({}, request)

        assert result.strip() == "/", f"Expected / but got: {result}"


class TestUrlTagEdgeCases:
    """Edge case tests for {% url %} tag regex parsing."""

    @override_settings(ROOT_URLCONF=__name__)
    def test_url_with_multiple_kwargs(self):
        """Test URL with multiple keyword arguments: {% url 'name' key1=val1 key2=val2 %}"""
        from djust.template_backend import DjustTemplateBackend

        backend = DjustTemplateBackend(
            {
                "NAME": "djust",
                "DIRS": [],
                "APP_DIRS": False,
                "OPTIONS": {},
            }
        )

        template = backend.from_string("{% url 'post_comment' post_id=42 comment_id=7 %}")
        request = RequestFactory().get("/")
        result = template.render({}, request)

        assert (
            result.strip() == "/post/42/comment/7/"
        ), f"Expected /post/42/comment/7/ but got: {result}"

    @override_settings(ROOT_URLCONF=__name__)
    def test_url_with_multiple_positional_args(self):
        """Test URL with multiple positional arguments."""
        from djust.template_backend import DjustTemplateBackend

        backend = DjustTemplateBackend(
            {
                "NAME": "djust",
                "DIRS": [],
                "APP_DIRS": False,
                "OPTIONS": {},
            }
        )

        # Using positional args for both post_id and comment_id
        template = backend.from_string("{% url 'post_comment' 42 7 %}")
        request = RequestFactory().get("/")
        result = template.render({}, request)

        assert (
            result.strip() == "/post/42/comment/7/"
        ), f"Expected /post/42/comment/7/ but got: {result}"

    @override_settings(ROOT_URLCONF=__name__)
    def test_url_with_string_containing_spaces_in_context(self):
        """Test URL with context variable containing spaces."""
        from djust.template_backend import DjustTemplateBackend

        backend = DjustTemplateBackend(
            {
                "NAME": "djust",
                "DIRS": [],
                "APP_DIRS": False,
                "OPTIONS": {},
            }
        )

        template = backend.from_string("{% url 'search' category=cat query=q %}")
        request = RequestFactory().get("/")
        # Note: Django URL patterns with str type will accept spaces
        result = template.render({"cat": "books", "q": "python"}, request)

        assert (
            "/search/books/python/" in result
        ), f"Expected /search/books/python/ but got: {result}"

    @override_settings(ROOT_URLCONF=__name__)
    def test_url_with_quoted_string_literal(self):
        """Test URL with quoted string literal containing special chars."""
        from djust.template_backend import DjustTemplateBackend

        backend = DjustTemplateBackend(
            {
                "NAME": "djust",
                "DIRS": [],
                "APP_DIRS": False,
                "OPTIONS": {},
            }
        )

        template = backend.from_string("{% url 'post_detail' 'my-awesome-post' %}")
        request = RequestFactory().get("/")
        result = template.render({}, request)

        assert (
            "/post/my-awesome-post/" in result
        ), f"Expected /post/my-awesome-post/ but got: {result}"

    @override_settings(ROOT_URLCONF=__name__)
    def test_url_preserves_surrounding_whitespace(self):
        """Test that URL resolution preserves surrounding template content."""
        from djust.template_backend import DjustTemplateBackend

        backend = DjustTemplateBackend(
            {
                "NAME": "djust",
                "DIRS": [],
                "APP_DIRS": False,
                "OPTIONS": {},
            }
        )

        template = backend.from_string("Link: {% url 'home' %} end")
        request = RequestFactory().get("/")
        result = template.render({}, request)

        assert "Link: / end" in result, f"Expected 'Link: / end' but got: {result}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
