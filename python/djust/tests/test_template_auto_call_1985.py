"""#1985 / ADR-024 — Django-parity template callable auto-call.

Doc-claim-verbatim tests (#1046): one asserting test per row of the ADR-024
semantics table, the two reported symptoms as regressions through the real
render path, the kill-switch gate-off (#1468), and the eager-site guard sweep
(Decision 2; #1104 — N sites, N tests).

The sidecar tests route values through ``get_context_data`` from a private
attr: eager serialization stringifies/skips the custom object, so the dotted
lookup misses the eager value-stack and exercises the REAL
``Context::resolve`` sidecar getattr walk (`crates/djust_core/src/context.rs`)
— the exact path the divergence lived on (reproduction fidelity).
"""

import logging

import pytest

from djust import LiveView
from djust.testing import LiveViewTestClient


class _Probe:
    """Callable-bearing helper covering every semantics-table row."""

    def __init__(self):
        self.alters_data_called = False
        self.do_not_call_called = False

    def get_greeting(self):
        return "hello-from-method"

    def get_settings(self):
        class _S:
            theme = "dark"

        return _S()

    def needs_args(self, required):  # args-required → renders empty
        return required

    def raises_internal_typeerror(self):
        raise TypeError("internal bug — must propagate")

    def raises_valueerror(self):
        raise ValueError("must propagate")

    @property
    def destructive(self):
        def _destroy():
            self.alters_data_called = True
            return "DESTROYED"

        _destroy.alters_data = True
        return _destroy

    @property
    def guarded(self):
        def _guarded():
            self.do_not_call_called = True
            return "CALLED"

        _guarded.do_not_call_in_templates = True
        return _guarded


def _make_view(template):
    class _V(LiveView):
        def mount(self, request, **kwargs):
            self._probe = _Probe()

        def get_context_data(self, **kwargs):
            ctx = super().get_context_data(**kwargs)
            ctx["probe"] = self._probe
            return ctx

    _V.template = template
    return _V


def _render(template):
    client = LiveViewTestClient(_make_view(template))
    client.mount()
    html, _, _ = client.render_with_patches()
    return html, client


@pytest.mark.django_db
class TestAutoCallSemantics:
    """One test per ADR-024 semantics-table row (doc-claim-verbatim, #1046)."""

    def test_no_arg_method_is_auto_called(self):
        html, _ = _render("<div>{{ probe.get_greeting }}</div>")
        assert "hello-from-method" in html
        assert "bound method" not in html

    def test_mid_path_call_continues_the_walk(self):
        """Django auto-calls at EVERY segment: {{ probe.get_settings.theme }}
        calls get_settings() mid-walk, then reads .theme on the result."""
        html, _ = _render("<div>{{ probe.get_settings.theme }}</div>")
        assert "dark" in html

    def test_args_required_callable_renders_empty(self):
        """TypeError from a callable that genuinely requires arguments is
        Django's string_if_invalid → empty, never a crash."""
        html, _ = _render("<div>[{{ probe.needs_args }}]</div>")
        assert "[]" in html

    def test_do_not_call_in_templates_is_not_called(self):
        html, client = _render("<div>{{ probe.guarded }}</div>")
        assert client.view_instance._probe.do_not_call_called is False, (
            "do_not_call_in_templates callables must be used as-is, never invoked"
        )
        assert "CALLED" not in html

    def test_alters_data_is_refused_and_renders_empty(self):
        """The data-destruction guard: alters_data callables are NEVER
        executed (side-effect sentinel, not just output) and the expression
        renders empty."""
        html, client = _render("<div>[{{ probe.destructive }}]</div>")
        assert client.view_instance._probe.alters_data_called is False, (
            "alters_data callable was EXECUTED — the ADR-024 guard is broken "
            "(a template typo like {{ user.delete }} would destroy data)"
        )
        assert "DESTROYED" not in html
        assert "[]" in html

    def test_internal_typeerror_propagates(self):
        """A TypeError raised INSIDE a zero-arg method is a real bug and must
        propagate (Django's signature-bind distinction), not render empty."""
        with pytest.raises(Exception, match="internal bug"):
            _render("<div>{{ probe.raises_internal_typeerror }}</div>")

    def test_other_exceptions_propagate(self):
        with pytest.raises(Exception, match="must propagate"):
            _render("<div>{{ probe.raises_valueerror }}</div>")


