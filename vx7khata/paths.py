"""Where the application keeps its data."""
from __future__ import annotations

import os
from pathlib import Path

DB_FILENAME = "khata.db"


def data_dir() -> Path:
    """Per-user data folder. ``VX7_DATA_DIR`` overrides it (portable use / tests)."""
    override = os.environ.get("VX7_DATA_DIR")
    if override:
        base = Path(override)
    elif os.name == "nt":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "VX7 KHATA PRO"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "vx7-khata-pro"
    base.mkdir(parents=True, exist_ok=True)
    return base


def db_path() -> Path:
    return data_dir() / DB_FILENAME


def safety_backup_dir() -> Path:
    p = data_dir() / "safety_backups"
    p.mkdir(parents=True, exist_ok=True)
    return p


# FOLDERID_Downloads = {374DE290-123F-4565-9164-39C4925E467B}
_DOWNLOADS_GUID_FIELDS = (0x374DE290, 0x123F, 0x4565, (0x91, 0x64, 0x39, 0xC4, 0x92, 0x5E, 0x46, 0x7B))


def downloads_dir() -> Path:
    """The current user's Downloads folder (Windows 'Known Folder', so a moved/redirected Downloads is respected)."""
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            class _GUID(ctypes.Structure):
                _fields_ = [("a", ctypes.c_ulong), ("b", ctypes.c_ushort), ("c", ctypes.c_ushort),
                            ("d", ctypes.c_ubyte * 8)]

            guid = _GUID(_DOWNLOADS_GUID_FIELDS[0], _DOWNLOADS_GUID_FIELDS[1], _DOWNLOADS_GUID_FIELDS[2],
                         (ctypes.c_ubyte * 8)(*_DOWNLOADS_GUID_FIELDS[3]))
            out = ctypes.c_wchar_p()
            fn = ctypes.windll.shell32.SHGetKnownFolderPath
            fn.argtypes = [ctypes.POINTER(_GUID), wintypes.DWORD, wintypes.HANDLE, ctypes.POINTER(ctypes.c_wchar_p)]
            if fn(ctypes.byref(guid), 0, None, ctypes.byref(out)) == 0 and out.value:
                found = Path(out.value)
                ctypes.windll.ole32.CoTaskMemFree(out)
                return found
        except Exception:  # noqa: BLE001 - fall back to the usual location
            pass
        return Path(os.environ.get("USERPROFILE") or Path.home()) / "Downloads"
    return Path.home() / "Downloads"


def asset_path(name: str) -> Path:
    """A bundled file in vx7khata/assets (logo, welcome background ...); works from source and from the EXE."""
    return Path(__file__).resolve().parent / "assets" / name
