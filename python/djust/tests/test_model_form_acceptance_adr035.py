"""ADR-035 F2: form acceptance for the managed edit object.

F1 (``test_model_form_lifecycle_adr035.py``) proves the order resolve ->
authorize -> bind on every transport. This module exercises the forms built
on it:

- no-custom-``mount()`` edit, create and non-model forms;
- hooks overridden on the view, and inherited from a parent;
- choices, relations (FK, M2M) and uploads;
- empty submission, validation and exactly one save;
- tampered, missing, deleted and revoked targets, and failing callbacks;
- reconnect with unsaved input;
- Django and Rust rendering, and typing.

Every write is counted in SQL, so a second save or a save for a denied
record fails a test rather than passing silently.
"""

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from asgiref.sync import sync_to_async
from django import forms
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, override_settings
from django.urls import path

from djust import LiveView
from djust.forms import FormMixin, ModelFormMixin
from djust.uploads import UploadMixin

EVENTS: list = []
REVOKED: set = set()
FAIL: dict = {}
DENIED = "Access denied for this object."


def _note(*event):
    EVENTS.append(event)


# --------------------------------------------------------------------------
# Forms and views. None of the edit views defines mount().
# --------------------------------------------------------------------------


class GroupForm(forms.ModelForm):
    class Meta:
        model = Group
        fields = ["name", "permissions"]


class ScopedEdit(ModelFormMixin[Group], LiveView):
    """Edit a group the user belongs to; REVOKED and FAIL are test levers."""

    template = (
        "<div dj-root><h1>{{ object.name }}</h1>"
        '<input name="name" value="{{ form_data.name }}">'
        "<p>{{ field_errors.name.0 }}</p><b>{{ success_message }}</b></div>"
    )
    model = Group
    form_class = GroupForm
    login_required = True
    enable_state_snapshot = True

    def get_queryset(self):
        if FAIL.get("queryset"):
            raise RuntimeError("queryset callback failed")
        return super().get_queryset().filter(user=self.request.user)

    def has_object_permission(self, request, obj):
        if FAIL.get("permission"):
            raise RuntimeError("permission callback failed")
        return obj.pk not in REVOKED

    def form_valid(self, form):
        _note("form_valid", form.instance.pk)
        self.object = form.save()
        self.success_message = "Saved " + self.object.name

    def form_invalid(self, form):
        _note("form_invalid", sorted(form.errors))


class NoSaveEdit(ScopedEdit):
    """``form_valid`` decides not to save (D5): nothing is written."""

    def form_valid(self, form):
        _note("form_valid", form.instance.pk)
        self.success_message = "Checked"


class HookedEdit(ScopedEdit):
    """Each public hook overridden on the adapter view."""

    template = '<div dj-root><input name="g-name" value="{{ form_data.name }}"></div>'

    def get_initial(self):
        return {"name": "Initial override"}

    def get_prefix(self):
        return "g"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["label_suffix"] = " >"
        return kwargs


class InheritedHookedEdit(HookedEdit):
    """The same hooks, inherited rather than declared."""


class PermissionForm(forms.ModelForm):
    class Meta:
        model = Permission
        fields = ["name", "content_type", "codename"]


class PermissionEdit(ModelFormMixin[Permission], LiveView):
    """An FK relation; editable only for the test content type."""

    template = "<div dj-root>{{ object.codename }}</div>"
    form_class = PermissionForm

    def get_queryset(self):
        return Permission.objects.filter(codename__startswith="adr035_")

    def form_valid(self, form):
        self.object = form.save()


class UploadForm(forms.ModelForm):
    attachment = forms.FileField()

    class Meta:
        model = Group
        fields = ["name"]


class UploadEdit(UploadMixin, ModelFormMixin[Group], LiveView):
    """Uploads reach the form through a get_form_kwargs override (D1)."""

    template = "<div dj-root>{{ object.name }}</div>"
    model = Group
    form_class = UploadForm

    def mount(self, request, **kwargs):
        super().mount(request, **kwargs)
        self.allow_upload("attachment", max_entries=1, max_file_size=1000)

    def get_queryset(self):
        return super().get_queryset().filter(user=self.request.user)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        if "data" in kwargs:
            files = {
                entry.upload_name: SimpleUploadedFile(
                    entry.safe_client_name, entry.data, entry.client_type
                )
                for entry in self.get_uploads("attachment")
                if entry.complete
            }
            kwargs["files"] = files
        return kwargs

    def form_valid(self, form):
        _note("upload", form.cleaned_data["attachment"].read())
        self.object = form.save()


