// SPDX-License-Identifier: GPL-3.0-or-later
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";

import {
  buildCleanupConfirmOptions,
  formatCleanupResult,
  formatRelativeAge,
  formatStorageBytes,
  gameRowFlags,
  gameRowParts,
  isCleanupActionAvailable,
  skippedEntriesNote,
  sortedStorageGames,
  stoppedScanNote,
} from "./storage.ts";
import { MockStorage } from "../services/mock/storage.ts";
import type { StorageGameRow } from "../services/api.ts";

const uiRoot = resolve(import.meta.dirname, "..");

test("router registers /storage and App.vue includes Storage sidebar link", () => {
  const routerSource = readFileSync(resolve(uiRoot, "router/index.ts"), "utf-8");
  assert.match(routerSource, /path:\s*"\/storage"/);
  assert.match(routerSource, /component:\s*StorageView/);

  const appSource = readFileSync(resolve(uiRoot, "App.vue"), "utf-8");
  assert.match(appSource, /to="\/storage"/);
  assert.match(appSource, /to="\/storage"[\s\S]*?>\s*Storage\s*<\/span/);
});

test("StorageView polls every 500ms only while scanning or cleaning, and opens UninstallDialog", () => {
  const viewSource = readFileSync(resolve(uiRoot, "views/StorageView.vue"), "utf-8");
  assert.match(viewSource, /window\.setTimeout\([\s\S]*?500\)/);
  assert.match(viewSource, /state\.value\?\.scanning\s*\|\|\s*state\.value\?\.cleanup_running/);
  assert.match(viewSource, /<UninstallDialog/);
  assert.match(viewSource, /uninstallOpen\.value = true/);
});

