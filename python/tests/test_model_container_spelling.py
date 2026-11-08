"""A model held in a container prints as the model's text when a filter prints the WHOLE container.

``{{ rows|pprint }}`` and ``{{ rows|json_script:"x" }}`` over a list, tuple, queryset or dict holding
models write each model's ``str()``, as ``{{ rows }}`` does. Django prints ``[<Account: a1>]`` for the
first and refuses the second; the ``DjustTemplateBackend`` already spelled a raw model list row as its
``str()`` (#3901), and every entry now agrees.

The model reaches the engine as a map carrying a ``"__str__"`` text. The sinks that treat a map as a
model rather than as a dict read the full identity shape a serializer stamps (``Value::model_str``):
``pprint``, ``json_script``, ``.items`` / ``.keys`` / ``.values``, iteration, ``stringformat``
mapping specs, ``in`` and ``pluralize``. A dict from user data that only carries a ``"__str__"`` key
stays a dict to them; display (``{{ d }}``, ``|length``) goes by the looser marker (#2294). What does
NOT change, and is pinned below: a named read (``{{ r.owner_code }}`` is the template author naming
the attribute, as in Django), ``{{ rows }}``, ``{{ r }}``, ``|length``, an ordinary dict, and the data
itself, so ``get_state()`` and the serialized context are unchanged.

Intentional divergences from Django, each pinned: ``pprint`` prints the model's ``str()`` where Django
prints its ``repr()`` (the spelling #3901 chose for the backend), ``json_script`` writes the text as
a JSON string where Django raises ``TypeError`` (the permissive rule of #2429), and a ``SafeString``
text is escaped like any other text where Django emits the author's markup.

Engine path: the Rust engine through the LiveView state entry, ``DjustTemplateBackend`` and the raw
``render_template`` entry, with Django's own engine on real model instances as the oracle.
"""

from __future__ import annotations

import html as html_lib
import re

import pytest
from django.contrib.auth.models import User
from django.template import Context as DjangoContext
from django.template import Template as DjangoTemplate

from djust import _rust
from djust.live_view import LiveView
from djust.serialization import normalize_django_value
from djust.testing import LiveViewTestClient

SENT_A = "SENTINEL-A-rc17"
SENT_B = "SENTINEL-B-rc17"
SENT_C = "SENTINEL-C-rc17"
SENTINELS = (SENT_A, SENT_B, SENT_C)
DJ_ROOT = re.compile(r"<div dj-root[^>]*>(.*?)</div>", re.S)


def _lv(src: str, **state) -> str:
    """The real LiveView entry, with the values held as PUBLIC STATE set in ``mount``."""

    def mount(self, request, **kwargs):
        for name, value in state.items():
            setattr(self, name, value)

    view = type(
        "ContainerView", (LiveView,), {"template": f"<div dj-root>{src}</div>", "mount": mount}
    )
    client = LiveViewTestClient(view)
    client.mount()
    match = DJ_ROOT.search(client.render())
    assert match is not None
    return html_lib.unescape(match.group(1))


def _backend(src: str, ctx: dict) -> str:
    from djust.template import DjustTemplateBackend

    backend = DjustTemplateBackend(
        params={"NAME": "djust", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}}
    )
    return html_lib.unescape(str(backend.from_string(src).render(context=dict(ctx), request=None)))


def _raw(src: str, ctx: dict) -> str:
    return html_lib.unescape(_rust.render_template(src, dict(ctx)))


def _django(src: str, ctx: dict) -> str:
    return html_lib.unescape(DjangoTemplate(src).render(DjangoContext(dict(ctx))))


def _lv_entry(src: str, ctx: dict) -> str:
    return _lv(src, **ctx)


@pytest.fixture
def scratch_models():
    """Remove every model a test registered (the proxy below), and the registry caches, when it ends."""
    from django.apps import apps

    before = set(apps.all_models["auth"])
    try:
        yield
    finally:
        for name in set(apps.all_models["auth"]) - before:
            del apps.all_models["auth"][name]
        apps.clear_cache()


ENTRIES = {"liveview": _lv_entry, "backend": _backend, "raw": _raw}


@pytest.fixture
def world(db, scratch_models):
    """Two accounts: a proxy of ``User`` whose field, property and method return recognisable sentinels."""

    class Account(User):
        class Meta:
            proxy = True
            app_label = "auth"

        @property
        def owner_code(self):
            return SENT_A

        def get_note_text(self):
            return SENT_B

        def __str__(self):
            return f"Account:{self.username}"

    a1 = Account.objects.create(username="a1", first_name=SENT_C)
    a2 = Account.objects.create(username="a2", first_name=SENT_C)
    return {"Account": Account, "a1": a1, "a2": a2}


