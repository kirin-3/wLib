// SPDX-License-Identifier: GPL-3.0-or-later
import type { GameRecord } from "../api";

export const isRecord = (value: unknown): value is Record<string, unknown> => {
  return typeof value === "object" && value !== null;
};

export const readMockGames = (): GameRecord[] => {
  try {
    const games: unknown = JSON.parse(localStorage.getItem("wlib-mock-games") || "[]");
    return Array.isArray(games) ? games.filter((game): game is GameRecord => isRecord(game) && typeof game.id === "number" && typeof game.title === "string" && typeof game.exe_path === "string") : [];
  } catch {
    return [];
  }
};
