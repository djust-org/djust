"""
Draft Mode functionality for djust LiveView.

Provides automatic draft saving to localStorage for forms and editors.
"""

from typing import Optional


class DraftModeMixin:
    """
    Mixin for LiveView classes to enable automatic draft saving to localStorage.

    Usage:
        class ArticleEditorView(DraftModeMixin, LiveView):
            template_name = 'article_editor.html'
            draft_enabled = True  # Enable draft mode (default: True)
            draft_key = 'article_editor'  # Custom draft key (optional)

            def mount(self, request, article_id=None):
                self.article_id = article_id
                # Draft will auto-restore on page load

            def save_article(self, title: str, content: str):
                # Save article to database
                article = Article.objects.create(title=title, content=content)
                # Clear draft on successful save
                self.clear_draft()
                return {'success': True}

    Features:
        - Auto-save every 500ms (debounced)
        - Auto-restore on page load
        - Clear draft on successful form submit
        - Works with any form fields or contenteditable elements

    Client-side JavaScript:
        - Monitors all input/textarea/contenteditable elements
        - Saves to localStorage automatically
        - Restores on page load
        - Can be controlled via data-draft-* attributes
    """

    # Override these in your view class
    draft_enabled: bool = True
    draft_key: Optional[str] = None

    def get_draft_key(self) -> str:
        """
        Get the draft key for localStorage.

        Override this method to customize the draft key based on view state.

        Returns:
            Draft key string (e.g., 'article_editor_42')
        """
        if self.draft_key:
            return self.draft_key

        # Default: Use view class name
        return f"{self.__class__.__name__.lower()}_draft"

    def clear_draft(self):
        """
        Clear the draft from localStorage.

        Call this method after successful form submission or when the draft
        should be discarded (e.g., user explicitly deletes it).

        This sets a flag that the client-side JavaScript reads to clear localStorage.
        """
        # Set a flag in the response that client-side JS will read
        if not hasattr(self, "_draft_clear_requested"):
            self._draft_clear_requested = True

    def get_context_data(self, **kwargs):
        """
        Add draft configuration to template context.

        This is called automatically by LiveView. Override to customize context.
        """
        context = super().get_context_data(**kwargs) if hasattr(super(), "get_context_data") else {}

        # Add draft configuration
        context["draft_enabled"] = self.draft_enabled
        context["draft_key"] = self.get_draft_key()

        # Add clear flag if requested
        if hasattr(self, "_draft_clear_requested") and self._draft_clear_requested:
            context["draft_clear"] = True
            self._draft_clear_requested = False  # Reset flag

        return context