@pytest.mark.django_db
class TestKillSwitch:
    """LIVEVIEW_CONFIG['template_auto_call'] — gate-off (#1468) + wiring."""

    def test_kill_switch_restores_plain_getattr_walk(self):
        """With auto-call disabled on the Rust view, the pre-ADR behavior
        returns: the bound method stringifies instead of being called. This
        is the behavioral gate-off — it fails if the kill-switch stops
        gating the new code path."""
        client = LiveViewTestClient(_make_view("<div>{{ probe.get_greeting }}</div>"))
        client.mount()
        client.render_with_patches()  # _rust_view initializes lazily on first render
        client.view_instance._rust_view.set_template_auto_call(False)
        html, _, _ = client.render_with_patches()
        assert "hello-from-method" not in html
        assert "bound method" in html  # the pre-ADR stringified method object

    def test_flag_wiring_reads_config(self, monkeypatch):
        """_apply_template_auto_call_flag forwards the config value to Rust
        (mirrors the #1967 loop-cache flag plumbing)."""
        client = LiveViewTestClient(_make_view("<div>x</div>"))
        client.mount()
        client.render_with_patches()  # _rust_view initializes lazily on first render
        view = client.view_instance
        assert view._rust_view.template_auto_call_enabled() is True  # default ON

        from djust import config as djust_config

        class _FakeConfig:
            def get(self, key, default=None):
                return False if key == "template_auto_call" else default

        monkeypatch.setattr(djust_config, "get_config", lambda: _FakeConfig())
        view._apply_template_auto_call_flag()
        assert view._rust_view.template_auto_call_enabled() is False


@pytest.mark.django_db
class TestReportedSymptoms:
    """The two #1985 symptoms as regressions through the real render path."""

    def test_request_scoped_user_get_full_name(self):
        """Symptom 1: `{{ user.get_full_name }}` rendered
        `<bound method AbstractUser.get_full_name of ...>`. Request-scoped
        `user` (auth context processor) is deliberately excluded from eager
        state, so it resolves ONLY via the sidecar walk."""
        from django.contrib.auth.models import User

        user = User.objects.create_user(username="jordan", first_name="Jordan", last_name="Reyes")
        client = LiveViewTestClient(_make_view("<div>{{ user.get_full_name }}</div>"))
        client.user = user
        client.mount()
        html, _, _ = client.render_with_patches()
        assert "Jordan Reyes" in html
        assert "bound method" not in html

    def test_manager_method_count_via_sidecar(self):
        """Symptom 2: `{{ workspace.memberships.count }}` rendered empty.
        Reverse/M2M managers are never eagerly serialized, so `.count` is
        reachable only via the sidecar walk — here with the real
        `user.groups` ManyRelatedManager."""
        from django.contrib.auth.models import Group, User

        user = User.objects.create_user(username="counter")
        user.groups.add(Group.objects.create(name="g1"), Group.objects.create(name="g2"))

        class _V(LiveView):
            template = "<div>[{{ member.groups.count }}]</div>"

            def mount(self, request, **kwargs):
                self._member = user

            def get_context_data(self, **kwargs):
                ctx = super().get_context_data(**kwargs)
                ctx["member"] = self._member
                return ctx

        client = LiveViewTestClient(_V)
        client.mount()
        html, _, _ = client.render_with_patches()
        assert "[2]" in html

    def test_orm_autocall_warning_is_emitted_once(self, caplog):
        """Observability rider: an auto-call bound to a Manager/QuerySet emits
        a one-shot warning on the djust.templates logger (debug mode — the
        Rust side reads settings.DEBUG live, so override_settings works).
        Uses a DISTINCT dotted path — the one-shot set is per-path
        per-process."""
        from django.contrib.auth.models import User
        from django.test import override_settings

        user = User.objects.create_user(username="warn-probe")

        class _V(LiveView):
            template = "<div>{{ warnee.groups.count }}</div>"

            def mount(self, request, **kwargs):
                self._warnee = user

            def get_context_data(self, **kwargs):
                ctx = super().get_context_data(**kwargs)
                ctx["warnee"] = self._warnee
                return ctx

        with (
            override_settings(DEBUG=True),
            caplog.at_level(logging.WARNING, logger="djust.templates"),
        ):
            client = LiveViewTestClient(_V)
            client.mount()
            client.render_with_patches()
        matching = [r for r in caplog.records if "auto-calls an ORM method" in r.getMessage()]
        assert matching, "expected the ADR-024 ORM auto-call warning on first render"


