// SPDX-License-Identifier: GPL-3.0-or-later
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
  normalizeActiveCollection,
  normalizeFilterPreset,
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

test("new collections and sortable table columns survive persistence", () => {
  const storage = new MemoryStorage();
  for (const filterCollection of [
    "Updates available",
    "New since finished",
    "Recently played",
  ]) {
    for (const sortBy of ["version", "play_status", "engine"]) {
      saveLibraryViewState(storage, normalizeLibraryViewState({ filterCollection, sortBy, layoutMode: "list" }));
      const { state } = readLibraryViewState(storage);
      assert.equal(state.filterCollection, filterCollection);
      assert.equal(state.sortBy, sortBy);
      assert.equal(state.layoutMode, "list");
    }
  }
});

test("install state filter survives persistence", () => {
  const storage = new MemoryStorage();
  saveLibraryViewState(storage, normalizeLibraryViewState({ filterInstallState: "wishlist" }));
  assert.equal(readLibraryViewState(storage).state.filterInstallState, "wishlist");
  assert.equal(normalizeLibraryViewState({ filterInstallState: "invalid" }).filterInstallState, "");
});

test("normalizeLibraryViewState falls back on invalid static values", () => {
  const state = normalizeLibraryViewState({
    layoutMode: "poster",
    sortBy: "developer",
    sortDir: "sideways",
    filterCollection: "FavoritesOnly",
    filterStatuses: ["Playing", "Unknown", "Playing", 7],
    filterEngines: ["  Ren'Py  ", "", "Unity", "Unity", 4],
    filterTags: ["  sci-fi  ", "", "tagged", 4],
    isFiltersCollapsed: "sometimes",
    filterSections: {
      collections: false,
      status: "yes",
      engines: "no",
    },
  });

  assert.equal(state.layoutMode, "grid");
  assert.equal(state.sortBy, "title");
  assert.equal(state.sortDir, "asc");
  assert.equal(state.filterCollection, "All");
  assert.deepEqual(state.filterStatuses, ["Playing", "Unknown"]);
  assert.deepEqual(state.filterEngines, ["Ren'Py", "Unity"]);
  assert.deepEqual(state.filterTags, ["sci-fi", "tagged"]);
  assert.equal(state.isFiltersCollapsed, true);
  assert.deepEqual(state.filterSections, {
    ...DEFAULT_FILTER_SECTIONS,
    collections: false,
  });});

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
    presets: true,
    collections: false,
    status: true,
    engines: true,
    tags: false,
  });

  saveLibraryViewState(storage, restored.state);
  clearLegacyLibraryViewState(storage);

  assert.equal(storage.getItem(LEGACY_LAYOUT_MODE_STORAGE_KEY), null);
  assert.equal(storage.getItem(LEGACY_FILTERS_PANE_STORAGE_KEY), null);
  assert.equal(storage.getItem(LEGACY_FILTER_SECTIONS_STORAGE_KEY), null);
  assert.notEqual(storage.getItem(LIBRARY_VIEW_STATE_STORAGE_KEY), null);
});

test("readLibraryViewState prefers current blob and prunes unsupported engines and tags", () => {
  const storage = new MemoryStorage();
  const state: LibraryViewState = {
    version: 1,
    layoutMode: "list",
    sortBy: "rating",
    sortDir: "desc",
    filterCollection: "Favorites",
    filterStatuses: ["Completed"],
    filterEngines: ["keep-engine", "drop-engine"],
    filterTags: ["keep", "drop"],
    isFiltersCollapsed: false,
    filterSections: { collections: true, status: false, engines: false, tags: true },
    activeCollection: "Backlog",
  };

  saveLibraryViewState(storage, state);
  const restored = readLibraryViewState(storage, {
    validEngines: ["keep-engine"],
    validTags: ["keep"],
    validCollections: ["Backlog"],
  });

  assert.equal(restored.source, "current");
  assert.equal(restored.state.layoutMode, "list");
  assert.equal(restored.state.sortBy, "rating");
  assert.equal(restored.state.filterCollection, "Favorites");
  assert.deepEqual(restored.state.filterStatuses, ["Completed"]);
  assert.deepEqual(restored.state.filterEngines, ["keep-engine"]);
  assert.deepEqual(restored.state.filterTags, ["keep"]);
  assert.equal(restored.state.activeCollection, "Backlog");
  assert.deepEqual(restored.state.filterSections, {
    presets: true,
    collections: true,
    status: false,
    engines: false,
    tags: true,
  });
});