def _shapes(w):
    Account = w["Account"]
    return {
        "list": lambda: [w["a1"], w["a2"]],
        "tuple": lambda: (w["a1"], w["a2"]),
        "queryset": lambda: Account.objects.order_by("username"),
        "dict-of-list": lambda: {"items": [w["a1"], w["a2"]]},
        "dict-of-model": lambda: {"owner": w["a1"]},
    }


#: templates that name nothing on the rows and print the whole container (or a derived one)
WHOLE_PRINTS = {
    "pprint": "{{ rows|pprint }}",
    "json_script": '{{ rows|json_script:"x" }}',
    "first-pprint": "{{ rows|first|pprint }}",
    "slice-pprint": "{{ rows|slice:':1'|pprint }}",
    "dictsort-pprint": "{{ rows|dictsort:'username'|pprint }}",
    "loop-pprint": "{% for r in rows %}{{ r|pprint }};{% endfor %}",
    "loop-json_script": "{% for r in rows %}{{ r|json_script:'y' }}{% endfor %}",
    "with-alias-pprint": "{% with x=rows %}{{ x|pprint }}{% endwith %}",
    "loop-with-alias-pprint": "{% for r in rows %}{% with y=r %}{{ y|pprint }}{% endwith %}{% endfor %}",
    "slice-json_script": '{{ rows|slice:":1"|json_script:"z" }}',
}
#: a dict has no first / slice / dictsort / row loop: those templates only mean something on a sequence
SEQUENCE_ONLY = {
    "first-pprint",
    "slice-pprint",
    "dictsort-pprint",
    "loop-pprint",
    "loop-json_script",
    "loop-with-alias-pprint",
    "slice-json_script",
}


def _cells():
    for shape in ("list", "tuple", "queryset", "dict-of-list", "dict-of-model"):
        for name in WHOLE_PRINTS:
            if shape.startswith("dict") and name in SEQUENCE_ONLY:
                continue
            yield pytest.param(shape, name, id=f"{shape}-{name}")


class TestAWholeContainerPrintPrintsTheText:
    @pytest.mark.parametrize("entry", sorted(ENTRIES))
    @pytest.mark.parametrize("shape,name", list(_cells()))
    def test_a_model_prints_as_its_text(self, world, entry, shape, name) -> None:
        rows = _shapes(world)[shape]()
        out = ENTRIES[entry](WHOLE_PRINTS[name], {"rows": rows})
        printed = [m for m in SENTINELS if m in out]
        assert not printed, f"{entry}/{shape}/{name} printed {printed}: {out[:300]!r}"
        assert "a1" in out, out
        # nothing of the map's shape either: no key names, no braces around fields
        assert "owner_code" not in out and "get_note_text" not in out, out
        assert "__model__" not in out, out

    @pytest.mark.parametrize("entry", sorted(ENTRIES))
    def test_the_exact_spelling_on_a_list_and_a_queryset(self, world, entry) -> None:
        for shape in ("list", "queryset"):
            rows = _shapes(world)[shape]()
            ctx = {"rows": rows}
            assert ENTRIES[entry]("{{ rows|pprint }}", ctx) == "[Account:a1, Account:a2]", (
                entry,
                shape,
            )
            assert ENTRIES[entry]('{{ rows|json_script:"x" }}', ctx) == (
                '<script id="x" type="application/json">["Account:a1", "Account:a2"]</script>'
            ), (entry, shape)

    @pytest.mark.parametrize("entry", sorted(ENTRIES))
    def test_the_exact_spelling_of_a_model_nested_in_a_dict(self, world, entry) -> None:
        got = ENTRIES[entry]("{{ rows|pprint }}", {"rows": {"items": [world["a1"], world["a2"]]}})
        assert got == "{'items': [Account:a1, Account:a2]}", entry
        got = ENTRIES[entry]('{{ rows|json_script:"x" }}', {"rows": {"owner": world["a1"]}})
        assert got == '<script id="x" type="application/json">{"owner": "Account:a1"}</script>', (
            entry
        )

    @pytest.mark.parametrize("entry", sorted(ENTRIES))
    def test_loop_and_alias_forms_spell_each_row_the_same(self, world, entry) -> None:
        ctx = {"rows": [world["a1"], world["a2"]]}
        run = ENTRIES[entry]
        assert (
            run("{% for r in rows %}{{ r|pprint }};{% endfor %}", ctx) == "Account:a1;Account:a2;"
        )
        assert (
            run("{% with x=rows %}{{ x|pprint }}{% endwith %}", ctx) == "[Account:a1, Account:a2]"
        )
        assert run("{{ rows|first|pprint }}", ctx) == "Account:a1"
        assert run("{{ rows|slice:':1'|pprint }}", ctx) == "[Account:a1]"
        assert run("{{ rows|dictsort:'username'|pprint }}", ctx) == "[Account:a1, Account:a2]"

    @pytest.mark.parametrize("entry", sorted(ENTRIES))
    def test_a_model_wider_than_the_line_is_one_atom(self, world, entry) -> None:
        """``pprint`` wraps a container at 80 columns, one element per line, and a model's text is
        an atom that overruns the line: it is never laid out as a wrapped map of its fields."""
        world["a1"].username = "w" * 100
        world["a2"].username = "v" * 100
        run = ENTRIES[entry]
        assert run("{{ rows|pprint }}", {"rows": [world["a1"], world["a2"]]}) == (
            "[Account:" + "w" * 100 + ",\n Account:" + "v" * 100 + "]"
        )
        assert run("{{ rows|pprint }}", {"rows": {"owner": world["a1"]}}) == (
            "{'owner': Account:" + "w" * 100 + "}"
        )

    def test_the_django_oracle_never_printed_them_either(self, world) -> None:
        """Django on the same real instances: no marker, ever. The cells above bring djust to the same."""
        for shape in ("list", "tuple", "queryset", "dict-of-list", "dict-of-model"):
            rows = _shapes(world)[shape]()
            out = _django("{{ rows|pprint }}", {"rows": rows})
            assert not [m for m in SENTINELS if m in out], (shape, out)


