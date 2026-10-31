"""
Navigation mixin for LiveView — URL state management.

Provides live_patch() (update URL without remount) and live_redirect()
(navigate to a different view over the same WebSocket connection).

Inspired by Phoenix LiveView's live_patch and live_redirect.
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def own_route_kwargs(view_instance: Any, url: str) -> Optional[Dict[str, Any]]:
    """``url``'s resolved kwargs if that route serves ``view_instance``'s class.

    ``None`` when the URL does not resolve, or resolves to another view: a URL
    routed elsewhere must not select this view's object (ADR-035). The query
    string never takes part in the route.
    """
    try:
        from urllib.parse import unquote, urlsplit

        from django.urls import resolve

        match = resolve(unquote(urlsplit(url).path))
    except Exception:  # noqa: BLE001 — unresolvable means no route kwargs
        return None
    if getattr(match.func, "view_class", None) is not type(view_instance):
        return None
    return dict(match.kwargs)


def same_origin_target(url: str) -> str:
    """``url`` reduced to a same-origin path and query, for a server redirect.

    The scheme, host and fragment are dropped, backslashes (which browsers
    read as slashes) are normalized, and leading slashes collapse to one, so
    the result can never name another origin (``//host`` or ``/\\host``) or a
    ``javascript:`` URL (PR #3159 review).
    """
    from urllib.parse import urlsplit

    parts = urlsplit(url.replace("\\", "/"))
    path = "/" + parts.path.lstrip("/")
    return path + ("?" + parts.query if parts.query else "")


class NavigationMixin:
    """
    Adds URL navigation capabilities to a LiveView.

    Methods:
        live_patch(params=None, path=None, replace=False):
            Update URL query params without remounting. Triggers re-render.

        live_redirect(path, params=None, replace=False):
            Navigate to a different view over the existing WebSocket.

    The mixin queues navigation commands that are flushed to the client
    after each handler execution, similar to push_event.
    """

    def _init_navigation(self) -> None:
        """Initialize navigation state. Called from __init__."""
        self._pending_navigation: List[Dict[str, Any]] = []

    def live_patch(
        self,
        params: Optional[Dict[str, Any]] = None,
        path: Optional[str] = None,
        replace: bool = False,
    ) -> None:
        """
        Update the browser URL without remounting the view.

        The view's state is updated and re-rendered, but mount() is NOT called
        again. The browser URL changes via history.pushState (or replaceState
        if replace=True).

        Args:
            params: Query parameters to set. Merged with existing params.
                    Pass None to keep current params, {} to clear them.
            path: Optional new path. Defaults to current path.
            replace: If True, use replaceState instead of pushState.

        Example::

            @event_handler
            def filter_results(self, category="all", **kwargs):
                self.category = category
                self.live_patch(params={"category": category, "page": 1})
        """
        nav = {
            "type": "live_patch",
            "replace": replace,
        }
        if params is not None:
            nav["params"] = params
        if path is not None:
            nav["path"] = path
        self._pending_navigation.append(nav)

    def live_redirect(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        replace: bool = False,
    ) -> None:
        """
        Navigate to a different LiveView over the existing WebSocket.

        The current view is unmounted and the new view is mounted without
        a full page reload or WebSocket reconnection.

        Args:
            path: URL path to navigate to (e.g. "/items/42/").
            params: Optional query parameters.
            replace: If True, use replaceState instead of pushState.

        Example::

            @event_handler
            def go_to_detail(self, item_id, **kwargs):
                self.live_redirect(f"/items/{item_id}/")
        """
        nav = {
            "type": "live_redirect",
            "path": path,
            "replace": replace,
        }
        if params is not None:
            nav["params"] = params
        self._pending_navigation.append(nav)

    def _drain_navigation(self) -> List[Dict[str, Any]]:
        """Drain and return pending navigation commands."""
        commands = self._pending_navigation
        self._pending_navigation = []
        return commands

    def handle_params(self, params: Dict[str, Any], uri: str) -> None:
        """
        Called after mount() on initial render AND on every subsequent URL change.

        This is the standard place to derive view state from URL parameters.
        It fires:
        1. After ``mount()`` on the HTTP render, and after ``mount()`` completes
           during the initial WebSocket connect.
        2. On ``live_patch`` (server-initiated URL change without remount).
        3. On browser back/forward navigation (popstate).

        Override this to update view state based on URL params.  This avoids
        duplicating URL-parsing logic between ``mount()`` and event handlers.

        Args:
            params: The URL query parameters as a dict. Raw strings (a repeated
                key is a list) unless the view sets ``params_form``, in which
                case it is that form's ``cleaned_data``.
            uri: The full URI string (path + query string).

        Query parameters arrive as untrusted strings, so declare them. A view
        that sets ``params_form`` gets validated, typed values on every
        transport, and a bad value is a message in the page instead of a 500.

        Example::

            class ListParams(forms.Form):
                category = forms.ChoiceField(
                    choices=[("all", "All"), ("books", "Books")],
                    required=False,
                    initial="all",
                )
                page = forms.IntegerField(min_value=1, required=False, initial=1)

            class ListView(LiveView):
                params_form = ListParams

                def handle_params(self, params, uri):
                    self.category = params["category"]
                    self.page = params["page"]

        Invalid input goes to :meth:`handle_invalid_params` instead, and the
        messages are in the template as ``params_errors``.
        """
        pass

    def handle_invalid_params(self, form: Any, uri: str) -> None:
        """
        Called instead of :meth:`handle_params` when ``params_form`` rejects the URL.

        ``form`` is the bound form (``form.errors`` holds the messages); ``uri``
        is the page URL. The framework has already put the messages on
        ``self.params_errors`` (``{field: [messages]}``, errors that belong to no
        field under ``"__all__"``) for the template::

            {% if params_errors.page %}<p>{{ params_errors.page.0 }}</p>{% endif %}

        The default hands :meth:`handle_params` the form's initial values, so the
        page renders its defaults and shows the errors. Override it to refuse the
        URL instead::

            def handle_invalid_params(self, form, uri):
                raise Http404("No such page.")

        or to answer 400 and keep the page (set a flag here and return 400 from
        ``get_http_status()``). Call ``super().handle_invalid_params(form, uri)``
        to keep the default as well (a sync override only: an ``async def``
        override calls its own ``handle_params`` instead). Over a WebSocket or SSE mount, an exception
        raised here fails the mount like one from ``handle_params``; a ``Http404``
        is a Django 404 only on the HTTP render.
        """
        from .._params_form import initial_values
        from .._view_hooks import call_hook_sync

        # Run to completion here, so an override that calls ``super()`` without
        # ``return`` (or an ``async def handle_params``) cannot drop the call.
        call_hook_sync(self.handle_params, initial_values(form), uri)
