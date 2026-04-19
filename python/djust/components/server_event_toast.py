"""ServerEventToast mixin for pushing toast notifications via WebSocket."""


class ServerEventToastMixin:
    """Mixin for LiveViews that adds push_toast() capability.

    Sends a special ``__toast__`` event over WebSocket that the
    ``{% toast_container %}`` template tag auto-renders.

    Usage::

        from djust_components.components import ServerEventToastMixin

        class MyView(ServerEventToastMixin, LiveView):
            def on_save(self):
                self.save_data()
                self.push_toast("Saved!", type="success")

            def on_delete(self):
                self.push_toast("Deleted", type="error", duration=5000)

    In template::

        {% load djust_components %}
        {% toast_container position="top-right" %}

    Args for push_toast:
        message: Toast text content
        type: Notification type (info, success, warning, error)
        duration: Auto-dismiss duration in ms (0 = no auto-dismiss)
    """

    def push_toast(
        self,
        message: str,
        type: str = "info",
        duration: int = 3000,
    ):
        """Push a toast notification to the client via WebSocket.

        Emits a ``__toast__`` event with the toast payload.
        The ``{% toast_container %}`` tag's JS listener renders it.
        """
        self.push_event(
            "__toast__",
            {
                "message": message,
                "type": type,
                "duration": duration,
            },
        )
