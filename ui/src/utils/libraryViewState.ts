// SPDX-License-Identifier: GPL-3.0-or-later
import { PLAY_STATUSES } from "./playStatus.ts";

export type LayoutMode = "grid" | "list" | "compact";
export type SortDir = "asc" | "desc";
export type SortField =
  | "title"
  | "version"
  | "play_status"
  | "engine"
  | "date_added"
  | "last_played"
  | "playtime_seconds"
  | "rating"
  | "own_rating";
export type FilterCollection =
  | "All"
  | "Favorites"
  | "Updates available"
  | "New since finished"
  | "Recently played";
export type FilterInstallState = "" | "installed" | "wishlist";

export interface FilterSections {
  presets: boolean;
  collections: boolean;
  status: boolean;
  engines: boolean;
  tags: boolean;
}

export interface LibraryViewState {
  version: number;
  layoutMode: LayoutMode;
  sortBy: SortField;
  sortDir: SortDir;
  filterCollection: FilterCollection;
  filterStatuses: string[];
  filterInstallState: FilterInstallState;
  filterEngines: string[];
  filterTags: string[];
  isFiltersCollapsed: boolean;
  filterSections: FilterSections;
  // Selected user collection tab; "" means "All". Validated against the
  // loaded collection list, falling back to "" when the name is gone.
  activeCollection: string;
}

// A named snapshot of the filters and sort order, saved as a preset and
// carried through backup and sync as the `library_filter_presets` setting.
export interface LibraryFilterPreset {
  name: string;
  filterCollection: FilterCollection;
  filterStatuses: string[];
  filterInstallState: FilterInstallState;
  filterEngines: string[];
  filterTags: string[];
  sortBy: SortField;
  sortDir: SortDir;
}

export type NormalizedFilterAndSort = Pick<
  LibraryFilterPreset,
  | "filterCollection"
  | "filterStatuses"
  | "filterInstallState"
  | "filterEngines"
  | "filterTags"
  | "sortBy"
  | "sortDir"
>;

export type LibraryViewStateSource = "default" | "current" | "legacy";

interface LibraryViewStateResult {
  state: LibraryViewState;
  source: LibraryViewStateSource;
}

interface NormalizeLibraryViewStateOptions {
  validEngines?: Iterable<string>;
  validTags?: Iterable<string>;
  validCollections?: Iterable<string>;
}

export const LIBRARY_VIEW_STATE_STORAGE_KEY = "wlib-library-view-state";
export const LEGACY_LAYOUT_MODE_STORAGE_KEY = "wlib-layout-mode";
export const LEGACY_FILTERS_PANE_STORAGE_KEY = "wlib-filters-collapsed";
export const LEGACY_FILTER_SECTIONS_STORAGE_KEY = "wlib-filter-sections";
export const LIBRARY_VIEW_STATE_VERSION = 1;

export const LIBRARY_PLAY_STATUSES = PLAY_STATUSES;

export const DEFAULT_FILTER_SECTIONS: FilterSections = {
  presets: true,
  collections: true,
  status: true,
  engines: true,
  tags: true,
};

export const DEFAULT_LIBRARY_VIEW_STATE: LibraryViewState = {
  version: LIBRARY_VIEW_STATE_VERSION,
  layoutMode: "grid",
  sortBy: "title",
  sortDir: "asc",
  filterCollection: "All",
  filterStatuses: [],
  filterInstallState: "",
  filterEngines: [],
  filterTags: [],
  isFiltersCollapsed: true,
  filterSections: { ...DEFAULT_FILTER_SECTIONS },
  activeCollection: "",
};

const VALID_LAYOUT_MODES = new Set<LayoutMode>(["grid", "list", "compact"]);
export const VALID_SORT_FIELDS = new Set<SortField>([
  "title",
  "version",
  "play_status",
  "engine",
  "date_added",
  "last_played",
  "playtime_seconds",
  "rating",
  "own_rating",
]);
const VALID_SORT_DIRECTIONS = new Set<SortDir>(["asc", "desc"]);
const VALID_FILTER_COLLECTIONS = new Set<FilterCollection>([
  "All",
  "Favorites",
  "Updates available",
  "New since finished",
  "Recently played",
]);

const normalizeStringArray = (value: unknown): string[] => {
  if (!Array.isArray(value)) {
    return [];
  }

  const normalized: string[] = [];
  const seen = new Set<string>();

  value.forEach((item) => {
    if (typeof item !== "string") {
      return;
    }

    const trimmed = item.trim();
    if (!trimmed || seen.has(trimmed)) {
      return;
    }

    seen.add(trimmed);
    normalized.push(trimmed);
  });

  return normalized;
};

