// SPDX-License-Identifier: GPL-3.0-or-later
import { ref } from "vue";

// Shared by every view, so covers can pause while any game runs.
export const runningGameIds = ref(new Set<number>());

let refreshRequest = 0;

export async function refreshRunningGames(load: () => Promise<number[]>): Promise<void> {
  const request = ++refreshRequest;
  try {
    const ids = await load();
    if (request === refreshRequest) runningGameIds.value = new Set(ids);
  } catch (error) {
    console.error("Failed to load running games", error);
  }
}

interface PlaytimeTick {
  gameId?: unknown;
  isFinal?: unknown;
}

// A final tick means the game exited; any other tick proves it is running,
// including a game launched from a view that never asked the backend.
export function applyPlaytimeTick(detail: PlaytimeTick | null | undefined): void {
  const gameId = Number(detail?.gameId);
  if (!Number.isInteger(gameId)) return;
  if (detail?.isFinal) {
    // A refresh already in flight may carry a stale list that still has this game.
    refreshRequest++;
    runningGameIds.value.delete(gameId);
  } else if (!runningGameIds.value.has(gameId)) {
    // A refresh already in flight may carry a list from before the launch.
    refreshRequest++;
    runningGameIds.value.add(gameId);
  }
}

let listening = false;

export function startRunningGamesTracking(): void {
  if (listening || typeof window === "undefined") return;
  listening = true;
  window.addEventListener("wlib-playtime-tick", (event) => applyPlaytimeTick((event as CustomEvent).detail));
}
