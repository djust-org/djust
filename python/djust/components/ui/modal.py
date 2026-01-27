"""
Modal/dialog component for djust.

Provides modal dialogs with customizable content and sizes.
"""

from typing import Dict, Any
from ..base import LiveComponent
from django.utils.safestring import mark_safe


class ModalComponent(LiveComponent):
    """
    Pre-built modal/dialog component.

    A dialog overlay component for displaying important content that requires
    user attention. Supports multiple sizes (sm, md, lg, xl) and includes
    a backdrop. Adapts to the configured CSS framework.

    Usage:
        from djust.components import ModalComponent

        # In your LiveView:
        def mount(self, request):
            self.confirm_modal = ModalComponent(
                title="Confirm Action",
                body="Are you sure you want to proceed?",
                show=False,
                size="md"
            )

        # In template:
        {{ confirm_modal.render }}

        # Programmatic control:
        def show_confirmation(self):
            self.confirm_modal.show("Confirm Delete", "This cannot be undone")

        def close_modal(self):
            self.confirm_modal.hide()
    """

    template_name = None  # Uses inline rendering

    def mount(self, **kwargs):
        """Initialize modal state"""
        self.title = kwargs.get("title", "")
        self.body = kwargs.get("body", "")
        self.show_modal = kwargs.get("show", False)
        self.size = kwargs.get("size", "md")  # sm, md, lg, xl

    def get_context(self) -> Dict[str, Any]:
        """Get modal context"""
        return {
            "title": self.title,
            "body": self.body,
            "show": self.show_modal,
            "size": self.size,
        }

    def show(self, title: str = None, body: str = None):
        """Show the modal"""
        if title:
            self.title = title
        if body:
            self.body = body
        self.show_modal = True
        self.trigger_update()

    def hide(self):
        """Hide the modal"""
        self.show_modal = False
        self.trigger_update()

    def set_title(self, title: str):
        """Update modal title"""
        self.title = title
        self.trigger_update()

    def set_body(self, body: str):
        """Update modal body"""
        self.body = body
        self.trigger_update()

    def render(self) -> str:
        """Render modal with inline HTML"""
        if not self.show_modal:
            return ""

        from ...config import config

        framework = config.get("css_framework", "bootstrap5")

        if framework == "bootstrap5":
            return mark_safe(self._render_bootstrap())
        elif framework == "tailwind":
            return mark_safe(self._render_tailwind())
        else:
            return mark_safe(self._render_plain())

    def _render_bootstrap(self) -> str:
        """Render Bootstrap 5 modal"""
        size_map = {"sm": "modal-sm", "md": "", "lg": "modal-lg", "xl": "modal-xl"}
        size_class = size_map.get(self.size, "")

        html = f"""
        <div class="modal fade show" id="{self.component_id}" style="display: block;" tabindex="-1">
            <div class="modal-dialog {size_class}">
                <div class="modal-content">
                    <div class="modal-header">
                        <h5 class="modal-title">{self.title}</h5>
                        <button type="button" class="btn-close" dj-click="dismiss" data-component-id="{self.component_id}" aria-label="Close"></button>
                    </div>
                    <div class="modal-body">
                        {self.body}
                    </div>
                    <div class="modal-footer">
                        <button type="button" class="btn btn-secondary" dj-click="dismiss" data-component-id="{self.component_id}">Close</button>
                    </div>
                </div>
            </div>
        </div>
        <div class="modal-backdrop fade show"></div>
        """
        return html

    def _render_tailwind(self) -> str:
        """Render Tailwind CSS modal"""
        size_map = {
            "sm": "max-w-sm",
            "md": "max-w-md",
            "lg": "max-w-lg",
            "xl": "max-w-xl",
        }
        size_class = size_map.get(self.size, "max-w-md")

        html = f"""
        <div class="fixed inset-0 z-10 overflow-y-auto" id="{self.component_id}">
            <div class="flex min-h-screen items-end justify-center px-4 pt-4 pb-20 text-center sm:block sm:p-0">
                <div class="fixed inset-0 bg-gray-500 bg-opacity-75 transition-opacity" dj-click="dismiss" data-component-id="{self.component_id}"></div>
                <span class="hidden sm:inline-block sm:h-screen sm:align-middle">&#8203;</span>
                <div class="inline-block {size_class} w-full transform overflow-hidden rounded-lg bg-white text-left align-bottom shadow-xl transition-all sm:align-middle">
                    <div class="bg-white px-4 pt-5 pb-4 sm:p-6 sm:pb-4">
                        <div class="flex items-start">
                            <div class="mt-3 w-full text-center sm:mt-0 sm:text-left">
                                <h3 class="text-lg font-medium leading-6 text-gray-900">{self.title}</h3>
                                <div class="mt-2">
                                    <p class="text-sm text-gray-500">{self.body}</p>
                                </div>
                            </div>
                        </div>
                    </div>
                    <div class="bg-gray-50 px-4 py-3 sm:flex sm:flex-row-reverse sm:px-6">
                        <button type="button" dj-click="dismiss" data-component-id="{self.component_id}" class="mt-3 inline-flex w-full justify-center rounded-md border border-gray-300 bg-white px-4 py-2 text-base font-medium text-gray-700 shadow-sm hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2 sm:mt-0 sm:w-auto sm:text-sm">
                            Close
                        </button>
                    </div>
                </div>
            </div>
        </div>
        """
        return html

    def _render_plain(self) -> str:
        """Render plain HTML modal"""
        html = f"""
        <div class="modal" id="{self.component_id}" style="display: block;">
            <div class="modal-dialog">
                <div class="modal-content">
                    <div class="modal-header">
                        <h3>{self.title}</h3>
                        <button type="button" dj-click="dismiss" data-component-id="{self.component_id}">×</button>
                    </div>
                    <div class="modal-body">
                        {self.body}
                    </div>
                    <div class="modal-footer">
                        <button type="button" dj-click="dismiss" data-component-id="{self.component_id}">Close</button>
                    </div>
                </div>
            </div>
        </div>
        <div class="modal-backdrop"></div>
        """
        return html