class TestTheIntentionalDivergencesFromDjango:
    def test_pprint_prints_the_text_where_django_prints_the_repr(self, world) -> None:
        ctx = {"rows": [world["a1"], world["a2"]]}
        assert _django("{{ rows|pprint }}", ctx) == "[<Account: Account:a1>, <Account: Account:a2>]"
        assert _backend("{{ rows|pprint }}", ctx) == "[Account:a1, Account:a2]"
        assert _lv("{{ rows|pprint }}", rows=ctx["rows"]) == "[Account:a1, Account:a2]"

    def test_json_script_writes_the_text_where_django_raises(self, world) -> None:
        ctx = {"rows": [world["a1"]]}
        with pytest.raises(TypeError):
            _django('{{ rows|json_script:"x" }}', ctx)
        assert _backend('{{ rows|json_script:"x" }}', ctx) == (
            '<script id="x" type="application/json">["Account:a1"]</script>'
        )
        assert _lv('{{ rows|json_script:"x" }}', rows=ctx["rows"]) == (
            '<script id="x" type="application/json">["Account:a1"]</script>'
        )


class TestWhatDoesNotChange:
    @pytest.mark.parametrize("entry", sorted(ENTRIES))
    def test_whole_value_spellings_and_counts_agree_with_each_other(self, world, entry) -> None:
        ctx = {"rows": [world["a1"], world["a2"]]}
        run = ENTRIES[entry]
        assert run("{{ rows }}", ctx) == "[Account:a1, Account:a2]"
        assert run("{% for r in rows %}{{ r }};{% endfor %}", ctx) == "Account:a1;Account:a2;"
        assert run("{{ rows|length }}", ctx) == "2"
        assert run("{{ rows|first }}|{{ rows|last }}", ctx) == "Account:a1|Account:a2"
        assert run("{{ rows|join:',' }}", ctx) == "Account:a1,Account:a2"

    @pytest.mark.parametrize("entry", sorted(ENTRIES))
    def test_a_named_read_still_prints_what_it_names(self, world, entry) -> None:
        """The template author names the property: Django prints it, and so does djust."""
        ctx = {"rows": [world["a1"], world["a2"]]}
        src = "{% for r in rows %}{{ r.username }}|{{ r.owner_code }}|{{ r.get_note_text }}|{{ r.first_name }};{% endfor %}"
        want = _django(src, ctx)
        assert want == f"a1|{SENT_A}|{SENT_B}|{SENT_C};a2|{SENT_A}|{SENT_B}|{SENT_C};"
        assert ENTRIES[entry](src, ctx) == want

    def test_a_named_read_beside_a_whole_print_prints_what_is_named(self, world) -> None:
        """When the template names ``owner_code`` on the rows, the serializer carries exactly that key
        (no ``__str__`` marker, so this is not a model map to the filter): the whole print shows what the
        template itself named and nothing it did not. Unchanged by the fix, and pinned so a later
        change to either half is seen."""
        ctx = {"rows": [world["a1"], world["a2"]]}
        src = "{% for r in rows %}{{ r.owner_code }};{% endfor %}|{{ rows|pprint }}"
        out = _lv(src, rows=ctx["rows"])
        assert out.startswith(f"{SENT_A};{SENT_A};|"), out
        assert SENT_B not in out and SENT_C not in out, out
        assert "get_note_text" not in out and "first_name" not in out, out

    @pytest.mark.parametrize("entry", sorted(ENTRIES))
    def test_an_ordinary_dict_is_still_a_map(self, entry) -> None:
        ctx = {"d": {"b": 2, "a": "x"}, "rows": [{"n": 1}, {"n": 2}]}
        run = ENTRIES[entry]
        assert run("{{ d|pprint }}", ctx) == _django("{{ d|pprint }}", ctx) == "{'a': 'x', 'b': 2}"
        assert run("{{ rows|pprint }}", ctx) == _django("{{ rows|pprint }}", ctx)
        assert run('{{ d|json_script:"x" }}', ctx) == _django('{{ d|json_script:"x" }}', ctx)
        assert run('{{ rows|json_script:"x" }}', ctx) == _django('{{ rows|json_script:"x" }}', ctx)

    def test_a_property_named_by_another_variable_is_still_reachable(self, world) -> None:
        """The reason the map keeps its properties: ``data_table`` columns name them from a variable."""
        rows = [world["a1"], world["a2"]]
        cols = [{"key": "owner_code", "label": "Code"}]
        out = _lv("{% data_table rows=rows columns=cols %}", rows=rows, cols=cols)
        assert SENT_A in out, out[:400]

    def test_the_state_and_the_serialized_row_are_unchanged(self, world) -> None:
        """The change is in two filters; the data they read is exactly what it was."""
        row = normalize_django_value(world["a1"])
        assert row["owner_code"] == SENT_A and row["get_note_text"] == SENT_B
        assert (
            row["first_name"] == SENT_C
            and row["__str__"] == "Account:a1"
            and row["__model__"] == "Account"
        )

        def mount(self, request, **kwargs):
            self.rows = [world["a1"], world["a2"]]

        view = type(
            "StateView",
            (LiveView,),
            {"template": "<div dj-root>{{ rows|pprint }}</div>", "mount": mount},
        )
        client = LiveViewTestClient(view)
        client.mount()
        client.render()
        state = client.view_instance.get_state()
        assert [r.owner_code for r in state["rows"]] == [SENT_A, SENT_A]
        assert [r.get_note_text() for r in state["rows"]] == [SENT_B, SENT_B]


