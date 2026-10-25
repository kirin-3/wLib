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
    // reports an update like the backend does (unknown installed version).
    assert.equal((await api.checkForUpdates("")).success, false);
    const unknownThread = await api.checkForUpdates("https://f95zone.to/threads/unknown.1/");
    assert.equal(unknownThread.success, true);
    assert.equal(unknownThread.has_update, true);
    assert.ok(unknownThread.version);
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

test("browser mock persists notes progress and attachment round-trip", async () => {
  const storage = new Map();
  const mockStorage = {
    getItem: (key) => (storage.has(key) ? storage.get(key) : null),
    setItem: (key, value) => storage.set(key, String(value)),
    removeItem: (key) => storage.delete(key),
  };
  globalThis.localStorage = mockStorage;
  globalThis.window = {
    localStorage: mockStorage,
    open: () => {},
    dispatchEvent() {},
    addEventListener() {},
    navigator: { userAgent: "node" },
  };
  const server = await createServer({ server: { middlewareMode: true }, appType: "custom" });
  try {
    const { api } = await server.ssrLoadModule("/src/services/api.ts");
    const created = await api.addGame("Noted", "/tmp/noted.exe");
    const gameId = created.id;
    assert.ok(gameId);

    const saved = await api.updateGame(gameId, { progress: "# Notes\nHello" });
    assert.equal(saved.success, true);
    const games = await api.getGames();
    assert.equal(games.find((game) => game.id === gameId).progress, "# Notes\nHello");

    assert.equal(await api.browsePdfFile(""), "/mock/guide.pdf");

    const empty = await api.listGameAttachments(gameId);
    assert.deepEqual(empty.attachments, []);
    const addedAttachment = await api.addGameAttachment(gameId, "/tmp/guide.pdf");
    assert.equal(addedAttachment.success, true);
    assert.equal(addedAttachment.attachment.name, "guide.pdf");
    const listed = await api.listGameAttachments(gameId);
    assert.deepEqual(listed.attachments.map((item) => item.name), ["guide.pdf"]);
    assert.equal((await api.openGameAttachment(gameId, "guide.pdf")).success, true);
    const removed = await api.removeGameAttachment(gameId, "guide.pdf");
    assert.equal(removed.success, true);
    assert.deepEqual((await api.listGameAttachments(gameId)).attachments, []);
    assert.equal((await api.addGameAttachment(gameId, "/tmp/notes.txt")).error_code, "not_a_pdf");
    assert.equal((await api.listGameAttachments(4242)).error_code, "game_not_found");
  } finally {
    await server.close();
    delete globalThis.window;
    delete globalThis.localStorage;
  }
});

