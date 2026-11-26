// SPDX-License-Identifier: GPL-3.0-or-later
// Discover's view choices: the filters, sort and "Hide library games" (kept on this
// device across restarts), and the search text and scroll position (kept only
// while the app runs). None of this is exported or synced.
import type { DiscoverCard, DiscoverQuery, DiscoverSite, DiscoverSort, DiscoverStatus, DiscoverTagOption } from "../services/api";
import {
  filterValueState,
  toggleExcludedFilterValue,
  toggleIncludedFilterValue,
  type FilterValueState,
} from "./libraryGames.ts";

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
export const DISCOVER_STATUSES: { key: DiscoverStatus; label: string }[] = [
  { key: "ongoing", label: "Ongoing" },
  { key: "completed", label: "Completed" },
  { key: "onhold", label: "On hold" },
  { key: "abandoned", label: "Abandoned" },
];
export const DISCOVER_SORTS: { key: DiscoverSort; label: string }[] = [
  { key: "updated", label: "Latest update" },
  { key: "rating", label: "Highest rated" },
  { key: "released", label: "Latest release" },
  { key: "title", label: "Title A–Z" },
];
/** The toolbar button and its chip. It hides installed and wishlisted library games. */
export const HIDE_LIBRARY_LABEL = "Hide library games";
export const DISCOVER_RATING_STEPS = ["", "3.5", "4.0", "4.5"] as const;
export type DiscoverRatingStep = (typeof DISCOVER_RATING_STEPS)[number];

/** The filter pane's sections, in pane order. Each one folds on its heading. */
export const DISCOVER_FILTER_SECTIONS = ["sources", "status", "rating", "engines", "tags"] as const;
export type DiscoverFilterSection = (typeof DISCOVER_FILTER_SECTIONS)[number];
/** Open (true) or folded (false) per section. */
export type DiscoverSections = Record<DiscoverFilterSection, boolean>;

/** Tags shown before "Show all". */
export const DISCOVER_TAG_PREVIEW = 12;

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
  sections: DiscoverSections;
}

const defaultSections = (): DiscoverSections => ({ sources: true, status: true, rating: true, engines: true, tags: true });

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
  sections: defaultSections(),
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
    sections: normalizeSections(raw.sections),
  };
};

const normalizeSections = (value: unknown): DiscoverSections => {
  const sections = defaultSections();
  if (typeof value !== "object" || value === null) return sections;
  const raw = value as Record<string, unknown>;
  for (const key of DISCOVER_FILTER_SECTIONS) if (typeof raw[key] === "boolean") sections[key] = raw[key];
  return sections;
};

export const toggleDiscoverSection = (state: DiscoverViewState, section: DiscoverFilterSection): DiscoverViewState => ({
  ...state,
  sections: { ...state.sections, [section]: !state.sections[section] },
});

/**
 * How many filters each pane section has on, so a folded section still shows
 * that it filters. "Hide library games" is a toolbar button, not a section.
 */
export const discoverSectionCounts = (state: DiscoverViewState): Record<DiscoverFilterSection, number> => ({
  sources: state.includeSites.length + state.excludeSites.length,
  status: state.statuses.length,
  rating: state.minRating ? 1 : 0,
  engines: state.includeEngines.length,
  tags: state.includeTags.length + state.excludeTags.length,
});

/**
 * The tag pills to show. Chosen (included or excluded) tags come first, then
 * by game count. Without search text and without "Show all", the list is the
 * top DISCOVER_TAG_PREVIEW tags by count plus every chosen tag, so a chosen
 * tag never hides. Search text searches every tag and shows every match.
 * `total` is the number of tags that "Show all" shows; `foldable` is true
 * when the list is longer than the preview (no search text), so the
 * "Show all" / "Show fewer" button applies.
 */
export const discoverTagList = (
  tags: readonly DiscoverTagOption[],
  state: DiscoverViewState,
  search: string,
  showAll: boolean,
  preview = DISCOVER_TAG_PREVIEW,
): { shown: DiscoverTagOption[]; total: number; foldable: boolean } => {
  const text = search.trim().toLowerCase();
  const chosen = (name: string) => tagState(state, name) !== "neutral";
  const matching = tags.filter((tag) => !text || tag.name.toLowerCase().includes(text));
  const byCount = matching.slice().sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));
  const foldable = !text && byCount.length > preview;
  const top = new Set(byCount.slice(0, preview));
  const kept = foldable && !showAll ? byCount.filter((tag) => top.has(tag) || chosen(tag.name)) : byCount;
  const shown = kept.slice().sort((a, b) => Number(chosen(b.name)) - Number(chosen(a.name)));
  return { shown, total: matching.length, foldable };
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

