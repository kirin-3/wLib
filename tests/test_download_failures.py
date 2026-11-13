# SPDX-License-Identifier: GPL-3.0-or-later
"""Every failure message the download workers produce maps to its code."""
import pytest

from core.downloads import failures
from core.downloads.hosts import rpdl
from core.downloads.manager import _resolve_failure  # pyright: ignore[reportPrivateUsage]
from core.downloads.torrents import MSG_INVALID, MSG_KEEPS_STOPPING, MSG_LOW_DISK, MSG_UNAVAILABLE
from core.downloads.transfer import disk_error


@pytest.mark.parametrize(
    ("message", "code"),
    [
        # transfer.py
        ("network error", failures.NETWORK),
        ("network error: incomplete download", failures.NETWORK),
        ("file invalid", failures.INVALID),
        ("file invalid: MEGA checksum mismatch", failures.INVALID),
        ("Not enough disk space for the download", failures.DISK),
        ("Not enough disk space: 100 bytes needed", failures.DISK),
        ("Could not save the download: Permission denied", failures.WRITE),
        ("Invalid download job", failures.UNKNOWN),
        # hosts
        ("link expired or removed", failures.REMOVED),
        ("transfer quota or rate limit reached", failures.QUOTA),
        ("blocked by a challenge", failures.CHALLENGE),
        ("unsupported host; open in browser", failures.INVALID),
        ("unsupported Pixeldrain link", failures.INVALID),
        ("unsupported MEGA link", failures.INVALID),
        ("unsupported Buzzheavier link", failures.INVALID),
        ("Pixeldrain folder links are not supported; open in browser", failures.INVALID),
        ("Pixeldrain returned an unexpected response", failures.INVALID),
        ("MEGA link is missing its key", failures.INVALID),
        ("invalid MEGA download URL", failures.INVALID),
        ("Pixeldrain rejected your API key; update it in Settings", failures.SIGN_IN),
        ("MEGA session expired; sign in again in Settings", failures.SIGN_IN),
        ("MEGA refused the request; try another mirror", failures.UNKNOWN),
        # manager.py and the bridge
        ("Open a browser with an up-to-date wLib extension to download in-app", failures.EXTENSION),
        ("Enable Buzzheavier downloads in the wLib extension settings", failures.PERMISSION),
        (_resolve_failure("extension did not resolve in time"), failures.CHECK_TIMEOUT),
        (_resolve_failure("disconnected"), failures.EXTENSION),
        (_resolve_failure("cancelled"), failures.CHECK_TIMEOUT),
        (_resolve_failure("weird"), failures.UNKNOWN),
        # update_downloads.py and the scraper
        ("Download link is unavailable; reload the picker", failures.MIRROR_GONE),
        ("Mirror no longer available; choose another mirror", failures.MIRROR_GONE),
        ("Game has no F95Zone thread", failures.MIRROR_GONE),
        ("Split archives are not supported yet; open in browser", failures.INVALID),
        ("This Buzzheavier link cannot be downloaded in-app; open in browser", failures.INVALID),
        ("Open a browser with the wLib extension", failures.EXTENSION),
        ("Open a browser with the wLib extension and try again", failures.EXTENSION),
        ("Sign in to F95Zone in your browser", failures.SIGN_IN),
        ("F95Zone returned HTTP 503", failures.NETWORK),
        # torrents.py
        (MSG_UNAVAILABLE, failures.ENGINE),
        (MSG_KEEPS_STOPPING, failures.ENGINE),
        (MSG_INVALID, failures.INVALID),
        (MSG_LOW_DISK, failures.DISK),
        ("Files could not be written", failures.WRITE),
        ("Not enough disk space: 6.0 GiB needed, 1.0 GiB free", failures.DISK),
        ("Torrent download failed", failures.UNKNOWN),
        # rpdl.py
        (rpdl.SIGN_IN_MESSAGE, failures.SIGN_IN),
        ("RPDL torrent not found", failures.REMOVED),
        ("RPDL rate limit reached", failures.QUOTA),
        ("RPDL is unavailable", failures.NETWORK),
        ("RPDL sent an invalid response", failures.INVALID),
        ("", failures.UNKNOWN),
    ],
)
def test_message_maps_to_code(message, code):
    assert failures.classify(message) == code


def test_disk_error_messages_map_to_disk_and_write():
    import errno

    assert failures.classify(str(disk_error(OSError(errno.ENOSPC, "No space")))) == failures.DISK
    assert failures.classify(str(disk_error(OSError(errno.EACCES, "Permission denied")))) == failures.WRITE


def test_only_network_retries():
    assert [code for code in failures.CODES if failures.retryable(code)] == [failures.NETWORK]
