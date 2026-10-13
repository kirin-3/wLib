// SPDX-License-Identifier: GPL-3.0-or-later
import test from "node:test";
import assert from "node:assert/strict";

import {
  INSTALL_ARCHIVE_EVENT,
  openInstallArchive,
  type InstallArchiveRequest,
} from "./installArchive.ts";

test("openInstallArchive dispatches custom event with details", () => {
  let dispatchedEvent: CustomEvent<InstallArchiveRequest> | null = null;

  const mockWindow = {
    dispatchEvent(event: CustomEvent<InstallArchiveRequest>) {
      dispatchedEvent = event;
      return true;
    },
  };

  (globalThis as unknown as { window: typeof mockWindow }).window = mockWindow;

  try {
    openInstallArchive({ gameId: 42, archivePath: "/path/to/archive.zip" });
    assert.ok(dispatchedEvent);
    assert.equal((dispatchedEvent as CustomEvent<InstallArchiveRequest>).type, INSTALL_ARCHIVE_EVENT);
    assert.deepEqual((dispatchedEvent as CustomEvent<InstallArchiveRequest>).detail, {
      gameId: 42,
      archivePath: "/path/to/archive.zip",
    });

    dispatchedEvent = null;
    openInstallArchive();
    assert.ok(dispatchedEvent);
    assert.deepEqual((dispatchedEvent as CustomEvent<InstallArchiveRequest>).detail, {});
  } finally {
    delete (globalThis as unknown as { window?: typeof mockWindow }).window;
  }
});