/** The view with every filter cleared; the sort, the pane and its sections stay. */
export const clearDiscoverFilters = (state: DiscoverViewState): DiscoverViewState => ({
  ...defaultDiscoverView(),
  sort: state.sort,
  filtersOpen: state.filtersOpen,
  sections: { ...state.sections },
});

export const siteState = (state: DiscoverViewState, site: DiscoverSite): FilterValueState =>
  filterValueState(state.includeSites, state.excludeSites, site);

/**
 * A click on a site pill: an included site goes back to neutral, any other
 * site becomes included. This is the Library's pill model.
 */
export const includeSite = (state: DiscoverViewState, site: DiscoverSite): DiscoverViewState => {
  const next = toggleIncludedFilterValue(state.includeSites, state.excludeSites, site);
  return { ...state, includeSites: next.included as DiscoverSite[], excludeSites: next.excluded as DiscoverSite[] };
};

/** The pill's "−" button, Alt+click or the minus key: exclude, or stop excluding. */
export const excludeSite = (state: DiscoverViewState, site: DiscoverSite): DiscoverViewState => {
  const next = toggleExcludedFilterValue(state.includeSites, state.excludeSites, site);
  return { ...state, includeSites: next.included as DiscoverSite[], excludeSites: next.excluded as DiscoverSite[] };
};

/** Tags compare without case and punctuation, as the backend's tag keys do. */
export const discoverTagKey = (name: string): string => name.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, "");

export const tagState = (state: DiscoverViewState, tag: string): FilterValueState =>
  filterValueState(state.includeTags, state.excludeTags, tag, discoverTagKey);

export const includeTag = (state: DiscoverViewState, tag: string): DiscoverViewState => {
  const next = toggleIncludedFilterValue(state.includeTags, state.excludeTags, tag, discoverTagKey);
  return { ...state, includeTags: next.included, excludeTags: next.excluded };
};