class TestTheTextIsEscapedLikeAnyOtherString:
    """The text is the model's ``str()``, which the application controls and a user may have typed."""

    UNUSUAL = [
        "</script><script>alert(1)</script>",
        'quote " backslash \\ tab \t newline \n nul \0 end',
        "<b>&amp;</b> café \U0001f600  ",
        "x" * 200_000,
    ]

    @pytest.mark.parametrize("entry", sorted(ENTRIES))
    @pytest.mark.parametrize(
        "text", UNUSUAL, ids=["script-close", "controls", "markup-unicode", "huge"]
    )
    def test_json_script_neutralises_the_text(self, world, entry, text) -> None:
        import json

        world["a1"].username = text
        out = ENTRIES[entry]('{{ rows|json_script:"x" }}', {"rows": [world["a1"], world["a2"]]})
        body = out[len('<script id="x" type="application/json">') : -len("</script>")]
        assert out.startswith('<script id="x" type="application/json">') and out.endswith(
            "</script>"
        )
        assert "</script>" not in body and "<script>" not in body and "<" not in body
        assert "\n" not in body and "\0" not in body
        assert json.loads(body) == [f"Account:{text}", "Account:a2"]
        assert not [m for m in SENTINELS if m in out]

    @pytest.mark.parametrize("entry", sorted(ENTRIES))
    def test_pprint_prints_the_text_as_it_is(self, world, entry) -> None:
        text = "</script><b>&</b>\n\0"
        world["a1"].username = text
        out = ENTRIES[entry]("{{ rows|pprint }}", {"rows": [world["a1"]]})
        assert not [m for m in SENTINELS if m in out]
        assert out.startswith("[Account:") and "owner_code" not in out


def _observe(run, src: str, ctx: dict) -> str:
    """The bytes, or ``RAISES`` for any exception (the TYPE differs across engines, the fact does not)."""
    try:
        return run(src, ctx)
    except Exception:  # noqa: BLE001 - a measurement: whether the render refuses is what is compared
        return "RAISES"


