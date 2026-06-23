from djust._log_utils import sanitize_for_log


def test_strips_cr_lf():
    # Both \r and \n are replaced with ?, yielding two ?s.
    assert sanitize_for_log("safe\r\nBOGUS_LOG_LINE") == "safe??BOGUS_LOG_LINE"


def test_strips_tab():
    assert sanitize_for_log("a\tb") == "a?b"


def test_strips_null():
    assert sanitize_for_log("a\x00b") == "a?b"


def test_preserves_ascii():
    assert sanitize_for_log("hello world") == "hello world"
    assert sanitize_for_log("handler-name_123") == "handler-name_123"


def test_handles_none():
    assert sanitize_for_log(None) == "None"


def test_handles_non_string():
    assert sanitize_for_log(42) == "42"
    assert sanitize_for_log({"k": "v"}) == "{'k': 'v'}"


def test_truncates_long_values():
    val = "x" * 500
    out = sanitize_for_log(val)
    assert len(out) == 200
    assert out.endswith("...")
    assert out.startswith("x" * 197)


def test_preserves_printable_unicode():
    assert sanitize_for_log("café") == "café"
    assert sanitize_for_log("日本語") == "日本語"


def test_log_sanitizer_barrier_pin():
    """Pin the explicit CR/LF ``.replace`` in ``sanitize_for_log`` (#2465/#2466).

    The ``isprintable()`` loop already maps line breaks to "?", so the trailing
    ``.replace("\\r", "").replace("\\n", "")`` is a runtime no-op — but it is the
    form CodeQL ``py/log-injection`` recognizes as a sanitizer barrier, and it is
    the reason ``sanitize_for_log(request.path)`` at the openapi / observability
    gates clears the alert. A "looks redundant, remove it" refactor would silently
    re-open the alert, so this pin is mechanical (#1859: a load-bearing pin must
    be able to go red).
    """
    import inspect

    from djust import _log_utils

    src = inspect.getsource(_log_utils.sanitize_for_log)
    assert r'.replace("\r", "")' in src and r'.replace("\n", "")' in src, (
        "sanitize_for_log must keep the explicit CR/LF .replace — it is the "
        "CodeQL py/log-injection recognized barrier (#2465/#2466)."
    )
    # The runtime guarantee the barrier pins: output is always line-break free.
    assert "\n" not in sanitize_for_log("a\nb")
    assert "\r" not in sanitize_for_log("a\r\nb")
