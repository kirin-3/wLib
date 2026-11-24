// SPDX-License-Identifier: GPL-3.0-or-later
// Discover's view choices: the filters, sort and "Hide my games" (kept on this
// device across restarts), and the search text and scroll position (kept only
// while the app runs). None of this is exported or synced.
import type { DiscoverCard, DiscoverQuery, DiscoverSite, DiscoverSort, DiscoverStatus } from "../services/api";
import { cycleFilterValue, filterValueState, type FilterValueState } from "./libraryGames.ts";

export const DISCOVER_VIEW_STORAGE_KEY = "discover.view";
export const DISCOVER_VIEW_VERSION = 1;
export const DISCOVER_PAGE_SIZE = 120;

export const DISCOVER_SITES: { key: DiscoverSite; label: string }[] = [
  { key: "f95", label: "F95Zone" },
  { key: "lc", label: "LewdCorner" },
  { key: "dlsite", label: "DLsite" },
  { key: "vndb", label: "VNDB" },
  { key: "ryuugames", label: "Ryuugames" },
];
export const DISCOVER_SITE_SHORT: Record<DiscoverSite, string> = {
  f95: "F95", lc: "LC", dlsite: "DLsite", vndb: "VNDB", ryuugames: "Ryuu",
};
export const DISCOVER_STATUSES: { key: DiscoverStatus; label: string }[] = [
  { key: "ongoing", label: "Ongoing" },
  { key: "completed", label: "Completed" },
  { key: "onhold", label: "On hold" },
  { key: "abandoned", label: "Abandoned" },
];
export const DISCOVER_SORTS: { key: DiscoverSort; label: string }[] = [
  { key: "updated", label: "Last updated" },
  { key: "rating", label: "Weighted rating" },
  { key: "released", label: "Release date" },
  { key: "title", label: "Title" },
];
export const DISCOVER_RATING_STEPS = ["", "3.5", "4.0", "4.5"] as const;
export type DiscoverRatingStep = (typeof DISCOVER_RATING_STEPS)[number];

export interface DiscoverViewState {
  includeSites: DiscoverSite[];
  excludeSites: DiscoverSite[];
  /** Tag names as the filter list shows them. */
  includeTags: string[];
  excludeTags: string[];
  statuses: DiscoverStatus[];
  includeEngines: string[];
  minRating: DiscoverRatingStep;
  hideLibrary: boolean;
  sort: DiscoverSort;
  filtersOpen: boolean;
}

export const defaultDiscoverView = (): DiscoverViewState => ({
  includeSites: [],
  excludeSites: [],
  includeTags: [],
  excludeTags: [],
  statuses: [],
  includeEngines: [],
  minRating: "",
  hideLibrary: false,
  sort: "updated",
  filtersOpen: true,
});

const SITE_KEYS = new Set<string>(DISCOVER_SITES.map((site) => site.key));
const STATUS_KEYS = new Set<string>(DISCOVER_STATUSES.map((status) => status.key));
const SORT_KEYS = new Set<string>(DISCOVER_SORTS.map((sort) => sort.key));

const strings = (value: unknown, allowed?: Set<string>, limit = 200): string[] => {
  if (!Array.isArray(value)) return [];
  const found: string[] = [];
  for (const item of value) {
    if (typeof item !== "string") continue;
    const text = item.trim();
    if (!text || text.length > 200 || found.includes(text) || (allowed && !allowed.has(text))) continue;
    found.push(text);
    if (found.length >= limit) break;
  }
  return found;
};

/** A view state from stored JSON; anything unknown falls back to the default. */
export const normalizeDiscoverView = (value: unknown): DiscoverViewState => {
  const base = defaultDiscoverView();
  if (typeof value !== "object" || value === null) return base;
  const raw = value as Record<string, unknown>;
  if (raw.version !== DISCOVER_VIEW_VERSION) return base;
  const includeSites = strings(raw.includeSites, SITE_KEYS) as DiscoverSite[];
  const includeTags = strings(raw.includeTags);
  return {
    includeSites,
    excludeSites: (strings(raw.excludeSites, SITE_KEYS) as DiscoverSite[]).filter((site) => !includeSites.includes(site)),
    includeTags,
    excludeTags: strings(raw.excludeTags).filter((tag) => !includeTags.includes(tag)),
    statuses: strings(raw.statuses, STATUS_KEYS) as DiscoverStatus[],
    includeEngines: strings(raw.includeEngines),
    minRating: (DISCOVER_RATING_STEPS as readonly string[]).includes(String(raw.minRating))
      ? (String(raw.minRating) as DiscoverRatingStep)
      : "",
    hideLibrary: raw.hideLibrary === true,
    sort: typeof raw.sort === "string" && SORT_KEYS.has(raw.sort) ? (raw.sort as DiscoverSort) : base.sort,
    filtersOpen: raw.filtersOpen !== false,
  };
};

