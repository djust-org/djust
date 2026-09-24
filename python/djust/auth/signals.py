"""Backend-neutral account signals (ADR-039). Every account backend sends these."""

from django.dispatch import Signal

#: Sent after a new account is created. Arguments: ``request``, ``user``.
user_signed_up = Signal()
#: Sent when a user confirms an email address. Arguments: ``request``, ``user``, ``email``.
email_verified = Signal()