class GroupNameForm(forms.ModelForm):
    class Meta:
        model = Group
        fields = ["name"]


class CreateGroup(FormMixin, LiveView):
    """Create forms keep using FormMixin: a ModelForm with no instance."""

    template = '<div dj-root><input name="name" value="{{ form_data.name }}"></div>'
    form_class = GroupNameForm

    def form_valid(self, form):
        _note("created", form.save().pk)


class ContactForm(forms.Form):
    email = forms.EmailField()


class Contact(FormMixin, LiveView):
    """A form with no model at all acquires no lookup requirement (D5)."""

    template = '<div dj-root><input name="email" value="{{ form_data.email }}"></div>'
    form_class = ContactForm

    def form_valid(self, form):
        _note("contact", form.cleaned_data["email"])


urlpatterns = [
    path("edit/<int:pk>/", ScopedEdit.as_view()),
    path("nosave/<int:pk>/", NoSaveEdit.as_view()),
    path("hooked/<int:pk>/", HookedEdit.as_view()),
    path("inherited/<int:pk>/", InheritedHookedEdit.as_view()),
    path("permission/<int:pk>/", PermissionEdit.as_view()),
    path("upload/<int:pk>/", UploadEdit.as_view()),
    path("create/", CreateGroup.as_view()),
    path("contact/", Contact.as_view()),
]


@pytest.fixture(autouse=True)
def _levers():
    EVENTS.clear()
    REVOKED.clear()
    FAIL.clear()
    with override_settings(ROOT_URLCONF=__name__, DJUST_TENANTS=None):
        yield
    EVENTS.clear()
    REVOKED.clear()
    FAIL.clear()


@pytest.fixture
def sql(monkeypatch):
    """Every SQL statement, from whichever thread and connection runs it."""
    from django.db.backends import utils

    statements: list = []
    original = utils.CursorWrapper._execute

    def record(self, query, params, *args):
        statements.append(query)
        return original(self, query, params, *args)

    monkeypatch.setattr(utils.CursorWrapper, "_execute", record)
    return statements


def _writes(statements, table="auth_group"):
    return [s for s in statements if s.startswith(("UPDATE", "INSERT", "DELETE")) and table in s]


def _world():
    from django.contrib.auth import get_user_model

    tag = uuid.uuid4().hex[:8]
    user = get_user_model().objects.create_user(username="f2-" + tag)
    mine = Group.objects.create(name="Mine " + tag)
    theirs = Group.objects.create(name="Theirs " + tag)
    user.groups.add(mine)
    return user, mine, theirs


def _session():
    from django.contrib.sessions.backends.db import SessionStore

    session = SessionStore()
    session.create()
    return session


def _get(view_class, user, url, session, **route):
    request = RequestFactory().get(url)
    request.user, request.session, request.tenant = user, session, None
    return view_class.as_view()(request, **route)


def _post(view_class, user, url, session, event, params, **route):
    request = RequestFactory().post(
        url,
        data=json.dumps({"event": event, "params": params}),
        content_type="application/json",
    )
    request.user, request.session, request.tenant = user, session, None
    return view_class.as_view()(request, **route)


def _edit(view_class, user, record, session, event="submit_form", **params):
    url = {
        ScopedEdit: "/edit/%s/",
        NoSaveEdit: "/nosave/%s/",
        HookedEdit: "/hooked/%s/",
        InheritedHookedEdit: "/inherited/%s/",
    }[view_class] % record
    return _post(view_class, user, url, session, event, params, pk=record)


def _open(view_class, user, pk, session):
    url = {
        ScopedEdit: "/edit/%s/",
        NoSaveEdit: "/nosave/%s/",
        HookedEdit: "/hooked/%s/",
        InheritedHookedEdit: "/inherited/%s/",
    }[view_class] % pk
    return _get(view_class, user, url, session, pk=pk)


def _names(*groups):
    return {g.pk: Group.objects.get(pk=g.pk).name for g in groups}


# --------------------------------------------------------------------------
# Edit, create and non-model examples; exactly one save.
# --------------------------------------------------------------------------


