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

/** Launch modes that never go through Wine/Proton. */
const NON_WINE_MODES = new Set(["native", "custom", "rpgmaker_linux"]);
type RunningIds = ReadonlySet<number> | readonly number[];
const isRunning = (id: number, running: RunningIds): boolean =>
  Array.isArray(running) ? running.includes(id) : (running as ReadonlySet<number>).has(id);

/** A shared prefix/runner only affects idle, unsandboxed Wine/Proton games. */
export const wineApplicable = (game: GameRecord, running: RunningIds): boolean =>
  !NON_WINE_MODES.has(game.launch_mode || "auto") && !game.sandbox_enabled && !isRunning(game.id, running);

export const launchModeApplicable = (game: GameRecord, mode: string, running: RunningIds): boolean =>
  !isRunning(game.id, running) && (game.launch_mode || "auto") !== mode;

const splitTags = (tags: GameRecord["tags"]): string[] => {
  const raw = typeof tags === "string" ? tags.split(",") : Array.isArray(tags) ? tags : [];
  const seen = new Set<string>();
  const out: string[] = [];
  for (const entry of raw) {
    const tag = typeof entry === "string" ? entry.trim() : "";
    if (tag && !seen.has(tag.toLowerCase())) { seen.add(tag.toLowerCase()); out.push(tag); }
  }
  return out;
};

/** New comma-separated tags with `tag` added, or null when the game has it. */
export const addTagCsv = (game: GameRecord, tag: string): string | null => {
  const wanted = tag.trim();
  if (!wanted) return null;
  const tags = splitTags(game.tags);
  if (tags.some((t) => t.toLowerCase() === wanted.toLowerCase())) return null;
  return [...tags, wanted].join(", ");
};

/** New comma-separated tags with `tag` removed, or null when the game lacks it. */
export const removeTagCsv = (game: GameRecord, tag: string): string | null => {
  const key = tag.trim().toLowerCase();
  const tags = splitTags(game.tags);
  if (!key || !tags.some((t) => t.toLowerCase() === key)) return null;
  return tags.filter((t) => t.toLowerCase() !== key).join(", ");
};

export const runtimeSnapshot = (game: GameRecord, update: UpdateGame): Restore => {
  const customPrefix = game.custom_prefix || "";
  const protonVersion = game.proton_version || "";
  return () => update(game.id, { custom_prefix: customPrefix, proton_version: protonVersion });
};
export const launchModeSnapshot = (game: GameRecord, update: UpdateGame): Restore => {
  const launchMode = game.launch_mode || "auto";
  return () => update(game.id, { launch_mode: launchMode });
};
export const sandboxSnapshot = (game: GameRecord, update: UpdateGame): Restore => {
  const enabled = !!game.sandbox_enabled;
  return () => update(game.id, { sandbox_enabled: enabled });
};
export const tagsSnapshot = (game: GameRecord, update: UpdateGame): Restore => {
  const tags = splitTags(game.tags).join(", ");
  const edited = !!game.tags_user_edited;
  return () => update(game.id, { tags, tags_user_edited: edited });
};
export const engineSnapshot = (game: GameRecord, update: UpdateGame): Restore => {
  const engine = game.engine || "";
  return () => update(game.id, { engine });
};

export interface BulkSaveBackupResult {
  backedUp: number;
  skipped: number;
  failed: number;
}

/** Back up saves one game at a time. No Undo: a backup only adds a snapshot. */
export async function runBulkSaveBackup(
  games: GameRecord[],
  backup: (id: number) => Promise<Result>,
): Promise<BulkSaveBackupResult> {
  const counts: BulkSaveBackupResult = { backedUp: 0, skipped: 0, failed: 0 };
  for (const game of games) {
    try {
      const result = await backup(game.id);
      if (result.success) counts.backedUp++;
      else if (/choose a saves folder|already running/i.test(result.error || "")) counts.skipped++;
      else counts.failed++;
    } catch { counts.failed++; }
  }
  return counts;
}
