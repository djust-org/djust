"""Pure functions over the recorder's JSON lines: summarise, format, compare.

Two buckets, deliberately:

``engine``
    the tests that made at least one call on the adapter. This is the
    HEADLINE — the only number engine work can move.
``all``
    the whole label. Printed and stored too, but ~28 % of ``template_tests``
    measures Django against itself (``test_parser``, ``test_context``, …) and
    cannot be moved by any engine work; a headline of the whole label would be
    padded and every later gain would read smaller than it is.

``untouched`` (the complement of ``engine``) must be 100 % OK by
construction — a failure there is a harness bug, not a djust finding, and the
summary prints it as a WARNING.

SKIP and XFAIL are excluded from every denominator; ``percent = ok / (ok +
fail + error)`` rounded half-up to two decimals, ``0/0 → 0.00``.

``not_ok`` (#2722)
    every id whose status is not OK — FAIL, ERROR, SKIP and XFAIL — keyed to
    that status. It is the per-cell half of the ratchet: the two percentages
    cannot see a swap that fixes five cells and breaks five others, so
    ``compare`` also reports every id that is FAIL/ERROR now and was NOT in
    the baseline's ``not_ok``. The not-OK set is stored rather than the OK
    set because it is ~30 ids against ~1440. SKIP and XFAIL are in it so a
    cell that was never counted cannot read as "was OK". The set is only
    ever derived from a run's records (``summarize``), never hand-typed.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Iterable

_COUNTED = ("OK", "FAIL", "ERROR")
_FAILING = ("FAIL", "ERROR")
_NOT_OK = ("FAIL", "ERROR", "SKIP", "XFAIL")


def percent(ok: int, ran: int) -> float:
    """``ok / ran`` as a percentage, two decimals, half-up; ``0/0 → 0.0``."""
    if ran == 0:
        return 0.0
    value = (Decimal(ok) * 100 / Decimal(ran)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return float(value)


def format_percent(value: float) -> str:
    return "%.2f%%" % value


@dataclass
class Bucket:
    ok: int = 0
    fail: int = 0
    error: int = 0

    @property
    def ran(self) -> int:
        return self.ok + self.fail + self.error

    @property
    def percent(self) -> float:
        return percent(self.ok, self.ran)

    def add(self, status: str) -> None:
        if status == "OK":
            self.ok += 1
        elif status == "FAIL":
            self.fail += 1
        elif status == "ERROR":
            self.error += 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "fail": self.fail,
            "error": self.error,
            "ran": self.ran,
            "percent": self.percent,
        }


@dataclass
class Summary:
    engine: Bucket = field(default_factory=Bucket)
    all: Bucket = field(default_factory=Bucket)
    untouched: Bucket = field(default_factory=Bucket)
    skipped: int = 0
    xfail: int = 0
    crashes: list[str] = field(default_factory=list)
    not_ok: dict[str, str] = field(default_factory=dict)

    @property
    def untouched_failures(self) -> int:
        return self.untouched.fail + self.untouched.error

    def as_dict(self) -> dict[str, Any]:
        """The baseline schema: top-level = the engine subset; ``all`` = the label."""
        data = self.engine.as_dict()
        data["all"] = {**self.all.as_dict(), "skipped": self.skipped}
        # ``all`` key order: ok, fail, error, ran, skipped, percent
        data["all"] = {
            key: data["all"][key] for key in ("ok", "fail", "error", "ran", "skipped", "percent")
        }
        data["untouched_failures"] = self.untouched_failures
        data["crashes"] = list(self.crashes)
        data["not_ok"] = {test_id: self.not_ok[test_id] for test_id in sorted(self.not_ok)}
        return data


def load_records(path: Path) -> list[dict[str, Any]]:
    """Every parseable JSON line. A crash can leave a torn last line; skip it."""
    records: list[dict[str, Any]] = []
    if not path.exists():
        return records
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except ValueError:
                continue
    return records


def results_by_id(records: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """The last ``result`` record per id, in first-seen order."""
    out: dict[str, dict[str, Any]] = {}
    for record in records:
        if record.get("event") != "result" or "id" not in record:
            continue
        out[record["id"]] = record
    return out


def in_flight_ids(records: Iterable[dict[str, Any]]) -> list[str]:
    """Ids that started and never produced a result — the crash victims."""
    started: list[str] = []
    finished: set[str] = set()
    for record in records:
        if record.get("event") == "start":
            started.append(record["id"])
        elif record.get("event") == "result":
            finished.add(record["id"])
    return [test_id for test_id in started if test_id not in finished]


def summarize(records: Iterable[dict[str, Any]]) -> Summary:
    summary = Summary()
    for record in results_by_id(records).values():
        status = record.get("status")
        if status in _NOT_OK:
            summary.not_ok[record["id"]] = status
        if status == "SKIP":
            summary.skipped += 1
            continue
        if status == "XFAIL":
            summary.xfail += 1
            continue
        if status not in _COUNTED:
            continue
        summary.all.add(status)
        if record.get("touched"):
            summary.engine.add(status)
        else:
            summary.untouched.add(status)
        if record.get("crash"):
            summary.crashes.append(record["id"])
    return summary


def format_per_test_line(record: dict[str, Any]) -> str:
    status = record["status"]
    line = "%-5s %s" % (status, record["id"])
    if status in ("FAIL", "ERROR", "SKIP") and record.get("message"):
        line += " | %s" % record["message"]
    return line


def format_summary(summary: Summary) -> list[str]:
    """The summary block — the exact lines the issue asks for, plus context."""
    engine, whole = summary.engine, summary.all
    if engine.ran:
        headline = "Django test suite passing: %s" % format_percent(engine.percent)
    else:
        headline = (
            "Django test suite passing: %s   (no test reached the djust engine — "
            "the headline is the whole label)" % format_percent(whole.percent)
        )
    lines = [
        headline,
        "%d ERROR / %d FAIL / %d OK   (%d tests exercised the djust engine; %d skipped)"
        % (engine.error, engine.fail, engine.ok, engine.ran, summary.skipped),
        "whole label: %s  (%d OK / %d FAIL / %d ERROR of %d; "
        "%d never reached djust and passed on Django)"
        % (
            format_percent(whole.percent),
            whole.ok,
            whole.fail,
            whole.error,
            whole.ran,
            summary.untouched.ok,
        ),
    ]
    if summary.untouched_failures:
        lines.append(
            "WARNING: harness integrity: %d untouched tests failed "
            "(Django-vs-Django must be 100%% — this is a runner bug, not a djust finding)"
            % summary.untouched_failures
        )
    else:
        lines.append("harness integrity: 0 untouched tests failed")
    lines.append(
        "crashes isolated: %d (listed above as ERROR: process crashed)" % len(summary.crashes)
    )
    return lines


def build_result(
    summary: Summary,
    *,
    django_version: str,
    tag: str,
    generated_by: str = "scripts/run-django-template-suite.py --write-baseline",
) -> dict[str, Any]:
    """The baseline document: identity, the two buckets, the crashers."""
    data: dict[str, Any] = {"django": django_version, "tag": tag}
    data.update(summary.as_dict())
    data["generated_at"] = (
        datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )
    data["generated_by"] = generated_by
    return data


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n", encoding="utf-8")


DOC_MARKER = "<!-- django-suite-claim -->"
_PERCENT_RE = re.compile(r"\d+\.\d{2}(?=\s?%)")
_COUNTS_RE = re.compile(r"(?<![\d,])(\d[\d,]*) of (the )?(\d[\d,]*)(?![\d,])")


def _same_grouping(new: int, like: str) -> str:
    """Render `new` with the thousands separators `like` used, or without."""
    return f"{new:,}" if "," in like else str(new)


def rewrite_doc_claim(text: str, result: dict[str, Any]) -> str:
    """Retarget every `<!-- django-suite-claim -->` line at `result`.

    The scoreboard headline lives in the baseline JSON and in prose in two
    docs (#2561 generalised the pin to both). Regenerating it by hand is
    three edits, and the issue that asked for this (#2615) counted three
    such regenerations across two PRs — plus a stale-by-34-cells baseline
    that passed green. So `--write-baseline` writes all three.

    Both the percentage AND the `<ok> of <ran>` pair are rewritten. The
    counts were prose nothing checked: `TestDocClaimMatchesBaseline`
    compared only the percentage, so `1032 of 1047` could go stale while
    the figure beside it stayed pinned and green. The comma grouping of
    whatever was there is preserved, because README writes `1,047` and
    `docs/TEMPLATE_BACKEND.md` writes `1047`.

    Lines without the marker are untouched, so a percentage elsewhere in
    the document is never rewritten by accident.
    """
    ok, ran = int(result["ok"]), int(result["ran"])
    percent_text = format_percent(result["percent"]).rstrip("%")

    out = []
    for line in text.splitlines(keepends=True):
        if DOC_MARKER not in line:
            out.append(line)
            continue
        line = _PERCENT_RE.sub(lambda _m: percent_text, line)
        line = _COUNTS_RE.sub(
            lambda m: (
                "%s of %s%s"
                % (
                    _same_grouping(ok, m.group(1)),
                    m.group(2) or "",
                    _same_grouping(ran, m.group(3)),
                )
            ),
            line,
        )
        out.append(line)
    return "".join(out)


def rewrite_doc_claims(paths: Iterable[Path], result: dict[str, Any]) -> list[Path]:
    """Apply `rewrite_doc_claim` in place; return the files that changed."""
    changed = []
    for path in paths:
        try:
            before = path.read_text(encoding="utf-8")
        except OSError:
            continue
        after = rewrite_doc_claim(before, result)
        if after != before:
            path.write_text(after, encoding="utf-8")
            changed.append(path)
    return changed


def compare(baseline: dict[str, Any], current: dict[str, Any]) -> tuple[int, list[str]]:
    """The ratchet: 1 if either percentage dropped below the baseline OR any
    cell that was OK in the baseline is FAIL/ERROR now, else 0.

    A tag mismatch is a WARNING and 0 — the numbers are not comparable, and
    refusing would block the Django-bump PR that is exactly when the
    baseline must be regenerated. A current run in which nothing reached
    the djust engine is 1: the adapter did not install, and a 100 % whole
    label in that state is not an improvement.

    The per-cell arm (#2722) is independent of the percentages: a swap that
    fixes five cells and breaks five keeps both numbers and still exits 1,
    with the broken ids named. "Was OK in the baseline" means "not in the
    baseline's ``not_ok``" — the OK set is not stored. An id in ``not_ok``
    that is absent from the run (removed or renamed upstream, or a
    ``--label`` subset) is listed as informational, never a regression,
    following the tag-mismatch arm. A baseline written before ``not_ok``
    existed, or a run JSON without per-test records, gets a WARNING and the
    percentage ratchet alone.
    """
    lines: list[str] = []
    base_tag, cur_tag = baseline.get("tag"), current.get("tag")
    if base_tag != cur_tag:
        lines.append(
            "WARNING: baseline is Django %s, this run is Django %s — not comparable, "
            "regenerate the baseline with --write-baseline" % (base_tag, cur_tag)
        )
        return 0, lines

    base_all = baseline.get("all", {})
    cur_all = current.get("all", {})
    lines.append(
        "engine subset: baseline %s (%d of %d) -> current %s (%d of %d)"
        % (
            format_percent(baseline.get("percent", 0.0)),
            baseline.get("ok", 0),
            baseline.get("ran", 0),
            format_percent(current.get("percent", 0.0)),
            current.get("ok", 0),
            current.get("ran", 0),
        )
    )
    lines.append(
        "whole label:   baseline %s (%d of %d) -> current %s (%d of %d)"
        % (
            format_percent(base_all.get("percent", 0.0)),
            base_all.get("ok", 0),
            base_all.get("ran", 0),
            format_percent(cur_all.get("percent", 0.0)),
            cur_all.get("ok", 0),
            cur_all.get("ran", 0),
        )
    )

    code = 0
    if baseline.get("ran", 0) and not current.get("ran", 0):
        lines.append(
            "FAIL: no test reached the djust engine in this run (the adapter did not install?)"
        )
        code = 1
    if current.get("percent", 0.0) < baseline.get("percent", 0.0):
        lines.append(
            "FAIL: engine subset dropped from %s to %s"
            % (format_percent(baseline["percent"]), format_percent(current["percent"]))
        )
        code = 1
    if cur_all.get("percent", 0.0) < base_all.get("percent", 0.0):
        lines.append(
            "FAIL: whole label dropped from %s to %s"
            % (format_percent(base_all["percent"]), format_percent(cur_all["percent"]))
        )
        code = 1
    per_cell_code, per_cell_lines = _compare_cells(baseline, current)
    lines.extend(per_cell_lines)
    code = max(code, per_cell_code)
    if code == 0:
        lines.append("OK: no drop against the baseline")
    return code, lines


def _compare_cells(baseline: dict[str, Any], current: dict[str, Any]) -> tuple[int, list[str]]:
    """The per-cell arm of ``compare``: (1, lines) on a regressed cell."""
    lines: list[str] = []
    base_not_ok = baseline.get("not_ok")
    if not isinstance(base_not_ok, dict):
        lines.append(
            "WARNING: the baseline has no per-cell `not_ok` set — only the percentages "
            "are ratcheted; regenerate it with --write-baseline"
        )
        return 0, lines
    tests = current.get("tests")
    if not isinstance(tests, list):
        lines.append(
            "WARNING: the run JSON has no per-test `tests` records — only the "
            "percentages are ratcheted"
        )
        return 0, lines

    records = {t["id"]: t for t in tests if isinstance(t, dict) and "id" in t}
    regressed = sorted(
        test_id
        for test_id, rec in records.items()
        if rec.get("status") in _FAILING and test_id not in base_not_ok
    )
    fixed = sorted(
        test_id
        for test_id in base_not_ok
        if test_id in records and records[test_id].get("status") == "OK"
    )
    absent = sorted(test_id for test_id in base_not_ok if test_id not in records)

    if fixed:
        lines.append(
            "%d cell(s) not OK in the baseline are OK now (regenerate the baseline "
            "with --write-baseline to keep them):" % len(fixed)
        )
        lines.extend("  %-5s -> OK    %s" % (base_not_ok[test_id], test_id) for test_id in fixed)
    if absent:
        lines.append(
            "%d baseline not-OK id(s) absent from this run (removed or renamed upstream, "
            "or a --label subset) — informational, not a regression:" % len(absent)
        )
        lines.extend("  %-5s (absent) %s" % (base_not_ok[test_id], test_id) for test_id in absent)
    if regressed:
        lines.append("FAIL: %d cell(s) were OK in the baseline and are not now:" % len(regressed))
        for test_id in regressed:
            line = "  %-5s %s" % (records[test_id].get("status"), test_id)
            if records[test_id].get("message"):
                line += " | %s" % records[test_id]["message"]
            lines.append(line)
        return 1, lines
    lines.append("per-cell: no cell that was OK in the baseline regressed")
    return 0, lines
