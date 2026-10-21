"""``permission_required`` "any of" (``AnyOf``) and the check_permissions() 403.

Covers every place djust reads ``permission_required``: the view check (mount,
HTTP GET, ``reauth_on_event`` through ``check_view_auth_lightweight``), the
``@permission_required`` handler check, the dispatch-level mixin, the client
route map, and ``djust_audit`` (inventory, ``--dump-permissions`` and
``--permissions``).
"""

from unittest.mock import MagicMock

import pytest
from django.core.exceptions import PermissionDenied

from djust.auth import (
    AnyOf,
    check_handler_permission,
    check_view_auth,
    check_view_auth_lightweight,
)
from djust.decorators import event_handler, permission_required
from djust.live_view import LiveView


class _User:
    """A user with a fixed permission set, answering like ModelBackend."""

    def __init__(self, perms=(), authenticated=True):
        self.perms = set(perms)
        self.is_authenticated = authenticated

    def has_perm(self, perm):
        return self.is_authenticated and perm in self.perms

    def has_perms(self, perms):
        return all(self.has_perm(p) for p in perms)


def _request(perms=(), authenticated=True):
    request = MagicMock()
    request.user = _User(perms, authenticated)
    return request


# ---------------------------------------------------------------------------
# AnyOf itself
# ---------------------------------------------------------------------------


class TestAnyOfValue:
    def test_rejects_empty(self):
        with pytest.raises(ValueError):
            AnyOf()

    def test_rejects_non_string_member(self):
        with pytest.raises(TypeError):
            AnyOf("a.x", 3)

    def test_list_argument_reads_like_varargs(self):
        assert AnyOf(["a.x", "b.y"]) == AnyOf("a.x", "b.y")

    def test_tuple_reading_requires_every_member(self):
        """Code that reads the spec as a plain tuple is stricter, never looser."""
        user = _User(["a.x"])
        assert not user.has_perms(tuple(AnyOf("a.x", "b.y")))

    def test_malformed_mixed_spec_raises(self):
        from djust.auth.any_of import user_has_permission_spec

        with pytest.raises(TypeError):
            user_has_permission_spec(_User(["a.x"]), ["a.x", 7])


# ---------------------------------------------------------------------------
# View-level permission_required
# ---------------------------------------------------------------------------


class _AnyView(LiveView):
    permission_required = AnyOf("runs.agent_run", "chat.chat_general")


class _MixedView(LiveView):
    permission_required = ["runs.view_run", AnyOf("runs.review", "runs.approve")]


class TestViewAnyOf:
    @pytest.mark.parametrize("perm", ["runs.agent_run", "chat.chat_general"])
    def test_any_one_member_grants(self, perm):
        assert check_view_auth(_AnyView(), _request([perm])) is None

    def test_none_of_them_is_403_when_signed_in(self):
        with pytest.raises(PermissionDenied):
            check_view_auth(_AnyView(), _request(["other.perm"]))

    def test_anonymous_goes_to_login(self):
        assert check_view_auth(_AnyView(), _request(authenticated=False)) is not None

    def test_mixed_needs_the_string_and_one_of_the_group(self):
        assert check_view_auth(_MixedView(), _request(["runs.view_run", "runs.approve"])) is None
        with pytest.raises(PermissionDenied):
            check_view_auth(_MixedView(), _request(["runs.view_run"]))
        with pytest.raises(PermissionDenied):
            check_view_auth(_MixedView(), _request(["runs.approve"]))

    def test_lightweight_recheck_used_by_reauth_on_event(self):
        """reauth_on_event re-checks through check_view_auth_lightweight."""
        assert check_view_auth_lightweight(_AnyView(), _request(["chat.chat_general"])) is True
        assert check_view_auth_lightweight(_AnyView(), _request(["other.perm"])) is False


class TestHandlerAnyOf:
    def _handler(self, spec):
        @permission_required(spec)
        @event_handler()
        def handler(self, **kwargs):
            pass

        return handler

    def test_any_one_member_grants(self):
        handler = self._handler(AnyOf("a.x", "b.y"))
        assert check_handler_permission(handler, _request(["b.y"])) is True

    def test_none_of_them_denies(self):
        handler = self._handler(AnyOf("a.x", "b.y"))
        assert check_handler_permission(handler, _request(["c.z"])) is False

    def test_mixed_spec(self):
        handler = self._handler(["a.x", AnyOf("b.y", "c.z")])
        assert check_handler_permission(handler, _request(["a.x", "c.z"])) is True
        assert check_handler_permission(handler, _request(["c.z"])) is False


