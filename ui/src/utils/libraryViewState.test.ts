import test from "node:test";
import assert from "node:assert/strict";

import {
  DEFAULT_FILTER_SECTIONS,
  LEGACY_FILTERS_PANE_STORAGE_KEY,
  LEGACY_FILTER_SECTIONS_STORAGE_KEY,
  LEGACY_LAYOUT_MODE_STORAGE_KEY,
  LIBRARY_PLAY_STATUSES,
  LIBRARY_VIEW_STATE_STORAGE_KEY,
  clearLegacyLibraryViewState,
  normalizeLibraryViewState,
  readLibraryViewState,
  saveLibraryViewState,
  type LibraryViewState,
} from "./libraryViewState.ts";

class MemoryStorage implements Storage {
  private readonly store = new Map<string, string>();

  get length(): number {
    return this.store.size;
  }

  clear(): void {
    this.store.clear();
  }

  getItem(key: string): string | null {
    return this.store.get(key) ?? null;
  }

  key(index: number): string | null {
    return [...this.store.keys()][index] ?? null;
  }

  removeItem(key: string): void {
    this.store.delete(key);
  }

  setItem(key: string, value: string): void {
    this.store.set(key, value);
  }
}

test("normalizeLibraryViewState falls back on invalid static values", () => {
  const state = normalizeLibraryViewState({
    layoutMode: "poster",
    sortBy: "developer",
    sortDir: "sideways",
    filterCollection: "FavoritesOnly",
    filterStatuses: ["Playing", "Unknown", "Playing", 7],
    filterTags: ["  sci-fi  ", "", "tagged", 4],
    isFiltersCollapsed: "sometimes",
    filterSections: {
      collections: false,
      status: "yes",
    },
  });

  assert.equal(state.layoutMode, "grid");
  assert.equal(state.sortBy, "title");
  assert.equal(state.sortDir, "asc");
  assert.equal(state.filterCollection, "All");
  assert.deepEqual(state.filterStatuses, ["Playing"]);
  assert.deepEqual(state.filterTags, ["sci-fi", "tagged"]);
  assert.equal(state.isFiltersCollapsed, true);
  assert.deepEqual(state.filterSections, {
    ...DEFAULT_FILTER_SECTIONS,
    collections: false,
  });
});

test("readLibraryViewState migrates legacy keys when no blob exists", () => {
  const storage = new MemoryStorage();
  storage.setItem(LEGACY_LAYOUT_MODE_STORAGE_KEY, "compact");
  storage.setItem(LEGACY_FILTERS_PANE_STORAGE_KEY, "false");
  storage.setItem(
    LEGACY_FILTER_SECTIONS_STORAGE_KEY,
    JSON.stringify({ collections: false, status: true, tags: false }),
  );

  const restored = readLibraryViewState(storage);

  assert.equal(restored.source, "legacy");
  assert.equal(restored.state.layoutMode, "compact");
  assert.equal(restored.state.isFiltersCollapsed, false);
  assert.deepEqual(restored.state.filterSections, {
    collections: false,
    status: true,
    tags: false,
  });

  saveLibraryViewState(storage, restored.state);
  clearLegacyLibraryViewState(storage);

  assert.equal(storage.getItem(LEGACY_LAYOUT_MODE_STORAGE_KEY), null);
  assert.equal(storage.getItem(LEGACY_FILTERS_PANE_STORAGE_KEY), null);
  assert.equal(storage.getItem(LEGACY_FILTER_SECTIONS_STORAGE_KEY), null);
  assert.notEqual(storage.getItem(LIBRARY_VIEW_STATE_STORAGE_KEY), null);
});

test("readLibraryViewState prefers current blob and prunes unsupported tags", () => {
  const storage = new MemoryStorage();
  const state: LibraryViewState = {
    version: 1,
    layoutMode: "list",
    sortBy: "rating",
    sortDir: "desc",
    filterCollection: "Favorites",
    filterStatuses: ["Completed"],
    filterTags: ["keep", "drop"],
    isFiltersCollapsed: false,
    filterSections: { collections: true, status: false, tags: true },
  };

  saveLibraryViewState(storage, state);
  const restored = readLibraryViewState(storage, { validTags: ["keep"] });

  assert.equal(restored.source, "current");
  assert.equal(restored.state.layoutMode, "list");
  assert.equal(restored.state.sortBy, "rating");
  assert.equal(restored.state.filterCollection, "Favorites");
  assert.deepEqual(restored.state.filterStatuses, ["Completed"]);
  assert.deepEqual(restored.state.filterTags, ["keep"]);
});

test("normalizeLibraryViewState preserves the expanded supported status set", () => {
  const state = normalizeLibraryViewState({
    filterStatuses: [...LIBRARY_PLAY_STATUSES, "Unknown"],
  });

  assert.deepEqual(state.filterStatuses, [...LIBRARY_PLAY_STATUSES]);
});
