"""Windows ACL trust check for discovered AI inventory entry points."""

import ctypes
from ctypes import wintypes
from typing import Iterable


_BROAD_SIDS = {"S-1-1-0", "S-1-5-11", "S-1-5-32-545"}
# GENERIC_ALL | GENERIC_WRITE | DELETE, WRITE_DAC, WRITE_OWNER | FILE_WRITE_DATA
# (add file), FILE_APPEND_DATA (add subdirectory), FILE_WRITE_EA,
# FILE_DELETE_CHILD (replace a file in a directory), FILE_WRITE_ATTRIBUTES.
_WRITE_MASK = 0x10000000 | 0x40000000 | 0x000D0000 | 0x00000156


def _acl_is_trusted(
    owner_sid: str, current_owner_sids: "str | Iterable[str]", aces: list[tuple[int, str, int]]
) -> bool:
    """Require an owner this process would itself assign (its user, or its
    token's default owner: BUILTIN\\Administrators for an elevated
    administrator) and no broad write-granting ACE."""
    trusted = (
        {current_owner_sids} if isinstance(current_owner_sids, str) else set(current_owner_sids)
    )
    if not owner_sid or owner_sid not in trusted:
        return False
    if any(ace_type not in (0, 1) for ace_type, _, _ in aces):
        return False
    return not any(
        ace_type == 0 and sid in _BROAD_SIDS and mask & _WRITE_MASK for ace_type, sid, mask in aces
    )


def _path_acl_is_trusted(path: str) -> bool:
    """Read a file or directory owner and DACL; reject every API failure."""
    if not hasattr(ctypes, "WinDLL"):
        return False

    class SID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]

    class TOKEN_USER(ctypes.Structure):
        _fields_ = [("User", SID_AND_ATTRIBUTES)]

    class TOKEN_OWNER(ctypes.Structure):
        _fields_ = [("Owner", ctypes.c_void_p)]

    class ACL(ctypes.Structure):
        _fields_ = [
            ("AclRevision", ctypes.c_ubyte),
            ("Sbz1", ctypes.c_ubyte),
            ("AclSize", wintypes.WORD),
            ("AceCount", wintypes.WORD),
            ("Sbz2", wintypes.WORD),
        ]

    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi.GetNamedSecurityInfoW.restype = wintypes.DWORD
    advapi.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi.OpenProcessToken.restype = wintypes.BOOL
    advapi.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi.GetTokenInformation.restype = wintypes.BOOL
    advapi.EqualSid.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    advapi.EqualSid.restype = wintypes.BOOL
    advapi.GetAce.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
    advapi.GetAce.restype = wintypes.BOOL
    advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    advapi.ConvertSidToStringSidW.restype = wintypes.BOOL
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p

    def sid_string(sid: int) -> str:
        value = wintypes.LPWSTR()
        if not advapi.ConvertSidToStringSidW(sid, ctypes.byref(value)):
            raise OSError(ctypes.get_last_error())
        try:
            return value.value
        finally:
            kernel.LocalFree(ctypes.cast(value, ctypes.c_void_p))

    descriptor = ctypes.c_void_p()
    owner = ctypes.c_void_p()
    dacl = ctypes.c_void_p()
    token = wintypes.HANDLE()
    try:
        result = advapi.GetNamedSecurityInfoW(
            path,
            1,
            1 | 4,
            ctypes.byref(owner),
            None,
            ctypes.byref(dacl),
            None,
            ctypes.byref(descriptor),
        )
        if result or not owner.value or not dacl.value:
            return False
        if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
            return False

        def token_information(info_class: int) -> "ctypes.Array[ctypes.c_char]":
            size = wintypes.DWORD()
            advapi.GetTokenInformation(token, info_class, None, 0, ctypes.byref(size))
            if not size.value:
                raise OSError(ctypes.get_last_error())
            buffer = ctypes.create_string_buffer(size.value)
            if not advapi.GetTokenInformation(token, info_class, buffer, size, ctypes.byref(size)):
                raise OSError(ctypes.get_last_error())
            return buffer

        user_buffer = token_information(1)  # TokenUser
        owner_buffer = token_information(4)  # TokenOwner: the owner new objects get
        user = ctypes.cast(user_buffer, ctypes.POINTER(TOKEN_USER)).contents.User.Sid
        default_owner = ctypes.cast(owner_buffer, ctypes.POINTER(TOKEN_OWNER)).contents.Owner
        if not (advapi.EqualSid(owner, user) or advapi.EqualSid(owner, default_owner)):
            return False
        aces = []
        for index in range(ctypes.cast(dacl, ctypes.POINTER(ACL)).contents.AceCount):
            ace = ctypes.c_void_p()
            if not advapi.GetAce(dacl, index, ctypes.byref(ace)):
                return False
            header = ctypes.string_at(ace, 8)
            ace_type = header[0]
            if ace_type not in (0, 1):
                return False  # unfamiliar ACE shape: cannot locate its SID safely
            mask = int.from_bytes(header[4:8], "little")
            aces.append((ace_type, sid_string(ace.value + 8), mask))
        return _acl_is_trusted(
            sid_string(owner), {sid_string(user), sid_string(default_owner)}, aces
        )
    except (OSError, ValueError, ctypes.ArgumentError):
        return False
    finally:
        if token.value:
            kernel.CloseHandle(token)
        if descriptor.value:
            kernel.LocalFree(descriptor)


def windows_path_is_trusted(path: str) -> bool:
    """Require both manage.py and its containing directory to be trusted."""
    import os

    return _path_acl_is_trusted(path) and _path_acl_is_trusted(os.path.dirname(path))
