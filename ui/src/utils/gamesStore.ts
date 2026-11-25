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

/** The bridge's failure shape (`{success: false, error}`), used in place of the list. */
export interface FailedGamesLoad {
  success: false;
  error?: string;
}

const isFailedLoad = (value: unknown): value is FailedGamesLoad =>
  typeof value === "object" && value !== null && !Array.isArray(value) && (value as { success?: unknown }).success === false;

/**
 * The cause of a failed library load as one short line, or "" when there is
 * none to show. Takes a thrown error (the bridge rejects with an Error or an
 * object with a message) or the bridge's `{success: false, error}` result.
 */
export const loadFailureMessage = (failure: unknown): string => {
  let text = "";
  if (typeof failure === "string") text = failure;
  else if (isFailedLoad(failure)) text = typeof failure.error === "string" ? failure.error : "";
  else if (typeof failure === "object" && failure !== null && typeof (failure as { message?: unknown }).message === "string") {
    text = (failure as { message: string }).message;
  }
  // A Python traceback can follow the message; the first line names the cause.
  return text.trim().split("\n", 1)[0]?.trim() ?? "";
};

class GamesLoadFailure extends Error {}

export interface GamesStore<T extends Identified> {
  /** Shallow: replace a game object to change it (see `patch`), never edit it in place. */
  games: ShallowRef<T[]>;
  /** True after the first successful load. */
  loaded: Ref<boolean>;
  /** The cause of the last failed load ("" after a successful one, or when the cause is unknown). */
  error: Ref<string>;
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
  fetchGames: () => Promise<T[] | FailedGamesLoad | null | undefined>,
  onLoaded: (games: T[]) => void = () => {},
): GamesStore<T> => {
  const games = shallowRef<T[]>([]) as ShallowRef<T[]>;
  const loaded = ref(false);
  const error = ref("");
  const snapshots = new WeakMap<object, string>();
  let scheduled: Promise<boolean> | null = null;
  let last: Promise<boolean> = Promise.resolve(true);

  const load = async (): Promise<boolean> => {
    scheduled = null;
    try {
      const result = await fetchGames();
      if (isFailedLoad(result)) throw new GamesLoadFailure(loadFailureMessage(result));
      const data = result || [];
      const next = reconcileGames(games.value, data, snapshots);
      const unchanged =
        next.length === games.value.length && next.every((game, index) => game === games.value[index]);
      if (!unchanged) games.value = next;
      loaded.value = true;
      error.value = "";
      onLoaded(games.value);
      return true;
    } catch (failure) {
      console.error("Failed to load games", failure);
      error.value = loadFailureMessage(failure);
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

  return { games, loaded, error, refresh, patch };
};