class TestDispatchMixinAnyOf:
    def test_permission_required_liveview_mixin_honours_any_of(self):
        from djust.auth.mixins import PermissionRequiredLiveViewMixin

        class Base:
            def dispatch(self, request, *args, **kwargs):
                return "ok"

        class View(PermissionRequiredLiveViewMixin, Base):
            permission_required = AnyOf("a.x", "b.y")

        assert View().dispatch(_request(["b.y"])) == "ok"
        with pytest.raises(PermissionDenied):
            View().dispatch(_request(["c.z"]))


class TestRouteMapAnyOf:
    def test_route_requirement_keeps_the_any_of_group(self):
        from djust.routing import _user_satisfies, _view_auth_requirement

        pattern = MagicMock(spec=["callback"])
        pattern.callback = MagicMock(spec=[])
        requirement = _view_auth_requirement(pattern, _AnyView)
        assert _user_satisfies(_request(["chat.chat_general"]), requirement) is True
        assert _user_satisfies(_request(["other.perm"]), requirement) is False


# ---------------------------------------------------------------------------
# check_permissions(): signed-in refusal is a 403, anonymous goes to login
# ---------------------------------------------------------------------------


class _RefusesFalse(LiveView):
    def check_permissions(self, request):
        return False


class _RefusesRaise(LiveView):
    def check_permissions(self, request):
        raise PermissionDenied("not yours")


class TestCheckPermissionsRefusal:
    @pytest.mark.parametrize("view_cls", [_RefusesFalse, _RefusesRaise])
    def test_signed_in_refusal_is_403(self, view_cls):
        with pytest.raises(PermissionDenied):
            check_view_auth(view_cls(), _request(["any.perm"]))

    @pytest.mark.parametrize("view_cls", [_RefusesFalse, _RefusesRaise])
    def test_anonymous_refusal_goes_to_login(self, view_cls):
        result = check_view_auth(view_cls(), _request(authenticated=False))
        assert result == "/accounts/login/" or result.endswith("login/")

    def test_custom_login_url_for_anonymous(self):
        class View(_RefusesFalse):
            login_url = "/sign-in/"

        assert check_view_auth(View(), _request(authenticated=False)) == "/sign-in/"

    def test_http_get_signed_in_refusal_is_403(self, rf):
        """The page GET raises PermissionDenied, which Django serves as 403."""

        class View(_RefusesFalse):
            template = "<div dj-root>secret</div>"

        request = rf.get("/x/")
        request.user = _User(authenticated=True)
        request.session = {}
        with pytest.raises(PermissionDenied):
            View.as_view()(request)

    def test_http_get_anonymous_refusal_redirects_to_login(self, rf):
        class View(_RefusesFalse):
            template = "<div dj-root>secret</div>"

        request = rf.get("/x/")
        request.user = _User(authenticated=False)
        request.session = {}
        response = View.as_view()(request)
        assert response.status_code == 302
        assert "next=" in response["Location"]


# ---------------------------------------------------------------------------
# djust_audit: inventory, --dump-permissions, --permissions
# ---------------------------------------------------------------------------


