- **A `check_permissions()` refusal is a 403 for a signed-in user.** Returning
  False or raising `PermissionDenied` sent a signed-in user to `LOGIN_URL`;
  they now get `PermissionDenied` (Django's `handler403` on the page GET, close
  4403 on a live mount), and only an anonymous user is redirected to sign in,
  as `permission_required` and Django's `AccessMixin` already do. Cases in
  `TestCheckPermissionsRefusal` (`python/tests/test_auth_any_of.py`).
