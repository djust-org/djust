"""The stored-session contract of the ADR-038 E5 matrix (no Playwright here).

Explicit exposure is about the CLIENT: nothing outside ``get_context_data()``
or a declared ``state()`` field may be rendered, sent, or set from the browser.
The server-side session is a different destination with its own contract
(ADR-038 amendment, ``docs/adr/notes/038-amendment-automatic-server-state.md``,
"The stored session is not a client destination"; #4107):

* A view's own state is saved in the CALLER'S OWN session: its undeclared
  public attributes and the ``_private`` attributes ``mount()`` created MAY be
  there, and only inside that view's saved-state envelope (``_djust_auto_...``),
  under ``values`` and ``private``. Anywhere else in the session is a failure.
* The envelope is bound to the session, user, tenant and route (``binding``)
  and expires (``expires``). Another session, user or tenant reads nothing
  (pinned by ``test_state_is_bound_to_the_user_and_the_route`` and
  ``test_a_tab_id_cannot_reach_another_users_or_sessions_state`` in
  ``python/djust/tests/test_automatic_state_4107.py``).
* What is on the floor is NEVER in the session, under any policy or transport:
  attribute names on the sensitive-field list (``password``, ...), request and
  user objects, secret form controls. The matrix carries one floor value,
  ``E5_FLOOR_SENTINEL`` under ``self.password``, on both demo views.

``python/djust/tests/test_exposure_session_contract_4107.py`` runs this
contract, and its gate-offs, without a browser.
"""

from __future__ import annotations

import json
from typing import Any, List

#: Never to any client destination (and, before #4107, never to the session).
CLIENT_SENTINELS = ("E5_UNDECLARED_SENTINEL", "E5_PRIVATE_SENTINEL")
#: Held under a floor name (``self.password``): never anywhere, the session included.
FLOOR_SENTINEL = "E5_FLOOR_SENTINEL"
ENVELOPE_PREFIX = "_djust_auto_"
#: Where each client sentinel may rest inside a saved-state envelope.
ALLOWED = {
    "E5_UNDECLARED_SENTINEL": ("values", "secret_note"),
    "E5_PRIVATE_SENTINEL": ("private", "_private_note"),
}


def _without_allowed(row: dict) -> Any:
    """The session row with each sentinel's one allowed resting place removed."""
    rest: dict = {}
    for key, value in row.items():
        if not (
            isinstance(key, str) and key.startswith(ENVELOPE_PREFIX) and isinstance(value, dict)
        ):
            rest[key] = value
            continue
        envelope = {name: item for name, item in value.items()}
        for sentinel, (section, name) in ALLOWED.items():
            held = envelope.get(section)
            if isinstance(held, dict) and held.get(name) == sentinel:
                envelope[section] = {k: v for k, v in held.items() if k != name}
        rest[key] = envelope
    return rest


def stored_session_failures(label: str, policy: str, rows: List[dict]) -> List[str]:
    """What the decoded session rows of one flow get wrong, as messages."""
    failures: List[str] = []
    dumped = json.dumps(rows, default=str)
    if FLOOR_SENTINEL in dumped:
        failures.append(f"{label}: {FLOOR_SENTINEL} (a floor value) in the stored session")
    if policy != "explicit":
        # The legacy policy is the matrix's control, not a subject of the
        # explicit contract: only the floor is asserted for it.
        return failures
    outside = json.dumps([_without_allowed(row) for row in rows], default=str)
    for sentinel in CLIENT_SENTINELS:
        if sentinel in outside:
            failures.append(
                f"{label}: {sentinel} in the stored session outside the view's saved-state "
                "envelope (values/private)"
            )
    envelopes = [
        value
        for row in rows
        for key, value in row.items()
        if isinstance(key, str) and key.startswith(ENVELOPE_PREFIX) and isinstance(value, dict)
    ]
    for envelope in envelopes:
        binding, expires = envelope.get("binding"), envelope.get("expires")
        if not (isinstance(binding, str) and len(binding) == 64 and isinstance(expires, int)):
            failures.append(f"{label}: a saved-state envelope is not bound or has no lifetime")
    # Pinned positively: the view's state IS saved server-side, for its own caller.
    for sentinel, (section, name) in ALLOWED.items():
        if not any(
            isinstance(envelope.get(section), dict) and envelope[section].get(name) == sentinel
            for envelope in envelopes
        ):
            failures.append(
                f"{label}: the view's own state ({name}) is not in its saved-state envelope"
            )
    return failures