@pytest.mark.django_db
class TestSidecarSerializationFloor:
    """#1986 review — the sidecar getattr walk must honor the serialization
    floor (SECURE_DEFAULTS Pattern 1), or auto-call/raw-model access leaks
    denylisted fields (password, is_superuser) and sensitive methods
    (get_session_auth_hash) to the client. Both explicitly-assigned models
    and request-scoped `user` are covered by _SidecarModelProxy."""

    def _user(self):
        from django.contrib.auth.models import Group, User

        u = User.objects.create_user(
            username="jordan", first_name="Jordan", last_name="Reyes", password="s3cret-pw"
        )
        u.groups.add(Group.objects.create(name="a"), Group.objects.create(name="b"))
        return u

    def test_explicit_model_floor_fields_do_not_leak(self):
        u = self._user()

        class _V(LiveView):
            template = (
                "<div>pw=[{{ m.password }}] su=[{{ m.is_superuser }}] "
                "st=[{{ m.is_staff }}] sess=[{{ m.get_session_auth_hash }}]</div>"
            )

            def mount(self, request, **kwargs):
                self._m = u

            def get_context_data(self, **kwargs):
                ctx = super().get_context_data(**kwargs)
                ctx["m"] = self._m
                return ctx

        client = LiveViewTestClient(_V)
        client.mount()
        html, _, _ = client.render_with_patches()
        assert "pbkdf2" not in html, f"password hash leaked: {html}"
        assert "pw=[]" in html and "su=[]" in html and "st=[]" in html
        assert "sess=[]" in html, f"session-auth hash leaked: {html}"

    def test_request_scoped_user_floor_fields_do_not_leak(self):
        """The pre-existing request-scoped leak (`{{ user.password }}`) is
        closed by the same proxy — request-scoped `user` is wrapped too."""
        u = self._user()

        class _V(LiveView):
            template = "<div>pw=[{{ user.password }}] su=[{{ user.is_superuser }}]</div>"

            def mount(self, request, **kwargs):
                pass

            def get_context_data(self, **kwargs):
                return super().get_context_data(**kwargs)

        client = LiveViewTestClient(_V)
        client.user = u
        client.mount()
        html, _, _ = client.render_with_patches()
        assert "pbkdf2" not in html, f"request-scoped password leaked: {html}"
        assert "pw=[]" in html and "su=[]" in html

    def test_floor_holds_with_kill_switch_off(self):
        """The floor is NOT gated on template_auto_call — flipping the
        kill-switch off must not re-open the field leak (the #1986 review
        confirmed the retention was ungated)."""
        u = self._user()

        class _V(LiveView):
            template = "<div>pw=[{{ m.password }}]</div>"

            def mount(self, request, **kwargs):
                self._m = u

            def get_context_data(self, **kwargs):
                ctx = super().get_context_data(**kwargs)
                ctx["m"] = self._m
                return ctx

        client = LiveViewTestClient(_V)
        client.mount()
        client.render_with_patches()  # lazy _rust_view init
        client.view_instance._rust_view.set_template_auto_call(False)
        html, _, _ = client.render_with_patches()
        assert "pbkdf2" not in html, f"kill-switch-off leaked password: {html}"

    def test_legit_methods_and_managers_still_work(self):
        """The proxy must NOT over-block: safe fields, get_* methods, and
        managers still resolve (the ADR-024 feature)."""
        u = self._user()

        class _V(LiveView):
            template = (
                "<div>n=[{{ m.get_full_name }}] g=[{{ m.groups.count }}] u=[{{ m.username }}]</div>"
            )

            def mount(self, request, **kwargs):
                self._m = u

            def get_context_data(self, **kwargs):
                ctx = super().get_context_data(**kwargs)
                ctx["m"] = self._m
                return ctx

        client = LiveViewTestClient(_V)
        client.mount()
        html, _, _ = client.render_with_patches()
        assert "Jordan Reyes" in html
        assert "g=[2]" in html
        assert "jordan" in html

    def test_custom_sensitive_field_refused(self, settings):
        """A DJUST_SENSITIVE_FIELDS-configured field is refused by the same
        authority (not just the built-in floor)."""
        settings.DJUST_SENSITIVE_FIELDS = ["last_name"]
        u = self._user()

        class _V(LiveView):
            template = "<div>ln=[{{ m.last_name }}] fn=[{{ m.first_name }}]</div>"

            def mount(self, request, **kwargs):
                self._m = u

            def get_context_data(self, **kwargs):
                ctx = super().get_context_data(**kwargs)
                ctx["m"] = self._m
                return ctx

        client = LiveViewTestClient(_V)
        client.mount()
        html, _, _ = client.render_with_patches()
        assert "ln=[]" in html, f"configured-sensitive last_name leaked: {html}"
        assert "Jordan" in html  # first_name (not sensitive) still renders

    def test_manager_traversal_does_not_leak(self):
        """#1986 re-review 🔴: a model returned by an auto-called manager/
        queryset method (`.first`/`.get`) must itself be floor-wrapped, or the
        next segment reads a raw model and leaks. Transitive protection."""
        u = self._user()

        class _V(LiveView):
            template = (
                "<div>f=[{{ m.groups.first.user_set.first.password }}] "
                "g=[{{ m.groups.first.user_set.get.password }}]</div>"
            )

            def mount(self, request, **kwargs):
                self._m = u

            def get_context_data(self, **kwargs):
                ctx = super().get_context_data(**kwargs)
                ctx["m"] = self._m
                return ctx

        client = LiveViewTestClient(_V)
        client.mount()
        html, _, _ = client.render_with_patches()
        assert "pbkdf2" not in html, f"manager-traversal leaked password: {html}"
        assert "f=[]" in html and "g=[]" in html

    def test_for_loop_iteration_floor_and_fields(self):
        """#1986 re-review 🔴: queryset items in a `{% for %}` went through the
        Rust `__dict__` bulk-dump (which filtered only `_`-names), leaking
        `password`. Items must be denylist-filtered dicts: floor field empty,
        safe field works."""
        u = self._user()

        class _V(LiveView):
            template = (
                "<ul>{% for x in m.groups.first.user_set.all %}"
                "<li>pw=[{{ x.password }}] u=[{{ x.username }}]</li>"
                "{% endfor %}</ul>"
            )

            def mount(self, request, **kwargs):
                self._m = u

            def get_context_data(self, **kwargs):
                ctx = super().get_context_data(**kwargs)
                ctx["m"] = self._m
                return ctx

        client = LiveViewTestClient(_V)
        client.mount()
        html, _, _ = client.render_with_patches()
        assert "pbkdf2" not in html, f"for-loop iteration leaked password: {html}"
        assert "pw=[]" in html
        assert "u=[jordan]" in html, f"for-loop lost the safe field: {html}"

    def test_values_projection_refused_no_leak(self):
        """#1986 re-review vector 5: `.values()` / `.values_list()` yield raw
        dict/tuple rows with no model identity — `.first`/`.get`/index/iteration
        would each hand back an unfiltered row. Projections are refused
        wholesale in the sidecar (empty), so no floor field leaks and no safe
        field renders either (precompute in get_context_data instead)."""
        u = self._user()

        class _V(LiveView):
            template = (
                "<div>"
                "{% for x in m.groups.first.user_set.values %}<span>vpw=[{{ x.password }}]</span>{% endfor %}"
                "{% for x in m.groups.first.user_set.values_list %}<span>lpw=[{{ x.1 }}]</span>{% endfor %}"
                "<b>first=[{{ m.groups.first.user_set.values.first.password }}]</b>"
                "</div>"
            )

            def mount(self, request, **kwargs):
                self._m = u

            def get_context_data(self, **kwargs):
                ctx = super().get_context_data(**kwargs)
                ctx["m"] = self._m
                return ctx

        client = LiveViewTestClient(_V)
        client.mount()
        html, _, _ = client.render_with_patches()
        assert "pbkdf2" not in html, f".values()/.values_list() leaked password: {html}"
        assert "first=[]" in html  # .values.first.password refused
        # projection refused → no <li> rows emitted at all
        assert "vpw=" not in html and "lpw=" not in html

    def test_underscore_prefixed_refused(self):
        """#1986 re-review 🟡: `_`-prefixed names must be refused (Django
        parity). `{{ obj._meta }}` would otherwise segfault the worker
        (Options extraction) and `{{ obj._meta.db_table }}` disclose schema."""
        u = self._user()

        class _V(LiveView):
            template = (
                "<div>meta=[{{ m._meta }}] tbl=[{{ m._meta.db_table }}] st=[{{ m._state }}]</div>"
            )

            def mount(self, request, **kwargs):
                self._m = u

            def get_context_data(self, **kwargs):
                ctx = super().get_context_data(**kwargs)
                ctx["m"] = self._m
                return ctx

        client = LiveViewTestClient(_V)
        client.mount()
        html, _, _ = client.render_with_patches()  # must not crash the worker
        assert "meta=[]" in html and "tbl=[]" in html and "st=[]" in html
        assert "auth_user" not in html, f"schema disclosed via _meta: {html}"

    def test_proxy_unit_floor_and_delegation(self):
        """Gate-off / unit pin (#1468): the proxy IS load-bearing — it raises
        AttributeError for floor fields + sensitive methods and delegates
        everything else. This fails if the proxy stops enforcing the floor."""
        from djust.serialization import _SidecarModelProxy

        u = self._user()
        proxy = _SidecarModelProxy(u)

        for refused in ("password", "is_superuser", "is_staff", "get_session_auth_hash"):
            with pytest.raises(AttributeError):
                getattr(proxy, refused)

        assert proxy.username == "jordan"  # safe field delegates
        assert callable(proxy.get_full_name)  # method delegates (callable)
        assert proxy.get_full_name() == "Jordan Reyes"


