"""A context processor exposing a derived boolean (#3282's documented pattern)."""


def can_manage(request):
    return {"can_manage": bool(getattr(request.user, "is_staff", False))}
