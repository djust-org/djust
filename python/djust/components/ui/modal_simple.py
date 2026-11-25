"""
Modal component for djust.

Simple stateless modal dialog (Bootstrap 5).

One renderer (#4002): ``_render_custom`` returns the markup of ``ModalComponent``, built from
this class's arguments (the mapping is in the class docstring), so the widget has one renderer.
"""

from typing import Any, Optional

from ..base import Component

from django.utils.html import conditional_escape
from djust.components._alias_deprecation import warn_alias

from ._twin import plain_text, render_twin
from .modal import ModalComponent


class Modal(Component):
    """
    Modal dialog component, drawn by ``ModalComponent``.

    Args:
        title: Modal title text (escaped)
        body: Modal body content (markup: escaped unless marked safe)
        footer: Optional footer content (markup: escaped unless marked safe). Without one,
            ``ModalComponent`` draws its default "Close" button.
        size: Modal size (sm, md, lg, xl)
        centered: Deprecated (#4002): ``ModalComponent`` has no centred dialog; ``True`` warns
            and draws the default dialog.
        dismissable: Deprecated (#4002): ``ModalComponent`` always draws its close button;
            ``False`` warns and the button is still drawn.
        id: Modal ID (``ModalComponent``'s ``component_id``; defaults to ``"modal"``)
        show: Whether the modal is shown. Hidden (the default), Bootstrap draws it as
            ``modal fade`` with no backdrop, for ``data-bs-toggle="modal"`` to open; Tailwind
            and plain draw nothing.

    It is rendered ``standalone``: no view receives the twin's ``dismiss``, so under Bootstrap
    the close controls are ``data-bs-dismiss="modal"``.

    Example:
        >>> modal = Modal(
        ...     title="Confirm Action",
        ...     body="Are you sure you want to continue?",
        ...     footer=mark_safe('<button class="btn btn-primary">Confirm</button>'),
        ...     id="confirm",
        ... )
        >>> modal.render()
        '... <div class="modal fade" id="confirm" tabindex="-1" aria-hidden="true"> ...'
    """

    # #3932: parameters the constructor already ``conditional_escape``s (a plain string is
    # escaped, a marked-safe one is developer markup): printed as given.
    markup_slots = frozenset({"body", "footer"})

    def __init__(
        self,
        body: str,
        id: Optional[str] = None,
        title: Optional[str] = None,
        footer: Optional[str] = None,
        size: str = "md",
        centered: bool = False,
        dismissable: bool = True,
        show: bool = False,
    ) -> None:
        # ``body`` and ``footer`` are markup: HTML-escaped here unless they were marked safe.
        body = conditional_escape(body)
        if footer is not None:
            footer = conditional_escape(footer)

        super().__init__(
            body=body,
            id=id,
            title=title,
            footer=footer,
            size=size,
            centered=centered,
            dismissable=dismissable,
            show=show,
        )

        # Declared for type checkers; the base class already set each from the kwargs.
        self.body = body
        self.title = title
        self.footer = footer
        self.size = size
        self.centered = centered
        self.dismissable = dismissable
        self.show = show

        self._warn_unmapped()

    def _warn_unmapped(self) -> None:
        """Warn for an argument value the twin has no equivalent for (#4002).

        ``stacklevel=5`` points at the caller of ``__init__`` / ``update``.
        """
        if self.centered:
            warn_alias(
                "Modal", "centered", "the default dialog (it draws no centred one)", stacklevel=5
            )
        if not self.dismissable:
            warn_alias(
                "Modal", "dismissable", "the close button (it is always drawn)", stacklevel=5
            )

    def update(self, **kwargs: Any) -> Component:
        """Update the properties, warning again for an argument the twin cannot draw."""
        super().update(**kwargs)
        self._warn_unmapped()
        return self

    def _render_custom(self) -> str:
        return render_twin(
            ModalComponent,
            self.id,
            title=plain_text(self.title),
            # Escaped again here, not only in ``__init__``: ``update(body=...)`` or an attribute
            # write puts a plain string back (a marked-safe one passes unchanged).
            body=conditional_escape(self.body),
            footer=conditional_escape(self.footer) if self.footer else "",
            size=self.size,
            show=bool(self.show),
            standalone=True,
        )
