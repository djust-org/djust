"""A state-store module that fails while it is imported (#4132, test fixture)."""

raise RuntimeError("boom at import")
