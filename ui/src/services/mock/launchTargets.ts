// SPDX-License-Identifier: GPL-3.0-or-later
import type { LaunchTarget } from "../api";
import { isRecord } from "./games";

const MOCK_LAUNCH_TARGETS_STORAGE_KEY = "wlib-mock-launch-targets";

const normalizeMockLaunchTarget = (value: unknown): LaunchTarget | null => {
  if (!isRecord(value)) return null;
  const id = Number(value.id);
  const gameId = Number(value.game_id);
  const label = typeof value.label === "string" ? value.label.trim() : "";
  const exePath = typeof value.exe_path === "string" ? value.exe_path.trim() : "";
  const sortOrder = Number(value.sort_order);
  if (!Number.isFinite(id) || !Number.isFinite(gameId) || !label || !exePath) {
    return null;
  }
  return {
    id,
    game_id: gameId,
    label,
    exe_path: exePath,
    sort_order: Number.isFinite(sortOrder) ? Math.max(0, Math.trunc(sortOrder)) : 0,
    created_at: typeof value.created_at === "string" ? value.created_at : "",
    updated_at: typeof value.updated_at === "string" ? value.updated_at : "",
  };
};

// Seeded mock games (tests/library_ui_check.js) may embed launch targets
// directly. Move them into the CRUD store once so listings and target
// mutations work on the same records.
const migrateEmbeddedMockLaunchTargets = (targets: LaunchTarget[]): LaunchTarget[] => {
  try {
    const games: unknown = JSON.parse(localStorage.getItem("wlib-mock-games") || "[]");
    if (!Array.isArray(games) || !games.some((game) => isRecord(game) && "launch_targets" in game)) {
      return targets;
    }
    const migrated = [...targets];
    const nextGames = games.map((game: unknown) => {
      if (!isRecord(game) || !("launch_targets" in game)) return game;
      const { launch_targets: embedded, ...rest } = game;
      for (const value of Array.isArray(embedded) ? embedded : []) {
        const target = normalizeMockLaunchTarget(isRecord(value) ? { ...value, game_id: game.id } : value);
        if (!target) continue;
        const existing = migrated.find((entry) => entry.id === target.id);
        if (existing?.game_id === target.game_id) continue;
        if (existing) target.id = nextMockLaunchTargetId(migrated);
        migrated.push(target);
      }
      return rest;
    });
    writeMockLaunchTargets(migrated);
    localStorage.setItem("wlib-mock-games", JSON.stringify(nextGames));
    return migrated;
  } catch (_error) {
    return targets;
  }
};

export const readMockLaunchTargets = (): LaunchTarget[] => {
  let targets: LaunchTarget[] = [];
  try {
    const raw = localStorage.getItem(MOCK_LAUNCH_TARGETS_STORAGE_KEY);
    const parsed = raw ? (JSON.parse(raw) as unknown) : [];
    targets = Array.isArray(parsed)
      ? parsed.map(normalizeMockLaunchTarget).filter((target): target is LaunchTarget => target !== null)
      : [];
  } catch (_error) {
    // Fall through with an empty store.
  }
  return migrateEmbeddedMockLaunchTargets(targets);
};

export const writeMockLaunchTargets = (targets: LaunchTarget[]): void => {
  try {
    localStorage.setItem(MOCK_LAUNCH_TARGETS_STORAGE_KEY, JSON.stringify(targets));
  } catch (_error) {
    // Ignore browser mock persistence failures and keep the desktop contract unchanged.
  }
};

export const nextMockLaunchTargetId = (targets: LaunchTarget[]): number => {
  return targets.reduce((max, target) => Math.max(max, target.id), 0) + 1;
};

export const sortMockLaunchTargets = (targets: LaunchTarget[]): LaunchTarget[] => {
  return [...targets].sort((a, b) => a.sort_order - b.sort_order || a.id - b.id);
};
