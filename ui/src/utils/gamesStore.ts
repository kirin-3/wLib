// SPDX-License-Identifier: GPL-3.0-or-later
import { ref, shallowRef, type Ref, type ShallowRef } from "vue";

interface Identified {
  id: number;
}

/**
 * Build the new list, keeping the previous object for every game whose data
 * did not change. A card whose game object is the same does not render again.
 * `snapshots` holds the JSON text each kept object was last loaded with.
 */
export const reconcileGames = <T extends Identified>(
  previous: readonly T[],
  loaded: readonly T[],
  snapshots: WeakMap<object, string>,
): T[] => {
  const previousById = new Map(previous.map((game) => [game.id, game]));
  return loaded.map((game) => {
    const text = JSON.stringify(game);
    const old = previousById.get(game.id);
    if (old && snapshots.get(old) === text) return old;
    snapshots.set(game, text);
    return game;
  });
};

export interface GamesStore<T extends Identified> {
  /** Shallow: replace a game object to change it (see `patch`), never edit it in place. */
  games: ShallowRef<T[]>;
  /** True after the first successful load. */
  loaded: Ref<boolean>;
  /** Load the library; resolves false when the load failed (the old list stays). */
  refresh: () => Promise<boolean>;
  /** Change fields of one game locally, before or without a reload. */
  patch: (id: number, fields: Partial<T>) => void;
}

/**
 * One library list for every view. Calls to `refresh` made in the same tick
 * share one load. A call made while a load runs gets one more load after it,
 * because the running one may have read the library before the change.
 */
export const createGamesStore = <T extends Identified>(
  fetchGames: () => Promise<T[] | null | undefined>,
  onLoaded: (games: T[]) => void = () => {},
): GamesStore<T> => {
  const games = shallowRef<T[]>([]) as ShallowRef<T[]>;
  const loaded = ref(false);
  const snapshots = new WeakMap<object, string>();
  let scheduled: Promise<boolean> | null = null;
  let last: Promise<boolean> = Promise.resolve(true);

  const load = async (): Promise<boolean> => {
    scheduled = null;
    try {
      const data = (await fetchGames()) || [];
      const next = reconcileGames(games.value, data, snapshots);
      const unchanged =
        next.length === games.value.length && next.every((game, index) => game === games.value[index]);
      if (!unchanged) games.value = next;
      loaded.value = true;
      onLoaded(games.value);
      return true;
    } catch (error) {
      console.error("Failed to load games", error);
      return false;
    }
  };

  const refresh = (): Promise<boolean> => {
    if (!scheduled) {
      scheduled = last.then(load);
      last = scheduled;
    }
    return scheduled;
  };

  const patch = (id: number, fields: Partial<T>): void => {
    if (!games.value.some((game) => game.id === id)) return;
    games.value = games.value.map((game) => (game.id === id ? { ...game, ...fields } : game));
  };

  return { games, loaded, refresh, patch };
};

/** How many library cards are in the page at first, and per later step. */
export const RENDER_BATCH = 120;

/** Grow the rendered count when the user is near the end of what is rendered. */
export const nextRenderLimit = (limit: number, total: number, distanceToEnd: number, viewport: number): number =>
  limit < total && distanceToEnd < viewport * 2 ? Math.min(total, limit + RENDER_BATCH) : limit;