test("browser mock game saves keep the desktop payload shape", async () => {
  const storage = new Map();
  const mockStorage = {
    getItem: (key) => (storage.has(key) ? storage.get(key) : null),
    setItem: (key, value) => storage.set(key, String(value)),
    removeItem: (key) => storage.delete(key),
  };
  globalThis.localStorage = mockStorage;
  globalThis.window = {
    localStorage: mockStorage,
    open: () => {},
    dispatchEvent() {},
    addEventListener() {},
    navigator: { userAgent: "node" },
  };
  const server = await createServer({ server: { middlewareMode: true }, appType: "custom" });
  try {
    const { api } = await server.ssrLoadModule("/src/services/api.ts");
    const missing = await api.getGameSaves(4242);
    assert.equal(missing.success, false);
    assert.equal(missing.error_code, "game_not_found");

    const gameId = (await api.addGame("Saved", "/tmp/saved.exe")).id;
    const saves = await api.getGameSaves(gameId);
    assert.equal(saves.success, true);
    assert.deepEqual(Object.keys(saves).filter((key) => key !== "mock").sort(), [
      "backups", "detected", "effective", "effective_exists", "keep", "override", "override_exists", "success",
    ]);
    assert.equal(saves.override, "");
    assert.equal(saves.keep, "all");
    assert.ok(saves.detected.every((entry) => typeof entry.eligible === "boolean"));
    assert.ok(saves.detected.some((entry) => entry.eligible === false));
    assert.equal(saves.effective, saves.detected.find((entry) => entry.eligible).path);

    const set = await api.setGameSaveFolder(gameId, "/home/me/saves");
    assert.deepEqual([set.success, set.override], [true, "/home/me/saves"]);
    assert.equal((await api.setGameSaveFolder(gameId, "relative")).success, false);
    const withOverride = await api.getGameSaves(gameId);
    assert.deepEqual([withOverride.effective, withOverride.detected], ["/home/me/saves", []]);

    const backup = await api.backupGameSaves(gameId);
    assert.equal(backup.success, true);
    assert.deepEqual(backup.backups.map((entry) => entry.name), [backup.backup]);
    assert.equal((await api.restoreGameSavesBackup(gameId, "2000-01-01_00-00-00")).success, false);
    assert.equal((await api.restoreGameSavesBackup(gameId, backup.backup)).success, true);
    assert.equal((await api.getGameSaves(gameId)).backups.length, 2);
    assert.equal((await api.openGameSaveBackup(gameId, backup.backup)).success, true);

    assert.equal((await api.getSettings()).save_backup_keep, "all");
    assert.equal((await api.saveSettings({ save_backup_keep: 100 })).success, false);
    assert.equal((await api.saveSettings({ save_backup_keep: 5 })).success, true);
    assert.equal((await api.getGameSaves(gameId)).keep, 5);
  } finally {
    await server.close();
    delete globalThis.window;
    delete globalThis.localStorage;
  }
});

test("browser mock game tools report support and advance progress across polls", async () => {
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
    api._mockToolStepMs = 40;
    const game = (id, engine) => ({ id, title: `G${id}`, engine, exe_path: `/g/${id}/run.sh` });
    localStorage.setItem(
      "wlib-mock-games",
      JSON.stringify([game(1, "Ren'Py"), game(2, "RPGM"), game(3, "Unity")]),
    );

    assert.equal((await api.getGameToolSupport(1)).engine, "renpy");
    assert.equal((await api.getGameToolSupport(2)).engine, "rpgm");
    assert.equal((await api.getGameToolSupport(3)).engine, "");
    assert.equal((await api.getGameToolSupport(99)).success, false);
    assert.equal((await api.getGameTools(1)).renpy.archives.length, 2);
    assert.equal((await api.getGameTools(2)).rpgm.missing, 12);
    assert.equal((await api.getGameTools(3)).engine, "");

    assert.equal((await api.setRenpyConsole(1, true, true)).state, "developer");
    assert.equal((await api.getGameTools(1)).renpy.console, "developer");
    assert.equal((await api.setRenpyConsole(2, true, false)).success, false);
    assert.equal((await api.extractRenpyArchives(1, { mode: "folder" })).success, false);

    assert.equal((await api.setRpgmUseDecrypted(2, true)).success, false);
    const run = api.decryptRpgmAssets(2);
    await new Promise((resolve) => setTimeout(resolve, 120));
    const early = await api.getGameToolStatus(2);
    await new Promise((resolve) => setTimeout(resolve, 200));
    const later = await api.getGameToolStatus(2);
    assert.equal(early.running, true);
    assert.equal(early.total, 12);
    assert.ok(later.done > early.done);
    assert.equal((await api.decryptRpgmAssets(2)).success, false);
    assert.equal((await api.cancelGameTool(2)).success, true);
    const cancelled = await run;
    assert.equal(cancelled.cancelled, true);
    assert.equal((await api.getGameToolStatus(2)).running, false);
    assert.equal((await api.cancelGameTool(2)).success, false);

    const done = await api.decryptRpgmAssets(2);
    assert.equal(done.decrypted, 12);
    assert.equal((await api.setRpgmUseDecrypted(2, true)).use_decrypted, true);
    assert.equal((await api.getGameTools(2)).rpgm.use_decrypted, true);
  } finally {
    await server.close();
    delete globalThis.window;
    delete globalThis.localStorage;
  }
});

