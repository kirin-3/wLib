// SPDX-License-Identifier: GPL-3.0-or-later
import assert from "node:assert/strict";
import { test } from "node:test";
import { createServer } from "vite";

test("game saves preserve order, isolate games, and recover after failures", async () => {
  const calls = [];
  globalThis.window = {
    pywebview: { api: { update_game: (id, fields) => new Promise((resolve, reject) => {
      calls.push({ id, fields, resolve, reject });
    }) } },
    dispatchEvent() {},
  };
  const server = await createServer({ server: { middlewareMode: true }, appType: "custom" });
  try {
    const { api } = await server.ssrLoadModule("/src/services/api.ts");
    const first = api.updateGame(1, { is_favorite: 1 });
    const fields = { is_favorite: 0 };
    const second = api.updateGame(1, fields);
    fields.is_favorite = 1;
    const other = api.updateGame(2, { play_status: "Playing" });
    await new Promise(setImmediate);
    assert.deepEqual(calls.map(call => call.id), [1, 2]);
    calls[1].resolve({ success: true });
    await other;
    calls[0].resolve({ success: true });
    await first;
    await new Promise(setImmediate);
    assert.equal(calls[2].fields.is_favorite, 0);
    const third = api.updateGame(1, { user_rating: 5 });
    const rejected = assert.rejects(second, /offline/);
    calls[2].reject(new Error("offline"));
    await rejected;
    await new Promise(setImmediate);
    assert.equal(calls[3].fields.user_rating, 5);
    calls[3].resolve({ success: true });
    await third;
    assert.equal(api.gameSaves.size, 0);
  } finally {
    await server.close();
    delete globalThis.window;
  }
});

test("browser mock payloads keep the desktop contract for every bridge method", async () => {
  const storage = new Map();
  const mockStorage = {
    getItem: (key) => (storage.has(key) ? storage.get(key) : null),
    setItem: (key, value) => storage.set(key, String(value)),
    removeItem: (key) => storage.delete(key),
  };
  globalThis.localStorage = mockStorage;
  const opened = [];
  globalThis.window = {
    localStorage: mockStorage,
    open: (...args) => opened.push(args),
    dispatchEvent() {},
    addEventListener() {},
    navigator: { userAgent: "node" },
  };
  const server = await createServer({ server: { middlewareMode: true }, appType: "custom" });
  try {
    const { api } = await server.ssrLoadModule("/src/services/api.ts");

    // Bridges that need a host still answer with their own payload shape
    // instead of the generic "unavailable outside PyWebView" error.
    assert.deepEqual(await api.checkAllUpdates(), {
      success: true,
      mock: true,
      total: 0,
      delay_seconds: 2,
    });
    assert.deepEqual(await api.cancelUpdateCheck(), { success: true, mock: true });
    assert.deepEqual(await api.setAutoCheckSetting("monthly"), { success: true, mock: true });
    assert.equal((await api.getAutoCheckSetting()).frequency, "monthly");
    assert.deepEqual(await api.openInBrowser("https://example.com"), {
      success: true,
      mock: true,
    });
    assert.deepEqual(opened, [["https://example.com", "_blank", "noopener"]]);
    assert.equal((await api.openInBrowser("javascript:alert(1)")).success, false);
    assert.deepEqual(await api.openFolder("/tmp"), { success: true, mock: true });
    const extension = await api.openExtensionFolder();
    assert.equal(extension.success, true);
    assert.equal(extension.reason, "mock");
    assert.equal(await api.openDevTools(), undefined);
    for (const call of [
      () => api.check_app_updates(),
      () => api.downloadProtonGe(),
      () => api.installRpgmakerDependencies(),
      () => api.installRpgmakerRtp(),
      () => api.downloadCheatEngine(),
    ]) {
      const result = await call();
      assert.equal(result.success, false);
      assert.match(String(result.error), /desktop app runtime/);
    }

    // Status payloads carry every field the desktop backend returns.
    const status = await api.getUpdateStatus();
    assert.equal(status.cancelling, false);
    const sync = await api.getSyncStatus();
    assert.equal(sync.connecting, false);
    const backup = await api.importLibraryBackup("/tmp/x.json", { sections: ["user_state"] });
    assert.equal(backup.deleted, 0);
    assert.deepEqual(backup.created_ids, []);
    assert.deepEqual(backup.updated_ids, []);
    const external = await api.importExternalLibrary("f95checker", "/tmp/x.db", {
      sections: ["user_state"],
    });
    assert.equal(external.source, "f95checker");
    assert.equal(external.deleted, 0);

    // Payload shapes the desktop backend always returns.
    const installStatus = await api.getInstallStatus();
    assert.equal(installStatus.success, true);
    const extensionService = await api.getExtensionServiceStatus();
    assert.equal(extensionService.success, true);
    assert.equal(extensionService.reachable, false);
    const systemDeps = await api.getSystemDepsCommand();
    assert.equal(systemDeps.code, "unsupported_platform");

    // check_for_updates only fails on a blank URL: an unknown thread URL
    // reports "no update" like the backend does.
    assert.equal((await api.checkForUpdates("")).success, false);
    const unknownThread = await api.checkForUpdates("https://f95zone.to/threads/unknown.1/");
    assert.equal(unknownThread.success, true);
    assert.equal(unknownThread.has_update, false);
    assert.equal(unknownThread.metadata_updated, 0);

    // A sync run reports the state SyncManager.start_run() answers with.
    localStorage.setItem("wlib-mock-sync", JSON.stringify({ state: "idle", location: "folder" }));
    const running = await api.syncNow();
    assert.equal(running.state, "syncing");
    assert.equal((await api.getSyncStatus()).connecting, false);

    // Export reports the sections it wrote and the games it serialised.
    const exported = await api.exportLibraryBackup(
      { sections: ["metadata", "user_state"] },
      "/tmp/wlib-library-export.json",
    );
    assert.deepEqual(exported.selected_sections, ["metadata", "user_state"]);
    assert.equal(exported.game_count, 0);

    // Collections and launch targets refuse ids that match no game.
    const missingCollection = await api.createCollection("Ghost", 4242);
    assert.equal(missingCollection.success, false);
    assert.equal(missingCollection.error, "Game not found");
    const missingTarget = await api.createLaunchTarget(4242, "Ghost", "/tmp/ghost.exe");
    assert.equal(missingTarget.success, false);
    assert.equal(missingTarget.error_code, "game_not_found");

    // inspect_archive guesses from the archive name and only fills blanks from
    // the stored game; .7z/.rar report the missing extraction tool.
    const archive = await api.inspectArchive("/tmp/Eternum-0.6.zip");
    assert.equal(archive.title, "Eternum");
    assert.equal(archive.version, "0.6");
    assert.equal(archive.tool_error, "");
    assert.equal(
      (await api.inspectArchive("/tmp/Eternum-0.6.7z")).tool_error,
      "Install 7-Zip (7z) or bsdtar to extract .7z archives.",
    );
    const added = await api.addGame("Eternum", "/tmp/Eternum-0.6-pc/Eternum.exe");
    assert.equal(added.title, "Eternum");
  } finally {
    await server.close();
    delete globalThis.window;
    delete globalThis.localStorage;
  }
});
