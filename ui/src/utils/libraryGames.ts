// SPDX-License-Identifier: GPL-3.0-or-later
import type { GameRecord } from "../services/api.ts";
import type { SortDir, SortField } from "./libraryViewState.ts";
import { normalizePlayStatus } from "./playStatus.ts";

// "v0.8", "0.8" and "V 0.8 " are the same version. Keep in sync with
// normalize_version() in core/api.py.
export const normalizeVersion = (version: string | null | undefined): string =>
  (version || "").replace(/\s+/g, "").toLowerCase().replace(/^v(?=\d)/, "");

export const isWishlisted = (game: Pick<GameRecord, "exe_path">): boolean =>
  !String(game.exe_path || "").trim();

export const hasAvailableUpdate = (
  game: Pick<GameRecord, "version" | "latest_version"> & Partial<Pick<GameRecord, "exe_path">>,
): boolean => {
  if ("exe_path" in game && isWishlisted(game as Pick<GameRecord, "exe_path">)) return false;
  const remote = normalizeVersion(game.latest_version);
  return !!remote && remote !== normalizeVersion(game.version);
};

// The installed version differs from the version the user finished. "Differs",
// not "is newer": free-form version strings can't be ordered reliably.
export const hasNewSinceFinished = (
  game: Pick<GameRecord, "version" | "finished_version">,
): boolean => {
  const finished = normalizeVersion(game.finished_version);
  return !!finished && finished !== normalizeVersion(game.version);
};

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
