// SPDX-License-Identifier: GPL-3.0-or-later
import assert from "node:assert/strict";
import test from "node:test";
import {
  addTagCsv, engineSnapshot, launchModeApplicable, launchModeSnapshot, removeTagCsv, runBulk, runBulkSaveBackup,
  runtimeSnapshot, sandboxSnapshot, statusSnapshot, tagsSnapshot, versionSnapshot, wineApplicable,
} from "./bulkActions.ts";
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

test("wine and launch-mode helpers skip games they cannot affect", () => {
  const running = new Set([9]);
  const g = (over: Partial<GameRecord>): GameRecord => ({ ...game(1), ...over });
  assert.equal(wineApplicable(g({ launch_mode: "wine_proton" }), running), true);
  assert.equal(wineApplicable(g({}), running), true);
  for (const mode of ["native", "custom", "rpgmaker_linux"] as const) assert.equal(wineApplicable(g({ launch_mode: mode }), running), false);
  assert.equal(wineApplicable(g({ sandbox_enabled: true }), running), false);
  assert.equal(wineApplicable(g({ id: 9 }), running), false);
  assert.equal(launchModeApplicable(g({ launch_mode: "auto" }), "native", running), true);
  assert.equal(launchModeApplicable(g({ launch_mode: "native" }), "native", running), false);
  assert.equal(launchModeApplicable(g({ id: 9 }), "native", running), false);
});

test("tag helpers are case-insensitive and return null for no-ops", () => {
  const g = { ...game(1), tags: "Sandbox, Fantasy" };
  assert.equal(addTagCsv(g, "sandbox"), null);
  assert.equal(addTagCsv(g, " Mystery "), "Sandbox, Fantasy, Mystery");
  assert.equal(addTagCsv(g, "  "), null);
  assert.equal(removeTagCsv(g, "SANDBOX"), "Fantasy");
  assert.equal(removeTagCsv(g, "other"), null);
  assert.equal(removeTagCsv({ ...game(2), tags: "Only" }, "only"), "");
  assert.equal(addTagCsv({ ...game(3), tags: ["a", "b"] }, "c"), "a, b, c");
});

test("runtime, sandbox, tags and engine snapshots restore previous values", async () => {
  const calls: Record<string, unknown>[] = [];
  const update = async (_id: number, fields: Record<string, unknown>) => { calls.push(fields); return { success: true }; };
  const g: GameRecord = { ...game(1), custom_prefix: "/p", proton_version: "GE", launch_mode: "wine_proton", sandbox_enabled: true, tags: "a,b", tags_user_edited: false, engine: "Unity" };
  await runtimeSnapshot(g, update)();
  await launchModeSnapshot(g, update)();
  await sandboxSnapshot(g, update)();
  await tagsSnapshot(g, update)();
  await engineSnapshot(g, update)();
  assert.deepEqual(calls, [
    { custom_prefix: "/p", proton_version: "GE" }, { launch_mode: "wine_proton" }, { sandbox_enabled: true },
    { tags: "a, b", tags_user_edited: false }, { engine: "Unity" },
  ]);
});

test("bulk save backup skips unavailable games and continues past failures", async () => {
  const outcomes: Record<number, { success: boolean; error?: string } | "throw"> = {
    1: { success: true }, 2: { success: false, error: "Choose a saves folder first" },
    3: { success: false, error: "A save operation is already running for this game" },
    4: { success: false, error: "disk full" }, 5: "throw", 6: { success: true },
  };
  const seen: number[] = [];
  const result = await runBulkSaveBackup([1, 2, 3, 4, 5, 6].map(game), async (id) => {
    seen.push(id);
    const outcome = outcomes[id]!;
    if (outcome === "throw") throw new Error("bridge");
    return outcome;
  });
  assert.deepEqual(result, { backedUp: 2, skipped: 2, failed: 2 });
  assert.deepEqual(seen, [1, 2, 3, 4, 5, 6]);
});