#: where a model sits when the template reads it: a loop variable over each container shape, a
#: ``{% with %}`` alias of ``|first`` / an index, a dict value, and a top-level model
WRAPS = {
    "loop": ("{% for r in rows %}BODY{% endfor %}", ("list", "tuple", "queryset")),
    "with-first": ("{% with r=rows|first %}BODY{% endwith %}", ("list", "tuple", "queryset")),
    "with-index": ("{% with r=rows.0 %}BODY{% endwith %}", ("list", "tuple")),
    "dict": ("{% for k, r in rows.items %}BODY{% endfor %}", ("dict-of-model",)),
    "top": ("{% with r=rows %}BODY{% endwith %}", ("model",)),
}
#: what a template does with a model that is not a mapping: Django answers every one of these
#: from the model itself (an attribute that is not there is empty), or refuses
NOT_A_MAPPING = {
    "items": "[{{ r.items }}]",
    "values": "[{{ r.values }}]",
    "keys": "[{{ r.keys }}]",
    "for-items": "{% for k, v in r.items %}{{ k }}={{ v }};{% endfor %}",
    "values-join": "{{ r.values|join:',' }}",
    "items-length": "{{ r.items|length }}",
    "items-if": "{% if r.items %}T{% else %}F{% endif %}",
    "iterate": "{% for k in r %}{{ k }},{% endfor %}",
    "join": "{{ r|join:',' }}",
    "unordered_list": "{{ r|unordered_list }}",
    "safeseq": "{{ r|safeseq|join:',' }}",
}


def _not_a_mapping_cells():
    for wrap, (_, shapes) in WRAPS.items():
        for shape in shapes:
            for name in NOT_A_MAPPING:
                yield pytest.param(wrap, shape, name, id=f"{wrap}-{shape}-{name}")


def _model_shapes(w):
    shapes = _shapes(w)
    shapes["model"] = lambda: w["a1"]
    return shapes