test("active collection restores, falls back to All, and defaults for legacy state", () => {
  const storage = new MemoryStorage();
  saveLibraryViewState(storage, normalizeLibraryViewState({ activeCollection: "Backlog" }));
  assert.equal(readLibraryViewState(storage).state.activeCollection, "Backlog");

  // Unknown names (deleted, renamed, or synced away) fall back to "" = All.
  const pruned = readLibraryViewState(storage, { validCollections: ["Best of"] });
  assert.equal(pruned.state.activeCollection, "");

  // Matching is case-insensitive and keeps the stored spelling.
  const recased = readLibraryViewState(storage, { validCollections: ["backlog"] });
  assert.equal(recased.state.activeCollection, "backlog");

  // Legacy blobs without the field restore as "" without failing.
  storage.setItem(
    LIBRARY_VIEW_STATE_STORAGE_KEY,
    JSON.stringify({ version: 1, layoutMode: "grid" }),
  );
  assert.equal(readLibraryViewState(storage).state.activeCollection, "");
  assert.equal(normalizeLibraryViewState({ activeCollection: 7 }).activeCollection, "");
  assert.equal(normalizeLibraryViewState({ activeCollection: "  " }).activeCollection, "");
});

test("normalizeFilterPreset keeps a valid preset and drops unknown values", () => {
  const valid = normalizeFilterPreset(
    {
      name: " Ren'Py now ",
      filterCollection: "Favorites",
      filterStatuses: ["Playing"],
      filterInstallState: "installed",
      filterEngines: ["Ren'Py"],
      filterTags: ["dating-sim"],
      sortBy: "rating",
      sortDir: "desc",
    },
  );
  assert.deepEqual(valid, {
    name: "Ren'Py now",
    filterCollection: "Favorites",
    filterStatuses: ["Playing"],
    filterInstallState: "installed",
    filterEngines: ["Ren'Py"],
    filterTags: ["dating-sim"],
    sortBy: "rating",
    sortDir: "desc",
  });

  // Unknown engines, tags and enum values are dropped on apply.
  const pruned = normalizeFilterPreset(
    {
      name: "Pruned",
      filterCollection: "FavoritesOnly",
      filterStatuses: ["Playing", "Ghost"],
      filterInstallState: "bogus",
      filterEngines: ["Ren'Py", "Ghost"],
      filterTags: ["dating-sim", "ghost-tag"],
      sortBy: "developer",
      sortDir: "sideways",
    },
    { validEngines: ["Ren'Py"], validTags: ["dating-sim"] },
  );
  assert.deepEqual(pruned, {
    name: "Pruned",
    filterCollection: "All",
    filterStatuses: ["Playing", "Ghost"],
    filterInstallState: "",
    filterEngines: ["Ren'Py"],
    filterTags: ["dating-sim"],
    sortBy: "title",
    sortDir: "asc",
  });

  // Entries without a usable name are refused.
  for (const bad of [null, {}, { name: "" }, { name: "   " }, { name: "x".repeat(41) }, "corrupt"]) {
    assert.equal(normalizeFilterPreset(bad), null);
  }
});

test("normalizeLibraryViewState preserves the expanded supported status set", () => {
  const state = normalizeLibraryViewState({
    filterStatuses: [...LIBRARY_PLAY_STATUSES, "Unknown"],
  });

  assert.deepEqual(state.filterStatuses, [...LIBRARY_PLAY_STATUSES, "Unknown"]);
});

test("normalizeActiveCollection handles dirty collection iterables without throwing", () => {
  // Contains null, undefined, number, and whitespace
  const dirtyCollections = ["RPG", null, undefined, 42, "  Favorites  "] as any;
  assert.equal(normalizeActiveCollection("rpg", dirtyCollections), "RPG");
  assert.equal(normalizeActiveCollection("favorites", dirtyCollections), "Favorites");
  assert.equal(normalizeActiveCollection("nonexistent", dirtyCollections), "");
});

test("normalizeLibraryViewState preserves canonical casing for engines and tags", () => {
  const normalized = normalizeLibraryViewState(
    {
      filterEngines: ["ren'py", "unity", "unsupported"],
      filterTags: ["dating-sim", "SCI-FI", "drop"],
    },
    {
      validEngines: ["Ren'Py", "Unity", null] as any,
      validTags: ["dating-sim", "sci-fi", 123] as any,
    },
  );

  assert.deepEqual(normalized.filterEngines, ["Ren'Py", "Unity"]);
  assert.deepEqual(normalized.filterTags, ["dating-sim", "sci-fi"]);
});
