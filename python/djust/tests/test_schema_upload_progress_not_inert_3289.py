"""#3289 — ``dj-upload-progress`` is a working directive. The schema (the source
of the generated AI context) described it as INERT, which told readers not to
use it. Pin the description against that wording."""

from djust.schema import DIRECTIVES


def _entry():
    return next(d for d in DIRECTIVES if d["name"] == "dj-upload-progress")


def test_schema_does_not_call_dj_upload_progress_inert():
    desc = _entry()["description"]
    assert "INERT" not in desc.upper()
    assert "no client code" not in desc.lower()
    assert "no progress bar is created" not in desc.lower()


def test_schema_describes_the_client_behaviour():
    desc = _entry()["description"]
    assert "<progress>" in desc
    assert "theme_progress" in desc
