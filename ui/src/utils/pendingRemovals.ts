// SPDX-License-Identifier: GPL-3.0-or-later
import { reactive } from "vue";
import type { GameRecord } from "../services/api.ts";
import { notifyError, notifyUndo } from "./toast.ts";

export const pendingRemovalIds = reactive(new Set<number>());
// Deleted games stay hidden until a loaded game list no longer has them, so a
// list fetched before the delete cannot show them again.
const committedIds = new Set<number>();
export const isPendingRemoval = (id: number): boolean => pendingRemovalIds.has(id);
export const cancelRemoval = (ids: number[]): void => { for (const id of ids) pendingRemovalIds.delete(id); };
export const settleRemovals = (loadedIds: Iterable<number>): void => {
  const loaded = new Set(loadedIds);
  for (const id of [...committedIds]) {
    if (loaded.has(id)) continue;
    committedIds.delete(id);
    pendingRemovalIds.delete(id);
  }
};

type DeleteGame = (id: number) => Promise<{ success?: boolean | undefined; error?: string | undefined }>;
export const scheduleRemoval = (games: GameRecord[], deleteGame?: DeleteGame): void => {
  const ids = games.map((game) => game.id).filter((id) => !pendingRemovalIds.has(id));
  if (!ids.length) return;
  ids.forEach((id) => pendingRemovalIds.add(id));
  notifyUndo(ids.length === 1 ? `${games.find((game) => game.id === ids[0])?.title} removed from library.` : `${ids.length} games removed from library.`, async () => cancelRemoval(ids), {
    onExpire: async () => {
      const remove: DeleteGame = deleteGame || (async (id) => (await import("../services/api")).api.deleteGame(id));
      let failed = 0;
      for (const id of ids) {
        let removed = false;
        try { removed = !!(await remove(id)).success; }
        catch { removed = false; }
        if (removed) committedIds.add(id);
        else { failed++; pendingRemovalIds.delete(id); }
      }
      if (failed) notifyError(`Could not remove ${failed} game${failed === 1 ? "" : "s"}.`);
      if (typeof window !== "undefined") window.dispatchEvent(new Event("wlib-refresh-library"));
    },
  });
};
