"""
Type stub for LiveView class.

This stub aggregates all mixin methods to provide comprehensive type hints
for LiveView instances. It includes methods from NavigationMixin, PushEventMixin,
StreamsMixin, and StreamingMixin that are used at runtime but may not be
fully discoverable by static analysis tools.
"""

from typing import Any, Callable, Dict, Optional

from django.views import View

from .mixins.navigation import NavigationMixin
from .mixins.push_events import PushEventMixin
from .mixins.streams import StreamsMixin
from .streaming import StreamingMixin
from .session_utils import Stream

class LiveView(
    StreamsMixin,
    StreamingMixin,
    NavigationMixin,
    PushEventMixin,
    View,
):
    """
    Base class for reactive LiveView components.

    Inherits methods from:
    - NavigationMixin: live_patch(), live_redirect(), handle_params()
    - PushEventMixin: push_event()
    - StreamsMixin: stream(), stream_insert(), stream_delete(), stream_reset()
    - StreamingMixin: stream_to(), push_state() (async methods)
    """

    template_name: Optional[str] = None
    template: Optional[str] = None

    # LiveView lifecycle methods
    def mount(self, request: Any, **kwargs: Any) -> None: ...
    def get_context_data(self, **kwargs: Any) -> Dict[str, Any]: ...
    def handle_tick(self) -> None: ...
    def get_state(self) -> Dict[str, Any]: ...

    # NavigationMixin methods
    def live_patch(
        self,
        params: Optional[Dict[str, Any]] = None,
        path: Optional[str] = None,
        replace: bool = False,
    ) -> None: ...
    def live_redirect(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        replace: bool = False,
    ) -> None: ...
    def handle_params(self, params: Dict[str, Any], uri: str) -> None: ...

    # PushEventMixin methods
    def push_event(self, event: str, payload: Optional[Dict[str, Any]] = None) -> None: ...

    # StreamsMixin methods
    def stream(
        self,
        name: str,
        items: Any,
        dom_id: Optional[Callable[[Any], str]] = None,
        at: int = -1,
        reset: bool = False,
    ) -> Stream: ...
    def stream_insert(self, name: str, item: Any, at: int = -1) -> None: ...
    def stream_delete(self, name: str, item_or_id: Any) -> None: ...
    def stream_reset(self, name: str, items: Any = None) -> None: ...

    # StreamingMixin async methods
    async def stream_to(
        self,
        stream_name: str,
        target: Optional[str] = None,
        html: Optional[str] = None,
    ) -> None: ...
    async def stream_insert(  # type: ignore[misc]  # noqa: F811  # Overload with StreamsMixin
        self,
        stream_name: str,
        html: str,
        at: str = "append",
        target: Optional[str] = None,
    ) -> None: ...
    async def stream_text(
        self,
        stream_name: str,
        text: str,
        mode: str = "append",
        target: Optional[str] = None,
    ) -> None: ...
    async def stream_error(
        self,
        stream_name: str,
        error: str,
        target: Optional[str] = None,
    ) -> None: ...
    async def stream_start(
        self,
        stream_name: str,
        target: Optional[str] = None,
    ) -> None: ...
    async def stream_done(
        self,
        stream_name: str,
        target: Optional[str] = None,
    ) -> None: ...
    async def stream_delete(  # type: ignore[misc]  # noqa: F811  # Overload with StreamsMixin
        self,
        stream_name: str,
        selector: str,
    ) -> None: ...
    async def push_state(self) -> None: ...
