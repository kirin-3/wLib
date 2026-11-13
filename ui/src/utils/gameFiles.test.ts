// SPDX-License-Identifier: GPL-3.0-or-later
import assert from "node:assert/strict";
import test from "node:test";
import type { GameFilePlanEntry, MoveStatus } from "../services/api";
import { backupDefault, confirmReason, moveProgressText, movePollMs, moveStateFor, moveSummaryText, showBackupOption } from "./gameFiles.ts";

const entry = (over: Partial<GameFilePlanEntry> = {}): GameFilePlanEntry => ({
  game_id: 1, title: "Game", root: "/lib/Game", root_source: "exe_dir", removable: true, reason: "",
  saves_folder: "", saves_inside_root: false, blocked_reason: "", ...over,
});

test("backup default follows saves inside the root of a game that can go", () => {
  assert.equal(backupDefault([entry({ saves_folder: "/lib/Game/www/save", saves_inside_root: true })]), true);
  assert.equal(backupDefault([entry({ saves_folder: "/home/u/.renpy/Game" })]), false);
  // A refused game does not decide the default.
  assert.equal(backupDefault([entry({ saves_folder: "/x", saves_inside_root: true, blocked_reason: "Running" }), entry({ game_id: 2 })]), false);
  assert.equal(showBackupOption([entry()]), false);
  assert.equal(showBackupOption([entry({ saves_folder: "/home/u/.renpy/Game" })]), true);
});

test("confirm is disabled while loading or when no game can go", () => {
  assert.equal(confirmReason([], true, "uninstall"), "Checking the game folders...");
  assert.equal(confirmReason([entry({ removable: false, reason: "Refusing to remove the library folder." })], false, "uninstall"), "Refusing to remove the library folder.");
  assert.equal(confirmReason([entry({ blocked_reason: "A" }), entry({ game_id: 2, blocked_reason: "B" })], false, "move"), "No selected game can move.");
  assert.equal(confirmReason([entry({ move_reason: "The target folder already exists." })], false, "move"), "The target folder already exists.");
  assert.equal(confirmReason([entry(), entry({ game_id: 2, blocked_reason: "B" })], false, "uninstall"), "");
});

const status = (over: Partial<MoveStatus> = {}): MoveStatus => ({ running: true, current: null, waiting: [], position: 0, total: 0, ...over });

test("moveStateFor reports moving percent and waiting", () => {
  const s = status({ current: { game_id: 3, title: "G", bytes_done: 250, bytes_total: 1000, speed_bps: 0 }, waiting: [4] });
  assert.deepEqual(moveStateFor(s, 3), { state: "moving", percent: 25 });
  assert.deepEqual(moveStateFor(s, 4), { state: "waiting", percent: 0 });
  assert.deepEqual(moveStateFor(s, 5), { state: "", percent: 0 });
  assert.deepEqual(moveStateFor(null, 3), { state: "", percent: 0 });
  const rename = status({ current: { game_id: 3, title: "G", bytes_done: 0, bytes_total: 0, speed_bps: 0 } });
  assert.deepEqual(moveStateFor(rename, 3), { state: "moving", percent: 0 });
});

test("poll every 500 ms while moves run and every 5 s otherwise", () => {
  assert.equal(movePollMs(status()), 500);
  assert.equal(movePollMs(status({ running: false })), 5000);
  assert.equal(movePollMs(null), 5000);
});

test("summary toast text", () => {
  assert.deepEqual(moveSummaryText({ moved: 4, skipped: 1, cancelled: 0, failed: 0, first_reason: "The target folder already exists." }), {
    text: "4 games moved, 1 skipped. The target folder already exists.", type: "info",
  });
  assert.deepEqual(moveSummaryText({ moved: 1, skipped: 0, cancelled: 0, failed: 0, first_reason: "" }), { text: "1 game moved.", type: "success" });
  assert.equal(moveSummaryText({ moved: 0, skipped: 0, cancelled: 2, failed: 1, first_reason: "Disk error" }).text, "2 cancelled, 1 failed. Disk error");
  assert.equal(moveSummaryText({ moved: 0, skipped: 0, cancelled: 2, failed: 1, first_reason: "Disk error" }).type, "error");
});

test("progress text", () => {
  const s = status({ current: { game_id: 3, title: "G", bytes_done: 1024 ** 3, bytes_total: 4 * 1024 ** 3, speed_bps: 85 * 1024 ** 2 } });
  assert.equal(moveProgressText(s), "1.0 GB of 4.0 GB · 85.0 MB/s");
  assert.equal(moveProgressText(status({ current: { game_id: 3, title: "G", bytes_done: 0, bytes_total: 0, speed_bps: 0 } })), "Moving on the same drive...");
});