@pytest.mark.django_db
def test_edit_without_mount_saves_exactly_once(sql):
    user, mine, theirs = _world()
    session = _session()
    page = _open(ScopedEdit, user, mine.pk, session)
    assert page.status_code == 200 and mine.name in page.content.decode()
    sql.clear()
    response = _edit(ScopedEdit, user, mine.pk, session, name="Renamed")
    assert response.status_code == 200, response.content
    assert "Saved Renamed" in response.content.decode()
    assert len(_writes(sql)) == 1, _writes(sql)
    assert _names(mine, theirs) == {mine.pk: "Renamed", theirs.pk: theirs.name}


@pytest.mark.django_db
def test_form_valid_that_does_not_save_writes_nothing(sql):
    user, mine, _ = _world()
    session = _session()
    _open(NoSaveEdit, user, mine.pk, session)
    sql.clear()
    response = _edit(NoSaveEdit, user, mine.pk, session, name="Unsaved")
    assert response.status_code == 200 and "Checked" in response.content.decode()
    assert _writes(sql) == []
    assert Group.objects.get(pk=mine.pk).name == mine.name


@pytest.mark.django_db
def test_create_form_creates_exactly_one_record(sql):
    user, _, _ = _world()
    session = _session()
    name = "Created " + uuid.uuid4().hex[:6]
    assert _get(CreateGroup, user, "/create/", session).status_code == 200
    sql.clear()
    response = _post(CreateGroup, user, "/create/", session, "submit_form", {"name": name})
    assert response.status_code == 200, response.content
    assert Group.objects.filter(name=name).count() == 1
    assert len([s for s in _writes(sql) if s.startswith("INSERT")]) == 1


@pytest.mark.django_db
def test_non_model_form_has_no_lookup_requirement():
    user, _, _ = _world()
    session = _session()
    assert _get(Contact, user, "/contact/", session).status_code == 200
    response = _post(Contact, user, "/contact/", session, "submit_form", {"email": "a@b.co"})
    assert response.status_code == 200
    assert EVENTS == [("contact", "a@b.co")]


@pytest.mark.django_db
def test_denied_edit_never_creates_a_record(sql):
    user, mine, theirs = _world()
    session = _session()
    before = Group.objects.count()
    sql.clear()
    for pk in (theirs.pk, theirs.pk + 1000):
        response = _post(
            ScopedEdit, user, "/edit/%s/" % pk, session, "submit_form", {"name": "Grab"}, pk=pk
        )
        assert response.status_code == 403
    assert Group.objects.count() == before
    assert _writes(sql) == []


# --------------------------------------------------------------------------
# Hooks, independently and inherited.
# --------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("view_class", [HookedEdit, InheritedHookedEdit])
def test_hooks_apply_to_the_adapter_form(view_class):
    user, mine, _ = _world()
    session = _session()
    page = _open(view_class, user, mine.pk, session).content.decode()
    # get_initial wins over the instance value, as in Django; get_prefix names
    # the HTML field.
    assert 'name="g-name" value="Initial override"' in page
    response = _edit(view_class, user, mine.pk, session, **{"g-name": "Prefixed"})
    assert response.status_code == 200, response.content
    assert Group.objects.get(pk=mine.pk).name == "Prefixed"


@pytest.mark.django_db
def test_hooks_are_called_on_the_edit_form():
    user, mine, _ = _world()
    request = RequestFactory().get("/hooked/%s/" % mine.pk)
    request.user, request.session, request.tenant = user, _session(), None
    view = InheritedHookedEdit()
    view.setup(request, pk=mine.pk)
    view._djust_bind_route_kwargs({"pk": mine.pk})
    view.mount(request, pk=mine.pk)
    form = view.form_instance
    assert form.prefix == "g"
    assert form.label_suffix == " >"
    assert form.instance.pk == mine.pk


# --------------------------------------------------------------------------
# Choices, relations and uploads.
# --------------------------------------------------------------------------


@pytest.mark.django_db
def test_m2m_choices_and_save():
    user, mine, _ = _world()
    perms = list(Permission.objects.order_by("pk")[:2])
    mine.permissions.add(perms[0])
    session = _session()
    request = RequestFactory().get("/edit/%s/" % mine.pk)
    request.user, request.session, request.tenant = user, session, None
    view = ScopedEdit()
    view.setup(request, pk=mine.pk)
    view._djust_bind_route_kwargs({"pk": mine.pk})
    view.mount(request, pk=mine.pk)
    # Initial M2M values are primary keys; choices are (pk, label) strings.
    assert view.form_data["permissions"] == [perms[0].pk]
    assert (str(perms[1].pk), str(perms[1])) in view.form_choices["permissions"]

    _open(ScopedEdit, user, mine.pk, session)
    response = _edit(
        ScopedEdit,
        user,
        mine.pk,
        session,
        name=mine.name,
        permissions=[str(p.pk) for p in perms],
    )
    assert response.status_code == 200, response.content
    assert set(mine.permissions.values_list("pk", flat=True)) == {p.pk for p in perms}


