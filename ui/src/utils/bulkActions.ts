// SPDX-License-Identifier: GPL-3.0-or-later
import type { GameRecord } from "../services/api.ts";

type Result = { success?: boolean | undefined; error?: string | undefined };
type Restore = () => Promise<Result>;
export interface BulkResult {
  changed: number;
  skipped: number;
  failed: number;
  undo: () => Promise<void>;
}

/** Serialize bridge calls, retain snapshots only for successful changes. */
export async function runBulk(
  games: GameRecord[],
  apply: (game: GameRecord) => Promise<Result | null>,
  snapshot: (game: GameRecord) => Restore,
): Promise<BulkResult> {
  const restores: Restore[] = [];
  let skipped = 0;
  let failed = 0;
  for (const game of games) {
    const restore = snapshot(game);
    try {
      const result = await apply(game);
      if (result === null) skipped++;
      else if (result.success) restores.push(restore);
      else failed++;
    } catch { failed++; }
  }
  const refresh = () => {
    if (typeof window !== "undefined") window.dispatchEvent(new Event("wlib-refresh-library"));
  };
  if (restores.length) refresh();
  return {
    changed: restores.length, skipped, failed,
    undo: async () => {
      let failures = 0;
      for (const restore of restores) {
        try { if (!(await restore()).success) failures++; }
        catch { failures++; }
      }
      refresh();
      if (failures) throw new Error(`Could not restore ${failures} game${failures === 1 ? "" : "s"}.`);
    },
  };
}

type UpdateGame = (id: number, fields: Record<string, unknown>) => Promise<Result>;
export const versionSnapshot = (game: GameRecord, update: UpdateGame): Restore => {
  const version = game.version || "";
  const skipped = game.skipped_version || "";
  return async () => {
    const result = await update(game.id, { version });
    if (!result.success || !skipped) return result;
    return update(game.id, { skipped_version: skipped });
  };
};
export const statusSnapshot = (game: GameRecord, update: UpdateGame): Restore => {
  const playStatus = game.play_status || "";
  const finishedVersion = game.finished_version || "";
  return () => update(game.id, { play_status: playStatus, finished_version: finishedVersion });
};
