"""A reused sticky child is re-authorized on every render.

When ``{% live_render ... sticky=True %}`` finds the child already registered
on the parent (the second and later renders of a long-lived parent), the tag
reuses that live instance. The same view-level and object-level checks the
fresh-mount path runs must apply to the reused instance against the current
request; when they deny, the child is dropped from the parent and the embed is
refused. Sticky children carried across a ``live_redirect`` are likewise kept
only when their object-level check passes for the new request.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.template import Context, Template, TemplateSyntaxError

from djust.live_view import LiveView

_MOD = "tests.unit.test_sticky_child_reuse_authorization"

# Per-test toggle for the object-level decision.
_OBJECT_ALLOWED = {"value": True}


class _Doc:
    body = "DOC-BODY-42"


class _FakeUser:
    is_authenticated = True

    def __init__(self, perms: set[str] | None = None):
        self._perms = set(perms or ())

    def has_perm(self, perm):
        return perm in self._perms

    def has_perms(self, perms):
        return all(p in self._perms for p in perms)


class _ObjectScopedSticky(LiveView):
    sticky = True
    sticky_id = "doc-panel"
    template = "<div><p>{{ body }}</p><span>{{ clicks }}</span></div>"

    def mount(self, request, **kwargs):
        self.clicks = 0
        self.unmount_calls = 0

    def get_object(self):
        return _Doc()

    def has_object_permission(self, request, obj):
        return _OBJECT_ALLOWED["value"]

    def get_context_data(self, **kwargs):
        return {"body": _Doc.body, "clicks": self.clicks, "view": self}

    def _on_sticky_unmount(self):
        self.unmount_calls += 1
        super()._on_sticky_unmount()


class _PermissionSticky(LiveView):
    sticky = True
    sticky_id = "perm-panel"
    permission_required = "auth.view_doc"
    template = "<div>perm panel</div>"

    def mount(self, request, **kwargs):
        pass


class _LoginSticky(LiveView):
    sticky = True
    sticky_id = "login-panel"
    login_required = True
    template = "<div>login panel</div>"

    def mount(self, request, **kwargs):
        pass


class _ParentObject(LiveView):
    template = (
        "{% load live_tags %}<div dj-root>"
        '{% live_render "' + _MOD + '._ObjectScopedSticky" sticky=True %}'
        "</div>"
    )

    def mount(self, request, **kwargs):
        pass


class _ParentPermission(LiveView):
    template = (
        "{% load live_tags %}<div dj-root>"
        '{% live_render "' + _MOD + '._PermissionSticky" sticky=True %}'
        "</div>"
    )

    def mount(self, request, **kwargs):
        pass


class _ParentLogin(LiveView):
    template = (
        "{% load live_tags %}<div dj-root>"
        '{% live_render "' + _MOD + '._LoginSticky" sticky=True %}'
        "</div>"
    )

    def mount(self, request, **kwargs):
        pass


@pytest.fixture(autouse=True)
def _reset_object_toggle():
    _OBJECT_ALLOWED["value"] = True
    yield
    _OBJECT_ALLOWED["value"] = True


def _make_parent(rf, parent_cls, user):
    request = rf.get("/")
    request.user = user
    parent = parent_cls()
    parent.request = request
    parent.mount(request)
    return parent


def _render(parent):
    return Template(type(parent).template).render(
        Context({"view": parent, "request": parent.request})
    )


class TestReuseObjectPermission:
    def test_reuse_while_permitted_keeps_live_instance(self, rf):
        parent = _make_parent(rf, _ParentObject, AnonymousUser())
        _render(parent)
        child = parent._child_views["doc-panel"]
        child.clicks = 3

        out = _render(parent)

        assert parent._child_views["doc-panel"] is child
        assert "DOC-BODY-42" in out
        assert "<span>3</span>" in out

    def test_reuse_after_object_permission_revoked_refuses_embed(self, rf):
        parent = _make_parent(rf, _ParentObject, AnonymousUser())
        _render(parent)
        child = parent._child_views["doc-panel"]

        _OBJECT_ALLOWED["value"] = False
        with pytest.raises(TemplateSyntaxError) as exc_info:
            _render(parent)

        assert "object-level permission" in str(exc_info.value)
        assert "doc-panel" not in parent._child_views
        assert child.unmount_calls == 1

    def test_reuse_denial_matches_fresh_mount_denial(self, rf):
        _OBJECT_ALLOWED["value"] = False
        fresh_parent = _make_parent(rf, _ParentObject, AnonymousUser())
        with pytest.raises(TemplateSyntaxError) as fresh_exc:
            _render(fresh_parent)

        _OBJECT_ALLOWED["value"] = True
        parent = _make_parent(rf, _ParentObject, AnonymousUser())
        _render(parent)
        _OBJECT_ALLOWED["value"] = False
        with pytest.raises(TemplateSyntaxError) as reuse_exc:
            _render(parent)

        assert str(reuse_exc.value) == str(fresh_exc.value)

    def test_permission_restored_after_denial_mounts_fresh_child(self, rf):
        parent = _make_parent(rf, _ParentObject, AnonymousUser())
        _render(parent)
        old_child = parent._child_views["doc-panel"]

        _OBJECT_ALLOWED["value"] = False
        with pytest.raises(TemplateSyntaxError):
            _render(parent)

        _OBJECT_ALLOWED["value"] = True
        out = _render(parent)
        new_child = parent._child_views["doc-panel"]
        assert new_child is not old_child
        assert "DOC-BODY-42" in out


class TestReuseViewLevelAuth:
    def test_reuse_after_permission_removed_raises_permission_denied(self, rf):
        user = _FakeUser(perms={"auth.view_doc"})
        parent = _make_parent(rf, _ParentPermission, user)
        _render(parent)
        assert "perm-panel" in parent._child_views

        user._perms.clear()
        with pytest.raises(PermissionDenied):
            _render(parent)
        assert "perm-panel" not in parent._child_views

    def test_reuse_with_permission_retained_renders(self, rf):
        user = _FakeUser(perms={"auth.view_doc"})
        parent = _make_parent(rf, _ParentPermission, user)
        _render(parent)
        child = parent._child_views["perm-panel"]

        out = _render(parent)
        assert "perm panel" in out
        assert parent._child_views["perm-panel"] is child

    def test_reuse_after_logout_refuses_embed(self, rf):
        parent = _make_parent(rf, _ParentLogin, _FakeUser())
        _render(parent)
        assert "login-panel" in parent._child_views

        parent.request.user = AnonymousUser()
        with pytest.raises(TemplateSyntaxError) as exc_info:
            _render(parent)
        assert "login redirect" in str(exc_info.value)
        assert "login-panel" not in parent._child_views


class TestPreserveAcrossRedirectObjectPermission:
    def test_object_permission_denied_for_new_request_drops_child(self, rf):
        parent = _make_parent(rf, _ParentObject, AnonymousUser())
        _render(parent)
        child = parent._child_views["doc-panel"]

        _OBJECT_ALLOWED["value"] = False
        new_request = rf.get("/other/")
        new_request.user = AnonymousUser()
        survivors = parent._preserve_sticky_children(new_request)

        assert "doc-panel" not in survivors
        assert child.unmount_calls == 1

    def test_object_permission_granted_for_new_request_keeps_child(self, rf):
        parent = _make_parent(rf, _ParentObject, AnonymousUser())
        _render(parent)
        child = parent._child_views["doc-panel"]

        new_request = rf.get("/other/")
        new_request.user = AnonymousUser()
        survivors = parent._preserve_sticky_children(new_request)

        assert survivors.get("doc-panel") is child
        assert child.request is new_request

    def test_object_check_reads_the_new_request(self, rf):
        """get_object() may read self.request: the check must see the new one
        (merge review of #2944)."""
        parent = _make_parent(rf, _ParentObject, AnonymousUser())
        _render(parent)
        child = parent._child_views["doc-panel"]
        seen = []
        original = child.has_object_permission

        def spy(request, obj):
            seen.append(child.request is request)
            return original(request, obj)

        child.has_object_permission = spy
        new_request = rf.get("/other/")
        new_request.user = AnonymousUser()
        parent._preserve_sticky_children(new_request)
        assert seen == [True]

    def test_raising_predicate_denies_only_that_child(self, rf, monkeypatch):
        """A predicate that raises fails closed for that child instead of
        aborting preservation."""
        parent = _make_parent(rf, _ParentObject, AnonymousUser())
        _render(parent)
        child = parent._child_views["doc-panel"]

        def boom(request, obj):
            raise RuntimeError("broken predicate")

        monkeypatch.setattr(child, "has_object_permission", boom)
        new_request = rf.get("/other/")
        new_request.user = AnonymousUser()
        survivors = parent._preserve_sticky_children(new_request)
        assert "doc-panel" not in survivors
        assert child.unmount_calls == 1
