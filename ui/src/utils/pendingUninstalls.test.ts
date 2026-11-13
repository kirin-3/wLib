// SPDX-License-Identifier: GPL-3.0-or-later
import assert from "node:assert/strict";
import test from "node:test";
import type { GameRecord, UninstallOptions } from "../services/api";
import { isPendingUninstall, isPendingUninstallRemoval, scheduleUninstall, settleUninstalls, withPendingUninstall } from "./pendingUninstalls.ts";
import { dismissToast, runNewestUndo, toasts } from "./toast.ts";

const game = (id: number, exe = "/lib/Game/Game.exe"): GameRecord => ({ id, title: `Game ${id}`, exe_path: exe });
const keep: UninstallOptions = { backup_saves: true, remove_from_library: false };
const flush = async () => {
  for (let i = 0; i < 4; i++) await new Promise<void>((resolve) => queueMicrotask(resolve));
};

test("the game changes at once and Undo leaves it as it was", async () => {
  let calls = 0;
  scheduleUninstall([game(1)], keep, async () => { calls++; return { success: true }; });
  assert.equal(isPendingUninstall(1), true);
  assert.equal(withPendingUninstall(game(1)).exe_path, "");
  assert.equal(isPendingUninstallRemoval(1), false);
  await runNewestUndo();
  assert.equal(isPendingUninstall(1), false);
  assert.equal(withPendingUninstall(game(1)).exe_path, "/lib/Game/Game.exe");
  assert.equal(calls, 0);
});

test("expiry calls uninstall once for every game and settles on reload", async (context) => {
  context.mock.timers.enable({ apis: ["setTimeout"] });
  const calls: [number[], UninstallOptions][] = [];
  const options: UninstallOptions = { backup_saves: false, remove_from_library: true };
  scheduleUninstall([game(2), game(3)], options, async (ids, opts) => {
    calls.push([ids, opts]);
    return { success: true, uninstalled: 2, failed: 0, results: ids.map((id) => ({ game_id: id, success: true, error: "" })) };
  });
  assert.equal(isPendingUninstallRemoval(2), true);
  context.mock.timers.tick(8000);
  await flush();
  assert.deepEqual(calls, [[[2, 3], options]]);
  // A list fetched before the uninstall still has the games.
  settleUninstalls([game(2), game(3)]);
  assert.equal(isPendingUninstall(2), true);
  settleUninstalls([]);
  assert.equal(isPendingUninstall(2), false);
  assert.equal(isPendingUninstall(3), false);
});

test("a failed game comes back and an error toast names the reason", async (context) => {
  context.mock.timers.enable({ apis: ["setTimeout"] });
  scheduleUninstall([game(4), game(5)], keep, async () => ({
    success: false,
    uninstalled: 1,
    failed: 1,
    error: "Save backup failed",
    results: [
      { game_id: 4, success: true, error: "" },
      { game_id: 5, success: false, error: "Save backup failed, so nothing was deleted: disk full" },
    ],
  }));
  context.mock.timers.tick(8000);
  await flush();
  assert.equal(isPendingUninstall(5), false);
  assert.equal(isPendingUninstall(4), true);
  assert.ok(toasts.some((toast) => toast.type === "error" && toast.message === "Could not uninstall 1 game. Save backup failed, so nothing was deleted: disk full"));
  settleUninstalls([game(4, "")]);
  assert.equal(isPendingUninstall(4), false);
  for (const toast of [...toasts]) dismissToast(toast.id);
});
