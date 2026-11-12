from __future__ import annotations

import os
import sys
from pathlib import Path

APP_VENDOR = "MohamedTamer"
APP_DIR_NAME = "CafePOS"
DATA_DIR_ENV_VAR = "CAFE_POS_DATA_DIR"


def get_bundle_assets_dir() -> Path:
    """Return read-only bundled assets path (works both in source tree and PyInstaller EXE)."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "cafe_pos" / "assets"
    return Path(__file__).resolve().parent / "assets"


def get_asset_path(filename: str) -> Path:
    return get_bundle_assets_dir() / filename


def get_translations_dir() -> Path:
    """Return read-only bundled translations path (works in source tree and PyInstaller EXE)."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "cafe_pos" / "translations"
    return Path(__file__).resolve().parent / "translations"


def get_data_dir() -> Path:
    """Return writable per-user Windows AppData directory (never inside bundled EXE)."""
    override = os.environ.get(DATA_DIR_ENV_VAR, "").strip()
    if override:
        data_dir = Path(override)
    elif os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
        if local_app_data:
            base = Path(local_app_data)
        else:
            base = Path.home() / "AppData" / "Local"
        data_dir = base / APP_VENDOR / APP_DIR_NAME
    else:
        xdg_data = os.environ.get("XDG_DATA_HOME", "").strip()
        base = Path(xdg_data) if xdg_data else (Path.home() / ".local" / "share")
        data_dir = base / APP_VENDOR / APP_DIR_NAME

    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def get_database_path() -> Path:
    return get_data_dir() / "cafe_pos.db"


def get_logos_dir() -> Path:
    logos_dir = get_data_dir() / "logos"
    logos_dir.mkdir(parents=True, exist_ok=True)
    return logos_dir


def get_product_images_dir() -> Path:
    images_dir = get_data_dir() / "product_images"
    images_dir.mkdir(parents=True, exist_ok=True)
    return images_dir


def get_backups_dir() -> Path:
    backups_dir = get_data_dir() / "backups"
    backups_dir.mkdir(parents=True, exist_ok=True)
    return backups_dir


def get_safety_backups_dir() -> Path:
    safety_dir = get_backups_dir() / "safety"
    safety_dir.mkdir(parents=True, exist_ok=True)
    return safety_dir


def get_default_receipts_dir() -> Path:
    receipts_dir = get_data_dir() / "receipts"
    receipts_dir.mkdir(parents=True, exist_ok=True)
    return receipts_dir


def get_receipts_dir(custom_dir: str = "") -> Path:
    cleaned = (custom_dir or "").strip()
    if cleaned:
        try:
            candidate = Path(cleaned).expanduser()
            candidate.mkdir(parents=True, exist_ok=True)
            if candidate.is_dir():
                return candidate
        except OSError:
            pass
    return get_default_receipts_dir()


def get_exports_dir() -> Path:
    exports_dir = get_data_dir() / "exports"
    exports_dir.mkdir(parents=True, exist_ok=True)
    return exports_dir
