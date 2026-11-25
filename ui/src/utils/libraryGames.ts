// SPDX-License-Identifier: GPL-3.0-or-later
import type { GameRecord, LaunchTarget } from "../services/api.ts";
import type { FilterCollection, FilterMinRating, SortDir, SortField, ThreadStateFilter } from "./libraryViewState.ts";
import { getPlayStatusMeta, normalizePlayStatus } from "./playStatus.ts";
import { automaticTags } from "./threadDetails.ts";
import { hasNewerVersion, normalizeVersion } from "./versionOrder.ts";

export { normalizeVersion } from "./versionOrder.ts";

// One shared collator: `localeCompare` with options builds a new one on every
// call, which made sorting a 2000-game library take tens of milliseconds.
// Same order as `a.localeCompare(b, undefined, { numeric: true })`.
const naturalCollator = new Intl.Collator(undefined, { numeric: true });
export const naturalCompare = (a: string, b: string): number => naturalCollator.compare(a, b);

export const LIBRARY_SOURCES = [
  { key: "f95", label: "F95Zone" },
  { key: "lc", label: "LewdCorner" },
  { key: "dlsite", label: "DLsite" },
  { key: "vndb", label: "VNDB" },
  { key: "ryuugames", label: "Ryuugames" },
] as const;

export const gameSources = (game: Pick<GameRecord, "f95_url" | "lc_url" | "links">): Set<string> => {
  const sources = new Set<string>();
  if (game.f95_url?.trim()) sources.add("f95");
  if (game.lc_url?.trim()) sources.add("lc");
  for (const link of game.links || []) {
    if (LIBRARY_SOURCES.some(({ key }) => key !== "f95" && key !== "lc" && key === link.source)) {
      sources.add(link.source);
    }
  }
  return sources;
};

export const matchesSourceFilter = (
  game: Pick<GameRecord, "f95_url" | "lc_url" | "links">,
  selected: readonly string[],
  excluded: readonly string[] = [],
): boolean => {
  if (!selected.length && !excluded.length) return true;
  const sources = gameSources(game);
  if (excluded.some((source) => sources.has(source))) return false;
  return !selected.length || selected.some((source) => sources.has(source));
};

// Engine identity is the trimmed value. Keep in step with uniqueEngines in LibraryView.
export const engineFilterKey = (value: unknown): string =>
  typeof value === "string" ? value.trim() : "";

export const matchesEngineFilter = (
  game: Pick<GameRecord, "engine">,
  selected: readonly string[],
  excluded: readonly string[] = [],
): boolean => {
  const engine = engineFilterKey(game.engine);
  // An empty engine never matches an excluded value.
  if (engine && excluded.some((value) => engineFilterKey(value) === engine)) return false;
  return !selected.length || selected.some((value) => engineFilterKey(value) === engine);
};

export type FilterValueState = "neutral" | "include" | "exclude";

export interface FilterValueLists {
  included: string[];
  excluded: string[];
}

const exactFilterKey = (value: string): string => String(value ?? "").trim();

export const filterValueState = (
  included: readonly string[],
  excluded: readonly string[],
  value: string,
  key: (value: string) => string = exactFilterKey,
): FilterValueState => {
  const target = key(value);
  if (included.some((entry) => key(entry) === target)) return "include";
  if (excluded.some((entry) => key(entry) === target)) return "exclude";
  return "neutral";
};

// Moves a value to its next state: neutral -> include -> exclude -> neutral.
// Every spelling with the same key leaves both lists first, so a value is
// never in both lists.
export const cycleFilterValue = (
  included: readonly string[],
  excluded: readonly string[],
  value: string,
  key: (value: string) => string = exactFilterKey,
): FilterValueLists => {
  const state = filterValueState(included, excluded, value, key);
  const target = key(value);
  const nextIncluded = included.filter((entry) => key(entry) !== target);
  const nextExcluded = excluded.filter((entry) => key(entry) !== target);
  if (state === "neutral") nextIncluded.push(value);
  else if (state === "include") nextExcluded.push(value);
  return { included: nextIncluded, excluded: nextExcluded };
};

