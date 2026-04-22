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
