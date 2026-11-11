"""``_rust.serialize_context`` ends a cycle and a deep chain instead of overflowing the stack (#3814).

``serialize_python_value`` (behind the public ``#[pyfunction]`` ``_rust.serialize_context``) builds a
model's dict with ``model_to_dict`` and recurses into every value. With ``Person.friends =
ManyToManyField("self")`` and A <-> B, ``model_to_dict(a)`` holds ``[b]``, whose dict holds ``[a']``
(a FRESH instance, so no identity check ends it), and so on until the stack overflows:
``Fatal Python error: Segmentation fault``. ``guard_panic`` cannot catch that, it is the process's
stack and not a Rust panic, so the only way to observe the failure is to run it in a CHILD and read
how the child ended.

**Who calls it** (measured in #3778): nothing in production. The question of #3814 was whether the
PRODUCTION serializers have the same shape. They do not, for the reason this file pins in
``TestTheProductionPathsDoNotRecurse``: ``serialize_object_with_paths`` / ``serialize_queryset`` walk
the template's field-path TREE (finite, whatever the data is), and the Python encoder is capped at
``serialization_max_depth``. Only this all-fields model arm had no path tree to bound it.

The fix is at the recursion: every recursive step goes through ``serialize_nested``, which fails
CLOSED to ``str(obj)`` past a depth ceiling or into a container / model row the walk is already
inside of. A ``render()`` that returns a non-``str`` falls back to ``str(obj)`` too (it raised
``TypeError`` out of the function).

Reproduction fidelity (#1650 / #1638): a REAL table and a REAL relation, run as a child process whose
exit status is the verdict (a segfault is ``-11``), watched on the clock AND on memory (macOS refuses
``setrlimit(RLIMIT_AS)``, hence the poll), with a control that proves the harness sees the failure it
guards against.

Refs #3814, #3778, #2624, #1468.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time

import pytest

pytestmark = pytest.mark.pr_tier

_DEADLINE = float(os.environ.get("DJUST_3814_DEADLINE", "60"))
_RSS_CEILING_KB = 1_500_000

#: A child that builds a REAL ``Person`` table (symmetrical self-M2M) and runs one SHAPE. Everything
#: it needs is configured inline, so it does not depend on the parent's Django settings or database.
_CHILD = r"""
import json, sys
import django
from django.conf import settings

settings.configure(
    INSTALLED_APPS=["django.contrib.contenttypes", "django.contrib.auth", "djust"],
    DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
    DEFAULT_AUTO_FIELD="django.db.models.AutoField",
    TEMPLATES=[{"BACKEND": "djust.template.backend.DjustTemplateBackend",
                "DIRS": [], "APP_DIRS": True, "OPTIONS": {}}],
    SECRET_KEY="x", ALLOWED_HOSTS=["*"],
)
django.setup()
from django.db import connection, models
from djust import _rust


class Tag(models.Model):
    name = models.CharField(max_length=20)

    class Meta:
        app_label = "auth"


class Person(models.Model):
    name = models.CharField(max_length=20)
    tags = models.ManyToManyField(Tag)
    friends = models.ManyToManyField("self")                       # symmetrical
    next = models.ManyToManyField("self", symmetrical=False, related_name="prev")

    class Meta:
        app_label = "auth"


with connection.schema_editor() as ed:
    ed.create_model(Tag)
    ed.create_model(Person)
a = Person.objects.create(name="A")
b = Person.objects.create(name="B")
a.friends.add(b)
tag = Tag.objects.create(name="T")  # pk 1, like Person A
a.tags.add(tag)

shape = sys.argv[1]
if shape == "queries":
    from django.test.utils import CaptureQueriesContext

    with CaptureQueriesContext(connection) as captured:
        _rust.serialize_context({"p": a})
    out = {"queries": len(captured), "sql": [q["sql"][-110:] for q in captured]}
elif shape == "flat_list":
    n = int(sys.argv[2])
    out = _rust.serialize_context({"p": list(range(n)), "d": [{"i": i} for i in range(n)]})
    out = {"ok": out["p"] == list(range(n)) and out["d"] == [{"i": i} for i in range(n)]}
elif shape == "symmetrical_m2m":
    out = _rust.serialize_context({"p": a})
