// SPDX-License-Identifier: GPL-3.0-or-later
import test from "node:test";
import assert from "node:assert/strict";

import {
  canReseedInstallArchiveDialog,
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

test("a new request re-seeds an open dialog but never a running install", () => {
  // A second request arrives while the dialog already shows another game.
  assert.equal(
    canReseedInstallArchiveDialog({ modelValue: true, installing: false }),
    true,
  );
  assert.equal(
    canReseedInstallArchiveDialog({ modelValue: false, installing: false }),
    false,
    "a closed dialog ignores requests handled by its open transition",
  );
  assert.equal(
    canReseedInstallArchiveDialog({ modelValue: true, installing: true }),
    false,
    "the running install keeps the dialog's fields, progress and result",
  );
});
