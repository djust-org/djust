"""
HandlerMixin - Handler metadata extraction for LiveView.
"""

import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class HandlerMixin:
    """Handler extraction: _extract_handler_metadata."""

    # Lazily populated cache; initialized to None in LiveView.__init__ and
    # filled on first _extract_handler_metadata() call (guarded by `is not None`).
    _handler_metadata: Optional[Dict[str, Dict[str, Any]]] = None

    def _extract_handler_metadata(self) -> Dict[str, Dict[str, Any]]:
        """
        Extract decorator metadata from all event handlers.

        Returns:
            Dictionary mapping handler names to their decorator metadata.
        """
        if self._handler_metadata is not None:
            logger.debug(
                "[LiveView] Using cached handler metadata for %s (%s handlers)",
                self.__class__.__name__,
                len(self._handler_metadata),
            )
            return self._handler_metadata

        logger.debug("[LiveView] Extracting handler metadata for %s", self.__class__.__name__)
        from .._parameter_metadata import handler_metadata, published_handlers

        # The discovery dispatch uses (ADR-037 D1); no property is evaluated.
        metadata = {
            name: handler_metadata(method) for name, method in published_handlers(self).items()
        }

        self._handler_metadata = metadata
        logger.debug(
            "[LiveView] Extracted %d decorated handlers, caching for future use", len(metadata)
        )

        return metadata