elif shape == "symmetrical_m2m_in_containers":
    out = _rust.serialize_context({"c": {"rows": [a, (b,)]}, "q": Person.objects.all()})
elif shape == "self_containing_list":
    lst = []
    lst.append(lst)
    out = _rust.serialize_context({"p": lst})
elif shape == "self_containing_dict":
    d = {"k": 1}
    d["me"] = d
    out = _rust.serialize_context({"p": d})
elif shape == "self_containing_via_tuple":
    lst = []
    lst.append((lst,))
    out = _rust.serialize_context({"p": lst})
elif shape == "long_chain_of_distinct_rows":
    people = Person.objects.bulk_create([Person(name="n%d" % i) for i in range(int(sys.argv[2]))])
    people = list(Person.objects.filter(name__startswith="n").order_by("id"))
    through = Person.next.through
    through.objects.bulk_create(
        [through(from_person_id=x.id, to_person_id=y.id) for x, y in zip(people, people[1:])]
    )
    out = _rust.serialize_context({"p": people[0]})
elif shape == "render_returns_int":
    class C:
        def render(self):
            return 42
        def __str__(self):
            return "c-as-str"
    out = _rust.serialize_context({"p": C()})
elif shape == "render_returns_none":
    class C:
        def render(self):
            return None
        def __str__(self):
            return "c-as-str"
    out = _rust.serialize_context({"p": C()})
elif shape == "render_returns_str":
    class C:
        def render(self):
            return "<b>x</b>"
    out = _rust.serialize_context({"p": C()})
else:
    raise SystemExit("unknown shape " + shape)
print(json.dumps(out, default=str))
"""

#: The PRODUCTION paths over the same relation: field-path serializers and the encoder.
_PRODUCTION_CHILD = (
    _CHILD.split("shape = sys.argv[1]")[0]
    + r"""
from django.template import engines
from djust import LiveView
from djust.testing import LiveViewTestClient

shape = sys.argv[1]
if shape == "queryset_paths":
    out = _rust.serialize_queryset(
        [a], ["name", "friends.all.0.friends.all.0.name", "friends.all.name", "friends"]
    )
elif shape == "queryset_deep_paths":
    path = ".".join(["friends.all.0"] * 20) + ".name"
    out = _rust.serialize_queryset([a], [path])
elif shape == "template_backend":
    tpl = engines.all()[0].from_string(
        "{{ p.name }}|{{ p.friends.all.0.friends.all.0.friends.all.0.name }}|"
        "{% for f in p.friends.all %}{{ f.name }}{% endfor %}|{{ p.friends.all }}|{{ p }}"
    )
    out = tpl.render({"p": a})
elif shape == "liveview_attribute":
    class V(LiveView):
        template = (
            "<div dj-root>{{ p.name }}|{{ p.friends.all.0.friends.all.0.name }}|"
            "{% for f in p.friends.all %}{{ f.name }}{% endfor %}|{{ p }}</div>"
        )

        def mount(self, request, **kwargs):
            self.p = a
    client = LiveViewTestClient(V)
    client.mount()
    out = client.render()
elif shape == "encoder":
    from djust.serialization import DjangoJSONEncoder
    out = json.dumps({"p": a, "rows": [a, b]}, cls=DjangoJSONEncoder)
else:
    raise SystemExit("unknown shape " + shape)