export const excludeTag = (state: DiscoverViewState, tag: string): DiscoverViewState => {
  const next = toggleExcludedFilterValue(state.includeTags, state.excludeTags, tag, discoverTagKey);
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

export type DiscoverFilterKind = "site" | "status" | "rating" | "engine" | "tag" | "hideLibrary";

/** One active filter as the chip row and the no-results text show it. */
export interface DiscoverFilterChip {
  kind: DiscoverFilterKind;
  value: string;
  /** Shown text: the name, or "−name" for an excluded value. */
  label: string;
  /** Name of the chip's remove button for screen readers. */
  removeLabel: string;
  excluded: boolean;
  key: string;
}

const SITE_LABELS = Object.fromEntries(DISCOVER_SITES.map((site) => [site.key, site.label])) as Record<DiscoverSite, string>;
const STATUS_LABELS = Object.fromEntries(DISCOVER_STATUSES.map((status) => [status.key, status.label])) as Record<DiscoverStatus, string>;

/**
 * Every active filter, one chip each, in the order of the filter pane. The
 * number of chips is the same as activeDiscoverFilterCount.
 */
export const discoverFilterChips = (state: DiscoverViewState): DiscoverFilterChip[] => {
  const chips: DiscoverFilterChip[] = [];
  const add = (kind: DiscoverFilterKind, value: string, name: string, what: string, excluded = false) =>
    chips.push({
      kind,
      value,
      label: excluded ? `−${name}` : name,
      removeLabel: `Remove filter: ${[excluded ? "excluded" : "", what, name].filter(Boolean).join(" ")}`,
      excluded,
      key: `${kind}:${excluded ? "not:" : ""}${value}`,
    });
  for (const site of state.includeSites) add("site", site, SITE_LABELS[site], "source");
  for (const site of state.excludeSites) add("site", site, SITE_LABELS[site], "source", true);
  for (const status of state.statuses) add("status", status, STATUS_LABELS[status], "status");
  if (state.minRating) add("rating", state.minRating, `F95 rating ${state.minRating}+`, "");
  for (const engine of state.includeEngines) add("engine", engine, engine, "engine");
  for (const tag of state.includeTags) add("tag", tag, tag, "tag");
  for (const tag of state.excludeTags) add("tag", tag, tag, "tag", true);
  if (state.hideLibrary) add("hideLibrary", "", HIDE_LIBRARY_LABEL, "");
  return chips;
};

/** The view without the filter of one chip. */
export const removeDiscoverFilter = (state: DiscoverViewState, chip: DiscoverFilterChip): DiscoverViewState => {
  switch (chip.kind) {
    case "site":
      return chip.excluded
        ? { ...state, excludeSites: state.excludeSites.filter((site) => site !== chip.value) }
        : { ...state, includeSites: state.includeSites.filter((site) => site !== chip.value) };
    case "tag": {
      const key = discoverTagKey(chip.value);
      return chip.excluded
        ? { ...state, excludeTags: state.excludeTags.filter((tag) => discoverTagKey(tag) !== key) }
        : { ...state, includeTags: state.includeTags.filter((tag) => discoverTagKey(tag) !== key) };
    }
    case "status":
      return { ...state, statuses: state.statuses.filter((status) => status !== chip.value) };
    case "engine":
      return { ...state, includeEngines: state.includeEngines.filter((engine) => engine !== chip.value) };
    case "rating":
      return { ...state, minRating: "" };
    case "hideLibrary":
      return { ...state, hideLibrary: false };
  }
};

/**
 * The no-results text: what is active, so the user sees what to remove. A long
 * search text is cut; more than `limit` filters end with "and N more".
 */
export const describeDiscoverNoResults = (
  search: string,
  chips: readonly DiscoverFilterChip[],
  limit = 6,
): { title: string; detail: string } => {
  const typed = search.trim();
  const shown = typed.length > 48 ? `${typed.slice(0, 47).trimEnd()}…` : typed;
  const names = chips.slice(0, limit).map((chip) => chip.label);
  const more = chips.length - names.length;
  const list = more > 0 ? `${names.join(", ")} and ${more} more` : names.join(", ");
  const detail = chips.length ? `${chips.length === 1 ? "Filter" : "Filters"} on: ${list}.` : "";
  if (shown) {
    return {
      title: `No games match “${shown}”`,
      detail: detail || "Check the spelling, or search by original title or developer.",
    };
  }
  return { title: chips.length ? "No games match your filters" : "No games to show", detail };
};

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

export interface DiscoverResultsInput {
  /** A page request is running. */
  loading: boolean;
  /** The running request loads the first page (a new search or filter). */
  firstPage: boolean;
  /** The last request's error, or "". */
  error: string;
  /** The offset of the page that failed. */
  errorOffset: number;
  /** All matches for the query, from the last good response. */
  total: number;
  /** The cards that are loaded. */
  shown: number;
}

export interface DiscoverResultsView {
  /** What fills the results area. */
  body: "error" | "empty" | "cards";
  /** Error card under the loaded cards: a next page failed. */
  moreError: boolean;
  /** The old cards stay on screen while the first page of a new query loads. */
  refreshing: boolean;
  /** The count line says "Searching…": nothing is on screen yet. */
  searching: boolean;
}

/**
 * Which state the results area shows. A first-page error replaces the cards;
 * a next-page error goes under them, so the loaded cards stay.
 */
export const discoverResultsView = (input: DiscoverResultsInput): DiscoverResultsView => {
  const { loading, firstPage, error, errorOffset, total, shown } = input;
  if (error && (errorOffset === 0 || shown === 0)) {
    return { body: "error", moreError: false, refreshing: false, searching: false };
  }
  if (!loading && total === 0) return { body: "empty", moreError: false, refreshing: false, searching: false };
  return {
    body: "cards",
    moreError: Boolean(error),
    refreshing: loading && firstPage && shown > 0,
    searching: loading && shown === 0,
  };
};

/** Full name of each site a card is on, in the card's site order. */
export const discoverSiteNames = (sites: DiscoverSite[]): string[] => sites.map((site) => SITE_LABELS[site] ?? site);

/**
 * The card's one source cluster: the first site's full name and how many
 * other sites the game is on ("F95Zone +3"). The preview lists every site.
 */
export const discoverSourceSummary = (sites: DiscoverSite[]): { lead: string; more: number } => ({
  lead: sites[0] ? SITE_LABELS[sites[0]] ?? sites[0] : "",
  more: Math.max(0, sites.length - 1),
});

/** "Ongoing", "Completed", "On hold" or "Abandoned"; "" when unknown. */
export const discoverStatusLabel = (status: DiscoverCard["status"]): string => (status ? STATUS_LABELS[status] ?? "" : "");

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
