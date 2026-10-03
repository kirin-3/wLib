// SPDX-License-Identifier: GPL-3.0-or-later
import type { GameRecord } from "../services/api.ts";
import type { SortDir, SortField } from "./libraryViewState.ts";
import { normalizePlayStatus } from "./playStatus.ts";

export const hasAvailableUpdate = (
  game: Pick<GameRecord, "version" | "latest_version">,
): boolean =>
  !!game.latest_version?.trim() &&
  game.latest_version.trim() !== (game.version || "").trim();

export const gameTags = (game: Pick<GameRecord, "tags">): string[] => [
  ...new Set(
    (typeof game.tags === "string" ? game.tags.split(",") : game.tags || [])
      .map((tag) => tag.trim())
      .filter(Boolean),
  ),
];

export const matchesGameSearch = (game: GameRecord, query: string): boolean =>
  [game.title, game.developer, game.engine, ...gameTags(game)].some((value) =>
    (value || "").toLowerCase().includes(query.trim().toLowerCase()),
  );

export const compareLibraryGames = (
  a: GameRecord,
  b: GameRecord,
  field: SortField,
  direction: SortDir,
): number => {
  const value = (game: GameRecord): string | number => {
    switch (field) {
      case "own_rating":
        return (
          ((Number(game.rating_graphics) || 0) +
            (Number(game.rating_story) || 0) +
            (Number(game.rating_fappability) || 0) +
            (Number(game.rating_gameplay) || 0)) /
          4
        );
      case "rating":
        return (
          parseFloat(String(game.rating || "").match(/[\d.]+/)?.[0] || "0") || 0
        );
      case "playtime_seconds":
        return Number(game.playtime_seconds) || 0;
      case "last_played":
      case "date_added":
        return Date.parse(game[field] || "") || 0;
      case "play_status":
        return normalizePlayStatus(game.play_status, game.status).toLowerCase();
      default:
        return (game[field] || "").trim().toLowerCase();
    }
  };
  const left = value(a),
    right = value(b);
  const order =
    typeof left === "string" && typeof right === "string"
      ? left.localeCompare(right, undefined, { numeric: true })
      : left < right
        ? -1
        : left > right
          ? 1
          : 0;
  return direction === "asc" ? order : -order;
};

export const gameFolder = (path: string): string => {
  const separator = Math.max(path.lastIndexOf("/"), path.lastIndexOf("\\"));
  if (separator < 0) return "";
  return path.slice(0, separator + 1);
};
