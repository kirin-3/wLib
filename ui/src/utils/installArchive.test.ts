// SPDX-License-Identifier: GPL-3.0-or-later
import test from "node:test";
import assert from "node:assert/strict";

import {
  INSTALL_ARCHIVE_EVENT,
  openInstallArchive,
  mergeArchiveFields,
  createInstallPreview,
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

test("extension seed reaches the install dialog event", () => {
  let detail: InstallArchiveRequest | undefined;
  const mockWindow = {
    dispatchEvent(event: CustomEvent<InstallArchiveRequest>) {
      detail = event.detail;
      return true;
    },
  };
  (globalThis as unknown as { window: typeof mockWindow }).window = mockWindow;
  const seed = {
    title: "Eternum", version: "0.8", creator: "Caribdis", engine: "Ren'Py",
    f95Url: "https://f95zone.to/threads/123/", coverImage: "cover.jpg",
    tags: ["3d"], rating: "4.5",
  };
  try {
    openInstallArchive({ seed });
    assert.deepEqual(detail, { seed });
  } finally {
    delete (globalThis as unknown as { window?: typeof mockWindow }).window;
  }
});

test("metadata fills untouched fields and keeps edits, including cleared fields", () => {
  const current = { title: "My title", version: "1", creator: "", engine: "" };
  const incoming = { title: "Thread title", version: "2", creator: "Dev", engine: "Ren'Py" };
  assert.deepEqual(mergeArchiveFields(current, incoming, new Set(["title", "creator"])), {
    title: "My title", version: "2", creator: "", engine: "Ren'Py",
  });
  assert.deepEqual(mergeArchiveFields(current, { title: "", version: "" }, new Set()), current);
});

test("preview debounces edits, discards stale responses immediately and cancels on close", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const requests: Array<{ input: string; resolve: (value: string) => void; reject: (error: unknown) => void }> = [];
  const applied: string[] = [];
  const errors: unknown[] = [];
  const preview = createInstallPreview(
    (input: string) => new Promise<string>((resolve, reject) => requests.push({ input, resolve, reject })),
    (value) => applied.push(value), (error) => errors.push(error),
  );
  preview.schedule("old");
  t.mock.timers.tick(200);
  preview.schedule("first");
  t.mock.timers.tick(249);
  assert.equal(requests.length, 0);
  t.mock.timers.tick(1);
  assert.equal(requests[0]?.input, "first");
  preview.schedule("second");
  requests[0]!.resolve("stale");
  await Promise.resolve();
  assert.deepEqual(applied, []);
  t.mock.timers.tick(250);
  requests[1]!.resolve("latest");
  await Promise.resolve();
  assert.deepEqual(applied, ["latest"]);
  preview.schedule("closed");
  t.mock.timers.tick(250);
  preview.cancel();
  requests[2]!.reject(new Error("stale failure"));
  await Promise.resolve();
  assert.deepEqual(errors, []);
  preview.schedule("cancelled timer");
  preview.cancel();
  t.mock.timers.tick(250);
  assert.equal(requests.length, 3);
  preview.schedule("failure");
  t.mock.timers.tick(250);
  requests[3]!.reject(new Error("offline"));
  await Promise.resolve();
  assert.equal(errors.length, 1);
});
