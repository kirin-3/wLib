export type LayoutMode = "grid" | "list" | "compact";
export type SortDir = "asc" | "desc";
export type SortField =
  | "title"
  | "date_added"
  | "last_played"
  | "playtime_seconds"
  | "rating"
  | "own_rating";
export type FilterCollection = "All" | "Favorites";

export interface FilterSections {
  collections: boolean;
  status: boolean;
  tags: boolean;
}

export interface LibraryViewState {
  version: number;
  layoutMode: LayoutMode;
  sortBy: SortField;
  sortDir: SortDir;
  filterCollection: FilterCollection;
  filterStatuses: string[];
  filterTags: string[];
  isFiltersCollapsed: boolean;
  filterSections: FilterSections;
}

export type LibraryViewStateSource = "default" | "current" | "legacy";

interface LibraryViewStateResult {
  state: LibraryViewState;
  source: LibraryViewStateSource;
}

interface NormalizeLibraryViewStateOptions {
  validTags?: Iterable<string>;
}

export const LIBRARY_VIEW_STATE_STORAGE_KEY = "wlib-library-view-state";
export const LEGACY_LAYOUT_MODE_STORAGE_KEY = "wlib-layout-mode";
export const LEGACY_FILTERS_PANE_STORAGE_KEY = "wlib-filters-collapsed";
export const LEGACY_FILTER_SECTIONS_STORAGE_KEY = "wlib-filter-sections";
export const LIBRARY_VIEW_STATE_VERSION = 1;

export const LIBRARY_PLAY_STATUSES = [
  "Playing",
  "Completed",
  "On Hold",
  "Plan to Play",
] as const;

export const DEFAULT_FILTER_SECTIONS: FilterSections = {
  collections: true,
  status: true,
  tags: true,
};

export const DEFAULT_LIBRARY_VIEW_STATE: LibraryViewState = {
  version: LIBRARY_VIEW_STATE_VERSION,
  layoutMode: "grid",
  sortBy: "title",
  sortDir: "asc",
  filterCollection: "All",
  filterStatuses: [],
  filterTags: [],
  isFiltersCollapsed: true,
  filterSections: { ...DEFAULT_FILTER_SECTIONS },
};

const VALID_LAYOUT_MODES = new Set<LayoutMode>(["grid", "list", "compact"]);
const VALID_SORT_FIELDS = new Set<SortField>([
  "title",
  "date_added",
  "last_played",
  "playtime_seconds",
  "rating",
  "own_rating",
]);
const VALID_SORT_DIRECTIONS = new Set<SortDir>(["asc", "desc"]);
const VALID_FILTER_COLLECTIONS = new Set<FilterCollection>(["All", "Favorites"]);
const VALID_STATUS_VALUES = new Set<string>(LIBRARY_PLAY_STATUSES);

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

  const allowedSet = new Set<string>(allowed);
  return values.filter((value) => allowedSet.has(value));
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
    collections:
      typeof source?.collections === "boolean"
        ? source.collections
        : DEFAULT_FILTER_SECTIONS.collections,
    status:
      typeof source?.status === "boolean"
        ? source.status
        : DEFAULT_FILTER_SECTIONS.status,
    tags:
      typeof source?.tags === "boolean"
        ? source.tags
        : DEFAULT_FILTER_SECTIONS.tags,
  };
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

  const filterStatuses = filterAllowedValues(
    normalizeStringArray(source.filterStatuses),
    VALID_STATUS_VALUES,
  );

  const filterTags = filterAllowedValues(normalizeStringArray(source.filterTags), options.validTags);

  return {
    version: LIBRARY_VIEW_STATE_VERSION,
    layoutMode,
    sortBy,
    sortDir,
    filterCollection,
    filterStatuses,
    filterTags,
    isFiltersCollapsed:
      typeof source.isFiltersCollapsed === "boolean"
        ? source.isFiltersCollapsed
        : DEFAULT_LIBRARY_VIEW_STATE.isFiltersCollapsed,
    filterSections: normalizeFilterSections(source.filterSections),
  };
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
