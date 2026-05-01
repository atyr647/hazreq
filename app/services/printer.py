"""Direct-to-printer via CUPS (the same stack xfce uses).

Sends a generated PDF to `lp`. Printer selection:
  1. caller-supplied printer name (highest priority)
  2. HAZREQ_PRINTER env var
  3. CUPS default printer (no -d flag passed)
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from app.config import settings

log = logging.getLogger(__name__)


class PrintError(RuntimeError):
    pass


def is_available() -> bool:
    return shutil.which("lp") is not None


def list_printers() -> list[str]:
    """Return printer names known to CUPS. Empty list if `lpstat` is missing
    or CUPS isn't running.
    """
    if not shutil.which("lpstat"):
        return []
    try:
        out = subprocess.run(
            ["lpstat", "-e"], capture_output=True, text=True, timeout=3
        )
    except (subprocess.SubprocessError, FileNotFoundError) as e:
        log.warning("lpstat failed: %s", e)
        return []
    if out.returncode != 0:
        return []
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


def default_printer() -> str | None:
    """Resolved default printer (config wins over CUPS default).
    Returns None if neither is set; lp will use CUPS default.
    """
    return settings.printer


def print_pdf(path: Path, *, printer: str | None = None, copies: int = 1) -> str:
    """Submit a PDF to lp. Returns the lp job id string for display."""
    if not is_available():
        raise PrintError(
            "`lp` (CUPS) is not installed. On Pi: `apt install cups cups-client`."
        )
    if not path.exists():
        raise PrintError(f"PDF not found at {path}")
    target = printer or settings.printer
    cmd = ["lp"]
    if target:
        cmd += ["-d", target]
    if copies and copies > 1:
        cmd += ["-n", str(copies)]
    cmd.append(str(path))
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except subprocess.SubprocessError as e:
        raise PrintError(f"lp submission failed: {e}") from None
    if result.returncode != 0:
        raise PrintError(
            f"lp returned {result.returncode}: {result.stderr.strip() or result.stdout.strip()}"
        )
    return result.stdout.strip()