@pytest.mark.django_db
def test_fk_relation_initial_choices_and_save():
    user, _, _ = _world()
    first, second = ContentType.objects.order_by("pk")[:2]
    perm = Permission.objects.create(
        name="ADR-035", content_type=first, codename="adr035_" + uuid.uuid4().hex[:6]
    )
    session = _session()
    url = "/permission/%s/" % perm.pk
    page = _get(PermissionEdit, user, url, session, pk=perm.pk)
    assert page.status_code == 200 and perm.codename in page.content.decode()
    response = _post(
        PermissionEdit,
        user,
        url,
        session,
        "submit_form",
        {"name": "Moved", "content_type": str(second.pk), "codename": perm.codename},
        pk=perm.pk,
    )
    assert response.status_code == 200, response.content
    perm.refresh_from_db()
    assert (perm.name, perm.content_type_id) == ("Moved", second.pk)
    # Initial FK value is the related pk, not the object (prepare_value).
    request = RequestFactory().get(url)
    request.user, request.session, request.tenant = user, session, None
    view = PermissionEdit()
    view.setup(request, pk=perm.pk)
    view._djust_bind_route_kwargs({"pk": perm.pk})
    view.mount(request, pk=perm.pk)
    assert view.form_data["content_type"] == second.pk


@pytest.mark.django_db
def test_upload_reaches_the_form_through_get_form_kwargs():
    user, mine, _ = _world()
    request = RequestFactory().get("/upload/%s/" % mine.pk)
    request.user, request.session, request.tenant = user, _session(), None
    view = UploadEdit()
    view.setup(request, pk=mine.pk)
    view._djust_bind_route_kwargs({"pk": mine.pk})
    view.mount(request, pk=mine.pk)
    view.request = request

    view.submit_form(name="With file")
    assert view.field_errors.get("attachment"), "a required upload is validated"
    assert Group.objects.get(pk=mine.pk).name == mine.name

    manager = view._upload_manager
    manager.register_entry("attachment", "ref-1", "note.txt", "text/plain", 5)
    manager.add_chunk("ref-1", 0, b"hello")
    manager.complete_upload("ref-1")
    view.submit_form(name="With file")
    assert ("upload", b"hello") in EVENTS
    assert Group.objects.get(pk=mine.pk).name == "With file"


# --------------------------------------------------------------------------
# Empty submission and validation.
# --------------------------------------------------------------------------


@pytest.mark.django_db
def test_empty_submission_revalidates_current_values(sql):
    user, mine, _ = _world()
    session = _session()
    _open(ScopedEdit, user, mine.pk, session)
    sql.clear()
    # No submitted fields: the form is bound to the current values and valid.
    response = _edit(ScopedEdit, user, mine.pk, session)
    assert response.status_code == 200
    assert ("form_valid", mine.pk) in EVENTS
    assert len(_writes(sql)) == 1


@pytest.mark.django_db
@pytest.mark.parametrize("case", ["blank", "duplicate"])
def test_invalid_input_is_reported_and_not_saved(sql, case):
    user, mine, theirs = _world()
    session = _session()
    _open(ScopedEdit, user, mine.pk, session)
    sql.clear()
    name = "" if case == "blank" else theirs.name
    response = _edit(ScopedEdit, user, mine.pk, session, name=name)
    assert response.status_code == 200
    body = response.content.decode()
    expected = "This field is required." if case == "blank" else "already exists"
    assert expected in body
    assert ("form_invalid", ["name"]) in EVENTS
    assert not any(event[0] == "form_valid" for event in EVENTS)
    assert _writes(sql) == []


# --------------------------------------------------------------------------
# Tampered, missing, deleted and revoked targets; failing callbacks.
# --------------------------------------------------------------------------


@pytest.mark.django_db
def test_tampered_identity_in_the_payload_is_ignored():
    user, mine, theirs = _world()
    session = _session()
    _open(ScopedEdit, user, mine.pk, session)
    response = _edit(
        ScopedEdit, user, mine.pk, session, name="Mine only", id=theirs.pk, pk=theirs.pk
    )
    assert response.status_code == 200
    assert _names(mine, theirs) == {mine.pk: "Mine only", theirs.pk: theirs.name}