test("companion tools persist definitions, overrides, run support and duplicate guards", async () => {
  const storage = new Map([
    ["wlib-mock-platform", "linux"],
    ["wlib-mock-games", JSON.stringify([
      { id: 1, title: "Wine", exe_path: "/game.exe" },
      { id: 2, title: "Native", exe_path: "/game.sh" },
      { id: 3, title: "Sandbox", exe_path: "/game.exe", sandbox_enabled: true },
    ])],
  ]);
  const mockStorage = { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, String(value)) };
  globalThis.localStorage = mockStorage;
  globalThis.window = { localStorage: mockStorage, dispatchEvent() {}, addEventListener() {} };
  const previousCustomEvent = globalThis.CustomEvent;
  globalThis.CustomEvent ??= class CustomEvent { constructor(type, options) { this.type = type; this.detail = options?.detail; } };
  const server = await createServer({ server: { middlewareMode: true }, appType: "custom" });
  try {
    const { api } = await server.ssrLoadModule("/src/services/api.ts");
    assert.equal((await api.getPlatformCapabilities()).companion_tools, true);
    const wine = (await api.saveCompanionTool({ name: "Textractor", kind: "wine", path: "/tool.exe", arguments: "", sort_order: 0, show_on_all_games: true })).tool;
    const native = (await api.saveCompanionTool({ name: "OCR", kind: "native", path: "/ocr", arguments: "", sort_order: 1, show_on_all_games: false })).tool;
    assert.equal((await api.saveCompanionTool({ ...native, name: "textractor" })).success, false);
    assert.equal((await api.listCompanionTools()).tools.length, 2);
    assert.equal((await api.getGameCompanionTools(1)).tools[0].overridden, false);
    await api.setGameCompanionToolOverride(1, wine.id, false);
    assert.equal((await api.getGameCompanionTools(1)).tools[0].visible, false);
    await api.setGameCompanionToolOverride(1, wine.id, null);
    await api.setGameCompanionToolOverride(1, native.id, true);
    assert.deepEqual((await api.listLaunchableCompanionTools(1)).tools, []);
    assert.equal((await api.launchCompanionTool(1, wine.id)).success, false);
    await api.launchGame(1, "/game.exe");
    assert.deepEqual((await api.listLaunchableCompanionTools(1)).tools.map(tool => tool.name), ["Textractor", "OCR"]);
    assert.equal((await api.launchCompanionTool(1, wine.id)).success, true);
    assert.equal((await api.launchCompanionTool(1, wine.id)).code, "tool_running");
    await api.setGameCompanionToolOverride(2, native.id, true);
    await api.launchGame(2, "/game.sh");
    assert.deepEqual((await api.listLaunchableCompanionTools(2)).tools.map(tool => tool.name), ["OCR"]);
    await api.launchGame(3, "/game.exe");
    assert.deepEqual((await api.listLaunchableCompanionTools(3)).tools, []);
    await api.stopGame(1);
    assert.deepEqual((await api.listLaunchableCompanionTools(1)).tools, []);
    await api.launchGame(1, "/game.exe");
    assert.equal((await api.launchCompanionTool(1, wine.id)).success, true);
    await api.deleteCompanionTool(native.id);
    assert.equal((await api.getGameCompanionTools(1)).tools.length, 1);
    await api.deleteGame(1);
    assert.equal(JSON.parse(storage.get("wlib-mock-companions")).overrides["1"], undefined);
    storage.set("wlib-mock-platform", "windows");
    assert.equal((await api.getPlatformCapabilities()).companion_tools, false);
    assert.equal((await api.listLaunchableCompanionTools(2)).success, false);
    assert.equal((await api.listCompanionTools()).tools.length, 1);
  } finally {
    await server.close();
    delete globalThis.window;
    delete globalThis.localStorage;
    globalThis.CustomEvent = previousCustomEvent;
  }
});