// Puts a value in one state. Every spelling with the same key leaves both
// lists first, so a value is never in both lists.
export const setFilterValueState = (
  included: readonly string[],
  excluded: readonly string[],
  value: string,
  state: FilterValueState,
  key: (value: string) => string = exactFilterKey,
): FilterValueLists => {
  const target = key(value);
  const nextIncluded = included.filter((entry) => key(entry) !== target);
  const nextExcluded = excluded.filter((entry) => key(entry) !== target);
  if (state === "include") nextIncluded.push(value);
  else if (state === "exclude") nextExcluded.push(value);
  return { included: nextIncluded, excluded: nextExcluded };
};

// A click on a library filter pill: an included value goes back to neutral,
// any other value becomes included.
export const toggleIncludedFilterValue = (
  included: readonly string[],
  excluded: readonly string[],
  value: string,
  key: (value: string) => string = exactFilterKey,
): FilterValueLists =>
  setFilterValueState(
    included,
    excluded,
    value,
    filterValueState(included, excluded, value, key) === "include" ? "neutral" : "include",
    key,
  );

// The pill's exclude button (or Alt+click, or the minus key): an excluded
// value goes back to neutral, any other value becomes excluded.
export const toggleExcludedFilterValue = (
  included: readonly string[],
  excluded: readonly string[],
  value: string,
  key: (value: string) => string = exactFilterKey,
): FilterValueLists =>
  setFilterValueState(
    included,
    excluded,
    value,
    filterValueState(included, excluded, value, key) === "exclude" ? "neutral" : "exclude",
    key,
  );

export const isWishlisted = (game: Pick<GameRecord, "exe_path">): boolean =>
  !String(game.exe_path || "").trim();

export const hasAvailableUpdate = (
  game: Pick<GameRecord, "version" | "latest_version"> &
    { skipped_version?: GameRecord["skipped_version"]; play_status?: GameRecord["play_status"] } &
    Partial<Pick<GameRecord, "exe_path">>,
): boolean => {
  if ("exe_path" in game && isWishlisted(game as Pick<GameRecord, "exe_path">))
    return false;
  // Archived games keep their found update hidden, like a skipped one.
  if (isArchived(game)) return false;
  return hasNewerVersion(game.version, game.latest_version, game.skipped_version);
};

/** Whether a game has the Archived play status (or the former "Abandoned"). */
export const isArchived = (game: { play_status?: GameRecord["play_status"] }): boolean =>
  normalizePlayStatus(game.play_status) === "Archived";

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

type ThreadStateGame = Pick<GameRecord, "f95_url" | "lc_url" | "thread_completed" | "thread_onhold" | "thread_abandoned">;

/** Whether a game matches one value of the Thread state filter section. */
export const matchesThreadState = (game: ThreadStateGame, value: ThreadStateFilter): boolean => {
  switch (value) {
    case "finished": return !!game.thread_completed;
    case "onhold": return game.thread_onhold === 1;
    case "abandoned": return game.thread_abandoned === 1;
    // A game with an F95 or LC thread that has none of the three recorded states.
    case "ongoing":
      return !!(game.f95_url?.trim() || game.lc_url?.trim()) &&
        !game.thread_completed && game.thread_onhold !== 1 && game.thread_abandoned !== 1;
  }
};

/** A game matches the section when it matches any checked value; none checked matches all. */
export const matchesThreadStateFilter = (game: ThreadStateGame, values: readonly ThreadStateFilter[]): boolean =>
  !values.length || values.some((value) => matchesThreadState(game, value));

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

// Tag identity is case-insensitive and ignores surrounding spaces everywhere
// else in the app (tag colors, search), so the library filter must agree or the
// same tag shows up twice and selecting one hides the other casing.
export const tagFilterKey = (tag: string): string =>
  String(tag ?? "").trim().toLowerCase();

export const hasTagFilter = (values: readonly string[], tag: string): boolean => {
  const key = tagFilterKey(tag);
  return values.some((value) => tagFilterKey(value) === key);
};