@pytest.mark.django_db
@pytest.mark.parametrize("change", ["revoke", "delete", "remove_membership"])
def test_target_lost_between_events_is_denied(sql, change):
    user, mine, theirs = _world()
    session = _session()
    _open(ScopedEdit, user, mine.pk, session)
    if change == "revoke":
        REVOKED.add(mine.pk)
    elif change == "delete":
        Group.objects.filter(pk=mine.pk).delete()
    else:
        user.groups.remove(mine)
    sql.clear()
    response = _edit(ScopedEdit, user, mine.pk, session, name="Late")
    assert (response.status_code, json.loads(response.content)) == (403, {"error": DENIED})
    assert _writes(sql) == []
    assert not any(event[0].startswith("form_") for event in EVENTS)


@pytest.mark.django_db
@pytest.mark.parametrize("callback", ["queryset", "permission"])
def test_failing_callback_fails_closed(sql, callback):
    user, mine, _ = _world()
    session = _session()
    FAIL[callback] = True
    page = _open(ScopedEdit, user, mine.pk, session)
    assert (page.status_code, page.content) == (403, DENIED.encode())
    FAIL.clear()
    _open(ScopedEdit, user, mine.pk, session)
    FAIL[callback] = True
    sql.clear()
    response = _edit(ScopedEdit, user, mine.pk, session, name="Late")
    assert response.status_code == 403
    assert _writes(sql) == []
    assert "failed" not in response.content.decode()


# --------------------------------------------------------------------------
# WebSocket: failing callbacks, reconnect with unsaved input.
# --------------------------------------------------------------------------


def _allow():
    return override_settings(
        LIVEVIEW_ALLOWED_MODULES=[__name__], ROOT_URLCONF=__name__, DJUST_TENANTS=None
    )


async def _connect(user, session):
    from channels.testing import WebsocketCommunicator
    from djust.websocket import LiveViewConsumer

    socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    socket.scope.update(session=session, user=user, tenant=None)
    assert (await socket.connect())[0]
    await socket.receive_json_from(timeout=3)
    return socket


async def _mount(socket, view, url, prerendered=False):
    await socket.send_json_to(
        {
            "type": "mount",
            "view": __name__ + "." + view,
            "url": url,
            "has_prerendered": prerendered,
        }
    )
    return await socket.receive_json_from(timeout=5)


async def _event(socket, ref, event, params):
    await socket.send_json_to({"type": "event", "event": event, "params": params, "ref": ref})
    frames = []
    for _ in range(6):
        frame = await socket.receive_json_from(timeout=5)
        frames.append(frame)
        if frame.get("type") in {"patch", "html_update", "error", "noop"}:
            break
    return frames


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("callback", ["queryset", "permission"])
async def test_websocket_failing_callback_fails_closed(callback):
    user, mine, _ = await sync_to_async(_world)()
    session = await sync_to_async(_session)()
    url = "/edit/%s/" % mine.pk
    with _allow():
        FAIL[callback] = True
        socket = await _connect(user, session)
        try:
            frame = await _mount(socket, "ScopedEdit", url)
            assert (frame["type"], frame.get("code"), frame.get("error")) == (
                "error",
                "permission_denied",
                DENIED,
            ), frame
            assert "failed" not in json.dumps(frame)
        finally:
            await socket.disconnect()
        FAIL.clear()
        socket = await _connect(user, session)
        try:
            assert (await _mount(socket, "ScopedEdit", url))["type"] == "mount"
            FAIL[callback] = True
            frames = await _event(socket, 1, "submit_form", {"name": "Late"})
            assert frames[-1]["type"] == "error", frames
            assert not any(event[0].startswith("form_") for event in EVENTS)
        finally:
            await socket.disconnect()
    assert await sync_to_async(lambda: Group.objects.get(pk=mine.pk).name)() == mine.name


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("draft", ["Draft name", ""])
async def test_reconnect_keeps_unsaved_input_and_revalidates_it(draft):
    """Typed input survives a reconnect (the per-event session save), but it is
    validated again before anything is saved (D4)."""
    user, mine, _ = await sync_to_async(_world)()
    session = await sync_to_async(_session)()
    url = "/edit/%s/" % mine.pk
    await sync_to_async(_open)(ScopedEdit, user, mine.pk, session)
    await sync_to_async(session.save)()
    with _allow():
        socket = await _connect(user, session)
        try:
            assert (await _mount(socket, "ScopedEdit", url, prerendered=True))["type"] == "mount"
            frames = await _event(socket, 1, "validate_field", {"field": "name", "value": draft})
            assert frames[-1]["type"] != "error", frames
        finally:
            await socket.disconnect()

        EVENTS.clear()
        socket = await _connect(user, session)
        try:
            frame = await _mount(socket, "ScopedEdit", url, prerendered=True)
            assert frame["type"] == "mount", frame
            frames = await _event(socket, 1, "submit_form", {})
            text = json.dumps(frames)
        finally:
            await socket.disconnect()
    saved = await sync_to_async(lambda: Group.objects.get(pk=mine.pk).name)()
    if draft:
        assert "Saved Draft name" in text, frames
        assert saved == "Draft name"
    else:
        assert "This field is required." in text, frames
        assert saved == mine.name
        assert not any(event[0] == "form_valid" for event in EVENTS)


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_reconnect_after_deletion_does_not_restore_an_editable_form():
    user, mine, _ = await sync_to_async(_world)()
    session = await sync_to_async(_session)()
    url = "/edit/%s/" % mine.pk
    await sync_to_async(_open)(ScopedEdit, user, mine.pk, session)
    await sync_to_async(session.save)()
    await sync_to_async(Group.objects.filter(pk=mine.pk).delete)()
    with _allow():
        socket = await _connect(user, session)
        try:
            frame = await _mount(socket, "ScopedEdit", url, prerendered=True)
            assert (frame["type"], frame.get("error")) == ("error", DENIED), frame
        finally:
            await socket.disconnect()