print(json.dumps(out, default=str))
"""
)


def _rss_kb(pid: int) -> int:
    try:
        with open(f"/proc/{pid}/statm") as fh:
            return int(fh.read().split()[1]) * (os.sysconf("SC_PAGE_SIZE") // 1024)
    except (OSError, ValueError, IndexError):
        pass
    try:
        out = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True, timeout=5
        ).stdout.strip()
        return int(out) if out else 0
    except Exception:  # noqa: BLE001 - a dead pid is just "no memory"
        return 0


def _drain(stream, sink: list[str]) -> None:
    for line in stream:
        sink.append(line)


class Ended:
    """How a child ended: ``code`` (``-11`` is SIGSEGV), ``reason`` (``None`` on its own exit)."""

    def __init__(self, code: int, reason: str | None, out: str, err: str, peak_kb: int) -> None:
        self.code, self.reason, self.out, self.err, self.peak_kb = code, reason, out, err, peak_kb

    @property
    def clean(self) -> bool:
        return self.reason is None and self.code == 0

    def __repr__(self) -> str:
        return f"Ended(code={self.code}, reason={self.reason}, err={self.err.strip()[-300:]!r})"


def run_child(source: str, *argv: str, deadline: float = _DEADLINE) -> Ended:
    """Run ``source`` in a child; bounded by a deadline AND an RSS ceiling."""
    proc = subprocess.Popen(
        [sys.executable, "-c", source, *argv],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    out: list[str] = []
    err: list[str] = []
    threads = [
        threading.Thread(target=_drain, args=(proc.stdout, out), daemon=True),
        threading.Thread(target=_drain, args=(proc.stderr, err), daemon=True),
    ]
    for t in threads:
        t.start()
    began = time.monotonic()
    reason: str | None = None
    peak = 0
    while proc.poll() is None:
        if time.monotonic() - began > deadline:
            reason = "HANG"
            break
        peak = max(peak, _rss_kb(proc.pid))
        if peak > _RSS_CEILING_KB:
            reason = "RUNAWAY-MEMORY"
            break
        time.sleep(0.05)
    if reason is not None:
        proc.kill()
    proc.wait()
    for t in threads:
        t.join(timeout=5)
    return Ended(proc.returncode, reason, "".join(out), "".join(err), peak)


def result_of(ended: Ended):
    assert ended.clean, ended
    lines = [line for line in ended.out.splitlines() if line.startswith(("{", "[", '"'))]
    assert lines, ended.out
    return json.loads(lines[-1])


# ---------------------------------------------------------------------------------------------
class TestTheHarnessSeesTheFailureItGuards:
    """Not vacuous: a child that really overflows the stack is reported as a crash, not as clean."""

    def test_a_child_that_segfaults_is_not_clean(self):
        ended = run_child("import ctypes; ctypes.string_at(0)")
        assert not ended.clean and ended.code == -11, ended

    def test_a_child_that_runs_away_in_time_is_reported(self):
        ended = run_child("import time; time.sleep(60)", deadline=1.0)
        assert ended.reason == "HANG", ended


class TestACycleEndsAtTheRowItCameBackTo:
    def test_a_symmetrical_self_referential_m2m_serializes_and_exits_normally(self):
        ended = run_child(_CHILD, "symmetrical_m2m")
        out = result_of(ended)
        p = out["p"]
        assert p["name"] == "A"
        (friend,) = p["friends"]
        assert friend["name"] == "B"
        # B's friend is A again: the row the walk is INSIDE of, so it crosses as str(), not a dict
        assert friend["friends"] == ["Person object (1)"]
        assert ended.peak_kb < _RSS_CEILING_KB

    def test_the_same_row_in_containers_and_a_queryset_is_bounded_too(self):
        out = result_of(run_child(_CHILD, "symmetrical_m2m_in_containers"))
        assert out["c"]["rows"][0]["name"] == "A" and out["c"]["rows"][1][0]["name"] == "B"
        assert [row["name"] for row in out["q"]] == ["A", "B"]

    def test_two_siblings_that_share_a_row_are_not_mistaken_for_a_cycle(self):
        """A DAG is not a cycle: the same row reached from two different parents serializes fully
        both times (the path stack is popped on the way out, it is not a global seen-set)."""
        out = result_of(run_child(_CHILD, "symmetrical_m2m_in_containers"))
        assert out["c"]["rows"][0]["name"] == "A"
        assert out["q"][0]["friends"][0]["name"] == "B"  # A's friend B, also seen under "c"
        assert out["q"][1]["friends"][0]["name"] == "A"

    @pytest.mark.parametrize(
        "shape, expected",
        [
            ("self_containing_list", {"p": ["[[...]]"]}),
            ("self_containing_dict", {"p": {"k": 1, "me": "{'k': 1, 'me': {...}}"}}),
            ("self_containing_via_tuple", {"p": [["[([...],)]"]]}),
        ],
    )
    def test_a_container_that_holds_itself_ends_at_the_first_return(self, shape, expected):
        """EXACT, not "contains ...": the depth ceiling would also end these, 128 levels down, and
        a cycle check that is gated off must not hide behind it (two mechanisms, one symptom)."""
        assert result_of(run_child(_CHILD, shape)) == expected


class TestWhatTheGuardMustNotCatch:
    """Each of these is something a cycle check gets WRONG when a mechanism is off by one."""

    def test_two_classes_that_share_a_primary_key_are_not_a_cycle(self):
        """Person 1 holds Tag 1. Keyed on the pk alone they are 'the same row', and the tag would
        cross as ``str()``; the key is (class, pk)."""
        out = result_of(run_child(_CHILD, "symmetrical_m2m"))
        (tag,) = out["p"]["tags"]
        assert isinstance(tag, dict) and tag["name"] == "T"

    def test_a_flat_list_of_a_thousand_is_not_cut_at_the_depth_ceiling(self):
        """The ceiling is NESTING, not a count: a depth counter that is never decremented would turn
        the 129th element of any list into ``str()``."""
        assert result_of(run_child(_CHILD, "flat_list", "1000")) == {"ok": True}

    def test_the_row_check_runs_before_model_to_dict_so_the_next_ring_is_never_queried(self):
        """A <-> B reads each row's three many-to-many fields (``friends``, ``next``, ``tags``) once: A, then B.
        The third row is A again, and it is stopped BEFORE ``model_to_dict`` would read them a
        third time; a check placed after it would still run those queries before stopping."""
        out = result_of(run_child(_CHILD, "queries"))
        assert out["queries"] == 6, out  # 2 rows x 3 fields; 9 if the third row were read too


class TestADeepChainOfDistinctRowsIsBounded:
    def test_a_chain_far_past_the_ceiling_exits_normally_with_bounded_output(self):
        ended = run_child(_CHILD, "long_chain_of_distinct_rows", "3000")
        out = result_of(ended)
        assert out["p"]["name"] == "n0"
        nesting = json.dumps(out).count('"next"')
        assert 5 < nesting < 200, nesting  # followed a good way down, then stopped at str()
        assert len(ended.out) < 200_000
        assert ended.peak_kb < _RSS_CEILING_KB


class TestRenderThatReturnsANonStr:
    """A ``render()`` that returns no ``str`` has not produced markup: ``str(obj)``, not a TypeError."""

    @pytest.mark.parametrize("shape", ["render_returns_int", "render_returns_none"])
    def test_it_falls_back_to_str(self, shape):
        assert result_of(run_child(_CHILD, shape)) == {"p": "c-as-str"}

    def test_a_str_render_is_still_wrapped(self):
        """Control: the fallback narrows the arm, it does not remove it."""
        assert result_of(run_child(_CHILD, "render_returns_str")) == {"p": {"render": "<b>x</b>"}}


class TestTheProductionPathsDoNotRecurse:
    """The question #3814 asked: do the serializers that RUN in production have this shape?

    No. They are bounded by something that is not the data: the field-path tree the template reads
    (``serialize_object_with_paths`` / ``serialize_queryset``) and ``serialization_max_depth`` (the
    Python encoder). Run against the very relation that overflows the all-fields arm, each exits
    normally, so no guard is added to them: that would be a second mechanism for a recursion that
    does not exist (#1079).
    """

    @pytest.mark.parametrize(
        "shape",
        [
            "queryset_paths",
            "queryset_deep_paths",
            "template_backend",
            "liveview_attribute",
            "encoder",
        ],
    )
    def test_the_symmetrical_relation_exits_normally(self, shape):
        ended = run_child(_PRODUCTION_CHILD, shape)
        assert ended.clean, ended
        assert ended.peak_kb < _RSS_CEILING_KB

    def test_the_backend_renders_what_the_template_asked_for(self):
        out = result_of(run_child(_PRODUCTION_CHILD, "template_backend"))
        name, deep, friends, whole, row = out.split("|")
        # three hops by the template's own path: A -> B -> A -> B
        assert (name, deep, friends) == ("A", "B", "B")

    def test_the_encoder_is_capped_by_serialization_max_depth(self):
        out = json.loads(result_of(run_child(_PRODUCTION_CHILD, "encoder")))
        assert out["p"]["name"] == "A"