const filterAllowedValues = (values: readonly string[], allowed?: Iterable<string>): string[] => {
  if (!allowed) {
    return [...values];
  }

  const allowedMap = new Map<string, string>();
  for (const item of allowed) {
    if (typeof item === "string") {
      const trimmed = item.trim();
      if (trimmed && !allowedMap.has(trimmed.toLowerCase())) {
        allowedMap.set(trimmed.toLowerCase(), trimmed);
      }
    }
  }

  const result: string[] = [];
  const seen = new Set<string>();
  for (const value of values) {
    if (typeof value === "string") {
      const canonical = allowedMap.get(value.trim().toLowerCase());
      if (canonical && !seen.has(canonical.toLowerCase())) {
        seen.add(canonical.toLowerCase());
        result.push(canonical);
      }
    }
  }

  return result;
};

const readStorageItem = (storage: Storage, key: string): string | null => {
  try {
    return storage.getItem(key);
  } catch (error) {
    console.warn(`Failed to read localStorage key '${key}'`, error);
    return null;
  }
};

const writeStorageItem = (storage: Storage, key: string, value: string): void => {
  try {
    storage.setItem(key, value);
  } catch (error) {
    console.warn(`Failed to write localStorage key '${key}'`, error);
  }
};

const removeStorageItem = (storage: Storage, key: string): void => {
  try {
    storage.removeItem(key);
  } catch (error) {
    console.warn(`Failed to remove localStorage key '${key}'`, error);
  }
};

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null;

export const normalizeFilterSections = (value: unknown): FilterSections => {
  const source = isRecord(value) ? value : null;

  return {
    presets:
      typeof source?.presets === "boolean"
        ? source.presets
        : DEFAULT_FILTER_SECTIONS.presets,
    collections:
      typeof source?.collections === "boolean"
        ? source.collections
        : DEFAULT_FILTER_SECTIONS.collections,
    status:
      typeof source?.status === "boolean"
        ? source.status
        : DEFAULT_FILTER_SECTIONS.status,
    engines:
      typeof source?.engines === "boolean"
        ? source.engines
        : DEFAULT_FILTER_SECTIONS.engines,
    tags:
      typeof source?.tags === "boolean"
        ? source.tags
        : DEFAULT_FILTER_SECTIONS.tags,
  };
};

// Shared by the view state and the saved filter presets so both drop the
// same unknown values.
export const normalizeFilterAndSort = (
  value: unknown,
  options: NormalizeLibraryViewStateOptions = {},
): NormalizedFilterAndSort => {
  const source = isRecord(value) ? value : {};

  const sortBy =
    typeof source.sortBy === "string" && VALID_SORT_FIELDS.has(source.sortBy as SortField)
      ? (source.sortBy as SortField)
      : DEFAULT_LIBRARY_VIEW_STATE.sortBy;

  const sortDir =
    typeof source.sortDir === "string" && VALID_SORT_DIRECTIONS.has(source.sortDir as SortDir)
      ? (source.sortDir as SortDir)
      : DEFAULT_LIBRARY_VIEW_STATE.sortDir;

  const filterCollection =
    typeof source.filterCollection === "string" &&
    VALID_FILTER_COLLECTIONS.has(source.filterCollection as FilterCollection)
      ? (source.filterCollection as FilterCollection)
      : DEFAULT_LIBRARY_VIEW_STATE.filterCollection;

  const filterStatuses = normalizeStringArray(source.filterStatuses).filter((status) => status.length <= 40);
  const filterInstallState: FilterInstallState =
    source.filterInstallState === "installed" || source.filterInstallState === "wishlist"
      ? source.filterInstallState
      : "";

  const filterEngines = filterAllowedValues(
    normalizeStringArray(source.filterEngines),
    options.validEngines,
  );

  const filterTags = filterAllowedValues(normalizeStringArray(source.filterTags), options.validTags);

  return {
    filterCollection,
    filterStatuses,
    filterInstallState,
    filterEngines,
    filterTags,
    sortBy,
    sortDir,
  };
};

// Returns null for entries without a usable 1–40 character name; malformed
// values fall back to the defaults instead of failing the whole list.
export const normalizeFilterPreset = (
  value: unknown,
  options: NormalizeLibraryViewStateOptions = {},
): LibraryFilterPreset | null => {
  const source = isRecord(value) ? value : {};
  const name = typeof source.name === "string" ? source.name.trim() : "";
  if (!name || name.length > 40) return null;
  return { name, ...normalizeFilterAndSort(source, options) };
};

