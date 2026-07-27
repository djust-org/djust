"""A **kwargs handler must not swallow a near-miss parameter silently (#2144).

#2137 was one instance of the class: the client sent ``field``, the framework's
own ``FormMixin.validate_field`` took ``field_name``, and because the signature
also had ``**kwargs`` the mismatch was absorbed instead of raising
``TypeError``. The handler ran on every keystroke and did nothing. No error, no
warning, nothing in the log.

Fixing two signatures fixed that instance. Every djust handler with ``**kwargs``
— the documented, recommended shape — had the same exposure, so the mechanism
is what needed the fix.

Why only *near misses*: an unexpected key bearing no resemblance to the
signature is the documented catch-all shape (``def on_event(self, **kwargs)``
reading ``kwargs`` directly). Warning on those would put noise on the very
paths — ``@input``, ``@change`` — where this has to stay quiet, and a warning
nobody can act on is one people learn to skip. The discriminator was chosen by
measurement, not taste: instrumenting the suppression point and running the
full suite showed the near-miss rule fires on **0 of 110** keys that legitimately
reach a ``**kwargs`` handler, while catching #2137's exact shape.
"""

from __future__ import annotations

import logging

import pytest

from djust import validation
from djust.validation import validate_handler_params


@pytest.fixture(autouse=True)
def _clear_warned_cache():
    """The warn-once cache is module-global, so a leak across tests would make
    a later test pass because an earlier one already warned."""
    validation._NEAR_MISS_WARNED.clear()
    yield
    validation._NEAR_MISS_WARNED.clear()


def _warnings(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]


# --- the regression this exists for --------------------------------------


def test_the_2137_shape_is_reported(caplog):
    # Verbatim: the client sends `field`, the handler declares `field_name`.
    def validate_field(field_name: str = "", value: str = "", **kwargs):
        pass

    with caplog.at_level(logging.WARNING):
        result = validate_handler_params(
            validate_field, {"field": "email", "value": "a@b.c"}, "validate_field"
        )

    assert result["valid"] is True, "the call itself still succeeds — only now it says so"
    msgs = _warnings(caplog)
    assert len(msgs) == 1, msgs
    assert "'field'" in msgs[0]
    assert "'field_name'" in msgs[0], "the message must name the parameter it should have been"
    assert "validate_field" in msgs[0]


def test_the_message_says_what_to_do_about_it(caplog):
    # A diagnostic that names a problem without naming a fix gets skimmed.
    # Asserted against the EMITTED message, not the source — a source grep
    # would pass on the text sitting in a docstring nobody logs.
    def validate_field(field_name: str = "", **kwargs):
        pass

    with caplog.at_level(logging.WARNING):
        validate_handler_params(validate_field, {"field": "email"}, "validate_field")

    msg = _warnings(caplog)[0]
    assert "Rename one side to match" in msg
    assert "**kwargs" in msg, "it must say WHY it was silent, or the reader cannot generalise"
    assert "field_name" in msg, "and list what the handler does accept"


# --- silence where silence is correct -------------------------------------


def test_an_unrelated_key_is_not_reported(caplog):
    # The documented catch-all shape. `metadata` resembles nothing declared,
    # so it is far more likely deliberate than a typo.
    def handler(query: str = "", **kwargs):
        pass

    with caplog.at_level(logging.WARNING):
        validate_handler_params(handler, {"query": "x", "metadata": {"a": 1}}, "search")
    assert _warnings(caplog) == []


def test_a_pure_kwargs_signature_is_never_reported(caplog):
    # Nothing declared to be a near miss OF — a catch-all by construction.
    def handler(**kwargs):
        pass

    with caplog.at_level(logging.WARNING):
        validate_handler_params(handler, {"anything": 1, "value": 2}, "generic")
    assert _warnings(caplog) == []


def test_a_handler_without_kwargs_is_untouched(caplog):
    # That path already fails loudly with `valid: False`; adding a warning
    # would double-report.
    def handler(value: str = ""):
        pass

    with caplog.at_level(logging.WARNING):
        result = validate_handler_params(handler, {"valu": "x"}, "e")
    assert result["valid"] is False, "the pre-existing hard error must still fire"
    assert _warnings(caplog) == []


@pytest.mark.parametrize("key", ["_args", "_cacheRequestId", "_activity", "_fuzz_extra"])
def test_underscore_prefixed_framework_keys_are_never_reported(caplog, key):
    # Framework-injected keys ride **kwargs by design. The private-name
    # convention covers the whole namespace, so a NEW `_foo` cannot start
    # generating noise later.
    def handler(cache_request_id: str = "", args: str = "", activity: str = "", **kwargs):
        pass

    with caplog.at_level(logging.WARNING):
        validate_handler_params(handler, {key: "x"}, "e")
    assert _warnings(caplog) == [], f"{key} is framework-injected"


