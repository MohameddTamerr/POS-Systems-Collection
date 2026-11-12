from __future__ import annotations

import faulthandler
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys
import traceback

from PySide6.QtCore import QtMsgType, qInstallMessageHandler

from .paths import get_data_dir

_INITIALIZED = False
_FAULT_FILE = None
_LOGGER: logging.Logger | None = None


def get_logs_dir() -> Path:
    logs_dir = get_data_dir() / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    return logs_dir


def _write_log(level: int, message: str) -> None:
    if _LOGGER is not None:
        try:
            _LOGGER.log(level, message)
        except Exception:
            pass


def install_crash_diagnostics() -> Path | None:
    """Enable faulthandler, Qt warning/critical logging, and persistent traceback logging."""
    global _INITIALIZED, _FAULT_FILE, _LOGGER
    if _INITIALIZED:
        return get_logs_dir() / "cafe_pos_diagnostics.log"

    log_path: Path | None = None
    try:
        logs_dir = get_logs_dir()
        log_path = logs_dir / "cafe_pos_diagnostics.log"
        logger = logging.getLogger("cafe_pos.diagnostics")
        logger.setLevel(logging.WARNING)
        logger.propagate = False
        if not logger.handlers:
            handler = RotatingFileHandler(
                str(log_path),
                maxBytes=1_024 * 1_024,
                backupCount=3,
                encoding="utf-8",
            )
            handler.setFormatter(
                logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
            )
            logger.addHandler(handler)
        _LOGGER = logger

        fault_path = logs_dir / "cafe_pos_fault.log"
        _FAULT_FILE = fault_path.open("a", encoding="utf-8", buffering=1)
        faulthandler.enable(file=_FAULT_FILE, all_threads=True)
    except OSError:
        log_path = None

    def _qt_message_handler(msg_type: QtMsgType, _context, message: str) -> None:
        if msg_type in (QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg):
            lvl = logging.ERROR if msg_type != QtMsgType.QtWarningMsg else logging.WARNING
            _write_log(lvl, f"[Qt:{msg_type.name}] {message}")

    qInstallMessageHandler(_qt_message_handler)

    prev_excepthook = sys.excepthook

    def _excepthook(exc_type, exc_value, exc_tb) -> None:
        tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        _write_log(logging.ERROR, f"Unhandled Python exception:\n{tb_text}")
        if prev_excepthook is not None:
            prev_excepthook(exc_type, exc_value, exc_tb)

    sys.excepthook = _excepthook
    _INITIALIZED = True
    return log_path

