# core/file_manager.py
import os
import sys
import tempfile
from pathlib import Path

def _get_onedrive_aware_desktop() -> Path:
    """
    Detects the current Windows Desktop path, handling OneDrive redirection.
    Falls back to Path.home() / "Desktop" if registry query or OneDrive path fails.
    """
    if os.name == "nt":
        try:
            import winreg
            # User Shell Folders stores redirected Shell Folder paths
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
            )
            val, _ = winreg.QueryValueEx(key, "Desktop")
            winreg.CloseKey(key)
            expanded = os.path.expandvars(val)
            p = Path(expanded)
            if p.exists() and os.access(p, os.W_OK):
                return p
        except Exception:
            pass

    # OneDrive environment variable fallback
    onedrive = os.environ.get("OneDrive")
    if onedrive:
        p = Path(onedrive) / "Desktop"
        if p.exists() and os.access(p, os.W_OK):
            return p

    # Standard fallback
    fallback = Path.home() / "Desktop"
    if fallback.exists() and os.access(fallback, os.W_OK):
        return fallback

    return Path.home()

DESKTOP_PATH = _get_onedrive_aware_desktop()

def get_desktop_path() -> Path:
    """Returns the verified, OneDrive-aware Desktop path."""
    return DESKTOP_PATH

def save_to_desktop(filename: str) -> Path:
    """Returns the absolute path to save a file on the Desktop."""
    return DESKTOP_PATH / Path(filename).name

def is_desktop_available() -> bool:
    """Verifies the Desktop path exists and is writable."""
    try:
        if not DESKTOP_PATH.exists():
            return False
        # Touch test file
        test_file = DESKTOP_PATH / ".nexus_write_test"
        test_file.touch()
        test_file.unlink()
        return True
    except Exception:
        return False

def resolve_save_path(path_str: str, default_to_desktop: bool = True) -> Path:
    """
    Resolves any path from input.
    Supports shortcuts (desktop, downloads, documents, home, temp).
    If relative or just a filename, defaults to Desktop.
    """
    if not path_str or path_str.strip() == "":
        return DESKTOP_PATH if (default_to_desktop and is_desktop_available()) else Path.cwd()

    path_str = path_str.strip()
    shortcuts = {
        "desktop":   DESKTOP_PATH,
        "downloads": Path.home() / "Downloads",
        "documents": Path.home() / "Documents",
        "pictures":  Path.home() / "Pictures",
        "music":     Path.home() / "Music",
        "videos":    Path.home() / "Videos",
        "home":      Path.home(),
        "temp":      Path(tempfile.gettempdir()),
    }

    normalized = path_str.replace("\\", "/")
    parts = normalized.split("/")
    first = parts[0].lower()

    if first in shortcuts:
        base = shortcuts[first]
        if len(parts) > 1:
            return base / Path(*parts[1:])
        return base

    p = Path(path_str).expanduser()
    if p.is_absolute():
        return p

    # Default relative paths to Desktop
    if default_to_desktop:
        if is_desktop_available():
            return DESKTOP_PATH / p
        else:
            return Path.home() / p

    return Path.cwd() / p
