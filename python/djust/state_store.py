"""Where a view's saved state is kept: a pluggable store (#4132).

By default the state a view keeps between requests (``djust._automatic_state``)
rests in the Django session, readable by whoever can read the session store.
One setting moves it::

    # settings.py
    DJUST_STATE_STORE = "djust.state_store.SealedSessionViewStateStore"
    DJUST_STATE_STORE_KEYS = [os.environ["DJUST_STATE_KEY"]]

A store only moves opaque bytes under a key djust builds. djust keeps the
state envelope and every check it makes on it: the binding to the session,
user, tenant, route and tab, the expiry, the size limits and which names may
be put back. The bytes a store returns are checked again before anything is
restored, so a store that returns another caller's bytes, old bytes or garbage
yields "nothing saved" (``mount()`` runs), never another caller's state.

What stays in the session, per tab, is an index entry with no values: the
store's name, the page, the binding digest, the tab id, the user and tenant
identifiers, when it was written and expires, its size, and a random stamp
naming the one write the session vouches for. The tab cap and the size
budgets work from it, so a store never has to list its contents.

Declared ``persist="server"`` fields are another envelope and do not go through
the store in this version.

Writing a store::

    from djust.state_store import ViewStateStore

    class TableStateStore(ViewStateStore):
        def load(self, key):
            row = ViewState.objects.filter(pk=key.id).first()
            return bytes(row.payload) if row else None

        def save(self, key, payload, *, expires):
            ViewState.objects.update_or_create(
                pk=key.id,
                defaults={"payload": payload, "expires": expires,
                          "user": key.user_id, "tenant": key.tenant_id},
            )

        def delete(self, keys):
            ViewState.objects.filter(pk__in=[key.id for key in keys]).delete()

        def clear(self, *, expired_only):
            # The sweep: djust deletes a state when it evicts it or `djust clear`
            # removes it, but never on logout or when a session ends. Run
            # `djust clear` on a schedule (or this query) to drop expired rows.
            rows = ViewState.objects.all()
            if expired_only:
                rows = rows.filter(expires__lte=int(time.time()))
            return rows.delete()[0]

``load``, ``save`` and ``delete`` are sync and run on the Django thread, each
as a ``"state_store"`` step of ``LIVEVIEW_CONFIG["sync_turn_wrapper"]``
(``djust.sync_turn``), so a wrapper that opens a caller-scoped transaction is
open around them. ``djust clear`` and ``djust stats`` call the store inside
the wrapper too, with ``view=None`` and ``turn=None``.

djust never deletes a state because a user logged out or a session ended
(``session.flush()``): what is left is unreadable without its index entry and
is removed by the store's own ``expires`` sweep.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from django.core.exceptions import ImproperlyConfigured

logger = logging.getLogger("djust.state_store")

__all__ = [
    "KEYS_SETTING",
    "SETTING",
    "STRICT_SETTING",
    "SealedSessionViewStateStore",
    "SessionKeptStore",
    "SessionViewStateStore",
    "StateKey",
    "StateStoreConfigurationError",
    "StateStoreError",
    "ViewStateStore",
    "configured_store",
    "generate_key",
    "store_clear",
    "store_stats",
    "strict",
]

#: The dotted path of the project's store. Unset: the session, as before.
SETTING = "DJUST_STATE_STORE"
#: The sealed store's keys: the first seals, every one opens.
KEYS_SETTING = "DJUST_STATE_STORE_KEYS"
#: ``True``: an event whose state cannot be stored is refused, not served.
STRICT_SETTING = "DJUST_STATE_STORE_STRICT"


class StateStoreConfigurationError(ImproperlyConfigured):
    """The store named in settings cannot be used as configured."""


class StateStoreError(Exception):
    """An event's state could not be stored and ``DJUST_STATE_STORE_STRICT``
    is on: the event is refused. Carries no value and no store exception."""

    user_message = "State unavailable. Please reload the page."


@dataclass(frozen=True)
class StateKey:
    """What one view state is stored under. Built by djust, never by a client.

    ``binding`` is the digest of the session, user, tenant and route the state
    belongs to; ``page`` the view class and route; ``tab`` the tab the page
    load was given. :attr:`id` joins the three and is unique: use it as the
    primary key. ``session`` is the session key; ``user_id`` and ``tenant_id``
    are the caller in the clear (``"anonymous"``, ``"user:int:5"``, ``"none"``,
    ``"tenant:str:acme"``), for a store that scopes a row to its caller.
    """

    session: str
    binding: str
    page: str
    tab: str
    user_id: str
    tenant_id: str

    @property
    def id(self) -> str:
        """One opaque identifier for this state (64 hex characters)."""
        joined = "|".join((self.binding, self.page, self.tab)).encode("utf-8")
        return hashlib.sha256(joined).hexdigest()


class ViewStateStore:
    """A place that keeps view-state bytes. Subclass and name it in
    ``DJUST_STATE_STORE``; djust builds one instance per process.

    A store never needs to read the payload and must not change it. It may
    ignore any part of the key; :attr:`StateKey.id` is unique.

    Optional, for ``djust stats`` and ``djust clear``:
    ``stats() -> dict`` and ``clear(*, expired_only: bool) -> int``.
    """

    def load(self, key: StateKey) -> Optional[bytes]:
        """The bytes last saved under ``key``, or ``None``."""
        raise NotImplementedError

    def save(self, key: StateKey, payload: bytes, *, expires: int) -> None:
        """Keep ``payload`` under ``key``, replacing what is there.
        ``expires`` is a Unix time after which djust will not read it back."""
        raise NotImplementedError

    def delete(self, keys: Iterable[StateKey]) -> None:
        """Forget these states. An unknown key is not an error."""
        raise NotImplementedError


class SessionViewStateStore(ViewStateStore):
    """The default: the state rests in the Django session, as it always has.

    Naming it in ``DJUST_STATE_STORE`` is the same as naming nothing.
    """


class SessionKeptStore(ViewStateStore):
    """A store whose bytes stay in the session entry, transformed.

    djust keeps what :meth:`seal` returns beside the index entry and hands it
    back to :meth:`open`; there is nothing to load, save or delete elsewhere.
    """

    def seal(self, key: StateKey, payload: bytes) -> str:
        """``payload`` as text that is safe to rest in the session."""
        raise NotImplementedError

    def open(self, key: StateKey, text: str) -> Optional[bytes]:
        """The payload :meth:`seal` was given, or ``None`` when ``text`` is
        not something this store sealed for ``key``."""
        raise NotImplementedError

    def load(self, key: StateKey) -> Optional[bytes]:
        return None

    def save(self, key: StateKey, payload: bytes, *, expires: int) -> None:
        return None

    def delete(self, keys: Iterable[StateKey]) -> None:
        return None


# ---------------------------------------------------------------------------
# The built-in sealed store
# ---------------------------------------------------------------------------

_KEY_BYTES = 32
_NONCE_BYTES = 12
_SEALED_PREFIX = "s1."
_INSTALL = (
    "the sealed state store needs the `cryptography` package: pip install 'djust[sealed-state]'"
)


def generate_key() -> str:
    """A new key for ``DJUST_STATE_STORE_KEYS`` (32 random bytes, as text).

    ``python -c "from djust.state_store import generate_key; print(generate_key())"``
    """
    return base64.urlsafe_b64encode(os.urandom(_KEY_BYTES)).decode("ascii")


def _key_bytes(value: Any) -> bytes:
    """One configured key as 32 bytes. The key never appears in an error."""
    if isinstance(value, (bytes, bytearray)):
        raw = bytes(value)
    elif isinstance(value, str):
        try:
            padded = value.strip() + "=" * (-len(value.strip()) % 4)
            raw = base64.b64decode(padded, altchars=b"-_", validate=True)
        except ValueError:
            raise StateStoreConfigurationError(
                "%s holds a key that is not base64 text. Each key is 32 bytes, or their "
                "base64 text (djust.state_store.generate_key())." % KEYS_SETTING
            ) from None
    else:
        raise StateStoreConfigurationError(
            "%s holds a key of type %s. Each key is 32 bytes, or their base64 text "
            "(djust.state_store.generate_key())." % (KEYS_SETTING, type(value).__name__)
        )
    if len(raw) != _KEY_BYTES:
        raise StateStoreConfigurationError(
            "%s holds a key of %d bytes. Each key is exactly 32 bytes "
            "(djust.state_store.generate_key())." % (KEYS_SETTING, len(raw))
        )
    return raw


def sealing_keys() -> List[bytes]:
    """The configured keys, first one first. Never ``SECRET_KEY``: a project
    that names the sealed store states its key."""
    from django.conf import settings

    configured = getattr(settings, KEYS_SETTING, None)
    if callable(configured):
        configured = configured()
    if isinstance(configured, (bytes, bytearray, str)):
        configured = [configured]
    if not isinstance(configured, (list, tuple)) or not configured:
        raise StateStoreConfigurationError(
            "The sealed state store needs %s: a list of keys, the first seals and every one "
            "opens. There is no fallback to SECRET_KEY. Make a key with "
            "djust.state_store.generate_key()." % KEYS_SETTING
        )
    return [_key_bytes(value) for value in configured]


def _aesgcm() -> Any:
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError:
        raise StateStoreConfigurationError(_INSTALL) from None
    return AESGCM


class SealedSessionViewStateStore(SessionKeptStore):
    """The state encrypted in the session entry (AES-256-GCM).

    A random nonce per write; the state's key (binding, page and tab) is the
    associated data, so sealed text moved to another session, user, tenant,
    route or tab does not open. The first key of ``DJUST_STATE_STORE_KEYS``
    seals; every key opens, so a key is rotated by putting the new one first
    and dropping the old one a state lifetime later.
    """

    def _associated(self, key: StateKey) -> bytes:
        return b"djust-view-state|" + key.id.encode("ascii")

    def seal(self, key: StateKey, payload: bytes) -> str:
        cipher = _aesgcm()(sealing_keys()[0])
        nonce = os.urandom(_NONCE_BYTES)
        sealed = nonce + cipher.encrypt(nonce, payload, self._associated(key))
        return _SEALED_PREFIX + base64.urlsafe_b64encode(sealed).decode("ascii")

    def open(self, key: StateKey, text: str) -> Optional[bytes]:
        if type(text) is not str or not text.startswith(_SEALED_PREFIX):
            return None
        try:
            sealed = base64.b64decode(
                text[len(_SEALED_PREFIX) :].encode("ascii"), altchars=b"-_", validate=True
            )
        except ValueError:
            return None
        if len(sealed) <= _NONCE_BYTES:
            return None
        from cryptography.exceptions import InvalidTag

        cipher_class = _aesgcm()
        nonce, body = sealed[:_NONCE_BYTES], sealed[_NONCE_BYTES:]
        for secret in sealing_keys():
            try:
                return bytes(cipher_class(secret).decrypt(nonce, body, self._associated(key)))
            except InvalidTag:
                continue
        return None


# ---------------------------------------------------------------------------
# The project's store
# ---------------------------------------------------------------------------


class _UnusableStore(ViewStateStore):
    """Stands for a store that could not be built: every call fails with the
    configuration error, so the failure is handled where a store that is down
    is handled. State is never written to the session in the clear instead."""

    def __init__(self, error: StateStoreConfigurationError) -> None:
        self.error = error

    def load(self, key: StateKey) -> Optional[bytes]:
        raise self.error

    def save(self, key: StateKey, payload: bytes, *, expires: int) -> None:
        raise self.error

    def delete(self, keys: Iterable[StateKey]) -> None:
        raise self.error


#: The store built for the configured value, as ``{"store": (raw, store)}``:
#: rebuilt when the setting changes (a test's ``settings`` override included).
_RESOLVED: Dict[str, Tuple[Any, Optional[ViewStateStore]]] = {}


def build_store(raw: Any) -> Optional[ViewStateStore]:
    """The store ``raw`` names, or ``None`` for the default.

    Raises :class:`StateStoreConfigurationError` for a path that does not
    import or does not name a :class:`ViewStateStore` subclass."""
    if raw is None:
        return None
    if isinstance(raw, str):
        from django.utils.module_loading import import_string

        path = raw
        try:
            raw = import_string(path)
        except Exception as error:  # noqa: BLE001 — whatever importing the module raised
            # Not only ImportError: a store module that fails while it is
            # imported (a NameError, a missing setting) is just as unusable,
            # and must not fail every request or the system checks.
            raise StateStoreConfigurationError(
                "%s = %r cannot be imported (%s: %s)."
                % (SETTING, path, type(error).__name__, str(error)[:300])
            ) from None
    if not (isinstance(raw, type) and issubclass(raw, ViewStateStore)):
        raise StateStoreConfigurationError(
            "%s must be the dotted path of a djust.state_store.ViewStateStore subclass." % SETTING
        )
    if raw is SessionViewStateStore:
        return None
    try:
        return raw()
    except Exception:  # noqa: BLE001 — a constructor's message may carry a secret
        raise StateStoreConfigurationError(
            "%s names a store that could not be created (its constructor raised)." % SETTING
        ) from None


def configured_store() -> Optional[ViewStateStore]:
    """The project's store, built once per configured value; ``None`` when
    the state rests in the session (nothing set, or the default named)."""
    try:
        from django.conf import settings

        raw = getattr(settings, SETTING, None)
    except Exception:  # noqa: BLE001 — settings unavailable: nothing is configured
        return None
    if raw is None:
        return None
    cached = _RESOLVED.get("store")
    if cached is not None and cached[0] is raw:
        return cached[1]
    store: Optional[ViewStateStore]
    try:
        store = build_store(raw)
    except StateStoreConfigurationError as error:
        store = _UnusableStore(error)
    _RESOLVED["store"] = (raw, store)
    return store


def store_tag(store: ViewStateStore) -> str:
    """What an index entry says its payload is held by."""
    owner = type(store)
    return "%s.%s" % (owner.__module__, owner.__qualname__)


#: Settings already warned about in this process (warn once, #4132).
_STRICT_WARNED: Set[str] = set()


def strict() -> bool:
    """Whether an event whose state cannot be stored is refused.

    Only ``True`` is strict. Any other value that is not ``False`` is a
    startup error (system check ``djust.C038``), as for the saving switch."""
    try:
        from django.conf import settings

        value = getattr(settings, STRICT_SETTING, False)
    except Exception:  # noqa: BLE001 — settings unavailable: the default
        return False
    if type(value) is not bool and STRICT_SETTING not in _STRICT_WARNED:
        # A server started without system checks never sees C038: say it once
        # per process where the setting is read.
        _STRICT_WARNED.add(STRICT_SETTING)
        logger.warning(
            "djust: %s is a %s, not True or False, so it is read as NOT strict: an event "
            "whose state the state store cannot take is served unsaved. Set it to True "
            "or False (system check djust.C038).",
            STRICT_SETTING,
            type(value).__name__,
        )
    return value is True


def store_stats() -> Optional[Dict[str, Any]]:
    """The store's own figures for ``djust stats``; ``None`` when no store is
    configured or it does not report any."""
    store = configured_store()
    report = getattr(store, "stats", None)
    if store is None or not callable(report):
        return None
    result = report()
    return dict(result) if isinstance(result, dict) else None


def store_clear(*, expired_only: bool) -> Optional[int]:
    """Ask the store to remove what it holds, for ``djust clear``; the count,
    or ``None`` when no store is configured or it cannot clear itself."""
    store = configured_store()
    clear = getattr(store, "clear", None)
    if store is None or not callable(clear):
        return None
    removed = clear(expired_only=expired_only)
    return removed if type(removed) is int else 0