class TestAuditAnyOf:
    def test_extract_auth_info_reports_the_group(self):
        from djust.management.commands.djust_audit import _extract_auth_info

        info = _extract_auth_info(_MixedView)
        assert info["permission_required"] == ["runs.view_run"]
        assert info["permission_required_any"] == [["runs.approve", "runs.review"]]

    def test_extract_auth_info_any_only(self):
        from djust.management.commands.djust_audit import _extract_auth_info

        info = _extract_auth_info(_AnyView)
        assert "permission_required" not in info
        assert info["permission_required_any"] == [["chat.chat_general", "runs.agent_run"]]

    def test_extract_auth_info_tuple_is_all_of(self):
        from djust.management.commands.djust_audit import _extract_auth_info

        class View(LiveView):
            permission_required = ("a.x", "b.y")

        assert _extract_auth_info(View)["permission_required"] == ["a.x", "b.y"]

    def test_dump_round_trips_through_the_document(self):
        from djust.management.commands.djust_audit import _extract_auth_info
        from djust.permissions import PermissionsDocument, dump_starter_document

        audits = [
            {"class": "app.views.AnyView", "auth": _extract_auth_info(_AnyView)},
            {"class": "app.views.MixedView", "auth": _extract_auth_info(_MixedView)},
        ]
        text = dump_starter_document(audits)
        import yaml

        doc = PermissionsDocument._from_data(yaml.safe_load(text), source="<dump>")
        assert doc.views["app.views.AnyView"].permissions_any == [
            ["chat.chat_general", "runs.agent_run"]
        ]
        assert doc.views["app.views.MixedView"].permissions == ["runs.view_run"]
        findings = doc.compare_all({audit["class"]: audit["auth"] for audit in audits})
        assert findings == []

    def _doc(self, entry):
        from djust.permissions import PermissionsDocument

        return PermissionsDocument._from_data(
            {"version": 1, "strict": True, "views": {"app.V": entry}}, source="<t>"
        )

    def test_flat_permissions_any_is_one_group(self):
        doc = self._doc({"login_required": True, "permissions_any": ["b.y", "a.x"]})
        assert doc.views["app.V"].permissions_any == [["a.x", "b.y"]]

    def test_permissions_any_alone_is_a_valid_entry(self):
        doc = self._doc({"permissions_any": [["a.x", "b.y"]]})
        assert doc.views["app.V"].permissions_any == [["a.x", "b.y"]]

    def test_permissions_any_must_be_strings(self):
        from djust.permissions import PermissionsDocumentError

        with pytest.raises(PermissionsDocumentError, match="permissions_any"):
            self._doc({"permissions_any": [["a.x", 3]]})

    def test_public_and_permissions_any_exclusive(self):
        from djust.permissions import PermissionsDocumentError

        with pytest.raises(PermissionsDocumentError, match="mutually exclusive"):
            self._doc({"public": True, "permissions_any": ["a.x"]})

    def test_p005_when_group_differs(self):
        doc = self._doc({"login_required": True, "permissions_any": [["a.x", "b.y"]]})
        findings = doc.compare_all({"app.V": {"permission_required_any": [["a.x", "c.z"]]}})
        assert [f.code for f in findings] == ["P005"]

    def test_p005_when_code_is_all_of_but_document_says_any(self):
        doc = self._doc({"login_required": True, "permissions_any": [["a.x", "b.y"]]})
        findings = doc.compare_all({"app.V": {"permission_required": ["a.x", "b.y"]}})
        assert [f.code for f in findings] == ["P005"]

    def test_p005_when_document_omits_the_codes_group(self):
        doc = self._doc({"login_required": True, "permissions": ["a.x"]})
        findings = doc.compare_all(
            {
                "app.V": {
                    "permission_required": ["a.x"],
                    "permission_required_any": [["b.y", "c.z"]],
                }
            }
        )
        assert [f.code for f in findings] == ["P005"]

    def test_any_of_counts_as_auth_for_p003(self):
        doc = self._doc({"public": True})
        findings = doc.compare_all({"app.V": {"permission_required_any": [["a.x"]]}})
        assert [f.code for f in findings] == ["P003"]

    def test_matching_group_has_no_finding(self):
        doc = self._doc({"login_required": True, "permissions_any": [["b.y", "a.x"]]})
        findings = doc.compare_all({"app.V": {"permission_required_any": [["a.x", "b.y"]]}})
        assert findings == []


class TestHandlerInventoryAnyOf:
    class _View(LiveView):
        template = "<div dj-root></div>"

        @permission_required(AnyOf("runs.review", "runs.approve"))
        @event_handler()
        def decide(self, **kwargs):
            pass

    def test_audit_tag_names_the_group(self):
        from djust.management.commands.djust_audit import _format_decorator_tags

        meta = self._View.decide._djust_decorators
        assert "@permission_required(AnyOf('runs.review', 'runs.approve'))" in (
            _format_decorator_tags(meta)
        )

    def test_schema_spells_out_the_group(self):
        import json

        from djust.schema import _extract_view_schema

        schema = _extract_view_schema(self._View, [LiveView])
        (handler,) = [h for h in schema["handlers"] if h["name"] == "decide"]
        assert handler["decorators"]["permission_required"] == {
            "all_of": [],
            "any_of": [["runs.approve", "runs.review"]],
        }
        json.dumps(schema)