class TestAModelIsNotAMappingToATemplate:
    """``{{ r.items }}`` / ``.values`` / ``.keys`` and iteration over a model are empty or refused in
    Django. A model that crossed as a map answered them with its entries. The tail
    ``{{ rows|pprint }}`` names nothing on the rows, which is what makes the serializer carry the
    whole map, the mode these cells need."""

    @pytest.mark.parametrize("entry", sorted(ENTRIES))
    @pytest.mark.parametrize("wrap,shape,name", list(_not_a_mapping_cells()))
    def test_a_model_is_not_walked_as_a_mapping(self, world, entry, wrap, shape, name) -> None:
        template = WRAPS[wrap][0].replace("BODY", NOT_A_MAPPING[name])
        ctx = {"rows": _model_shapes(world)[shape]()}
        out = _observe(ENTRIES[entry], template + "|||{{ rows|pprint }}", ctx)
        head = out.split("|||")[0]
        printed = [m for m in SENTINELS if m in head]
        names = [n for n in ("owner_code", "get_note_text", "first_name", "__model__") if n in head]
        assert not printed and not names, f"{entry}/{wrap}/{shape}/{name}: {head[:300]!r}"

    @pytest.mark.parametrize("entry", sorted(ENTRIES))
    @pytest.mark.parametrize("wrap,shape,name", list(_not_a_mapping_cells()))
    def test_the_render_agrees_with_django_on_real_instances(
        self, world, entry, wrap, shape, name
    ) -> None:
        template = WRAPS[wrap][0].replace("BODY", NOT_A_MAPPING[name])
        make = _model_shapes(world)[shape]
        want = _observe(_django, template, {"rows": make()})
        # the tail is what makes the serializer carry the whole map (see the class docstring)
        got = _observe(ENTRIES[entry], template + "|||{{ rows|pprint }}", {"rows": make()})
        # a template that names ``items`` makes the serializer carry an empty subset map, which a
        # bare ``{{ r.items }}`` spells ``dict_items([])`` where Django prints nothing: an older,
        # cosmetic difference that prints no data, so it is read as empty here
        head = re.sub(r"dict_(items|keys|values)\(\[\]\)", "", got.split("|||")[0])
        assert head == want, f"{entry}/{wrap}/{shape}/{name}: djust {got!r}, Django {want!r}"

    @pytest.mark.parametrize("entry", sorted(ENTRIES))
    def test_a_named_read_and_the_whole_value_spellings_are_unchanged(self, world, entry) -> None:
        ctx = {"rows": [world["a1"], world["a2"]]}
        run = ENTRIES[entry]
        src = "{% for r in rows %}{{ r.owner_code }}|{{ r.username }};{% endfor %}"
        assert run(src, ctx) == _django(src, ctx)
        assert run("{{ rows }}|{{ rows|length }}", ctx) == "[Account:a1, Account:a2]|2"

    @pytest.mark.parametrize("entry", sorted(ENTRIES))
    def test_an_ordinary_dict_keeps_its_methods_and_its_iteration(self, entry) -> None:
        ctx = {"d": {"b": 2, "a": "x"}, "rows": [{"n": 1}, {"n": 2}]}
        run = ENTRIES[entry]
        for src in (
            "{% for k, v in d.items %}{{ k }}={{ v }};{% endfor %}",
            "{{ d.keys|join:',' }}|{{ d.values|join:',' }}",
            "{% for k in d %}{{ k }},{% endfor %}",
            "{{ d|join:',' }}",
            "{% for r in rows %}{% for k, v in r.items %}{{ k }}{{ v }}{% endfor %}{% endfor %}",
        ):
            assert run(src, ctx) == _django(src, ctx), src

    @pytest.mark.parametrize("entry", ["backend", "raw"])
    def test_a_container_whose_in_raises_is_false_for_in_and_not_in(self, entry) -> None:
        """Django's smart-if reads a raising ``in`` as false for ``in`` AND ``not in``: the same rule
        as for a model, here for an object the engine holds as a live handle."""

        class RaisesOnIn:
            def __bool__(self):
                return False

            def __contains__(self, item):
                raise RuntimeError("no membership")

            def __iter__(self):
                raise RuntimeError("no iteration")

        class RaisesOnNext:
            def __iter__(self):
                return self

            def __next__(self):
                raise RuntimeError("no items")

        for make in (RaisesOnIn, RaisesOnNext):
            for src in (
                "{% if 1 in c %}YES{% else %}NO{% endif %}",
                "{% if 1 not in c %}YES{% else %}NO{% endif %}",
            ):
                want = _observe(_django, src, {"c": make()})
                assert want == "NO"
                assert _observe(ENTRIES[entry], src, {"c": make()}) == want, (entry, make, src)

    def test_a_plain_dict_carrying_a_text_key_is_a_dict_to_the_sinks(self) -> None:
        """A dict from user data (a stored JSON column) that carries a string ``"__str__"`` key is a
        dict to every sink that treats a map as a model: it keeps its views, its iteration and its
        mapping spelling. Display
        (``{{ d }}``, ``|length``) still goes by the looser marker (#2294)."""
        d = {"__str__": "lbl", "k1": "v1"}
        assert _lv("{{ d }}|{{ d.items|length }}", d=d) == "lbl|2"
        assert _lv("{% for k in d %}{{ k }},{% endfor %}", d=d) == "__str__,k1,"
        assert _lv("{% for k, v in d.items %}{{ k }}={{ v }};{% endfor %}", d=d) == (
            "__str__=lbl;k1=v1;"
        )
        assert _lv("{{ d|pprint }}", d=d) == "{'__str__': 'lbl', 'k1': 'v1'}"
        assert _lv('{{ d|json_script:"x" }}', d=d) == (
            '<script id="x" type="application/json">{"__str__": "lbl", "k1": "v1"}</script>'
        )
        assert _lv("{{ d|join:',' }}", d=d) == "__str__,k1"
        # a partial identity shape (``__str__`` beside only one of ``__model__`` / ``pk``) is a dict too
        for partial in (
            {"__str__": "x", "__model__": "T", "k1": 1},
            {"__str__": "x", "pk": 1, "k1": 1},
        ):
            out = _lv("{{ d|pprint }}|{{ d|join:',' }}|{{ d.items|length }}", d=partial)
            assert out.startswith("{") and "'__str__': 'x'" in out and out.endswith("|3"), out
        # a non-text marker is not a marker for display either
        assert _lv("{{ d.n }}|{{ d.items|length }}", d={"__str__": 5, "n": 1}) == "1|2"

    def test_a_dict_carrying_the_whole_identity_shape_is_still_read_as_a_model(self) -> None:
        """The honest limit: any dict can carry the three keys a serializer stamps (``__str__``,
        ``__model__``, ``pk``). The requirement narrows the accident, it does not make the marker
        unforgeable, and this pins that so a change to it is a decision."""
        d = {"__str__": "label", "__model__": "Thing", "pk": 1, "n": 1}
        assert _lv("{{ d|pprint }}|{{ d.items|length }}", d=d) == "label|0"

    @pytest.mark.parametrize("entry", sorted(ENTRIES))
    def test_a_model_is_not_a_mapping_to_stringformat_in_or_pluralize(self, world, entry) -> None:
        """Django: ``"%(name)s" % model`` is a ``TypeError`` (the filter answers empty), ``x in model``
        and ``x not in model`` both raise (smart-if reads each as false, so ``not in`` takes the
        ``else`` branch too) and a model has no ``len()`` (``pluralize`` answers empty)."""
        ctx = {"rows": [world["a1"], world["a2"]]}
        for src in (
            '{% for r in rows %}[{{ r|stringformat:"(username)s" }}]{% endfor %}',
            "{% for r in rows %}[{% if 'username' in r %}IN{% else %}OUT{% endif %}]{% endfor %}",
            "{% for r in rows %}[{% if 'zzz' in r %}IN{% else %}OUT{% endif %}]{% endfor %}",
            "{% for r in rows %}[{% if 'username' not in r %}NOTIN{% else %}ELSE{% endif %}]{% endfor %}",
            "{% for r in rows %}[{% if 'zzz' not in r %}NOTIN{% else %}ELSE{% endif %}]{% endfor %}",
            "{% for r in rows %}[{{ r|pluralize }}]{% endfor %}",
            "{% for r in rows %}[{{ r|pluralize:'y,ies' }}]{% endfor %}",
        ):
            got = ENTRIES[entry](src + "|||{{ rows|pprint }}", ctx).split("|||")[0]
            assert got == _django(src, ctx), (entry, src, got)


