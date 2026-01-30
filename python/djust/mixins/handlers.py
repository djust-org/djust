"""
HandlerMixin - Handler metadata extraction for LiveView.
"""

import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)


class HandlerMixin:
    """Handler extraction: _extract_handler_metadata."""

    def _extract_handler_metadata(self) -> Dict[str, Dict[str, Any]]:
        """
        Extract decorator metadata from all event handlers.

        Returns:
            Dictionary mapping handler names to their decorator metadata.
        """
        if self._handler_metadata is not None:
            logger.debug(
                f"[LiveView] Using cached handler metadata for {self.__class__.__name__} "
                f"({len(self._handler_metadata)} handlers)"
            )
            return self._handler_metadata

        logger.debug(f"[LiveView] Extracting handler metadata for {self.__class__.__name__}")
        metadata = {}

        for name in dir(self):
            if name.startswith("_"):
                continue

            try:
                method = getattr(self, name)

                if not callable(method):
                    continue

                if hasattr(method, "_djust_decorators"):
                    metadata[name] = method._djust_decorators
                    logger.debug(
                        f"[LiveView]   Found decorated handler: {name} -> "
                        f"{list(method._djust_decorators.keys())}"
                    )

            except (AttributeError, TypeError):
                continue

        self._handler_metadata = metadata
        logger.debug(
            f"[LiveView] Extracted {len(metadata)} decorated handlers, caching for future use"
        )

        return metadata
