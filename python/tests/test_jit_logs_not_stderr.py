"""The JIT serializer logs through ``logging``, never ``print`` to stderr.

``JITMixin._jit_serialize_queryset`` printed four diagnostics to
``sys.stderr``: no paths found, cache HIT, cache MISS and the query
optimization. The cache-HIT line fired on every render of a view that assigns
a QuerySet, bypassing logging's levels and handlers (reported downstream as
Point Guard N-29). They are ``logger.debug`` records of ``djust.mixins.jit``
now.
"""

import logging

import pytest
from django.contrib.auth.models import User

from djust.mixins.jit import JITMixin
from djust.session_utils import _jit_serializer_cache

TEMPLATE = "{% for u in users %}{{ u.username }}{% endfor %}"


@pytest.fixture
def users(db):
    User.objects.create(username="n29-ada")
    User.objects.create(username="n29-grace")
    _jit_serializer_cache.clear()
    yield User.objects.filter(username__startswith="n29-").order_by("username")
    _jit_serializer_cache.clear()


def _jit_messages(caplog):
    return [r.getMessage() for r in caplog.records if r.name == "djust.mixins.jit"]


def test_cache_miss_and_hit_write_nothing_to_stderr(users, capfd, caplog):
    caplog.set_level(logging.DEBUG, logger="djust.mixins.jit")
    mixin = JITMixin()

    first = mixin._jit_serialize_queryset(users.all(), TEMPLATE, "users")
    second = mixin._jit_serialize_queryset(users.all(), TEMPLATE, "users")

    assert [row["username"] for row in first] == ["n29-ada", "n29-grace"]
    assert second == first
    captured = capfd.readouterr()
    assert "[JIT]" not in captured.err
    assert "[JIT]" not in captured.out
    messages = _jit_messages(caplog)
    assert any(m.startswith("[JIT] Cache MISS for 'users'") for m in messages)
    assert any(m.startswith("[JIT] Cache HIT for 'users'") for m in messages)
    assert all(r.levelno == logging.DEBUG for r in caplog.records if r.name == "djust.mixins.jit")


def test_no_paths_fallback_writes_nothing_to_stderr(users, capfd, caplog):
    caplog.set_level(logging.DEBUG, logger="djust.mixins.jit")

    rows = JITMixin()._jit_serialize_queryset(users.all(), "{{ other }}", "users")

    assert len(rows) == 2
    assert "[JIT]" not in capfd.readouterr().err
    assert "[JIT] No paths found for 'users', using DjangoJSONEncoder fallback" in (
        _jit_messages(caplog)
    )


def test_query_optimization_is_a_debug_record(users, capfd, caplog):
    caplog.set_level(logging.DEBUG, logger="djust.mixins.jit")
    template = "{% for u in users %}{{ u.username }}{% for g in u.groups.all %}{{ g.name }}{% endfor %}{% endfor %}"

    JITMixin()._jit_serialize_queryset(users.all(), template, "users")

    assert "[JIT]" not in capfd.readouterr().err
    assert any(m.startswith("[JIT] Query optimization:") for m in _jit_messages(caplog))


def test_silent_at_info_level(users, capfd, caplog):
    caplog.set_level(logging.INFO, logger="djust.mixins.jit")
    mixin = JITMixin()

    mixin._jit_serialize_queryset(users.all(), TEMPLATE, "users")
    mixin._jit_serialize_queryset(users.all(), TEMPLATE, "users")

    assert capfd.readouterr().err == ""
    assert _jit_messages(caplog) == []
