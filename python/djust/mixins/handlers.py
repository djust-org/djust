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
                    from ..validation import (
                        get_handler_parameter_policy,
                        get_strict_handler_contract,
                    )

                    if get_handler_parameter_policy(method) == "strict":
                        # No server default values or mutable shared decorator
                        # dictionaries cross into strict client metadata.
                        metadata[name] = dict(method._djust_decorators)
                        key = (
                            "event_handler"
                            if "event_handler" in metadata[name]
                            else "server_function"
                        )
                        if key in metadata[name]:
                            contract = get_strict_handler_contract(method)
                            parameters = list(contract.metadata())
                            item = dict(metadata[name][key])
                            item.update(
                                parameter_policy="strict",
                                params=parameters,
                                param_names=[p["name"] for p in parameters],
                                required=[p["name"] for p in parameters if p["required"]],
                                optional=[p["name"] for p in parameters if not p["required"]],
                            )
                            metadata[name][key] = item
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
