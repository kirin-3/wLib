// SPDX-License-Identifier: GPL-3.0-or-later
import assert from "node:assert/strict";
import test from "node:test";
import { runBulk, statusSnapshot, versionSnapshot } from "./bulkActions.ts";
import type { GameRecord } from "../services/api.ts";
const game = (id: number): GameRecord => ({ id, title: String(id), exe_path: "", version: "1", skipped_version: "2", play_status: "Playing", finished_version: "0" });

test("bulk actions serialize, count failures and restore only successes", async () => {
  const order: string[] = [];
  const result = await runBulk([game(1), game(2), game(3)], async (g) => {
    order.push(`apply${g.id}`);
    return g.id === 2 ? null : { success: g.id !== 3 };
  }, (g) => async () => { order.push(`undo${g.id}`); return { success: true }; });
  assert.deepEqual([result.changed, result.skipped, result.failed], [1, 1, 1]);
  await result.undo();
  assert.deepEqual(order, ["apply1", "apply2", "apply3", "undo1"]);
});

test("snapshots restore version before skipped version and status with finished version", async () => {
  const calls: Record<string, unknown>[] = [];
  const update = async (_id: number, fields: Record<string, unknown>) => { calls.push(fields); return { success: true }; };
  await versionSnapshot(game(1), update)();
  await statusSnapshot(game(1), update)();
  assert.deepEqual(calls, [{ version: "1" }, { skipped_version: "2" }, { play_status: "Playing", finished_version: "0" }]);
});