export const readDiscoverView = (storage: Storage | undefined = globalThis.localStorage): DiscoverViewState => {
  try {
    const text = storage?.getItem(DISCOVER_VIEW_STORAGE_KEY);
    return normalizeDiscoverView(text ? (JSON.parse(text) as unknown) : null);
  } catch {
    return defaultDiscoverView();
  }
};

export const writeDiscoverView = (state: DiscoverViewState, storage: Storage | undefined = globalThis.localStorage): void => {
  try {
    storage?.setItem(DISCOVER_VIEW_STORAGE_KEY, JSON.stringify({ version: DISCOVER_VIEW_VERSION, ...state }));
  } catch {
    // Private windows or full storage: the choices last for this session only.
  }
};

/** The backend query for a view state, search text and page offset. */
export const discoverQuery = (state: DiscoverViewState, search: string, offset = 0): DiscoverQuery => ({
  search: search.trim(),
  include_sites: [...state.includeSites],
  exclude_sites: [...state.excludeSites],
  include_tags: [...state.includeTags],
  exclude_tags: [...state.excludeTags],
  statuses: [...state.statuses],
  engines: [...state.includeEngines],
  min_rating: state.minRating ? Number(state.minRating) : null,
  hide_library: state.hideLibrary,
  sort: state.sort,
  offset,
  limit: DISCOVER_PAGE_SIZE,
});

/** The number of set filters (the sort is not a filter). */
export const activeDiscoverFilterCount = (state: DiscoverViewState): number =>
  state.includeSites.length + state.excludeSites.length + state.includeTags.length + state.excludeTags.length +
  state.statuses.length + state.includeEngines.length + (state.minRating ? 1 : 0) + (state.hideLibrary ? 1 : 0);

/** The view with every filter cleared; the sort and the pane stay. */
export const clearDiscoverFilters = (state: DiscoverViewState): DiscoverViewState => ({
  ...defaultDiscoverView(),
  sort: state.sort,
  filtersOpen: state.filtersOpen,
});

export const siteState = (state: DiscoverViewState, site: DiscoverSite): FilterValueState =>
  filterValueState(state.includeSites, state.excludeSites, site);

/** Sites cycle neutral -> include -> exclude -> neutral, like the library pills. */
export const cycleSite = (state: DiscoverViewState, site: DiscoverSite): DiscoverViewState => {
  const next = cycleFilterValue(state.includeSites, state.excludeSites, site);
  return { ...state, includeSites: next.included as DiscoverSite[], excludeSites: next.excluded as DiscoverSite[] };
};

/** Tags compare without case and punctuation, as the backend's tag keys do. */
export const discoverTagKey = (name: string): string => name.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, "");

export const tagState = (state: DiscoverViewState, tag: string): FilterValueState =>
  filterValueState(state.includeTags, state.excludeTags, tag, discoverTagKey);

export const cycleTag = (state: DiscoverViewState, tag: string): DiscoverViewState => {
  const next = cycleFilterValue(state.includeTags, state.excludeTags, tag, discoverTagKey);
  return { ...state, includeTags: next.included, excludeTags: next.excluded };
};

const toggle = <T>(list: readonly T[], value: T): T[] =>
  list.includes(value) ? list.filter((item) => item !== value) : [...list, value];

export const toggleStatus = (state: DiscoverViewState, status: DiscoverStatus): DiscoverViewState => ({
  ...state,
  statuses: toggle(state.statuses, status),
});

export const toggleEngine = (state: DiscoverViewState, engine: string): DiscoverViewState => ({
  ...state,
  includeEngines: toggle(state.includeEngines, engine),
});

/**
 * Search text, scroll position and loaded results, kept while the app runs so
 * that coming back to Discover shows the same results at the same place.
 */
export interface DiscoverSession {
  search: string;
  scrollTop: number;
  /** The query the results belong to (JSON), so a changed filter loads again. */
  queryKey: string;
  games: DiscoverCard[];
  total: number;
  nextOffset: number | null;
}

export const discoverSession: DiscoverSession = { search: "", scrollTop: 0, queryKey: "", games: [], total: 0, nextOffset: null };

/** A stable key for the parts of a query that decide the result list. */
export const discoverQueryKey = (query: DiscoverQuery): string => JSON.stringify({ ...query, offset: 0, limit: 0 });

/** "1 game", "12,345 games". */
export const discoverCountLabel = (total: number): string =>
  `${total.toLocaleString("en-US")} ${total === 1 ? "game" : "games"}`;

/** The F95 preview host for an F95 attachment; other URLs stay as they are. */
export const discoverCoverUrl = (url: string): string => {
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== "https:" && parsed.protocol !== "http:") return "";
    if (/(^|\.)lewdcorner\.com$/i.test(parsed.hostname)) return "";
    if (parsed.hostname === "attachments.f95zone.to") parsed.hostname = "preview.f95zone.to";
    return parsed.toString();
  } catch {
    return "";
  }
};