export const toggleTagFilter = (
  values: readonly string[],
  tag: string,
  checked: boolean,
): string[] => {
  const key = tagFilterKey(tag);
  const remaining = values.filter((value) => tagFilterKey(value) !== key);
  return checked ? [...remaining, tag] : remaining;
};

type FilterTagFields = Pick<GameRecord, "tags" | "developer" | "language">;

// The game's own tags plus the automatic "Developer:" and "Language:" tags,
// which the filter and search use but the game's tag list never stores.
export const gameFilterTags = (game: FilterTagFields): string[] => {
  const seen = new Set<string>();
  return [...gameTags(game), ...automaticTags(game)].filter((tag) => {
    const key = tagFilterKey(tag);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
};

export const matchesTagFilter = (
  game: FilterTagFields,
  selectedTags: readonly string[],
  excludedTags: readonly string[] = [],
): boolean => {
  if (!selectedTags.length && !excludedTags.length) return true;
  const keys = new Set(gameFilterTags(game).map(tagFilterKey));
  if (excludedTags.some((tag) => keys.has(tagFilterKey(tag)))) return false;
  return selectedTags.every((tag) => keys.has(tagFilterKey(tag)));
};

export interface TagFilterOption {
  label: string;
  count: number;
}

// One option per logical tag, with the first spelling seen as the label.
export const buildTagFilterOptions = (
  games: readonly FilterTagFields[],
): TagFilterOption[] => {
  const byKey = new Map<string, TagFilterOption>();
  games.forEach((game) => {
    const seenInGame = new Set<string>();
    gameFilterTags(game).forEach((tag) => {
      const key = tagFilterKey(tag);
      // A game carrying both "Sandbox" and "sandbox" is still one game.
      if (seenInGame.has(key)) return;
      seenInGame.add(key);
      const existing = byKey.get(key);
      if (existing) existing.count += 1;
      else byKey.set(key, { label: tag, count: 1 });
    });
  });
  return [...byKey.values()].sort((a, b) =>
    naturalCompare(a.label, b.label),
  );
};

const compareTagLabels = (a: string, b: string): number =>
  naturalCompare(a, b);

// The pinned group of the tag list: included tag keys A-Z, then excluded A-Z.
export const pinnedTagKeysFor = (
  included: readonly string[],
  excluded: readonly string[],
): string[] => {
  const seen = new Set<string>();
  const group = (values: readonly string[]): string[] =>
    [...values]
      .sort(compareTagLabels)
      .map(tagFilterKey)
      .filter((key) => {
        if (!key || seen.has(key)) return false;
        seen.add(key);
        return true;
      });
  return [...group(included), ...group(excluded)];
};

export interface OrderedTagOptions {
  pinned: TagFilterOption[];
  rest: TagFilterOption[];
}

// Pinned tags come first in snapshot order and ignore the search. The other
// tags stay A-Z and only show when their name contains the search text.
export const orderTagOptions = (
  options: readonly TagFilterOption[],
  pinnedKeys: readonly string[],
  search: string,
): OrderedTagOptions => {
  const byKey = new Map(options.map((option) => [tagFilterKey(option.label), option]));
  const pinned = pinnedKeys
    .map((key) => byKey.get(key))
    .filter((option): option is TagFilterOption => !!option);
  const pinnedSet = new Set(pinnedKeys);
  const query = search.trim().toLowerCase();
  const rest = options
    .filter((option) => !pinnedSet.has(tagFilterKey(option.label)))
    .filter((option) => !query || option.label.toLowerCase().includes(query))
    .sort((a, b) => compareTagLabels(a.label, b.label));
  return { pinned, rest };
};

const normalizeSearchText = (text: unknown): string =>
  String(text ?? "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase();

export const matchesGameSearch = (game: GameRecord, query: string): boolean => {
  const cleanQuery = normalizeSearchText(query.trim());
  if (!cleanQuery) return true;
  return [
    game.title,
    game.original_title,
    ...(game.aliases ?? []),
    game.developer,
    game.engine,
    ...gameFilterTags(game),
  ].some((value) =>
    normalizeSearchText(value).includes(cleanQuery),
  );
};

export const F95_RATING_PRIOR_MEAN = 3.2;
export const F95_RATING_PRIOR_REVIEWS = 25;

/** Bayesian weighted F95 rating: (v * R + m * C) / (v + m), or null without a valid rating and positive count. */
export const weightedRating = (
  game: Pick<GameRecord, "rating" | "rating_count">,
): number | null => {
  const count = game.rating_count;
  if (typeof count !== "number" || !Number.isFinite(count) || count <= 0) return null;
  const match = String(game.rating || "").match(/-?\d+(?:\.\d+)?/);
  if (!match) return null;
  const rating = parseFloat(match[0]);
  if (!Number.isFinite(rating) || rating <= 0 || rating > 5) return null;
  return (
    (count * rating + F95_RATING_PRIOR_REVIEWS * F95_RATING_PRIOR_MEAN) /
    (count + F95_RATING_PRIOR_REVIEWS)
  );
};

/** Hides games below the chosen minimum weighted F95 rating or with no weighted rating. */
export const matchesMinRatingFilter = (
  game: Pick<GameRecord, "rating" | "rating_count">,
  minRating: FilterMinRating,
): boolean => {
  if (!minRating) return true;
  const threshold = Number(minRating);
  if (!Number.isFinite(threshold)) return true;
  const score = weightedRating(game);
  return score !== null && score >= threshold;
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
      case "weighted_rating":
        return weightedRating(game) ?? 0;
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
      ? naturalCompare(left, right)
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
    const titleOrder = naturalCompare(titleA, titleB);
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


export function libraryDisplayState(
  gamesLoaded: boolean, loadError: string | null, gameCount: number, filteredCount: number,
): "loading" | "error" | "empty" | "no-results" | "list" {
  if (!gamesLoaded) return loadError ? "error" : "loading";
  if (!gameCount) return "empty";
  return filteredCount ? "list" : "no-results";
}

// Why a library with games shows none, so the no-results state can name the
// cause and offer only the ways out that apply.
export interface LibraryNoResultsInput {
  /** The selected collection tab, "" for All. */
  collection: string;
  /** Games in that collection before search and filters. */
  collectionGameCount: number;
  quickFilter: FilterCollection;
  /** Active filters other than the quick filter. */
  otherFilterCount: number;
  search: string;
}
export interface LibraryNoResults {
  kind: "collection-empty" | "quick-filter-empty" | "no-match";
  title: string;
  detail: string;
}

const QUICK_FILTER_EMPTY: Record<Exclude<FilterCollection, "All">, { title: string; scoped: string; detail: string }> = {
  Favorites: {
    title: "No favorites yet",
    scoped: "No favorites in",
    detail: "Right-click a game and choose Favorite to add it here.",
  },
  "Updates available": {
    title: "No updates available",
    scoped: "No updates available in",
    detail: "Every game is on the latest version that wLib knows of.",
  },
  "Unplayed update": {
    title: "No unplayed updates",
    scoped: "No unplayed updates in",
    detail: "A game shows here when you install a new version and have not played it yet.",
  },
  "New since finished": {
    title: "No new versions of finished games",
    scoped: "No new versions of finished games in",
    detail: "A finished game shows here when you install a newer version of it.",
  },
  "Recently played": {
    title: "No games played yet",
    scoped: "No played games in",
    detail: "Games that you start from wLib show here.",
  },
};

const filterCountText = (count: number): string =>
  count === 1 ? "1 filter is on." : `${count} filters are on.`;

export function describeLibraryNoResults(input: LibraryNoResultsInput): LibraryNoResults {
  const typed = input.search.trim();
  // A long query would push the title over several lines.
  const search = typed.length > 48 ? `${typed.slice(0, 47).trimEnd()}…` : typed;
  const scope = input.collection ? ` in “${input.collection}”` : "";
  if (input.collection && input.collectionGameCount <= 0) {
    return {
      kind: "collection-empty",
      title: `“${input.collection}” has no games yet`,
      detail: "Right-click a game, choose Collections…, then select this collection.",
    };
  }
  if (!search && !input.otherFilterCount && input.quickFilter !== "All") {
    const copy = QUICK_FILTER_EMPTY[input.quickFilter];
    return {
      kind: "quick-filter-empty",
      title: input.collection ? `${copy.scoped} “${input.collection}”` : copy.title,
      detail: copy.detail,
    };
  }
  const filterCount = input.otherFilterCount + (input.quickFilter !== "All" ? 1 : 0);
  if (search) {
    return {
      kind: "no-match",
      title: `No games match “${search}”${scope}`,
      detail: filterCount
        ? `${filterCountText(filterCount)} The search only looks in games that pass ${filterCount === 1 ? "it" : "them"}.`
        : "Check the spelling, or search by developer or tag.",
    };
  }
  return {
    kind: "no-match",
    title: filterCount ? `No games match your filters${scope}` : `No games to show${scope}`,
    detail: filterCount ? `${filterCountText(filterCount)} Remove one above, or clear them all.` : "",
  };
}

export interface EffectiveLaunchTarget {
  id: string;
  label: string;
  exe_path: string;
  isDefault: boolean;
}

export const formatPlaytime = (seconds: number | null | undefined): string => {
  if (!seconds) return "0.0 hrs";
  return (seconds / 3600).toFixed(1) + " hrs";
};

/** One cover rating format: the F95 score with one decimal, and the review count for the label only. */
export interface CardRating {
  value: string;
  label: string;
}

export const formatCardRating = (
  game: Pick<GameRecord, "rating" | "rating_count">,
): CardRating | null => {
  const text = String(game.rating || "");
  const match = text.match(/\d+(?:\.\d+)?/);
  if (!match) return null;
  const score = parseFloat(match[0]);
  if (!Number.isFinite(score) || score <= 0 || score > 5) return null;
  const counted = typeof game.rating_count === "number" && Number.isFinite(game.rating_count) && game.rating_count > 0
    ? game.rating_count
    : Number(text.match(/\((\d[\d,]*)\s+reviews?\)/i)?.[1]?.replace(/,/g, "") || 0);
  const value = score.toFixed(1);
  const reviews = counted > 0 ? ` from ${counted} ${counted === 1 ? "review" : "reviews"}` : "";
  return { value, label: `F95 rating ${value} out of 5${reviews}` };
};

/** The Library heading line, for example "25 games · 4 updates". */
export const formatLibrarySummary = (gameCount: number, updateCount: number): string => {
  if (gameCount <= 0) return "";
  const games = `${gameCount} ${gameCount === 1 ? "game" : "games"}`;
  if (updateCount <= 0) return games;
  return `${games} · ${updateCount} ${updateCount === 1 ? "update" : "updates"}`;
};

export const formatLastPlayed = (value?: string | null): string => {
  const date = new Date(value || "");
  return Number.isNaN(date.getTime()) ? "Never" : date.toLocaleDateString();
};

export const getGamePlayStatusMeta = (game: Pick<GameRecord, "play_status" | "status">) =>
  getPlayStatusMeta(game.play_status, game.status);

const sortLaunchTargets = (targets: LaunchTarget[]): LaunchTarget[] =>
  [...targets].sort((a, b) => a.sort_order - b.sort_order || a.id - b.id);

/** Name of the canonical default launch target; empty shows "Default". */
export const defaultTargetLabel = (
  game: Pick<GameRecord, "default_target_label"> | null | undefined,
): string => game?.default_target_label?.trim() || "Default";

export const getEffectiveLaunchTargets = (
  game: Pick<GameRecord, "id" | "exe_path" | "launch_targets" | "default_target_label">,
): EffectiveLaunchTarget[] => [
  { id: `default-${game.id}`, label: defaultTargetLabel(game), exe_path: game.exe_path, isDefault: true },
  ...sortLaunchTargets(game.launch_targets || []).map((target) => ({
    id: `target-${target.id}`,
    label: target.label,
    exe_path: target.exe_path,
    isDefault: false,
  })),
];

export const hasAdditionalLaunchTargets = (
  game: Pick<GameRecord, "exe_path" | "launch_targets">,
  running: boolean,
): boolean => !isWishlisted(game) && !running && (game.launch_targets || []).length > 0;