@pytest.mark.django_db
class TestEagerSiteGuards:
    """ADR-024 Decision 2 — the pre-existing eager auto-call sites share the
    same alters_data / do_not_call_in_templates guards (#1104: N sites, N
    tests)."""

    def test_serializer_skips_alters_data_get_method(self, monkeypatch):
        """_add_safe_model_methods must not call a get_* method stamped
        alters_data=True (side-effect sentinel)."""
        from django.contrib.auth.models import User

        from djust.serialization import DjangoJSONEncoder

        executed = []

        def get_marker(self):
            executed.append(True)
            return "MARKER"

        get_marker.alters_data = True
        monkeypatch.setattr(User, "get_marker", get_marker, raising=False)

        user = User.objects.create_user(username="serializer-guard")
        result = DjangoJSONEncoder()._serialize_model_safely(user)

        assert not executed, "serializer executed an alters_data get_* method"
        assert "get_marker" not in str(result)

    def test_serializer_skips_do_not_call_get_method(self, monkeypatch):
        from django.contrib.auth.models import User

        from djust.serialization import DjangoJSONEncoder

        executed = []

        def get_marker2(self):
            executed.append(True)
            return "MARKER2"

        get_marker2.do_not_call_in_templates = True
        monkeypatch.setattr(User, "get_marker2", get_marker2, raising=False)

        user = User.objects.create_user(username="serializer-guard2")
        DjangoJSONEncoder()._serialize_model_safely(user)

        assert not executed, "serializer executed a do_not_call_in_templates method"

    def test_codegen_emits_guards_at_both_method_call_sites(self):
        """The JIT codegen's generated source guards BOTH method-call sites
        (root get_* leaf and nested get_*/all/count/exists leaf) with the
        shared semantics."""
        from djust.optimization.codegen import generate_serializer_code

        code_root = generate_serializer_code("Post", ["get_status"], "ser_root_guard")
        code_nested = generate_serializer_code("Post", ["author.get_bio"], "ser_nested_guard")

        for code in (code_root, code_nested):
            assert "alters_data" in code, "generated serializer lost the alters_data guard"
            assert "do_not_call_in_templates" in code

    def test_codegen_generated_code_skips_alters_data_at_runtime(self):
        """Compile the generated serializer and prove the guard executes:
        an alters_data method on the object is NOT called."""
        from djust.optimization.codegen import compile_serializer, generate_serializer_code

        executed = []

        class _Obj:
            pk = 1
            id = 1

            def get_marker(self):
                executed.append(True)
                return "MARKER"

        _Obj.get_marker.alters_data = True

        code = generate_serializer_code("Obj", ["get_marker"], "ser_guard_rt")
        func = compile_serializer(code, "ser_guard_rt")
        result = func(_Obj())

        assert not executed, "generated serializer executed an alters_data method"
        assert "get_marker" not in result
