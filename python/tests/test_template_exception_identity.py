"""User exceptions retain their identity across the Rust/backend boundary (#2568)."""

import traceback

import pytest
from django.template import Context, Engine

from djust.template import DjustTemplateBackend


class ApplicationError(Exception):
    pass


@pytest.mark.parametrize("error_type", [ZeroDivisionError, RuntimeError, ApplicationError])
@pytest.mark.parametrize(
    "source", ["{{ object.fail }}", "{% with x=object.fail %}{{ x }}{% endwith %}"]
)
def test_lookup_exception_identity_and_traceback(error_type, source):
    error = error_type("application failure")

    class Object:
        def fail(self):
            raise error

    context = {"object": Object()}
    backend = DjustTemplateBackend({"NAME": "test", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}})
    for render in [
        lambda: Engine().from_string(source).render(Context(context)),
        lambda: backend.from_string(source).render(context),
    ]:
        with pytest.raises(error_type) as caught:
            render()
        assert caught.value is error
        assert traceback.extract_tb(caught.value.__traceback__)[-1].name == "fail"


@pytest.mark.parametrize("body", [" \n<b> hi </b> \t <i>x</i>\n ", " \n plain text \t "])
def test_spaceless_trims_surrounding_whitespace(body):
    source = "{% spaceless %}" + body + "{% endspaceless %}"
    backend = DjustTemplateBackend({"NAME": "test", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}})
    assert backend.from_string(source).render({}) == Engine().from_string(source).render(Context())
