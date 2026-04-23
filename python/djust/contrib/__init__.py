"""djust.contrib — optional integrations and non-core extensions.

Modules under ``djust.contrib`` are shipped with djust but are feature-
gated behind optional extras. They import cleanly without the extras
installed, but raise ``ImportError`` with a clear hint pointing at the
right ``pip install djust[<extra>]`` command when a feature that needs
the extra is actually exercised.
"""
