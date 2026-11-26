// SPDX-License-Identifier: GPL-3.0-or-later
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";

import {
  buildCleanupConfirmOptions,
  buildSafeCleanupConfirmOptions,
  CLEANUP_PREVIEW_TTL_MS,
  cleanupActionBlockedReason,
  cleanupActionScopeNote,
  cleanupPreviewExpired,
  cleanupRunError,
  PREVIEW_EXPIRED_MESSAGE,
  previewItemLines,
  storageWineProton,
  cleanupActionTier,
  cleanupTierTotals,
  formatCleanupResult,
  formatSafeCleanupResult,
  groupCleanupActionsByTier,
  NOTHING_TO_FREE,
  planSafeCleanup,
  formatRelativeAge,
  formatStorageBytes,
  gameRowFlags,
  gameRowHint,
  gameRowParts,
  groupStorageCategories,
  isCleanupActionAvailable,
  labelInSentence,
  skippedEntriesNote,
  sortedStorageCategories,
  sortedStorageGames,
  splitLargestGames,
  stoppedScanNote,
  visibleStorageActions,
  visibleStorageCategories,
} from "./storage.ts";
import { MockStorage } from "../services/mock/storage.ts";
import type {
  StorageCategoryReport,
  StorageCleanupAction,
  StorageGameRow,
} from "../services/api.ts";

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

