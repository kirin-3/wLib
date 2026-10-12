# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared library-backup format identifiers and validation error.

This is a leaf module: it imports nothing from wLib so that both
``core.library_backup`` and ``core.external_import`` can use it without
creating an import cycle. ``core.library_backup`` re-exports these names, so
existing ``from core.library_backup import ...`` imports keep working.
"""

from __future__ import annotations

BACKUP_FORMAT = "wlib.library_migration"
BACKUP_FORMAT_VERSION = 1
ENCRYPTED_FORMAT = "wlib.encrypted"


class BackupValidationError(ValueError):
    def __init__(self, message: str, error_code: str = "invalid_backup") -> None:
        super().__init__(message)
        self.error_code: str = error_code