export const normalizeLibraryViewState = (
  value: unknown,
  options: NormalizeLibraryViewStateOptions = {},
): LibraryViewState => {
  const source = isRecord(value) ? value : {};

  const layoutMode =
    typeof source.layoutMode === "string" && VALID_LAYOUT_MODES.has(source.layoutMode as LayoutMode)
      ? (source.layoutMode as LayoutMode)
      : DEFAULT_LIBRARY_VIEW_STATE.layoutMode;

  const activeCollection = normalizeActiveCollection(
    source.activeCollection,
    options.validCollections,
  );

  return {
    version: LIBRARY_VIEW_STATE_VERSION,
    layoutMode,
    ...normalizeFilterAndSort(source, options),
    isFiltersCollapsed:
      typeof source.isFiltersCollapsed === "boolean"
        ? source.isFiltersCollapsed
        : DEFAULT_LIBRARY_VIEW_STATE.isFiltersCollapsed,
    filterSections: normalizeFilterSections(source.filterSections),
    activeCollection,
  };
};

// "" means the "All" tab. Without a collection list the stored value is kept;
// the caller re-normalises once the real list has loaded.
export const normalizeActiveCollection = (
  value: unknown,
  validCollections?: Iterable<string>,
): string => {
  const name = typeof value === "string" ? value.trim() : "";
  if (!name) return "";
  if (!validCollections) return name;
  for (const collection of validCollections) {
    if (
      typeof collection === "string" &&
      collection.trim().toLowerCase() === name.toLowerCase()
    ) {
      return collection.trim();
    }
  }
  return "";
};

const readLegacyLibraryViewState = (storage: Storage): LibraryViewState | null => {
  const rawLayoutMode = readStorageItem(storage, LEGACY_LAYOUT_MODE_STORAGE_KEY);
  const rawFiltersCollapsed = readStorageItem(storage, LEGACY_FILTERS_PANE_STORAGE_KEY);
  const rawFilterSections = readStorageItem(storage, LEGACY_FILTER_SECTIONS_STORAGE_KEY);

  if (rawLayoutMode === null && rawFiltersCollapsed === null && rawFilterSections === null) {
    return null;
  }

  const legacyState: Record<string, unknown> = {};

  if (rawLayoutMode !== null) {
    legacyState.layoutMode = rawLayoutMode;
  }

  if (rawFiltersCollapsed === "true" || rawFiltersCollapsed === "false") {
    legacyState.isFiltersCollapsed = rawFiltersCollapsed === "true";
  }

  if (rawFilterSections) {
    try {
      legacyState.filterSections = JSON.parse(rawFilterSections) as unknown;
    } catch (error) {
      console.warn("Failed to parse legacy library filter section state", error);
    }
  }

  return normalizeLibraryViewState(legacyState);
};

export const readLibraryViewState = (
  storage: Storage,
  options: NormalizeLibraryViewStateOptions = {},
): LibraryViewStateResult => {
  const rawState = readStorageItem(storage, LIBRARY_VIEW_STATE_STORAGE_KEY);

  if (rawState) {
    try {
      return {
        state: normalizeLibraryViewState(JSON.parse(rawState) as unknown, options),
        source: "current",
      };
    } catch (error) {
      console.warn("Failed to parse saved library view state", error);
    }
  }

  const legacyState = readLegacyLibraryViewState(storage);
  if (legacyState) {
    return {
      state: normalizeLibraryViewState(legacyState, options),
      source: "legacy",
    };
  }

  return {
    state: { ...DEFAULT_LIBRARY_VIEW_STATE, filterSections: { ...DEFAULT_FILTER_SECTIONS } },
    source: "default",
  };
};

export const saveLibraryViewState = (storage: Storage, state: LibraryViewState): void => {
  writeStorageItem(
    storage,
    LIBRARY_VIEW_STATE_STORAGE_KEY,
    JSON.stringify(normalizeLibraryViewState(state)),
  );
};

export const clearLegacyLibraryViewState = (storage: Storage): void => {
  removeStorageItem(storage, LEGACY_LAYOUT_MODE_STORAGE_KEY);
  removeStorageItem(storage, LEGACY_FILTERS_PANE_STORAGE_KEY);
  removeStorageItem(storage, LEGACY_FILTER_SECTIONS_STORAGE_KEY);
};
