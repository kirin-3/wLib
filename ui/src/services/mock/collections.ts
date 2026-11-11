// SPDX-License-Identifier: GPL-3.0-or-later
import type { GameRecord } from "../api";
import { readMockGames } from "./games";
import { readMockSettings, writeMockSettings } from "./settings";

// Mirrors the backend's name rules: 1–40 characters, unique ignoring case,
// and "All" reserved for the built-in tab.
export const validateMockCollectionList = (names: readonly string[]): string => {
  const seen = new Set(["all"]);
  for (const raw of names) {
    const name = raw.trim();
    if (!name || name.length > 40) return "Collection names must contain 1–40 characters.";
    if (name.toLowerCase() === "all") return "'All' is reserved for the library-wide tab.";
    if (seen.has(name.toLowerCase())) return `A collection named '${name}' already exists.`;
    seen.add(name.toLowerCase());
  }
  return "";
};

export const rewriteMockGameCollections = (
  rewrite: (names: string[], game: GameRecord) => string[],
): void => {
  const games = readMockGames().map((game) => ({
    ...game,
    collections: rewrite(game.collections || [], game),
  }));
  try {
    localStorage.setItem("wlib-mock-games", JSON.stringify(games));
  } catch (_error) {
    // Ignore browser mock persistence failures and keep the desktop contract unchanged.
  }
};

export const writeMockCollections = (collections: string[]): void => {
  writeMockSettings({ ...readMockSettings(), collections });
};
