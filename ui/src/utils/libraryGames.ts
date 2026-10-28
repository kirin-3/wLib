// SPDX-License-Identifier: GPL-3.0-or-later
import type { GameRecord } from "../services/api.ts";
import type { SortDir, SortField } from "./libraryViewState.ts";
import { normalizePlayStatus } from "./playStatus.ts";

// "v0.8", "0.8" and "V 0.8 " are the same version. Keep in sync with
// normalize_version() in core/api.py. String() matches the Python mirror, which
// coerces whatever a hand-edited or imported library row holds instead of
// throwing on a non-string value.
export const normalizeVersion = (version: string | null | undefined): string =>
  String(version || "")
    .replace(/\s+/g, "")
    .toLowerCase()
    .replace(/^v(?=\d)/, "");

export const isWishlisted = (game: Pick<GameRecord, "exe_path">): boolean =>
  !String(game.exe_path || "").trim();

export const hasAvailableUpdate = (
  game: Pick<GameRecord, "version" | "latest_version"> &
    { skipped_version: GameRecord["skipped_version"] } &
    Partial<Pick<GameRecord, "exe_path">>,
): boolean => {
  if ("exe_path" in game && isWishlisted(game as Pick<GameRecord, "exe_path">))
    return false;
  const remote = normalizeVersion(game.latest_version);
  return !!remote && remote !== normalizeVersion(game.version) && !isUpdateSkipped(game);
};

export const isUpdateSkipped = (
  game: Pick<GameRecord, "latest_version"> & { skipped_version?: string | undefined } & Partial<Pick<GameRecord, "exe_path">>,
): boolean => {
  if ("exe_path" in game && isWishlisted(game as Pick<GameRecord, "exe_path">)) return false;
  const skipped = normalizeVersion(game.skipped_version);
  return !!skipped && skipped === normalizeVersion(game.latest_version);
};

export const hasUnplayedUpdate = (
  game: Pick<GameRecord, "exe_path" | "version" | "last_played_version">,
): boolean => {
  const installed = normalizeVersion(game.version);
  const played = normalizeVersion(game.last_played_version);
  return !isWishlisted(game) && !!installed && !!played && installed !== played;
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
    (typeof game.tags === "string"
      ? game.tags.split(",")
      : Array.isArray(game.tags)
        ? game.tags
        : [])
      .map((tag) => (typeof tag === "string" ? tag.trim() : ""))
      .filter(Boolean),
  ),
];

const normalizeSearchText = (text: unknown): string =>
  String(text ?? "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase();

export const matchesGameSearch = (game: GameRecord, query: string): boolean => {
  const cleanQuery = normalizeSearchText(query.trim());
  if (!cleanQuery) return true;
  return [game.title, game.developer, game.engine, ...gameTags(game)].some((value) =>
    normalizeSearchText(value).includes(cleanQuery),
  );
};

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
      case "version":
        // "v0.9" and "0.9" are the same version. Sorting raw strings would put
        // every "v"-prefixed version on one side of the list regardless of the
        // number, so compare the normalized form.
        return normalizeVersion(game.version);
      default:
        return String(game[field] ?? "").trim().toLowerCase();
    }
  };
  const left = value(a),
    right = value(b);
  // Missing values (no date, unrated, never played) sink to the bottom in both directions.
  const leftEmpty = left === "" || left === 0,
    rightEmpty = right === "" || right === 0;
  if (leftEmpty !== rightEmpty) return leftEmpty ? 1 : -1;
  const order =
    typeof left === "string" && typeof right === "string"
      ? left.localeCompare(right, undefined, { numeric: true })
      : left < right
        ? -1
        : left > right
          ? 1
          : 0;

  if (order !== 0) {
    return direction === "asc" ? order : -order;
  }

  // Ties fall back to ascending title so the order doesn't depend on load order.
  if (field !== "title") {
    const titleA = String(a.title ?? "").trim().toLowerCase();
    const titleB = String(b.title ?? "").trim().toLowerCase();
    const titleAEmpty = !titleA;
    const titleBEmpty = !titleB;
    if (titleAEmpty !== titleBEmpty) return titleAEmpty ? 1 : -1;
    const titleOrder = titleA.localeCompare(titleB, undefined, { numeric: true });
    if (titleOrder !== 0) return titleOrder;
  }

  // Stable sort: fall back to ascending ID so distinct games have a deterministic total order.
  return (Number(a.id) || 0) - (Number(b.id) || 0);
};

export const gameFolder = (path: string): string => {
  const separator = Math.max(path.lastIndexOf("/"), path.lastIndexOf("\\"));
  if (separator < 0) return "";
  return path.slice(0, separator + 1);
};