test("SettingsView exposes storage_recount_after_changes toggle in Library & Downloads", () => {
  const settingsSource = readFileSync(resolve(uiRoot, "views/SettingsView.vue"), "utf-8");
  assert.match(settingsSource, /storage_recount_after_changes/);
  assert.match(settingsSource, /Count a game's size again after install, update or move/);
  assert.match(settingsSource, /library-downloads-storage-recount/);

  const mockSettingsSource = readFileSync(resolve(uiRoot, "services/mock/settings.ts"), "utf-8");
  assert.match(mockSettingsSource, /storage_recount_after_changes:\s*false/);
});

test("formatStorageBytes, formatRelativeAge, stoppedScanNote and skippedEntriesNote", () => {
  assert.equal(formatStorageBytes(0), "0 B");
  assert.equal(formatStorageBytes(512), "512 B");
  assert.equal(formatStorageBytes(1024 * 1024 * 145), "145 MB");
  assert.equal(formatStorageBytes(Math.round(3.1 * 1024 ** 3)), "3.1 GB");

  const now = 1_700_000_000_000;
  assert.equal(formatRelativeAge(now / 1000 - 10, now), "just now");
  assert.equal(formatRelativeAge(now / 1000 - 300, now), "5 minutes ago");
  assert.equal(formatRelativeAge(now / 1000 - 3 * 3600, now), "3 hours ago");
  assert.equal(formatRelativeAge(now / 1000 - 3 * 86400, now), "3 days ago");

  assert.equal(stoppedScanNote("", true), "");
  assert.match(stoppedScanNote("user", true), /previous report/);
  assert.match(stoppedScanNote("game", true), /game started/);
  assert.equal(skippedEntriesNote(0), "");
  assert.match(skippedEntriesNote(1), /1 entry/);
  assert.match(skippedEntriesNote(4), /4 entries/);
});

test("gameRowFlags, gameRowParts and sortedStorageGames cover all flags and ordering", () => {
  const baseRow = (overrides: Partial<StorageGameRow>): StorageGameRow => ({
    game_id: 1,
    title: "Alpha",
    root: "/games/Alpha",
    root_source: "pattern",
    exe_path: "/games/Alpha/alpha.exe",
    picked: false,
    uncertain: false,
    unsafe: false,
    unsafe_reason: "",
    unavailable: false,
    changed: false,
    shared_with: [],
    shared_with_ids: [],
    parts: { game: 1000, saves: 200, player: 0, sandbox: 0, prefix: 300 },
    torrent_bytes: 500,
    total_bytes: 1500,
    ...overrides,
  });

  const flagged = baseRow({
    unsafe: true,
    uncertain: true,
    unavailable: true,
    changed: true,
    shared_with: ["Beta"],
  });
  const labels = gameRowFlags(flagged).map((f) => f.label);
  assert.deepEqual(labels, [
    "not clear",
    "may be incomplete",
    "not available",
    "changed since the scan",
    "shared with Beta",
  ]);

  const parts = gameRowParts(flagged).map((p) => p.key);
  assert.deepEqual(parts, ["game", "saves", "prefix"]);

  const sorted = sortedStorageGames({
    "1": baseRow({ game_id: 1, title: "Small", total_bytes: 100 }),
    "2": baseRow({ game_id: 2, title: "Huge", total_bytes: 5000 }),
  });
  assert.deepEqual(
    sorted.map((g) => g.title),
    ["Huge", "Small"],
  );
});

test("cleanup confirmation, availability, result formatting and MockStorage lifecycle", () => {
  const removedPreview = {
    success: true,
    preview_id: "tok-1",
    action: "removed_game_data" as const,
    label: "Data of removed games",
    count: 2,
    bytes: Math.round(380 * 1024 * 1024),
    what_stays: "Active games stay.",
    warning: "Removed game sandboxes and built-in player storage can hold game saves.",
  };
  const confirm = buildCleanupConfirmOptions(removedPreview);
  assert.equal(confirm.danger, true);
  assert.match(confirm.confirmLabel, /^Delete 380 MB$/);
  assert.match(confirm.message, /2 items/);
  assert.match(confirm.message, /hold game saves/);
  assert.match(confirm.message, /cannot be restored/);

  assert.match(
    formatCleanupResult({ success: true, freed_bytes: 1024 * 1024 * 10, removed: 4, failed: 1, skipped: 0 }),
    /Freed 10\.0 MB\. 1 item could not be removed\./,
  );

  assert.equal(
    isCleanupActionAvailable({
      id: "unpack_leftovers",
      label: "Unpack leftovers",
      bytes: 0,
      count: 0,
      blocked_reason: "",
      what_stays: "",
      warning: "",
    }),
    false,
  );

  // MockStorage: empty -> scanning -> finished -> preview -> run -> expired token.
  const mock = new MockStorage(() => false);
  const s0 = mock.getState();
  assert.equal(s0.report, null);
  assert.equal(s0.scanning, false);

  assert.equal(mock.startScan().success, true);
  const s1 = mock.getState();
  assert.equal(s1.scanning, true);
  assert.ok(s1.files_counted > 0);
  // Stop mid-scan sets stopped_reason.
  assert.equal(mock.stopScan("user").stopped, true);
  assert.equal(mock.getState().stopped_reason, "user");

  // Run full scan to completion.
  assert.equal(mock.startScan().success, true);
  let state = mock.getState();
  while (state.scanning) state = mock.getState();
  assert.ok(state.report !== null);
  assert.ok(state.report.total_bytes > 0);

  // Preview + confirm deletes download archives and updates report.
  const prev = mock.previewCleanup("download_archives");
  assert.equal(prev.success, true);
  assert.ok(prev.preview_id);
  const run = mock.runCleanup(prev.preview_id);
  assert.equal(run.success, true);
  assert.ok((run.freed_bytes ?? 0) > 0);

  // Re-using the consumed/expired token fails cleanly.
  const expired = mock.runCleanup(prev.preview_id);
  assert.equal(expired.success, false);
  assert.equal(expired.code, "preview_expired");
});