test("SettingsView exposes storage_recount_after_changes toggle in the Library section", () => {
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
  assert.equal(skippedEntriesNote(1), "1 file or folder could not be read and was not counted.");
  assert.match(skippedEntriesNote(4), /^4 files or folders could not be read/);
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
    "folder not counted",
    "may be incomplete",
    "folder not found",
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
  assert.equal(confirm.title, "Clean data of removed games?");
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
      label: "Temporary install files",
      tier: "safe",
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

test("sortedStorageCategories puts the largest first and keeps the order of ties", () => {
  const cat = (id: string, bytes: number): StorageCategoryReport => ({
    id,
    label: id,
    bytes,
    files: 0,
    percent: 0,
  });
  const input = [cat("games", 10), cat("a", 0), cat("proton", 30), cat("b", 0), cat("catalog", 20)];
  assert.deepEqual(
    sortedStorageCategories(input).map((c) => c.id),
    ["proton", "catalog", "games", "a", "b"],
  );
  assert.deepEqual(input.map((c) => c.id), ["games", "a", "proton", "b", "catalog"]);
});

test("visibleStorageCategories and visibleStorageActions hide empty Wine and Proton parts without Wine", () => {
  const cat = (id: string, bytes: number): StorageCategoryReport => ({
    id,
    label: id,
    bytes,
    files: 0,
    percent: 0,
  });
  const cats = [cat("games", 10), cat("proton_builds", 0), cat("wine_prefixes", 0), cat("shared_wine_prefix", 5)];
  assert.deepEqual(
    visibleStorageCategories(cats, false).map((c) => c.id),
    ["games", "shared_wine_prefix"],
  );
  assert.equal(visibleStorageCategories(cats, true).length, 4);

  const action = (id: StorageCleanupAction["id"], bytes: number, count: number): StorageCleanupAction => ({
    id,
    label: id,
    tier: "review",
    bytes,
    count,
    blocked_reason: "",
    what_stays: "",
    warning: "",
  });
  const actions = [action("cover_cache", 0, 0), action("proton_builds", 0, 0)];
  assert.deepEqual(visibleStorageActions(actions, false).map((a) => a.id), ["cover_cache"]);
  assert.equal(visibleStorageActions([action("proton_builds", 4096, 1)], false).length, 1);
  assert.equal(visibleStorageActions(actions, true).length, 2);
});

const tierAction = (
  id: StorageCleanupAction["id"],
  tier: StorageCleanupAction["tier"],
  bytes: number,
  extra: Partial<StorageCleanupAction> = {},
): StorageCleanupAction => ({
  id,
  label: id,
  tier,
  bytes,
  count: bytes > 0 ? 1 : 0,
  blocked_reason: "",
  what_stays: "",
  warning: "",
  ...extra,
});

test("groupCleanupActionsByTier puts Safe first, sorts each tier by size and sums it", () => {
  const actions = [
    tierAction("download_archives", "review", 3000),
    tierAction("unpack_leftovers", "safe", 0),
    tierAction("removed_game_data", "risky", 380),
    tierAction("save_backups", "risky", 260),
    tierAction("proton_builds", "review", 1100),
    tierAction("cover_cache", "safe", 145),
    tierAction("bepinex_cache", "safe", 64),
  ];
  const groups = groupCleanupActionsByTier(actions);
  assert.deepEqual(
    groups.map((g) => [g.tier, g.bytes, g.actions.map((a) => a.id)]),
    [
      ["safe", 209, ["cover_cache", "bepinex_cache", "unpack_leftovers"]],
      ["review", 4100, ["download_archives", "proton_builds"]],
      ["risky", 640, ["removed_game_data", "save_backups"]],
    ],
  );
  assert.deepEqual(cleanupTierTotals(actions), { safe: 209, review: 4100, risky: 640 });

  // Ties keep the backend order; a tier without actions is left out.
  const ties = groupCleanupActionsByTier([
    tierAction("bepinex_cache", "safe", 10),
    tierAction("cover_cache", "safe", 10),
  ]);
  assert.deepEqual(ties.map((g) => g.tier), ["safe"]);
  assert.deepEqual(ties[0]?.actions.map((a) => a.id), ["bepinex_cache", "cover_cache"]);

  // An unknown tier from an older backend counts as risky.
  const unknown = { tier: "other" } as unknown as Pick<StorageCleanupAction, "tier">;
  assert.equal(cleanupActionTier(unknown), "risky");
});

test("planSafeCleanup runs only available Safe actions and lists the skipped ones", () => {
  const plan = planSafeCleanup([
    tierAction("download_archives", "review", 3000),
    tierAction("unpack_leftovers", "safe", 0),
    tierAction("cover_cache", "safe", 145),
    tierAction("bepinex_cache", "safe", 640, {
      blocked_reason: "A BepInEx install or update is running.",
    }),
    tierAction("removed_game_data", "risky", 380),
  ]);
  assert.deepEqual(plan.ready.map((a) => a.id), ["cover_cache"]);
  assert.equal(plan.bytes, 145);
  assert.deepEqual(plan.skipped, [
    { label: "bepinex_cache", reason: "A BepInEx install or update is running." },
    { label: "unpack_leftovers", reason: NOTHING_TO_FREE },
  ]);
});

test("safe cleanup confirmation lists each size, the total and the skipped actions", () => {
  const MB = 1024 * 1024;
  const options = buildSafeCleanupConfirmOptions(
    [
      {
        success: true,
        preview_id: "a",
        label: "Cover cache",
        count: 520,
        bytes: 145 * MB,
        what_stays: "User-chosen local covers stay.",
        warning: "",
      },
      { success: true, preview_id: "b", label: "BepInEx download cache", count: 2, bytes: 64 * MB },
    ],
    [
      { label: "Temporary install files", reason: NOTHING_TO_FREE },
      { label: "Old covers", reason: "A cover download is running." },
    ],
  );
  assert.equal(options.confirmLabel, "Delete 209 MB");
  assert.match(options.message, /522 items \(209 MB\)/);
  assert.match(options.message, /Cover cache: 145 MB, 520 items\. User-chosen local covers stay\./);
  assert.match(options.message, /BepInEx download cache: 64\.0 MB, 2 items\./);
  // Only actions that could not run are listed; "nothing to free" is left out.
  assert.match(options.message, /Skipped:\nOld covers: A cover download is running\./);
  assert.doesNotMatch(options.message, /Temporary install files/);
  assert.match(options.message, /cannot be restored/);
});

test("formatSafeCleanupResult adds up the runs and names failed and blocked actions", () => {
  const MB = 1024 * 1024;
  const summary = formatSafeCleanupResult(
    [
      { label: "Cover cache", result: { success: true, freed_bytes: 100 * MB, failed: 1, skipped: 0 } },
      { label: "BepInEx download cache", result: { success: true, freed_bytes: 50 * MB, skipped: 2 } },
      { label: "Temporary install files", result: { success: false, error: "This cleanup preview has expired." } },
    ],
    [
      { label: "Old thing", reason: NOTHING_TO_FREE },
      { label: "Busy thing", reason: "A cleanup is running." },
    ],
  );
  assert.match(summary.message, /^Freed 150 MB\. 1 item could not be removed\. 2 items were skipped/);
  assert.match(summary.message, /Skipped: Busy thing\.$/);
  assert.doesNotMatch(summary.message, /Old thing/);
  assert.equal(summary.error, "Temporary install files: This cleanup preview has expired.");

  const allFailed = formatSafeCleanupResult([
    { label: "Cover cache", result: { success: false, error: "Nope" } },
  ]);
  assert.equal(allFailed.message, "");
  assert.equal(allFailed.error, "Cover cache: Nope");
});

test("MockStorage gives every cleanup action and preview a tier", () => {
  const mock = new MockStorage(() => false);
  assert.equal(mock.startScan().success, true);
  let state = mock.getState();
  while (state.scanning) state = mock.getState();
  const tiers = Object.fromEntries(state.actions.map((a) => [a.id, a.tier]));
  assert.deepEqual(tiers, {
    download_archives: "review",
    unpack_leftovers: "safe",
    removed_game_data: "risky",
    save_backups: "risky",
    proton_builds: "review",
    cover_cache: "safe",
    bepinex_cache: "safe",
  });
  assert.equal(mock.previewCleanup("cover_cache").tier, "safe");
});


test("gameRowHint says what is wrong with a flagged row and what to do", () => {
  const row = (overrides: Partial<StorageGameRow>): StorageGameRow => ({
    game_id: 1,
    title: "Alpha",
    root: "/games/Alpha",
    root_source: "pattern",
    exe_path: "/games/Alpha/game.sh",
    picked: false,
    uncertain: false,
    unsafe: false,
    unsafe_reason: "",
    unavailable: false,
    changed: false,
    shared_with: [],
    shared_with_ids: [],
    parts: { game: 1000, saves: 0, player: 0, sandbox: 0, prefix: 0 },
    torrent_bytes: 0,
    total_bytes: 1000,
    ...overrides,
  });
  assert.equal(gameRowHint(row({})), "");
  assert.equal(
    gameRowHint(row({ unsafe: true, unsafe_reason: "A library folder cannot be used as a game folder." })),
    "A library folder cannot be used as a game folder. Use Pick folder to choose the game's own folder.",
  );
  assert.match(gameRowHint(row({ unsafe: true })), /Use Pick folder/);
  assert.match(gameRowHint(row({ unavailable: true, uncertain: true })), /drive that is not connected/);
  assert.match(gameRowHint(row({ uncertain: true })), /executable was counted.*Pick folder/);
});

test("labelInSentence and cleanupActionScopeNote", () => {
  assert.equal(labelInSentence("Data of removed games"), "data of removed games");
  assert.equal(labelInSentence("Unused Proton builds"), "unused Proton builds");
  assert.equal(labelInSentence("BepInEx download cache"), "BepInEx download cache");
  assert.equal(labelInSentence("Proton builds"), "Proton builds");
  assert.equal(labelInSentence(""), "");

  const GB = 1024 ** 3;
  const categories = [
    { id: "proton_builds", label: "Proton builds", bytes: 2.4 * GB, files: 1, percent: 0 },
    { id: "save_backups", label: "Save backups", bytes: 0.92 * GB, files: 1, percent: 0 },
    { id: "download_archives", label: "Download archives", bytes: 3.1 * GB, files: 1, percent: 0 },
  ];
  assert.equal(
    cleanupActionScopeNote({ id: "proton_builds", bytes: 1.1 * GB }, categories),
    "Frees 1.1 GB of the 2.4 GB in Proton builds.",
  );
  assert.equal(
    cleanupActionScopeNote({ id: "save_backups", bytes: 260 * 1024 * 1024 }, categories),
    "Frees 260 MB of the 942 MB in save backups.",
  );
  // Same size, nothing to free, or no matching category: no note.
  assert.equal(cleanupActionScopeNote({ id: "download_archives", bytes: 3.1 * GB }, categories), "");
  assert.equal(cleanupActionScopeNote({ id: "proton_builds", bytes: 0 }, categories), "");
  assert.equal(cleanupActionScopeNote({ id: "bepinex_cache", bytes: 10 }, categories), "");
});

test("groupStorageCategories keeps the large categories and folds the small ones into Other", () => {
  const cat = (id: string, bytes: number, percent: number): StorageCategoryReport => ({
    id,
    label: id,
    bytes,
    files: 0,
    percent,
  });
  const input = [
    cat("games", 600, 60),
    cat("torrent", 120, 12),
    cat("archives", 90, 9),
    cat("proton", 80, 8),
    cat("wine", 50, 5),
    cat("saves", 30, 3),
    cat("prefixes", 15, 1.5),
    cat("covers", 8, 0.8),
    cat("logs", 7, 0.7),
    cat("empty", 0, 0),
  ];
  const grouped = groupStorageCategories(input);
  assert.deepEqual(grouped.main.map((c) => c.id), ["games", "torrent", "archives", "proton", "wine", "saves"]);
  assert.deepEqual(grouped.other.map((c) => c.id), ["prefixes", "covers", "logs", "empty"]);
  assert.equal(grouped.otherBytes, 30);
  assert.equal(Math.round(grouped.otherPercent * 10) / 10, 3);

  // Under the share floor a category folds even when there is room.
  const small = groupStorageCategories([cat("games", 980, 98), cat("a", 10, 0.5), cat("b", 10, 0.5)]);
  assert.deepEqual(small.main.map((c) => c.id), ["games"]);
  assert.deepEqual(small.other.map((c) => c.id), ["a", "b"]);

  // A fold of one category is not worth it: it stays a row.
  const one = groupStorageCategories([cat("games", 990, 99), cat("a", 10, 1)], 1);
  assert.deepEqual(one.main.map((c) => c.id), ["games", "a"]);
  assert.deepEqual(one.other, []);
  assert.equal(one.otherBytes, 0);
});

test("splitLargestGames shows the largest measured games and keeps 0 B games behind Show all", () => {
  const row = (id: number, bytes: number): StorageGameRow => ({
    game_id: id,
    title: `Game ${id}`,
    root: "",
    root_source: "pattern",
    exe_path: "",
    picked: false,
    uncertain: false,
    unsafe: false,
    unsafe_reason: "",
    unavailable: false,
    changed: false,
    shared_with: [],
    shared_with_ids: [],
    parts: { game: bytes, saves: 0, player: 0, sandbox: 0, prefix: 0 },
    torrent_bytes: 0,
    total_bytes: bytes,
  });
  const ordered = [row(1, 50), row(2, 40), row(3, 30), row(4, 0), row(5, 0)];
  const split = splitLargestGames(ordered, 2);
  assert.deepEqual(split.top.map((r) => r.game_id), [1, 2]);
  assert.deepEqual(split.rest.map((r) => r.game_id), [3, 4, 5]);
  assert.equal(split.restNotCounted, 2);

  const all = splitLargestGames(ordered.slice(0, 3));
  assert.equal(all.top.length, 3);
  assert.deepEqual(all.rest, []);
  assert.equal(all.restNotCounted, 0);
});

test("single cleanup confirmation lists the previewed items and keeps danger off the safe tier", () => {
  const MB = 1024 * 1024;
  const risky = buildCleanupConfirmOptions({
    success: true,
    preview_id: "r",
    label: "Data of removed games",
    tier: "risky",
    count: 12,
    bytes: 380 * MB,
    warning: "Removed game sandboxes and built-in player storage can hold game saves.",
    what_stays: "Active games stay.",
    items: [
      { label: "Sandbox of a game removed 2026-11-02 21:14", bytes: 300 * MB },
      { label: "Built-in player data of a game removed 2026-10-18 09:40", bytes: 80 * MB },
    ],
    more_items: 10,
  });
  assert.equal(risky.danger, true);
  assert.match(
    risky.message,
    /^This will permanently delete 12 items \(380 MB\):\n• Sandbox of a game removed 2026-11-02 21:14 \(300 MB\)\n• Built-in player data of a game removed 2026-10-18 09:40 \(80\.0 MB\)\n• and 10 more items\n\nRemoved game sandboxes/,
  );

  const safe = buildCleanupConfirmOptions({
    success: true,
    preview_id: "s",
    label: "Cover cache",
    tier: "safe",
    count: 520,
    bytes: 145 * MB,
    items: [],
    more_items: 0,
  });
  assert.equal(safe.danger, false);
  assert.match(safe.message, /^This will permanently delete 520 items \(145 MB\)\./);

  const review = buildCleanupConfirmOptions({ success: true, preview_id: "v", tier: "review", count: 1, bytes: MB });
  assert.equal(review.danger, true);

  assert.equal(previewItemLines({ items: [{ label: "a.zip", bytes: 2048 }], more_items: 1 }), "• a.zip (2.0 KB)\n• and 1 more item");
  assert.equal(previewItemLines({}), "");

  const batch = buildSafeCleanupConfirmOptions(
    [{ success: true, preview_id: "a", label: "Cover cache", count: 1, bytes: MB }],
    [],
  );
  assert.equal(batch.danger, false);
});

test("cleanup previews expire a little before the backend's 15 minutes", () => {
  const start = 1_000_000;
  assert.equal(cleanupPreviewExpired(start, start + 60_000), false);
  assert.equal(cleanupPreviewExpired(start, start + CLEANUP_PREVIEW_TTL_MS - 31_000), false);
  assert.equal(cleanupPreviewExpired(start, start + CLEANUP_PREVIEW_TTL_MS - 30_000), true);
  assert.equal(cleanupPreviewExpired(start, start + CLEANUP_PREVIEW_TTL_MS + 1), true);

  assert.equal(cleanupRunError({ code: "preview_expired", error: "This cleanup preview has expired." }), PREVIEW_EXPIRED_MESSAGE);
  assert.equal(cleanupRunError({ error: "Disk is read-only." }), "Disk is read-only.");
  assert.equal(cleanupRunError({}), "Cleanup failed.");
});

test("MockStorage refuses a cleanup preview older than its time limit", () => {
  const storage = new MockStorage(() => false);
  const realNow = Date.now;
  try {
    assert.equal(storage.startScan().success, true);
    for (let i = 0; i < 3; i += 1) storage.getState();
    const preview = storage.previewCleanup("removed_game_data");
    assert.equal(preview.success, true);
    assert.deepEqual(
      preview.items?.map((item) => item.label),
      ["Sandbox of a game removed 2026-11-02 21:14", "Built-in player data of a game removed 2026-10-18 09:40"],
    );
    const later = realNow() + CLEANUP_PREVIEW_TTL_MS + 1000;
    Date.now = () => later;
    const run = storage.runCleanup(preview.preview_id ?? "");
    assert.equal(run.success, false);
    assert.equal(run.code, "preview_expired");
  } finally {
    Date.now = realNow;
  }
});

test("Clean buttons say why they cannot run instead of dropping out of the tab order", () => {
  const action: StorageCleanupAction = {
    id: "cover_cache",
    label: "Cover cache",
    tier: "safe",
    bytes: 100,
    count: 2,
    blocked_reason: "",
    what_stays: "",
    warning: "",
  };
  assert.equal(cleanupActionBlockedReason(action, ""), "");
  assert.equal(cleanupActionBlockedReason(action, "cover_cache"), "This cleanup is running.");
  assert.equal(cleanupActionBlockedReason(action, "safe"), "Wait for the running cleanup to finish.");
  assert.equal(cleanupActionBlockedReason({ ...action, blocked_reason: "A storage scan is running." }, ""), "A storage scan is running.");
  assert.equal(cleanupActionBlockedReason({ ...action, bytes: 0, count: 0 }, ""), NOTHING_TO_FREE);
});

test("Wine and Proton rows wait for the platform and stay hidden when it cannot be read", () => {
  assert.equal(storageWineProton(null), false);
  assert.equal(storageWineProton(undefined), false);
  assert.equal(storageWineProton({ wine_proton: false }), false);
  assert.equal(storageWineProton({ wine_proton: true }), true);
});