@pytest.fixture
def safe_world(db, scratch_models):  # noqa: F811
    """Two rows of a model whose ``__str__`` is markup-safe text (``format_html``)."""
    from django.utils.html import format_html

    class SafeAccount(User):
        class Meta:
            proxy = True
            app_label = "auth"

        @property
        def owner_code(self):
            return SENT_A

        def get_note_text(self):
            return SENT_B

        def __str__(self):
            return format_html("<b>{}</b>", self.username)

    s1 = SafeAccount.objects.create(username="s1", first_name=SENT_C)
    s2 = SafeAccount.objects.create(username="s2", first_name=SENT_C)
    return {"Account": SafeAccount, "a1": s1, "a2": s2}


class TestAModelWithAMarkupSafeTextIsAModelToo:
    """``str(model)`` may be a ``SafeString`` (``format_html`` / ``mark_safe``). The marker carries that text
    as a ``SafeString`` value, and every sink must recognise it exactly as it recognises a plain text."""

    #: every sink the plain-text model is pinned on, over a list, a tuple, a queryset, a dict and a
    #: top-level model
    SINKS = {
        "bare": "{{ rows }}",
        "pprint": "{{ rows|pprint }}",
        "json_script": '{{ rows|json_script:"x" }}',
        "loop-pprint": "{% for r in rows %}{{ r|pprint }};{% endfor %}",
        "loop-items": "{% for r in rows %}[{{ r.items }}]{% endfor %}",
        "loop-for-items": "{% for r in rows %}{% for k, v in r.items %}{{ k }}={{ v }};{% endfor %}{% endfor %}",
        "loop-values-join": "{% for r in rows %}{{ r.values|join:',' }}{% endfor %}",
        "loop-keys": "{% for r in rows %}[{{ r.keys }}]{% endfor %}",
        "loop-iterate": "{% for r in rows %}{% for k in r %}{{ k }},{% endfor %}{% endfor %}",
        "loop-join": "{% for r in rows %}{{ r|join:',' }}{% endfor %}",
        "loop-unordered_list": "{% for r in rows %}{{ r|unordered_list }}{% endfor %}",
        "loop-safeseq": "{% for r in rows %}{{ r|safeseq|join:',' }}{% endfor %}",
        "loop-escapeseq": "{% for r in rows %}{{ r|escapeseq|join:',' }}{% endfor %}",
        "loop-stringformat": '{% for r in rows %}[{{ r|stringformat:"(username)s" }}]{% endfor %}',
        "loop-in": "{% for r in rows %}[{% if 'username' in r %}IN{% else %}OUT{% endif %}]{% endfor %}",
        "loop-pluralize": "{% for r in rows %}[{{ r|pluralize }}]{% endfor %}",
    }

    @staticmethod
    def _shapes(w):
        A = w["Account"]
        return {
            "list": lambda: [w["a1"], w["a2"]],
            "tuple": lambda: (w["a1"], w["a2"]),
            "queryset": lambda: A.objects.order_by("username"),
            "dict-of-list": lambda: {"items": [w["a1"], w["a2"]]},
            "dict-of-model": lambda: {"owner": w["a1"]},
            "model": lambda: w["a1"],
        }

    @pytest.mark.parametrize("entry", sorted(ENTRIES))
    @pytest.mark.parametrize("sink", sorted(SINKS))
    @pytest.mark.parametrize(
        "shape", ["list", "tuple", "queryset", "dict-of-list", "dict-of-model", "model"]
    )
    def test_a_model_prints_as_its_text(self, safe_world, entry, sink, shape) -> None:
        src = self.SINKS[sink]
        if shape.startswith("dict") and sink.startswith("loop"):
            pytest.skip("a row loop needs a sequence")
        if shape == "model" and sink.startswith("loop"):
            src = (
                "{% with r=rows %}"
                + src.split("{% for r in rows %}")[1].rsplit("{% endfor %}", 1)[0]
                + "{% endwith %}"
            )
        out = _observe(
            ENTRIES[entry],
            src + "|||{{ rows|pprint }}",
            {"rows": self._shapes(safe_world)[shape]()},
        )
        head = out.split("|||")[0]
        printed = [m for m in SENTINELS if m in head]
        names = [n for n in ("owner_code", "get_note_text", "first_name", "__model__") if n in head]
        assert not printed and not names, f"{entry}/{shape}/{sink}: {head[:300]!r}"

    @pytest.mark.parametrize("entry", sorted(ENTRIES))
    @pytest.mark.parametrize(
        "sink",
        [s for s in sorted(SINKS) if s not in ("bare", "pprint", "json_script", "loop-pprint")],
    )
    def test_the_non_text_sinks_agree_with_django(self, safe_world, entry, sink) -> None:
        """The sinks that do not print the model's text itself answer like Django on real instances."""
        ctx = {"rows": [safe_world["a1"], safe_world["a2"]]}
        want = _observe(_django, self.SINKS[sink], ctx)
        got = _observe(ENTRIES[entry], self.SINKS[sink] + "|||{{ rows|pprint }}", ctx).split("|||")[
            0
        ]
        got = re.sub(r"dict_(items|keys|values)\(\[\]\)", "", got)
        assert got == want, f"{entry}/{sink}: djust {got!r}, Django {want!r}"

    @pytest.mark.parametrize("entry", sorted(ENTRIES))
    def test_the_text_is_spelled_and_stays_escaped(self, safe_world, entry) -> None:
        """The text sinks print the text. A ``SafeString`` text is printed as TEXT here (escaped by
        the page like any other string), where Django emits the author's markup raw: the safe
        direction, and the only difference from Django on these cells."""
        ctx = {"rows": [safe_world["a1"], safe_world["a2"]]}
        run = ENTRIES[entry]
        assert run("{{ rows|pprint }}", ctx) == "[<b>s1</b>, <b>s2</b>]"
        assert run('{{ rows|json_script:"x" }}', ctx) == (
            '<script id="x" type="application/json">["\\u003Cb\\u003Es1\\u003C/b\\u003E", "\\u003Cb\\u003Es2\\u003C/b\\u003E"]</script>'
        )
        assert run("{{ rows }}", ctx) == "[<b>s1</b>, <b>s2</b>]"

    def test_the_text_is_not_marked_safe_by_being_printed(self, safe_world) -> None:
        """Raw bytes (not through the tests' unescape): the markup in the text is escaped on the page."""

        def mount(self, request, **kwargs):
            self.rows = [safe_world["a1"]]

        view = type(
            "RawView",
            (LiveView,),
            {
                "template": "<div dj-root>{{ rows }}|{{ rows|pprint }}|{{ rows.0 }}</div>",
                "mount": mount,
            },
        )
        client = LiveViewTestClient(view)
        client.mount()
        html = DJ_ROOT.search(client.render()).group(1)
        assert "<b>" not in html and html.count("&lt;b&gt;s1&lt;/b&gt;") == 3, html

    def test_a_markup_text_stays_inert(self, world) -> None:
        world["a1"].username = "</b><script>alert(1)</script>"

        def mount(self, request, **kwargs):
            self.rows = [world["a1"]]

        view = type(
            "RawView2",
            (LiveView,),
            {"template": "<div dj-root>{{ rows }}|{{ rows|pprint }}</div>", "mount": mount},
        )
        client = LiveViewTestClient(view)
        client.mount()
        html = DJ_ROOT.search(client.render()).group(1)
        assert "<script>" not in html and "&lt;script&gt;" in html, html
