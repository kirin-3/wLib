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

test("checkAllUpdates only sends the optional game-id list when the run is scoped", async () => {
  const calls = [];
  globalThis.window = {
    pywebview: {
      api: {
        check_all_updates: (...args) => {
          calls.push(args);
          return Promise.resolve({ success: true, total: 0, delay_seconds: 2 });
        },
      },
    },
    dispatchEvent() {},
  };
  const server = await createServer({ server: { middlewareMode: true }, appType: "custom" });
  try {
    const { api } = await server.ssrLoadModule("/src/services/api.ts");
    await api.checkAllUpdates();
    await api.checkAllUpdates([3, 4]);
    // pywebview JSON-encodes arguments, so an `undefined` parameter reaches
    // Python as null and check_all_updates(game_ids=None) iterates it.
    assert.deepEqual(calls, [[], [[3, 4]]]);
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
  globalThis.window = {
    localStorage: mockStorage,
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
    assert.deepEqual(await api.setAutoCheckSetting("weekly"), { success: true, mock: true });
    assert.deepEqual(await api.openInBrowser("https://example.com"), {
      success: true,
      mock: true,
    });
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
  } finally {
    await server.close();
    delete globalThis.window;
    delete globalThis.localStorage;
  }
});