# --------------------------------------------------------------------------
# Django and Rust rendering; typing.
# --------------------------------------------------------------------------


class ExplicitScopedEdit(ScopedEdit):
    exposure_policy = "explicit"


@pytest.mark.django_db
@pytest.mark.parametrize("view_class", [ScopedEdit, ExplicitScopedEdit])
@pytest.mark.parametrize("engine", ["django", "rust"])
def test_object_and_form_render_in_both_engines(view_class, engine):
    from django.template import Context, Engine

    user, mine, _ = _world()
    request = RequestFactory().get("/edit/%s/" % mine.pk)
    request.user, request.session, request.tenant = user, _session(), None
    view = view_class()
    view.setup(request, pk=mine.pk)
    view._djust_bind_route_kwargs({"pk": mine.pk})
    view.mount(request, pk=mine.pk)
    from djust.auth.core import enforce_object_permission

    enforce_object_permission(view, request)
    context = dict(view.get_context_data())
    source = "<h1>{{ object.name }}</h1><i>{{ form_data.name }}</i>"
    if engine == "django":
        output = Engine().from_string(source).render(Context(context))
    else:
        from djust.template_backend import DjustTemplateBackend

        backend = DjustTemplateBackend(
            params={"NAME": "djust", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}}
        )
        output = backend.from_string(source).render(context)
    assert output == "<h1>%s</h1><i>%s</i>" % (mine.name, mine.name)


def test_object_is_typed_by_the_model(tmp_path):
    pytest.importorskip("mypy")
    config = tmp_path / "mypy.ini"
    config.write_text(
        "[mypy]\nfollow_imports = silent\nignore_missing_imports = True\n"
        "[mypy-django.*]\nfollow_untyped_imports = True\n"
    )
    source = tmp_path / "consumer.py"
    source.write_text(
        "from typing import Optional\n"
        "from django.contrib.auth.models import Group, Permission\n"
        "from djust import LiveView\n"
        "from djust.forms import ModelFormMixin\n"
        "class Edit(ModelFormMixin[Group], LiveView):\n"
        "    model = Group\n"
        "view = Edit()\n"
        "current: Optional[Group] = view.object\n"
        "view.object = Group()\n"
        "wrong: Optional[Permission] = view.object\n"
        "view.object = Permission()\n"
        "Edit.model = Permission\n"
    )
    root = Path(__file__).resolve().parents[3]
    env = {**os.environ, "MYPYPATH": str(root / "python")}
    result = subprocess.run(
        [sys.executable, "-m", "mypy", "--config-file", str(config), str(source)],
        capture_output=True,
        check=False,
        text=True,
        env=env,
        timeout=180,
    )
    errors = [line for line in result.stdout.splitlines() if ": error:" in line]
    assert [line.split(":")[1] for line in errors] == ["10", "11", "12"], result.stdout
