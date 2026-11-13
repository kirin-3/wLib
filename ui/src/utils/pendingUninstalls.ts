// SPDX-License-Identifier: GPL-3.0-or-later
// Uninstall waits for the Undo offer: the game changes in the library at once,
// and the backend touches no file until the toast expires. Nothing is stored,
// so a reload before then shows the game as it was.
import { reactive } from "vue";
import type { GameRecord, UninstallGamesResponse, UninstallOptions } from "../services/api.ts";
import { notifyError, notifyUndo } from "./toast.ts";

/** Game id -> whether the uninstall also removes the game from the library. */
export const pendingUninstalls = reactive(new Map<number, boolean>());
// Uninstalled games stay changed until a loaded game list shows the change.
const committed = new Set<number>();

export const isPendingUninstall = (id: number): boolean => pendingUninstalls.has(id);
/** The pending uninstall removes the game from the library, so it is hidden. */
export const isPendingUninstallRemoval = (id: number): boolean => pendingUninstalls.get(id) === true;

/** The game as the library shows it while its uninstall is pending: wishlisted. */
export const withPendingUninstall = (game: GameRecord): GameRecord =>
  pendingUninstalls.get(game.id) === false && game.exe_path ? { ...game, exe_path: "", launch_targets: [] } : game;

export const cancelUninstall = (ids: number[]): void => {
  for (const id of ids) pendingUninstalls.delete(id);
};

/** Drop the pending mark once a loaded list shows the uninstall. */
export const settleUninstalls = (loaded: Iterable<GameRecord>): void => {
  const byId = new Map<number, GameRecord>();
  for (const game of loaded) byId.set(game.id, game);
  for (const id of [...committed]) {
    const game = byId.get(id);
    if (game && game.exe_path.trim()) continue;
    committed.delete(id);
    pendingUninstalls.delete(id);
  }
};

type UninstallGames = (ids: number[], options: UninstallOptions) => Promise<UninstallGamesResponse>;

export const uninstallToastText = (games: GameRecord[], removeFromLibrary: boolean): string => {
  const suffix = removeFromLibrary ? " and removed from library" : "";
  return games.length === 1 ? `${games[0]!.title} uninstalled${suffix}.` : `${games.length} games uninstalled${suffix}.`;
};

export const scheduleUninstall = (games: GameRecord[], options: UninstallOptions, uninstallGames?: UninstallGames): void => {
  const chosen = games.filter((game) => !pendingUninstalls.has(game.id));
  const ids = chosen.map((game) => game.id);
  if (!ids.length) return;
  for (const id of ids) pendingUninstalls.set(id, options.remove_from_library);
  notifyUndo(uninstallToastText(chosen, options.remove_from_library), async () => cancelUninstall(ids), {
    onExpire: async () => {
      const run: UninstallGames = uninstallGames || (async (list, opts) => (await import("../services/api")).api.uninstallGames(list, opts));
      let result: UninstallGamesResponse;
      try {
        result = await run(ids, options);
      } catch (error) {
        result = { success: false, error: String(error), results: ids.map((id) => ({ game_id: id, success: false, error: String(error) })) };
      }
      const results = result.results ?? ids.map((id) => ({ game_id: id, success: false, error: result.error || "" }));
      const failed = results.filter((item) => !item.success);
      for (const item of results) {
        if (item.success) committed.add(item.game_id);
        else pendingUninstalls.delete(item.game_id);
      }
      if (failed.length) {
        const reason = failed[0]?.error || result.error || "";
        notifyError(`Could not uninstall ${failed.length} game${failed.length === 1 ? "" : "s"}.${reason ? ` ${reason}` : ""}`);
      }
      if (typeof window !== "undefined") window.dispatchEvent(new Event("wlib-refresh-library"));
    },
  });
};