@pytest.mark.parametrize("key", ["view_id", "component_id"])
def test_the_two_non_underscore_framework_keys_are_never_reported(caplog, key):
    # Found by instrumenting the real dispatch rather than reading call sites:
    # `view_id` survives the actor path (runtime.py:2734 reads it with .get
    # rather than the .pop at :3457).
    def handler(view: str = "", component: str = "", **kwargs):
        pass

    with caplog.at_level(logging.WARNING):
        validate_handler_params(handler, {key: "x"}, "e")
    assert _warnings(caplog) == [], f"{key} is a framework routing key"


# --- it must not become the noise it warns about --------------------------


def test_it_warns_once_per_handler_and_key_not_once_per_event(caplog):
    # This fires on @input/@change. A warning per keystroke is worse than none:
    # it buries the signal and trains people to filter the logger.
    def handler(field_name: str = "", **kwargs):
        pass

    with caplog.at_level(logging.WARNING):
        for _ in range(50):
            validate_handler_params(handler, {"field": "x"}, "validate_field")

    assert len(_warnings(caplog)) == 1, "50 keystrokes must produce one line, not fifty"


def test_a_second_distinct_key_still_warns(caplog):
    # Dedup must be per (handler, key) — collapsing to per-handler would hide
    # the second mismatch in a handler that has two.
    def handler(field_name: str = "", value_id: str = "", **kwargs):
        pass

    with caplog.at_level(logging.WARNING):
        validate_handler_params(handler, {"field": "x"}, "e")
        validate_handler_params(handler, {"valueid": "y"}, "e")
    assert len(_warnings(caplog)) == 2


def test_the_cache_is_bounded_against_client_controlled_names(caplog):
    # Parameter names come from the client, so an unbounded warn-once cache is
    # a memory-exhaustion vector for anyone who can open a socket.
    def handler(field_name: str = "", **kwargs):
        pass

    with caplog.at_level(logging.WARNING):
        for i in range(validation._NEAR_MISS_WARNED_CAP + 200):
            validate_handler_params(handler, {f"field_nam{i}": "x"}, "e")

    assert len(validation._NEAR_MISS_WARNED) <= validation._NEAR_MISS_WARNED_CAP, (
        "the cache must stop growing; client-supplied names would otherwise "
        "let a socket exhaust memory"
    )
    # And on reaching the cap it must go quiet rather than degrade into the
    # per-event logging this is designed to avoid.
    assert len(_warnings(caplog)) <= validation._NEAR_MISS_WARNED_CAP


def test_a_hostile_parameter_name_is_sanitised(caplog):
    # Names are client-controlled and land in a log line: CRLF would forge a
    # second log record.
    def handler(field_name: str = "", **kwargs):
        pass

    # The payload has to be a near miss or the rule never fires and the test
    # passes vacuously — the first version of this test used a long injection
    # string that resembled nothing, so it asserted nothing.
    hostile = "field_nam\r\n"
    with caplog.at_level(logging.WARNING):
        validate_handler_params(handler, {hostile: "x"}, "e")

    msgs = _warnings(caplog)
    assert len(msgs) == 1, "precondition: the hostile name must actually trigger the warning"
    assert "\n" not in msgs[0] and "\r" not in msgs[0], (
        "a newline in a client-supplied name must not reach the log intact — it "
        "would forge a second log record"
    )


def test_a_long_event_name_is_truncated(caplog):
    # The length cap is the part `sanitize_for_log` contributes that `%r` does
    # not. It is reachable only via event_name: a KEY long enough to matter can
    # never be a near miss of a short parameter, so capping the key is inert.
    def handler(field_name: str = "", **kwargs):
        pass

    with caplog.at_level(logging.WARNING):
        validate_handler_params(handler, {"field": "x"}, "E" * 10_000)

    msg = _warnings(caplog)[0]
    assert len(msg) < 1_000, (
        "a client-supplied event name must not be able to write an arbitrarily "
        f"long log line; got {len(msg)} chars"
    )


# --- the rule catches real mismatch shapes, not just the one we hit -------


@pytest.mark.parametrize(
    "declared,sent",
    [
        ("field_name", "field"),  # #2137
        ("itemId", "item_id"),  # snake/camel drift across the wire
        ("page_number", "page_num"),  # abbreviation
        ("is_checked", "checked"),  # prefix drop
        ("value", "val"),
    ],
)
def test_realistic_mismatches_are_caught(caplog, declared, sent):
    handler = eval(f"lambda {declared}='', **kwargs: None")  # noqa: S307
    with caplog.at_level(logging.WARNING):
        validate_handler_params(handler, {sent: "x"}, "e")
    assert len(_warnings(caplog)) == 1, f"{sent!r} vs {declared!r} should be reported"


@pytest.mark.parametrize("sent", ["csrf_token", "metadata", "extra", "id", "payload"])
def test_deliberate_passthrough_keys_stay_silent(caplog, sent):
    def handler(field_name: str = "", value: str = "", **kwargs):
        pass

    with caplog.at_level(logging.WARNING):
        validate_handler_params(handler, {sent: "x"}, "e")
    assert _warnings(caplog) == [], f"{sent!r} resembles nothing declared"
